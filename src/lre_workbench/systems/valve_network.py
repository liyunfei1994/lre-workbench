"""Explicit three-valve concept network, distinct from a fixed-engine trim.

Design targets still prescribe the branch flows. The MFV reference opening is
held in candidate mode; OPFV and FPOV openings are eliminated from their flow
laws. No extra oxygen-main throttling degree of freedom is introduced.
"""
from copy import deepcopy
import math

from lre_workbench.components.valves import (
    configuration_hash, characteristic_hash, topology_hash, valve_pressure_drop,
    evaluate_valve,
)

PAPER = "paper_split_before_cooling_v1"


def configure_network(base, config, cooling_drop_Pa, pump_densities):
    network = deepcopy(base)
    rows = {r["id"]: r for r in network["branches"]}
    required = {k: r["required_source_pressure_Pa"] for k, r in rows.items()}
    paper = config["topology_id"] == PAPER
    valves = config["valves"]
    mfv_flow = (rows["fuel_to_fpb"]["mass_flow_kg_s"] if paper
                else network["source_totals_kg_s"]["fuel"])
    if config["mode"] == "candidate_check":
        mfv_dp = valve_pressure_drop(valves["MFV"], valves["MFV"]["reference_opening"],
                                     mfv_flow, pump_densities["fuel"])
    else:
        mfv_dp = valves["MFV"]["design_pressure_drop_Pa"]
    fuel_pump = required["fuel_to_fpb"] + cooling_drop_Pa + mfv_dp
    if paper and config["mode"] == "requirement_sizing":
        fuel_pump = max(fuel_pump, required["fuel_to_opb"] + valves["OPFV"]["design_pressure_drop_Pa"])
        mfv_dp = fuel_pump - required["fuel_to_fpb"] - cooling_drop_Pa
    # Oxygen main branch has no control valve. Raising this pressure merely to
    # satisfy FPOV would silently add a fourth adjustable restriction.
    ox_pump = required["ox_to_opb"]
    fuel_tap = required["fuel_to_fpb"]
    for key, row in rows.items():
        row["source"] = ("fuel_after_pump" if paper and key == "fuel_to_opb" else row["source"])
        source_p = (fuel_pump if row["source"] == "fuel_after_pump" else
                    fuel_tap if row["propellant"] == "fuel" else ox_pump)
        row["source_pressure_Pa"] = source_p
        row["valve_drop_Pa"] = source_p - required[key]
        row["margin_before_valve_Pa"] = row["valve_drop_Pa"]
        row["active_minimum_head_constraint"] = False
        row["physical_valve_id"] = {"fuel_to_opb": "OPFV", "ox_to_fpb": "FPOV"}.get(key)
    network.update(
        topology=config["topology_id"], mode="valve_constrained_concept_design",
        valve_configuration=deepcopy(config), configuration_hash=configuration_hash(config),
        topology_hash=topology_hash(config), hardware_snapshot=None,
        tap_pressures_Pa={"fuel": fuel_tap, "ox": ox_pump},
        pump_pressures_Pa={"fuel": fuel_pump, "ox": ox_pump},
        mfv_drop_Pa=mfv_dp, cooling_drop_Pa=cooling_drop_Pa,
        cooling_mass_flow_kg_s=mfv_flow,
        solver="algebraic_inverse_valve_design",
        unknown_count=None, equation_count=None,
        equations=["four branch mass and prescribed PB mixture-ratio equations",
                   "four branch pressure equations, no valve on oxygen main branch",
                   "fuel pump - MFV - cooling - fuel main injector pressure equation",
                   "MFV reference opening fixed; OPFV/FPOV openings inverted from CdA laws (candidate mode)"],
        assumptions=[
            "Explicit candidate installation template; not verified flight-engine plumbing.",
            "Concept sizing retains thrust, chamber pressure, overall OF and PB targets as design inputs.",
            "Candidate MFV loss changes pump work and shaft matching; branch valve openings satisfy prescribed split.",
            "No pump/turbine maps: this is not fixed-hardware engine throttling or a dynamic engine model.",
            "Base branch pressure budgets represent passive pipes and injectors, excluding the three named valves.",
        ],
    )
    network.update(audit_valve_network(network))
    return network


