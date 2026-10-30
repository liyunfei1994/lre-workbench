"""
geometry/pumps.py

泵主尺寸初算。转速由调用方传入，保证部件结果/几何/同轴涡轮三处一致；
由压头系数反算叶尖速度与叶轮直径，比转速仅提示型式趋势。

泵设计背景参考 NASA SP-8109；本项目系数与初算范围见 docs/PUMP_GEOMETRY_SCOPE.md。
"""

from __future__ import annotations

import math

from .types import PumpGeometry


def estimate_pump(
    name: str,
    fluid: str,
    mdot: float,
    rho: float,
    dp: float,
    power: float,
    shaft_speed_rpm: float,
    n_stages: int,
    psi: float = 0.50,
    tip_speed_limit_ms: float = 550.0,
) -> PumpGeometry:
    """
    Parameters
    ──────────
    shaft_speed_rpm  : 轴转速 [rpm]（与同侧 Pump、Turbine 共用）
    n_stages         : 级数（与 Pump 对象保持一致）
    psi              : 单级压头系数 g·H_per_stage / u_tip²（方案假设）
    tip_speed_limit_ms : u_tip 上限 [m/s]，超出在调用方报警
    """
    g = 9.80665
    for label, value in (("mdot", mdot), ("rho", rho), ("dp", dp),
                         ("shaft_speed_rpm", shaft_speed_rpm), ("psi", psi)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"泵几何 {label} 必须为有限正数，不能截断后定尺寸。")
    if (isinstance(n_stages, bool) or not isinstance(n_stages, (int, float))
            or not math.isfinite(n_stages) or n_stages < 1 or int(n_stages) != n_stages):
        raise ValueError("泵几何级数必须为正整数。")
    n_stages = int(n_stages)
    Q = mdot / rho                    # 体积流量 [m³/s]，使用同一入口密度
    H = dp / (rho * g)                # 不可压缩水力参考总扬程 [m]
    H_per_stage = H / n_stages

    # 叶尖速度由压头系数定，叶轮直径由 ω 推：
    #   ψ = g · H_per_stage / u_tip²   →  u_tip = sqrt(g·H1/ψ)
    #   D  = 2·u_tip / ω
    u_tip = math.sqrt(g * H_per_stage / psi)
    omega = shaft_speed_rpm * 2.0 * math.pi / 60.0
    D_impeller = 2.0 * u_tip / omega
    # 不以显示范围替代求出的尺寸；否则 D、u_tip 与同轴转速不再一致。

    # 欧洲制 nq = N[rpm]·sqrt(Q[m³/s])/H_per_stage[m]^0.75，非无量纲。
    Ns_actual = shaft_speed_rpm * math.sqrt(Q) / H_per_stage ** 0.75
    if not all(math.isfinite(value) and value > 0 for value in (Q, H, u_tip, D_impeller, Ns_actual)):
        raise ValueError("泵几何派生量不是有限正数；请核对量纲与输入范围。")

    # 泵型分类（基于 per-stage Ns）
    if Ns_actual < 15.0:
        pump_type = "低比转速离心趋势（型式与级数仍需部件设计）"
    elif Ns_actual < 30.0:
        pump_type = "离心趋势（型式与级数仍需部件设计）"
    elif Ns_actual < 80.0:
        pump_type = "离心趋势（型式与级数仍需部件设计）"
    elif Ns_actual < 200.0:
        pump_type = "混流趋势（需部件设计确认）"
    else:
        pump_type = "轴流趋势（需部件设计确认）"

    return PumpGeometry(
        name=name,
        fluid=fluid,
        impeller_diameter_m=D_impeller,
        shaft_speed_rpm=shaft_speed_rpm,
        specific_speed=Ns_actual,
        num_stages=n_stages,
        tip_speed_ms=u_tip,
        head_m=H,
        volume_flow_m3s=Q,
        pump_type=pump_type,
    )
