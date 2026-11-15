"""Auditable nozzle performance bookkeeping, not a new flow solution.

Ambient-pressure characteristics hold the saved internal state and hardware fixed.
The empirical separation screen is only a trigger for further analysis.
"""
from __future__ import annotations

import math

from lre_workbench.domain.models import G0

SEPARATION_SCREEN_FACTOR = 0.4
SCREENING_NOTE = (
    "pe/pa < 0.4 仅触发早期 Summerfield 型经验筛查；截面平均出口压力不等于局部壁压。"
    "未触发也不证明附着流或无侧载；不预测 FSS/RSS、分离位置或侧向力。"
)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def expansion_regime(pe, pa):
    if pa == 0:
        return "vacuum"
    if math.isclose(pe, pa, rel_tol=1e-6):
        return "matched"
    return "underexpanded" if pe > pa else "overexpanded"


def ambient_characteristic(*, pc, mdot, at, ae, ve, pe, design_pa,
                           maximum_pressure_Pa=None, point_count=21):
    """Return full-flow algebraic extrapolations, with explicit screen statuses.

    The 0.2 pc / 5 MPa ceiling is a software guard, NOT a validated operating
    envelope or choking criterion. A shock / separated solution is not computed.
    """
    for key, value in dict(pc=pc, mdot=mdot, at=at, ae=ae, ve=ve, pe=pe).items():
        if not finite_number(value) or value <= 0:
            raise ValueError(f"喷管环境特性缺少有效的 {key}。")
    if not finite_number(design_pa) or design_pa < 0 or ae < at:
        raise ValueError("保存的环境压力或面积关系无效。")
    force_scale, mass_scale, momentum = pc*at, mdot*G0, mdot*ve
    if any(not finite_number(v) or v <= 0 for v in (force_scale, mass_scale, momentum)):
        raise ValueError("喷管尺度超出有限数值计算范围。")
    upper = min(5e6, .2 * pc)
    if maximum_pressure_Pa is None:
        maximum_pressure_Pa = min(max(101325., design_pa), upper)
    if not finite_number(maximum_pressure_Pa) or not 0 < maximum_pressure_Pa <= upper:
        raise ValueError(f"环境压力上限须在 0 与 {upper:g} Pa 之间；此范围仅是计算保护。")
    if not isinstance(point_count, int) or isinstance(point_count, bool) or not 3 <= point_count <= 101:
        raise ValueError("环境特性点数须为 3～101 的整数。")
    def point(pa):
        pressure_thrust = (pe-pa)*ae
        force = momentum + pressure_thrust
        values = dict(p_amb_Pa=pa, thrust_N=force, Isp_s=force/mass_scale,
                           Cf=force/force_scale, pressure_thrust_N=pressure_thrust,
                           pe_pa_ratio=pe/pa if pa else None, regime=expansion_regime(pe, pa),
                           screening=("vacuum" if pa == 0 else
                                      "flag" if pe < SEPARATION_SCREEN_FACTOR*pa else "not_flagged"))
        if any(not finite_number(values[k]) for k in ("thrust_N", "Isp_s", "Cf", "pressure_thrust_N")) or (pa and not finite_number(values["pe_pa_ratio"])):
            raise ValueError("环境特性派生量超出有限数值计算范围。")
        return values
    points = [point(maximum_pressure_Pa * i / (point_count - 1)) for i in range(point_count)]
    return dict(available=True, points=points, design_p_amb_Pa=design_pa, match_p_amb_Pa=pe,
                design_point=point(design_pa),
                maximum_pressure_Pa=maximum_pressure_Pa, maximum_allowed_pressure_Pa=upper,
                range_note=("保存设计背压超出本次绘图范围；图中不包含该设计点。" if design_pa > maximum_pressure_Pa else
                            "压力上限属于软件计算保护，不是经过验证的工况包线。"),
                point_count=point_count, screening_factor=SEPARATION_SCREEN_FACTOR,
                screening_note=SCREENING_NOTE,
                scope="固定保存的 Pc、质量流量、几何和出口状态，仅改变环境背压：F=ṁVe+(pe−pa)Ae。"
                      "曲线假设满流；触发经验筛查后的点仅为代数外推。不是节流、弹道或分离流求解。")
