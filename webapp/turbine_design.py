"""Saved-result turbine design handoff; no thermodynamic work is done by GET.

``capture_turbine_gas_properties`` is a solve-time operation on the actual
selected mechanism. ``turbine_design_view`` only audits saved data. A complete
global setup is not a blade design, a validated CFD boundary, or a CFturbo file.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from webapp.turbine_geometry import R_UNIVERSAL, _specs, turbine_geometry_view


SCHEMA_VERSION = 1
METHOD = "saved_turbine_design_handoff_v1"
SOURCE_FINGERPRINT_VERSION = 1
REFERENCES = [
    {"title": "CFturbo · Global setup", "url": "https://manual.cfturbo.com/en/global_setup.html"},
    {"title": "CFturbo · Axial turbine parameters and efficiencies", "url": "https://manual.cfturbo.com/en/md_parameter_axturbine.html"},
    {"title": "Cantera · Thermodynamic properties", "url": "https://cantera.org/stable/python/thermo.html"},
]
POINT_FIELDS = {
    "mass_flow_kg_s": "mass_flow_kgs", "pressure_in_pa": "p_in_Pa", "pressure_out_pa": "p_out_Pa",
    "temperature_in_k": "T_in_K", "temperature_out_k": "T_exhaust_K", "shaft_speed_rpm": "shaft_speed_rpm",
    "mean_diameter_m": "D_mean_m", "mean_speed_m_s": "U_mean_actual_ms", "stages": "n_stages_recommended",
    "power_w": "turbine_power_W", "isentropic_drop_j_kg": "isentropic_drop_Jkg",
    "actual_drop_j_kg": "actual_drop_Jkg", "pressure_ratio": "turbine_pressure_ratio", "efficiency": "eta_turbine",
    "molar_mass_kg_kmol": "molar_mass_in", "cp_in_j_kg_k": "cp_in_Jkgk", "gamma_in": "gamma_in",
    "u_over_c_target": "u_over_c_target", "u_over_c_actual": "u_over_c_actual", "reaction_degree_target": "reaction_degree",
}
PROPERTY_COLUMNS = [
    ("temperature_k", "温度", "K"), ("pressure_pa", "参考压力", "Pa"),
    ("cp_j_kg_k", "定压比热", "J/(kg·K)"), ("cv_j_kg_k", "定容比热", "J/(kg·K)"),
    ("gamma", "比热比", "—"), ("density_kg_m3", "密度", "kg/m³"),
    ("enthalpy_j_kg", "比焓（机制基准）", "J/kg"), ("entropy_j_kg_k", "比熵（机制基准）", "J/(kg·K)"),
    ("viscosity_pa_s", "动力黏度", "Pa·s"), ("thermal_conductivity_w_m_k", "导热率", "W/(m·K)"),
]


def _num(value, positive=False):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (not positive or value > 0):
            return float(value)
    except OverflowError:
        pass
    return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, bool)):
        return value
    return _num(value)


def _state_keys(component_key):
    if component_key == "gg_turbine":
        return "gg_outlet", "gg_turbine_exhaust"
    if component_key.startswith("gg_turbine_"):
        return "gg_outlet", component_key + "_exhaust"
    prefix = "ox" if component_key.startswith("ox_") else "fuel"
    return prefix + "_preburner_outlet", prefix + "_turbine_exhaust"


def _context(result, component_key):
    calc = _dict(result.get("calculation"))
    states = _dict(calc.get("states"))
    inlet_key, outlet_key = _state_keys(component_key)
    return (_dict(_dict(_dict(calc.get("components")).get(component_key)).get("outputs")),
            _dict(states.get(inlet_key)), _dict(states.get(outlet_key)), inlet_key, outlet_key)


def _capture_source(result, component_key):
    outputs, inlet, outlet, inlet_key, outlet_key = _context(result, component_key)
    environment = _dict(result.get("environment"))
    # Outputs, exact mass fractions and state metadata are source evidence;
    # current UI drafts and unrelated result cards do not enter this identity.
    return _hash(_json_safe({"component_key": component_key, "outputs": outputs,
                            "inlet_state_key": inlet_key, "inlet": inlet,
                            "outlet_state_key": outlet_key, "outlet": outlet,
                            "environment": environment}))


def _composition(state):
    comp = state.get("composition")
    if not isinstance(comp, dict) or not comp:
        raise ValueError("记录未保存完整入口质量分数组成。")
    for species, fraction in comp.items():
        if not isinstance(species, str) or not species or _num(fraction) is None or fraction < 0:
            raise ValueError("保存的燃气质量分数组成无效。")
    if not math.isclose(sum(comp.values()), 1., rel_tol=0., abs_tol=2e-7):
        raise ValueError("保存的燃气质量分数之和不为 1；不自动归一化或补齐物种。")
    return {species: float(fraction) for species, fraction in comp.items()}


def _empty_table(note):
    return {"status": "missing", "temperature_range_k": None, "pressure_pa": None,
            "composition_policy": "frozen_saved_inlet", "columns": [{"key": k, "label": l, "unit": u} for k, l, u in PROPERTY_COLUMNS],
            "rows": [], "note": note}


def _gas_row(gas, temperature, pressure, composition):
    gas.TPY = temperature, pressure, composition
    cp, cv = float(gas.cp_mass), float(gas.cv_mass)
    row = {"temperature_k": float(gas.T), "pressure_pa": float(gas.P), "cp_j_kg_k": cp, "cv_j_kg_k": cv,
           "gamma": cp / cv, "density_kg_m3": float(gas.density_mass),
           "enthalpy_j_kg": float(gas.enthalpy_mass), "entropy_j_kg_k": float(gas.entropy_mass),
           "molar_mass_kg_kmol": float(gas.mean_molecular_weight),
           "viscosity_pa_s": None, "thermal_conductivity_w_m_k": None}
    for key, attr in (("viscosity_pa_s", "viscosity"), ("thermal_conductivity_w_m_k", "thermal_conductivity")):
        try:
            row[key] = _num(float(getattr(gas, attr)), positive=True)
        except Exception:  # Mechanisms without a transport model remain usable for thermo.
            pass
    for key, value in row.items():
        if key in ("viscosity_pa_s", "thermal_conductivity_w_m_k"):
            continue
        if _num(value, positive=key not in ("enthalpy_j_kg", "entropy_j_kg_k")) is None:
            raise ValueError("物性采样返回无效值：" + key)
    row["within_mechanism_common_temperature_range"] = float(gas.min_temp) <= temperature <= float(gas.max_temp)
    return row


def capture_turbine_gas_properties(result, thermo):
    """Capture properties during the new solve using its selected actual model.

    ``thermo`` is the project's imported ``thermo`` module. This function never
    mutates the result or leaves the thread-local Cantera phase in another state.
    Existing captures are returned unchanged, preventing accidental recapture.
    """
    if not isinstance(result, dict):
        return {"schema_version": SCHEMA_VERSION, "turbines": {}, "reason": "计算记录无效。"}
    if "turbine_gas_properties" in result:
        return deepcopy(result["turbine_gas_properties"])
    answer = {"schema_version": SCHEMA_VERSION, "capture_method": "solve_time_frozen_composition_cantera_v1", "turbines": {}}
    try:
        specs, _, inputs = _specs(result)
    except Exception as exc:
        answer["reason"] = str(exc)
        return answer
    environment = _dict(result.get("environment"))
    physics = _dict(environment.get("physics"))
    for side, component_key, _, _ in specs:
        capture = {"status": "missing", "source": {"component_key": component_key, "kind": "solve_time_capture"},
                   "model": {}, "endpoint_properties": {}, "property_table": _empty_table("本次求解未能记录物性采样。")}
        answer["turbines"][side] = capture
        gas, saved_state = None, None
        try:
            outputs, inlet, outlet, _, _ = _context(result, component_key)
            composition = _composition(inlet)
            mechanism, phase = physics.get("mechanism"), physics.get("phase")
            fuel_species = physics.get("fuel_species")
            expected_hash = environment.get("mechanism_sha256")
            if not all(isinstance(v, str) and v for v in (mechanism, phase, fuel_species, expected_hash)):
                raise ValueError("未保存机制、相名称、燃料物种或机制 SHA256，不能改用当前默认热力学模型。")
            if not getattr(thermo, "CANTERA_AVAILABLE", False) or physics.get("chemistry") != "Cantera":
                raise ValueError("本次求解未声明使用 Cantera，不能事后替换热力学来源。")
            if _dict(environment.get("dependencies")).get("cantera") != str(thermo.ct.__version__):
                capture["status"] = "conflict"
                raise ValueError("实际 Cantera 版本与保存的求解环境不一致。")
            if _dict(inlet.get("metadata")).get("thermo_source") != 1.0:
                raise ValueError("保存入口节点未确认 Cantera 热力学来源。")
            if Path(mechanism).name != mechanism:
                raise ValueError("机制文件身份必须是已记录的本地数据文件名。")
            mechanism_path = Path(thermo.ct.__file__).resolve().parent / "data" / mechanism
            actual_hash = hashlib.sha256(mechanism_path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                capture["status"] = "conflict"
                raise ValueError("求解环境记录的机制 SHA256 与当前实际机制文件不一致。")
            dataset = inputs.get("methane_thermo_dataset")
            if fuel_species == "CH4" and not isinstance(dataset, str):
                raise ValueError("未保存甲烷热力学数据集选择。")
            gas = thermo._get_gas(fuel_species, methane_dataset=dataset)
            if gas.name != phase or thermo.gas_data_filename(gas) != mechanism:
                capture["status"] = "conflict"
                raise ValueError("实际热力学相或机制与本次求解环境身份不一致。")
            saved_state = gas.state.copy()
            for key in ("T_in_K", "T_exhaust_K", "p_in_Pa", "p_out_Pa"):
                if _num(outputs.get(key), positive=True) is None:
                    raise ValueError("物性采样缺少保存的涡轮温压端点：" + key)
            unknown = set(composition) - set(gas.species_names)
            if unknown:
                capture["status"] = "conflict"
                raise ValueError("保存组成含所选机制没有的物种：" + ", ".join(sorted(unknown)))
            inlet_row = _gas_row(gas, outputs["T_in_K"], outputs["p_in_Pa"], composition)
            outlet_row = _gas_row(gas, outputs["T_exhaust_K"], outputs["p_out_Pa"], composition)
            low, high = sorted((outputs["T_exhaust_K"], outputs["T_in_K"]))
            temperatures = [low + (high - low) * i / 10 for i in range(11)] if low != high else [low]
            rows = [_gas_row(gas, temperature, outputs["p_in_Pa"], composition) for temperature in temperatures]
            model = {"source": "Cantera", "mechanism": mechanism, "mechanism_sha256": actual_hash,
                     "phase": gas.name, "gas_eos": gas.thermo_model, "transport_model": gas.transport_model,
                     "cantera_version": str(thermo.ct.__version__), "composition_policy": "frozen_saved_inlet",
                     "enthalpy_entropy_reference": "selected_mechanism_species_thermochemistry",
                     "common_min_temperature_k": float(gas.min_temp), "common_max_temperature_k": float(gas.max_temp),
                     "environment": deepcopy(environment)}
            species_models = []
            for species in composition:
                if composition[species] <= 0:
                    continue
                sp = gas.species(species)
                species_models.append({"species": species, "thermo": _json_safe(dict(sp.thermo.input_data))})
            model["participating_species_thermo"] = species_models
            model["participating_species_thermo_sha256"] = _hash(species_models)
            extrapolated = any(not row["within_mechanism_common_temperature_range"] for row in rows)
            table = {"status": "saved_with_extrapolation" if extrapolated else "saved",
                     "temperature_range_k": [low, high], "pressure_pa": float(outputs["p_in_Pa"]),
                     "composition_policy": "frozen_saved_inlet",
                     "columns": [{"key": k, "label": l, "unit": u} for k, l, u in PROPERTY_COLUMNS], "rows": rows,
                     "note": "固定本次入口质量分数，温区覆盖保存的实际进出口温度；表内压力固定为入口压力，故密度/熵不是沿涡轮的流动轨迹。端点物性另按各自压力保存；未重新平衡化学组成。黏度/导热率无模型时留空。"}
            capture.update(status="saved", source_sha256=_capture_source(result, component_key),
                           composition_mass_fractions=composition, model=model,
                           endpoint_properties={"inlet": inlet_row, "outlet": outlet_row}, property_table=table)
            capture["capture_sha256"] = _hash(capture)
        except Exception as exc:
            capture["reason"] = str(exc)
        finally:
            if gas is not None and saved_state is not None:
                gas.state = saved_state
    return answer


def _field(key, label, value, unit="—", status="saved", note="", source=""):
    if value is None:
        status = "conflict" if status == "conflict" else "missing"
    return {"key": key, "label": label, "value": value, "unit": unit, "status": status, "source": source, "note": note}


def _check(checks, key, label, status, note, required=True):
    checks.append({"id": key, "label": label, "status": status, "note": note, "required": required})


def _equal(checks, key, label, a, b, *, required=True, rel=2e-6, absolute=2e-5):
    if _num(a) is None or _num(b) is None:
        _check(checks, key, label, "missing", "缺少可核对的保存值；未按当前模型补算。", required)
    else:
        passed = math.isclose(a, b, rel_tol=rel, abs_tol=absolute)
        _check(checks, key, label, "pass" if passed else "conflict",
               f"保存值 {a:.10g}；独立对账值 {b:.10g}。" + ("" if passed else " 两者不一致，阻止就绪。"), required)


def _source_readiness_checks(result):
    """Use the project's acceptance contract, not a generic 'converged' flag."""
    checks = []
    calc = _dict(result.get("calculation"))
    summary, convergence = _dict(calc.get("summary")), _dict(calc.get("convergence"))
    assessment = _dict(result.get("assessment"))
    try:
        # Same lazy import boundary used by webapp.validation_checks. Importing
        # the serializer initializes src imports; it performs no science solve.
        from webapp.solver import EngineInputs  # noqa: F401
        from convergence import numerical_convergence
        numerical = numerical_convergence(calc)
        known_failure = (numerical["status"] == "fail" or convergence.get("status") == "fail"
                         or _dict(assessment.get("numerical")).get("status") == "fail")
        # These are real attach_convergence_status outputs, not alternate
        # acceptance criteria. A stale success flag never overrides the layers.
        if numerical["status"] == "pass" and _num(summary.get("numerical_converged")) == 0:
            known_failure = True
        status = "conflict" if known_failure else "pass" if numerical["status"] == "pass" else "missing"
        if status == "pass" and _num(summary.get("solver_evidence_incomplete")) == 1:
            status = "conflict"
        note = ("复用系统四层数值收敛判据：inner、thermal、preburner、continuation，"
                "核对启用层残差/阈值、完整目标到达及保存汇总残差。"
                + ("源计算明确未收敛或保存的失败标记与当前层证据冲突，不能交接为就绪设计点。" if status == "conflict" else
                   "收敛证据不完整；小残差或单个成功标记不能证明完整设计点已经收敛。" if status == "missing" else
                   "完整数值证据通过；这不代替工程可行性与部件验证。"))
        _check(checks, "source_numerical_convergence", "来源计算完整数值收敛", status, note)
        checks[-1]["evidence"] = _json_safe(numerical)
    except Exception as exc:
        _check(checks, "source_numerical_convergence", "来源计算完整数值收敛", "missing",
               "无法核对保存的分层收敛证据：" + str(exc))
    if assessment.get("status") == "fail":
        _check(checks, "source_engineering_failure", "来源系统约束失败", "conflict",
               "保存的系统工程校核明确为 fail；数值收敛或可填全局参数不能覆盖该失败。")
    canonical = calc.get("inputs")
    submitted = result.get("submitted_inputs")
    if "submitted_inputs" in result and not isinstance(submitted, dict):
        _check(checks, "input_snapshot_consistency", "保存输入快照一致性", "conflict",
               "submitted_inputs 格式无效；继续保留 calculation.inputs 的值，但不能声称来源完全一致。")
    elif isinstance(canonical, dict) and isinstance(submitted, dict):
        common = sorted(set(canonical) & set(submitted))

        def same(a, b):
            if _num(a) is not None and _num(b) is not None:
                return math.isclose(a, b, rel_tol=2e-12, abs_tol=1e-12)
            return type(a) is type(b) and a == b

        conflicts = [key for key in common if not same(canonical[key], submitted[key])]
        _check(checks, "input_snapshot_consistency", "保存输入快照一致性", "conflict" if conflicts else "pass",
               ("calculation.inputs 与 submitted_inputs 的共同字段冲突：" + "、".join(conflicts)
                + "。任务书仍以 calculation.inputs 为权威，冲突解决前不标记就绪。" if conflicts else
                f"已核对两份保存输入共有的 {len(common)} 个字段；仅单侧存在的历史/新增字段不作冲突判定。"))
        checks[-1]["conflicting_keys"] = conflicts
    return checks


