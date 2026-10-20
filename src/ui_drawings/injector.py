"""
ui_drawings.injector —— 喷注器 的二维剖面图 / 3D 模型 / 估算说明（从 ui_drawings.py 拆出）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from ._common import *  # noqa: F401,F403


def _fig_injector(geom: Dict[str, Any]) -> Figure:
    fig = _make_figure(9.6, 5.0)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.1])
    ax_face = fig.add_subplot(gs[0, 0])
    ax_elem = fig.add_subplot(gs[0, 1])

    inj = geom.get("injector", {}) or {}
    c = geom.get("chamber", {}) or {}
    D = _num(inj.get("face_diameter_m"), _num(c.get("diameter_m"), 0.38))
    N = max(20, int(_num(inj.get("num_elements"), 160)))
    R = D / 2

    # ── 正面阵列 ──────────────────────────────────────
    ax_face.add_patch(Circle((0, 0), R, fill=False, ec="#1e40af", lw=2.2))
    # 外侧"膜冷却"环（贴壁的小一圈）
    ax_face.add_patch(Circle((0, 0), R * 0.97, fill=False, ec="#9ca3af", lw=0.8, ls="--"))

    # 按同心环铺点
    pts = []
    rings = max(4, min(14, int(round(math.sqrt(N) * 0.7))))
    for k in range(1, rings + 1):
        rr = R * 0.92 * k / rings
        m = max(6, int(2 * math.pi * rr / (R / rings * 0.95)))
        for i in range(m):
            a = 2 * math.pi * (i + 0.5 * (k % 2)) / m
            pts.append((rr * math.cos(a), rr * math.sin(a)))
    pts = pts[:N]
    xs, ys = zip(*pts) if pts else ([], [])
    # 同轴元素用两个同心圆表示
    elem_outer = R / max(rings, 1) * 0.32
    elem_inner = elem_outer * 0.55
    for x, y in pts:
        ax_face.add_patch(Circle((x, y), elem_outer, fill=False, ec="#dc2626", lw=0.6))
        ax_face.add_patch(Circle((x, y), elem_inner, color="#2563eb", alpha=0.85))
    ax_face.text(0, -R * 1.15, f"喷注单元 ≈ {N} 个   面板 D = {D:.3f} m\n"
                 "蓝点=富氧热气中心管, 红环=富燃热气环缝", ha="center", fontsize=9,
                 color="#374151")
    ax_face.set_aspect("equal")
    ax_face.set_xlim(-R * 1.25, R * 1.25)
    ax_face.set_ylim(-R * 1.35, R * 1.20)
    ax_face.axis("off")
    ax_face.set_title("喷注面正视图（Front view）", fontsize=10, fontweight="bold")

    # ── 单元剖面（同轴旋流型，气-气）────────────────
    # 比例放大：富氧热气中心管 d_o, 富燃热气环缝 gap, 凹陷 recess
    d_lox = 0.10
    d_post = 0.16
    d_ch4_out = 0.26
    recess = 0.12
    L_post = 0.55

    # 喷注面板
    ax_elem.add_patch(Rectangle((-1.0, 0.42), 2.0, 0.10, color=OUTER_WALL, alpha=0.85))
    # LOX 中心管（蓝）
    ax_elem.add_patch(Rectangle((-d_lox/2, -0.05 - L_post + recess), d_lox, L_post,
                                color="#1d4ed8", alpha=0.9))
    # LOX post 外壁（不锈钢）
    ax_elem.add_patch(Rectangle((-d_post/2, -0.10 - L_post + recess), d_post, L_post + 0.05,
                                fill=False, ec="#1f2937", lw=1.2))
    # CH4 环缝
    ax_elem.add_patch(Rectangle((-d_ch4_out/2, 0.0), d_ch4_out, 0.45,
                                color="#fde68a", alpha=0.5))
    ax_elem.add_patch(Rectangle((d_post/2, 0.0), (d_ch4_out - d_post)/2, 0.45,
                                color="#dc2626", alpha=0.45))
    ax_elem.add_patch(Rectangle((-d_ch4_out/2, 0.0), (d_ch4_out - d_post)/2, 0.45,
                                color="#dc2626", alpha=0.45))

    # 燃烧区
    ax_elem.add_patch(Rectangle((-1.0, -0.65 - L_post + recess),
                                2.0, 0.55 + L_post - recess,
                                color="#fff7ed", alpha=0.4))
    # 旋流混合扩张（示意，气-气非液滴喷雾）
    ax_elem.plot([0, -0.25], [-0.05 - L_post + recess, -0.55],
                 color="#0891b2", lw=1, ls="--")
    ax_elem.plot([0,  0.25], [-0.05 - L_post + recess, -0.55],
                 color="#0891b2", lw=1, ls="--")

    # 标注
    _dim_arrow(ax_elem, -0.55, 0.05, -0.55, -0.10 - L_post + recess,
               f"L_post", fontsize=8)
    _dim_arrow(ax_elem, -d_lox/2, 0.30, d_lox/2, 0.30, "d_o 富氧", fontsize=8)
    _dim_arrow(ax_elem, -d_ch4_out/2, 0.55, d_ch4_out/2, 0.55, "d_f 富燃(外)", fontsize=8)
    ax_elem.text(0.34, -0.05 - L_post + recess - 0.04, "recess", fontsize=8, color="#7c2d12")
    ax_elem.annotate("", xy=(0.30, -0.05 - L_post + recess),
                     xytext=(0.30, -0.05 - L_post),
                     arrowprops=dict(arrowstyle="<->", color="#7c2d12", lw=0.7))

    ax_elem.text(-0.95, -0.55, "气-气旋流混合区\ngas-gas swirl mixing",
                 color="#7c2d12", fontsize=8)
    ax_elem.text(-0.95, 0.62, "蓝=富氧热气, 红=富燃热气(环缝), 黄=喷注面板",
                 color="#374151", fontsize=8)

    ax_elem.set_xlim(-1.05, 1.05)
    ax_elem.set_ylim(-0.95, 0.85)
    ax_elem.set_aspect("equal")
    ax_elem.axis("off")
    ax_elem.set_title("同轴旋流单元剖面 / Coaxial swirl element", fontsize=10, fontweight="bold")
    return fig

def _explain_injector(geom: Dict[str, Any]) -> str:
    inj = geom.get("injector", {}) or {}
    nel = int(_num(inj.get("num_elements"))); d = _num(inj.get("face_diameter_m"))
    sp = _num(inj.get("element_spacing_mm"))
    rows = "".join([
        _row("面板直径", f"{d:.3f} m", "= 燃烧室内径 Dc", "喷注面与燃烧室同径"),
        _row("喷注单元数", f"{nel}", "ṁ_total / (~1 kg/s 每单元)", "同轴旋流单元估算个数"),
        _row("单元间距", f"{sp:.1f} mm", "√(面板面积 / N)", "单元在面板上的平均节距"),
    ])
    return (_EXPLAIN_CSS + "<body><p><b>喷注器几何(气-气同轴旋流式)</b>　FFSC 主喷注器进的是"
            "富氧/富燃两股热气(非液滴雾化)；单元数按总流量估算,面板与燃烧室同径。</p>"
            "<table width='100%'>"
            "<tr><th>参数</th><th>当前值</th><th>公式</th><th>物理意义</th></tr>"
            + rows + "</table><p class='src'>蓝点=富氧热气中心管,红环=富燃热气环缝(图中示意)。</p></body>")


# ── 友好别名 ──
fig = _fig_injector
explain = _explain_injector
