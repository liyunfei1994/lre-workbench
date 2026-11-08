"""Serialization boundary around the existing solver, without Qt or formula changes."""
from dataclasses import asdict
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
import numbers
from pathlib import Path
import platform
import re
import sys
import warnings

ROOT = Path(__file__).resolve().parent.parent
from lre_workbench.domain.models import EngineInputs, ValidationError
from lre_workbench.systems.pressure_budget import BRANCH_BUDGET_FIELDS
from lre_workbench.application.presets import ENGINE_PRESETS, ENGINE_REFERENCES, preset_input_values
from lre_workbench.properties.propellants import get_propellant
from lre_workbench.properties.datasets import METHANE_DATASETS, methane_dataset_scope, saved_input_identity
from lre_workbench.domain.analysis_contract import (concept_design_task, concept_inputs, require_concept_mode,
                               FFSC_INPUT_FIELDS, RETIRED_PLATFORM_FIELDS)
from webapp.design_scope import SCOPE_MESSAGE, supports_design, require_supported_design

SCIENCE_PINS = {
    "numpy": "2.3.5", "cantera": "3.2.0", "CoolProp": "7.2.0",
    "scipy": "1.16.3", "scikit-fem": "11.0.0",
}


def system_calculation_scope():
    """Version the web execution policy separately from immutable user inputs."""
    return {
        "id": "engine_system", "version": 4, "label": "系统仿真 · 稳态设计点",
        "disabled_postprocessing": ["cfd", "fea"],
        "cooling_profile_source": "calculation.components.cooling_channel.profile",
        "independent_cooling_postprocessing": False,
        "limitations": [
            "本次运行执行发动机循环匹配与稳态总体性能计算。",
            "物理冷却模式中的系统级准一维沿程模型参与循环热量、供给压力和出口状态闭合。",
            "完整沿程最大热面壁温作为系统热设计限值独立校核；数值收敛不代表壁温通过，也不代表材料强度或结构疲劳已验证。",
            "沿程图直接读取本次循环保存的 cooling_channel.profile，不运行独立冷却后处理；给定热量/压降的预算模式不生成沿程解。",
            "详细部件估算沿用底层模型并保留在原始结果中，不作为本阶段的部件设计结论；未执行有限元或 CFD 求解。",
            "方案参数完整保留；历史沿程后处理开关不控制循环内统一冷却模型。历史记录不按当前参数补算曲线。",
            "预燃室温度约束分别记录状态与依据；未设定时尚未评估，暂定限值内仍待核实，已有依据仅代表用户登记的方案条件。",
        ],
    }


def ffsc_input_values(values):
    """Project the shared solver input structure onto the platform contract."""
    return {key: value for key, value in values.items() if key not in RETIRED_PLATFORM_FIELDS}


def platform_defaults():
    return ffsc_input_values(asdict(EngineInputs()))


