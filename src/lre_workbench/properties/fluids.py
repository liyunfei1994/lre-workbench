"""Checked pure-fluid HEOS states used by pumps and the coolant integrator.

Declared EOS temperature/pressure limits are necessary, not experimental
validation. An available HEOS melting boundary is also checked; no complete
phase-equilibrium validation is claimed. Gas-mixture properties do not use
this module. No property fallback or silent extrapolation is allowed.
"""
from __future__ import annotations

from functools import lru_cache
import math
import threading

try:
    import CoolProp as CP
    from CoolProp.CoolProp import PropsSI
except ImportError:
    CP = None
    PropsSI = None

_LOCAL = threading.local()
_ALIASES = {
    "CH4": "Methane", "Methane": "Methane", "methane": "Methane",
    "O2": "Oxygen", "o2": "Oxygen", "LOX": "Oxygen", "Oxygen": "Oxygen",
    "c12h26": "n-Dodecane", "C12H26": "n-Dodecane", "n-Dodecane": "n-Dodecane",
    "nDodecane": "n-Dodecane", "RP1": "n-Dodecane", "RP-1": "n-Dodecane",
}


def _name(fluid):
    try:
        return _ALIASES[fluid]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"不支持的纯流体：{fluid!r}") from exc


@lru_cache(maxsize=3)
def _limits(name):
    if CP is None:
        raise RuntimeError("纯流体计算需要 CoolProp HEOS；不使用经验物性回退。")
    return {"fluid": name, "backend": "HEOS", "version": CP.__version__,
            "Tmin_K": float(PropsSI("Tmin", name)), "Tmax_K": float(PropsSI("Tmax", name)),
            "pmax_Pa": float(PropsSI("pmax", name)),
            "Tcritical_K": float(PropsSI("Tcrit", name)),
            "pcritical_Pa": float(PropsSI("pcrit", name)),
            "rhocritical_kgm3": float(PropsSI("rhocrit", name)),
            "validation_status": "declared_range_only"}


def fluid_spec(fluid):
    return dict(_limits(_name(fluid)))


def validate_pressure(fluid, pressure):
    spec = fluid_spec(fluid)
    if not math.isfinite(pressure) or not 0 < pressure <= spec["pmax_Pa"]:
        raise ValueError(f"{spec['fluid']} 压力 {pressure:g} Pa 超出 HEOS 声明范围 (0, {spec['pmax_Pa']:g}] Pa。")
    return spec


def validate_tp(fluid, temperature, pressure):
    spec = validate_pressure(fluid, pressure)
    if not math.isfinite(temperature) or not spec["Tmin_K"] <= temperature <= spec["Tmax_K"]:
        raise ValueError(f"{spec['fluid']} 温度 {temperature:g} K 超出 HEOS 声明范围 [{spec['Tmin_K']:g}, {spec['Tmax_K']:g}] K；禁止外推或截温。")
    return spec


def _state(fluid):
    name = _name(fluid)
    _limits(name)
    if not hasattr(_LOCAL, "states"):
        _LOCAL.states = {}
    if name not in _LOCAL.states:
        _LOCAL.states[name] = CP.AbstractState("HEOS", name)
    return _LOCAL.states[name]


def _output(state, fluid, require_liquid=False, transport=False):
    spec = validate_tp(fluid, state.T(), state.p())
    melting = _check_melting(state, state.T(), state.p())
    phase_names = {CP.iphase_liquid: "liquid", CP.iphase_gas: "gas",
                   CP.iphase_twophase: "twophase", CP.iphase_supercritical: "supercritical",
                   CP.iphase_supercritical_liquid: "supercritical_liquid",
                   CP.iphase_supercritical_gas: "supercritical_gas",
                   CP.iphase_critical_point: "critical_point"}
    phase = phase_names.get(state.phase(), "unknown")
    if phase in ("twophase", "unknown", "critical_point"):
        raise ValueError(f"{spec['fluid']} 相态 {phase} 不属于当前单相端口模型。")
    density = float(state.rhomass())
    pump_domain = ("liquid" if phase in ("liquid", "supercritical_liquid") else
                   "dense_supercritical" if phase == "supercritical" and density >= spec["rhocritical_kgm3"] else
                   "outside_liquid_pump_domain")
    if require_liquid and pump_domain == "outside_liquid_pump_domain":
        raise ValueError(f"液体泵入口不能采用 {phase} 状态（{spec['fluid']}，rho={density:g} kg/m³）。")
    result = {"T_K": float(state.T()), "p_Pa": float(state.p()),
              "h_Jkg": float(state.hmass()), "s_JkgK": float(state.smass()),
              "rho_kgm3": density, "cp_JkgK": float(state.cpmass()),
              "sound_speed_ms": float(state.speed_sound()), "phase": phase,
              "pump_domain": pump_domain, "limits": spec,
              "range_status": "within_declared_ranges", "melting_boundary": melting}
    if transport:
        result.update(mu_Pas=float(state.viscosity()), k_WmK=float(state.conductivity()))
    for key in ("rho_kgm3", "cp_JkgK", "sound_speed_ms", "mu_Pas", "k_WmK"):
        if key in result and (not math.isfinite(result[key]) or result[key] <= 0):
            raise ValueError(f"{spec['fluid']} 返回无效物性 {key}={result[key]}。")
    if any(not math.isfinite(result[key]) for key in ("h_Jkg", "s_JkgK")):
        raise ValueError("纯流体焓/熵不是有限数值。")
    return result


