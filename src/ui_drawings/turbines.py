"""
ui_drawings.turbines —— 涡轮 的二维剖面图 / 3D 模型 / 估算说明（从 ui_drawings.py 拆出）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from ._common import *  # noqa: F401,F403


def _fig_turbines(geom: Dict[str, Any]) -> Figure:
    fig = _make_figure(9.6, 5.0)
    axes = fig.subplots(1, 2)
    for ax, key, title, color in [
        (axes[0], "ox_turbine", "富氧涡轮 O-rich turbine", "#a855f7"),
        (axes[1], "fuel_turbine", "富燃涡轮 Fuel-rich turbine", "#f472b6"),
    ]:
        t = geom.get(key, {}) or {}
        D = _num(t.get("rotor_diameter_m"), 0.20)
        h_blade = _num(t.get("blade_height_mm"), 25) / 1000.0
        stages = max(1, int(_num(t.get("num_stages"), 2)))
        uoc = _num(t.get("u_over_c"), 0.5)
        N = _num(t.get("shaft_speed_rpm"), 20000)

        # 子午面：多级（stages）轴流涡轮
        r_hub = D / 2 - h_blade
        r_tip = D / 2
        L_stage = h_blade * 1.8
        L_total = L_stage * stages

        # 流道（hub & casing）
        ax.fill_between([0, L_total], [r_tip, r_tip], [r_tip * 1.18, r_tip * 1.18],
                        color=OUTER_WALL, alpha=0.7)
        ax.fill_between([0, L_total], [-r_tip * 1.18, -r_tip * 1.18], [-r_tip, -r_tip],
                        color=OUTER_WALL, alpha=0.7)
        ax.fill_between([0, L_total], [-r_hub, -r_hub], [r_hub, r_hub],
                        color="#94a3b8", alpha=0.6)
        # 流道气体
        ax.fill_between([0, L_total], [r_hub, r_hub], [r_tip, r_tip],
                        color="#fde68a", alpha=0.45)
        ax.fill_between([0, L_total], [-r_tip, -r_tip], [-r_hub, -r_hub],
                        color="#fde68a", alpha=0.45)

        # 叶片简化矩形（每级一对：静子+动子）
        for s in range(stages):
            x0 = s * L_stage
            # 静子
            ax.add_patch(Rectangle((x0 + L_stage * 0.10, r_hub),
                                   L_stage * 0.18, h_blade,
                                   color="#475569", alpha=0.85))
            ax.add_patch(Rectangle((x0 + L_stage * 0.10, -r_tip),
                                   L_stage * 0.18, h_blade,
                                   color="#475569", alpha=0.85))
            # 动子
            ax.add_patch(Rectangle((x0 + L_stage * 0.55, r_hub),
                                   L_stage * 0.20, h_blade,
                                   color=color, alpha=0.85))
            ax.add_patch(Rectangle((x0 + L_stage * 0.55, -r_tip),
                                   L_stage * 0.20, h_blade,
                                   color=color, alpha=0.85))
            ax.text(x0 + L_stage * 0.5, r_tip * 1.30, f"Stage {s+1}",
                    fontsize=7, ha="center", color="#374151")

        # 尺寸
        _dim_arrow(ax, L_total + 0.02, -r_tip, L_total + 0.02, r_tip,
                   f"D = {D:.3f} m", fontsize=8)
        _dim_arrow(ax, 0, -r_tip * 1.45, L_total, -r_tip * 1.45,
                   f"L = {L_total:.3f} m  ({stages} stages)", fontsize=8)

        info = (
            f"D_rotor = {D:.3f} m  ·  叶高 = {h_blade*1000:.1f} mm  ·  {stages} 级\n"
            f"U/C₀ ≈ {uoc:.2f}  ·  N = {N:,.0f} rpm（与同侧泵共轴）"
        )
        ax.text(0.5, -0.18, info, transform=ax.transAxes, ha="center", va="top",
                fontsize=8.5, color="#1f2937",
                bbox=dict(boxstyle="round,pad=0.4", fc="#f8fafc", ec="#cbd5e1"))

        ax.set_aspect("equal")
        ax.set_xlim(-0.03, L_total + 0.08)
        ax.set_ylim(-r_tip * 1.7, r_tip * 1.7)
        ax.axis("off")
        ax.set_title(title, fontsize=10, fontweight="bold", color="#1f2937")
    return fig

def _explain_turbines(geom: Dict[str, Any]) -> str:
    return (_EXPLAIN_CSS + "<body><p><b>涡轮几何(校核式)</b>　按目标速比 U/C₀ 反算节圆直径,"
            "按 U/C₀ 选级数,按流量连续性估叶高。与同侧泵共轴(同转速)。</p>"
            "<table width='100%'><tr><th>参数</th><th>公式</th><th>物理意义</th></tr>"
            "<tr><td><b>等熵速度 C₀</b></td><td><code>C₀=√(2·Δh_s)</code></td><td>焓降全转动能的理想喷流速度</td></tr>"
            "<tr><td><b>节圆直径 D</b></td><td><code>D=2·(U/C₀)·C₀/ω</code></td><td>U/C₀ 目标 0.50–0.55(反动级)</td></tr>"
            "<tr><td><b>级数</b></td><td>按 U/C₀ 分档</td><td>&lt;0.2 速度复合 3 级,≥0.5 单级</td></tr>"
            "<tr><td><b>叶高 h</b></td><td><code>h=ṁ/(ρ·Cm·πD)</code></td><td>由流量连续性,用真实工质密度</td></tr>"
            "</table><p class='src'>依据：NASA SP-8110 §4.4。"
            "(<code>geometry.py: _estimate_turbine</code>)</p></body>")


# ── 友好别名 ──
fig = _fig_turbines
explain = _explain_turbines
