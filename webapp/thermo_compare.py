"""Auditable independent chamber/nozzle comparison, not an engine certification.

Both backends receive identical elemental feed, mass O/F and chemical inlet
enthalpy. Native product databases remain a distinct comparison from a named,
restricted common gas-species benchmark; matching species does not match data.
No empirical backend, fabricated CEA result, or percent-difference pass verdict.
"""
from dataclasses import asdict
import hashlib
import importlib.metadata
import math
from pathlib import Path
import sys
import threading
import tempfile
import os
import warnings
import re
import json
from functools import lru_cache

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
import thermo
from propellants import get_propellant, propellant_models
from thermo_data import METHANE_DATASETS, methane_dataset_scope
from task_control import checkpoint

MODES = ("equilibrium", "frozen_chamber", "frozen_throat")
PRODUCT_POLICIES = ("native_databases", "matched_cho_gas")
THERMO_DATASETS = ("production", "nasa_gas_candidate")
# A deliberately small neutral C/H/O gas benchmark, NOT a complete product set
# for fuel-rich preburners, carbon deposition, ions or condensed-phase chemistry.
# Exact names exist in GRI30, Cantera's nasa_gas and the bundled CEA database.
COMMON_CHO_SPECIES = ("C", "CH", "CH2", "CH3", "CH4", "CO", "CO2", "H", "H2", "H2O",
                      "H2O2", "HCO", "HO2", "O", "O2", "OH")
# Verified against RocketCEA 1.2.3 py_cea.f DATA atmwt and thermo.inp.
# Normalize only the independent benchmark, not production engine calculations.
CEA_ATOMIC_WEIGHTS = {"C": 12.0107, "H": 1.00794, "O": 15.9994}
# CEA's input parser uses this historical psi/atm constant (py_cea.f, INPUT).
# Using the exact modern SI psi conversion would shift the actual CEA pressure.
CEA_PSIA_PER_ATM = 14.696006
METRICS = ("Tc_K", "Cstar_m_s", "Isp_vac_s", "Cf_vac", "MW_chamber_kg_kmol", "gamma_chamber")
MAX_AXIS_POINTS = 20
MAX_COMPARISON_POINTS = 60
_LOCK = threading.RLock()  # RocketCEA's FORTRAN and cached Cantera states are mutable.
_CEA_SCRATCH = None


def _prepare_cea_scratch():
    """Keep FORTRAN's fixed filenames private to this process, with an ASCII path."""
    global _CEA_SCRATCH
    if _CEA_SCRATCH is None:
        _CEA_SCRATCH = tempfile.TemporaryDirectory(prefix="lre-cea-", ignore_cleanup_errors=True)
    directory = _CEA_SCRATCH.name
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(directory, buffer, len(buffer))
        if not length or length >= len(buffer):
            raise RuntimeError("无法为 RocketCEA 建立独立 ASCII 工作目录。")
        directory = buffer.value
    if not directory.isascii() or " " in directory:
        raise RuntimeError("RocketCEA 工作目录需要无空格 ASCII 路径；请配置合适的系统临时目录。")
    import rocketcea.cea_obj as cea_module
    cea_module.ROCKETCEA_DATA_DIR = directory
    return directory


def _version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def comparison_capabilities():
    return {
        "cantera": {"available": thermo.CANTERA_AVAILABLE, "version": _version("cantera"),
                    "reason": None if thermo.CANTERA_AVAILABLE else "Cantera 未安装。"},
        "cea": {"available": thermo.ROCKETCEA_AVAILABLE, "version": _version("rocketcea"),
                "reason": None if thermo.ROCKETCEA_AVAILABLE else "RocketCEA 未安装；不会生成 CEA 数值。"},
        "coolprop": {"available": thermo.COOLPROP_AVAILABLE, "version": _version("CoolProp"),
                     "reason": None if thermo.COOLPROP_AVAILABLE else "相同真实流体入口焓需要 CoolProp。"},
        "modes": list(MODES), "propellants": propellant_models(), "max_points": MAX_COMPARISON_POINTS,
        "product_policies": list(PRODUCT_POLICIES), "cantera_datasets": list(THERMO_DATASETS),
    }


