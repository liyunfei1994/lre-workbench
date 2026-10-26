"""A thermal material choice must not silently inherit another alloy's strength."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import EngineInputs
import chamber_design
import thermal_structural as ts
from tests.test_saved_cooling_views import profile_fixture
from wall_materials import GRCOP84_DATASET_ID, CUZR_DATASET_ID, get_wall_dataset


def material_profile(dataset_ids, *, match="matched", status="within_range"):
    profile = profile_fixture()
    datasets = {key: get_wall_dataset(key) for key in dataset_ids}
    profile["material_applicability"] = {
        "datasets": [data.metadata() for data in datasets.values()]}
    for i, cell in enumerate(profile["cells"]):
        data = datasets[dataset_ids[i % len(dataset_ids)]]
        cell["material"] = {
            "dataset_id": data.dataset_id, "material_id": data.material_id,
            "manufacturing_state_id": data.manufacturing_state.state_id,
            "manufacturing_match": match, "status": status,
        }
    return profile


class WallStructuralScopeTests(unittest.TestCase):
    def test_cuzr_preserves_loads_without_grcop84_strength(self):
        profile = material_profile([CUZR_DATASET_ID])
        before = deepcopy(profile)
        with patch.object(ts, "_wall_stress", side_effect=AssertionError("wrong alloy strength")):
            wf = ts.wall_field_from_profile(profile, {"chamber_pressure_Pa": 30e6}, {}, domain="full")
        self.assertEqual(profile, before)
        self.assertEqual(wf.structural_status, "not_assessed")
        self.assertEqual(wf.T_wg, profile["T_wg_K"])
        self.assertEqual(wf.q, profile["q_W_m2"])
        self.assertEqual(wf.p_coolant, profile["p_co_Pa"])
        self.assertEqual(wf.coolant_dp_Pa, 7e6)
        self.assertEqual(wf.SF, [])
        self.assertEqual(wf.sigma_eq, [])
        self.assertEqual(wf.sigma_y, [])
        self.assertEqual(wf.sigma_th, [])
        self.assertTrue(wf.sigma_p)
        self.assertIsNone(wf.min_SF)
        self.assertIsNone(wf.loads.liner_min_SF)
        self.assertIsNone(wf.loads.sigma_th_peak_MPa)
        self.assertEqual(wf.mat_T_max_use_K, 0)
        self.assertIn("CuZr", wf.material_name)
        self.assertIn("CuZr", wf.loads.liner_material)
        self.assertIn("未评估", wf.loads.to_rows()[-1][-1])
        # No NaN sentinels are introduced into an otherwise complete record.
        json.dumps(asdict(wf), ensure_ascii=False, allow_nan=False)

    def test_mixed_and_unmatched_assignments_are_not_structural_passes(self):
        for profile in (
                material_profile([GRCOP84_DATASET_ID, CUZR_DATASET_ID]),
                material_profile([GRCOP84_DATASET_ID], match="unknown"),
                material_profile([GRCOP84_DATASET_ID], match="mismatch"),
                material_profile([GRCOP84_DATASET_ID], status="out_of_range")):
            with self.subTest(profile=profile["material_applicability"]):
                wf = ts.wall_field_from_profile(profile, {}, {}, domain="full")
                self.assertEqual(wf.structural_status, "not_assessed")
                self.assertEqual(wf.SF, [])

    def test_only_historical_grcop84_category_retains_screening(self):
        profile = material_profile([GRCOP84_DATASET_ID])
        wf = ts.wall_field_from_profile(profile, {}, {}, domain="full")
        self.assertEqual(wf.structural_status, "screening_only")
        self.assertEqual(len(wf.SF), len(profile["x_m"]))
        self.assertIn("不代表结构或寿命资格已验证", wf.structural_reason)
        # A different legacy structural selection must not override saved alloy.
        other = ts.wall_field_from_profile(profile, {}, {}, inner_material_key="grcop42", domain="full")
        self.assertEqual(other.structural_status, "not_assessed")

    def test_explicit_missing_evidence_does_not_fall_back_to_legacy(self):
        incomplete = []
        profile = profile_fixture()
        profile["material_applicability"] = {}
        incomplete.append(profile)
        profile = material_profile([GRCOP84_DATASET_ID])
        del profile["cells"][0]["material"]
        incomplete.append(profile)
        profile = material_profile([GRCOP84_DATASET_ID])
        profile["material_applicability"]["datasets"] = []
        incomplete.append(profile)
        for profile in incomplete:
            wf = ts.wall_field_from_profile(profile, {}, {}, domain="full")
            self.assertEqual(wf.structural_status, "not_assessed")
            self.assertEqual(wf.SF, [])

    def test_scorecard_marks_unsupported_material_sf_not_assessed(self):
        profile = material_profile([CUZR_DATASET_ID])
        result = SimpleNamespace(inputs=EngineInputs(), summary={},
                                 components={"cooling_channel": SimpleNamespace(profile=profile)})
        wf = ts.wall_field_from_profile(profile, {}, {}, domain="full")
        sf_rows = [row for row in chamber_design.compute_scorecard(wf, result, result.inputs)
                   if row["criterion"] == "内壁安全系数 SF"]
        self.assertTrue(sf_rows)
        self.assertTrue(all(row["status"] == "na" and row["value"] == "未评估" for row in sf_rows))

    def test_legacy_profile_retains_explicitly_labeled_screening(self):
        wf = ts.wall_field_from_profile(profile_fixture(), {}, {}, domain="full")
        self.assertEqual(wf.structural_status, "legacy_screening")
        self.assertTrue(wf.SF)
        self.assertIn("未保存独立材料热物性身份", wf.structural_reason)

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib not installed")
    def test_plotters_display_na_without_empty_array_or_nan_errors(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        wf = ts.wall_field_from_profile(material_profile([CUZR_DATASET_ID]), {}, {}, domain="full")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Glyph.*missing from font")
            for plot in (ts.plot_wall_field, ts.plot_cooling_profiles):
                figure = Figure()
                FigureCanvasAgg(figure)
                plot(figure, wf)
                figure.canvas.draw()
                text = " ".join(item.get_text() for axis in figure.axes for item in axis.texts)
                self.assertIn("未评估", text)


if __name__ == "__main__":
    unittest.main()
