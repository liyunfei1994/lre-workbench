"""Numerical behavior and evidence for safeguarded quasi-Newton solves."""
from pathlib import Path
import json
import math
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.solvers.nonlinear import ModelDomainError, initial_evaluation_diagnostics, solve_nonlinear
from lre_workbench.runtime.tasks import CancellationToken, TaskCancelled, cancellation_scope


def solve(fun, x=(1.,), *, backend="broyden", **kwargs):
    options = dict(residual_scales=[1.] * len(x), variable_scales=[1.] * len(x),
                   lower=[-10.] * len(x), upper=[10.] * len(x))
    options.update(kwargs)
    return solve_nonlinear(fun, x, backend=backend, **options)


class BroydenTests(unittest.TestCase):
    def test_same_root_and_tolerance_with_fewer_actual_calls_on_smooth_system(self):
        results = {}
        for method in ("newton", "broyden", "trf"):
            calls = []
            def model(x):
                calls.append(x[:])
                return [x[0] ** 2 - 2, x[1] ** 2 - 3], x[:]
            result = solve(model, [1., 1.], backend=method, residual_tolerances=[1e-8] * 2)
            self.assertTrue(result.passed, (method, result.reason))
            np.testing.assert_allclose(result.x, [math.sqrt(2), math.sqrt(3)], atol=1e-8)
            self.assertEqual(len(calls), result.nfev)
            self.assertEqual(sum(result.diagnostics["evaluation_counts"].values()), result.nfev)
            self.assertLessEqual(result.diagnostics["acceptance_norm"], 1)
            self.assertTrue(all(row["passed"] for row in result.diagnostics["equations"]))
            results[method] = result
        self.assertLess(results["broyden"].nfev, results["newton"].nfev)
        self.assertGreater(results["broyden"].diagnostics["approximate_updates"], 0)
        self.assertLess(results["broyden"].njev, results["newton"].njev)

    def test_refresh_interval_rebuilds_without_changing_acceptance(self):
        result = solve(lambda x: ([x[0] ** 2 - 2], x), broyden_refresh_interval=2, tol=1e-10)
        self.assertTrue(result.passed)
        reasons = [e["reason"] for e in result.diagnostics["events"] if e["kind"] == "jacobian_rebuild"]
        self.assertIn("refresh_interval", reasons)
        self.assertGreater(result.diagnostics["jacobian_rebuilds"], 1)

    def test_curved_valley_refreshes_on_poor_progress_and_still_converges(self):
        result = solve(lambda x: ([10 * (x[1] - x[0] ** 2), 1 - x[0]], x), [-1.2, 1.],
                       lower=[-3., -3.], upper=[3., 3.], max_iter=100)
        self.assertTrue(result.passed)
        self.assertTrue(any(e["reason"] == "poor_progress" for e in result.diagnostics["events"]))

    def test_failed_approximation_retries_full_jacobian_before_failure(self):
        # A quadratic with a nonzero minimum: after the first accepted secant,
        # its direction is misleading. Rebuilding must not invent a root.
        result = solve(lambda x: ([x[0] ** 2 + x[0] + 1], x), [0.], max_nfev=200)
        self.assertFalse(result.passed)
        reasons = [e["reason"] for e in result.diagnostics["events"] if e["kind"] == "jacobian_rebuild"]
        self.assertIn("approximate_line_search_failed", reasons)
        self.assertGreater(abs(result.residuals[0]), .7)
        self.assertNotEqual(result.reason, "physical_no_solution")

    def test_discarded_singular_update_does_not_replace_valid_matrix_evidence(self):
        def model(x):
            residual = (x[0] + x[1]) ** 2 - 16
            return [residual, 2 * residual], x
        result = solve(model, [1., 1.], max_iter=1)
        self.assertFalse(result.passed)
        self.assertTrue(any(e["reason"] == "ill_conditioned_update" for e in result.diagnostics["events"]))
        self.assertEqual(result.diagnostics["jacobian"]["source"], "finite_difference")
        self.assertEqual(result.diagnostics["jacobian"]["at_x"], [1., 1.])
        self.assertEqual(result.diagnostics["approximate_updates"], 0)

    def test_broyden_budget_and_cancellation_count_actual_work(self):
        calls = []
        def model(x):
            calls.append(x[:])
            return [x[0] ** 2 - 2], x[:]
        result = solve(model, max_nfev=4)
        self.assertEqual(result.reason, "max_evaluations")
        self.assertEqual(result.nfev, len(calls))
        self.assertEqual(result.nfev, 4)
        self.assertEqual(result.payload, result.x)
        token = CancellationToken()
        def cancelled(x):
            token.cancel()
            return [1.], None
        with cancellation_scope(token), self.assertRaises(TaskCancelled):
            solve(cancelled)