def normalize_request(request):
    if not isinstance(request, dict):
        raise ValueError("热力学对比请求必须是对象。")
    allowed = {"propellant_pair", "pc_pa", "of_values", "eps_values", "T_fuel_K", "T_ox_K", "mode",
               "product_policy", "cantera_dataset", "methane_thermo_dataset"}
    unknown = set(request) - allowed
    if unknown:
        raise ValueError("不支持的热力学对比参数：" + ", ".join(sorted(unknown)))
    pair = request.get("propellant_pair", "lox_methane")
    model = get_propellant(pair)
    mode = request.get("mode", "equilibrium")
    if mode not in MODES:
        raise ValueError("膨胀模式必须为 equilibrium、frozen_chamber 或 frozen_throat。")
    product_policy = request.get("product_policy", "native_databases")
    dataset = request.get("cantera_dataset", "production")
    methane_dataset = request.get("methane_thermo_dataset", "gri30")
    if methane_dataset not in METHANE_DATASETS:
        raise ValueError("不支持的甲烷生产热化学数据集。")
    if product_policy not in PRODUCT_POLICIES or dataset not in THERMO_DATASETS:
        raise ValueError("不支持的产品物种政策或 Cantera 数据集。")
    if product_policy == "matched_cho_gas" and pair != "lox_methane":
        raise ValueError("共同 CHO 气相基准当前只支持液氧/甲烷。")
    if dataset == "nasa_gas_candidate" and product_policy != "matched_cho_gas":
        raise ValueError("NASA gas 候选数据只用于共同 CHO 气相基准；不替换生产机制。")

    def number(value, label, lo, hi):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(label + " 必须是有限数值。")
        if not lo <= value <= hi:
            raise ValueError(f"{label} 必须在 [{lo:g}, {hi:g}] 内。")
        if not math.isfinite(value):
            raise ValueError(label + " 必须是有限数值。")
        return float(value)

    def values(key, default, lo, hi):
        source = request.get(key, default)
        if not isinstance(source, list) or not source or len(source) > MAX_AXIS_POINTS:
            raise ValueError(key + f" 必须为含 1–{MAX_AXIS_POINTS} 个数值的列表。")
        return [number(value, key, lo, hi) for value in source]

    result = {"propellant_pair": pair, "mode": mode, "product_policy": product_policy,
              "cantera_dataset": dataset, "methane_thermo_dataset": methane_dataset,
              "pc_pa": number(request.get("pc_pa", 10e6), "燃烧室压力 / Pa", 1e4, min(80e6, model.fluid_pressure_max_Pa)),
              "T_fuel_K": number(request.get("T_fuel_K", 300.0 if pair == "lox_kerosene" else 110.0),
                                 "燃料入口温度 / K", model.inlet_temperature_min_K, model.inlet_temperature_max_K),
              "T_ox_K": number(request.get("T_ox_K", 90.0), "氧入口温度 / K", 54.361, 2000.0),
              "of_values": values("of_values", [2.6] if pair == "lox_kerosene" else [3.4], 0.05, 20.0),
              "eps_values": values("eps_values", [40.0], 1.0, 1000.0)}
    if len(result["of_values"]) * len(result["eps_values"]) > MAX_COMPARISON_POINTS:
        raise ValueError(f"一次对比最多 {MAX_COMPARISON_POINTS} 个 O/F × 面积比组合。")
    return result


