"""A fixed-hardware study must retain the source material data and partition."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from web_tests.test_throttle_cooling_contract import physical_source_fixture
from webapp.throttle import source_baseline
from wall_materials import get_wall_dataset, GRCOP84_DATASET_ID, CUZR_DATASET_ID
from wall_thermal import audit_cell, material_summary


class WallThrottleBindingTests(unittest.TestCase):
    def test_partitioned_partial_coverage_rebuilds_same_geometry(self):
        zones = [dict(x_start_m=.1, x_end_m=.2, dataset_id=CUZR_DATASET_ID,
                      manufacturing_state="unspecified")]
        source = physical_source_fixture(material_zones=zones, cover_eps=4.)
        before = deepcopy(source)
        _, baseline = source_baseline(source)
        saved = source["result"]["calculation"]["components"]["cooling_channel"]["profile"]
        hardware = baseline["cooling_hardware"]
        self.assertEqual(hardware["geometry"], saved["geometry"])
        self.assertEqual(hardware["wall_material_binding"]["material_boundaries_m"], [.1, .2])
        self.assertEqual(hardware["wall_material_binding"]["manufacturing_match"], "unknown")
        self.assertEqual(hardware["wall_material_binding"]["structural_qualification"], "not_assessed")
        self.assertEqual(source, before)

    def test_missing_historical_material_data_requires_new_design(self):
        for missing in ("material_assignment", "material_applicability"):
            with self.subTest(missing=missing):
                source = physical_source_fixture()
                source["result"]["calculation"]["components"]["cooling_channel"]["profile"].pop(missing)
                with self.assertRaisesRegex(ValueError, "材料.*历史记录.*重新计算"):
                    source_baseline(source)

    def test_changed_live_dataset_is_not_silently_used(self):
        source = physical_source_fixture()
        original = get_wall_dataset(GRCOP84_DATASET_ID)
        changed_curve = replace(original.conductivity_property,
                                coefficients=(7000., -3466., 599.5, -34.18))
        changed = replace(original, conductivity_property=changed_curve)
        with patch("wall_materials._DATASETS", {GRCOP84_DATASET_ID: changed}):
            with self.assertRaisesRegex(ValueError, "材料.*哈希.*重新计算"):
                source_baseline(source)

    def test_tampered_identity_assignment_or_audit_is_rejected(self):
        def profile(source):
            return source["result"]["calculation"]["components"]["cooling_channel"]["profile"]
        mutations = [
            lambda s: profile(s)["material_applicability"]["datasets"][0].update(data_sha256="0"*64),
            lambda s: profile(s)["material_applicability"]["datasets"][0]["conductivity_property"].update(coefficients=[1.]),
            lambda s: profile(s)["material_assignment"].update(default_dataset_id=CUZR_DATASET_ID),
            lambda s: profile(s)["cells"][1]["material"].update(dataset_id=CUZR_DATASET_ID),
            lambda s: profile(s)["cells"][1]["material"].update(manufacturing_match="matched"),
            lambda s: profile(s)["material_applicability"].update(manufacturing_match="matched"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                source = physical_source_fixture()
                mutate(source)
                with self.assertRaisesRegex(ValueError, "材料.*重新计算"):
                    source_baseline(source)

    def test_out_of_range_diagnostics_are_preserved_not_qualified(self):
        source = physical_source_fixture()
        calc = source["result"]["calculation"]
        profile = calc["components"]["cooling_channel"]["profile"]
        cell = profile["cells"][1]
        cell["T_wall_hot_K"] = 1300.
        cell["material"] = audit_cell(cell, get_wall_dataset(GRCOP84_DATASET_ID), "unspecified")
        profile["material_applicability"] = material_summary(profile["cells"][1:-1],
                                                            calc["inputs"]["cooling_wall_temp_limit_K"])
        binding = source_baseline(source)[1]["cooling_hardware"]["wall_material_binding"]
        self.assertEqual(binding["applicability_status"], "out_of_range")
        self.assertEqual(binding["manufacturing_match"], "unknown")
        self.assertEqual(binding["structural_qualification"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
