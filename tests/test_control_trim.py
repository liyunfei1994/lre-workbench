"""Nested numerical trim never implies physical feasibility from solver status."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lre_workbench.systems.control_trim import ControlSpec, TargetSpec, SCENARIOS, run_trim_framework_check, solve_trim
from lre_workbench.runtime.tasks import CancellationToken, TaskCancelled, cancellation_scope


def control(name="u", **kwargs):
    return ControlSpec(**(dict(name=name, label=name, unit="1", lower=0, upper=1, initial=.2) | kwargs))


def target(name="x", **kwargs):
    return TargetSpec(**(dict(name=name, label=name, unit="1", value=.6, tolerance=1e-6) | kwargs))


def inner(outputs):
    return {"status": "converged", "valid": True, "domain_valid": True, "outputs": outputs,
            "iterations": 1, "residual_norm": 0., "points": []}


def model(evaluate=None, **kwargs):
    return SimpleNamespace(**(dict(model_id="test_only", controls=(control(),), targets=(target(),),
        solve_inner=evaluate or (lambda values: inner({"x": values["u"]}))) | kwargs))


class ControlTrimTests(unittest.TestCase):
    def test_actual_nested_check_converges_with_bound_preserving_ledger(self):
        report = run_trim_framework_check()
        self.assertEqual(report["status"], "converged", report)
        self.assertEqual(report["analysis_type"], "control_trim_framework_check")
        self.assertEqual(report["scope"], "numerical_framework_only")
        self.assertIs(report["is_engine_result"], False)
        self.assertEqual(report["dof"]["rank"], 2)
        self.assertEqual(report["dof"]["checked_at_iteration"], report["points"][-1]["iteration"])
        self.assertEqual(report["dof"]["inner_evaluation_id"], report["points"][-1]["inner_evaluation_id"])
        self.assertEqual(report["dof"]["controls"], report["final_controls"])
        self.assertGreater(report["iterations"], 1)
        self.assertGreater(report["nfev"], len(report["points"]))
        self.assertEqual(report["nfev"], len(report["evaluations"]))
        self.assertLessEqual(report["points"][-1]["residual_norm"], 1)
        self.assertAlmostEqual(report["final_controls"]["u_a"], .5625, places=7)
        self.assertAlmostEqual(report["final_controls"]["u_b"], .3225, places=7)
        self.assertEqual([p["iteration"] for p in report["points"]], list(range(len(report["points"]))))
        for entry in report["evaluations"]:
            self.assertEqual(entry["status"], "valid")
            self.assertGreater(entry["inner_iterations"], 1)
            self.assertGreater(len(entry["inner_points"]), 1)
            self.assertLessEqual(entry["inner_residual_norm"], 1)
            self.assertTrue(all(0 <= value <= 1 for value in entry["controls"].values()))
        for point in report["points"]:
            entry = report["evaluations"][point["inner_evaluation_id"] - 1]
            self.assertNotEqual(entry["phase"], "sensitivity")
            self.assertEqual(point["controls"], entry["controls"])
            self.assertEqual(point["outputs"], entry["outputs"])
        json.dumps(report, allow_nan=False)

    def test_only_algebra_case_can_issue_global_infeasibility_evidence(self):
        ordinary = solve_trim(model(targets=(target(value=2),)))
        self.assertEqual(ordinary["status"], "target_not_met")
        self.assertEqual(ordinary["reason"], "bound_limited")
        self.assertIsNone(ordinary["evidence"])
        self.assertEqual(ordinary["final_controls"]["u"], 1)
        self.assertGreater(ordinary["points"][-1]["residual_norm"], 1)
        report = run_trim_framework_check("bound_limited")
        self.assertEqual(report["status"], "target_unreachable")
        self.assertEqual(report["evidence"]["kind"], "analytic_global_bound")
        self.assertEqual(report["evidence"]["model_id"], report["model_id"])
        self.assertEqual(report["evidence"]["scenario"], "bound_limited")
        self.assertLess(report["evidence"]["upper"] + report["evidence"]["tolerance"], report["evidence"]["target"])
        self.assertTrue(report["points"][-1]["active_bounds"])

    def test_failed_inner_never_supplies_outer_residual_or_success_outputs(self):
        report = run_trim_framework_check("inner_failure")
        self.assertEqual(report["status"], "numerical_failure")
        self.assertEqual(report["reason"], "inner_evaluation_failed")
        self.assertEqual(len(report["points"]), 1)
        self.assertIsNone(report["final_controls"])
        self.assertIsNone(report["final_outputs"])
        self.assertEqual(report["last_valid_point"], report["points"][-1])
        failure = report["evaluations"][-1]
        self.assertEqual(failure["phase"], "trial")
        self.assertEqual(failure["status"], "failed")
        self.assertNotIn("outputs", failure)
        self.assertNotIn("residuals", failure)
        self.assertTrue(failure["error"])
        json.dumps(report, allow_nan=False)

    def test_rank_deficiency_is_distinct_from_control_count_and_infeasibility(self):
        report = run_trim_framework_check("rank_deficient")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "rank_deficient")
        self.assertEqual(report["dof"]["control_count"], report["dof"]["target_count"])
        self.assertEqual(report["dof"]["rank"], 1)
        self.assertEqual(report["dof"]["independence"], "rank_deficient")
        self.assertIsNone(report["evidence"])

    def test_underactuation_or_no_controls_blocks_before_inner_is_called(self):
        calls = []
        for controls in ((), (control(),)):
            report = solve_trim(model(lambda _: calls.append(1), controls=controls,
                                      targets=(target(), target("y"))))
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["reason"], "insufficient_controls")
            self.assertEqual(report["nfev"], 0)
            self.assertEqual(report["points"], [])
            self.assertIsNone(report["dof"]["rank"])
        self.assertEqual(calls, [])

    def test_two_controls_can_realize_one_target_without_implicit_extra_control(self):
        calls = []
        def evaluate(values):
            calls.append(dict(values))
            return inner({"x": values["u"] + values["v"]})
        report = solve_trim(model(evaluate, controls=(control(), control("v")), targets=(target(value=1.5),)))
        self.assertEqual(report["status"], "converged")
        self.assertEqual(report["dof"]["rank"], 1)
        self.assertTrue(all(set(values) == {"u", "v"} for values in calls))
        self.assertTrue(all(0 <= value <= 1 for values in calls for value in values.values()))

    def test_initial_boundary_uses_inward_sensitivity_and_never_exceeds_limits(self):
        calls = []
        def evaluate(values):
            calls.append(values["u"])
            return inner({"x": values["u"]})
        for initial in (0, 1):
            report = solve_trim(model(evaluate, controls=(control(initial=initial),)))
            self.assertEqual(report["status"], "converged")
        self.assertTrue(all(0 <= value <= 1 for value in calls))

    def test_inner_validity_and_convergence_are_required_even_when_target_matches(self):
        changes = ({"status": "failed"}, {"valid": False}, {"domain_valid": False},
                   {"valid": 1}, {"residual_norm": 2}, {"residual_norm": -1},
                   {"residual_norm": math.inf}, {"iterations": True}, {"outputs": {"x": math.nan}},
                   {"outputs": {}}, {"points": "not a trace"}, {"points": [{}] * 101})
        for change in changes:
            with self.subTest(change=change):
                report = solve_trim(model(lambda _: inner({"x": .6}) | deepcopy(change)))
                self.assertEqual(report["status"], "numerical_failure")
                self.assertEqual(report["reason"], "inner_evaluation_failed")
                self.assertEqual(report["points"], [])
                self.assertIsNone(report["last_valid_point"])
                self.assertIsNone(report["final_outputs"])
                json.dumps(report, allow_nan=False)

    def test_failed_sensitivity_probe_is_not_a_point_or_a_rank_result(self):
        def evaluate(values):
            if values["u"] != .2:
                raise ValueError("probe unavailable")
            return inner({"x": .2})
        report = solve_trim(model(evaluate))
        self.assertEqual(report["status"], "numerical_failure")
        self.assertEqual(len(report["points"]), 1)
        self.assertIsNone(report["dof"]["rank"])
        self.assertEqual(report["evaluations"][-1]["phase"], "sensitivity")

    def test_numeric_budgets_do_not_turn_into_unreachable_targets(self):
        for settings, reason in (({"max_iter": 0}, "iteration_limit"), ({"max_nfev": 1}, "evaluation_limit")):
            with self.subTest(settings=settings):
                report = run_trim_framework_check(settings=settings)
                self.assertEqual(report["status"], "numerical_failure")
                self.assertEqual(report["reason"], reason)
                self.assertEqual(report["iterations"], 0)
                self.assertEqual(len(report["points"]), 1)
                self.assertIsNone(report["evidence"])

    def test_strict_specs_prevent_invalid_or_implicitly_clipped_inputs(self):
        for changes in ({"initial": 2}, {"initial": True}, {"lower": 1}, {"upper": math.inf},
                        {"name": ""}, {"unit": None}, {"initial": "0.5"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                control(**changes)
        for changes in ({"tolerance": 0}, {"tolerance": math.nan}, {"value": True}, {"name": " x"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                target(**changes)
        with self.assertRaises(FrozenInstanceError):
            control().initial = 2
        for item in (model(controls=(control(), control())), model(targets=(target(), target())),
                     model(controls=(dict(name="u"),)), model(targets=()), model(solve_inner=None)):
            with self.subTest(item=item), self.assertRaises(ValueError):
                solve_trim(item)

    def test_scenarios_and_resource_caps_cannot_be_changed_by_untrusted_settings(self):
        for scenario in (None, "engine", {}, True):
            with self.subTest(scenario=scenario), self.assertRaises(ValueError):
                run_trim_framework_check(scenario)
        for settings in ({"max_iter": 31}, {"max_nfev": 201}, {"max_nfev": 0},
                         {"max_iter": True}, {"formula": "x+1"}, []):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                run_trim_framework_check(settings=settings)

    def test_callback_cannot_mutate_history_and_cancellation_is_not_numeric_failure(self):
        def mutate(point):
            point["controls"].clear()
            point["outputs"]["x"] = -1
        report = run_trim_framework_check(progress_callback=mutate)
        self.assertEqual(report["status"], "converged")
        self.assertTrue(report["points"][0]["controls"])
        self.assertGreater(report["points"][0]["outputs"]["x"], 0)
        token = CancellationToken()
        with cancellation_scope(token), self.assertRaises(TaskCancelled):
            run_trim_framework_check(progress_callback=lambda point: token.cancel())

    def test_self_checks_do_not_import_engine_models(self):
        code = ("import sys; sys.path.insert(0, 'src'); from lre_workbench.systems.control_trim import run_trim_framework_check; "
                "assert run_trim_framework_check()['status'] == 'converged'; "
                "assert not ({'lre_workbench.systems.ffsc','lre_workbench.legacy.gg_cycle','lre_workbench.domain.models','lre_workbench.properties.thermochemistry','lre_workbench.systems.steady_network','lre_workbench.operations.feed_network',"
                "'lre_workbench.operations.transient','lre_workbench.properties.propellants'} & set(sys.modules))")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
