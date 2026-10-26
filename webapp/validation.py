"""Versioned validation requests, separate from immutable design calculations."""
from copy import deepcopy
import hashlib
import json
from functools import lru_cache


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def capabilities():
    from webapp.thermo_compare import comparison_capabilities
    from webapp.validation_checks import property_capabilities
    return comparison_capabilities() | property_capabilities()


def normalize_request(value, source):
    if not isinstance(value, dict):
        raise ValueError("校核请求必须是对象。")
    allowed = {"source_job_id", "thermochemistry", "properties", "experiment"}
    if set(value) - allowed:
        raise ValueError("校核请求含未知字段。")
    normalized = {"source_job_id": source["id"]}
    if value.get("thermochemistry") is not None:
        from webapp.thermo_compare import normalize_request as normalize_thermo
        submitted = value["thermochemistry"]
        if not isinstance(submitted, dict):
            raise ValueError("热力学对比请求必须是对象。")
        source_dataset = source["inputs"].get("methane_thermo_dataset", "gri30")
        if submitted.get("methane_thermo_dataset", source_dataset) != source_dataset:
            raise ValueError("生产热化学数据必须与来源计算记录一致。")
        thermo = normalize_thermo(submitted | {"methane_thermo_dataset": source_dataset})
        pair = source["inputs"].get("propellant_pair", "lox_methane")
        if thermo["propellant_pair"] != pair:
            raise ValueError("校核推进剂必须与来源计算记录一致；请先选择对应的计算记录。")
        normalized["thermochemistry"] = thermo
    if value.get("properties") is not None:
        from webapp.validation_checks import validate_property_request
        normalized["properties"] = validate_property_request(value["properties"])
    if value.get("experiment") is not None:
        from webapp.validation_checks import validate_experiment_request
        normalized["experiment"] = validate_experiment_request(value["experiment"])
    # No CEA is required for conservation-only or independent property checks.
    json.dumps(normalized, allow_nan=False)
    return normalized


def calculate_validation(request, source, progress=None):
    from webapp.validation_checks import conservation_checks, property_checks, experiment_checks
    from webapp.solver import plain
    original = source["result"]
    result = {"schema_version": 1, "source": {
        "job_id": source["id"], "project_id": source["project_id"],
        "revision": source["revision"], "created": source["created"],
        "inputs_sha256": fingerprint(source["inputs"]),
        "result_sha256": fingerprint(original),
        "application_version": original.get("application_version"),
        "environment": original.get("environment"),
    }, "request": deepcopy(request), "conservation": conservation_checks(original),
        "thermochemistry": None, "properties": None,
        "experiment": experiment_checks(original, request.get("experiment"))}
    if request.get("thermochemistry") is not None:
        from webapp.thermo_compare import compare_thermochemistry
        result["thermochemistry"] = compare_thermochemistry(request["thermochemistry"], progress)
    if request.get("properties") is not None:
        result["properties"] = property_checks(request["properties"])
    missing = []
    result = plain(result, missing)
    result["nonfinite_fields"] = missing
    json.dumps(result, allow_nan=False)
    return result