def _apply_readiness(value):
    checks = value.get("checks", [])
    conflicts = [c for c in checks if c["status"] == "conflict"]
    missing = [c["label"] + "：" + c["note"] for c in checks if c["status"] == "missing" and c.get("required", True)]
    status = "conflict" if conflicts else "incomplete" if missing else "ready"
    value.update(status=status, ready_for_global_setup=status == "ready", missing_requirements=missing)


def _update_stage_handoff(stage, meanline):
    """Describe an already saved kinematic solution without upgrading its scope."""
    if meanline.get("status") == "kinematic_only":
        stage["missing_requirements"] = ["核验已给定的级负荷、反动度及流量系数约束在逐级气动上的可行性",
            "将已有均径速度三角形落实为含可压缩流、损失及端口总静态的一维逐级气动解",
            "叶片数量、型线、扭转、弦长、厚度、喉部及泄漏/间隙预算", "结构、材料及气动性能验证"]
        stage["note"] = ("均径初选已按本记录显式约束给出级比功分配和欧拉速度三角形，详见独立一维初选区域；"
                         "这是运动学闭合，尚未得到逐级压比、静态、损失、叶型或气动性能。")
    elif meanline.get("status") == "unavailable":
        stage["note"] = "本记录请求的一维均径初选未能满足输入或闭合条件：" + meanline.get("reason", "请核对显式约束。")


