"""Save guards and recovery of raw workbench edits, including invalid text."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
from pathlib import Path
import tempfile

from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

from case_recovery import RecoveryStore


LOGGER = logging.getLogger(__name__)
_THERMO_FIELDS = ("of_min", "of_max", "of_n", "eps", "pc", "T_fuel", "T_ox")


class CaseSafetyMixin:
    """Call initialization after the legacy widget tree has been built.

    UI prompts are explicit: construction never offers a recovered draft.
    Saving/loading a case should call ``_remember_saved_case``; accepted
    close should call ``_finish_case_close`` after its abandonment guard.
    """

    def _init_case_safety(self, recovery_directory=None):
        self._case_safety_ready = False
        self._case_recovery_store = None
        self._case_recovery_temporary = None
        self._case_recovery_forced_dirty = False
        self._case_recovery_timer = QTimer(self)
        self._case_recovery_timer.setSingleShot(True)
        self._case_recovery_timer.timeout.connect(self._flush_case_recovery)
        app = QApplication.instance()
        if recovery_directory is None and app is not None and app.platformName() == "offscreen":
            self._case_recovery_temporary = tempfile.TemporaryDirectory(prefix="lre-workbench-test-recovery-")
            recovery_directory = self._case_recovery_temporary.name
        try:
            self._case_recovery_store = RecoveryStore(recovery_directory)
        except Exception:
            LOGGER.exception("Draft recovery could not be initialized")
        self._case_baseline = self._case_edit_state()
        self._case_safety_ready = True

    def _case_raw_inputs(self):
        return {key: edit.currentData() if isinstance(edit, QComboBox) else edit.text()
                for key, edit in self.input_edits.items()}

    def _case_analysis_inputs(self):
        values = {"of_sweep": {}, "thermo": {}}
        side = getattr(self, "of_sweep_side", None)
        if isinstance(side, QComboBox):
            values["of_sweep"]["side"] = side.currentIndex()
        for name in ("start", "end", "step"):
            edit = getattr(self, f"of_sweep_{name}", None)
            if isinstance(edit, QLineEdit):
                values["of_sweep"][name] = edit.text()
        for name in _THERMO_FIELDS:
            edit = getattr(self, f"_thermo_{name}", None)
            if isinstance(edit, QLineEdit):
                values["thermo"][name] = edit.text()
        return values

    def _analysis_restore_operations(self, values):
        if not isinstance(values, dict):
            raise ValueError("分析设置必须是对象")
        sweep, thermo = values.get("of_sweep", {}), values.get("thermo", {})
        if not isinstance(sweep, dict) or not isinstance(thermo, dict):
            raise ValueError("扫描与热力学设置必须是对象")
        operations = []
        side = getattr(self, "of_sweep_side", None)
        if "side" in sweep and isinstance(side, QComboBox):
            index = sweep["side"]
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < side.count():
                raise ValueError("扫描对象选项不受支持")
            operations.append((side, index))
        for group, prefix, names in ((sweep, "of_sweep_", ("start", "end", "step")),
                                      (thermo, "_thermo_", _THERMO_FIELDS)):
            for name in names:
                edit = getattr(self, prefix + name, None)
                if name in group and isinstance(edit, QLineEdit):
                    text = group[name]
                    if not isinstance(text, str):
                        raise ValueError(f"分析设置 {name} 必须保留为文本")
                    operations.append((edit, text))
        return operations

    def _restore_case_analysis(self, values):
        # Validate all settings before changing any widget. Blocking the side
        # selector prevents its legacy handler from replacing the saved range.
        operations = self._analysis_restore_operations(values)
        blockers = [QSignalBlocker(widget) for widget, _ in operations]
        try:
            for widget, value in operations:
                if isinstance(widget, QComboBox):
                    widget.setCurrentIndex(value)
                else:
                    widget.setText(value)
        finally:
            for blocker in blockers:
                blocker.unblock()
        refresh = getattr(self, "_update_scan_preview", None)
        if callable(refresh):
            refresh()

    def _case_edit_state(self):
        return {"raw_inputs": self._case_raw_inputs(),
                "extra_inputs": deepcopy(getattr(self, "_extra_inputs", {})),
                "analysis_inputs": self._case_analysis_inputs()}

    def _has_unsaved_changes(self):
        if not getattr(self, "_case_safety_ready", False):
            signature = getattr(self, "_signature", None)
            return bool(callable(signature) and signature() != getattr(self, "_saved_signature", None))
        return self._case_recovery_forced_dirty or self._case_edit_state() != self._case_baseline

    def _remember_saved_case(self):
        if not getattr(self, "_case_safety_ready", False):
            return
        self._case_baseline = self._case_edit_state()
        self._case_recovery_forced_dirty = False
        signature = getattr(self, "_signature", None)
        if callable(signature):
            self._saved_signature = signature()
        self._case_recovery_timer.stop()
        if self._case_recovery_store is not None:
            try:
                self._case_recovery_store.clear()
            except Exception:
                LOGGER.exception("Saved case, but could not clear its recovery draft")

    def _case_recovery_snapshot(self):
        snapshot = self._case_edit_state()
        path = getattr(self, "_case_path", None)
        snapshot["saved_path"] = str(path) if path is not None else None
        snapshot["saved_inputs"] = deepcopy(self._case_baseline)
        return snapshot

    def _schedule_case_recovery(self, *_):
        if getattr(self, "_case_safety_ready", False) and not getattr(self, "_loading_case", False):
            self._case_recovery_timer.start(750)

    def _flush_case_recovery(self):
        if not getattr(self, "_case_safety_ready", False) or self._case_recovery_store is None:
            return False
        self._case_recovery_timer.stop()
        try:
            if self._has_unsaved_changes():
                self._case_recovery_store.save(self._case_recovery_snapshot())
            else:
                self._case_recovery_store.clear()
            return True
        except Exception:
            LOGGER.exception("Could not update recovery draft")
            status_bar = getattr(self, "statusBar", None)
            if callable(status_bar):
                status_bar().showMessage("草稿备份写入失败，请及时保存方案。", 8000)
            return False

    def _ask_case_abandon(self, reason):
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Warning)
        prompt.setWindowTitle("未保存的修改")
        prompt.setText(f"当前方案有未保存的修改。{reason}前是否保存？")
        prompt.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        prompt.button(QMessageBox.Save).setText("保存")
        prompt.button(QMessageBox.Discard).setText("放弃修改")
        prompt.button(QMessageBox.Cancel).setText("取消")
        prompt.setDefaultButton(QMessageBox.Cancel)
        prompt.setEscapeButton(QMessageBox.Cancel)
        return prompt.exec()

    def _can_abandon_case(self, reason):
        if not getattr(self, "_case_safety_ready", False) or getattr(self, "_loading_case", False):
            return True
        if not self._has_unsaved_changes():
            return True
        # Preserve pending edits even while the user considers the prompt.
        self._flush_case_recovery()
        choice = self._ask_case_abandon(reason)
        if choice == QMessageBox.Save:
            return bool(self.save_case())
        return choice == QMessageBox.Discard

    def _ask_case_recovery(self, draft):
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle("恢复未保存的方案")
        stamp = datetime.fromisoformat(draft.updated_at).astimezone().strftime("%Y-%m-%d %H:%M")
        saved_path = draft.snapshot.get("saved_path")
        name = Path(saved_path).name if isinstance(saved_path, str) and saved_path else "未命名方案"
        prompt.setText(f"发现上次退出前未保存的草稿：{name}\n最后备份时间：{stamp}\n是否恢复这些输入和分析设置？")
        restore = prompt.addButton("恢复草稿", QMessageBox.AcceptRole)
        discard = prompt.addButton("丢弃草稿", QMessageBox.DestructiveRole)
        later = prompt.addButton("稍后处理", QMessageBox.RejectRole)
        prompt.setDefaultButton(restore)
        prompt.setEscapeButton(later)
        prompt.exec()
        if prompt.clickedButton() == restore:
            return "restore"
        if prompt.clickedButton() == discard:
            return "discard"
        return "later"

    def _restore_case_snapshot(self, snapshot):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("raw_inputs"), dict):
            raise ValueError("草稿中没有有效的原始输入")
        raw = snapshot["raw_inputs"]
        extra, analysis = snapshot.get("extra_inputs", {}), snapshot.get("analysis_inputs", {})
        baseline = snapshot.get("saved_inputs")
        if not isinstance(extra, dict) or not isinstance(baseline, dict):
            raise ValueError("草稿缺少附加输入或保存基准")
        for key in ("raw_inputs", "extra_inputs", "analysis_inputs"):
            if not isinstance(baseline.get(key), dict):
                raise ValueError("草稿保存基准不完整")
        saved_path = snapshot.get("saved_path")
        if saved_path is not None and not isinstance(saved_path, str):
            raise ValueError("草稿方案路径不正确")
        operations = []
        for key, widget in self.input_edits.items():
            if key not in raw:
                continue
            if isinstance(widget, QComboBox):
                index = widget.findData(raw[key])
                if index < 0:
                    raise ValueError(f"草稿选项 {key} 不受支持")
                operations.append((widget, index))
            else:
                if not isinstance(raw[key], str):
                    raise ValueError(f"草稿输入 {key} 没有保留原始文本")
                operations.append((widget, raw[key]))
        operations += self._analysis_restore_operations(analysis)
        blockers = [QSignalBlocker(widget) for widget, _ in operations]
        old_loading = getattr(self, "_loading_case", False)
        old_suppression = getattr(self, "_suppress_preset_prompt", False)
        self._loading_case = True
        self._suppress_preset_prompt = True
        try:
            for widget, value in operations:
                if isinstance(widget, QComboBox):
                    widget.setCurrentIndex(value)
                else:
                    widget.setText(value)
            self._extra_inputs = deepcopy(extra)
            self._case_path = Path(saved_path) if saved_path else None
            self._case_baseline = deepcopy(baseline)
            self._case_recovery_forced_dirty = True
            visible = tuple(sorted(baseline["raw_inputs"].items()))
            self._saved_signature = visible + tuple(sorted(baseline["extra_inputs"].items()))
            self._result_signature = None
            for name in ("_on_cycle_type_changed", "_refresh_group_selector", "_refresh_preset_combo",
                         "_sync_of_sweep_availability", "_update_scan_preview"):
                refresh = getattr(self, name, None)
                if callable(refresh):
                    refresh()
        finally:
            for blocker in blockers:
                blocker.unblock()
            self._loading_case = old_loading
            self._suppress_preset_prompt = old_suppression
        update = getattr(self, "_update_case_state", None)
        if callable(update):
            update()

    def offer_case_recovery(self):
        """Offer the newest abandoned draft only when explicitly requested."""
        if not getattr(self, "_case_safety_ready", False) or self._case_recovery_store is None:
            return False
        try:
            drafts = self._case_recovery_store.discover()
            if not drafts:
                return False
            draft = drafts[0]
            choice = self._ask_case_recovery(draft)
            if choice == "discard":
                return self._case_recovery_store.discard(draft)
            if choice != "restore" or not self._can_abandon_case("恢复草稿"):
                return False
            snapshot = self._case_recovery_store.read(draft)
            if snapshot is None:
                return False
            self._restore_case_snapshot(snapshot)
            if not self._flush_case_recovery():
                QMessageBox.warning(self, "草稿备份失败", "输入已恢复，但新备份写入失败。原草稿已保留，请及时保存方案。")
                return False
            self._case_recovery_store.discard(draft)
            return True
        except Exception as error:
            LOGGER.exception("Could not restore recovery draft")
            QMessageBox.warning(self, "草稿恢复失败", f"{error}\n原草稿已保留。")
            return False

    def _finish_case_close(self):
        if not getattr(self, "_case_safety_ready", False):
            return
        self._case_recovery_timer.stop()
        store = self._case_recovery_store
        if store is not None:
            try:
                store.clear()
            except Exception:
                LOGGER.exception("Could not clear draft during accepted close")
            finally:
                store.close()
        self._case_safety_ready = False
        temporary = self._case_recovery_temporary
        if temporary is not None:
            temporary.cleanup()
