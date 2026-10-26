"""Windows desktop integration checks."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox
from workbench_fonts import configure_fonts
from workbench import PAGES, WorkbenchWindow
from windows_identity import configure_process_identity, current_process_identity, APP_USER_MODEL_ID
from qt_chart_canvas import SafeFigureCanvasQTAgg
from engine_presets import preset_input_values
from core import ComponentResult, CycleResults, EngineInputs


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        configure_fonts(cls.app)
        cls.window = WorkbenchWindow()
        # Keep these real workers isolated from other desktop test classes.
        cls.window.thread_pool = QThreadPool(cls.window)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._remember_saved_case()
        cls.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def setUp(self):
        self.window._result_signature = None
        self.window.parameter_search.clear()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with patch.object(self.window, "_ask_case_abandon", return_value=QMessageBox.Discard):
                self.window.reset_defaults()
        self.window._saved_signature = self.window._signature()
        self.window._remember_saved_case()

    def tearDown(self):
        w = self.window
        if w._busy or w.thread_pool.activeThreadCount():
            # A timeout must still fail its test, but must not leave a live
            # worker or a locked parameter pane behind for the next test.
            w._connect_active_worker()
            worker = getattr(w, "_active_worker", None)
            if worker is not None and hasattr(worker, "request_cancel"):
                worker.request_cancel()
            deadline = time.monotonic() + 10
            while (w._busy or w.thread_pool.activeThreadCount()) and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.app.processEvents()
            self.assertEqual(w.thread_pool.activeThreadCount(), 0, "worker did not cancel during cleanup")
            self.assertFalse(w._busy, "worker completion did not restore the UI")

    def wait_for_worker(self, timeout):
        deadline = time.monotonic() + timeout
        while (self.window._busy or self.window.thread_pool.activeThreadCount()) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window._busy, "worker timeout")
        self.assertEqual(self.window.thread_pool.activeThreadCount(), 0, "worker timeout")

    def test_all_analysis_pages_and_small_window(self):
        w = self.window
        for key, title, _, _ in PAGES:
            w._nav_buttons[key].click()
            self.app.processEvents()
            self.assertEqual(w.page_title.text(), title)
            self.assertTrue(w._nav_buttons[key].isChecked())
        w.resize(1100, 700)
        self.app.processEvents()
        self.assertLessEqual(w.workspace_splitter.width() + 176, w.width())
        w.navigate("overview")
        w.resize(1480, 930)

    def test_input_group_search_and_cycle_filter(self):
        w = self.window
        w.parameter_search.setText("温度")
        self.app.processEvents()
        self.assertTrue(any(box.isVisible() for box in w._param_group_boxes.values()))
        w.parameter_search.clear()
        w._suppress_preset_prompt = True
        w.input_edits["cycle_type"].setCurrentIndex(1)
        w._suppress_preset_prompt = False
        titles = [w.group_selector.itemData(i) for i in range(w.group_selector.count())]
        self.assertIn("燃气发生器(GG) / Gas generator", titles)
        self.assertNotIn("预燃室(FFSC) / Preburners", titles)
        self.assertTrue(w.input_edits["gg_layout"].isEnabled())

    def test_case_roundtrip_preserves_units_and_hidden_fields(self):
        w = self.window
        w.input_edits["thrust"].setText("2450.12345")
        w.input_edits["pc"].setText("32.87654321")
        document = w.case_document()
        # A non-visible model parameter must not be lost on import/export.
        hidden = next(key for key, value in document["inputs"].items()
                      if key not in w.input_edits and isinstance(value, float) and value > 0)
        document["inputs"][hidden] *= 1.000001
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            w.load_case_path(path)
            restored = w.case_document()["inputs"]
        for key, value in document["inputs"].items():
            if isinstance(value, (int, float)):
                self.assertTrue(math.isclose(value, restored[key], rel_tol=1e-12, abs_tol=1e-12), key)
            else:
                self.assertEqual(value, restored[key], key)

    def test_invalid_input_is_rejected_without_truncation(self):
        w = self.window
        w.input_edits["throttle_steps"].setText("1.9")
        with self.assertRaisesRegex(ValueError, "整数"):
            w._read_inputs()
        w.input_edits["throttle_steps"].setText("8")
        w.input_edits["pc"].setText("nan")
        with self.assertRaisesRegex(ValueError, "有限"):
            w._read_inputs()

    def test_bad_case_does_not_partially_replace_inputs(self):
        w = self.window
        before = w._signature()
        document = w.case_document()
        document["inputs"]["thrust"] = -1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(Exception):
                w.load_case_path(path)
        self.assertEqual(w._signature(), before)

    def test_actual_gg_worker_and_result_staleness(self):
        w = self.window
        w._apply_engine_preset("tq12a_gg")
        # This integration test covers real GG solving, UI unlock and result
        # identity/staleness. Use an explicit system-budget case, also used by
        # the real-library GG feed-state tests, rather than making worker/UI
        # correctness depend on nested quasi-1D cooling convergence time.
        values = preset_input_values("tq12a_gg")
        values.update(cooling_bartz_mode=0., cooling_heat_load=10e6,
                      cooling_dp_pa=2e6)
        w._write_input_values(values)
        w.last_results = None
        error_messages = []
        modal_guard = patch.object(QMessageBox, "critical", side_effect=lambda parent, title, message: error_messages.append(message))
        modal_guard.start()
        self.addCleanup(modal_guard.stop)
        w.run_cycle()
        self.assertTrue(w._busy)
        self.assertFalse(w.header_run.isEnabled())
        self.wait_for_worker(75)
        self.assertIsNotNone(w.last_results, " | ".join(error_messages))
        self.assertEqual(w.last_results.inputs.cycle_type, "gg")
        self.assertEqual(w.last_results.inputs.cooling_bartz_mode, 0.)
        self.assertEqual(w.last_results.summary["numerical_converged"], 1.)
        self.assertFalse(error_messages)
        self.assertTrue(w.header_run.isEnabled())
        self.assertEqual(w._result_signature, w._signature())
        self.assertGreater(w.last_results.summary["thrust_N"], 0)
        self.assertIsNotNone(w.last_geometry)
        self.assertGreater(w.geometry_table.rowCount(), 0)
        w.input_edits["pc"].setText("10.2")
        w._update_case_state()
        self.assertIn("上一计算工况", w.result_banner_label.text())
        w.chamber_panel._set_preview_busy(True)
        self.assertTrue(w._busy)
        self.assertFalse(w.header_run.isEnabled())
        w.chamber_panel._set_preview_busy(False)
        self.assertFalse(w._busy)

    def test_scan_button_restores_after_real_thermo_comparison(self):
        w = self.window
        self.assertTrue(w.btn_run_of_sweep.isEnabled())
        w._thermo_of_min.setText("3.4")
        w._thermo_of_max.setText("3.6")
        w._thermo_of_n.setText("2")
        w._thermo_eps.setText("40")
        w.run_thermo_compare()
        self.assertTrue(w._busy)
        self.assertFalse(w.btn_run_of_sweep.isEnabled())
        self.wait_for_worker(40)
        self.assertEqual(w.thermo_table.rowCount(), 2)
        self.assertTrue(w.btn_run_of_sweep.isEnabled())
        # Repeated busy notifications must not replace the idle-state snapshot.
        w._set_busy(True, "第一次进入忙态")
        w._set_busy(True, "更新进度")
        w._set_busy(False, "完成")
        self.assertTrue(w.btn_run_of_sweep.isEnabled())

    def test_actual_single_point_preburner_scan(self):
        w = self.window
        # Exercise the real worker, streamed row and UI unlock using the known
        # branch also used by test_scan_improvements. This is a system-budget
        # case; physical coolant/hot-gas closure has independent regression tests.
        values = preset_input_values("raptor3_ffsc")
        values.update(cooling_bartz_mode=0.0, cooling_heat_load=90e6,
                      cooling_dp_pa=5e6, use_target_preburner_temp=0.0,
                      use_target_preburner_mr=1.0,
                      ox_preburner_mr_target=59.81689453125,
                      fuel_preburner_mr_target=0.1599658203125)
        w._write_input_values(values)
        target = values["ox_preburner_mr_target"]
        w.last_of_sweep = None
        w.of_sweep_start.setText(str(target))
        w.of_sweep_end.setText(str(target))
        w.of_sweep_step.setText("1")
        w.btn_run_of_sweep.click()
        self.assertTrue(w._busy)
        self.wait_for_worker(60)
        self.assertIsNotNone(w.last_of_sweep)
        self.assertEqual(w.of_sweep_table.rowCount(), 1)
        self.assertEqual(w.last_of_sweep["records"][0]["of_target"], target)
        self.assertTrue(w.last_of_sweep["records"][0]["converged"])
        self.assertTrue(w.btn_run_of_sweep.isEnabled())
        self.assertTrue(w.parameter_pane.isEnabled())

    def test_gg_preburner_scan_explains_limitation_without_wrong_model(self):
        from sweep import sweep_ox_preburner_mr, sweep_fuel_preburner_mr
        w = self.window
        w._apply_engine_preset("tq12a_gg")
        self.assertFalse(w.btn_run_of_sweep.isEnabled())
        self.assertIn("GG", w._of_sweep_intro.text())
        self.assertIn("FFSC", w._of_sweep_intro.text())
        w.run_of_sweep_ox()
        self.assertFalse(w._busy)
        for sweep in (sweep_ox_preburner_mr, sweep_fuel_preburner_mr):
            with self.assertRaisesRegex(ValueError, "FFSC"):
                sweep(w._read_inputs(), [80])
        with patch.object(w, "_ask_case_abandon", return_value=QMessageBox.Discard):
            w.reset_defaults()
        self.assertTrue(w.btn_run_of_sweep.isEnabled())
        self.assertNotIn("GG 循环", w.of_sweep_hint.text())

    def test_deleted_chart_cancels_queued_redraw(self):
        from matplotlib.figure import Figure
        from shiboken6 import isValid
        errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: errors.append(value)
        try:
            for _ in range(40):
                canvas = SafeFigureCanvasQTAgg(Figure())
                canvas.draw_idle()
                self.assertIs(canvas._redraw_timer.parent(), canvas)
                canvas.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertFalse(isValid(canvas))
                self.app.processEvents()
                # Also simulate an already posted callback after native deletion.
                canvas._draw_idle()
            self.assertEqual(errors, [])
        finally:
            sys.excepthook = old_hook

    def test_material_rapid_selection_and_wheel_redraw(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        w = self.window
        # Rendering/selection fixture, not a claimed engine solution. Do not
        # depend on another test having completed an actual design calculation.
        material_result = CycleResults(
            EngineInputs(**preset_input_values("raptor3_ffsc")), {}, {},
            {
                "ox_preburner_turbine": ComponentResult("ox_preburner_turbine", {"T_in_K": 750.0, "U_mean_actual_ms": 40.0}),
                "fuel_preburner_turbine": ComponentResult("fuel_preburner_turbine", {"T_in_K": 850.0, "U_mean_actual_ms": 40.0}),
                "ox_pump": ComponentResult("ox_pump", {"T_out_K": 85.0, "u_tip_est_ms": 250.0}),
                "fuel_pump": ComponentResult("fuel_pump", {"T_out_K": 120.0, "u_tip_est_ms": 300.0}),
            }, [],
        )
        w._latest_results = material_result
        w._populate_materials_tab(material_result)
        leaves = [w.materials_tree.topLevelItem(i).child(j)
                  for i in range(w.materials_tree.topLevelItemCount())
                  for j in range(w.materials_tree.topLevelItem(i).childCount())]
        self.assertGreater(len(leaves), 5)
        errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: errors.append(value)
        try:
            w.navigate("materials")
            for round_number in range(4):
                for item in leaves:
                    w.materials_tree.setCurrentItem(item)
                    combo = w.materials_tree.itemWidget(item, 1)
                    if combo and combo.count() > 1:
                        wheel = QWheelEvent(QPointF(8, 8), QPointF(8, 8), QPoint(), QPoint(0, -120),
                                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
                        QApplication.sendEvent(combo, wheel)
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    self.app.processEvents()
                # Force successive material detail replacement before redraws run.
                for item in leaves[:4]:
                    w._render_material_detail(item.data(0, Qt.UserRole))
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    self.app.processEvents()
            w._material_detail_timer.stop()
            w._render_selected_material()
            self.app.processEvents()
            self.assertGreater(len(w.materials_detail_scroll.findChildren(SafeFigureCanvasQTAgg)), 0)
            self.assertEqual(errors, [])
        finally:
            sys.excepthook = old_hook

    @unittest.skipUnless(sys.platform == "win32", "Windows shell identity")
    def test_windows_app_identity_and_icon_resource(self):
        configure_process_identity()
        self.assertEqual(current_process_identity(), APP_USER_MODEL_ID)
        icon = self.window.windowIcon()
        self.assertFalse(icon.isNull())
        self.assertGreaterEqual(len(icon.availableSizes()), 4)

    def test_popup_remains_readable_with_dark_application_palette(self):
        from PySide6.QtGui import QPalette, QColor
        w = self.window
        original = self.app.palette()
        dark = QPalette(original)
        dark.setColor(QPalette.Base, QColor("#303030"))
        dark.setColor(QPalette.Text, QColor("#203747"))
        self.app.setPalette(dark)
        try:
            w.group_selector.showPopup()
            self.app.processEvents()
            view = w.group_selector.view()
            self.assertEqual(view.palette().color(QPalette.Base).name(), "#ffffff")
            self.assertEqual(view.palette().color(QPalette.Text).name(), "#203747")
            self.assertEqual(view.palette().color(QPalette.HighlightedText).name(), "#ffffff")
            self.assertEqual(view.model().rowCount(), w.group_selector.count())
            w.group_selector.hidePopup()
        finally:
            self.app.setPalette(original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