def _product(*values):
    if any(_num(value) is None for value in values):
        return None
    return _num(math.prod(values))


def _ratio(a, b):
    return _num(a / b) if _num(a) is not None and _num(b) is not None and b != 0 else None


def _validate_capture_table(capture, point):
    """Algebraic validation only: no property package, fitting or resampling."""
    model, table = _dict(capture.get("model")), _dict(capture.get("property_table"))
    if model.get("participating_species_thermo_sha256") != _hash(model.get("participating_species_thermo")):
        raise ValueError("参与物种的热力学模型校验值不一致。")
    if table.get("composition_policy") != "frozen_saved_inlet":
        raise ValueError("物性采样组成约定无法识别。")
    rows = table.get("rows")
    bounds = table.get("temperature_range_k")
    if not isinstance(rows, list) or not rows or not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("物性采样表或温区不完整。")
    if any(_num(v, positive=True) is None for v in bounds) or bounds[0] > bounds[1]:
        raise ValueError("物性采样温区无效。")
    pressure = _num(table.get("pressure_pa"), positive=True)
    if pressure is None or not math.isclose(pressure, point["pressure_in_pa"], rel_tol=2e-6):
        raise ValueError("物性表参考压力与保存入口不一致。")
    previous = None
    for row in rows + list(_dict(capture.get("endpoint_properties")).values()):
        if not isinstance(row, dict):
            raise ValueError("物性行格式无效。")
        for key in ("temperature_k", "pressure_pa", "cp_j_kg_k", "cv_j_kg_k", "gamma", "density_kg_m3", "molar_mass_kg_kmol"):
            if _num(row.get(key), positive=True) is None:
                raise ValueError("物性表缺少有限正值：" + key)
        for key in ("enthalpy_j_kg", "entropy_j_kg_k"):
            if _num(row.get(key)) is None:
                raise ValueError("物性表缺少有限的焓/熵值。")
        if not math.isclose(row["gamma"], row["cp_j_kg_k"] / row["cv_j_kg_k"], rel_tol=2e-6):
            raise ValueError("物性表 cp/cv 与 gamma 不一致。")
        if model.get("gas_eos") == "ideal-gas":
            r = R_UNIVERSAL / row["molar_mass_kg_kmol"]
            if not math.isclose(row["cp_j_kg_k"] - row["cv_j_kg_k"], r, rel_tol=2e-6):
                raise ValueError("理想气体物性表 cp-cv 与 R 不一致。")
            if not math.isclose(row["density_kg_m3"], row["pressure_pa"] / (r * row["temperature_k"]), rel_tol=2e-6):
                raise ValueError("理想气体物性表密度与 p/(RT) 不一致。")
    for row in rows:
        if previous is not None and row["temperature_k"] <= previous:
            raise ValueError("物性温度采样点未严格递增。")
        previous = row["temperature_k"]
        if not math.isclose(row["pressure_pa"], pressure, rel_tol=2e-6):
            raise ValueError("固定入口参考压力的物性表中出现其他压力。")
    for actual, expected in ((rows[0]["temperature_k"], bounds[0]), (rows[-1]["temperature_k"], bounds[1])):
        if not math.isclose(actual, expected, rel_tol=2e-6):
            raise ValueError("物性温区端点与采样点不一致。")
    for end, saved in (("inlet", "in"), ("outlet", "out")):
        row = _dict(_dict(capture.get("endpoint_properties")).get(end))
        for prop, point_key in (("temperature_k", "temperature_" + saved + "_k"), ("pressure_pa", "pressure_" + saved + "_pa")):
            if _num(row.get(prop)) is None or _num(point.get(point_key)) is None or not math.isclose(row[prop], point[point_key], rel_tol=2e-6):
                raise ValueError("物性端点与保存系统温压不一致。")


