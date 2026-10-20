"""Validate a bounded inclusive parameter grid before starting any work."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import math

MAX_SCAN_POINTS = 200


def build_scan_points(start, end, step, max_points=MAX_SCAN_POINTS):
    try:
        values = [Decimal(str(value).strip()) for value in (start, end, step)]
    except (InvalidOperation, ValueError):
        raise ValueError("起始、终止和步长必须是有效数字。") from None
    if any(not value.is_finite() or not math.isfinite(float(value)) for value in values):
        raise ValueError("扫描参数必须是有限数字，不能使用 NaN 或无穷大。")
    if any(value != 0 and float(value) == 0 for value in values):
        raise ValueError("参数超出当前数值精度，请使用更合理的范围和步长。")
    first, last, stride = values
    if first <= 0 or last <= 0 or stride <= 0:
        raise ValueError("O/F 起始、终止和步长必须大于 0。")
    if last < first:
        raise ValueError("终止值必须大于或等于起始值。")
    intervals = int(((last - first) / stride).to_integral_value(rounding=ROUND_FLOOR))
    tail = first + stride * intervals < last
    count = intervals + 1 + int(tail)
    if count > max_points:
        minimum = (last - first) / Decimal(max_points - 1)
        raise ValueError(f"预计 {count:,} 个点，最多支持 {max_points} 个。"
                         f"请增大步长（至少约 {float(minimum):.8g}）或缩小范围；不会截断扫描。")
    points = [float(first + stride * i) for i in range(intervals + 1)]
    if tail:
        points.append(float(last))
    if any(b <= a for a, b in zip(points, points[1:])):
        raise ValueError("步长过小，超出当前数值精度；请增大步长。")
    return points


def verify_scan_replay(record, results):
    from sweep import _extract_record
    actual = _extract_record(float(record["of_target"]), results, record.get("side", "ox"))
    differences = []
    for key in ("of_actual", "T_PB_K", "pi_t", "p_in_MPa", "p_out_MPa", "mass_flow_kgs"):
        expected = record.get(key)
        observed = actual.get(key)
        if expected is None:
            continue
        if not isinstance(expected, (int, float)) or not isinstance(observed, (int, float)):
            differences.append(key)
            continue
        tolerance = 0.5 if key == "T_PB_K" else max(1e-6, abs(expected) * 1e-3)
        if not math.isfinite(observed) or not math.isfinite(expected) or abs(observed - expected) > tolerance:
            differences.append(key)
    if not actual["converged"]:
        differences.append("converged")
    return {"consistent": not differences, "different_fields": differences,
            "expected": record, "actual": actual}


def validate_thermo_scan(of_min, of_max, count, eps_text, pressure, fuel_temp, ox_temp):
    try:
        n = int(str(count).strip())
        lower, upper, pc, fuel, ox = map(float, (of_min, of_max, pressure, fuel_temp, ox_temp))
        eps = [float(value.strip()) for value in str(eps_text).split(",") if value.strip()] or [40.0, 80.0, 120.0]
    except (ValueError, OverflowError):
        raise ValueError("热力学扫描参数必须是有效数字，采样点数必须为整数。") from None
    values = [lower, upper, pc, fuel, ox, *eps]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("混合比、压力、温度与膨胀比必须为有限正数。")
    if upper < lower or n < 2 or any(value < 1 for value in eps):
        raise ValueError("请确保 O/F 终止 ≥ 起始、采样点数 ≥ 2，且膨胀比 ≥ 1。")
    total = n * len(eps)
    if total > MAX_SCAN_POINTS:
        raise ValueError(f"预计 {total:,} 个计算点，最多支持 {MAX_SCAN_POINTS} 个；请减少采样点或膨胀比组合。")
    return total
