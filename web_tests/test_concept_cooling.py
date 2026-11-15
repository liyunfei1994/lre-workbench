"""One real cycle proves the conceptual model's integration and evidence boundary."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from webapp.solver import calculate, presets
from webapp.nozzle_design import nozzle_design_view
from webapp.throttle import _cooling_baseline
from lre_workbench.components.chambers.geometry import compute_chamber
from lre_workbench.domain.analysis_contract import concept_design_task, concept_inputs, GROUPS
from lre_workbench.domain.models import EngineInputs
from lre_workbench.solvers.convergence import numerical_convergence


class ConceptCoolingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = dict(next(p["inputs"] for p in presets() if p["key"] == "raptor3_ffsc"))
        cls.values.update(cooling_bartz_mode=2, cooling_estimate_basis="自动回归算例，参考壁温与压降为假设")
        cls.result = calculate(cls.values)

    def test_actual_cycle_closes_without_prescribed_heat(self):
        c = self.result["calculation"]
        self.assertEqual(c["convergence"]["status"], "pass")
        self.assertEqual(c["steady_closure"]["feed_network"]["status"], "pass")
        self.assertEqual(c["steady_closure"]["energy_balance"]["status"], "pass")
        thermal = c["convergence"]["layers"]["thermal"]
        self.assertEqual(thermal["source"], "regen_concept_heat_v1")
        self.assertEqual(len(thermal["checks"]), 6)
        self.assertTrue(all(abs(x["residual"]) <= x["tolerance"] for x in thermal["checks"]))
        cool = c["components"]["cooling_channel"]
        output, profile = cool["outputs"], cool["profile"]
        self.assertGreater(abs(output["heat_load_W"] - self.values["cooling_heat_load"]), 1e6)
        self.assertAlmostEqual(sum(x["Q_W"] for x in profile["cells"]), output["heat_load_W"], delta=.001)
        self.assertAlmostEqual(output["heat_load_chamber_W"]+output["heat_load_nozzle_W"], output["heat_load_W"], delta=.001)
        self.assertLess(abs(output["energy_residual_W"]), .01)
        self.assertNotIn("T_wall_hot_max_K", output)
        self.assertFalse(profile["assumptions"]["channel_hydraulics_solved"])
        self.assertEqual(c["thermodynamic_applicability"]["solid_wall_thermal"]["status"], "not_evaluated")
        self.assertFalse(any("壁面制造状态未" in w for w in self.result["warnings"]))

    def test_saved_surface_supports_component_handoff_without_inventing_hardware(self):
        c = self.result["calculation"]
        profile = c["components"]["cooling_channel"]["profile"]
        geom = compute_chamber(At=.1, contraction_ratio=3, l_star=2, pc=self.values["pc"], cooling_profile=profile)
        self.assertEqual(geom.geometry_scope, "concept_hot_surface")
        self.assertAlmostEqual(geom.radius_m, profile["geometry"]["r_c_m"])
        for key in ("wall_thickness_mm", "inner_wall_thickness_mm", "cooling_channel_height_mm", "cooling_channel_width_mm", "num_cooling_channels"):
            self.assertIsNone(getattr(geom, key))
        self.assertTrue(self.result["nozzle_geometry"]["available"])
        thermal = nozzle_design_view(self.result)["thermal"]
        self.assertEqual(thermal["status"], "concept_estimate")
        self.assertTrue(thermal["nodes"])
        self.assertTrue(all(n["T_wall_hot_K"] is None for n in thermal["nodes"]))
        self.assertNotIn("nozzle_peak_wall_temperature_K", [m["key"] for m in thermal["metrics"]])
        with self.assertRaisesRegex(ValueError, "概略热负荷"):
            _cooling_baseline(c, self.values, .1, .005)

    def test_concept_coupling_requires_each_physical_residual(self):
        c = deepcopy(self.result["calculation"])
        c["convergence"]["layers"]["thermal"]["checks"][-1]["residual"] = 1.
        self.assertEqual(numerical_convergence(c)["status"], "fail")


class ConceptCoolingHistoryTests(unittest.TestCase):
    def test_old_tasks_keep_original_mode_and_missing_new_fields(self):
        keys = {"cooling_reference_wall_temp_K", "cooling_estimated_dp_pa", "cooling_estimate_basis"}
        for mode in (0, 1):
            values = asdict(EngineInputs(cooling_bartz_mode=mode))
            task = concept_design_task(values)
            for group in GROUPS:
                task[group] = {k:v for k,v in task[group].items() if k not in keys}
            original = deepcopy(task)
            restored = concept_inputs(task)
            self.assertEqual(restored, {k:v for k,v in values.items() if k not in keys})
            self.assertEqual(task, original)

    def test_new_mode_needs_complete_assumptions_and_round_trips(self):
        values = asdict(EngineInputs(cooling_bartz_mode=2))
        task = concept_design_task(values)
        self.assertEqual(concept_inputs(task), values)
        for key in ("cooling_reference_wall_temp_K", "cooling_estimated_dp_pa", "cooling_estimate_basis"):
            broken = deepcopy(task)
            for group in GROUPS:
                broken[group].pop(key, None)
            with self.assertRaises(ValueError):
                concept_inputs(broken)


if __name__ == "__main__":
    unittest.main()
