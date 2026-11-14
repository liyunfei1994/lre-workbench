"""Explicit concept valve hardware and a bounded dense-fluid screening model.

CdA is effective area, not a manufactured bore or a measured valve map.  This
module never substitutes four legacy balancing losses for three real valves.
The pressure law is a low-compressibility, single-phase candidate approximation;
HEOS inlet properties and domain checks are required for a reported assessment.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math


SCHEMA_VERSION = 1
VALVE_IDS = ("MFV", "OPFV", "FPOV")
MODES = ("requirement_sizing", "candidate_check")
MODEL_ID = "dense_single_phase_cda_v1"
MAX_PRESSURE_DROP_RATIO = 0.3
MAX_DENSITY_CHANGE_ESTIMATE = 0.1
MIN_DENSE_FLUID_DENSITY_KG_M3 = 100.0
# A starting reserve for concept sizing, not a certified control-authority or
# cavitation criterion. Save its evaluated pressure in each input snapshot.
DEFAULT_DESIGN_PRESSURE_DROP_RATIO = 0.01
DEFAULT_REFERENCE_CHAMBER_PRESSURE_PA = 33.0e6
RECOMMENDED_TOPOLOGY_ID = "paper_split_before_cooling_v1"
LIMITATIONS = [
    "CdA 为有效流通面积；没有流量系数及结构信息时不能解释为实际阀孔直径或完成工程选型。",
    "采用单相稠密流体孔口近似；HEOS 入口物性与压差/压缩性筛查不构成汽蚀、闪蒸、阻塞流或试验验证。",
    "默认线性曲线仅为显式概念假设；没有提供实测或厂家阀门特性。",
    "密度、压差比及压缩性阈值为本项目候选筛查假设，不是 IEC 通用有效域或精度保证。",
    "缺少压力恢复系数 FL；即使等焓出口保持单相，也不能证明缩流截面无汽蚀。",
    "阀门需求或候选开度校核不等于完整固定硬件发动机节流能力。",
]
TOPOLOGIES = {
    "paper_split_before_cooling_v1": {
        "label": "燃料泵后先分流；MFV 后冷却，OPFV 绕过冷却",
        "valves": {
            "MFV": ("CH4", "fuel_after_pump", "cooling_inlet"),
            "OPFV": ("CH4", "fuel_after_pump", "opfv_outlet"),
            "FPOV": ("O2", "lox_after_pump", "fpov_outlet"),
        },
    },
    "all_fuel_cooled_then_split_valves_v1": {
        "label": "全部燃料先经 MFV 和冷却，再分流至两预燃室",
        "valves": {
            "MFV": ("CH4", "fuel_after_pump", "cooling_inlet"),
            "OPFV": ("CH4", "fuel_after_cooling", "opfv_outlet"),
            "FPOV": ("O2", "lox_after_pump", "fpov_outlet"),
        },
    },
}
_FIELDS = frozenset({
    "id", "fluid", "upstream_node", "downstream_node", "characteristic_version",
    "rated_cda_m2", "area_curve", "opening_min", "opening_max", "reference_opening",
    "design_pressure_drop_Pa", "leak_fraction", "max_pressure_drop_ratio",
    "actuator_tau_s", "actuator_rate_per_s",
})


def _number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} 必须是有限数值。")
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} 数值超出有效范围。") from exc
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f"{label} 数值超出有效范围。")
    return value


def _unit(value, label):
    value = _number(value, label)
    if not 0 <= value <= 1:
        raise ValueError(f"{label} 必须位于 0–1。")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def recommended_design_pressure_drop(chamber_pressure_Pa):
    """Return the project's explicit 1% Pc preliminary reserve in SI units."""
    return DEFAULT_DESIGN_PRESSURE_DROP_RATIO * _number(chamber_pressure_Pa, "主燃烧室压力", positive=True)


