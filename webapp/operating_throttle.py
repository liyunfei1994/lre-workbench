"""Immutable design-record boundary for the local fixed-geometry throttle model.

This adapter cannot accept new geometry, preburner targets or concept-design
inputs. The local calibrated model remains separate from the full fixed-steady
model-data contract, whose missing component characteristics are not waived.
"""
from copy import deepcopy
import json
import math

from webapp.throttle import source_baseline
from webapp.validation import fingerprint


DEFAULTS = {"throttle_min": .9, "throttle_max": 1., "throttle_steps": 5}
MAX_POINTS = 15
MODE = "fixed_geometry_local_throttle"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def _source(source):
    values, baseline = source_baseline(source)
    calculation = source["result"]["calculation"]
    thrust = _number(calculation.get("summary", {}).get("thrust_N"))
    if thrust is None or thrust <= 0:
        raise ValueError("来源记录缺少有效的实际计算推力，不能以设计目标替代 100% 推力基准。")
    # Capture geometry that is saved in the design record; never reconstruct a
    # pump, turbine or nozzle here and never substitute current project edits.
    payload = {
        "cycle_type": values["cycle_type"], "propellant_pair": values["propellant_pair"],
        "nozzle": {"throat_area_m2": baseline["throat_area_m2"],
                   "expansion_ratio": baseline["expansion_ratio"],
                   "contour_definition": deepcopy(baseline["contour_inputs"])},
        "feed": deepcopy(baseline.get("feed_hardware")),
        "cooling": deepcopy(baseline.get("cooling_hardware")),
        "saved_geometry": {key: deepcopy(source["result"][key]) for key in
                           ("nozzle_geometry", "pump_geometry", "turbine_geometry")
                           if key in source["result"]},
    }
    hardware = {"schema_version": 1, "id": fingerprint(payload), "sha256": fingerprint(payload),
                "payload": payload, "scope": "saved_geometry_and_resistance_evidence"}
    provenance = {"job_id": source["id"], "project_id": source["project_id"],
                  "revision": source["revision"], "created": source.get("created"),
                  "result_sha256": fingerprint(source["result"]),
                  "calculation_inputs_sha256": fingerprint(calculation["inputs"])}
    return calculation, hardware, provenance


def configuration(source):
    """Inspect readiness without replacing a missing source with design targets."""
    from lre_workbench.operations.throttle import configuration as core_configuration
    result = {"schema_version": 1, "analysis_mode": MODE, "defaults": deepcopy(DEFAULTS),
              "ready": False, "issues": [], "baseline": {}, "hardware": None,
              "model": {"id": "fixed_geometry_local_throttle_v1"}, "limitations": []}
    try:
        calculation, hardware, provenance = _source(source)
        setup = core_configuration(deepcopy(calculation))
        result.update(deepcopy(setup))
        result.update(hardware=hardware, source=provenance, defaults=deepcopy(DEFAULTS), analysis_mode=MODE)
        result["ready"] = bool(setup.get("ready", (setup.get("readiness") or {}).get("can_run", False)))
        result["issues"] = list(setup.get("issues", []))
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
        result["issues"] = [str(exc) or "来源记录不满足固定几何局部节流模型初始化要求。"]
    return result


def normalize_request(value, source):
    allowed = {"source_job_id", *DEFAULTS}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("节流请求仅允许来源记录、最低/最高推力比例与扫描点数；不能修改硬件或预燃室目标。")
    if value.get("source_job_id") != source["id"]:
        raise ValueError("来源计算记录不匹配。")
    normalized = {"source_job_id": source["id"], **DEFAULTS, **value}
    for key in ("throttle_min", "throttle_max"):
        number = _number(normalized[key])
        if number is None or not .05 <= number <= 1.:
            raise ValueError("节流推力比例须在 0.05–1.00（5%–100%）范围内。")
        normalized[key] = number
    if normalized["throttle_min"] >= normalized["throttle_max"]:
        raise ValueError("最低推力比例必须小于最高推力比例。")
    count = _number(normalized["throttle_steps"])
    if count is None or not count.is_integer() or not 2 <= count <= MAX_POINTS:
        raise ValueError(f"节流扫描点数须为 2–{MAX_POINTS} 的整数。")
    normalized["throttle_steps"] = int(count)
    setup = configuration(source)
    if not setup["ready"]:
        raise ValueError("；".join(str(item) for item in setup["issues"]) or "来源尚不能初始化局部固定几何模型。")
    return normalized


def calculate_operating_throttle(request, source, progress=None):
    from lre_workbench.operations.throttle import calculate
    from lre_workbench.version import APP_VERSION
    request = normalize_request(request, source)
    calculation, hardware, provenance = _source(source)
    source_hash = fingerprint(calculation)

    def attach(result):
        result = deepcopy(result)
        result.update(application_version=APP_VERSION, source=deepcopy(provenance),
                      hardware=deepcopy(hardware), request=deepcopy(request))
        for point in result.get("points", []):
            # Keep a model-calibration identity distinct from saved CAD/hardware
            # evidence. The former must never be presented as a geometry hash.
            identity = point.setdefault("hardware", {})
            calibration = identity.pop("fingerprint", None)
            if calibration:
                identity["calibration_sha256"] = calibration
            identity["full_snapshot_sha256"] = hardware["sha256"]
        if result.get("analysis_mode") != MODE:
            raise ValueError("节流求解器返回了不匹配的分析模式。")
        json.dumps(result, allow_nan=False)
        return result

    def publish(status, partial):
        if progress:
            progress(status, attach(partial))

    result = calculate(deepcopy(calculation), deepcopy(request), progress=publish)
    if fingerprint(calculation) != source_hash:
        raise ValueError("节流计算期间来源硬件记录发生变化。")
    return attach(result)