def audit_valve_network(network, *, total_fuel=None, total_ox=None, mr_ox=None, mr_fuel=None):
    """Independently audit saved branch equations; negative losses stay failures."""
    errors, pressure_errors = [], []
    try:
        rows = deepcopy(network["branches"])
        by_id = {r["id"]: r for r in rows}
        expected = {"fuel_to_opb", "fuel_to_fpb", "ox_to_opb", "ox_to_fpb"}
        if len(rows) != 4 or set(by_id) != expected:
            raise ValueError("Expected four unique physical feed branches")
        config = network["valve_configuration"]
        from lre_workbench.components.valves import parse_configuration
        config = parse_configuration(config)
        paper = config["topology_id"] == PAPER
        if (network["topology"] != config["topology_id"] or topology_hash(config) != network["topology_hash"]
                or configuration_hash(config) != network["configuration_hash"]):
            raise ValueError("Valve topology identity mismatch")
        for key in ("mfv_drop_Pa", "cooling_drop_Pa", "cooling_mass_flow_kg_s"):
            if not math.isfinite(network[key]) or network[key] < 0:
                raise ValueError(f"Invalid {key}")
        for mapping in (network["pump_pressures_Pa"], network["tap_pressures_Pa"], network["source_totals_kg_s"]):
            if set(mapping) != {"fuel", "ox"} or any(not math.isfinite(v) or v <= 0 for v in mapping.values()):
                raise ValueError("Invalid feed-network boundary")
        ptol = max(1., max(network["pump_pressures_Pa"].values()) * 1e-9)
        mtol = max(1e-8, max(network["source_totals_kg_s"].values()) * 1e-9)
        for kind, external in (("fuel", total_fuel), ("ox", total_ox)):
            if external is not None and (not math.isfinite(external) or abs(external-network["source_totals_kg_s"][kind]) > mtol):
                errors.append(f"{kind}: saved total differs from external source boundary")
        expected_cooling = by_id["fuel_to_fpb"]["mass_flow_kg_s"] if paper else network["source_totals_kg_s"]["fuel"]
        if abs(network["cooling_mass_flow_kg_s"] - expected_cooling) > mtol:
            errors.append("cooling mass flow differs from installed topology")
        pressure_errors.append(abs(network["pump_pressures_Pa"]["ox"] - network["tap_pressures_Pa"]["ox"]))
        mass = {kind: sum(r["mass_flow_kg_s"] for r in rows if r["propellant"] == kind) - total
                for kind, total in network["source_totals_kg_s"].items()}
        for key, row in by_id.items():
            kind = "fuel" if key.startswith("fuel") else "ox"
            source = ("fuel_after_pump" if paper and key == "fuel_to_opb" else
                      "fuel_after_cooling" if kind == "fuel" else "lox_after_pump")
            target = "ox_preburner" if key.endswith("opb") else "fuel_preburner"
            if row["source"] != source or row["target"] != target or row["propellant"] != kind:
                raise ValueError(f"{key}: inconsistent installation topology")
            for k in ("mass_flow_kg_s", "source_pressure_Pa", "destination_pressure_Pa"):
                if not math.isfinite(row[k]) or row[k] <= 0:
                    raise ValueError(f"{key}: invalid {k}")
            dp = row["valve_drop_Pa"]
            residual = row["source_pressure_Pa"] - row["destination_pressure_Pa"] - row["line_drop_Pa"] - row["injector_drop_Pa"] - dp
            budget = network["design_loss_inputs"]["branches"][key]
            loss_error = max(abs(row["line_drop_Pa"] - budget["line_dp_Pa"]),
                             abs(row["injector_drop_Pa"] - row["destination_pressure_Pa"] * budget["injector_ratio"]))
            source_p = (network["pump_pressures_Pa"]["fuel"] if source == "fuel_after_pump" else
                        network["tap_pressures_Pa"][kind])
            source_error = abs(row["source_pressure_Pa"] - source_p)
            if not all(math.isfinite(v) for v in (residual, loss_error, source_error, dp)):
                raise ValueError(f"{key}: nonfinite valve pressure accounting")
            pressure_errors.extend((abs(residual), loss_error, source_error))
            if dp < -ptol:
                errors.append(f"{key}: required valve would add pressure; target is infeasible")
            if key in ("fuel_to_fpb", "ox_to_opb") and abs(dp) > ptol:
                errors.append(f"{key}: unmodelled extra control restriction")
            row.update(pressure_residual_Pa=residual, loss_law_residual_Pa=loss_error,
                       mass_residual_kg_s=mass[kind], status="pass" if dp >= -ptol and max(abs(residual), loss_error, source_error) <= ptol else "fail")
        pressure_errors.append(abs(network["pump_pressures_Pa"]["fuel"] - network["mfv_drop_Pa"]
                                   - network["cooling_drop_Pa"] - network["tap_pressures_Pa"]["fuel"]))
        mr = {}
        targets = network.get("target_mixture_ratios")
        for side, external in (("ox", mr_ox), ("fuel", mr_fuel)):
            if external is not None and (not targets or not math.isfinite(external) or abs(external-targets[side]) > 1e-10):
                errors.append(f"{side}: mixture-ratio target differs from external constraint")
        if targets:
            mr = {side: by_id[f"ox_to_{pb}"]["mass_flow_kg_s"] - targets[side] * by_id[f"fuel_to_{pb}"]["mass_flow_kg_s"]
                  for side, pb in (("ox", "opb"), ("fuel", "fpb"))}
        maxp = max(pressure_errors)
        maxm = max(abs(v) for v in [*mass.values(), *mr.values()])
        return dict(status="pass" if not errors and maxp <= ptol and maxm <= mtol else "fail",
                    branches=rows, max_pressure_residual_Pa=maxp, max_mass_residual_kg_s=maxm,
                    pressure_tolerance_Pa=ptol, mass_tolerance_kg_s=mtol,
                    source_mass_residuals_kg_s=mass, mixture_ratio_residuals_kg_s=mr,
                    minimum_valve_residuals_Pa={}, errors=errors)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return dict(status="fail", max_pressure_residual_Pa=None, max_mass_residual_kg_s=None, errors=[str(exc)])


