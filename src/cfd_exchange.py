"""Solver-neutral CFD file exchange; this module never runs a CFD solver.

All model states are references, not automatically valid CFD boundary conditions.
Pressure is absolute and SI; total/static definitions require explicit review.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import numbers
import os
from pathlib import Path
import tempfile
import uuid

from engineering_assessment import assess_engineering
from core import EngineInputs, ValidationError

MAX_FILE_BYTES = 20 * 1024 * 1024
TASKS = frozenset({"chamber_flow", "cooling_flow", "turbine_flow"})
FILES = {"result": "result.json", "boundaries": "boundaries.csv",
         "geometry": "geometry_contour.csv", "nozzle_reference": "nozzle_contour.csv",
         "template": "result_template.json", "units": "SI_UNITS.md"}
METRIC_RULES = {
    "pressure_drop_Pa": ("Pa", 0.0, 1e10),
    "outlet_temperature_K": ("K", 0.0, 20000.0),
    "max_wall_temperature_K": ("K", 0.0, 20000.0),
    "heat_load_W": ("W", 0.0, 1e13),
    "thrust_N": ("N", 0.0, 1e10),
    "mass_flow_kg_s": ("kg/s", 0.0, 1e9),
    "efficiency": ("1", 0.0, 1.0),
    "shaft_power_W": ("W", 0.0, 1e13),
}
PROFILE_RULES = {"x_m": (-1e6, 1e6), "wall_temperature_K": (0.0, 20000.0),
                 "coolant_temperature_K": (0.0, 20000.0), "pressure_Pa": (0.0, 1e10),
                 "heat_flux_W_m2": (0.0, 1e13), "radius_m": (0.0, 1e5)}
_IDENTITY_FIELDS = ("task", "domain", "inputs", "initial_results", "states", "environment",
                    "geometry_reference", "settings", "boundaries", "boundary_review")


def _plain(value):
    if is_dataclass(value):
        value = asdict(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("CFD 交换数据不能包含 NaN 或 Infinity")
        return value
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON 字典键必须是字符串")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "tolist"):
        return _plain(value.tolist())
    raise ValueError(f"不能导出的 CFD 数据类型：{type(value).__name__}")


def _json_bytes(document):
    data = json.dumps(_plain(document), ensure_ascii=False, allow_nan=False,
                      indent=2, sort_keys=True).encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("CFD 文件超过 20 MiB 限制")
    return data


def _fixed_path(directory: Path, name: str) -> Path:
    path = directory / name
    if path.resolve().parent != directory.resolve():
        raise ValueError("任务文件路径必须位于任务目录中，不能指向外部文件")
    return path


def _atomic_write(path: Path, content: bytes):
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("CFD 文件超过 20 MiB 限制")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".cfd-", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("CFD 文件超过 20 MiB 限制")
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("CFD 文件超过 20 MiB 限制")
    def reject_constant(value):
        raise ValueError(f"JSON 不允许 {value}")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 字段重复：{key}")
            result[key] = value
        return result
    try:
        result = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant,
                            object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"CFD JSON 文件损坏：{exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("CFD JSON 顶层必须是对象")
    try:
        return _plain(result)
    except RecursionError as exc:
        raise ValueError("CFD JSON 嵌套过深") from exc


def _number(value, label, lower=None, upper=None, strict_positive=False):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{label} 必须是有限数值，不能为 bool 或字符串")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数值")
    if lower is not None and value < lower or upper is not None and value > upper or strict_positive and value <= 0:
        raise ValueError(f"{label} 超出允许范围")
    return value


def _settings(settings, task):
    settings = {} if settings is None else _plain(settings)
    if not isinstance(settings, dict):
        raise ValueError("CFD settings 必须为对象")
    if "task" in settings and settings["task"] != task:
        raise ValueError("settings.task 与任务类型不一致")
    clean = {"pressure_basis": settings.get("pressure_basis", "unresolved"),
             "temperature_basis": settings.get("temperature_basis", "unresolved"),
             "setup_reviewed": settings.get("setup_reviewed", False),
             "turbine_side": settings.get("turbine_side", "ox")}
    for key in ("pressure_basis", "temperature_basis"):
        if clean[key] not in ("unresolved", "static", "total"):
            raise ValueError(f"{key} 必须为 unresolved / static / total")
    if type(clean["setup_reviewed"]) is not bool:
        raise ValueError("setup_reviewed 必须是 bool")
    if clean["turbine_side"] not in ("ox", "fuel"):
        raise ValueError("turbine_side 必须为 ox / fuel")
    return clean


def _fingerprint(manifest):
    payload = {key: manifest[key] for key in _IDENTITY_FIELDS}
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _domain(task, inputs, settings):
    if task == "cooling_flow":
        count = _number(inputs.get("cooling_n_channels"), "cooling_n_channels", 1, 10000)
        if count != int(count):
            raise ValueError("cooling_n_channels 必须为整数")
        return {"flow_scope": "full_engine", "heat_load_scope": "full_engine",
                "cooling_channel_count": int(count), "no_implicit_multiplication": True}
    if task == "turbine_flow":
        return {"flow_scope": "selected_turbine", "turbine_side": settings["turbine_side"],
                "no_implicit_multiplication": True}
    return {"flow_scope": "main_chamber_and_nozzle", "no_implicit_multiplication": True}


def _same_domain(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same_domain(first[key], second[key]) for key in first)
    return first == second


def _contour(points_x, points_r):
    if not isinstance(points_x, list) or not isinstance(points_r, list) or len(points_x) != len(points_r):
        raise ValueError("几何型面 x/r 必须为等长列表")
    if points_x and len(points_x) < 2:
        raise ValueError("几何型面至少需要 2 个点")
    xs = [_number(x, "型面 x_m", -1e6, 1e6) for x in points_x]
    rs = [_number(r, "型面 radius_m", 0, 1e5, strict_positive=True) for r in points_r]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError("几何型面 x_m 必须严格递增")
    return xs, rs


def _geometry_reference(geometry, inputs):
    geometry = _plain(geometry or {})
    if not isinstance(geometry, dict):
        raise ValueError("geometry 必须为几何字典")
    nozzle = geometry.get("nozzle", geometry)
    if not isinstance(nozzle, dict):
        raise ValueError("geometry.nozzle 必须为对象")
    nx, nr = _contour(nozzle.get("contour_x", []), nozzle.get("contour_r", []))
    contour = {"x_m": nx, "radius_m": nr, "coordinate_origin": "nozzle_throat_origin",
               "includes_cylindrical_chamber": False, "source": "geometry.nozzle.contour_x/r",
               "note": "仅喷管收缩 / 扩张段，未包含燃烧室圆筒段。"}
    chamber = geometry.get("chamber", {})
    needed = (chamber.get("cylindrical_section_length_m"), chamber.get("radius_m"),
              nozzle.get("throat_radius_m"), nozzle.get("exit_radius_m")) if isinstance(chamber, dict) else ()
    if needed and all(isinstance(v, numbers.Real) and not isinstance(v, bool) and v > 0 for v in needed):
        from geometry.nozzle import full_hot_gas_contour
        length, rc, rt, re = needed
        expansion = nozzle.get("expansion_ratio", inputs.get("expansion_ratio"))
        expansion = _number(expansion, "expansion_ratio", 1, 1e5)
        theta = _number(nozzle.get("convergent_half_angle_deg", inputs.get("chamber_conv_half_angle_deg", 30.0)),
                        "convergent_half_angle_deg", 1, 89)
        bell = _number(nozzle.get("bell_fraction", inputs.get("nozzle_bell_fraction", 0.8)), "bell_fraction", 0.1, 1.5)
        if nx:
            # Saved contour coordinates own geometry; do not re-evaluate a
            # historical method or substitute mutable parameter defaults.
            throat = length - nx[0]
            xs = [0.] + [x + throat for x in nx]
            rs = [rc] + nr
        else:
            xs, rs, throat = full_hot_gas_contour(rt, re, rc, expansion, length,
                theta_conv_deg=theta, bell_fraction=bell,
                nozzle_contour_method=nozzle.get("nozzle_contour_method", inputs.get("nozzle_contour_method", "legacy_log_v1")),
                nozzle_initial_angle_deg=nozzle.get("initial_expansion_angle_deg", inputs.get("nozzle_initial_angle_deg", 35.)),
                nozzle_exit_angle_deg=nozzle.get("exit_angle_deg", inputs.get("nozzle_exit_angle_deg", 10.)))
        xs, rs = _contour(list(xs), list(rs))
        contour = {"x_m": xs, "radius_m": rs, "coordinate_origin": "injection_face_origin",
                   "includes_cylindrical_chamber": True, "throat_x_m": throat,
                   "source": "saved_nozzle_contour_with_cylinder" if nx else "geometry.nozzle.full_hot_gas_contour",
                   "note": "圆筒、收缩、喉部及钟形段的轴对称燃气侧参考型面；非 CAD 实体或 CFD 网格。"}
    return {"snapshot": geometry, "contour": contour,
            "nozzle_contour": {"x_m": nx, "radius_m": nr, "coordinate_origin": "nozzle_throat_origin"},
            "is_mesh": False, "scope": "axisymmetric_reference_only"}


def _boundary_references(results, task, settings, states, inputs, summary, components):
    rows, missing = [], []
    def station(key, role, flow=None, note="", required=True, reference_state=None):
        state = states.get(key) if reference_state is None else reference_state
        if not isinstance(state, dict):
            if required:
                missing.append(key)
            return
        pressure = _number(state.get("pressure"), f"{key}.pressure", 0, 1e10, True)
        temperature = _number(state.get("temperature"), f"{key}.temperature", 0, 20000, True)
        if role == "inlet":
            flow = state.get("mass_flow") if flow is None else flow
            flow = _number(flow, f"{key}.mass_flow", 0, 1e9, True)
        else:
            flow = None  # downstream/internal station is not an additional inlet
        rows.append({"boundary_id": f"{role}_{key}", "role": role, "state_key": key,
                     "pressure_absolute_Pa": pressure, "pressure_basis": settings["pressure_basis"],
                     "temperature_K": temperature, "temperature_basis": settings["temperature_basis"],
                     "mass_flow_kg_s": flow, "composition": state.get("composition", {}), "notes": note})
    cycle = inputs.get("cycle_type", "ffsc")
    if task == "chamber_flow":
        if cycle == "gg":
            flow = summary.get("mdot_chamber_kg_s")
            ratio = summary.get("chamber_mixture_ratio")
            if flow is None or ratio is None:
                missing.append("GG 主室专用流量 / 混合比（不能把 GG 旁路计入主室）")
            else:
                flow = _number(flow, "mdot_chamber_kg_s", 0, 1e9, True)
                ratio = _number(ratio, "chamber_mixture_ratio", 0, 1e5, True)
                station("lox_after_pump", "inlet", flow * ratio / (1 + ratio), "按主室流量分离 GG 旁路。")
                station("fuel_after_cooling", "inlet", flow / (1 + ratio), "主室专用冷却后燃料流量。")
        else:
            station("ox_turbine_exhaust", "inlet", note="富氧涡轮排气进入主室。")
            station("fuel_turbine_exhaust", "inlet", note="富燃涡轮排气进入主室。")
        station("main_chamber", "domain_reference", note="主室内部状态参考，不能作为喷管出口边界；不重复计入流量。", required=False)
    elif task == "cooling_flow":
        outlet = states.get("fuel_after_cooling") or {}
        flow = outlet.get("mass_flow")
        if flow is None:
            missing.append("冷却通道实际流量（不能使用含 GG 支路的泵总流量）")
        else:
            station("fuel_after_pump", "inlet", flow, "全冷却通道域的实际通流；单通道模型需另行分配流量。")
        station("fuel_after_cooling", "outlet_reference", note="冷却后参考状态；出口流量不重复计入。")
    else:
        side = settings["turbine_side"]
        if cycle == "gg":
            name = f"gg_turbine_{side}" if summary.get("gg_layout_dual", 0) else "gg_turbine"
            outputs = components.get(name, {}).get("outputs", {})
            flow = outputs.get("mass_flow_kgs")
            if flow is None:
                missing.append(f"{name}.mass_flow_kgs")
            else:
                station("gg_outlet", "inlet", flow, f"选中 {name} 的专用通流；并联双轴不能使用 GG 总流量。")
            if summary.get("gg_layout_dual", 0):
                temperature = outputs.get("T_exhaust_K")
                pressure = summary.get("gg_turbine_backpressure_Pa")
                if temperature is None or pressure is None:
                    missing.append(f"{name} 专用出口压力 / 温度")
                else:
                    station(f"{name}_exhaust", "outlet_reference", note=f"{name} 专用出口参考。",
                            reference_state={"pressure": pressure, "temperature": temperature, "composition": {}})
            else:
                station("gg_turbine_exhaust", "outlet_reference", note="单轴 GG 出口参考。")
        else:
            station(f"{side}_preburner_outlet", "inlet", note=f"{side} 侧涡轮入口参考。")
            station(f"{side}_turbine_exhaust", "outlet_reference", note="涡轮出口参考，流量不重复计入。")
    return rows, {"complete": not missing, "missing_references": missing,
                  "note": "P 为绝压；P/T 总量或静量含义未由模型状态名称推定，须由使用者核实。"}


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def create_cfd_job(results, geometry, parent_directory, task="chamber_flow", settings=None, environment=None,
                   *, source_inputs=None) -> Path:
    if task not in TASKS:
        raise ValueError("不支持的 CFD 任务类型")
    effective_inputs, states, summary, components = (_plain(results.inputs), _plain(results.states),
                                                     _plain(results.summary), _plain(results.components))
    inputs = effective_inputs if source_inputs is None else _plain(source_inputs)
    if not isinstance(inputs, dict):
        raise ValueError("source_inputs 必须为 EngineInputs 或完整输入字典")
    # Keep the submitted snapshot distinct from any effective values modified
    # by model outer loops. Validate without mutating or filling the snapshot.
    try:
        EngineInputs(**inputs)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"source_inputs 无效：{exc}") from exc
    clean_settings = _settings(settings, task)
    references = _geometry_reference(geometry, effective_inputs)
    boundaries, boundary_review = _boundary_references(results, task, clean_settings, states, effective_inputs, summary, components)
    job_id = str(uuid.uuid4())
    manifest = {"format": "lre.cfd.job", "schema_version": 1, "job_id": job_id, "task": task,
                "domain": _domain(task, effective_inputs, clean_settings),
                "created_at": datetime.now(timezone.utc).isoformat(), "inputs": inputs,
                "initial_results": {"effective_inputs": effective_inputs, "summary": summary, "components": components,
                                    "warnings": _plain(results.warnings), "engineering_assessment": assess_engineering(results)},
                "states": states, "environment": _plain(environment or {}), "geometry_reference": references,
                "settings": clean_settings, "boundaries": boundaries, "boundary_review": boundary_review,
                "files": dict(FILES), "native_solver_adapter": None,
                "notice": "通用文件交接，没有 Fluent / OpenFOAM 原生适配；型面不是三维网格，收敛不是工程认证。"}
    manifest["input_fingerprint"] = _fingerprint(manifest)
    template = {"format": "lre.cfd.result", "schema_version": 1, "job_id": job_id,
                "input_fingerprint": manifest["input_fingerprint"], "task": task,
                "domain": manifest["domain"],
                "source_kind": "test", "solver": {"name": "REPLACE_WITH_SOLVER_NAME", "version": "REPLACE_WITH_VERSION"},
                "converged": False, "metrics": {}, "template": True,
                "notes": "必须用实际求解输出替换本模板；测试数据不能用于设计回写。"}
    boundary_rows = [{**row, "composition": json.dumps(row["composition"], ensure_ascii=False, sort_keys=True)} for row in boundaries]
    payloads = {"manifest.json": _json_bytes(manifest), FILES["template"]: _json_bytes(template),
                FILES["boundaries"]: _csv_bytes(["boundary_id", "role", "state_key", "pressure_absolute_Pa", "pressure_basis",
                    "temperature_K", "temperature_basis", "mass_flow_kg_s", "composition", "notes"], boundary_rows)}
    for key, contour in (("geometry", references["contour"]), ("nozzle_reference", references["nozzle_contour"])):
        payloads[FILES[key]] = _csv_bytes(["x_m", "radius_m", "coordinate_origin"],
            [{"x_m": x, "radius_m": r, "coordinate_origin": contour["coordinate_origin"]}
             for x, r in zip(contour["x_m"], contour["radius_m"])])
    payloads[FILES["units"]] = ("# SI units and review\n\nPressure: Pa absolute (never gauge). Temperature: K. "
        "Mass flow: kg/s, counted only once at each domain inlet. Geometry: m. "
        "P/T station definitions require reviewed static/total choices.\n\n"
        "Geometry is an axisymmetric reference, not a CAD solid or 3D CFD mesh. "
        "Boundary rows are model references, not a complete solver setup. "
        "Species, turbulence, wall/thermal models, mesh convergence and conservation need verification.\n\n"
        "Cooling flow_scope and heat_load_scope are full_engine. heat_load_W must be the aggregate "
        "of all engine cooling channels. A single-channel/sector solver must aggregate using its actual "
        "external domain; this Workbench never implicitly multiplies values. Cooling results must echo "
        "manifest.domain exactly to become eligible for design feedback.\n\n"
        "Result units: " + ", ".join(f"{key}: {rule[0]}" for key, rule in METRIC_RULES.items()) +
        ".\n\nSolver-reported convergence is not engineering certification. Test or unconverged data is diagnostic only.\n").encode("utf-8")
    parent = Path(parent_directory).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / f"CFD_{task}_{job_id[:8]}"
    directory.mkdir()
    try:
        # Publish manifest last. An interrupted export never presents a complete
        # job whose companion files were not written.
        for name, content in payloads.items():
            if name != "manifest.json":
                _atomic_write(_fixed_path(directory, name), content)
        _atomic_write(_fixed_path(directory, "manifest.json"), payloads["manifest.json"])
    except BaseException:
        # All names are owned files in the newly created, unique directory.
        for name in payloads:
            owned = directory / name
            if owned.is_file() and not owned.is_symlink():
                owned.unlink()
        if not any(directory.iterdir()):
            directory.rmdir()
        raise
    return directory


def load_cfd_job(job_directory) -> dict:
    directory = Path(job_directory).expanduser().resolve()
    manifest = _read_json(_fixed_path(directory, "manifest.json"))
    if manifest.get("format") != "lre.cfd.job" or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("不支持的 CFD 任务格式 / 版本")
    if manifest.get("task") not in TASKS:
        raise ValueError("CFD 任务类型不支持")
    try:
        if str(uuid.UUID(manifest["job_id"])) != manifest["job_id"]:
            raise ValueError("任务 ID 不是规范 UUID")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("CFD 任务 ID 无效") from exc
    if manifest.get("files") != FILES:
        raise ValueError("CFD files 必须使用固定文件名，不能指定任意路径")
    for name in FILES.values():
        _fixed_path(directory, name)
    for key in _IDENTITY_FIELDS:
        if key not in manifest:
            raise ValueError(f"CFD 任务缺少 {key}")
    if manifest["settings"] != _settings(manifest["settings"], manifest["task"]):
        raise ValueError("CFD settings 字段不符合公开契约")
    if manifest.get("input_fingerprint") != _fingerprint(manifest):
        raise ValueError("CFD 输入指纹不匹配，任务快照已被修改")
    for key in ("inputs", "initial_results", "states", "environment", "geometry_reference", "boundary_review"):
        if not isinstance(manifest[key], dict):
            raise ValueError(f"CFD {key} 必须为对象")
    if not isinstance(manifest["boundaries"], list):
        raise ValueError("CFD boundaries 必须为列表")
    if type(manifest["boundary_review"].get("complete")) is not bool:
        raise ValueError("boundary_review.complete 必须为 bool")
    effective_inputs = manifest["initial_results"].get("effective_inputs", manifest["inputs"])
    if not isinstance(effective_inputs, dict):
        raise ValueError("initial_results.effective_inputs 必须为输入对象")
    if not _same_domain(manifest.get("domain"), _domain(manifest["task"], effective_inputs, manifest["settings"])):
        raise ValueError("CFD domain 与任务范围不一致")
    return manifest


def _validate_result(document, manifest):
    if document.get("format") != "lre.cfd.result" or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("不支持的 CFD 结果格式 / 版本")
    for key in ("job_id", "input_fingerprint", "task"):
        if document.get(key) != manifest[key]:
            raise ValueError(f"CFD 结果 {key} 与当前任务不匹配")
    if document.get("template") is True:
        raise ValueError("占位模板不能作为 CFD 结果导入")
    if document.get("source_kind") not in ("cfd", "test"):
        raise ValueError("source_kind 必须为 cfd 或 test")
    solver = document.get("solver")
    if not isinstance(solver, dict) or not all(isinstance(solver.get(key), str) and solver[key].strip()
                                              and not solver[key].startswith("REPLACE_WITH_") for key in ("name", "version")):
        raise ValueError("solver.name/version 必须填写实际名称与版本")
    if type(document.get("converged")) is not bool:
        raise ValueError("converged 必须是严格 bool，不能是 0/1 或字符串")
    metrics = document.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics 至少需要一个支持的指标")
    for key, item in metrics.items():
        if key not in METRIC_RULES or not isinstance(item, dict) or set(item) != {"value", "unit"}:
            raise ValueError(f"不支持的指标或指标结构：{key}")
        unit, lower, upper = METRIC_RULES[key]
        if item["unit"] != unit:
            raise ValueError(f"{key} 单位必须精确为 {unit}")
        _number(item["value"], key, lower, upper, strict_positive=unit == "K")
    profile = document.get("wall_profile")
    if profile is not None:
        if not isinstance(profile, dict) or not {"x_m", "wall_temperature_K"}.issubset(profile):
            raise ValueError("wall_profile 至少需要 x_m 和 wall_temperature_K")
        length = len(profile["x_m"]) if isinstance(profile["x_m"], list) else 0
        if length < 2:
            raise ValueError("wall_profile 至少需要 2 个点")
        for key, values in profile.items():
            if key not in PROFILE_RULES or not isinstance(values, list) or len(values) != length:
                raise ValueError(f"wall_profile.{key} 必须是支持的等长列表")
            lo, hi = PROFILE_RULES[key]
            for value in values:
                _number(value, f"wall_profile.{key}", lo, hi, key.endswith("_K") or key == "radius_m")
        if any(b <= a for a, b in zip(profile["x_m"], profile["x_m"][1:])):
            raise ValueError("wall_profile.x_m 必须严格递增")
    reasons = []
    if document["source_kind"] != "cfd":
        reasons.append("测试数据不能用于设计回写")
    if not document["converged"]:
        reasons.append("求解器报告未收敛，结果仅供诊断")
    settings = manifest["settings"]
    if not settings["setup_reviewed"]:
        reasons.append("CFD 建模设置尚未人工审阅")
    if any(settings[key] == "unresolved" for key in ("pressure_basis", "temperature_basis")):
        reasons.append("P/T 总量或静量口径未明确")
    if not manifest["boundary_review"]["complete"]:
        reasons.append("模型边界参考缺失，须补充与核实")
    if manifest["task"] == "cooling_flow" and not _same_domain(document.get("domain"), manifest["domain"]):
        reasons.append("冷却结果未确认全发动机通流 / 全通道热负荷域，禁止把单通道结果直接回写或凭空倍乘")
    normalized = dict(document)
    normalized.update(feedback_eligible=not reasons, diagnostic_only=bool(reasons),
                      eligibility_reasons=reasons,
                      convergence_notice="收敛仅为外部求解器声明，不能据此认证工程可行性或计算精度。")
    return normalized


def import_cfd_result(job_directory, result_path=None) -> dict:
    directory = Path(job_directory).expanduser().resolve()
    manifest = load_cfd_job(directory)
    path = _fixed_path(directory, FILES["result"]) if result_path is None else Path(result_path).expanduser().resolve()
    document = _validate_result(_read_json(path), manifest)
    document["imported_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(_fixed_path(directory, "imported_result.json"), _json_bytes(document))
    return document


def load_imported_result(job_directory) -> dict | None:
    directory = Path(job_directory).expanduser().resolve()
    manifest = load_cfd_job(directory)
    path = _fixed_path(directory, "imported_result.json")
    if not path.exists():
        return None
    return _validate_result(_read_json(path), manifest)