def _gas_view(result, side, component_key, point, inlet, checks):
    raw_model = _dict(result.get("environment"))
    physics = _dict(raw_model.get("physics"))
    gas = {"status": "missing", "composition_basis": "mass_fraction", "composition": [], "fields": [],
           "model": {"source": physics.get("chemistry"), "mechanism": physics.get("mechanism"),
                     "phase": physics.get("phase"), "mechanism_sha256": raw_model.get("mechanism_sha256"),
                     "gas_eos": _dict(raw_model.get("thermodynamic_scope")).get("gas_eos"),
                     "cantera_version": _dict(raw_model.get("dependencies")).get("cantera")},
           "endpoint_properties": {}, "property_table": _empty_table("此记录未在求解时保存物性温区采样；历史记录读取时不调用物性库补算。")}
    composition = None
    try:
        composition = _composition(inlet)
        gas["composition"] = [{"species": name, "mass_fraction": value} for name, value in sorted(composition.items())]
        _check(checks, "composition", "入口质量分数组成", "pass", "直接读取保存入口节点的全部组分，未硬编码、截断或重新平衡。")
    except ValueError as exc:
        _check(checks, "composition", "入口质量分数组成", "missing" if not inlet.get("composition") else "conflict", str(exc))
    source = "calculation.components." + component_key + ".outputs."
    for key, label, unit in (("molar_mass_kg_kmol", "平均摩尔质量 M", "kg/kmol"),
                             ("cp_in_j_kg_k", "入口定压比热 cp", "J/(kg·K)"), ("gamma_in", "入口比热比 γ", "—")):
        gas["fields"].append(_field(key, label, point[key], unit, source=source + POINT_FIELDS[key]))
    r = _ratio(R_UNIVERSAL, point["molar_mass_kg_kmol"])
    gas["fields"].append(_field("gas_constant_j_kg_k", "比气体常数 R", r, "J/(kg·K)", "derived", "R=8314.462618/M；M 使用 kg/kmol。", source + "molar_mass_in"))
    capture_root = _dict(result.get("turbine_gas_properties"))
    capture = _dict(_dict(capture_root.get("turbines")).get(side))
    if not capture:
        _check(checks, "property_capture", "求解时物性采样", "missing", gas["property_table"]["note"])
        return gas
    try:
        if capture_root.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("物性采样快照版本无效。")
        if capture.get("status") != "saved":
            _check(checks, "property_capture", "求解时物性采样", "conflict" if capture.get("status") == "conflict" else "missing", capture.get("reason", "本次未保存物性采样。"))
            return gas
        if capture.get("capture_sha256") != _hash({k: v for k, v in capture.items() if k != "capture_sha256"}):
            raise ValueError("保存的物性采样校验值不一致。")
        if capture.get("source_sha256") != _capture_source(result, component_key):
            raise ValueError("物性采样与本记录部件、节点或热力学环境不一致。")
        if _dict(capture.get("source")).get("component_key") != component_key:
            raise ValueError("物性采样对应另一台涡轮。")
        if composition != capture.get("composition_mass_fractions"):
            raise ValueError("物性采样组成与入口保存组成不一致。")
        model = _dict(capture.get("model"))
        if model.get("mechanism_sha256") != raw_model.get("mechanism_sha256") or model.get("mechanism") != physics.get("mechanism") or model.get("phase") != physics.get("phase"):
            raise ValueError("物性采样机制与保存求解环境不一致。")
        if model.get("cantera_version") != _dict(raw_model.get("dependencies")).get("cantera"):
            raise ValueError("物性采样与保存 Cantera 版本不一致。")
        _validate_capture_table(capture, point)
        gas.update(status="saved", model=deepcopy(model), endpoint_properties=deepcopy(capture["endpoint_properties"]),
                   property_table=deepcopy(capture["property_table"]))
        _check(checks, "property_capture", "求解时物性采样", "pass", "使用同一记录保存的机制 SHA256、全组分及端点/温区物性，读取时未重算。")
        endpoint = _dict(gas["endpoint_properties"].get("inlet"))
        for key, prop, label in (("molar_mass_kg_kmol", "molar_mass_kg_kmol", "入口摩尔质量"),
                                 ("cp_in_j_kg_k", "cp_j_kg_k", "入口比热"), ("gamma_in", "gamma", "入口比热比")):
            _equal(checks, "gas_" + prop, label + "与保存物性采样", point[key], endpoint.get(prop))
        _equal(checks, "gas_h_in", "入口节点焓与保存物性采样", inlet.get("enthalpy"), endpoint.get("enthalpy_j_kg"))
        if gas["property_table"].get("status") == "saved_with_extrapolation":
            _check(checks, "gas_temperature_range", "物性共同声明温区", "missing", "部分保存采样位于机制共同声明温区外；须审核参与物种热容温区和外推适用性，不能当作已验证物性。")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        gas["status"] = "conflict"
        _check(checks, "property_capture", "求解时物性采样", "conflict", str(exc))
    return gas


