"""Re-solve a saved FFSC design with its explicitly identified gas EOS candidate."""
from copy import deepcopy
from dataclasses import asdict
import math
import warnings


PR_EOS = "Peng-Robinson"
PR_BM_EOS = "Peng-Robinson-Boston-Mathias"
EOS_MODELS = ("ideal-gas", PR_EOS, PR_BM_EOS)
SPECIES_POLICY = "pr_supported_v1"
COMMON_HOT_SIDE_METHOD = "same_eos"
PROGRESS_PHASE = "整循环燃气 EOS 对比"


def require_comparison_combustion_model(values):
    """Retain old records for reading, without executing retired corrections."""
    if values.get("combustion_loss_model") != "adiabatic":
        raise ValueError("来源设计采用旧版本温升折减，不能用于新的整循环燃气 EOS 对照。"
                         "请明确采用当前燃烧模型，保存并运行新的设计点后选择新记录；原计算记录保留。")


def validate_candidate_eos(candidate_eos):
    if candidate_eos is not None and candidate_eos not in (PR_EOS, PR_BM_EOS):
        raise ValueError("candidate_eos 仅接受 Peng-Robinson 或 Peng-Robinson-Boston-Mathias；省略时沿用来源模型。")
    return candidate_eos

GAS_STATIONS = (
    ("ox_preburner_outlet", "富氧预燃室出口"),
    ("fuel_preburner_outlet", "富燃预燃室出口"),
    ("ox_turbine_exhaust", "富氧涡轮排气"),
    ("fuel_turbine_exhaust", "富燃涡轮排气"),
    ("main_chamber", "主燃烧室"),
    ("nozzle_exit", "喷管出口"),
)
HOT_SIDE_STATIONS = frozenset({"main_chamber", "nozzle_exit"})
SUMMARY_METRICS = (
    ("thrust_N", "推力（同一设计目标）", "N"),
    ("chamber_pressure_Pa", "主室压力（同一设计边界）", "Pa"),
    ("Isp_s", "比冲（记录环境压力）", "s"),
    ("Isp_vac_s", "真空比冲", "s"),
    ("mdot_total_kg_s", "总推进剂流量", "kg/s"),
    ("mdot_ox_kg_s", "液氧总流量", "kg/s"),
    ("mdot_fuel_kg_s", "甲烷总流量", "kg/s"),
    ("ox_preburner_mr", "富氧预燃室氧燃比", "1"),
    ("fuel_preburner_mr", "富燃预燃室氧燃比", "1"),
)
COMPONENT_METRICS = (
    ("nozzle", "At_m2", "匹配喉面积", "m²"),
    ("cooling_channel", "heat_load_W", "冷却吸热量", "W"),
    ("cooling_channel", "delta_P_MPa", "冷却压降", "MPa"),
    ("ox_pump", "p_out_MPa", "液氧主泵所需出口压力", "MPa"),
    ("fuel_pump", "p_out_MPa", "甲烷主泵所需出口压力", "MPa"),
    ("ox_pump", "delta_p_Pa", "液氧主泵所需压升", "Pa"),
    ("fuel_pump", "delta_p_Pa", "甲烷主泵所需压升", "Pa"),
    ("ox_pump", "volume_flow_m3s", "液氧主泵入口体积流量", "m³/s"),
    ("fuel_pump", "volume_flow_m3s", "甲烷主泵入口体积流量", "m³/s"),
    ("ox_pump", "nr_rpm", "液氧轴初选转速", "rpm"),
    ("fuel_pump", "nr_rpm", "甲烷轴初选转速", "rpm"),
    ("ox_pump", "power_W", "液氧主泵所需轴功", "W"),
    ("fuel_pump", "power_W", "甲烷主泵所需轴功", "W"),
    ("ox_preburner_turbine", "turbine_power_W", "富氧涡轮轴功", "W"),
    ("fuel_preburner_turbine", "turbine_power_W", "富燃涡轮轴功", "W"),
    ("ox_preburner_turbine", "turbine_pressure_ratio", "富氧涡轮压比", "1"),
    ("fuel_preburner_turbine", "turbine_pressure_ratio", "富燃涡轮压比", "1"),
    ("ox_preburner_turbine", "isentropic_drop_Jkg", "富氧涡轮等熵焓降", "J/kg"),
    ("fuel_preburner_turbine", "isentropic_drop_Jkg", "富燃涡轮等熵焓降", "J/kg"),
)
STATION_PROPERTIES = (
    ("pressure", "压力", "Pa"), ("temperature", "温度", "K"),
    ("mass_flow", "质量流量", "kg/s"), ("density", "密度", "kg/m³"),
    ("enthalpy", "比焓", "J/kg"), ("entropy_JkgK", "比熵", "J/(kg·K)"),
    ("cp_JkgK", "定压比热", "J/(kg·K)"), ("cv_JkgK", "定容比热", "J/(kg·K)"),
    ("gamma", "cp/cv", "1"), ("sound_speed_m_s", "声速", "m/s"),
    ("molar_mass_kg_per_kmol", "平均摩尔质量", "kg/kmol"),
    ("compressibility_factor", "压缩因子 Z", "1"),
)
LIMITATIONS = (
    "两侧从同一保存设计输入出发，统一采用受限物种集合，各自重新匹配全循环。推力、主室压力及已指定的预燃室目标仍由设计任务约束。",
    "候选方案按 PR 涡轮排气的实际质量流量、组成及同基准比焓向主室交接。差值包含循环重匹配效应。",
    "受限集合仅覆盖具备 Peng–Robinson 参数的物种，未覆盖完整 GRI 机理中的高温解离物种与自由基。比冲和热状态只用于这组受限模型的敏感性比较。",
    "两相、凝聚相、燃烧反应速率和高压燃气物性验证不属于这项对比。液体推进剂继续使用同一纯流体模型。",
    "泵出口压力、压升、功率和初选转速是设计需求，固定硬件可用性仍需部件特性及试验支持。",
    "差值为本记录所声明的 PR 候选减理想气体。焓和熵仅在保存参考基准一致时给绝对差值。",
)


