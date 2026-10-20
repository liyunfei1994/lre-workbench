"""Exercise actual window controls and queued worker signals."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from workbench import WorkbenchWindow
from ui_chamber_design import _PreviewWorker
from scan_parameters import validate_thermo_scan


class JobControlTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        self.window = WorkbenchWindow(recovery_directory=self.directory.name)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        if self.window._busy:
            self.window.cancel_task()
            deadline = time.monotonic() + 10
            while self.window._busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.002)
        self.window._busy = False  # Avoid a modal cleanup if the assertion failed.
        self.window._remember_saved_case()
        self.window.close()
        self.directory.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 15
        while self.window._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertFalse(self.window._busy)

    def test_thermo_bad_parameters_cannot_start_worker(self):
        for count, of_min, eps in (("nan", "3", "40"), ("2", "inf", "40"),
                                   ("100", "3", "40,80,120"), ("2", "3", "0.5")):
            w = self.window
            w._thermo_of_n.setText(count)
            w._thermo_of_min.setText(of_min)
            w._thermo_eps.setText(eps)
            with patch("gui_pyside6.ThermoWorker") as worker, patch.object(QMessageBox, "warning") as warning:
                w.run_thermo_compare()
            self.assertFalse(w._busy)
            worker.assert_not_called()
            warning.assert_called_once()
        self.assertEqual(validate_thermo_scan("3", "4", "2", "40,80", "30", "110", "90"), 4)

    def test_actual_thermo_cancel_preserves_rows_and_reenables_run(self):
        w = self.window
        w._thermo_of_n.setText("30")
        w._thermo_of_min.setText("2.5")
        w._thermo_of_max.setText("2.6")
        w._thermo_eps.setText("40")
        w.run_thermo_compare()
        worker = w._active_worker
        # Request cancellation at a deterministic completed native point. This
        # callback only sets a threading.Event; it never accesses Qt widgets.
        worker.signals.record_ready.connect(lambda _: worker.request_cancel(), Qt.DirectConnection)
        self.wait_idle()
        self.assertIn("已取消", w._thermo_status.text())
        self.assertGreater(w.thermo_table.rowCount(), 0)
        self.assertLess(w.thermo_table.rowCount(), 30)
        self.assertTrue(w._thermo_run_btn.isEnabled())
        self.assertTrue(w.header_run.isEnabled())

    def test_design_cancel_through_actual_button_does_not_publish_partial_solution(self):
        w = self.window
        w.run_cycle()
        self.assertTrue(w._busy)
        w.cancel_button.click()
        self.wait_idle()
        self.assertIsNone(w.last_results)
        self.assertIn("已取消", w.progress_label.text())
        self.assertTrue(w.input_edits["thrust"].isEnabled())
        self.assertFalse(w.cancel_button.isVisible())

    def test_preview_cancel_restores_both_preview_buttons(self):
        w = self.window
        w.chamber_panel._set_preview_busy(True)
        worker = _PreviewWorker(None, None)
        w._active_worker = worker
        w._task_kind = "preview"
        w._connect_active_worker()
        worker.request_cancel()
        worker.run()
        self.app.processEvents()
        self.assertFalse(w._busy)
        self.assertFalse(w.chamber_panel._preview_running)
        self.assertTrue(w.chamber_panel.btn_preview.isEnabled())
        self.assertTrue(w.chamber_panel.btn_preview_m.isEnabled())


if __name__ == "__main__":
    unittest.main()