def _check_melting(state, temperature, pressure):
    if not state.has_melting_line():
        return {"status": "not_available"}
    if temperature >= state.T_critical():
        return {"status": "not_near_solid_boundary"}
    try:
        melting_T = float(state.melting_line(CP.iT, CP.iP, pressure))
    except ValueError:
        # The melting correlation has its own pressure interval. Its absence
        # outside that interval must remain explicit, rather than extrapolated.
        return {"status": "outside_melting_correlation_pressure_range"}
    if temperature <= melting_T:
        raise ValueError(f"纯流体温度 {temperature:g} K 不高于该压力的熔化边界 {melting_T:g} K；当前模型不计算固相。")
    return {"status": "above_melting_boundary", "temperature_K": melting_T}


def state_tp(fluid, temperature, pressure, *, require_liquid=False, transport=False):
    validate_tp(fluid, temperature, pressure)
    state = _state(fluid)
    _check_melting(state, temperature, pressure)
    state.update(CP.PT_INPUTS, pressure, temperature)
    return _output(state, fluid, require_liquid, transport)


def state_ph(fluid, pressure, enthalpy, *, require_liquid=False, transport=False):
    validate_pressure(fluid, pressure)
    if not math.isfinite(enthalpy):
        raise ValueError("纯流体焓必须为有限数值。")
    state = _state(fluid)
    state.update(CP.HmassP_INPUTS, enthalpy, pressure)
    return _output(state, fluid, require_liquid, transport)


def state_ps(fluid, pressure, entropy, *, require_liquid=False, transport=False):
    validate_pressure(fluid, pressure)
    if not math.isfinite(entropy):
        raise ValueError("纯流体熵必须为有限数值。")
    state = _state(fluid)
    state.update(CP.PSmass_INPUTS, pressure, entropy)
    return _output(state, fluid, require_liquid, transport)


def props_tp(output, fluid, temperature, pressure):
    """A small checked property interface, with no direct unchecked PropsSI path."""
    keys = {"D": "rho_kgm3", "H": "h_Jkg", "S": "s_JkgK", "C": "cp_JkgK",
            "V": "mu_Pas", "L": "k_WmK", "A": "sound_speed_ms"}
    if output not in keys:
        raise ValueError(f"不支持的物性输出：{output!r}")
    return state_tp(fluid, temperature, pressure, transport=output in ("V", "L"))[keys[output]]


@lru_cache(maxsize=16)
def ideal_reference_enthalpy(fluid, temperature, pressure):
    """HEOS ideal-gas enthalpy at T, stripping the finite-pressure residual.

    Hmass_idealgas and Hmass-Hmolar_residual/M are identical in HEOS. This
    avoids treating a finite-pressure gas as an exact ideal reference.
    """
    validate_tp(fluid, temperature, pressure)
    state = _state(fluid)
    state.update(CP.PT_INPUTS, pressure, temperature)
    return float(state.hmass() - state.hmolar_residual() / state.molar_mass())


def saturation_pressure(fluid, temperature):
    spec = fluid_spec(fluid)
    if not math.isfinite(temperature) or not spec["Tmin_K"] <= temperature < spec["Tcritical_K"]:
        raise ValueError(f"{spec['fluid']} 在 {temperature:g} K 无当前液相模型可用的饱和压；不可代入固定占位值计算 NPSH。")
    return float(PropsSI("P", "T", temperature, "Q", 0, spec["fluid"]))


def supply_liquid_screen(fluid, temperature, pressure):
    """Design-point phase/subcooling evidence, NOT an NPSHr comparison.

    Query only when producing a new calculation record. Missing saturation or
    melting evidence stays explicit; a supercritical state has no boiling margin.
    Absolute pressure refers to the saved lumped port, not an ullage model.
    """
    checked = state_tp(fluid, temperature, pressure, require_liquid=True)
    spec = checked["limits"]
    report = {"status": "evaluated", "model": {"backend": "HEOS", "version": spec["version"],
              "fluid": spec["fluid"]}, "phase": checked["phase"],
              "density_kg_m3": checked["rho_kgm3"],
              "saturation_temperature_K": None, "subcooling_K": None,
              "vapor_pressure_Pa": None, "static_npsh_component_m": None,
              "saturation_temperature_status": "not_defined_at_pressure",
              "vapor_pressure_status": "not_defined_at_temperature",
              "melting_boundary": checked["melting_boundary"],
              "cavitation_status": "requires_npshr_and_inlet_definition"}
    if pressure < spec["pcritical_Pa"]:
        try:
            tsat = float(PropsSI("T", "P", pressure, "Q", 0, spec["fluid"]))
            if not math.isfinite(tsat):
                raise ValueError("饱和温度不是有限值")
            report.update(saturation_temperature_K=tsat, subcooling_K=tsat-temperature,
                          saturation_temperature_status="available")
        except (ValueError, RuntimeError) as exc:
            report.update(saturation_temperature_status="unavailable",
                          saturation_temperature_reason=str(exc), status="partial")
    if temperature < spec["Tcritical_K"]:
        try:
            pv = saturation_pressure(fluid, temperature)
            report.update(vapor_pressure_Pa=pv,
                          static_npsh_component_m=(pressure-pv)/(checked["rho_kgm3"]*9.80665),
                          vapor_pressure_status="available")
        except (ValueError, RuntimeError) as exc:
            report.update(vapor_pressure_status="unavailable", vapor_pressure_reason=str(exc), status="partial")
    return report
