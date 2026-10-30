"""Optional preburner gas-temperature limits must not imply material approval."""
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
import warnings
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import EngineInputs, ValidationError, preburner_temperature_constraint
from cycle import FFSCCycleModel
from engineering_assessment import assess_engineering
from tests.test_environment_assessment import example_result
from webapp.solver import input_values


class PreburnerConstraintTests(unittest.TestCase):
    def test_old_numeric_limits_remain_provisional_without_rewriting_source(self):
        legacy = {"ox_preburner_temp_limit": 820.0, "fuel_preburner_temp_limit": 940.0}
        before = dict(legacy)
        values, _ = input_values(legacy, saved_snapshot=True)
        self.assertEqual(legacy, before)
        for side, limit in (("ox", 820), ("fuel", 940)):
            self.assertEqual(values[f"{side}_preburner_temp_limit"], limit)
            self.assertEqual(values[f"{side}_preburner_temp_limit_status"], "provisional")
            self.assertEqual(values[f"{side}_preburner_temp_limit_basis"], "")
            self.assertEqual(preburner_temperature_constraint(legacy, side)["status"], "provisional")

    def test_unset_preserves_old_value_without_blocking_temperature_target(self):
        inputs = EngineInputs(ox_preburner_temp_limit_status="unset",
                              fuel_preburner_temp_limit_status="unset",
                              ox_preburner_temp_target=1000, fuel_preburner_temp_target=1100)
        self.assertEqual(inputs.ox_preburner_temp_limit, 800)
        self.assertEqual(inputs.fuel_preburner_temp_limit, 900)
        self.assertIsNone(preburner_temperature_constraint(inputs, "ox")["limit_K"])
        self.assertIsNone(preburner_temperature_constraint(inputs, "fuel")["limit_K"])

    def test_enabled_limit_still_blocks_conflicting_target(self):
        for status in ("provisional", "documented"):
            with self.subTest(status=status), self.assertRaisesRegex(ValidationError, "目标温度不能超过"):
                EngineInputs(ox_preburner_temp_target=900,
                             ox_preburner_temp_limit_status=status,
                             ox_preburner_temp_limit_basis="本方案试验条件下的上限")

    def test_documented_requires_nonempty_basis_but_does_not_claim_certification(self):
        with self.assertRaisesRegex(ValidationError, "已有依据状态必须填写"):
            EngineInputs(ox_preburner_temp_limit_status="documented",
                         ox_preburner_temp_limit_basis=" \n ")
        inputs = EngineInputs(ox_preburner_temp_limit_status="documented",
                              ox_preburner_temp_limit_basis="试验报告 A；对应压力和任务寿命")
        self.assertEqual(preburner_temperature_constraint(inputs, "ox")["basis"],
                         "试验报告 A；对应压力和任务寿命")

    def test_active_limits_and_targets_are_finite_positive_not_material_allowables(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                EngineInputs(ox_preburner_temp_limit=value)
        # Old 1200/1500 K input caps were not evidence of material allowables.
        inputs = EngineInputs(ox_preburner_temp_limit=1800, fuel_preburner_temp_limit=1800,
                              ox_preburner_temp_target=1250, fuel_preburner_temp_target=1550)
        self.assertEqual(inputs.ox_preburner_temp_target, 1250)

    def test_only_temperature_mode_applies_target_limit_conflict(self):
        mixture = EngineInputs(use_target_preburner_temp=0, use_target_preburner_mr=1,
                               ox_preburner_temp_target=1000, fuel_preburner_temp_target=1100)
        self.assertFalse(FFSCCycleModel(mixture)._temp_target_mode())
        with self.assertRaisesRegex(ValidationError, "目标温度不能超过"):
            EngineInputs(**(asdict(mixture) | {"use_target_preburner_temp": 1}))

    def test_gg_does_not_validate_inactive_ffsc_temperature_constraints(self):
        inputs = EngineInputs(cycle_type="gg", ox_preburner_temp_target=2000,
                              fuel_preburner_temp_target=2000,
                              ox_preburner_temp_limit_status="documented")
        self.assertEqual(inputs.cycle_type, "gg")
        result = example_result()
        result.inputs = inputs
        result.summary["gg_T_combustion_K"] = 900
        checks = {row["key"]: row for row in assess_engineering(result, scope="system")["checks"]}
        self.assertNotIn("ox_preburner_T_combustion_K", checks)
        self.assertNotIn("fuel_preburner_T_combustion_K", checks)
        self.assertEqual(checks["gg_T_combustion_K"]["status"], "pass")

    def test_inactive_preburner_mixture_ratios_do_not_block_gg_or_free_split(self):
        for mode in ({"cycle_type": "gg"},
                     {"cycle_type": "gg", "propellant_pair": "lox_kerosene",
                      "fuel_inlet_temp": 290, "cooling_bartz_mode": 0},
                     {"cycle_type": "ffsc", "use_target_preburner_temp": 0,
                      "use_target_preburner_mr": 0}):
            with self.subTest(mode=mode), warnings.catch_warnings(record=True) as caught:
                inputs = EngineInputs(**mode, ox_preburner_mr_target=0, fuel_preburner_mr_target=10)
                self.assertEqual(inputs.ox_preburner_mr_target, 0)
                self.assertEqual(inputs.fuel_preburner_mr_target, 10)
                self.assertFalse(any("preburner_mr_target" in str(item.message) for item in caught))

    def test_active_preburner_mixture_ratios_are_checked_in_their_actual_role(self):
        for temperature_mode, role in ((1, "O/F 初值"), (0, "指定 O/F")):
            for key, value in (("ox_preburner_mr_target", 0),
                               ("fuel_preburner_mr_target", 10),
                               ("fuel_preburner_mr_target", float("nan"))):
                with self.subTest(mode=temperature_mode, key=key), \
                     self.assertRaisesRegex(ValidationError, role):
                    EngineInputs(use_target_preburner_temp=temperature_mode,
                                 use_target_preburner_mr=1, **{key: value})

    def test_supported_mixture_ratios_have_no_unsubstantiated_recommendation_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            EngineInputs(ox_preburner_mr_target=30, fuel_preburner_mr_target=0.5)
        self.assertFalse(any("preburner_mr_target" in str(item.message) for item in caught))

    def test_assessment_separates_unset_provisional_and_documented(self):
        for state, expected in (("unset", "unknown"), ("provisional", "review"), ("documented", "pass")):
            for scope in ("engineering", "system"):
                with self.subTest(state=state, scope=scope):
                    result = example_result()
                    result.inputs.ox_preburner_temp_limit_status = state
                    result.inputs.ox_preburner_temp_limit_basis = "试验条件下的方案限值"
                    row = next(row for row in assess_engineering(result, scope=scope)["checks"]
                               if row["key"] == "ox_preburner_T_combustion_K")
                    self.assertEqual(row["status"], expected)
                    self.assertEqual(row["limit_status"], state)
                    self.assertEqual(row["value"], 760)
                    self.assertEqual(row["limit"], None if state == "unset" else 800)
                    self.assertNotIn("材料温度上限", row["detail"])

    def test_unset_large_temperature_is_unknown_while_enabled_exceedance_fails(self):
        result = example_result()
        result.summary["ox_preburner_T_combustion_K"] = 1250
        for state, expected in (("unset", "unknown"), ("provisional", "fail"), ("documented", "fail")):
            result.inputs.ox_preburner_temp_limit_status = state
            result.inputs.ox_preburner_temp_limit_basis = "试验条件下的方案限值"
            row = next(row for row in assess_engineering(result, scope="system")["checks"]
                       if row["key"] == "ox_preburner_T_combustion_K")
            self.assertEqual(row["status"], expected)

    def test_record_with_missing_documented_basis_cannot_pass(self):
        result = example_result()
        result.inputs.ox_preburner_temp_limit_status = "documented"
        row = next(row for row in assess_engineering(result, scope="system")["checks"]
                   if row["key"] == "ox_preburner_T_combustion_K")
        self.assertEqual(row["status"], "unknown")

    def test_provisional_constraints_prevent_overall_system_pass(self):
        result = example_result()
        result.steady_closure = {"schema_version": 1}
        with patch("steady_network.audit_network", return_value={"status": "pass", "pressure_tolerance_Pa": 1}), \
             patch("steady_closure.audit_energy", return_value={"status": "pass"}):
            assessment = assess_engineering(result, scope="system")
        self.assertEqual(assessment["status"], "review")
        self.assertEqual(assessment["review_count"], 2)
        self.assertEqual(assessment["failure_count"], 0)
        self.assertEqual(assessment["unknown_count"], 0)

    def test_real_cycle_does_not_send_unset_limits_to_turbines(self):
        from turbine import Turbine
        inputs = EngineInputs(cooling_bartz_mode=0, cooling_heat_load=90e6,
            use_target_preburner_temp=0, ox_preburner_mr_target=59.81689453125,
            fuel_preburner_mr_target=0.1599658203125,
            ox_preburner_temp_limit_status="unset", fuel_preburner_temp_limit_status="unset",
            ox_preburner_temp_limit=1, fuel_preburner_temp_limit=1)
        with patch("cycle.Turbine", wraps=Turbine) as turbine:
            result = FFSCCycleModel(inputs)._evaluate_once(766, 1.54, 1.79)
        self.assertEqual(turbine.call_count, 2)
        self.assertTrue(all(call.kwargs["t_in_limit_K"] is None for call in turbine.call_args_list))
        for key in ("ox_preburner_turbine", "fuel_preburner_turbine"):
            self.assertNotIn("t_in_over_limit", result.components[key].outputs)
        self.assertEqual(sum("出口温度上限未设定" in text for text in result.warnings), 2)


if __name__ == "__main__":
    unittest.main()
