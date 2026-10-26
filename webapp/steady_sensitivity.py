"""Finite-interval response gains along an existing fixed-throat throttle scan.

This is postprocessing, not a new solver, an independent-parameter derivative,
or a calibrated uncertainty model. Failed or missing scan points remain gaps.
"""
from __future__ import annotations

import math


METRICS = (
    ("thrust_N", "实际推力", "N"),
    ("pc_Pa", "主燃烧室压力", "Pa"),
    ("Isp_s", "环境压力下比冲", "s"),
    ("mdot_total_kg_s", "总质量流量", "kg/s"),
    ("lox_pump_out_pressure_Pa", "氧泵出口压力", "Pa"),
    ("fuel_pump_out_pressure_Pa", "燃料泵出口压力", "Pa"),
    ("ox_preburner_pressure_Pa", "富氧预燃室压力", "Pa"),
    ("fuel_preburner_pressure_Pa", "富燃预燃室压力", "Pa"),
    ("gg_pressure_Pa", "燃气发生器压力", "Pa"),
    ("ox_pump_power_MW", "氧泵功率", "MW"),
    ("fuel_pump_power_MW", "燃料泵功率", "MW"),
    ("chamber_temperature_K", "主燃烧室温度", "K"),
    ("ox_preburner_T_combustion_K", "富氧预燃室温度", "K"),
    ("fuel_preburner_T_combustion_K", "富燃预燃室温度", "K"),
    ("gg_T_combustion_K", "燃气发生器温度", "K"),
)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if abs(value) > 1e100 or not math.isfinite(value):
        return None
    return float(value)


def _value(point, key):
    if key == "pc_Pa":
        return _finite(point.get(key))
    summary = point.get("summary")
    return _finite(summary.get(key)) if isinstance(summary, dict) else None


def _valid(point, reference_area):
    if not isinstance(point, dict) or point.get("converged") is not True:
        return False
    assessment = point.get("assessment") or {}
    geometry = point.get("geometry") or {}
    if assessment.get("numerical", {}).get("status") != "pass" or geometry.get("status") != "pass":
        return False
    area = _finite(geometry.get("throat_area_m2"))
    return area is not None and area > 0 and abs(area / reference_area - 1) < 5e-4


