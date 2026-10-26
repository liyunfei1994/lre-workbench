"""One fail-closed numerical acceptance contract for solvers and consumers.

Physics/engineering feasibility is deliberately assessed elsewhere. Reading an
old record never changes it or invents iterations that were not recorded.
"""
from __future__ import annotations

from copy import deepcopy
import math

INNER_TOLERANCE = 1e-4
LAYER_NAMES = ("inner", "thermal", "preburner", "continuation")


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _check(residual, tolerance):
    r, t = _finite(residual), _finite(tolerance)
    if r is None or t is None or r < 0 or t < 0:
        return "unknown"
    return "pass" if r <= t else "fail"


def _layer(raw):
    if not isinstance(raw, dict):
        return {"status": "unknown", "termination_reason": "missing_layer"}
    layer = deepcopy(raw)
    if layer.get("enabled") is False and layer.get("status") == "disabled":
        return layer
    states = [_check(layer.get("residual"), layer.get("tolerance"))]
    for check in layer.get("checks", []):
        states.append(_check(check.get("residual"), check.get("tolerance")))
    if layer.get("status") == "fail" or "fail" in states:
        layer["status"] = "fail"
    elif layer.get("status") != "pass" or "unknown" in states or layer.get("enabled") is not True:
        layer["status"] = "unknown"
    else:
        layer["status"] = "pass"
    return layer


def numerical_convergence(results) -> dict:
    """Read a CycleResults, calculation dict, or serialized web result."""
    results = _get(results, "calculation", results)
    summary = _get(results, "summary", {}) or {}
    metadata = _get(results, "convergence", {}) or {}
    residual = _finite(summary.get("solver_residual_norm"))
    warning = bool(summary.get("solver_warning", 0))
    layers = {}
    complete = metadata.get("schema_version") == 1
    if complete:
        layers = {key: _layer((metadata.get("layers") or {}).get(key)) for key in LAYER_NAMES}
        inputs = _get(results, "inputs", {}) or {}
        def enabled(name, threshold=.5):
            value = _get(inputs, name)
            number = _finite(value)
            return value is True or (number is not None and (number > 0 if threshold is None else number >= threshold))
        # cooling_dp_auto is retained for old input serialization only.
        required = {"thermal": enabled("cooling_bartz_mode", None),
                    "preburner": enabled("use_target_gg_temp" if _get(inputs, "cycle_type") == "gg"
                                         else "use_target_preburner_temp")}
        for key, needed in required.items():
            if needed and layers[key].get("enabled") is not True:
                layers[key]["status"] = "fail"
                layers[key]["termination_reason"] = "required_layer_disabled"
        # A stand-alone inner solve is diagnostic, not a complete design solve.
        for key in ("inner", "continuation"):
            if layers[key].get("enabled") is not True:
                layers[key]["status"] = "unknown"
                layers[key]["termination_reason"] = "missing_full_evaluation"
        thermal = layers["thermal"]
        if thermal.get("enabled") is True and thermal["status"] == "pass":
            names = {check.get("name", check.get("key")) for check in thermal.get("checks", [])}
            from regen_coupling import THERMAL_FEEDBACK_SOURCE, COUPLING_CRITERIA, coupling_passed
            if thermal.get("source") == THERMAL_FEEDBACK_SOURCE:
                values = {c.get("name"): c.get("residual") for c in thermal.get("checks", [])}
                present = {name for name, _, _ in COUPLING_CRITERIA}.issubset(names)
                if not present or not coupling_passed(values):
                    thermal["status"] = "fail" if present else "unknown"
                    thermal["termination_reason"] = "missing_or_failed_coupled_feedback_checks"
            elif not ("temperature" in names
                    and names.intersection(("gamma", "gamma_relative_residual"))
                    and names.intersection(("molar_mass", "molar_mass_relative_residual"))):
                thermal["status"] = "unknown"
                thermal["termination_reason"] = "missing_feedback_checks"
        continuation = layers["continuation"]
        if continuation["status"] == "pass":
            ratio = _finite(continuation.get("reached_ratio", continuation.get("last_stage_ratio")))
            if ratio is None:
                continuation.update(status="unknown", termination_reason="missing_full_evaluation")
            elif ratio != 1.0 or continuation.get("termination_reason") != "target_reached":
                continuation.update(status="fail", termination_reason="target_not_reached")
        statuses = [layer["status"] for layer in layers.values()]
        # The independent summary cross-check also catches stale serialized flags.
        inner_check = _check(residual, layers["inner"].get("tolerance", INNER_TOLERANCE))
        statuses.append(inner_check)
        status = "fail" if warning or "fail" in statuses else (
            "pass" if all(s in ("pass", "disabled") for s in statuses) else "unknown")
        reason = "all_layers_converged" if status == "pass" else "incomplete_or_failed_layers"
    else:
        # Legacy snapshots lack the gamma/MW feedback and continuation evidence.
        # Known violations can be rejected, but small inner residuals cannot
        # certify the complete calculation.
        thermal = _finite(summary.get("tc_outer_residual_K"))
        known_failure = warning or (residual is not None and (residual < 0 or residual >= INNER_TOLERANCE))
        known_failure |= thermal is not None and (thermal < 0 or thermal > 5.0)
        for key in ("ox_preburner_temp_error_K", "fuel_preburner_temp_error_K", "gg_temp_outer_error_K"):
            error = _finite(summary.get(key))
            known_failure |= error is not None and abs(error) > 3.0
        status = "fail" if known_failure else "unknown"
        reason = "legacy_outer_or_inner_failure" if known_failure else "legacy_incomplete_evidence"
    return {"status": status,
            "label": {"pass": "数值收敛", "fail": "未收敛", "unknown": "收敛证据不完整"}[status],
            "residual": residual, "tolerance": layers.get("inner", {}).get("tolerance", INNER_TOLERANCE),
            "solver_warning": warning, "iterations": _finite(summary.get("solver_iterations")),
            "layers": layers, "complete_evidence": complete,
            "termination_reason": reason, "legacy": not complete}


def attach_convergence_status(results):
    """Finalize fresh solver evidence, retaining the inner warning separately."""
    summary = results.summary
    metadata = results.convergence
    # Idempotence: a previously aggregated warning must not taint a recheck.
    if "inner_solver_warning" not in summary:
        summary["inner_solver_warning"] = float(bool(summary.get("solver_warning", 0)))
    summary["solver_warning"] = summary["inner_solver_warning"]
    assessment = numerical_convergence(results)
    metadata["layers"] = assessment["layers"]
    metadata["status"] = assessment["status"]
    metadata["termination_reason"] = assessment["termination_reason"]
    summary["solver_warning"] = float(assessment["status"] == "fail")
    summary["solver_evidence_incomplete"] = float(assessment["status"] == "unknown")
    summary["numerical_converged"] = float(assessment["status"] == "pass")
    return results
