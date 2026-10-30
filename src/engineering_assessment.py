"""Postprocess constraints without changing the cycle model or solver.

This is a first-pass check of constraints actually available in CycleResults.
Numerical convergence and engineering constraints deliberately have separate
statuses. A passed check does not certify a complete engine design.
"""
from __future__ import annotations

import math

from materials import material_evidence
from convergence import numerical_convergence
from core import preburner_temperature_constraint, wall_temperature_constraint


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _cooling_profile_evidence(component, inputs):
    """Read complete common-mesh evidence; never use an old postpass/single point."""
    missing = (None, None, "缺少主循环完整沿程冷却证据；预算模式或旧记录须重新计算，不能用单点温度代替沿程峰值。")
    profile = getattr(component, "profile", None)
    if not bool(getattr(inputs, "cooling_bartz_mode", False)) or not isinstance(profile, dict):
        return missing
    if profile.get("version") != 1 or profile.get("model_id") != "regen_quasi1d_ph_v1":
        return missing
    nodes, cells = profile.get("nodes"), profile.get("cells")
    geometry = profile.get("geometry") or {}
    if not isinstance(geometry, dict):
        return missing
    geometric_cells, geometric_nodes = geometry.get("cells"), geometry.get("nodes")
    if not all(isinstance(value, list) and value for value in
               (nodes, cells, geometric_cells, geometric_nodes)):
        return missing
    if (not all(isinstance(value, dict) for value in nodes + cells + geometric_cells + geometric_nodes)
            or len(nodes) != len(cells) + 1 or len(geometric_nodes) != len(geometric_cells) + 1
            or len(cells) != len(geometric_cells) + 2
            or cells[0].get("kind") != "inlet_manifold" or cells[-1].get("kind") != "outlet_manifold"
            or nodes[0].get("kind") != "inlet_port" or nodes[-1].get("kind") != "outlet_port"
            or any(cell.get("kind") != "channel" for cell in cells[1:-1])):
        return missing
    # The complete cooled coverage must be present, including both manifold
    # endpoints. Positive/recovering static pressure need not be monotonic.
    for actual, expected in zip(nodes[1:-1], geometric_nodes):
        for key in ("s_m", "x_m"):
            first, second = _finite(actual.get(key)), _finite(expected.get(key))
            if first is None or second is None or abs(first - second) > 1e-9:
                return missing
    validity = profile.get("validity") or {}
    if not isinstance(validity, dict):
        return missing
    if validity.get("status") not in ("pass", "review", "fail"):
        return missing
    hydraulic_failed = validity.get("status") == "fail"
    for node in nodes:
        values = [_finite(node.get(key)) for key in ("p_Pa", "T_K", "rho_kgm3", "Mach")]
        if any(value is None for value in values) or node.get("range_status") != "within_declared_ranges":
            return missing
        hydraulic_failed |= any(value <= 0 for value in values[:3]) or not 0 <= values[3] < 1
    for index, cell in enumerate(cells):
        losses = [_finite(cell.get(key)) for key in ("dp_friction_Pa", "dp_momentum_Pa", "dp_local_Pa")]
        if any(value is None for value in losses):
            return missing
        momentum_residual = _finite(nodes[index + 1]["p_Pa"]) - _finite(nodes[index]["p_Pa"]) + sum(losses)
        hydraulic_failed |= abs(momentum_residual) > .5 or losses[0] < 0 or losses[2] < 0
    if hydraulic_failed:
        return 1.0, None, "主循环沿程记录存在无效状态或水力方程残差；不据此判定壁温通过。"
    temperatures = [_finite(cell.get("T_wall_hot_K")) for cell in cells[1:-1]]
    wall_residuals = [_finite(cell.get("wall_residual_K")) for cell in cells[1:-1]]
    wall = (max(temperatures) if all(value is not None and value > 0 for value in temperatures)
            and all(value is not None and abs(value) <= 1e-5 for value in wall_residuals) else None)
    return 0.0, wall, ("读取主循环唯一冷却剖面的全部覆盖单元，重算压力／动量残差并取热面壁温最大值；"
                       "与用户热设计限值比较，不代表结构强度、疲劳寿命或硬件换热关联式已验证。")


