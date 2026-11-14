"""Traceable, complete-species Peng--Robinson phase construction.

The bundled equilibrium mechanisms do not contain PR data for every species.
Full-species PR reports that gap and refuses to drop species, assign zero
acentric factors, borrow parameters, or fall back to an ideal gas. The separate
pr_supported_v1 policy is an explicitly restricted candidate, paired with an
ideal phase containing exactly the same thermochemical species.
Critical-parameter declarations establish provenance, not mixture accuracy.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import threading

from lre_workbench.properties.datasets import METHANE_DATASETS


PR_EOS = "Peng-Robinson"
BM_EOS = "Peng-Robinson-Boston-Mathias"
PR_EOS_MODELS = (PR_EOS, BM_EOS)
PR_SUPPORTED_V1 = (
    "H2", "O2", "H2O", "CH4", "CO", "CO2", "CH2O", "CH3OH", "C2H2",
    "C2H4", "C2H6", "NH3", "N2O", "N2", "AR", "C3H8",
)
_LOCAL = threading.local()
_COOLPROP_NAMES = {
    "H2": "Hydrogen", "O2": "Oxygen", "H2O": "Water", "CH4": "Methane",
    "CO": "CarbonMonoxide", "CO2": "CarbonDioxide", "CH3OH": "Methanol",
    "C2H2": "Acetylene", "C2H4": "Ethylene", "C2H6": "Ethane",
    "N2": "Nitrogen", "AR": "Argon", "C3H8": "Propane",
    "NH3": "Ammonia", "N2O": "NitrousOxide",
}
_PARAMETERS = ("critical_temperature_K", "critical_pressure_Pa", "acentric_factor")


class GasEOSParameterError(ValueError):
    """A declared phase cannot be built without complete PR evidence."""

    def __init__(self, coverage):
        self.coverage = deepcopy(coverage)
        missing = ", ".join(coverage["missing_species"])
        super().__init__(
            f"Peng–Robinson 参数未完整覆盖 {coverage['mechanism']}："
            f"{coverage['supported_species_count']}/{coverage['species_count']}，"
            f"缺少 {missing}。不删减物种、不估填参数、不回退理想气体。")


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _validate_parameters(values, species):
    if not isinstance(values, dict):
        raise ValueError(f"{species}: PR 参数必须为对象。")
    out = {}
    for key in _PARAMETERS:
        value = values.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or (key != "acentric_factor" and value <= 0)):
            raise ValueError(f"{species}: PR 参数 {key} 缺失或无效。")
        out[key] = float(value)
    return out


@lru_cache(maxsize=2)
def _bundled_data(dataset):
    """Private cached declarations; callers receive copies, never mutable states."""
    if dataset not in METHANE_DATASETS:
        raise ValueError(f"不支持的甲烷热化学数据集：{dataset!r}")
    import cantera as ct
    from ruamel.yaml import YAML

    definition = METHANE_DATASETS[dataset]
    directory = Path(ct.__file__).resolve().parent / "data"
    raw = (directory / definition["file"]).read_bytes()
    ideal = ct.Solution(yaml=raw.decode("utf-8"), name=definition["phase"])
    critical_raw = (directory / "critical-properties.yaml").read_bytes()
    critical_doc = YAML(typ="safe").load(critical_raw.decode("utf-8"))
    critical = {row["name"].upper(): row["critical-parameters"]
                for row in critical_doc["species"]}
    try:
        import CoolProp
        import CoolProp.CoolProp as CP
    except ImportError:
        CP = None

    parameters = {}
    unavailable = {}
    for species in ideal.species_names:
        entry = critical.get(species.upper())
        if entry is not None and all(key in entry for key in
                ("critical-temperature", "critical-pressure", "acentric-factor")):
            values = {"critical_temperature_K": entry["critical-temperature"],
                      "critical_pressure_Pa": entry["critical-pressure"],
                      "acentric_factor": entry["acentric-factor"]}
            source = {
                "title": "Cantera bundled critical-properties.yaml",
                "url": f"https://github.com/Cantera/cantera/blob/v{ct.__version__}/data/critical-properties.yaml",
                "locator": species, "method": "bundled_critical_parameters",
                "file_sha256": hashlib.sha256(critical_raw).hexdigest(),
                "upstream_reference": entry.get("source"),
            }
        elif CP is not None and species in _COOLPROP_NAMES:
            fluid = _COOLPROP_NAMES[species]
            try:
                values = {"critical_temperature_K": float(CP.PropsSI("Tcrit", fluid)),
                          "critical_pressure_Pa": float(CP.PropsSI("Pcrit", fluid)),
                          "acentric_factor": float(CP.PropsSI("acentric", fluid))}
                source = {"title": "CoolProp HEOS critical parameters", "url": "https://coolprop.org/fluid_properties/fluids/" + fluid + ".html",
                          "locator": fluid, "method": "pure_fluid_EOS_critical_parameters",
                          "coolprop_version": CoolProp.__version__,
                          "upstream_reference": CP.get_fluid_param_string(fluid, "BibTeX-EOS"),
                          "file_sha256": hashlib.sha256(CP.get_fluid_param_string(fluid, "JSON").encode()).hexdigest()}
            except Exception as exc:
                unavailable[species] = str(exc)
                continue
        else:
            unavailable[species] = "无完整、可追溯的 Tc、Pc、偏心因子声明；RK 系数不能直接作为 PR 参数。"
            continue
        parameters[species] = _validate_parameters(values, species) | {"source": source}
    return {
        "dataset": dataset, "mechanism": definition["file"], "phase": definition["phase"],
        "mechanism_sha256": hashlib.sha256(raw).hexdigest(), "cantera_version": ct.__version__,
        "species_names": list(ideal.species_names),
        "species_data": [json.loads(json.dumps(dict(sp.input_data))) for sp in ideal.species()],
        "parameters": parameters, "unavailable_reasons": unavailable,
    }


def pr_parameter_coverage(dataset="gri30_highT"):
    """Audit ALL allowed equilibrium species, including currently absent ones."""
    data = deepcopy(_bundled_data(dataset))
    missing = [name for name in data["species_names"] if name not in data["parameters"]]
    identity = {"eos": PR_EOS, "mechanism_sha256": data["mechanism_sha256"],
                "species_order": data["species_names"], "species_parameters": data["parameters"],
                "mixing_rule": "Cantera_default_quadratic_attraction_linear_covolume",
                "binary_interactions": "default_geometric_attraction_unfitted"}
    return {"schema_version": 1, "status": "complete" if not missing else "incomplete",
            "eos": PR_EOS, "dataset": dataset, "mechanism": data["mechanism"],
            "phase": data["phase"], "mechanism_sha256": data["mechanism_sha256"],
            "cantera_version": data["cantera_version"], "species_count": len(data["species_names"]),
            "supported_species_count": len(data["parameters"]), "species_order": data["species_names"],
            "missing_species": missing, "species_parameters": data["parameters"],
            "missing_reasons": data["unavailable_reasons"],
            "eos_parameter_sha256": _fingerprint(identity),
            "mixing_rule": identity["mixing_rule"], "binary_interactions": identity["binary_interactions"],
            "validation_status": "not_validated",
            "notes": ["参数覆盖不代表高压化学平衡、混合物相稳定性或输运精度已验证。",
                      "物种集必须完整；不会删减痕量物种、借用同类物种参数或假定偏心因子为零。"]}


def _construct_phase(data, coverage):
    import cantera as ct

    species = []
    for original in data["species_data"]:
        item = deepcopy(original)
        name = item["name"]
        values = coverage["species_parameters"][name]
        # Preserve thermochemistry and composition exactly; replace only the EOS
        # declaration so native automatic fallback cannot import other values.
        item.pop("equation-of-state", None)
        item["critical-parameters"] = {
            "critical-temperature": values["critical_temperature_K"],
            "critical-pressure": values["critical_pressure_Pa"],
            "acentric-factor": values["acentric_factor"],
        }
        species.append(ct.Species.from_dict(item))
    gas = ct.Solution(thermo=PR_EOS, species=species, kinetics=None,
                      transport_model="mixture-averaged", name=data["phase"])
    if gas.species_names != data["species_names"]:
        raise RuntimeError("PR 构建改变了允许物种集合或顺序。")
    return gas


def build_gas(dataset="gri30_highT", eos="ideal", species_policy="auto"):
    """Return (thread-local Solution, independent identity dictionary).

    ``auto`` selects full ideal chemistry or the explicit PR candidate policy.
    The restricted ideal phase is a control for separating species-set changes
    from EOS changes. Neither candidate is certified production thermochemistry.
    No composition-dependent filtering is performed: the 16 species are fixed.
    """
    import cantera as ct

    aliases = {"ideal": "ideal-gas", "ideal-gas": "ideal-gas",
               "peng_robinson": PR_EOS, PR_EOS: PR_EOS,
               "peng_robinson_boston_mathias": BM_EOS, BM_EOS: BM_EOS}
    if eos not in aliases:
        raise ValueError(f"不支持的燃气 EOS：{eos!r}")
    native_eos = aliases[eos]
    if species_policy == "auto":
        species_policy = "full" if native_eos == "ideal-gas" else "pr_supported_v1"
    if species_policy not in ("full", "pr_supported_v1"):
        raise ValueError(f"不支持的燃气物种策略：{species_policy!r}")
    lookup = (dataset, native_eos, species_policy)
    if lookup in getattr(_LOCAL, "lookup", {}):
        gas, saved_identity = _LOCAL.gases[_LOCAL.lookup[lookup]]
        return gas, deepcopy(saved_identity)
    data = deepcopy(_bundled_data(dataset))
    coverage = pr_parameter_coverage(dataset)
    original_species = list(data["species_names"])
    candidate = species_policy == "pr_supported_v1"
    if candidate:
        selected = [name for name in original_species if name in PR_SUPPORTED_V1]
        if set(selected) != set(PR_SUPPORTED_V1):
            raise ValueError("所选机制不能完整提供 pr_supported_v1 的固定物种集合。")
        data["species_names"] = selected
        data["species_data"] = [sp for sp in data["species_data"] if sp["name"] in selected]
    missing = [name for name in data["species_names"] if name not in data["parameters"]]
    if (native_eos in PR_EOS_MODELS or candidate) and missing:
        selected_coverage = coverage | {"missing_species": missing,
            "species_count": len(data["species_names"]),
            "supported_species_count": len(data["species_names"]) - len(missing)}
        raise GasEOSParameterError(selected_coverage)
    selected_parameters = {name: data["parameters"][name] for name in data["species_names"]
                           if name in data["parameters"]}
    policy_identity = {"policy": species_policy, "mechanism_sha256": data["mechanism_sha256"],
                       "species_names": data["species_names"]}
    identity = {
        "mechanism": data["mechanism"], "phase": data["phase"],
        "mechanism_sha256": data["mechanism_sha256"], "cantera_version": ct.__version__,
        "eos": native_eos, "gas_eos": ("peng_robinson_boston_mathias" if native_eos == BM_EOS else
                                      "peng_robinson" if native_eos == PR_EOS else "ideal"),
        "species_policy": species_policy, "species_names": data["species_names"],
        "species_count": len(data["species_names"]),
        "species_policy_sha256": _fingerprint(policy_identity),
        "excluded_species": [name for name in original_species if name not in data["species_names"]],
        "candidate_model": candidate, "validation_status": "not_validated",
        "equilibrium_scope": "restricted_gas_phase_candidate" if candidate else "gas_phase_only",
        "enthalpy_reference_id": f"cantera_{dataset}_coolprop_298p15K_ideal_reference_v2",
        "composition_basis": "mass_fraction",
        "transport_model": "mixture-averaged",
        "transport_scope": "dilute-gas correlation; high-pressure transport not validated",
        "phase_scope": "single homogeneous phase; no flash or global multiphase stability verification",
        "species_parameters": selected_parameters if (candidate or native_eos in PR_EOS_MODELS) else {},
        "eos_parameter_sha256": _fingerprint(selected_parameters) if (candidate or native_eos in PR_EOS_MODELS) else None,
        "mixing_rule": coverage["mixing_rule"] if native_eos in PR_EOS_MODELS else "ideal_mixture",
        "binary_interactions": coverage["binary_interactions"] if native_eos in PR_EOS_MODELS else "not_applicable",
        "limitations": (["固定16物种候选；自由基等37物种未纳入允许的化学平衡集合。",
                         "必须与同一16物种理想模型配对，另行区分物种集合效应和EOS效应。"] if candidate else [])
                       + (["默认二元混合规则未标定；高压平衡、相稳定与输运未获独立验证。"] if native_eos in PR_EOS_MODELS else []),
    }
    if native_eos == BM_EOS:
        from lre_workbench.properties.pr_boston_mathias import BM_MODEL_VERSION, BM_SOURCE
        model = {"version": BM_MODEL_VERSION, "alpha": "published_Boston_Mathias_supercritical_C1",
                 "subcritical": "standard_PR_kappa_1976_1978",
                 "supercritical": "alpha=exp(2*c*(1-Tr**d));d=1+kappa/2;c=1-1/d",
                 "mixing_rule": "quadratic_geometric_attraction_linear_covolume_kij_zero",
                 "phase_root": "largest_locally_stable_gas_root",
                 "source": BM_SOURCE}
        identity.update(eos_model=model, eos_model_sha256=_fingerprint(model),
                        property_backend="independent_PR_BM_residual_Helmholtz_with_Cantera_NASA_reference",
                        equilibrium_backend="nonideal_fugacity_element_constrained_KKT",
                        mixing_rule=model["mixing_rule"])
        identity["limitations"].append("发表的 Boston–Mathias 延拓在各物种 Tc 处仅 C1 连续；焓熵连续，热容/声速可有有限跳变。")
    key = _fingerprint(identity)
    if not hasattr(_LOCAL, "gases"):
        _LOCAL.gases = {}
        _LOCAL.lookup = {}
    if key not in _LOCAL.gases:
        if native_eos == BM_EOS:
            from lre_workbench.properties.pr_boston_mathias import PengRobinsonBMPhase
            gas = PengRobinsonBMPhase(data["species_data"], selected_parameters, data["phase"])
        elif native_eos == PR_EOS:
            gas = _construct_phase(data, coverage)
        else:
            species = [ct.Species.from_dict(deepcopy(sp)) for sp in data["species_data"]]
            gas = ct.Solution(thermo="ideal-gas", species=species, kinetics=None,
                              transport_model="mixture-averaged", name=data["phase"])
        gas.TP = 300.0, 101325.0
        _LOCAL.gases[key] = (gas, deepcopy(identity))
    _LOCAL.lookup[lookup] = key
    gas, saved_identity = _LOCAL.gases[key]
    return gas, deepcopy(saved_identity)


def gas_identity(gas):
    """Identify the exact cached instance, never infer identity from active inputs."""
    for instance, coverage in getattr(_LOCAL, "gases", {}).values():
        if gas is instance:
            return deepcopy(coverage)
    raise ValueError("未登记的燃气实例；不能借用当前默认参数身份。")


def equilibrate_hp(gas, h_target, pressure, Y_init, context="", *, temperature_bounds=(300.0, 5500.0)):
    """Safeguarded PR HP equilibrium using real-gas TP equilibria.

    Cantera's frozen-composition HP setter can leave an invalid state without
    throwing when pre-equilibrated hot products cannot reach the target h. Solve
    the equilibrium h(T,P) instead; never use a constant-Z caloric correction.
    The callback resets the original elemental inventory at every evaluation.
    Numerical acceptance is not global multiphase or EOS accuracy validation.
    """
    from scipy.optimize import brentq
    from lre_workbench.runtime.tasks import checkpoint

    if gas.thermo_model not in PR_EOS_MODELS:
        raise ValueError("此 HP 助手只接受 Peng-Robinson 气相。")
    if not math.isfinite(h_target) or not math.isfinite(pressure) or pressure <= 0:
        raise ValueError("PR HP 平衡要求有限目标焓和正压力。")
    low, high = temperature_bounds
    if not (math.isfinite(low) and math.isfinite(high) and 0 < low < high):
        raise ValueError("PR HP 平衡温度包络无效。")
    gas.TPY = (low + high) / 2, pressure, Y_init
    initial_y = gas.Y.copy()
    elements = [float(gas.elemental_mass_fraction(e)) for e in gas.element_names]
    evaluations = {}

    def evaluate(temperature):
        checkpoint()
        # Brent revisits its bracket endpoints and its returned root. Reuse
        # only exact temperatures from THIS HP call (same phase/P/h/elements).
        # Restore the complete state as callers rely on the final gas state.
        if temperature in evaluations:
            value, state = evaluations[temperature]
            gas.state = state
            return value
        errors = []
        # VCS handles the cold oxygen-rich bracket substantially better than
        # MultiPhaseEquil's Gibbs solver. Each retry restarts the same inventory,
        # and every successful native return still has to satisfy invariants.
        for solver in ("vcs", "gibbs"):
            try:
                gas.TPY = temperature, pressure, initial_y
                gas.equilibrate("TP", solver=solver, rtol=1e-10, max_steps=1000)
                checkpoint()
                if (not math.isclose(gas.T, temperature, rel_tol=1e-9, abs_tol=1e-7)
                        or not math.isclose(gas.P, pressure, rel_tol=1e-8, abs_tol=.1)
                        or not math.isfinite(gas.enthalpy_mass)):
                    raise ValueError("未保持指定温度、压力或有限焓。")
                if any(abs(float(gas.elemental_mass_fraction(e)) - expected) > 1e-8
                       for e, expected in zip(gas.element_names, elements)):
                    raise ValueError("元素库存未闭合。")
                value = float(gas.enthalpy_mass - h_target)
                evaluations[temperature] = value, gas.state.copy()
                return value
            except Exception as exc:
                errors.append(f"{solver}: {exc}")
        raise ValueError("PR TP 平衡失败：" + "；".join(errors))

    f_low, f_high = evaluate(low), evaluate(high)
    if f_low > 0 or f_high < 0:
        raise ValueError(f"PR HP {context} 目标焓不在 {low:g}–{high:g} K 的平衡包络内；不截温。")
    temperature = brentq(evaluate, low, high, xtol=1e-8, rtol=1e-12, maxiter=80)
    error = evaluate(temperature)
    if abs(error) > max(.05, abs(h_target) * 1e-8):
        raise ValueError("PR HP 平衡未满足目标焓残差。")
    if not all(math.isfinite(v) and v > 0 for v in
               (gas.cp_mass, gas.cv_mass, gas.isothermal_compressibility)):
        raise ValueError("PR HP 结果未通过局部单相稳定性必要条件。")


def _set_frozen(gas, target, pressure, kind):
    from scipy.optimize import brentq
    from lre_workbench.runtime.tasks import checkpoint

    if gas.thermo_model not in PR_EOS_MODELS:
        raise ValueError("此冻结状态助手只接受 Peng-Robinson 气相。")
    if not math.isfinite(target) or not math.isfinite(pressure) or pressure <= 0:
        raise ValueError("PR 冻结状态要求有限焓/熵和正压力。")
    original = gas.state.copy()
    original_temperature = float(gas.T)
    original_y = gas.Y.copy()
    attribute = "enthalpy_mass" if kind == "HP" else "entropy_mass"
    tolerance = max(.05, abs(target) * 1e-8) if kind == "HP" else max(1e-6, abs(target) * 1e-9)

    def accepted():
        return (math.isfinite(gas.T) and gas.T > 0
                and math.isclose(gas.P, pressure, rel_tol=1e-8, abs_tol=.1)
                and abs(float(getattr(gas, attribute)) - target) <= tolerance
                and max(abs(gas.Y - original_y)) <= 1e-12
                and all(math.isfinite(v) and v > 0 for v in
                        (gas.cp_mass, gas.cv_mass, gas.isothermal_compressibility)))

    try:
        checkpoint()
        setattr(gas, kind, (target, pressure))
        if accepted():
            return
    except Exception:
        pass
    gas.state = original

    def evaluate(temperature):
        checkpoint()
        gas.TPY = temperature, pressure, original_y
        if not math.isclose(gas.P, pressure, rel_tol=1e-8, abs_tol=.1):
            raise ValueError(f"PR 冻结 {kind} 状态未保持目标压力。")
        value = float(getattr(gas, attribute)) - target
        if not math.isfinite(value):
            raise ValueError(f"PR 冻结 {kind} 状态返回非有限焓/熵。")
        return value

    # A computational bracket is not a thermochemical validity declaration.
    # Callers retain their final-state species-temperature applicability audit.
    low, high = min(50., original_temperature / 4), max(6000., original_temperature * 2)
    try:
        f_low, f_high = evaluate(low), evaluate(high)
        if f_low > 0 or f_high < 0:
            raise ValueError(f"PR 冻结 {kind} 目标不在计算温度包络内。")
        temperature = brentq(evaluate, low, high, xtol=1e-8, rtol=1e-12, maxiter=80)
        evaluate(temperature)
        if not accepted():
            residual = float(getattr(gas, attribute)) - target
            raise ValueError(f"PR 冻结 {kind} 状态未满足焓/熵、压力、组成或局部稳定性检查："
                             f"T={gas.T:.9g} K，目标={target:.12g}，残差={residual:.9g}。"
                             "温度求根未获得连续 EOS 状态，可能跨越 PR 高温吸引项导数间断；不放宽残差或切换模型。")
    except Exception:
        gas.state = original
        raise


def set_frozen_hp(gas, enthalpy, pressure):
    """Set PR H/P without reactions, accepting only a verified native/root result."""
    _set_frozen(gas, enthalpy, pressure, "HP")


def set_frozen_sp(gas, entropy, pressure):
    """Set PR S/P without reactions, accepting only a verified native/root result."""
    _set_frozen(gas, entropy, pressure, "SP")
