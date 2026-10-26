"""Regression tests for GG solver acceptance and recorded, evaluated iterates."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import CycleResults, EngineInputs, FlowState
from convergence import numerical_convergence
from gg_cycle import GGCycleModel


class AnalyticGG(GGCycleModel):
    """Avoid thermochemistry in tests of solver control flow, not cycle physics."""

    def _initialize_design_guess(self):
        # The physical initial-enthalpy inversion has its own tests; these
        # analytic residuals deliberately exercise the continuation controller.
        return None

    def __init__(self, inputs=None, feedback=None):
        super().__init__(inputs or EngineInputs(cycle_type="gg", use_target_gg_temp=0))
        self.feedback = feedback
        self.evaluations = []

    def _evaluate_once(self, *x):
        self.evaluations.append((self.inputs.pc, tuple(x), self._gg_mr,
                                 self.inputs.chamber_temp_guess,
                                 self.inputs.chamber_gamma, self.inputs.molar_mass))
        tc, gamma, mw = (self.feedback(self) if self.feedback else
                         (self.inputs.chamber_temp_guess,
                          self.inputs.chamber_gamma, self.inputs.molar_mass))
        return CycleResults(
            inputs=self.inputs,
            summary={"chamber_temperature_K": tc, "chamber_gamma": gamma,
                     "chamber_molar_mass": mw, "gg_T_combustion_K": self.inputs.gg_temp_target,
                     "gg_pressure_Pa": self.inputs.pc, "gg_mr": self._gg_mr},
            states={key: FlowState(name=key, temperature=100.0, pressure=1e6, mass_flow=1.0)
                    for key in ("fuel_after_pump", "lox_after_pump")},
            components={}, warnings=[],
        )

    def residual_vector(self, *x):
        return [0.0, 0.0], self._evaluate_once(*x)


class GGConvergenceTests(unittest.TestCase):
    def test_bisection_returns_evaluated_temperature_match(self):
        model = AnalyticGG()
        with patch("thermo.combustion_equilibrium",
                   side_effect=lambda **kw: SimpleNamespace(temperature=1000 * kw["mdot_ox"])):
            mr = model._mr_for_target_gg_temp(475, 100, 90, 1e6)
        self.assertAlmostEqual(mr, .475)
        self.assertAlmostEqual(mr * 1000, 475)

    def test_bisection_preserves_exact_bracket_endpoints(self):
        model = AnalyticGG()
        with patch("thermo.combustion_equilibrium",
                   side_effect=lambda **kw: SimpleNamespace(temperature=1000 * kw["mdot_ox"])):
            self.assertEqual(model._mr_for_target_gg_temp(50, 100, 90, 1e6), .05)
            self.assertEqual(model._mr_for_target_gg_temp(900, 100, 90, 1e6), .90)

    def test_final_permitted_newton_step_is_evaluated_and_accepted(self):
        model = AnalyticGG()

        def residual(*x):
            return [(x[0] - 99) * model.inputs.thrust / 100,
                    (x[1] - .03) * 5e6], model._evaluate_once(*x)

        with patch.object(model, "residual_vector", side_effect=residual):
            x, result = model.solve_cycle(x_init=[100, .03], max_iter=1)
        self.assertAlmostEqual(x[0], 99)
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
        points = result.convergence["inner_runs"][0]["points"]
        self.assertEqual([p["iteration"] for p in points], [0, 1])
        self.assertEqual(result.summary["solver_iterations"], 1)
        self.assertAlmostEqual(points[-1]["residual_norm"], result.summary["solver_residual_norm"])
        self.assertGreater(len(model.evaluations), len(points))  # No Jacobian probes in trajectory.

    def test_zero_budget_records_initial_state_and_fails_if_not_converged(self):
        model = AnalyticGG()
        with patch.object(model, "residual_vector",
                          side_effect=lambda *x: ([model.inputs.thrust, 0], model._evaluate_once(*x))):
            _, result = model.solve_cycle(max_iter=0)
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "fail")
        self.assertEqual(result.convergence["layers"]["inner"]["termination_reason"], "max_iterations")
        self.assertEqual(len(result.convergence["inner_runs"][0]["points"]), 1)
        self.assertEqual(result.summary["solver_iterations"], 0)

    def test_nonfinite_power_residual_cannot_pass_after_finite_thrust(self):
        model = AnalyticGG()
        with patch.object(model, "residual_vector",
                          side_effect=lambda *x: ([0, float("nan")], model._evaluate_once(*x))):
            _, result = model.solve_cycle(max_iter=0)
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "fail")
        self.assertEqual(result.summary["solver_warning"], 1)
        self.assertEqual(result.summary["inner_solver_warning"], 1)
        self.assertIsNone(result.convergence["inner_runs"][0]["points"][0]["residual_norm"])
        json.dumps(result.convergence, allow_nan=False)

    def test_solver_rejects_nonfinite_wrong_shape_and_invalid_limits(self):
        model = AnalyticGG()
        cases = ({"x_init": [100]}, {"x_init": [100, .03, .5]},
                 {"x_init": [100, float("nan")]}, {"x_init": [float("inf"), .03]},
                 {"tol_rel": float("nan")}, {"tol_rel": 0}, {"max_iter": -1})
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                model.solve_cycle(**values)
        self.assertEqual(model.evaluations, [])

    def test_bound_probes_remain_feasible_and_reuse_scale_evaluation(self):
        model = AnalyticGG()
        def residual(*x):
            self.assertGreaterEqual(x[1], .004)
            self.assertLessEqual(x[1], .12)
            return [(x[0] - 99) * model.inputs.thrust / 100,
                    (x[1] - .08) * 1e5], model._evaluate_once(*x)
        with patch.object(model, "residual_vector", side_effect=residual):
            x, result = model.solve_cycle(x_init=[100, .12])
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
        self.assertAlmostEqual(x[1], .08, places=5)
        self.assertEqual(result.summary["solver_function_evaluations"], len(model.evaluations))

    def test_jacobian_failure_keeps_last_evaluated_state_and_trace(self):
        model = AnalyticGG()
        def residual(*x):
            if list(x) != [100, .03]:
                raise RuntimeError("probe failed")
            return [model.inputs.thrust, 0], model._evaluate_once(*x)
        with patch.object(model, "residual_vector", side_effect=residual):
            x, result = model.solve_cycle(x_init=[100, .03])
        self.assertEqual(x, [100, .03])
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "fail")
        self.assertIn("jacobian", result.convergence["layers"]["inner"]["termination_reason"])
        self.assertEqual(len(result.convergence["inner_runs"][0]["points"]), 1)

    def test_stationary_nonroot_cannot_pass_either_backend(self):
        for backend in ("newton", "trf"):
            model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=0,
                                           nonlinear_solver=backend))
            with patch.object(model, "residual_vector", side_effect=lambda *x: (
                    [model.inputs.thrust, 0], model._evaluate_once(*x))):
                x, result = model.solve_cycle(x_init=[100, .03])
            self.assertEqual(x, [100, .03])
            self.assertEqual(result.convergence["layers"]["inner"]["status"], "fail")
            self.assertGreater(result.summary["solver_residual_norm"], 1e-4)

    def test_thermal_limit_is_failure_and_last_state_is_evaluated(self):
        model = AnalyticGG(feedback=lambda m: (m.inputs.chamber_temp_guess + 20,
                                               m.inputs.chamber_gamma, m.inputs.molar_mass))
        original = model.inputs.chamber_temp_guess
        result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(numerical_convergence(result)["status"], "fail")
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
        self.assertEqual(result.convergence["layers"]["thermal"]["termination_reason"], "max_iterations")
        self.assertEqual(result.summary["tc_outer_residual_K"], 20)
        self.assertEqual(len(result.convergence["thermal_history"]), 20)
        self.assertEqual(result.summary["chamber_temp_guess_used_K"], model.evaluations[-1][3])
        self.assertEqual(model.inputs.chamber_temp_guess, original)
        self.assertEqual(result.summary["solver_warning"], 1)

    def test_gamma_and_molar_mass_feedback_must_both_converge(self):
        for changed in ("gamma", "molar_mass"):
            with self.subTest(changed=changed):
                model = AnalyticGG(feedback=lambda m: (
                    m.inputs.chamber_temp_guess,
                    m.inputs.chamber_gamma * (1.001 if changed == "gamma" else 1),
                    m.inputs.molar_mass * (1.001 if changed == "molar_mass" else 1)))
                result = model.evaluate(include_cooling_marching=False)
                self.assertEqual(result.summary["tc_outer_residual_K"], 0)
                self.assertEqual(numerical_convergence(result)["status"], "fail")
                self.assertEqual(result.convergence["layers"]["thermal"]["status"], "fail")

    def test_stable_temperature_does_not_skip_property_feedback_resolve(self):
        model = AnalyticGG(feedback=lambda m: (m.inputs.chamber_temp_guess, 1.21, .021))
        result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertGreater(len(result.convergence["thermal_history"]), 1)
        self.assertEqual(result.convergence["layers"]["preburner"]["status"], "disabled")
        self.assertAlmostEqual(result.convergence["thermal_history"][-1]["gamma_relative_residual"], 0)
        self.assertEqual(result.summary["solver_inner_solves"], len(result.convergence["inner_runs"]))

    def test_nonstagnating_inner_failure_blocks_continuation_and_target_loop(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1))
        original_pc = model.inputs.pc
        original_solver = model.solve_cycle
        with patch.object(model, "residual_vector",
                          side_effect=lambda *x: ([model.inputs.thrust * .001, 0], model._evaluate_once(*x))), \
                patch.object(model, "solve_cycle", side_effect=lambda **kw: original_solver(max_iter=0, **kw)):
            result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(result.summary["solver_stalled"], 0)
        self.assertGreater(len(model.evaluations), 1)
        self.assertEqual(model.evaluations[0][0], original_pc * .6)
        self.assertTrue(all(not p["accepted"] for p in result.convergence["continuation_history"]))
        self.assertEqual(result.convergence["layers"]["continuation"]["reached_ratio"], 0)
        self.assertEqual(result.convergence["layers"]["continuation"]["status"], "fail")
        self.assertEqual(result.convergence["layers"]["preburner"]["status"], "fail")
        self.assertEqual(result.convergence["thermal_history"], [])
        self.assertEqual(numerical_convergence(result)["status"], "fail")
        self.assertEqual(model.inputs.pc, original_pc)

    def test_temperature_target_limit_keeps_final_evaluated_mr_and_all_histories(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1))
        evaluate_once = model._evaluate_once

        def unmet_target(*x):
            result = evaluate_once(*x)
            result.summary["gg_T_combustion_K"] = model.inputs.gg_temp_target + 10
            return result

        with patch.object(model, "_evaluate_once", side_effect=unmet_target), \
                patch.object(model, "_mr_for_target_gg_temp", return_value=.4) as invert:
            result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(len(result.convergence["preburner_history"]), 12)
        self.assertEqual(invert.call_count, 11)  # Never update an MR which will not be solved.
        self.assertEqual(len(result.convergence["thermal_history"]), 12)
        self.assertEqual(len(result.convergence["inner_runs"]), 13)
        self.assertEqual(result.summary["solver_inner_solves"], 13)
        self.assertEqual(result.summary["solver_warm_start_successes"], 11)
        self.assertEqual(result.summary["gg_mr"], model.evaluations[-1][2])
        self.assertEqual(result.convergence["layers"]["preburner"]["status"], "fail")
        self.assertEqual(numerical_convergence(result)["status"], "fail")
        self.assertIsNone(model._gg_mr_override)

    def test_target_success_retains_all_enabled_layers_and_iteration_coordinates(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1))
        result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertEqual({v["status"] for v in result.convergence["layers"].values()}, {"pass"})
        runs = result.convergence["inner_runs"]
        self.assertEqual([r["stage_ratio"] for r in runs], [.6, 1.0])
        self.assertEqual([r["thermal_iteration"] for r in runs], [None, 1])
        self.assertEqual([r["preburner_iteration"] for r in runs], [1, 1])

    def test_deprecated_pressure_switch_cannot_enable_a_second_physical_model(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=0,
                                        cooling_bartz_mode=0, cooling_dp_auto=1),
                           feedback=lambda m: (m.inputs.chamber_temp_guess, 1.21, .021))
        result = model.evaluate(include_cooling_marching=False)
        self.assertFalse(result.convergence["layers"]["thermal"]["enabled"])
        self.assertEqual(result.convergence["thermal_history"], [])
        self.assertEqual(numerical_convergence(result)["status"], "pass")

    def test_fixed_heat_load_and_pressure_drop_disable_unused_thermal_loop(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1,
                                        cooling_bartz_mode=0, cooling_dp_auto=0))
        result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "disabled")
        self.assertEqual(result.convergence["thermal_history"], [])
        self.assertEqual(numerical_convergence(result)["status"], "pass")

    def test_invalid_thermal_feedback_has_json_safe_failure_evidence(self):
        model = AnalyticGG(feedback=lambda m: (float("nan"), 1.2, 22))
        result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "fail")
        self.assertEqual(result.convergence["layers"]["thermal"]["termination_reason"], "invalid_thermal_feedback")
        json.dumps(result.convergence, allow_nan=False)

    def test_temperature_inversion_failure_keeps_last_evaluated_mr(self):
        model = AnalyticGG(EngineInputs(cycle_type="gg", use_target_gg_temp=1))
        evaluate_once = model._evaluate_once

        def unmet_target(*x):
            result = evaluate_once(*x)
            result.summary["gg_T_combustion_K"] += 10
            return result

        with patch.object(model, "_evaluate_once", side_effect=unmet_target), \
                patch.object(model, "_mr_for_target_gg_temp", side_effect=ValueError("invalid temperature")):
            result = model.evaluate(include_cooling_marching=False)
        self.assertEqual(result.convergence["layers"]["preburner"]["termination_reason"], "temperature_inversion_failed")
        self.assertEqual(result.summary["gg_mr"], model.evaluations[-1][2])
        self.assertEqual(numerical_convergence(result)["status"], "fail")


if __name__ == "__main__":
    unittest.main()
