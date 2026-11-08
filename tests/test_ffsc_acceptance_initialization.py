"""Controlled residual tests: task acceptance and pre-iteration failures."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.domain.models import ComponentResult, CycleResults, EngineInputs, ValidationError
from lre_workbench.systems.ffsc import FFSCCycleModel
from lre_workbench.solvers.convergence import numerical_convergence
from lre_workbench.solvers.nonlinear import ModelDomainError
from lre_workbench.solvers.attempt import SolverInitializationError
from lre_workbench.solvers.acceptance import ffsc_acceptance_policy
from lre_workbench.runtime.tasks import TaskCancelled
from tests.test_ffsc_convergence import inner_result


class TaskAcceptanceTests(unittest.TestCase):
    def test_absolute_tolerances_are_validated_and_preserved_in_input_roundtrip(self):
        for key in ("steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"):
            for bad in (0, -1, math.nan, math.inf, True, "1"):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValidationError):
                    EngineInputs(**{key: bad})
        inputs = EngineInputs(steady_ox_power_tolerance_W=.25, steady_fuel_power_tolerance_W=.75)
        self.assertEqual(EngineInputs.from_dict(asdict(inputs)), inputs)

    def test_initials_and_numerical_scales_cannot_change_physical_acceptance(self):
        for backend in ("newton", "broyden", "trf"):
            for tolerance, expected in ((1., "fail"), (2., "pass")):
                outputs = []
                for initial in (10., 20.):
                    inputs = EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0,
                        nonlinear_solver=backend, steady_ox_power_tolerance_W=tolerance,
                        steady_fuel_power_tolerance_W=tolerance)
                    model = FFSCCycleModel(inputs)
                    def residual(total, *unused):
                        result = CycleResults(model.inputs, {}, {}, {
                            key: ComponentResult(key, outputs={"power_W": total * 1e5})
                            for key in ("ox_pump", "fuel_pump")}, [])
                        return [0., 1.5, 1.5], result
                    with patch.object(model, "residual_vector", side_effect=residual):
                        _, result = model.solve_cycle(x_init=[initial, 2, 2], max_iter=0)
                    result.convergence["layers"]["continuation"] = model._convergence_layer(
                        True, "pass", 0., 0., "1", 1, "target_reached", reached_ratio=1.)
                    self.assertEqual(result.convergence["layers"]["inner"]["status"], expected)
                    self.assertEqual(numerical_convergence(result)["status"], expected)
                    self.assertEqual(result.summary["solver_residual_norm"], 1.5 / tolerance)
                    outputs.append(result)
                runs = [result.convergence["inner_runs"][0] for result in outputs]
                self.assertEqual(runs[0]["physical_tolerances"], runs[1]["physical_tolerances"])
                self.assertEqual(runs[0]["acceptance_policy"], runs[1]["acceptance_policy"])
                self.assertNotEqual(runs[0]["residual_scales"], runs[1]["residual_scales"])
                self.assertNotEqual(outputs[0].summary["solver_numerical_residual_norm"],
                                    outputs[1].summary["solver_numerical_residual_norm"])

    def test_named_initial_guess_parameters_do_not_enter_acceptance_policy(self):
        inputs = EngineInputs()
        policy = ffsc_acceptance_policy(inputs)
        inputs.target_isp *= 1.5
        inputs.chamber_temp_guess *= 1.2
        inputs.ox_preburner_pressure_ratio *= 1.4
        inputs.ox_preburner_mr_target *= 1.1
        self.assertEqual(ffsc_acceptance_policy(inputs), policy)

    def test_continuation_stages_share_original_task_thrust_reference(self):
        model = FFSCCycleModel(EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0))
        seen = []
        def solve(**kwargs):
            seen.append((model.inputs.thrust, deepcopy(kwargs["acceptance_policy"])))
            return inner_result(model)
        with patch.object(model, "solve_cycle", side_effect=solve):
            model._evaluate_design_core()
        self.assertGreater(len({thrust for thrust, _ in seen}), 1)
        self.assertTrue(all(policy == seen[0][1] for _, policy in seen))
        self.assertEqual(seen[0][1]["task_thrust_N"], model.inputs.thrust)

    def test_progress_uses_acceptance_norm_not_numerical_scaling(self):
        model = FFSCCycleModel(EngineInputs())
        payload = CycleResults(model.inputs, {}, {}, {}, [])
        progress = []
        with patch.object(model, "residual_vector", return_value=([0, .5, .25], payload)):
            _, result = model.solve_cycle(progress_callback=lambda *args: progress.append(args))
        self.assertEqual(progress[-1][-1], result.summary["solver_residual_norm"])
        self.assertEqual(progress[-1][-1], .5)
        self.assertNotEqual(progress[-1][-1], result.summary["solver_numerical_residual_norm"])


class InitializationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.model = FFSCCycleModel(EngineInputs(cooling_bartz_mode=1))
        self.before = asdict(self.model.inputs)

    def seed(self):
        return CycleResults(self.model.inputs,
            dict(ox_preburner_pressure_Pa=5e7, fuel_preburner_pressure_Pa=5.1e7,
                 chamber_temperature_K=3500., chamber_gamma=1.2, chamber_molar_mass=20.),
            {"fuel_after_cooling": SimpleNamespace(temperature=350., enthalpy=-4.7e6),
             "lox_after_pump": SimpleNamespace(temperature=90., enthalpy=-4e5)}, {}, [])

    def assert_failure(self, callable_, stage):
        with self.assertRaises(SolverInitializationError) as caught:
            callable_()
        evidence = caught.exception.initialization_diagnostics
        self.assertEqual(evidence["failure"]["stage"], stage)
        self.assertEqual(evidence["status"], "failed")
        self.assertFalse(evidence["formal_iteration_started"])
        self.assertEqual(evidence["attempted_inputs"], self.before)
        self.assertEqual(asdict(self.model.inputs), self.before)
        self.assertIn("不能据此判定物理无解", str(caught.exception))
        json.dumps(evidence, allow_nan=False)
        return evidence

    def test_guess_preparation_failure_has_zero_model_calls(self):
        with patch.object(self.model, "_initial_guess_vector", side_effect=ValueError("bad seed")):
            evidence = self.assert_failure(lambda: self.model.evaluate(include_cooling_marching=False), "prepare_initial_guess")
        self.assertEqual(evidence["counts"]["forward_attempted"], 0)
        self.assertEqual(evidence["counts"]["inversion_attempted"], 0)
        self.assertEqual(evidence["x"], [])

    def test_forward_failure_records_actual_seed_component_and_original_type(self):
        error = ModelDomainError("seed outside domain", component="mock_component", variable="mock_pressure")
        with patch.object(self.model, "_initial_guess_vector", return_value=[10, 2, 3]), \
                patch.object(self.model, "_evaluate_once", side_effect=error), \
                patch.object(self.model, "_evaluate_design_core") as core:
            evidence = self.assert_failure(lambda: self.model.evaluate(include_cooling_marching=False), "forward_seed")
        self.assertEqual(core.call_count, 0)
        self.assertEqual(evidence["x"], [10, 2, 3])
        self.assertEqual(evidence["counts"]["forward_attempted"], 1)
        self.assertEqual(evidence["counts"]["forward_completed"], 0)
        self.assertEqual(evidence["failure"]["component"], "mock_component")
        self.assertEqual(evidence["failure"]["variable"], "mock_pressure")
        self.assertEqual(evidence["failure"]["error_type"], "ModelDomainError")

    def test_invalid_forward_payload_still_counts_its_completed_call(self):
        seed = self.seed();seed.states.clear()
        with patch.object(self.model, "_evaluate_once", return_value=seed):
            evidence = self.assert_failure(lambda: self.model.evaluate(include_cooling_marching=False), "prepare_temperature_inversion")
        self.assertEqual(evidence["counts"]["forward_completed"], 1)
        self.assertEqual(evidence["counts"]["inversion_attempted"], 0)

    def test_untyped_forward_failure_reports_location_without_inventing_component(self):
        def failing_forward(*args):
            raise ValueError("untyped property failure")
        with patch.object(self.model, "_evaluate_once", side_effect=failing_forward):
            evidence = self.assert_failure(lambda: self.model.evaluate(include_cooling_marching=False), "forward_seed")
        self.assertFalse(evidence["failure"]["component_identified"])
        self.assertIn("具体部件未定位", evidence["failure"]["component"])
        self.assertEqual(evidence["failure"]["location"]["function"], "failing_forward")
        self.assertGreater(evidence["failure"]["location"]["line"], 0)

    def test_second_inversion_failure_keeps_completed_stages_and_restores_inputs(self):
        with patch.object(self.model, "_evaluate_once", return_value=self.seed()), \
                patch.object(self.model, "_mr_for_target_temp", side_effect=[60., ValueError("no valid bracket")]):
            evidence = self.assert_failure(lambda: self.model.evaluate(include_cooling_marching=False), "fuel_temperature_inversion")
        self.assertEqual(evidence["counts"]["forward_completed"], 1)
        self.assertEqual(evidence["counts"]["inversion_attempted"], 2)
        self.assertEqual(evidence["counts"]["inversion_completed"], 1)
        self.assertEqual(evidence["steps"][-2]["status"], "passed")
        self.assertEqual(evidence["steps"][-1]["calls_completed"], 0)

    def test_actual_inversion_property_calls_are_counted_on_success(self):
        def chemistry(**kwargs):
            ratio = kwargs["mdot_ox"]
            temperature = 2000 - 10 * ratio if ratio >= 15 else 400 + 1000 * ratio
            return SimpleNamespace(temperature=temperature)
        with patch.object(self.model, "_evaluate_once", return_value=self.seed()), \
                patch('lre_workbench.properties.thermochemistry.combustion_equilibrium', side_effect=chemistry) as calls:
            evidence = self.model._initialize_preburner_temperature_guess()
        self.assertEqual(evidence["status"], "passed")
        self.assertFalse(evidence["formal_iteration_started"])
        self.assertEqual(evidence["counts"]["forward_attempted"], 1)
        self.assertEqual(evidence["counts"]["forward_completed"], 1)
        self.assertEqual(evidence["counts"]["inversion_completed"], 2)
        self.assertEqual(evidence["counts"]["property_attempted"], calls.call_count)
        self.assertEqual(evidence["counts"]["property_completed"], calls.call_count)
        self.assertIsNone(evidence["component_internal_property_calls"])

    def test_failed_thermochemistry_records_actual_candidate_not_only_target(self):
        def failing_chemistry(**arguments):
            raise ValueError("candidate enthalpy outside property domain")
        with patch.object(self.model, "_evaluate_once", return_value=self.seed()), \
                patch('lre_workbench.properties.thermochemistry.combustion_equilibrium', side_effect=failing_chemistry) as calls:
            evidence = self.assert_failure(lambda: self.model.evaluate(include_cooling_marching=False), "ox_temperature_inversion")
        self.assertEqual(evidence["counts"]["property_attempted"], calls.call_count)
        self.assertEqual(evidence["counts"]["property_completed"], 0)
        candidate = evidence["last_property_failure"]
        self.assertEqual(candidate, evidence["last_property_call"])
        self.assertEqual(candidate["status"], "failed")
        self.assertEqual(candidate["attempted_inputs"]["mdot_ox"], calls.call_args.kwargs["mdot_ox"])
        self.assertEqual(candidate["attempted_inputs"]["oxygen_fuel_mass_ratio"], 160.)
        self.assertEqual(candidate["input_units"]["pressure"], "Pa")
        self.assertEqual(candidate["location"]["function"], "failing_chemistry")
        self.assertIn("candidate enthalpy", candidate["message"])

    def test_cancelled_inversion_is_not_persisted_as_numerical_failure(self):
        with patch.object(self.model, "_evaluate_once", return_value=self.seed()), \
                patch.object(self.model, "_mr_for_target_temp", side_effect=[60., TaskCancelled()]):
            with self.assertRaises(TaskCancelled):
                self.model.evaluate(include_cooling_marching=False)
        self.assertEqual(asdict(self.model.inputs), self.before)


if __name__ == "__main__":
    unittest.main()
