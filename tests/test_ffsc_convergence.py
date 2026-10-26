"""Regression tests for nested FFSC convergence, independent of thermo libraries."""
from pathlib import Path
import math
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import CycleResults, EngineInputs
from cycle import FFSCCycleModel
from convergence import numerical_convergence


def result_for(model, **summary):
    return CycleResults(model.inputs, summary, {}, {}, [])


def inner_result(model, *, passed=True, tc=None, gamma=None, mw=None):
    """A solved residual system with an independently controlled thermal output."""
    i = model.inputs
    result = result_for(
        model, solver_iterations=1, solver_residual_norm=1e-6 if passed else 1e-2,
        solver_warning=float(not passed), inner_solver_warning=float(not passed),
        chamber_temperature_K=i.chamber_temp_guess if tc is None else tc,
        chamber_gamma=i.chamber_gamma if gamma is None else gamma,
        chamber_molar_mass=i.molar_mass if mw is None else mw,
        chamber_pressure_Pa=i.pc)
    result.convergence = model._empty_convergence()
    reason = "tolerance_met" if passed else "max_iterations"
    result.convergence["layers"]["inner"] = model._convergence_layer(
        True, "pass" if passed else "fail", result.summary["solver_residual_norm"],
        1e-4, "1", 1, reason)
    context = getattr(model, "_convergence_context", {})
    result.convergence["inner_runs"] = [dict(
        id=1, stage_ratio=context.get("stage_ratio", 1),
        thermal_iteration=context.get("thermal_iteration", 0),
        preburner_iteration=context.get("preburner_iteration", 0),
        tolerance=1e-4, termination_reason=reason,
        points=[dict(iteration=0, residual_norm=0.1, residuals={}),
                dict(iteration=1, residual_norm=result.summary["solver_residual_norm"], residuals={})])]
    return [100, 2, 2], result


class PreburnerInversionTests(unittest.TestCase):
    def setUp(self):
        self.model = FFSCCycleModel(EngineInputs())

    def test_midpoint_hit_returns_the_evaluated_midpoint(self):
        with patch("thermo.combustion_equilibrium", side_effect=lambda **kw:
                   SimpleNamespace(temperature=2000 - 10 * kw["mdot_ox"])):
            # The original bug returned 123.75 after a successful 87.5 trial.
            mr = self.model._mr_for_target_temp("ox", 1125, 350, 90, 60e6)
        self.assertEqual(mr, 87.5)

    def test_exact_endpoint_is_returned(self):
        with patch("thermo.combustion_equilibrium", side_effect=lambda **kw:
                   SimpleNamespace(temperature=2000 - 10 * kw["mdot_ox"])):
            self.assertEqual(self.model._mr_for_target_temp("ox", 1850, 350, 90, 60e6), 15)

    def test_nonfinite_temperature_or_target_is_rejected(self):
        with patch("thermo.combustion_equilibrium", return_value=SimpleNamespace(temperature=math.nan)):
            with self.assertRaises(ValueError):
                self.model._mr_for_target_temp("ox", 750, 350, 90, 60e6)
        with self.assertRaises(ValueError):
            self.model._mr_for_target_temp("fuel", math.nan, 350, 90, 60e6)


