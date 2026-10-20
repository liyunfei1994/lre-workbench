"""Inclusive scan validation, full candidate replay, and streamed cancellation."""
from copy import deepcopy
from dataclasses import asdict
import inspect
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication, QMessageBox
from core import EngineInputs
from cycle import FFSCCycleModel
from gui_pyside6 import WorkerSignals
from scan_parameters import build_scan_points, verify_scan_replay
from sweep import sweep_ox_preburner_mr
from workbench import WorkbenchWindow
from workbench_fonts import configure_fonts


class ScanGridTests(unittest.TestCase):
    def test_nan_inf_underflow_invalid_values_never_form_a_grid(self):
        bad = ("nan", "sNaN", "inf", "-inf", "1e999", "1e-999", "", "words", "0", "-1")
        for value in bad:
            for position in range(3):
                with self.subTest(value=value, position=position):
                    args = ["60", "110", "1"]
                    args[position] = value
                    with self.assertRaises(ValueError):
                        build_scan_points(*args)

    def test_large_grid_reports_true_count_and_does_not_silently_cap(self):
        with self.assertRaisesRegex(ValueError, "5,001.*200"):
            build_scan_points("60", "110", "0.01")

    def test_includes_end_with_shorter_final_interval(self):
        points = build_scan_points("60", "61", "0.3")
        self.assertEqual(points, [60, 60.3, 60.6, 60.9, 61])
        self.assertLess(points[-1] - points[-2], 0.3)

    def test_exact_limit_and_single_point(self):
        self.assertEqual(build_scan_points("1", "200", "1"), list(range(1, 201)))
        self.assertEqual(build_scan_points("80", "80", "0.01"), [80])

    def test_grid_rejects_float_resolution_loss(self):
        with self.assertRaisesRegex(ValueError, "精度"):
            build_scan_points("100000000000000000000", "100000000000000000001", "1")


class FakeSweepWorker:
    def __init__(self, inputs, side, points):
        self.inputs, self.side, self.of_list = inputs, side, points
        self.signals = WorkerSignals()
        self.cancel_requested = False

    def request_cancel(self):
        self.cancel_requested = True


class ScanUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_fonts(cls.app)
        cls.recovery_directory = tempfile.TemporaryDirectory(prefix="lre-scan-ui-")
        kwargs = ({"recovery_directory": cls.recovery_directory.name}
                  if "recovery_directory" in inspect.signature(WorkbenchWindow).parameters else {})
        cls.window = WorkbenchWindow(**kwargs)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._saved_signature = cls.window._signature()
        remember = getattr(cls.window, "_remember_saved_case", None)
        if callable(remember):
            remember()
        cls.window.close()
        cls.app.processEvents()
        cls.recovery_directory.cleanup()

    def setUp(self):
        w = self.window
        w._busy = False
        remember = getattr(w, "_remember_saved_case", None)
        if callable(remember):
            remember()
        w._saved_signature = w._signature()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            w.reset_defaults()
        w.last_of_sweep = None
        w._applied_scan_record = None
        w._last_scan_replay_check = None
        w._saved_signature = w._signature()
        if callable(remember):
            remember()
        self.app.processEvents()

    def select_candidate(self, source, record):
        w = self.window
        w.last_of_sweep = {"side": record["side"], "records": [record], "inputs": source, "complete": True}
        w._populate_of_sweep_table([record])
        w.of_sweep_table.selectRow(0)

    def test_apply_restores_entire_source_snapshot_and_fixed_mr_mode(self):
        w = self.window
        source = asdict(w._read_inputs())
        source["thrust"] = 1950123.456789
        hidden = next(key for key, value in source.items()
                      if key not in w.input_edits and isinstance(value, float) and value > 0)
        source[hidden] *= 1.00001
        original = deepcopy(source)
        candidate = {"side": "ox", "of_target": 65.123456789, "converged": True, "error": ""}
        self.select_candidate(source, candidate)
        w.input_edits["thrust"].setText("1500")
        w.input_edits["pc"].setText("25")
        w._extra_inputs[hidden] = source[hidden] * 1.1
        w._on_of_sweep_apply()
        expected = dict(source, use_target_preburner_temp=0.0, use_target_preburner_mr=1.0,
                        ox_preburner_mr_target=candidate["of_target"])
        actual = asdict(w._read_inputs())
        for key, value in expected.items():
            if isinstance(value, float):
                self.assertTrue(math.isclose(actual[key], value, rel_tol=1e-12, abs_tol=1e-12), key)
            else:
                self.assertEqual(actual[key], value, key)
        self.assertEqual(source, original, "Applying a candidate mutated the scan source")
        self.assertEqual(w._applied_scan_record["signature"], w._signature())

    def test_unconverged_or_failed_candidate_cannot_change_inputs(self):
        w = self.window
        for candidate in ({"side": "ox", "of_target": 70, "converged": False, "error": ""},
                          {"side": "fuel", "of_target": 0.2, "converged": True, "error": "bad point"}):
            with self.subTest(candidate=candidate):
                signature = w._signature()
                self.select_candidate(asdict(w._read_inputs()), candidate)
                with patch.object(QMessageBox, "warning") as warn:
                    w._on_of_sweep_apply()
                warn.assert_called_once()
                self.assertEqual(w._signature(), signature)
                self.assertIsNone(w._applied_scan_record)

    def test_invalid_grid_cannot_construct_or_start_a_worker(self):
        w = self.window
        for step in ("nan", "inf", "1e-999", "0.01"):
            with self.subTest(step=step):
                w.of_sweep_start.setText("60")
                w.of_sweep_end.setText("110")
                w.of_sweep_step.setText(step)
                with patch("workbench.legacy.OFSweepWorker") as worker_class:
                    with patch.object(w.thread_pool, "start") as start:
                        with patch.object(QMessageBox, "warning") as warn:
                            w._on_run_of_sweep_clicked()
                self.assertFalse(w._busy)
                worker_class.assert_not_called()
                start.assert_not_called()
                warn.assert_called_once()

    def test_streamed_partial_rows_survive_cancellation_and_inputs_unlock(self):
        w = self.window
        w.of_sweep_start.setText("60")
        w.of_sweep_end.setText("62")
        w.of_sweep_step.setText("1")
        with patch("workbench.legacy.OFSweepWorker", side_effect=FakeSweepWorker):
            with patch.object(w.thread_pool, "start") as start:
                w._on_run_of_sweep_clicked()
        worker = w._active_worker
        start.assert_called_once_with(worker)
        self.assertTrue(w._busy)
        self.assertFalse(w.parameter_pane.isEnabled())
        self.assertFalse(w.btn_run_of_sweep.isEnabled())
        record = {"side": "ox", "of_target": 60, "converged": True, "error": "", "T_PB_K": 750}
        worker.signals.record_ready.emit(record)
        self.assertEqual(w.of_sweep_table.rowCount(), 1)
        self.assertEqual(w.last_of_sweep["records"], [record])
        self.assertFalse(w.last_of_sweep["complete"])
        self.assertTrue(w.cancel_button.isEnabled())
        w.cancel_task()
        self.assertTrue(worker.cancel_requested)
        self.assertFalse(w.cancel_button.isEnabled())
        worker.signals.cancelled.emit({"kind": "of_sweep", "side": "ox", "records": [record],
                                       "inputs": asdict(worker.inputs)})
        self.assertFalse(w._busy)
        self.assertTrue(w.parameter_pane.isEnabled())
        self.assertTrue(w.btn_run_of_sweep.isEnabled())
        self.assertEqual(w.of_sweep_table.rowCount(), 1)
        self.assertEqual(w.last_of_sweep["records"], [record])
        self.assertTrue(w.last_of_sweep["cancelled"])
        self.assertFalse(w.last_of_sweep["complete"])

    def test_genuine_fixed_mr_scan_candidate_replays_with_same_model(self):
        w = self.window
        inputs = EngineInputs()
        inputs.cooling_marching_enable = 0
        source = asdict(inputs)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            record = sweep_ox_preburner_mr(inputs, [60])[0]
            self.assertTrue(record["converged"], f"Selected regression point failed: {record}")
            self.select_candidate(source, record)
            w._on_of_sweep_apply()
            replay = FFSCCycleModel(w._read_inputs()).evaluate()
        result = verify_scan_replay(record, replay)
        self.assertTrue(result["consistent"], result["different_fields"])
        # The actual completion callback must also publish the replay check.
        w._submitted_signature = w._signature()
        w._on_solve_finished(replay)
        self.assertTrue(w._last_scan_replay_check["consistent"], w._last_scan_replay_check)
        # A valid checker must detect material changes, not always report success.
        wrong_record = dict(record, T_PB_K=record["T_PB_K"] + 10)
        self.assertFalse(verify_scan_replay(wrong_record, replay)["consistent"])
        # A later, unrelated solve must not export the old candidate's check.
        w._submitted_signature = ("another design point",)
        with patch("workbench.legacy.MainWindow._on_solve_finished"):
            w._on_solve_finished(replay)
        self.assertIsNone(w._last_scan_replay_check)


if __name__ == "__main__":
    unittest.main()
