"""
ui_drawings.nozzle —— 喷管 的二维剖面图 / 3D 模型 / 估算说明（从 ui_drawings.py 拆出）。
"""

from __future__ import annotations

import math
from html import escape
from typing import Any, Dict, List, Tuple

from ._common import *  # noqa: F401,F403


def _nozzle_method_note(nozzle: Dict[str, Any]) -> str:
    """Describe saved construction metadata without upgrading historical evidence."""
    method = str(nozzle.get("nozzle_contour_method") or "")
    source = str(nozzle.get("angle_source") or "")
    if not method and not source:
        return "历史轮廓；角度来源未记录"
    if "legacy" in method.lower() or "legacy" in source.lower():
        return "历史经验角度；未验证为优化型面"
    if "explicit" in method.lower() or "explicit" in source.lower():
        return "指定起始角与出口角；未进行推力优化"
    return "已记录构造方法；未进行推力优化"


def _build_nozzle_3d(plotter, geom: Dict[str, Any]):
    n = geom.get("nozzle", {}) or {}
    c = geom.get("chamber", {}) or {}
    xs = list(n.get("contour_x") or [])
    rs = list(n.get("contour_r") or [])
    rc = _num(c.get("radius_m"), 0.19)
    Lc = _num(c.get("length_m"), 0.40)
    rt = _num(n.get("throat_radius_m"), 0.12)
    re = _num(n.get("exit_radius_m"), 0.70)

    if xs and rs:
        x_min = min(xs)
        # 在收缩段前补一段燃烧室圆柱,给出完整燃气流路
        prof_x = [x_min - Lc, x_min] + xs
        prof_r = [rc, rc] + rs
        surf = _pv_revolve(prof_x, prof_r)
        plotter.add_mesh(surf, color="#8ea2f5", smooth_shading=True,
                         specular=0.35, specular_power=12, show_edges=False,
                         label="喷管 + 燃烧室 (revolve from contour)")
        # 喉部高亮环
        try:
            ring = _pv_revolve([-1e-3, 1e-3], [rt, rt], resolution=120)
            plotter.add_mesh(ring, color="#dc2626", line_width=2)
        except Exception:
            pass
    else:
        cone = pv.Cone(center=(Lc * 0.6, 0, 0), direction=(-1, 0, 0),
                       radius=re, height=Lc * 1.2, resolution=80)
        plotter.add_mesh(cone, color="#8ea2f5", opacity=0.8)

    bf = _num(n.get("bell_fraction"), 0.80)
    txt = (f"抛物线钟形喷管 · 系统级工程近似 ({int(round(bf*100))}% bell)\n"
           f"{_nozzle_method_note(n)}\n"
           f"Dt = {2*rt:.3f} m, De = {2*re:.3f} m\n"
           f"ε = {_num(n.get('expansion_ratio'), 0):.1f}, "
           f"L_nozzle = {_num(n.get('nozzle_length_m'), 0):.3f} m")
    plotter.add_text(txt, position="upper_left", font_size=9, color="#1f2937")

