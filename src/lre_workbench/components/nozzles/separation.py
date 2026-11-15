"""
geometry/separation.py

喷管过膨胀流动分离的一维经验筛查（不预测侧载）。

判据
────
采用 Summerfield 型壁面分离准则（与本数字样机作者论文一致）：
    p_sep / p_a ≈ 0.40
此早期经验规则不能替代局部壁压、边界层和型面相关的分离分析。
这里以截面平均出口压力 pe < 0.4·p_amb 触发进一步分析；
按等熵形状估计的阈值交点不代表真实分离点。

依据
────
  · Y. Li, W. Guo, "A generative adversarial network for enhancing over-expansion
    flow field perception of nozzles with large expansion ratio",
    Aerospace Science and Technology 151 (2024) 109324.（p_sep/p_a = 0.4）
  · Summerfield 等，喷管分离经典准则;FSS(自由激波分离)/RSS(受限激波分离)模式。

说明：NASA SP-8120 p17、p68 将经验分离预测定位为研制指导。
以下 separated_* 历史字段仅表示触发筛查，False 也不能证明无分离。
本模块不预测 FSS/RSS 转换、侧向载荷或结构安全。
"""

from __future__ import annotations

import math
from typing import Dict, List

from lre_workbench.domain.geometry import NozzleFlow

SEP_RATIO = 0.40          # p_sep / p_amb（论文判据）
P_SEA_LEVEL = 101325.0    # 海平面环境压力 [Pa]


def _area_ratio_from_mach(mach: float, gamma: float) -> float:
    t = 1.0 + (gamma - 1.0) / 2.0 * mach * mach
    exp = (gamma + 1.0) / (2.0 * (gamma - 1.0))
    return (1.0 / mach) * (2.0 / (gamma + 1.0) * t) ** exp


def _mach_from_area_ratio(area_ratio: float, gamma: float) -> float:
    """由面积比反解超声速马赫数(二分)。"""
    if area_ratio <= 1.0:
        return 1.0
    lo, hi = 1.0001, 60.0
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        if _area_ratio_from_mach(mid, gamma) < area_ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _p_ratio_from_area(area_ratio: float, gamma: float) -> float:
    """超声速段:由面积比给出 静压/总压 比 p/p0。"""
    m = _mach_from_area_ratio(area_ratio, gamma)
    t = 1.0 + (gamma - 1.0) / 2.0 * m * m
    return t ** (-gamma / (gamma - 1.0))


def _separation_point(
    contour_x: List[float], contour_r: List[float], rt: float,
    gamma: float, p_amb: float, eps_exit: float, pe: float,
):
    """返回 (separated, eps_sep, x_sep_throat_rel, flowing_fraction)。

    筛查:pe < 0.4·p_amb 触发早期经验规则，并不直接判定实际分离。
    交点:以出口平均压力 pe 锚定等熵压力形状，沿轮廓寻找阈值截面。
        p(ε) ≈ pe · p_isen(ε) / p_isen(ε_exit)，自洽保证 ε_sep < ε_exit。

    x_sep_throat_rel: 分离点轴向位置(喉部 x=0 坐标系)。
    flowing_fraction: 历史字段；几何喉部至阈值交点长度比例，未触发时为1。
    """
    p_sep = SEP_RATIO * p_amb
    if pe >= p_sep:
        return (False, 0.0, 0.0, 1.0)          # 满流(含欠膨胀)

    ratio_exit = _p_ratio_from_area(max(eps_exit, 1.001), gamma)
    x_max = max(contour_x) if contour_x else 0.0
    x_sep, eps_sep = None, eps_exit
    for x, r in zip(contour_x, contour_r):
        if x <= 1e-9:                          # 只看喉部下游(扩张段)
            continue
        eps_loc = (r / max(rt, 1e-9)) ** 2
        p_loc = pe * _p_ratio_from_area(eps_loc, gamma) / max(ratio_exit, 1e-12)
        if p_loc <= p_sep:                     # 壁压降到分离阈值
            x_sep, eps_sep = x, eps_loc
            break
    if x_sep is None:
        x_sep, eps_sep = x_max, eps_exit
    flowing = x_sep / x_max if x_max > 1e-9 else 1.0
    return (True, eps_sep, x_sep, max(0.0, min(1.0, flowing)))