def _comparison_scope(eos):
    full = eos == PR_BM_EOS
    return {"candidate_eos": eos,
            "comparison": "full_pr_bm_vs_same_species_ideal" if full else "segmented_preburner_turbine_pr_vs_same_species_ideal",
            "pr_preburners_and_turbines": eos,
            "main_chamber_nozzle": eos if full else "ideal-gas",
            "cooling_gas_side": eos if full else "ideal-gas",
            **({"hot_side_thermo_method": COMMON_HOT_SIDE_METHOD,
                "hot_side_protocol": {"id": "same_eos_heated_frozen_quasi1d_v1",
                    "heating_model": "generalized_frozen_quasi1d", "station_sampling": "cell_centers",
                    "bartz_temperature_ratio": "actual_total_to_static_temperature"}} if full else {})}


def _limitations(eos):
    scope = ("PR–BM 候选的预燃室 HP 平衡、冻结组成涡轮、主燃烧室、冷却燃气侧和喷管统一使用 Peng–Robinson–Boston–Mathias；这是独立高温 alpha 修正身份，不是原标准 PR。"
             if eos == PR_BM_EOS else
             "历史分段候选的两预燃室 HP 平衡和两涡轮冻结 SP／HP 使用标准 Peng–Robinson；主室、冷却燃气侧和喷管保留理想气体。")
    method = (["两侧采用共同热侧算法：广义冻结组成准一维加热求解、单元中心采样、实际总温／静温比 Bartz 修正。冷却未启用时该加热方法不适用。"]
              if eos == PR_BM_EOS else [])
    return [scope, *method, *LIMITATIONS]