def input_values(values, *, saved_snapshot=False):
    if not isinstance(values, dict):
        raise ValueError("方案参数必须为 JSON 对象。")
    if saved_snapshot:
        values = saved_input_identity(values)
    require_supported_design(values)
    # Earlier FFSC files carried unused fields from the desktop's other cycle.
    # Discard those inactive fields without changing any FFSC physical input.
    values = ffsc_input_values(values)
    defaults = platform_defaults()
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError("无法识别的参数：" + ", ".join(sorted(unknown)))
    merged = defaults | values
    if saved_snapshot:
        # Older saved records used numeric limits without a status field.
        # Preserve that contract; new custom inputs retain the unset default.
        for side in ("ox", "fuel"):
            key = f"{side}_preburner_temp_limit_status"
            if key not in values:
                merged[key] = "provisional"
    # Omitted fields identify the earlier sizing/constraint contract, not a
    # request to reinterpret a stored 100 m/s value at a different section.
    for key, legacy in {"cooling_sizing_reference": "legacy_inlet_density_throat_width",
                        "cooling_wall_temp_limit_status": "provisional",
                        "cooling_channel_auto_size": 1.0,
                        "cooling_channel_height_m": 0.005}.items():
        if key not in values:
            merged[key] = legacy
    if "nozzle_contour_method" not in values:
        # An older parameter document must not acquire new wall angles on load.
        merged["nozzle_contour_method"] = "legacy_log_v1"
    if "wall_thermal_dataset_id" not in values:
        # Resolve a historical material explicitly in EngineInputs; do not let
        # the current default silently replace a different saved copper alloy.
        merged["wall_thermal_dataset_id"] = ""
    optional_budgets = {key for branch in BRANCH_BUDGET_FIELDS.values() for key in branch.values()}
    optional_cooling = {"cooling_channel_roughness_m", "cooling_k_minor_inlet", "cooling_k_minor_outlet"}
    for key, default in defaults.items():
        value = merged[key]
        if key in optional_budgets | optional_cooling and value is None:
            continue  # Explicit null retains the corresponding legacy model input.
        if isinstance(default, str):
            text_limit = 256000 if key == "cooling_channel_table_json" else 16000
            if not isinstance(value, str) or len(value) > text_limit:
                raise ValueError(f"{key} 必须是有效文本。")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} 必须是有限数值。")
            if isinstance(default, int):
                if int(value) != value:
                    raise ValueError(f"{key} 必须是整数。")
                merged[key] = int(value)
    get_propellant(merged["propellant_pair"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        inputs = EngineInputs(**merged)
        inputs.validate_wall_material_binding()
    return ffsc_input_values(asdict(inputs)), list(dict.fromkeys(str(w.message) for w in caught))


_INPUT_FIELDS = FFSC_INPUT_FIELDS

# Older validators report some failures by component name instead of input key.
_READINESS_FIELD_HINTS = (
    (SCOPE_MESSAGE, ("cycle_type", "propellant_pair")),
    ("循环类型只能", ("cycle_type",)),
    ("不支持的推进剂组合", ("propellant_pair",)),
    ("Unknown wall manufacturing state", ("wall_manufacturing_state",)),
    ("Unknown solid-wall thermal dataset", ("wall_thermal_dataset_id",)),
    ("No source-backed wall dataset", ("chamber_inner_wall_material", "wall_thermal_dataset_id")),
    ("内衬材料与默认热物性数据集不一致", ("chamber_inner_wall_material", "wall_thermal_dataset_id")),
    ("壁面热物性设置无效", ("wall_material_zones_json",)),
    ("未知的喷管型面方法", ("nozzle_contour_method",)),
    ("喷管角度必须", ("nozzle_initial_angle_deg", "nozzle_exit_angle_deg")),
    ("喷管长度、面积比与角度不相容", ("expansion_ratio", "nozzle_bell_fraction", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg")),
    ("喷管切线交点", ("nozzle_bell_fraction", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg")),
    ("圆筒入口过渡圆角退化", ("chamber_contraction_ratio", "chamber_conv_half_angle_deg")),
    ("涡轮接口状态约定", ("turbine_boundary_basis",)),
    ("涡轮均径初选模式", ("turbine_meanline_mode",)),
    ("富氧侧 ox_turbine.rotor_blade", ("turb_ox_rotor_material",)),
    ("富氧侧 ox_turbine.stator_vane", ("turb_ox_stator_material",)),
    ("富氧侧 ox_turbine.disk", ("turb_ox_disk_material",)),
    ("富氧侧 ox_turbine.housing", ("turb_ox_housing_material",)),
    ("富氧侧 lox_pump.impeller", ("lox_pump_impeller_material",)),
)


def design_readiness(values):
    """Validate a complete editor draft without running or persisting a design."""
    if not isinstance(values, dict):
        return {"valid": False, "issues": [{"fields": [], "message": "方案参数必须为 JSON 对象。"}], "warnings": []}
    values = ffsc_input_values(values)
    issues = [{"fields": [key], "message": "缺少参数值。"}
              for key in sorted(_INPUT_FIELDS - values.keys())]
    unknown = values.keys() - _INPUT_FIELDS
    if unknown:
        issues.append({"fields": [], "message": ("无法识别的参数：" + ", ".join(sorted(unknown)))[:1600]})
    if issues:
        return {"valid": False, "issues": issues, "warnings": []}

    try:
        require_supported_design(values)
        _, messages = input_values(values)
    except (ValidationError, ValueError, OverflowError) as exc:
        message = str(exc)[:1600]
        hint = next((keys for phrase, keys in _READINESS_FIELD_HINTS if phrase in message), ())
        mentioned = (token for token in re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", message) if token in _INPUT_FIELDS)
        keys = list(dict.fromkeys((*hint, *mentioned)))
        if isinstance(exc, OverflowError):
            message = "参数数值超出可处理范围。"
        return {"valid": False, "issues": [{"fields": keys, "message": message}], "warnings": []}
    return {"valid": True, "issues": [], "warnings": messages}


def presets():
    return [{"key": key, "label": item["label"], "source": item["source"],
             "display_description": item.get("display_description", ""),
             "routine_design": bool(preset_input_values(key)["cooling_bartz_mode"]),
             "inputs": input_values(preset_input_values(key))[0]}
            for key, item in ENGINE_PRESETS.items() if supports_design(preset_input_values(key))]


def reference_engines():
    """Public reference catalog, separate from loadable simulation inputs."""
    available = {key for key in ENGINE_PRESETS if supports_design(preset_input_values(key))}
    return [{"key": key, **deepcopy(item)} for key, item in ENGINE_REFERENCES.items()
            if item.get("preset_key") in available]


def science_environment(propellant_pair="lox_methane", methane_thermo_dataset="gri30"):
    import cantera
    import CoolProp
    import scipy
    import skfem
    import numpy
    from lre_workbench.properties.thermochemistry import _get_gas
    from CoolProp.CoolProp import PropsSI
    versions = {name: importlib.metadata.version(name) for name in SCIENCE_PINS}
    drift = [name for name, version in versions.items() if version != SCIENCE_PINS[name]]
    if drift:
        raise RuntimeError("科学环境版本不匹配：" + ", ".join(drift))
    model = get_propellant(propellant_pair)
    gas = _get_gas(model.fuel_species, methane_dataset=methane_thermo_dataset)
    filename = (METHANE_DATASETS[methane_thermo_dataset]["file"]
                if propellant_pair == "lox_methane" else model.mechanism)
    density = PropsSI("D", "T", 90, "P", 1e6, "Oxygen")
    if gas.n_species <= 0 or not math.isfinite(density) or density <= 0:
        raise RuntimeError("科学库功能检查失败。")
    result = {
        "python": platform.python_version(), "platform": platform.system(),
        "dependencies": versions,
        "mechanism_sha256": hashlib.sha256(
            (Path(cantera.__file__).parent / "data" / filename).read_bytes()).hexdigest(),
        "physics": {"chemistry": "Cantera", "properties": "CoolProp",
                    "propellant_pair": model.id, "fuel_species": model.fuel_species,
                    "mechanism": filename, "phase": gas.name},
        "thermodynamic_scope": {"gas_eos": gas.thermo_model,
            "mechanism_common_min_temperature_K": gas.min_temp,
            "mechanism_common_max_temperature_K": gas.max_temp,
            "pure_fluid_backend": "HEOS", "high_pressure_mixture_validation": "not_validated",
            "production_nozzle_assumption": "frozen_chamber",
            "policy_version": 1},
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def plain(value, missing, path="result"):
    if isinstance(value, dict):
        return {str(k): plain(v, missing, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v, missing, f"{path}[{i}]") for i, v in enumerate(value)]
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        if not math.isfinite(value):
            missing.append(path)
            return None
        return float(value)
    if hasattr(value, "tolist"):
        return plain(value.tolist(), missing, path)
    raise TypeError(f"不能序列化的结果类型：{path}")


def calculate(values, progress=None, *, saved_snapshot=False, mode="concept_design"):
    require_concept_mode(mode)
    require_supported_design(values)
    values, input_warnings = input_values(values, saved_snapshot=saved_snapshot)
    with methane_dataset_scope(values["methane_thermo_dataset"]):
        return _calculate(values, progress, input_warnings)


def calculate_task(task, progress=None):
    """Dispatch a typed task before loading or invoking any physical model."""
    if not isinstance(task, dict):
        raise ValueError("计算任务必须为 JSON 对象。")
    if task.get("mode") == "fixed_hardware_steady":
        from webapp.fixed_steady import assert_runnable
        assert_runnable(task)
        raise ValueError("固定硬件稳态求解器尚未接入。")
    values = concept_inputs(task)
    return calculate(values, progress, saved_snapshot=True, mode=task["mode"])


def calculate_design_job(job, progress=None):
    """Read new typed records and legacy concept records without rewriting them."""
    if job.get("kind", "design") != "design":
        raise ValueError("非概念设计任务不能调用概念设计求解入口。")
    values = json.loads(job["inputs"])
    if job.get("analysis_task"):
        task = json.loads(job["analysis_task"])
        if concept_inputs(task) != values:
            raise ValueError("任务分组数据与保存的概念设计输入不一致。")
        return calculate_task(task, progress)
    return calculate(values, progress, saved_snapshot=True)


def initialization_failure_result(exc, job):
    """Persist failed initialization evidence without fabricating a solution.

    The job snapshot describes the submitted task; attempted inputs in the
    diagnostic describe the actual failed call. Neither is read from the draft.
    Other job kinds and exceptions retain their existing failure behavior.
    """
    from lre_workbench.solvers.attempt import SolverInitializationError
    if job.get("kind", "design") != "design" or not isinstance(exc, SolverInitializationError):
        return None
    missing = []
    output = plain({
        "schema_version": 1,
        "result_kind": "initialization_failure",
        "submitted_inputs": json.loads(job["inputs"]),
        "analysis_task": json.loads(job["analysis_task"]) if job.get("analysis_task") else None,
        "initialization_diagnostics": exc.initialization_diagnostics,
    }, missing)
    output["nonfinite_fields"] = missing
    return output


def _calculate(values, progress, input_warnings):
    from lre_workbench.systems.ffsc import FFSCCycleModel
    from lre_workbench.analysis.assessment import assess_engineering
    from lre_workbench.version import APP_VERSION
    environment = science_environment(values["propellant_pair"], values["methane_thermo_dataset"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = FFSCCycleModel(EngineInputs(**values)).evaluate(
            progress_callback=progress, include_cooling_marching=False)
    calculation = asdict(result)
    calculation['inputs'] = ffsc_input_values(calculation['inputs'])
    missing = []
    output = plain({
        "application_version": APP_VERSION, "submitted_inputs": values,
        "analysis_task": concept_design_task(values),
        "propellant_model": asdict(get_propellant(values["propellant_pair"])) | {
            "supported_cycles": ["ffsc"],
            "mechanism": environment["physics"]["mechanism"] if "physics" in environment else "unrecorded"},
        "calculation_scope": system_calculation_scope(),
        "calculation": calculation, "assessment": assess_engineering(result, scope="system"),
        "environment": environment,
        "warnings": list(dict.fromkeys(input_warnings + result.warnings +
                                      [str(w.message) for w in caught])),
    }, missing)
    output["nonfinite_fields"] = missing
    from lre_workbench.integrations.gas_handoff import capture_gas_handoff
    output["gas_handoff"] = capture_gas_handoff(output)
    from webapp.nozzle_geometry import nozzle_geometry_view
    output["nozzle_geometry"] = nozzle_geometry_view(output)
    from webapp.pump_geometry import pump_geometry_view
    output["pump_geometry"] = pump_geometry_view(output)
    from webapp.injector_design import injector_design_view
    output["injector_design"] = injector_design_view(output)
    from webapp.turbine_geometry import turbine_geometry_view
    output["turbine_geometry"] = turbine_geometry_view(output)
    import lre_workbench.properties.thermochemistry as thermo
    from webapp.turbine_design import capture_turbine_gas_properties, turbine_design_view
    output["turbine_gas_properties"] = capture_turbine_gas_properties(output, thermo)
    output["turbine_design"] = turbine_design_view(output)
    from webapp.result_consistency import result_consistency_view
    output["result_consistency"] = result_consistency_view(output)
    from webapp.steady_design import steady_design_view
    output["steady_design"] = steady_design_view(output)
    if missing:
        output["warnings"].append("部分结果不是有限数值，已标为缺失；请检查计算和工程警告。")
    json.dumps(output, allow_nan=False)
    return output
