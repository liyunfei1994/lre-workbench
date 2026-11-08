"""Immutable outer-control task declarations, separate from fixed-valve tasks.

No engine solver is dispatched here. A saved concept output can define the
percentage reference, but cannot certify a rated fixed-hardware operating point.
"""
from copy import deepcopy
import math

from webapp.validation import fingerprint


OPTIONAL_TARGETS = {
    "overall_of": ("整机 氧燃质量比", "1"),
    "ox_preburner_temperature": ("富氧预燃室出口温度", "K"),
    "fuel_preburner_temperature": ("富燃预燃室出口温度", "K"),
}


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"{label}含未知或不允许的字段。")
    return value


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须为有限正数。")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label}必须为有限正数。") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label}必须为有限正数。")
    return result


def _saved_number(value, *, zero=False):
    try:
        if zero and not isinstance(value, bool) and isinstance(value, (int, float)) and value == 0:
            return 0.0
        return _positive(value, "保存的输出")
    except ValueError:
        return None


def configuration(record, source):
    task = record["task"]
    if task.get("mode") != "fixed_hardware_steady" or task.get("schema_version") not in (1, 2):
        raise ValueError("配平定义只能绑定受支持的固定硬件稳态任务。")
    hardware = task["hardware"]
    if fingerprint(hardware["payload"]) != hardware["id"]:
        raise ValueError("硬件内容与保存的硬件摘要不一致，不能建立配平定义。")
    origin = task["source"]
    if (source["id"] != origin["job_id"]
            or fingerprint(source["result"]) != origin["result_sha256"]):
        raise ValueError("来源结果与固定硬件任务绑定不一致，请重新建立任务；历史定义不会被改写。")
    calculation = source["result"].get("calculation") or {}
    summary = calculation.get("summary") or {}
    inputs = calculation.get("inputs") or {}
    reference = {
        "value_N": _saved_number(summary.get("thrust_N")), "unit": "N",
        "source_path": "calculation.summary.thrust_N",
        "basis": "saved_concept_output", "validated_fixed_hardware": False,
        "label": "来源设计点计算推力（百分比基准）",
        "ambient_pressure_Pa": _saved_number(inputs.get("ambient_pressure"), zero=True),
    }
    blockers = [
        {"code": "inner_model_unavailable", "label": "固定硬件内层尚未接入并验证",
         "detail": "外层只能调用固定硬件工作点模型；独立代数自检通过不能开放发动机配平。"},
        {"code": "actuators_missing", "label": "尚未登记可操纵阀门及特性",
         "detail": "需阀门唯一标识、连接端口、开度特性、硬件限位及控制分配；不把四个供给支路自动视为四个独立阀门。"},
        {"code": "reference_point_unverified", "label": "百分比基准尚未验证为固定硬件额定点",
         "detail": "使用保存的概念计算输出仅定义目标数值；需用同一冻结硬件与明确工况复核额定工作点。"},
    ]
    if reference["value_N"] is None:
        blockers.append({"code": "reference_thrust_missing", "label": "来源缺少有效计算推力",
                         "detail": "不能用概念设计输入的推力目标替代实际保存输出。"})
    return {
        "schema_version": 1, "analysis_type": "fixed_hardware_control_trim_setup",
        "binding": {"task_id": record["id"], "task_sha256": fingerprint(task),
                    "hardware_id": task["hardware"]["id"],
                    "source_result_sha256": origin["result_sha256"]},
        "reference_thrust": reference,
        # Characteristics declarations have not been adopted or validated yet.
        # Even a nonempty client/stale hardware dictionary is not a registry.
        "available_controls": [],
        "defaults": {"thrust_percent": 100.0, "thrust_relative_tolerance": 0.001,
                     "additional_targets": {}, "controls": []},
        "readiness": {"can_run": False, "status": "blocked", "blockers": blockers},
    }


def normalize_request(value, record, source):
    _object(value, {"thrust_percent", "thrust_relative_tolerance", "additional_targets", "controls"}, "配平请求")
    setup = configuration(record, source)
    percent = _positive(value.get("thrust_percent"), "目标推力百分比")
    relative = _positive(value.get("thrust_relative_tolerance", 0.001), "推力相对容差")
    if relative >= 1:
        raise ValueError("推力相对容差必须小于 1。")
    controls = value.get("controls", [])
    if not isinstance(controls, list) or controls:
        raise ValueError("尚无已登记且验证的可操纵阀门；controls 当前只能为空列表，不能隐式改变分流。")
    additional = _object(value.get("additional_targets", {}), OPTIONAL_TARGETS, "附加控制目标")
    reference = setup["reference_thrust"]["value_N"]
    target = _positive(reference * (percent / 100.0), "目标推力") if reference is not None else None
    tolerance = _positive(target * relative, "推力绝对容差") if target is not None else None
    targets = [{"name": "thrust", "label": "推力", "unit": "N", "value": target, "tolerance": tolerance}]
    normalized = {}
    for name, item in additional.items():
        _object(item, {"value", "tolerance"}, "附加控制目标")
        if set(item) != {"value", "tolerance"}:
            raise ValueError("每个附加控制目标必须显式提供 value 和绝对 tolerance。")
        label, unit = OPTIONAL_TARGETS[name]
        normalized[name] = {"value": _positive(item["value"], label),
                            "tolerance": _positive(item["tolerance"], label + "绝对容差")}
        targets.append({"name": name, "label": label, "unit": unit, **normalized[name]})
    readiness = deepcopy(setup["readiness"])
    readiness["blockers"].append({"code": "insufficient_control_count", "label": "可调控制量数量不足",
                                  "detail": f"当前有 {len(targets)} 个等式目标、0 个已登记可调阀门；数量检查也不能代替控制独立性检查。"})
    return {
        "schema_version": 1, "mode": "fixed_hardware_steady_trim", "scope": "task_definition_only",
        "binding": setup["binding"], "reference_thrust": setup["reference_thrust"],
        "thrust_percent": percent, "thrust_relative_tolerance": relative,
        "additional_targets": normalized, "targets": targets, "controls": [],
        "control_dof": {"control_count": 0, "target_count": len(targets), "count_sufficient": False,
                        "independence": "not_checked", "rank": None},
        "constraints": deepcopy(record["task"].get("constraints", {})),
        "readiness": readiness,
        "result_policy": {
            "inner_failure": "numerical_failure", "outer_failure": "numerical_failure",
            "bounds_without_proof": "target_not_met", "certified_infeasible": "target_unreachable",
            "note": "到达阀位边界仅记录限位与目标误差；不可达结论需要适用域内的额外证据。",
        },
    }


def assert_runnable(definition):
    # Never trust persisted/client readiness and never fall back to sizing.
    raise ValueError("实际稳态控制配平尚未开放：需先接入并验证固定硬件内层、阀门特性与额定点。")
