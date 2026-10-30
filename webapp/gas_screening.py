"""Explicit, immutable-record gas-model sensitivity calculations.

This is a validation-job backend, never a production EOS or a GET projection.
No species is silently dropped. Reduced mixtures are separate, fully accounted
screening models, not reactions, corrected engine results, or deposition rates.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path

from webapp.validation_checks import _PROPERTY_LOCK, property_capabilities

STATIONS = (
    ("ox_preburner_outlet", "富氧预燃室出口 / 涡轮入口", "ox", "inlet"),
    ("ox_turbine_exhaust", "富氧涡轮排气 / 主室入口", "ox", "outlet"),
    ("fuel_preburner_outlet", "富燃预燃室出口 / 涡轮入口", "fuel", "inlet"),
    ("fuel_turbine_exhaust", "富燃涡轮排气 / 主室入口", "fuel", "outlet"),
)
_FLUIDS = {"O2": "Oxygen", "H2": "Hydrogen", "H2O": "Water", "CO": "CarbonMonoxide",
           "CO2": "CarbonDioxide", "CH4": "Methane", "N2": "Nitrogen", "AR": "Argon",
           "C2H2": "Acetylene", "C2H4": "Ethylene", "C2H6": "Ethane",
           "C3H8": "Propane", "CH3OH": "Methanol"}
_REFPROP_FILENAMES = {"O2": "OXYGEN", "H2": "HYDROGEN", "H2O": "WATER", "CO": "CO",
                     "CO2": "CO2", "CH4": "METHANE", "N2": "NITROGEN", "AR": "ARGON",
                     "C2H2": "ACETYLENE", "C2H4": "ETHYLENE", "C2H6": "ETHANE",
                     "C3H8": "PROPANE", "CH3OH": "METHANOL"}
_MECHANISMS = {"gri30.yaml", "gri30_highT.yaml"}
_REDUCTION_CUTOFF = 1e-8
_MAX_REMOVED = 1e-4
_METRICS = ("density_kg_m3", "compressibility_factor", "cp_j_kg_k", "cv_j_kg_k",
            "speed_sound_m_s", "enthalpy_j_kg", "entropy_j_kg_k")
_SOURCES = ["https://cantera.org/stable/reference/thermo/phase-thermo.html",
            "https://cantera.org/stable/yaml/species.html",
            "https://cantera.org/stable/examples/python/thermo/adiabatic.html",
            "https://coolprop.org/coolprop/REFPROP.html", "https://www.nist.gov/srd/refprop"]


def _checkpoint():
    # The solver adds src to sys.path. Also support standalone validation callers.
    try:
        from task_control import checkpoint
    except ImportError:
        from src.task_control import checkpoint
    checkpoint()


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("保存记录含缺失或非数值物性。")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("保存记录含非有限值或非正温压。")
    return value


def normalize_screen_request(request=None):
    request = {} if request is None else request
    if not isinstance(request, dict):
        raise ValueError("燃气筛查请求必须为对象。")
    flags = ("compare_cubic", "compare_refprop", "carbon", "allow_reduced")
    if set(request) - {*flags, "stations"}:
        raise ValueError("燃气筛查请求含未知字段。")
    out = {}
    for key in flags:
        value = request.get(key, True)
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须为布尔值。")
        out[key] = value
    ids = request.get("stations", [s[0] for s in STATIONS])
    if (not isinstance(ids, list) or not 1 <= len(ids) <= len(STATIONS)
            or any(not isinstance(s, str) or s not in {x[0] for x in STATIONS} for s in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("燃气筛查站点必须是四个已定义接口中的非重复列表。")
    out["stations"] = list(ids)
    return out


def _phase(ct, text, name):
    return ct.Solution(yaml=text, name=name)


def _composition(gas):
    # All nonzero values, including tiny radicals, are retained and never rounded.
    return {s: float(y) for s, y in zip(gas.species_names, gas.Y) if y > 0}


def _elements(gas):
    return {e: float(gas.elemental_mass_fraction(e)) for e in gas.element_names}


def _properties(gas):
    import cantera as ct
    out = {"temperature_k": float(gas.T), "pressure_pa": float(gas.P),
           "density_kg_m3": float(gas.density), "cp_j_kg_k": float(gas.cp_mass),
           "cv_j_kg_k": float(gas.cv_mass), "speed_sound_m_s": float(gas.sound_speed),
           "enthalpy_j_kg": float(gas.enthalpy_mass), "entropy_j_kg_k": float(gas.entropy_mass),
           "molar_mass_kg_kmol": float(gas.mean_molecular_weight),
           "compressibility_factor": float(gas.P * gas.mean_molecular_weight /
                                            (gas.density * ct.gas_constant * gas.T))}
    for value in out.values():
        _number(value)
    if any(out[k] <= 0 for k in ("density_kg_m3", "cp_j_kg_k", "cv_j_kg_k", "speed_sound_m_s")):
        raise ValueError("EOS 未返回稳定单相正物性。")
    return out


def _difference(candidate, baseline):
    return {key: {"difference": candidate[key] - baseline[key],
                  "relative_difference": ((candidate[key] - baseline[key]) / abs(baseline[key])
                                          if key not in ("enthalpy_j_kg", "entropy_j_kg_k") and abs(baseline[key]) > 1e-12 else None),
                  "comparison_basis": "absolute_aligned_reference" if key in ("enthalpy_j_kg", "entropy_j_kg_k") else "relative"}
            for key in _METRICS if key in candidate and key in baseline}


def _gas_domain(gas):
    # Check every present species. Valid temperature coverage is not EOS accuracy.
    from thermo import gas_temperature_applicability
    evidence = gas_temperature_applicability(gas)
    _properties(gas)
    compressibility = _number(float(gas.isothermal_compressibility), positive=True)
    phase = gas.phase_of_matter
    # Cantera 3.2 cubic phases report 'unspecified'; local derivatives can be
    # checked, but no global phase classification may be inferred from them.
    if phase not in ("gas", "supercritical", "unspecified"):
        raise ValueError(f"候选燃气 EOS 返回非气体／超临界单相：{phase}")
    return {"temperature_k": float(gas.T), "pressure_pa": float(gas.P),
            "phase": phase, "isothermal_compressibility_1_pa": compressibility,
            "phase_classification": "not_provided_by_backend" if phase == "unspecified" else "reported_by_backend",
            "within_declared_range": evidence["status"] == "within_declared_ranges",
            "temperature_evidence": evidence}


def _sample_expansion(pin, pout, efficiency, isentropic, actual, inspect):
    """Nine log-pressure samples: isentrope plus a cumulative-efficiency envelope.

    This is a domain screen, not a resolved turbine loss distribution or proof
    of global phase stability. Inspect endpoints even when an intermediate fails.
    """
    pin, pout = _number(pin, True), _number(pout, True)
    if not pout < pin:
        raise ValueError("保存出口压力未小于入口压力。")
    if efficiency is not None and not 0 < _number(efficiency) <= 1:
        raise ValueError("膨胀效率必须满足 0 < η ≤ 1。")
    samples, out = [], {"status": "computed", "composition_policy": "frozen",
                        "state_basis": "saved_total_to_total_lumped_interface"}
    try:
        for i in range(9):
            _checkpoint()
            p = pin if i == 0 else pout if i == 8 else pin * (pout / pin) ** (i / 8)
            drop, residual = isentropic(p)
            point = inspect() | {"path": "isentropic"}
            samples.append(point)
            if not _finite_drop(drop):
                raise ValueError("候选 EOS 的等熵膨胀焓降非有限值或为负。")
            if i == 8:
                out.update(outlet_pressure_pa=pout, isentropic_drop_j_kg=float(drop),
                           isentropic_outlet_temperature_k=point["temperature_k"], entropy_residual_j_kg_k=float(residual))
            if efficiency is not None:
                actual(p, efficiency * drop)
                point = inspect() | {"path": "constant_cumulative_efficiency_envelope"}
                samples.append(point)
                if i == 8:
                    out.update(efficiency=efficiency, actual_drop_j_kg=float(efficiency*drop),
                               actual_outlet_temperature_k=point["temperature_k"])
        if any(not p["within_declared_range"] for p in samples):
            out.update(status="computed_outside_declared_range", reason="膨胀入口、出口或中间采样超出声明范围。")
    except Exception as error:
        out.update(status="unavailable", reason=str(error)[:600])
    out["domain_check"] = {"samples": samples, "sample_count_per_path": 9,
                           "coverage": "sampled_not_continuous", "phase_stability": "local_single_phase_checks_only",
                           "description": "对等熵路径与固定累计效率包络采样；不代表真实级间损失分布，也不是连续路径或全局相平衡证明。"}
    return out


def _finite_drop(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= -1e-5


def _reduction(gas, original, supported):
    kept = {s: y for s, y in original.items() if s in supported and y >= _REDUCTION_CUTOFF}
    removed = {s: y for s, y in original.items() if s not in kept}
    retained = math.fsum(kept.values())
    result = {"status": "available" if retained > 0 else "unavailable",
              "mass_fraction_cutoff": _REDUCTION_CUTOFF,
              "max_permitted_removed_mass_fraction": _MAX_REMOVED,
              "removed_species_mass_fractions": removed,
              "removed_mass_fraction": math.fsum(removed.values()),
              "retained_mass_fraction": retained,
              "composition_mass_fractions": {s: y / retained for s, y in kept.items()} if retained else {},
              "renormalized": bool(removed),
              "meaning": "仅候选冻结组成筛查；删除物种及重新归一化均显式记录，不能回写生产状态。"}
    if retained == 0 or result["removed_mass_fraction"] > _MAX_REMOVED:
        result.update(status="unavailable", reason="可追溯参数覆盖不足；删去质量超过筛查上限或无保留物种。")
        return result
    T, P, saved = gas.T, gas.P, gas.state.copy()
    original_h, original_e = gas.enthalpy_mass, _elements(gas)
    try:
        gas.TPY = T, P, result["composition_mass_fractions"]
        retained_e = _elements(gas)
        result["enthalpy_difference_after_renormalization_j_kg"] = float(gas.enthalpy_mass - original_h)
        result["element_mass_fraction_difference_after_renormalization"] = {
            e: retained_e[e] - value for e, value in original_e.items()}
        result["removed_element_mass_kg_per_kg_original"] = {
            e: original_e[e] - retained * retained_e[e] for e in original_e}
        result["removed_enthalpy_j_per_kg_original"] = float(original_h - retained * gas.enthalpy_mass)
    finally:
        gas.state = saved
    return result


def _critical_species(ct, gas):
    """Use installed source data, not guesses for radicals or fitted coefficients."""
    from ruamel.yaml import YAML
    path = Path(ct.__file__).resolve().parent / "data" / "critical-properties.yaml"
    raw = path.read_bytes()
    doc = YAML(typ="safe").load(raw.decode("utf-8"))
    catalog = {entry["name"].upper(): entry["critical-parameters"] for entry in doc["species"]}
    prepared, sources = {}, {}
    try:
        import CoolProp.CoolProp as CP
    except ImportError:
        CP = None
    for sp in gas.species():
        _checkpoint()
        name = sp.name
        data = dict(sp.input_data)
        if name.upper() in catalog:
            data["critical-parameters"] = deepcopy(catalog[name.upper()])
            source = {"source": "Cantera installed critical-properties.yaml", "file_sha256": hashlib.sha256(raw).hexdigest()}
        elif CP is not None and name in _FLUIDS:
            try:
                fluid = _FLUIDS[name]
                parameters = {"critical-temperature": CP.PropsSI("Tcrit", fluid),
                              "critical-pressure": CP.PropsSI("Pcrit", fluid),
                              "acentric-factor": CP.PropsSI("acentric", fluid)}
                for value in parameters.values():
                    _number(value)
                data["critical-parameters"] = parameters
                source = {"source": "installed CoolProp HEOS critical parameters", "fluid": fluid,
                          "eos_reference": CP.get_fluid_param_string(fluid, "BibTeX-EOS"),
                          "fluid_definition_sha256": hashlib.sha256(CP.get_fluid_param_string(fluid, "JSON").encode()).hexdigest()}
            except Exception:
                continue
        else:
            continue
        # An explicit local parameter declaration avoids Cantera's Windows
        # non-ASCII native path fallback, with no copy or edit of installed data.
        prepared[name] = ct.Species.from_dict(data)
        sources[name] = source | {"critical_parameters": deepcopy(data["critical-parameters"])}
    return prepared, sources


def _expansion(gas, outlet_pressure, efficiency):
    if outlet_pressure is None:
        return {"status": "not_applicable", "reason": "排气站点不再向下游外推；仅涡轮入口按保存出口压力筛查。"}
    original = gas.state.copy()
    try:
        h, s, pin = float(gas.enthalpy_mass), float(gas.entropy_mass), float(gas.P)
        def isentropic(p):
            gas.SP = s, p
            return float(h - gas.enthalpy_mass), float(gas.entropy_mass - s)
        def actual(p, drop):
            gas.HP = h - drop, p
        return _sample_expansion(pin, outlet_pressure, efficiency, isentropic, actual, lambda: _gas_domain(gas))
    except Exception as error:
        return {"status": "unavailable", "reason": str(error)[:600]}
    finally:
        gas.state = original


def _cubic_comparison(ct, baseline, original_y, prepared, sources, eos, request, pout, efficiency):
    supported, rejected = {}, {}
    for name, sp in prepared.items():
        _checkpoint()
        try:
            ct.Solution(thermo=eos, species=[sp], kinetics=None)
            supported[name] = sp
        except Exception as error:
            rejected[name] = str(error)[-400:]
    missing = [s for s in baseline.species_names if s not in supported]
    missing_present = {s: y for s, y in original_y.items() if s not in supported and y > 0}
    out = {"backend": f"Cantera {eos}", "status": "unavailable",
           "full_composition_status": "unavailable" if missing_present else "supported",
           "parameter_coverage": {"mechanism_species_count": baseline.n_species,
                                  "supported_species": list(supported), "missing_species": missing,
                                  "missing_present_species_mass_fractions": missing_present,
                                  "missing_present_mass_fraction": math.fsum(missing_present.values()),
                                  "construction_failures": rejected, "parameter_sources": sources},
           "reaction_equilibrium": "not_performed",
           "mixture_EOS_accuracy_validation": "not_established",
           "transport_properties": "not_calculated_or_validated",
           "notes": ["采用可追溯单物种临界参数及 Cantera 默认混合规则；二元相互作用未拟合、未独立验证。",
                     "不将缺参数自由基或痕量物种视为零，不替换生产 EOS；未验证高压化学平衡和输运。"]}
    chosen = {s: y for s, y in original_y.items() if y > 0}
    if missing_present:
        out["reason"] = "完整组成含缺少候选 EOS 参数的物种。"
        if not request["allow_reduced"]:
            return out
        reduction = _reduction(baseline, original_y, supported)
        out["reduction"] = reduction
        if reduction["status"] != "available":
            return out
        chosen = reduction["composition_mass_fractions"]
        out["scope"] = "reduced_composition_screening"
    else:
        out["scope"] = "full_frozen_composition_screening"
    try:
        species = [supported[s] for s in chosen]
        candidate = ct.Solution(thermo=eos, species=species, kinetics=None)
        ideal = ct.Solution(thermo="ideal-gas", species=species, kinetics=None)
        candidate.TPY = baseline.T, baseline.P, chosen
        ideal.TPY = baseline.T, baseline.P, chosen
        props, ideal_props = _properties(candidate), _properties(ideal)
        domain = _gas_domain(candidate)
        out.update(status="computed", candidate=props, paired_ideal_baseline=ideal_props,
                   inlet_domain=domain, property_constraint="frozen_composition",
                   differences=_difference(props, ideal_props), composition_mass_fractions=deepcopy(chosen),
                   species_temperature_limits={"minimum_k": float(ideal.min_temp), "maximum_k": float(ideal.max_temp),
                                               "within_range": ideal.min_temp <= baseline.T <= ideal.max_temp},
                   reference_alignment={"method": "same_Cantera_species_standard_state",
                                        "enthalpy_residual_j_kg": props["enthalpy_j_kg"] - ideal_props["enthalpy_j_kg"],
                                        "entropy_residual_j_kg_k": props["entropy_j_kg_k"] - ideal_props["entropy_j_kg_k"]},
                   frozen_expansion=_expansion(candidate, pout, efficiency),
                   paired_ideal_expansion=_expansion(ideal, pout, efficiency))
        if not domain["within_declared_range"]:
            out["status"] = "computed_outside_declared_range"
        if out["frozen_expansion"]["status"] == out["paired_ideal_expansion"]["status"] == "computed":
            out["frozen_expansion"]["isentropic_drop_difference_j_kg"] = (
                out["frozen_expansion"]["isentropic_drop_j_kg"] - out["paired_ideal_expansion"]["isentropic_drop_j_kg"])
    except Exception as error:
        out["reason"] = f"候选 EOS 此站计算失败：{str(error)[:600]}"
    return out


def _refprop_data_files(CP, species_names):
    """Fingerprint only files actually needed; never embed proprietary contents."""
    roots = [CP.get_config_string(CP.ALTERNATIVE_REFPROP_PATH), os.environ.get("RPPREFIX", ""),
             os.environ.get("COOLPROP_REFPROP_ROOT", "")]
    library = CP.get_config_string(CP.ALTERNATIVE_REFPROP_LIBRARY_PATH)
    if library:
        roots.append(str(Path(library).parent))
    out = []
    for name in ["HMX.BNC"] + [_REFPROP_FILENAMES[s] + ".FLD" for s in species_names]:
        candidates = [Path(root) / folder / name for root in roots if root for folder in ("FLUIDS", "fluids", "")]
        path = next((p for p in candidates if p.is_file()), None)
        out.append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path else None,
                    "status": "fingerprinted" if path else "not_located"})
    return out


@contextmanager
def _strict_refprop_configuration(CP):
    """No estimated binary parameters or implicit alternate EOS, even if globally enabled."""
    choices = {"REFPROP_DONT_ESTIMATE_INTERACTION_PARAMETERS": True,
               "REFPROP_IGNORE_ERROR_ESTIMATED_INTERACTION_PARAMETERS": False,
               "REFPROP_USE_GERG": False, "REFPROP_USE_PENGROBINSON": False}
    previous = {key: CP.get_config_bool(getattr(CP, key)) for key in choices}
    threshold = CP.get_config_int(CP.REFPROP_ERROR_THRESHOLD)
    try:
        for key, value in choices.items():
            CP.set_config_bool(getattr(CP, key), value)
        CP.set_config_int(CP.REFPROP_ERROR_THRESHOLD, 0)
        yield choices | {"REFPROP_ERROR_THRESHOLD": 0}
    finally:
        for key, value in previous.items():
            CP.set_config_bool(getattr(CP, key), value)
        CP.set_config_int(CP.REFPROP_ERROR_THRESHOLD, threshold)


def _range_at(T, P, limits):
    try:
        return (limits["minimum_temperature_k"] <= T <= limits["maximum_temperature_k"]
                and 0 < P <= limits["maximum_pressure_pa"])
    except (KeyError, TypeError):
        return False


def _refprop_domain(CP, state, component_limits, mixture_limits):
    t, p = _number(state.T(), True), _number(state.p(), True)
    for value in (state.rhomass(), state.cpmass(), state.cvmass(), state.speed_sound(), state.isothermal_compressibility()):
        _number(value, True)
    phase, phase_note = None, "reported_by_backend"
    try:
        phase = int(state.phase())
    except ValueError as error:
        if "does not implement calc_phase" not in str(error):
            raise
        phase_note = "not_available_for_REFPROP_mixture_wrapper"
    quality = _number(float(state.Q()))
    if 0 <= quality <= 1:
        raise ValueError("REFPROP 返回两相质量干度，不能用于单相燃气膨胀对照。")
    allowed = {CP.iphase_gas, CP.iphase_liquid, CP.iphase_supercritical,
               CP.iphase_supercritical_gas, CP.iphase_supercritical_liquid}
    if phase is not None and phase not in allowed:
        raise ValueError("REFPROP 未确认稳定单相，不能据此给出燃气膨胀对照。")
    components = {name: _range_at(t, p, limits) for name, limits in component_limits.items()}
    return {"temperature_k": t, "pressure_pa": p, "phase_code": phase,
            "phase_classification": phase_note, "quality_indicator": quality,
            "within_declared_range": _range_at(t, p, mixture_limits) and bool(components) and all(components.values()),
            "component_range_checks": components}


def _refprop_comparison(ct, baseline, original_y, capabilities, request, pout, efficiency):
    out = {"backend": "REFPROP", "status": "unavailable", "reaction_equilibrium": "not_performed",
           "full_composition_status": "unavailable", "model": deepcopy(capabilities.get("refprop", {})),
           "transport_properties": "not_calculated_or_validated",
           "mixture_EOS_accuracy_validation": "not_established",
           "notes": ["Cantera 完整组成用于交接；REFPROP 仅作固定组成物性和冻结膨胀对照。",
                     "焓熵在共同温压和相同组成处作常数参考平移；不可用于跨组成化学反应平衡。",
                     "各模型的理想气体热容数据可能不同；焓熵差异不等于单纯的 EOS 残余贡献。"]}
    if not capabilities.get("refprop", {}).get("available"):
        out["reason"] = capabilities.get("refprop", {}).get("reason", "本机 REFPROP 不可用。")
        return out
    missing = {s: y for s, y in original_y.items() if s not in _FLUIDS and y > 0}
    out["unsupported_species_mass_fractions"] = missing
    out["unsupported_mass_fraction"] = math.fsum(missing.values())
    chosen = {s: y for s, y in original_y.items() if y > 0}
    if missing:
        out["reason"] = "完整组成包含 REFPROP 接口未建立对应流体数据的物种。"
        if not request["allow_reduced"]:
            return out
        reduction = _reduction(baseline, original_y, _FLUIDS)
        out["reduction"] = reduction
        if reduction["status"] != "available":
            return out
        chosen = reduction["composition_mass_fractions"]
        out["scope"] = "reduced_composition_screening"
    else:
        out["full_composition_status"] = "supported"
        out["scope"] = "full_frozen_composition_screening"
    try:
        import CoolProp.CoolProp as CP
        # Every REFPROP call (including constructing/loading a mixture) is under
        # the same native-library lock used by the pure-fluid validation backend.
        with _PROPERTY_LOCK, _strict_refprop_configuration(CP) as config:
            out["model"]["strict_runtime_configuration"] = config
            _checkpoint()
            names = list(chosen)
            out["model"]["mixture_data_files"] = _refprop_data_files(CP, names)
            # REFPROP's mixture Tmin/Tmax may be composition weighted. Preserve
            # each pure-fluid limit as well; averaging does not validate a trace.
            component_limits = {}
            for name in names:
                _checkpoint()
                try:
                    pure = CP.AbstractState("REFPROP", _FLUIDS[name])
                    component_limits[name] = {"minimum_temperature_k": pure.Tmin(),
                                              "maximum_temperature_k": pure.Tmax(), "maximum_pressure_pa": pure.pmax()}
                except Exception as error:
                    component_limits[name] = {"status": "unavailable", "reason": str(error)[:300]}
            out["component_limits"] = component_limits
            # Preserve this diagnosis even if loading the complete mixture fails.
            out["component_range_checks"] = {name: _range_at(baseline.T, baseline.P, limits)
                                               for name, limits in component_limits.items()}
            out["within_all_component_declared_ranges"] = all(out["component_range_checks"].values())
            ideal = ct.Solution(thermo="ideal-gas", species=[baseline.species(s) for s in names], kinetics=None)
            ideal.TPY = baseline.T, baseline.P, chosen
            state = CP.AbstractState("REFPROP", "&".join(_FLUIDS[s] for s in names))
            state.set_mole_fractions([float(ideal.X[ideal.species_index(s)]) for s in names])
            # REFPROP and Cantera have slightly different atomic weights. Keep
            # identical molecule amounts and report the resulting mass difference.
            out["composition_mole_fractions"] = {s: float(ideal.X[ideal.species_index(s)]) for s in names}
            out["refprop_mass_fractions"] = dict(zip(names, map(float, state.get_mass_fractions())))
            out["composition_mass_fraction_difference_due_molar_masses"] = {
                s: out["refprop_mass_fractions"][s] - chosen[s] for s in names}
            out["composition_mass_fractions"] = deepcopy(chosen)
            reference_T, reference_P = min(500.0, float(baseline.T)), 1000.0
            state.update(CP.PT_INPUTS, reference_P, reference_T)
            h_ref, s_ref = state.hmass(), state.smass()
            ideal.TP = reference_T, reference_P
            h_shift, s_shift = ideal.enthalpy_mass - h_ref, ideal.entropy_mass - s_ref
            out["reference_alignment"] = {"method": "constant_offsets_at_common_frozen_reference",
                                          "temperature_k": reference_T, "pressure_pa": reference_P,
                                          "enthalpy_offset_j_kg": h_shift, "entropy_offset_j_kg_k": s_shift}
            ideal.TP = baseline.T, baseline.P
            ideal_props = _properties(ideal)
            state.update(CP.PT_INPUTS, baseline.P, baseline.T)
            h, s = state.hmass(), state.smass()
            props = {"temperature_k": state.T(), "pressure_pa": state.p(), "density_kg_m3": state.rhomass(),
                     "compressibility_factor": state.compressibility_factor(), "cp_j_kg_k": state.cpmass(),
                     "cv_j_kg_k": state.cvmass(), "speed_sound_m_s": state.speed_sound(),
                     "enthalpy_j_kg": h + h_shift, "entropy_j_kg_k": s + s_shift,
                     "raw_enthalpy_j_kg": h, "raw_entropy_j_kg_k": s,
                     "molar_mass_kg_kmol": state.molar_mass() * 1000}
            for value in props.values():
                _number(value)
            limits = {"minimum_temperature_k": state.Tmin(), "maximum_temperature_k": state.Tmax(),
                      "maximum_pressure_pa": state.pmax()}
            domain = _refprop_domain(CP, state, component_limits, limits)
            within = limits["minimum_temperature_k"] <= baseline.T <= limits["maximum_temperature_k"] and baseline.P <= limits["maximum_pressure_pa"]
            component_range_known = all("maximum_temperature_k" in value for value in component_limits.values())
            within_components = component_range_known and all(
                value["minimum_temperature_k"] <= baseline.T <= value["maximum_temperature_k"] and baseline.P <= value["maximum_pressure_pa"]
                for value in component_limits.values())
            within = within and within_components
            out.update(status="computed" if within else "computed_outside_declared_range", candidate=props,
                       inlet_domain=domain, property_constraint="frozen_composition",
                       paired_ideal_baseline=ideal_props, differences=_difference(props, ideal_props),
                       limits=limits, within_backend_declared_range=_range_at(baseline.T, baseline.P, limits),
                       within_all_component_declared_ranges=within_components,
                       reference_within_all_component_declared_ranges=component_range_known and all(
                           value["minimum_temperature_k"] <= reference_T <= value["maximum_temperature_k"] and reference_P <= value["maximum_pressure_pa"]
                           for value in component_limits.values()),
                       paired_ideal_expansion=_expansion(ideal, pout, efficiency))
            # Approximate residuals use an explicit dilute pressure at the same T;
            # REFPROP's CoolProp wrapper does not expose hmolar_residual.
            try:
                state.update(CP.PT_INPUTS, reference_P, baseline.T)
                out["dilute_limit_departure"] = {
                    "status": "computed", "reference_pressure_pa": reference_P,
                    "enthalpy_departure_j_kg": h - state.hmass(),
                    "entropy_departure_j_kg_k": s - state.smass() + state.gas_constant() / state.molar_mass() * math.log(baseline.P / reference_P),
                    "method": "finite_low_pressure_approximation_at_same_T_and_composition"}
            except Exception as error:
                out["dilute_limit_departure"] = {"status": "unavailable", "reason": str(error)[:400]}
            if pout is not None:
                try:
                    def isentropic(p):
                        state.update(CP.PSmass_INPUTS, p, s)
                        return h - state.hmass(), state.smass() - s
                    def actual(p, drop):
                        state.update(CP.HmassP_INPUTS, h - drop, p)
                    expansion = _sample_expansion(float(baseline.P), pout, efficiency, isentropic, actual,
                                                 lambda: _refprop_domain(CP, state, component_limits, limits))
                    if expansion["status"] == out["paired_ideal_expansion"]["status"] == "computed":
                        expansion["isentropic_drop_difference_j_kg"] = expansion["isentropic_drop_j_kg"] - out["paired_ideal_expansion"]["isentropic_drop_j_kg"]
                    out["frozen_expansion"] = expansion
                except Exception as error:
                    out["frozen_expansion"] = {"status": "unavailable", "reason": str(error)[:600]}
            else:
                out["frozen_expansion"] = {"status": "not_applicable"}
            _checkpoint()
    except Exception as error:
        out["reason"] = f"本机 REFPROP 混合物或该状态不可计算：{str(error)[:600]}"
    return out


def _carbon_screen(ct, text, phase, source, baseline):
    def mass(mix, i):
        return float(mix.phase_moles(i) * mix.phase(i).mean_molecular_weight)
    def enthalpy(mix):
        return float(sum(mix.phase_moles(i) * mix.phase(i).enthalpy_mole for i in range(mix.n_phases)))
    def inventory(mix):
        elements = sorted({e for i in range(mix.n_phases) for e in mix.phase(i).element_names})
        return {e: sum(mass(mix, i) * mix.phase(i).elemental_mass_fraction(e)
                       for i in range(mix.n_phases) if e in mix.phase(i).element_names) for e in elements}
    out = {"status": "unavailable", "scope": "ideal_gas_plus_graphite_HP_equilibrium_sensitivity",
           "not_deposition_rate": True, "mass_basis_kg": 1.0,
           "notes": ["同元素、同总焓、同压力的相集合敏感性，不是实际积碳率、沉积量或寿命预测。",
                     "气相 EOS 仍为理想混合物；只加入石墨，未穷尽全部凝聚相。"]}
    try:
        graphite_path = Path(ct.__file__).resolve().parent / "data" / "graphite.yaml"
        graphite_text = graphite_path.read_text(encoding="utf-8")
        out["graphite_file_sha256"] = hashlib.sha256(graphite_path.read_bytes()).hexdigest()
        gas = _phase(ct, text, phase)
        gas.HPY = source["enthalpy_j_kg"], source["pressure_pa"], source["composition_mass_fractions"]
        original_elements = _elements(gas)
        _checkpoint()
        gas.equilibrate("HP", solver="gibbs", rtol=1e-10, max_steps=3000)
        out["gas_only_hp"] = {"temperature_k": float(gas.T), "enthalpy_j_kg": float(gas.enthalpy_mass),
                              "enthalpy_residual_j_kg": float(gas.enthalpy_mass - source["enthalpy_j_kg"]),
                              "element_mass_fraction_residuals": {e: _elements(gas)[e] - v for e, v in original_elements.items()},
                              "composition_mass_fractions": _composition(gas)}
        attempts = []
        for solver in ("gibbs", "vcs"):
            _checkpoint()
            gas = _phase(ct, text, phase)
            gas.HPY = source["enthalpy_j_kg"], source["pressure_pa"], source["composition_mass_fractions"]
            carbon = ct.Solution(yaml=graphite_text)
            mix = ct.Mixture([(gas, 1.0 / gas.mean_molecular_weight), (carbon, 0.0)])
            mix.T, mix.P = gas.T, source["pressure_pa"]
            before_mass = sum(mass(mix, i) for i in range(mix.n_phases))
            before_h, before_e = enthalpy(mix), inventory(mix)
            try:
                mix.equilibrate("HP", solver=solver, rtol=1e-10, max_steps=3000, max_iter=500)
                after_mass = sum(mass(mix, i) for i in range(mix.n_phases))
                after_h, after_e = enthalpy(mix), inventory(mix)
                residuals = {e: after_e[e] - v for e, v in before_e.items()}
                carbon_mass = mass(mix, 1)
                h_scale = max(abs(before_h), 1e5)
                closed = abs(after_mass - before_mass) < 1e-7 and max(map(abs, residuals.values())) < 1e-7 and abs(after_h - before_h) / h_scale < 1e-6
                result = {"solver": solver, "temperature_k": float(mix.T), "pressure_pa": float(mix.P),
                          "gas_mass_kg": mass(mix, 0), "solid_carbon_mass_kg": carbon_mass,
                          "total_equilibrium_mass_kg": after_mass,
                          "solid_carbon_mass_fraction_total_gas_plus_solid": carbon_mass / after_mass,
                          "fraction_of_input_element_carbon_in_solid": carbon_mass / before_e["C"] if before_e.get("C", 0) > 0 else None,
                          "enthalpy_before_j": before_h, "enthalpy_after_j": after_h,
                          "enthalpy_residual_j": after_h - before_h,
                          "enthalpy_scaled_residual": (after_h - before_h) / h_scale,
                          "mass_residual_kg": after_mass - before_mass,
                          "element_mass_residual_kg": residuals,
                          "gas_composition_mass_fractions": _composition(gas), "conservation_passed": closed}
                attempts.append({"solver": solver, "status": "computed" if closed else "closure_failed"})
                if closed:
                    out.update(status="computed", result=result, solver_attempts=attempts)
                    break
            except Exception as error:
                attempts.append({"solver": solver, "status": "unavailable", "reason": str(error)[-600:]})
        else:
            out.update(reason="气固 HP 平衡求解或守恒检查未通过。", solver_attempts=attempts)
        _checkpoint()
    except Exception as error:
        out["reason"] = f"气固平衡筛查不可用：{str(error)[:600]}"
    return out


def _station_source(record, station, ct):
    sid, label, side, endpoint = station
    calculation = record.get("calculation", {})
    state = calculation.get("states", {}).get(sid, {})
    capture = record.get("turbine_gas_properties", {}).get("turbines", {}).get(side, {})
    metadata = state.get("metadata", {})
    state_model = metadata.get("model", {})
    capture_model = capture.get("model", {})
    model = capture_model or (state_model | {"gas_eos": state_model.get("eos")})
    filename, phase = model.get("mechanism"), model.get("phase")
    if filename not in _MECHANISMS or not isinstance(phase, str):
        raise ValueError("缺少支持的、保存时捕获的 Cantera 模型身份；不借用当前默认数据。")
    path = Path(ct.__file__).resolve().parent / "data" / filename
    text = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if model.get("mechanism_sha256") != sha:
        raise ValueError("保存的机制指纹缺失或与本机文件不同；不能重新解释历史记录。")
    if model.get("gas_eos") != "ideal-gas":
        raise ValueError("来源记录未明确声明理想气相生产 EOS。")
    if state_model:
        for key in ("mechanism", "phase", "mechanism_sha256"):
            if state_model.get(key) != model.get(key):
                raise ValueError("逐端口 model 与涡轮捕获模型身份不一致。")
        if state_model.get("eos") != model["gas_eos"] or state_model.get("composition_basis") != "mass_fraction":
            raise ValueError("逐端口 EOS 或组成口径与筛查基线不一致。")
        expected_entropy_reference = f"cantera_species_standard_entropy:{sha}:{phase}"
        if state_model.get("entropy_reference_id") and state_model["entropy_reference_id"] != expected_entropy_reference:
            raise ValueError("逐端口熵参考与保存机制身份不一致。")
    if metadata.get("composition_complete") is False:
        raise ValueError("来源明确标记组成不完整，不能进行完整燃气交接筛查。")
    state_mechanism = state.get("metadata", {}).get("thermochemical_data")
    if state_mechanism and state_mechanism != filename:
        raise ValueError("站点与涡轮捕获的物种数据身份不同。")
    y = state.get("composition")
    if not isinstance(y, dict) or not y:
        raise ValueError("保存站点缺少完整组成。")
    y = {s: _number(v) for s, v in y.items()}
    if any(not isinstance(s, str) or v < 0 for s, v in y.items()) or abs(math.fsum(y.values()) - 1) > 1e-8:
        raise ValueError("保存组成含负值、非规范物种名或质量分数不闭合。")
    source = {"temperature_k": _number(state.get("temperature"), True),
              "pressure_pa": _number(state.get("pressure"), True),
              "enthalpy_j_kg": _number(state.get("enthalpy")),
              "composition_mass_fractions": deepcopy(y),
              "mass_flow_kg_s": _number(state.get("mass_flow"), True),
              "reference_id": metadata.get("reference_id") or state_model.get("enthalpy_reference_id"),
              "mechanism": filename, "mechanism_sha256": sha, "phase": phase}
    if not source["reference_id"]:
        raise ValueError("保存状态缺少焓参考身份；不能假定为本机当前基准。")
    expected_reference = f"cantera_{Path(filename).stem}_coolprop_298p15K_ideal_reference_v2"
    if source["reference_id"] != expected_reference or (state_model and state_model.get("enthalpy_reference_id") != source["reference_id"]):
        raise ValueError("保存状态的化学焓参考与机制身份不一致。")
    gas = _phase(ct, text, phase)
    if gas.thermo_model != "ideal-gas":
        raise ValueError("该筛查的生产基线必须是明确记录的 Cantera 理想气相。")
    if any(s not in gas.species_names for s in y):
        raise ValueError("保存组成含所选数据中不存在的物种。")
    gas.TPY = source["temperature_k"], source["pressure_pa"], y
    mismatch = float(gas.enthalpy_mass - source["enthalpy_j_kg"])
    if abs(mismatch) > max(1e-3, abs(source["enthalpy_j_kg"]) * 1e-8):
        raise ValueError("保存的 T/p/组成 与保存比焓不一致；先修复交接，不能替换焓后继续筛查。")
    saved_entropy = metadata.get("entropy_JkgK")
    if saved_entropy is not None:
        saved_entropy = _number(saved_entropy)
        if abs(float(gas.entropy_mass) - saved_entropy) > max(1e-5, abs(saved_entropy) * 1e-8):
            raise ValueError("逐端口保存熵与 T/p/组成 回算不一致。")
        source["entropy_j_kg_k"] = saved_entropy
    if capture:
        saved_endpoint = capture.get("endpoint_properties", {}).get(endpoint, {})
        for key in ("temperature_k", "pressure_pa", "enthalpy_j_kg"):
            value = _number(saved_endpoint.get(key), key != "enthalpy_j_kg")
            if abs(value - source[key]) > max(1e-3, abs(source[key]) * 1e-8):
                raise ValueError("状态点与涡轮保存捕获数据不一致。")
        if "entropy_j_kg_k" in saved_endpoint:
            entropy = _number(saved_endpoint["entropy_j_kg_k"])
            if abs(entropy - float(gas.entropy_mass)) > max(1e-5, abs(entropy) * 1e-8):
                raise ValueError("涡轮捕获熵与 T/p/组成 回算不一致。")
            source.setdefault("entropy_j_kg_k", entropy)
        captured_y = capture.get("composition_mass_fractions", {})
        if not isinstance(captured_y, dict) or any(abs(_number(captured_y.get(s, 0)) - y.get(s, 0)) > 1e-12 for s in set(y) | set(captured_y)):
            raise ValueError("状态点组成与保存涡轮冻结组成不一致。")
    source["model"] = deepcopy(state_model or model)
    output = calculation.get("components", {}).get(f"{side}_preburner_turbine", {}).get("outputs", {})
    source["state_basis"] = state.get("metadata", {}).get("state_basis") or output.get("state_basis", "unspecified")
    source["state_assumption"] = output.get("state_assumption")
    source["reconstructed_enthalpy_difference_j_kg"] = mismatch
    source["elements_mass_fractions"] = _elements(gas)
    source["element_mass_flows_kg_s"] = {e: v * source["mass_flow_kg_s"] for e, v in _elements(gas).items()}
    source["enthalpy_flow_w"] = source["enthalpy_j_kg"] * source["mass_flow_kg_s"]
    pout = efficiency = None
    if endpoint == "inlet" and output.get("state_basis") == "total_to_total":
        pout = _number(output.get("p_out_Pa"), True)
        efficiency = _number(output.get("eta_turbine"), True)
        if not 0 < pout < source["pressure_pa"] or efficiency > 1:
            raise ValueError("保存涡轮边界压比或效率无效。")
    return source, gas, text, phase, pout, efficiency


def screen_gas_record(source_result: dict, request: dict | None = None, progress=None) -> dict:
    """Run four recorded interfaces; cancellation escapes, physics failures persist."""
    normalized = normalize_screen_request(request)
    if not isinstance(source_result, dict):
        raise ValueError("筛查来源必须是保存计算结果。")
    _checkpoint()
    result = {"schema_version": 1, "status": "unavailable", "request": normalized,
              "model_scope": "record_bound_screening_only_no_production_EOS_change",
              "provenance": {"source_result_sha256": _fingerprint(source_result), "sources": _SOURCES},
              "stations": [], "notes": ["筛查完成不等于准确性验证通过；候选物性不回写循环、几何或设计任务书。",
                                             "完整物种失败与缩减候选结果分别保存；所有组成、元素和焓均以来源记录为准。"]}
    try:
        import cantera as ct
    except ImportError:
        result["reason"] = "Cantera 不可用。"
        return result
    result["provenance"]["cantera_version"] = ct.__version__
    capabilities = property_capabilities() if normalized["compare_refprop"] else {}
    result["provenance"]["property_capabilities"] = capabilities
    selected = [s for s in STATIONS if s[0] in normalized["stations"]]
    species_cache = {}
    for index, station in enumerate(selected):
        _checkpoint()
        sid, label, side, endpoint = station
        item = {"id": sid, "label": label, "status": "unavailable", "baseline": None,
                "eos_comparisons": [], "carbon_screen": {"status": "not_requested"}, "notes": []}
        try:
            source, gas, text, phase, pout, efficiency = _station_source(source_result, station, ct)
            item["baseline"] = _properties(gas) | source
            item["baseline"]["source"] = "immutable_saved_record_reconstructed_with_matching_mechanism"
            if normalized["compare_cubic"]:
                cache_key = source["mechanism_sha256"]
                if cache_key not in species_cache:
                    species_cache[cache_key] = _critical_species(ct, gas)
                prepared, sources = species_cache[cache_key]
                for eos in ("Peng-Robinson", "Redlich-Kwong"):
                    _checkpoint()
                    item["eos_comparisons"].append(_cubic_comparison(ct, gas, source["composition_mass_fractions"],
                                                                   prepared, sources, eos, normalized, pout, efficiency))
            if normalized["compare_refprop"]:
                item["eos_comparisons"].append(_refprop_comparison(ct, gas, source["composition_mass_fractions"],
                                                                 capabilities, normalized, pout, efficiency))
            if normalized["carbon"]:
                item["carbon_screen"] = _carbon_screen(ct, text, phase, source, gas)
            computed = any(c["status"].startswith("computed") for c in item["eos_comparisons"]) or item["carbon_screen"]["status"] == "computed"
            item["status"] = "screened_with_limitations" if computed else "unavailable"
        except Exception as error:
            item["reason"] = str(error)[:1000]
        result["stations"].append(item)
        if progress:
            progress(index + 1, len(selected), {"station_id": sid, "label": label, "status": item["status"]})
        _checkpoint()
    if any(s["status"] == "screened_with_limitations" for s in result["stations"]):
        result["status"] = "screened_with_limitations"
    # Reject non-finite native results instead of serializing NaN as apparent data.
    json.dumps(result, allow_nan=False)
    return result
