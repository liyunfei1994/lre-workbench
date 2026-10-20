"""
geometry/injector.py

喷注器几何（同轴剪切式）：面板与燃烧室同径，单元数按总流量估算。
"""

from __future__ import annotations

import math

from .types import InjectorGeometry


def compute_injector(rc: float, mdot_total: float) -> InjectorGeometry:
    """
    参数
    ────
    rc          : 燃烧室半径 [m]（喷注面与燃烧室同径）
    mdot_total  : 总质量流量 [kg/s]
    """
    pi = math.pi
    injector_diameter = 2.0 * rc  # 与燃烧室同径
    injector_area = pi * rc * rc
    # 同轴剪切喷注器，每个元件流量约 0.5–1.5 kg/s
    n_elements = max(20, int(mdot_total / 1.0))
    element_spacing = 1000.0 * math.sqrt(injector_area / max(n_elements, 1))  # mm

    return InjectorGeometry(
        face_diameter_m=injector_diameter,
        face_area_m2=injector_area,
        num_elements=n_elements,
        element_spacing_mm=element_spacing,
    )
