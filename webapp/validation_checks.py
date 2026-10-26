"""Evidence-based checks; an unavailable check is never a successful validation.

This module does not change solver inputs, tune parameters, or install databases.
All numerical comparisons use SI internally. Request validators are idempotent.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import threading

MAX_POINTS = 20
FLUIDS = ("Oxygen", "Methane", "n-Dodecane")
_PROPERTY_LOCK = threading.RLock()  # REFPROP's shared native state is not reentrant.
UNITS = {
    "N": {"N": 1.0, "kN": 1e3}, "s": {"s": 1.0},
    "kg/s": {"kg/s": 1.0}, "K": {"K": 1.0}, "1": {"1": 1.0},
    "Pa": {"Pa": 1.0, "kPa": 1e3, "MPa": 1e6, "bar": 1e5},
    "W": {"W": 1.0, "kW": 1e3, "MW": 1e6},
}
# metric: (recorded summary key, conversion to SI, SI unit, readable label)
METRICS = {
    "thrust_N": ("thrust_N", 1, "N", "环境压力下推力"),
    "thrust_vac_N": ("thrust_vac_N", 1, "N", "真空推力"),
    "Isp_s": ("Isp_s", 1, "s", "环境压力下比冲"),
    "Isp_vac_s": ("Isp_vac_s", 1, "s", "真空比冲"),
    "mdot_total_kg_s": ("mdot_total_kg_s", 1, "kg/s", "总质量流量"),
    "mdot_fuel_kg_s": ("mdot_fuel_kg_s", 1, "kg/s", "燃料流量"),
    "mdot_ox_kg_s": ("mdot_ox_kg_s", 1, "kg/s", "氧化剂流量"),
    "gg_mdot_kg_s": ("gg_mdot_kg_s", 1, "kg/s", "燃气发生器流量"),
    "chamber_temperature_K": ("chamber_temperature_K", 1, "K", "主燃烧室温度"),
    "gg_T_combustion_K": ("gg_T_combustion_K", 1, "K", "燃气发生器温度"),
    "gg_turbine_exhaust_T_K": ("gg_turbine_exhaust_T_K", 1, "K", "GG 涡轮排气温度"),
    "lox_pump_out_pressure_Pa": ("lox_pump_out_pressure_Pa", 1, "Pa", "氧泵出口压力"),
    "fuel_pump_out_pressure_Pa": ("fuel_pump_out_pressure_Pa", 1, "Pa", "燃料泵出口压力"),
    "gg_pressure_Pa": ("gg_pressure_Pa", 1, "Pa", "GG 压力"),
    "ox_pump_power_W": ("ox_pump_power_MW", 1e6, "W", "氧泵功率"),
    "fuel_pump_power_W": ("fuel_pump_power_MW", 1e6, "W", "燃料泵功率"),
    "turbine_power_W": ("turbine_power_MW", 1e6, "W", "涡轮总功率"),
}
CONDITIONS = {
    "pc": "Pa", "of_ratio": "1", "fuel_inlet_temp": "K", "ox_inlet_temp": "K",
    "ambient_pressure": "Pa", "fuel_tank_pressure": "Pa", "ox_tank_pressure": "Pa",
}


def _number(value, label, *, minimum=0.0, maximum=1e12, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须为数值，不能是布尔值或字符串。")
    # Bounds first: math.isfinite(10**1000) itself overflows in Python.
    if value < minimum or value > maximum or not math.isfinite(value) or (positive and value == 0):
        raise ValueError(f"{label} 超出允许的有限数值范围。")
    return float(value)


def _finite(value, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Avoid overflowing subsequent sums/unit conversions on malformed records.
    return float(value) if abs(value) <= 1e100 and math.isfinite(value) and (not nonnegative or value >= 0) else None


def _object(value, label, keys):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError(f"{label} 必须为 JSON 对象。")
    if set(value) - set(keys):
        raise ValueError(f"{label} 含无法识别的字段。")
    return value


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label} 必须为 {' / '.join(choices)}。")
    return value


def _quantity(value, unit, label, *, condition=False):
    row = _object(value, label, {"value", "unit", "tolerance", "uncertainty"})
    factor = UNITS[unit].get(row.get("unit")) if isinstance(row.get("unit"), str) else None
    if factor is None:
        raise ValueError(f"{label} 的单位不兼容；允许 {', '.join(UNITS[unit])}。")
    result = {"value": _number(row.get("value"), label) * factor, "unit": unit}
    bounds = {"Pa": 1e10, "K": 10000, "N": 1e10, "W": 1e13, "s": 1e5, "kg/s": 1e7, "1": 1000}
    if result["value"] > bounds[unit] or (unit in ("K", "1") and result["value"] == 0):
        raise ValueError(f"{label} 超出物理量输入边界。")
    for key in ("tolerance", "uncertainty"):
        if key in row:
            result[key] = _number(row[key], f"{label}.{key}") * factor
            if result[key] > bounds[unit]:
                raise ValueError(f"{label}.{key} 超出输入边界。")
    if condition and "tolerance" not in result:
        raise ValueError(f"{label} 需要明确工况容差 tolerance。")
    if not condition and not ({"tolerance", "uncertainty"} & result.keys()):
        raise ValueError(f"{label} 需要 tolerance 或 uncertainty。")
    return result


def validate_property_request(request):
    request = _object(request, "物性检查请求", {"points", "compare_refprop"})
    points = request.get("points")
    if not isinstance(points, list) or not 1 <= len(points) <= MAX_POINTS:
        raise ValueError(f"物性检查需要 1–{MAX_POINTS} 个状态点。")
    compare = request.get("compare_refprop", False)
    if not isinstance(compare, bool):
        raise ValueError("compare_refprop 必须为布尔值。")
    result = []
    for point in points:
        _object(point, "物性状态点", {"fluid", "temperature_K", "pressure_Pa"})
        result.append({
            "fluid": _choice(point.get("fluid"), FLUIDS, "fluid"),
            "temperature_K": _number(point.get("temperature_K"), "temperature_K", maximum=10000, positive=True),
            "pressure_Pa": _number(point.get("pressure_Pa"), "pressure_Pa", maximum=1e10, positive=True),
        })
    return {"points": result, "compare_refprop": compare}


def validate_experiment_request(request):
    if request is None or request == {}:
        return {}
    request = _object(request, "试验检查请求", {"role", "conditions", "measurements"})
    role = _choice(request.get("role"), ("holdout", "calibration"), "role")
    conditions = _object(request.get("conditions", {}), "试验工况", set(CONDITIONS) | {"propellant_pair", "cycle_type", "gg_layout"})
    normalized = {}
    for key, value in conditions.items():
        if key == "propellant_pair":
            normalized[key] = _choice(value, ("lox_methane", "lox_kerosene"), key)
        elif key == "cycle_type":
            normalized[key] = _choice(value, ("ffsc", "gg"), key)
        elif key == "gg_layout":
            normalized[key] = _choice(value, ("single_shaft", "dual_shaft"), key)
        else:
            normalized[key] = _quantity(value, CONDITIONS[key], key, condition=True)
    measurements = request.get("measurements", [])
    if not isinstance(measurements, list) or len(measurements) > MAX_POINTS:
        raise ValueError(f"measurements 必须为最多 {MAX_POINTS} 项的数组。")
    rows, seen = [], set()
    for measurement in measurements:
        _object(measurement, "实测指标", {"metric", "value", "unit", "tolerance", "uncertainty"})
        metric = _choice(measurement.get("metric"), tuple(METRICS), "metric")
        if metric in seen:
            raise ValueError("同一实测指标不能重复；请分次提交不同试验工况。")
        seen.add(metric)
        rows.append({"metric": metric, **_quantity({k: v for k, v in measurement.items() if k != "metric"}, METRICS[metric][2], metric)})
    return {"role": role, "conditions": normalized, "measurements": rows}


def _parts(source):
    if not isinstance(source, dict):
        raise ValueError("计算结果必须为 JSON 对象。")
    calculation = source.get("calculation")
    calculation = calculation if isinstance(calculation, dict) else {}
    return tuple(calculation.get(k) if isinstance(calculation.get(k), dict) else {} for k in ("inputs", "summary", "components", "states"))


def _source_checks(source, summary):
    from webapp.solver import EngineInputs  # initializes the shared src import path
    from convergence import numerical_convergence
    numerical = numerical_convergence(source)
    residual = numerical["residual"]
    checks = [{"id": "numerical_convergence", "label": "源计算数值收敛", "status":
               "unavailable" if numerical["status"] == "unknown" else numerical["status"],
               "value": residual, "tolerance": numerical["tolerance"],
               "reason": "从冻结结果的四层收敛证据重新判定；旧记录缺证据时不通过，不等同于物理模型验证。"}]
    assessment = source.get("assessment") if isinstance(source.get("assessment"), dict) else {}
    reported_numerical = assessment.get("numerical")
    if isinstance(reported_numerical, dict) and reported_numerical.get("status") == "fail":
        checks[0].update(status="fail", reason="源记录的数值收敛校核已失败；不能被汇总残差或实测吻合覆盖。")
    state = assessment.get("status")
    checks.append({"id": "source_assessment", "label": "源结果工程约束", "status":
                   "pass" if state == "pass" else "fail" if state == "fail" else "unavailable",
                   "reason": f"源工程校核状态：{state or '缺失'}；失效或未确认结果不能获得完整验证通过。"})
    if source.get("nonfinite_fields"):
        checks.append({"id": "source_finite", "label": "源结果有限数值", "status": "unavailable",
                       "reason": "源记录存在已转为空值的非有限结果；相关检查需补齐数据。"})
    return checks


def _status(checks):
    statuses = {c["status"] for c in checks}
    if "fail" in statuses:
        return "fail"
    if statuses <= {"pass"} and statuses:
        return "pass"
    return "partial" if statuses & {"pass", "partial"} else "unavailable"


def _closure(key, label, left, right, unit, *, atol=1e-6, rtol=1e-6):
    check = {"id": key, "label": label, "unit": unit, "left_si": left, "right_si": right,
             "absolute_tolerance_si": atol, "relative_tolerance": rtol}
    if left is None or right is None:
        return check | {"status": "unavailable", "reason": "缺少有限、非负的独立计算输出，不能计算闭合误差。"}
    delta = left - right
    scale = max(abs(left), abs(right))
    tolerance = atol + rtol * scale
    return check | {"status": "pass" if abs(delta) <= tolerance else "fail", "difference_si": delta,
                    "relative_difference": delta / scale if scale else 0.0, "tolerance_si": tolerance}


def conservation_checks(source_result):
    inputs, summary, components, states = _parts(source_result)
    checks = _source_checks(source_result, summary)
    def val(key, factor=1):
        value = _finite(summary.get(key))
        return value * factor if value is not None else None
    def total(*values):
        return sum(values) if all(v is not None for v in values) else None
    def flow(key, label, left, right):
        checks.append(_closure(key, label, left, right, "kg/s"))
    total_flow, fuel, ox = (val(k) for k in ("mdot_total_kg_s", "mdot_fuel_kg_s", "mdot_ox_kg_s"))
    flow("total_mass", "总流量 = 燃料 + 氧化剂", total_flow, total(fuel, ox))
    cycle = inputs.get("cycle_type")
    if cycle == "gg":
        gg_fuel, gg_ox, gg, main = (val(k) for k in ("gg_mdot_fuel_kg_s", "gg_mdot_ox_kg_s", "gg_mdot_kg_s", "mdot_chamber_kg_s"))
        flow("gg_mass", "GG 流量 = GG 燃料 + GG 氧化剂", gg, total(gg_fuel, gg_ox))
        flow("chamber_gg_split", "总流量 = 主室通流 + GG 通流", total_flow, total(main, gg))
        flow("fuel_split", "燃料总流量 = 主路冷却流量 + GG 燃料", fuel, total(val("mdot_coolant_kg_s"), gg_fuel))
        eta = val("gg_mech_efficiency")
        if eta is not None and not 0 < eta <= 1:
            checks.append({"id": "mechanical_efficiency", "label": "GG 机械效率范围", "status": "fail", "value": eta})
            eta = None
        turbine = val("gg_turbine_power_MW", 1e6)
    elif cycle == "ffsc":
        flow("fuel_split", "燃料总流量 = 两预燃室燃料流量之和", fuel,
             total(val("mdot_fuel_to_ox_preburner_kg_s"), val("mdot_fuel_to_fuel_preburner_kg_s")))
        flow("ox_split", "氧化剂总流量 = 两预燃室氧化剂流量之和", ox,
             total(val("mdot_ox_to_ox_preburner_kg_s"), val("mdot_ox_to_fuel_preburner_kg_s")))
        flow("exhaust_mass", "总流量 = 两路涡轮热气流量之和", total_flow,
             total(val("mdot_LO_ox_exhaust_kg_s"), val("mdot_LF_fuel_exhaust_kg_s")))
        eta, turbine = 1.0, val("turbine_power_MW", 1e6)
    else:
        checks.append({"id": "cycle_identity", "label": "源循环模型", "status": "unavailable", "reason": "源结果未记录受支持的循环类型。"})
        eta, turbine = None, None
    pumps = total(val("ox_pump_power_MW", 1e6), val("fuel_pump_power_MW", 1e6))
    checks.append(_closure("shaft_power", "主轴涡轮可用功率 = 主泵功率", turbine * eta if turbine is not None and eta is not None else None,
                           pumps, "W", atol=1, rtol=1e-3))
    if cycle == "ffsc" or (cycle == "gg" and inputs.get("gg_layout") == "dual_shaft"):
        for side, label in (("ox", "氧侧"), ("fuel", "燃料侧")):
            power = val(f"{side}_turbine_power_MW", 1e6)
            checks.append(_closure(f"{side}_shaft_power", f"{label}轴可用涡轮功率 = 泵功率",
                                   power * eta if power is not None and eta is not None else None,
                                   val(f"{side}_pump_power_MW", 1e6), "W", atol=1, rtol=1e-3))
    for key, label in (("ox_pump", "氧泵"), ("fuel_pump", "燃料泵")):
        component = components.get(key, {})
        outputs = component.get("outputs", {}) if isinstance(component, dict) else {}
        component_power = _finite(outputs.get("power_W")) if isinstance(outputs, dict) else None
        checks.append(_closure(f"{key}_reported_power", f"{label}部件功率与汇总一致性",
                               component_power, val(f"{key}_power_MW", 1e6), "W", atol=1, rtol=1e-6))
    checks.append({"id": "element_balance", "label": "完整元素守恒", "status": "unavailable",
                   "reason": "当前序列化记录没有完整的全部入口/出口物种质量分数、组成基准及凝聚相流量，不能独立复算元素收支。"})
    energy_balance = None
    if cycle == "ffsc":
        from steady_closure import audit_energy
        # Recompute from saved states and outputs; do not trust the saved ledger,
        # pass flag, or summary ratios. Legacy records lack the needed evidence.
        energy_balance = audit_energy(states, components)
        checks.append({"id": "whole_system_energy", "label": "集总 FFSC 边界能量守恒",
                       "status": energy_balance["status"], "unit": "W",
                       "difference_si": energy_balance["residual_W"],
                       "tolerance_si": energy_balance["tolerance_W"],
                       "reason": energy_balance["boundary"] + " " + energy_balance["reference"]})
        for row in energy_balance["checks"]:
            checks.append({"id": f"energy_{row['id']}", "label": row["label"], "status": row["status"],
                           "unit": "W", "left_si": row["left_W"], "right_si": row["right_W"],
                           "difference_si": row["residual_W"], "tolerance_si": row["tolerance_W"],
                           "absolute_tolerance_si": energy_balance["absolute_tolerance_W"],
                           "relative_tolerance": energy_balance["relative_tolerance"], "reason": row["source"]})
    else:
        checks.append({"id": "whole_system_energy", "label": "完整系统能量守恒", "status": "unavailable",
                       "reason": "缺少所有边界的统一参考焓、排气动能、壁面热损失和外部增压泵功，不能把轴功率平衡当成系统能量守恒。"})
    return {"kind": "conservation", "status": _status(checks), "checks": checks, "energy_balance": energy_balance,
            "notes": ["流量闭合采用绝对容差 1e-6 kg/s + 相对容差 1e-6；轴功率采用 1 W + 0.1%，属于数值检查政策。",
                      "GG 功率含记录的机械效率；FFSC 按当前模型的轴功率定义。外部增压/补压泵未纳入主轴平衡。",
                      "FFSC 能量分项另按 10 W + 1e-5 × 最大绝对能量项重新核算；内部再生热与主轴功不重复计入边界。",
                      "这些是同一计算记录的内部一致性检查，不是独立试验验证。"]}


def _coolprop():
    import CoolProp
    import CoolProp.CoolProp as CP
    return CoolProp, CP


def _local_refprop_library(CP):
    """Look for a real local installation before invoking its native loader."""
    roots = [CP.get_config_string(CP.ALTERNATIVE_REFPROP_PATH), os.environ.get("RPPREFIX", ""),
             os.environ.get("COOLPROP_REFPROP_ROOT", "")]
    roots += [str(Path(os.environ.get(k, "C:/Program Files")) / "REFPROP") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)")]
    roots += os.environ.get("PATH", "").split(os.pathsep)
    explicit = CP.get_config_string(CP.ALTERNATIVE_REFPROP_LIBRARY_PATH)
    names = (("REFPRP64.DLL",) if sys.maxsize > 2**32 else ("REFPROP.DLL",)) if sys.platform == "win32" else ("librefprop.so", "librefprop.dylib")
    candidates = ([Path(explicit)] if explicit else []) + [Path(p) / n for p in roots if p for n in names]
    return next((str(p) for p in candidates if p.is_file()), None)


def _file_identity(path):
    """Fingerprint installed evidence; never copy licensed fluid/library data."""
    path = Path(path)
    try:
        return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError:
        return {"name": path.name, "sha256": None, "reason": "文件不可读，未生成指纹。"}


def _refprop_files(CP, library):
    roots = [os.environ.get("COOLPROP_REFPROP_ROOT", ""),
             CP.get_config_string(CP.ALTERNATIVE_REFPROP_PATH),
             os.environ.get("RPPREFIX", ""), str(Path(library).parent)]
    files = [_file_identity(library)]
    # Only the three explicitly supported pure fluids; do not enumerate a user's
    # installation or embed proprietary coefficient tables in result exports.
    for name in ("OXYGEN.FLD", "METHANE.FLD", "C12.FLD"):
        candidates = [Path(root) / folder / name for root in roots if root
                      for folder in ("FLUIDS", "fluids")]
        file = next((p for p in candidates if p.is_file()), None)
        if file:
            files.append(_file_identity(file))
    return files


@lru_cache(maxsize=1)
def _capabilities():
    result = {"coolprop": {"available": False, "version": None},
              "refprop": {"available": False, "version": None, "reason": "未发现本机已安装的 REFPROP 动态库；不会安装或下载授权软件。"},
              "fluids": list(FLUIDS)}
    try:
        result["ctrefprop"] = {"available": True, "version": importlib.metadata.version("ctREFPROP")}
    except importlib.metadata.PackageNotFoundError:
        result["ctrefprop"] = {"available": False, "version": None}
    result["production_backend"] = "CoolProp HEOS"
    result["refprop"]["interface"] = "CoolProp REFPROP backend"
    try:
        package, CP = _coolprop()
        result["coolprop"] = {"available": True, "version": package.__version__,
                              "git_revision": CP.get_global_param_string("gitrevision")}
    except (ImportError, OSError):
        result["coolprop"]["reason"] = "未安装 CoolProp。"
        result["refprop"]["reason"] = "CoolProp 接口不可用。"
        return result
    library = _local_refprop_library(CP)
    if not library:
        return result
    try:
        # Finding an installed DLL does not put it on the native loader's path.
        # Configure both library and fluid-data locations before the first load.
        # An explicit CoolProp root/configuration continues to take precedence.
        if not os.environ.get("COOLPROP_REFPROP_ROOT"):
            if not CP.get_config_string(CP.ALTERNATIVE_REFPROP_PATH):
                CP.set_config_string(CP.ALTERNATIVE_REFPROP_PATH,
                                     os.environ.get("RPPREFIX") or str(Path(library).resolve().parent))
            if not CP.get_config_string(CP.ALTERNATIVE_REFPROP_LIBRARY_PATH):
                CP.set_config_string(CP.ALTERNATIVE_REFPROP_LIBRARY_PATH, str(Path(library).resolve()))
        state = CP.AbstractState("REFPROP", "Oxygen")
        state.update(CP.PT_INPUTS, 1e5, 300)
        if not math.isfinite(state.rhomass()) or state.rhomass() <= 0:
            raise ValueError("REFPROP 功能探测未返回有效密度。")
        result["refprop"] = {"available": True, "version": CP.get_global_param_string("REFPROP_version"),
                             "interface": "CoolProp REFPROP backend", "files": _refprop_files(CP, library),
                             "reason": "已加载本机 REFPROP 库并完成氧气状态点计算；各流体仍需逐点检查，不切换循环计算后端。"}
    except Exception:
        result["refprop"]["reason"] = "发现本机动态库，但 CoolProp 无法完成 REFPROP 功能探测；请核对安装、位数、流体数据及路径配置。"
    return result


def property_capabilities():
    with _PROPERTY_LOCK:
        return deepcopy(_capabilities())


PROPERTY_COMPARISON_KEYS = (
    "density_kg_m3", "cp_J_kg_K", "cv_J_kg_K", "speed_sound_m_s",
    "viscosity_Pa_s", "conductivity_W_m_K", "enthalpy_delta_J_kg", "entropy_delta_J_kg_K",
)


def _property_model_metadata(state, CP=None):
    """Report only metadata exposed by this backend, never infer an EOS match."""
    metadata = {}
    for key, getter in (("backend_name", "backend_name"), ("fluid_names", "fluid_names")):
        try:
            metadata[key] = getattr(state, getter)()
        except Exception:
            pass
    if CP is not None and metadata.get("backend_name") == "HelmholtzEOSBackend":
        try:
            fluid = metadata["fluid_names"][0]
            raw = CP.get_fluid_param_string(fluid, "JSON")
            document = json.loads(raw)[0]
            metadata["fluid_definition_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            metadata["fluid_definition_source"] = "installed CoolProp fluid JSON"
            metadata["melting_line_reference"] = document.get("ANCILLARIES", {}).get("melting_line", {}).get("BibTeX")
        except Exception:
            pass
    for key, parameter in (("cas", "CAS"), ("eos_reference", "BibTeX-EOS"),
                           ("viscosity_reference", "BibTeX-VISCOSITY"),
                           ("conductivity_reference", "BibTeX-CONDUCTIVITY")):
        try:
            value = state.fluid_param_string(parameter)
            if isinstance(value, str) and value.strip():
                metadata[key] = value
        except Exception:
            pass
    critical = {}
    for key, getter in (("temperature_K", "T_critical"), ("pressure_Pa", "p_critical")):
        try:
            value = float(getattr(state, getter)())
            if math.isfinite(value) and value > 0:
                critical[key] = value
        except Exception:
            pass
    return metadata, critical


def _melting_boundary(CP, state, temperature, pressure):
    """A fluid EOS rectangle alone does not exclude solid/metastable states."""
    result = {"status": "unavailable", "temperature_K": None, "margin_K": None,
              "reason": "该后端未提供此状态可用的熔化线；不据此宣称已排除固相。"}
    try:
        if not state.has_melting_line():
            return result
        melt = float(state.melting_line(CP.iT, CP.iP, pressure))
        if not math.isfinite(melt) or melt <= 0:
            return result
        margin = temperature - melt
        return {"status": "pass" if margin > 0 else "fail", "temperature_K": melt, "margin_K": margin,
                "reason": "按本后端熔化线检查 T > T_melt(P)；边界及固相侧不作为单相流体结果使用。"}
    except Exception:
        return result


def _properties(CP, backend, point):
    state = CP.AbstractState(backend, point["fluid"])
    limits = {"temperature_min_K": state.Tmin(), "temperature_max_K": state.Tmax(), "pressure_max_Pa": state.pmax()}
    metadata, critical = _property_model_metadata(state, CP)
    context = {"limits": limits, "model_metadata": metadata, "critical_state": critical,
               "range_scope": "后端公开的流体 EOS 温压范围；不是逐物性的实验不确定度边界。",
               "transport_applicability": {"status": "not_assessed",
                   "reason": "黏度与导热率采用独立关联式；数值有限及 EOS 范围通过不能验证输运模型适用性。"}}
    temperature, pressure = point["temperature_K"], point["pressure_Pa"]
    if not limits["temperature_min_K"] <= temperature <= limits["temperature_max_K"] or pressure > limits["pressure_max_Pa"]:
        return {"status": "fail", **context, "reason": "输入超出该后端公开温压有效范围；不执行外推。"}
    context["melting_boundary"] = _melting_boundary(CP, state, temperature, pressure)
    if context["melting_boundary"]["status"] == "fail":
        return {"status": "fail", **context, "reason": "状态位于本后端熔化线边界或固相侧；不把流体 EOS 的亚稳延拓作为有效流体物性。"}
    state.update(CP.PT_INPUTS, pressure, temperature)
    phases = {getattr(CP, "iphase_" + name): name for name in (
        "liquid", "gas", "twophase", "supercritical", "supercritical_gas", "supercritical_liquid", "critical_point", "unknown", "not_imposed")}
    phase = phases.get(state.phase(), "unknown")
    if phase in ("twophase", "unknown", "not_imposed"):
        return {"status": "unavailable", **context, "phase": phase, "reason": "T/P 未给出可唯一解释的单相状态；两相检查需要额外的干度或相态约束。"}
    getters = {"density_kg_m3": "rhomass", "enthalpy_J_kg": "hmass", "cp_J_kg_K": "cpmass",
               "entropy_J_kg_K": "smass", "viscosity_Pa_s": "viscosity", "conductivity_W_m_K": "conductivity",
               "cv_J_kg_K": "cvmass", "speed_sound_m_s": "speed_sound"}
    props, missing = {}, []
    for key, getter in getters.items():
        try:
            value = float(getattr(state, getter)())
            if not math.isfinite(value) or (key not in ("enthalpy_J_kg", "entropy_J_kg_K") and value <= 0):
                raise ValueError("invalid property")
            props[key] = value
        except Exception:
            props[key] = None
            missing.append(key)
    # Do not change global reference states: subtract each backend's own h/s at
    # the exact same dilute gas reference instead, including phase enthalpy.
    reference = {"temperature_K": 600.0 if point["fluid"] == "n-Dodecane" else 300.0, "pressure_Pa": 1000.0}
    try:
        ref = CP.AbstractState(backend, point["fluid"])
        ref.update(CP.PT_INPUTS, reference["pressure_Pa"], reference["temperature_K"])
        reference["phase"] = phases.get(ref.phase(), "unknown")
        if reference["phase"] not in ("gas", "supercritical_gas"):
            raise ValueError("reference is not dilute gas")
        for raw, delta, getter in (("enthalpy_J_kg", "enthalpy_delta_J_kg", "hmass"), ("entropy_J_kg_K", "entropy_delta_J_kg_K", "smass")):
            reference_value = float(getattr(ref, getter)())
            if props[raw] is None or not math.isfinite(reference_value):
                raise ValueError("reference is not finite")
            props[delta] = props[raw] - reference_value
    except Exception:
        missing.extend(("enthalpy_delta_J_kg", "entropy_delta_J_kg_K"))
        props.update(enthalpy_delta_J_kg=None, entropy_delta_J_kg_K=None)
    return {"status": "partial" if missing else "pass", "properties_si": props, "phase": phase,
            **context, "reference_state": reference, "missing_properties": missing,
            "reason": "单相物性已计算；pass 仅表示范围与数值检查通过，不代表物性精度已验证。"}


def _property_comparison(heos, refprop, requested):
    """Compare only like phase states and like h/s reference coordinates."""
    result = {"status": "not_requested", "phase_agreement": None, "reference_agreement": None,
              "evidence_independence": "not_established",
              "compared_properties": [], "excluded_properties": {},
              "reason": "未请求 REFPROP 同点对照。"}
    if not requested:
        return result, {}
    first, second = heos.get("properties_si", {}), refprop.get("properties_si", {})
    if not first or not second:
        return result | {"status": "unavailable", "reason": "至少一个后端未返回该状态的物性；保留可用结果，不计算双后端差异。"}, {}
    phase_first, phase_second = heos.get("phase"), refprop.get("phase")
    valid_phases = {"liquid", "gas", "supercritical", "supercritical_gas", "supercritical_liquid", "critical_point"}
    if phase_first not in valid_phases or phase_second not in valid_phases:
        return result | {"status": "not_comparable", "reason": "缺少可解释的单相标识，不计算双后端差异。"}, {}
    result["phase_agreement"] = phase_first == phase_second
    if not result["phase_agreement"]:
        return result | {"status": "not_comparable", "reason": "两个后端的相态判定不同；分别展示数值，不把跨相态差值解释为物性偏差。",
                         "excluded_properties": {key: "两个后端相态不一致。" for key in PROPERTY_COMPARISON_KEYS}}, {}
    ref_first, ref_second = heos.get("reference_state", {}), refprop.get("reference_state", {})
    result["reference_agreement"] = all(
        _finite(ref_first.get(key)) is not None and ref_first[key] == ref_second.get(key)
        for key in ("temperature_K", "pressure_Pa")) and all(
        ref.get("phase") in ("gas", "supercritical_gas") for ref in (ref_first, ref_second))
    differences = {}
    for key in PROPERTY_COMPARISON_KEYS:
        value, other = _finite(first.get(key), nonnegative=False), _finite(second.get(key), nonnegative=False)
        if key in ("enthalpy_delta_J_kg", "entropy_delta_J_kg_K") and not result["reference_agreement"]:
            result["excluded_properties"][key] = "参考温度、压力或参考气相条件不一致/缺失。"
        elif value is None or other is None:
            result["excluded_properties"][key] = "至少一个后端未提供有限数值。"
        else:
            difference = value - other
            differences[key] = {"heos_minus_refprop": difference,
                                "relative_difference": difference / abs(other) if abs(other) > 1e-30 else None}
    result["compared_properties"] = list(differences)
    result["status"] = "partial" if result["excluded_properties"] else "comparable"
    if not differences:
        result["status"] = "unavailable"
    result["reason"] = "报告 HEOS − REFPROP 及相对 REFPROP 的有符号差异；未预设一致性容差，不能据此判定精度验证通过。"
    return result, differences


def property_checks(request):
    # Loading/configuring REFPROP and querying its shared DLL must be serialized
    # across concurrent validation jobs in this process.
    with _PROPERTY_LOCK:
        return _property_checks(request)


def _property_checks(request):
    request = validate_property_request(request)
    capabilities = property_capabilities()
    checks = []
    if capabilities["coolprop"]["available"]:
        _, CP = _coolprop()
    for index, point in enumerate(request["points"]):
        row = {"id": f"property_{index}", "label": f"{point['fluid']} · {point['temperature_K']:g} K / {point['pressure_Pa']:g} Pa", **point}
        if not capabilities["coolprop"]["available"]:
            row.update(status="unavailable", heos_status="unavailable", reason="CoolProp 不可用。")
            row["refprop"] = {"status": "unavailable" if request["compare_refprop"] else "not_provided",
                              "reason": "CoolProp 接口不可用。" if request["compare_refprop"] else "未请求 REFPROP 同点对照。"}
            row["comparison"], _ = _property_comparison(row, row["refprop"], request["compare_refprop"])
            checks.append(row)
            continue
        try:
            row.update(_properties(CP, "HEOS", point))
        except Exception as error:
            row.update(status="unavailable", reason=f"HEOS 无法在该 T/P 状态计算单相物性（{type(error).__name__}）。")
        row["heos_status"] = row["status"]
        comparison = {"status": "not_provided", "reason": "未请求 REFPROP 同点对照。"}
        if request["compare_refprop"]:
            comparison = {"status": "unavailable", "reason": capabilities["refprop"]["reason"]}
            if capabilities["refprop"]["available"]:
                try:
                    comparison = _properties(CP, "REFPROP", point)
                    comparison["agreement_status"] = "not_assessed"
                except Exception as error:
                    comparison = {"status": "unavailable", "reason": f"本机 REFPROP 无法计算该流体/状态（{type(error).__name__}）。"}
            if comparison["status"] != "pass" and row.get("status") == "pass":
                row["status"] = "partial"
        row["comparison"], differences = _property_comparison(row, comparison, request["compare_refprop"])
        if comparison.get("properties_si"):
            comparison["differences"] = differences
        if row["comparison"]["status"] in ("partial", "not_comparable") and row.get("status") == "pass":
            row["status"] = "partial"
        row["refprop"] = comparison
        checks.append(row)
    return {"kind": "properties", "status": _status(checks), "checks": checks, "capabilities": capabilities,
            "notes": ["仅使用实际安装的物性库；范围内可计算不等于具有统一的实验不确定度保证。",
                      "焓、熵对照使用同一稀薄气参考点的差值；原始绝对焓/熵不直接相减。",
                      "流体 EOS 另检查可用熔化线；熔化线缺失时明确标识，黏度和导热率的实验适用范围仍需独立证据。",
                      "HEOS 与 REFPROP 可能共享状态方程和实验数据；n-Dodecane 是纯正十二烷，不是 RP-1 混合物。"]}


def experiment_checks(source_result, experiment):
    experiment = validate_experiment_request(experiment)
    if not experiment or not experiment["measurements"]:
        return {"kind": "experiment", "status": "not_provided", "checks": [], "notes": ["未提供实测数据；未执行试验验证，也不会生成通过结论。"]}
    inputs, summary, _, _ = _parts(source_result)
    checks = _source_checks(source_result, summary)
    conditions = experiment["conditions"]
    required = {"propellant_pair", "cycle_type", "pc", "of_ratio", "fuel_inlet_temp", "ox_inlet_temp"}
    if inputs.get("cycle_type") == "gg" or conditions.get("cycle_type") == "gg":
        required.add("gg_layout")
    if any(m["metric"] in ("thrust_N", "Isp_s") for m in experiment["measurements"]):
        required.add("ambient_pressure")
    condition_checks = []
    for key in sorted(required | conditions.keys()):
        row = {"id": f"condition_{key}", "label": f"可比工况 · {key}"}
        if key not in conditions or key not in inputs:
            row.update(status="unavailable", reason="缺少实测试验工况或源记录工况；不能默认相同。")
        elif key not in CONDITIONS:
            row.update(status="pass" if conditions[key] == inputs[key] else "fail", measured=conditions[key], predicted=inputs[key])
        else:
            recorded = _finite(inputs[key])
            measured = conditions[key]
            row.update(predicted_si=recorded, measured_si=measured["value"], unit=measured["unit"], tolerance_si=measured["tolerance"])
            if recorded is None:
                row.update(status="unavailable", reason="源记录缺少有限的工况数值。")
            else:
                difference = recorded - measured["value"]
                row.update(status="pass" if abs(difference) <= measured["tolerance"] else "fail", difference_si=difference)
        condition_checks.append(row)
    checks.extend(condition_checks)
    comparable = all(c["status"] == "pass" for c in condition_checks)
    source_valid = all(c["status"] == "pass" for c in _source_checks(source_result, summary))
    for measurement in experiment["measurements"]:
        metric = measurement["metric"]
        key, factor, unit, label = METRICS[metric]
        predicted = _finite(summary.get(key))
        predicted = predicted * factor if predicted is not None else None
        tolerance = measurement.get("tolerance", measurement.get("uncertainty"))
        row = {"id": f"measurement_{metric}", "label": label, "metric": metric,
               "predicted_si": predicted, "measured_si": measurement["value"], "unit": unit,
               "tolerance_si": tolerance, "uncertainty_si": measurement.get("uncertainty"),
               "criterion": "explicit_tolerance" if "tolerance" in measurement else "within_reported_uncertainty"}
        if predicted is not None:
            difference = predicted - measurement["value"]
            row.update(difference_si=difference, relative_difference=difference / abs(measurement["value"]) if measurement["value"] else None)
        if not comparable or not source_valid:
            row.update(status="unavailable", reason="源计算尚未有效通过，或试验工况不匹配/缺失；差值仅供诊断，不作验证通过判定。")
        elif predicted is None:
            row.update(status="unavailable", reason="源计算未输出该指标的有限值。")
        else:
            row["status"] = "pass" if abs(row["difference_si"]) <= tolerance else "fail"
        checks.append(row)
    return {"kind": "experiment", "status": _status(checks), "role": experiment["role"],
            "comparable_conditions": comparable, "checks": checks,
            "notes": ["标定一致性检查；这些数据不能同时作为独立留出验证。" if experiment["role"] == "calibration" else "留出验证：仅适用于未用于调参的独立数据；该角色由提交者声明。",
                      "比较稳态、同一指标定义下的计算与实测。用户必须确认试验工况和指标测量位置一致；不能把复制的计算工况当作实测证据。",
                      "tolerance 为明确验收容差；仅给 uncertainty 时只检查是否落入所报区间，不推定置信水平、覆盖因子或模型误差。"]}