def _power_matching(point, components, inputs, side, component_key, checks):
    gg = component_key.startswith("gg_turbine")
    pump_keys = ["ox_pump", "fuel_pump"] if side == "shared" else ["ox_pump" if side == "ox" else "fuel_pump"]
    pump_powers = [_num(_dict(_dict(components.get(key)).get("outputs")).get("power_W"), positive=True) for key in pump_keys]
    demand = sum(pump_powers) if all(p is not None for p in pump_powers) else None
    mech = _num(inputs.get("gg_mech_efficiency"), positive=True) if gg else None
    delivered = _product(point["power_w"], mech) if gg else point["power_w"]
    power_checks = []
    if gg and (mech is None or mech > 1):
        _check(power_checks, "mechanical_efficiency", "GG 机械效率", "missing" if mech is None else "conflict", "必须使用本记录保存的 gg_mech_efficiency；不以涡轮等熵效率或默认值代替。")
    note = "GG 使用保存机械效率计算传递功率。" if gg else "FFSC 当前保存循环采用涡轮气体实际功率直接匹配同轴主泵；未另建轴承/密封机械损失预算，不据此填入 CFturbo ηm 或 ηadd。"
    if delivered is None or demand is None:
        _check(power_checks, "shaft_power_balance", "轴系功率匹配", "missing", "缺少涡轮功率、同轴主泵功率或机械效率。")
    else:
        relative = (delivered - demand) / demand
        _check(power_checks, "shaft_power_balance", "轴系功率匹配", "pass" if abs(relative) <= .005 else "conflict",
               f"传递功率 {delivered:.10g} W；主泵需求 {demand:.10g} W；相对偏差 {relative:.6%}。任务书对账阈值为 0.5%，不是部件性能验收容差。")
    checks.extend(power_checks)
    fields = [
        _field("isentropic_power_w", "等熵参考功率 PQ", _product(point["mass_flow_kg_s"], point["isentropic_drop_j_kg"]), "W", "derived", "PQ=ṁ·Δhs；与实际气体功率及轴端输出功率不同。"),
        _field("actual_gas_power_w", "实际气体功率", point["power_w"], "W", source="components." + component_key + ".outputs.turbine_power_W"),
        _field("turbine_isentropic_efficiency", "系统涡轮等熵效率", point["efficiency"], note="ηt=Δha/Δhs；是整台系统涡轮效率，未自动分配给各叶排。"),
        _field("mechanical_efficiency", "独立机械效率 ηm", mech, note="仅 GG 的已保存机械效率；它也不等于 CFturbo 的附加流动损失效率。", source="inputs.gg_mech_efficiency" if gg else "未单独建模"),
        _field("additional_efficiency", "CFturbo 附加效率 ηadd", None, note="独立的附加非典型流动损失项；本系统未保存该预算，不能复制 ηt、ηm，也不自动填 1。"),
        _field("delivered_power_w", "循环轴系记账功率", delivered, "W", "derived", note),
        _field("pump_power_required_w", "同轴主泵所需功率", demand, "W", "derived", "只汇总实际同轴主泵，增压泵外部功源不重复计入。", ", ".join(pump_keys)),
        _field("power_margin_w", "循环记账功率余量", delivered - demand if delivered is not None and demand is not None else None, "W", "derived", note),
    ]
    return {"fields": fields, "checks": power_checks, "pump_component_keys": pump_keys, "note": note}


