"""
ui_drawings.chamber —— 主燃烧室 的二维剖面图 / 3D 模型 / 估算说明（从 ui_drawings.py 拆出）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from ._common import *  # noqa: F401,F403


def _build_chamber_3d(plotter, geom: Dict[str, Any]):
    c = geom.get("chamber", {}) or {}
    n = geom.get("nozzle", {}) or {}
    rc = _num(c.get("radius_m"), 0.19)
    Lc = _num(c.get("length_m"), 0.40)
    rt = _num(n.get("throat_radius_m"), 0.12)
    Lconv = _num(n.get("convergent_length_m"), 0.17)
    ti = _num(c.get("inner_wall_thickness_mm"), 3.6) / 1000.0
    tc = _num(c.get("cooling_channel_height_mm"), 6.0) / 1000.0
    to = _num(c.get("outer_wall_thickness_mm"), 14.4) / 1000.0
    n_cool = max(1, int(_num(c.get("num_cooling_channels"), 305)))
    cw = _num(c.get("cooling_channel_width_mm"), 2.2) / 1000.0

    # 燃气侧轮廓 (圆柱段 + 收缩段：直锥 + 喉部圆弧规范轮廓)
    from chamber_contour import convergent_contour
    conv_angle = _num(n.get("convergent_half_angle_deg"), 30.0)
    cvx, cvr, _Lconv = convergent_contour(rc, rt, conv_half_angle_deg=conv_angle, x0=Lc)
    prof_x = np.asarray([0.0, Lc] + list(cvx))
    prof_r = np.asarray([rc, rc] + list(cvr))

    # 内壁 (铜合金,燃气侧) — 完整旋转体,不透明
    liner = _pv_revolve(prof_x, prof_r)
    plotter.add_mesh(liner, color="#fcd34d", smooth_shading=True,
                     specular=0.2, label="内壁 GRCop-84 (燃气侧)")
    # 外壳 (Inconel) — 剖切一半,半透明,露出内部
    outer = _pv_revolve(prof_x, prof_r + ti + tc + to)
    plotter.add_mesh(_pv_half(outer), color="#94a3b8", opacity=0.35,
                     smooth_shading=True, label="外壳 Inconel-718 (剖切)")

    # 再生冷却通道 — 沿圆柱段周向铺一圈小方管 (示意,数量上限 80)
    r_mid = rc + ti + 0.5 * tc
    x0 = Lc * 0.06
    Lbox = Lc * 0.88
    half_h = 0.45 * tc
    n_show = min(n_cool, 80)
    blocks = []
    for i in range(n_show):
        th = 360.0 * i / n_show
        box = pv.Box(bounds=(x0, x0 + Lbox,
                             -0.5 * cw, 0.5 * cw,
                             r_mid - half_h, r_mid + half_h))
        try:
            box = box.rotate_x(th, point=(0, 0, 0))
        except Exception:
            box.rotate_x(th, point=(0, 0, 0), inplace=True)
        blocks.append(box)
    if blocks:
        try:
            channels = pv.merge(blocks)
        except Exception:
            channels = blocks[0]
            for b in blocks[1:]:
                channels = channels.merge(b)
        plotter.add_mesh(channels, color="#2563eb", opacity=0.92,
                         label="再生冷却通道 CH4")

    txt = (f"主燃烧室剖切 (cutaway)\n"
           f"Dc = {2*rc:.3f} m, Lc = {Lc:.3f} m\n"
           f"冷却通道 N ≈ {n_cool} (示意 {n_show} 条)\n"
           f"壁: 内 {ti*1000:.1f} / 通道 {tc*1000:.1f} / 外 {to*1000:.1f} mm")
    plotter.add_text(txt, position="upper_left", font_size=9, color="#1f2937")
    try:
        plotter.add_legend(face="rectangle", size=(0.22, 0.16))
    except Exception:
        pass

def _fig_chamber(geom: Dict[str, Any]) -> Figure:
    """主燃烧室纵向剖面 + A-A 截面 (V7.1 改进版)。

    改动:
      · 画布加大,顶部留出图例区(不再压住主体);
      · 三种壁层若 geometry.py 给出明确分层厚度则用其,否则按总壁厚分配;
      · A-A 截面顶部信息加入冷却通道宽/高及周向间距;
      · 底部尺寸线 y 坐标抬高,xlim/ylim 加大避免裁切。
    """
    fig = _make_figure(10.4, 5.4)
    gs = fig.add_gridspec(1, 3, width_ratios=[2.2, 0.06, 1.0])
    ax = fig.add_subplot(gs[0, 0])
    ax_sec = fig.add_subplot(gs[0, 2])

    c = geom.get("chamber", {}) or {}
    n = geom.get("nozzle", {}) or {}
    rc = _num(c.get("radius_m"), 0.20)
    L_ch = _num(c.get("length_m"), 0.40)
    L_star = _num(c.get("l_star_m"), 1.0)
    t_wall_mm = _num(c.get("wall_thickness_mm"), 8.0)
    rt = _num(n.get("throat_radius_m"), 0.12)
    L_conv = _num(n.get("convergent_length_m"), 0.17)
    cr = _num(c.get("contraction_ratio"), 2.5)

    # ── 左:纵向剖面 ──────────────────────────────────
    # 优先用 geometry.py 输出的明确分层厚度;不可用时按经验分配
    t_inner_mm  = _num(c.get("inner_wall_thickness_mm"), 0)
    t_chan_mm   = _num(c.get("cooling_channel_height_mm"), 0)
    t_outer_mm  = _num(c.get("outer_wall_thickness_mm"), 0)
    if t_inner_mm > 0 and t_chan_mm > 0 and t_outer_mm > 0:
        t_inner_m   = t_inner_mm / 1000.0
        t_channel_m = t_chan_mm / 1000.0
        t_outer_m   = t_outer_mm / 1000.0
    else:
        t_inner_m   = max(0.0008, t_wall_mm * 0.0003)
        t_channel_m = max(0.001,  t_wall_mm * 0.0004)
        t_outer_m   = max(0.001,  (t_wall_mm * 0.001) - t_inner_m - t_channel_m)
    t_total_m = t_inner_m + t_channel_m + t_outer_m

    # ── V11:材料名称从 materials 数据库查 (中英双语),不再硬编码 ──
    try:
        from materials import get_chamber_wall_material
        _mat_inner = get_chamber_wall_material("chamber_inner_wall")
        _mat_outer = get_chamber_wall_material("chamber_outer_wall")
        inner_label = f"内壁 {_mat_inner.name}\n  ({_mat_inner.name_cn})"
        outer_label = f"外壳 {_mat_outer.name}\n  ({_mat_outer.name_cn})"
        cool_label  = "再生冷却通道 CH4"
        # 用于底部注释的简短名
        inner_short = _mat_inner.name
        outer_short = _mat_outer.name
    except Exception:
        inner_label = "内壁 GRCop-84"
        outer_label = "外壳 Inconel-718"
        cool_label  = "再生冷却通道 CH4"
        inner_short = "GRCop-84"
        outer_short = "Inconel-718"

    # 内表面（燃气侧）：收缩段取唯一规范轮廓（直锥 + 喉部圆弧），不再画成直线
    from chamber_contour import convergent_contour
    conv_angle = _num(n.get("convergent_half_angle_deg"), 30.0)
    x_inj = 0
    x_conv0 = L_ch
    cvx, cvr, L_conv = convergent_contour(rc, rt, conv_half_angle_deg=conv_angle, x0=x_conv0)
    x_throat = x_conv0 + L_conv
    profile_x = np.concatenate([[x_inj, x_conv0], np.asarray(cvx)])
    profile_r = np.concatenate([[rc, rc], np.asarray(cvr)])

    # 内壁(铜合金,默认 GRCop-84)
    ax.fill_between(profile_x, profile_r, profile_r + t_inner_m,
                    color=INNER_WALL, alpha=0.85, label=inner_label)
    ax.fill_between(profile_x, -profile_r - t_inner_m, -profile_r,
                    color=INNER_WALL, alpha=0.85)
    # 冷却通道
    ax.fill_between(profile_x, profile_r + t_inner_m, profile_r + t_inner_m + t_channel_m,
                    color=COOLING_FILL, alpha=0.85, label=cool_label)
    ax.fill_between(profile_x, -profile_r - t_inner_m - t_channel_m, -profile_r - t_inner_m,
                    color=COOLING_FILL, alpha=0.85)
    # 外壳(默认 Inconel-718)
    ax.fill_between(profile_x, profile_r + t_inner_m + t_channel_m,
                    profile_r + t_total_m,
                    color=OUTER_WALL, alpha=0.7, label=outer_label)
    ax.fill_between(profile_x, -profile_r - t_total_m,
                    -profile_r - t_inner_m - t_channel_m,
                    color=OUTER_WALL, alpha=0.7)

    # 燃气区域
    ax.fill_between(profile_x, -profile_r, profile_r, color=CHAMBER_FILL, alpha=0.6)

    # 喷注面
    ax.add_patch(Rectangle((x_inj - 0.05, -rc - t_total_m), 0.05, 2*(rc + t_total_m),
                           color=INJECTOR_FILL, alpha=0.85))
    ax.plot([x_inj - 0.05, x_inj], [rc + t_total_m, rc + t_total_m], color="#1f2937", lw=1.2)
    ax.plot([x_inj - 0.05, x_inj], [-rc - t_total_m, -rc - t_total_m], color="#1f2937", lw=1.2)
    ax.text(x_inj - 0.025, 0, "喷注\n面板", ha="center", va="center", fontsize=8,
            color="#7c2d12")

    # 轴线
    ax.axhline(0, color="#9ca3af", lw=0.6, ls="-.")
    # A-A 剖切位置
    x_AA = L_ch * 0.55
    ax.axvline(x_AA, color="#dc2626", lw=0.8, ls="--", alpha=0.7)
    ax.text(x_AA, rc * 1.35, "A", color="#dc2626", fontsize=10, ha="center", fontweight="bold")
    ax.text(x_AA, -rc * 1.35, "A", color="#dc2626", fontsize=10, ha="center", fontweight="bold")

    # ── 长度尺寸 + 直径尺寸 (注意 y 坐标抬高,避免裁切) ─────
    # L_chamber 分量
    _dim_arrow(ax, x_inj, -rc * 1.55, x_conv0, -rc * 1.55,
               f"L_chamber = {L_ch:.3f} m", fontsize=8)
    # L_conv 分量
    _dim_arrow(ax, x_conv0, -rc * 1.55, x_throat, -rc * 1.55,
               f"L_conv = {L_conv:.3f} m", fontsize=8)
    # 直径尺寸:Dc 放到喷注面板更左外侧(避免标注压住喷注面板),引虚线对齐室壁
    x_Dc = (x_inj - 0.05) - 0.075
    ax.plot([x_inj - 0.05, x_Dc], [rc, rc], color=DIM_COLOR, lw=0.5, ls=":")
    ax.plot([x_inj - 0.05, x_Dc], [-rc, -rc], color=DIM_COLOR, lw=0.5, ls=":")
    _dim_arrow(ax, x_Dc, -rc, x_Dc, rc, f"Dc = {2*rc:.3f} m", fontsize=9)
    _dim_arrow(ax, x_throat + 0.05, -rt, x_throat + 0.05, rt,
               f"Dt = {2*rt:.3f} m", fontsize=8.5)

    # ── 图例:放到图形上方,不再压主体 ──────────────────
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.16),
              ncol=3, fontsize=7.5, frameon=True,
              borderpad=0.35, columnspacing=0.8)

    # ── 工程图注:放到底部右侧,跟尺寸线分开,避免被裁 ──
    note = (
        f"L* = {L_star:.2f} m,  收缩比 ε_c = {cr:.2f}\n"
        f"壁总厚 ≈ {t_wall_mm:.1f} mm\n"
        f"   = 内壁 {t_inner_m*1000:.1f} ({inner_short}) + "
        f"通道 {t_channel_m*1000:.1f} + 外壳 {t_outer_m*1000:.1f} ({outer_short})"
    )
    ax.text(0.99, -0.015, note, transform=ax.transAxes, fontsize=7.8,
            color="#374151", ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.4", fc="#f8fafc", ec="#cbd5e1"))

    _engineering_style(ax, "主燃烧室纵向剖面 / Chamber longitudinal section", "x [m]", "r [m]")
    ax.set_xlim(x_Dc - 0.05, x_throat + 0.18)
    ax.set_ylim(-rc * 2.05, rc * 2.05)

    # ── 右:A-A 横截面 ─────────────────────────────
    ax_sec.add_patch(Circle((0, 0), rc + t_total_m, color=OUTER_WALL, alpha=0.8))
    ax_sec.add_patch(Circle((0, 0), rc + t_inner_m + t_channel_m, color=COOLING_FILL, alpha=0.85))
    ax_sec.add_patch(Circle((0, 0), rc + t_inner_m, color=INNER_WALL, alpha=0.95))
    ax_sec.add_patch(Circle((0, 0), rc, color=CHAMBER_FILL, alpha=0.85))

    # 冷却通道径向辐条:数量优先从 geometry 字段取,否则按 1.2 mm 周向节距估
    n_channels_geom = int(_num(c.get("num_cooling_channels"), 0))
    if n_channels_geom > 0:
        n_channels = n_channels_geom
    else:
        n_channels = max(40, int(2 * math.pi * rc / 0.012))
    ch_width_mm = _num(c.get("cooling_channel_width_mm"), 0)
    ch_height_mm = _num(c.get("cooling_channel_height_mm"), t_channel_m * 1000)
    rr_inner = rc + t_inner_m * 0.5
    rr_outer = rc + t_inner_m + t_channel_m * 0.95
    for i in range(n_channels):
        a = 2 * math.pi * i / n_channels
        x1 = rr_inner * math.cos(a); y1 = rr_inner * math.sin(a)
        x2 = rr_outer * math.cos(a); y2 = rr_outer * math.sin(a)
        ax_sec.plot([x1, x2], [y1, y2], color="#1e40af", lw=0.4, alpha=0.5)

    ax_sec.set_title("A-A 横截面 (放大示意)", fontsize=10,
                     fontweight="bold", color="#1f2937")
    ax_sec.set_aspect("equal")
    ax_sec.axis("off")
    R = rc + t_total_m
    ax_sec.set_xlim(-R * 1.18, R * 1.18)
    ax_sec.set_ylim(-R * 1.45, R * 1.55)
    # 顶/底文字注释:通道数 + 截面 (放在图外,不挤入剖面)
    ax_sec.text(0, R * 1.42,
                f"冷却通道数 N ≈ {n_channels}",
                ha="center", fontsize=8.5, color="#374151", fontweight="bold")
    if ch_width_mm > 0:
        ax_sec.text(0, R * 1.20,
                    f"通道截面 ≈ {ch_width_mm:.2f} × {ch_height_mm:.2f} mm",
                    ha="center", fontsize=8, color="#374151")
    ax_sec.text(0, -R * 1.20,
                f"内壁 {inner_short}\n通道 CH4 再生冷却\n外壳 {outer_short}",
                ha="center", va="center", fontsize=7.5, color="#475569",
                bbox=dict(boxstyle="round,pad=0.3", fc="#f8fafc", ec="#cbd5e1"))
    return fig

def _explain_chamber(geom: Dict[str, Any]) -> str:
    c = geom.get("chamber", {}) or {}
    dc = _num(c.get("diameter_m")); lc = _num(c.get("length_m"))
    ls = _num(c.get("l_star_m")); cr = _num(c.get("contraction_ratio"))
    tw = _num(c.get("wall_thickness_mm"))
    ti = _num(c.get("inner_wall_thickness_mm")); tch = _num(c.get("cooling_channel_height_mm"))
    to = _num(c.get("outer_wall_thickness_mm"))
    ncool = int(_num(c.get("num_cooling_channels"))); cwid = _num(c.get("cooling_channel_width_mm"))
    rows = "".join([
        _row("内径 Dc", f"{dc:.3f} m", "Ac=εc·At, Dc=√(4Ac/π)",
             f"收缩比 εc={cr:.2f}(你输入),即 Dc=√εc·Dt"),
        _row("圆柱段长 Lc", f"{lc:.3f} m", "Lc=(Vc−V_conv)/Ac",
             "扣除收缩段(直锥+喉部圆弧)体积后的圆柱段净长"),
        _row("特征长度 L*", f"{ls:.2f} m", "L* = Vc/At (你输入)",
             "燃气停留时间/掺混完全度,LOX/CH₄ 典型 0.8–1.2 m"),
        _row("冷却通道数 N", f"{ncool}", "= 冷却模块设计值(输入 cooling_n_channels)",
             "与再生冷却物理同一口径(Raptor≈300、RS-25≈430,量级合理)"),
        _row("通道截面", f"{cwid:.2f}×{tch:.2f} mm", "= 冷却模块通道宽 × 通道层厚", "单通道周向宽 × 径向高(同冷却物理)"),
    ])
    return (_EXPLAIN_CSS +
            "<body><p><b>主燃烧室几何</b>　由喉部面积按收缩比放大成室截面,"
            "再用特征长度 L* 定容积(<code>geometry.py: compute_geometry</code>)。</p>"
            "<table width='100%'><tr><th>参数</th><th>当前值</th><th>公式</th><th>物理意义</th></tr>"
            + rows + "</table>"
            f"<p class='warn'><b>壁厚分层（V12：按各层真实失效模式分别定厚，对标 Raptor 通道壁）</b>："
            f"当前总壁厚≈{tw:.1f} mm = 内衬 {ti:.1f} + 通道 {tch:.1f} + 外壳 {to:.1f} mm。三层判据不同："
            "<b>①内衬(铜)按传热限</b> <code>t=k·ΔT/q</code>（喉部高热流 → 内衬必须薄至 ~0.5–1 mm，"
            "厚了热侧温降 q·t/k 过大会烧穿）；<b>②通道深</b>按冷却需求；"
            "<b>③外壳(镍基)按环向应力</b> <code>t=SF·P·rc/σ</code> —— 注意它包容的是"
            "<b>高压冷却剂（≈泵出口压，约 2×pc）</b>而非 pc，故外壳厚而内衬薄。"
            "已替代旧版'单一 Barlow(0.25pc)+15/25/60 切分'（那版内衬虚高 3–4 倍、外壳对冷却剂压力反偏薄）。"
            "Dc/Lc/L* 同样是可信的初步设计量级。</p>"
            "<p class='src'>依据：Huzel &amp; Huang；NASA SP-125《Design of Liquid Propellant "
            "Rocket Engines》。</p></body>")


# ── 友好别名 ──
fig = _fig_chamber
explain = _explain_chamber
build_3d = _build_chamber_3d