def evaluate_installed_valves(network, states):
    """Evaluate each valve from one coherent saved inlet state and physical port."""
    config = network["valve_configuration"]
    rows = {r["id"]: r for r in network["branches"]}
    result = {}
    for name, definition in config["valves"].items():
        if name == "MFV":
            inlet = states["fuel_after_pump"]
            inlet_key = "fuel_after_pump"
            outlet_p = states["cooling_inlet"].pressure
            flow = network["cooling_mass_flow_kg_s"]
        else:
            branch = rows["fuel_to_opb" if name == "OPFV" else "ox_to_fpb"]
            inlet = states[branch["source"]]
            inlet_key = branch["source"]
            outlet_p = inlet.pressure - branch["valve_drop_Pa"]
            flow = branch["mass_flow_kg_s"]
        result[name] = evaluate_valve(definition, mass_flow_kg_s=flow,
            upstream_pressure_Pa=inlet.pressure, upstream_temperature_K=inlet.temperature,
            downstream_pressure_Pa=outlet_p, mode=config["mode"])
        if config["mode"] == "requirement_sizing":
            reserve = inlet.pressure - outlet_p - definition["design_pressure_drop_Pa"]
            result[name].update(design_minimum_pressure_drop_Pa=definition["design_pressure_drop_Pa"],
                                pressure_drop_reserve_Pa=reserve)
            if reserve < -max(1., inlet.pressure * 1e-9) and result[name]["status"] == "available":
                result[name].update(status="infeasible", reason="below_minimum_design_pressure_drop")
        result[name]["upstream_enthalpy_J_kg"] = inlet.enthalpy
        result[name]["upstream_enthalpy_reference_id"] = inlet.metadata.get("reference_id")
        result[name]["inlet_state_source"] = "states." + inlet_key
    return dict(schema_version=1, configuration=deepcopy(config),
        topology_hash=topology_hash(config), configuration_hash=configuration_hash(config),
        characteristic_hash={key: characteristic_hash(v) for key, v in config["valves"].items()},
        valves=result, scope="valve_constrained_concept_design",
        status="pass" if network["status"] == "pass" and all(v.get("status") in ("available", "feasible") for v in result.values()) else "infeasible",
        limitations=list(network["assumptions"]))
