"""
cross_validate_charts.py

NASA CEA × Cantera 热力学交叉验证图表的独立绘图模块。

设计目标:
    · 专业的航空工程可视化风格(去顶/右 spines、微妙网格、清晰排版)
    · Cantera/CEA 视觉对位通过"色阶 + 线型 + 标记形状"三重编码
    · 多 ε 用色阶深浅区分(同色家族),不再依赖 dash 模式区分(线条多时看不清)
    · 共享底部 legend(每个 subplot 不再自带 legend,腾出绘图空间)
    · 中英双语轴标题(中文给国内用户,英文便于工程出版)
    · 与 matplotlib + Qt 嵌入完全兼容(参数就是个 Figure 对象)

公开接口:
    init_chart_placeholders(fig)        — 占位绘制(数据未来之前)
    plot_cross_validate(fig, records)   — 接收 cross_validate_cea_cantera 的输出

使用:
    from cross_validate_charts import plot_cross_validate
    plot_cross_validate(self.thermo_fig, records)
    self.thermo_canvas.draw_idle()
"""

from __future__ import annotations

from typing import List, Dict, Sequence, Tuple

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import AutoMinorLocator


# ─────────────────────────────────────────────────────────────────
# 视觉设计令牌(Design Tokens)
# ─────────────────────────────────────────────────────────────────

# 背景与中性色
BG_COLOR        = "#FAFAFA"
TEXT_PRIMARY    = "#111827"
TEXT_SECONDARY  = "#6B7280"
SPINE_COLOR     = "#D1D5DB"
GRID_COLOR      = "#E5E7EB"

# Cantera = 蓝色家族,CEA = 红色家族
# 浅 → 深对应 ε 从小到大(膨胀比越大颜色越深,直观)
CANTERA_PALETTE = ["#93C5FD", "#3B82F6", "#1E3A8A"]   # ε 浅 → 深
CEA_PALETTE     = ["#FCA5A5", "#DC2626", "#7F1D1D"]   # ε 浅 → 深

# 字体设计(尺寸 + 字重)
FONT_TITLE      = dict(fontsize=10.5, fontweight="bold", color=TEXT_PRIMARY)
FONT_SUBTITLE   = dict(fontsize=8.5,  color=TEXT_SECONDARY, style="italic")
FONT_AXIS_LABEL = dict(fontsize=9.5,  color=TEXT_SECONDARY)
FONT_TICK_SIZE  = 8.5
FONT_LEGEND     = dict(fontsize=8.5)

# 物理常量
G0 = 9.80665  # m/s²


# ─────────────────────────────────────────────────────────────────
# 私有工具
# ─────────────────────────────────────────────────────────────────

def _style_axes(ax) -> None:
    """给单个 Axes 应用工程绘图专业样式。"""
    # 去掉上 / 右 spine(航空数据可视化常见风格)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    # 保留的 spine 用浅灰
    for s in ("left", "bottom"):
        ax.spines[s].set_color(SPINE_COLOR)
        ax.spines[s].set_linewidth(0.8)
    # 刻度
    ax.tick_params(
        axis="both", which="major",
        labelsize=FONT_TICK_SIZE, colors=TEXT_SECONDARY,
        length=4, width=0.8, direction="out",
    )
    ax.tick_params(axis="both", which="minor", length=2, width=0.6, colors=SPINE_COLOR)
    # 次刻度(每个主刻度间 1 个)增强密度感
    ax.xaxis.set_minor_locator(AutoMinorLocator(2))
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    # 微妙网格 — 主刻度处实线、次刻度处更淡虚线
    ax.grid(True, which="major", linestyle="-", linewidth=0.4, color=GRID_COLOR, alpha=0.8)
    ax.grid(True, which="minor", linestyle=":", linewidth=0.3, color=GRID_COLOR, alpha=0.5)
    ax.set_axisbelow(True)
    # 背景
    ax.set_facecolor("white")


def _set_axes_labels(ax, title_zh: str, title_en: str, ylabel: str,
                     subnote: str | None = None) -> None:
    """单行双语标题"中文 / English",简洁省空间。可选右上角小注释。"""
    full_title = f"{title_zh}  /  {title_en}"
    ax.set_title(full_title, **FONT_TITLE, pad=8, loc="left")
    ax.set_xlabel("O/F 混合比 / Mixture Ratio", **FONT_AXIS_LABEL, labelpad=3)
    ax.set_ylabel(ylabel, **FONT_AXIS_LABEL, labelpad=3)
    if subnote:
        # 右上角小注(如"ε 无关")
        ax.text(
            0.985, 0.97, subnote, transform=ax.transAxes,
            ha="right", va="top", **FONT_SUBTITLE,
            bbox=dict(boxstyle="round,pad=0.30", facecolor="white",
                      edgecolor=SPINE_COLOR, linewidth=0.6),
        )