def assess_engineering(results, power_tolerance_percent: float = 1.0, *, scope: str = "engineering") -> dict:
    """Return JSON-compatible checks of pressure, shaft power and temperature.

    The power tolerance is a displayed screening criterion, not the solver's
    stopping tolerance. Missing/nonfinite quantities are never treated as pass.
    Both scopes include the common cooling profile's hydraulic and wall-temperature
    limits. Only the desktop engineering scope includes material evidence.
    """
    if scope not in ("engineering", "system"):
        raise ValueError("scope must be engineering or system")
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

    def state_pressure(name):
        state = states.get(name)
        return _finite(getattr(state, "pressure", None))

    if cycle_type == "ffsc":
        from steady_network import audit_network
        from steady_closure import audit_energy
        closure = getattr(results, "steady_closure", {}) or {}
        feed_pressure_tolerance_pa = 0.0
        if closure.get("schema_version") == 1:
            network = audit_network(closure.get("feed_network", {}),
                total_fuel=summary.get("mdot_fuel_kg_s"), total_ox=summary.get("mdot_ox_kg_s"),
                mr_ox=summary.get("ox_preburner_mr_target") if summary.get("solve_mode") == 2 else None,
                mr_fuel=summary.get("fuel_preburner_mr_target") if summary.get("solve_mode") == 2 else None)
            energy = audit_energy(states, components)
            # Use the independently recomputed network tolerance, never a saved
            # badge/tolerance. Preserve raw signed margins; sub-Pa closure error
            # is not an engineering pressure reserve or a real supply deficit.
            if network["status"] == "pass":
                feed_pressure_tolerance_pa = network["pressure_tolerance_Pa"]
            check("feed_network_closure", "四支路供给方程闭合", 0.0 if network["status"] == "pass" else 1.0,
                  0.0, "", "maximum", "由记录支路重新核算质量、压力、阻力与非负阀压降；闭合不代表具有压力储备。")
            heat_terms = [_finite(getattr(components.get(name), "outputs", {}).get("heat_loss_W"))
                          for name in ("main_chamber", "nozzle")]
            heat_allocation = (
                f"本记录主室控制体扣热 {heat_terms[0]/1e6:g} MW、喷管控制体扣热 {heat_terms[1]/1e6:g} MW。"
                if all(value is not None for value in heat_terms) else "本记录缺少完整的主室／喷管扣热分工数据。")
            check("energy_ledger_closure", "声明边界内的能量账闭合",
                  0.0 if energy["status"] == "pass" else None if energy["status"] == "unavailable" else 1.0,
                  0.0, "", "maximum", "从同基准端口状态及各控制体实际扣热重算能量账；" + heat_allocation
                  + "再生冷却吸热属于内部传热；能量闭合不替代换热模型适用性验证。")
        else:
            check("feed_network_closure", "四支路供给方程闭合", None, 0.0, "", "maximum", "旧记录缺少完整方程证据，需重算。")
            check("energy_ledger_closure", "声明边界内的能量账闭合", None, 0.0, "", "maximum", "旧记录缺少同基准能量账，需重算。")
        for key, label in (("crossflow_fuel_to_oxpb_margin_MPa", "CH₄ → 富氧预燃室供给压力裕度"),
                           ("crossflow_lox_to_fuelpb_margin_MPa", "LOX → 富燃预燃室供给压力裕度")):
            tolerance_detail = (f" 独立网络审计通过，允许 {feed_pressure_tolerance_pa:g} Pa 数值闭合误差；"
                                "保留原始有符号值，接近零不代表具有抗扰压力储备。"
                                if feed_pressure_tolerance_pa else "")
            check(key, label, summary.get(key), -feed_pressure_tolerance_pa / 1e6, "MPa", "minimum",
                  "供给压力扣除基础管损后相对喷注需求的压头；新模型将多余压头耗散于调节阀，不含额外补压设备。"
                  + tolerance_detail)
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
                                   ("fuel", "fuel_after_cooling", "燃料主室供给压力裕度")):
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

    if cycle_type == "gg":
        check("gg_T_combustion_K", "燃气发生器出口温度", summary.get("gg_T_combustion_K"),
              getattr(inputs, "gg_temp_limit", None), "K", "maximum",
              "与当前工况设定的气体出口温度上限比较；气体温度不等于金属温度。")
    else:
        for side, label in (("ox", "富氧"), ("fuel", "富燃")):
            constraint = preburner_temperature_constraint(inputs, side)
            result_key = f"{side}_preburner_T_combustion_K"
            check(result_key, f"{label}预燃室出口温度", summary.get(result_key),
                  constraint["limit_K"], "K", "maximum")
            row = checks[-1]
            row.update(limit_status=constraint["status"], limit_basis=constraint["basis"])
            if constraint["status"] == "unset":
                row["detail"] = "该项尚未评估：未设定出口温度上限，保存的旧数值不参与判断。"
            elif constraint["status"] == "provisional":
                if row["status"] == "pass":
                    row["status"] = "review"
                row["detail"] = "仅与方案暂定上限比较；限值依据待核实，处于暂定上限内不代表工程验收通过。"
            elif constraint["status"] == "documented" and constraint["basis"]:
                row["detail"] = ("与已有用户填写依据的方案上限比较。气体温度不等于金属温度；"
                                 "填写依据不等于已完成材料、寿命或氧相容性验证。")
            else:
                row["status"] = "unknown"
                row["detail"] = "温度约束状态无效或缺少依据，该项尚未评估。"

    hydraulic, wall, cooling_detail = _cooling_profile_evidence(components.get("cooling_channel"), inputs)
    check("cooling_hydraulics", "冷却通道水力可行性", hydraulic, 0.0, "", "maximum", cooling_detail)
    wall_constraint = wall_temperature_constraint(inputs)
    check("wall_temperature", "沿程热面壁温峰值", wall,
          wall_constraint["limit_K"], "K", "maximum", cooling_detail)
    row = checks[-1]
    row.update(limit_status=wall_constraint["status"], limit_basis=wall_constraint["basis"])
    if wall_constraint["status"] == "unset":
        row["detail"] += " 该项尚未评估：未设定方案壁温上限，保留的示例数值不参与判断。"
    elif wall_constraint["status"] == "provisional":
        if row["status"] == "pass":
            row["status"] = "review"
        row["detail"] += " 仅与方案暂定上限比较，限值依据待核实；它不是材料数据温区上限。"
    elif wall_constraint["status"] == "documented" and wall_constraint["basis"]:
        row["detail"] += " 与登记了来源和适用条件的方案壁温上限比较；不代表材料、结构或寿命认证。"
    else:
        row["status"] = "unknown"
        row["detail"] += " 温度约束状态无效或缺少依据，该项尚未评估。"

    material_records = {}
    for name in (vars(inputs) if scope == "engineering" else ()):
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
    review = sum(row["status"] == "review" for row in checks)
    placeholder = any(record["status"] in ("placeholder", "unknown") for record in material_records.values())
    status = "fail" if failures else ("unknown" if unknown else ("review" if review or placeholder else "pass"))
    numerical = numerical_convergence(results)
    if numerical["status"] != "pass" and status == "pass":
        status = "unknown"
    label = {"fail": "工程约束未通过", "unknown": "工程约束待确认", "review": "工程约束依据待核实",
             "pass": "已检查的工程约束通过"}[status]
    limitations = ["本校核只覆盖模型已有的供给压力、轴功率与温度约束，不代表整机设计认证。",
                   "材料数据为简化工程数据；富氧相容性需要具体压力、温度、表面状态下的试验依据。",
                   "未完成结构疲劳、燃烧稳定性、完整瞬态、寿命及试验验证。"]
    if scope == "system":
        label = {"fail": "系统约束未通过", "unknown": "系统约束待确认", "review": "系统约束依据待核实",
                 "pass": "已检查的系统约束通过"}[status]
        limitations = ["本校核覆盖稳态设计点的供给压力、轴功率、预燃室／燃气发生器出口温度，以及统一冷却剖面的水力与沿程壁温限值。",
                       "沿程壁温属于系统热设计约束，与数值收敛独立判定；预算模式或缺少完整剖面时标记待确认。此项不是材料强度、结构或疲劳验证。",
                       "系统约束通过不代表部件设计或重复使用能力已验证；启动、关机、再启动瞬态、疲劳寿命和试验验证需后续完成。"]
    return {"schema_version": 1, "scope": scope, "cycle_type": cycle_type, "numerical": numerical,
            "status": status, "label": label, "checks": checks, "failure_count": failures,
            "unknown_count": unknown, "review_count": review, "materials": material_records,
            "limitations": limitations,
            "model_warnings": list(results.warnings)}
