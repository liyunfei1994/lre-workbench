"""
ui_drawings.preburners —— 预燃室 的二维剖面图 / 3D 模型 / 估算说明（从 ui_drawings.py 拆出）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from ._common import *  # noqa: F401,F403


def _fig_preburners(geom: Dict[str, Any]) -> Figure:
    fig = _make_figure(9.6, 4.6)
    axes = fig.subplots(1, 2)
    for ax, key, title, color in [
        (axes[0], "ox_preburner", "富氧预燃室 O-rich preburner", "#a855f7"),
        (axes[1], "fuel_preburner", "富燃预燃室 Fuel-rich preburner", "#f472b6"),
    ]:
        pb = geom.get(key, {}) or {}
        D = _num(pb.get("diameter_m"), 0.20)
        L = _num(pb.get("length_m"), 0.35)
        r = D / 2

        # 主筒
        ax.add_patch(Rectangle((0, -r), L, 2 * r, color="#fef3c7", alpha=0.75,
                               ec="#1f2937", lw=1.2))
        # 喷注面
        ax.add_patch(Rectangle((0, -r), 0.04 * L, 2 * r, color=INJECTOR_FILL,
                               alpha=0.95, ec="#1f2937", lw=1.0))
        # 出口缩口（导向涡轮）
        ax.add_patch(Polygon([
            (L, r), (L * 1.12, r * 0.55),
            (L * 1.12, -r * 0.55), (L, -r),
        ], color=color, alpha=0.5, ec="#1f2937"))

        # 燃气流向箭头
        ax.annotate("", xy=(L * 1.10, 0), xytext=(L * 0.30, 0),
                    arrowprops=dict(arrowstyle="-|>", color="#dc2626", lw=2))

        # 标注
        _dim_arrow(ax, 0, -r * 1.45, L, -r * 1.45,
                   f"L = {L:.3f} m", fontsize=9)
        _dim_arrow(ax, -0.04 * L, -r, -0.04 * L, r,
                   f"D = {D:.3f} m", fontsize=9)

        ax.text(0.02 * L, r * 1.20, "喷注面板", fontsize=8, color="#7c2d12")
        ax.text(L * 1.05, r * 1.10, "→ 涡轮", fontsize=8, color="#7c2d12")

        V = _num(pb.get("volume_m3"), 0.01)
        ax.text(0.5, -0.18,
                f"{title}\nV = {V*1000:.2f} L  ·  L* ≈ {L/D*D:.2f} m",
                transform=ax.transAxes, ha="center", va="top",
                fontsize=9, color="#1f2937",
                bbox=dict(boxstyle="round,pad=0.4", fc="#f8fafc", ec="#cbd5e1"))

        ax.set_aspect("equal")
        ax.set_xlim(-0.10 * L, L * 1.30)
        ax.set_ylim(-r * 1.85, r * 1.85)
        ax.axis("off")
        ax.set_title(title, fontsize=10, fontweight="bold", color="#1f2937")
    return fig

def _explain_preburners(geom: Dict[str, Any]) -> str:
    return (_EXPLAIN_CSS + "<body><p><b>预燃室几何(简化估算)</b>　设截面燃气速度 ~30 m/s 定截面积,"
            "用 L*≈0.6 m 定容积,直径/长度再做工程钳位。</p>"
            "<table width='100%'><tr><th>参数</th><th>公式</th><th>物理意义</th></tr>"
            "<tr><td><b>截面积 A</b></td><td><code>A=ṁ/(ρ_hot·30)</code></td><td>按燃气轴向速度 ~30 m/s</td></tr>"
            "<tr><td><b>直径 D</b></td><td><code>D=2√(A/π)</code>, 钳 0.12–0.50 m</td><td>预燃室内径</td></tr>"
            "<tr><td><b>长度 L</b></td><td><code>L=L*·A/A=L*</code>, 钳 0.10–0.50 m</td><td>由 L*≈0.6 m</td></tr>"
            "</table><p class='src'>富氧/富燃两台预燃室分别驱动液氧/甲烷涡轮(全流量分级燃烧)。</p></body>")


# ── 友好别名 ──
fig = _fig_preburners
explain = _explain_preburners
