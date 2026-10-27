"""A new desktop shell around the original calculation and analysis pages.

The calculation modules are kept separate from the workspace. No physical
constants or solver tolerances are changed by this interface.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QIcon, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStatusBar,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QToolButton, QVBoxLayout, QWidget, QListView,
)

import gui_pyside6 as legacy
from core import EngineInputs
from thermo import CANTERA_AVAILABLE, COOLPROP_AVAILABLE, ROCKETCEA_AVAILABLE
from version import (APP_NAME, APP_FORMAL_NAME, APP_ENGLISH_NAME,
                     APP_ENGLISH_FULL_NAME, APP_VERSION, APP_VERSION_LABEL)
from workbench_canvas import EngineCanvas
from workbench_style import stylesheet
from qt_chart_canvas import retire_charts
from shiboken6 import isValid
from windows_identity import configure_window_identity, clear_window_identity
from scan_parameters import build_scan_points, verify_scan_replay, validate_thermo_scan
from case_safety import CaseSafetyMixin
from runtime_environment import environment_report, environment_problems
from engineering_assessment import assess_engineering
from materials import material_evidence
from cfd_integration import CfdIntegrationMixin
from cfd_panel import CfdPanel

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    ("overview", "总体性能", "查看设计点性能、求解状态与可行性警告", "总体设计"),
    ("topology", "流程拓扑", "查看推进剂流路、分流和部件连接关系", ""),
    ("states", "状态点", "查看沿流路的压力、温度、组分和压降", ""),
    ("components", "部件结果", "查看泵、涡轮、预燃室和喷管的详细结果", ""),
    ("geometry", "几何估算", "初步尺寸、部件图纸与三维示意", "部件设计"),
    ("chamber_design", "推力室设计", "边界快照 → 型面 → 冷却 → 热结构 → 校核", ""),
    ("cfd", "CFD 协同", "循环初算 → 任务交接 → 外部 CFD → 结果回传", ""),
    ("materials", "材料库", "材料工况校核、内衬对比与研发指标", ""),
    ("engchar", "发动机特性", "节流扫描与固定喷管的高度特性", "参数分析"),
    ("of_sweep", "涡轮设计点扫描", "扫描预燃室混合比并选取候选设计点", ""),
    ("thermo", "热力学对比", "比较化学平衡计算及不同模型的预测", ""),
    ("log", "日志与警告", "检查计算进度、残差和错误原因", "运行记录"),
]


def chinese_title(text):
    """Keep English in tooltips, reduce labels without changing quantities."""
    return text.split(" / ", 1)[0]


class ResponsiveKpis(QFrame):
    """Keep performance values readable when the workspace is narrow."""
    def __init__(self, cards):
        super().__init__()
        self.setObjectName("KpiRow")
        self.cards = cards
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.columns = 0
        for card in cards:
            card.setMinimumHeight(110)
            for label in card.findChildren(QLabel, "KpiValue"):
                label.setWordWrap(True)
                label.setMinimumHeight(42)
        self.arrange(3)

    def arrange(self, columns):
        if columns == self.columns:
            return
        while self.grid.count():
            self.grid.takeAt(0)
        for i, card in enumerate(self.cards):
            self.grid.addWidget(card, i // columns, i % columns)
        for i in range(3):
            self.grid.setColumnStretch(i, 1 if i < columns else 0)
        row_count = math.ceil(len(self.cards) / columns)
        for i in range(len(self.cards)):
            self.grid.setRowMinimumHeight(i, 120 if i < row_count else 0)
        self.setMinimumHeight(row_count * 120 + max(0, row_count - 1) * 10)
        self.grid.invalidate()
        self.updateGeometry()
        self.columns = columns

    def resizeEvent(self, event):
        self.arrange(3 if self.width() >= 800 else 2 if self.width() >= 440 else 1)
        super().resizeEvent(event)


class WorkbenchWindow(CfdIntegrationMixin, CaseSafetyMixin, legacy.MainWindow):
    def __init__(self, recovery_directory=None):
        self._busy = False
        self._case_path = None
        self._saved_signature = None
        self._result_signature = None
        self._submitted_signature = None
        self._submitted_input_snapshot = None
        self._solved_input_snapshot = None
        self._loading_case = False
        self._nav_buttons = {}
        self._form_rows = {}
        self._disabled_buttons = []
        self._extra_inputs = {}
        self._task_kind = None
        self._applied_scan_record = None
        self._last_scan_replay_check = None
        self._cfd_feedback_history = []
        self._taskbar_hwnd = None
        self._taskbar_screen_connected = False
        super().__init__()
        self._taskbar_icon_timer = QTimer(self)
        self._taskbar_icon_timer.setSingleShot(True)
        self._taskbar_icon_timer.timeout.connect(self._refresh_taskbar_icon)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION_LABEL} · {APP_ENGLISH_NAME}")
        self.setWindowIcon(QIcon(str(ROOT / "assets" / ("app.ico" if sys.platform == "win32" else "app.svg"))))
        self.resize(1480, 930)
        self.setMinimumSize(1100, 700)
        self._change_timer = QTimer(self)
        self._change_timer.setSingleShot(True)
        self._change_timer.timeout.connect(self._update_case_state)
        for edit in self.input_edits.values():
            signal = edit.currentIndexChanged if isinstance(edit, QComboBox) else edit.textChanged
            signal.connect(self._queue_case_change)
        self._saved_signature = self._signature()
        self._update_case_state()
        self._polish_analysis_pages()
        self._polish_combo_popups()
        self._material_detail_timer = QTimer(self)
        self._material_detail_timer.setSingleShot(True)
        self._material_detail_timer.timeout.connect(self._render_selected_material)
        self._sync_of_sweep_availability()
        self._runtime_report = environment_report(ROOT)
        self.environment_label.setText("完整科学环境 · Cantera / CoolProp" if self._runtime_report["ready"] else "环境不完整 · 查看使用指南")
        self.environment_label.setToolTip("运行环境已校验；版本信息会随方案与结果导出。" if self._runtime_report["ready"] else "\n".join(environment_problems(self._runtime_report)))
        self._init_case_safety(recovery_directory)
        for edit in (self.of_sweep_start, self.of_sweep_end, self.of_sweep_step,
                     self._thermo_of_min, self._thermo_of_max, self._thermo_of_n,
                     self._thermo_eps, self._thermo_pc, self._thermo_T_fuel, self._thermo_T_ox):
            edit.textChanged.connect(self._analysis_case_changed)
        self.of_sweep_side.currentIndexChanged.connect(self._analysis_case_changed)
        self.cfd_panel.settings_changed.connect(self._on_cfd_settings_changed)
        self.cfd_panel.apply_feedback.connect(self._apply_cfd_feedback)
        self.cfd_panel._runner.state_changed.connect(self._on_cfd_process_state)
        self._apply_cfd_route()
        self._update_case_state()
        self.statusBar().showMessage("就绪 · 选择工况后运行设计点")

    def _analysis_case_changed(self, *_):
        self._schedule_case_recovery()
        self._update_case_state()

    def _build_ui(self):
        self._font_size = 14
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(host)
        self.setStatusBar(QStatusBar())
        header = QFrame()
        header.setObjectName("AppHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(18, 11, 18, 11)
        logo = QLabel()
        logo.setPixmap(QIcon(str(ROOT / "assets" / "app.svg")).pixmap(34, 34))
        row.addWidget(logo)
        branding = QVBoxLayout()
        branding.setSpacing(0)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        brand.setToolTip(APP_FORMAL_NAME)
        sub = QLabel(APP_ENGLISH_NAME)
        sub.setObjectName("BrandSub")
        sub.setToolTip(APP_ENGLISH_FULL_NAME)
        branding.addWidget(brand)
        branding.addWidget(sub)
        row.addLayout(branding)
        self.document_label = QLabel("未命名方案")
        self.document_label.setObjectName("DocumentTitle")
        row.addWidget(self.document_label, 1)
        self.open_button = QPushButton("打开方案")
        self.open_button.clicked.connect(self.open_case)
        self.save_button = QPushButton("保存方案")
        self.save_button.clicked.connect(self.save_case)
        row.addWidget(self.open_button)
        row.addWidget(self.save_button)
        self.export_button = QToolButton()
        self.export_button.setText("导出")
        self.export_button.setPopupMode(QToolButton.InstantPopup)
        self._export_menu = QMenu(self)
        self.export_button.setMenu(self._export_menu)
        row.addWidget(self.export_button)
        self.header_run = QPushButton("运行设计点")
        self.header_run.setObjectName("PrimaryButton")
        self.header_run.clicked.connect(self.run_cycle)
        row.addWidget(self.header_run)
        self.cancel_button = QPushButton("取消计算")
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)
        layout.addWidget(header)

        body = QWidget()
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        navigation = QFrame()
        navigation.setObjectName("Navigation")
        navigation.setFixedWidth(176)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(0, 10, 0, 10)
        nav_layout.setSpacing(1)
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_contents = QWidget()
        nav_contents.setObjectName("NavContents")
        nav_flow = QVBoxLayout(nav_contents)
        nav_flow.setContentsMargins(0, 0, 0, 0)
        nav_flow.setSpacing(1)
        section_number = 0
        for key, title, description, section in PAGES:
            if section:
                section_number += 1
                section_frame = QFrame()
                section_frame.setObjectName("NavSection")
                section_row = QHBoxLayout(section_frame)
                section_row.setContentsMargins(11, 7, 9, 7)
                section_row.setSpacing(8)
                number = QLabel(f"{section_number:02d}")
                number.setObjectName("NavSectionNumber")
                lab = QLabel(section)
                lab.setObjectName("NavSectionTitle")
                section_row.addWidget(number)
                section_row.addWidget(lab, 1)
                nav_flow.addWidget(section_frame)
            button = QPushButton(title)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setToolTip(description)
            button.clicked.connect(lambda checked=False, k=key: self.navigate(k))
            self._nav_buttons[key] = button
            nav_flow.addWidget(button)
        nav_flow.addStretch()
        nav_scroll.setWidget(nav_contents)
        nav_layout.addWidget(nav_scroll, 1)
        about_button = QPushButton("使用指南与环境")
        about_button.setObjectName("NavButton")
        about_button.clicked.connect(self.show_guide)
        nav_layout.addWidget(about_button)
        version = QLabel(f"   DESKTOP  /  {APP_VERSION_LABEL}")
        nav_layout.addWidget(version)
        body_row.addWidget(navigation)

        self.workspace_splitter = QSplitter(Qt.Horizontal)
        self.parameter_pane = QFrame()
        self.parameter_pane.setObjectName("ParameterPane")
        pane_layout = QVBoxLayout(self.parameter_pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.addWidget(self._build_left_panel())
        self.parameter_pane.setMinimumWidth(290)
        self.parameter_pane.setMaximumWidth(440)
        self.workspace_splitter.addWidget(self.parameter_pane)

        # All eleven analysis pages stay connected to their original callbacks.
        original_right = super()._build_right_panel()
        self.cfd_panel = CfdPanel(self)
        self._tab_idx["cfd"] = self.tabs.addTab(self.cfd_panel, "CFD 协同")
        self.tabs.setTabBarAutoHide(True)
        self.tabs.tabBar().hide()
        for _ in range(original_right.layout().count()):
            item = original_right.layout().takeAt(0)
            if item.widget() and item.widget() is not self.tabs:
                item.widget().hide()
        # Original labels are used by the result callback; hide their old header.
        # Their legacy parent is no longer part of the UI. Give the labels a
        # lasting QObject parent so queued worker callbacks cannot see a
        # deleted C++ widget after the temporary container is collected.
        self._result_title_label.setParent(self)
        self._result_subtitle_label.setParent(self)
        self._result_title_label.hide()
        self._result_subtitle_label.hide()
        original_right.deleteLater()
        original_right.hide()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(22, 17, 22, 12)
        right_layout.setSpacing(12)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("PageSubtitle")
        self.page_subtitle.setWordWrap(True)
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        heading.addLayout(titles, 1)
        heading.addWidget(self.badge)
        self.toggle_parameters = QPushButton("收起参数")
        self.toggle_parameters.clicked.connect(self.toggle_parameter_pane)
        heading.addWidget(self.toggle_parameters)
        right_layout.addLayout(heading)
        self.result_banner = QFrame()
        self.result_banner.setObjectName("ResultBanner")
        banner_layout = QHBoxLayout(self.result_banner)
        banner_layout.setContentsMargins(10, 7, 10, 7)
        self.result_banner_label = QLabel()
        self.result_banner_label.setWordWrap(True)
        banner_layout.addWidget(self.result_banner_label)
        right_layout.addWidget(self.result_banner)
        right_layout.addWidget(self.tabs, 1)
        self.workspace_splitter.addWidget(right)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([310, 950])
        body_row.addWidget(self.workspace_splitter, 1)
        layout.addWidget(body, 1)
        self.tabs.currentChanged.connect(self._sync_navigation)
        self.navigate("overview")
        self._show_welcome()
        mode = "Cantera" if CANTERA_AVAILABLE else "经验热力学"
        self.environment_label = QLabel(f"{mode}   ·   {'真实流体' if COOLPROP_AVAILABLE else '备用物性修正'}")
        self.environment_label.setToolTip("在「使用指南与环境」查看可选计算依赖")
        self.statusBar().addPermanentWidget(self.environment_label)

    def _build_left_panel(self):
        panel = super()._build_left_panel()
        layout = panel.layout()
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(9)
        # Move the legacy text-size control into the View menu.
        layout.itemAt(0).widget().setText("参数设置")
        font_row = layout.itemAt(1).layout()
        for i in range(font_row.count()):
            if font_row.itemAt(i).widget():
                font_row.itemAt(i).widget().hide()
        self._font_combo.setCurrentIndex(1)
        self._font_size = 14
        layout.itemAt(2).widget().setText("推力 kN · 压力 MPa · 温度 K")
        self.parameter_search = QLineEdit()
        self.parameter_search.setPlaceholderText("搜索参数或单位…")
        self.parameter_search.setClearButtonEnabled(True)
        self.parameter_search.textChanged.connect(self._filter_parameters)
        self.group_selector = QComboBox()
        self.group_selector.currentIndexChanged.connect(self._filter_parameters)
        layout.insertWidget(3, self.parameter_search)
        layout.insertWidget(4, self.group_selector)
        scroll = panel.findChild(QScrollArea)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = scroll.widget()
        content.setMinimumWidth(0)
        content.layout().setContentsMargins(0, 0, 3, 0)
        for title, box in self._param_group_boxes.items():
            box.setTitle(chinese_title(title))
            form = box.layout()
            form.setRowWrapPolicy(QFormLayout.WrapAllRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(6)
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.LabelRole)
                field_item = form.itemAt(row, QFormLayout.FieldRole)
                label = label_item.widget() if label_item else None
                edit = field_item.widget() if field_item else None
                if isinstance(label, QLabel):
                    full = label.text()
                    label.setToolTip(full)
                    # Preserve units that were at the end of bilingual labels.
                    unit = full[full.rfind(" ["):] if " [" in full else ""
                    label.setText(chinese_title(full) + (unit if " [" not in chinese_title(full) else ""))
                    label.setWordWrap(True)
                if isinstance(edit, QComboBox):
                    edit.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                    edit.setMinimumContentsLength(8)
                if edit:
                    edit.setMinimumWidth(0)
                search = (title + " " + (label.toolTip() if label else "") + " " + (edit.toolTip() if edit else "")).casefold()
                self._form_rows[(title, row)] = search
        self.btn_run.setText("运行设计点")
        self.btn_throttle.setText("节流扫描")
        self._preset_btn.setText("应用参考工况")
        self.progress.setTextVisible(False)
        self.progress_label.setWordWrap(True)
        self._refresh_group_selector()
        return panel

    def _compatible_groups(self):
        is_gg = self._current_cycle_type() == "gg"
        return [title for title in self._param_group_boxes
                if not (is_gg and title in legacy.FFSC_ONLY_GROUPS)
                and not (not is_gg and title in legacy.GG_ONLY_GROUPS)]

    def _refresh_group_selector(self):
        if not hasattr(self, "group_selector"):
            return
        selected = self.group_selector.currentData()
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        for title in self._compatible_groups():
            self.group_selector.addItem(chinese_title(title), title)
        index = self.group_selector.findData(selected)
        self.group_selector.setCurrentIndex(max(0, index))
        self.group_selector.blockSignals(False)
        self._filter_parameters()

    def _filter_parameters(self, *_):
        if not hasattr(self, "group_selector"):
            return
        query = self.parameter_search.text().strip().casefold()
        selected = self.group_selector.currentData()
        for title, box in self._param_group_boxes.items():
            compatible = title in self._compatible_groups()
            matches = False
            form = box.layout()
            for row in range(form.rowCount()):
                found = not query or query in self._form_rows.get((title, row), title.casefold())
                form.setRowVisible(row, found)
                matches |= found
            box.setVisible(compatible and matches and (bool(query) or title == selected))
        self.group_selector.setEnabled(not query)
        cycle_form = self._param_group_boxes["循环方式 / Engine cycle"].layout()
        # This field has no meaning in FFSC, so avoid showing it as an input.
        cycle_form.setRowVisible(self.input_edits["gg_layout"], self._current_cycle_type() == "gg" and (not query or query in self._form_rows.get(("循环方式 / Engine cycle", 1), "")))

    def _on_cycle_type_changed(self, *args):
        super()._on_cycle_type_changed(*args)
        self._refresh_group_selector()
        self._sync_of_sweep_availability()

    def _refresh_preset_combo(self, cycle=None):
        super()._refresh_preset_combo(cycle)
        if hasattr(self, "_preset_combo"):
            for i in range(self._preset_combo.count()):
                key = self._preset_combo.itemData(i)
                full = self._preset_combo.itemText(i)
                self._preset_combo.setItemData(i, full, Qt.ToolTipRole)
                self._preset_combo.setItemText(i, {"raptor3_ffsc": "Raptor 3 · FFSC", "tq12a_gg": "天鹊 TQ-12A · GG"}.get(key, full))

    def _polish_analysis_pages(self):
        # Unicode emoji differ across platforms; use plain action labels.
        for button in self.findChildren(QPushButton) + self.findChildren(QToolButton):
            text = re.sub(r"^[\U0001F000-\U0001FFFF\u2600-\u27bf\ufe0f\s]+", "", button.text())
            button.setText(text)
        cycle = self.input_edits["cycle_type"]
        cycle.setItemText(0, "全流量分级燃烧（FFSC）")
        cycle.setItemText(1, "燃气发生器（GG）")
        shaft = self.input_edits["gg_layout"]
        shaft.setItemText(0, "单轴：一台涡轮驱动双泵")
        shaft.setItemText(1, "双轴：双涡轮分别驱动")
        self._refresh_preset_combo()

    def _polish_combo_popups(self):
        """Style the actual popup and viewport, independently of dark OS themes.

        The parameter pane's generic transparent QWidget rule otherwise wins
        over the inherited popup background. A dedicated QListView with its
        own palette/style also avoids platform-native popup colour changes.
        """
        for combo in self.findChildren(QComboBox):
            view = combo.view()
            # Keep the wrapper alive as well as letting Qt own the native view.
            # A malformed/retired wrapper must not receive QObject methods.
            if not hasattr(self, "_combo_popup_views"):
                self._combo_popup_views = {}
            if isinstance(view, QListView) and isValid(view) and view.objectName() == "ComboPopup":
                self._combo_popup_views[combo] = view
                continue
            view = QListView(combo)
            view.setObjectName("ComboPopup")
            combo.setView(view)
            self._combo_popup_views[combo] = view
            palette = view.palette()
            for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                palette.setColor(group, QPalette.Base, QColor("#ffffff"))
                palette.setColor(group, QPalette.Window, QColor("#ffffff"))
                palette.setColor(group, QPalette.Text, QColor("#203747"))
                palette.setColor(group, QPalette.Highlight, QColor("#355e75"))
                palette.setColor(group, QPalette.HighlightedText, QColor("#ffffff"))
            view.setPalette(palette)
            view.viewport().setPalette(palette)
            view.viewport().setAutoFillBackground(True)
            view.setStyleSheet("""
                QListView#ComboPopup { background-color: #ffffff; color: #203747;
                    border: 1px solid #c4d2dd; padding: 4px;
                    selection-background-color: #355e75; selection-color: #ffffff; outline: 0; }
                QListView#ComboPopup::item { background-color: #ffffff; color: #203747;
                    padding: 6px 10px; min-height: 22px; border-radius: 3px; }
                QListView#ComboPopup::item:selected { background-color: #355e75; color: #ffffff; }
                QListView#ComboPopup::item:hover:!selected { background-color: #e6eef4; color: #203747; }
            """)
            view.viewport().setStyleSheet("background-color: #ffffff;")

    def _populate_materials_tab(self, results):
        super()._populate_materials_tab(results)
        self.materials_tree.setColumnCount(8)
        self.materials_tree.headerItem().setText(5, "富氧 O2")
        self.materials_tree.headerItem().setText(7, "数据依据")
        header = self.materials_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, legacy.QHeaderView.Interactive)
        self.materials_tree.setColumnWidth(1, 270)
        for column, width in ((6, 200), (7, 180)):
            header.setSectionResizeMode(column, legacy.QHeaderView.Interactive)
            self.materials_tree.setColumnWidth(column, width)
        self._tag_material_evidence()
        self._polish_combo_popups()

    def _tag_material_evidence(self):
        for i in range(self.materials_tree.topLevelItemCount()):
            parent = self.materials_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                meta = item.data(0, Qt.UserRole) or {}
                if not meta.get("mat_key"):
                    continue
                evidence = material_evidence(meta["mat_key"])
                item.setText(7, evidence["status_label"])
                item.setToolTip(7, evidence["source"] + "\n" + evidence["note"])
                item.setForeground(7, QColor("#a65a0a"))
                if evidence["oxygen_compatibility_status"] == "unknown":
                    item.setText(5, "待核实")
                    item.setForeground(5, QColor("#a65a0a"))
                if evidence["status"] == "placeholder":
                    item.setText(6, "占位估算 · 校核结论待确认")
                    item.setForeground(6, QColor("#a65a0a"))
                combo = self.materials_tree.itemWidget(item, 1)
                if combo:
                    combo.setToolTip("此处仅浏览材料，不修改设计输入；要用于计算，请在参数设置中选材。")

    def _on_tree_material_changed(self, field_key, new_key, item):
        super()._on_tree_material_changed(field_key, new_key, item)
        self._tag_material_evidence()

    @Slot()
    def _on_material_selected(self):
        # Scroll-wheel changes can emit many selections in a single gesture.
        # Read the final selected row when the short debounce timer expires.
        if not hasattr(self, "_material_detail_timer"):
            return
        if self.materials_tree.selectedItems():
            self._material_detail_timer.start(80)
        else:
            self._material_detail_timer.stop()

    def _render_selected_material(self):
        super()._on_material_selected()

    def _render_material_detail(self, meta):
        while self.materials_detail_layout.count():
            item = self.materials_detail_layout.takeAt(0)
            if item.widget():
                retire_charts(item.widget())
                item.widget().hide()
                item.widget().deleteLater()
        super()._render_material_detail(meta)
        evidence = material_evidence(meta["mat_key"])
        card = self._make_card("数据依据与可信度", [
            ("数据性质", evidence["status_label"], ""),
            ("来源", evidence["source"], ""),
            ("氧相容性", "待试验核实" if evidence["oxygen_compatibility_status"] == "unknown" else "模型筛选值，需结合具体工况试验", ""),
        ], notes=evidence["note"], badges=[("仅用于初步筛选", "warn")])
        self.materials_detail_layout.insertWidget(0, card)
        if evidence["status"] == "placeholder":
            for label in self.materials_detail_scroll.findChildren(QLabel):
                text = label.text()
                if "LOX/GOX 兼容" in text or "✓ 允许" in text:
                    label.setText("氧相容性待核实")
                elif "SF=" in text and "✓" in text:
                    label.setText(text.replace("✓", "（估算，待核实）"))
                elif text == "无报警":
                    label.setText("占位材料数据，不能据此判定工程通过")
                if label.objectName() == "PillOk":
                    label.setObjectName("PillWarn")
                    label.style().unpolish(label)
                    label.style().polish(label)

    def _build_of_sweep_tab(self):
        page = super()._build_of_sweep_tab()
        self._of_sweep_intro = page.layout().itemAt(0).widget()
        self._of_sweep_ffsc_intro = self._of_sweep_intro.text()
        self.of_sweep_preview = QLabel()
        self.of_sweep_preview.setObjectName("MutedLabel")
        self.of_sweep_preview.setWordWrap(True)
        page.layout().insertWidget(2, self.of_sweep_preview)
        self.of_sweep_preview.setToolTip("包含起止两端；最后一段可能小于步长。最多 200 点，超限时请调整范围或步长。")
        self.of_sweep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for edit in (self.of_sweep_start, self.of_sweep_end, self.of_sweep_step):
            edit.textChanged.connect(self._update_scan_preview)
        self._update_scan_preview()
        return page

    def _update_scan_preview(self, *_):
        try:
            points = build_scan_points(self.of_sweep_start.text(), self.of_sweep_end.text(), self.of_sweep_step.text())
            self.of_sweep_preview.setText(f"预计 {len(points)} 个点 · O/F {points[0]:.8g} → {points[-1]:.8g}（含终止值）")
            self.of_sweep_preview.setStyleSheet("")
        except ValueError as exc:
            self.of_sweep_preview.setText(str(exc))
            self.of_sweep_preview.setStyleSheet("color: #a65a0a;")

    def _sync_of_sweep_availability(self):
        if not hasattr(self, "btn_run_of_sweep"):
            return
        ffsc = self._current_cycle_type() == "ffsc"
        panel = getattr(self, "cfd_panel", None)
        enabled = ffsc and not self._busy and not (panel and panel.has_running_task())
        reason = "该页仅支持 FFSC 富氧 / 富燃预燃室扫描；GG 循环请使用发动机特性的节流扫描。"
        self.btn_run_of_sweep.setEnabled(enabled)
        self.btn_run_of_sweep.setToolTip("开始预燃室 O/F 扫描" if ffsc else reason)
        for widget in (self.of_sweep_side, self.of_sweep_start, self.of_sweep_end, self.of_sweep_step, self.btn_of_sweep_apply):
            widget.setEnabled(enabled)
        self._of_sweep_intro.setText(self._of_sweep_ffsc_intro if ffsc else
            "当前为 GG 燃气发生器循环。" + reason + "\n需要预燃室扫描时，请在左侧将循环方式切换为 FFSC。")
        if not ffsc:
            self.of_sweep_hint.setText("GG 循环不适用预燃室扫描，请切换至 FFSC。")
        elif self.last_of_sweep is None:
            self.of_sweep_hint.setText("点击「开始扫描」运行 O/F 扫描。")
        elif self.of_sweep_hint.text().startswith("GG 循环"):
            count = len(self.last_of_sweep.get("records", []))
            self.of_sweep_hint.setText(f"保留上次 FFSC 扫描的 {count} 个点；修改工况后请重新扫描。")

    def _on_run_of_sweep_clicked(self):
        if self._busy or self.cfd_panel.has_running_task() or self._current_cycle_type() != "ffsc":
            self._sync_of_sweep_availability()
            return
        try:
            inputs = self._read_inputs()
            points = build_scan_points(self.of_sweep_start.text(), self.of_sweep_end.text(), self.of_sweep_step.text())
        except ValueError as exc:
            QMessageBox.warning(self, "扫描参数错误", str(exc))
            return
        side = "ox" if self.of_sweep_side.currentIndex() == 0 else "fuel"
        self.last_of_sweep = {"side": side, "records": [], "inputs": asdict(inputs), "complete": False}
        self.of_sweep_table.setRowCount(0)
        self._task_kind = "of_sweep"
        self._set_busy(True, f"开始预燃室扫描 · {len(points)} 个点")
        worker = legacy.OFSweepWorker(inputs, side, points)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_of_sweep_finished)
        worker.signals.error.connect(self._on_worker_error)
        worker.signals.record_ready.connect(self._on_of_sweep_record)
        self._active_worker = worker
        self._connect_active_worker()
        self.thread_pool.start(worker)

    @Slot(object)
    def _on_of_sweep_record(self, record):
        self.last_of_sweep["records"].append(record)
        self._populate_of_sweep_table(self.last_of_sweep["records"])
        self.of_sweep_hint.setText(f"已完成 {len(self.last_of_sweep['records'])} 个点；已完成结果可查看，取消后可导出。")

    def _on_of_sweep_apply(self):
        if self._busy or self.cfd_panel.has_running_task() or self._current_cycle_type() != "ffsc":
            self._sync_of_sweep_availability()
            return
        records = (self.last_of_sweep or {}).get("records", [])
        row = self.of_sweep_table.currentRow()
        if row < 0 or row >= len(records):
            QMessageBox.information(self, "选择扫描点", "请先运行扫描并选中一行已收敛的结果。")
            return
        record = records[row]
        if not record.get("converged") or record.get("error"):
            QMessageBox.warning(self, "该点未收敛", "该点仅供诊断，不能作为可复算设计点应用。")
            return
        values = dict(self.last_of_sweep.get("inputs") or asdict(self._read_inputs()))
        values["use_target_preburner_temp"] = 0.0
        values["use_target_preburner_mr"] = 1.0
        key = "ox_preburner_mr_target" if record.get("side", "ox") == "ox" else "fuel_preburner_mr_target"
        values[key] = float(record["of_target"])
        self._write_input_values(values)
        self._applied_scan_record = {"record": dict(record), "signature": self._signature()}
        self._last_scan_replay_check = None
        self._update_case_state()
        self.statusBar().showMessage("已恢复扫描工况并切换为固定混合比模式；运行设计点后会校验复算一致性。", 12000)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("文件")
        self._case_actions = []
        for text, callback, shortcut in [
            ("打开方案…", self.open_case, "Ctrl+O"),
            ("保存方案", self.save_case, "Ctrl+S"),
            ("方案另存为…", lambda: self.save_case(save_as=True), "Ctrl+Shift+S"),
            ("恢复默认参数", self.reset_defaults, ""),
        ]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            if shortcut:
                action.setShortcut(shortcut)
            file_menu.addAction(action)
            self._case_actions.append(action)
        file_menu.addSeparator()
        close = file_menu.addAction("退出")
        close.triggered.connect(self.close)
        self._run_actions = []
        solve_menu = self.menuBar().addMenu("计算")
        for text, callback in [("运行设计点", self.run_cycle), ("节流扫描", self.run_throttle),
                               ("涡轮设计点扫描", self.run_of_sweep_ox), ("热力学对比", self.run_thermo_compare),
                               ("计算几何", self.compute_geometry_from_last)]:
            action = solve_menu.addAction(text)
            action.triggered.connect(callback)
            self._run_actions.append(action)
        self._run_actions[0].setShortcut("F5")
        for text, callback in [("结果 JSON…", self.export_json), ("CFTurbo 参考…", self.export_cfturbo_md),
                               ("全部状态点 CSV…", self.export_all_states_csv)]:
            action = self._export_menu.addAction(text)
            action.triggered.connect(callback)
        view = self.menuBar().addMenu("视图")
        toggle = view.addAction("显示 / 收起参数面板")
        toggle.setShortcut("Ctrl+B")
        toggle.triggered.connect(self.toggle_parameter_pane)
        size_menu = view.addMenu("界面字号")
        for title in ["小 (12px)", "中 (14px)", "大 (16px)", "特大 (18px)"]:
            action = size_menu.addAction(title)
            action.triggered.connect(lambda checked=False, t=title: self._on_font_size_changed(t))
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("使用指南与运行环境").triggered.connect(self.show_guide)

    def navigate(self, key):
        self.tabs.setCurrentIndex(self._tab_idx[key])
        self._sync_navigation()

    def _sync_navigation(self, *_):
        index = self.tabs.currentIndex()
        for key, title, description, _ in PAGES:
            selected = self._tab_idx[key] == index
            self._nav_buttons[key].setChecked(selected)
            if selected:
                self.page_title.setText(title)
                self.page_subtitle.setText(description)

    def toggle_parameter_pane(self):
        visible = not self.parameter_pane.isVisible()
        self.parameter_pane.setVisible(visible)
        self.toggle_parameters.setText("收起参数" if visible else "显示参数")

    def _apply_style(self):
        self.setStyleSheet(stylesheet(self._font_size, QApplication.instance().font().family()))

    def _show_welcome(self):
        card = QFrame()
        card.setObjectName("ResultCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(25, 25, 25, 25)
        left = QVBoxLayout()
        eyebrow = QLabel("DESIGN POINT  /  初步设计")
        eyebrow.setObjectName("MutedLabel")
        left.addWidget(eyebrow)
        title = QLabel("从一个设计点开始")
        title.setStyleSheet("font-size: 26px; font-weight: 600; background: transparent;")
        left.addWidget(title)
        intro = QLabel("工作台负责循环匹配与初步尺寸。\n详细流场与换热可在 CFD 协同页交给外部求解器。")
        intro.setWordWrap(True)
        intro.setObjectName("MutedLabel")
        left.addWidget(intro)
        left.addSpacing(16)
        for number, heading, text in [
            ("01", "选择工况", "在参数设置中选取循环与参考机型。"),
            ("02", "运行设计点", "检查推力、轴功率平衡和收敛状态。"),
            ("03", "查看分析", "沿左侧工作流程查看各部件结果。"),
        ]:
            step = QLabel(f"<span style='color:#c68a36;'>{number}</span>　<b>{heading}</b><br>"
                          f"<span style='color:#7c8995;'>{text}</span>")
            step.setWordWrap(True)
            left.addWidget(step)
            left.addSpacing(10)
        left.addStretch()
        button = QPushButton("运行当前设计点")
        button.setObjectName("PrimaryButton")
        button.clicked.connect(self.run_cycle)
        left.addWidget(button, alignment=Qt.AlignLeft)
        cfd_button = QPushButton("进入 CFD 协同")
        cfd_button.clicked.connect(lambda: self.navigate("cfd"))
        left.addWidget(cfd_button, alignment=Qt.AlignLeft)
        row.addLayout(left, 1)
        row.addWidget(EngineCanvas(), 1)
        self.overview_layout.insertWidget(0, card)
        hints = self._make_card("开始之前", [
            ("计算范围", "液氧 / 甲烷 · FFSC / GG", ""),
            ("结果用途", "系统匹配、初步尺寸与热结构校核", ""),
            ("设计点要求", "先检查收敛，再检查工程约束", ""),
        ])
        self.overview_layout.insertWidget(1, hints)

    def _make_group_header(self, title, subtitle=""):
        frame = QFrame()
        row = QHBoxLayout(frame)
        row.setContentsMargins(2, 12, 2, 4)
        lab = QLabel(chinese_title(title))
        lab.setObjectName("CardTitle")
        row.addWidget(lab)
        row.addStretch()
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("MutedLabel")
            sub.setWordWrap(True)
            row.addWidget(sub)
        return frame

    def _make_kpi_row(self, results):
        original = super()._make_kpi_row(results)
        cards = []
        while original.layout().count():
            item = original.layout().takeAt(0)
            if item.widget():
                cards.append(item.widget())
        row = ResponsiveKpis(cards)
        original.deleteLater()
        return row

    def _make_status_strip(self, results):
        s = results.summary
        residual = float(s.get("solver_residual_norm", 1.0))
        count = len(results.warnings or [])
        frame = QFrame()
        frame.setObjectName("StatusStrip")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(8)
        assessment = assess_engineering(results)
        for text, level in [
            (assessment["numerical"]["label"], {"pass": "ok", "fail": "error", "unknown": "warning"}[assessment["numerical"]["status"]]),
            (assessment["label"], "ok" if assessment["status"] == "pass" else "warning"),
            ("Cantera" if s.get("thermo_source", 0) else "经验模型", "neutral"),
            (f"残差 {residual:.3g}", "neutral"),
            (f"警告 {count}", "warning" if count else "neutral"),
        ]:
            label = QLabel(text)
            label.setObjectName({"error": "PillError", "warning": "PillWarn", "ok": "PillOk"}.get(level, "PillNeutral"))
            layout.addWidget(label)
        layout.addStretch()
        frame.setToolTip(str(s.get("solve_mode_text", "")))
        return frame

    def _make_card(self, title, rows, status="", badges=None, subtitle="", export_name="", notes="", notes_title=""):
        card = QFrame()
        card.setObjectName("ResultCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        header = QHBoxLayout()
        lab = QLabel(chinese_title(title))
        lab.setObjectName("CardTitle")
        lab.setToolTip(title)
        header.addWidget(lab, 1)
        for text, level in badges or []:
            badge = QLabel(text)
            badge.setObjectName({"ok": "PillOk", "warn": "PillWarn", "error": "PillError"}.get(level, "PillNeutral"))
            header.addWidget(badge)
        if export_name:
            button = QPushButton("导出")
            button.clicked.connect(lambda checked=False: self._export_card_as_png(card, button, export_name))
            header.addWidget(button)
        layout.addLayout(header)
        if status or subtitle:
            info = QLabel(" · ".join(part for part in [status, subtitle] if part))
            info.setObjectName("MutedLabel")
            info.setWordWrap(True)
            layout.addWidget(info)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for i, (key, value, unit) in enumerate(rows):
            key_label = QLabel(chinese_title(key))
            key_label.setObjectName("KVKey")
            key_label.setToolTip(key)
            key_label.setWordWrap(True)
            value_label = QLabel(f"{value} {unit}".strip())
            value_label.setObjectName("KVValue")
            value_label.setWordWrap(True)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(key_label, i, 0)
            grid.addWidget(value_label, i, 1)
        layout.addLayout(grid)
        if notes:
            note_label = QLabel((f"<b>{notes_title}</b><br>" if notes_title else "") + notes)
            note_label.setWordWrap(True)
            note_label.setObjectName("MutedLabel")
            layout.addWidget(note_label)
        return card

    def _populate_overview(self, results):
        super()._populate_overview(results)
        # Preserve all detailed results, present them as expandable sections.
        for i in range(self.overview_layout.count() - 1, 1, -1):
            item = self.overview_layout.itemAt(i)
            card = item.widget()
            if card is None or card.objectName() != "ResultCard":
                continue
            label = card.findChild(QLabel, "CardTitle")
            if label is None:
                continue
            title = label.text()
            if "可行性警告" in title:
                continue  # Engineering warnings are always directly visible.
            self.overview_layout.takeAt(i)
            wrapper = QFrame()
            wrapper.setObjectName("ResultCard")
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            toggle = QToolButton()
            toggle.setObjectName("SectionToggle")
            toggle.setText(title)
            toggle.setCheckable(True)
            toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            toggle.setArrowType(Qt.RightArrow)
            toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            toggle.toggled.connect(lambda checked, c=card, b=toggle: (c.setVisible(checked), b.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)))
            layout.addWidget(toggle)
            layout.addWidget(card)
            card.hide()
            self.overview_layout.insertWidget(i, wrapper)
        assessment = assess_engineering(results)
        card = QFrame()
        card.setObjectName("ResultCard")
        layout = QVBoxLayout(card)
        title = QLabel("工程约束校核 · " + assessment["label"])
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        table = QTableWidget(len(assessment["checks"]), 4)
        table.setObjectName("EngineeringChecks")
        table.setHorizontalHeaderLabels(["校核项", "当前值", "判据", "状态"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().hide()
        labels = {"pass": "通过", "fail": "未通过", "unknown": "待确认", "review": "依据待核实"}
        colors = {"pass": "#15803d", "fail": "#b91c1c", "unknown": "#a65a0a", "review": "#a65a0a"}
        for row, check in enumerate(assessment["checks"]):
            value = "—" if check["value"] is None else f"{check['value']:.5g} {check['unit']}"
            limit = "—" if check["limit"] is None else f"{'≥' if check['criterion'] == 'minimum' else '≤'} {check['limit']:.5g} {check['unit']}"
            for column, text in enumerate((check["label"].replace("₄", "4"), value, limit, labels[check["status"]])):
                item = QTableWidgetItem(text)
                item.setToolTip(check["detail"])
                if column == 3:
                    item.setForeground(QColor(colors[check["status"]]))
                table.setItem(row, column, item)
        table.horizontalHeader().setSectionResizeMode(0, legacy.QHeaderView.Stretch)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for column, width in ((1, 160), (2, 130), (3, 95)):
            table.horizontalHeader().setSectionResizeMode(column, legacy.QHeaderView.Fixed)
            table.setColumnWidth(column, width)
        table.setMinimumHeight(min(440, 38 + 31 * table.rowCount()))
        layout.addWidget(table)
        note = QLabel("本表单独检查模型已有的供给压力、轴功率与温度约束；数值收敛不代表整机工程可行，材料估算仍需核实。")
        note.setWordWrap(True)
        note.setObjectName("MutedLabel")
        layout.addWidget(note)
        self.overview_layout.insertWidget(2, card)

    def _populate_results(self, results):
        super()._populate_results(results)
        numerical = assess_engineering(results)["numerical"]
        self.badge.setText(numerical["label"])
        colors = {"pass": ("#e9f5ef", "#17673f"), "fail": ("#fff0ee", "#a9372b"),
                  "unknown": ("#fff4e1", "#9b641a")}
        background, foreground = colors[numerical["status"]]
        self.badge.setStyleSheet(f"background: {background}; color: {foreground}; padding: 10px; border-radius: 4px;")
        self.badge.setToolTip("这里只表示数值收敛状态；工程可行性见独立校核表。")

    def _clear_layout_keep_stretch(self, layout):
        # Hide immediately: deferred deletion must not leave old cards visible
        # underneath the newly computed results during repaint/export.
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                retire_charts(item.widget())
                item.widget().hide()
                item.widget().deleteLater()
        layout.addStretch(1)

    def _read_inputs(self):
        # Integer fields must not silently truncate 1.9 to 1; reject NaN/Inf.
        for key, edit in self.input_edits.items():
            if not isinstance(edit, QLineEdit) or key in self._STRING_FIELDS:
                continue
            try:
                value = float(edit.text())
            except ValueError:
                continue  # The original parser gives the detailed field error.
            if not math.isfinite(value):
                raise ValueError(f"参数 {key} 必须是有限数字")
            if key in self._INT_FIELDS and not value.is_integer():
                raise ValueError(f"参数 {key} 必须是整数，不能输入 {edit.text()}")
        values = asdict(super()._read_inputs())
        values.update(self._extra_inputs)
        if self._cfd_route() == "external":
            values["cooling_marching_enable"] = 0.0
        return EngineInputs(**values)

    def _write_input_values(self, values):
        # Write a complete, validated input snapshot without rounding O/F values.
        EngineInputs(**values)
        self._loading_case = True
        self._suppress_preset_prompt = True
        try:
            for key, edit in self.input_edits.items():
                value = values[key]
                if isinstance(edit, QComboBox):
                    index = edit.findData(value)
                    if index < 0:
                        raise ValueError(f"参数 {key} 的选项不受支持：{value}")
                    edit.setCurrentIndex(index)
                elif isinstance(value, (int, float)):
                    factor = legacy.DISPLAY_UNIT_MAP.get(key, (1.0, ""))[0]
                    edit.setText(format(value * factor, ".15g"))
                else:
                    edit.setText(str(value))
            self._extra_inputs = {key: value for key, value in values.items() if key not in self.input_edits}
        finally:
            self._loading_case = False
            self._suppress_preset_prompt = False
        self._refresh_group_selector()
        self._queue_case_change()

    def _signature(self):
        visible = tuple((key, edit.currentData() if isinstance(edit, QComboBox) else edit.text())
                        for key, edit in sorted(self.input_edits.items()))
        return visible + tuple(sorted(self._extra_inputs.items())) + (("cfd_route", self._cfd_route()),)

    def _queue_case_change(self, *_):
        if hasattr(self, "_change_timer") and not self._loading_case:
            self._change_timer.start(180)
            self._schedule_case_recovery()

    def _set_banner(self, text, kind="warning"):
        self.result_banner_label.setText(text)
        self.result_banner.setProperty("kind", kind)
        self.result_banner.style().unpolish(self.result_banner)
        self.result_banner.style().polish(self.result_banner)
        self.result_banner_label.style().unpolish(self.result_banner_label)
        self.result_banner_label.style().polish(self.result_banner_label)

    def _update_case_state(self):
        if hasattr(self, "cfd_panel"):
            self.cfd_panel.refresh_source()
        name = self._case_path.stem if self._case_path else "未命名方案"
        changed = self._has_unsaved_changes() if getattr(self, "_case_safety_ready", False) else self._signature() != self._saved_signature
        self.document_label.setText(name + (" · 已修改" if changed else ""))
        if self._busy:
            return
        mode = "" if CANTERA_AVAILABLE else " · 当前使用经验热力学模型"
        if self._cfd_route() == "external":
            mode += " · 外部 CFD 协同：此处为循环初算，详细结果见 CFD 页。"
        if self.last_results is None:
            self._set_banner("尚无计算结果。选择参考工况后运行设计点。" + mode)
            if hasattr(self, "_runtime_report") and not self._runtime_report["ready"]:
                self._set_banner("科学计算环境不完整，结果可能使用经验公式。请运行 setup_windows.bat 修复，或在使用指南查看缺失项。")
        elif self._result_signature != self._signature():
            self._set_banner("参数已修改；下方仍是上一计算工况。请重新运行设计点。" + mode)
        else:
            s = self.last_results.summary
            residual = float(s.get("solver_residual_norm", 1.0))
            if assess_engineering(self.last_results)["numerical"]["status"] != "pass":
                self._set_banner("本次未完全收敛，结果仅用于诊断。请查看日志与警告。" + mode, "error")
            else:
                assessment = assess_engineering(self.last_results)
                warning_count = len(self.last_results.warnings or [])
                if assessment["status"] != "pass":
                    self._set_banner(f"数值收敛；{assessment['label']}。请查看总体性能的独立校核表及材料数据依据。" + mode)
                    return
                if warning_count:
                    self._set_banner(f"数值基本收敛，但有 {warning_count} 项工程警告。请查看可行性警告与日志。" + mode)
                else:
                    self._set_banner("显示最近设计点结果；请继续检查温度、压力与材料约束。" + mode, "ok" if CANTERA_AVAILABLE else "warning")

    def run_cycle(self):
        if self._busy or self.cfd_panel.has_running_task():
            return
        self._apply_cfd_route()
        try:
            self._submitted_input_snapshot = asdict(self._read_inputs())
        except Exception:
            super().run_cycle()  # Preserve the existing detailed input prompt.
            return
        self._submitted_signature = self._signature()
        self._task_kind = "solve"
        super().run_cycle()
        self._connect_active_worker()

    def run_throttle(self):
        if self._busy or self.cfd_panel.has_running_task():
            return
        self._task_kind = "throttle"
        self._partial_throttle = []
        super().run_throttle()
        if self._busy:
            self.last_throttle = []
            self._populate_throttle([])
            self._connect_active_worker()
            self._active_worker.signals.record_ready.connect(self._on_throttle_record)

    def _connect_active_worker(self):
        worker = getattr(self, "_active_worker", None)
        if worker is None or not hasattr(worker, "request_cancel"):
            self.cancel_button.setEnabled(False)
            return
        if not getattr(worker, "_workbench_cancel_connected", False):
            worker.signals.cancelled.connect(self._on_task_cancelled)
            worker._workbench_cancel_connected = True
        self.cancel_button.setText("取消计算")
        self.cancel_button.setEnabled(self._busy)

    def cancel_task(self):
        worker = getattr(self, "_active_worker", None)
        if not self._busy or worker is None or not hasattr(worker, "request_cancel"):
            return
        worker.request_cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("正在停止…")
        self.progress_label.setText("正在停止计算；已完成的扫描结果将保留。")

    @Slot(object)
    def _on_throttle_record(self, record):
        self._partial_throttle.append(record)
        self.last_throttle = list(self._partial_throttle)
        self._populate_throttle(self.last_throttle)

    @Slot(object)
    def _on_thermo_record(self, record):
        self._partial_thermo.append(record)
        self._render_partial_thermo(self._partial_thermo)
        self._update_thermo_charts(self._partial_thermo)

    def _render_partial_thermo(self, records):
        self.thermo_table.setRowCount(0)
        for rec in records:
            ct, cea = rec.get("cantera") or {}, rec.get("cea") or {}
            values = [f"{rec['of']:.4g}", f"{rec['eps']:.4g}"]
            for key, delta in (("Isp_vac_s", "delta_Isp_vac_s_pct"),
                               ("Cstar_m_s", "delta_Cstar_m_s_pct"), ("Tc_K", "delta_Tc_K_pct")):
                for result in (ct, cea):
                    value = result.get(key)
                    values.append(f"{value:.3f}" if isinstance(value, (float, int)) else "—")
                difference = rec.get(delta)
                values.append(f"{difference:+.2f}" if isinstance(difference, (float, int)) else "—")
            row = self.thermo_table.rowCount()
            self.thermo_table.insertRow(row)
            for column, value in enumerate(values):
                self.thermo_table.setItem(row, column, QTableWidgetItem(value))

    @Slot(object)
    def _on_task_cancelled(self, payload):
        kind = payload.get("kind")
        records = payload.get("records", [])
        if kind == "of_sweep":
            self.last_of_sweep = {**payload, "complete": False, "cancelled": True}
            self._populate_of_sweep_table(records)
            self.of_sweep_hint.setText(f"扫描已取消，保留 {len(records)} 个已完成点，可查看或导出。")
        elif kind == "throttle":
            self.last_throttle = records
            self._populate_throttle(records)
        elif kind == "thermo":
            self._render_partial_thermo(records)
            self._thermo_run_btn.setEnabled(True)
            self._thermo_status.setText(f"已取消 · 保留 {len(records)} 个结果")
        elif kind == "preview":
            self.chamber_panel._set_preview_busy(False)
        self._set_busy(False, f"已取消计算 · 保留 {len(records)} 个已完成结果" if kind != "solve" else "设计点计算已取消")
        if kind == "thermo":
            self._thermo_run_btn.setEnabled(True)
        self._task_kind = None
        self._set_log_status("空闲 / Idle", conv="已取消", conv_kind="warn")
        self._append_log("用户取消计算；已完成的扫描点已保留，设计点结果未被未完成计算覆盖。")
        self._update_case_state()

    def _set_busy(self, busy, msg):
        was_busy = self._busy
        if busy and not was_busy:
            # Snapshot BEFORE the legacy handler disables its run buttons.
            self._disabled_buttons = [(button, button.isEnabled()) for button in self.tabs.findChildren(QPushButton)]
        self._busy = busy
        self.cancel_button.setVisible(busy)
        if busy:
            self.cancel_button.setEnabled(False)
            QTimer.singleShot(0, self._connect_active_worker)
        super()._set_busy(busy, msg)
        for button in (self.header_run, self.open_button, self.save_button, self.export_button):
            button.setEnabled(not busy)
        for action in getattr(self, "_case_actions", []) + getattr(self, "_run_actions", []):
            action.setEnabled(not busy)
        if busy:
            for button, _ in self._disabled_buttons:
                if isValid(button):
                    button.setEnabled(False)
        else:
            for button, enabled in self._disabled_buttons:
                if isValid(button):
                    button.setEnabled(enabled)
            self._disabled_buttons = []
        self.parameter_pane.setEnabled(not busy)
        for edit in self.input_edits.values():
            # GG shaft layout is kept disabled in FFSC mode by the original UI.
            edit.setEnabled(not busy and (edit is not self.input_edits.get("gg_layout") or self._current_cycle_type() == "gg"))
        for edit in (self._thermo_of_min, self._thermo_of_max, self._thermo_of_n,
                     self._thermo_eps, self._thermo_pc, self._thermo_T_fuel, self._thermo_T_ox):
            edit.setEnabled(not busy)
        self.progress_label.setText(msg)
        if busy:
            self._set_banner("正在计算，请等待结果。参数已暂时锁定。")
        self._sync_of_sweep_availability()
        self._apply_cfd_route()

    @Slot(object)
    def _on_solve_finished(self, results):
        self.last_geometry = None  # Never mix a new solve with old geometry.
        self._result_signature = self._submitted_signature
        self._solved_input_snapshot = self._submitted_input_snapshot
        results.summary["analysis_route_text"] = "external_cfd" if self._cfd_route() == "external" else "internal"
        super()._on_solve_finished(results)
        self._update_case_state()
        self._last_scan_replay_check = None
        if self._applied_scan_record and self._applied_scan_record["signature"] == self._result_signature:
            self._last_scan_replay_check = verify_scan_replay(self._applied_scan_record["record"], results)
            if self._last_scan_replay_check["consistent"]:
                self.statusBar().showMessage("扫描点复算一致；请继续检查工程约束。", 15000)
            else:
                fields = "、".join(self._last_scan_replay_check["different_fields"])
                self._set_banner(f"扫描点复算存在差异（{fields}），请查看结果与运行记录。", "error")
        self._task_kind = None
        self._apply_cfd_route()

    @Slot(str)
    def _on_worker_error(self, tb):
        super()._on_worker_error(tb)
        self._task_kind = None
        self._set_banner("本次计算失败。" + ("下方保留上一工况结果；请查看日志。" if self.last_results else "请查看日志与输入参数。"), "error")

    @Slot(object)
    def _on_throttle_finished(self, sweep):
        super()._on_throttle_finished(sweep)
        self._task_kind = None
        self._update_case_state()

    @Slot(object)
    def _on_of_sweep_finished(self, payload):
        payload.setdefault("inputs", (self.last_of_sweep or {}).get("inputs"))
        payload["complete"] = True
        super()._on_of_sweep_finished(payload)
        self._task_kind = None
        self._update_case_state()

    def reset_defaults(self):
        if getattr(self, "_case_safety_ready", False) and not self._can_abandon_case("恢复默认参数"):
            return
        self._extra_inputs.clear()
        super().reset_defaults()
        self._refresh_group_selector()

    def _apply_engine_preset(self, preset_key):
        if getattr(self, "_case_safety_ready", False) and not self._can_abandon_case("应用参考工况"):
            return
        from engine_presets import preset_input_values
        values = preset_input_values(preset_key)
        self._extra_inputs = {key: value for key, value in values.items() if key not in self.input_edits}
        super()._apply_engine_preset(preset_key)

    def run_thermo_compare(self):
        if self._busy or self.cfd_panel.has_running_task():
            return
        try:
            validate_thermo_scan(self._thermo_of_min.text(), self._thermo_of_max.text(),
                self._thermo_of_n.text(), self._thermo_eps.text(), self._thermo_pc.text(),
                self._thermo_T_fuel.text(), self._thermo_T_ox.text())
        except ValueError as exc:
            QMessageBox.warning(self, "热力学扫描参数错误", str(exc))
            return
        self._task_kind = "thermo"
        self._partial_thermo = []
        self.thermo_table.setRowCount(0)
        self._init_thermo_axes()
        super().run_thermo_compare()
        if not self._thermo_run_btn.isEnabled():
            self._set_busy(True, "热力学对比中")
            self.progress.setRange(0, len(self._active_worker.of_list) * len(self._active_worker.eps_list))
            self._connect_active_worker()
            self._active_worker.signals.record_ready.connect(self._on_thermo_record)

    @Slot(list)
    def _on_thermo_finished(self, records):
        self._set_busy(False, "热力学对比完成")
        super()._on_thermo_finished(records)
        self._task_kind = None
        self._update_case_state()

    @Slot(str)
    def _on_thermo_error(self, tb):
        self._set_busy(False, "热力学对比失败")
        super()._on_thermo_error(tb)
        self._task_kind = None
        self._update_case_state()

    def case_document(self):
        return {"format": "ffsc.workbench.case", "schema_version": 1, "app_version": APP_VERSION,
                "saved_at": datetime.now().astimezone().isoformat(), "inputs": asdict(self._read_inputs()),
                "analysis": self._case_analysis_inputs(), "environment": self._runtime_report,
                "cfd_feedback_history": self._cfd_feedback_history}

    def save_case(self, checked=False, save_as=False):
        if self._busy:
            return False
        try:
            document = self.case_document()
            path = self._case_path
            if path is None or save_as:
                selected, _ = QFileDialog.getSaveFileName(self, "保存计算方案", str(path or Path.home() / "lre_case.json"), "工作台方案 (*.json)")
                if not selected:
                    return False
                path = Path(selected)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            temporary.replace(path)
            self._case_path = path
            self._saved_signature = self._signature()
            self._remember_saved_case()
            self._update_case_state()
            self.statusBar().showMessage(f"方案已保存：{path.name}", 5000)
            return True
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False

    def load_case_path(self, path):
        if self.cfd_panel.has_running_task():
            raise ValueError("外部 CFD 任务进行中，请先停止任务再替换方案。")
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        history = self._cfd_history_from_document(document)
        if document.get("format") == "ffsc.workbench.case" and document.get("schema_version") != 1:
            raise ValueError("不支持的方案格式版本")
        if "inputs" not in document or not isinstance(document["inputs"], dict):
            raise ValueError("文件中没有有效的 inputs 参数。")
        known = {field.name for field in fields(EngineInputs)}
        unknown = set(document["inputs"]) - known
        if unknown:
            raise ValueError("未知参数：" + ", ".join(sorted(unknown)))
        values = asdict(EngineInputs())
        values.update(document["inputs"])
        if "nozzle_contour_method" not in document["inputs"]:
            values["nozzle_contour_method"] = "legacy_log_v1"
        if document.get("format") == "ffsc.workbench.case":
            from thermo_data import saved_input_identity
            values.update(saved_input_identity(document["inputs"]))
        # Reject non-finite values even for fields not currently shown in the UI.
        for key, value in values.items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError(f"参数 {key} 必须是有限数字")
        EngineInputs(**values)
        if "analysis" in document:
            self._analysis_restore_operations(document["analysis"])
        # Validate choices and integer inputs before making any UI changes.
        for key, edit in self.input_edits.items():
            value = values[key]
            if isinstance(edit, QComboBox) and edit.findData(value) < 0:
                raise ValueError(f"参数 {key} 的选项不受支持：{value}")
            if key in self._INT_FIELDS and (not isinstance(value, (int, float)) or not float(value).is_integer()):
                raise ValueError(f"参数 {key} 必须是整数")
        self._loading_case = True
        self._suppress_preset_prompt = True
        try:
            for key, edit in self.input_edits.items():
                value = values[key]
                if isinstance(edit, QComboBox):
                    edit.setCurrentIndex(edit.findData(value))
                else:
                    if isinstance(value, (int, float)):
                        factor = legacy.DISPLAY_UNIT_MAP.get(key, (1.0, ""))[0]
                        edit.setText(format(value * factor, ".15g"))
                    else:
                        edit.setText(str(value))
        finally:
            self._loading_case = False
            self._suppress_preset_prompt = False
        self._extra_inputs = {key: value for key, value in values.items() if key not in self.input_edits}
        self._case_path = Path(path)
        self._cfd_feedback_history = history
        self._saved_signature = self._signature()
        if "analysis" in document:
            self._restore_case_analysis(document["analysis"])
        self._remember_saved_case()
        self._refresh_group_selector()
        self._refresh_preset_combo()
        self._update_case_state()

    def open_case(self):
        if self._busy or self.cfd_panel.has_running_task():
            return
        if not self._can_abandon_case("打开另一方案"):
            return
        selected, _ = QFileDialog.getOpenFileName(self, "打开方案或旧版结果 JSON", str(Path.home()), "JSON (*.json)")
        if selected:
            try:
                self.load_case_path(selected)
                self.statusBar().showMessage("方案已加载。点击运行设计点重新计算。", 5000)
            except Exception as exc:
                QMessageBox.warning(self, "打开失败", str(exc))

    def export_json(self):
        if self.last_results is None:
            return super().export_json()
        selected, _ = QFileDialog.getSaveFileName(self, "导出计算结果", str(Path.home() / "lre_results.json"), "JSON (*.json)")
        if not selected:
            return
        document = {
            "format": "ffsc.workbench.results", "schema_version": 1, "app_version": APP_VERSION,
            "exported_at": datetime.now().astimezone().isoformat(),
            "input_modified_since_solve": self._result_signature != self._signature(),
            "models": {"cantera": CANTERA_AVAILABLE, "coolprop": COOLPROP_AVAILABLE, "rocketcea": ROCKETCEA_AVAILABLE},
            "environment": self._runtime_report,
            "engineering_assessment": assess_engineering(self.last_results),
            "scan_replay_check": self._last_scan_replay_check,
            "cfd_feedback_history": self._cfd_feedback_history,
            "inputs": asdict(self.last_results.inputs), "summary": self.last_results.summary,
            "states": {k: asdict(v) for k, v in self.last_results.states.items()},
            "components": {k: asdict(v) for k, v in self.last_results.components.items()},
            "warnings": self.last_results.warnings, "geometry": self.last_geometry,
        }
        try:
            Path(selected).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            self._append_log(f"已导出结果：{selected}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def show_guide(self):
        import importlib.util
        dialog = QDialog(self)
        dialog.setWindowTitle("使用指南与运行环境")
        dialog.resize(680, 680)
        layout = QVBoxLayout(dialog)
        text = QLabel(
            f"<h2>{APP_NAME}</h2>"
            f"<p>{APP_FORMAL_NAME}<br>{APP_ENGLISH_NAME}（{APP_ENGLISH_FULL_NAME}）</p>"
            "<p>1. 参数设置中选择循环与参考工况，点击应用参考工况。</p>"
            "<p>2. 按 F5 或点击运行设计点；查看收敛状态和警告。</p>"
            "<p>3. 沿左侧导航查看几何、推力室、材料与参数分析。</p>"
            "<p>4. CFD 协同页可选择外部路线：初算 → 导出任务包 → 外部建模与求解 → 导入结果。"
            "真实冷却结果可采用压降与热负荷并复算。</p>"
            "<p>本版提供通用文件接口；具体 CFD 软件由外部适配器处理，参考型面不是三维网格。"
            "接口测试数据不能用于设计反馈。</p>"
            "<p>Ctrl+S 保存输入方案；Ctrl+O 恢复方案；Ctrl+B 收起参数。</p>"
            "<p>几何图为初步方案，未收敛结果仅用于诊断。</p>"
            f"<hr><p>版本 {APP_VERSION_LABEL}</p>"
            f"<p>Cantera：{'可用' if CANTERA_AVAILABLE else '未安装，使用经验模型'}<br>"
            f"CoolProp：{'可用' if COOLPROP_AVAILABLE else '未安装，使用备用物性修正'}<br>"
            f"二维 FEA：{'可用' if importlib.util.find_spec('skfem') else '未安装科学计算扩展'}<br>"
            f"RocketCEA：{'可用' if ROCKETCEA_AVAILABLE else '未安装，CEA 交叉对比不可用'}</p>"
            "<p>安装与操作见根目录 README.md；协议见 docs/CFD_INTERFACE.md。</p>"
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        button = QPushButton("关闭")
        button.clicked.connect(dialog.accept)
        layout.addWidget(button, alignment=Qt.AlignRight)
        dialog.exec()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_taskbar_icon()

    def _queue_taskbar_icon_refresh(self, *_):
        self._taskbar_icon_timer.start(0)

    def _refresh_taskbar_icon(self):
        if sys.platform != "win32" or QApplication.platformName() != "windows" or not self.isVisible():
            return
        hwnd = int(self.winId())
        try:
            configure_window_identity(hwnd, ROOT)
            self._taskbar_hwnd = hwnd
            # A fresh QIcon prompts Qt to update native icons for the new DPI.
            self.setWindowIcon(QIcon(str(ROOT / "assets" / "app.ico")))
            if not self._taskbar_screen_connected:
                self.windowHandle().screenChanged.connect(self._queue_taskbar_icon_refresh)
                self._taskbar_screen_connected = True
        except OSError:
            logging.exception("Could not apply Windows taskbar identity")

    def closeEvent(self, event):
        if self.cfd_panel.has_running_task():
            QMessageBox.information(self, "CFD 任务进行中", "请先在 CFD 协同页取消任务，待外部进程停止后再关闭窗口。")
            event.ignore()
            return
        if self._busy:
            QMessageBox.information(self, "计算进行中", "请先点击“取消计算”，待任务停止后再关闭窗口，以保留已完成的结果。")
            event.ignore()
            return
        if not self._can_abandon_case("关闭工作台"):
            event.ignore()
            return
        super().closeEvent(event)
        if event.isAccepted():
            self._finish_case_close()
            self._taskbar_icon_timer.stop()
            if self._taskbar_hwnd is not None:
                try:
                    clear_window_identity(self._taskbar_hwnd)
                except OSError:
                    logging.exception("Could not release Windows taskbar properties")
                self._taskbar_hwnd = None
