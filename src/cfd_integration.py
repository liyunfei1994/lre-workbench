"""Connect external CFD work to case persistence and the initial cycle model."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json

from PySide6.QtWidgets import QMessageBox


class CfdIntegrationMixin:
    @staticmethod
    def _cfd_history_from_document(document):
        history = document.get("cfd_feedback_history", [])
        if not isinstance(history, list) or len(history) > 100 or any(not isinstance(item, dict) for item in history):
            raise ValueError("CFD 反馈记录必须为最多 100 项的对象列表。")
        json.dumps(history, allow_nan=False)
        return deepcopy(history)

    def _case_recovery_snapshot(self):
        snapshot = super()._case_recovery_snapshot()
        snapshot["cfd_feedback_history"] = deepcopy(getattr(self, "_cfd_feedback_history", []))
        return snapshot

    def _cfd_route(self):
        panel = getattr(self, "cfd_panel", None)
        return panel.snapshot_settings().get("route", "internal") if panel else "internal"

    def _case_analysis_inputs(self):
        values = super()._case_analysis_inputs()
        if hasattr(self, "cfd_panel"):
            values["cfd"] = self.cfd_panel.snapshot_settings()
            edit = self.input_edits.get("cooling_marching_enable")
            values["cfd"]["internal_marching_text"] = (
                getattr(self, "_cfd_marching_backup", "1") if self._cfd_route() == "external"
                else edit.text() if edit is not None else "1")
        return values

    def _analysis_restore_operations(self, values):
        if hasattr(self, "cfd_panel") and self.cfd_panel.has_running_task():
            raise ValueError("外部 CFD 任务进行中，请先停止任务再替换方案或恢复草稿。")
        operations = super()._analysis_restore_operations(values)
        if hasattr(self, "cfd_panel") and "cfd" in values:
            self.cfd_panel.validate_settings(values["cfd"])
            preference = values["cfd"].get("internal_marching_text")
            if preference is not None and not isinstance(preference, str):
                raise ValueError("内部沿程计算偏好必须保存为原始文本。")
        return operations

    def _restore_cfd_preferences(self, config):
        self.cfd_panel.restore_settings(config)
        self._cfd_marching_overridden = False
        self._apply_cfd_route()
        if self._cfd_route() == "external" and "internal_marching_text" in config:
            self._cfd_marching_backup = config["internal_marching_text"]
        elif self._cfd_route() == "internal" and "internal_marching_text" in config:
            # Preserve the saved editor spelling (1.0 versus 1), while an
            # analysis preference cannot replace a different typed input.
            edit = self.input_edits.get("cooling_marching_enable")
            try:
                if edit is not None and float(edit.text()) == float(config["internal_marching_text"]):
                    edit.setText(config["internal_marching_text"])
            except ValueError:
                pass  # Raw recovery already restored any unfinished value.

    def _restore_case_analysis(self, values):
        super()._restore_case_analysis(values)
        if hasattr(self, "cfd_panel"):
            self._restore_cfd_preferences(values.get("cfd", {}))

    def _restore_case_snapshot(self, snapshot):
        history = self._cfd_history_from_document(snapshot)
        super()._restore_case_snapshot(snapshot)
        self._cfd_feedback_history = history
        if hasattr(self, "cfd_panel"):
            self._restore_cfd_preferences(snapshot.get("analysis_inputs", {}).get("cfd", {}))
            self._update_case_state()

    def _on_cfd_settings_changed(self):
        self._apply_cfd_route()
        self._analysis_case_changed()

    def _on_cfd_process_state(self, *_):
        """Prevent concurrent native tasks without disabling CFD cancellation."""
        panel = getattr(self, "cfd_panel", None)
        can_run = not self._busy and not (panel and panel.has_running_task())
        for name in ("header_run", "btn_run", "btn_throttle", "open_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(can_run)
        for action in getattr(self, "_run_actions", []):
            action.setEnabled(can_run)
        for action in getattr(self, "_case_actions", []):
            if action.text() == "打开方案":
                action.setEnabled(can_run)
        self._sync_of_sweep_availability()

    def _apply_cfd_route(self):
        panel = getattr(self, "cfd_panel", None)
        if panel is None:
            return
        external = self._cfd_route() == "external"
        busy = getattr(self, "_busy", False)
        edit = self.input_edits.get("cooling_marching_enable")
        if edit is not None:
            if external and not getattr(self, "_cfd_marching_overridden", False):
                self._cfd_marching_backup = edit.text()
                self._cfd_marching_overridden = True
                edit.setText("0")
            elif external and edit.text() != "0":
                self._cfd_marching_backup = edit.text()
                edit.setText("0")
            elif not external and getattr(self, "_cfd_marching_overridden", False):
                self._cfd_marching_overridden = False
                edit.setText(getattr(self, "_cfd_marching_backup", "1"))
            edit.setEnabled(not busy and not external)
        buttons = [getattr(self, key, None) for key in ("fea_btn", "matcmp_btn")]
        chamber = getattr(self, "chamber_panel", None)
        if chamber is not None:
            buttons += [getattr(chamber, key, None) for key in ("btn_preview", "btn_preview_m", "btn_station_fea")]
            chamber.chan_table.setEnabled(not busy)
        for button in buttons:
            if button is None:
                continue
            if button.property("CfdOriginalTooltip") is None:
                button.setProperty("CfdOriginalTooltip", button.toolTip())
            if external:
                button.setEnabled(False)
                button.setToolTip("外部 CFD 协同模式：请在 CFD 协同页交接详细流场、换热与外部分析。")
            elif not busy:
                button.setToolTip(button.property("CfdOriginalTooltip"))
                # Preview may use a freshly created contour. Local FEM needs
                # the wall field produced by the current internal analysis.
                is_fem = button in (getattr(self, "fea_btn", None), getattr(self, "matcmp_btn", None),
                                    getattr(chamber, "btn_station_fea", None) if chamber else None)
                button.setEnabled(not is_fem or getattr(self, "_last_wallfield", None) is not None)
        panel.setEnabled(not busy)
        panel.refresh_source()
        self._on_cfd_process_state()

    def _apply_cfd_feedback(self, requested):
        """Use an explicit validated CFD cooling result, then recompute balance."""
        if self._busy or self.cfd_panel.has_running_task():
            return
        from cfd_exchange import load_cfd_job, load_imported_result
        try:
            directory = Path(self.cfd_panel.snapshot_settings()["job_path"])
            job = load_cfd_job(directory)
            result = load_imported_result(directory)
            if (not result or job["task"] != "cooling_flow" or result["source_kind"] != "cfd"
                    or not result["converged"] or not result.get("feedback_eligible", False)):
                raise ValueError("只有已确认设置、报告收敛的真实 CFD 冷却结果能回写。")
            settings = self.cfd_panel.snapshot_settings()
            if settings["route"] != "external" or not self.cfd_panel._job_settings_match():
                raise ValueError("当前计算路线或边界确认设置已改变，请重新创建对应的 CFD 任务。")
            values = asdict(self._read_inputs())
            if values != job["inputs"]:
                raise ValueError("当前输入与 CFD 任务的原工况不一致，请恢复原方案或创建新的 CFD 任务。")
            mapping = {"pressure_drop_Pa": "cooling_dp_pa", "heat_load_W": "cooling_heat_load"}
            updates = {target: result["metrics"][source]["value"]
                       for source, target in mapping.items() if source in result["metrics"]}
            if not updates:
                raise ValueError("结果缺少冷却压降或全发动机冷却热负荷，无法回写。")
            if "cooling_heat_load" in updates:
                updates["cooling_bartz_mode"] = 0.0
            if "cooling_dp_pa" in updates:
                updates["cooling_dp_auto"] = 0.0
            # The signal supplies a request, never authoritative values.
            if not isinstance(requested, dict):
                raise ValueError("CFD 回写请求不正确。")
            before = {key: values[key] for key in updates}
            values.update(updates)
            self._write_input_values(values)
            record = {"job_id": job["job_id"], "input_fingerprint": job["input_fingerprint"],
                      "solver": deepcopy(result["solver"]), "applied_at": datetime.now().astimezone().isoformat(),
                      "before": before, "updates": updates, "result_source_kind": "cfd"}
            history = getattr(self, "_cfd_feedback_history", [])
            self._cfd_feedback_history = (history + [record])[-100:]
            self._applied_scan_record = None
            self._last_scan_replay_check = None
            self._append_log(f"已采用 CFD 冷却反馈（任务 {job['job_id']}）；重算循环匹配。")
            self.run_cycle()
        except (OSError, ValueError, KeyError, TypeError) as error:
            QMessageBox.warning(self, "CFD 反馈未应用", str(error))