def _provenance(model, request):
    production_file = (METHANE_DATASETS[request["methane_thermo_dataset"]]["file"]
                       if model.fuel_species == "CH4" else model.mechanism)
    filename = "nasa_gas.yaml" if request["cantera_dataset"] == "nasa_gas_candidate" else production_file
    mechanism = Path(thermo.ct.__file__).resolve().parent / "data" / filename if thermo.CANTERA_AVAILABLE else None
    result = {"cantera_version": _version("cantera"), "coolprop_version": _version("CoolProp"),
              "rocketcea_version": _version("rocketcea"), "mechanism": filename, "phase": model.phase,
              "product_policy": request["product_policy"], "cantera_dataset": request["cantera_dataset"],
              "common_product_species": list(COMMON_CHO_SPECIES) if request["product_policy"] == "matched_cho_gas" else None,
              "gas_equation_of_state": "ideal-gas (both backends)",
              "inlet_enthalpy_basis": "Production ideal chemical reference plus HEOS H(T,p) minus HEOS ideal-reference enthalpy; finite-pressure residual removed (v2), held identical for all datasets.",
              "inlet_chemical_reference_file": production_file,
              "common_atomic_weights_kg_kmol": CEA_ATOMIC_WEIGHTS,
              "cea_pressure_encoding": "Pc_Pa / 101325 * 14.696006; matches bundled CEA INPUT psi/atm conversion",
              "mechanism_sha256": hashlib.sha256(mechanism.read_bytes()).hexdigest() if mechanism and mechanism.is_file() else None,
              "cea_database_files": []}
    if thermo.ROCKETCEA_AVAILABLE:
        import rocketcea
        for filename in ("thermo.lib", "thermo.inp", "trans.lib"):
            file = Path(rocketcea.__file__).resolve().parent / filename
            if file.is_file():
                result["cea_database_files"].append({"name": filename, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    return result


def _comparison_gas(request, fuel_species):
    if request["product_policy"] == "native_databases":
        original = thermo._get_gas(fuel_species, methane_dataset=request["methane_thermo_dataset"])
        selected, phase = original.species(), original.name
    else:
        filename = ("nasa_gas.yaml" if request["cantera_dataset"] == "nasa_gas_candidate"
                    else METHANE_DATASETS[request["methane_thermo_dataset"]]["file"])
        source = Path(thermo.ct.__file__).resolve().parent / "data" / filename
        species = {item.name: item for item in thermo.ct.Species.list_from_yaml(source.read_text(encoding="utf-8"), section="species")}
        selected, phase = [species[name] for name in COMMON_CHO_SPECIES], "comparison_cho16"
    elements = sorted({name for species in selected for name in species.composition})
    definition = {"phases": [{"name": phase, "thermo": "ideal-gas", "elements": elements,
                              "species": [s.name for s in selected]}],
                  "elements": [{"symbol": name, "atomic-weight": CEA_ATOMIC_WEIGHTS.get(name, thermo.ct.Element(name).weight)}
                               for name in elements], "species": [s.input_data for s in selected]}
    return thermo.ct.Solution(yaml=json.dumps(definition))


def _comparison_reactants(request, model):
    with methane_dataset_scope(request["methane_thermo_dataset"]):
        result = thermo.validation_reactants(request["T_fuel_K"], request["T_ox_K"], request["pc_pa"], model.fuel_species)
    carbon, hydrogen, oxygen = (CEA_ATOMIC_WEIGHTS[name] for name in ("C", "H", "O"))
    fuel_mw = 12*carbon+26*hydrogen if model.fuel_species == "c12h26" else carbon+4*hydrogen
    for role, mw in (("fuel", fuel_mw), ("oxidizer", 2*oxygen)):
        result[role+"_production_molecular_weight_kg_kmol"] = result[role+"_molecular_weight_kg_kmol"]
        result[role+"_molecular_weight_kg_kmol"] = mw
        result[role+"_enthalpy_cal_mol"] = result[role+"_enthalpy_Jkg"]*mw/4184.0
    result["common_atomic_weights_kg_kmol"] = dict(CEA_ATOMIC_WEIGHTS)
    return result


@lru_cache(maxsize=1)
def _cea_species_metadata():
    import rocketcea
    path = Path(rocketcea.__file__).resolve().parent / "thermo.inp"
    if not path.is_file():
        return {}
    lines, result = path.read_text(encoding="ascii").splitlines(), {}
    for index, line in enumerate(lines[:-1]):
        if not line or line.startswith((" ", "!")):
            continue
        header = lines[index+1]
        try:
            count = int(header[:2])
            if not 1 <= count <= 9:
                continue
            intervals = [lines[index+2+3*offset] for offset in range(count)]
            limits = [(float(row[:11]), float(row[11:22])) for row in intervals]
            result[line[:18].strip()] = {"min_K": min(x[0] for x in limits), "max_K": max(x[1] for x in limits),
                "molecular_weight_kg_kmol": float(header[52:65])}
        except (ValueError, IndexError):
            continue
    for element, weight in CEA_ATOMIC_WEIGHTS.items():
        if element not in result or abs(result[element]["molecular_weight_kg_kmol"]-weight) > 1e-9:
            raise ValueError("本机 CEA 原子量表与对照约定不同；需显式重新对齐。")
    return result


def _station_ranges(gas, stations):
    """Check every permitted species and every finite composition, not mixture max_temp alone."""
    ranges = {species.name: {"min_K": species.thermo.min_temp, "max_K": species.thermo.max_temp}
              for species in gas.species()}
    return {section: {"temperature_K": state["temperature_K"],
                     "outside_species": {name: ranges[name] | {"mass_fraction": fraction}
                        for name, fraction in state["mass_fractions"].items()
                        if fraction > 0 and not ranges[name]["min_K"] <= state["temperature_K"] <= ranges[name]["max_K"]},
                     "permitted_species_ranges_K": ranges}
            for section, state in stations.items()}


def _failure(status, reason, exception=None):
    return {"status": status, "reason": reason, "error_type": type(exception).__name__ if exception else None,
            "metrics": {}, "diagnostics": {}, "warnings": []}


def _cantera_point(request, of, eps, reactants):
    gas = _comparison_gas(request, reactants["fuel_species"])
    pressure = request["pc_pa"]
    gas.TPY = 600.0, pressure, {reactants["fuel_species"]: 1.0, reactants["oxidizer_species"]: of}
    initial_Y = gas.Y.copy()
    elements = {name: float(gas.elemental_mass_fraction(name)) for name in gas.element_names}
    h_mix = (reactants["fuel_enthalpy_Jkg"] + of * reactants["oxidizer_enthalpy_Jkg"]) / (1.0 + of)
    thermo._equilibrate_HP(gas, h_mix, pressure, initial_Y, context="thermodynamic-validation")
    enthalpy_residual = float(gas.enthalpy_mass-h_mix)
    if abs(enthalpy_residual) > max(10.0, abs(h_mix)*1e-5):
        raise ValueError(f"燃烧室 HP 平衡未保持指定入口焓：残差 {enthalpy_residual:g} J/kg。")
    state = thermo._gas_to_state(gas, pressure)
    state.mechanism = ("nasa_gas.yaml" if request["cantera_dataset"] == "nasa_gas_candidate"
        else METHANE_DATASETS[request["methane_thermo_dataset"]]["file"]
        if reactants["fuel_species"] == "CH4" else "nDodecane_Reitz.yaml")
    # Validation must not introduce the legacy display composition truncation.
    state.composition = {name: float(value) for name, value in zip(gas.species_names, gas.Y) if value > 0}
    nozzle = thermo._cantera_nozzle_expand(state, pressure, eps,
        equilibrium=request["mode"] != "frozen_chamber", freeze_at_throat=request["mode"] == "frozen_throat",
        n_iter=70, diagnostics=True, gas_override=gas)
    if nozzle is None:
        raise ValueError("Cantera 喷管没有有效解。")
    stations = nozzle["stations"]
    chamber = stations["chamber"]
    element_residuals = {section: {name: values["element_mass_fractions"].get(name, 0.0)-fraction
                                 for name, fraction in elements.items()}
                         for section, values in stations.items()}
    residual_max = max(abs(value) for section in element_residuals.values() for value in section.values())
    if residual_max > 1e-7:
        raise ValueError(f"燃烧室/喷管元素质量守恒残差超限：{residual_max:g}。")
    return {"status": "ok", "reason": None,
            "metrics": {"Tc_K": state.temperature, "Cstar_m_s": nozzle["Cstar"],
                        "Isp_vac_s": nozzle["Isp_vac"], "Cf_vac": nozzle["Cf_vac"],
                        "MW_chamber_kg_kmol": chamber["MW_kg_kmol"], "gamma_chamber": chamber["gamma_isentropic"]},
            "stations": stations,
            "diagnostics": nozzle["diagnostics"] | {"chamber_enthalpy_residual_Jkg": enthalpy_residual,
                "element_mass_fraction_residuals": element_residuals, "element_residual_max": residual_max,
                "species_mass_fraction_sum_residual": sum(state.composition.values())-1.0,
                "thermo_extrapolation": state.thermo_extrapolation,
                "station_temperature_ranges": _station_ranges(gas, stations),
                "allowed_product_species": list(gas.species_names),
                "gas_equation_of_state": gas.thermo_model}, "warnings": []}


def _cea_object(request, ox_name, fuel_name):
    if request["product_policy"] == "native_databases":
        return thermo._CEA_Obj(oxName=ox_name, fuelName=fuel_name, makeOutput=1)
    # RocketCEA 1.2.3 does not expose an ONLY keyword argument. Its input deck
    # accepts the documented NASA CEA dataset. Give the restricted problem a
    # distinct reactant/cache identity so native cached performance cannot leak.
    from rocketcea.cea_obj import oxCards, fuelCards
    restricted_ox, restricted_fuel = ox_name + "_CHO16", fuel_name + "_CHO16"
    if restricted_ox not in oxCards:
        thermo._add_new_ox(restricted_ox, "\n".join(oxCards[ox_name]))
    if restricted_fuel not in fuelCards:
        thermo._add_new_fuel(restricted_fuel, "\n".join(fuelCards[fuel_name]))
    cea = thermo._CEA_Obj(oxName=restricted_ox, fuelName=restricted_fuel, makeOutput=1)
    cea.cea_deck.append("only " + " ".join(COMMON_CHO_SPECIES))
    return cea


def _cea_mass_fractions(cea, arguments, flags):
    """Read all products, including RocketCEA 1.2.3's omitted final product.

    Its public getter loops range(ngc), although the character name common block
    retains a blank element zero and names 1..ngc; numeric arrays start at zero.
    Verify this layout and conservation rather than renormalizing lost mass.
    """
    from rocketcea import py_cea
    cea.setupCards(**arguments, **flags)
    count = int(py_cea.indx.ngc)
    if py_cea.cdata.prod[0].decode("ascii").strip():
        raise RuntimeError("CEA 物种名称数组布局已改变；需复核接口后才能读取组分。")
    result = {}
    indices = [cea.i_injface, cea.i_chm, cea.i_thrt, cea.i_exit]
    for index in range(count):
        name = py_cea.cdata.prod[index+1].decode("ascii").strip()
        if not name:
            raise ValueError("CEA 产品集合出现未命名物种，不能确认组成。")
        weight = float(py_cea.therm.mw[index])
        fractions = [float(py_cea.comp.en[index, station])*weight for station in indices]
        if flags["frozen"]:
            fractions = ([fractions[0], fractions[1], fractions[2], fractions[2]] if flags["frozenAtThroat"]
                         else [fractions[0]]*4)
        if max(fractions) > 0:
            result[name] = fractions
    for station in range(4):
        residual = sum(values[station] for values in result.values())-1.0
        if abs(residual) > 1e-7:
            raise ValueError(f"CEA 报告组分质量分数不闭合：截面 {station} 残差 {residual:g}。")
    return result


def _cea_point(request, of, eps, reactants):
    _prepare_cea_scratch()
    species_metadata = _cea_species_metadata()
    ox_name, fuel_name = thermo._ensure_cea_custom_reactants(request["T_ox_K"], request["T_fuel_K"],
        request["pc_pa"], reactants["fuel_species"], reactants=reactants)
    cea = _cea_object(request, ox_name, fuel_name)
    flags = {"frozen": int(request["mode"] != "equilibrium"), "frozenAtThroat": int(request["mode"] == "frozen_throat")}
    arguments = {"Pc": request["pc_pa"] / 101325.0 * CEA_PSIA_PER_ATM, "MR": of, "eps": eps}
    isp, cstar_ft, tc_rankine = cea.get_IvacCstrTc(**arguments, **flags)
    cstar, tc = float(cstar_ft)*0.3048, float(tc_rankine)/1.8
    if not all(math.isfinite(float(value)) and value > 0 for value in (isp, cstar, tc)):
        raise ValueError("CEA 返回零值或非有限性能指标，不能视为有效点。")
    temperatures = cea.get_Temperatures(**arguments, **flags)
    densities = cea.get_Densities(**arguments, **flags)
    sound_speeds = cea.get_SonicVelocities(**arguments, **flags)
    # RocketCEA's native unit conversion is h/R * 1.8 * 8314.51 / 4184.
    # Undo that exact documented implementation, rather than rounding BTU/lbm.
    enthalpies = [float(value)*4184.0/1.8 for value in cea.get_Enthalpies(**arguments, **flags)]
    mixture_enthalpy = (reactants["fuel_enthalpy_Jkg"]+of*reactants["oxidizer_enthalpy_Jkg"])/(1+of)
    enthalpy_residual = enthalpies[0]-mixture_enthalpy
    if abs(enthalpy_residual) > max(10.0, abs(mixture_enthalpy)*1e-5):
        raise ValueError(f"CEA 入口焓与燃烧室焓不一致：残差 {enthalpy_residual:g} J/kg。")
    chamber_mw, _ = cea.get_Chamber_MolWt_gamma(**arguments)
    exit_mw, _ = cea.get_exit_MolWt_gamma(**arguments, **flags)
    p_exit = request["pc_pa"] / cea.get_PcOvPe(**arguments, **flags)
    # The documented throat pressure getter only supports equilibrium. For the
    # frozen case use the identity c*=Pc/(rho_t*a_t), not an equilibrium pressure.
    mass_fractions = _cea_mass_fractions(cea, arguments, flags)
    rho_chamber = float(densities[0]) * 0.45359237 / 0.3048**3
    a_chamber = float(sound_speeds[0]) * 0.3048
    chamber_gamma = a_chamber*a_chamber*rho_chamber/request["pc_pa"]
    stations = {}
    for index, section in enumerate(("chamber", "throat", "exit")):
        composition = {name: float(values[index+1]) for name, values in mass_fractions.items() if float(values[index+1]) > 0}
        stations[section] = {"temperature_K": float(temperatures[index])/1.8,
            "enthalpy_Jkg": enthalpies[index],
            "density_kg_m3": float(densities[index])*0.45359237/0.3048**3,
            "sound_speed_m_s": float(sound_speeds[index])*0.3048,
            "mass_fractions": composition}
    stations["chamber"].update({"pressure_Pa": request["pc_pa"], "MW_kg_kmol": float(chamber_mw),
                                 "gamma_isentropic": chamber_gamma})
    stations["exit"].update({"pressure_Pa": p_exit, "MW_kg_kmol": float(exit_mw)})
    raw = cea.get_full_cea_output(**arguments, **flags, short_output=0, show_transport=0, show_mass_frac=True)
    restricted = request["product_policy"] == "matched_cho_gas"
    if restricted:
        echoed = re.search(r"\bonly\s+(.+?)(?=\s+prob\b)", raw, flags=re.IGNORECASE | re.DOTALL)
        if not echoed or tuple(echoed.group(1).split()) != COMMON_CHO_SPECIES:
            raise ValueError("CEA 未原样回显限定产品 ONLY 卡；不能确认物种范围已对齐。")
        unexpected = {name.lstrip("*") for name in mass_fractions} - set(COMMON_CHO_SPECIES)
        if unexpected:
            raise ValueError("CEA 返回限定物种集合之外的产物：" + ", ".join(sorted(unexpected)))
    temperature_ranges = {}
    for section, station in stations.items():
        outside, missing = {}, []
        for name, fraction in station["mass_fractions"].items():
            metadata = species_metadata.get(name.lstrip("*"))
            if metadata is None:
                missing.append(name)
            elif not metadata["min_K"] <= station["temperature_K"] <= metadata["max_K"]:
                outside[name] = metadata | {"mass_fraction": fraction}
        temperature_ranges[section] = {"temperature_K": station["temperature_K"], "outside_species": outside,
                                        "unverified_species": missing}
    return {"status": "ok", "reason": None,
        "metrics": {"Tc_K": tc, "Cstar_m_s": cstar, "Isp_vac_s": float(isp),
                    "Cf_vac": float(isp)*thermo._G0/cstar,
                    "MW_chamber_kg_kmol": float(chamber_mw), "gamma_chamber": chamber_gamma},
        "stations": stations, "diagnostics": {"reactant_cards": {"oxidizer_name": ox_name, "fuel_name": fuel_name},
            "phase_policy": ("共同 CHO16 中性气相集合；无凝聚相/离子；数据库仍不同。" if restricted else
                             "CEA 默认产品数据库；未限制为 Cantera 同一气相物种集合。"),
            "allowed_product_species": list(COMMON_CHO_SPECIES) if restricted else None,
            "only_card_verified": restricted,
            "gas_equation_of_state": "ideal-gas",
            "station_temperature_ranges": temperature_ranges,
            "chamber_enthalpy_residual_Jkg": enthalpy_residual,
            "reported_mass_fraction_sum_residuals": {section: sum(state["mass_fractions"].values())-1
                                                       for section, state in stations.items()},
            "product_species_metadata": {name: species_metadata.get(name.lstrip("*")) for name in mass_fractions},
            "product_species_reported": sorted(mass_fractions),
            "species_reporting_min_fraction": 0.0,
            "species_reader": "CEA common block; all ngc names, including final product; mass-sum checked without renormalization",
            "cea_output": raw}, "warnings": []}


def compare_thermochemistry(request, progress=None):
    request = normalize_request(request)
    model = get_propellant(request["propellant_pair"])
    availability = comparison_capabilities()
    total = len(request["of_values"]) * len(request["eps_values"])
    restricted = request["product_policy"] == "matched_cho_gas"
    phase_policy = ("Cantera 与 CEA 均限定 CHO16 中性气相；同一物种集合，热力学系数与参考熵仍可能不同。"
                    if restricted else "Cantera 为指定机制的单一理想气相；CEA 使用默认产品数据库，物种与凝聚相范围未统一。")
    result = {"schema_version": 2, "request": request, "availability": availability,
        "scope": {"label": "独立燃烧室与理想喷管热力学基准", "propellant": asdict(model),
            "mode": request["mode"], "conditions_aligned": True, "products_aligned": restricted,
            "product_policy": request["product_policy"], "cantera_dataset": request["cantera_dataset"],
            "gas_equation_of_state": "两端燃气均采用理想气体模型；本对照不验证高压燃气非理想性。",
            "gamma_definition": "同一燃烧室截面的等熵指数 a²ρ/p；平衡模式包含组分随等熵扰动变化，非固定 cp/cv。",
            "phase_policy": phase_policy,
            "limitations": ["比较相同元素配比、入口化学焓、压力、面积比与膨胀模式；产品热力学数据库仍独立。",
                "CHO16 是隔离数据库影响的约简气相基准；不用于声称富燃预燃室、积碳、凝聚或电离已验证。",
                "NASA gas 候选是 Cantera 随附 TM-4513 (1993) 来源的 NASA7 数据，所选物种 200–6000 K；不是最新 CEA 数据，也不是生产机制切换。",
                "差异用于定位数据库与建模假设影响，不构成独立精度合格判定或实机验证。",
                "这是独立燃烧室/喷管基准；不包含泵、涡轮、支路与循环损失，不能直接替代 GG 或 FFSC 整机比冲。",
                "正十二烷为明确的煤油替代示例，不代表真实 RP-1 配方、有限速率、雾化、积碳或实机标定。"],
            "sources": ["https://cantera.org/3.1/examples/python/thermo/sound_speed.html",
                        "https://rocketcea.readthedocs.io/en/latest/functions.html",
                        "https://ntrs.nasa.gov/citations/19960044559",
                        "https://github.com/Cantera/cantera/blob/main/data/nasa_gas.yaml"]},
        "provenance": _provenance(model, request), "counts": {"requested": total, "cantera_success": 0,
            "cea_success": 0, "paired_success": 0, "failed": 0, "unpaired": 0}, "points": []}
    with _LOCK:
        for of in request["of_values"]:
            for eps in request["eps_values"]:
                checkpoint()
                point = {"of": of, "eps": eps, "pc_pa": request["pc_pa"], "reactants": None,
                         "differences": {}, "comparable": {"conditions_aligned": False, "products_aligned": False,
                         "notes": [result["scope"]["phase_policy"]]}}
                try:
                    reactants = _comparison_reactants(request, model)
                    point["reactants"] = reactants | {"mixture_enthalpy_Jkg": (
                        reactants["fuel_enthalpy_Jkg"] + of*reactants["oxidizer_enthalpy_Jkg"])/(1+of)}
                except Exception as exc:
                    for backend in ("cantera", "cea"):
                        point[backend] = _failure("error", "无法建立共同入口焓：" + str(exc), exc)
                else:
                    for backend, calculate in (("cantera", _cantera_point), ("cea", _cea_point)):
                        checkpoint()
                        if not availability[backend]["available"]:
                            point[backend] = _failure("unavailable", availability[backend]["reason"])
                            continue
                        try:
                            with warnings.catch_warnings(record=True) as caught:
                                warnings.simplefilter("always")
                                point[backend] = calculate(request, of, eps, reactants)
                            point[backend]["warnings"] = list(dict.fromkeys(str(item.message) for item in caught))
                        except Exception as exc:
                            point[backend] = _failure("error", str(exc), exc)
                for backend in ("cantera", "cea"):
                    if point[backend]["status"] == "ok":
                        result["counts"][backend + "_success"] += 1
                paired = all(point[backend]["status"] == "ok" for backend in ("cantera", "cea"))
                if paired:
                    result["counts"]["paired_success"] += 1
                    point["comparable"]["conditions_aligned"] = True
                    point["comparable"]["products_aligned"] = restricted and point["cea"]["diagnostics"]["only_card_verified"]
                    for key in METRICS:
                        ct_value, cea_value = point["cantera"]["metrics"][key], point["cea"]["metrics"][key]
                        point["differences"][key] = {"absolute": cea_value-ct_value,
                            "relative_percent": (cea_value-ct_value)/abs(ct_value)*100 if ct_value else None}
                else:
                    result["counts"]["unpaired"] += 1
                if any(point[backend]["status"] == "error" for backend in ("cantera", "cea")):
                    result["counts"]["failed"] += 1
                result["points"].append(point)
                if progress:
                    progress(len(result["points"]), total, point)
    return result
