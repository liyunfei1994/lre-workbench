"""
ui_chamber_design.py — 顶层「推力室设计」工作区（V19，阶段1+2）
================================================================================

部件级详细设计工作区，子页：
  0 边界条件快照   —— 从最近循环解冻结的推力室边界 + 过期检测（双层握手上半环）
  1 型面与分区     —— 收缩比/L*/β/bell 等型面参数 + 分区着色轮廓预览
  2 冷却通道设计   —— 单段基准参数 + 沿程分布断点表 + 展开视图 + 穿壁截面 + 可行性
  3 沿程热分析     —— 原「再生冷却」页内容 + 膜冷却/辐射控制 + 站位游标 + 站位明细
  4 热结构与FEA    —— 原「热结构云图」页内容 + 结构参数/材料 + 游标站位 2D FEM
  5 校核汇总       —— 区域 × 判据 裕度记分卡

数据流关键设计：
  · 本面板的所有输入控件**注册进 MainWindow.input_edits**（单一数据源），
    _read_inputs / reset_defaults / 导出 JSON 全部无感兼容；
  · 通道分布表经隐藏 QLineEdit（cooling_channel_table_json）序列化，随输入走；
  · 冷却沿程只读取本次循环保存的 cooling_channel.profile。
    修改通道、换热或损失参数后，调用完整循环重算；不能只回写压降。
"""

from __future__ import annotations

import math
from html import escape
import sys
import traceback
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, Slot
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
    QDialog, QApplication,
)

from core import EngineInputs
from ui_metadata import CHAMBER_INPUTS
import chamber_design as cd

try:
    from qt_chart_canvas import SafeFigureCanvasQTAgg as FigureCanvasQTAgg
    from matplotlib.figure import Figure
    MPL = True
except ImportError:
    MPL = False

# 分区着色（型面预览 / 展开视图共用）
ZONE_COLORS = {
    "cylinder":   "#dbeafe",
    "convergent": "#fde68a",
    "throat":     "#fecaca",
    "divergent":  "#bbf7d0",
    "extension":  "#e5e7eb",
}

_STATUS_STYLE = {
    "ok":   ("✓", "#16a34a", "#f0fdf4"),
    "warn": ("~", "#d97706", "#fffbeb"),
    "fail": ("✗", "#dc2626", "#fef2f2"),
    "na":   ("—", "#6b7280", "#f8fafc"),
}


class _Signals(QObject):
    finished = Signal(object)
    cancelled = Signal(object)
    error = Signal(str)