def _build(result, side, component_key, prefix, label, components, inputs, record_id, source_checks=None):
    outputs, inlet, outlet, inlet_key, outlet_key = _context(result, component_key)
    source = {"record_id": record_id, "component_key": component_key, "assumption_prefix": prefix,
              "inlet_state_key": inlet_key, "outlet_state_key": outlet_key, "kind": "saved_system_result"}
    point = {key: _num(outputs.get(raw), positive=True) for key, raw in POINT_FIELDS.items()}
    # Reaction can be a valid zero target; it remains a target, not a solved stage value.
    point["reaction_degree_target"] = _num(outputs.get("reaction_degree"))
    point["state_basis"] = outputs.get("state_basis") if outputs.get("state_basis") in ("unspecified", "total_to_total") else "unspecified"
    point["state_assumption"] = outputs.get("state_assumption") if isinstance(outputs.get("state_assumption"), str) else None
    point["inlet_enthalpy_j_kg"] = _num(inlet.get("enthalpy"))
    point["outlet_enthalpy_j_kg"] = _num(outlet.get("enthalpy"))
    checks = deepcopy(source_checks if source_checks is not None else _source_readiness_checks(result))
    for key in ("mass_flow_kg_s", "pressure_in_pa", "pressure_out_pa", "temperature_in_k", "temperature_out_k", "shaft_speed_rpm",
                "mean_diameter_m", "mean_speed_m_s", "stages", "power_w", "isentropic_drop_j_kg", "actual_drop_j_kg", "efficiency"):
        if point[key] is None:
            _check(checks, "required_" + key, "保存系统点 " + key, "missing", "缺少有限正值，任务书不补默认值。")
    _equal(checks, "power_identity", "功率 = 质量流量 × 实际焓降", point["power_w"], _product(point["mass_flow_kg_s"], point["actual_drop_j_kg"]))
    _equal(checks, "efficiency_identity", "实际焓降 = 等熵效率 × 等熵焓降", point["actual_drop_j_kg"], _product(point["efficiency"], point["isentropic_drop_j_kg"]))
    _equal(checks, "pressure_ratio_identity", "压比与进出口压力", point["pressure_ratio"], _ratio(point["pressure_in_pa"], point["pressure_out_pa"]))
    _equal(checks, "diameter_speed_identity", "平均直径、转速和平均圆周速度", point["mean_speed_m_s"], _product(math.pi / 60, point["mean_diameter_m"], point["shaft_speed_rpm"]))
    if point["pressure_ratio"] is not None and point["pressure_ratio"] <= 1:
        _check(checks, "expansion_direction", "涡轮膨胀方向", "conflict", "涡轮入口压力必须高于出口压力。")
    if point["efficiency"] is not None and point["efficiency"] > 1:
        _check(checks, "efficiency_range", "涡轮效率范围", "conflict", "涡轮等熵效率大于 1。")
    if point["stages"] is not None and (int(point["stages"]) != point["stages"] or not 1 <= point["stages"] <= 6):
        _check(checks, "stages_integer", "保存级数", "conflict", "保存级数不是 1–6 的整数。")
    for end, state, state_key in (("in", inlet, inlet_key), ("out", outlet, outlet_key)):
        _equal(checks, "node_pressure_" + end, "部件压力与节点 " + state_key, point["pressure_" + end + "_pa"], state.get("pressure"))
        _equal(checks, "node_temperature_" + end, "部件温度与节点 " + state_key, point["temperature_" + end + "_k"], state.get("temperature"))
        # GG parallel inlet is the common generator node, whose mass is total.
        if end != "in" or not component_key.startswith("gg_turbine_"):
            _equal(checks, "node_mass_" + end, "部件流量与节点 " + state_key, point["mass_flow_kg_s"], state.get("mass_flow"))
    actual_from_states = (point["inlet_enthalpy_j_kg"] - point["outlet_enthalpy_j_kg"]
                          if point["inlet_enthalpy_j_kg"] is not None and point["outlet_enthalpy_j_kg"] is not None else None)
    _equal(checks, "node_enthalpy_drop", "实际焓降与保存节点焓差", point["actual_drop_j_kg"], actual_from_states)
    if component_key.startswith("gg_turbine_"):
        branch_flows = [_num(_dict(_dict(components.get(k)).get("outputs")).get("mass_flow_kgs"), positive=True)
                        for k in ("gg_turbine_ox", "gg_turbine_fuel")]
        total = sum(branch_flows) if all(value is not None for value in branch_flows) else None
        _equal(checks, "parallel_mass", "GG 两支路流量与公共入口", total, inlet.get("mass_flow"))
    try:
        if _composition(inlet) != _composition(outlet):
            # Float noise from a saved Cantera reconstitution is acceptable.
            a, b = _composition(inlet), _composition(outlet)
            same = all(math.isclose(a.get(k, 0), b.get(k, 0), abs_tol=2e-12, rel_tol=2e-6) for k in set(a) | set(b))
            if not same:
                _check(checks, "frozen_composition", "进出口冻结组成", "conflict", "保存进出口质量分数不一致；不能使用冻结组成物性表代替该过程。")
    except ValueError:
        _check(checks, "frozen_composition", "进出口冻结组成", "missing", "入口或出口节点缺少完整质量分数组成。")
    requested_basis = inputs.get("turbine_boundary_basis")
    if "state_basis" in outputs and outputs["state_basis"] not in ("unspecified", "total_to_total"):
        _check(checks, "unknown_boundary_basis", "部件状态基准", "conflict", "记录中的状态基准无法识别。")
    total_defined = requested_basis == "total_to_total" and point["state_basis"] == "total_to_total" and bool(point["state_assumption"])
    if total_defined:
        _check(checks, "boundary_basis", "总态接口约定", "assumed", point["state_assumption"])
    elif point["state_basis"] == "total_to_total" and requested_basis not in (None, "total_to_total"):
        _check(checks, "boundary_basis", "总态接口约定", "conflict", "保存输入与部件输出的边界状态基准不一致。")
    else:
        _check(checks, "boundary_basis", "总态接口约定", "missing", "记录未同时保存总总接口选择、部件状态标识和约定说明；历史节点 p/T 不能直接标为 pt/Tt，也不能推断 πts。")
    raw_source = "calculation.components." + component_key + ".outputs."
    global_fields = [
        _field("mass_flow_kg_s", "质量流量 ṁ", point["mass_flow_kg_s"], "kg/s", source=raw_source + "mass_flow_kgs"),
        _field("shaft_speed_rpm", "转速 n", point["shaft_speed_rpm"], "rpm", source=raw_source + "shaft_speed_rpm"),
    ]
    for key, pkey, field_label, unit in (("total_pressure_in_pa", "pressure_in_pa", "入口总压 pt,in", "Pa"),
                                  ("total_temperature_in_k", "temperature_in_k", "入口总温 Tt,in", "K"),
                                  ("total_pressure_out_pa", "pressure_out_pa", "出口总压 pt,out", "Pa"),
                                  ("total_temperature_out_k", "temperature_out_k", "出口实际总温 Tt,out", "K"),
                                  ("total_to_total_pressure_ratio", "pressure_ratio", "总总压比 πtt", "—")):
        global_fields.append(_field(key, field_label, point[pkey] if total_defined else None, unit, "assumed",
                                    "按本记录显式总总接口近似赋义；未求解端口速度后的总静转换。" if total_defined else "状态基准未定义，保留原节点值但不填此总态字段。", raw_source + POINT_FIELDS[pkey]))
    global_fields.extend([
        _field("total_to_static_pressure_ratio", "总静压比 πts", None, note="未求出口静压与绝对速度；不把 πtt 或节点压比复制为 πts。"),
        _field("isentropic_power_w", "等熵参考功率 PQ", _product(point["mass_flow_kg_s"], point["isentropic_drop_j_kg"]), "W", "derived", "PQ=ṁΔhs；不是实际轴功率。"),
        _field("additional_efficiency", "附加效率 ηadd（可选）", None, note="额外流动损失必须独立设定；不是系统等熵效率，也不是轴系机械效率。"),
    ])
    power = _power_matching(point, components, inputs, side, component_key, checks)
    gas = _gas_view(result, side, component_key, point, inlet, checks)
    if gas["status"] == "saved":
        out_properties = _dict(gas["endpoint_properties"].get("outlet"))
        _equal(checks, "gas_h_out", "出口节点焓与保存物性采样", point["outlet_enthalpy_j_kg"], out_properties.get("enthalpy_j_kg"))
    geometry = {"status": "missing", "geometry_sha256": None, "note": "未保存可用概念环道；系统设计任务书可独立读取。"}
    if "turbine_geometry" in result:
        shape = _dict(turbine_geometry_view(result, record_id)["turbines"].get(side))
        if shape.get("available"):
            geometry.update(status="assumed", geometry_sha256=shape.get("geometry_sha256"),
                            note="几何是保存的静态参考环道包络；总态接口近似未完成速度相关总静转换，CFturbo 须在一致边界下重新定流道，不能当作已验证叶型。")
            geom_point = _dict(shape.get("design_point"))
            for key in ("mass_flow_kg_s", "pressure_in_pa", "pressure_out_pa", "temperature_in_k", "temperature_out_k", "shaft_speed_rpm", "mean_diameter_m", "stages", "power_w"):
                _equal(checks, "geometry_" + key, "概念几何与任务书 " + key, point[key], geom_point.get(key))
            if _dict(shape.get("source")).get("component_key") != component_key:
                _check(checks, "geometry_component", "概念几何部件来源", "conflict", "几何对应另一台涡轮。")
        else:
            geometry["note"] = shape.get("reason", geometry["note"])
            raw_shape = _dict(_dict(_dict(result.get("turbine_geometry")).get("turbines")).get(side))
            if raw_shape.get("available"):
                geometry["status"] = "conflict"
                _check(checks, "geometry_integrity", "概念几何快照完整性", "conflict", geometry["note"])
    stage = {"fields": [
        _field("stages", "保存级数", point["stages"], source=raw_source + "n_stages_recommended"),
        _field("isentropic_drop_j_kg", "整台等熵焓降", point["isentropic_drop_j_kg"], "J/kg", source=raw_source + "isentropic_drop_Jkg"),
        _field("actual_drop_j_kg", "整台实际焓降", point["actual_drop_j_kg"], "J/kg", source=raw_source + "actual_drop_Jkg"),
        _field("mean_diameter_m", "系统估算平均直径", point["mean_diameter_m"], "m", note="保留系统已有直径及其限幅；不是已经优化的详细设计直径。"),
        _field("u_over_c_target", "目标 U/C0", point["u_over_c_target"], note="目标值不等于限幅后实际速比。"),
        _field("u_over_c_actual", "实际 U/C0", point["u_over_c_actual"]),
        _field("reaction_degree_target", "系统反动度目标", point["reaction_degree_target"], note="保存目标仅用于后续设计约束；不代表各级已解反动度。"),
        _field("per_stage_pressure_ratio", "逐级压比", None, note="无逐级热力学状态求解，不能等分/开方整机压比填入。"),
    ], "missing_requirements": ["逐级负荷与焓降分配、反动度分配及其适用假设", "各叶排速度三角形的边界约束与可行性核对", "叶片数量、型线、扭转、弦长、厚度、喉部及泄漏/间隙预算", "逐级总静态、损失模型、结构与材料验证"],
             "note": "此处仅移交保存的整机标量；不从反动度目标和概念环道自动编造逐级压比、静态或叶型。"}
    try:
        from webapp.turbine_meanline import meanline_view
        meanline = meanline_view(outputs, inputs, "fuel" if side == "shared" else side)
    except ImportError:
        meanline = {"available": False, "status": "disabled", "reason": "未启用独立的一维级设计。"}
    _update_stage_handoff(stage, meanline)
    value = {"available": bool(outputs), "label": label, "source": source,
             "global_fields": global_fields, "design_point": point,
             "power_matching": power, "gas": gas, "stage_handoff": stage, "meanline": meanline, "geometry": geometry,
             "checks": checks, "references": deepcopy(REFERENCES),
             "notes": ["就绪仅指本记录全局设计点与物性数据可供核对录入；不表示已经完成 CFturbo 项目、叶型、CFD 或制造设计。",
                       "总总接口是显式系统近似，未解析端口速度或自动恢复真实总态；πts、静压和各叶排参数保持待定。",
                       "Cantera 模型记录为冻结组成、温度相关比热；即使气体 EOS 是 ideal-gas，也不等于高压非理想性已验证或恒定比热已适用。",
                       "CFturbo 的气体模型及 cp(T) 表示方式须与保存物性相容；此任务书没有宣称可直接原生导入或自动替代其气体库。"]}
    _apply_readiness(value)
    value["design_sha256"] = _design_digest(value)
    return value