def capabilities():
    from lre_workbench.properties.thermochemistry import CANTERA_AVAILABLE, COOLPROP_AVAILABLE
    available = CANTERA_AVAILABLE and COOLPROP_AVAILABLE
    return {"available": available, "models": list(EOS_MODELS),
            "reason": None if available else "整循环对比需要 Cantera 与 CoolProp。"}


def _number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


def _station(state):
    metadata = state.get("metadata") or {}
    model = metadata.get("model") or {}
    values = {key: _number(state.get(key, metadata.get(key))) for key, _, _ in STATION_PROPERTIES}
    if values["gamma"] is None and values["cp_JkgK"] is not None and values["cv_JkgK"]:
        values["gamma"] = values["cp_JkgK"] / values["cv_JkgK"]
    if values["molar_mass_kg_per_kmol"] is None:
        values["molar_mass_kg_per_kmol"] = _number(metadata.get("molar_mass"))
    composition = state.get("composition") or {}
    complete = (metadata.get("composition_complete") is True and bool(composition)
                and all(_number(y) is not None and 0 <= y <= 1 for y in composition.values())
                and math.isclose(math.fsum(composition.values()), 1, rel_tol=1e-7))
    return {"values": values, "basis": deepcopy(metadata.get("state_basis")),
            "model": deepcopy(model), "temperature_range": deepcopy(metadata.get("temperature_range_evidence")),
            "source_ports": deepcopy(metadata.get("source_ports")),
            "eos_transition": deepcopy(metadata.get("eos_transition")),
            "composition_mass_fractions": deepcopy(composition) if complete else None}


def _model_scope(model):
    return {key: deepcopy(model.get(key)) for key in (
        "species_policy", "species_names", "species_count", "species_policy_sha256", "excluded_species",
        "eos_parameter_sha256", "eos_model", "eos_model_sha256", "mixing_rule", "binary_interactions", "transport_model", "limitations")}


def _evaluate(values, progress_callback=None):
    # Same production entry used by concept design; detailed geometry views
    # are unrelated to this two-cycle thermodynamic audit.
    from lre_workbench.analysis.assessment import assess_engineering
    from lre_workbench.domain.models import EngineInputs
    from lre_workbench.systems.ffsc import FFSCCycleModel

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = FFSCCycleModel(EngineInputs(**values)).evaluate(
            progress_callback=progress_callback, include_cooling_marching=False)
    return asdict(result), assess_engineering(result, scope="system"), [str(item.message) for item in caught]


def _hot_side_method_audit(calculation, inputs, eos):
    requested = inputs.get("hot_side_thermo_method", "legacy")
    profile = (calculation.get("components", {}).get("nozzle", {}).get("profile") or {})
    evidence = {"requested_method": requested, "actual_method": profile.get("thermo_method"),
                "actual_profile_id": profile.get("model_id")}
    if requested != COMMON_HOT_SIDE_METHOD:
        return evidence | {"status": "not_requested", "reason": "此历史分段协议未要求共同热侧求解方法。"}
    if not inputs.get("cooling_bartz_mode"):
        model = profile.get("gas_model") or {}
        matches = profile.get("model_id") == "adiabatic_frozen_same_eos_v1" and model.get("eos") == eos
        return evidence | {"status": "not_applicable" if matches else "fail",
            "expected_profile_id": "adiabatic_frozen_same_eos_v1", "actual_eos": model.get("eos"),
            "adiabatic_same_eos_verified": matches,
            "reason": "未启用分布加热；已核对同 EOS 冻结组成绝热喷管。" if matches else
                      "未启用分布加热，但缺少同 EOS 冻结组成绝热喷管的实际模型证据。"}
    expected = {"ideal-gas": "heated_frozen_ideal_same_eos_quasi1d_v1",
                PR_BM_EOS: "heated_frozen_pr_bm_quasi1d_v1"}.get(eos)
    matches = bool(expected and profile.get("model_id") == expected
                   and profile.get("thermo_method") == COMMON_HOT_SIDE_METHOD)
    return evidence | {"status": "pass" if matches else "fail", "expected_profile_id": expected,
                       "reason": None if matches else "实际热侧记录未证明采用共同冻结准一维算法、单元中心采样及实际总温／静温比修正。"}


