"""
topology_drawing.py — FFSC 流程拓扑图渲染器(matplotlib 实现)
============================================================

把原本在 gui_pyside6.py 里 700+ 行的 _draw_topology() 拆出来,改用 matplotlib。

设计原则
────────
1. **数据契约干净**:输入只有两个 dict(summary + geometry),不依赖 GUI 类
2. **风格集中**:所有颜色/字体在 TopologyPalette 一个地方调
3. **可独立测试**:python -c "from topology_drawing import demo; demo()" 就能跑
4. **可导出**:fig.savefig("topology.svg" / ".pdf" / ".png")

接入方式
────────
GUI 调用:
    from topology_drawing import FFSCTopologyRenderer
    fig = FFSCTopologyRenderer(summary, geometry).render()
    # 渲染到 QPixmap,放进 QGraphicsScene(沿用现有 wheel-zoom / drag-pan)

为什么 matplotlib 比 QPainterPath 更合适
─────────────────────────────────────────
QPainterPath:每条 cubicTo 都要手算 4 个控制点;矩形圆角要分四段 quadTo;
            渐变填充要 QLinearGradient 拼;阴影要 QGraphicsDropShadowEffect。
matplotlib :FancyBboxPatch 一句话搞定带圆角的盒子;PathPatch 自动渲染贝塞尔;
            渐变/colormap 原生;数百行 QPainterPath 通常能压到几十行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg", force=True)
from matplotlib import font_manager as _fm
from matplotlib import rcParams as _rc
from matplotlib.figure import Figure
from matplotlib.patches import (
    Circle, FancyBboxPatch, PathPatch, Polygon, Rectangle,
)
from matplotlib.path import Path as MplPath


# ─────────────────────────────────────────────────────────
# 中文字体配置:matplotlib 默认 DejaVu Sans 不支持 CJK。
# 依次尝试常见的中文字体,选第一个系统装了的。
# Windows: Microsoft YaHei / SimHei;Linux: Noto Sans CJK SC;macOS: PingFang SC
# ─────────────────────────────────────────────────────────
def _setup_cjk_font():
    candidates = [
        "Noto Sans SC", "Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC",
        "Noto Sans SC", "PingFang SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
    ]
    installed = {f.name for f in _fm.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            _rc["font.family"] = ["sans-serif"]
            _rc["font.sans-serif"] = [name, "DejaVu Sans"]
            _rc["axes.unicode_minus"] = False
            return name
    # 找不到 CJK 字体时,中文会显示为方框 —— 至少不报错
    return None

_CJK_FONT = _setup_cjk_font()


# ─────────────────────────────────────────────────────────
# 色板:Raptor 2 风格,集中可改
# ─────────────────────────────────────────────────────────

@dataclass
class TopologyPalette:
    lox_liq:    str = "#bae6fd"
    lox_stroke: str = "#0284c7"
    ch4_liq:    str = "#fecdd3"
    ch4_stroke: str = "#e11d48"
    or_gas:     str = "#e9d5ff"
    or_stroke:  str = "#7c3aed"
    fr_gas:     str = "#fed7aa"
    fr_stroke:  str = "#ea580c"
    hot_top:    str = "#fef9c3"   # 主燃室顶部:浅黄
    hot_mid:    str = "#fbbf24"   # 喉部:琥珀
    hot_bot:    str = "#ea580c"   # 出口:红橙
    hot_stroke: str = "#ca8a04"
    shroud:     str = "#f1f5f9"   # 泵机壳浅灰
    shaft:      str = "#475569"
    text:       str = "#0f172a"
    muted:      str = "#64748b"
    bg:         str = "#f8fafc"
    label_bg:   str = "#ffffff"
    pressure:   str = "#1e3a8a"   # 压力数字蓝色
    temp:       str = "#7c3aed"   # 温度数字紫色
    throat:     str = "#7c2d12"
    exhaust:    str = "#dc2626"


# ─────────────────────────────────────────────────────────
# 主渲染器
# ─────────────────────────────────────────────────────────

class FFSCTopologyRenderer:
    """FFSC 全流量分级燃烧循环拓扑图渲染器。

    Attributes
    ──────────
    s : dict      cycle.summary(P、T、ṁ、功率、温度等)
    g : dict      EngineGeometry asdict(rc/rt/re、L_chamber/L_nozzle、contour_x/r)
    p : TopologyPalette  色板
    """

    # 画布尺寸(虚拟像素,1:1 与 GUI 协调)
    # W 加宽到 1460 —— 给右侧信息栏更多空间(更大字体 + 完整中文标签 + 双语标题不溢出)
    W = 1460
    H = 940

    # 关键 x 坐标 —— 泵 / 预燃室 / 涡轮 必须同列(物理:同根轴)
    CH4_X = 260
    LOX_X = 920
    CHAMBER_X = 600
    SIDEBAR_X = 1140
    SIDEBAR_W = 300

    # 关键 y 坐标
    Y_PUMP = 145
    Y_PB   = 320
    Y_TURB = 430
    Y_CH_TOP = 500

    def __init__(self,
                 summary: Dict[str, Any],
                 geometry: Optional[Dict[str, Any]] = None,
                 palette: Optional[TopologyPalette] = None,
                 inputs_pc: Optional[float] = None,
                 inputs_thrust: Optional[float] = None):
        self.s = dict(summary) if summary else {}
        self.g = dict(geometry) if geometry else {}
        self.p = palette or TopologyPalette()
        self.inputs_pc = inputs_pc
        self.inputs_thrust = inputs_thrust

        # 几何尺寸(若可用)
        g_ch = self.g.get("chamber") or {}
        g_nz = self.g.get("nozzle")  or {}
        self.rc_m   = float(g_ch.get("radius_m", 0.0) or 0.0)
        self.rt_m   = float(g_nz.get("throat_radius_m", 0.0) or 0.0)
        self.re_m   = float(g_nz.get("exit_radius_m", 0.0) or 0.0)
        self.Lc_m   = float(g_ch.get("length_m", 0.0) or 0.0)
        self.Lcv_m  = float(g_nz.get("convergent_length_m", 0.0) or 0.0)
        self.Lnz_m  = float(g_nz.get("nozzle_length_m", 0.0) or 0.0)
        self.contour_x = list(g_nz.get("contour_x") or [])
        self.contour_r = list(g_nz.get("contour_r") or [])
        self.theta_n  = float(g_nz.get("initial_expansion_angle_deg", 0.0) or 0.0)
        self.theta_e  = float(g_nz.get("exit_angle_deg", 0.0) or 0.0)
        self.geom_ok  = (self.rc_m > 0 and self.rt_m > 0 and self.re_m > 0
                         and self.Lc_m > 0 and self.Lnz_m > 0)

        # 喷管尺寸映射到拓扑画布坐标
        if self.geom_ok:
            target_axial_px = 340
            max_radial_px = 145
            total_L = self.Lc_m + max(self.Lcv_m, 0.05) + self.Lnz_m
            self.scale_y = target_axial_px / total_L
            self.scale_r = min(self.scale_y, max_radial_px / self.re_m)
            self.ch_top    = self.Y_CH_TOP
            self.ch_bot    = self.ch_top + max(20, int(self.Lc_m * self.scale_y))
            self.throat_y  = self.ch_bot + (max(8, int(self.Lcv_m * self.scale_y))
                                            if self.Lcv_m > 0 else 12)
            self.noz_bot   = self.throat_y + int(self.Lnz_m * self.scale_y)
            self.ch_hw     = max(18, int(self.rc_m * self.scale_r))
            self.throat_hw = max(4,  int(self.rt_m * self.scale_r))
            self.exit_hw   = max(20, int(self.re_m * self.scale_r))
        else:
            self.scale_y = 145.0; self.scale_r = 100.0
            self.ch_top, self.ch_bot = 500, 558
            self.throat_y, self.noz_bot = 580, 840
            self.ch_hw, self.throat_hw, self.exit_hw = 40, 16, 145

    # ═════════════════════════════════════════════════════
    # 公共渲染入口
    # ═════════════════════════════════════════════════════

    def render(self, dpi: int = 100) -> Figure:
        """渲染整张拓扑图,返回 matplotlib Figure。"""
        fig = Figure(figsize=(self.W / 100, self.H / 100), dpi=dpi)
        fig.patch.set_facecolor(self.p.bg)
        ax = fig.add_subplot(111)
        ax.set_xlim(0, self.W)
        ax.set_ylim(self.H, 0)   # y 反向,坐标与 QGraphics 一致
        ax.set_aspect('equal')
        ax.axis('off')
        ax.set_facecolor(self.p.bg)
        # 坐标轴填满整张图(去掉 matplotlib 默认四周白边)——既让导出 PNG 更紧凑,
        # 也让 (画布坐标 ↔ 像素) 干净成比例。
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

        # 顺序:虚线轴最底,管路次底,部件中,标签最上
        self._draw_title(ax)
        self._draw_internal_shafts(ax)
        self._draw_inlets(ax)
        self._draw_cross_feeds(ax)
        self._draw_main_pipes(ax)
        self._draw_regen_box(ax)
        self._draw_turbopumps(ax)
        self._draw_preburners(ax)
        self._draw_turbines(ax)
        self._draw_turbine_exhausts(ax)
        self._draw_chamber_and_nozzle(ax)
        self._draw_geometry_annotations(ax)
        self._draw_sidebar(ax)

        return fig

    def render_svg(self) -> bytes:
        """渲染为 SVG 字节流(矢量),供 QGraphicsSvgItem 无损缩放使用。

        相比 render()→位图→QPixmap,SVG 在 QGraphicsView 里滚轮放大时由
        QSvgRenderer 按当前缩放重新栅格化,任意倍率都清晰,不糊。

        关键:强制 svg.fonttype='path'。matplotlib 默认 'none' 会把文字输出成
        `<text>` + 字体引用(如 #MicrosoftYaHei-20),而 Qt 的 QSvgRenderer
        不支持 SVG 字体引用 → 刷 "link #... is undefined" 警告且文字可能丢失。
        改成 'path' 后每个字符变成矢量 <path>,Qt 渲染无障碍,文字仍可无损缩放。
        """
        import io
        from matplotlib.backends.backend_svg import FigureCanvasSVG

        fig = self.render(dpi=100)
        buf = io.BytesIO()
        canvas = FigureCanvasSVG(fig)
        # 局部覆盖 svg.fonttype,渲染完恢复,避免影响其它 matplotlib 输出
        _prev = _rc.get("svg.fonttype", "path")
        try:
            _rc["svg.fonttype"] = "path"
            canvas.print_svg(buf)
        finally:
            _rc["svg.fonttype"] = _prev
        return buf.getvalue()

    # ═════════════════════════════════════════════════════
    # 数据格式化 helper
    # ═════════════════════════════════════════════════════

    def _fmt(self, key: str, fmt: str, divisor: float = 1.0,
             default: str = "—") -> str:
        v = self.s.get(key)
        if v is None:
            return default
        try:
            return fmt.format(float(v) / divisor)
        except Exception:
            return default

    def _bar(self, key):  return self._fmt(key, "{:.0f} bar", 1e5)
    def _mpa(self, key):  return self._fmt(key, "{:.2f} MPa", 1e6)
    def _K(self, key):    return self._fmt(key, "{:.0f} K")
    def _kg(self, key):   return self._fmt(key, "{:.1f} kg/s")
    def _MW(self, key):   return self._fmt(key, "{:.1f} MW")
    def _kN(self, key):   return self._fmt(key, "{:.0f} kN", 1e3)
    def _f3(self, key):   return self._fmt(key, "{:.3f}")

    # ═════════════════════════════════════════════════════
    # 各部分绘制
    # ═════════════════════════════════════════════════════

    def _draw_title(self, ax):
        ax.text(20, 22,
                "全流量液氧甲烷分级燃烧发动机循环拓扑 / FFSC LOX-CH4 Engine Cycle",
                fontsize=14, fontweight='bold', color=self.p.text)
        ax.text(20, 42,
                "matplotlib 渲染 · 数据驱动",
                fontsize=9, color=self.p.muted)

    def _draw_internal_shafts(self, ax):
        """每侧涡轮泵独立竖向传动轴(虚线,zorder 最低,贯穿 pump→PB→turbine)。"""
        for cx in (self.CH4_X, self.LOX_X):
            ax.plot([cx, cx], [155, 460],
                    color=self.p.shaft, lw=2.4,
                    linestyle=(0, (5, 3)), zorder=0.5)

    def _draw_inlets(self, ax):
        """LOX/CH4 入口箭头。"""
        for cx, color, label in [
            (self.CH4_X, self.p.ch4_stroke, "CH4 入口"),
            (self.LOX_X, self.p.lox_stroke, "LOX 入口"),
        ]:
            ax.annotate("", xy=(cx, 115), xytext=(cx, 60),
                        arrowprops=dict(arrowstyle="-|>,head_width=0.55,head_length=0.8",
                                        color=color, lw=4, mutation_scale=20))
            ax.text(cx, 53, label, ha='center', va='bottom',
                    fontsize=9, fontweight='bold', color=color)

    def _draw_turbopumps(self, ax):
        self._draw_one_turbopump(
            ax, cx=self.CH4_X, l1="CH4 涡轮泵", l2="Methane TP",
            liq=self.p.ch4_liq, stroke=self.p.ch4_stroke,
            p_text=self._bar("fuel_pump_out_pressure_Pa"),
            m_text=self._kg("mdot_fuel_kg_s"),
            power_text=self._MW("fuel_pump_power_MW"),
            side='left',
        )
        self._draw_one_turbopump(
            ax, cx=self.LOX_X, l1="LOX 涡轮泵", l2="LOX TP",
            liq=self.p.lox_liq, stroke=self.p.lox_stroke,
            p_text=self._bar("lox_pump_out_pressure_Pa"),
            m_text=self._kg("mdot_ox_kg_s"),
            power_text=self._MW("ox_pump_power_MW"),
            side='right',
        )

    def _draw_one_turbopump(self, ax, cx, l1, l2, liq, stroke,
                            p_text, m_text, power_text, side='left'):
        """单个涡轮泵总成:进口短管 + 蜗壳 + 叶轮 + 泵机壳 + 外侧标签卡。"""
        cy = self.Y_PUMP
        # 进口短管(从入口箭头终点过渡到叶轮顶)
        ax.add_patch(Rectangle((cx-8, cy-62), 16, 20,
                               facecolor="#e2e8f0", edgecolor=stroke,
                               lw=1.4, zorder=2))
        # 离心泵叶轮剖面符号(蜗壳 + 背弯叶片 + 轮毂)
        self._draw_centrifugal_impeller(ax, cx, cy - 30, r=26,
                                        fill=liq, stroke=stroke,
                                        n_blades=7, zorder=2.6)
        # 泵机壳本体(下半截带圆角,承载标签)
        ax.add_patch(FancyBboxPatch((cx-60, cy-2), 120, 46,
                                    boxstyle="round,pad=0,rounding_size=10",
                                    facecolor=liq, edgecolor=stroke,
                                    lw=2.5, zorder=2.5))
        # 中央双语标签
        ax.text(cx, cy+14, l1, ha='center', va='center',
                fontsize=10, fontweight='bold', color=self.p.text, zorder=5)
        ax.text(cx, cy+31, l2, ha='center', va='center',
                fontsize=8, color=self.p.muted, zorder=5)

        # 外侧卡片:压力 + 流量合到一个框(两行,不再重叠);功率单独一框
        if side == 'left':
            anchor, box_side, ha = cx - 72, 'left', 'right'
        else:
            anchor, box_side, ha = cx + 72, 'right', 'left'
        self._two_line_box(ax, anchor, cy - 4, p_text, m_text,
                           color=stroke, box_side=box_side)
        ax.text(anchor, cy + 44, "P = " + power_text, ha=ha, va='center',
                fontsize=10, fontweight='bold', color=stroke,
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg,
                          edgecolor=stroke, lw=1.3))

    def _draw_cross_feeds(self, ax):
        """交叉小流量:CH4 → 富氧 PB,LOX → 富燃 PB。"""
        # CH4 → 富氧 PB
        ch4_xf = self.s.get("mdot_fuel_to_ox_preburner_kg_s")
        ch4_text = (f"少量 CH4: {float(ch4_xf):.2f} kg/s"
                    if ch4_xf is not None else "少量 CH4 → 富氧 PB")
        self._curved_pipe(ax,
            [(320, 145), (320, 75), (845, 75), (845, 290)],
            color=self.p.ch4_stroke, lw=2.5, corner_r=14)
        self._valve(ax, 320, 145, self.p.ch4_stroke)
        ax.text(490, 64, ch4_text, fontsize=8, fontweight='bold',
                color=self.p.ch4_stroke,
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.92))
        # LOX → 富燃 PB
        lox_xf = self.s.get("mdot_ox_to_fuel_preburner_kg_s")
        lox_text = (f"少量 LOX: {float(lox_xf):.2f} kg/s"
                    if lox_xf is not None else "少量 LOX → 富燃 PB")
        self._curved_pipe(ax,
            [(860, 145), (860, 95), (335, 95), (335, 290)],
            color=self.p.lox_stroke, lw=2.5, corner_r=14)
        self._valve(ax, 860, 145, self.p.lox_stroke)
        ax.text(490, 108, lox_text, fontsize=8, fontweight='bold',
                color=self.p.lox_stroke,
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.92))

    def _draw_main_pipes(self, ax):
        """CH4 主路 → cooling jacket → 富燃 PB;LOX 主路 → 富氧 PB。"""
        # CH4 主路:泵出口 → cooling jacket
        self._curved_pipe(ax,
            [(320, 172), (320, 235), (380, 235), (380, 245)],
            color=self.p.ch4_stroke, lw=4, corner_r=14)
        ax.text(330, 215, "高压 CH4", fontsize=8, fontweight='bold',
                color=self.p.ch4_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.92))
        # cooling jacket 出口 → 富燃 PB
        self._curved_pipe(ax,
            [(380, 295), (320, 312), (260, 290)],
            color=self.p.ch4_stroke, lw=4, corner_r=12)
        # LOX 主路:泵出口 → 富氧 PB
        self._curved_pipe(ax,
            [(860, 172), (860, 252), (920, 252), (920, 290)],
            color=self.p.lox_stroke, lw=4, corner_r=14)
        ax.text(800, 215, "主路 LOX", fontsize=8, fontweight='bold',
                color=self.p.lox_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.92))

    def _draw_regen_box(self, ax):
        """中部 '再生冷却通道' 概念框(连 CH4 泵和富燃 PB)。"""
        cx, cy = 430, 270
        ax.add_patch(FancyBboxPatch((cx-80, cy-25), 160, 50,
                                    boxstyle="round,pad=0,rounding_size=8",
                                    facecolor=self.p.ch4_liq,
                                    edgecolor=self.p.ch4_stroke, lw=2))
        ax.text(cx, cy-10, "再生冷却通道", ha='center', va='center',
                fontsize=9, fontweight='bold', color=self.p.text)
        ax.text(cx, cy+5, "Regen cooling", ha='center', va='center',
                fontsize=8, color=self.p.muted)
        # 波纹换热线
        for i in range(4):
            xx = cx - 55 + i * 28
            ax.plot([xx, xx+20], [cy+18, cy+18],
                    color=self.p.ch4_stroke, lw=2)

    def _draw_preburners(self, ax):
        self._draw_one_preburner(
            ax, cx=self.CH4_X, l1="富燃预燃室", l2="Fuel-rich PB",
            fill=self.p.fr_gas, stroke=self.p.fr_stroke,
            p_key="fuel_preburner_pressure_Pa",
            t_key="fuel_preburner_T_combustion_K",
            mr_key="fuel_preburner_mr", side='left',
        )
        self._draw_one_preburner(
            ax, cx=self.LOX_X, l1="富氧预燃室", l2="O-rich PB",
            fill=self.p.or_gas, stroke=self.p.or_stroke,
            p_key="ox_preburner_pressure_Pa",
            t_key="ox_preburner_T_combustion_K",
            mr_key="ox_preburner_mr", side='right',
        )

    def _draw_one_preburner(self, ax, cx, l1, l2, fill, stroke,
                            p_key, t_key, mr_key, side):
        cy = self.Y_PB
        ax.add_patch(FancyBboxPatch((cx-75, cy-30), 150, 60,
                                    boxstyle="round,pad=0,rounding_size=10",
                                    facecolor=fill, edgecolor=stroke, lw=2.5))
        # 喷注面板(顶部黄色横条)
        ax.add_patch(Rectangle((cx-75, cy-30), 150, 6,
                               facecolor=self.p.hot_top, edgecolor=stroke, lw=1.2))
        ax.text(cx, cy-3, l1, ha='center', va='center',
                fontsize=10, fontweight='bold', color=self.p.text)
        ax.text(cx, cy+14, l2, ha='center', va='center',
                fontsize=8, color=self.p.muted)
        # P/T 合到一个框(压力蓝 + 温度紫,不重叠);O/F 单列其下
        if side == 'left':
            anchor, box_side, ha = cx - 84, 'left', 'right'
        else:
            anchor, box_side, ha = cx + 84, 'right', 'left'
        self._two_line_box(ax, anchor, cy - 6, self._bar(p_key), self._K(t_key),
                           color=self.p.pressure, color2=self.p.temp,
                           box_side=box_side)
        v = self.s.get(mr_key)
        if v is not None:
            ax.text(anchor, cy + 22, f"O/F = {float(v):.3f}", ha=ha, va='center',
                    fontsize=9, color=self.p.muted)

    def _draw_turbines(self, ax):
        for cx, fill, stroke, p_key, t_key, pwr_key, l1, l2, side in [
            (self.CH4_X, self.p.fr_gas, self.p.fr_stroke,
             "fuel_turbine_exhaust_pressure_Pa", "fuel_turbine_exhaust_T_K",
             "fuel_turbine_power_MW", "富燃涡轮", "F-rich turbine", 'left'),
            (self.LOX_X, self.p.or_gas, self.p.or_stroke,
             "ox_turbine_exhaust_pressure_Pa", "ox_turbine_exhaust_T_K",
             "ox_turbine_power_MW", "富氧涡轮", "O-rich turbine", 'right'),
        ]:
            cy = self.Y_TURB
            # 轴流涡轮转子剖面符号(转盘 + 上下叶栅)
            self._draw_turbine_rotor(ax, cx, cy, hw=65, hh=30,
                                     fill=fill, stroke=stroke, zorder=2.5)
            ax.text(cx, cy-2, l1, ha='center', va='center',
                    fontsize=10, fontweight='bold', color=self.p.text, zorder=4)
            ax.text(cx, cy+13, l2, ha='center', va='center',
                    fontsize=8, color=self.p.muted, zorder=4)
            # 外侧排气 P/T 合到一个框(压力蓝 + 温度紫,不重叠)
            if side == 'left':
                anchor, box_side = cx - 80, 'left'
            else:
                anchor, box_side = cx + 80, 'right'
            self._two_line_box(ax, anchor, cy, self._bar(p_key), self._K(t_key),
                               color=self.p.pressure, color2=self.p.temp,
                               box_side=box_side)
            # 功率芯片(下方居中)
            ax.text(cx, cy+52, "P = " + self._MW(pwr_key),
                    ha='center', va='center',
                    fontsize=10, fontweight='bold', color=stroke,
                    bbox=dict(boxstyle='round,pad=0.3',
                              facecolor=self.p.label_bg,
                              edgecolor=stroke, lw=1.3))

    def _draw_turbine_exhausts(self, ax):
        """涡轮排气 → 主燃室侧壁。"""
        ch_top, ch_hw = self.ch_top, self.ch_hw
        cx = self.CHAMBER_X
        wall_y = ch_top + 30
        # 富燃排气
        self._curved_pipe(ax,
            [(self.CH4_X, 460), (self.CH4_X, 495),
             (cx - ch_hw - 14, 495),
             (cx - ch_hw - 14, wall_y),
             (cx - ch_hw,      wall_y)],
            color=self.p.fr_stroke, lw=5, corner_r=16)
        ax.text(380, 478,
                f"富燃排气 {self._kg('mdot_fuel_kg_s')}",
                fontsize=8, fontweight='bold', color=self.p.fr_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.92))
        # 富氧排气
        self._curved_pipe(ax,
            [(self.LOX_X, 460), (self.LOX_X, 495),
             (cx + ch_hw + 14, 495),
             (cx + ch_hw + 14, wall_y),
             (cx + ch_hw,      wall_y)],
            color=self.p.or_stroke, lw=5, corner_r=16)
        ax.text(700, 478,
                f"富氧排气 {self._kg('mdot_ox_kg_s')}",
                fontsize=8, fontweight='bold', color=self.p.or_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.92))

    def _draw_chamber_and_nozzle(self, ax):
        """主燃室 + TOP 抛物线钟形喷管 + 外壁再生冷却通道 + 标签。"""
        cx = self.CHAMBER_X
        ch_top, ch_bot = self.ch_top, self.ch_bot
        throat_y, noz_bot = self.throat_y, self.noz_bot
        ch_hw, throat_hw, exit_hw = self.ch_hw, self.throat_hw, self.exit_hw
        corner_r = 8

        # 构造 bell 路径
        verts: List[Tuple[float, float]] = []
        codes: List[int] = []

        # 顶面 + 右上圆角
        verts.append((cx - ch_hw + corner_r, ch_top))
        codes.append(MplPath.MOVETO)
        verts.append((cx + ch_hw - corner_r, ch_top))
        codes.append(MplPath.LINETO)
        verts.append((cx + ch_hw, ch_top))             # 圆角控制点
        codes.append(MplPath.CURVE3)
        verts.append((cx + ch_hw, ch_top + corner_r))
        codes.append(MplPath.CURVE3)
        # 右侧柱段
        verts.append((cx + ch_hw, ch_bot))
        codes.append(MplPath.LINETO)

        # 右侧轮廓(用 contour_x/r 数据)
        if self.geom_ok and len(self.contour_x) >= 8:
            cx0 = float(self.contour_x[0])
            for x_p, r_p in zip(self.contour_x, self.contour_r):
                y_topo = ch_bot + (float(x_p) - cx0) * self.scale_y
                verts.append((cx + float(r_p) * self.scale_r, y_topo))
                codes.append(MplPath.LINETO)
            # 出口底面
            last_r = float(self.contour_r[-1])
            last_y = ch_bot + (float(self.contour_x[-1]) - cx0) * self.scale_y
            verts.append((cx - last_r * self.scale_r, last_y))
            codes.append(MplPath.LINETO)
            # 左侧轮廓(反向)
            for x_p, r_p in zip(reversed(self.contour_x), reversed(self.contour_r)):
                y_topo = ch_bot + (float(x_p) - cx0) * self.scale_y
                verts.append((cx - float(r_p) * self.scale_r, y_topo))
                codes.append(MplPath.LINETO)
        else:
            # fallback:简化梯形钟形
            verts.extend([(cx + throat_hw, throat_y),
                          (cx + exit_hw, noz_bot),
                          (cx - exit_hw, noz_bot),
                          (cx - throat_hw, throat_y)])
            codes.extend([MplPath.LINETO]*4)

        # 左侧柱段 + 左上圆角 + 闭合
        verts.append((cx - ch_hw, ch_bot))
        codes.append(MplPath.LINETO)
        verts.append((cx - ch_hw, ch_top + corner_r))
        codes.append(MplPath.LINETO)
        verts.append((cx - ch_hw, ch_top))
        codes.append(MplPath.CURVE3)
        verts.append((cx - ch_hw + corner_r, ch_top))
        codes.append(MplPath.CURVE3)
        verts.append((cx - ch_hw + corner_r, ch_top))
        codes.append(MplPath.CLOSEPOLY)

        bell_path = MplPath(verts, codes)
        ax.add_patch(PathPatch(bell_path, facecolor=self.p.hot_top,
                               edgecolor=self.p.hot_stroke, lw=2.5, zorder=3))

        # 渐变叠加(用一组分段填充近似从顶到底的温度色变)
        # 简单做法:在主体上叠加一个 alpha 渐变 polygon
        # 这里先省略,后续可改成 LinearSegmentedColormap + imshow

        # 外壁再生冷却通道(沿 contour 偏移的虚线)
        if self.geom_ok and len(self.contour_x) >= 12:
            cx0 = float(self.contour_x[0])
            for offset_px in (4, 9):
                for side_sgn in (+1, -1):
                    xs, ys = [], []
                    for x_p, r_p in zip(self.contour_x, self.contour_r):
                        y_topo = ch_bot + (float(x_p) - cx0) * self.scale_y
                        xs.append(cx + side_sgn * (float(r_p) * self.scale_r + offset_px))
                        ys.append(y_topo)
                    ax.plot(xs, ys, color=self.p.ch4_stroke, lw=1.0,
                            linestyle=(0, (3, 3)), zorder=3.5)
                    # 柱段
                    ax.plot([cx + side_sgn * (ch_hw + offset_px)] * 2,
                            [ch_top + corner_r, ch_bot],
                            color=self.p.ch4_stroke, lw=1.0,
                            linestyle=(0, (3, 3)), zorder=3.5)
            # Regen manifold
            mw = max(2 * ch_hw + 32, 80)
            ax.add_patch(Rectangle((cx - mw/2, ch_top - 10), mw, 8,
                                   facecolor=self.p.ch4_liq,
                                   edgecolor=self.p.ch4_stroke, lw=1.5, zorder=5))
            ax.text(cx - mw/2, ch_top - 18, "Regen 入口", fontsize=7,
                    fontweight='bold', color=self.p.ch4_stroke,
                    bbox=dict(boxstyle='round,pad=0.2',
                              facecolor=self.p.label_bg,
                              edgecolor='none', alpha=0.9))

        # 喉部细线
        ax.plot([cx - throat_hw - 4, cx + throat_hw + 4],
                [throat_y, throat_y],
                color=self.p.throat, lw=1, linestyle=':', zorder=5)
        ax.text(cx + throat_hw + 13, throat_y - 2, "喉部 throat",
                fontsize=7, color=self.p.throat)

        # 燃烧室标签(放左侧外,下移到两股排气入口下方,避免压到排气竖管;引线连过来)
        _mc_y = ch_bot - 4
        ax.text(cx - ch_hw - 14, _mc_y,
                "主燃烧室 / Main Chamber",
                ha='right', va='center', fontsize=9, fontweight='bold',
                color=self.p.text)
        ax.plot([cx - ch_hw - 12, cx - ch_hw],
                [_mc_y, _mc_y],
                color=self.p.text, lw=0.6, linestyle=':')

        # 喷管标签(扩张段中下部)
        ax.text(cx, throat_y + 80, "喷管 / Nozzle",
                ha='center', va='center', fontsize=9, fontweight='bold',
                color=self.p.text,
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg,
                          edgecolor='none', alpha=0.7))

        # 主燃室 P/T 卡(右侧外)
        pc_text = self._bar("chamber_pressure_Pa")
        if pc_text == "—" and self.inputs_pc is not None:
            pc_text = f"{float(self.inputs_pc)/1e5:.0f} bar"
        # 放燃烧室体右侧(排气入口下方~喉部之上的直段),与喉部标签拉开间距
        ax.text(cx + ch_hw + 12, ch_top + 40,
                pc_text, ha='left', va='center',
                fontsize=9, fontweight='bold', color=self.p.pressure,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg,
                          edgecolor='#cbd5e1', lw=0.8))
        ax.text(cx + ch_hw + 12, ch_top + 55,
                self._K("chamber_temperature_K"), ha='left', va='center',
                fontsize=8, fontweight='bold', color=self.p.temp)

        # 5 道排气箭头(中间最长,两侧渐短)
        for off, length in [(-90, 32), (-45, 42), (0, 48), (45, 42), (90, 32)]:
            ax.annotate("",
                        xy=(cx + off, noz_bot + 6 + length),
                        xytext=(cx + off, noz_bot + 6),
                        arrowprops=dict(
                            arrowstyle="-|>,head_width=0.55,head_length=0.7",
                            color=self.p.exhaust, lw=6, mutation_scale=18,
                        ))

        # Isp / F 主标注(真空比冲为主——发动机内禀属性)
        isp = self.s.get("Isp_vac_s", self.s.get("Isp_s"))
        thr = self.s.get("thrust_vac_N") or self.s.get("thrust_N") or self.inputs_thrust
        isp_str = f"{float(isp):.1f}" if isp is not None else "—"
        thr_str = f"{float(thr)/1e3:.0f} kN" if thr is not None else "—"
        ax.text(cx, noz_bot + 70,
                f"Isp(vac) = {isp_str} s   |   F(vac) = {thr_str}",
                ha='center', va='center', fontsize=13, fontweight='bold',
                color=self.p.exhaust)

    def _draw_geometry_annotations(self, ax):
        """Dc / Dt / De / L_noz / ε / θn / θe 标注(与几何估算页同源)。"""
        if not self.geom_ok:
            return
        cx = self.CHAMBER_X
        Dc = 2 * self.rc_m; Dt = 2 * self.rt_m; De = 2 * self.re_m
        eps = (self.re_m / self.rt_m) ** 2 if self.rt_m > 0 else 0.0
        # Dc(柱段外侧,紧贴"主燃烧室"标签下方)
        ax.text(cx - self.ch_hw - 14, self.ch_bot + 11,
                f"Dc = {Dc:.3f} m",
                ha='right', va='center', fontsize=8, color=self.p.muted)
        # Dt(喉部右侧,右推让开冷却线,与"喉部 throat"对齐成一组)
        ax.text(cx + self.throat_hw + 13, self.throat_y + 12,
                f"Dt = {Dt:.3f} m",
                ha='left', va='center', fontsize=8, fontweight='bold',
                color=self.p.throat)
        # De(出口右侧,右推让开外壁冷却线)
        ax.text(cx + self.exit_hw + 16, self.noz_bot - 16,
                f"De = {De:.3f} m",
                ha='left', va='center', fontsize=8, fontweight='bold',
                color=self.p.hot_stroke)
        # 综合卡片:L_noz / ε / θn / θe
        ax.text(cx + self.exit_hw + 12, self.throat_y + 60,
                f"L_noz = {self.Lnz_m:.2f} m\nε = {eps:.1f}\nθn = {self.theta_n:.1f}°\nθe = {self.theta_e:.1f}°",
                ha='left', va='top', fontsize=7, color=self.p.muted,
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg,
                          edgecolor='#cbd5e1', lw=0.8))

    # 侧栏排版常量(集中可调):大字号、宽框,解决"字太小/英文溢出/缩写看不懂"
    _SB_TITLE_FS = 13     # 段标题字号
    _SB_KEY_FS   = 11     # 键(左)字号
    _SB_VAL_FS   = 11     # 值(右)字号
    _SB_ROW      = 26     # 行距
    _SB_PAD      = 14     # 框内左右边距
    _SB_HEAD     = 32     # 标题到首行间距

    def _draw_sidebar(self, ax):
        """右侧信息栏:工况摘要 + 图例 + 技术规格。

        V2(用户反馈):侧栏加宽 + 字号放大 + 中文完整表述 + 双语标题不再溢出框。
        """
        x0, w = self.SIDEBAR_X, self.SIDEBAR_W
        TFS, KFS, VFS = self._SB_TITLE_FS, self._SB_KEY_FS, self._SB_VAL_FS
        ROW, PAD, HEAD = self._SB_ROW, self._SB_PAD, self._SB_HEAD

        def _panel(y_top: float, title: str, n: int) -> float:
            """画圆角框 + 段标题,返回框高。"""
            box_h = HEAD + n * ROW + 12
            ax.add_patch(FancyBboxPatch((x0, y_top), w, box_h,
                                        boxstyle="round,pad=0,rounding_size=5",
                                        facecolor='white', edgecolor='#cbd5e1', lw=1.2))
            ax.text(x0 + PAD, y_top + 20, title, fontsize=TFS,
                    fontweight='bold', color=self.p.text)
            return box_h

        def _kv(y_top: float, i: int, k: str, v: str):
            yy = y_top + HEAD + 13 + i * ROW
            ax.text(x0 + PAD, yy, k, fontsize=KFS, color=self.p.muted, va='center')
            ax.text(x0 + w - PAD, yy, v, fontsize=VFS, color=self.p.text,
                    ha='right', fontweight='bold', va='center')

        # 1) 当前工况摘要(键全部写完整)
        rows = [
            ("总流量",          self._kg("mdot_total_kg_s")),
            ("LOX 流量",        self._kg("mdot_ox_kg_s")),
            ("CH4 流量",        self._kg("mdot_fuel_kg_s")),
            ("LOX 泵出口压力",   self._mpa("lox_pump_out_pressure_Pa")),
            ("CH4 泵出口压力",   self._mpa("fuel_pump_out_pressure_Pa")),
            ("富氧预燃室 O/F",   self._f3("ox_preburner_mr")),
            ("富燃预燃室 O/F",   self._f3("fuel_preburner_mr")),
            ("主燃烧室 Pc",      self._mpa("chamber_pressure_Pa")),
        ]
        y0 = 64
        h = _panel(y0, "当前工况摘要", len(rows))
        for i, (k, v) in enumerate(rows):
            _kv(y0, i, k, v)

        # 2) 图例
        legend = [
            (self.p.lox_liq, self.p.lox_stroke, "液态氧 LOX"),
            (self.p.ch4_liq, self.p.ch4_stroke, "液态甲烷 CH4"),
            (self.p.or_gas,  self.p.or_stroke,  "富氧燃气 O-rich gas"),
            (self.p.fr_gas,  self.p.fr_stroke,  "富燃燃气 F-rich gas"),
            (self.p.hot_top, self.p.hot_stroke, "主燃烧室热燃气"),
        ]
        y1 = y0 + h + 16
        _panel(y1, "图例", len(legend))
        for i, (fill, stroke, name) in enumerate(legend):
            yy = y1 + HEAD + 13 + i * ROW
            ax.add_patch(Rectangle((x0 + PAD, yy - 9), 28, 18,
                                   facecolor=fill, edgecolor=stroke, lw=1.6))
            ax.text(x0 + PAD + 40, yy, name, fontsize=KFS,
                    color=self.p.text, va='center')
        h = HEAD + len(legend) * ROW + 12

        # 3) 技术规格(双语完整表述)
        thr_str = self._kN("target_thrust_N")
        if thr_str == "—" and self.inputs_thrust is not None:
            thr_str = f"{float(self.inputs_thrust)/1e3:.0f} kN"
        pc_str = self._mpa("chamber_pressure_Pa")
        if pc_str == "—" and self.inputs_pc is not None:
            pc_str = f"{float(self.inputs_pc)/1e6:.2f} MPa"
        spec_rows = [
            ("循环 Cycle",        "FFSC"),
            ("推进剂",            "LOX + CH4"),
            ("设计推力 Thrust",   thr_str),
            ("真空比冲 Isp_vac",  self._fmt("Isp_vac_s", "{:.1f} s")),
            ("燃烧室压力 Pc",     pc_str),
            ("总混合比 O/F",      self._f3("effective_injector_OF")),
        ]
        y2 = y1 + h + 16
        _panel(y2, "技术规格", len(spec_rows))
        for i, (k, v) in enumerate(spec_rows):
            _kv(y2, i, k, v)

    # ═════════════════════════════════════════════════════
    # 通用绘图原语
    # ═════════════════════════════════════════════════════

    def _curved_pipe(self, ax, points: List[Tuple[float, float]],
                     color: str, lw: float = 4.0,
                     corner_r: float = 12.0,
                     arrow: bool = True):
        """折角圆弧过渡的多段管路。在内拐点用二次贝塞尔生成圆角。"""
        if len(points) < 2:
            return
        verts: List[Tuple[float, float]] = [points[0]]
        codes: List[int] = [MplPath.MOVETO]
        if len(points) == 2:
            verts.append(points[1])
            codes.append(MplPath.LINETO)
        else:
            for i in range(1, len(points) - 1):
                p_prev = points[i - 1]
                p_curr = points[i]
                p_next = points[i + 1]
                dx1 = p_curr[0] - p_prev[0]; dy1 = p_curr[1] - p_prev[1]
                len1 = max(math.hypot(dx1, dy1), 1e-3)
                r1 = min(corner_r, len1 * 0.45)
                dx2 = p_next[0] - p_curr[0]; dy2 = p_next[1] - p_curr[1]
                len2 = max(math.hypot(dx2, dy2), 1e-3)
                r2 = min(corner_r, len2 * 0.45)
                verts.append((p_curr[0] - dx1/len1*r1,
                              p_curr[1] - dy1/len1*r1))
                codes.append(MplPath.LINETO)
                verts.append(p_curr)
                codes.append(MplPath.CURVE3)
                verts.append((p_curr[0] + dx2/len2*r2,
                              p_curr[1] + dy2/len2*r2))
                codes.append(MplPath.CURVE3)
            verts.append(points[-1])
            codes.append(MplPath.LINETO)
        ax.add_patch(PathPatch(MplPath(verts, codes),
                               facecolor='none', edgecolor=color, lw=lw,
                               capstyle='round', joinstyle='round'))
        if arrow:
            # 末端三角箭头
            px, py = points[-1]
            qx, qy = points[-2]
            dx, dy = px - qx, py - qy
            length = max(math.hypot(dx, dy), 1e-3)
            ux, uy = dx / length, dy / length
            ax.add_patch(Polygon([(px, py),
                                  (px - ux*12 + uy*6, py - uy*12 - ux*6),
                                  (px - ux*12 - uy*6, py - uy*12 + ux*6)],
                                 closed=True, facecolor=color, edgecolor=color))

    def _valve(self, ax, cx: float, cy: float, color: str, size: float = 8):
        """阀门符号:两个相对小三角(白底)。"""
        for sgn in (-1, +1):
            ax.add_patch(Polygon([(cx + sgn*size, cy - size*0.7),
                                  (cx, cy),
                                  (cx + sgn*size, cy + size*0.7)],
                                 closed=True, facecolor='white',
                                 edgecolor=color, lw=1.4))

    def _two_line_box(self, ax, x_anchor: float, y_center: float,
                      line1: str, line2: str, color: str, box_side: str,
                      color2: Optional[str] = None,
                      fs1: float = 10, fs2: float = 9):
        """把两行数据装进同一个圆角框,彻底避免上下两行各自带框/裸文字相互
        重叠的问题(用户反馈)。line1 加粗 color;line2 用 color2(默认次要灰)。

        box_side='left'  → 框贴在 x_anchor 左侧(框右缘 = x_anchor);
        box_side='right' → 框贴在 x_anchor 右侧(框左缘 = x_anchor)。
        框宽按较长一行的字符数估算(P/ṁ/T 均为短数字串,够用)。
        """
        c2 = color2 if color2 is not None else self.p.muted
        bold2 = color2 is not None       # 若指定了 color2(如温度紫),则也加粗
        maxlen = max(len(line1), len(line2))
        bw = maxlen * fs1 * 0.60 + 18.0
        bh = fs1 + fs2 + 17.0
        if box_side == 'left':
            bx, tx, ha = x_anchor - bw, x_anchor - 9, 'right'
        else:
            bx, tx, ha = x_anchor, x_anchor + 9, 'left'
        ax.add_patch(FancyBboxPatch((bx, y_center - bh/2), bw, bh,
                                    boxstyle="round,pad=0,rounding_size=6",
                                    facecolor=self.p.label_bg,
                                    edgecolor='#cbd5e1', lw=1.0, zorder=4))
        ax.text(tx, y_center - fs2*0.62, line1, ha=ha, va='center',
                fontsize=fs1, fontweight='bold', color=color, zorder=5)
        ax.text(tx, y_center + fs1*0.62, line2, ha=ha, va='center',
                fontsize=fs2, fontweight=('bold' if bold2 else 'normal'),
                color=c2, zorder=5)

    # ── 离心泵 / 涡轮 剖面工程符号(参考 Raptor 2 schematic 风格)──────

    def _draw_centrifugal_impeller(self, ax, cx, cy, r, fill, stroke,
                                   n_blades=7, zorder=3):
        """离心泵叶轮正视剖面:蜗壳圆 + 背弯叶片(对数螺旋) + 轮毂。"""
        # 蜗壳外圈(略大的浅色圆,模拟 volute 集流环)
        ax.add_patch(Circle((cx, cy), r + 5,
                            facecolor=self.p.shroud, edgecolor=stroke,
                            lw=1.6, zorder=zorder))
        # 叶轮主圆
        ax.add_patch(Circle((cx, cy), r,
                            facecolor=fill, edgecolor=stroke,
                            lw=1.8, zorder=zorder + 0.2))
        # 背弯叶片:从轮毂 r_hub 到外缘 r,角度随半径后掠 β
        r_hub = r * 0.28
        beta  = math.radians(55.0)   # 后掠角
        for i in range(n_blades):
            th0 = 2.0 * math.pi * i / n_blades
            xs, ys = [], []
            for t in [k / 10.0 for k in range(11)]:
                rr = r_hub + (r - r_hub) * t
                th = th0 - beta * t
                xs.append(cx + rr * math.cos(th))
                ys.append(cy + rr * math.sin(th))
            ax.plot(xs, ys, color=stroke, lw=1.3, solid_capstyle='round',
                    zorder=zorder + 0.4)
        # 轮毂(中心实心小圆 = 轴)
        ax.add_patch(Circle((cx, cy), r_hub * 0.7,
                            facecolor=stroke, edgecolor=stroke,
                            zorder=zorder + 0.6))

    def _draw_turbine_rotor(self, ax, cx, cy, hw, hh, fill, stroke, zorder=3):
        """轴流涡轮转子剖面:转盘 + 上下两排叶栅(小平行四边形)。"""
        # 转盘主体(梯形,底略宽)
        ax.add_patch(Polygon([(cx - hw, cy - hh), (cx + hw, cy - hh * 0.7),
                              (cx + hw, cy + hh * 0.7), (cx - hw, cy + hh)],
                             closed=True, facecolor=fill,
                             edgecolor=stroke, lw=2.2, zorder=zorder))
        # 叶栅:顶部一排 + 底部一排倾斜叶片(平行四边形)
        n = 9
        bw = (2 * hw - 10) / n          # 每片宽
        skew = 4.0                       # 倾斜量
        for row_y, sgn in [(cy - hh + 5, +1), (cy + hh - 5, -1)]:
            for k in range(n):
                bx = cx - hw + 5 + k * bw
                ax.add_patch(Polygon([
                    (bx,            row_y),
                    (bx + bw * 0.7, row_y),
                    (bx + bw * 0.7 + sgn * skew, row_y - sgn * 7),
                    (bx + sgn * skew,            row_y - sgn * 7),
                ], closed=True, facecolor='white', edgecolor=stroke,
                   lw=0.7, zorder=zorder + 0.3))


# ─────────────────────────────────────────────────────────
# 燃气发生器循环拓扑渲染器(V20):复用 FFSC 渲染器的全部绘图原语,
# 只重排循环布局 —— 单 GG + 涡轮(单轴/双轴) + 开式排气喷管。
# ─────────────────────────────────────────────────────────

class GGTopologyRenderer(FFSCTopologyRenderer):
    """燃气发生器开式循环拓扑图渲染器(TQ-12 类)。

    布局(单轴):
        泵/涡轮同列一根横轴(y=145):CH4 泵(260) — 涡轮(590) — LOX 泵(920)
        燃气发生器在轴上方(590, 75),两股小流量从泵引出向上供入;
        GG 燃气向下驱动涡轮,排气向右下经小膨胀比排气喷管直排(开式)。
        主路:CH4 → 再生冷却 → 主燃室左壁;LOX → 主燃室右壁(液-液喷注)。
    双轴:GG 燃气并联分流至两台涡轮,各自同轴驱动一侧泵。
    """

    GG_X = 590          # 燃气发生器 / 单轴涡轮中心
    Y_SHAFT = 145       # 泵-涡轮公共轴线(同 Y_PUMP)
    Y_GG = 75           # GG 中心
    EXH_X = 1030        # 排气喷管中心
    TURB_OX_X = 750     # 双轴:LOX 侧涡轮
    TURB_FUEL_X = 430   # 双轴:CH4 侧涡轮

    def _is_dual(self) -> bool:
        return float(self.s.get("gg_layout_dual", 0.0) or 0.0) >= 0.5

    # ── 标题 ─────────────────────────────────────────────
    def _draw_title(self, ax):
        layout_txt = "双轴(燃气分流)" if self._is_dual() else "单轴(单涡轮双泵)"
        ax.text(20, 22,
                "液氧甲烷燃气发生器开式循环拓扑 / LOX-CH4 Gas-Generator (Open) Cycle",
                fontsize=14, fontweight='bold', color=self.p.text)
        ax.text(20, 42,
                f"matplotlib 渲染 · 数据驱动 · {layout_txt} · 涡轮排气直排(不进主燃室)",
                fontsize=9, color=self.p.muted)

    # ── 传动轴:横向虚线(泵-涡轮同轴) ─────────────────────
    def _draw_internal_shafts(self, ax):
        y = self.Y_SHAFT
        if self._is_dual():
            segs = [(self.CH4_X, self.TURB_FUEL_X), (self.TURB_OX_X, self.LOX_X)]
        else:
            segs = [(self.CH4_X, self.LOX_X)]
        for x0, x1 in segs:
            ax.plot([x0, x1], [y, y], color=self.p.shaft, lw=2.4,
                    linestyle=(0, (5, 3)), zorder=0.5)

    # ── FFSC 交叉流不存在 ────────────────────────────────
    def _draw_cross_feeds(self, ax):
        pass

    # ── GG 本体 + 两股小流量供应 ─────────────────────────
    def _draw_preburners(self, ax):
        cx, cy = self.GG_X, self.Y_GG
        # 供应管(沿用 FFSC 交叉流的视觉习惯:从泵体引出、翻越顶部)
        ch4_m = self.s.get("gg_mdot_fuel_kg_s")
        lox_m = self.s.get("gg_mdot_ox_kg_s")
        ch4_text = (f"少量 CH4: {float(ch4_m):.2f} kg/s"
                    if ch4_m is not None else "少量 CH4 → GG")
        lox_text = (f"少量 LOX: {float(lox_m):.2f} kg/s"
                    if lox_m is not None else "少量 LOX → GG")
        self._curved_pipe(ax, [(320, 145), (320, 62), (cx - 85, 62)],
                          color=self.p.ch4_stroke, lw=2.5, corner_r=14)
        self._valve(ax, 320, 145, self.p.ch4_stroke)
        ax.text(386, 50, ch4_text, fontsize=8, fontweight='bold',
                color=self.p.ch4_stroke,
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))
        self._curved_pipe(ax, [(860, 145), (860, 88), (cx + 85, 88)],
                          color=self.p.lox_stroke, lw=2.5, corner_r=14)
        self._valve(ax, 860, 145, self.p.lox_stroke)
        ax.text(792, 108, lox_text, fontsize=8, fontweight='bold',
                color=self.p.lox_stroke, ha='right',
                bbox=dict(boxstyle='round,pad=0.3',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))

        # GG 盒体(富燃配色)
        ax.add_patch(FancyBboxPatch((cx - 85, cy - 27), 170, 54,
                                    boxstyle="round,pad=0,rounding_size=10",
                                    facecolor=self.p.fr_gas,
                                    edgecolor=self.p.fr_stroke, lw=2.5, zorder=2.5))
        ax.add_patch(Rectangle((cx - 85, cy - 27), 170, 6,
                               facecolor=self.p.hot_top,
                               edgecolor=self.p.fr_stroke, lw=1.2, zorder=2.6))
        ax.text(cx, cy - 1, "燃气发生器(富燃)", ha='center', va='center',
                fontsize=10, fontweight='bold', color=self.p.text, zorder=4)
        ax.text(cx, cy + 15, "Gas Generator", ha='center', va='center',
                fontsize=8, color=self.p.muted, zorder=4)

        # P/T 信息框(左侧,下移避开 CH4 供应管标签) + O/F、流量分数(其下)
        self._two_line_box(ax, cx - 94, cy + 10,
                           self._bar("gg_pressure_Pa"),
                           self._K("gg_T_combustion_K"),
                           color=self.p.pressure, color2=self.p.temp,
                           box_side='left')
        mr = self.s.get("gg_mr")
        frac = self.s.get("gg_fraction")
        parts = []
        if mr is not None:
            parts.append(f"O/F = {float(mr):.3f}")
        if frac is not None:
            parts.append(f"α_gg = {float(frac)*100:.1f}%")
        if parts:
            ax.text(cx - 94, cy + 42, "  ".join(parts), ha='right', va='center',
                    fontsize=9, color=self.p.muted)

    # ── 涡轮(单轴 1 台 / 双轴 2 台) + GG 燃气管 ───────────
    def _draw_turbines(self, ax):
        cy = self.Y_SHAFT
        pi_t = self._fmt("gg_turbine_pi_t", "π_t = {:.1f}")
        if self._is_dual():
            split = float(self.s.get("gg_split_to_ox_shaft", 0.5) or 0.5)
            # GG → 两台涡轮的分流管
            self._curved_pipe(ax, [(self.GG_X - 30, 102), (self.GG_X - 30, 112),
                                   (self.TURB_FUEL_X, 112), (self.TURB_FUEL_X, 117)],
                              color=self.p.fr_stroke, lw=4, corner_r=10)
            self._curved_pipe(ax, [(self.GG_X + 30, 102), (self.GG_X + 30, 112),
                                   (self.TURB_OX_X, 112), (self.TURB_OX_X, 117)],
                              color=self.p.fr_stroke, lw=4, corner_r=10)
            specs = [
                (self.TURB_FUEL_X, "涡轮(CH4 轴)", f"{(1-split)*100:.0f}% 燃气",
                 "fuel_turbine_power_MW"),
                (self.TURB_OX_X, "涡轮(LOX 轴)", f"{split*100:.0f}% 燃气",
                 "ox_turbine_power_MW"),
            ]
        else:
            # GG → 单涡轮(正下方)
            self._curved_pipe(ax, [(self.GG_X, 102), (self.GG_X, 117)],
                              color=self.p.fr_stroke, lw=5, corner_r=8)
            specs = [(self.GG_X, "涡轮 / Turbine", None, "gg_turbine_power_MW")]

        for cx, l1, l2, pwr_key in specs:
            self._draw_turbine_rotor(ax, cx, cy, hw=58, hh=26,
                                     fill=self.p.fr_gas, stroke=self.p.fr_stroke,
                                     zorder=2.5)
            ax.text(cx, cy - 2, l1, ha='center', va='center',
                    fontsize=10, fontweight='bold', color=self.p.text, zorder=4)
            if l2:
                ax.text(cx, cy + 13, l2, ha='center', va='center',
                        fontsize=8, color=self.p.muted, zorder=4)
            ax.text(cx, cy + 50, "P = " + self._MW(pwr_key),
                    ha='center', va='center',
                    fontsize=10, fontweight='bold', color=self.p.fr_stroke,
                    bbox=dict(boxstyle='round,pad=0.3',
                              facecolor=self.p.label_bg,
                              edgecolor=self.p.fr_stroke, lw=1.3))
        # 涡轮压比芯片(公共)
        ax.text(self.GG_X + 96, cy - 34, pi_t + "(开式大压比)",
                fontsize=8, fontweight='bold', color=self.p.fr_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))

    # ── 涡轮排气 → 排气喷管(直排,开式核心特征) ────────────
    def _draw_turbine_exhausts(self, ax):
        ex, ey = self.EXH_X, 300     # 排气喷管入口中心
        y_duct = 208                 # 汇流横管高度
        if self._is_dual():
            self._curved_pipe(ax, [(self.TURB_FUEL_X, 171), (self.TURB_FUEL_X, y_duct),
                                   (ex, y_duct), (ex, ey - 42)],
                              color=self.p.fr_stroke, lw=5, corner_r=16)
            self._curved_pipe(ax, [(self.TURB_OX_X, 171), (self.TURB_OX_X, y_duct)],
                              color=self.p.fr_stroke, lw=5, corner_r=16, arrow=False)
        else:
            self._curved_pipe(ax, [(self.GG_X, 171), (self.GG_X, y_duct),
                                   (ex, y_duct), (ex, ey - 42)],
                              color=self.p.fr_stroke, lw=5, corner_r=16)
        ax.text(self.GG_X + 60, y_duct - 10,
                f"涡轮排气 {self._kg('gg_mdot_kg_s')}(不进主燃室)",
                fontsize=8, fontweight='bold', color=self.p.fr_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))

        # 小膨胀比排气喷管(锥形)
        ax.add_patch(Polygon([(ex - 14, ey - 42), (ex + 14, ey - 42),
                              (ex + 30, ey + 20), (ex - 30, ey + 20)],
                             closed=True, facecolor=self.p.fr_gas,
                             edgecolor=self.p.fr_stroke, lw=2.2, zorder=3))
        ax.text(ex, ey + 34, "排气喷管(直排)", ha='center', va='center',
                fontsize=8, fontweight='bold', color=self.p.text)
        ax.text(ex, ey + 48, "GG exhaust", ha='center', va='center',
                fontsize=7, color=self.p.muted)
        for off in (-16, 0, 16):
            ax.annotate("", xy=(ex + off, ey + 88), xytext=(ex + off, ey + 58),
                        arrowprops=dict(arrowstyle="-|>,head_width=0.4,head_length=0.6",
                                        color=self.p.exhaust, lw=3, mutation_scale=12))
        f_gg = self.s.get("thrust_gg_exhaust_N")
        isp_gg = self.s.get("Isp_gg_exhaust_s")
        if f_gg is not None:
            ax.text(ex, ey + 104,
                    f"F = {float(f_gg)/1e3:.1f} kN"
                    + (f" | Isp = {float(isp_gg):.0f} s" if isp_gg is not None else ""),
                    ha='center', va='center', fontsize=8, fontweight='bold',
                    color=self.p.fr_stroke)

    # ── 主路管线:CH4 → 再生冷却 → 主燃室;LOX → 主燃室 ────
    def _draw_main_pipes(self, ax):
        cx = self.CHAMBER_X
        ch_hw = self.ch_hw
        wall_y = self.ch_top + 30
        # CH4 主路:泵出口 → 冷却套入口
        self._curved_pipe(ax, [(320, 172), (320, 235), (380, 235), (380, 245)],
                          color=self.p.ch4_stroke, lw=4, corner_r=14)
        ax.text(330, 215, "高压 CH4(主路)", fontsize=8, fontweight='bold',
                color=self.p.ch4_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))
        # 冷却套出口 → 主燃室左壁(液态喷注)
        self._curved_pipe(ax, [(380, 295), (380, 460),
                               (cx - ch_hw - 14, 460),
                               (cx - ch_hw - 14, wall_y), (cx - ch_hw, wall_y)],
                          color=self.p.ch4_stroke, lw=5, corner_r=16)
        ax.text(400, 420, f"冷却后 CH4 {self._kg('mdot_coolant_kg_s')}",
                fontsize=8, fontweight='bold', color=self.p.ch4_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))
        # LOX 主路:泵出口 → 主燃室右壁
        self._curved_pipe(ax, [(860, 172), (860, 460),
                               (cx + ch_hw + 14, 460),
                               (cx + ch_hw + 14, wall_y), (cx + ch_hw, wall_y)],
                          color=self.p.lox_stroke, lw=5, corner_r=16)
        ax.text(800, 300, "主路 LOX", fontsize=8, fontweight='bold',
                color=self.p.lox_stroke,
                bbox=dict(boxstyle='round,pad=0.25',
                          facecolor=self.p.label_bg, edgecolor='none', alpha=0.92))

    # ── 右侧信息栏(GG 版) ────────────────────────────────
    def _draw_sidebar(self, ax):
        x0, w = self.SIDEBAR_X, self.SIDEBAR_W
        TFS, KFS, VFS = self._SB_TITLE_FS, self._SB_KEY_FS, self._SB_VAL_FS
        ROW, PAD, HEAD = self._SB_ROW, self._SB_PAD, self._SB_HEAD

        def _panel(y_top: float, title: str, n: int) -> float:
            box_h = HEAD + n * ROW + 12
            ax.add_patch(FancyBboxPatch((x0, y_top), w, box_h,
                                        boxstyle="round,pad=0,rounding_size=5",
                                        facecolor='white', edgecolor='#cbd5e1', lw=1.2))
            ax.text(x0 + PAD, y_top + 20, title, fontsize=TFS,
                    fontweight='bold', color=self.p.text)
            return box_h

        def _kv(y_top: float, i: int, k: str, v: str):
            yy = y_top + HEAD + 13 + i * ROW
            ax.text(x0 + PAD, yy, k, fontsize=KFS, color=self.p.muted, va='center')
            ax.text(x0 + w - PAD, yy, v, fontsize=VFS, color=self.p.text,
                    ha='right', fontweight='bold', va='center')

        frac = self.s.get("gg_fraction")
        rows = [
            ("总流量",          self._kg("mdot_total_kg_s")),
            ("主燃室通流",       self._kg("mdot_chamber_kg_s")),
            ("GG 流量",         self._kg("gg_mdot_kg_s")
                                + (f" ({float(frac)*100:.1f}%)" if frac is not None else "")),
            ("GG 混合比 O/F",   self._f3("gg_mr")),
            ("GG 出口温度",      self._K("gg_T_combustion_K")),
            ("涡轮压比 π_t",     self._fmt("gg_turbine_pi_t", "{:.1f}")),
            ("LOX 泵出口压力",   self._mpa("lox_pump_out_pressure_Pa")),
            ("CH4 泵出口压力",   self._mpa("fuel_pump_out_pressure_Pa")),
            ("主燃烧室 Pc",      self._mpa("chamber_pressure_Pa")),
        ]
        y0 = 64
        h = _panel(y0, "当前工况摘要", len(rows))
        for i, (k, v) in enumerate(rows):
            _kv(y0, i, k, v)

        legend = [
            (self.p.lox_liq, self.p.lox_stroke, "液态氧 LOX"),
            (self.p.ch4_liq, self.p.ch4_stroke, "液态甲烷 CH4"),
            (self.p.fr_gas,  self.p.fr_stroke,  "GG 富燃燃气(开式排气)"),
            (self.p.hot_top, self.p.hot_stroke, "主燃烧室热燃气"),
        ]
        y1 = y0 + h + 16
        _panel(y1, "图例", len(legend))
        for i, (fill, stroke, name) in enumerate(legend):
            yy = y1 + HEAD + 13 + i * ROW
            ax.add_patch(Rectangle((x0 + PAD, yy - 9), 28, 18,
                                   facecolor=fill, edgecolor=stroke, lw=1.6))
            ax.text(x0 + PAD + 40, yy, name, fontsize=KFS,
                    color=self.p.text, va='center')
        h = HEAD + len(legend) * ROW + 12

        thr_str = self._kN("target_thrust_N")
        if thr_str == "—" and self.inputs_thrust is not None:
            thr_str = f"{float(self.inputs_thrust)/1e3:.0f} kN"
        pc_str = self._mpa("chamber_pressure_Pa")
        if pc_str == "—" and self.inputs_pc is not None:
            pc_str = f"{float(self.inputs_pc)/1e6:.2f} MPa"
        layout_txt = "双轴分流" if self._is_dual() else "单轴双泵"
        spec_rows = [
            ("循环 Cycle",        "GG 开式"),
            ("轴系布局",          layout_txt),
            ("推进剂",            "LOX + CH4"),
            ("设计推力 Thrust",   thr_str),
            ("真空比冲 Isp_vac",  self._fmt("Isp_vac_s", "{:.1f} s")),
            ("比冲代价 ΔIsp",     self._fmt("Isp_gg_penalty_s", "{:.1f} s")),
            ("燃烧室压力 Pc",     pc_str),
            ("主室混合比 O/F",    self._f3("chamber_mixture_ratio")),
        ]
        y2 = y1 + h + 16
        _panel(y2, "技术规格", len(spec_rows))
        for i, (k, v) in enumerate(spec_rows):
            _kv(y2, i, k, v)


# ─────────────────────────────────────────────────────────
# 独立调试入口:python -c "from topology_drawing import demo; demo()"
# ─────────────────────────────────────────────────────────

def demo(out_path: str = "topology_demo.png"):
    """用一组合理的虚拟数据渲染一张图,用于单独调试样式(不依赖 GUI/Cycle)。"""
    summary = dict(
        mdot_total_kg_s=850.5, mdot_fuel_kg_s=184.9, mdot_ox_kg_s=665.6,
        mdot_fuel_to_ox_preburner_kg_s=10.7,
        mdot_ox_to_fuel_preburner_kg_s=22.6,
        fuel_pump_out_pressure_Pa=7.46e7, lox_pump_out_pressure_Pa=6.22e7,
        fuel_pump_power_MW=36.8, ox_pump_power_MW=38.7,
        fuel_turbine_power_MW=36.8, ox_turbine_power_MW=38.7,
        fuel_preburner_pressure_Pa=8.5e7, ox_preburner_pressure_Pa=8.0e7,
        fuel_preburner_T_combustion_K=849, ox_preburner_T_combustion_K=899,
        fuel_preburner_mr=0.130, ox_preburner_mr=60.0,
        fuel_turbine_exhaust_pressure_Pa=3.61e7,
        ox_turbine_exhaust_pressure_Pa=4.10e7,
        fuel_turbine_exhaust_T_K=805, ox_turbine_exhaust_T_K=846,
        chamber_pressure_Pa=3.3e7, chamber_temperature_K=3822,
        Isp_s=329.7, thrust_N=2.75e6, target_thrust_N=2.75e6,
        effective_injector_OF=3.60,
    )
    geometry = dict(
        chamber=dict(radius_m=0.194, length_m=0.40),
        nozzle=dict(throat_radius_m=0.123, exit_radius_m=0.727,
                    convergent_length_m=0.173, nozzle_length_m=1.80,
                    initial_expansion_angle_deg=34.3, exit_angle_deg=9.0,
                    contour_x=[-0.173, -0.05, 0.0, 0.05, 0.4, 0.8, 1.3, 1.8],
                    contour_r=[0.194, 0.135, 0.123, 0.155, 0.35, 0.50, 0.62, 0.727]),
    )
    fig = FFSCTopologyRenderer(summary, geometry).render(dpi=110)
    fig.savefig(out_path, dpi=130, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    print(f"Saved: {out_path}")
    return fig


if __name__ == "__main__":
    demo()