def _design_digest(value):
    copy = deepcopy(value)
    copy.pop("design_sha256", None)
    _dict(copy.get("source")).pop("record_id", None)
    return _hash(copy)


def _failure(label, record_id, reason):
    return {"available": False, "label": label, "source": {"record_id": record_id, "kind": "saved_record"},
            "status": "conflict", "ready_for_global_setup": False, "reason": reason, "global_fields": [],
            "design_point": {}, "power_matching": {"fields": [], "checks": []},
            "gas": {"status": "missing", "composition_basis": "mass_fraction", "composition": [], "fields": [], "model": {}, "property_table": _empty_table(reason)},
            "stage_handoff": {"fields": [], "missing_requirements": []}, "geometry": {"status": "missing", "note": reason},
            "checks": [{"id": "record_integrity", "label": "记录完整性", "status": "conflict", "note": reason}],
            "missing_requirements": [], "notes": [], "references": deepcopy(REFERENCES)}


def _result_fingerprint(result):
    """Version 1: bind saved science, not display-only record identifiers.

    Keep this projection stable. Any future projection change must increment
    SOURCE_FINGERPRINT_VERSION rather than silently changing stored identities.
    """
    calc = _dict(result.get("calculation"))
    geometry = deepcopy(result.get("turbine_geometry"))
    if isinstance(geometry, dict):
        _dict(geometry.get("source")).pop("record_id", None)
        for turbine in _dict(geometry.get("turbines")).values():
            _dict(_dict(turbine).get("source")).pop("record_id", None)
    return _hash(_json_safe({"calculation": calc, "effective_saved_inputs": calc.get("inputs") or result.get("submitted_inputs"),
                            "environment": result.get("environment"),
                            "turbine_gas_properties": result.get("turbine_gas_properties"),
                            "turbine_geometry": geometry}))


