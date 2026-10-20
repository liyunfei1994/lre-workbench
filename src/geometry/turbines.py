"""
geometry/turbines.py

涡轮几何 *校核* 计算器（V10）。与同侧泵共轴；按目标速比 U/C₀ 反算节圆直径，
按 U/C₀ 选级数，按流量连续性估叶高（用真实工质物性算密度）。

依据：NASA SP-8110 §4.4。
"""

from __future__ import annotations

import math

from .types import TurbineGeometry


def estimate_turbine(
    name: str,
    shaft_speed_rpm: float,
    power: float,
    mdot: float,
    delta_h: float,
    p_in: float,
    T_in: float,
    u_over_c_target: float = 0.55,
    n_stages_user: int = 0,
    cp_gas: float | None = None,
    gamma_gas: float | None = None,
    molar_mass_gas: float | None = None,
    u_max_ms: float = 450.0,
) -> TurbineGeometry:
    """
    Parameters
    ──────────
    delta_h         : 等熵焓降 [J/kg]
    u_over_c_target : 目标速比；用于反算理想 U_mean
    u_max_ms        : 叶尖速度物理上限 [m/s]，超出报警
    """
    # 等熵速度（spouting velocity） C0 = sqrt(2·Δh_s)
    C0 = math.sqrt(max(2.0 * delta_h, 1.0))

    omega = shaft_speed_rpm * 2.0 * math.pi / 60.0

    # 1. 按目标 U/C0 反算理想 U_mean
    U_mean_target = u_over_c_target * C0

    # 2. 实际可达的 U_mean 受 D_mean 的工程范围限制
    D_mean_ideal = 2.0 * U_mean_target / max(omega, 1.0)
    D_mean = max(0.05, min(0.80, D_mean_ideal))
    U_mean_actual = 0.5 * D_mean * omega
    u_over_c_actual = U_mean_actual / max(C0, 1.0)

    # 3. 级数选择
    if n_stages_user >= 1:
        n_stages = int(n_stages_user)
    else:
        if u_over_c_actual < 0.20:
            n_stages = 3
        elif u_over_c_actual < 0.35:
            n_stages = 2
        elif u_over_c_actual < 0.50:
            n_stages = 2
        else:
            n_stages = 1

    # 4. 叶高估算（流量连续性）
    if cp_gas is not None and gamma_gas is not None and molar_mass_gas is not None:
        R_specific = 8314.462618 / max(molar_mass_gas, 1.0)
        rho_gas = p_in / max(R_specific * T_in, 1.0)
    else:
        rho_gas = p_in / max(300.0 * T_in, 1.0)
    Cm = max(50.0, 0.30 * C0)
    A_flow = mdot / max(rho_gas * Cm, 1.0)
    h_blade = A_flow / max(math.pi * D_mean, 0.01)
    h_blade = max(0.003, min(0.150, h_blade))

    return TurbineGeometry(
        name=name,
        rotor_diameter_m=D_mean,
        shaft_speed_rpm=shaft_speed_rpm,
        num_stages=n_stages,
        blade_height_mm=h_blade * 1000.0,
        u_over_c=u_over_c_actual,
    )