def _fig_nozzle(geom: Dict[str, Any]) -> Figure:
    """喷管工程剖面图 (V7.1 重制)。

    设计原则:
      · 所有尺寸线/数值/角度都摆在轮廓 **外侧** ,通过虚线延伸线对齐轮廓,
        避免符号跟曲线重叠。
      · 长度尺寸分两层堆叠在轮廓上方:第一层是 L_chamber / L_conv / L_nozzle,
        第二层是 L_total。
      · 直径尺寸放在轮廓上下两侧:Dc 在燃烧室段左端外侧、Dt 在喉部正下方、
        De 在喷管出口右侧外侧。
      · 角度 θn / θe 用引出线 (annotate arrowprops) 拉到轮廓外标注,不再压线。
    """
    n = geom.get("nozzle", {}) or {}
    c = geom.get("chamber", {}) or {}
    xs = n.get("contour_x") or []
    rs = n.get("contour_r") or []
    rt = _num(n.get("throat_radius_m"), 0.12)
    re = _num(n.get("exit_radius_m"), 0.70)
    rc = _num(c.get("radius_m"), 0.20)
    L_ch = _num(c.get("length_m"), 0.40)
    L_conv = _num(n.get("convergent_length_m"), 0.17)
    L_noz = _num(n.get("nozzle_length_m"), 1.8)
    theta_n = _num(n.get("initial_expansion_angle_deg"), 30)
    theta_e = _num(n.get("exit_angle_deg"), 9)

    if xs and rs:
        xs_arr = np.asarray(xs)
        rs_arr = np.asarray(rs)
    else:
        xs_arr = np.array([-L_conv, 0.0, L_noz])
        rs_arr = np.array([rc, rt, re])

    # 留出更宽画布,确保外侧尺寸有空间;高度按内容自适应
    fig = _make_figure(12.2, 6.0)
    ax = fig.add_subplot(111)

    # ── 主轮廓 + 燃烧室圆柱段 ────────────────────────
    x_ch_start = xs_arr.min() - L_ch
    # 燃烧室圆柱段填充 (放最底,被轮廓覆盖)
    ax.fill_between([x_ch_start, xs_arr.min()], [rc, rc], [-rc, -rc],
                    color=CHAMBER_FILL, alpha=0.65, zorder=1)
    # 喷管腔体填充
    ax.fill_between(xs_arr, rs_arr, -rs_arr,
                    color=NOZZLE_FILL, alpha=0.55, zorder=1)
    # 边壁线
    ax.plot([x_ch_start, xs_arr.min()], [rc, rc], color="#1e3a8a", lw=2.2, zorder=3)
    ax.plot([x_ch_start, xs_arr.min()], [-rc, -rc], color="#1e3a8a", lw=2.2, zorder=3)
    ax.plot(xs_arr, rs_arr, color="#1e3a8a", lw=2.2, zorder=3)
    ax.plot(xs_arr, -rs_arr, color="#1e3a8a", lw=2.2, zorder=3)
    # 喷注面 (左端闭合)
    ax.plot([x_ch_start, x_ch_start], [-rc, rc], color="#1e3a8a", lw=2.2, zorder=3)

    # 轴线
    ax.axhline(0, color="#9ca3af", lw=0.7, ls="-.", zorder=2)

    # 喉部位置参考线
    ax.axvline(0, color="#dc2626", lw=0.6, ls=":", alpha=0.55, zorder=2)

    # ── 画布范围 (先定下来,后续尺寸位置依赖此) ─────────
    x_left = x_ch_start
    x_right = xs_arr.max()
    span_x = x_right - x_left
    # 给左右各留充足边距用于直径/出口标注;比例略偏左 (Dc 列要避开 y 轴刻度)
    margin_left = max(0.30, span_x * 0.13)
    margin_right = max(0.30, span_x * 0.16)
    # 顶部三层尺寸线:窄段错开放,避免文字相撞
    #   y1a:L_chamber (低位)
    #   y1b:L_conv   (略高,避开 L_chamber 字)
    #   y1c:L_nozzle (低位,跟 L_chamber 同高)
    #   y2 :L_total  (最高)
    y1a = re * 1.16
    y1b = re * 1.32
    y2  = re * 1.50
    bot_dim_y = -re * 1.16    # Dt 标注层
    ax.set_xlim(x_left - margin_left, x_right + margin_right)
    ax.set_ylim(-re * 1.55, re * 1.75)

    # ── 延伸线 (从轮廓向上拉到 L_total 上方) ───────
    for x_ref in (x_ch_start, xs_arr.min(), 0.0, x_right):
        if x_ref <= 0:
            r_at = rc
        else:
            r_at = rs_arr[np.argmin(np.abs(xs_arr - x_ref))]
        ax.plot([x_ref, x_ref], [r_at, y2 + 0.02],
                color=DIM_COLOR, lw=0.5, ls=":", zorder=2)

    # ── 长度尺寸 ─────────────────────────────────
    # L_chamber 和 L_nozzle 较宽,文字摆得下,放低位
    _dim_arrow(ax, x_ch_start, y1a, xs_arr.min(), y1a,
               f"L_chamber = {L_ch:.3f} m", fontsize=8.5)
    _dim_arrow(ax, 0.0, y1a, x_right, y1a,
               f"L_nozzle = {L_noz:.3f} m", fontsize=8.5)
    # L_conv 段窄 → 抬到 y1b,两端用斜延伸线
    _dim_arrow(ax, xs_arr.min(), y1b, 0.0, y1b,
               f"L_conv = {L_conv:.3f} m", fontsize=8)
    ax.plot([xs_arr.min(), xs_arr.min()], [y1a, y1b],
            color=DIM_COLOR, lw=0.5, ls=":")
    ax.plot([0.0, 0.0], [y1a, y1b],
            color=DIM_COLOR, lw=0.5, ls=":")
    # L_total 最上层
    _dim_arrow(ax, x_ch_start, y2, x_right, y2,
               f"L_total = {(L_ch + L_conv + L_noz):.3f} m", fontsize=9.5)

    # ── 直径尺寸 ─────────────────────────────────
    # Dc:燃烧室段左侧外,文字加白色背景遮住 y 轴刻度
    x_Dc = x_ch_start - margin_left * 0.55
    ax.plot([x_ch_start, x_Dc - 0.005], [rc, rc], color=DIM_COLOR, lw=0.5, ls=":")
    ax.plot([x_ch_start, x_Dc - 0.005], [-rc, -rc], color=DIM_COLOR, lw=0.5, ls=":")
    _dim_arrow(ax, x_Dc, -rc, x_Dc, rc, f"Dc = {2*rc:.3f} m", fontsize=9)

    # De:喷管出口右侧外
    x_De = x_right + margin_right * 0.55
    ax.plot([x_right, x_De - 0.005], [re, re], color=DIM_COLOR, lw=0.5, ls=":")
    ax.plot([x_right, x_De - 0.005], [-re, -re], color=DIM_COLOR, lw=0.5, ls=":")
    _dim_arrow(ax, x_De, -re, x_De, re, f"De = {2*re:.3f} m", fontsize=9)

    # Dt:喉部正下方 (轮廓外),斜延伸线引到下方尺寸线两端
    Dt_offset_x = max(0.06, rt * 1.6)
    ax.plot([0, -Dt_offset_x], [rt, bot_dim_y], color=DIM_COLOR, lw=0.5, ls=":")
    ax.plot([0, +Dt_offset_x], [-rt, bot_dim_y], color=DIM_COLOR, lw=0.5, ls=":")
    _dim_arrow(ax, -Dt_offset_x, bot_dim_y, +Dt_offset_x, bot_dim_y,
               f"Dt = {2*rt:.3f} m", fontsize=8.5)
    # throat 字样:放在 Dt 尺寸线左侧,避免压主轮廓
    ax.text(-Dt_offset_x - 0.04 * span_x, bot_dim_y, "throat",
            color="#dc2626", fontsize=8, ha="right", va="center")

    # ── 角度 θn / θe :用引出线指向轮廓外 ───────────
    # θn:喉部下游初始膨胀角(下方,避免跟顶部 L_x 群冲突)
    idx_n = min(int(np.argmin(np.abs(xs_arr - 0.0))) + 2, len(xs_arr) - 1)
    x_n, r_n = xs_arr[idx_n], rs_arr[idx_n]
    # 把 θn 文字放在喷管腔体下方(负 r 侧),引线穿过腔体到对称点
    tx_n, ty_n = x_n + 0.10 * span_x, -r_n - 0.22 * re
    ax.annotate(f"θn = {theta_n:.1f}°",
                xy=(x_n, -r_n), xytext=(tx_n, ty_n),
                fontsize=8.5, color="#7c2d12", ha="left", va="top",
                arrowprops=dict(arrowstyle="-", color="#7c2d12", lw=0.6))
    # θe:喷管出口处,放到正 r 侧斜下方,跟 L_nozzle/L_total 错开
    idx_e = len(xs_arr) - 1
    x_e, r_e = xs_arr[idx_e], rs_arr[idx_e]
    tx_e, ty_e = x_e - 0.10 * span_x, r_e + 0.05 * re
    ax.annotate(f"θe = {theta_e:.1f}°",
                xy=(x_e, r_e), xytext=(tx_e, ty_e),
                fontsize=8.5, color="#7c2d12", ha="right", va="center",
                arrowprops=dict(arrowstyle="-", color="#7c2d12", lw=0.6))

    # ── 工程图注(仅结构几何) ──────────────────────────
    eps = _num(n.get("expansion_ratio"), 35)
    cr = _num(n.get("contraction_ratio"), 2.5)
    bf = _num(n.get("bell_fraction"), 0.80)
    Ru = _num(n.get("throat_upstream_radius_m"), 0.0)
    Rd = _num(n.get("throat_downstream_radius_m"), 0.0)
    note = (
        f"抛物线钟形喷管 · 系统级工程近似 ({int(round(bf * 100))}% bell)\n"
        f"{_nozzle_method_note(n)}\n"
        f"面积膨胀比 ε = {eps:.1f},  面积收缩比 = {cr:.2f}\n"
        f"喉部上游圆弧 Ru = {Ru:.4f} m,  下游圆弧 Rd = {Rd:.4f} m"
    )
    ax.text(0.99, 0.02, note, transform=ax.transAxes, fontsize=8,
            color="#374151", ha="right", va="bottom",
            bbox=dict(boxstyle="round,pad=0.4", fc="#f8fafc", ec="#cbd5e1"))

    _engineering_style(ax, "喷管工程剖面图 / Nozzle engineering profile",
                       "x [m]  (喉部 x = 0)", "r [m]")
    return fig

