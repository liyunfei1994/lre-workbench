"""Read saved physical-valve evidence and aggregate compatible design points.

No cycle solve, property lookup, draft access or sizing is performed here. A range is
an index of complete saved points; its extrema are never assembled into a new
synthetic inlet state. Evidence hashes identify records, not hardware approval.
"""
from copy import deepcopy
import json
import math

from webapp.design_traceability import fingerprint, source_context


VALVE_IDS = ("MFV", "OPFV", "FPOV")
SCOPE = "saved_valve_design"
SAVED_PATH = "calculation.steady_closure.valve_design"
LIMITATIONS = [
    "仅使用所选计算保存的实体阀结果；不会从当前草稿或其他方案补齐缺项。",
    "稠密单相节流模型及候选特性用于概念配平和初步选型，不等于固定硬件或厂家选型认证。",
    "开度、饱和和余量仅对应保存的工况及特性版本；执行器稳态值不证明动态稳定。",
    "阀门工况检查通过不代表源整机的数值收敛、两轴匹配和工程校核通过。",
]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _json_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return _dict(value)


def _number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    except (TypeError, OverflowError):
        return False


def _hash_or_none(value):
    try:
        return fingerprint(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _source(result, record, saved):
    calculation = _dict(result.get("calculation"))
    return {
        "project_id": record.get("project_id"), "revision": record.get("revision"),
        "source_design_job_id": record.get("id"), "input_hash": _hash_or_none(calculation.get("inputs")),
        "topology_hash": saved.get("topology_hash"),
        "characteristic_hash": deepcopy(_dict(saved.get("characteristic_hash"))),
    }


def _trace_issues(result, record):
    """Audit the shared completion seal without reconstructing other components."""
    trace = _dict(result.get("design_traceability"))
    if not trace:
        return ["本记录未保存完成时的方案追溯证据，无法确认实体阀结果归属。"], "missing"
    issues = []
    seal = _hash_or_none({k: v for k, v in trace.items() if k != "evidence_sha256"})
    if trace.get("schema_version") != 1 or not seal or trace.get("evidence_sha256") != seal:
        issues.append("保存的方案追溯证据封印无效。")
    for key, value in source_context(record).items():
        if _dict(trace.get("source")).get(key) != value:
            issues.append("实体阀结果的方案记录归属冲突：" + key)
    if not record.get("project_id") or not record.get("id") or record.get("revision") is None:
        issues.append("所选记录缺少方案、修订或设计任务身份。")
    calculation = _dict(result.get("calculation"))
    evidence = {
        "submitted_inputs_sha256": _json_dict(record.get("inputs")),
        "solver_inputs_sha256": _dict(result.get("submitted_inputs")),
        "calculation_inputs_sha256": _dict(calculation.get("inputs")),
        "calculation_sha256": calculation,
    }
    for key, value in evidence.items():
        actual = _hash_or_none(value)
        if not actual or trace.get(key) != actual:
            issues.append("实体阀结果与本次保存证据不一致：" + key)
    return issues, "conflict" if issues else "verified"


def valve_design_view(result, record=None):
    """Project one immutable job; ``record`` is its saved database row."""
    result, record = _json_dict(result), _dict(record)
    saved = _dict(_dict(_dict(result.get("calculation")).get("steady_closure")).get("valve_design"))
    report = {
        "schema_version": 1, "scope": SCOPE, "source_path": SAVED_PATH,
        "status": "missing", "calculation_status": None,
        "source": _source(result, record, saved), "configuration": {}, "rows": [],
        "issues": [], "traceability_status": "missing", "selection_eligible": False,
        "limitations": list(LIMITATIONS), "validation_status": "candidate_not_certified",
    }
    if not saved:
        report["issues"].append("该历史记录未保存实体阀计算；请在概念设计定义三阀并新建计算。")
        return report
    configuration = _dict(saved.get("configuration"))
    report.update(configuration=deepcopy(configuration), calculation_status=saved.get("status"))
    report["limitations"].extend(x for x in saved.get("limitations", []) if isinstance(x, str))
    issues, trace_status = _trace_issues(result, record)
    report.update(issues=issues, traceability_status=trace_status)
    if saved.get("schema_version") != 1 or saved.get("scope") != "valve_constrained_concept_design":
        issues.append("保存的实体阀模型版本或计算范围无法识别。")
    if saved.get("status") not in ("pass", "infeasible"):
        issues.append("保存的实体阀计算缺少明确通过或不可行状态。")
    valves, configurations = _dict(saved.get("valves")), _dict(configuration.get("valves"))
    if set(valves) != set(VALVE_IDS) or set(configurations) != set(VALVE_IDS):
        issues.append("保存的实体阀及配置必须恰好包含 MFV、OPFV、FPOV。")
    try:
        # Hashing the saved declarations performs no property calculation.
        from lre_workbench.components.valves import characteristic_hash, topology_hash, parse_configuration
        used_inputs = _dict(_dict(result.get("calculation")).get("inputs"))
        if parse_configuration(used_inputs.get("valve_configuration_json")) != parse_configuration(configuration):
            issues.append("实体阀配置与本次求解输入不一致。")
        if saved.get("topology_hash") != topology_hash(configuration):
            issues.append("保存的阀拓扑指纹与安装节点不一致。")
        hashes = _dict(saved.get("characteristic_hash"))
        if set(hashes) != set(VALVE_IDS):
            issues.append("三阀特性指纹未完整保存。")
        for valve_id in VALVE_IDS:
            if hashes.get(valve_id) != characteristic_hash(_dict(configurations.get(valve_id))):
                issues.append(valve_id + " 特性指纹与保存配置不一致。")
    except (ImportError, ValueError, KeyError, TypeError) as exc:
        issues.append("无法核对保存的阀门拓扑/特性：" + str(exc))
    missing = []
    for valve_id in VALVE_IDS:
        valve, definition = _dict(valves.get(valve_id)), _dict(configurations.get(valve_id))
        if not valve:
            missing.append(valve_id + " 缺少实体阀结果。")
        else:
            for key in ("id", "fluid", "upstream_node", "downstream_node", "characteristic_version"):
                if valve.get(key) != definition.get(key):
                    issues.append(valve_id + " 求解结果与保存安装/特性身份冲突：" + key)
            if valve.get("characteristic_hash") != _dict(saved.get("characteristic_hash")).get(valve_id):
                issues.append(valve_id + " 求解结果的特性指纹不一致。")
            if valve.get("mode") != configuration.get("mode"):
                issues.append(valve_id + " 求解模式与保存配置不一致。")
            if valve.get("status") not in ("available", "feasible", "infeasible", "unavailable"):
                missing.append(valve_id + " 缺少可识别的物理状态。")
            if saved.get("status") == "pass" and valve.get("status") not in ("available", "feasible"):
                issues.append(valve_id + " 状态与三阀通过汇总不一致。")
            inlet = _dict(valve.get("inlet_state"))
            if inlet:
                for inlet_key, row_key in (("pressure_Pa", "upstream_pressure_Pa"),
                                           ("temperature_K", "upstream_temperature_K"), ("fluid", "fluid")):
                    if inlet.get(inlet_key) != valve.get(row_key):
                        issues.append(valve_id + " 保存入口状态与安装端口工况不一致：" + inlet_key)
        row = {"id": valve_id, "configuration": deepcopy(definition),
               "result": deepcopy(valve), "source": deepcopy(report["source"])}
        row["actuator_definition"] = {key: deepcopy(definition.get(key)) for key in (
            "actuator_tau_s", "actuator_rate_per_s", "opening_min", "opening_max")}
        row["actuator_preview"] = valve_actuator_preview(definition, row["source"])
        report["rows"].append(row)
    # An unsealed legacy result can be displayed, but cannot become a selection
    # package merely because the caller supplies a project id at read time.
    report["status"] = "conflict" if issues and trace_status != "missing" else "incomplete" if issues or missing else "recorded"
    report["issues"].extend(missing)
    report["selection_eligible"] = report["status"] == "recorded"
    return report


def valve_actuator_preview(definition, source=None):
    """Analytical travel-step preview of the shared rate-limited actuator law.

    This calculation uses only the saved actuator declaration, never a cycle
    solve. It is explicitly a command-response estimate, not a valve-force or
    engine-dynamic validation. Each sample records the shared kernel's rate.
    """
    preview = {"status": "unavailable", "model": "first_order_rate_limited_step_analytic_v1",
               "source": deepcopy(source), "samples": [], "rise_t90_s": None, "fall_t90_s": None,
               "note": "采用已保存行程、时间常数和速度限值，复用共享执行器速率内核；未耦合阀芯受力、流体瞬态或整机控制。"}
    try:
        from lre_workbench.components.valves import actuator_derivative
        low, high = definition["opening_min"], definition["opening_max"]
        tau, rate = definition["actuator_tau_s"], definition["actuator_rate_per_s"]
        if not all(_number(value) for value in (low, high, tau, rate)) or not 0 <= low < high <= 1 or tau <= 0 or rate <= 0:
            raise ValueError("执行器行程、时间常数或速度限值无效。")
        travel = high - low
        switch = max(0., (travel - rate * tau) / rate)
        remaining = travel - rate * switch

        def time_to(fraction):
            target_travel = fraction * travel
            if target_travel <= rate * switch:
                return target_travel / rate
            return switch + tau * math.log(remaining / ((1 - fraction) * travel))

        t90, horizon = time_to(.9), time_to(.99)
        if not all(_number(value) and value > 0 for value in (t90, horizon)):
            raise ValueError("执行器响应时间超出有限计算范围。")
        for index in range(51):
            time = horizon * (index / 50)
            displacement = rate * time if time < switch else travel - remaining * math.exp(-(time-switch)/tau)
            rise, fall = low + displacement, high - displacement
            preview["samples"].append({"time_s": time, "rise_opening": rise, "fall_opening": fall,
                "rise_rate_per_s": actuator_derivative(rise, high, tau, rate, lower=low, upper=high),
                "fall_rate_per_s": actuator_derivative(fall, low, tau, rate, lower=low, upper=high)})
        preview.update(status="estimated", rise_t90_s=t90, fall_t90_s=t90, response_horizon_t99_s=horizon,
                       opening_min=low, opening_max=high, actuator_tau_s=tau, actuator_rate_per_s=rate)
    except (KeyError, ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
        preview.update(reason=str(exc), samples=[])
    return preview


# (result field path, label, unit). These are saved-state observations only.
ENVELOPE_FIELDS = (
    (("mass_flow_kg_s",), "流量", "kg/s"),
    (("pressure_drop_Pa",), "阀压差", "Pa"),
    (("inlet_state", "pressure_Pa"), "入口压力", "Pa"),
    (("inlet_state", "temperature_K"), "入口温度", "K"),
    (("inlet_state", "density_kg_m3"), "入口密度", "kg/m³"),
    (("required_cda_m2",), "所需有效面积", "m²"),
    (("required_rated_cda_m2",), "所需额定有效面积", "m²"),
    (("opening",), "开度", "1"),
    (("opening_margin_lower",), "下开度余量", "1"),
    (("opening_margin_upper",), "上开度余量", "1"),
    (("flow_margin_kg_s",), "流量余量", "kg/s"),
)


def _at(value, path):
    for key in path:
        value = _dict(value).get(key)
    return value


def valve_selection_envelope(records):
    """Collect compatible saved jobs, rejecting identity and model mixing.

    Candidate failures remain visible: the envelope is a requirements package,
    not a pass certificate. Missing observations are explicit, never fabricated.
    """
    if not isinstance(records, (list, tuple)) or not records:
        raise ValueError("请明确选择至少一个已保存的设计工况。")
    reports = [valve_design_view(_json_dict(row.get("result")), row)
               for row in records if isinstance(row, dict)]
    if len(reports) != len(records) or any(not report["selection_eligible"] for report in reports):
        raise ValueError("选型包只接受实体阀结果和方案追溯证据完整且一致的计算记录。")
    sources = [report["source"] for report in reports]
    ids = [source["source_design_job_id"] for source in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("不能重复选择同一个设计工况。")
    first = sources[0]
    for key, label in (("project_id", "方案"), ("topology_hash", "拓扑版本"),
                       ("characteristic_hash", "三阀特性版本")):
        if any(source[key] != first[key] for source in sources[1:]):
            raise ValueError("多工况选型包必须使用相同" + label + "，禁止混合不同记录配置。")
    points = [{"source": deepcopy(report["source"]), "calculation_status": report["calculation_status"],
               "rows": deepcopy(report["rows"])} for report in reports]
    package = {
        "schema_version": 1, "scope": "saved_valve_selection_envelope", "status": "requirements_only",
        "project_id": first["project_id"], "topology_hash": first["topology_hash"],
        "characteristic_hash": deepcopy(first["characteristic_hash"]), "sources": deepcopy(sources),
        "points": points, "valves": {}, "validation_status": "candidate_not_certified",
        "all_points_feasible": all(report["calculation_status"] == "pass" for report in reports),
        "limitations": list(LIMITATIONS) + [
            "各极值只是已选工况的范围；每个极值保留完整驱动工况，禁止拼接极值计算虚构入口状态。",
            "未选工况、启动关机和特性适用域之外的运行点不在本包覆盖范围内。",
        ],
    }
    for valve_id in VALVE_IDS:
        rows = [next(row for row in report["rows"] if row["id"] == valve_id) for report in reports]
        envelope = {"id": valve_id, "configuration": deepcopy(rows[0]["configuration"]), "ranges": {},
                    "saturated_sources": [deepcopy(row["source"]) for row in rows if row["result"].get("saturated") is True],
                    "infeasible_sources": [deepcopy(report["source"]) for report in reports
                                           if report["calculation_status"] != "pass"]}
        for path, label, unit in ENVELOPE_FIELDS:
            present = [(row, _at(row["result"], path)) for row in rows if _number(_at(row["result"], path))]
            missing_sources = [deepcopy(row["source"]) for row in rows if not _number(_at(row["result"], path))]
            value = {"label": label, "unit": unit, "status": "complete" if not missing_sources else "partial" if present else "missing",
                     "missing_sources": missing_sources, "min": None, "max": None}
            for extremum, select in (("min", min), ("max", max)):
                if present:
                    row, number = select(present, key=lambda item: item[1])
                    value[extremum] = {"value": number, "source": deepcopy(row["source"]), "point": deepcopy(row)}
            envelope["ranges"][".".join(path)] = value
        package["valves"][valve_id] = envelope
    package["package_sha256"] = fingerprint(package)
    return package
