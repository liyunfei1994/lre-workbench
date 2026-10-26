"""Rated physical initialization is an evaluated seed, never convergence."""
from dataclasses import asdict
import unittest
from unittest.mock import patch

from tests.test_gg_convergence import AnalyticGG
from core import EngineInputs
from gg_cycle import GGCycleModel
from convergence import numerical_convergence


class SeededAnalyticGG(AnalyticGG):
    _initialize_design_guess = GGCycleModel._initialize_design_guess

    def _evaluate_once(self, *x):
        result = super()._evaluate_once(*x)
        result.states["fuel_after_pump"].enthalpy = -1.1e6
        result.states["lox_after_pump"].enthalpy = -2.2e5
        return result


class GGInitializationTests(unittest.TestCase):
    def test_rated_seed_uses_actual_enthalpies_and_counts_forward_once(self):
        model = SeededAnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1))
        original = asdict(model.inputs)
        with patch.object(model, "_mr_for_target_gg_temp", return_value=.4) as invert:
            result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(invert.call_count, 1)
        self.assertEqual(invert.call_args.kwargs,
                         {"fuel_enthalpy": -1.1e6, "ox_enthalpy": -2.2e5})
        self.assertEqual([r["stage_ratio"] for r in result.convergence["inner_runs"]], [1.0])
        self.assertEqual(result.summary["solver_function_evaluations"], len(model.evaluations))
        self.assertEqual(result.summary["solver_initialization_evaluations"], 1)
        self.assertEqual(result.summary["solver_inner_solves"], 1)
        seed = result.convergence["initialization_history"][0]
        self.assertFalse(seed["accepted_as_final"])
        self.assertEqual(seed["gg_mixture_ratio"], .4)
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertEqual(asdict(model.inputs), original)
        self.assertIsNone(model._gg_mr_override)

    def test_failed_seed_is_preserved_and_continuation_can_recover(self):
        model = SeededAnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1))
        with patch.object(model, "_mr_for_target_gg_temp", side_effect=ValueError("seed domain")):
            result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        seed = result.convergence["initialization_history"][0]
        self.assertEqual(seed["status"], "failed")
        self.assertIn("seed domain", seed["error"])
        self.assertEqual([r["stage_ratio"] for r in result.convergence["inner_runs"]], [.6, 1.0])
        self.assertEqual(result.summary["solver_function_evaluations"], len(model.evaluations))

    def test_fixed_mr_seed_does_not_change_mr_or_skip_failed_root(self):
        model = SeededAnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=0))
        original = asdict(model.inputs)
        solve = model.solve_cycle
        with patch.object(model, "_mr_for_target_gg_temp") as invert, \
                patch.object(model, "residual_vector", side_effect=lambda *x: (
                    [model.inputs.thrust, 0], model._evaluate_once(*x))), \
                patch.object(model, "solve_cycle", side_effect=lambda **kw: solve(max_iter=0, **kw)):
            result = model.evaluate(include_cooling_marching=False)
        invert.assert_not_called()
        self.assertEqual(numerical_convergence(result)["status"], "fail")
        self.assertEqual(result.convergence["initialization_history"][0]["status"], "seed_ready")
        self.assertFalse(result.convergence["continuation_history"][0]["accepted"])
        self.assertEqual(asdict(model.inputs), original)

    def test_budget_mode_has_no_extra_physical_seed_or_evaluations(self):
        model = SeededAnalyticGG(EngineInputs(cycle_type="gg", cooling_bartz_mode=0,
                                            use_target_gg_temp=0))
        result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(result.convergence["initialization_history"], [])
        self.assertEqual(result.summary["solver_initialization_evaluations"], 0)
        self.assertEqual(result.summary["solver_function_evaluations"], len(model.evaluations))
        self.assertEqual([r["stage_ratio"] for r in result.convergence["inner_runs"]], [.6, 1.0])


if __name__ == "__main__":
    unittest.main()
