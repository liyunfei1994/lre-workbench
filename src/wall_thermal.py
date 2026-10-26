"""Bind solid thermal datasets to the canonical cooled wall, not fluid states."""
from __future__ import annotations
import json
import math
from wall_materials import get_wall_dataset

MANUFACTURING_STATES = ("unspecified", "powder_metallurgy_extruded_high_fe",
                        "qi2024_solution_cold_work_aged", "other")


def validate_manufacturing_state(state):
    if state not in MANUFACTURING_STATES:
        raise ValueError(f"Unknown wall manufacturing state: {state!r}")
    return state


def parse_material_zones(value):
    """Optional non-overlapping overrides; gaps inherit the default dataset.

    Coordinates are physical axial metres from the injector plane, independent
    of coolant flow direction. Extent validation uses the actual cooled mesh.
    """
    zones = (json.loads(value) if value.strip() else []) if isinstance(value, str) else (value or [])
    if not isinstance(zones, list):
        raise ValueError("Wall material zones must be a JSON list")
    parsed = []
    for item in zones:
        required = {"x_start_m", "x_end_m", "dataset_id", "manufacturing_state"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("Each wall zone requires x_start_m, x_end_m, dataset_id, manufacturing_state only")
        start, end = item["x_start_m"], item["x_end_m"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (start, end)) or not 0 <= start < end:
            raise ValueError("Wall zone bounds must be finite metres with 0 <= start < end")
        get_wall_dataset(item["dataset_id"])
        validate_manufacturing_state(item["manufacturing_state"])
        parsed.append(dict(item, x_start_m=float(start), x_end_m=float(end)))
    parsed.sort(key=lambda row: row["x_start_m"])
    if any(a["x_end_m"] > b["x_start_m"] for a, b in zip(parsed, parsed[1:])):
        raise ValueError("Wall material zones must not overlap")
    return parsed


def material_for_cell(x_m, dataset_id, manufacturing_state, zones):
    for zone in zones:
        if zone["x_start_m"] <= x_m < zone["x_end_m"]:
            return get_wall_dataset(zone["dataset_id"]), zone["manufacturing_state"]
    return get_wall_dataset(dataset_id), manufacturing_state


def audit_cell(cell, dataset, manufacturing_state):
    # Fin conductivity is evaluated at a representative rib temperature. Audit
    # that evaluation as well as both wall faces; no endpoint clipping of T.
    temperatures = [cell[key] for key in ("T_wall_hot_K", "T_wall_cold_K", "fin_reference_temperature_K")]
    report = dataset.audit_interval(min(temperatures), max(temperatures))
    match = ("unknown" if manufacturing_state == "unspecified" else "matched"
             if manufacturing_state == dataset.manufacturing_state.state_id else "mismatch")
    report.update(material_name=dataset.label, process=dataset.manufacturing_state.label,
                  declared_manufacturing_state=manufacturing_state,
                  manufacturing_match=match, x_m=cell["x_m"], s_m=cell["s_m"],
                  wall_temperature_range_K=sorted((cell["T_wall_cold_K"], cell["T_wall_hot_K"])),
                  fin_reference_temperature_K=cell["fin_reference_temperature_K"],
                  conductivity_WmK=cell["wall_conductivity_WmK"])
    return report


def material_summary(channels, wall_limit):
    rows = [c.get("material") for c in channels]
    complete = bool(rows) and all(isinstance(row, dict) for row in rows)
    rows = [row for row in rows if isinstance(row, dict)]
    outside = sum(row["status"] == "out_of_range" for row in rows)
    ids = sorted({row["dataset_id"] for row in rows})
    return dict(schema_version=1,
        status="missing_evidence" if not complete else "out_of_range" if outside else "within_range",
        dataset_ids=ids, datasets=[get_wall_dataset(key).metadata() for key in ids],
        total_cells=len(channels), audited_cells=len(rows), out_of_range_cell_count=outside,
        min_temperature_K=min((r["actual_temperature_range_K"][0] for r in rows), default=None),
        max_temperature_K=max((r["actual_temperature_range_K"][1] for r in rows), default=None),
        manufacturing_match="mismatch" if any(r["manufacturing_match"] == "mismatch" for r in rows)
            else "unknown" if not complete or any(r["manufacturing_match"] == "unknown" for r in rows) else "matched",
        manufacturing_match_basis="user_declared_process_category_not_specimen_or_hardware_qualification",
        cells=rows, wall_temperature_limit_K=wall_limit,
        wall_temperature_limit_status="exceeded" if any(c["T_wall_hot_K"] > wall_limit for c in channels) else "within_limit",
        active_properties=["conductivity"], inactive_properties=["heat_capacity", "reference_density"],
        out_of_range_policy="constant_endpoint_continuation_for_diagnostics_not_validated",
        conduction_model="integral_k_dT=q_wall_times_thickness",
        fin_model="straight_fin_with_k_at_mean_of_bulk_and_hot_wall_temperatures",
        outer_wall_model="not_in_series_with_liner; no_outer_shell_thermal_capacity_or_strength_validation")
