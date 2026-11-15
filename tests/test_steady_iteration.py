"""Named residual contracts and honest engine-independent iteration traces."""
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lre_workbench.solvers.iteration import ResidualSpec, VariableSpec, run_framework_check, solve_model
from lre_workbench.runtime.tasks import CancellationToken, TaskCancelled, cancellation_scope


def variable(name="x", **changes):
    values = dict(name=name, label=name, unit="1", initial=1.0,
                  lower=0.1, upper=5.0, scale=1.0)
    values.update(changes)
    return VariableSpec(**values)


def residual(name="r", **changes):
    values = dict(name=name, label=name, unit="1", scale=1.0,
                  tolerance=1e-8, group="algebra")
    values.update(changes)
    return ResidualSpec(**values)


def model(evaluate=None, **changes):
    if evaluate is None:
        evaluate = lambda state: {"residuals": {"r": state["x"] ** 2 - 2}, "outputs": state}
    values = dict(model_id="test_algebra", variables=(variable(),),
                  residuals=(residual(),), evaluate=evaluate)
    values.update(changes)
    return SimpleNamespace(**values)


class ModelContractTests(unittest.TestCase):
    def test_frozen_specs_reject_missing_nonfinite_and_out_of_bounds_initial(self):
        for initial in (None, "1", True, math.nan, math.inf, -1, 6):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                variable(initial=initial)
        with self.assertRaises(FrozenInstanceError):
            variable().initial = 2

    def test_bounds_scales_names_and_tolerances_are_strict(self):
        for changes in ({"lower": math.inf}, {"upper": math.inf}, {"lower": 5},
                        {"scale": 0}, {"scale": -1}, {"scale": math.nan},
                        {"name": ""}, {"name": " x"}, {"unit": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                variable(**changes)
        for changes in ({"scale": 0}, {"tolerance": 0}, {"tolerance": True},
                        {"tolerance": math.inf}, {"group": ""},
                        {"scale": 1e-300, "tolerance": 1e-300},
                        {"scale": 1e300, "tolerance": 1e300}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                residual(**changes)

    def test_duplicate_names_and_nonsquare_models_rejected_before_evaluation(self):
        calls = []
        cases = [
            model(variables=(variable(), variable()), residuals=(residual(), residual("s"))),
            model(variables=(variable(), variable("y")), residuals=(residual(), residual())),
            model(variables=(variable(), variable("y"))),
            model(variables=()),
            model(residuals=[{"name": "r"}]),
            model(model_id=""),
        ]
        for item in cases:
            item.evaluate = lambda state: calls.append(state)
            with self.subTest(item=item), self.assertRaises(ValueError):
                solve_model(item)
        self.assertEqual(calls, [])

    def test_missing_extra_or_nonfinite_residual_is_failed_without_fake_point(self):
        for supplied in ({}, {"r": 1, "extra": 2}, {"r": math.nan},
                         {"r": math.inf}, {"r": "2"}, {"r": True}, []):
            with self.subTest(supplied=supplied):
                result = solve_model(model(lambda state: {"residuals": supplied, "outputs": {}}))
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["reason"], "initial_evaluation_failed")
                self.assertTrue(result["error"])
                self.assertEqual(result["points"], [])
                self.assertEqual(result["nfev"], 1)
                self.assertIsNone(result["final_state"])
                self.assertIsNone(result["outputs"])
                json.dumps(result, allow_nan=False)

    def test_response_and_output_contract_reject_non_json_values_and_keys(self):
        cases = [{"residuals": {"r": 0}},
                 {"residuals": {"r": 0}, "outputs": {}, "formula": "x"}]
        for outputs in ([], {1: "silently converted key"}, {"x": math.nan},
                        {"x": {1, 2}}, {"x": (1, 2)}, {"x": object()}):
            cases.append({"residuals": {"r": 0}, "outputs": outputs})
        for response in cases:
            with self.subTest(response=response):
                result = solve_model(model(lambda state: response))
                self.assertEqual(result["reason"], "initial_evaluation_failed")
                self.assertEqual(result["points"], [])

    def test_no_formula_strings_or_unknown_solver_options(self):
        for item in ("x*x-2", {"model_id": "user_formula", "evaluate": "x*x-2"},
                     model(evaluate="x*x-2")):
            with self.subTest(item=item), self.assertRaises(ValueError):
                solve_model(item)
        for settings in ({"formula": "x*x"}, {"tol": 1}, {"max_iter": 1.5},
                         {"max_iter": True}, {"max_nfev": 0}, {"backend": "eval"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                solve_model(model(), settings=settings)


class NamedIterationTests(unittest.TestCase):
    def test_coupled_check_uses_real_accepted_iterations_and_known_root(self):
        events = []
        result = run_framework_check(progress_callback=events.append)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["reason"], "tolerance_met")
        self.assertEqual(result["analysis_type"], "numerical_framework_check")
        self.assertEqual(result["scope"], "numerical_framework_only")
        self.assertFalse(result["is_engine_result"])
        self.assertEqual(result["equation_independence"], "local_jacobian_only")
        self.assertAlmostEqual(result["final_state"]["x"], 2, places=7)
        self.assertAlmostEqual(result["final_state"]["y"], 1, places=7)
        self.assertGreater(result["iterations"], 1)
        self.assertEqual(result["iterations"], len(result["points"]) - 1)
        self.assertGreater(result["nfev"], len(result["points"]))
        self.assertEqual(events, result["points"])
        self.assertGreater(result["points"][0]["residual_norm"], 1)
        self.assertLessEqual(result["points"][-1]["residual_norm"], 1)
        for point in result["points"]:
            x, y = point["variables"]["x"], point["variables"]["y"]
            np.testing.assert_allclose(list(point["raw_residuals"].values()),
                                       [x*x+y-5, y*y+x-3], atol=1e-15)
            self.assertEqual(point["residual_norm"], max(map(abs, point["residuals"].values())))
        json.dumps(result, allow_nan=False)

    def test_units_residual_tolerance_and_trace_groups_have_unambiguous_meaning(self):
        base = solve_model(model())
        dimensional = solve_model(model(
            lambda state: {"residuals": {"r": 1e7 * ((state["x"] / 1e6) ** 2 - 2)},
                           "outputs": {"nested": [state, None, True]}},
            variables=(variable(unit="mm", initial=1e6, lower=1e5, upper=5e6, scale=1e6),),
            residuals=(residual(unit="N", scale=1e7, tolerance=1e-8, group="balance"),)))
        self.assertEqual(base["status"], "pass")
        self.assertEqual(dimensional["status"], "pass")
        self.assertEqual(base["iterations"], dimensional["iterations"])
        np.testing.assert_allclose(
            [p["variables"]["x"] for p in base["points"]],
            [p["variables"]["x"] / 1e6 for p in dimensional["points"]], rtol=1e-7)
        self.assertEqual(dimensional["groups"], {"balance": ["r"]})
        for point in dimensional["points"]:
            self.assertAlmostEqual(point["scaled_residuals"]["r"],
                                   point["raw_residuals"]["r"] / 1e7, places=14)
            self.assertAlmostEqual(point["residuals"]["r"],
                                   point["scaled_residuals"]["r"] / 1e-8, delta=1e-6)
        self.assertEqual(dimensional["tolerance"], 1)

    def test_model_error_keeps_last_accepted_state_outputs_and_trace(self):
        calls = []

        def evaluate(state):
            calls.append(state)
            if len(calls) >= 4:
                raise ValueError("characteristic unavailable at evaluation")
            return {"residuals": {"r": state["x"] ** 2 - 2}, "outputs": dict(state)}

        result = solve_model(model(evaluate))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "jacobian_failed")
        self.assertEqual(len(result["points"]), 2)
        self.assertEqual(result["nfev"], len(calls))
        self.assertEqual(result["final_state"], result["points"][-1]["variables"])
        self.assertEqual(result["outputs"], result["final_state"])
        self.assertTrue(result["error"])
        self.assertIn("characteristic unavailable", result["last_model_error"])
        self.assertGreater(result["model_error_count"], 0)

    def test_trf_backend_preserves_named_trace_and_equation_acceptance(self):
        result = run_framework_check({"backend": "trf"})
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["settings"]["backend"], "trf")
        self.assertLessEqual(result["points"][-1]["residual_norm"], 1)
        self.assertAlmostEqual(result["final_state"]["x"], 2, places=7)
        self.assertAlmostEqual(result["final_state"]["y"], 1, places=7)

    def test_broyden_adapter_preserves_physical_acceptance_and_work_counts(self):
        calls = []
        def evaluate(state):
            calls.append(state.copy())
            return {"residuals": {"r": 1e7 * ((state["x"] / 1e6) ** 2 - 2)},
                    "outputs": dict(state)}
        result = solve_model(model(evaluate,
            variables=(variable(unit="mm", initial=1e6, lower=1e5, upper=5e6, scale=1e6),),
            residuals=(residual(label="尺寸约束", unit="N", scale=1e7, tolerance=1e-8, group="连接件"),)),
            settings={"backend": "broyden"})
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["nfev"], len(calls))
        diagnostics = result["diagnostics"]
        self.assertEqual(diagnostics["model_evaluations"], len(calls))
        self.assertGreater(diagnostics["approximate_updates"], 0)
        row = diagnostics["equations"][0]
        self.assertEqual((row["unit"], row["component"]), ("N", "连接件"))
        self.assertAlmostEqual(row["absolute_tolerance"], .1)
        self.assertLessEqual(abs(row["raw_residual"]), .1)
        self.assertEqual(diagnostics["variables"][0]["unit"], "mm")
        self.assertEqual(result["final_state"], result["outputs"])
        json.dumps(result, allow_nan=False)

    def test_failed_named_model_exposes_domain_location_without_fake_independence(self):
        from lre_workbench.solvers.nonlinear import ModelDomainError
        def evaluate(state):
            raise ModelDomainError("特性区间外", component="测试部件", variable="x", equation="r")
        result = solve_model(model(evaluate), settings={"backend": "broyden"})
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["equation_independence"], "not_checked")
        failure = result["diagnostics"]["failures"][0]
        self.assertEqual((failure["component"], failure["variable"], failure["equation"]), ("测试部件", "x", "r"))

    def test_iteration_and_evaluation_budget_keep_initial_point_without_claiming_pass(self):
        for settings, reason in (({"max_iter": 0}, "max_iterations"),
                                 ({"max_nfev": 1}, "max_evaluations")):
            with self.subTest(settings=settings):
                result = run_framework_check(settings)
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["iterations"], 0)
                self.assertEqual(len(result["points"]), 1)
                self.assertEqual(result["nfev"], 1)
                self.assertGreater(result["points"][0]["residual_norm"], 1)

    def test_callback_cannot_mutate_persisted_trace(self):
        def mutate(point):
            point["variables"]["x"] = -100
            point["residuals"].clear()

        result = solve_model(model(), progress_callback=mutate)
        self.assertEqual(result["status"], "pass")
        self.assertGreater(result["points"][0]["variables"]["x"], 0)
        self.assertIn("r", result["points"][0]["residuals"])

    def test_cancellation_propagates_and_does_not_become_failed_result(self):
        token, points = CancellationToken(), []

        def accepted(point):
            points.append(point)
            token.cancel()

        with cancellation_scope(token):
            with self.assertRaises(TaskCancelled):
                run_framework_check(progress_callback=accepted)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["iteration"], 0)

    def test_self_check_hard_limits_cannot_be_increased(self):
        for settings in ({"max_iter": 51}, {"max_nfev": 401}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                run_framework_check(settings)

    def test_equation_count_is_not_reported_as_independence_proof(self):
        result = solve_model(model(
            lambda state: {"residuals": {"r": state["x"] + state["y"] - 4,
                                          "s": 2 * (state["x"] + state["y"] - 4)},
                           "outputs": {}},
            variables=(variable(), variable("y")),
            residuals=(residual(), residual("s"))))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["equation_independence"], "local_jacobian_only")
        self.assertEqual(result["diagnostics"]["jacobian"]["rank"], 1)
        self.assertTrue(result["diagnostics"]["jacobian"]["local_only"])
        self.assertTrue(any("独立性" in text for text in result["limitations"]))

    def test_framework_import_and_self_check_do_not_load_engine_models(self):
        code = (
            "import sys; sys.path.insert(0, 'src'); "
            "from lre_workbench.solvers.iteration import run_framework_check; "
            "assert run_framework_check()['status'] == 'pass'; "
            "assert not ({'lre_workbench.systems.ffsc', 'lre_workbench.legacy.gg_cycle', 'lre_workbench.domain.models', 'lre_workbench.properties.thermochemistry', 'lre_workbench.systems.steady_network', "
            "'lre_workbench.operations.feed_network', 'lre_workbench.operations.transient', 'lre_workbench.properties.propellants'} & set(sys.modules))"
        )
        completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
