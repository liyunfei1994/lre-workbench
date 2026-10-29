"""主喷注器已存尺寸的横截面与说明；不补造轴向长度、缩进或旋流孔。"""

from __future__ import annotations

import html
import math
from typing import Any, Dict

from ._common import *  # noqa: F401,F403


def _positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _display(value, unit="", digits=3):
    number = _positive(value)
    return f"{number:.{digits}f}{unit}" if number is not None else "未记录"


def _fig_injector(geom: Dict[str, Any]) -> Figure:
    fig = _make_figure(10.2, 5.6)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.15])
    ax_face = fig.add_subplot(gs[0, 0])
    ax_elem = fig.add_subplot(gs[0, 1])
    inj = geom.get("injector", {}) or {}
    diameter = _positive(inj.get("face_diameter_m"))
    count = _positive(inj.get("num_elements"))
    count_text = str(int(count)) if count is not None and count.is_integer() else "未记录"

    ax_face.set_title("主喷注面布局示意", fontsize=11, fontweight="bold")
    if diameter is None:
        ax_face.text(.5, .5, "本记录未保存喷注面尺寸\n不能由当前默认参数补造",
                     ha="center", va="center", transform=ax_face.transAxes)
    else:
        radius = diameter * 500.0  # mm
        ax_face.add_patch(Circle((0, 0), radius, fill=False, ec="#475569", lw=1.8))
        # 固定示意符号；不声称实现 N 个元素的逐孔排布或边缘排布校核。
        points = [(0., 0.)]
        for ring, amount in ((.34, 6), (.70, 12)):
            points.extend((radius * ring * math.cos(2 * math.pi * i / amount),
                           radius * ring * math.sin(2 * math.pi * i / amount))
                          for i in range(amount))
        for x, y in points:
            ax_face.add_patch(Circle((x, y), radius * .075, color="#e88972"))
            ax_face.add_patch(Circle((x, y), radius * .048, color="#94a3b8"))
            ax_face.add_patch(Circle((x, y), radius * .036, color="#4678b8"))
        ax_face.text(0, -radius * 1.15,
                     f"记录面径 D = {diameter * 1000:.3f} mm\n记录单元数 N = {count_text}\n"
                     "图示符号数量及间距不代表实际逐孔排布",
                     ha="center", va="top", fontsize=9, color="#475569")
        ax_face.set_xlim(-radius * 1.15, radius * 1.15)
        ax_face.set_ylim(-radius * 1.65, radius * 1.15)
    ax_face.set_aspect("equal")
    ax_face.axis("off")

    ax_elem.set_title("同轴圆孔—环缝单元横截面", fontsize=11, fontweight="bold")
    d_ox, d_post, d_outer = (_positive(inj.get(key)) for key in
                            ("d_ox_mm", "d_post_mm", "d_outer_mm"))
    valid_section = (d_ox is not None and d_post is not None and d_outer is not None
                     and d_ox < d_post < d_outer)
    if not valid_section:
        ax_elem.text(.5, .5, "本记录缺少有效的单元直径\n不生成假定截面或纵向详细图",
                     ha="center", va="center", transform=ax_elem.transAxes)
        ax_elem.axis("off")
    else:
        radius = d_outer / 2.0
        # 按实际半径绘制：外筒内边界 -> 富燃环缝 -> 实体隔壁 -> 富氧圆孔。
        ax_elem.add_patch(Circle((0, 0), radius, facecolor="#e88972", edgecolor="#334155", lw=2))
        ax_elem.add_patch(Circle((0, 0), d_post / 2, facecolor="#94a3b8", edgecolor="#475569", lw=1))
        ax_elem.add_patch(Circle((0, 0), d_ox / 2, facecolor="#4678b8", edgecolor="#315984", lw=1))
        ax_elem.text(0, 0, "中心流体\n富氧热气", ha="center", va="center", fontsize=9, color="white")
        ax_elem.annotate("富燃热气环缝", xy=(0, (d_post + d_outer) / 4),
                         xytext=(0, radius * 1.35), ha="center", fontsize=9,
                         arrowprops=dict(arrowstyle="-", color="#9f4635"))
        ax_elem.annotate("实体隔壁", xy=((d_ox + d_post) / 4, 0),
                         xytext=(radius * 1.22, radius * .45), ha="left", fontsize=9,
                         arrowprops=dict(arrowstyle="-", color="#475569"))
        ax_elem.annotate("外筒内边界\n壁厚未设计", xy=(radius / math.sqrt(2), -radius / math.sqrt(2)),
                         xytext=(radius * 1.22, -radius * .45), ha="left", fontsize=9,
                         arrowprops=dict(arrowstyle="-", color="#475569"))
        _dim_arrow(ax_elem, -d_ox / 2, -radius * 1.25, d_ox / 2, -radius * 1.25,
                   f"中心孔 {d_ox:.3f} mm", fontsize=8)
        _dim_arrow(ax_elem, -d_post / 2, -radius * 1.65, d_post / 2, -radius * 1.65,
                   f"内管外径 {d_post:.3f} mm", fontsize=8)
        _dim_arrow(ax_elem, -radius, -radius * 2.05, radius, -radius * 2.05,
                   f"环缝外径 {d_outer:.3f} mm", fontsize=8)
        ax_elem.set_xlim(-radius * 1.25, radius * 2.0)
        ax_elem.set_ylim(-radius * 2.3, radius * 1.65)
        ax_elem.set_aspect("equal")
        ax_elem.axis("off")
    closure = inj.get("geometry_closure_passed")
    status_text = ("本记录几何与所用流量关系闭合" if closure is True else
                   "本记录几何流量闭合未通过" if closure is False else
                   "历史记录：几何流量闭合未验证")
    fig.suptitle("主喷注器通流初步尺寸 · " + status_text +
                 "\n轴向长度、缩进、切向孔及真实孔阵列尚未设计", fontsize=10, color="#475569")
    return fig


