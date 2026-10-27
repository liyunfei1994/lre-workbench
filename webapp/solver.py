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
import sys
import warnings

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from core import EngineInputs
from engine_presets import ENGINE_PRESETS, ENGINE_REFERENCES, preset_input_values
from propellants import get_propellant
from thermo_data import METHANE_DATASETS, methane_dataset_scope, saved_input_identity

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


def input_values(values, *, saved_snapshot=False):
    if not isinstance(values, dict):
        raise ValueError("方案参数必须为 JSON 对象。")
    if saved_snapshot:
        values = saved_input_identity(values)
    defaults = asdict(EngineInputs())
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError("无法识别的参数：" + ", ".join(sorted(unknown)))
    merged = defaults | values
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
    for key, default in defaults.items():
        value = merged[key]
        if isinstance(default, str):
            if not isinstance(value, str) or len(value) > 16000:
                raise ValueError(f"{key} 必须是有效文本。")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} 必须是有限数值。")
            if isinstance(default, int):
                if int(value) != value:
                    raise ValueError(f"{key} 必须是整数。")
                merged[key] = int(value)
    if merged["cycle_type"] not in ("ffsc", "gg"):
        raise ValueError("循环类型只能是 ffsc 或 gg。")
    get_propellant(merged["propellant_pair"])
    if merged["gg_layout"] not in ("single_shaft", "dual_shaft"):
        raise ValueError("GG 轴系类型无效。")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        inputs = EngineInputs(**merged)
    return asdict(inputs), list(dict.fromkeys(str(w.message) for w in caught))


def presets():
    return [{"key": key, "label": item["label"], "source": item["source"],
             "routine_design": bool(preset_input_values(key)["cooling_bartz_mode"]),
             "inputs": input_values(preset_input_values(key))[0]}
            for key, item in ENGINE_PRESETS.items()]


def reference_engines():
    """Public reference catalog, separate from loadable simulation inputs."""
    return [{"key": key, **deepcopy(item)} for key, item in ENGINE_REFERENCES.items()]


def science_environment(propellant_pair="lox_methane", methane_thermo_dataset="gri30"):
    import cantera
    import CoolProp
    import scipy
    import skfem
    import numpy
    from thermo import _get_gas
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


def calculate(values, progress=None, *, saved_snapshot=False):
    values, input_warnings = input_values(values, saved_snapshot=saved_snapshot)
    with methane_dataset_scope(values["methane_thermo_dataset"]):
        return _calculate(values, progress, input_warnings)


def _calculate(values, progress, input_warnings):
    from gg_cycle import make_cycle_model
    from engineering_assessment import assess_engineering
    from version import APP_VERSION
    environment = science_environment(values["propellant_pair"], values["methane_thermo_dataset"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = make_cycle_model(EngineInputs(**values)).evaluate(
            progress_callback=progress, include_cooling_marching=False)
    missing = []
    output = plain({
        "application_version": APP_VERSION, "submitted_inputs": values,
        "propellant_model": asdict(get_propellant(values["propellant_pair"])) | {
            "mechanism": environment["physics"]["mechanism"] if "physics" in environment else "unrecorded"},
        "calculation_scope": system_calculation_scope(),
        "calculation": asdict(result), "assessment": assess_engineering(result, scope="system"),
        "environment": environment,
        "warnings": list(dict.fromkeys(input_warnings + result.warnings +
                                      [str(w.message) for w in caught])),
    }, missing)
    output["nonfinite_fields"] = missing
    from webapp.nozzle_geometry import nozzle_geometry_view
    output["nozzle_geometry"] = nozzle_geometry_view(output)
    if missing:
        output["warnings"].append("部分结果不是有限数值，已标为缺失；请检查计算和工程警告。")
    json.dumps(output, allow_nan=False)
    return output