def _run(values, eos, progress_callback=None):
    from lre_workbench.domain.models import ValidationError
    from lre_workbench.solvers.convergence import numerical_convergence
    from lre_workbench.systems.steady_closure import audit_energy
    from lre_workbench.systems.steady_network import audit_network, pressure_contract

    inputs = values | {"gas_eos": eos}
    try:
        calculation, assessment, cautions = (
            _evaluate(inputs, progress_callback=progress_callback) if progress_callback else _evaluate(inputs))
    except (ValidationError, ValueError, RuntimeError, ArithmeticError) as exc:
        run = {"eos": eos, "inputs": inputs, "status": "error", "reason": f"{type(exc).__name__}: {exc}"[:1600]}
        diagnostic = getattr(exc, "initialization_diagnostics", None)
        if isinstance(diagnostic, dict):
            run["initialization_diagnostics"] = diagnostic
        return run

    states, components, summary = (calculation[key] for key in ("states", "components", "summary"))
    convergence = numerical_convergence(calculation)
    network = calculation.get("steady_closure", {}).get("feed_network", {})
    feed = audit_network(network, total_fuel=summary.get("mdot_fuel_kg_s"), total_ox=summary.get("mdot_ox_kg_s"),
                         mr_ox=summary.get("ox_preburner_mr_target") if summary.get("solve_mode") == 2 else None,
                         mr_fuel=summary.get("fuel_preburner_mr_target") if summary.get("solve_mode") == 2 else None)
    pressure = pressure_contract(network, states, calculation["inputs"], components.get("cooling_channel"))
    energy = audit_energy(states, components)
    stations = {key: _station(states[key]) for key, _ in GAS_STATIONS if key in states}
    scope = _model_scope(stations.get("ox_preburner_outlet", {}).get("model", {}))
    policy_hash = scope["species_policy_sha256"]
    station_models = {}
    for key, _ in GAS_STATIONS:
        expected = "ideal-gas" if eos == PR_EOS and key in HOT_SIDE_STATIONS else eos
        observed = stations.get(key, {}).get("model", {})
        station_models[key] = {"expected_eos": expected, "actual_eos": observed.get("eos"),
                               "eos_parameter_sha256": observed.get("eos_parameter_sha256"),
                               "eos_model_sha256": observed.get("eos_model_sha256"),
                               "match": observed.get("eos") == expected
                               and observed.get("species_policy_sha256") == policy_hash
                               and bool(scope["eos_parameter_sha256"]
                                        and observed.get("eos_parameter_sha256") == scope["eos_parameter_sha256"])
                               and (expected != PR_BM_EOS or bool(scope["eos_model_sha256"]
                                    and observed.get("eos_model_sha256") == scope["eos_model_sha256"]))}
    eos_verified = (bool(policy_hash and scope["species_names"] and scope["species_policy"] == SPECIES_POLICY)
                    and all(row["match"] for row in station_models.values()))
    thermal_method = _hot_side_method_audit(calculation, inputs, eos)
    thermal_verified = thermal_method["status"] != "fail"
    scope["hot_side_thermo_method"] = inputs.get("hot_side_thermo_method", "legacy")
    closure = {"feed_network": feed, "pressure_contract": pressure, "energy_balance": energy,
               "hot_side_method": thermal_method}
    metrics = {key: _number(summary.get(key)) for key, _, _ in SUMMARY_METRICS}
    for component, key, _, _ in COMPONENT_METRICS:
        metrics[f"{component}.{key}"] = _number(components.get(component, {}).get("outputs", {}).get(key))
    thermo = calculation.get("thermodynamic_applicability", {})
    closed = (convergence["status"] == "pass" and feed["status"] == "pass"
              and pressure["pressure_link_status"] == "pass" and energy["status"] == "pass")
    status = "not_converged" if not closed else "computed" if eos_verified and thermal_verified else "unavailable"
    reason = {"not_converged": "该方案的循环收敛或独立守恒／压力检查未通过。",
              "unavailable": "缺少关键燃气站点采用所声明 EOS 范围及统一受限物种集合的求解证据。"}.get(status)
    if status == "unavailable" and not thermal_verified:
        reason = thermal_method["reason"]
    return {"eos": eos, "inputs": inputs, "status": status, "reason": reason, "eos_verified": eos_verified,
            "model_scope": scope, "station_models": station_models,
            "convergence": convergence, "closure": closure, "assessment": assessment,
            "operating_point": {key: _number(summary.get(key)) for key in (
                "evaluated_thrust_target_N", "evaluated_chamber_pressure_Pa", "continuation_reached_ratio")},
            "model_applicability": {key: deepcopy(thermo.get(key)) for key in (
                "status", "gas_model", "pure_fluid_status", "thermochemical_data", "enthalpy_reference")},
            "metrics": metrics, "stations": stations,
            "warnings": list(dict.fromkeys(calculation.get("warnings", []) + cautions))}