class _PreviewWorker(QRunnable):
    """历史任务接口：只读保存的冷却剖面，不再独立求解冷却。"""

    def __init__(self, base_results, inputs):
        super().__init__()
        self.base_results = base_results
        self.inputs = inputs
        self.signals = _Signals()
        from task_control import CancellationToken
        self.cancel_token = CancellationToken()

    def request_cancel(self):
        self.cancel_token.cancel()

    @Slot()
    def run(self):
        from task_control import TaskCancelled, cancellation_scope, checkpoint
        try:
            with cancellation_scope(self.cancel_token):
                result = cd.marching_preview(self.base_results, self.inputs)
                checkpoint()
                self.signals.finished.emit(result)
        except TaskCancelled:
            self.signals.cancelled.emit({"kind": "preview", "records": []})
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class ChamberDesignPanel(QWidget):
    """顶层「推力室设计」工作区面板。"""

    def __init__(self, main):
        super().__init__()
        self.main = main                      # MainWindow 引用（单一数据源）
        self._defaults = EngineInputs()
        self._wf_current = None               # 当前循环保存的整程 WallField
        self._preview_mode = False
        self._last_preview = None
        self._cursor_idx: Optional[int] = None
        self._cursor_lines: List[Any] = []
        self._cursor_cid = None
        self._table_loading = False
        self._build_ui()

    # ────────────────────────────────────────────────────────────
    # 基础设施
    # ────────────────────────────────────────────────────────────

    def _G(self):
        """Shared input helpers; independent of the concrete workspace class."""
        import gui_pyside6
        return gui_pyside6

    def _register_edit(self, form: QFormLayout, key: str, label: str, unit: str = "",
                       tooltip: str = "") -> QLineEdit:
        """创建数值输入框并注册进 main.input_edits（与左面板同一套显示单位换算）。"""
        G = self._G()
        si_val = getattr(self._defaults, key)
        init_text = G._to_display(key, si_val) if isinstance(si_val, float) else str(si_val)
        edit = QLineEdit(init_text)
        edit.setAlignment(Qt.AlignRight)
        edit.setObjectName("ParamEdit")
        tip = f"{key} [{unit}]\n内部 SI 值：{si_val}"
        if key in G.DISPLAY_UNIT_MAP:
            factor, dunit = G.DISPLAY_UNIT_MAP[key]
            tip += f"（输入为 {dunit}，程序自动换算为 SI）"
        if tooltip:
            tip += "\n" + tooltip
        edit.setToolTip(tip)
        self.main.input_edits[key] = edit
        form.addRow(f"{label} [{unit}]" if unit else label, edit)
        return edit

    def _register_material_combo(self, form: QFormLayout, key: str, label: str) -> QComboBox:
        """材料下拉（按部位筛选），注册进 main.input_edits。"""
        G = self._G()
        combo = QComboBox()
        combo.setObjectName("ParamCombo")
        default_key = getattr(self._defaults, key)
        cur = 0
        for i, m in enumerate(G._material_choices_for(key)):
            combo.addItem(m.display_name, userData=m.key)
            combo.setItemData(i, G._material_tooltip(m), Qt.ToolTipRole)
            if m.key == default_key:
                cur = i
        combo.setCurrentIndex(cur)
        combo.setToolTip(f"参数 {key}\n驱动：再生冷却 k(T)、热结构 σy(T)/SF、2D FEM、材料库卡片")
        self.main.input_edits[key] = combo
        form.addRow(label, combo)
        return combo

    def _mat_key(self, key: str, default: str) -> str:
        w = self.main.input_edits.get(key)
        if w is not None and hasattr(w, "currentData"):
            v = w.currentData()
            if isinstance(v, str) and v:
                return v
        return default

    def _read_inputs_safe(self) -> Optional[EngineInputs]:
        try:
            return self.main._read_inputs()
        except Exception:
            return None

    def _get_geom(self) -> Optional[Dict[str, Any]]:
        """当前几何 dict：优先 main.last_geometry，缺则从 last_results 现算。"""
        geom = getattr(self.main, "last_geometry", None)
        if geom:
            return geom
        res = getattr(self.main, "last_results", None)
        if res is None:
            return None
        try:
            from geometry import compute_geometry, geometry_to_dict
            inp = res.inputs
            return geometry_to_dict(compute_geometry(
                res,
                contraction_ratio=getattr(inp, "chamber_contraction_ratio", 2.5),
                l_star=getattr(inp, "chamber_l_star_m", 1.0)))
        except Exception:
            return None

    @staticmethod
    def _card(title: str, body_html: str) -> QLabel:
        lab = QLabel(f"<div style='font-size:12.5px;font-weight:bold;color:#0f172a;"
                     f"margin-bottom:4px;'>{title}</div>"
                     f"<div style='font-size:12px;color:#374151;'>{body_html}</div>")
        lab.setObjectName("ResultCard")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        lab.setStyleSheet("QLabel#ResultCard{background:#ffffff;border:1px solid #e5e7eb;"
                          "border-radius:8px;padding:10px 12px;}")
        return lab

    # ────────────────────────────────────────────────────────────
    # UI 构建
    # ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        intro = QLabel(
            "推力室工作区：查看当前循环保存的型面、通道、沿程热负荷和结构载荷估计。"
            "修改冷却几何、换热或损失参数后，必须重新求解完整循环。结构估计不代表结构验证。"
            "本页所有输入与左侧面板同源，「运行设计点」读取的就是这里的值。")
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.sub_tabs = QTabWidget()
        self.sub_tabs.addTab(self._build_snapshot_tab(), "0 边界快照")
        self.sub_tabs.addTab(self._build_contour_tab(),  "1 型面与分区")
        self.sub_tabs.addTab(self._build_channels_tab(), "2 冷却通道设计")
        self.sub_tabs.addTab(self._build_marching_tab(), "3 沿程热分析")
        self.sub_tabs.addTab(self._build_thermstr_tab(), "4 热结构与FEA")
        self.sub_tabs.addTab(self._build_scorecard_tab(), "5 校核汇总")
        root.addWidget(self.sub_tabs, 1)

        # 隐藏字段：通道分布表 JSON（单一数据源；表格 UI 与它双向同步）
        self._table_json_edit = QLineEdit("")
        self._table_json_edit.setVisible(False)
        self._table_json_edit.textChanged.connect(self._on_table_json_changed)
        self.main.input_edits["cooling_channel_table_json"] = self._table_json_edit

    # ---- 0 边界快照 ------------------------------------------------

    def _build_snapshot_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        self.snap_banner = QLabel("尚无循环解——点「运行设计点」生成边界快照。")
        self.snap_banner.setObjectName("MutedLabel")
        self.snap_banner.setWordWrap(True)
        btn = QPushButton("▶ 运行设计点（刷新快照）")
        btn.setObjectName("PrimaryButton")
        btn.clicked.connect(self.main.run_cycle)
        row.addWidget(self.snap_banner, 1)
        row.addWidget(btn)
        lay.addLayout(row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.snap_cards = QVBoxLayout(inner)
        self.snap_cards.setSpacing(8)
        self.snap_cards.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        return w

    def _update_snapshot_tab(self):
        # 清卡片（保留末尾 stretch）
        while self.snap_cards.count() > 1:
            item = self.snap_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        res = getattr(self.main, "last_results", None)
        snap = cd.snapshot_from_results(res)
        if snap is None:
            self.snap_banner.setText("尚无循环解——点「运行设计点」生成边界快照。")
            return
        # 过期检测
        stale: List[str] = []
        cur = self._read_inputs_safe()
        if cur is not None:
            stale = cd.snapshot_stale_fields(snap, cur)
        if stale:
            self.snap_banner.setText(
                f"⚠ 快照已过期：{len(stale)} 个输入已修改（{', '.join(stale[:4])}"
                + ("…" if len(stale) > 4 else "") + "）——重跑设计点后边界才会更新。")
            self.snap_banner.setStyleSheet("color:#b45309;font-weight:bold;")
        else:
            self.snap_banner.setText(
                f"✓ 快照与当前输入一致 · 求解于 {snap.solved_at}"
                + ("" if snap.converged else " · ⚠ 该解未完全收敛"))
            self.snap_banner.setStyleSheet("color:#15803d;")

        def fmt_p(pa): return f"{pa/1e6:.2f} MPa"
        cards = [
            ("主燃烧室（燃气侧边界）",
             f"pc = {fmt_p(snap.pc_Pa)} ｜ T_c = {snap.T_c_K:.0f} K ｜ γ = {snap.gamma:.3f} ｜ "
             f"MW = {snap.molar_mass:.2f} kg/kmol<br>"
             f"ṁ_total = {snap.mdot_total:.1f} kg/s（LOX {snap.mdot_ox:.1f} + CH4 {snap.mdot_fuel:.1f}）｜ "
             f"O/F = {snap.of_ratio:.2f} ｜ 推力 {snap.thrust_N/1e3:.0f} kN ｜ Isp {snap.isp_s:.1f} s"),
            ("冷却剂入口（= CH4 主泵出口，全部燃料流经再生冷却套）",
             f"T_in = {snap.coolant_in_T_K:.1f} K ｜ p_in = {fmt_p(snap.coolant_in_p_Pa)} ｜ "
             f"ṁ = {snap.mdot_fuel:.1f} kg/s"),
            ("冷却剂出口（当前循环采用的状态）",
             f"T_out = {snap.coolant_out_T_K:.1f} K ｜ p_out = {fmt_p(snap.coolant_out_p_Pa)}"),
            ("主喷注器入口（两股涡轮排气，FFSC 气-气）",
             f"富氧：T = {snap.inj_ox_T_K:.0f} K ｜ p = {fmt_p(snap.inj_ox_p_Pa)} ｜ "
             f"ṁ = {snap.inj_ox_mdot:.1f} kg/s<br>"
             f"富燃：T = {snap.inj_fuel_T_K:.0f} K ｜ p = {fmt_p(snap.inj_fuel_p_Pa)} ｜ "
             f"ṁ = {snap.inj_fuel_mdot:.1f} kg/s（兼膜冷却气源 T_film={snap.film_T_K:.0f} K）"),
            ("几何基准（由循环热力学反推）",
             f"r_t = {snap.r_t_m*1e3:.1f} mm ｜ r_c = {snap.r_c_m*1e3:.1f} mm ｜ "
             f"L_cyl = {snap.L_cyl_m*1e3:.0f} mm ｜ L_conv = {snap.L_conv_m*1e3:.0f} mm ｜ "
             f"ε = {snap.expansion_ratio:.1f}"),
        ]
        insert_at = 0
        for title, body in cards:
            self.snap_cards.insertWidget(insert_at, self._card(title, body))
            insert_at += 1

    # ---- 1 型面与分区 ----------------------------------------------

    def _build_contour_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        left = QVBoxLayout()
        box = QGroupBox("型面参数（改后重跑设计点生效）")
        form = QFormLayout(box)
        self._register_edit(form, "expansion_ratio", *CHAMBER_INPUTS['expansion_ratio'])
        self._register_edit(form, "chamber_contraction_ratio", *CHAMBER_INPUTS['chamber_contraction_ratio'])
        self._register_edit(form, "chamber_l_star_m", *CHAMBER_INPUTS['chamber_l_star_m'])
        self._register_edit(form, "chamber_conv_half_angle_deg", *CHAMBER_INPUTS['chamber_conv_half_angle_deg'])
        self._register_edit(form, "nozzle_bell_fraction", *CHAMBER_INPUTS['nozzle_bell_fraction'])
        self._register_edit(form, "cooling_cover_eps", *CHAMBER_INPUTS['cooling_cover_eps'])
        left.addWidget(box)

        btn = QPushButton("⟳ 重跑设计点（应用型面）")
        btn.setObjectName("PrimaryButton")
        btn.clicked.connect(self.main.run_cycle)
        left.addWidget(btn)

        self.contour_info = self._card("分区尺寸", "运行设计点后显示。")
        left.addWidget(self.contour_info)
        left.addStretch(1)

        right = QVBoxLayout()
        if MPL:
            self.contour_fig = Figure(figsize=(7.5, 5.2), facecolor="#FAFAFA")
            self.contour_canvas = FigureCanvasQTAgg(self.contour_fig)
            self.contour_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            right.addWidget(self.contour_canvas, 1)
        else:
            right.addWidget(QLabel("⚠ matplotlib 未安装"))

        lw = QWidget(); lw.setLayout(left); lw.setMaximumWidth(430)
        rw = QWidget(); rw.setLayout(right)
        lay.addWidget(lw)
        lay.addWidget(rw, 1)
        return w

    def _update_contour_tab(self):
        if not MPL:
            return
        geom = self._get_geom()
        fig = self.contour_fig
        fig.clear()
        ax = fig.add_subplot(111)
        if not geom:
            ax.axis("off")
            ax.text(0.5, 0.5, "运行设计点后生成分区轮廓", ha="center", va="center",
                    transform=ax.transAxes, color="#9CA3AF", style="italic")
            self.contour_canvas.draw_idle()
            return
        try:
            g_nz = geom.get("nozzle") or {}
            g_ch = geom.get("chamber") or {}
            cx = list(g_nz.get("contour_x") or [])
            cr = list(g_nz.get("contour_r") or [])
            if len(cx) < 8:
                raise ValueError("轮廓数据不足")
            L_cyl = float(g_ch.get("cylindrical_section_length_m", 0.0) or 0.0)
            L_conv = float(g_nz.get("convergent_length_m", 0.0) or 0.0)
            rc = float(g_ch.get("radius_m", max(cr)))
            rt = float(g_nz.get("throat_radius_m", min(cr)))
            # 喷注面坐标系：x=0 喷注面（nozzle 轮廓 x=0 在喉部）
            xs = [0.0, L_cyl] + [x + L_cyl + L_conv for x in cx]
            rs = [rc, rc] + list(cr)
            xs_mm = [x * 1e3 for x in xs]
            rs_mm = [r * 1e3 for r in rs]
            x_t = (L_cyl + L_conv) * 1e3
            inp = self._read_inputs_safe()
            cover_eps = float(getattr(inp, "cooling_cover_eps", 0.0) or 0.0) if inp else 0.0
            eps_full = float(getattr(inp, "expansion_ratio", 0.0) or 0.0) if inp else 0.0
            # 覆盖终点 x（按面积比找轮廓站）
            x_cov = xs_mm[-1]
            if cover_eps and 1.0 < cover_eps < (eps_full or 1e9):
                r_cov = rt * math.sqrt(cover_eps) * 1e3
                for xi, ri in zip(xs_mm, rs_mm):
                    if xi > x_t and ri >= r_cov:
                        x_cov = xi
                        break
            zones = [
                ("cylinder",   0.0,                 L_cyl * 1e3),
                ("convergent", L_cyl * 1e3,         x_t - rt * 1e3),
                ("throat",     x_t - rt * 1e3,      x_t + rt * 1e3),
                ("divergent",  x_t + rt * 1e3,      x_cov),
                ("extension",  x_cov,               xs_mm[-1]),
            ]
            r_max = max(rs_mm)
            for zkey, x0, x1 in zones:
                if x1 - x0 < 1e-6:
                    continue
                ax.axvspan(x0, x1, color=ZONE_COLORS[zkey], alpha=0.55, zorder=0)
                ax.text(0.5 * (x0 + x1), r_max * 1.12, cd.ZONE_LABELS[zkey],
                        ha="center", va="bottom", fontsize=8.5, color="#334155")
            ax.plot(xs_mm, rs_mm, color="#0f172a", lw=1.8, zorder=3)
            ax.plot(xs_mm, [-v for v in rs_mm], color="#0f172a", lw=1.8, zorder=3)
            ax.fill_between(xs_mm, rs_mm, [-v for v in rs_mm], color="#f8fafc", zorder=1)
            ax.axvline(x_t, color="#7c2d12", ls=":", lw=1.0)
            ax.set_xlabel("x [mm]（喷注面 → 喷管出口）", fontsize=9)
            ax.set_ylabel("r [mm]", fontsize=9)
            ax.set_aspect("equal")
            ax.set_ylim(-r_max * 1.05, r_max * 1.30)
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            ax.set_title("推力室轴向分区 · 燃气侧内壁轮廓（全链唯一规范来源）",
                         fontsize=10.5, fontweight="bold", loc="left")
            fig.tight_layout()
            self.contour_canvas.draw_idle()
            # 分区尺寸卡
            De = float(g_nz.get("exit_diameter_m", 0.0) or 0.0)
            info = (f"D_c = {2*rc*1e3:.0f} mm ｜ D_t = {2*rt*1e3:.0f} mm ｜ "
                    f"D_e = {De*1e3:.0f} mm<br>"
                    f"L_cyl = {L_cyl*1e3:.0f} mm ｜ L_conv = {L_conv*1e3:.0f} mm ｜ "
                    f"L_nozzle = {float(g_nz.get('nozzle_length_m',0) or 0)*1e3:.0f} mm ｜ "
                    f"喉部 x_t = {x_t:.0f} mm<br>"
                    f"θn = {float(g_nz.get('initial_expansion_angle_deg',0) or 0):.1f}° ｜ "
                    f"θe = {float(g_nz.get('exit_angle_deg',0) or 0):.1f}° ｜ "
                    f"Ru = {float(g_nz.get('throat_upstream_radius_m',0) or 0)*1e3:.0f} mm ｜ "
                    f"Rd = {float(g_nz.get('throat_downstream_radius_m',0) or 0)*1e3:.0f} mm<br>"
                    f"壁分层：内衬 {float(g_ch.get('inner_wall_thickness_mm',0) or 0):.2f} + "
                    f"通道 {float(g_ch.get('cooling_channel_height_mm',0) or 0):.1f} + "
                    f"外壳 {float(g_ch.get('outer_wall_thickness_mm',0) or 0):.1f} mm")
            self.contour_info.setText(
                "<div style='font-size:12.5px;font-weight:bold;color:#0f172a;"
                "margin-bottom:4px;'>分区尺寸</div>"
                f"<div style='font-size:12px;color:#374151;'>{info}</div>")
        except Exception as ex:
            ax.axis("off")
            ax.text(0.5, 0.5, f"轮廓绘制失败：{ex}", ha="center", va="center",
                    transform=ax.transAxes, color="#dc2626", fontsize=9)
            self.contour_canvas.draw_idle()

    # ---- 2 冷却通道设计 --------------------------------------------

    _TABLE_COLS = ("锚点", "x [mm]", "通道数 N", "宽 w [mm]", "深 h [mm]", "内衬 t [mm]")

    def _build_channels_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        top = QHBoxLayout()
        box = QGroupBox("单段基准参数（分布表为空时生效；auto_size 会反算通道深）")
        form = QFormLayout(box)
        self._register_edit(form, "cooling_channel_auto_size", *CHAMBER_INPUTS['cooling_channel_auto_size'])
        self._register_edit(form, "cooling_target_velocity_ms", *CHAMBER_INPUTS['cooling_target_velocity_ms'])
        self._register_edit(form, "cooling_n_channels", *CHAMBER_INPUTS['cooling_n_channels'])
        self._register_edit(form, "cooling_channel_width_m", *CHAMBER_INPUTS['cooling_channel_width_m'])
        self._register_edit(form, "cooling_channel_height_m", *CHAMBER_INPUTS['cooling_channel_height_m'])
        self._register_edit(form, "cooling_wall_thickness_m", *CHAMBER_INPUTS['cooling_wall_thickness_m'])
        top.addWidget(box, 1)

        box2 = QGroupBox("通道流动/校核参数")
        form2 = QFormLayout(box2)
        self._register_edit(form2, "cooling_htc_enhancement", *CHAMBER_INPUTS['cooling_htc_enhancement'])
        self._register_edit(form2, "cooling_channel_rel_roughness", *CHAMBER_INPUTS['cooling_channel_rel_roughness'])
        self._register_edit(form2, "cooling_channel_k_minor", *CHAMBER_INPUTS['cooling_channel_k_minor'])
        self._register_edit(form2, "cooling_wall_temp_limit_K", *CHAMBER_INPUTS['cooling_wall_temp_limit_K'])
        top.addWidget(box2, 1)
        lay.addLayout(top)

        # 分布断点表
        tbl_box = QGroupBox("沿程分布断点表（N 分段常数=断点分叉；w/h/t 分段线性；空表=单段模式）")
        tbl_lay = QVBoxLayout(tbl_box)
        self.chan_table = QTableWidget(0, len(self._TABLE_COLS))
        self.chan_table.setHorizontalHeaderLabels(list(self._TABLE_COLS))
        self.chan_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.chan_table.verticalHeader().setVisible(False)
        self.chan_table.setMaximumHeight(190)
        self.chan_table.itemChanged.connect(self._on_table_edited)
        tbl_lay.addWidget(self.chan_table)

        btn_row = QHBoxLayout()
        for text, slot in [
            ("＋ 添加断点行", self._table_add_row),
            ("－ 删除选中行", self._table_del_row),
            ("从已解剖面生成节点表", self._table_from_scalars),
            ("清空（回退单段模式）", self._table_clear),
        ]:
            b = QPushButton(text)
            b.setObjectName("SecondaryButton")
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        tbl_lay.addLayout(btn_row)

        self.chan_feas = QLabel("（表为空：单段模式，与 V15 行为一致）")
        self.chan_feas.setObjectName("MutedLabel")
        self.chan_feas.setWordWrap(True)
        self.chan_feas.setTextFormat(Qt.RichText)
        tbl_lay.addWidget(self.chan_feas)
        lay.addWidget(tbl_box)

        # 展开视图 + 穿壁截面
        if MPL:
            figs = QHBoxLayout()
            self.unroll_fig = Figure(figsize=(6.0, 3.4), facecolor="#FAFAFA")
            self.unroll_canvas = FigureCanvasQTAgg(self.unroll_fig)
            self.section_fig = Figure(figsize=(4.6, 3.4), facecolor="#FAFAFA")
            self.section_canvas = FigureCanvasQTAgg(self.section_fig)
            for c in (self.unroll_canvas, self.section_canvas):
                c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            figs.addWidget(self.unroll_canvas, 3)
            figs.addWidget(self.section_canvas, 2)
            lay.addLayout(figs, 1)

        # 双层握手按钮
        act_row = QHBoxLayout()
        self.btn_preview = QPushButton("查看保存的冷却沿程")
        self.btn_preview.setObjectName("PrimaryButton")
        self.btn_preview.clicked.connect(self.run_marching_preview)
        self.btn_writeback = QPushButton("按当前参数重算完整循环")
        self.btn_writeback.setObjectName("SecondaryButton")
        self.btn_writeback.clicked.connect(self._writeback_and_rerun)
        self.preview_note = QLabel("")
        self.preview_note.setObjectName("MutedLabel")
        self.preview_note.setWordWrap(True)
        act_row.addWidget(self.btn_preview)
        act_row.addWidget(self.btn_writeback)
        act_row.addWidget(self.preview_note, 1)
        lay.addLayout(act_row)
        return w

    # -- 表格 ↔ JSON 同步 --

    def _table_rows(self) -> List[cd.ChannelRow]:
        rows: List[cd.ChannelRow] = []
        for r in range(self.chan_table.rowCount()):
            combo = self.chan_table.cellWidget(r, 0)
            anchor = combo.currentData() if isinstance(combo, QComboBox) else "x_mm"

            def _num(col, default):
                # V19 审查修复：只有空单元格才取默认；不可解析的文本必须抛错——
                # 否则表格显示一个值、实际序列化进 JSON 的是另一个（静默分歧）。
                it = self.chan_table.item(r, col)
                txt = (it.text().strip() if it else "")
                if not txt:
                    return default
                try:
                    return float(txt)
                except ValueError:
                    raise ValueError(f"第 {r + 1} 行第 {col + 1} 列 {txt!r} 不是数字")
            rows.append(cd.ChannelRow(
                anchor=anchor or "x_mm",
                x_mm=_num(1, 0.0),
                n=_num(2, 300),
                w_mm=_num(3, 1.5),
                h_mm=_num(4, 5.0),
                t_mm=_num(5, 0.5),
            ))
        return rows

    def _table_set_rows(self, rows: List[cd.ChannelRow]):
        self._table_loading = True
        try:
            self.chan_table.setRowCount(0)
            for row in rows:
                self._table_insert_row(row)
        finally:
            self._table_loading = False

    def _table_insert_row(self, row: cd.ChannelRow):
        r = self.chan_table.rowCount()
        self.chan_table.insertRow(r)
        combo = QComboBox()
        for a in cd.ANCHORS:
            combo.addItem(cd.ANCHOR_LABELS[a], userData=a)
        combo.setCurrentIndex(list(cd.ANCHORS).index(row.anchor)
                              if row.anchor in cd.ANCHORS else 4)
        combo.currentIndexChanged.connect(lambda *_: self._on_table_edited(None))
        self.chan_table.setCellWidget(r, 0, combo)
        for col, val in ((1, f"{row.x_mm:.17g}"), (2, str(int(row.n))), (3, f"{row.w_mm:.17g}"),
                         (4, f"{row.h_mm:.17g}"), (5, f"{row.t_mm:.17g}")):
            self.chan_table.setItem(r, col, QTableWidgetItem(val))

    def _table_add_row(self):
        self._table_insert_row(cd.ChannelRow())
        self._on_table_edited(None)

    def _table_del_row(self):
        r = self.chan_table.currentRow()
        if r >= 0:
            self.chan_table.removeRow(r)
            self._on_table_edited(None)

    def _table_from_scalars(self):
        inp = self._read_inputs_safe()
        if inp is None:
            QMessageBox.warning(self, "输入无效", "当前输入有误，无法生成。")
            return
        res = getattr(self.main, "last_results", None)
        co = None
        profile = None
        if res is not None and "cooling_channel" in res.components:
            co = res.components["cooling_channel"].outputs
            profile = getattr(res.components["cooling_channel"], "profile", None)
        if res is None or cd.snapshot_stale_fields(
                cd.BoundarySnapshot(inputs_fingerprint=vars(res.inputs)), inp):
            QMessageBox.warning(self, "需要当前结果", "参数已修改或尚无计算结果；请先运行当前设计点再生成节点采样表。")
            return
        try:
            rows = cd.default_rows_from_inputs(inp, co, cooling_profile=profile)
        except (ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "无法生成节点采样表", str(exc))
            return
        self._table_set_rows(rows)
        self._on_table_edited(None)
        QMessageBox.information(self, "已生成节点采样表",
            "已保留当前已解剖面的全部几何节点，按绝对轴向坐标分段线性插值。\n"
            "这是当前几何的节点采样表示，不是跨网格或新轮廓下的解析等效规则；修改后需重算完整循环。")

    def _table_clear(self):
        self._table_set_rows([])
        self._on_table_edited(None)

    def _on_table_edited(self, _item):
        """表格编辑 → 写回隐藏 JSON 字段 + 可行性/图刷新。"""
        if self._table_loading:
            return
        try:
            rows = self._table_rows()
            for row in rows:
                row.validate()
            js = cd.serialize_channel_table(rows)
        except Exception as ex:
            self.chan_feas.setText(f"<span style='color:#dc2626;'>✗ 表格无效：{ex}</span>")
            return
        self._table_loading = True
        try:
            self._table_json_edit.setText(js)
        finally:
            self._table_loading = False
        self._refresh_channel_views()

    def _on_table_json_changed(self, text: str):
        """隐藏 JSON 字段变化（如 reset_defaults）→ 重载表格 UI。"""
        if self._table_loading:
            return
        try:
            rows = cd.parse_channel_table(text) or []
        except Exception:
            rows = []
        self._table_set_rows(rows)
        self._refresh_channel_views()

    def _current_contour(self):
        """(x_arr, r_arr, x_throat, L_cyl) 用于可行性/展开视图：优先当前 WallField，
        回退几何轮廓。"""
        wf = self._wf_current
        if wf is not None and getattr(wf, "x", None):
            res = getattr(self.main, "last_results", None)
            L_cyl = 0.0
            if res is not None and "main_chamber" in res.components:
                L_cyl = float(res.components["main_chamber"].outputs.get("cc_length_m", 0.0) or 0.0)
            return list(wf.x), list(wf.r), wf.x[wf.throat_index], L_cyl
        geom = self._get_geom()
        if geom:
            g_nz = geom.get("nozzle") or {}
            g_ch = geom.get("chamber") or {}
            cx = list(g_nz.get("contour_x") or [])
            cr = list(g_nz.get("contour_r") or [])
            if len(cx) >= 8:
                L_cyl = float(g_ch.get("cylindrical_section_length_m", 0.0) or 0.0)
                L_conv = float(g_nz.get("convergent_length_m", 0.0) or 0.0)
                xs = [0.0] + [x + L_cyl + L_conv for x in cx]
                rs = [float(g_ch.get("radius_m", max(cr)))] + list(cr)
                return xs, rs, L_cyl + L_conv, L_cyl
        return None

    def _refresh_channel_views(self):
        """可行性清单 + 展开视图 + 穿壁截面刷新。"""
        rows: List[cd.ChannelRow] = []
        try:
            rows = cd.parse_channel_table(self._table_json_edit.text()) or []
        except Exception:
            pass
        ct = self._current_contour()
        # 可行性
        if not rows:
            self.chan_feas.setText("（表为空：单段模式，与 V15 行为一致。基准参数见上方标量组）")
        elif ct is None:
            self.chan_feas.setText("表已保存；运行设计点后按真实轮廓给出可行性校核。")
        else:
            xs, rs, x_t, L_cyl = ct
            try:
                dist = cd.resolve_channel_distribution(
                    rows, xs, rs, x_throat=x_t, L_cyl=L_cyl, x_exit=xs[-1])
                items = cd.channel_feasibility(dist, xs, x_t)
                html = []
                for lvl, msg in items:
                    mk, color, _bg = _STATUS_STYLE.get(lvl, _STATUS_STYLE["na"])
                    html.append(f"<span style='color:{color};'>{mk} {msg}</span>")
                self.chan_feas.setText("<br>".join(html))
            except Exception as ex:
                self.chan_feas.setText(f"<span style='color:#dc2626;'>✗ 分布解析失败：{ex}</span>")
        self._draw_unrolled(rows, ct)
        self._draw_section()

    def _draw_unrolled(self, rows, ct):
        """展开视图：通道宽/肋宽/节距沿 x + N(x) 阶跃（分叉线）。"""
        if not MPL:
            return
        fig = self.unroll_fig
        fig.clear()
        ax = fig.add_subplot(111)
        if ct is None:
            ax.axis("off")
            ax.text(0.5, 0.5, "运行设计点后生成通道展开视图", ha="center", va="center",
                    transform=ax.transAxes, color="#9CA3AF", style="italic", fontsize=9)
            self.unroll_canvas.draw_idle()
            return
        xs, rs, x_t, L_cyl = ct
        try:
            if rows:
                dist = cd.resolve_channel_distribution(
                    rows, xs, rs, x_throat=x_t, L_cyl=L_cyl, x_exit=xs[-1])
            else:
                # 单段模式：按 V15 规则可视化（N 恒定，宽∝r，深/衬厚恒定）
                inp = self._read_inputs_safe() or self._defaults
                r_t = min(rs)
                n0 = int(getattr(inp, "cooling_n_channels", 300))
                w0 = float(getattr(inp, "cooling_channel_width_m", 1.5e-3))
                h0 = float(getattr(inp, "cooling_channel_height_m", 5.0e-3))
                t0 = float(getattr(inp, "cooling_wall_thickness_m", 0.5e-3))
                dist = {"n": [float(n0)] * len(xs),
                        "w_m": [w0 * max(r / r_t, 0.3) for r in rs],
                        "h_m": [h0] * len(xs), "t_m": [t0] * len(xs),
                        "pitch_m": [2 * math.pi * r / n0 for r in rs]}
                dist["rib_m"] = [p - w for p, w in zip(dist["pitch_m"], dist["w_m"])]
            x_mm = [x * 1e3 for x in xs]
            ax.plot(x_mm, [v * 1e3 for v in dist["w_m"]], color="#2563eb", lw=1.6, label="通道宽 w")
            ax.plot(x_mm, [v * 1e3 for v in dist["rib_m"]], color="#dc2626", lw=1.4, label="肋宽")
            ax.plot(x_mm, [v * 1e3 for v in dist["pitch_m"]], color="#94a3b8", lw=1.0,
                    ls="--", label="节距")
            ax.plot(x_mm, [v * 1e3 for v in dist["h_m"]], color="#059669", lw=1.2,
                    ls="-.", label="通道深 h")
            ax.axhline(0., color="#dc2626", ls=":", lw=0.9, label="几何重叠边界")
            ax.axvline(x_t * 1e3, color="#7c2d12", ls=":", lw=0.9)
            ax.set_ylabel("[mm]", fontsize=8)
            ax.set_xlabel("x [mm]", fontsize=8)
            ax2 = ax.twinx()
            ax2.step(x_mm, dist["n"], where="post", color="#7c3aed", lw=1.4)
            ax2.set_ylabel("通道数 N", fontsize=8, color="#7c3aed")
            ax2.tick_params(axis="y", labelcolor="#7c3aed", labelsize=7.5)
            # 分叉站
            for i in range(1, len(dist["n"])):
                if dist["n"][i] != dist["n"][i - 1]:
                    ax.axvline(x_mm[i], color="#7c3aed", ls="--", lw=1.0, alpha=0.7)
            ax.legend(fontsize=7, ncol=4, loc="upper right", framealpha=0.85)
            ax.set_title("通道周向展开 · 宽/肋/节距/深 沿程分布（紫竖线=分叉站）",
                         fontsize=9.5, fontweight="bold", loc="left")
            ax.tick_params(labelsize=7.5)
            for s in ("top",):
                ax.spines[s].set_visible(False)
            ax.grid(True, alpha=0.2)
            fig.tight_layout()
        except Exception as ex:
            ax.axis("off")
            ax.text(0.5, 0.5, f"展开视图失败：{ex}", ha="center", va="center",
                    transform=ax.transAxes, color="#dc2626", fontsize=8)
        self.unroll_canvas.draw_idle()

    def _draw_section(self):
        """穿壁截面详图（游标站位；无游标取喉部）——分层实际尺寸。"""
        if not MPL:
            return
        fig = self.section_fig
        fig.clear()
        ax = fig.add_subplot(111)
        wf = self._wf_current
        if wf is None or not getattr(wf, "x", None):
            ax.axis("off")
            ax.text(0.5, 0.5, "运行设计点/重算沿程后\n生成穿壁截面详图", ha="center", va="center",
                    transform=ax.transAxes, color="#9CA3AF", style="italic", fontsize=9)
            self.section_canvas.draw_idle()
            return
        try:
            idx = self._cursor_idx if self._cursor_idx is not None else wf.throat_index
            idx = max(0, min(idx, len(wf.x) - 1))

            def _at(arr, default):
                return float(arr[idx]) if arr and len(arr) > idx and arr[idx] else default
            r_loc = _at(wf.r, 0.1)
            n = int(_at(wf.n_chan, 0.0) or 0)
            geom = self._get_geom() or {}
            g_ch = geom.get("chamber") or {}
            if n <= 0:
                n = int(g_ch.get("num_cooling_channels", 300) or 300)
            pitch = 2 * math.pi * r_loc / max(n, 1)
            w = _at(wf.w_chan, 0.0) or float(g_ch.get("cooling_channel_width_mm", 1.5) or 1.5) / 1e3
            h = _at(wf.h_chan, 0.0) or float(g_ch.get("cooling_channel_height_mm", 5.0) or 5.0) / 1e3
            t = _at(wf.t_liner, 0.0) or float(wf.t_wall_m or 0.5e-3)
            t_close = 1.0e-3
            t_jacket = max(float(g_ch.get("outer_wall_thickness_mm", 6.0) or 6.0) / 1e3 - t_close, 1e-3)
            mm = 1e3
            P, W, H, T, C, J = pitch * mm, w * mm, h * mm, t * mm, t_close * mm, t_jacket * mm
            from matplotlib.patches import Rectangle
            # 燃气侧在下：内衬（含肋）→ 闭合层 → 外壳
            ax.add_patch(Rectangle((0, 0), P, T, fc="#f59e0b", ec="#92400e", lw=0.8))
            # 肋（两条半肋 + 中间通道空腔）
            rib_half = max((P - W) / 2.0, 0.0)
            ax.add_patch(Rectangle((0, T), rib_half, H, fc="#f59e0b", ec="#92400e", lw=0.8))
            ax.add_patch(Rectangle((P - rib_half, T), rib_half, H, fc="#f59e0b", ec="#92400e", lw=0.8))
            ax.add_patch(Rectangle((rib_half, T), W, H, fc="#bae6fd", ec="#0369a1", lw=0.8))
            ax.add_patch(Rectangle((0, T + H), P, C, fc="#fbbf24", ec="#92400e", lw=0.8))
            ax.add_patch(Rectangle((0, T + H + C), P, J, fc="#cbd5e1", ec="#475569", lw=0.8))
            # 尺寸标注
            zone = cd.ZONE_LABELS.get(
                cd.zone_of_x(wf.x[idx],
                             L_cyl=self._current_contour()[3] if self._current_contour() else 0.0,
                             x_throat=wf.x[wf.throat_index], r_t=min(wf.r)), "")
            ax.annotate(f"节距 {P:.2f}", (P / 2, -0.12 * (T + H + C + J)), ha="center",
                        fontsize=7.5, color="#334155")
            ax.annotate(f"w {W:.2f}", (P / 2, T + H / 2), ha="center", fontsize=7.5, color="#0c4a6e")
            ax.annotate(f"h {H:.2f}", (rib_half + W + 0.02 * P, T + H / 2), ha="left",
                        fontsize=7.5, color="#0c4a6e")
            ax.annotate(f"内衬 t {T:.2f}", (0.02 * P, T * 0.5), ha="left", va="center",
                        fontsize=7.5, color="#7c2d12")
            ax.annotate(f"肋 {max(P - W, 0):.2f}", (rib_half * 0.5, T + H * 0.9),
                        ha="center", fontsize=7, color="#7c2d12")
            ax.annotate(f"外壳 {J:.1f}", (P / 2, T + H + C + J / 2), ha="center",
                        fontsize=7.5, color="#1e293b")
            ax.set_title(f"穿壁截面 @ x={wf.x[idx]*mm:.0f} mm（{zone}，N={n}）  [mm]",
                         fontsize=9, fontweight="bold", loc="left")
            ax.set_xlim(-0.05 * P, 1.05 * P)
            ax.set_ylim(-0.2 * (T + H + C + J), (T + H + C + J) * 1.08)
            ax.set_aspect("equal")
            ax.axis("off")
            fig.tight_layout()
        except Exception as ex:
            ax.axis("off")
            ax.text(0.5, 0.5, f"截面图失败：{ex}", ha="center", va="center",
                    transform=ax.transAxes, color="#dc2626", fontsize=8)
        self.section_canvas.draw_idle()

    # ---- 3 沿程热分析（嵌入原再生冷却页 + 游标 + 膜冷却控制）--------

    def _build_marching_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        top = QHBoxLayout()
        box = QGroupBox("膜冷却 / 辐射（改变后需重算完整循环）")
        form = QFormLayout(box)
        self._register_edit(form, "film_cooling_fraction", *CHAMBER_INPUTS['film_cooling_fraction'])
        self._register_edit(form, "film_effectiveness_initial", *CHAMBER_INPUTS['film_effectiveness_initial'])
        self._register_edit(form, "film_decay_length_m", *CHAMBER_INPUTS['film_decay_length_m'])
        self._register_edit(form, "cooling_gas_emissivity", *CHAMBER_INPUTS['cooling_gas_emissivity'])
        top.addWidget(box, 2)

        self.station_card = self._card("站位明细（在下方曲线上点击选站）", "—")
        top.addWidget(self.station_card, 3)

        side = QVBoxLayout()
        # 存为属性：run_marching_preview 需要把两个触发按钮一起禁用（防竞态）
        self.btn_preview_m = QPushButton("查看保存的冷却沿程")
        self.btn_preview_m.setObjectName("PrimaryButton")
        self.btn_preview_m.clicked.connect(self.run_marching_preview)
        b2 = self.btn_station_fea = QPushButton("▶ 对游标站运行 2D FEM")
        b2.setObjectName("SecondaryButton")
        b2.clicked.connect(self._run_station_fea)
        side.addWidget(self.btn_preview_m)
        side.addWidget(b2)
        side.addStretch(1)
        top.addLayout(side)
        lay.addLayout(top)

        # 嵌入 MainWindow 的再生冷却页（figure/populate 逻辑复用）
        self._cooling_widget = self.main._build_cooling_tab()
        lay.addWidget(self._cooling_widget, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _connect_cursor(self):
        """（重）连接沿程图的点击游标。populate 会重建 axes，故每次刷新后重连。"""
        canvas = getattr(self.main, "cooling_canvas", None)
        if canvas is None:
            return
        if self._cursor_cid is not None:
            try:
                canvas.mpl_disconnect(self._cursor_cid)
            except Exception:
                pass
        self._cursor_lines = []
        self._cursor_cid = canvas.mpl_connect("button_press_event", self._on_profile_click)

    def _on_profile_click(self, event):
        wf = self._wf_current
        if wf is None or not getattr(wf, "x", None) or event.xdata is None:
            return
        x_click = float(event.xdata)   # mm
        idx = min(range(len(wf.x)), key=lambda i: abs(wf.x[i] * 1e3 - x_click))
        self.set_cursor_station(idx)

    def set_cursor_station(self, idx: int):
        wf = self._wf_current
        if wf is None or not wf.x:
            return
        idx = max(0, min(int(idx), len(wf.x) - 1))
        self._cursor_idx = idx
        x_mm = wf.x[idx] * 1e3
        fig = getattr(self.main, "cooling_fig", None)
        canvas = getattr(self.main, "cooling_canvas", None)
        if fig is not None and canvas is not None:
            for ln in self._cursor_lines:
                try:
                    ln.remove()
                except Exception:
                    pass
            self._cursor_lines = []
            for ax in fig.axes:
                if not getattr(ax, "axison", True):
                    continue
                try:
                    self._cursor_lines.append(
                        ax.axvline(x_mm, color="#be185d", lw=1.3, ls="-", alpha=0.85, zorder=9))
                except Exception:
                    pass
            canvas.draw_idle()
        self._update_station_card(idx)
        self._draw_section()

    def _update_station_card(self, idx: int):
        wf = self._wf_current
        if wf is None:
            return

        def _at(arr, scale=1.0, fmt="{:.1f}", default="—"):
            try:
                if arr and len(arr) > idx:
                    return fmt.format(arr[idx] * scale)
            except Exception:
                pass
            return default
        ct = self._current_contour()
        L_cyl = ct[3] if ct else 0.0
        zone = cd.ZONE_LABELS.get(cd.zone_of_x(
            wf.x[idx], L_cyl=L_cyl, x_throat=wf.x[wf.throat_index], r_t=min(wf.r)), "—")
        n = int(wf.n_chan[idx]) if wf.n_chan and wf.n_chan[idx] else 0
        pitch = 2 * math.pi * wf.r[idx] / n * 1e3 if n else 0.0
        rib = pitch - (wf.w_chan[idx] * 1e3 if wf.w_chan else 0.0) if n else 0.0
        body = (
            f"x = {wf.x[idx]*1e3:.0f} mm ｜ 分区：{zone} ｜ r = {wf.r[idx]*1e3:.1f} mm<br>"
            f"q = {_at(wf.q, 1e-6, '{:.1f}')} MW/m² ｜ h_g = {_at(wf.h_g, 1e-3, '{:.1f}')} kW/m²K ｜ "
            f"h_c = {_at(wf.h_c, 1e-3, '{:.0f}')} kW/m²K<br>"
            f"T_wg = {_at(wf.T_wg, 1, '{:.0f}')} K ｜ T_wc = {_at(wf.T_wc, 1, '{:.0f}')} K ｜ "
            f"T_co = {_at(wf.T_coolant, 1, '{:.0f}')} K ｜ p_co = {_at(wf.p_coolant, 1e-6, '{:.1f}')} MPa<br>"
            f"V = {_at(wf.v_coolant, 1, '{:.0f}')} m/s ｜ "
            f"通道：N={n or '—'}，w={_at(wf.w_chan, 1e3, '{:.2f}')}×h={_at(wf.h_chan, 1e3, '{:.2f}')} mm，"
            f"肋 {rib:.2f} mm，内衬 t={_at(wf.t_liner, 1e3, '{:.2f}')} mm<br>"
            f"σ_eq = {_at(wf.sigma_eq, 1, '{:.0f}')} MPa ｜ SF = {_at(wf.SF, 1, '{:.2f}')}")
        self.station_card.setText(
            "<div style='font-size:12.5px;font-weight:bold;color:#0f172a;margin-bottom:4px;'>"
            f"站位明细 · #{idx}</div><div style='font-size:12px;color:#374151;'>{body}</div>")

    def _run_station_fea(self):
        """Solve the selected station snapshot without blocking the UI thread."""
        if getattr(self.main, "_busy", False):
            return
        wf = self._wf_current
        if wf is None or not getattr(wf, "x", None):
            QMessageBox.information(self, "无数据", "当前记录未保存统一冷却沿程；请重算完整循环。")
            return
        geom = self._get_geom()
        if geom is None:
            QMessageBox.information(self, "无几何", "请先运行设计点。")
            return
        try:
            import fea_chamber
        except Exception:
            QMessageBox.warning(self, "缺少依赖", "2D 有限元需要 scikit-fem：pip install scikit-fem")
            return
        from copy import deepcopy
        from auxiliary_worker import start_auxiliary_task
        idx = self._cursor_idx if self._cursor_idx is not None else wf.throat_index
        inner_key = self._mat_key("chamber_inner_wall_material", "grcop84")
        wf, geom = deepcopy(wf), deepcopy(geom)
        main_results = getattr(self.main, "last_results", None)
        cooling = main_results.components.get("cooling_channel") if main_results is not None else None
        is_dist = bool(cooling and getattr(cooling, "profile", {}).get("dist_mode"))
        preview_mode = bool(getattr(self, "_preview_mode", False))
        start_auxiliary_task(
            self.main,
            lambda: {"result": fea_chamber.solve_from_wallfield(wf, geom, inner_material_key=inner_key, station_index=idx),
                     "wallfield": wf, "station_index": idx, "is_dist": is_dist, "preview_mode": preview_mode},
            "station_fea", f"站位 #{idx} 的 2D 有限元求解中", self._on_station_fea_finished)

    @Slot(object)
    def _on_station_fea_finished(self, payload):
        self.main._set_busy(False, "站位 2D 有限元完成")
        self.main._task_kind = None
        try:
            self._show_station_fea_result(payload)
        except Exception:
            self.main._on_worker_error(traceback.format_exc())

    def _show_station_fea_result(self, payload):
        import fea_chamber
        res, wf, idx = payload["result"], payload["wallfield"], payload["station_index"]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"站位 2D 有限元 · x = {wf.x[idx]*1e3:.0f} mm（站 #{idx}）")
        dlg.resize(1150, 800)
        lay = QVBoxLayout(dlg)
        if MPL and res.ok:
            fig = Figure(figsize=(12, 6), facecolor="#FAFAFA")
            canvas = FigureCanvasQTAgg(fig)
            fea_chamber.plot_fea_fields(fig, res)
            lay.addWidget(canvas, 3)
        # 单段模式下非喉部站的通道宽只是"宽∝局部半径"的流通面积假设（不是设计
        # 几何），该站的结构结论仅供定性参考——分布表模式给的才是真实局部截面。
        _notes = list(res.notes)
        _is_dist = payload["is_dist"]
        if (not _is_dist and not payload["preview_mode"]
                and abs(idx - wf.throat_index) > 2):
            _notes.insert(0, "⚠ 单段通道模式：本站通道宽为『宽∝局部半径』的流通面积假设，"
                             "非真实设计几何——结构应力结论仅供参考；要按真实截面校核请在"
                             "「冷却通道设计」页启用沿程分布表。")
        note = QLabel("<br>".join(f"· {n}" for n in _notes))
        note.setWordWrap(True)
        note.setTextFormat(Qt.RichText)
        lay.addWidget(note, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("关闭")
        b.setObjectName("PrimaryButton")
        b.clicked.connect(dlg.accept)
        row.addWidget(b)
        lay.addLayout(row)
        dlg.exec()

    # ---- 4 热结构与FEA（嵌入原热结构云图页 + 结构/材料参数）--------

    def _build_thermstr_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        top = QHBoxLayout()
        box = QGroupBox("结构参数")
        form = QFormLayout(box)
        self._register_edit(form, "chamber_wall_allow_pa", *CHAMBER_INPUTS['chamber_wall_allow_pa'])
        self._register_edit(form, "chamber_wall_safety_factor", *CHAMBER_INPUTS['chamber_wall_safety_factor'])
        top.addWidget(box, 1)

        box2 = QGroupBox("推力室壁材料（分层）")
        form2 = QFormLayout(box2)
        self._register_material_combo(form2, "chamber_inner_wall_material",
                                      CHAMBER_INPUTS['chamber_inner_wall_material'][0])
        self._register_material_combo(form2, "chamber_outer_wall_material",
                                      CHAMBER_INPUTS['chamber_outer_wall_material'][0])
        top.addWidget(box2, 2)
        lay.addLayout(top)

        self._thermstr_widget = self.main._build_thermstr_tab()
        lay.addWidget(self._thermstr_widget, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(w)
        return scroll

    # ---- 5 校核汇总 -----------------------------------------------

    def _build_scorecard_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.score_banner = QLabel("运行设计点后生成区域 × 判据裕度记分卡。")
        self.score_banner.setObjectName("MutedLabel")
        lay.addWidget(self.score_banner)
        self.score_table = QTableWidget(0, 6)
        self.score_table.setHorizontalHeaderLabels(
            ["区域", "判据", "当前值", "限值", "裕度", "状态"])
        self.score_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.score_table.verticalHeader().setVisible(False)
        self.score_table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.score_table, 1)
        return w

    def _update_scorecard(self):
        from PySide6.QtGui import QColor, QBrush
        res = getattr(self.main, "last_results", None)
        wf = self._wf_current
        self.score_table.setRowCount(0)
        if res is None or wf is None:
            return
        inp = res.inputs
        try:
            rows = cd.compute_scorecard(wf, res, inp)
        except Exception as ex:
            self.score_banner.setText(f"记分卡生成失败：{ex}")
            return
        n_fail = sum(1 for r in rows if r["status"] == "fail")
        n_warn = sum(1 for r in rows if r["status"] == "warn")
        mode = "（保存的冷却载荷；结构估计未经验证）"
        self.score_banner.setText(
            f"区域 × 判据裕度记分卡：{len(rows)} 项 ｜ ✗ 不满足 {n_fail} ｜ ~ 边缘 {n_warn}{mode}")
        for row in rows:
            r = self.score_table.rowCount()
            self.score_table.insertRow(r)
            mk, color, bg = _STATUS_STYLE.get(row["status"], _STATUS_STYLE["na"])
            vals = [row["zone_label"], row["criterion"], row["value"],
                    row["limit"], row["margin_text"], mk]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setBackground(QBrush(QColor(bg)))
                if c == 5:
                    it.setForeground(QBrush(QColor(color)))
                    it.setTextAlignment(Qt.AlignCenter)
                self.score_table.setItem(r, c, it)

    # ────────────────────────────────────────────────────────────
    # 保存剖面读取 / 完整循环重算
    # ────────────────────────────────────────────────────────────

    def _set_preview_busy(self, busy: bool):
        """两个触发按钮（通道页 + 沿程页）一起禁用/恢复——只锁一个会出现并发
        预览 worker，后完成者覆盖先完成者，界面可能显示旧输入的结果。"""
        if hasattr(self.main, "_set_busy"):
            self.main._set_busy(busy, "读取保存的冷却沿程" if busy else "冷却沿程已读取")
        for btn, idle_text in ((getattr(self, "btn_preview", None),
                                "查看保存的冷却沿程"),
                               (getattr(self, "btn_preview_m", None),
                                "查看保存的冷却沿程")):
            if btn is not None:
                btn.setEnabled(not busy)
                btn.setText("读取中…" if busy else idle_text)
        self._preview_running = busy

    def run_marching_preview(self):
        if getattr(self, "_preview_running", False) or getattr(self.main, "_busy", False):
            return
        res = getattr(self.main, "last_results", None)
        if res is None:
            QMessageBox.information(self, "无循环解", "请先运行完整设计点。")
            return
        try:
            self._on_preview_done(cd.saved_cooling_result(res))
        except ValueError as ex:
            QMessageBox.information(self, "需要完整循环重算", str(ex))

    def _on_preview_done(self, result: dict, seq: int = -1):
        if seq != -1 and seq != getattr(self, "_preview_seq", -1):
            return   # 迟到的旧代际结果，丢弃
        self._set_preview_busy(False)
        try:
            profile = result.get("profile")
            comp = result.get("component")
            if not profile or comp is None:
                raise ValueError("当前循环未保存统一冷却剖面，请重算完整循环。")
            res = self.main.last_results
            geom = self._get_geom() or {}
            from thermal_structural import (wall_field_from_profile,
                                            plot_cooling_profiles, plot_wall_field)
            inner = getattr(res.inputs, "chamber_inner_wall_material", "grcop84")
            outer = getattr(res.inputs, "chamber_outer_wall_material", "inconel718_wall")
            wf_full = wall_field_from_profile(profile, res.summary, geom,
                                              inner_material_key=inner,
                                              outer_material_key=outer, domain="full")
            wf_ch = wall_field_from_profile(profile, res.summary, geom,
                                            inner_material_key=inner,
                                            outer_material_key=outer, domain="chamber")
            if wf_full is None:
                raise ValueError("WallField 构建失败")
            self._wf_current = wf_full
            self._preview_mode = False
            self._last_preview = result
            # 两幅主图采用同一循环保存的热负荷。
            if hasattr(self.main, "cooling_fig"):
                plot_cooling_profiles(self.main.cooling_fig, wf_full)
                self.main.cooling_canvas.draw_idle()
                self.main.cooling_note.setText(
                    "【当前循环保存的冷却沿程】 " + "  ·  ".join(wf_full.notes))
            if wf_ch is not None and hasattr(self.main, "thermstr_fig"):
                plot_wall_field(self.main.thermstr_fig, wf_ch)
                self.main.thermstr_canvas.draw_idle()
                self.main.thermstr_note.setText(
                    "【当前循环保存的冷却载荷】 " + "  ·  ".join(wf_ch.notes))
                self.main._render_chamber_loads(getattr(wf_ch, "loads", None))
            outs = profile.get("summary") or {}
            def recorded(key, scale=1.0):
                value = outs.get(key)
                return f"{value * scale:.2f}" if isinstance(value, (int, float)) and math.isfinite(value) else "未记录"
            dtxt = (f"Δp={recorded('dp_total_Pa', 1e-6)} MPa｜ "
                    f"T_wg,max={recorded('T_wall_hot_max_K')} K｜ "
                    f"Q={recorded('Q_total_W', 1e-6)} MW｜ T_out={recorded('T_out_K')} K")
            warns = result.get("warnings") or []
            if warns:
                dtxt += "<br><span style='color:#b45309;'>" + escape("；".join(map(str, warns))) + "</span>"
            self.preview_note.setText(f"<b>保存的循环结果</b>：{dtxt}<br>修改参数后需重算完整循环；此处不重算热流或压降。")
            self._cursor_idx = None
            self._connect_cursor()
            self.set_cursor_station(wf_full.throat_index)
            self._refresh_channel_views()
            self._update_scorecard()
        except Exception as ex:
            self.main._log_info("保存的冷却剖面读取失败\n" + traceback.format_exc(), source="Chamber")
            QMessageBox.warning(self, "读取失败", str(ex))

    def _on_preview_error(self, tb: str, seq: int = -1):
        if seq != -1 and seq != getattr(self, "_preview_seq", -1):
            return
        self._set_preview_busy(False)
        self.main._log_info("保存的冷却剖面读取异常\n" + tb, source="Chamber")
        QMessageBox.warning(self, "读取失败", tb.splitlines()[-1] if tb else "未知错误")

    def _writeback_and_rerun(self):
        """Compatibility slot: recompute the entire cycle from the current inputs."""
        if self._read_inputs_safe() is None:
            QMessageBox.warning(self, "输入无效", "当前输入有误，请检查。")
            return
        self.main._log_step(
            "按当前输入重算完整循环：统一冷却热负荷、压降与出口状态一起参与闭合。",
            source="Chamber")
        self.main.run_cycle()

    # ────────────────────────────────────────────────────────────
    # 主结果更新入口（_populate_results 调用）
    # ────────────────────────────────────────────────────────────

    def on_results_updated(self, results):
        """设计点求解完成后刷新工作区（主结果模式）。"""
        self._preview_mode = False
        self._last_preview = None
        self._wf_current = getattr(self.main, "_last_wallfield_full", None)
        self.preview_note.setText("")
        self._update_snapshot_tab()
        self._update_contour_tab()
        self._refresh_channel_views()
        self._connect_cursor()
        self._cursor_idx = None
        if self._wf_current is not None and getattr(self._wf_current, "x", None):
            self.set_cursor_station(self._wf_current.throat_index)
        self._update_scorecard()