def turbine_design_view(result, record_id=None):
    """Audit only saved state; never import or call the thermodynamic library."""
    record_id = record_id or ""
    answer = {"schema_version": SCHEMA_VERSION, "method_id": METHOD,
              "source_fingerprint_version": SOURCE_FINGERPRINT_VERSION,
              "source": {"record_id": record_id, "kind": "saved_record"}, "turbines": {}}
    failure_labels = {"ox": "氧泵轴涡轮", "fuel": "燃料泵轴涡轮"}
    source_checks = []
    try:
        if not isinstance(result, dict):
            raise ValueError("请选择已经保存的系统计算记录。")
        source_checks = _source_readiness_checks(result)
        saved = result.get("turbine_design")
        if saved is not None:
            if not isinstance(saved, dict) or saved.get("schema_version") != SCHEMA_VERSION or saved.get("method_id") != METHOD:
                raise ValueError("保存的涡轮设计任务书版本无法识别。")
            if saved.get("source_fingerprint_version") != SOURCE_FINGERPRINT_VERSION:
                raise ValueError("保存的任务书来源指纹版本无法识别；不能按另一种指纹约定验证。")
            turbines = saved.get("turbines")
            if not isinstance(turbines, dict) or set(turbines) not in ({"ox", "fuel"}, {"shared"}):
                raise ValueError("保存的涡轮设计任务书轴系拓扑无效。")
            failure_labels = {side: _dict(value).get("label", "涡轮设计任务书") for side, value in turbines.items()}
            if "calculation" in result and saved.get("source_sha256") != _result_fingerprint(result):
                raise ValueError("任务书来源与保存的系统结果、物性或几何快照不一致。")
            answer["source_sha256"] = saved.get("source_sha256")
            for side, value in turbines.items():
                if not isinstance(value, dict) or value.get("design_sha256") != _design_digest(value):
                    answer["turbines"][side] = _failure("涡轮设计任务书", record_id, "保存的任务书校验值不一致；不从当前输入补建。")
                    continue
                value = deepcopy(value)
                value["source"]["record_id"] = record_id
                if "calculation" in result:
                    # Historical presentation snapshots do not get to bypass a
                    # newer acceptance gate. Re-audit saved evidence only.
                    replacements = {check["id"]: check for check in source_checks}
                    old_checks = value.get("checks", [])
                    seen = {c.get("id") for c in old_checks}
                    value["checks"] = [deepcopy(replacements.get(c.get("id"), c)) for c in old_checks]
                    value["checks"].extend(deepcopy(c) for c in source_checks if c["id"] not in seen)
                    _apply_readiness(value)
                    _update_stage_handoff(_dict(value.get("stage_handoff")), _dict(value.get("meanline")))
                    value["design_sha256"] = _design_digest(value)
                answer["turbines"][side] = value
            return answer
        specs, components, inputs = _specs(result)
        failure_labels = {side: label for side, _, _, label in specs}
        for side, component_key, prefix, label in specs:
            try:
                answer["turbines"][side] = _build(result, side, component_key, prefix, label, components, inputs, record_id, source_checks)
            except Exception as exc:
                answer["turbines"][side] = _failure(label, record_id, str(exc))
        answer["source_sha256"] = _result_fingerprint(result)
        json.dumps(answer, allow_nan=False)
    except Exception as exc:
        answer["turbines"] = {side: _failure(label, record_id, str(exc)) for side, label in failure_labels.items()}
        for value in answer["turbines"].values():
            value["checks"].extend(deepcopy(source_checks))
    return answer
