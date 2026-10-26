"""Equation acceptance, scaling, bounded probes, and trace integrity."""
from pathlib import Path
import math
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nonlinear_solver import solve_nonlinear
from task_control import CancellationToken, TaskCancelled, cancellation_scope


def solve(fun, x0=(1,), lower=(-10,), upper=(10,), **kw):
    defaults = dict(residual_scales=[1] * len(x0), variable_scales=[1] * len(x0))
    defaults.update(kw)
    return solve_nonlinear(fun, x0, lower, upper, **defaults)


class NewtonTests(unittest.TestCase):
    def test_analytic_root_records_only_initial_and_accepted_points(self):
        calls, events = [], []
        def fun(x):
            calls.append(list(x))
            return [x[0] * x[0] - 2], {"x": x[0]}
        result = solve(fun, names=["energy"], progress_callback=events.append)
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "tolerance_met")
        self.assertAlmostEqual(result.x[0], math.sqrt(2), places=4)
        self.assertEqual(result.nfev, len(calls))
        self.assertEqual(result.nfev, 1 + 2 * result.iterations)
        self.assertEqual(result.njev, result.iterations)
        self.assertEqual(result.points, events)
        self.assertEqual(result.payload["x"], result.x[0])
        self.assertEqual(result.points[-1]["x"], result.x)
        self.assertEqual(set(result.points[0]["residuals"]), {"energy"})
        self.assertTrue(all(b["merit"] < a["merit"] for a, b in zip(result.points, result.points[1:])))

    def test_same_problem_in_different_units_has_same_iterates(self):
        a = solve(lambda x: ([x[0] ** 2 - 2, x[1] - 3], x), [1, 1], [-10] * 2, [10] * 2)
        b = solve(lambda x: ([1e7 * ((x[0] / 1e6) ** 2 - 2),
                             1e-9 * (x[1] / 1e-5 - 3)], x),
                  [1e6, 1e-5], [-1e7, -1e-4], [1e7, 1e-4],
                  residual_scales=[1e7, 1e-9], variable_scales=[1e6, 1e-5])
        self.assertTrue(a.passed and b.passed)
        self.assertEqual(a.iterations, b.iterations)
        np.testing.assert_allclose(np.array(b.x) / [1e6, 1e-5], a.x, rtol=1e-7)
        np.testing.assert_allclose([p["residual_norm"] for p in b.points],
                                   [p["residual_norm"] for p in a.points], rtol=1e-5)

    def test_upper_bound_uses_inward_probe_and_all_calls_respect_bounds(self):
        calls = []
        def fun(x):
            calls.append(x[0])
            self.assertGreaterEqual(x[0], 0)
            self.assertLessEqual(x[0], 1)
            return [x[0] - 0.25], x[0]
        result = solve(fun, [1], [0], [1], diff_steps=[0.01])
        self.assertTrue(result.passed)
        self.assertEqual(calls[1], 0.99)
        self.assertAlmostEqual(result.x[0], 0.25)

    def test_initial_guess_is_projected_and_zero_budget_still_evaluates_it(self):
        result = solve(lambda x: ([x[0] - 0.5], x), [5], [0], [1], max_iter=0)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "max_iterations")
        self.assertEqual(result.x, [1])
        self.assertEqual(result.iterations, 0)
        self.assertEqual(result.nfev, 1)
        self.assertEqual(len(result.points), 1)

    def test_last_allowed_step_can_pass(self):
        result = solve(lambda x: ([x[0] - 2], x), max_iter=1)
        self.assertTrue(result.passed)
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.nfev, 3)

    def test_rank_deficient_consistent_equations_can_converge(self):
        result = solve(lambda x: ([x[0] + x[1] - 4, 2 * (x[0] + x[1] - 4)], x),
                       [1, 1], [-10] * 2, [10] * 2)
        self.assertTrue(result.passed)
        self.assertAlmostEqual(sum(result.x), 4)

    def test_coupled_curved_valley_uses_merit_not_max_component_for_steps(self):
        result = solve(lambda x: ([10 * (x[1] - x[0] ** 2), 1 - x[0]], x),
                       [-1.2, 1], [-3, -3], [3, 3])
        self.assertTrue(result.passed)
        np.testing.assert_allclose(result.x, [1, 1], atol=1e-4)
        self.assertGreater(result.rejected_steps, 0)
        self.assertTrue(all(b["merit"] < a["merit"] for a, b in zip(result.points, result.points[1:])))
        # The infinity norm displayed for component-wise acceptance is not
        # the smooth merit used by Armijo; a temporary rise is legitimate.
        self.assertTrue(any(b["residual_norm"] > a["residual_norm"]
                            for a, b in zip(result.points, result.points[1:])))

    def test_no_root_at_active_bound_does_not_pass(self):
        result = solve(lambda x: ([x[0] - 2], x), [0.5], [0], [1])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "stationary_nonzero_residual")
        self.assertEqual(result.x, [1])
        self.assertEqual(result.residuals, [-1])

    def test_invalid_trials_backtrack_without_losing_last_valid_state(self):
        calls = []
        def fun(x):
            calls.append(x[0])
            if x[0] > 0.6:
                raise ValueError("outside model domain")
            return [x[0] - 1], {"actual_x": x[0]}
        result = solve(fun, [0.4], [0], [2], diff_steps=[1e-6], max_iter=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.rejected_steps, 2)
        self.assertAlmostEqual(result.x[0], 0.55)
        self.assertEqual(result.payload["actual_x"], result.x[0])
        self.assertEqual(result.nfev, len(calls))
        self.assertEqual(result.nfev, 5)
        self.assertEqual(len(result.points), 2)

    def test_no_improvement_never_accepts_tiny_unchecked_fallback(self):
        def fun(x):
            # Only the finite-difference probe suggests a downhill direction;
            # every positive step genuinely increases the evaluated merit.
            return [1 - x[0] if x[0] <= 0 else 1 + x[0]], x[0]
        result = solve(fun, [0], [-2], [2], diff_steps=[0.001])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "line_search_failed")
        self.assertEqual(result.x, [0])
        self.assertEqual(result.payload, 0)
        self.assertEqual(len(result.points), 1)
        self.assertGreater(result.rejected_steps, 0)

    def test_initial_nan_or_exception_has_no_invented_trace(self):
        for fun in (lambda x: ([float("nan")], x),
                    lambda x: (_ for _ in ()).throw(RuntimeError("model failed"))):
            with self.subTest(fun=fun):
                result = solve(fun)
                self.assertFalse(result.passed)
                self.assertEqual(result.reason, "initial_evaluation_failed")
                self.assertEqual(result.points, [])
                self.assertIsNone(result.payload)
                self.assertEqual(result.nfev, 1)

    def test_failed_jacobian_preserves_base_and_counts_both_probes(self):
        def fun(x):
            return ([1 if x[0] == 1 else math.nan], {"x": x[0]})
        result = solve(fun)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "jacobian_failed")
        self.assertEqual(result.x, [1])
        self.assertEqual(result.payload, {"x": 1})
        self.assertEqual(result.nfev, 3)
        self.assertEqual(result.njev, 1)

    def test_failed_forward_probe_can_use_valid_backward_probe(self):
        def fun(x):
            if x[0] > 1:
                return [math.nan], None
            return [x[0] - 0.5], x[0]
        result = solve(fun, diff_steps=[0.01])
        self.assertTrue(result.passed)
        self.assertEqual(result.nfev, 4)
        self.assertAlmostEqual(result.x[0], 0.5)

    def test_cancellation_is_not_a_model_failure(self):
        for backend in ("newton", "trf"):
            with self.subTest(backend=backend):
                token = CancellationToken()
                def fun(x):
                    token.cancel()
                    return [1], None
                with cancellation_scope(token), self.assertRaises(TaskCancelled):
                    solve(fun, backend=backend)

    def test_invalid_configuration_is_rejected_before_model_call(self):
        for kw in (dict(residual_scales=[0]), dict(variable_scales=[math.nan]),
                   dict(diff_steps=[0]), dict(tol=0), dict(backend="unknown"),
                   dict(max_iter=-1), dict(max_iter=True), dict(names=[])):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                solve(lambda x: self.fail("configuration must be checked first"), **kw)


