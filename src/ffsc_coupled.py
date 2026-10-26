"""Experimental 18-variable formulation of the same lumped FFSC design model.

This comparison deliberately has no production/UI selector. It starts from a
validated partitioned solution, exposes the previously eliminated feed states,
and solves identical pressure/mass/power/thermal equations simultaneously. A
local comparison is not evidence of global robustness or hardware validation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
import math
import time

from convergence import numerical_convergence
from nonlinear_solver import solve_nonlinear
from steady_closure import audit_energy
from steady_network import BRANCH_IDS, audit_network


VARIABLE_NAMES = (
    "total_flow_kg_s", "ox_turbine_pressure_ratio", "fuel_turbine_pressure_ratio",
    "ox_preburner_mr", "fuel_preburner_mr", "feedback_temperature_K",
    "feedback_gamma", "feedback_molar_mass_kg_kmol",
    *[f"{key}_flow_kg_s" for key in BRANCH_IDS],
    "fuel_tap_pressure_Pa", "ox_tap_pressure_Pa",
    *[f"{key}_extra_valve_drop_Pa" for key in BRANCH_IDS],
)
RESIDUAL_NAMES = (
    "thrust_N", "ox_shaft_power_W", "fuel_shaft_power_W",
    "ox_preburner_temperature_K", "fuel_preburner_temperature_K",
    "feedback_temperature_K", "feedback_gamma", "feedback_molar_mass_kg_kmol",
    "fuel_mass_kg_s", "ox_mass_kg_s", "ox_preburner_mr_mass_kg_s", "fuel_preburner_mr_mass_kg_s",
    *[f"{key}_pressure_Pa" for key in BRANCH_IDS],
    "fuel_minimum_valve_Pa", "ox_minimum_valve_Pa",
)
MUTATED_INPUTS = ("ox_preburner_mr_target", "fuel_preburner_mr_target",
                  "chamber_temp_guess", "chamber_gamma", "molar_mass")


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _serial(value):
    return asdict(value) if is_dataclass(value) else deepcopy(value)


def _positive(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name}: expected positive finite number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}: expected positive finite number")
    return value


def _source_equations(reference, numeric):
    """Recheck the saved physical state instead of accepting stale pass flags."""
    try:
        inputs = _get(reference, "inputs")
        states = _get(reference, "states")
        components = _get(reference, "components")
        metadata = _get(reference, "convergence")
        feedback = metadata["solution"]
        layers = numeric["layers"]
        inner_checks = {row["name"]: row for row in layers["inner"]["checks"]}
        power = lambda component, key: float(_get(_get(components[component], "outputs"), key))
        temperature = lambda state: float(_get(states[state], "temperature"))
        chamber_metadata = _get(states["main_chamber"], "metadata")
        values = [
            ("thrust", power("nozzle", "thrust_N") - float(_get(inputs, "thrust")), inner_checks["thrust"]["tolerance"]),
            ("ox_power", power("ox_preburner_turbine", "turbine_power_W") - power("ox_pump", "power_W"), inner_checks["ox_power"]["tolerance"]),
            ("fuel_power", power("fuel_preburner_turbine", "turbine_power_W") - power("fuel_pump", "power_W"), inner_checks["fuel_power"]["tolerance"]),
            ("ox_preburner_temperature", temperature("ox_preburner_outlet") - float(_get(inputs, "ox_preburner_temp_target")), layers["preburner"]["tolerance"]),
            ("fuel_preburner_temperature", temperature("fuel_preburner_outlet") - float(_get(inputs, "fuel_preburner_temp_target")), layers["preburner"]["tolerance"]),
        ]
        if layers["thermal"].get("enabled"):
            thermal_checks = {row["name"]: row for row in layers["thermal"]["checks"]}
            values.extend([
                ("feedback_temperature", temperature("main_chamber") - feedback["chamber_temp_guess"], thermal_checks["temperature"]["tolerance"]),
                ("feedback_gamma", (chamber_metadata["gamma"] - feedback["chamber_gamma"]) / feedback["chamber_gamma"], thermal_checks["gamma"]["tolerance"]),
                ("feedback_molar_mass", (chamber_metadata["molar_mass"] - feedback["molar_mass"]) / feedback["molar_mass"], thermal_checks["molar_mass"]["tolerance"]),
            ])
        rows = []
        for name, residual, tolerance in values:
            tolerance = _positive(tolerance, name + " tolerance")
            rows.append(dict(name=name, residual=residual, tolerance=tolerance,
                             status="pass" if math.isfinite(residual) and abs(residual) <= tolerance else "fail"))
        return dict(status="pass" if all(row["status"] == "pass" for row in rows) else "fail", checks=rows)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return dict(status="unavailable", errors=[str(exc)])


def _source_checks(reference):
    reference = _get(reference, "calculation", reference)
    summary = _get(reference, "summary", {})
    closure = _get(reference, "steady_closure", {}) or {}
    inputs = _get(reference, "inputs", {})
    network = closure.get("feed_network") or {}
    numeric = numerical_convergence(reference)
    try:
        feed = audit_network(network, total_fuel=summary["mdot_fuel_kg_s"],
                             total_ox=summary["mdot_ox_kg_s"],
                             mr_ox=summary["ox_preburner_mr_target"], mr_fuel=summary["fuel_preburner_mr_target"])
        energy = audit_energy(_get(reference, "states", {}), _get(reference, "components", {}))
    except (KeyError, TypeError, ValueError) as exc:
        return reference, {"numeric": numeric, "feed": {"status": "fail", "errors": [str(exc)]},
                           "energy": {"status": "unavailable"}}, inputs
    return reference, {"numeric": numeric, "feed": feed, "energy": energy,
                       "equations": _source_equations(reference, numeric)}, inputs


def _initial_vector(reference):
    summary = _get(reference, "summary")
    states = _get(reference, "states")
    chamber = states["main_chamber"]
    metadata = _get(chamber, "metadata")
    feedback = (_get(reference, "convergence", {}) or {}).get("solution") or {}
    network = _get(reference, "steady_closure")["feed_network"]
    rows = {row["id"]: row for row in network["branches"]}
    return [summary["mdot_total_kg_s"], summary["ox_turbine_pi_t"], summary["fuel_turbine_pi_t"],
            summary["ox_preburner_mr"], summary["fuel_preburner_mr"],
            feedback.get("chamber_temp_guess", summary.get("chamber_temp_guess_used_K", _get(chamber, "temperature"))),
            feedback.get("chamber_gamma", metadata["gamma"]), feedback.get("molar_mass", metadata["molar_mass"]),
            *[rows[key]["mass_flow_kg_s"] for key in BRANCH_IDS],
            network["tap_pressures_Pa"]["fuel"], network["tap_pressures_Pa"]["ox"],
            *[rows[key]["valve_drop_Pa"] for key in BRANCH_IDS]]


def coupled_residuals(model, x):
    """Evaluate all 18 physical equations once; caller owns temporary state.

    The model's `_network_trial` hook must propagate supplied branch flows and
    source pressures without eliminating their residuals or clipping flows.
    """
    if len(x) != 18 or not all(math.isfinite(float(value)) for value in x):
        raise ValueError("The coupled FFSC state requires 18 finite values")
    inputs = model.inputs
    for key, value in zip(MUTATED_INPUTS, x[3:8]):
        setattr(inputs, key, float(value))
    flows = dict(zip(BRANCH_IDS, map(float, x[8:12])))
    valves = dict(zip(BRANCH_IDS, map(float, x[14:18])))
    model._network_trial = {"flows": flows,
                            "tap_pressures_Pa": dict(fuel=float(x[12]), ox=float(x[13])),
                            "valve_drops_Pa": valves}
    result = model._evaluate_once(*map(float, x[:3]))
    summary = result.summary
    chamber = result.states["main_chamber"]
    network = result.steady_closure["feed_network"]
    rows = {row["id"]: row for row in network["branches"]}
    # Reject an absent/no-op hook: a deceptively converged eliminated network
    # is not an evaluation of this expanded system.
    for key in BRANCH_IDS:
        if not math.isclose(rows[key]["mass_flow_kg_s"], flows[key], rel_tol=1e-12, abs_tol=1e-10):
            raise ValueError("The cycle did not honor the expanded branch-flow trial")
        kind = "fuel" if key.startswith("fuel") else "ox"
        expected_pressure = float(x[12 if kind == "fuel" else 13])
        if not math.isclose(rows[key]["source_pressure_Pa"], expected_pressure, rel_tol=1e-12, abs_tol=1e-6):
            raise ValueError("The cycle did not honor the expanded tap-pressure trial")
        if not math.isclose(rows[key]["valve_drop_Pa"], valves[key], rel_tol=1e-12, abs_tol=1e-6):
            raise ValueError("The cycle did not honor the expanded valve-loss trial")
    components = result.components
    output = lambda key, field: components[key].outputs[field]
    total_fuel = float(x[0]) / (1 + inputs.of_ratio)
    total_ox = float(x[0]) - total_fuel
    raw = [
        summary["thrust_N"] - inputs.thrust,
        output("ox_preburner_turbine", "turbine_power_W") - output("ox_pump", "power_W"),
        output("fuel_preburner_turbine", "turbine_power_W") - output("fuel_pump", "power_W"),
        summary["ox_preburner_T_combustion_K"] - inputs.ox_preburner_temp_target,
        summary["fuel_preburner_T_combustion_K"] - inputs.fuel_preburner_temp_target,
        chamber.temperature - float(x[5]), chamber.metadata["gamma"] - float(x[6]),
        chamber.metadata["molar_mass"] - float(x[7]),
        flows["fuel_to_opb"] + flows["fuel_to_fpb"] - total_fuel,
        flows["ox_to_opb"] + flows["ox_to_fpb"] - total_ox,
        flows["ox_to_opb"] - float(x[3]) * flows["fuel_to_opb"],
        flows["ox_to_fpb"] - float(x[4]) * flows["fuel_to_fpb"],
    ]
    for key in BRANCH_IDS:
        row = rows[key]
        raw.append(row["source_pressure_Pa"] - row["destination_pressure_Pa"]
                   - row["line_drop_Pa"] - row["injector_drop_Pa"] - valves[key])
    raw.extend((min(valves["fuel_to_opb"], valves["fuel_to_fpb"]),
                min(valves["ox_to_opb"], valves["ox_to_fpb"])))
    return raw, result


def solve_coupled(model, reference, *, perturbation=.02, backend="trf", max_iter=50, max_nfev=800):
    """Compare an expanded solve to a validated partitioned design reference.

    All tolerances are frozen from the reference; scipy optimizer success never
    substitutes for equation acceptance. A missing/failed reference produces a
    blocked diagnostic without any cycle evaluation.
    """
    started = time.perf_counter()
    reference, checks, source_inputs = _source_checks(reference)
    report = dict(schema_version=1, analysis="experimental_coupled_ffsc", variable_count=18,
                  equation_count=18, backend=backend, reference_checks=checks,
                  source_comparison="same_lumped_equations_local_perturbed_validated_reference",
                  reference_reused=True, perturbation=perturbation)
    if (_get(source_inputs, "cycle_type") != "ffsc"
            or float(_get(source_inputs, "use_target_preburner_temp", 0)) < .5
            or (_get(reference, "steady_closure", {}).get("feed_network") or {}).get("mode") != "design"):
        return report | dict(status="blocked", termination_reason="only_temperature_target_ffsc_design_supported", nfev=0)
    if any(check.get("status") != "pass" for check in checks.values()):
        return report | dict(status="blocked", termination_reason="reference_not_numerically_and_physically_closed", nfev=0)
    if not math.isfinite(float(perturbation)) or not 0 <= perturbation <= .1:
        raise ValueError("Local coupled comparison perturbation must be between 0 and 0.1")
    if model.inputs.cycle_type != "ffsc" or model.inputs.use_target_preburner_temp < .5:
        raise ValueError("Coupled comparison requires an FFSC temperature-target model")
    if getattr(model, "_feed_hardware", None) is not None:
        raise ValueError("Design comparison cannot reuse an offdesign frozen-hardware model")
    # The reference and candidate must have identical physical inputs. The five
    # solved feedback quantities are not independent operating specifications.
    current_inputs = _serial(model.inputs)
    source_dict = _serial(source_inputs)
    if any(current_inputs.get(key) != value for key, value in source_dict.items() if key not in MUTATED_INPUTS):
        raise ValueError("Coupled comparison model inputs differ from the source operating point")
    initial = [float(value) for value in _initial_vector(reference)]
    if not all(math.isfinite(value) for value in initial):
        raise ValueError("Reference contains a nonfinite coupled state")
    summary = _get(reference, "summary")
    comp = _get(reference, "components")
    power = lambda key: _positive(_get(_get(comp[key], "outputs"), "power_W"), key)
    p_tol = float(checks["feed"]["pressure_tolerance_Pa"])
    m_tol = float(checks["feed"]["mass_tolerance_kg_s"])
    inner = checks["numeric"].get("layers", {}).get("inner", {})
    inner_relative = _positive(inner.get("tolerance", 1e-6), "reference inner tolerance")
    inner_checks = {row.get("name"): row for row in inner.get("checks", [])}
    def source_tolerance(name, scale):
        return _positive(inner_checks.get(name, {}).get("tolerance", scale * inner_relative),
                         f"reference {name} tolerance")
    tolerances = [source_tolerance("thrust", max(abs(model.inputs.thrust), 100)),
                  source_tolerance("ox_power", max(power("ox_pump"), 1e5)),
                  source_tolerance("fuel_power", max(power("fuel_pump"), 1e5)),
                  3.0, 3.0, 5.0, abs(initial[6]) * 1e-4, abs(initial[7]) * 1e-4,
                  *([m_tol] * 4), *([p_tol] * 6)]
    # Characteristic magnitudes condition the linear problem. Acceptance
    # tolerances are separate: using the tiny mass audit tolerance itself as a
    # residual scale overwhelms temperature/power equations by ~1e9.
    residual_scales = [max(abs(model.inputs.thrust), 100),
                       max(power("ox_pump"), 1e5), max(power("fuel_pump"), 1e5),
                       model.inputs.ox_preburner_temp_target, model.inputs.fuel_preburner_temp_target,
                       abs(initial[5]), abs(initial[6]), abs(initial[7]),
                       summary["mdot_fuel_kg_s"], summary["mdot_ox_kg_s"],
                       summary["mdot_ox_kg_s"], summary["mdot_ox_kg_s"],
                       *([model.inputs.pc] * 6)]
    internal_tolerance = min(1e-10, min(t / scale for t, scale in zip(tolerances, residual_scales)) * .1)
    lower = [1e-3, 1.02, 1.02, 5, .01, 200, 1.01, 1,
             *([1e-6] * 4), model.inputs.pc, model.inputs.pc, *([0.0] * 4)]
    upper = [math.inf, 10, 10, 200, 2, 6000, 1.8, 100,
             *([math.inf] * 4), 1e9, 1e9, *([1e9] * 4)]
    variable_scales = [max(abs(value), model.inputs.pc if index >= 12 else 1.0)
                       for index, value in enumerate(initial)]
    start = [value * (1 + perturbation * (1 if index % 2 == 0 else -1))
             for index, value in enumerate(initial)]
    # Explicitly disclose projection of the deterministic local perturbation.
    start = [max(lo, min(hi, value)) for value, lo, hi in zip(start, lower, upper)]
    steps = [max(initial[0] * 1e-4, 1e-5), .002, .002,
             max(initial[3] * 1e-4, 1e-4), max(initial[4] * 1e-4, 1e-6),
             .1, 1e-5, 1e-4, *[max(v * 1e-4, 1e-5) for v in initial[8:12]],
             *([1000.0] * 6)]
    saved = {key: getattr(model.inputs, key) for key in MUTATED_INPUTS}
    had_trial = hasattr(model, "_network_trial")
    old_trial = getattr(model, "_network_trial", None)
    try:
        solved = solve_nonlinear(lambda x: coupled_residuals(model, x), start, lower, upper,
                                residual_scales=residual_scales,
                                variable_scales=variable_scales, diff_steps=steps, tol=internal_tolerance,
                                max_iter=max_iter, backend=backend, names=RESIDUAL_NAMES,
                                max_nfev=max_nfev)
        # A CycleResults references model.inputs. A final Jacobian probe may be
        # the last mutation even when its payload was not accepted. Serialize
        # the accepted state's feedback values, never those of that last probe.
        if solved.payload is not None:
            for key, value in zip(MUTATED_INPUTS, solved.x[3:8]):
                setattr(model.inputs, key, float(value))
        calculation = _serial(solved.payload) if solved.payload is not None else None
    finally:
        for key, value in saved.items():
            setattr(model.inputs, key, value)
        if had_trial:
            model._network_trial = old_trial
        elif hasattr(model, "_network_trial"):
            delattr(model, "_network_trial")
    residuals = [dict(name=name, residual=value, tolerance=tolerance,
                      status="pass" if math.isfinite(value) and abs(value) <= tolerance else "fail")
                 for name, value, tolerance in zip(RESIDUAL_NAMES, solved.residuals, tolerances)]
    final_checks = {}
    if calculation is not None:
        final_network = calculation["steady_closure"]["feed_network"]
        final_checks["feed"] = audit_network(final_network,
            total_fuel=solved.x[0] / (1 + model.inputs.of_ratio),
            total_ox=solved.x[0] * model.inputs.of_ratio / (1 + model.inputs.of_ratio),
            mr_ox=solved.x[3], mr_fuel=solved.x[4])
        final_checks["energy"] = audit_energy(calculation["states"], calculation["components"])
    passed = (len(residuals) == 18 and all(row["status"] == "pass" for row in residuals)
              and len(final_checks) == 2 and all(check["status"] == "pass" for check in final_checks.values()))
    final_reason = (solved.reason if solved.passed else "physical_equations_and_independent_audits_passed") if passed else (
        "independent_closure_failed" if solved.passed else solved.reason)
    report.update(status="pass" if passed else "fail", termination_reason=final_reason,
                  acceptance_tolerance_source="reference_recorded_inner_checks_and_existing_outer_network_criteria",
                  solver_termination_reason=solved.reason, internal_norm_target_met=solved.passed,
                  internal_norm_tolerance=internal_tolerance, residual_scales=residual_scales,
                  backend_status=solved.backend_status, nfev=solved.nfev, njev=solved.njev,
                  iterations=solved.iterations, rejected_steps=solved.rejected_steps,
                  evaluation_budget=solved.evaluation_budget, error=solved.error,
                  variables=dict(zip(VARIABLE_NAMES, solved.x)), initial_variables=dict(zip(VARIABLE_NAMES, start)),
                  residuals=residuals, trace=solved.points, final_checks=final_checks,
                  calculation=calculation, elapsed_seconds=time.perf_counter() - started,
                  limitations=["仅在同一已闭合参考点附近比较，不证明全局稳健性。",
                               "18 个未知量联立同一零维方程；压力支路最小阀压降约束在活动支路切换时不光滑。",
                               "保持与分块方法相同的温度和物性容差，网络质量/压力按独立审计容差验收。",
                               "内部试探状态可能不守恒；只有最终 18 条残差与独立能量/网络审计全部通过才接受。"])
    return report
