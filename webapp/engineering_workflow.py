"""Engineering task ownership and immutable design-iteration records.

This layer organizes existing models. It never promotes sizing assumptions to
component characteristics, or declares an unavailable hardware solver runnable.
The legacy EngineInputs keys remain the single editable parameter source.
"""
from copy import deepcopy
import hashlib
import json


COMPONENTS = [
    {"id": "chamber", "label": "推力室与喷管", "groups": ["chamber_contour"],
     "views": ["geometry", "chamber_design", "nozzle_design"],
     "objects": ["main_chamber", "nozzle"], "impact": "cycle",
     "description": "接收总体状态与推力要求，设计内型面并核对性能、热环境与尺寸。"},
    {"id": "cooling", "label": "再生冷却系统", "groups": ["chamber_cooling_layout", "chamber_channels", "wall_thermal"],
     "views": ["cooling_workspace"], "objects": ["cooling_channel"], "impact": "cycle",
     "description": "定义通道、覆盖范围和壁面材料；详细模型的吸热与压降反馈整机。"},
    {"id": "pumps", "label": "泵与轴系", "groups": ["pumps"],
     "views": ["pump_geometry"], "objects": ["ox_pump", "fuel_pump"], "impact": "component",
     "description": "以保存的流量、压升和功率需求开展级数、转速与流道初步设计。"},
    {"id": "turbines", "label": "涡轮", "groups": ["turbines"],
     "views": ["turbine_geometry"], "objects": ["ox_preburner_turbine", "fuel_preburner_turbine"], "impact": "component",
     "description": "以保存的入口状态与做功需求开展级方案、均径初选和建模交接。"},
    {"id": "injectors", "label": "预燃室与喷注器", "groups": [],
     "views": ["injector_design"], "objects": ["ox_preburner", "fuel_preburner"], "impact": "cycle",
     "description": "读取循环设计要求和喷注器初估；预燃室匹配目标仍归总体设计。"},
    {"id": "valves", "label": "阀门、管路与供给部件", "groups": ["valve_configuration"],
     "views": ["valve_design"], "objects": ["MFV", "OPFV", "FPOV"], "impact": "cycle",
     "description": "定义实体流路与阀门方案，核对压降、开度和多工况选型证据。"},
]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def workflow_catalog():
    return {"version": 1, "components": deepcopy(COMPONENTS), "stages": [
        {"id": "concept", "label": "总体方案与循环设计"},
        {"id": "components", "label": "部件设计与特性建模"},
        {"id": "integration", "label": "整机集成与基准确认"},
        {"id": "steady", "label": "固定硬件稳态分析"},
        {"id": "transient", "label": "瞬态运行分析"},
    ]}


def component_definition(component_id):
    return next((deepcopy(c) for c in COMPONENTS if c["id"] == component_id), None)


def parameter_ownership(groups):
    owners = {}
    for group in groups:
        component = next((c for c in COMPONENTS if group["id"] in c["groups"]), None)
        for field in group["fields"]:
            if field.get("virtual"):
                continue
            if field["key"] in owners:
                raise ValueError(f"参数存在多个编辑归属：{field['key']}")
            owners[field["key"]] = {"group": group["id"], "editable": not field.get("read_only", False),
                "component": component["id"] if component else None,
                "stage": group.get("workflow_stage", "components" if component else "concept")}
    return owners


def changed_parameters(before, after):
    return sorted(key for key in set(before) | set(after)
                  if (key in before) != (key in after) or before.get(key) != after.get(key))


def numerical_evidence(result):
    """Read solver evidence separately from a job's execution status."""
    from lre_workbench.solvers.convergence import numerical_convergence
    evidence = numerical_convergence(result)
    if (result.get("initialization_diagnostics") or {}).get("status") == "failed":
        evidence.update(status="fail", label="初始化失败", termination_reason="initialization_failed")
    elif (result.get("assessment") or {}).get("numerical", {}).get("status") == "fail":
        evidence.update(status="fail", label="未收敛")
    return {key: evidence[key] for key in ("status", "label", "termination_reason")}


def component_patch(component_id, patch, before, groups, *, allow_unchanged=False):
    component = component_definition(component_id)
    if not component or not patch:
        raise ValueError("请选择有设计输入的部件，并提供本次修改。")
    owners = parameter_ownership(groups)
    allowed = {key for key, owner in owners.items() if owner["component"] == component_id and owner["editable"]}
    if set(patch) - allowed:
        raise ValueError("部件反馈只能修改本部件拥有的参数；总体要求请在总体设计中修改。")
    values = deepcopy(before)
    values.update(deepcopy(patch))
    changes = changed_parameters(before, values)
    if not changes and not allow_unchanged:
        raise ValueError("本部件没有待保存的参数修改。")
    return values, changes


def baseline_snapshot(source):
    """Freeze evidence actually present in a saved job; do not fill absent data."""
    result = source["result"]
    calculation = result.get("calculation") or {}
    payload = {"source_job_id": source["id"], "source_revision": source["revision"],
               "inputs": deepcopy(source["inputs"]),
               "calculated_inputs": deepcopy(calculation.get("inputs")),
               "reference_states": deepcopy(calculation.get("states")),
               "geometries": {key: deepcopy(result[key]) for key in
                              ("nozzle_geometry", "pump_geometry", "turbine_geometry") if key in result},
               "component_evidence": deepcopy(calculation.get("components") or {}),
               "model_evidence": deepcopy(result.get("design_traceability") or {}),
               "numerical_evidence": numerical_evidence(result),
               "result_sha256": fingerprint(result)}
    blockers = []
    try:
        from webapp.fixed_steady import configuration
        task = configuration(source)
        blockers = deepcopy(task["readiness"]["blockers"])
        payload["fixed_hardware_definition"] = task["hardware"]
        payload["model_data"] = task["model_data"]
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        blockers.append({"code": "source_incomplete", "label": "来源尚不具备固定硬件交接条件",
                         "detail": str(exc)[:1200]})
    # Numerical convergence of a sizing task does not verify a fixed engine.
    return {"schema_version": 1, "status": "candidate", "label": "候选硬件与模型基准",
            "payload": payload, "sha256": fingerprint(payload),
            "nominal_verification": "unavailable", "steady_runnable": False,
            "transient_scope": "existing_bounded_research", "blockers": blockers,
            "note": "保存候选方案及来源；固定硬件名义工况复核尚未开放，不能标记为已确认运行基准。"}