def _row(key, label, unit, ideal, pr, *, comparable, relative=True):
    difference = pr - ideal if comparable and ideal is not None and pr is not None else None
    return {"id": key, "label": label, "unit": unit, "ideal": ideal, "pr": pr,
            "difference": difference, "relative_difference": difference / abs(ideal)
            if relative and difference is not None and ideal else None}


def _compare_stations(ideal, pr, closed):
    rows = []
    for key, label in GAS_STATIONS:
        left = ideal.get("stations", {}).get(key, {})
        right = pr.get("stations", {}).get(key, {})
        if not left and not right:
            continue
        left_basis, right_basis = left.get("basis") or {}, right.get("basis") or {}
        comparable = closed and all(left_basis.get(field) and left_basis[field] == right_basis.get(field)
                                    for field in ("pressure", "temperature", "enthalpy"))
        properties = []
        for field, name, unit in STATION_PROPERTIES:
            reference = {"enthalpy": "enthalpy_reference_id", "entropy_JkgK": "entropy_reference_id"}.get(field)
            left_ref = left.get("model", {}).get(reference) if reference else None
            shared_reference = not reference or bool(left_ref and left_ref == right.get("model", {}).get(reference))
            properties.append(_row(field, name, unit, left.get("values", {}).get(field),
                                   right.get("values", {}).get(field), comparable=comparable and shared_reference,
                                   relative=not reference))
        left_y, right_y = left.get("composition_mass_fractions"), right.get("composition_mass_fractions")
        composition = []
        if left_y is not None and right_y is not None:
            for species in sorted(left_y.keys() | right_y.keys()):
                a, b = left_y.get(species, 0.), right_y.get(species, 0.)
                composition.append({"species": species, "ideal": a, "pr": b,
                                    "difference": b - a if comparable else None})
        rows.append({"id": key, "label": label, "properties": properties, "composition": composition,
                     "basis": {"ideal": left.get("basis"), "pr": right.get("basis")}})
    return rows


