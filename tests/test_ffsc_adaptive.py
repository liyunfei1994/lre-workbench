"""Safeguards and work accounting for the revised FFSC numerical path."""
from dataclasses import asdict
from pathlib import Path
import copy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import EngineInputs, ValidationError
from cycle import FFSCCycleModel
from task_control import TaskCancelled
from tests.test_ffsc_convergence import inner_result, result_for


class AdaptivePathTests(unittest.TestCase):
    def setUp(self):
        self.model = FFSCCycleModel(EngineInputs(
            use_target_preburner_temp=0, cooling_marching_enable=0))
        seed_patch=patch.object(self.model,"_initialize_preburner_temperature_guess",return_value={})
        seed_patch.start()
        self.addCleanup(seed_patch.stop)

    def warm_state(self):
        i = self.model.inputs
        return dict(x=[100, 2, 2], chamber_temp_guess=i.chamber_temp_guess,
                    chamber_gamma=i.chamber_gamma, molar_mass=i.molar_mass)

    def test_successful_full_warm_state_bypasses_cold_stages_and_restores_inputs(self):
        before = asdict(self.model.inputs)
        warm = self.warm_state()
        warm["chamber_temp_guess"] += 100
        received = []
        def solve(**kw):
            received.append((copy.deepcopy(kw["x_init"]), self.model.inputs.chamber_temp_guess))
            return inner_result(self.model)
        with patch.object(self.model, "solve_cycle", side_effect=solve):
            result = self.model._evaluate_design_core(warm_state=warm)
        self.assertEqual(received, [(warm["x"], warm["chamber_temp_guess"])])
        self.assertEqual(result.convergence["warm_start"],
                         dict(requested=True, accepted=True, fallback=False))
        self.assertEqual(asdict(self.model.inputs), before)

    def test_failed_warm_attempt_is_counted_before_cold_fallback(self):
        def solve(**kw):
            return inner_result(self.model, passed=not self.model._convergence_context["warm_start"])
        with patch.object(self.model, "solve_cycle", side_effect=solve):
            result = self.model._evaluate_design_core(warm_state=self.warm_state())
        stages = result.convergence["continuation_history"]
        self.assertEqual([s["stage_ratio"] for s in stages], [1, .5, .75, 1])
        self.assertEqual([s["accepted"] for s in stages], [False, True, True, True])
        self.assertTrue(result.convergence["warm_start"]["fallback"])
        self.assertEqual(result.summary["solver_inner_solves"], 4)
        self.assertEqual(result.convergence["layers"]["continuation"]["reached_ratio"], 1)

    def test_rejected_stage_reduces_step_without_advancing_reached_ratio(self):
        failed_once = False
        def solve(**kw):
            nonlocal failed_once
            ratio = self.model._convergence_context["stage_ratio"]
            passed = not (ratio == .75 and not failed_once)
            failed_once |= not passed
            return inner_result(self.model, passed=passed)
        with patch.object(self.model, "solve_cycle", side_effect=solve):
            result = self.model._evaluate_design_core()
        stages = result.convergence["continuation_history"]
        self.assertEqual([s["stage_ratio"] for s in stages[:4]], [.5, .75, .625, .75])
        self.assertFalse(stages[1]["accepted"])
        self.assertEqual(stages[2]["from_ratio"], .5)
        self.assertEqual(stages[2]["step"], .125)
        self.assertEqual(result.convergence["layers"]["continuation"]["status"], "pass")

    def test_thermal_growth_rolls_back_and_reduces_relaxation(self):
        before = asdict(self.model.inputs)
        def solve(**kw):
            iteration = self.model._convergence_context["thermal_iteration"]
            delta = {1: 10, 2: 30}.get(iteration, 0)
            return inner_result(self.model, tc=self.model.inputs.chamber_temp_guess + delta)
        with patch.object(self.model, "solve_cycle", side_effect=solve):
            result = self.model._evaluate_design_core()
        points = result.convergence["thermal_history"]
        self.assertEqual(len(points), 3)
        self.assertFalse(points[1]["accepted"])
        self.assertLess(points[2]["relaxation"], points[1]["relaxation"])
        self.assertEqual(points[2]["temperature_input_K"], before["chamber_temp_guess"] + 2.5)
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "pass")
        self.assertEqual(asdict(self.model.inputs), before)

    def test_alternating_thermal_map_is_damped_instead_of_repeatedly_accelerated(self):
        # G(T)=17500-4T has T*=3500 K; aggressive relaxation oscillates.
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model, tc=17500 - 4 * self.model.inputs.chamber_temp_guess)):
            result = self.model._evaluate_design_core()
        trace = result.convergence["thermal_history"]
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "pass")
        self.assertLess(len(trace), 10)
        self.assertTrue(any(point.get("relaxation_action") == "oscillation_damped" for point in trace))
        self.assertLessEqual(trace[-1]["residual_K"], 5)

    def test_warm_thermal_failure_is_not_cached_as_a_converged_seed(self):
        def solve(**kw):
            warm = self.model._convergence_context["warm_start"]
            return inner_result(self.model, tc=self.model.inputs.chamber_temp_guess + (10 if warm else 0))
        with patch.object(self.model, "solve_cycle", side_effect=solve):
            result = self.model._evaluate_design_core(warm_state=self.warm_state())
        history = result.convergence["continuation_history"]
        self.assertEqual(history[0]["termination_reason"], "thermal_solver_failed")
        self.assertFalse(history[0]["accepted"])
        self.assertTrue(result.convergence["warm_start"]["fallback"])
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "pass")
        self.assertEqual(len(result.convergence["thermal_history"]), 21)

    def test_separate_core_calls_do_not_reuse_a_previous_solution(self):
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw: inner_result(self.model)):
            first = self.model._evaluate_design_core()
            second = self.model._evaluate_design_core()
        self.assertFalse(first.convergence["warm_start"]["requested"])
        self.assertFalse(second.convergence["warm_start"]["requested"])
        self.assertEqual(first.convergence["continuation_history"], second.convergence["continuation_history"])

    def test_cancellation_restores_original_inputs_and_is_not_a_failed_physics_result(self):
        before = asdict(self.model.inputs)
        def cancel(**kw):
            self.assertNotEqual(self.model.inputs.pc, before["pc"])
            raise TaskCancelled()
        with patch.object(self.model, "solve_cycle", side_effect=cancel):
            with self.assertRaises(TaskCancelled):
                self.model._evaluate_design_core()
        self.assertEqual(asdict(self.model.inputs), before)

    def test_invalid_input_validation_is_not_retried_as_a_continuation_step(self):
        before = asdict(self.model.inputs)
        with patch.object(self.model, "solve_cycle", side_effect=ValidationError("invalid submitted input")) as solve:
            with self.assertRaisesRegex(ValidationError, "submitted input"):
                self.model._evaluate_design_core()
        self.assertEqual(solve.call_count, 1)
        self.assertEqual(asdict(self.model.inputs), before)

    def test_preburner_loops_reuse_full_solution_but_new_evaluate_starts_cold(self):
        self.model.inputs.use_target_preburner_temp = 1
        before = asdict(self.model.inputs)
        calls = []
        def solve(**kw):
            context = self.model._convergence_context
            calls.append(copy.deepcopy(context))
            x, result = inner_result(self.model, tc=3800)
            offset = 10 if context["preburner_iteration"] == 1 else 0
            result.summary.update(
                ox_preburner_T_combustion_K=self.model.inputs.ox_preburner_temp_target + offset,
                fuel_preburner_T_combustion_K=self.model.inputs.fuel_preburner_temp_target + offset,
                ox_preburner_pressure_ratio=2, fuel_preburner_pressure_ratio=2)
            result.states = {"fuel_after_cooling": SimpleNamespace(temperature=350, enthalpy=-4.7e6, composition={}),
                             "lox_after_pump": SimpleNamespace(temperature=90, enthalpy=-4e5, composition={})}
            return x, result
        with patch.object(self.model, "solve_cycle", side_effect=solve), \
                patch.object(self.model, "_mr_for_target_temp", return_value=.5):
            first = self.model.evaluate(include_cooling_marching=False)
            first_count = len(calls)
            second = self.model.evaluate(include_cooling_marching=False)
        self.assertEqual(first.convergence["status"], "pass")
        self.assertEqual(second.convergence["status"], "pass")
        second_pb = [call for call in calls[:first_count] if call["preburner_iteration"] == 2]
        self.assertEqual(len(second_pb), 1)
        self.assertTrue(second_pb[0]["warm_start"])
        self.assertEqual(second_pb[0]["stage_ratio"], 1)
        self.assertFalse(calls[first_count]["warm_start"])
        self.assertEqual(calls[first_count]["stage_ratio"], .5)
        self.assertEqual(asdict(self.model.inputs), before)


