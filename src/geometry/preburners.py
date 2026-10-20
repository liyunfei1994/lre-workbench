"""
geometry/preburners.py

预燃室几何（简化估算）：设截面燃气速度 ~30 m/s 定截面积，用 L*≈0.6 m 定容积，
直径/长度再做工程钳位。富氧/富燃两台分别驱动液氧/甲烷涡轮（全流量分级燃烧）。
"""

from __future__ import annotations

import math

from .types import PreburnerGeometry


def compute_preburner(name: str, mdot_pb: float, pressure: float) -> PreburnerGeometry:
    """
    参数
    ────
    name      : 预燃室名称
    mdot_pb   : 通过预燃室的总质量流量 [kg/s]
    pressure  : 预燃室压力 [Pa]
    """
    pi = math.pi
    # 简化估算：假设预燃室截面速度 ~30 m/s
    rho_hot = pressure / (400.0 * 800.0)  # 近似
    rho_hot = max(rho_hot, 5.0)
    A_pb = mdot_pb / max(rho_hot * 30.0, 0.1)
    r_pb = math.sqrt(A_pb / pi)
    r_pb = max(0.06, min(0.25, r_pb))
    l_star_pb = 0.6
    V_pb = l_star_pb * A_pb
    L_pb = V_pb / A_pb
    L_pb = max(0.10, min(0.50, L_pb))
    return PreburnerGeometry(
        name=name,
        diameter_m=2.0 * r_pb,
        length_m=L_pb,
        volume_m3=pi * r_pb * r_pb * L_pb,
    )