class ScalingAndDiagnosisTests(unittest.TestCase):
    def test_physical_acceptance_is_independent_of_numeric_residual_scale(self):
        for backend in ("newton", "broyden", "trf"):
            with self.subTest(backend=backend):
                result = solve(lambda x: ([x[0] - 2], x), backend=backend,
                               residual_scales=[1e12], residual_tolerances=[1e-8], max_iter=0)
                self.assertFalse(result.passed)
                self.assertLess(result.points[0]["residual_norm"], 1e-4)
                self.assertEqual(result.points[0]["acceptance_norm"], 1e8)
                self.assertEqual(result.diagnostics["equations"][0]["absolute_tolerance"], 1e-8)
        for scale in (1., 1e3):
            result = solve(lambda x: ([x[0] ** 2 - 2], x), residual_scales=[scale],
                           residual_tolerances=[1e-9])
            self.assertTrue(result.passed)
            self.assertLessEqual(abs(result.residuals[0]), 1e-9)

    def test_variable_limits_and_unit_changes_preserve_accepted_steps(self):
        for method in ("newton", "broyden"):
            a = solve(lambda x: ([x[0] ** 2 - 2], x), backend=method,
                      max_steps=[.1], residual_tolerances=[1e-8], diff_steps=[1e-5])
            b = solve(lambda x: ([1e4 * ((x[0] / 1e6) ** 2 - 2)], x), [1e6], backend=method,
                      lower=[-1e7], upper=[1e7], max_steps=[1e5],
                      residual_tolerances=[1e-4], residual_scales=[1e4],
                      variable_scales=[1e6], diff_steps=[10.])
            self.assertTrue(a.passed and b.passed)
            np.testing.assert_allclose(np.array([p["x"] for p in a.points]),
                                       np.array([p["x"] for p in b.points]) / 1e6, rtol=1e-8)
            self.assertTrue(all(abs(q["x"][0] - p["x"][0]) <= .1 + 1e-12
                                for p, q in zip(a.points, a.points[1:])))
            self.assertGreater(a.diagnostics["step_reduction_counts"]["variable_step_limit"], 0)

    def test_trust_region_does_not_claim_to_enforce_unavailable_step_limits(self):
        with self.assertRaisesRegex(ValueError, "TRF does not support"):
            solve(lambda x: self.fail("must fail before model call"), backend="trf", max_steps=[1.])
        result = solve(lambda x: ([0.], x), backend="trf")
        self.assertEqual(result.diagnostics["step_reduction_observability"], "backend_internal_unavailable")

    def test_rank_deficiency_names_coupled_variables_and_equations(self):
        result = solve(lambda x: ([x[0] + x[1] - 4, 2 * (x[0] + x[1] - 4)], x), [1., 1.],
                       names=["balance_one", "balance_two"], variable_names=["flow", "speed"])
        self.assertTrue(result.passed)
        jac = result.diagnostics["jacobian"]
        self.assertEqual(jac["rank"], 1)
        self.assertIsNone(jac["condition_number"])
        self.assertEqual(set(jac["weak_variables"]), {"flow", "speed"})
        self.assertEqual(set(jac["dependent_equations"]), {"balance_one", "balance_two"})
        self.assertTrue(jac["local_only"])

    def test_model_domain_failure_preserves_explicit_and_probe_variables(self):
        def model(x):
            if x[0] != 1:
                raise ModelDomainError("declared map domain", component="test component",
                                       variable="pressure", equation="capacity")
            return [1.], {"x": x[0]}
        result = solve(model, variable_names=["flow"])
        failure = result.diagnostics["failures"][0]
        self.assertEqual(failure["stage"], "finite_difference")
        self.assertEqual(failure["variable"], "pressure")
        self.assertEqual(failure["probe_variable"], "flow")
        self.assertEqual(failure["component"], "test component")
        self.assertEqual(failure["equation"], "capacity")
        self.assertEqual(result.diagnostics["domain_status"], "violation_observed")
        self.assertEqual(result.x, [1.])
        self.assertEqual(result.nfev, 3)

    def test_generic_failures_are_not_reported_as_component_domain_violations(self):
        result = solve(lambda x: ([float("nan")], x), names=["energy"])
        self.assertEqual(result.diagnostics["domain_status"], "not_independently_checked")
        self.assertEqual(result.diagnostics["failures"][0]["equation"], "energy")
        self.assertEqual(result.diagnostics["failures"][0]["kind"], "model_evaluation_failed")
        self.assertIsNone(result.diagnostics["equations"][0]["raw_residual"])
        json.dumps(result.diagnostics, allow_nan=False)

    def test_opt_in_shorter_difference_probe_recovers_narrow_valid_domain(self):
        calls = []
        def model(x):
            calls.append(x[0])
            if abs(x[0] - 1.) > 1e-4:
                raise ModelDomainError("narrow domain")
            return [x[0] - 1.00003], x
        result = solve(model, diff_steps=[.01], diff_step_shrink_attempts=8, tol=1e-10)
        self.assertTrue(result.passed)
        self.assertLess(abs(result.diagnostics["variables"][0]["diff_step"]), 1e-4)
        self.assertEqual(len(calls), result.nfev)
        self.assertGreater(result.diagnostics["total_failure_count"], 0)

    def test_initialization_failure_schema_has_no_invented_physical_results(self):
        diagnostic = initial_evaluation_diagnostics(ValueError("missing coefficient"), [1.], backend="trf")
        self.assertEqual(diagnostic["model_evaluations"], 1)
        self.assertIsNone(diagnostic["elapsed_seconds"])
        self.assertIsNone(diagnostic["jacobian"])
        self.assertEqual(diagnostic["equations"], [])
        self.assertEqual(diagnostic["failures"][0]["stage"], "initialization")
        json.dumps(diagnostic, allow_nan=False)

    def test_bounded_history_reports_truncation_without_losing_total_counts(self):
        result = solve(lambda x: ([x[0] - 100.], x), backend="newton",
                       max_steps=[.001], max_iter=1005)
        diagnostic = result.diagnostics
        self.assertEqual(result.iterations, 1005)
        self.assertEqual(len(diagnostic["events"]), 1000)
        self.assertEqual(diagnostic["total_event_count"],
                         len(diagnostic["events"]) + diagnostic["truncated_records"]["events"])
        self.assertEqual(sum(diagnostic["step_reduction_counts"].values()),
                         len(diagnostic["step_reductions"]) + diagnostic["truncated_records"]["step_reductions"])
        self.assertIn("variable_step_limit", result.points[-1]["reduction_reasons"])


if __name__ == "__main__":
    unittest.main()
