"""主喷注器几何视图：读取已计算部件，避免与喷注器模型重复定尺寸。"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .types import InjectorGeometry


def _positive_number(outputs: Mapping, key: str) -> float | None:
    value = outputs.get(key)
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def compute_injector(
    rc: float,
    mdot_total: float,
    *,
    component_outputs: Mapping | None = None,
    component_profile: Mapping | None = None,
) -> InjectorGeometry:
    """复制 components.injector 的已保存尺寸，不重新计算或修复历史记录。

    rc、mdot_total 保留旧调用兼容性，不再用于独立估算 N 或面径。
    外筒壁厚、轴向长度、缩进及切向孔没有计算依据，故不在这里生成。
    """
    outputs = component_outputs if isinstance(component_outputs, Mapping) else {}
    profile = component_profile if isinstance(component_profile, Mapping) else {}
    contract = profile.get("geometry_contract", {})
    contract = contract if isinstance(contract, Mapping) else {}
    count = _positive_number(outputs, "num_elements")
    count = int(count) if count is not None and count.is_integer() else None
    passed = outputs.get("geometry_closure_passed")
    if isinstance(passed, (bool, int, float)) and passed in (0, 1):
        passed = bool(passed)
    else:
        passed = None
    warning_values = contract.get("warnings", [])
    warnings = [str(value) for value in warning_values] if isinstance(warning_values, list) else []
    status = str(contract.get("status", "legacy_unverified")) if outputs else "unavailable"
    if outputs and passed is None:
        warnings.append("历史记录未保存几何流量闭合校核；尺寸仅按原记录显示。")
    if not outputs:
        warnings.append("记录未保存主喷注器计算结果；未根据总流量或当前默认值补造几何。")
    return InjectorGeometry(
        face_diameter_m=_positive_number(outputs, "face_diameter_m"),
        face_area_m2=_positive_number(outputs, "face_area_m2"),
        num_elements=count,
        element_spacing_mm=_positive_number(outputs, "element_spacing_mm"),
        d_ox_mm=_positive_number(outputs, "d_ox_mm"),
        d_post_mm=_positive_number(outputs, "d_post_mm"),
        d_outer_mm=_positive_number(outputs, "d_outer_mm"),
        delta_fuel_mm=_positive_number(outputs, "delta_fuel_mm"),
        post_wall_thickness_mm=_positive_number(outputs, "post_wall_thickness_mm"),
        geometry_closure_passed=passed,
        geometry_source="components.injector.outputs" if outputs else "unavailable",
        geometry_status=status,
        geometry_warnings=warnings,
    )