class TrustRegionTests(unittest.TestCase):
    def test_trf_uses_same_component_acceptance_and_counts_all_physics_calls(self):
        calls = []
        def fun(x):
            calls.append(list(x))
            return [1e6 * (x[0] ** 2 - 2)], {"accepted": x[0]}
        result = solve(fun, backend="trf", residual_scales=[1e6], names=["power"])
        self.assertTrue(result.passed)
        self.assertLessEqual(abs(result.residuals[0] / 1e6), 1e-4)
        self.assertEqual(result.nfev, len(calls))
        self.assertGreater(result.nfev, len(result.points))
        self.assertGreater(result.njev, 0)
        self.assertEqual(result.points[-1]["x"], result.x)
        self.assertEqual(result.payload["accepted"], result.x[0])
        self.assertEqual(len({tuple(p["x"]) for p in result.points}), len(result.points))

    def test_optimizer_success_at_nonzero_minimum_cannot_pass_equations(self):
        result = solve(lambda x: ([x[0] - 2], x), [0.5], [0], [1], backend="trf")
        self.assertFalse(result.passed)
        self.assertEqual(result.backend_status, 1)
        self.assertEqual(result.reason, "stationary_nonzero_residual")
        self.assertGreater(result.points[-1]["residual_norm"], 0.9)
        self.assertLessEqual(result.x[0], 1)

    def test_trf_accepted_iteration_budget_and_last_payload(self):
        result = solve(lambda x: ([x[0] ** 2 - 2], {"x": x[0]}), backend="trf", max_iter=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "max_iterations")
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.payload["x"], result.x[0])
        self.assertEqual(result.points[-1]["x"], result.x)

    def test_trf_invalid_trial_preserves_last_accepted_payload(self):
        calls = []
        def fun(x):
            calls.append(x[0])
            if x[0] > 1.01:
                raise RuntimeError("outside model domain")
            return [x[0] - 2], {"x": x[0]}
        result = solve(fun, backend="trf", diff_steps=[1e-5])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "evaluation_failed")
        self.assertEqual(result.x, [1])
        self.assertEqual(result.payload, {"x": 1})
        self.assertEqual(result.nfev, len(calls))
        self.assertEqual(len(result.points), 1)
        self.assertEqual(result.rejected_steps, 1)

    def test_trf_from_bound_has_bounded_probes_and_reaches_an_interior_root(self):
        calls = []
        def fun(x):
            calls.append(x[0])
            self.assertGreaterEqual(x[0], 0)
            self.assertLessEqual(x[0], 10)
            return [x[0] - 2], x[0]
        result = solve(fun, [0], [0], [10], backend="trf", diff_steps=[1e-5])
        self.assertTrue(result.passed)
        self.assertEqual(result.nfev, len(calls))
        self.assertEqual(result.points[0]["x"], [0])
        self.assertEqual(result.points[-1]["x"], result.x)


