"""Optional project wall limits must not become material qualification or physics."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import ComponentResult, CycleResults, EngineInputs, ValidationError, wall_temperature_constraint
from cooling_marching import march_regen_circuit
from engineering_assessment import assess_engineering
from tests.cooling_profile_fixture import cooling_profile
from tests.test_regen_circuit import BASE
from wall_thermal import material_summary, audit_cell, wall_limit_assessment
from wall_materials import get_wall_dataset, GRCOP84_DATASET_ID


class WallLimitContractTests(unittest.TestCase):
    def test_new_inputs_unset_with_given_geometry_old_documents_remain_explicitly_provisional(self):
        fresh = EngineInputs()
        self.assertEqual(fresh.cooling_wall_temp_limit_status, "unset")
        self.assertIsNone(wall_temperature_constraint(fresh)["limit_K"])
        self.assertEqual(fresh.cooling_channel_auto_size, 0)
        self.assertEqual(fresh.cooling_channel_height_m, .008)
        self.assertEqual(fresh.cooling_sizing_reference, "coolant_inlet_velocity")
        raw = {"cooling_wall_temp_limit_K": 950.0}
        before = deepcopy(raw)
        old = EngineInputs.from_dict(raw)
        self.assertEqual(raw, before)
        self.assertEqual(old.cooling_wall_temp_limit_status, "provisional")
        self.assertEqual(wall_temperature_constraint(old)["limit_K"], 950)
        self.assertEqual(old.cooling_channel_auto_size, 1)
        self.assertEqual(old.cooling_channel_height_m, .005)
        self.assertEqual(old.cooling_sizing_reference, "legacy_inlet_density_throat_width")
        self.assertEqual(asdict(EngineInputs.from_dict(asdict(fresh))), asdict(fresh))

    def test_documented_requires_basis_active_limit_requires_positive_number(self):
        for settings in (dict(cooling_wall_temp_limit_status="invalid"),
                         dict(cooling_wall_temp_limit_status="documented"),
                         dict(cooling_wall_temp_limit_status="provisional", cooling_wall_temp_limit_K=0),
                         dict(cooling_wall_temp_limit_status="documented", cooling_wall_temp_limit_basis="  ")):
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                EngineInputs(**settings)
        # Validation is numeric; it is not an invented 400-1500 K material window.
        inputs = EngineInputs(cooling_wall_temp_limit_status="documented",
                              cooling_wall_temp_limit_basis="User project requirement A", cooling_wall_temp_limit_K=1600.)
        self.assertEqual(wall_temperature_constraint(inputs)["limit_K"], 1600)

    def test_constraint_assessment_separates_unset_provisional_and_documented(self):
        unset = wall_limit_assessment([900.], 800., "unset")
        self.assertEqual(unset["wall_temperature_limit_status"], "not_evaluated")
        self.assertIsNone(unset["wall_temperature_limit_K"])
        self.assertIsNone(unset["wall_over_limit_fraction"])
        self.assertEqual(unset["wall_temperature_limit_declared_K"], 800)
        self.assertEqual(wall_limit_assessment([700.], 800.)["wall_temperature_limit_status"], "provisional_within_limit")
        self.assertEqual(wall_limit_assessment([900.], 800.)["wall_temperature_limit_status"], "exceeded")
        self.assertEqual(wall_limit_assessment([700.], 800., "documented", "requirement A")["wall_temperature_limit_status"], "within_limit")
        self.assertEqual(wall_limit_assessment([700.], 800., "documented")["wall_temperature_limit_status"], "invalid_constraint")
        self.assertEqual(wall_limit_assessment([], 800., "documented", "A")["wall_temperature_limit_status"], "not_evaluated")

    def test_material_data_range_and_project_allowance_remain_independent(self):
        dataset = get_wall_dataset(GRCOP84_DATASET_ID)
        cell = dict(T_wall_hot_K=1300., T_wall_cold_K=1000., fin_reference_temperature_K=900.,
                    x_m=.3, s_m=.5, wall_conductivity_WmK=300.)
        cell["material"] = audit_cell(cell, dataset, "unspecified")
        summary = material_summary([cell], 1400., "documented", "example requirement A")
        self.assertEqual(summary["status"], "out_of_range")
        self.assertEqual(summary["wall_temperature_limit_status"], "within_limit")
        self.assertEqual(summary["max_temperature_K"], 1300.)
        self.assertEqual(summary["wall_temperature_limit_K"], 1400.)
        legacy = material_summary([cell], 1400.)
        self.assertEqual(legacy["wall_temperature_limit_status"], "within_limit")
        self.assertNotIn("wall_temperature_constraint_status", legacy)

    def test_system_check_never_passes_unset_or_provisional_constraint(self):
        for status, basis, expected in (("unset", "", "unknown"), ("provisional", "", "review"),
                                        ("documented", "Project requirement A", "pass")):
            inputs = EngineInputs(cooling_wall_temp_limit_status=status, cooling_wall_temp_limit_basis=basis,
                                  cooling_wall_temp_limit_K=2000.)
            results = CycleResults(inputs, {}, {}, {"cooling_channel": ComponentResult("cooling_channel", {}, profile=cooling_profile())}, [])
            row = next(row for row in assess_engineering(results, scope="system")["checks"] if row["key"] == "wall_temperature")
            self.assertEqual(row["status"], expected)
            self.assertEqual(row["limit_status"], status)

    def test_constraint_changes_recorded_acceptance_without_changing_heat_solution(self):
        settings = BASE | {"n_points": 24}
        unset = march_regen_circuit(**settings, wall_temp_limit_status="unset")
        limited = march_regen_circuit(**settings, wall_temp_limit_status="documented", wall_temp_limit_K=400.,
                                      wall_temp_limit_basis="Test project requirement")
        self.assertEqual(unset["profile"]["nodes"], limited["profile"]["nodes"])
        self.assertEqual(unset["profile"]["cells"], limited["profile"]["cells"])
        self.assertEqual(unset["summary"]["Q_total_W"], limited["summary"]["Q_total_W"])
        self.assertIsNone(unset["summary"]["wall_over_limit_fraction"])
        self.assertEqual(limited["profile"]["material_applicability"]["wall_temperature_limit_status"], "exceeded")
        self.assertGreater(limited["summary"]["T_wall_hot_max_K"], 400.)

    def test_new_constraint_metadata_is_bound_when_reusing_fixed_hardware(self):
        from web_tests.test_throttle_cooling_contract import physical_source_fixture
        from webapp.throttle import source_baseline
        source = physical_source_fixture()
        calc = source["result"]["calculation"]
        values, profile = calc["inputs"], calc["components"]["cooling_channel"]["profile"]
        profile["material_applicability"] = material_summary(profile["cells"][1:-1],
            values["cooling_wall_temp_limit_K"], values["cooling_wall_temp_limit_status"],
            values["cooling_wall_temp_limit_basis"])
        original = deepcopy(source)
        source_baseline(source)
        self.assertEqual(source, original)
        profile["material_applicability"]["wall_temperature_limit_basis"] = "changed after calculation"
        with self.assertRaisesRegex(ValueError, "壁温方案约束"):
            source_baseline(source)

    def test_desktop_scorecard_does_not_invent_coking_velocity_or_manufacturing_allowances(self):
        from tests.test_saved_cooling_views import result_fixture
        from thermal_structural import wall_field_from_profile
        from chamber_design import compute_scorecard, channel_feasibility
        result = result_fixture()
        wf = wall_field_from_profile(result.components["cooling_channel"].profile,
                                     result.summary, {}, domain="full")
        rows = compute_scorecard(wf, result, result.inputs)
        for row in rows:
            if row["criterion"] in ("热面壁温 T_wg", "冷面壁温 T_wc（甲烷结焦）", "冷却剂流速 V", "最小肋宽"):
                self.assertEqual(row["status"], "na")
        # A positive 0.1 mm rib only establishes non-overlap; its manufacturing
        # feasibility cannot be rejected or accepted without process evidence.
        distribution = dict(rib_m=[.0001], w_m=[.001], h_m=[.01], n=[300], pitch_m=[.0011])
        checks = channel_feasibility(distribution, [0.], 0.)
        self.assertFalse(any(level == "fail" for level, _ in checks))
        self.assertTrue(any("制造适用性尚未评估" in message for _, message in checks))
        distribution["rib_m"] = [-.001]
        self.assertTrue(any(level == "fail" for level, _ in channel_feasibility(distribution, [0.], 0.)))


if __name__ == "__main__":
    unittest.main()
