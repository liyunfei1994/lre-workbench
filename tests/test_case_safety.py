"""Exercise draft restoration and save decisions without the solver UI."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox, QWidget
from case_safety import CaseSafetyMixin


class SmallCaseWindow(CaseSafetyMixin, QWidget):
    def __init__(self, directory=None):
        super().__init__()
        cycle = QComboBox(self)
        cycle.addItem("FFSC", "ffsc")
        cycle.addItem("GG", "gg")
        self.input_edits = {"cycle_type": cycle, "p_c": QLineEdit("30", self)}
        self._extra_inputs = {"hidden_pressure": 41.0}
        self._loading_case = False
        self._suppress_preset_prompt = False
        self._case_path = None
        self._result_signature = None
        self.of_sweep_side = QComboBox(self)
        self.of_sweep_side.addItems(["氧", "燃料"])
        for name, text in (("start", "60"), ("end", "110"), ("step", "5")):
            setattr(self, f"of_sweep_{name}", QLineEdit(text, self))
        for name in ("of_min", "of_max", "of_n", "eps", "pc", "T_fuel", "T_ox"):
            setattr(self, f"_thermo_{name}", QLineEdit("3", self))
        self.save_succeeds = True
        self.save_calls = 0
        self._init_case_safety(directory)
        for edit in self.input_edits.values():
            signal = edit.currentIndexChanged if isinstance(edit, QComboBox) else edit.textChanged
            signal.connect(self._schedule_case_recovery)
        for name in ("start", "end", "step"):
            getattr(self, f"of_sweep_{name}").textChanged.connect(self._schedule_case_recovery)

    def _signature(self):
        return tuple(sorted(self._case_raw_inputs().items())) + tuple(sorted(self._extra_inputs.items()))

    def save_case(self):
        self.save_calls += 1
        if self.save_succeeds:
            self._remember_saved_case()
        return self.save_succeeds


class CaseSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def window(self, directory=None):
        window = SmallCaseWindow(directory or self.directory)
        def cleanup():
            window._finish_case_close()
            window.close()
        self.addCleanup(cleanup)
        return window

    def test_save_cancel_and_failed_save_keep_edits_and_draft(self):
        window = self.window()
        window.input_edits["p_c"].setText("-")
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Cancel):
            self.assertFalse(window._can_abandon_case("关闭软件"))
        self.assertEqual(window._case_recovery_store.read()["raw_inputs"]["p_c"], "-")
        window.save_succeeds = False
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Save):
            self.assertFalse(window._can_abandon_case("打开方案"))
        self.assertEqual(window.save_calls, 1)
        self.assertTrue(window._has_unsaved_changes())
        self.assertTrue(window._case_recovery_store.path.exists())
        window.save_succeeds = True
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Save):
            self.assertTrue(window._can_abandon_case("关闭软件"))
        self.assertFalse(window._has_unsaved_changes())
        self.assertFalse(window._case_recovery_store.path.exists())

    def test_abandon_prompt_has_localized_buttons_and_escape_keeps_work(self):
        window = self.window()
        window.input_edits["p_c"].setText("32")
        observed = []
        def cancel_dialog():
            prompt = QApplication.activeModalWidget()
            observed.append([prompt.button(button).text() for button in
                             (QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel)])
            prompt.done(QMessageBox.Cancel)
        QTimer.singleShot(0, cancel_dialog)
        self.assertFalse(window._can_abandon_case("关闭软件"))
        self.assertEqual(observed, [["保存", "放弃修改", "取消"]])

    def test_restore_preserves_invalid_raw_text_baseline_and_analysis(self):
        crashed = self.window()
        crashed._case_path = self.directory / "原方案.json"
        baseline = crashed._case_edit_state()
        crashed.input_edits["p_c"].setText("1e")
        crashed.input_edits["cycle_type"].setCurrentIndex(1)
        crashed.of_sweep_start.setText("nan")
        crashed.of_sweep_side.setCurrentIndex(1)
        crashed._thermo_eps.setText("40,broken,80")
        crashed._extra_inputs["hidden_pressure"] = 42.0
        self.assertTrue(crashed._flush_case_recovery())
        old_store = crashed._case_recovery_store
        old_store.close()
        crashed._case_safety_ready = False
        new = self.window()
        with patch.object(new, "_ask_case_recovery", return_value="restore"):
            self.assertTrue(new.offer_case_recovery())
        self.assertEqual(new.input_edits["p_c"].text(), "1e")
        self.assertEqual(new.input_edits["cycle_type"].currentData(), "gg")
        self.assertEqual(new.of_sweep_start.text(), "nan")
        self.assertEqual(new.of_sweep_side.currentIndex(), 1)
        self.assertEqual(new._thermo_eps.text(), "40,broken,80")
        self.assertEqual(new._extra_inputs["hidden_pressure"], 42.0)
        self.assertEqual(new._case_path, self.directory / "原方案.json")
        self.assertEqual(new._case_baseline, baseline)
        self.assertTrue(new._has_unsaved_changes())
        self.assertFalse(old_store.path.exists())
        self.assertTrue(new._case_recovery_store.path.exists())

    def test_later_preserves_draft_and_current_save_failure_preserves_original(self):
        crashed = self.window()
        crashed.input_edits["p_c"].setText("35")
        crashed._flush_case_recovery()
        old_store = crashed._case_recovery_store
        old_store.close()
        crashed._case_safety_ready = False
        new = self.window()
        with patch.object(new, "_ask_case_recovery", return_value="later"):
            self.assertFalse(new.offer_case_recovery())
        self.assertTrue(old_store.path.exists())
        with patch.object(new, "_ask_case_recovery", return_value="restore"), \
                patch.object(new._case_recovery_store, "save", side_effect=OSError("disk full")), \
                patch("case_safety.QMessageBox.warning"), \
                self.assertLogs("case_safety", level="ERROR"):
            self.assertFalse(new.offer_case_recovery())
        self.assertTrue(old_store.path.exists())
        self.assertEqual(new.input_edits["p_c"].text(), "35")
        self.assertTrue(new._has_unsaved_changes())

    def test_dirty_analysis_is_autosaved_and_accepted_discard_clears_session(self):
        window = self.window()
        self.assertFalse(window._has_unsaved_changes())
        window.of_sweep_start.setText("65.2")
        self.assertTrue(window._has_unsaved_changes())
        QTest.qWait(830)
        self.assertEqual(window._case_recovery_store.read()["analysis_inputs"]["of_sweep"]["start"], "65.2")
        with patch.object(window, "_ask_case_abandon", return_value=QMessageBox.Discard):
            self.assertTrue(window._can_abandon_case("关闭软件"))
        store = window._case_recovery_store
        window._finish_case_close()
        self.assertFalse(store.path.exists())
        self.assertFalse(window._case_safety_ready)

    def test_analysis_restore_validates_before_writing_and_keeps_invalid_text(self):
        window = self.window()
        with self.assertRaises(ValueError):
            window._restore_case_analysis({"of_sweep": {"start": "90", "side": 99}})
        self.assertEqual(window.of_sweep_start.text(), "60")
        window._restore_case_analysis({"of_sweep": {"start": "-", "side": 1}, "thermo": {"of_n": ""}})
        self.assertEqual(window.of_sweep_start.text(), "-")
        self.assertEqual(window._thermo_of_n.text(), "")
        self.assertTrue(window._has_unsaved_changes())

    def test_offscreen_default_uses_ephemeral_directory_without_recovery_prompt(self):
        with patch("case_safety.QMessageBox.exec", side_effect=AssertionError("constructor must not prompt")):
            window = SmallCaseWindow()
        self.addCleanup(window.close)
        directory = window._case_recovery_store.directory
        self.assertIn("lre-workbench-test-recovery-", directory.name)
        window._finish_case_close()
        self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
