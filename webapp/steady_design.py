"""Cheap, record-bound design evidence. No property queries or cycle re-solves.

The report separates equation closure, model coverage and hardware validation.
It can also project historical records without filling missing fields from a draft.
"""
from __future__ import annotations

from copy import deepcopy
import math

from src.convergence import numerical_convergence
from src.steady_closure import audit_energy
from src.steady_network import audit_network


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _aggregate(values):
    values = list(values)
    if any(x in ("fail", "inconsistent", "invalid_or_unavailable", "extrapolated", "out_of_range") for x in values):
        return "fail"
    if values and all(x in ("pass", "complete", "within_declared_ranges") for x in values):
        return "pass"
    return "review"


def _checked_value(row):
    if row.get("status") == "fail":
        return "fail"
    value, limit = row.get("value"), row.get("limit")
    if not _finite(value) or not _finite(limit):
        return "review"
    if row.get("criterion") not in ("minimum", "maximum"):
        return "review"
    satisfied = value >= limit if row["criterion"] == "minimum" else value <= limit
    if not satisfied:
        return "fail"
    if row.get("limit_status") in ("provisional", "unset"):
        return "review"
    return "pass" if row.get("status") == "pass" else "review"


def _supply_basis(states, parts, thermo):
    """Project saved evidence only, with no live property queries for old jobs."""
    evidence = {r.get("id"): r for r in _rows(thermo.get("pure_fluid_states"))}
    ports = []
    for side, label in (("fuel", "燃料"), ("lox", "液氧")):
        for suffix, station in (("tank", "贮箱出口"), ("boost_out", "增压泵出口"), ("pump_inlet", "主泵入口")):
            key = f"{side}_{suffix}"
            state = _dict(states.get(key))
            saved = _dict(evidence.get(key))
            screen = deepcopy(_dict(saved.get("supply_screen")))
            # A cached screen from a different port state must not be displayed.
            matches = all(_finite(state.get(a)) and state.get(a) == saved.get(b)
                          for a, b in (("temperature", "T_K"), ("pressure", "p_Pa")))
            if not matches:
                screen = {}
            ports.append({"id": key, "label": label+station, "T_K": state.get("temperature"),
                          "p_Pa": state.get("pressure"), "screen": screen,
                          "status": "recorded" if screen else "missing"})
    powers = [{"label": label, "power_W": _dict(_dict(parts.get(key)).get("outputs")).get("power_W")}
              for key, label in (("fuel_boost_pump", "燃料增压泵"), ("ox_boost_pump", "液氧增压泵"))]
    return {"ports": ports, "external_powers": powers,
            "description": "当前两路采用共用压力边界与前置增压方案。增压功由外部功源提供，计入整机能量账；主涡轮轴不承担该功。",
            "limits": "过冷度 = 该绝压下的饱和温度 − 液温。静压分量 = (p − p饱和)/(ρg)，未包含端口动能修正；未单独求解贮箱液柱、加速度与吸入管损，也没有 NPSHr 数据，不能判定抗汽蚀裕度通过。超临界区不定义饱和温度。历史记录未保存筛查时不回填。"}