def compare_gas_eos(source, progress=None, *, candidate_eos=None):
    from webapp.model_validity import require_reusable_model
    require_reusable_model(source)
    from webapp.solver import input_values

    explicit_candidate = validate_candidate_eos(candidate_eos)
    values, input_warnings = input_values(deepcopy(source["inputs"]), saved_snapshot=True)
    require_comparison_combustion_model(values)
    # Legacy input normalization is applied once to both sides. No other input
    # may vary between runs, including solver, propellant data and temperature targets.
    values.pop("gas_eos", None)
    values["gas_species_policy"] = SPECIES_POLICY
    saved_preburner = source.get("result", {}).get("calculation", {}).get("states", {}).get("ox_preburner_outlet", {})
    saved_model = (saved_preburner.get("metadata") or {}).get("model") or {}
    source_model = {"eos": saved_model.get("eos", source["inputs"].get("gas_eos", "ideal-gas")),
                    "species_policy": saved_model.get("species_policy", source["inputs"].get("gas_species_policy", "auto")),
                    "eos_parameter_sha256": saved_model.get("eos_parameter_sha256"),
                    "eos_model_sha256": saved_model.get("eos_model_sha256")}
    candidate_eos = explicit_candidate or (PR_BM_EOS if source_model["eos"] == PR_BM_EOS else PR_EOS)
    source_model["hot_side_thermo_method"] = values.get("hot_side_thermo_method", "legacy")
    if candidate_eos == PR_BM_EOS:
        # Both runs use the same heated-flow algorithm and sampling; production
        # defaults and the immutable source record are deliberately unchanged.
        values["hot_side_thermo_method"] = COMMON_HOT_SIDE_METHOD
    pair = ("ideal-gas", candidate_eos)
    source_model["station_eos"] = {
        key: ((state.get("metadata") or {}).get("model") or {}).get("eos")
        for key, state in source.get("result", {}).get("calculation", {}).get("states", {}).items()
        if key in dict(GAS_STATIONS)}
    source_model["scope"] = ("all_ideal" if source_model["eos"] == "ideal-gas" else
                             "full_pr_bm" if source_model["eos"] == PR_BM_EOS else "segmented_standard_pr")
    support = capabilities()
    runs = {}
    for index, eos in enumerate(pair):
        callback = None
        if progress:
            progress(index, len(pair), {"phase": PROGRESS_PHASE, "eos": eos, "stage": "initializing"})

            def callback(current, maximum, residual):
                progress(index, len(pair), {"phase": PROGRESS_PHASE, "eos": eos, "stage": "solving",
                                                 "solver_iteration": _number(current), "solver_maximum": _number(maximum),
                                                 "residual": _number(residual)})

        runs[eos] = (_run(values, eos, callback) if support["available"] else
                     {"eos": eos, "inputs": values | {"gas_eos": eos}, "status": "unavailable", "reason": support["reason"]})
    if progress:
        progress(len(pair), len(pair), {"phase": PROGRESS_PHASE, "stage": "reviewed"})
    ideal, pr = (runs[eos] for eos in pair)
    scopes = (pr.get("model_scope", {}), ideal.get("model_scope", {}))
    model_scope = next((scope for scope in scopes if scope.get("species_policy_sha256")),
                       {"species_policy": SPECIES_POLICY})
    model_scope = model_scope | _comparison_scope(candidate_eos)
    computed = sum(run["status"] == "computed" for run in runs.values())
    same_species = computed == 2 and all(ideal["model_scope"][key] == pr["model_scope"][key]
        for key in ("species_policy_sha256", "species_names", "eos_parameter_sha256"))
    status = "complete" if same_species else "partial" if computed == 1 else "unavailable"
    metric_specs = SUMMARY_METRICS + tuple((f"{component}.{key}", name, unit)
                                          for component, key, name, unit in COMPONENT_METRICS)
    metrics = []
    if any("metrics" in run for run in runs.values()):
        metrics = [_row(key, label, unit, ideal.get("metrics", {}).get(key), pr.get("metrics", {}).get(key),
                        comparable=same_species) for key, label, unit in metric_specs]
    return {"schema_version": 3, "kind": "full_ffsc_eos_comparison", "status": status,
            "candidate_eos": candidate_eos,
            "candidate_selection": "explicit" if explicit_candidate else "source_default",
            "reason": "两侧保存的物种集合或 PR 参数身份不一致。" if computed == 2 and not same_species else None,
            "source_model": source_model, "model_scope": model_scope,
            "shared_inputs": values, "input_warnings": input_warnings, "runs": runs, "metrics": metrics,
            "stations": _compare_stations(ideal, pr, same_species), "limitations": _limitations(candidate_eos)}