def _explain_nozzle(geom: Dict[str, Any]) -> str:
    n = geom.get("nozzle", {}) or {}
    dt = _num(n.get("throat_diameter_m")); de = _num(n.get("exit_diameter_m"))
    eps = _num(n.get("expansion_ratio")); cr = _num(n.get("contraction_ratio"))
    ru = _num(n.get("throat_upstream_radius_m")); rd = _num(n.get("throat_downstream_radius_m"))
    tn = _num(n.get("initial_expansion_angle_deg")); te = _num(n.get("exit_angle_deg"))
    lnoz = _num(n.get("nozzle_length_m")); bf = _num(n.get("bell_fraction"), 0.8)
    angle_note = escape(_nozzle_method_note(n))
    rows = "".join([
        _row("喉部直径 Dt", f"{dt:.3f} m", "At = ṁ·c*/pc, Dt=√(4At/π)",
             "由设计点质量流量、特征速度与燃烧室压力确定"),
        _row("出口直径 De", f"{de:.3f} m", "Ae = ε·At, De=√(4Ae/π)",
             f"ε={eps:.1f} 是你输入的面积膨胀比,决定膨胀程度/真空比冲"),
        _row("上游圆弧 Ru", f"{ru:.4f} m", "读取统一轮廓的实际圆弧半径", "喉部上游过渡；与热侧换热计算使用同一几何"),
        _row("下游圆弧 Rd", f"{rd:.4f} m", "读取统一轮廓的实际圆弧半径", "喉部至抛物线段的相切过渡"),
        _row("初始抛物线角 θn", f"{tn:.1f}°", "读取本次轮廓记录", angle_note),
        _row("出口角 θe", f"{te:.1f}°", "读取本次轮廓记录", "出口壁面切线角；单独减小此角不保证整条型面性能提高"),
        _row("喷管长度 L_nozzle", f"{lnoz:.3f} m", f"{bf:.0%}×(15°锥形长度)",
             f"{int(round(bf*100))}% 钟形喷管的定义长度"),
    ])
    # 只报告保存的实际无量纲几何，不推定与任何实机或论文型面相同。
    rt_v = _num(n.get("throat_radius_m"))
    ru_rt = (ru / rt_v) if rt_v else 0.0
    rd_rt = (rd / rt_v) if rt_v else 0.0
    cfd_note = (
        f"<p class='src'><b>实际无量纲比：</b>"
        f"Ru/Rt = {ru_rt:.3f}，Rd/Rt = {rd_rt:.3f}。"
        "<br>图示与导出坐标用于系统级尺寸核对及后续流动分析；"
        "旋转内轮廓得到的是燃气侧曲面，不代表完整壁厚、冷却通道或可制造结构。"
        "角度和长度的选择尚需部件流场、分离与性能分析支持。</p>"
    )
    method = escape(str(n.get("nozzle_contour_method") or "历史记录未保存"))
    source = escape(str(n.get("angle_source") or "历史记录未保存"))

    return (_EXPLAIN_CSS +
            "<body><p><b>喷管几何 —— 抛物线钟形喷管：系统级工程近似</b>。"
            "圆弧连接喉部，二次 Bézier 连接抛物线起点与出口；其控制点为两端切线的交点。"
            "这定义了几何构造，并不自动证明该型面具有最优推力。"
            f"本次记录：{angle_note}。</p>"
            "<table width='100%'><tr><th>参数</th><th>当前值</th><th>公式</th><th>物理意义</th></tr>"
            + rows + "</table>"
            "<p class='src'>方法参考：NASA SP-8120《Liquid Rocket Engine Nozzles》，"
            "§2.1.2.1.2、图5（正文第15页）与 §3.1.2.1.2（正文第67页）。"
            "该资料讨论倾斜抛物线近似及入口、出口壁角的选择，并要求通过性能计算比较候选型面；"
            "本程序未将未经追溯的经验角度表达式认定为该图的拟合结果。"
            "<a href='https://ntrs.nasa.gov/citations/19770009165'>NASA 原始资料</a>。</p>"
            f"<p class='src'>保存的构造方法：{method}；角度来源：{source}。</p>"
            + cfd_note + "</body>")


# ── 友好别名 ──
fig = _fig_nozzle
explain = _explain_nozzle
build_3d = _build_nozzle_3d