def _risk_level(separated: bool, flowing_fraction: float) -> str:
    if not separated:
        return "未触发早期经验筛查；侧载未评估，不能判定无分离"
    return "触发经验分离筛查；需非定常流动与结构分析，侧载幅值未计算"


def analyze_nozzle_flow(
    nozzle: Dict | object,
    pc: float,
    pe: float,
    p_amb_design: float,
    gamma: float = 1.20,
) -> NozzleFlow:
    """
    生成喷管过膨胀与经验分离筛查；历史字段不表示完成侧载校核。

    参数
    ────
    nozzle        : NozzleGeometry(或其 asdict);需要 contour_x/contour_r/
                    throat_radius_m/expansion_ratio。
    pc            : 燃烧室压力 [Pa]
    pe            : 喷管出口压力 [Pa]（来自循环求解，独立于环境压力）
    p_amb_design  : 设计点环境压力 [Pa]
    gamma         : 燃气比热比（影响分离点位置；默认 1.20，近似值）
    """
    def _g(name, default=0.0):
        if isinstance(nozzle, dict):
            return nozzle.get(name, default)
        return getattr(nozzle, name, default)

    xs = list(_g("contour_x", []) or [])
    rs = list(_g("contour_r", []) or [])
    rt = float(_g("throat_radius_m", 0.0) or 0.0)
    eps_exit = float(_g("expansion_ratio", 0.0) or 0.0)
    pc = max(float(pc), 1.0)
    pe = max(float(pe), 0.0)
    gamma = float(gamma) if gamma and gamma > 1.0 else 1.20

    sl_sep, sl_eps, sl_x, sl_flow = _separation_point(
        xs, rs, rt, gamma, P_SEA_LEVEL, eps_exit, pe)
    dz_sep, _de, _dx, _df = _separation_point(
        xs, rs, rt, gamma, max(p_amb_design, 1.0), eps_exit, pe)

    npr_sl = pc / P_SEA_LEVEL
    npr_dz = pc / max(p_amb_design, 1.0)
    risk = _risk_level(sl_sep, sl_flow)

    if sl_sep:
        summary = (f"海平面触发经验分离筛查：pe={pe/1e3:.1f} kPa < 0.4·p_amb="
                   f"{0.4*P_SEA_LEVEL/1e3:.1f} kPa；等熵形状阈值交点 ε≈{sl_eps:.1f}，"
                   "不是实际分离位置或侧载预测。")
    elif pe < P_SEA_LEVEL:
        summary = (f"海平面过膨胀：pe={pe/1e3:.1f} kPa < p_amb="
                   f"{P_SEA_LEVEL/1e3:.1f} kPa；未触发 0.4 经验阈值，不能据此证明无分离。")
    else:
        summary = f"海平面欠/完全膨胀：pe={pe/1e3:.1f} kPa ≥ p_amb；设计点未触发经验分离筛查，瞬态侧载仍未评估。"

    return NozzleFlow(
        chamber_pressure_pa=pc,
        exit_pressure_pa=pe,
        ambient_design_pa=float(p_amb_design),
        gamma=gamma,
        npr_sea_level=npr_sl,
        npr_design=npr_dz,
        separated_sea_level=bool(sl_sep),
        separated_design=bool(dz_sep),
        sep_pressure_sl_pa=SEP_RATIO * P_SEA_LEVEL,
        sep_area_ratio_sl=float(sl_eps),
        sep_axial_x_sl_m=float(sl_x),
        flowing_fraction_sl=float(sl_flow),
        sideload_risk=risk,
        summary=summary,
    )