def steady_design_view(result, record_id="", *, consistency=None):
    result = _dict(result)
    calc = _dict(result.get("calculation"))
    inputs, summary = _dict(calc.get("inputs")), _dict(calc.get("summary"))
    report = {"schema_version": 1, "record_id": record_id,
              "scope": "lox_methane_ffsc_steady_design", "status": "unavailable",
              "source": "所选保存记录；不读取当前草稿，不重新求解，不回填历史物性。"}
    if not calc:
        return report
    if inputs.get("cycle_type") != "ffsc" or inputs.get("propellant_pair") != "lox_methane":
        return report | {"status": "not_applicable"}
    states, parts = _dict(calc.get("states")), _dict(calc.get("components"))
    thermo = _dict(calc.get("thermodynamic_applicability"))
    handoff = _dict(result.get("gas_handoff"))
    assessment = _dict(result.get("assessment"))
    closure = _dict(calc.get("steady_closure"))
    network = _dict(closure.get("feed_network"))
    numerical = numerical_convergence(calc)
    energy = audit_energy(states, parts)
    feed = audit_network(network, total_fuel=summary.get("mdot_fuel_kg_s"),
                         total_ox=summary.get("mdot_ox_kg_s")) if network else {"status": "unavailable"}
    consistency = _dict(consistency if consistency is not None else result.get("result_consistency"))
    check_rows = _rows(assessment.get("checks"))
    constraints = _aggregate(_checked_value(row) for row in check_rows)
    gas_ranges = []
    for row in _rows(thermo.get("states")):
        value = _dict(row.get("thermo_range"))
        lo, hi, t = value.get("valid_temperature_min_K"), value.get("valid_temperature_max_K"), row.get("T_K")
        known = all(_finite(x) for x in (lo, hi, t))
        gas_ranges.append("extrapolated" if (known and not lo <= t <= hi) or value.get("outside_mass_fraction", 0)
                          else "within_declared_ranges" if known and value.get("outside_mass_fraction") == 0 else "unavailable")
    pure_ranges = []
    for row in _rows(thermo.get("pure_fluid_states")):
        limits = _dict(row.get("limits"))
        t, p, lo, hi, pm = [row.get("T_K"), row.get("p_Pa"), limits.get("Tmin_K"), limits.get("Tmax_K"), limits.get("pmax_Pa")]
        known = all(_finite(x) for x in (t, p, lo, hi, pm))
        valid = known and lo <= t <= hi and 0 < p <= pm
        single_phase = row.get("phase") in ("gas", "liquid", "supercritical", "supercritical_gas", "supercritical_liquid")
        violation = (known and not valid) or row.get("phase") in ("twophase", "two_phase")
        pure_ranges.append("invalid_or_unavailable" if violation or row.get("status") == "invalid_or_unavailable"
                           else "within_declared_ranges" if valid and single_phase and row.get("status") == "within_declared_ranges"
                           else "unavailable")
    range_status = _aggregate(gas_ranges + pure_ranges + ([] if gas_ranges and pure_ranges else ["unavailable"]))
    handoff_checks = [c for s in _rows(handoff.get("stations")) for c in _rows(s.get("checks"))]
    handoff_checks += [c for b in _rows(handoff.get("component_balances")) for c in _rows(b.get("checks"))]
    handoff_status = _aggregate([handoff.get("status")] + [c.get("status") for c in handoff_checks])
    if not handoff_checks:
        handoff_status = "review"
    for port in _rows(handoff.get("stations")):
        source = _dict(states.get(port.get("id")))
        for field, key in (("p_Pa", "pressure"), ("T_K", "temperature"), ("h_Jkg", "enthalpy"), ("mass_flow_kg_s", "mass_flow")):
            a, b = port.get(field), source.get(key)
            if _finite(a) and _finite(b) and not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8):
                handoff_status = "fail"
            elif not _finite(a) or not _finite(b):
                handoff_status = "fail" if handoff_status == "fail" else "review"
        if _dict(port.get("composition_mass_fractions")) != _dict(source.get("composition")):
            handoff_status = "fail"
    gates = []
    def gate(key, title, status, detail, target):
        gates.append({"id": key, "label": title, "status": status, "detail": detail, "target": target})
    gate("numerical", "数值收敛", numerical["status"], "内层匹配、热耦合、预燃室及延续求解分别验收。", "convergence")
    gate("feed", "流量与压力闭合", feed["status"], "按四条实际支路重核；最低需求压头不代表泵能力或压力储备。", "checks")
    gate("energy", "全循环能量收支", energy["status"], "从储箱到喷管出口，轴功与再生传热按边界分别记账。", "checks")
    gate("handoff", "组成、元素与焓交接", handoff_status, "检查实际两股涡轮排气及主室入口；总静态沿用明确保存的约定。", "models")
    gate("range", "物性声明范围", range_status, "检查最终燃气和纯推进剂端口；范围内不等于高压 EOS 或输运精度已验证。", "models")
    gate("constraints", "系统设计约束", constraints, "暂定预燃室温限、未设置壁温限值分别保留待确认。", "checks")
    gate("consistency", "跨页数据一致性", "fail" if consistency.get("conflict_count", 0) else
         "pass" if consistency.get("status") == "pass" else "review",
         f"已核对 {consistency.get('checked_count', 0)} 项；缺失映射不作为通过。", "models")
    # Model identity is read from saved evidence, never inferred from file names.
    models = [{"label": "气相热化学数据", "value": _dict(thermo.get("thermochemical_data")).get("id", "未记录")},
              {"label": "燃烧处理", "value": _dict(thermo.get("combustion")).get("label", "未记录")},
              {"label": "燃气状态方程", "value": _dict(thermo.get("gas_model")).get("label", "未记录")},
              {"label": "冷却模型", "value": _dict(thermo.get("cooling")).get("label", "未记录")},
              {"label": "热气膨胀", "value": _dict(thermo.get("nozzle")).get("label", "未记录")}]
    modules = []
    def module(key, title, ids, delivers, pending, target):
        available = all(bool(_dict(_dict(parts.get(i)).get("outputs"))) for i in ids)
        modules.append({"id": key, "label": title, "status": "recorded" if available else "missing",
                        "delivers": delivers, "pending": pending, "target": target, "component_ids": ids})
    module("feed", "泵、阀门与供给", ["ox_pump", "fuel_pump"], "四支路流量、压力预算、所需泵功与调节压降", "泵特性图、汽蚀裕度、阀门流量系数及开度范围", "checks")
    module("preburners", "富氧／富燃预燃室", ["ox_preburner", "fuel_preburner"], "实际入口焓与组成、出口平衡状态和元素收支", "高压混合物 EOS、凝聚相及平衡假设适用性", "models")
    module("turbines", "涡轮总体任务书", ["ox_preburner_turbine", "fuel_preburner_turbine"], "入口状态、流量、转速、压比、功率及完整燃气组成", "专业叶型设计、损失模型、性能图和结构校核", "models")
    module("chamber", "主燃烧室", ["main_chamber"], "接收两股实际涡轮排气，按统一化学焓求主室参考状态", "有限速率、混合效率及燃烧稳定性；不由平衡结果推定", "models")
    module("injectors", "三处喷注器接口", ["main_injector"], "预燃室与主室接口的流量／压降任务及概念通流尺寸", "主室气体可压缩流量关系、流量系数和元件试验", "checks")
    # Some historical versions name the main injector simply 'injector'.
    if not _dict(parts.get("main_injector")) and _dict(parts.get("injector")):
        modules[-1].update(component_ids=["injector"], status="recorded" if _dict(parts["injector"].get("outputs")) else "missing")
    module("cooling", "推力室与再生冷却", ["cooling_channel"], "统一通道的吸热、压降、出口焓与壁温约束", "换热／输运适用性、材料温区与制造状态", "cooling")
    module("nozzle", "喷管与整机性能", ["nozzle"], "采用本记录膨胀模型，输出推力、比冲及出口状态", "流动损失、分离及外部校核；按所用假设解释性能", "models")
    actions = [{"label": g["label"], "detail": g["detail"], "target": g["target"], "status": g["status"]}
               for g in gates if g["status"] != "pass"]
    for row in check_rows:
        if _checked_value(row) != "pass":
            actions.append({"label": row.get("label", "未命名约束"), "detail": row.get("detail", "查看原始判据"), "target": "checks", "status": _checked_value(row)})
    solid = _dict(thermo.get("solid_wall_thermal"))
    if solid.get("status") == "out_of_range":
        actions.append({"label": "壁材热物性超出来源温区", "detail": "壁温解仍需核对材料数据覆盖；该问题独立于能量闭合。", "target": "cooling", "status": "review"})
    actions.append({"label": "高压燃气与凝聚相定量筛查", "detail": "到热力学校核的“燃气交接与筛查”，选择同一计算记录运行；独立校核报告不自动替换本记录的生产物性。", "target": "models", "status": "review"})
    station_rows = [{"id": s.get("id"), "label": s.get("label"), "p_Pa": s.get("p_Pa"), "T_K": s.get("T_K"),
                     "mass_flow_kg_s": s.get("mass_flow_kg_s"), "h_Jkg": s.get("h_Jkg"),
                     "state_basis": deepcopy(s.get("state_basis")), "species_count": len(_dict(s.get("composition_mass_fractions"))),
                     "status": s.get("status")} for s in _rows(handoff.get("stations"))]
    report.update(status="blocked" if any(g["status"] == "fail" for g in gates) else "review_required",
                  gates=gates, models=models, modules=modules, actions=actions, stations=station_rows,
                  supply=_supply_basis(states, parts, thermo),
                  scope_label="液氧／甲烷 FFSC · 稳态总体设计基线",
                  property_definitions="气相 cp、cv 为固定组成热容，γ 为 cp/cv；Cantera sound_speed 为冻结声速。平衡导数另行定义，不混用。",
                  validation_status="not_certified", detail="本页组织总体设计证据；部件详细设计和试验验证仍按任务书继续。",
                  audit={"energy_status": energy["status"], "energy_residual_W": energy.get("residual_W"),
                         "energy_tolerance_W": energy.get("tolerance_W"), "feed_status": feed["status"]})
    return report