def template_configuration(topology_id, mode="requirement_sizing", *,
                           chamber_pressure_Pa=DEFAULT_REFERENCE_CHAMBER_PRESSURE_PA):
    """Create a sizing template; never use this to fill a saved configuration.

    Pressure reserve is an initial engineering assumption. Candidate mode still
    requires supplied hardware area. Parsing retains all saved values verbatim.
    """
    if not isinstance(topology_id, str) or topology_id not in TOPOLOGIES or mode not in MODES:
        raise ValueError("不支持的阀门拓扑或分析模式。")
    pressure_drop = recommended_design_pressure_drop(chamber_pressure_Pa)
    valves = {}
    for valve_id, (fluid, upstream, downstream) in TOPOLOGIES[topology_id]["valves"].items():
        valves[valve_id] = dict(
            id=valve_id, fluid=fluid, upstream_node=upstream, downstream_node=downstream,
            characteristic_version="concept_linear_v1", rated_cda_m2=None,
            area_curve=[[0.0, 0.0], [1.0, 1.0]], opening_min=0.0, opening_max=1.0,
            reference_opening=0.7, design_pressure_drop_Pa=pressure_drop, leak_fraction=0.0,
            max_pressure_drop_ratio=0.1, actuator_tau_s=0.1, actuator_rate_per_s=1.0,
        )
    return dict(schema_version=SCHEMA_VERSION, enabled=True, topology_id=topology_id,
                mode=mode, valves=valves)


def valve_configuration_catalog():
    return dict(schema_version=SCHEMA_VERSION, disabled=dict(schema_version=1, enabled=False),
                recommended_topology_id=RECOMMENDED_TOPOLOGY_ID,
                pressure_drop_recommendation=dict(
                    id="concept_valve_minimum_1pct_pc_v1", chamber_pressure_ratio=DEFAULT_DESIGN_PRESSURE_DROP_RATIO,
                    reference_pressure_Pa=DEFAULT_REFERENCE_CHAMBER_PRESSURE_PA,
                    basis="初选最小压降 = 当前主燃烧室压力的 1%。这是本项目的概念设计余量假设，"
                          "不是通用标准或已验证的控制裕度；实际压差由网络配平，最终须结合调节范围、"
                          "泵功、阀门特性与汽蚀试验复核。该压降与管路、喷注器预算分别计算。",
                    source_url="https://ntrs.nasa.gov/citations/19740018866",
                    source_scope="NASA SP-8097 提供阀门综合选型原则，未规定此 1% 数值。"),
                templates={key: template_configuration(key) for key in TOPOLOGIES},
                topology_labels={key: value["label"] for key, value in TOPOLOGIES.items()},
                mode_labels={"requirement_sizing": "按工况提出有效面积需求",
                             "candidate_check": "按已定义候选特性校核开度"},
                model=dict(id=MODEL_ID, limitations=list(LIMITATIONS)))


def _validate_valve(value, mode, *, expected=None):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("阀门字段不完整或包含未知字段。")
    row = deepcopy(value)
    if row["id"] not in VALVE_IDS:
        raise ValueError("阀门 ID 必须为 MFV、OPFV 或 FPOV。")
    if expected is not None and tuple(row[key] for key in ("fluid", "upstream_node", "downstream_node")) != expected:
        raise ValueError(f"{row['id']} 的介质与上下游节点必须符合所选拓扑。")
    if row["fluid"] not in ("CH4", "O2"):
        raise ValueError("候选阀模型仅支持甲烷或氧纯流体。")
    for field in ("upstream_node", "downstream_node", "characteristic_version"):
        text = row[field]
        if not isinstance(text, str) or not text.strip() or len(text) > 128 or any(ord(c) < 32 for c in text):
            raise ValueError(f"{row['id']}.{field} 必须为非空版本/节点标识。")
    curve = row["area_curve"]
    if not isinstance(curve, list) or not 2 <= len(curve) <= 101:
        raise ValueError("area_curve 需要 2–101 个 [开度, 归一化有效面积] 点。")
    points = []
    for point in curve:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("area_curve 每个点必须包含开度与归一化面积。")
        point = [_unit(point[0], "曲线开度"), _unit(point[1], "曲线面积")]
        if points and (point[0] <= points[-1][0] or point[1] < points[-1][1]):
            raise ValueError("曲线开度须严格递增，面积须单调不减。")
        points.append(point)
    if points[0] != [0.0, 0.0] or points[-1] != [1.0, 1.0]:
        raise ValueError("归一化曲线必须从 [0,0] 到 [1,1]；关闭泄漏单独定义。")
    row["area_curve"] = points
    for key in ("opening_min", "opening_max", "reference_opening", "leak_fraction"):
        row[key] = _unit(row[key], key)
    if not row["opening_min"] < row["opening_max"]:
        raise ValueError("阀门最小开度必须小于最大开度。")
    if not row["opening_min"] <= row["reference_opening"] <= row["opening_max"]:
        raise ValueError("参考开度超出行程限位。")
    if row["leak_fraction"] >= 1:
        raise ValueError("关闭泄漏比例必须小于 1。")
    for key in ("actuator_tau_s", "actuator_rate_per_s", "max_pressure_drop_ratio"):
        row[key] = _number(row[key], key, positive=True)
    if mode == "requirement_sizing" or row["design_pressure_drop_Pa"] is not None:
        row["design_pressure_drop_Pa"] = _number(row["design_pressure_drop_Pa"], "design_pressure_drop_Pa", positive=True)
    if row["max_pressure_drop_ratio"] > MAX_PRESSURE_DROP_RATIO:
        raise ValueError(f"压差比筛查上限不得超过 {MAX_PRESSURE_DROP_RATIO}。")
    if row["rated_cda_m2"] is not None:
        row["rated_cda_m2"] = _number(row["rated_cda_m2"], "rated_cda_m2", positive=True)
    if mode == "candidate_check" and row["rated_cda_m2"] is None:
        raise ValueError("候选开度校核必须提供额定有效面积 rated_cda_m2。")
    if _area_fraction(row, row["reference_opening"]) <= 0:
        raise ValueError("参考开度的有效流通面积必须为正。")
    return row


