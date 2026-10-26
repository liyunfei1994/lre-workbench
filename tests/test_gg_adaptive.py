"""Control-flow evidence for bounded continuation and full-state warm starts."""
from dataclasses import asdict
import unittest
from unittest.mock import patch

from tests.test_gg_convergence import AnalyticGG
from core import EngineInputs
from convergence import numerical_convergence
from task_control import TaskCancelled
from nonlinear_solver import solve_nonlinear


class GGAdaptiveTests(unittest.TestCase):
    def test_work_budget_keeps_last_evaluated_state_for_both_backends(self):
        for backend in ("newton", "trf"):
            model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=0,
                                           nonlinear_solver=backend))
            with patch.object(model, "residual_vector", side_effect=lambda *x: (
                    [(x[0] - 90) * 1e5, 0], model._evaluate_once(*x))), \
                    patch("gg_cycle.solve_nonlinear", side_effect=lambda *args, **kwargs:
                          solve_nonlinear(*args, **kwargs, max_nfev=1)):
                x, result = model.solve_cycle(x_init=[100, .03])
            self.assertEqual(x, [100, .03])
            self.assertEqual(len(model.evaluations), 1)
            self.assertEqual(result.convergence["layers"]["inner"]["status"], "fail")
            self.assertEqual(result.convergence["layers"]["inner"]["termination_reason"], "max_evaluations")
            self.assertEqual(result.convergence["inner_runs"][0]["evaluation_budget"], 1)

    def test_continuation_halves_rejected_increment_and_recovers(self):
        model = AnalyticGG()
        base_pc = model.inputs.pc
        accepted_ratio = 0.0
        original_solve = model.solve_cycle

        def residual(*x):
            ratio = model.inputs.pc / base_pc
            failed = ratio - accepted_ratio > .26
            return [model.inputs.thrust if failed else 0.0, 0.0], model._evaluate_once(*x)

        def solve(**kw):
            nonlocal accepted_ratio
            output = original_solve(max_iter=0, **kw)
            if output[1].convergence["layers"]["inner"]["status"] == "pass":
                accepted_ratio = model.inputs.pc / base_pc
            return output

        with patch.object(model, "residual_vector", side_effect=residual), \
                patch.object(model, "solve_cycle", side_effect=solve):
            result = model.evaluate(include_cooling_marching=False)
        history = result.convergence["continuation_history"]
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertTrue(any(not p["accepted"] for p in history))
        for first, second in zip(history, history[1:]):
            if not first["accepted"]:
                self.assertAlmostEqual(second["step"], first["step"] / 2)
                self.assertEqual(second["from_ratio"], first["from_ratio"])
        self.assertEqual(result.convergence["layers"]["continuation"]["reached_ratio"], 1)
        self.assertEqual(result.summary["solver_function_evaluations"], len(model.evaluations))

    def test_failed_warm_state_falls_back_without_losing_attempt(self):
        model = AnalyticGG()
        original = asdict(model.inputs)
        state = {"x": [100, .03], "chamber_temp_guess": 4000,
                 "chamber_gamma": 1.2, "molar_mass": .022}
        residual_original = model.residual_vector

        def residual(*x):
            if model.inputs.chamber_temp_guess == 4000:
                raise RuntimeError("warm thermodynamic state is outside domain")
            return residual_original(*x)

        with patch.object(model, "residual_vector", side_effect=residual):
            result = model._evaluate_design_core(warm_state=state)
        history = result.convergence["continuation_history"]
        self.assertTrue(history[0]["warm_start"])
        self.assertFalse(history[0]["accepted"])
        self.assertEqual(history[1]["stage_ratio"], .6)
        self.assertTrue(result.convergence["warm_start"]["fallback"])
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertEqual(result.summary["solver_function_evaluations"], len(model.evaluations) + 1)
        self.assertEqual(asdict(model.inputs), original)
        self.assertEqual(state["chamber_temp_guess"], 4000)

    def test_complete_warm_state_is_reused_then_cleared_between_evaluations(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1),
                           feedback=lambda m: (3600.0, 1.2, 22.0))
        original = asdict(model.inputs)
        evaluate_once = model._evaluate_once

        def temperature(*x):
            result = evaluate_once(*x)
            result.summary["gg_T_combustion_K"] = 1000 * model._gg_mr
            return result

        with patch.object(model, "_evaluate_once", side_effect=temperature), \
                patch.object(model, "_mr_for_target_gg_temp", return_value=.85):
            first = model.evaluate(include_cooling_marching=False)
            first_work = len(model.evaluations)
            second = model.evaluate(include_cooling_marching=False)
        self.assertEqual(numerical_convergence(first)["status"], "pass")
        self.assertEqual(numerical_convergence(second)["status"], "pass")
        history = first.convergence["thermal_history"]
        later = [p for p in history if p["preburner_iteration"] > 1]
        self.assertTrue(later)
        self.assertTrue(all(abs(p["temperature_guess_K"] - 3600) <= 5 for p in later))
        self.assertGreater(first.summary["solver_warm_start_successes"], 0)
        self.assertEqual(second.convergence["continuation_history"][0]["stage_ratio"], .6)
        self.assertFalse(second.convergence["continuation_history"][0]["warm_start"])
        self.assertEqual(len(model.evaluations), 2 * first_work)
        self.assertEqual(asdict(model.inputs), original)

    def test_thermal_growth_is_recorded_and_relaxation_is_reduced(self):
        model = AnalyticGG(feedback=lambda m: (17500 - 4 * m.inputs.chamber_temp_guess,
                                               m.inputs.chamber_gamma, m.inputs.molar_mass))
        result = model.evaluate(include_cooling_marching=False)
        history = result.convergence["thermal_history"]
        rejected = [i for i, point in enumerate(history) if not point["accepted"]]
        self.assertTrue(rejected)
        for i in rejected:
            if i + 1 < len(history):
                self.assertLess(history[i + 1]["relaxation"], history[i]["relaxation"])
        self.assertEqual(numerical_convergence(result)["status"], "pass")

    def test_cancellation_propagates_and_restores_inputs(self):
        model = AnalyticGG()
        original = asdict(model.inputs)
        with patch.object(model, "residual_vector", side_effect=TaskCancelled("stop")):
            with self.assertRaises(TaskCancelled):
                model.evaluate(include_cooling_marching=False)
        self.assertEqual(asdict(model.inputs), original)

    def test_single_and_dual_backends_use_pump_demand_as_fixed_scale(self):
        for dual in (False, True):
            for backend in ("newton", "trf"):
                model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=0,
                    nonlinear_solver=backend, gg_layout="dual_shaft" if dual else "single_shaft"))
                def residual(*x):
                    result = model._evaluate_once(*x)
                    result.summary.update(pump_power_MW=12, ox_pump_power_MW=7, fuel_pump_power_MW=5)
                    return [0.0] * (3 if dual else 2), result
                with patch.object(model, "residual_vector", side_effect=residual):
                    _, result = model.solve_cycle()
                run = result.convergence["inner_runs"][0]
                self.assertEqual(run["residual_scales"][1:], [7e6, 5e6] if dual else [12e6])
                self.assertEqual(run["nfev"], 1)


if __name__ == "__main__":
    unittest.main()