class EvaluationBudgetTests(unittest.TestCase):
    def test_default_budget_is_identical_for_backends_and_counts_variables(self):
        for backend in ("newton", "trf"):
            for dimensions, expected in ((2, 306), (3, 408)):
                with self.subTest(backend=backend, dimensions=dimensions):
                    result = solve(lambda x: ([0] * dimensions, x),
                                   [1] * dimensions, [-10] * dimensions, [10] * dimensions,
                                   backend=backend, max_iter=50)
                    self.assertTrue(result.passed)
                    self.assertEqual(result.evaluation_budget, expected)
                    self.assertEqual(result.nfev, 1)

    def test_initial_only_budget_retains_actual_initial_state(self):
        for backend in ("newton", "trf"):
            calls = []
            def fun(x):
                calls.append(x[:])
                return [x[0] - 2], {"x": x[0]}
            with self.subTest(backend=backend):
                result = solve(fun, max_nfev=1, backend=backend)
                self.assertFalse(result.passed)
                self.assertEqual(result.reason, "max_evaluations")
                self.assertEqual(result.nfev, len(calls))
                self.assertEqual(result.nfev, 1)
                self.assertEqual(result.payload, {"x": 1})
                self.assertEqual(result.x, [1])
                self.assertEqual(len(result.points), 1)
                self.assertEqual(result.rejected_steps, 0)

    def test_budget_mid_jacobian_never_calls_remaining_probes(self):
        for backend in ("newton", "trf"):
            calls = []
            def fun(x):
                calls.append(x[:])
                return [value - 2 for value in x], {"x": x[:]}
            with self.subTest(backend=backend):
                result = solve(fun, [1] * 3, [-10] * 3, [10] * 3,
                               max_nfev=2, backend=backend)
                self.assertFalse(result.passed)
                self.assertEqual(result.reason, "max_evaluations")
                self.assertEqual(result.nfev, 2)
                self.assertEqual(len(calls), 2)
                self.assertEqual(result.njev, 1)
                self.assertEqual(result.x, [1] * 3)
                self.assertEqual(result.payload, {"x": [1] * 3})
                self.assertEqual(len(result.points), 1)

    def test_budget_during_backtracking_counts_only_attempted_model_calls(self):
        calls = []
        def fun(x):
            calls.append(x[:])
            return [1 + abs(x[0])], {"x": x[0]}
        result = solve(fun, [0], [-2], [2], diff_steps=[.001], max_nfev=4)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "max_evaluations")
        self.assertEqual(result.nfev, 4)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result.rejected_steps, 2)
        self.assertEqual(result.x, [0])
        self.assertEqual(len(result.points), 1)

    def test_last_budgeted_call_can_be_an_accepted_root(self):
        result = solve(lambda x: ([x[0] - 2], x[:]), max_nfev=3)
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "tolerance_met")
        self.assertEqual(result.nfev, 3)
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.points[-1]["x"], result.x)

    def test_trf_budget_retains_last_accepted_not_a_jacobian_probe(self):
        calls = []
        def fun(x):
            calls.append(x[:])
            return [x[0] - 2], {"x": x[0]}
        result = solve(fun, max_nfev=4, backend="trf")
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "max_evaluations")
        self.assertEqual(result.nfev, 4)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result.points[-1]["x"], result.x)
        self.assertEqual(result.payload["x"], result.x[0])
        self.assertNotEqual(calls[-1], result.x)

    def test_zero_iteration_limit_and_cancellation_keep_their_meaning(self):
        for backend in ("newton", "trf"):
            with self.subTest(backend=backend):
                result = solve(lambda x: ([1], x), max_iter=0, max_nfev=1, backend=backend)
                self.assertEqual(result.reason, "max_iterations")
                self.assertEqual(result.nfev, 1)
                self.assertEqual(result.iterations, 0)
                token = CancellationToken()
                calls = []
                def fun(x):
                    calls.append(x[:])
                    if len(calls) == 2:
                        token.cancel()
                    return [x[0] - 2], x
                with cancellation_scope(token), self.assertRaises(TaskCancelled):
                    solve(fun, max_nfev=2, backend=backend)
                self.assertEqual(len(calls), 2)

    def test_invalid_budget_never_calls_the_model(self):
        for budget in (0, -1, True, math.nan, math.inf, 1.5):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                solve(lambda x: self.fail("invalid budget must be rejected first"), max_nfev=budget)


if __name__ == "__main__":
    unittest.main()