def _explain_injector(geom: Dict[str, Any]) -> str:
    inj = geom.get("injector", {}) or {}
    rows = "".join([
        _row("喷注面直径", _display(inj.get("face_diameter_m"), " m"),
             "读取 components.injector.outputs", "与主喷注器部件使用同一计算记录"),
        _row("喷注单元数", _display(inj.get("num_elements"), digits=0),
             "读取已计算 N", "不由几何页重新按流量估算"),
        _row("等效占用尺度", _display(inj.get("element_spacing_mm"), " mm"),
             "√(A_face / N)", "不是完成孔阵列设计后的真实中心距"),
        _row("中心流体孔径", _display(inj.get("d_ox_mm"), " mm"),
             "A_ox = π d_ox² / 4", "FFSC 主喷注器中心富氧热气通道"),
        _row("中心管外径", _display(inj.get("d_post_mm"), " mm"),
             "环缝内边界", "与中心孔之间为实体隔壁"),
        _row("环缝外径", _display(inj.get("d_outer_mm"), " mm"),
             "A_f = π (d_outer² − d_post²) / 4", "外筒内径；外筒壁厚尚未设计"),
        _row("单侧径向环缝宽", _display(inj.get("delta_fuel_mm"), " mm"),
             "δ = (d_outer − d_post) / 2", "FFSC 主喷注器外环富燃热气通道"),
        _row("中心管壁厚假设", _display(inj.get("post_wall_thickness_mm"), " mm"),
             "读取已保存假设", "尚未经热、强度及制造校核"),
    ])
    warnings = inj.get("geometry_warnings") or []
    warnings_html = "".join(f"<p>{html.escape(str(value))}</p>" for value in warnings)
    if inj.get("geometry_closure_passed") is None:
        warnings_html += "<p>本记录未保存几何流量闭合校核；历史尺寸不在显示时重新修正。</p>"
    return (_EXPLAIN_CSS + "<body><p><b>主喷注器同轴圆孔—环缝通流尺寸</b>　"
            "FFSC 主喷注器位于两股涡轮排气与主燃烧室之间。富氧、富燃预燃室各有自己的喷注器；"
            "本图不代表预燃室喷注器。</p><p>这里给出中心圆孔和外环通道的初步尺寸。"
            "尚未确定切向孔、槽、旋流室、轴向长度或缩进，因此不能称为已完成的同轴旋流喷注器设计，"
            "也不代表任何特定发动机的实际构型。</p><table width='100%'>"
            "<tr><th>参数</th><th>记录值</th><th>定义／来源</th><th>物理意义</th></tr>"
            + rows + "</table>" + warnings_html +
            "<p class='src'>左图仅示意同轴单元分布；右图按保存直径绘制横截面。"
            "流量闭合只说明与所用孔板模型一致，不代表可压缩流、燃烧、结构或制造验证通过。</p></body>")


fig = _fig_injector
explain = _explain_injector
