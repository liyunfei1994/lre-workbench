"""Immutable design-job evidence shared by steady review and pump exchange.

Hashes identify saved evidence, not engineering acceptance or a digital signature.
Readers never consult the project's current draft and never rewrite old results.
"""
from copy import deepcopy
import hashlib
import json


IDENTITY_KEYS = ("project_id", "revision", "record_id", "mode")
EVIDENCE_KEYS = ("submitted_inputs_sha256", "solver_inputs_sha256",
                 "calculation_inputs_sha256", "calculation_sha256",
                 "pump_outputs_sha256", "geometry_sha256", "steady_sha256")
LEGACY_IDENTITY_INPUTS = ("cycle_type", "propellant_pair", "thrust", "pc", "of_ratio")


def _dict(value):
    return value if isinstance(value, dict) else {}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def source_context(record):
    return {"project_id": record.get("project_id"), "revision": record.get("revision"),
            "record_id": record.get("id"), "mode": record.get("mode") or "concept_design"}


def _inputs(record):
    value = record.get("inputs")
    return _dict(json.loads(value) if isinstance(value, str) else value)


def _evidence(result, record, geometry):
    calc = _dict(result.get("calculation"))
    parts = _dict(calc.get("components"))
    steady = _dict(result.get("steady_design"))
    inputs = {"submitted": deepcopy(_inputs(record)),
              "normalized": deepcopy(_dict(result.get("submitted_inputs"))),
              "calculation": deepcopy(_dict(calc.get("inputs")))}
    outputs = {side: deepcopy(_dict(_dict(parts.get(key)).get("outputs")))
               for side, key in (("ox", "ox_pump"), ("fuel", "fuel_pump"))}
    return {"schema_version": 1, "source": source_context(record), "inputs": inputs,
            "submitted_inputs_sha256": fingerprint(inputs["submitted"]),
            "solver_inputs_sha256": fingerprint(inputs["normalized"]),
            "calculation_inputs_sha256": fingerprint(inputs["calculation"]),
            "calculation_sha256": fingerprint(calc),
            "pump_outputs_sha256": fingerprint(outputs), "pump_outputs": outputs,
            "geometry_sha256": {side: _dict(_dict(geometry.get("pumps")).get(side)).get("geometry_sha256")
                                for side in ("ox", "fuel")},
            "steady_sha256": fingerprint({k: v for k, v in steady.items() if k != "traceability"}),
            "steady": {"status": steady.get("status", "unavailable"),
                       "gates": deepcopy(steady.get("gates", [])),
                       "validation_status": steady.get("validation_status", "not_certified")}}


def _seal(value):
    return fingerprint({k: v for k, v in value.items() if k != "evidence_sha256"})


def capture_design_traceability(result, record):
    """Called once before a completed design job is persisted by its worker."""
    from webapp.pump_geometry import pump_geometry_view
    context = source_context(record)
    geometry = pump_geometry_view(result, context["record_id"] or "", source_context=context)
    result["pump_geometry"] = geometry
    evidence = _evidence(result, record, geometry)
    conflicts = [side + " 泵几何与保存设计证据冲突。" for side, pump in geometry["pumps"].items()
                 if pump.get("export_allowed") is False]
    evidence.update(status="conflict" if conflicts else "verified", issues=conflicts, capture="design_job_completion")
    evidence["evidence_sha256"] = _seal(evidence)
    result["design_traceability"] = evidence
    # Raw result downloads retain the same exchange provenance as the API view.
    geometry["traceability"] = deepcopy(evidence)
    return result


def design_traceability_view(result, record, geometry):
    result = _dict(result)
    value = _evidence(result, record, geometry)
    saved = result.get("design_traceability")
    issues = []
    if saved is not None:
        saved = _dict(saved)
        if saved.get("schema_version") != 1 or saved.get("evidence_sha256") != _seal(saved):
            issues.append("保存的方案追溯证据校验失败。")
        for key in IDENTITY_KEYS:
            if _dict(saved.get("source")).get(key) != value["source"].get(key):
                issues.append("追溯来源与所选方案记录不一致：" + key)
        for key in EVIDENCE_KEYS:
            if saved.get(key) != value.get(key):
                issues.append("保存证据与本记录不一致：" + key)
    # Captured stages have independently sealed hashes: normalization and solved
    # variables need not equal the original request. Legacy records lack seals;
    # compare only immutable declarations actually present in both snapshots.
    if saved is None:
        submitted = value["inputs"]["submitted"]
        used = value["inputs"]["normalized"] or value["inputs"]["calculation"]
        mismatches = [key for key in LEGACY_IDENTITY_INPUTS
                      if key in submitted and key in used and submitted[key] != used[key]]
        if mismatches:
            issues.append("求解设计目标与任务提交输入不一致：" + "、".join(mismatches))
    for side, pump in _dict(geometry.get("pumps")).items():
        if _dict(pump).get("export_allowed") is False:
            issues.append(("液氧泵" if side == "ox" else "燃料泵") + "几何来源或系统点存在冲突。")
    status = "conflict" if issues else "verified" if saved is not None else "legacy"
    value.update(status=status, issues=issues, capture="saved_evidence_audit" if saved is not None else "legacy_read_only_projection",
                 note="历史记录未保存完成时的追溯证据；当前仅据该记录投影，不补入当前草稿。" if saved is None else
                      "追溯校验只确认记录归属和证据一致性，不代表稳态约束或部件详细设计通过。")
    value["evidence_sha256"] = saved.get("evidence_sha256") if saved is not None else None
    return value


def attach_design_traceability(row):
    """Attach a read-only common lineage to both API views, leaving result intact."""
    geometry = row["pump_geometry"]
    trace = design_traceability_view(row.get("result"), row, geometry)
    row["design_traceability"] = trace
    geometry["traceability"] = deepcopy(trace)
    row["steady_design"]["traceability"] = deepcopy(trace)
    if trace["status"] == "conflict":
        for pump in geometry["pumps"].values():
            pump["export_allowed"] = False
        for handoff in geometry.get("design_handoff", {}).get("pumps", {}).values():
            handoff["status"] = "conflict"
            handoff.setdefault("conflicts", []).extend(trace["issues"])
        report = row["steady_design"]
        report["status"] = "blocked"
        gate = {"id": "design_traceability", "label": "方案级追溯", "status": "fail",
                "detail": "；".join(trace["issues"]), "target": "models"}
        report.setdefault("gates", []).append(gate)
        report.setdefault("actions", []).append(deepcopy(gate))
    return row