class ScalingAndBackendTests(unittest.TestCase):
    def test_fraction_boundary_roundoff_does_not_hide_an_initial_evaluation(self):
        model = FFSCCycleModel(EngineInputs(use_target_preburner_mr=0,
                              use_target_preburner_temp=0))
        fuel = 6989.307040795001
        total = fuel * (1 + model.inputs.of_ratio)
        fuel, ox = model._flows_from_total(total)
        with patch.object(model, "residual_vector",
                          side_effect=lambda *args: ([0, 0, 0], result_for(model))) as residual:
            _, result = model.solve_cycle(x_init=[total, .8 * fuel, .8 * ox])
        self.assertEqual(residual.call_count, 1)
        self.assertEqual(result.summary["solver_function_evaluations"], 1)

    def test_power_scales_are_frozen_from_initial_pump_demand(self):
        model = FFSCCycleModel(EngineInputs())
        def residual(*x):
            result = result_for(model)
            result.components = {
                "ox_pump": SimpleNamespace(outputs={"power_W": x[0] * 1e5}),
                "fuel_pump": SimpleNamespace(outputs={"power_W": x[0] * 2e5})}
            return [(x[0] - 11) * model.inputs.thrust,
                    (x[1] - 2.1) * 1e6, (x[2] - 3.1) * 2e6], result
        with patch.object(model, "residual_vector", side_effect=residual):
            _, result = model.solve_cycle(x_init=[10, 2, 3])
        run = result.convergence["inner_runs"][0]
        self.assertEqual(run["residual_scales"][1:], [1e6, 2e6])
        self.assertEqual(run["physical_tolerances"][1:], [1, 2])
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")

    def test_free_split_fraction_coordinates_obey_physical_bounds_for_both_backends(self):
        for backend in ("newton", "trf"):
            with self.subTest(backend=backend):
                model = FFSCCycleModel(EngineInputs(use_target_preburner_mr=0,
                                      use_target_preburner_temp=0, nonlinear_solver=backend))
                calls = []
                def residual(*x):
                    fuel, ox = model._flows_from_total(x[0])
                    fractions = (x[1] / fuel, x[2] / ox)
                    calls.append(fractions)
                    self.assertGreaterEqual(fractions[0], .005 - 1e-14)
                    self.assertLessEqual(fractions[0], .80 + 1e-14)
                    self.assertGreaterEqual(fractions[1], .001 - 1e-14)
                    self.assertLessEqual(fractions[1], .80 + 1e-14)
                    return [(x[0] - 100) * 1e3,
                            (fractions[0] - .05) * 1e5,
                            (fractions[1] - .04) * 1e5], result_for(model)
                fuel, ox = model._flows_from_total(90)
                with patch.object(model, "residual_vector", side_effect=residual):
                    _, result = model.solve_cycle(x_init=[90, .08 * fuel, .08 * ox])
                self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
                self.assertEqual(len(calls), result.summary["solver_function_evaluations"])


if __name__ == "__main__":
    unittest.main()