def throttle_response_gains(result):
    """Return JSON-safe coupled response gains without modifying the scan.

    u is the commanded target-thrust fraction, not elapsed time. Normalization
    uses the same scan's valid 100% output Y100. Slopes use adjacent requested
    positions only: G = ((Yb-Ya)/Y100) / (ub-ua), with u100 = 1.
    System-constraint failures may retain a mathematical response, clearly
    marked review; they never become validated operating intervals.
    """
    output = {
        "schema_version": 1,
        "method": "adjacent_fixed_throat_secant",
        "label": "有限区间节流响应增益",
        "status": "unavailable",
        "formula": "G = ((Y_b - Y_a) / Y_100) / (u_b - u_a); u_100 = 1",
        "reference": None,
        "intervals": [],
        "metrics": [],
        "limitations": [
            "增益表示固定主喷管喉部扫描中的耦合响应，是有限区间割线，不是独立模型参数的偏导数。",
            "以同次扫描的有效100%结果归一化；不跨越失败、缺失、重复或喉部闭合失效的工况。",
            "增益随扫描步长与区间变化；未提供步长收敛、重复性试验或统计置信区间。",
            "系统约束未通过的工况只用于诊断；区间增益不能证明实机可用节流范围。",
            "输出继承原节流扫描的固定效率、压降与局部模型假设；不是完整固定硬件特性图计算。",
        ],
    }
    if not isinstance(result, dict):
        output["reason"] = "缺少节流扫描结果。"
        return output
    request = result.get("request") or {}
    minimum = _finite(request.get("throttle_min"))
    count = request.get("throttle_steps")
    area = _finite((result.get("baseline") or {}).get("throat_area_m2"))
    if (minimum is None or not 0 < minimum < 1 or isinstance(count, bool)
            or not isinstance(count, int) or not 2 <= count <= 15 or area is None or area <= 0):
        output["reason"] = "缺少有效的请求网格或固定主喉部基准。"
        return output
    by_index = {}
    for point in result.get("points") or []:
        if not isinstance(point, dict):
            continue
        index = point.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= count:
            continue
        ratio = _finite(point.get("throttle_ratio"))
        expected = minimum + (1 - minimum) * (index - 1) / (count - 1)
        if index in by_index:
            by_index[index] = None  # A duplicate is ambiguous, never pick one.
        elif ratio is None or not math.isclose(ratio, expected, rel_tol=0, abs_tol=1e-9):
            by_index[index] = None
        else:
            by_index[index] = point
    reference = by_index.get(count)
    if not _valid(reference, area):
        output["reason"] = "需要同次扫描中数值收敛且主喉部闭合的100%工况；计算过程中可先查看性能曲线。"
        return output
    reference_status = "pass" if reference.get("assessment", {}).get("status") == "pass" else "review"
    output["reference"] = {"point_index": count, "throttle_ratio": 1.0, "status": reference_status}
    for index in range(1, count):
        left, right = by_index.get(index), by_index.get(index + 1)
        lower = minimum + (1 - minimum) * (index - 1) / (count - 1)
        upper = minimum + (1 - minimum) * index / (count - 1)
        valid = _valid(left, area) and _valid(right, area)
        status = "unavailable"
        if valid:
            status = ("pass" if reference_status == "pass" and all(
                point.get("assessment", {}).get("status") == "pass" for point in (left, right)
            ) else "review")
        output["intervals"].append({
            "from_index": index, "to_index": index + 1,
            "from_ratio": lower, "to_ratio": upper, "step_ratio": upper - lower,
            "status": status,
            "reason": ("相邻工况或100%参考点存在未通过/未确认的系统约束。" if status == "review"
                       else "缺少相邻有效工况，不跨点计算。" if not valid else ""),
        })
    for key, label, unit in METRICS:
        if not any(isinstance(point, dict) and _value(point, key) is not None for point in by_index.values()):
            continue
        reference_value = _value(reference, key)
        metric = {"key": key, "label": label, "unit": unit, "reference_value": reference_value,
                  "segments": [], "near_nominal_gain": None, "gain_min": None, "gain_max": None}
        for interval in output["intervals"]:
            segment = {**interval, "gain": None, "slope_per_ratio": None}
            left, right = by_index.get(interval["from_index"]), by_index.get(interval["to_index"])
            if interval["status"] != "unavailable":
                ya, yb = _value(left, key), _value(right, key)
                if reference_value is None or reference_value <= 0 or ya is None or yb is None or ya <= 0 or yb <= 0:
                    segment.update(status="unavailable", reason="该指标缺少有限正值或有效的100%归一化值。")
                else:
                    slope = (yb - ya) / interval["step_ratio"]
                    gain = slope / reference_value
                    if math.isfinite(gain) and math.isfinite(slope):
                        segment.update(gain=gain, slope_per_ratio=slope)
                    else:
                        segment.update(status="unavailable", reason="差分结果非有限，不能计算增益。")
            metric["segments"].append(segment)
        gains = [segment["gain"] for segment in metric["segments"] if segment["gain"] is not None]
        if gains:
            metric.update(gain_min=min(gains), gain_max=max(gains))
        metric["near_nominal_gain"] = metric["segments"][-1]["gain"]
        output["metrics"].append(metric)
    segments = [segment for metric in output["metrics"] for segment in metric["segments"]]
    if any(segment["gain"] is not None for segment in segments):
        output["status"] = "available" if all(segment["status"] == "pass" for segment in segments) else "partial"
        output["reason"] = "相邻有效工况的响应增益已计算；复核区间与缺口分别保留。"
    else:
        output["reason"] = "没有可计算的相邻有效区间。"
    return output