def _extract(records: Sequence[Dict], method: str, key: str) -> list:
    """从单 eps 子组中按方法 + 字段名提取数值;缺失返回 None。"""
    out = []
    for r in records:
        m = r.get(method)
        if m is None:
            out.append(None)
        else:
            out.append(m.get(key))
    return out


def _filter_xy(of_arr: Sequence[float], vals: Sequence) -> Tuple[list, list]:
    """过滤掉 None,返回成对的 (x, y) 列表。"""
    xs, ys = [], []
    for x, y in zip(of_arr, vals):
        if y is not None:
            xs.append(x)
            ys.append(y)
    return xs, ys


def _build_grid(fig: Figure):
    """2×2 主图 + 底部 legend 区。"""
    fig.patch.set_facecolor(BG_COLOR)
    fig.clear()
    gs = fig.add_gridspec(
        nrows=3, ncols=2,
        height_ratios=[1.0, 1.0, 0.10],
        hspace=0.85, wspace=0.32,    # hspace 加大避免上下两行标题/横轴打架
        left=0.075, right=0.97, top=0.95, bottom=0.07,
    )
    axes = {
        "isp":   fig.add_subplot(gs[0, 0]),
        "cstar": fig.add_subplot(gs[0, 1]),
        "cf":    fig.add_subplot(gs[1, 0]),
        "tc":    fig.add_subplot(gs[1, 1]),
        "legend": fig.add_subplot(gs[2, :]),
    }
    axes["legend"].axis("off")
    for k in ("isp", "cstar", "cf", "tc"):
        _style_axes(axes[k])
    return axes


# ─────────────────────────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────────────────────────

def init_chart_placeholders(fig: Figure) -> Dict:
    """
    初始化 2x2 图表(数据未到来前调用)。
    返回 axes 字典(给调用方持有,后续 update 用)。
    """
    axes = _build_grid(fig)

    # 用 matplotlib mathtext 渲染下标(替代 Unicode ₛₚ ғ,避免字体不支持)
    placeholders = [
        (axes["isp"],   "真空比冲", "Vacuum Specific Impulse",          r"$I_{sp}$ [s]",  None),
        (axes["cstar"], "特征速度 (燃烧室贡献)", "Characteristic Velocity — Chamber",  r"$C^*$ [m/s]",   "ε 无关"),
        (axes["cf"],    "推力系数 (喷管贡献)",  "Thrust Coefficient — Nozzle",          r"$C_F$ [—]",     None),
        (axes["tc"],    "燃烧室温度", "Chamber Temperature",             r"$T_c$ [K]",     "ε 无关"),
    ]
    for ax, t_zh, t_en, ylab, note in placeholders:
        _set_axes_labels(ax, t_zh, t_en, ylab, subnote=note)
        ax.text(
            0.5, 0.5, "点击「运行对比」后自动绘制",
            ha="center", va="center", transform=ax.transAxes,
            color=TEXT_SECONDARY, fontsize=9.5, style="italic",
        )
    return axes


