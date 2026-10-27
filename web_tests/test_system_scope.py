"""The web execution scope must not rewrite scientific inputs or pass detail checks."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core import ComponentResult, CycleResults, EngineInputs
from cycle import FFSCCycleModel
from engineering_assessment import assess_engineering
from gg_cycle import GGCycleModel
from webapp.solver import calculate, system_calculation_scope
from web_tests.convergence_fixture import converged_evidence


def example_result():
    inputs = EngineInputs(turb_ox_rotor_material="sx500")
    summary = {
        "solver_residual_norm": 1e-5, "solver_iterations": 8,
        "crossflow_fuel_to_oxpb_margin_MPa": 0.4,
        "crossflow_lox_to_fuelpb_margin_MPa": 0.3,
        "chamber_pressure_Pa": inputs.pc,
        "ox_turbine_exhaust_pressure_Pa": inputs.pc * (1 + inputs.ox_turbine_exhaust_dp_ratio) + 1e5,
        "fuel_turbine_exhaust_pressure_Pa": inputs.pc * (1 + inputs.fuel_turbine_exhaust_dp_ratio) + 1e5,
        "ox_side_power_balance_ratio": 1.0001, "fuel_side_power_balance_ratio": 0.9998,
        "power_balance_ratio": 0.9999,
        "ox_preburner_T_combustion_K": 760, "fuel_preburner_T_combustion_K": 860,
    }
    components = {
        "cooling_marching": ComponentResult("cooling_marching", {
            "hydraulic_infeasible": 1, "T_wall_hot_max_K": 1343}),
        "cooling_channel": ComponentResult("cooling_channel", {
            "heat_load_MW": 63.7, "delta_P_MPa": 2.0, "T_out_K": 381.6}),
    }
    return CycleResults(inputs, summary, {}, components, ["保留原始模型警告"], convergence=converged_evidence())


class SystemAssessmentTests(unittest.TestCase):
    def test_system_scope_separates_constraints_without_rewriting_legacy_result(self):
        result = example_result()
        original = deepcopy(asdict(result))
        legacy = assess_engineering(result)
        scoped = assess_engineering(result, scope="system")
        self.assertEqual(legacy["status"], "unknown")
        self.assertEqual(legacy["materials"]["turb_ox_rotor_material"]["status"], "placeholder")
        self.assertEqual(scoped["scope"], "system")
        self.assertEqual(scoped["status"], "unknown", scoped)
        self.assertEqual(scoped["label"], "系统约束待确认")
        checks = {row["key"]: row for row in scoped["checks"]}
        missing = ("feed_network_closure", "energy_ledger_closure", "cooling_hydraulics", "wall_temperature")
        for key in missing:
            self.assertEqual(checks[key]["status"], "unknown")
            self.assertIsNone(checks[key]["value"])
        provisional = {"ox_preburner_T_combustion_K", "fuel_preburner_T_combustion_K"}
        self.assertTrue(all(row["status"] == ("review" if key in provisional else "pass") for key, row in checks.items()
                            if key not in missing))
        self.assertEqual(scoped["materials"], {})
        self.assertIn("cooling_hydraulics", {check["key"] for check in scoped["checks"]})
        self.assertIn("wall_temperature", {check["key"] for check in scoped["checks"]})
        self.assertEqual(scoped["model_warnings"], result.warnings)
        self.assertEqual(asdict(result), original)
        self.assertEqual(assess_engineering(result), legacy)

    def test_pressure_power_and_temperature_failures_remain_effective(self):
        result = example_result()
        result.summary.update(crossflow_fuel_to_oxpb_margin_MPa=-1,
                              fuel_side_power_balance_ratio=0.5,
                              ox_preburner_T_combustion_K=2000)
        assessment = assess_engineering(result, scope="system")
        self.assertEqual(assessment["numerical"]["status"], "pass")
        self.assertEqual(assessment["status"], "fail")
        self.assertEqual(assessment["failure_count"], 3)

    def test_missing_or_nonconverged_system_never_passes(self):
        for changed in ({"crossflow_fuel_to_oxpb_margin_MPa": float("nan")},
                        {"solver_warning": 1}, {"solver_residual_norm": float("nan")}):
            with self.subTest(changed=changed):
                result = example_result()
                result.summary.update(changed)
                self.assertEqual(assess_engineering(result, scope="system")["status"], "unknown")

    def test_invalid_scope_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "scope"):
            assess_engineering(example_result(), scope="typo")


class SystemExecutionTests(unittest.TestCase):
    def test_legacy_postprocessing_flag_keeps_core_path_and_inputs(self):
        for model_class in (FFSCCycleModel, GGCycleModel):
            for temperature_target in (True, False):
                with self.subTest(model=model_class.__name__, temperature_target=temperature_target):
                    result = example_result()
                    original = asdict(result.inputs)
                    model = model_class(result.inputs)
                    progress = Mock()
                    method = "_evaluate_temp_targeted" if temperature_target else "_evaluate_design_core"
                    with patch.object(model, "_temp_target_mode", return_value=temperature_target), \
                         patch.object(model, method, return_value=result) as core, \
                         patch.object(model, "_attach_cooling_marching") as marching:
                        self.assertIs(model.evaluate(progress, include_cooling_marching=False), result)
                        if model_class is FFSCCycleModel:
                            expected = {"warm_state": None}
                            if temperature_target:
                                expected["preburner_mr_targets"] = None
                            core.assert_called_once_with(progress, **expected)
                        else:
                            core.assert_called_once_with(progress)
                        marching.assert_not_called()
                        self.assertIs(model.evaluate(progress), result)
                        marching.assert_called_once_with(result)
                    self.assertEqual(asdict(result.inputs), original)

    def test_scope_records_coupled_cooling_and_excludes_cfd_fea(self):
        scope = system_calculation_scope()
        self.assertEqual(scope["version"], 4)
        self.assertEqual(scope["disabled_postprocessing"], ["cfd", "fea"])
        self.assertFalse(scope["independent_cooling_postprocessing"])
        self.assertEqual(scope["cooling_profile_source"], "calculation.components.cooling_channel.profile")
        description = " ".join(scope["limitations"])
        self.assertIn("沿程模型参与循环", description)
        self.assertIn("预算模式不生成沿程解", description)
        self.assertIn("最大热面壁温作为系统热设计限值", description)
        self.assertNotIn("沿程冷却详细后处理未执行", description)

    def test_web_snapshot_records_scope_and_retains_saved_cooling(self):
        result = example_result()
        result.components.pop("cooling_marching")
        profile = {"version": 1, "model_id": "regen_quasi1d_ph_v1",
                   "summary": {"Q_total_W": 63.7e6, "dp_total_Pa": 2e6, "T_out_K": 381.6},
                   "nodes": [{"s_m": 0.0, "p_Pa": 70e6}, {"s_m": 1.0, "p_Pa": 68e6}],
                   "cells": [{"kind": "channel", "s_m": .5, "Q_W": 63.7e6}]}
        result.components["cooling_channel"].profile = deepcopy(profile)
        submitted = asdict(result.inputs)
        model = Mock()
        model.evaluate.return_value = result
        with patch("gg_cycle.make_cycle_model", return_value=model) as factory, \
             patch("webapp.solver.science_environment", return_value={"fingerprint": "test"}):
            record = calculate(submitted)
        self.assertEqual(asdict(factory.call_args.args[0]), submitted)
        model.evaluate.assert_called_once_with(progress_callback=None, include_cooling_marching=False)
        self.assertEqual(record["submitted_inputs"], submitted)
        self.assertEqual(record["calculation"]["inputs"], submitted)
        self.assertEqual(submitted["cooling_marching_enable"], 1.0)
        self.assertEqual(record["calculation_scope"]["id"], "engine_system")
        self.assertEqual(record["calculation_scope"]["version"], 4)
        self.assertEqual(record["calculation_scope"]["disabled_postprocessing"], ["cfd", "fea"])
        self.assertFalse(record["calculation_scope"]["independent_cooling_postprocessing"])
        self.assertEqual(record["assessment"]["scope"], "system")
        self.assertEqual(record["calculation"]["components"]["cooling_channel"]["outputs"],
                         result.components["cooling_channel"].outputs)
        self.assertEqual(record["calculation"]["components"]["cooling_channel"]["profile"], profile)
        self.assertEqual(record["warnings"], result.warnings)


if __name__ == "__main__":
    unittest.main()
