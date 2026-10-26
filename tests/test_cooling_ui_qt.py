"""Qt mounting of saved cooling data must never launch another thermal solve."""
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).resolve().parent)]
QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None
if QT_AVAILABLE:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QComboBox, QListView, QMessageBox, QSpacerItem
    from shiboken6 import isValid
    from workbench import WorkbenchWindow
    from test_saved_cooling_views import result_fixture
    import chamber_design as cd


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is required for the desktop smoke test")
class SavedCoolingQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.directory = tempfile.TemporaryDirectory(prefix="lre-cooling-qt-")
        cls.window = WorkbenchWindow(recovery_directory=cls.directory.name)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._remember_saved_case()
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        cls.app.processEvents()
        cls.directory.cleanup()

    def setUp(self):
        w = self.window
        w._busy = False
        w.last_geometry = {"chamber": {"radius_m": .15, "wall_thickness_mm": 8},
                           "nozzle": {"throat_radius_m": .1}}
        self.result = result_fixture()
        self.result.states = {}
        self.result.warnings = []
        w.last_results = self.result

    def test_saved_profile_mounts_cooling_and_thermal_loads_without_solver(self):
        w = self.window
        before = deepcopy(self.result.components["cooling_channel"].profile)
        with patch("thermal_structural.compute_chamber_wall_field", side_effect=AssertionError("duplicate solve")) as duplicate:
            w._populate_cooling(self.result)
            w._populate_thermstr(self.result)
            self.app.processEvents()
        duplicate.assert_not_called()
        self.assertIsNotNone(w._last_wallfield_full, w.cooling_note.text())
        self.assertIsNotNone(w._last_wallfield, w.thermstr_note.text())
        self.assertEqual(w._last_wallfield_full.coolant_dp_Pa, 7e6)
        self.assertEqual(w._last_wallfield_full.coolant_out_K, 315.)
        self.assertEqual(w._last_wallfield_full.T_wg, before["T_wg_K"])
        self.assertEqual(self.result.components["cooling_channel"].profile, before)
        self.assertIn("未经结构验证", w.thermstr_note.text())
        self.assertGreaterEqual(w.cooling_canvas.minimumHeight(), 800)
        self.assertGreaterEqual(w.thermstr_canvas.minimumHeight(), 700)

    def test_missing_profile_clears_previous_plots_and_disables_fea(self):
        w = self.window
        w._populate_cooling(self.result)
        w._populate_thermstr(self.result)
        self.result.components["cooling_channel"].profile = None
        with patch("thermal_structural.compute_chamber_wall_field", side_effect=AssertionError("duplicate solve")) as duplicate:
            w._populate_cooling(self.result)
            w._populate_thermstr(self.result)
        duplicate.assert_not_called()
        self.assertIsNone(w._last_wallfield_full)
        self.assertIsNone(w._last_wallfield)
        self.assertEqual(w.cooling_fig.axes, [])
        self.assertEqual(w.thermstr_fig.axes, [])
        self.assertFalse(w.fea_btn.isEnabled())
        self.assertIn("完整循环", w.cooling_note.text())

    def test_recompute_button_never_writes_only_pressure_drop(self):
        w = self.window
        pressure = w.input_edits["cooling_dp_pa"].text()
        with patch.object(w, "run_cycle") as full_cycle:
            w.chamber_panel._writeback_and_rerun()
        full_cycle.assert_called_once_with()
        self.assertEqual(w.input_edits["cooling_dp_pa"].text(), pressure)

    def test_saved_profile_button_does_not_start_background_physics(self):
        w = self.window
        with patch.object(w.chamber_panel, "_on_preview_done") as mounted, \
             patch.object(w.thread_pool, "start") as start, \
             patch.object(w, "run_cycle") as solve:
            w.chamber_panel.run_marching_preview()
        mounted.assert_called_once()
        self.assertEqual(mounted.call_args.args[0]["source"], "saved_cycle_cooling_profile")
        start.assert_not_called()
        solve.assert_not_called()

    def test_popup_rebuilds_unexpected_view_wrapper_and_retains_real_views(self):
        w = self.window
        combo = w.findChildren(QComboBox)[0]
        with patch.object(combo, "view", return_value=QSpacerItem(1, 1)):
            w._polish_combo_popups()
        view = combo.view()
        self.assertIsInstance(view, QListView)
        self.assertTrue(isValid(view))
        self.assertEqual(view.objectName(), "ComboPopup")
        self.assertIs(w._combo_popup_views[combo], view)
        w._polish_combo_popups()
        self.assertIs(combo.view(), view)


if __name__ == "__main__":
    unittest.main()
