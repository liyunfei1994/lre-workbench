"""Postprocess constraints without changing the cycle model or solver.

This is a first-pass check of constraints actually available in CycleResults.
Numerical convergence and engineering constraints deliberately have separate
statuses. A passed check does not certify a complete engine design.
"""
from __future__ import annotations

import math

from materials import material_evidence


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def numerical_convergence(results) -> dict:
    summary = results.summary
    residual = _finite(summary.get("solver_residual_norm"))
    warning = bool(summary.get("solver_warning", 0.0))
    status = "unknown" if residual is None else ("pass" if residual < 1e-3 and not warning else "fail")
    return {"status": status, "label": {"pass": "数值收敛", "fail": "未收敛", "unknown": "缺少收敛信息"}[status],
            "residual": residual, "tolerance": 1e-3, "solver_warning": warning,
            "iterations": _finite(summary.get("solver_iterations"))}


def assess_engineering(results, power_tolerance_percent: float = 1.0) -> dict:
    """Return JSON-compatible checks of pressure, shaft power and temperature.

    The power tolerance is a displayed screening criterion, not the solver's
    stopping tolerance. Missing/nonfinite quantities are never treated as pass.
    """
    power_tolerance_percent = _finite(power_tolerance_percent)
    if power_tolerance_percent is None or power_tolerance_percent < 0:
        raise ValueError("power_tolerance_percent must be finite and nonnegative")
    summary = results.summary
    inputs = results.inputs
    components = results.components
    states = results.states
    cycle_type = getattr(inputs, "cycle_type", summary.get("cycle_type_text", "ffsc"))
    checks = []

    def check(key, label, value, limit, unit, sense, detail=""):
        value, limit = _finite(value), _finite(limit)
        if value is None or limit is None:
            status = "unknown"
        elif sense == "minimum":
            status = "pass" if value >= limit - 1e-9 else "fail"
        elif sense == "maximum":
            status = "pass" if value <= limit else "fail"
        else:
            raise ValueError(sense)
        checks.append({"key": key, "label": label, "status": status, "value": value,
                       "limit": limit, "unit": unit, "criterion": sense, "detail": detail})

    def component_output(name, key):
        component = components.get(name)
        return getattr(component, "outputs", {}).get(key) if component is not None else None

    def state_pressure(name):
        state = states.get(name)
        return _finite(getattr(state, "pressure", None))

    if cycle_type == "ffsc":
        for key, label in (("crossflow_fuel_to_oxpb_margin_MPa", "CH₄ → 富氧预燃室供给压力裕度"),
                           ("crossflow_lox_to_fuelpb_margin_MPa", "LOX → 富燃预燃室供给压力裕度")):
            check(key, label, summary.get(key), 0.0, "MPa", "minimum",
                  "供给压力扣除阀路损失后必须达到预燃室压力与喷注压降；独立补压泵不在主轴功率平衡内。")
        for side, label in (("ox", "富氧"), ("fuel", "富燃")):
            pressure = _finite(summary.get(f"{side}_turbine_exhaust_pressure_Pa"))
            chamber = _finite(summary.get("chamber_pressure_Pa", getattr(inputs, "pc", None)))
            dp_ratio = _finite(getattr(inputs, f"{side}_turbine_exhaust_dp_ratio", None))
            margin = ((pressure - chamber * (1.0 + dp_ratio)) / 1e6
                      if None not in (pressure, chamber, dp_ratio) else None)
            check(f"{side}_exhaust_feed_margin_MPa", f"{label}涡轮排气 → 主燃烧室压力裕度",
                  margin, 0.0, "MPa", "minimum", "采用输入的涡轮排气段压降预算。")
    elif cycle_type == "gg":
        chamber = _finite(summary.get("chamber_pressure_Pa", getattr(inputs, "pc", None)))
        valve = _finite(summary.get("valve_line_dp_MPa"))
        for side, state, label in (("ox", "lox_after_pump", "LOX 主室供给压力裕度"),
                                   ("fuel", "fuel_after_cooling", "CH₄ 主室供给压力裕度")):
            supply = state_pressure(state)
            injector = _finite(summary.get(f"injector_dp_{side}_MPa"))
            margin = (supply / 1e6 - chamber / 1e6 - valve - injector
                      if None not in (supply, chamber, valve, injector) else None)
            check(f"{side}_main_feed_margin_MPa", label, margin, 0.0, "MPa", "minimum",
                  "泵后/冷却后状态压力扣除阀路和主喷注器压降。")
        pump_ox, pump_fuel = state_pressure("lox_after_pump"), state_pressure("fuel_after_pump")
        gg_pressure = _finite(summary.get("gg_pressure_Pa"))
        ratio = _finite(getattr(inputs, "gg_injector_dp_ratio", None))
        gg_margin = ((min(pump_ox, pump_fuel) - gg_pressure * (1 + ratio)) / 1e6 - valve
                     if None not in (pump_ox, pump_fuel, gg_pressure, ratio, valve) else None)
        check("gg_feed_margin_MPa", "燃气发生器供给压力裕度", gg_margin, 0.0, "MPa", "minimum",
              "采用 GG 模型从泵出口取流的供给路径。")

    power_sides = [("", "主轴总功率平衡")]
    if cycle_type == "ffsc" or bool(summary.get("gg_layout_dual", 0.0)):
        power_sides += [("ox_side_", "氧侧轴功率平衡"), ("fuel_side_", "燃料侧轴功率平衡")]
    for prefix, label in power_sides:
        error = _finite(summary.get(f"{prefix}power_error_percent" if prefix else "power_balance_error_percent"))
        if error is None:
            ratio_key = f"{prefix}power_balance_ratio" if prefix else "power_balance_ratio"
            ratio = _finite(summary.get(ratio_key))
            error = 100 * (ratio - 1.0) if ratio is not None else None
        check(f"{prefix}power_error_percent", label, abs(error) if error is not None else None,
              power_tolerance_percent, "%", "maximum",
              f"绝对功率差 ≤ {power_tolerance_percent:g}%；GG 比值已含机械效率；不包含外部增压/补压泵。")

    temperatures = ([('gg_T_combustion_K', 'gg_temp_limit', '燃气发生器出口温度')]
                    if cycle_type == "gg" else
                    [('ox_preburner_T_combustion_K', 'ox_preburner_temp_limit', '富氧预燃室出口温度'),
                     ('fuel_preburner_T_combustion_K', 'fuel_preburner_temp_limit', '富燃预燃室出口温度')])
    for result_key, input_key, label in temperatures:
        check(result_key, label, summary.get(result_key), getattr(inputs, input_key, None), "K", "maximum",
              "与当前工况设定的材料温度上限比较。")

    marching = components.get("cooling_marching")
    if marching is not None:
        hydraulic = _finite(component_output("cooling_marching", "hydraulic_infeasible"))
        if hydraulic is None:
            hydraulic = _finite(summary.get("cooling_marching_infeasible"))
        check("cooling_hydraulics", "冷却通道水力可行性", hydraulic, 0.0, "", "maximum",
              "通道水力不可行时沿程壁温结果无效。")
        wall = component_output("cooling_marching", "T_wall_hot_max_K") if hydraulic == 0 else None
        check("wall_temperature", "沿程热面壁温峰值", wall,
              getattr(inputs, "cooling_wall_temp_limit_K", None), "K", "maximum",
              "沿程冷却后处理；水力校核不通过或缺少数据时不判定壁温通过。")
    else:
        check("wall_temperature", "喉部单点热面壁温", component_output("cooling_channel", "T_wall_hot_K"),
              getattr(inputs, "cooling_wall_temp_limit_K", None), "K", "maximum",
              "只有循环单点估算；尚未检查全程壁温峰值。")

    material_records = {}
    for name in vars(inputs):
        if name.endswith("_material"):
            key = getattr(inputs, name)
            if not isinstance(key, str) or not key:
                continue
            try:
                material_records[name] = material_evidence(key)
            except KeyError:
                material_records[name] = {"key": key, "status": "unknown", "status_label": "缺少材料数据",
                                          "estimated": True, "oxygen_compatibility_status": "unknown"}
    failures = sum(row["status"] == "fail" for row in checks)
    unknown = sum(row["status"] == "unknown" for row in checks)
    placeholder = any(record["status"] in ("placeholder", "unknown") for record in material_records.values())
    status = "fail" if failures else ("unknown" if unknown else ("review" if placeholder else "pass"))
    numerical = numerical_convergence(results)
    if numerical["status"] != "pass" and status == "pass":
        status = "unknown"
    label = {"fail": "工程约束未通过", "unknown": "工程约束待确认", "review": "材料数据待核实",
             "pass": "已检查的工程约束通过"}[status]
    return {"schema_version": 1, "cycle_type": cycle_type, "numerical": numerical,
            "status": status, "label": label, "checks": checks, "failure_count": failures,
            "unknown_count": unknown, "materials": material_records,
            "limitations": ["本校核只覆盖模型已有的供给压力、轴功率与温度约束，不代表整机设计认证。",
                            "材料数据为简化工程数据；富氧相容性需要具体压力、温度、表面状态下的试验依据。",
                            "未完成结构疲劳、燃烧稳定性、完整瞬态、寿命及试验验证。"],
            "model_warnings": list(results.warnings)}