def parse_configuration(value=None):
    """Normalize and validate without enabling valves in historic records."""
    if value is None or value == "":
        return dict(schema_version=SCHEMA_VERSION, enabled=False)
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (TypeError, ValueError) as exc:
            raise ValueError("阀门配置不是有效的有限数值 JSON。") from exc
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("不支持的阀门配置 schema_version。")
    if type(value.get("enabled")) is not bool:
        raise ValueError("阀门配置 enabled 必须为布尔值。")
    if not value["enabled"]:
        if set(value) != {"schema_version", "enabled"}:
            raise ValueError("禁用阀门配置不得夹带启用的拓扑或特性。")
        return dict(schema_version=SCHEMA_VERSION, enabled=False)
    if set(value) != {"schema_version", "enabled", "topology_id", "mode", "valves"}:
        raise ValueError("阀门配置字段不完整或包含未知字段。")
    topology, mode = value["topology_id"], value["mode"]
    if not isinstance(topology, str) or topology not in TOPOLOGIES or mode not in MODES:
        raise ValueError("不支持的阀门拓扑或分析模式。")
    if not isinstance(value["valves"], dict) or set(value["valves"]) != set(VALVE_IDS):
        raise ValueError("拓扑必须完整定义 MFV、OPFV 和 FPOV 三阀。")
    rows = {}
    for valve_id in VALVE_IDS:
        row = value["valves"][valve_id]
        if not isinstance(row, dict) or row.get("id") != valve_id:
            raise ValueError("阀门字典键与实体 ID 不一致。")
        rows[valve_id] = _validate_valve(row, mode, expected=TOPOLOGIES[topology]["valves"][valve_id])
    return dict(schema_version=SCHEMA_VERSION, enabled=True, topology_id=topology, mode=mode, valves=rows)


def configuration_hash(configuration):
    return _digest(parse_configuration(configuration))


def topology_hash(configuration):
    config = parse_configuration(configuration)
    if not config["enabled"]:
        return _digest(config)
    return _digest({"schema_version": SCHEMA_VERSION, "topology_id": config["topology_id"],
                    "nodes": {key: {field: row[field] for field in ("id", "fluid", "upstream_node", "downstream_node")}
                              for key, row in config["valves"].items()}})


def characteristic_hash(valve):
    mode = "candidate_check" if isinstance(valve, dict) and valve.get("design_pressure_drop_Pa") is None else "requirement_sizing"
    row = _validate_valve(valve, mode)
    # Pressure budget and reference opening are point requirements, not hardware.
    return _digest({key: value for key, value in row.items()
                    if key not in ("design_pressure_drop_Pa", "reference_opening")})


