"""Algebraically eliminated, four-branch FFSC feed-pressure equations.

Topology is an explicit model assumption: all fuel passes through regenerative
cooling before its split; oxygen splits after its main pump.  Passive valves
only dissipate pressure.  No unmodelled booster pump or alternative tap exists.

For specified total flows and preburner mixture ratios, ten unknowns (four
branch flows, two source pressures, four extra valve losses) satisfy two source
mass balances, two mixture-ratio equations, four branch pressure equations and
two minimum-head complementarity conditions.  Analytical elimination is exact;
the caller must feed the resulting pump heads and inlet enthalpies back into
the cycle power/thermal equations at EVERY nonlinear residual evaluation.

Frozen coefficients describe lumped dense-fluid hydraulic resistances, not
validated injector maps.  Pump/turbine maps and valve area characteristics are
absent: frozen-resistance runs remain inverse-control/minimum-head rematching.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy


SCHEMA_VERSION = 1
TOPOLOGY = "ffsc_all_fuel_cooled_then_split_v1"
LOSS_MODEL = "delta_p=K*mass_flow^2/upstream_density"
BRANCHES = (
    ("fuel_to_opb", "CH₄ → 富氧预燃室", "fuel", "fuel_after_cooling", "ox_preburner"),
    ("fuel_to_fpb", "CH₄ → 富燃预燃室", "fuel", "fuel_after_cooling", "fuel_preburner"),
    ("ox_to_opb", "LOX → 富氧预燃室", "ox", "lox_after_pump", "ox_preburner"),
    ("ox_to_fpb", "LOX → 富燃预燃室", "ox", "lox_after_pump", "fuel_preburner"),
)
BRANCH_IDS = tuple(row[0] for row in BRANCHES)


def _finite(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if (not math.isfinite(value) or (positive and value <= 0)
            or (nonnegative and value < 0)):
        raise ValueError(f"{name} is outside its physical domain")
    return value


def split_flows(total_fuel, total_ox, mr_ox, mr_fuel):
    """Solve mass2+MR2 without silently clipping incompatible mixture ratios."""
    fuel = _finite(total_fuel, "total_fuel", positive=True)
    ox = _finite(total_ox, "total_ox", positive=True)
    mr_o = _finite(mr_ox, "mr_ox", positive=True)
    mr_f = _finite(mr_fuel, "mr_fuel", positive=True)
    overall = ox / fuel
    if not mr_f < overall < mr_o:
        raise ValueError("Four positive FFSC branches require MR_fuel < overall O/F < MR_ox")
    fuel_opb = (ox - mr_f * fuel) / (mr_o - mr_f)
    fuel_fpb = fuel - fuel_opb
    ox_fpb = mr_f * fuel_fpb
    return dict(zip(BRANCH_IDS, (fuel_opb, fuel_fpb, ox - ox_fpb, ox_fpb)))


def validate_hardware(snapshot):
    """Return a checked copy; incomplete historical snapshots cannot be frozen."""
    if not isinstance(snapshot, dict):
        raise ValueError("A four-branch hardware snapshot is required")
    if snapshot.get("schema_version") != SCHEMA_VERSION or snapshot.get("topology") != TOPOLOGY:
        raise ValueError("Unsupported four-branch hardware snapshot version or topology")
    if snapshot.get("loss_model") != LOSS_MODEL:
        raise ValueError("Unsupported frozen hydraulic loss law")
    rows = snapshot.get("branches")
    if not isinstance(rows, dict) or set(rows) != set(BRANCH_IDS):
        raise ValueError("Hardware snapshot must contain exactly four named branches")
    result = deepcopy(snapshot)
    for branch_id, _, propellant, source, target in BRANCHES:
        row = result["branches"][branch_id]
        if not isinstance(row, dict) or row.get("source") != source or row.get("target") != target:
            raise ValueError(f"{branch_id}: inconsistent source/target topology")
        if row.get("propellant") != propellant:
            raise ValueError(f"{branch_id}: inconsistent propellant")
        for key in ("reference_flow_kg_s", "reference_density_kg_m3", "K_injector"):
            row[key] = _finite(row.get(key), f"{branch_id}.{key}", positive=True)
        row["K_line"] = _finite(row.get("K_line"), f"{branch_id}.K_line", nonnegative=True)
    return result


def solve_design(*, total_fuel, total_ox, mr_ox, mr_fuel, p_pre_ox, p_pre_fuel,
                 injector_ratio, line_dp, densities=None, temperatures=None,
                 fixed_hardware=None, flows_override=None, allow_unclosed_flows=False):
    """Solve the reduced network; pressures are SI absolute and flows kg/s.

    ``densities`` and ``temperatures`` are keyed ``fuel``/``ox`` and refer to
    the two upstream tap states. ``flows_override`` uses the four BRANCH_IDS
    and disables MR constraints, for the cycle's free-split mode. ``line_dp``
    is the design baseline branch line/full-open-valve loss, not extra control
    throttling.  Offdesign uses only frozen K and current upstream densities;
    the supplied design pressure-loss ratio and line_dp are then ignored.
    ``allow_unclosed_flows`` is only for expanded-system trial evaluations;
    it never bypasses the returned independent mass-balance failure status.
    """
    total_fuel = _finite(total_fuel, "total_fuel", positive=True)
    total_ox = _finite(total_ox, "total_ox", positive=True)
    p_pre = {"ox_preburner": _finite(p_pre_ox, "p_pre_ox", positive=True),
             "fuel_preburner": _finite(p_pre_fuel, "p_pre_fuel", positive=True)}
    if flows_override is None:
        flows = split_flows(total_fuel, total_ox, mr_ox, mr_fuel)
        closure = "target_mixture_ratios"
    else:
        if not isinstance(flows_override, dict) or set(flows_override) != set(BRANCH_IDS):
            raise ValueError("flows_override must contain exactly four branch flows")
        flows = {key: _finite(flows_override[key], key, positive=True) for key in BRANCH_IDS}
        closure = "prescribed_split"
        for kind, total in (("fuel", total_fuel), ("ox", total_ox)):
            difference = sum(flows[b[0]] for b in BRANCHES if b[2] == kind) - total
            if not allow_unclosed_flows and abs(difference) > max(1e-8, total * 1e-9):
                raise ValueError(f"{kind}: explicit branch flows violate source mass conservation")
    density = {}
    temperature = {}
    for kind in ("fuel", "ox"):
        if densities is not None and densities.get(kind) is not None:
            density[kind] = _finite(densities[kind], f"{kind} density", positive=True)
        if temperatures is not None and temperatures.get(kind) is not None:
            temperature[kind] = _finite(temperatures[kind], f"{kind} temperature", positive=True)
    hardware = validate_hardware(fixed_hardware) if fixed_hardware is not None else None
    if hardware is not None and len(density) != 2:
        raise ValueError("Frozen-resistance rematching requires both current upstream densities")
    if hardware is None:
        injector_ratio = _finite(injector_ratio, "injector_ratio", positive=True)
        line_dp = _finite(line_dp, "line_dp", nonnegative=True)

    rows = []
    for branch_id, label, kind, source, target in BRANCHES:
        flow = flows[branch_id]
        if hardware is None:
            dp_inj = injector_ratio * p_pre[target]
            dp_line = line_dp
        else:
            coefficient = hardware["branches"][branch_id]
            dp_inj = coefficient["K_injector"] * flow ** 2 / density[kind]
            dp_line = coefficient["K_line"] * flow ** 2 / density[kind]
        rows.append({
            "id": branch_id, "label": label, "propellant": kind,
            "source": source, "target": target, "mass_flow_kg_s": flow,
            "temperature_K": temperature.get(kind), "density_kg_m3": density.get(kind),
            "destination_pressure_Pa": p_pre[target],
            "line_drop_Pa": dp_line, "injector_drop_Pa": dp_inj,
            "required_source_pressure_Pa": p_pre[target] + dp_line + dp_inj,
            "loss_source": "frozen_design_resistance" if hardware else "specified_design_pressure_budget",
            "temperature_location": "source_tap",
        })
    taps = {kind: max(row["required_source_pressure_Pa"] for row in rows
                      if row["propellant"] == kind) for kind in ("fuel", "ox")}
    for row in rows:
        row["source_pressure_Pa"] = taps[row["propellant"]]
        row["valve_drop_Pa"] = taps[row["propellant"]] - row["required_source_pressure_Pa"]
        row["margin_before_valve_Pa"] = row["valve_drop_Pa"]
        row["active_minimum_head_constraint"] = row["valve_drop_Pa"] == 0

    snapshot = hardware
    if snapshot is None and len(density) == 2:
        snapshot = {"schema_version": SCHEMA_VERSION, "topology": TOPOLOGY,
                    "loss_model": LOSS_MODEL, "branches": {},
                    "source": "inferred_from_this_design_point",
                    "extra_valve_throttling_frozen": False,
                    "valve_characteristic_available": False,
                    "pump_turbine_maps_available": False}
        for row in rows:
            snapshot["branches"][row["id"]] = {
                "propellant": row["propellant"], "source": row["source"], "target": row["target"],
                "reference_flow_kg_s": row["mass_flow_kg_s"],
                "reference_density_kg_m3": row["density_kg_m3"],
                "K_injector": row["injector_drop_Pa"] * row["density_kg_m3"] / row["mass_flow_kg_s"] ** 2,
                "K_line": row["line_drop_Pa"] * row["density_kg_m3"] / row["mass_flow_kg_s"] ** 2,
                "reference_extra_valve_drop_Pa": row["valve_drop_Pa"],
            }
    result = {
        "schema_version": SCHEMA_VERSION, "topology": TOPOLOGY,
        "mode": "fixed_resistance" if hardware else "design",
        "flow_closure": closure, "tap_pressures_Pa": taps, "branches": rows,
        "hardware_snapshot": snapshot,
        "source_totals_kg_s": {"fuel": total_fuel, "ox": total_ox},
        "design_loss_inputs": {"injector_ratio": injector_ratio, "line_dp_Pa": line_dp} if hardware is None else None,
        "target_mixture_ratios": {"ox": mr_ox, "fuel": mr_fuel} if flows_override is None else None,
        "pressure_reserve_status": "not_demonstrated",
        "solver": "exact_algebraic_elimination",
        "unknown_count": 10 if flows_override is None else 6,
        "equation_count": 10 if flows_override is None else 6,
        "equations": (["sum(m_fuel_branches)=m_fuel", "sum(m_ox_branches)=m_ox",
                       "m_ox_OPB=MR_OPB*m_fuel_OPB", "m_ox_FPB=MR_FPB*m_fuel_FPB"]
                      if flows_override is None else []) + [
                          "p_source-p_PB-dp_line-dp_injector-dp_extra_valve=0 (four branches)",
                          "dp_extra_valve>=0; min(dp_extra_valve_of_each_source)=0 (two sources)"],
        "assumptions": [
            "All fuel passes through cooling before splitting; oxygen splits after its main pump.",
            "Minimum required source head; at least one active branch per source has no extra throttling.",
            "Baseline line/full-open-valve losses and extra control throttling are separate.",
            "No physical valve opening is inferred without an area/flow characteristic.",
            "Frozen resistance uses current upstream density and a lumped dense-fluid loss law.",
            "Pump/turbine maps and valve travel limits are absent; this is not a validated fixed-engine envelope.",
        ],
    }
    checked = audit_network(result, total_fuel=total_fuel, total_ox=total_ox,
                            mr_ox=mr_ox if flows_override is None else None,
                            mr_fuel=mr_fuel if flows_override is None else None)
    result.update(checked)
    return result


def audit_network(network, *, total_fuel, total_ox, mr_ox=None, mr_fuel=None):
    """Recompute conservation from recorded branch states, never cached residuals.

    The result is suitable for saved-data validation and counterexample tests.
    This checks algebraic model consistency, not hardware validation.
    """
    errors = []
    try:
        total = {"fuel": _finite(total_fuel, "total_fuel", positive=True),
                 "ox": _finite(total_ox, "total_ox", positive=True)}
        raw_rows = network.get("branches")
        if not isinstance(raw_rows, list) or len(raw_rows) != 4:
            raise ValueError("Expected four branch rows")
        rows = deepcopy(raw_rows)
        if {row.get("id") for row in rows} != set(BRANCH_IDS):
            raise ValueError("Invalid or repeated branch identifiers")
        by_id = {row["id"]: row for row in rows}
        pressure_scale = max(_finite(row.get("source_pressure_Pa"), "source_pressure", positive=True)
                             for row in rows)
        p_tolerance = max(1.0, pressure_scale * 1e-9)
        m_tolerance = max(1e-8, max(total.values()) * 1e-9)
        mass = {}
        complementarity = {}
        source_match = {}
        p_residuals = []
        hydraulic_residuals = []
        snapshot = network.get("hardware_snapshot")
        coefficients = validate_hardware(snapshot)["branches"] if snapshot is not None else None
        for branch_id, _, kind, source, target in BRANCHES:
            row = by_id[branch_id]
            if row.get("propellant") != kind or row.get("source") != source or row.get("target") != target:
                raise ValueError(f"{branch_id}: inconsistent topology")
            for key in ("source_pressure_Pa", "destination_pressure_Pa", "mass_flow_kg_s"):
                row[key] = _finite(row.get(key), key, positive=True)
            for key in ("line_drop_Pa", "injector_drop_Pa"):
                row[key] = _finite(row.get(key), key, nonnegative=True)
            row["valve_drop_Pa"] = _finite(row.get("valve_drop_Pa"), "valve_drop_Pa")
            residual = row["source_pressure_Pa"] - row["destination_pressure_Pa"] - row["line_drop_Pa"] - row["injector_drop_Pa"] - row["valve_drop_Pa"]
            row["pressure_residual_Pa"] = residual
            row["required_source_pressure_Pa"] = row["destination_pressure_Pa"] + row["line_drop_Pa"] + row["injector_drop_Pa"]
            row["margin_before_valve_Pa"] = row["source_pressure_Pa"] - row["required_source_pressure_Pa"]
            row["active_minimum_head_constraint"] = abs(row["valve_drop_Pa"]) <= p_tolerance
            # Only the tap and PB are thermodynamic states. The intermediate
            # pressure is a lumped bookkeeping value, not a resolved valve node.
            row["pressure_nodes"] = [
                {"id": "source_tap", "label": "取流点", "pressure_Pa": row["source_pressure_Pa"],
                 "kind": "recorded_model_state"},
                {"id": "injector_upstream", "label": "喷注器前（集总扣账）",
                 "pressure_Pa": row["source_pressure_Pa"] - row["line_drop_Pa"] - row["valve_drop_Pa"],
                 "kind": "derived_pressure_accounting"},
                {"id": "preburner", "label": "预燃室", "pressure_Pa": row["destination_pressure_Pa"],
                 "kind": "recorded_model_state"},
            ]
            row["pressure_nodes_note"] = "中间值仅扣除基础管阀损失与附加调节损失；未解析阀门几何位置、局部速度或中间温度。"
            p_residuals.append(abs(residual))
            if coefficients is not None:
                rho = _finite(row.get("density_kg_m3"), "density_kg_m3", positive=True)
                expected_inj = coefficients[branch_id]["K_injector"] * row["mass_flow_kg_s"] ** 2 / rho
                expected_line = coefficients[branch_id]["K_line"] * row["mass_flow_kg_s"] ** 2 / rho
                loss_error = max(abs(row["injector_drop_Pa"] - expected_inj), abs(row["line_drop_Pa"] - expected_line))
            else:
                design_inputs = network.get("design_loss_inputs") or {}
                ratio = _finite(design_inputs.get("injector_ratio"), "injector_ratio", positive=True)
                expected_line = _finite(design_inputs.get("line_dp_Pa"), "line_dp_Pa", nonnegative=True)
                loss_error = max(abs(row["injector_drop_Pa"] - ratio * row["destination_pressure_Pa"]), abs(row["line_drop_Pa"] - expected_line))
            row["loss_law_residual_Pa"] = loss_error
            hydraulic_residuals.append(loss_error)
            if row["valve_drop_Pa"] < -p_tolerance:
                errors.append(f"{branch_id}: passive valve cannot add pressure")
        for kind in ("fuel", "ox"):
            group = [row for row in rows if row["propellant"] == kind]
            mass[kind] = sum(row["mass_flow_kg_s"] for row in group) - total[kind]
            complementarity[kind] = min(row["valve_drop_Pa"] for row in group)
            tap = _finite(network["tap_pressures_Pa"][kind], f"{kind} tap pressure", positive=True)
            source_match[kind] = max(abs(row["source_pressure_Pa"] - tap) for row in group)
            if abs(complementarity[kind]) > p_tolerance:
                errors.append(f"{kind}: minimum-head complementarity not closed")
            if source_match[kind] > p_tolerance:
                errors.append(f"{kind}: branches do not share the recorded tap pressure")
            for row in group:
                row["mass_residual_kg_s"] = mass[kind]
        mr_residual = {}
        if mr_ox is not None:
            mr_residual["ox"] = by_id["ox_to_opb"]["mass_flow_kg_s"] - _finite(mr_ox, "mr_ox", positive=True) * by_id["fuel_to_opb"]["mass_flow_kg_s"]
        if mr_fuel is not None:
            mr_residual["fuel"] = by_id["ox_to_fpb"]["mass_flow_kg_s"] - _finite(mr_fuel, "mr_fuel", positive=True) * by_id["fuel_to_fpb"]["mass_flow_kg_s"]
        max_p = max(p_residuals + hydraulic_residuals + list(source_match.values()) + [abs(v) for v in complementarity.values()])
        max_m = max([abs(v) for v in mass.values()] + [abs(v) for v in mr_residual.values()])
        passed = not errors and max_p <= p_tolerance and max_m <= m_tolerance
        for row in rows:
            row["status"] = "pass" if (abs(row["pressure_residual_Pa"]) <= p_tolerance
                and abs(row["mass_residual_kg_s"]) <= m_tolerance
                and row["valve_drop_Pa"] >= -p_tolerance
                and row["loss_law_residual_Pa"] <= p_tolerance
                and source_match[row["propellant"]] <= p_tolerance) else "fail"
        return {"status": "pass" if passed else "fail", "branches": rows,
                "max_pressure_residual_Pa": max_p, "max_mass_residual_kg_s": max_m,
                "pressure_tolerance_Pa": p_tolerance, "mass_tolerance_kg_s": m_tolerance,
                "source_mass_residuals_kg_s": mass, "mixture_ratio_residuals_kg_s": mr_residual,
                "minimum_valve_residuals_Pa": complementarity, "errors": errors}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"status": "fail", "max_pressure_residual_Pa": None,
                "max_mass_residual_kg_s": None, "errors": [str(exc)]}


def pressure_contract(network, states, inputs, cooling_component=None):
    """Expose pressure definitions and independently check saved state links.

    This report distinguishes an imposed loss budget, a solved pressure, and
    demonstrated component capability. It performs no thermodynamic calculation
    and never turns the absence of a pump map into a successful capacity check.
    Dictionaries from JSON and live dataclass states use the same code path.
    """
    def get(value, key, default=None):
        return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)

    missing = []
    def number(value, name, *, positive=False, nonnegative=False):
        try:
            return _finite(value, name, positive=positive, nonnegative=nonnegative)
        except ValueError:
            missing.append(name)
            return None

    def pressure(name):
        return number(get(states.get(name), "pressure"), f"states.{name}.pressure", positive=True)

    def difference(a, b):
        return a - b if a is not None and b is not None else None

    def ratio(a, b):
        return a / b if a is not None and b is not None and b > 0 else None

    def product(a, b):
        return a * b if a is not None and b is not None else None

    def status(residual, tolerance, *, valid=True):
        return "unavailable" if residual is None or tolerance is None else "pass" if valid and abs(residual) <= tolerance else "fail"

    states = states if isinstance(states, Mapping) else {}
    network = network if isinstance(network, Mapping) else {}
    mode = network.get("mode")
    pc = pressure("main_chamber")
    gas_paths = []
    for side, label in (("ox", "富氧燃气"), ("fuel", "富燃燃气")):
        p_in = pressure(f"{side}_preburner_outlet")
        p_out = pressure(f"{side}_turbine_exhaust")
        specified_ratio = number(get(inputs, f"{side}_turbine_exhaust_dp_ratio"),
                                 f"inputs.{side}_turbine_exhaust_dp_ratio", nonnegative=True)
        dp = difference(p_out, pc)
        budget = product(specified_ratio, pc)
        residual = difference(dp, budget)
        tolerance = max(1.0, max(p_in, p_out, pc) * 1e-9) if None not in (p_in, p_out, pc) else None
        valid = None not in (p_in, p_out, pc) and p_in >= p_out >= pc
        gas_paths.append(dict(
            id=side, label=label, preburner_pressure_Pa=p_in,
            turbine_inlet_pressure_Pa=p_in, turbine_outlet_pressure_Pa=p_out,
            chamber_pressure_Pa=pc, turbine_pressure_ratio=ratio(p_in, p_out),
            preburner_chamber_pressure_ratio=ratio(p_in, pc),
            hot_gas_drop_Pa=dp, hot_gas_drop_ratio=ratio(dp, pc),
            specified_hot_gas_drop_ratio=specified_ratio, hot_gas_budget_drop_Pa=budget,
            pressure_residual_Pa=residual, pressure_tolerance_Pa=tolerance,
            status=status(residual, tolerance, valid=valid),
            loss_source="specified_design_pressure_budget",
            inlet_link="preburner_outlet_equals_turbine_inlet_no_separate_duct_loss",
            detail="预燃室出口直接作为涡轮入口，未另计两者间管路压降；涡轮排气至主室为热气喷注段预算，固定四支路阻力扫描中仍沿用该预算。",
        ))

    cooling_budget = number(get(inputs, "cooling_dp_pa"), "inputs.cooling_dp_pa", nonnegative=True)
    profile = get(cooling_component, "profile", {}) or {}
    outputs = get(cooling_component, "outputs", {}) or {}
    unified = get(profile, "version") == 1 and get(profile, "model_id") == "regen_quasi1d_ph_v1"
    recorded_drop = None
    if unified:
        cooling_source = "unified_cooling_profile"
        recorded_drop = number(get(get(profile, "summary", {}) or {}, "dp_total_Pa"),
                               "cooling.profile.summary.dp_total_Pa", nonnegative=True)
    elif profile:
        cooling_source = "unavailable"
        missing.append("cooling.profile.supported_model")
    elif cooling_component is not None:
        source = get(outputs, "delta_P_source")
        cooling_source = ("current_hydraulic_model" if source == 1 else
                          "specified_design_budget" if source == 0 else "unavailable")
        if cooling_source != "unavailable":
            recorded_mpa = number(get(outputs, "delta_P_MPa"), "cooling.outputs.delta_P_MPa", nonnegative=True)
            recorded_drop = product(recorded_mpa, 1e6)
        else:
            missing.append("cooling.outputs.delta_P_source")
    else:
        # Compatibility for callers auditing old records without a component.
        # New cycle records always pass the component actually used by the solve.
        cooling_source = "current_hydraulic_model" if bool(get(inputs, "cooling_dp_auto", False)) else "specified_design_budget"
    cooling_auto = cooling_source in ("unified_cooling_profile", "current_hydraulic_model")
    pumps = []
    for side, prefix, label in (("ox", "lox", "液氧主泵"), ("fuel", "fuel", "甲烷主泵")):
        p_in = pressure(f"{prefix}_pump_inlet")
        p_out = pressure(f"{prefix}_after_pump")
        tap_key = "fuel_after_cooling" if side == "fuel" else "lox_after_pump"
        tap_state = pressure(tap_key)
        tap_required = number((network.get("tap_pressures_Pa") or {}).get(side), f"network.tap_pressures_Pa.{side}", positive=True)
        cooling_drop = difference(p_out, tap_state) if side == "fuel" else 0.0
        adopted_cooling_drop = (recorded_drop if cooling_component is not None else
                                cooling_drop if cooling_auto else cooling_budget) if side == "fuel" else 0.0
        required_out = tap_required + adopted_cooling_drop if None not in (tap_required, adopted_cooling_drop) else None
        residual = difference(p_out, required_out)
        tolerance = max(1.0, max(p_out, required_out) * 1e-9) if None not in (p_out, required_out) else None
        pumps.append(dict(id=side, label=label, inlet_pressure_Pa=p_in,
            required_outlet_pressure_Pa=required_out, required_rise_Pa=difference(required_out, p_in),
            model_outlet_pressure_Pa=p_out, source_tap_pressure_Pa=tap_state,
            required_tap_pressure_Pa=tap_required, cooling_drop_Pa=cooling_drop,
            adopted_cooling_drop_Pa=adopted_cooling_drop,
            cooling_drop_source=cooling_source if side == "fuel" else "not_in_oxygen_path",
            cooling_budget_residual_Pa=difference(cooling_drop, cooling_budget) if side == "fuel" and cooling_source == "specified_design_budget" else None,
            pressure_residual_Pa=residual, pressure_tolerance_Pa=tolerance,
            pressure_link_status=status(residual, tolerance,
                valid=None not in (p_in, p_out, cooling_drop) and p_out >= p_in and cooling_drop >= 0),
            capacity_status="not_verified", available_outlet_pressure_Pa=None,
            available_pressure_margin_Pa=None,
            detail="所需出口压力反馈主泵功；输入转速或按比转速反推转速不构成固定泵扬程—流量能力证据。"))

    roles = []
    definitions = [
        ("preburner_injector_dp_ratio", "预燃室推进剂喷注压降比 Δp/pPB", "1", "specified_design_budget" if mode == "design" else "inactive_with_frozen_resistance",
         "设计点按每条支路目的预燃室压力乘该比例；固定阻力模式由保存的 K 与当前流量、密度求压降，不使用此比例重算。"),
        ("valve_line_dp_ratio", "支路基础管阀压降比 Δp/pc", "1", "specified_design_budget" if mode == "design" else "inactive_with_frozen_resistance",
         "设计点的基础管路/全开阀预算；不包含另外求出的附加调节阀压降，也不包含上游冷却压降。"),
        ("ox_turbine_exhaust_dp_ratio", "富氧热气喷注段压降比 Δp/pc", "1", "specified_design_budget",
         "富氧涡轮排气进入主室的热气侧预算，并非液氧直接进入主室的喷注损失。"),
        ("fuel_turbine_exhaust_dp_ratio", "富燃热气喷注段压降比 Δp/pc", "1", "specified_design_budget",
         "富燃涡轮排气进入主室的热气侧预算，并非液甲烷直接进入主室的喷注损失。"),
        ("cooling_dp_pa", "给定再生冷却总压降", "Pa", "inactive_with_hydraulic_model" if cooling_auto else "specified_design_budget" if cooling_source == "specified_design_budget" else "not_recorded",
         "统一沿程模式采用本次冷却剖面的压降，该预算输入不生效；给定预算模式采用组件记录的压降，不是初值。"),
    ]
    for key, label, unit, role, detail in definitions:
        roles.append(dict(key=key, label=label, value=number(get(inputs, key), f"inputs.{key}", nonnegative=True),
                          unit=unit, role=role, source_status="unverified_engineering_assumption", detail=detail))
    branch_links = []
    for branch in network.get("branches") or []:
        source = pressure(branch.get("source"))
        target_key = "ox_preburner_outlet" if branch.get("target") == "ox_preburner" else "fuel_preburner_outlet"
        target = pressure(target_key)
        branch_state = pressure("branch_" + str(branch.get("id")))
        recorded_source = number(branch.get("source_pressure_Pa"), f"branches.{branch.get('id')}.source_pressure_Pa", positive=True)
        recorded_target = number(branch.get("destination_pressure_Pa"), f"branches.{branch.get('id')}.destination_pressure_Pa", positive=True)
        residuals = [difference(recorded_source, source), difference(recorded_target, target), difference(branch_state, target)]
        max_error = max(abs(v) for v in residuals) if all(v is not None for v in residuals) else None
        tolerance = max(1.0, max(source, target) * 1e-9) if None not in (source, target) else None
        branch_links.append(dict(id=branch.get("id"), label=branch.get("label"),
            source_state_residual_Pa=residuals[0], preburner_state_residual_Pa=residuals[1],
            branch_outlet_state_residual_Pa=residuals[2], max_pressure_residual_Pa=max_error,
            pressure_tolerance_Pa=tolerance, status=status(max_error, tolerance)))
    link_states = ([r["status"] for r in gas_paths] + [r["pressure_link_status"] for r in pumps]
                   + [r["status"] for r in branch_links])
    return dict(schema_version=1, cooling_audit_version=2, mode=mode,
        pressure_convention=dict(kind="lumped_absolute_pressure", label="集总绝对压力 / Pa",
            detail="内部流路宏观动能变化被忽略；涡轮压比使用模型入口/出口绝对压力。尚未独立解析各截面总压与静压，不能自动解释为实机总压压比。"),
        parameter_roles=roles, gas_paths=gas_paths, branch_state_links=branch_links,
        pressure_link_status="fail" if "fail" in link_states else "unavailable" if "unavailable" in link_states else "pass",
        pump_capacity=dict(status="not_verified", pumps=pumps,
            detail="当前求得满足损失与流量约束的最低所需泵压；缺少固定泵扬程/效率图、涡轮流通图和阀门特性，未证明固定硬件可提供该压力。"),
        budget_evidence=dict(status="unverified_for_raptor", source="project_engineering_assumptions",
            detail="现有压降数值未附对应 Raptor 3 机型、工况、测点和原文页码证据；方程依据或文献中的其他机型参数不能充当这些数值的实测来源。"),
        legacy_summary_fields=dict(status="design_budget_only",
            keys=["injector_dp_ox_MPa", "injector_dp_fuel_MPa", "valve_line_dp_MPa"],
            detail="兼容汇总键表示设计比例预算，不代表固定阻力模式四支路的实际损失；实际值以 branches 中逐支路记录为准。"),
        missing_evidence=list(dict.fromkeys(missing)),
        audit_scope="保存状态间的集总压力关系与指定预算一致性；不验证实机损失或部件能力。")