class NewtonTraceTests(unittest.TestCase):
    def setUp(self):
        self.model = FFSCCycleModel(EngineInputs())

    def test_last_allowed_step_can_pass_and_trace_excludes_jacobian_probes(self):
        calls = []
        def residual(*x):
            calls.append(x)
            values = [(x[0] - 11) * self.model.inputs.thrust,
                      (x[1] - 2.1) * 1e7, (x[2] - 3.1) * 1e7]
            return values, result_for(self.model)
        with patch.object(self.model, "residual_vector", side_effect=residual):
            _, result = self.model.solve_cycle(x_init=[10, 2, 3], max_iter=1)
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
        self.assertEqual(len(calls), 5)  # initial, 3 Jacobian probes, accepted final
        points = result.convergence["inner_runs"][0]["points"]
        self.assertEqual([p["iteration"] for p in points], [0, 1])
        self.assertLessEqual(points[-1]["residual_norm"], 1e-4)
        # A standalone Newton solve proves only this layer, not a full design.
        self.assertFalse(result.convergence["layers"]["continuation"]["enabled"])

    def test_zero_step_limit_records_initial_failure_without_invented_iterations(self):
        with patch.object(self.model, "residual_vector", return_value=([1e6, 0, 0], result_for(self.model))):
            _, result = self.model.solve_cycle(max_iter=0)
        self.assertEqual(result.summary["solver_iterations"], 0)
        self.assertEqual(result.convergence["layers"]["inner"]["termination_reason"], "max_iterations")
        self.assertEqual(len(result.convergence["inner_runs"][0]["points"]), 1)

    def test_nan_in_any_residual_cannot_disappear_in_max(self):
        with patch.object(self.model, "residual_vector", return_value=([0, math.nan, 0], result_for(self.model))):
            _, result = self.model.solve_cycle()
        self.assertEqual(result.convergence["layers"]["inner"]["termination_reason"], "invalid_state")
        self.assertEqual(numerical_convergence(result)["status"], "fail")
        self.assertIsNone(result.convergence["inner_runs"][0]["points"][0]["residual_norm"])

    def test_upper_bound_uses_a_real_backward_jacobian_difference(self):
        calls = []
        def residual(*x):
            calls.append(x)
            return [(x[0] - 100) * 1e3, (x[1] - 9.8) * 1e5,
                    (x[2] - 9.8) * 1e5], result_for(self.model)
        with patch.object(self.model, "residual_vector", side_effect=residual):
            x, result = self.model.solve_cycle(x_init=[100, 10, 10])
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
        self.assertAlmostEqual(x[1], 9.8)
        self.assertAlmostEqual(x[2], 9.8)
        self.assertTrue(any(point[1] < 10 for point in calls[1:4]))
        self.assertTrue(all(point[1] <= 10 and point[2] <= 10 for point in calls))

    def test_nan_initial_guess_is_not_silently_clamped_into_a_valid_value(self):
        with self.assertRaises(ValueError):
            self.model.solve_cycle(x_init=[100, math.nan, 2])


class NestedConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.model = FFSCCycleModel(EngineInputs(cooling_marching_enable=0))
        seed_patch=patch.object(self.model,"_initialize_preburner_temperature_guess",return_value={})
        seed_patch.start()
        self.addCleanup(seed_patch.stop)

    def test_current_quasi1d_feedback_replaces_only_redundant_picard_solves(self):
        from dataclasses import asdict
        from regen_coupling import close_regen_cycle
        from tests.test_regen_coupling import CouplingTests
        fixture = CouplingTests()
        initial = (self.model.inputs.chamber_temp_guess, self.model.inputs.chamber_gamma,
                   self.model.inputs.molar_mass)
        self.model.inputs.use_target_preburner_temp = 0
        warm = dict(x=[100., 2., 2.], chamber_temp_guess=initial[0],
                    chamber_gamma=initial[1], molar_mass=initial[2])
        def solve(**kwargs):
            vector, result = inner_result(self.model, tc=3800., gamma=1.25, mw=23.)
            # Exercise production coupling acceptance on an analytic circuit.
            closed = close_regen_cycle(self.model, lambda: fixture.evaluation(self.model))
            result.components = closed.components
            result._current_regen_thermal_evidence = closed._current_regen_thermal_evidence
            return vector, result
        with patch("pump.fluid_density", return_value=400.), patch.object(
                self.model, "solve_cycle", side_effect=solve) as solver:
            result = self.model._evaluate_design_core(warm_state=warm)
        self.assertEqual(solver.call_count, 1)
        thermal = result.convergence["layers"]["thermal"]
        self.assertEqual(thermal["source"], "regen_quasi1d_ph_v1")
        self.assertEqual(len(thermal["checks"]), 6)
        self.assertGreater(thermal["initialization_difference"]["molar_mass_relative"], 1e-4)
        self.assertEqual(result.summary["tc_outer_iterations_total"], 0)
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertNotIn("_current_regen_thermal_evidence", asdict(result))
        self.assertEqual(result.convergence["solution"]["chamber_temp_guess"], 3800.)
        self.assertEqual((self.model.inputs.chamber_temp_guess, self.model.inputs.chamber_gamma,
                          self.model.inputs.molar_mass), initial)
        self.assertIsNone(self.model._thermal_feedback_epoch)

    def test_saved_quasi1d_pass_is_not_current_production_evidence(self):
        self.model.inputs.use_target_preburner_temp = 0
        def solve(**kwargs):
            vector, result = inner_result(self.model, gamma=1.25, mw=23.)
            result.components["cooling_channel"] = SimpleNamespace(profile={
                "model_id": "regen_quasi1d_ph_v1", "hot_side_coupling": {"status": "pass"}})
            return vector, result
        with patch.object(self.model, "solve_cycle", side_effect=solve) as solver:
            result = self.model._evaluate_design_core()
        self.assertGreater(solver.call_count, 3)
        self.assertNotIn("source", result.convergence["layers"]["thermal"])
        self.assertEqual(numerical_convergence(result)["status"], "pass")

    def test_gamma_and_molecular_mass_must_close_even_when_temperature_passes(self):
        self.model.inputs.use_target_preburner_temp = 0  # core-only mode
        initial = (self.model.inputs.chamber_gamma, self.model.inputs.molar_mass)
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model, gamma=1.25, mw=23)):
            result = self.model._evaluate_design_core()
        trace = result.convergence["thermal_history"]
        self.assertGreaterEqual(len(trace), 2)
        self.assertLessEqual(len(trace), 4)
        self.assertEqual(trace[0]["residual_K"], 0)
        self.assertGreater(trace[0]["gamma_relative_residual"], 1e-4)
        self.assertGreater(trace[0]["molar_mass_relative_residual"], 1e-4)
        self.assertEqual(numerical_convergence(result)["status"], "pass")
        self.assertEqual((self.model.inputs.chamber_gamma, self.model.inputs.molar_mass), initial)

    def test_thermal_iteration_limit_is_failure_despite_converged_newton(self):
        original_tc = self.model.inputs.chamber_temp_guess
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model, tc=self.model.inputs.chamber_temp_guess + 10)):
            result = self.model._evaluate_design_core()
        self.assertEqual(result.convergence["layers"]["inner"]["status"], "pass")
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "fail")
        self.assertEqual(result.convergence["layers"]["thermal"]["termination_reason"], "max_iterations")
        self.assertEqual(len(result.convergence["thermal_history"]), 20)
        self.assertEqual(len(result.convergence["inner_runs"]), 22)
        self.assertEqual(numerical_convergence(result)["status"], "fail")
        self.assertEqual(self.model.inputs.chamber_temp_guess, original_tc)

    def test_nonstalled_inner_failure_stops_continuation_at_actual_stage(self):
        original_pc = self.model.inputs.pc
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model, passed=False)) as solve:
            result = self.model._evaluate_design_core()
        self.assertEqual(solve.call_count, 6)
        self.assertEqual(result.summary["chamber_pressure_Pa"], original_pc / 64)
        self.assertEqual(result.summary["evaluated_chamber_pressure_Pa"], original_pc / 64)
        self.assertEqual(result.inputs.pc, original_pc)
        self.assertEqual(result.convergence["layers"]["continuation"]["status"], "fail")
        self.assertEqual(result.convergence["layers"]["continuation"]["reached_ratio"], 0.0)
        self.assertEqual(result.convergence["layers"]["continuation"]["termination_reason"], "minimum_step_reached")

    def test_fixed_heat_load_disables_the_unused_thermal_feedback(self):
        self.model.inputs.use_target_preburner_temp = 0  # core-only mode
        self.model.inputs.cooling_bartz_mode = False
        self.model.inputs.cooling_dp_auto = 0
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model)):
            result = self.model._evaluate_design_core()
        self.assertEqual(len(result.convergence["inner_runs"]), 3)
        self.assertFalse(result.convergence["thermal_history"])
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "disabled")
        self.assertEqual(numerical_convergence(result)["status"], "pass")

    def test_deprecated_pressure_switch_cannot_enable_a_second_physical_model(self):
        self.model.inputs.use_target_preburner_temp = 0  # core-only mode
        self.model.inputs.cooling_bartz_mode = False
        self.model.inputs.cooling_dp_auto = 1
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model, gamma=1.25, mw=23)):
            result = self.model._evaluate_design_core()
        self.assertFalse(result.convergence["layers"]["thermal"]["enabled"])
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "disabled")
        self.assertEqual(result.convergence["thermal_history"], [])
        self.assertEqual(numerical_convergence(result)["status"], "pass")

    def test_nonfinite_thermal_properties_fail_and_do_not_mutate_inputs(self):
        gamma = self.model.inputs.chamber_gamma
        with patch.object(self.model, "solve_cycle", side_effect=lambda **kw:
                          inner_result(self.model, gamma=math.nan)):
            result = self.model._evaluate_design_core()
        self.assertEqual(result.convergence["layers"]["thermal"]["termination_reason"], "invalid_state")
        self.assertEqual(result.convergence["layers"]["thermal"]["status"], "fail")
        self.assertIsNone(result.convergence["thermal_history"][0]["gamma_output"])
        self.assertFalse(result.convergence["thermal_history"][0]["accepted"])
        self.assertEqual(self.model.inputs.chamber_gamma, gamma)

    def _temperature_core(self, temperatures):
        _, result = inner_result(self.model)
        c = result.convergence
        c["layers"]["continuation"] = self.model._convergence_layer(
            True, "pass", 0, 0, "1", 3, "target_reached")
        c["layers"]["thermal"] = self.model._convergence_layer(
            True, "pass", 0, 5, "K", 1, "tolerance_met")
        c["thermal_history"] = [dict(iteration=1,
                                    preburner_iteration=self.model._convergence_context["preburner_iteration"],
                                    residual_K=0)]
        result.summary.update(ox_preburner_T_combustion_K=temperatures[0],
                              fuel_preburner_T_combustion_K=temperatures[1],
                              ox_preburner_pressure_ratio=2, fuel_preburner_pressure_ratio=2)
        result.states = {"fuel_after_cooling": SimpleNamespace(temperature=350, enthalpy=-4.7e6),
                         "lox_after_pump": SimpleNamespace(temperature=90, enthalpy=-4e5)}
        return result

    def test_preburner_outer_keeps_all_runs_and_only_reports_evaluated_mr(self):
        inputs = self.model.inputs
        original_mr = inputs.ox_preburner_mr_target
        actual_mrs = []
        def core(*args, **kwargs):
            actual_mrs.append(inputs.ox_preburner_mr_target)
            offset = 10 if len(actual_mrs) == 1 else 0
            return self._temperature_core((inputs.ox_preburner_temp_target + offset,
                                           inputs.fuel_preburner_temp_target + offset))
        with patch.object(self.model, "_evaluate_design_core", side_effect=core), \
                patch.object(self.model, "_mr_for_target_temp", return_value=0.5):
            result = self.model._evaluate_temp_targeted()
        self.assertEqual(result.summary["ox_preburner_mr_solved_for_temp"], actual_mrs[-1])
        self.assertEqual(inputs.ox_preburner_mr_target, original_mr)
        self.assertEqual(result.summary["solver_inner_solves"], 2)
        self.assertEqual(result.summary["solver_iterations"], 2)
        self.assertEqual([r["id"] for r in result.convergence["inner_runs"]], [1, 2])
        self.assertLessEqual(len(result.convergence["thermal_history"]), 4)
        self.assertEqual(result.convergence["layers"]["preburner"]["status"], "pass")

    def test_preburner_limit_never_reports_an_unsolved_next_proposal(self):
        inputs = self.model.inputs
        actual_mrs = []
        def core(*args, **kwargs):
            actual_mrs.append(inputs.ox_preburner_mr_target)
            return self._temperature_core((inputs.ox_preburner_temp_target + 10,
                                           inputs.fuel_preburner_temp_target + 10))
        with patch.object(self.model, "_evaluate_design_core", side_effect=core), \
                patch.object(self.model, "_mr_for_target_temp", return_value=0.5) as inversion:
            result = self.model._evaluate_temp_targeted()
        self.assertEqual(len(actual_mrs), 12)
        self.assertEqual(inversion.call_count, 22)  # No unused proposal after last solve.
        self.assertEqual(result.summary["ox_preburner_mr_solved_for_temp"], actual_mrs[-1])
        self.assertEqual(result.convergence["layers"]["preburner"]["termination_reason"], "max_iterations")
        self.assertEqual(numerical_convergence(result)["status"], "fail")

    def test_failed_dependency_stops_preburner_adjustments(self):
        def core(*args, **kwargs):
            result = self._temperature_core((750, 850))
            result.convergence["layers"]["inner"].update(status="fail", termination_reason="max_iterations")
            return result
        with patch.object(self.model, "_evaluate_design_core", side_effect=core) as solve, \
                patch.object(self.model, "_mr_for_target_temp") as inversion:
            result = self.model._evaluate_temp_targeted()
        self.assertEqual(solve.call_count, 1)
        inversion.assert_not_called()
        self.assertEqual(result.convergence["layers"]["preburner"]["termination_reason"], "dependent_solver_failed")

    def test_temperature_inversion_failure_restores_inputs_and_records_failure(self):
        original = (self.model.inputs.ox_preburner_mr_target, self.model.inputs.fuel_preburner_mr_target)
        with patch.object(self.model, "_evaluate_design_core", side_effect=lambda *args, **kwargs:
                          self._temperature_core((900, 1000))), \
                patch.object(self.model, "_mr_for_target_temp", side_effect=ValueError("invalid temperature")):
            result = self.model._evaluate_temp_targeted()
        self.assertEqual(result.convergence["layers"]["preburner"]["termination_reason"], "temperature_inversion_failed")
        self.assertEqual((self.model.inputs.ox_preburner_mr_target,
                          self.model.inputs.fuel_preburner_mr_target), original)


if __name__ == "__main__":
    unittest.main()