def _area_fraction(valve, opening):
    points = valve["area_curve"]
    if opening == 1:
        nominal = 1.0
    else:
        nominal = 0.0
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= opening <= x1:
                nominal = y0 + (opening - x0) * (y1 - y0) / (x1 - x0)
                break
    return valve["leak_fraction"] + (1.0 - valve["leak_fraction"]) * nominal


def effective_cda(valve, opening):
    row = _validate_valve(valve, "candidate_check")
    opening = _unit(opening, "开度")
    if not row["opening_min"] <= opening <= row["opening_max"]:
        raise ValueError("开度超出实体阀门行程范围。")
    return row["rated_cda_m2"] * _area_fraction(row, opening)


def valve_pressure_drop(valve, opening, mass_flow_kg_s, density_kg_m3):
    """Algebraic candidate loss; caller must independently screen inlet domain."""
    mass = _number(mass_flow_kg_s, "质量流量", nonnegative=True)
    density = _number(density_kg_m3, "入口密度", positive=True)
    area = effective_cda(valve, opening)
    if area <= 0:
        if mass == 0:
            return 0.0
        raise ValueError("完全关闭阀门不能通过正向流量。")
    return 0.5 * (mass / area) ** 2 / density


def _heos_inlet(fluid, pressure, temperature):
    try:
        import CoolProp.CoolProp as CP
        eos = CP.AbstractState("HEOS", {"CH4": "Methane", "O2": "Oxygen"}[fluid])
        if not eos.Tmin() <= temperature <= eos.Tmax() or pressure > eos.pmax():
            raise ValueError("入口压力或温度超出 HEOS 数据有效域。")
        eos.update(CP.PT_INPUTS, pressure, temperature)
        phase_names = {CP.iphase_liquid: "liquid", CP.iphase_supercritical: "supercritical",
                       CP.iphase_supercritical_liquid: "supercritical_liquid",
                       CP.iphase_supercritical_gas: "supercritical_gas", CP.iphase_gas: "gas",
                       CP.iphase_twophase: "two_phase", CP.iphase_critical_point: "critical_point"}
        phase = phase_names.get(eos.phase(), "unknown")
        density = float(eos.rhomass())
        compressibility = float(eos.isothermal_compressibility())
        if not all(math.isfinite(v) and v > 0 for v in (density, compressibility)):
            raise ValueError("入口密度或压缩性不是有效的正有限值。")
        return dict(backend="CoolProp::HEOS", enthalpy_reference_id="CoolProp::HEOS::default", fluid=fluid, pressure_Pa=pressure,
                    temperature_K=temperature, density_kg_m3=density,
                    enthalpy_J_kg=float(eos.hmass()), entropy_J_kg_K=float(eos.smass()),
                    isothermal_compressibility_1_Pa=compressibility, phase=phase)
    except (ImportError, ValueError, OverflowError) as exc:
        raise ValueError(f"HEOS 阀门入口物性不可用：{exc}") from exc


def _heos_outlet(fluid, pressure, enthalpy):
    """The resting downstream state of an adiabatic valve retains inlet h."""
    try:
        import CoolProp.CoolProp as CP
        eos = CP.AbstractState("HEOS", {"CH4": "Methane", "O2": "Oxygen"}[fluid])
        if pressure > eos.pmax():
            raise ValueError("出口压力超出 HEOS 数据有效域。")
        eos.update(CP.HmassP_INPUTS, enthalpy, pressure)
        temperature = float(eos.T())
        if not eos.Tmin() <= temperature <= eos.Tmax():
            raise ValueError("等焓出口温度超出 HEOS 数据有效域。")
        phase_names = {CP.iphase_liquid: "liquid", CP.iphase_supercritical: "supercritical",
                       CP.iphase_supercritical_liquid: "supercritical_liquid",
                       CP.iphase_supercritical_gas: "supercritical_gas", CP.iphase_gas: "gas",
                       CP.iphase_twophase: "two_phase", CP.iphase_critical_point: "critical_point"}
        result = dict(backend="CoolProp::HEOS", enthalpy_reference_id="CoolProp::HEOS::default", fluid=fluid, pressure_Pa=pressure,
                      temperature_K=temperature, density_kg_m3=float(eos.rhomass()),
                      enthalpy_J_kg=float(eos.hmass()), entropy_J_kg_K=float(eos.smass()),
                      phase=phase_names.get(eos.phase(), "unknown"), vapor_pressure_Pa=None,
                      process="adiabatic_isenthalpic_resting_outlet")
        if eos.Ttriple() < temperature < eos.T_critical():
            eos.update(CP.QT_INPUTS, 0.0, temperature)
            result["vapor_pressure_Pa"] = float(eos.p())
        if not math.isfinite(result["density_kg_m3"]) or result["density_kg_m3"] <= 0:
            raise ValueError("出口密度不是正有限值。")
        return result
    except (ImportError, ValueError, OverflowError) as exc:
        raise ValueError(f"HEOS 阀门等焓出口不可用：{exc}") from exc


