"""External CFD jobs beside the native cycle calculation, with explicit provenance."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import math
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


TASKS = {"chamber_flow": "推力室流动", "cooling_flow": "冷却通道流动", "turbine_flow": "涡轮流动"}
BASES = {"unresolved": "尚未确认", "static": "静态", "total": "总量"}
DEFAULT_SETTINGS = {
    "route": "internal", "task": "chamber_flow", "pressure_basis": "unresolved",
    "temperature_basis": "unresolved", "setup_reviewed": False, "executable": "",
    "arguments_text": "[]", "timeout_seconds_text": "3600", "job_path": "", "turbine_side": "ox",
}


def _plain_inputs(inputs):
    if is_dataclass(inputs):
        return asdict(inputs)
    if isinstance(inputs, dict):
        return dict(inputs)
    raise ValueError("没有可比较的初算输入")


def _same_fallback_inputs(solved, current):
    """Permit only display-roundtrip float noise when no submit snapshot exists."""
    if solved.keys() != current.keys():
        return False
    for key, actual in current.items():
        original = solved[key]
        if (type(actual) in (int, float) and type(original) in (int, float)
                and (type(actual) is float or type(original) is float)):
            if not math.isfinite(actual) or not math.isfinite(original) or not math.isclose(
                    actual, original, rel_tol=1e-12, abs_tol=1e-12):
                return False
        elif type(actual) is not type(original) or actual != original:
            return False
    return True


class CfdPanel(QWidget):
    settings_changed = Signal()
    apply_feedback = Signal(dict)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main = main_window
        self._restoring = False
        self._job = None
        self._result = None
        self.job_directory = None
        self._runner = None
        self._build_ui()
        from cfd_runner import CfdProcessRunner
        self._runner = CfdProcessRunner(self)
        self._runner.output.connect(self._append_log)
        self._runner.state_changed.connect(self._runner_state_changed)
        self._runner.finished.connect(self._runner_finished)
        self.refresh_source()

    @staticmethod
    def validate_settings(settings):
        """Validate saved structure while preserving unfinished editor text."""
        if not isinstance(settings, dict):
            raise ValueError("CFD 设置必须是对象")
        values = dict(DEFAULT_SETTINGS)
        values.update({key: value for key, value in settings.items() if key in values})
        if "arguments_text" not in settings and "arguments" in settings:
            arguments = settings["arguments"]
            if not isinstance(arguments, list) or any(not isinstance(arg, str) for arg in arguments):
                raise ValueError("CFD 参数必须是字符串数组")
            values["arguments_text"] = json.dumps(arguments, ensure_ascii=False)
        if "timeout_seconds_text" not in settings and "timeout_seconds" in settings:
            timeout = settings["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout):
                raise ValueError("CFD 超时配置必须是有限数字")
            values["timeout_seconds_text"] = str(int(timeout)) if float(timeout).is_integer() else str(timeout)
        for key, accepted in (("route", {"internal", "external"}), ("task", set(TASKS)),
                              ("pressure_basis", set(BASES)), ("temperature_basis", set(BASES)),
                              ("turbine_side", {"ox", "fuel"})):
            if not isinstance(values[key], str) or values[key] not in accepted:
                raise ValueError(f"CFD 设置 {key} 的选项不受支持")
        if not isinstance(values["setup_reviewed"], bool):
            raise ValueError("CFD 外部复核标志必须是布尔值")
        for key in ("executable", "arguments_text", "timeout_seconds_text", "job_path"):
            if not isinstance(values[key], str):
                raise ValueError(f"CFD 设置 {key} 必须保留为文本")
        return values

    def snapshot_settings(self):
        return {"route": self.route_combo.currentData(), "task": self.task_combo.currentData(),
                "pressure_basis": self.pressure_basis_combo.currentData(),
                "temperature_basis": self.temperature_basis_combo.currentData(),
                "setup_reviewed": self.reviewed_checkbox.isChecked(),
                "turbine_side": self.turbine_side_combo.currentData(),
                "executable": self.executable_edit.text(), "arguments_text": self.arguments_edit.text(),
                "timeout_seconds_text": self.timeout_edit.text(), "job_path": self.job_path_edit.text()}

    def restore_settings(self, settings):
        values = self.validate_settings(settings)
        widgets = (self.route_combo, self.task_combo, self.turbine_side_combo, self.pressure_basis_combo, self.temperature_basis_combo,
                   self.reviewed_checkbox, self.executable_edit, self.arguments_edit, self.timeout_edit,
                   self.job_path_edit)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self._restoring = True
        try:
            for combo, key in ((self.route_combo, "route"), (self.task_combo, "task"),
                               (self.turbine_side_combo, "turbine_side"),
                               (self.pressure_basis_combo, "pressure_basis"),
                               (self.temperature_basis_combo, "temperature_basis")):
                combo.setCurrentIndex(combo.findData(values[key]))
            self.reviewed_checkbox.setChecked(values["setup_reviewed"])
            for edit, key in ((self.executable_edit, "executable"), (self.arguments_edit, "arguments_text"),
                              (self.timeout_edit, "timeout_seconds_text"), (self.job_path_edit, "job_path")):
                edit.setText(values[key])
            self._job = self._result = None
            self.job_directory = None
            if values["job_path"]:
                try:
                    self._load_job(values["job_path"], adopt_settings=False)
                except Exception as error:
                    self._append_log(f"暂未能打开保存的任务包：{error}")
        finally:
            self._restoring = False
            for blocker in blockers:
                blocker.unblock()
        self._render_result()
        self.refresh_source()

    def _card(self, heading, subtitle=""):
        frame = QFrame()
        frame.setObjectName("ResultCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel(heading)
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName("MutedLabel")
            label.setWordWrap(True)
            layout.addWidget(label)
        self.content_layout.addWidget(frame)
        return layout

    @staticmethod
    def _combo(choices):
        combo = QComboBox()
        for value, title in choices.items():
            combo.addItem(title, value)
        return combo

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QWidget()
        self.content_layout = QVBoxLayout(contents)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(12)
        scroll.setWidget(contents)
        layout.addWidget(scroll)
        route_card = self._card("计算路线", "循环初算提供边界快照；外部 CFD 结果保持独立，按来源逐项核对。")
        row = QHBoxLayout()
        self.route_combo = self._combo({"internal": "内部初算", "external": "外部 CFD 协同"})
        self.task_combo = self._combo(TASKS)
        row.addWidget(QLabel("路线"))
        row.addWidget(self.route_combo, 1)
        row.addWidget(QLabel("任务"))
        row.addWidget(self.task_combo, 1)
        route_card.addLayout(row)
        turbine_row = QHBoxLayout()
        self.turbine_side_label = QLabel("涡轮侧")
        self.turbine_side_combo = self._combo({"ox": "富氧侧 / 氧泵轴", "fuel": "富燃侧 / 燃料泵轴"})
        turbine_row.addWidget(self.turbine_side_label)
        turbine_row.addWidget(self.turbine_side_combo)
        turbine_row.addStretch()
        route_card.addLayout(turbine_row)
        self.route_note = QLabel()
        self.route_note.setWordWrap(True)
        self.route_note.setObjectName("MutedLabel")
        route_card.addWidget(self.route_note)
        self.source_status = QLabel()
        self.source_status.setWordWrap(True)
        route_card.addWidget(self.source_status)

        job_card = self._card("任务包与边界", "导出通用任务包后，在外部确认网格、物理模型与边界映射。修改口径后请创建新任务包。")
        self.job_path_edit = QLineEdit()
        self.job_path_edit.setReadOnly(True)
        self.job_path_edit.setPlaceholderText("尚未选择任务包")
        job_card.addWidget(self.job_path_edit)
        buttons = QHBoxLayout()
        self.export_button = QPushButton("导出通用任务包")
        self.export_button.setObjectName("PrimaryButton")
        self.open_button = QPushButton("打开既有任务")
        self.export_button.clicked.connect(self._choose_export_job)
        self.open_button.clicked.connect(self._choose_open_job)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        job_card.addLayout(buttons)
        bases = QHBoxLayout()
        self.pressure_basis_combo = self._combo({"unresolved": "尚未确认", "static": "静压", "total": "总压"})
        self.temperature_basis_combo = self._combo({"unresolved": "尚未确认", "static": "静温", "total": "总温"})
        bases.addWidget(QLabel("压力口径"))
        bases.addWidget(self.pressure_basis_combo, 1)
        bases.addWidget(QLabel("温度口径"))
        bases.addWidget(self.temperature_basis_combo, 1)
        job_card.addLayout(bases)
        self.reviewed_checkbox = QCheckBox("已在外部确认网格、物理模型和边界映射")
        self.reviewed_checkbox.setStyleSheet("QCheckBox::indicator:unchecked { border: 1px solid #9baab8; background: #ffffff; border-radius: 2px; }")
        job_card.addWidget(self.reviewed_checkbox)
        self.job_status = QLabel()
        self.job_status.setWordWrap(True)
        job_card.addWidget(self.job_status)

        runner_card = self._card("外部适配器", "选择处理任务包的适配器程序；参数以 JSON 字符串数组传入。适配器负责调用 CFD 求解器。")
        executable_row = QHBoxLayout()
        self.executable_edit = QLineEdit()
        self.executable_edit.setPlaceholderText("选择适配器可执行文件")
        self.browse_button = QPushButton("浏览…")
        self.browse_button.clicked.connect(self._choose_executable)
        executable_row.addWidget(self.executable_edit, 1)
        executable_row.addWidget(self.browse_button)
        runner_card.addLayout(executable_row)
        form = QFormLayout()
        self.arguments_edit = QLineEdit("[]")
        self.arguments_edit.setPlaceholderText('["--manifest", "{manifest}", "--result", "{result}"]')
        self.arguments_edit.setToolTip("支持 {manifest}、{result}、{job_dir}；每项作为独立参数，不经过 shell。")
        self.timeout_edit = QLineEdit("3600")
        form.addRow("参数数组", self.arguments_edit)
        form.addRow("超时 / 秒", self.timeout_edit)
        runner_card.addLayout(form)
        run_row = QHBoxLayout()
        self.run_button = QPushButton("运行外部适配器")
        self.run_button.setObjectName("PrimaryButton")
        self.cancel_button = QPushButton("取消任务")
        self.run_button.clicked.connect(self._start_clicked)
        self.cancel_button.clicked.connect(self.cancel_task)
        self.runner_status = QLabel("尚未运行外部适配器")
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.cancel_button)
        run_row.addWidget(self.runner_status, 1)
        runner_card.addLayout(run_row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(2000)
        self.log_view.setMinimumHeight(125)
        self.log_view.setMaximumHeight(220)
        runner_card.addWidget(self.log_view)

        result_card = self._card("外部结果与初算对比", "支持手动导入其他电脑的 result JSON。结果必须匹配当前任务包；不会覆盖循环初算结果。")
        self.import_button = QPushButton("导入结果 JSON…")
        self.import_button.clicked.connect(self._choose_import_result)
        self.result_status = QLabel("尚未导入外部结果")
        self.result_status.setWordWrap(True)
        result_card.addWidget(self.import_button, alignment=Qt.AlignLeft)
        result_card.addWidget(self.result_status)
        self.metric_table = QTableWidget(0, 3)
        self.metric_table.setHorizontalHeaderLabels(["指标", "外部 CFD / 原始 SI", "初算参考 / SI"])
        self.metric_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.metric_table.setAlternatingRowColors(True)
        self.metric_table.horizontalHeader().setStretchLastSection(True)
        self.metric_table.setMinimumHeight(175)
        result_card.addWidget(self.metric_table)
        from matplotlib.figure import Figure
        from qt_chart_canvas import SafeFigureCanvasQTAgg
        self.wall_figure = Figure(figsize=(8, 2.7), facecolor="#ffffff", layout="constrained")
        self.wall_axes = self.wall_figure.add_subplot(111)
        self.wall_canvas = SafeFigureCanvasQTAgg(self.wall_figure)
        self.wall_canvas.setMinimumHeight(235)
        self.wall_canvas.setMaximumHeight(350)
        self.wall_canvas.hide()
        result_card.addWidget(self.wall_canvas)
        self.feedback_button = QPushButton("采用冷却压降 / 热负荷并复算")
        self.feedback_button.clicked.connect(self._emit_feedback)
        result_card.addWidget(self.feedback_button, alignment=Qt.AlignLeft)
        self.feedback_note = QLabel()
        self.feedback_note.setObjectName("MutedLabel")
        self.feedback_note.setWordWrap(True)
        result_card.addWidget(self.feedback_note)
        self.content_layout.addStretch()
        for combo in (self.route_combo, self.task_combo, self.turbine_side_combo, self.pressure_basis_combo, self.temperature_basis_combo):
            combo.currentIndexChanged.connect(self._settings_edited)
        for edit in (self.executable_edit, self.arguments_edit, self.timeout_edit):
            edit.textChanged.connect(self._settings_edited)
        self.reviewed_checkbox.toggled.connect(self._settings_edited)

    def _settings_edited(self, *_):
        if not self._restoring:
            self.settings_changed.emit()
        self.refresh_source()

    def _source_readiness(self):
        results = getattr(self.main, "last_results", None)
        if results is None:
            return False, "请先运行循环初算，再建立 CFD 任务包。"
        if getattr(self.main, "_result_signature", None) != self.main._signature():
            return False, "当前输入已变化，请重新运行循环初算。"
        try:
            summary = results.summary
            residual = float(summary.get("solver_residual_norm", math.inf))
            if summary.get("solver_warning", False) or not math.isfinite(residual) or residual >= 1e-3:
                return False, "初算尚未数值收敛，不能作为 CFD 任务的边界来源。"
            current_inputs = _plain_inputs(self.main._read_inputs())
            submitted = getattr(self.main, "_solved_input_snapshot", None)
            same = (_plain_inputs(submitted) == current_inputs if submitted is not None else
                    _same_fallback_inputs(_plain_inputs(results.inputs), current_inputs))
            if not same:
                return False, "初算与当前实际计算输入不一致，请重新运行设计点。"
        except Exception as error:
            return False, f"请检查当前输入：{error}"
        count = len(getattr(results, "warnings", []) or [])
        return True, f"初算已数值收敛 · 残差 {residual:.3g}" + (f" · 保留 {count} 项工程警告，导出后仍需检查。" if count else " · 可导出边界快照。")

    def _job_settings_match(self):
        if self._job is None:
            return False
        settings = self.snapshot_settings()
        stored = self._job.get("settings", {})
        return self._job.get("task") == settings["task"] and all(
            stored.get(key, DEFAULT_SETTINGS[key]) == settings[key]
            for key in ("pressure_basis", "temperature_basis", "setup_reviewed", "turbine_side"))

    def _job_inputs_match(self):
        if self._job is None:
            return False
        try:
            return self._job.get("inputs") == _plain_inputs(self.main._read_inputs())
        except Exception:
            return False

    def _feedback_values(self):
        result = self._result
        if not result or not self._job or self._job.get("task") != "cooling_flow":
            return None
        if result.get("source_kind") != "cfd" or result.get("converged") is not True or not result.get("feedback_eligible", False):
            return None
        if result.get("job_id") != self._job.get("job_id") or result.get("input_fingerprint") != self._job.get("input_fingerprint"):
            return None
        if not self._job_settings_match() or not self._job_inputs_match():
            return None
        feedback = {}
        metrics = result.get("metrics", {})
        for metric, field in (("pressure_drop_Pa", "cooling_dp_pa"), ("heat_load_W", "cooling_heat_load")):
            item = metrics.get(metric)
            value = item.get("value") if isinstance(item, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                feedback[field] = float(value)
        return feedback or None

    def refresh_source(self):
        external = self.route_combo.currentData() == "external"
        turbine = self.task_combo.currentData() == "turbine_flow"
        self.turbine_side_combo.setVisible(turbine)
        self.turbine_side_label.setVisible(turbine)
        self.route_note.setText("外部 CFD 协同：保留循环初算；详细流动和沿程冷却交由外部求解。暂停本地 FEM，结构分析需独立流程。"
                                if external else "内部初算：保留已有循环、冷却、预览和 FEM 工作流程。选择外部路线后建立独立 CFD 任务。")
        source_ready, source_text = self._source_readiness()
        self.source_status.setText(source_text)
        busy = bool(getattr(self.main, "_busy", False)) or self.has_running_task()
        self.export_button.setEnabled(external and source_ready and not busy)
        self.open_button.setEnabled(not busy)
        self.import_button.setEnabled(self._job is not None and not busy)
        reviewed = self._job is not None and self._job.get("settings", {}).get("setup_reviewed") is True
        bases_ready = self._job is not None and all(self._job.get("settings", {}).get(key) in {"static", "total"}
                                                   for key in ("pressure_basis", "temperature_basis"))
        matched = self._job_settings_match()
        same_inputs = self._job_inputs_match()
        result_exists = self.job_directory is not None and (self.job_directory / "result.json").exists()
        self.run_button.setEnabled(external and reviewed and bases_ready and matched and same_inputs and not busy and not result_exists)
        self.cancel_button.setEnabled(self.has_running_task())
        for widget in (self.route_combo, self.task_combo, self.turbine_side_combo, self.pressure_basis_combo, self.temperature_basis_combo,
                       self.reviewed_checkbox, self.executable_edit, self.arguments_edit, self.timeout_edit, self.browse_button):
            widget.setEnabled(not busy)
        if self._job is None:
            self.job_status.setText("尚未打开有效任务包。")
        elif not matched:
            self.job_status.setText("当前任务或边界口径已变化，请创建新任务包。")
        elif not same_inputs:
            self.job_status.setText("任务包来自另一组输入；可以查看外部结果，不能用于当前工况复算。")
        elif not reviewed or not bases_ready:
            self.job_status.setText("任务包仍待确认边界口径或外部复核；请确认后新建任务包再运行。")
        elif not self._job.get("boundary_review", {}).get("complete", False):
            missing = self._job.get("boundary_review", {}).get("missing_references", [])
            self.job_status.setText("模型边界参考尚不完整，外部结果仅供诊断：" + "；".join(map(str, missing)))
        elif result_exists:
            self.job_status.setText("任务包已有 result.json，可导入查看；重新运行请新建任务。")
        else:
            self.job_status.setText("任务包就绪 · 来源与当前输入一致。")
        feedback = self._feedback_values()
        self.feedback_button.setEnabled(external and feedback is not None and not busy)
        feedback_text = ("仅采用经任务来源校验、求解器报告收敛的冷却 CFD 压降与热负荷；复算仍需检查工程约束。"
                         if feedback else "冷却 CFD 结果需报告收敛、明确边界口径并完成外部复核，且来源必须匹配当前输入，才可用于复算。")
        self.feedback_note.setText(feedback_text + "\n热负荷与流量按全发动机 / 全部冷却通道总量交接；单通道 CFD 须由适配器汇总后反馈。")

    def _load_job(self, directory, *, adopt_settings):
        from cfd_exchange import load_cfd_job, load_imported_result
        path = Path(directory).expanduser().resolve()
        job = load_cfd_job(path)
        result = load_imported_result(path)
        self.job_directory, self._job, self._result = path, job, result
        self.job_path_edit.setText(str(path))
        if adopt_settings:
            settings = job.get("settings", {})
            widgets = (self.route_combo, self.task_combo, self.turbine_side_combo, self.pressure_basis_combo, self.temperature_basis_combo, self.reviewed_checkbox)
            blockers = [QSignalBlocker(widget) for widget in widgets]
            try:
                self.route_combo.setCurrentIndex(self.route_combo.findData("external"))
                self.task_combo.setCurrentIndex(self.task_combo.findData(job["task"]))
                self.turbine_side_combo.setCurrentIndex(self.turbine_side_combo.findData(settings.get("turbine_side", "ox")))
                for combo, key in ((self.pressure_basis_combo, "pressure_basis"), (self.temperature_basis_combo, "temperature_basis")):
                    combo.setCurrentIndex(combo.findData(settings.get(key, "unresolved")))
                self.reviewed_checkbox.setChecked(settings.get("setup_reviewed", False))
            finally:
                for blocker in blockers:
                    blocker.unblock()
        self._render_result()
        self.refresh_source()

    def open_job(self, directory):
        self._load_job(directory, adopt_settings=True)
        self.settings_changed.emit()

    def create_job(self, directory):
        if self.has_running_task() or getattr(self.main, "_busy", False):
            raise ValueError("请先等待当前任务结束")
        if self.route_combo.currentData() != "external":
            raise ValueError("请先选择外部 CFD 协同路线")
        ready, reason = self._source_readiness()
        if not ready:
            raise ValueError(reason)
        from cfd_exchange import create_cfd_job
        settings = self.snapshot_settings()
        path = create_cfd_job(self.main.last_results, getattr(self.main, "last_geometry", None), directory,
                              settings["task"], settings, getattr(self.main, "_runtime_report", {}),
                              source_inputs=_plain_inputs(self.main._read_inputs()))
        self._load_job(path, adopt_settings=False)
        for warning in getattr(self.main.last_results, "warnings", []) or []:
            self._append_log("初算工程警告：" + str(warning))
        self.settings_changed.emit()
        return Path(path)

    def import_result(self, result_path=None):
        if self.job_directory is None:
            raise ValueError("请先打开结果对应的任务包")
        from cfd_exchange import import_cfd_result
        self._result = import_cfd_result(self.job_directory, result_path)
        self._render_result()
        self.refresh_source()
        return self._result

    def _render_result(self):
        result = self._result
        self.metric_table.setRowCount(0)
        if not result:
            self.result_status.setText("尚未导入外部结果")
            self.wall_canvas.hide()
            return
        if result.get("source_kind") == "test":
            self.result_status.setText("测试数据 · 仅用于接口验证，不代表实际 CFD 计算。")
        else:
            reported = "求解器报告收敛" if result.get("converged") is True else "求解器报告未收敛"
            diagnostic = " · 当前仅供诊断" if result.get("diagnostic_only") else ""
            self.result_status.setText(reported + diagnostic)
        reasons = result.get("eligibility_reasons", [])
        if reasons:
            self.result_status.setText(self.result_status.text() + "\n" + "；".join(map(str, reasons)))
            self.result_status.setToolTip("\n".join(map(str, reasons)))
        else:
            self.result_status.setToolTip("")
        reference = self._reference_metrics()
        labels = {"pressure_drop_Pa": "压降 / Pa", "heat_load_W": "热负荷 / W"}
        for metric, item in result.get("metrics", {}).items():
            value = item.get("value") if isinstance(item, dict) else None
            row = self.metric_table.rowCount()
            self.metric_table.insertRow(row)
            values = (labels.get(metric, metric), self._metric_text(value), self._metric_text(reference.get(metric)))
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.metric_table.setItem(row, column, item)
        self.metric_table.resizeColumnsToContents()
        profile = result.get("wall_profile")
        if isinstance(profile, dict) and profile.get("x_m") and profile.get("wall_temperature_K"):
            self.wall_axes.clear()
            self.wall_axes.plot(profile["x_m"], profile["wall_temperature_K"], color="#527f9a", linewidth=1.8)
            self.wall_axes.set_xlabel("x / m")
            self.wall_axes.set_ylabel("Wall temperature / K")
            self.wall_axes.set_title("External wall profile · solver output", fontsize=11)
            self.wall_axes.grid(True, color="#e1e7ed", linewidth=0.6)
            origin = (self._job or {}).get("geometry_reference", {}).get("contour", {}).get("coordinate_origin", "未定义")
            self.wall_canvas.setToolTip(f"外部求解器提供的壁温曲线；x 坐标原点：{origin}。曲线展示不代表场数据认证。")
            self.wall_canvas.show()
            self.wall_canvas.draw_idle()
        else:
            self.wall_canvas.hide()

    def _reference_metrics(self):
        job = self._job or {}
        inputs = job.get("inputs", {})
        initial = job.get("initial_results", {})
        summary = initial.get("summary", {})
        reference = {"thrust_N": summary.get("thrust_N")}
        inlets = [row.get("mass_flow_kg_s") for row in job.get("boundaries", []) if row.get("role") == "inlet"]
        if inlets and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                          for value in inlets):
            reference["mass_flow_kg_s"] = sum(inlets)
        if job.get("task") == "cooling_flow":
            reference.update(pressure_drop_Pa=inputs.get("cooling_dp_pa"), heat_load_W=inputs.get("cooling_heat_load"))
        elif job.get("task") == "turbine_flow":
            side = job.get("settings", {}).get("turbine_side", "ox")
            if inputs.get("cycle_type") == "gg":
                component_name = f"gg_turbine_{side}" if summary.get("gg_layout_dual") else "gg_turbine"
            else:
                component_name = f"{side}_preburner_turbine"
            outputs = initial.get("components", {}).get(component_name, {}).get("outputs", {})
            reference["shaft_power_W"] = outputs.get("turbine_power_W")
            reference["efficiency"] = outputs.get("eta_turbine")
        outlets = [row.get("temperature_K") for row in job.get("boundaries", []) if row.get("role") == "outlet_reference"]
        if len(outlets) == 1:
            reference["outlet_temperature_K"] = outlets[0]
        return reference

    @staticmethod
    def _metric_text(value):
        if value is None:
            return "—"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return format(value, ".10g")
        return str(value)

    def _adapter_arguments(self):
        try:
            arguments = json.loads(self.arguments_edit.text())
        except (ValueError, TypeError) as error:
            raise ValueError("参数数组必须是有效 JSON，例如 [\"--manifest\", \"{manifest}\"]") from error
        if not isinstance(arguments, list) or any(not isinstance(argument, str) for argument in arguments):
            raise ValueError("适配器参数必须是 JSON 字符串数组")
        text = self.timeout_edit.text().strip()
        try:
            timeout = int(text)
        except ValueError as error:
            raise ValueError("超时必须是 1–86400 秒之间的整数") from error
        if not 1 <= timeout <= 86400:
            raise ValueError("超时必须是 1–86400 秒之间的整数")
        return arguments, timeout

    def start_task(self):
        self.refresh_source()
        if not self.run_button.isEnabled():
            raise ValueError("任务尚未就绪，请检查来源、边界口径与外部复核状态")
        arguments, timeout = self._adapter_arguments()
        executable = self.executable_edit.text().strip()
        if not executable:
            raise ValueError("请选择外部适配器可执行文件")
        self._runner.start(self.job_directory, executable, arguments, timeout_seconds=timeout)
        self.refresh_source()

    def has_running_task(self):
        return bool(self._runner is not None and self._runner.is_running)

    def cancel_task(self):
        if self._runner is not None and self._runner.is_running:
            self._runner.cancel()
            self.refresh_source()

    def _emit_feedback(self):
        feedback = self._feedback_values()
        if feedback is not None and self.route_combo.currentData() == "external" and not self.has_running_task() and not getattr(self.main, "_busy", False):
            self.apply_feedback.emit(feedback)

    def _append_log(self, text):
        if text:
            self.log_view.appendPlainText(str(text).rstrip("\n"))

    def _runner_state_changed(self, state):
        self.runner_status.setText({"starting": "正在启动适配器", "running": "外部适配器运行中",
                                    "cancelling": "正在取消任务"}.get(state, str(state)))
        self.refresh_source()

    def _runner_finished(self, exit_code, state):
        self.runner_status.setText({"completed": "适配器已结束", "failed": "适配器运行失败",
                                    "cancelled": "外部任务已取消", "timed_out": "外部任务超时"}.get(state, str(state)))
        self._append_log(f"适配器退出：{state}，退出码 {exit_code}")
        if state == "completed":
            try:
                self.import_result()
            except Exception as error:
                self._append_log(f"结果尚未能导入：{error}")
        self.refresh_source()

    def _choose_export_job(self):
        directory = QFileDialog.getExistingDirectory(self, "选择 CFD 任务包存放位置")
        if directory:
            self._ui_action(lambda: self.create_job(directory), "导出任务失败")

    def _choose_open_job(self):
        directory = QFileDialog.getExistingDirectory(self, "打开 CFD 任务包文件夹")
        if directory:
            self._ui_action(lambda: self.open_job(directory), "打开任务失败")

    def _choose_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择外部适配器程序", self.executable_edit.text(), "所有文件 (*)")
        if path:
            self.executable_edit.setText(path)

    def _choose_import_result(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 CFD 结果 JSON", str(self.job_directory or Path.home()), "JSON (*.json)")
        if path:
            self._ui_action(lambda: self.import_result(path), "导入结果失败")

    def _start_clicked(self):
        self._ui_action(self.start_task, "外部任务未启动")

    def _ui_action(self, action, heading):
        try:
            action()
        except Exception as error:
            self._append_log(f"{heading}：{error}")
            QMessageBox.warning(self, heading, str(error))
