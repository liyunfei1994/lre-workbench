"""Serialization boundary around the existing solver, without Qt or formula changes."""
from dataclasses import asdict
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
from engine_presets import ENGINE_PRESETS, preset_input_values

SCIENCE_PINS = {
    "numpy": "2.3.5", "cantera": "3.2.0", "CoolProp": "7.2.0",
    "scipy": "1.16.3", "scikit-fem": "11.0.0",
}


def input_values(values):
    if not isinstance(values, dict):
        raise ValueError("方案参数必须为 JSON 对象。")
    defaults = asdict(EngineInputs())
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError("无法识别的参数：" + ", ".join(sorted(unknown)))
    merged = defaults | values
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
    if merged["gg_layout"] not in ("single_shaft", "dual_shaft"):
        raise ValueError("GG 轴系类型无效。")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        inputs = EngineInputs(**merged)
    return asdict(inputs), list(dict.fromkeys(str(w.message) for w in caught))


def presets():
    return [{"key": key, "label": item["label"], "source": item["source"],
             "inputs": input_values(preset_input_values(key))[0]}
            for key, item in ENGINE_PRESETS.items()]


def science_environment():
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
    gas = _get_gas()
    density = PropsSI("D", "T", 90, "P", 1e6, "Oxygen")
    if gas.n_species <= 0 or not math.isfinite(density) or density <= 0:
        raise RuntimeError("科学库功能检查失败。")
    result = {
        "python": platform.python_version(), "platform": platform.system(),
        "dependencies": versions,
        "mechanism_sha256": hashlib.sha256(
            (Path(cantera.__file__).parent / "data/gri30.yaml").read_bytes()).hexdigest(),
        "physics": {"chemistry": "Cantera", "properties": "CoolProp"},
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


def calculate(values, progress=None):
    from gg_cycle import make_cycle_model
    from engineering_assessment import assess_engineering
    from version import APP_VERSION
    values, input_warnings = input_values(values)
    environment = science_environment()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = make_cycle_model(EngineInputs(**values)).evaluate(progress_callback=progress)
    missing = []
    output = plain({
        "application_version": APP_VERSION, "submitted_inputs": values,
        "calculation": asdict(result), "assessment": assess_engineering(result),
        "environment": environment,
        "warnings": list(dict.fromkeys(input_warnings + result.warnings +
                                      [str(w.message) for w in caught])),
    }, missing)
    output["nonfinite_fields"] = missing
    if missing:
        output["warnings"].append("部分结果不是有限数值，已标为缺失；请检查计算和工程警告。")
    json.dumps(output, allow_nan=False)
    return output