def _inverse_fraction(row, required_fraction):
    leak = row["leak_fraction"]
    if required_fraction < leak or required_fraction > 1:
        return None
    nominal = (required_fraction - leak) / (1 - leak)
    for (x0, y0), (x1, y1) in zip(row["area_curve"], row["area_curve"][1:]):
        if y0 <= nominal <= y1:
            return x0 if y1 == y0 else x0 + (nominal - y0) * (x1 - x0) / (y1 - y0)
    return None


def evaluate_valve(valve, *, mass_flow_kg_s, upstream_pressure_Pa,
                   upstream_temperature_K, downstream_pressure_Pa, mode="candidate_check"):
    """Assess a demand or candidate; domain failures return no successful opening."""
    if mode not in MODES:
        raise ValueError("不支持的阀门分析模式。")
    row = _validate_valve(valve, mode)
    mass = _number(mass_flow_kg_s, "质量流量", positive=True)
    pu = _number(upstream_pressure_Pa, "阀门入口绝压", positive=True)
    pd = _number(downstream_pressure_Pa, "阀门出口绝压", positive=True)
    temperature = _number(upstream_temperature_K, "阀门入口温度", positive=True)
    result = dict(id=row["id"], fluid=row["fluid"], upstream_node=row["upstream_node"],
                  downstream_node=row["downstream_node"], mode=mode, model=MODEL_ID,
                  characteristic_version=row["characteristic_version"], characteristic_hash=characteristic_hash(row),
                  mass_flow_kg_s=mass, upstream_pressure_Pa=pu, downstream_pressure_Pa=pd,
                  upstream_temperature_K=temperature, pressure_drop_Pa=pu-pd,
                  pressure_drop_ratio=(pu-pd)/pu, status="unavailable", reason=None,
                  inlet_state=None, outlet_state=None, downstream_temperature_K=None,
                  downstream_density_kg_m3=None, required_cda_m2=None, required_rated_cda_m2=None,
                  opening=None, saturated=None, opening_margin_lower=None, opening_margin_upper=None,
                  flow_margin_kg_s=None, limitations=list(LIMITATIONS))
    if pd >= pu:
        result["reason"] = "nonpositive_pressure_drop"
        return result
    if (pu-pd)/pu > row["max_pressure_drop_ratio"] + 1e-12:
        result["reason"] = "pressure_drop_ratio_exceeds_model_domain"
        return result
    try:
        inlet = _heos_inlet(row["fluid"], pu, temperature)
    except ValueError as exc:
        result.update(reason="inlet_properties_unavailable", detail=str(exc))
        return result
    result["inlet_state"] = inlet
    if inlet["phase"] not in ("liquid", "supercritical", "supercritical_liquid", "supercritical_gas"):
        result["reason"] = "not_single_dense_phase"
        return result
    if inlet["density_kg_m3"] < MIN_DENSE_FLUID_DENSITY_KG_M3:
        result["reason"] = "density_below_dense_candidate_domain"
        return result
    estimate = inlet["isothermal_compressibility_1_Pa"] * (pu-pd)
    result["density_change_estimate"] = estimate
    if estimate > MAX_DENSITY_CHANGE_ESTIMATE:
        result["reason"] = "compressibility_exceeds_model_domain"
        return result
    try:
        outlet = _heos_outlet(row["fluid"], pd, inlet["enthalpy_J_kg"])
    except ValueError as exc:
        result.update(reason="outlet_properties_unavailable", detail=str(exc))
        return result
    result.update(outlet_state=outlet, downstream_temperature_K=outlet["temperature_K"],
                  downstream_density_kg_m3=outlet["density_kg_m3"],
                  cavitation_assessment="not_established_without_pressure_recovery_factor_FL")
    if outlet["phase"] not in ("liquid", "supercritical", "supercritical_liquid", "supercritical_gas"):
        result["reason"] = "outlet_not_single_dense_phase"
        return result
    if outlet["density_kg_m3"] < MIN_DENSE_FLUID_DENSITY_KG_M3:
        result["reason"] = "outlet_density_below_dense_candidate_domain"
        return result
    if outlet.get("vapor_pressure_Pa") is not None and pd <= outlet["vapor_pressure_Pa"]:
        result["reason"] = "outlet_at_or_below_vapor_pressure"
        return result
    density_change = abs(outlet["density_kg_m3"] / inlet["density_kg_m3"] - 1)
    result["isenthalpic_density_change_fraction"] = density_change
    if density_change > MAX_DENSITY_CHANGE_ESTIMATE:
        result["reason"] = "outlet_density_change_exceeds_model_domain"
        return result
    flux = math.sqrt(2 * inlet["density_kg_m3"] * (pu-pd))
    required = mass / flux
    result.update(required_cda_m2=required,
                  required_rated_cda_m2=required / _area_fraction(row, row["reference_opening"]),
                  reference_opening=row["reference_opening"], rated_cda_m2=row["rated_cda_m2"],
                  opening_min=row["opening_min"], opening_max=row["opening_max"])
    if mode == "requirement_sizing":
        result.update(status="available", reason="requirement_only")
        return result
    low, high = row["opening_min"], row["opening_max"]
    min_area = row["rated_cda_m2"] * _area_fraction(row, low)
    max_area = row["rated_cda_m2"] * _area_fraction(row, high)
    result.update(minimum_cda_m2=min_area, maximum_cda_m2=max_area,
                  minimum_flow_kg_s=min_area*flux, maximum_flow_kg_s=max_area*flux,
                  flow_margin_kg_s=max_area*flux-mass)
    opening = _inverse_fraction(row, required / row["rated_cda_m2"])
    if required < min_area - max(1e-15, min_area*1e-10) or required > max_area + max(1e-15, max_area*1e-10):
        result.update(status="infeasible", reason="below_minimum_flow" if required < min_area else "above_maximum_flow",
                      saturated=True, requested_opening=opening)
        return result
    # A flat characteristic has multiple inverse roots; choose the first allowed
    # root, and verify it reproduces the demand rather than clamping a failure.
    if opening is not None and opening < low and math.isclose(required, min_area, rel_tol=1e-10, abs_tol=1e-15):
        opening = low
    if opening is None or not low - 1e-10 <= opening <= high + 1e-10:
        result.update(status="infeasible", reason="no_admissible_opening", saturated=True)
        return result
    if math.isclose(opening, low, abs_tol=1e-10):
        opening = low
    elif math.isclose(opening, high, abs_tol=1e-10):
        opening = high
    saturated = opening == low or opening == high
    result.update(status="feasible", reason="at_travel_limit" if saturated else "within_travel",
                  opening=opening, saturated=saturated,
                  opening_margin_lower=opening-low, opening_margin_upper=high-opening)
    return result


def actuator_derivative(actual, command, tau_s, rate_per_s, *, lower=0.0, upper=1.0):
    """First-order actuator with rate limit; actual may be an ODE trial stage."""
    actual = _number(actual, "实际开度")
    command = _number(command, "指令开度")
    lower, upper = _unit(lower, "最小行程"), _unit(upper, "最大行程")
    if lower >= upper or not lower <= command <= upper:
        raise ValueError("执行器指令或行程范围无效。")
    tau = _number(tau_s, "时间常数", positive=True)
    rate = _number(rate_per_s, "行程速度", positive=True)
    return min(rate, max(-rate, (command-actual)/tau))
