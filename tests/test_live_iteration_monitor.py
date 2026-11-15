"""Streaming observations use evaluated states and survive incomplete solves."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lre_workbench.domain.models import CycleResults, EngineInputs
from lre_workbench.runtime.tasks import TaskCancelled
from lre_workbench.solvers.monitor import IterationMonitor, MonitorCallbackError, monitor_run
from lre_workbench.solvers.nonlinear import solve_nonlinear
from lre_workbench.systems.ffsc import FFSCCycleModel
from lre_workbench.systems.regenerative import close_regen_cycle
from tests.test_ffsc_convergence import inner_result
from tests import test_regen_coupling


class AcceptedPointTests(unittest.TestCase):
    def test_all_backends_observe_exact_accepted_payload_without_extra_calls(self):
        for backend in ("newton", "broyden", "trf"):
            with self.subTest(backend=backend):
                def run(observe):
                    calls, samples = [], []
                    def model(x):
                        calls.append(x[:])
                        return [x[0] ** 2 - 2.], {"physical": x[0], "call": len(calls)}
                    def accepted(point, payload):
                        self.assertEqual(point["x"][0], payload["physical"])
                        samples.append((deepcopy(point), deepcopy(payload)))
                    result = solve_nonlinear(model, [1.], [.1], [5.], residual_scales=[1.],
                        variable_scales=[1.], backend=backend,
                        accepted_callback=accepted if observe else None)
                    return result, calls, samples
                plain, plain_calls, _ = run(False)
                result, calls, samples = run(True)
                self.assertEqual(calls, plain_calls)
                self.assertEqual(result.x, plain.x)
                self.assertEqual(result.nfev, len(calls))
                self.assertEqual(len(samples), len(result.points))
                self.assertLess(len(samples), len(calls))

    def test_ffsc_stream_matches_physical_residuals_and_carries_fail_end(self):
        model = FFSCCycleModel(EngineInputs())
        events, calls = [], []
        model._iteration_monitor = IterationMonitor(events.append)
        model._convergence_context = dict(stage_ratio=.5, continuation_attempt=2)
        def residual(*x):
            calls.append(x)
            raw = [(x[0] - 11.) * 1e5, (x[1] - 2.1) * 1e5, (x[2] - 3.1) * 1e5]
            payload = CycleResults(model.inputs,
                dict(thrust_N=model.inputs.thrust + raw[0], Isp_s=x[0] * 10), {}, {}, [])
            return raw, payload
        with patch.object(model, "residual_vector", side_effect=residual):
            _, result = model.solve_cycle(x_init=[10., 2., 3.], max_iter=0)
        points = [event for event in events if event["kind"] == "point"]
        self.assertEqual(len(points), 1)
        self.assertEqual(len(calls), 1)
        point = points[0]
        self.assertEqual(point["values"]["mass_flow_kg_s"], 10.)
        self.assertEqual(point["values"]["Isp_s"], 100.)
        self.assertEqual(point["values"]["thrust_residual_N"], -1e5)
        self.assertEqual(point["values"]["acceptance_norm"], result.summary["solver_residual_norm"])
        self.assertEqual(point["context"]["continuation_attempt"], 2)
        self.assertEqual(events[-1]["status"], "fail")
        self.assertEqual(events[-1]["termination_reason"], "max_iterations")
        json.dumps(events, allow_nan=False)

    def test_legacy_split_coordinates_are_not_mislabelled_pressure_ratios(self):
        model = FFSCCycleModel(EngineInputs(use_target_preburner_temp=0, use_target_preburner_mr=0))
        events = []
        model._iteration_monitor = IterationMonitor(events.append)
        with patch.object(model, "residual_vector", return_value=([0, 0, 0], CycleResults(model.inputs, {}, {}, {}, []))):
            model.solve_cycle()
        values = next(e["values"] for e in events if e["kind"] == "point")
        self.assertIn("fuel_crossflow_fraction", values)
        self.assertNotIn("ox_pressure_ratio", values)

    @patch('lre_workbench.components.pumps.model.fluid_density', return_value=400.)
    def test_nested_thermal_distinguishes_jacobian_probes_from_accepted_iterates(self, _):
        fixture = test_regen_coupling.CouplingTests()
        model, events = FFSCCycleModel(EngineInputs()), []
        model._iteration_monitor = IterationMonitor(events.append)
        def residual(*x):
            close_regen_cycle(model, lambda: fixture.evaluation(model))
            return [(x[0]-11)*1e5, (x[1]-2.1)*1e5, (x[2]-3.1)*1e5], CycleResults(model.inputs, {}, {}, {}, [])
        with patch.object(model, "residual_vector", side_effect=residual):
            model.solve_cycle(x_init=[10, 2, 3], max_iter=1)
        starts = [e for e in events if e["layer"] == "thermal" and e["kind"] == "start"]
        self.assertEqual([e["context"]["evaluation_role"] for e in starts],
                         ["initial", "finite_difference", "finite_difference", "finite_difference", "trial"])
        self.assertEqual([e["context"]["model_evaluation"] for e in starts], [1, 2, 3, 4, 5])
        inner_points = [e for e in events if e["layer"] == "inner" and e["kind"] == "point"]
        self.assertEqual(len(inner_points), 2)
        self.assertTrue(all(e["context"]["inner_run_id"] == inner_points[0]["run_id"] for e in starts))

    def test_web_dispatch_forwards_monitor_without_including_it_in_input_snapshot(self):
        from webapp.solver import calculate_design_job
        events = []
        inputs = asdict(EngineInputs())
        callback = events.append
        job = dict(inputs=json.dumps(inputs), kind="design")
        with patch('webapp.solver.calculate', return_value={}) as calculate:
            calculate_design_job(job, monitor_callback=callback)
        self.assertEqual(calculate.call_args.args[0], inputs)
        self.assertIs(calculate.call_args.kwargs["monitor_callback"], callback)


class LayerMonitorTests(unittest.TestCase):
    @patch('lre_workbench.components.pumps.model.fluid_density', return_value=400.)
    def test_thermal_sink_failure_is_not_retried_as_a_physics_failure(self, _):
        fixture = test_regen_coupling.CouplingTests()
        for backend in ("newton", "broyden", "trf"):
            for failure_role, expected_calls in (("initial", 1), ("finite_difference", 2)):
                with self.subTest(backend=backend, failure_role=failure_role):
                    model = FFSCCycleModel(EngineInputs(use_target_preburner_temp=0, nonlinear_solver=backend))
                    events, calls, failed_writes = [], [], []
                    sink_error = OSError("controlled disk write failure")
                    def collect(event):
                        events.append(event)
                        if (event["layer"] == "thermal" and event["kind"] == "point"
                                and event["context"].get("evaluation_role") == failure_role):
                            failed_writes.append(event)
                            raise sink_error
                    def residual(*x):
                        calls.append(x)
                        close_regen_cycle(model, lambda: fixture.evaluation(model))
                        return [(x[0]-11)*1e5, (x[1]-2.1)*1e5, (x[2]-3.1)*1e5], CycleResults(model.inputs, {}, {}, {}, [])
                    with patch.object(model, "residual_vector", side_effect=residual):
                        with self.assertRaises(MonitorCallbackError) as caught:
                            model.evaluate(monitor_callback=collect, include_cooling_marching=False)
                    self.assertIs(caught.exception.__cause__, sink_error)
                    self.assertEqual(len(failed_writes), 1)
                    self.assertEqual(len(calls), expected_calls)
                    self.assertFalse(any(e["layer"] == "continuation" and e["kind"] == "point" for e in events))
                    self.assertIsNone(model._iteration_monitor)

    @patch('lre_workbench.components.pumps.model.fluid_density', return_value=400.)
    def test_sink_failure_during_temperature_initialization_is_not_wrapped_as_model_failure(self, _):
        fixture = test_regen_coupling.CouplingTests()
        model, events, calls = FFSCCycleModel(EngineInputs()), [], []
        def collect(event):
            events.append(event)
            if event["layer"] == "thermal" and event["kind"] == "point":
                raise RuntimeError("controlled storage failure")
        def forward(*x):
            calls.append(x)
            return close_regen_cycle(model, lambda: fixture.evaluation(model))
        with patch.object(model, "_evaluate_once", side_effect=forward):
            with self.assertRaises(MonitorCallbackError) as caught:
                model.evaluate(monitor_callback=collect, include_cooling_marching=False)
        self.assertEqual(type(caught.exception.__cause__), RuntimeError)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(model._iteration_monitor)

    def test_continuation_rejected_attempt_has_distinct_inner_run_and_no_false_progress(self):
        model = FFSCCycleModel(EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0))
        events = []
        model._iteration_monitor = IterationMonitor(events.append)
        failed = False
        def residual(*x):
            nonlocal failed
            ratio = model._convergence_context["stage_ratio"]
            if ratio == .75 and not failed:
                failed = True
                raise ValueError("controlled initial evaluation failure")
            return [0., 0., 0.], CycleResults(model.inputs, {}, {}, {}, [])
        with patch.object(model, "residual_vector", side_effect=residual):
            result = model._evaluate_design_core()
        points = [e for e in events if e["layer"] == "continuation" and e["kind"] == "point"]
        self.assertEqual([e["values"]["stage_ratio"] for e in points[:4]], [.5, .75, .625, .75])
        self.assertFalse(points[1]["accepted"])
        self.assertEqual(points[1]["values"]["reached_ratio"], .5)
        failed_ends = [e for e in events if e["layer"] == "inner" and e["kind"] == "end" and e["status"] == "fail"]
        self.assertEqual(len(failed_ends), 1)
        inner_starts = [e for e in events if e["layer"] == "inner" and e["kind"] == "start"]
        self.assertEqual(len({e["run_id"] for e in inner_starts}), len(inner_starts))
        self.assertTrue(all(e["context"]["parent_run_id"] == events[0]["run_id"] for e in inner_starts))
        self.assertEqual(result.convergence["layers"]["continuation"]["status"], "pass")

    def test_preburner_failure_before_first_iterate_emits_end_and_restores_monitor(self):
        model = FFSCCycleModel(EngineInputs())
        events = []
        before = asdict(model.inputs)
        with patch.object(model, "_initialize_preburner_temperature_guess", side_effect=ValueError("initialization failed")):
            with self.assertRaisesRegex(ValueError, "initialization failed"):
                model.evaluate(monitor_callback=events.append, include_cooling_marching=False)
        self.assertEqual([e["kind"] for e in events], ["start", "end"])
        self.assertEqual(events[-1]["status"], "fail")
        self.assertIsNone(model._iteration_monitor)
        self.assertEqual(before, asdict(model.inputs))

    def test_cooperative_cancel_keeps_initial_point_and_emits_interruption(self):
        model = FFSCCycleModel(EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0))
        events = []
        def collect(event):
            events.append(event)
            if event["kind"] == "point" and event["layer"] == "inner":
                raise TaskCancelled()
        with patch.object(model, "residual_vector", return_value=([100., 0., 0.], CycleResults(model.inputs, {}, {}, {}, []))):
            with self.assertRaises(TaskCancelled):
                model.evaluate(monitor_callback=collect, include_cooling_marching=False)
        self.assertTrue(any(e["kind"] == "point" for e in events))
        self.assertEqual(events[-1]["status"], "interrupted")
        self.assertIsNone(model._iteration_monitor)

    @patch('lre_workbench.components.pumps.model.fluid_density', return_value=400.)
    def test_actual_thermal_closure_streams_before_next_evaluation_and_retains_failure(self, _):
        fixture = test_regen_coupling.CouplingTests()
        model, events, evaluation_count = fixture.model(), [], 0
        model._iteration_monitor = IterationMonitor(events.append)
        def evaluate():
            nonlocal evaluation_count
            evaluation_count += 1
            if evaluation_count == 2:
                self.assertEqual(len([e for e in events if e["kind"] == "point"]), 1)
                raise ValueError("thermal evaluation interrupted")
            return fixture.evaluation(model)
        with monitor_run(model, "inner"):
            with self.assertRaisesRegex(ValueError, "thermal evaluation interrupted"):
                close_regen_cycle(model, evaluate)
        points = [e for e in events if e["kind"] == "point"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["context"]["scope"], "model_evaluation")
        self.assertEqual(points[0]["context"]["parent_run_id"], events[0]["run_id"])
        self.assertNotIn("accepted", points[0])
        self.assertEqual(points[0]["values"]["acceptance_norm"], 5e7)
        self.assertEqual(events[-1]["status"], "fail")
        json.dumps(events, allow_nan=False)

    def test_nonfinite_thermal_values_are_missing_not_invented(self):
        model = FFSCCycleModel(EngineInputs(use_target_preburner_temp=0))
        events = []
        model._iteration_monitor = IterationMonitor(events.append)
        with patch.object(model, "solve_cycle", side_effect=lambda **kw: inner_result(model, gamma=float("nan"))):
            model._evaluate_design_core()
        point = next(e for e in events if e["layer"] == "thermal" and e["kind"] == "point")
        self.assertIsNone(point["values"]["acceptance_norm"])
        self.assertFalse(point["accepted"])
        json.dumps(events, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
