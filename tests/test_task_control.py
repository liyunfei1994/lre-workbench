"""Cancellation must preserve finished rows and never invent failed physics rows."""
import os
from dataclasses import asdict
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThreadPool
from core import EngineInputs
from cycle import FFSCCycleModel
from gg_cycle import GGCycleModel
from gui_pyside6 import SolveWorker, ThrottleWorker, ThermoWorker, OFSweepWorker
from task_control import CancellationToken, TaskCancelled, cancellation_scope, checkpoint
import sweep
import thermo


def fake_result():
    return SimpleNamespace(
        summary={"solver_residual_norm": 1e-5},
        components={"nozzle": SimpleNamespace(outputs={"At_m2": 0.1})})


class CancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def collect(self, worker):
        events = {"finished": [], "cancelled": [], "error": [], "record_ready": []}
        for name, values in events.items():
            getattr(worker.signals, name).connect(values.append)
        return events

    def test_scope_restores_token_after_cancel_and_nested_job(self):
        outer, inner = CancellationToken(), CancellationToken()
        with cancellation_scope(outer):
            with cancellation_scope(inner):
                inner.cancel()
                with self.assertRaises(TaskCancelled):
                    checkpoint()
            checkpoint()
            outer.cancel()
            with self.assertRaises(TaskCancelled):
                checkpoint()
        checkpoint()  # Reusing a pool thread must not inherit its previous token.

    def test_pre_cancelled_workers_do_not_start_calculations(self):
        workers = [SolveWorker(EngineInputs()), ThrottleWorker(EngineInputs()),
                   ThermoWorker([3.4], [40], 30e6, 110, 90),
                   OFSweepWorker(EngineInputs(), "ox", [80])]
        for worker in workers:
            with self.subTest(worker=type(worker).__name__):
                events = self.collect(worker)
                worker.request_cancel()
                with patch("gui_pyside6.make_cycle_model") as make_model:
                    worker.run()
                make_model.assert_not_called()
                self.assertEqual(len(events["cancelled"]), 1)
                self.assertFalse(events["finished"])
                self.assertFalse(events["error"])

    def test_both_sweep_sides_stream_before_next_point_and_keep_cancelled_rows(self):
        for side, targets in (("ox", [80, 81]), ("fuel", [0.2, 0.21])):
            with self.subTest(side=side):
                worker = OFSweepWorker(EngineInputs(), side, targets)
                events = self.collect(worker)
                worker.signals.record_ready.connect(lambda record: worker.request_cancel())
                with patch.object(sweep.FFSCCycleModel, "evaluate", return_value=fake_result()) as solve:
                    worker.run()
                self.assertEqual(solve.call_count, 1)
                self.assertEqual(len(events["record_ready"]), 1)
                payload = events["cancelled"][0]
                self.assertEqual(payload["kind"], "of_sweep")
                self.assertEqual(payload["side"], side)
                self.assertEqual(payload["inputs"]["cycle_type"], "ffsc")
                self.assertEqual(payload["records"], events["record_ready"])
                self.assertEqual(payload["records"][0]["of_target"], targets[0])
                self.assertEqual(payload["records"][0]["error"], "")
                self.assertFalse(events["finished"])
                self.assertFalse(events["error"])

    def test_cancel_inside_sweep_solve_is_not_a_failed_scan_row(self):
        token = CancellationToken()
        records = []
        def cancelled_solve(*args, **kwargs):
            token.cancel()
            checkpoint()
        with cancellation_scope(token):
            with patch.object(sweep.FFSCCycleModel, "evaluate", side_effect=cancelled_solve):
                with self.assertRaises(TaskCancelled):
                    sweep.sweep_ox_preburner_mr(EngineInputs(), [80], record_callback=records.append)
        self.assertEqual(records, [])

    def test_both_design_models_stop_in_numerical_iteration(self):
        for model_class in (FFSCCycleModel, GGCycleModel):
            with self.subTest(model=model_class.__name__):
                token = CancellationToken()
                model = model_class(EngineInputs())
                n = 3 if model_class is FFSCCycleModel else 2
                result = fake_result()
                with patch.object(model, "residual_vector", return_value=([1e6] * n, result)) as residual:
                    with cancellation_scope(token):
                        with self.assertRaises(TaskCancelled):
                            model.solve_cycle(progress_callback=lambda *args: token.cancel())
                self.assertEqual(residual.call_count, 1)

    def test_throttle_models_preserve_one_completed_record_on_cancel(self):
        from web_tests.test_throttle_analysis import solved_fixture
        for model_class in (FFSCCycleModel, GGCycleModel):
            with self.subTest(model=model_class.__name__):
                inputs = EngineInputs()
                inputs.cooling_marching_enable = 0
                inputs.cooling_channel_auto_size = 0
                inputs.cooling_bartz_mode = 0  # Analytic fixture is an explicit heat budget.
                inputs.throttle_steps = 3
                token, records = CancellationToken(), []
                def on_record(record):
                    records.append(record)
                    token.cancel()
                with patch.object(model_class, "evaluate", return_value=solved_fixture(asdict(inputs))):
                    with cancellation_scope(token):
                        with self.assertRaises(TaskCancelled):
                            model_class(inputs).throttle_sweep(record_callback=on_record)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["error"], "")

    def test_thermo_worker_keeps_completed_points_when_cancelled(self):
        worker = ThermoWorker([3.4, 3.6], [40], 30e6, 110, 90)
        events = self.collect(worker)
        worker.signals.record_ready.connect(lambda record: worker.request_cancel())
        with patch("thermo.cross_validate_cea_cantera", return_value=[{"of": 3.4, "eps": 40}]) as compute:
            worker.run()
        self.assertEqual(compute.call_count, 1)
        self.assertEqual(events["cancelled"][0]["records"], events["record_ready"])
        self.assertFalse(events["finished"])
        self.assertFalse(events["error"])

    def test_native_thermo_equilibrium_cancel_bypasses_fallback(self):
        token = CancellationToken()
        class Gas:
            def equilibrate(self, mode):
                token.cancel()  # Simulate cancellation while a native call was running.
        with cancellation_scope(token):
            with patch("thermo._equilibrate_HP_bisection") as fallback:
                with self.assertRaises(TaskCancelled):
                    thermo._equilibrate_HP(Gas(), -1e6, 30e6, "CH4:1,O2:1")
        fallback.assert_not_called()

    def test_normal_sweep_finished_payload_remains_compatible(self):
        worker = OFSweepWorker(EngineInputs(), "ox", [80, 81])
        events = self.collect(worker)
        with patch.object(sweep.FFSCCycleModel, "evaluate", return_value=fake_result()):
            worker.run()
        payload = events["finished"][0]
        self.assertEqual(payload["side"], "ox")
        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual(payload["records"], events["record_ready"])
        self.assertFalse(events["cancelled"])
        self.assertFalse(events["error"])

    def test_real_design_worker_cancels_after_calculation_has_started(self):
        if thermo.CANTERA_AVAILABLE:
            gas = thermo._get_gas()
            self.assertEqual(gas.n_species, 53)
            self.assertEqual(gas.n_reactions, 325)
        # Exercise the real solver thread and cancellation checkpoints with an
        # explicit heat/pressure budget. The legacy marching flag only omits
        # a saved-profile view; it no longer disables the coupled cooling solve.
        # Physical cooling integration has separate conservation/coupling tests.
        inputs = EngineInputs(cooling_bartz_mode=0., cooling_heat_load=90e6,
                              cooling_dp_pa=5e6)
        worker = SolveWorker(inputs)
        worker.setAutoDelete(False)
        events = self.collect(worker)
        progress = []
        worker.signals.progress.connect(lambda *args: progress.append(args))
        pool = QThreadPool()
        pool.setMaxThreadCount(1)
        pool.start(worker)
        deadline = time.monotonic() + 10
        while not progress and not events["finished"] and not events["error"] and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        worker.request_cancel()
        cancelled_at = time.monotonic()
        while not events["cancelled"] and pool.activeThreadCount() and time.monotonic() - cancelled_at < 5:
            self.app.processEvents()
            time.sleep(0.005)
        pool.waitForDone(5000)
        self.app.processEvents()
        self.assertTrue(progress, "Real solver never reached its first iteration")
        self.assertLess(time.monotonic() - cancelled_at, 5)
        self.assertEqual(len(events["cancelled"]), 1)
        self.assertEqual(events["cancelled"][0]["kind"], "solve")
        self.assertFalse(events["finished"])
        self.assertFalse(events["error"])


if __name__ == "__main__":
    unittest.main()
