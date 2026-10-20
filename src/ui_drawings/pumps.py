"""
ui_drawings.pumps —— 涡轮泵 的二维剖面图 / 3D 模型 / 估算说明（从 ui_drawings.py 拆出）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from ._common import *  # noqa: F401,F403


def _fig_pumps(geom: Dict[str, Any]) -> Figure:
    fig = _make_figure(9.6, 5.0)
    axes = fig.subplots(1, 2)
    for ax, key, title, color in [
        (axes[0], "ox_pump", "液氧泵 LOX pump", "#2563eb"),
        (axes[1], "fuel_pump", "甲烷泵 CH4 pump", "#dc2626"),
    ]:
        p = geom.get(key, {}) or {}
        D = _num(p.get("impeller_diameter_m"), 0.20)
        N = _num(p.get("shaft_speed_rpm"), 20000)
        stages = max(1, int(_num(p.get("num_stages"), 1)))
        u_tip = _num(p.get("tip_speed_ms"), 200)
        head = _num(p.get("head_m"), 3000)
        Q = _num(p.get("volume_flow_m3s"), 0.3)
        Ns = _num(p.get("specific_speed"), 40)
        ptype = p.get("pump_type", "centrifugal")

        # 子午面剖面：进口诱导轮 + 离心叶轮 + 扩压器/蜗壳
        r_inducer_in = 0.18 * D / 2
        r_inducer_out = 0.42 * D / 2
        r_impeller_in = 0.42 * D / 2
        r_impeller_out = D / 2 * 0.92
        L_inducer = 0.18 * D
        L_impeller = 0.22 * D

        # 入口管
        ax.add_patch(Rectangle((-0.05, -r_inducer_in), 0.05 + L_inducer, 2 * r_inducer_in,
                               color="#dbeafe", alpha=0.7))
        # 诱导轮（梯形）
        ax.add_patch(Polygon([
            (0, r_inducer_in), (L_inducer, r_inducer_out),
            (L_inducer, -r_inducer_out), (0, -r_inducer_in)
        ], color=color, alpha=0.45, ec="#1f2937", lw=1.0))
        # 叶轮
        x_imp = L_inducer
        ax.add_patch(Polygon([
            (x_imp, r_impeller_in), (x_imp + L_impeller * 0.3, r_impeller_out),
            (x_imp + L_impeller, r_impeller_out),
            (x_imp + L_impeller, -r_impeller_out),
            (x_imp + L_impeller * 0.3, -r_impeller_out),
            (x_imp, -r_impeller_in),
        ], color=color, alpha=0.65, ec="#1f2937", lw=1.0))
        # 叶片暗示线
        for k in range(1, 5):
            frac = k / 5
            xb = x_imp + L_impeller * 0.3 * frac
            rb_in = r_impeller_in + (r_impeller_out - r_impeller_in) * frac
            ax.plot([xb, xb + 0.06 * D], [rb_in, r_impeller_out * 0.98],
                    color="#1f2937", lw=0.8)
            ax.plot([xb, xb + 0.06 * D], [-rb_in, -r_impeller_out * 0.98],
                    color="#1f2937", lw=0.8)

        # 扩压器/蜗壳
        x_vol = x_imp + L_impeller
        ax.add_patch(Polygon([
            (x_vol, r_impeller_out), (x_vol + 0.05 * D, r_impeller_out * 1.18),
            (x_vol + 0.05 * D, -r_impeller_out * 1.18), (x_vol, -r_impeller_out)
        ], color="#cbd5e1", alpha=0.65, ec="#1f2937"))

        # 轴
        ax.add_patch(Rectangle((-0.05, -0.02 * D), x_vol + 0.10 * D, 0.04 * D,
                               color="#6b7280"))

        # 尺寸
        _dim_arrow(ax, x_imp, -r_impeller_out * 1.4, x_imp + L_impeller, -r_impeller_out * 1.4,
                   f"L_imp ≈ {L_impeller:.3f} m", fontsize=8)
        _dim_arrow(ax, x_vol + 0.07 * D, -r_impeller_out, x_vol + 0.07 * D, r_impeller_out,
                   f"D = {D:.3f} m", fontsize=8)

        info = (
            f"{title}\n"
            f"D_impeller = {D:.3f} m  ·  N ≈ {N:,.0f} rpm  ·  {stages} 级\n"
            f"叶尖速度 u = {u_tip:.0f} m/s  ·  扬程 H ≈ {head:,.0f} m  ·  Q = {Q:.3f} m³/s\n"
            f"比转速 Ns ≈ {Ns:.1f}  ·  类型: {ptype}"
        )
        ax.text(0.5, -0.18, info, transform=ax.transAxes, ha="center", va="top",
                fontsize=8.5, color="#1f2937",
                bbox=dict(boxstyle="round,pad=0.4", fc="#f8fafc", ec="#cbd5e1"))

        ax.set_aspect("equal")
        ax.set_xlim(-0.10, x_vol + 0.16 * D)
        ax.set_ylim(-r_impeller_out * 1.7, r_impeller_out * 1.7)
        ax.axis("off")
        ax.set_title(title, fontsize=10, fontweight="bold", color="#1f2937")
    return fig

def _explain_pumps(geom: Dict[str, Any]) -> str:
    return (_EXPLAIN_CSS + "<body><p><b>涡轮泵几何(校核式)</b>　转速由输入指定(与同轴涡轮一致),"
            "由压头系数 ψ 反算叶尖速度,再得叶轮直径;并算比转速 Ns 判定泵型。</p>"
            "<table width='100%'><tr><th>参数</th><th>公式</th><th>物理意义</th></tr>"
            "<tr><td><b>体积流量 Q</b></td><td><code>Q=ṁ/ρ</code></td><td>泵送的体积流率</td></tr>"
            "<tr><td><b>扬程 H</b></td><td><code>H=Δp/(ρg)</code></td><td>泵升压折算成的液柱高</td></tr>"
            "<tr><td><b>叶尖速度 u</b></td><td><code>u=√(g·H_级/ψ)</code></td><td>ψ=单级压头系数(0.45–0.55)</td></tr>"
            "<tr><td><b>叶轮直径 D</b></td><td><code>D=2u/ω</code></td><td>ω 由转速换算</td></tr>"
            "<tr><td><b>比转速 Ns</b></td><td><code>N√Q/H_级^0.75</code></td><td>&lt;30 多级离心,30–80 离心,&gt;200 轴流</td></tr>"
            "</table><p class='src'>依据：NASA SP-8109/8110 涡轮泵设计准则。"
            "(<code>geometry.py: _estimate_pump</code>)</p></body>")


# ── 友好别名 ──
fig = _fig_pumps
explain = _explain_pumps
