"""Verify case safety in the actual workbench, not just in the helper mixin."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from workbench import WorkbenchWindow


class CaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.recovery = self.directory / "recovery"

    def window(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            window = WorkbenchWindow(recovery_directory=self.recovery)
            window.show()
            self.app.processEvents()
        def cleanup():
            window._remember_saved_case()
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(cleanup)
        return window

    def test_close_cancel_leaves_window_edits_and_draft_open(self):
        window = self.window()
        window.input_edits["pc"].setText("32.5")
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Cancel), \
                patch.object(window, "save_case", side_effect=AssertionError("Cancel must not save")):
            self.assertFalse(window.close())
        self.assertTrue(window.isVisible())
        self.assertTrue(window._case_safety_ready)
        self.assertEqual(window.input_edits["pc"].text(), "32.5")
        self.assertEqual(window._case_recovery_store.read()["raw_inputs"]["pc"], "32.5")

    def test_close_save_cancelled_filename_does_not_close_or_erase_draft(self):
        window = self.window()
        window.input_edits["pc"].setText("32.5")
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Save), \
                patch("workbench.QFileDialog.getSaveFileName", return_value=("", "")):
            self.assertFalse(window.close())
        self.assertTrue(window.isVisible())
        self.assertTrue(window._has_unsaved_changes())
        self.assertIsNone(window._case_path)
        self.assertTrue(window._case_recovery_store.path.exists())

    def test_close_save_invalid_numeric_input_does_not_close(self):
        window = self.window()
        window.input_edits["pc"].setText("-")
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Save), \
                patch("workbench.QMessageBox.warning") as warning, \
                patch("workbench.QFileDialog.getSaveFileName", side_effect=AssertionError("Invalid inputs cannot be saved")):
            self.assertFalse(window.close())
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "保存失败")
        self.assertTrue(window.isVisible())
        self.assertEqual(window.input_edits["pc"].text(), "-")
        self.assertTrue(window._case_recovery_store.path.exists())

    def test_close_save_writes_case_then_releases_recovery_session(self):
        window = self.window()
        window.input_edits["pc"].setText("32.5")
        path = self.directory / "已保存方案.json"
        recovery_path = window._case_recovery_store.path
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Save), \
                patch("workbench.QFileDialog.getSaveFileName", return_value=(str(path), "JSON (*.json)")):
            self.assertTrue(window.close())
        self.assertFalse(window.isVisible())
        self.assertFalse(window._case_safety_ready)
        self.assertFalse(recovery_path.exists())
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(document["inputs"]["pc"], 32.5e6)
        self.assertIn("environment", document)

    def test_close_discard_clears_draft_without_writing_case(self):
        window = self.window()
        window.input_edits["pc"].setText("32.5")
        window._flush_case_recovery()
        recovery_path = window._case_recovery_store.path
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Discard), \
                patch.object(window, "save_case", side_effect=AssertionError("Discard must not save")):
            self.assertTrue(window.close())
        self.assertFalse(window._case_safety_ready)
        self.assertFalse(recovery_path.exists())

    def test_dirty_open_reset_and_preset_cancel_keep_every_input(self):
        window = self.window()
        window.input_edits["pc"].setText("34.125")
        window.of_sweep_step.setText("1.2")
        before = window._case_edit_state()
        baseline = deepcopy(window._case_baseline)
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Cancel) as prompt, \
                patch("workbench.QFileDialog.getOpenFileName", side_effect=AssertionError("Cancelled open must not pick a file")):
            window.open_case()
            self.assertEqual(window._case_edit_state(), before)
            window.reset_defaults()
            self.assertEqual(window._case_edit_state(), before)
            window._apply_engine_preset("tq12a_gg")
            self.assertEqual(window._case_edit_state(), before)
        self.assertEqual(prompt.call_count, 3)
        self.assertEqual(window._case_baseline, baseline)
        self.assertTrue(window._has_unsaved_changes())

    def test_analysis_and_environment_saved_with_case_and_analysis_roundtrip(self):
        window = self.window()
        window.of_sweep_side.setCurrentIndex(1)
        window.of_sweep_start.setText("0.13")
        window.of_sweep_end.setText("0.27")
        window.of_sweep_step.setText("0.011")
        window._thermo_of_min.setText("3.125")
        window._thermo_of_max.setText("4.75")
        window._thermo_of_n.setText("11")
        window._thermo_eps.setText("40,80,140")
        window._thermo_pc.setText("31")
        window._thermo_T_fuel.setText("112")
        window._thermo_T_ox.setText("91")
        analysis = window._case_analysis_inputs()
        path = self.directory / "分析参数方案.json"
        with patch("workbench.QFileDialog.getSaveFileName", return_value=(str(path), "JSON (*.json)")):
            self.assertTrue(window.save_case())
        self.assertFalse(window._has_unsaved_changes())
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["analysis"], analysis)
        self.assertEqual(document["environment"], window._runtime_report)
        self.assertTrue(document["environment"]["fingerprint"])
        self.assertIn("cantera", document["environment"]["dependencies"])
        self.assertIn("CoolProp", document["environment"]["dependencies"])
        window.of_sweep_side.setCurrentIndex(0)
        window._thermo_eps.setText("30")
        window.input_edits["pc"].setText("12")
        window.load_case_path(path)
        self.assertEqual(window._case_analysis_inputs(), analysis)
        self.assertFalse(window._has_unsaved_changes())
        self.assertEqual(window._case_path, path)
        self.assertFalse(window._case_recovery_store.path.exists())

    def test_invalid_analysis_structure_rejects_case_before_any_input_changes(self):
        window = self.window()
        window.input_edits["pc"].setText("34")
        window._thermo_of_n.setText("12")
        before = window._case_edit_state()
        baseline = deepcopy(window._case_baseline)
        original_path = window._case_path
        document = window.case_document()
        document["inputs"]["pc"] = 17.5e6
        document["analysis"] = {"of_sweep": {"start": "10", "side": 9}}
        path = self.directory / "结构无效.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "扫描对象"):
            window.load_case_path(path)
        self.assertEqual(window._case_edit_state(), before)
        self.assertEqual(window._case_baseline, baseline)
        self.assertEqual(window._case_path, original_path)

    def test_integer_si_inputs_keep_display_units_on_import_and_snapshot(self):
        window = self.window()
        document = window.case_document()
        document["inputs"].update(pc=33000000, thrust=2450000)
        path = self.directory / "整数单位方案.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        for writer in (lambda: window.load_case_path(path),
                       lambda: window._write_input_values(document["inputs"])):
            with self.subTest(writer=writer):
                writer()
                self.assertEqual(window.input_edits["pc"].text(), "33")
                self.assertEqual(window.input_edits["thrust"].text(), "2450")
                inputs = window._read_inputs()
                self.assertEqual(inputs.pc, 33000000)
                self.assertEqual(inputs.thrust, 2450000)

    def test_actual_startup_recovery_preserves_invalid_text_and_remains_dirty(self):
        crashed = self.window()
        crashed.input_edits["pc"].setText("1e")
        crashed.of_sweep_start.setText("nan")
        crashed._thermo_eps.setText("40,broken,80")
        crashed._extra_inputs["fuel_margin"] = 1.04
        self.assertTrue(crashed._flush_case_recovery())
        previous = crashed._case_recovery_store
        previous.close()
        crashed._case_safety_ready = False
        recovered = self.window()
        self.assertNotEqual(recovered.input_edits["pc"].text(), "1e")
        with patch.object(recovered, "_ask_case_recovery", return_value="restore"), \
                patch.object(recovered, "_read_inputs", side_effect=AssertionError("Draft restoration must not parse unfinished inputs")):
            self.assertTrue(recovered.offer_case_recovery())
        self.assertEqual(recovered.input_edits["pc"].text(), "1e")
        self.assertEqual(recovered.of_sweep_start.text(), "nan")
        self.assertEqual(recovered._thermo_eps.text(), "40,broken,80")
        self.assertEqual(recovered._extra_inputs["fuel_margin"], 1.04)
        self.assertTrue(recovered._has_unsaved_changes())
        self.assertIn("已修改", recovered.document_label.text())
        self.assertFalse(previous.path.exists())
        self.assertTrue(recovered._case_recovery_store.path.exists())
        self.assertIn("有限", recovered.of_sweep_preview.text())


if __name__ == "__main__":
    unittest.main()
