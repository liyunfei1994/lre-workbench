"""
geometry/pumps.py

涡轮泵几何 *校核* 计算器（V10）。转速由调用方传入，保证部件结果/几何/同轴
涡轮三处一致；由压头系数反算叶尖速度与叶轮直径，并算比转速判定泵型。

依据：NASA SP-8109 / SP-8110 涡轮泵设计准则。
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
    psi              : 单级压头系数 g·H_per_stage / u_tip²（典型 0.45–0.55）
    tip_speed_limit_ms : u_tip 上限 [m/s]，超出在调用方报警
    """
    g = 9.80665
    Q = mdot / max(rho, 100.0)          # 体积流量 [m³/s]
    H = dp / max(rho * g, 1.0)          # 总扬程 [m]
    H_per_stage = H / max(n_stages, 1)

    # 叶尖速度由压头系数定，叶轮直径由 ω 推：
    #   ψ = g · H_per_stage / u_tip²   →  u_tip = sqrt(g·H1/ψ)
    #   D  = 2·u_tip / ω
    u_tip = math.sqrt(g * max(H_per_stage, 1.0) / max(psi, 0.1))
    omega = shaft_speed_rpm * 2.0 * math.pi / 60.0
    D_impeller = 2.0 * u_tip / max(omega, 1.0)
    # 实际工程合理范围（仅做下/上界报警，不再 clip 转速）
    D_impeller = max(0.03, min(0.80, D_impeller))

    # 量纲比转速（rpm·m^(3/4)/(m³/s)^(1/2)，per-stage）
    Ns_actual = (
        shaft_speed_rpm * math.sqrt(max(Q, 1e-9))
        / max(H_per_stage ** 0.75, 1e-6)
    )

    # 泵型分类（基于 per-stage Ns）
    if Ns_actual < 15.0:
        pump_type = "多级离心 multi-stage centrifugal (Ns 偏低，建议增加级数或提升转速)"
    elif Ns_actual < 30.0:
        pump_type = "多级离心 multi-stage centrifugal"
    elif Ns_actual < 80.0:
        pump_type = "单级离心 centrifugal"
    elif Ns_actual < 200.0:
        pump_type = "混流 mixed-flow"
    else:
        pump_type = "轴流 axial"

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