def plot_cross_validate(fig: Figure, records: List[Dict]) -> None:
    """
    用交叉验证记录刷新 2x2 图表。

    输入 records 结构(来自 thermo.cross_validate_cea_cantera):
        [{"of": 3.6, "eps": 35, "pc_bar": 300,
          "cantera": {"Isp_vac_s": ..., "Cstar_m_s": ..., "Tc_K": ..., ...},
          "cea":     {"Isp_vac_s": ..., "Cstar_m_s": ..., "Tc_K": ..., ...},
          "delta_Isp_vac_s_pct": ..., ...
         }, ...]
    """
    if not records:
        return
    axes = _build_grid(fig)

    eps_set = sorted(set(r["eps"] for r in records))

    legend_lines: list = []
    legend_labels: list = []

    # ─────────────────────────────────────────────────────────
    # 阶段 1:Tc 和 C*(ε 无关)── 只画一次,用 palette 中段色
    # ─────────────────────────────────────────────────────────
    first_grp = sorted([r for r in records if r["eps"] == eps_set[0]],
                       key=lambda r: r["of"])
    of_first = [r["of"] for r in first_grp]
    ct_tc_first  = _extract(first_grp, "cantera", "Tc_K")
    cea_tc_first = _extract(first_grp, "cea",     "Tc_K")
    ct_cs_first  = _extract(first_grp, "cantera", "Cstar_m_s")
    cea_cs_first = _extract(first_grp, "cea",     "Cstar_m_s")

    mid_idx = len(CANTERA_PALETTE) // 2
    ct_mid  = CANTERA_PALETTE[mid_idx]
    cea_mid = CEA_PALETTE[mid_idx]

    line_kw_ct_mid = dict(
        color=ct_mid, lw=1.8, marker="o", markersize=4.8,
        markerfacecolor="white", markeredgewidth=1.4, zorder=3,
    )
    line_kw_cea_mid = dict(
        color=cea_mid, lw=1.6, ls="--", marker="s", markersize=4.5,
        markerfacecolor="white", markeredgewidth=1.4, zorder=2,
    )

    for ax_key, ct_vals, cea_vals in [
        ("tc",    ct_tc_first,  cea_tc_first),
        ("cstar", ct_cs_first,  cea_cs_first),
    ]:
        ax = axes[ax_key]
        x_ct,  y_ct  = _filter_xy(of_first, ct_vals)
        x_cea, y_cea = _filter_xy(of_first, cea_vals)
        if x_ct:
            ax.plot(x_ct, y_ct, **line_kw_ct_mid)
        if x_cea:
            ax.plot(x_cea, y_cea, **line_kw_cea_mid)

    # ─────────────────────────────────────────────────────────
    # 阶段 2:Isp 和 C_F(强 ε 依赖)── 按 ε 画多条
    # ─────────────────────────────────────────────────────────
    def _safe_cf(isps, cstars):
        return [
            (isps[k] * G0 / cstars[k]) if (isps[k] and cstars[k]) else None
            for k in range(len(isps))
        ]

    for i, eps in enumerate(eps_set):
        grp = sorted([r for r in records if r["eps"] == eps], key=lambda r: r["of"])
        of_arr = [r["of"] for r in grp]
        ct_color  = CANTERA_PALETTE[min(i, len(CANTERA_PALETTE) - 1)]
        cea_color = CEA_PALETTE[    min(i, len(CEA_PALETTE)     - 1)]

        ct_isp  = _extract(grp, "cantera", "Isp_vac_s")
        cea_isp = _extract(grp, "cea",     "Isp_vac_s")
        ct_cs   = _extract(grp, "cantera", "Cstar_m_s")
        cea_cs  = _extract(grp, "cea",     "Cstar_m_s")
        ct_cf, cea_cf = _safe_cf(ct_isp, ct_cs), _safe_cf(cea_isp, cea_cs)

        line_kw_ct = dict(
            color=ct_color, lw=1.7, marker="o", markersize=4.5,
            markerfacecolor="white", markeredgewidth=1.3, zorder=3,
        )
        line_kw_cea = dict(
            color=cea_color, lw=1.5, ls="--", marker="s", markersize=4.2,
            markerfacecolor="white", markeredgewidth=1.3, zorder=2,
        )

        for ax_key, ct_vals, cea_vals in [("isp", ct_isp, cea_isp),
                                          ("cf",  ct_cf,  cea_cf)]:
            ax = axes[ax_key]
            x_ct,  y_ct  = _filter_xy(of_arr, ct_vals)
            x_cea, y_cea = _filter_xy(of_arr, cea_vals)
            if x_ct:
                ax.plot(x_ct, y_ct, **line_kw_ct)
            if x_cea:
                ax.plot(x_cea, y_cea, **line_kw_cea)

        # 收集 legend(每个 ε 收一对 Cantera + CEA)
        legend_lines.append(Line2D([0], [0], **line_kw_ct))
        legend_labels.append(f"Cantera  ε={eps:.0f}")
        legend_lines.append(Line2D([0], [0], **line_kw_cea))
        legend_labels.append(f"CEA  ε={eps:.0f}")

    # ── 标题/轴标(mathtext 渲染下标,带"ε 无关"注释)─────────
    titles = [
        ("isp",   "真空比冲",            "Vacuum Specific Impulse",             r"$I_{sp}$ [s]",   None),
        ("cstar", "特征速度 (燃烧室贡献)", "Characteristic Velocity — Chamber",   r"$C^*$ [m/s]",    "ε 无关 / ε-independent"),
        ("cf",    "推力系数 (喷管贡献)",  "Thrust Coefficient — Nozzle",          r"$C_F$ [—]",      None),
        ("tc",    "燃烧室温度",          "Chamber Temperature",                  r"$T_c$ [K]",      "ε 无关 / ε-independent"),
    ]
    for k, t_zh, t_en, ylab, note in titles:
        _set_axes_labels(axes[k], t_zh, t_en, ylab, subnote=note)

    # ── 底部共享 legend ──────────────────────────────────
    ncol = min(len(legend_lines), 6)
    leg = axes["legend"].legend(
        legend_lines, legend_labels,
        loc="center", ncol=ncol, frameon=False,
        handlelength=2.5, handletextpad=0.6, columnspacing=1.8,
        **FONT_LEGEND,
    )
    for txt in leg.get_texts():
        txt.set_color(TEXT_PRIMARY)
