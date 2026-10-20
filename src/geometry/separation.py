"""
geometry/separation.py

喷管过膨胀流动分离 / 侧载 一维工程校核。

判据
────
采用 Summerfield 型壁面分离准则（与本数字样机作者论文一致）：
    p_sep / p_a ≈ 0.40
即当喷管内壁静压降到约 0.4 倍环境压力时，附面层在逆压梯度下分离。
若喷管出口压力 pe < 0.4·p_amb，则流动在出口上游某截面已分离，
分离点对应的面积比 ε_sep 处壁压 = 0.4·p_amb。

依据
────
  · Y. Li, W. Guo, "A generative adversarial network for enhancing over-expansion
    flow field perception of nozzles with large expansion ratio",
    Aerospace Science and Technology 151 (2024) 109324.（p_sep/p_a = 0.4）
  · Summerfield 等，喷管分离经典准则;FSS(自由激波分离)/RSS(受限激波分离)模式。

说明：这是 *一维稳态* 指标，用于快速判断「是否分离 / 分离点位置 / 侧载风险定性」，
不替代三维 CFD 或侧载幅值预测（后者正是上述论文 GAN 方法的用武之地）。
"""

from __future__ import annotations

import math
from typing import Dict, List

from .types import NozzleFlow

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

    判定:pe < 0.4·p_amb 即分离(论文 Summerfield 判据,与高度无关地用真实 pe)。
    定位:以真实出口压力 pe 锚定等熵壁压形状,沿轮廓找壁压降到 p_sep 的截面。
        p(ε) ≈ pe · p_isen(ε) / p_isen(ε_exit)，自洽保证 ε_sep < ε_exit。

    x_sep_throat_rel: 分离点轴向位置(喉部 x=0 坐标系)。
    flowing_fraction: 满流段(喉部→分离点)占扩张段长度比例(0..1);未分离=1。
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
        return "无（海平面满流，不分离）"
    if flowing_fraction >= 0.75:
        return "低（分离贴近出口；启动/关机瞬态仍有侧载）"
    if flowing_fraction >= 0.45:
        return "中（启动/关机扫过 FSS↔RSS 转换区，侧载显著）"
    return "高（深度分离，强侧载/结构疲劳风险）"


def analyze_nozzle_flow(
    nozzle: Dict | object,
    pc: float,
    pe: float,
    p_amb_design: float,
    gamma: float = 1.20,
) -> NozzleFlow:
    """
    生成喷管过膨胀/分离/侧载校核结果。

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
        summary = (f"海平面过膨胀并分离：pe={pe/1e3:.1f} kPa < 0.4·p_amb="
                   f"{0.4*P_SEA_LEVEL/1e3:.1f} kPa，分离点约在面积比 ε≈{sl_eps:.1f} "
                   f"(满流段约占喷管 {sl_flow*100:.0f}%)。")
    elif pe < P_SEA_LEVEL:
        summary = (f"海平面过膨胀但未分离：pe={pe/1e3:.1f} kPa < p_amb="
                   f"{P_SEA_LEVEL/1e3:.1f} kPa，仍满流(pe > 0.4·p_amb)。")
    else:
        summary = f"海平面欠/完全膨胀：pe={pe/1e3:.1f} kPa ≥ p_amb，满流。"

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
