"""Record-bound HP equilibrium checks using actual turbine exhaust budgets.

The CHO16 calculation is a separate database comparison. Element-equivalent
reactant cards are an input representation of the saved hot streams, never a
replacement of their molecular composition in the production cycle.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from webapp.solver import ROOT  # also installs the existing src import path
import thermo
from task_control import checkpoint
from webapp.thermo_compare import (
    _LOCK, _comparison_gas, _cea_object, _cea_mass_fractions,
    _cea_species_metadata, _prepare_cea_scratch, COMMON_CHO_SPECIES,
    CEA_ATOMIC_WEIGHTS, CEA_PSIA_PER_ATM, _station_ranges,
)


def _finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} 缺少有限数值。")
    if positive and value <= 0:
        raise ValueError(f"{name} 必须大于零。")
    return float(value)


def _load_source_gas(source, ports):
    from gas_handoff import load_verified_gas_model
    inputs = source.get("calculation", {}).get("inputs", {})
    if inputs.get("propellant_pair") != "lox_methane" or inputs.get("cycle_type") != "ffsc":
        raise ValueError("本次交接对照仅支持 FFSC 液氧甲烷记录。")
    references, identities = set(), set()
    gas = None
    for port in ports:
        model = port.get("model", {})
        gas = load_verified_gas_model(model)
        identities.add((model.get("mechanism"), model.get("phase"), model.get("mechanism_sha256")))
        if model.get("eos", model.get("gas_eos")) != "ideal-gas":
            raise ValueError("本复核器只重现已保存的 ideal-gas 化学平衡；非理想燃烧需独立模型。")
        reference = model.get("enthalpy_reference_id")
        if not reference:
            raise ValueError("排气未保存可识别的化学焓基准。")
        references.add(reference)
    if len(references) != 1:
        raise ValueError("两股排气的化学焓参考不同，禁止直接相加。")
    if len(identities) != 1:
        raise ValueError("两股排气的热化学数据库身份不同。")
    return gas


def _budgets(source, report):
    stations = {item["id"]: item for item in report.get("stations", [])}
    ports = [deepcopy(stations[key]) for key in ("ox_turbine_exhaust", "fuel_turbine_exhaust") if key in stations]
    if len(ports) != 2 or "main_chamber" not in stations:
        raise ValueError("缺少两股涡轮排气或主室保存状态；不能用纯推进剂默认值补齐。")
    gas = _load_source_gas(source, ports)
    total, hflux, mixed, elements = 0.0, 0.0, {}, {}
    for port in ports:
        mass = _finite(port.get("mass_flow_kg_s"), "排气流量", True)
        enthalpy = _finite(port.get("h_Jkg"), "排气化学焓")
        composition = port.get("composition_mass_fractions")
        if not isinstance(composition, dict) or not composition:
            raise ValueError("缺少完整排气组成。")
        for name, fraction in composition.items():
            if _finite(fraction, "质量分数") < 0 or name not in gas.species_names:
                raise ValueError("排气含负质量分数或未知物种，禁止截断后归一化。")
            mixed[name] = mixed.get(name, 0.0) + mass*fraction
        if abs(sum(composition.values()) - 1) > 1e-8:
            raise ValueError("排气质量分数和不为一，可能只保存了主要组分。")
        gas.TPY = _finite(port.get("T_K"), "排气温度", True), _finite(port.get("p_Pa"), "排气压力", True), composition
        if abs(gas.enthalpy_mass - enthalpy) > max(10.0, abs(enthalpy)*1e-5):
            raise ValueError("保存的排气焓与其完整组成、温度和机制不一致。")
        budget = {e: float(gas.elemental_mass_fraction(e)) for e in gas.element_names}
        port["element_mass_fractions"] = budget
        for name, fraction in budget.items():
            elements[name] = elements.get(name, 0.0) + mass*fraction
        total += mass
        hflux += mass*enthalpy
    chamber = stations["main_chamber"]
    if abs(_finite(chamber.get("mass_flow_kg_s"), "主室流量", True)-total) > max(1e-8, total*1e-8):
        raise ValueError("主室流量与保存的两股排气流量之和不符。")
    outputs = source.get("calculation", {}).get("components", {}).get("main_chamber", {}).get("outputs", {})
    heat = _finite(outputs.get("heat_loss_W"), "主室抽热功率")
    loss = _finite(outputs.get("combustion_loss_W"), "历史燃烧温升折减功率")
    if heat < 0 or loss < 0:
        raise ValueError("主室损失账本不能为负。")
    saved_out_h = _finite(chamber.get("h_Jkg"), "主室保存出口焓")
    energy_residual = hflux-heat-loss-total*saved_out_h
    if abs(energy_residual) > max(1.0, abs(hflux)*1e-8):
        raise ValueError("主室保存出口焓不满足入口焓流、抽热和经验折减损失账本。")
    target = {"p_Pa": _finite(chamber.get("p_Pa"), "主室压力", True),
              "mass_flow_kg_s": total, "h_Jkg": (hflux-heat)/total,
              "inlet_enthalpy_flow_W": hflux, "heat_loss_W": heat,
              "legacy_combustion_loss_W": loss,
              "saved_exit_energy_residual_W": energy_residual,
              "element_mass_fractions": {name: value/total for name, value in elements.items()},
              "scope": "保存入口总焓扣除集中抽热后的 HP 平衡，历史经验温升折减前"}
    return gas, ports, target, {name: value/total for name, value in mixed.items()}, outputs


def _equilibrate(gas, target, composition):
    gas.TPY = 1000.0, target["p_Pa"], composition
    thermo._equilibrate_HP(gas, target["h_Jkg"], target["p_Pa"], gas.Y.copy(), context="saved-cycle-main-chamber-audit")
    temperature = _finite(float(gas.T), "HP 平衡温度", True)
    h = _finite(float(gas.enthalpy_mass), "HP 平衡焓")
    residuals = {name: float(gas.elemental_mass_fraction(name))-fraction
                 for name, fraction in target["element_mass_fractions"].items() if name in gas.element_names}
    max_element = max((abs(v) for v in residuals.values()), default=0.0)
    h_residual = float(gas.enthalpy_mass)-target["h_Jkg"]
    if max_element > 1e-7 or abs(h_residual) > max(10.0, abs(target["h_Jkg"])*1e-5):
        raise ValueError("独立 HP 平衡未保持保存的元素或化学焓。")
    composition = {name: _finite(float(y), "HP 产物质量分数") for name, y in zip(gas.species_names, gas.Y) if y != 0}
    if any(y < 0 for y in composition.values()) or abs(sum(composition.values())-1) > 1e-8:
        raise ValueError("HP 平衡产物质量分数不闭合。")
    ranges = _station_ranges(gas, {"chamber": {"temperature_K": temperature, "mass_fractions": composition}})["chamber"]
    return {"status": "ok", "temperature_K": temperature, "enthalpy_Jkg": h,
            "enthalpy_residual_Jkg": h_residual, "element_max_residual": max_element,
            "element_residuals": residuals,
            "temperature_range_evidence": ranges,
            "thermochemical_range_status": "extrapolated" if ranges["outside_species"] else "within_declared_ranges",
            "composition_mass_fractions": composition}


def _cea_actual_ports(ports, target):
    if not thermo.ROCKETCEA_AVAILABLE:
        return {"status": "unavailable", "reason": "RocketCEA 未安装。"}
    _prepare_cea_scratch()
    token = hashlib.sha256(json.dumps([ports, target], sort_keys=True).encode()).hexdigest()[:18]
    names, cards = ["HO_"+token, "HF_"+token], []
    # Formula mass 100 kg/kmol is an arbitrary exact representation. It preserves
    # each saved stream's element mass fractions and chemical h, including cold
    # feed/pump/preburner/turbine history. This is NOT fresh O2/CH4 combustion.
    for role, name, port in zip(("oxid", "fuel"), names, ports):
        formula = " ".join(f"{e} {100*f/CEA_ATOMIC_WEIGHTS[e]:.15g}"
                           for e, f in port["element_mass_fractions"].items() if f > 0)
        h = port["h_Jkg"] - target["heat_loss_W"]/target["mass_flow_kg_s"]
        card = f"{role} Hot_stream {formula} wt%=100.\nh,cal={h*100/4184:.15g} t(k)={port['T_K']:.15g}\n"
        (thermo._add_new_ox if role == "oxid" else thermo._add_new_fuel)(name, card)
        cards.append(card)
    cea = _cea_object({"product_policy": "matched_cho_gas"}, *names)
    # eps is required by RocketCEA's rocket deck; only the chamber is compared.
    args = {"Pc": target["p_Pa"]/101325*CEA_PSIA_PER_ATM,
            "MR": ports[0]["mass_flow_kg_s"]/ports[1]["mass_flow_kg_s"], "eps": 10.0}
    flags = {"frozen": 0, "frozenAtThroat": 0}
    temperature = _finite(float(cea.get_Temperatures(**args, **flags)[0])/1.8, "CEA 温度", True)
    h = _finite(float(cea.get_Enthalpies(**args, **flags)[0])*4184/1.8, "CEA 焓")
    metadata = _cea_species_metadata()
    fractions, unverified = _cea_mass_fractions(cea, args, flags, metadata)
    composition = {name.lstrip("*"): _finite(float(values[1]), "CEA 质量分数") for name, values in fractions.items() if values[1] != 0}
    if any(y < 0 for y in composition.values()) or abs(sum(composition.values())-1) > 1e-7:
        raise ValueError("CEA 产物质量分数不闭合。")
    if unverified or set(composition)-set(COMMON_CHO_SPECIES):
        raise ValueError("CEA 产物名称或摩尔质量读回未通过校核。")
    elements = dict.fromkeys(CEA_ATOMIC_WEIGHTS, 0.0)
    for name, fraction in composition.items():
        data = metadata[name]
        for element, count in data["elements"].items():
            elements[element] += fraction*count*CEA_ATOMIC_WEIGHTS[element]/data["molecular_weight_kg_kmol"]
    residuals = {e: elements[e]-target["element_mass_fractions"].get(e, 0.0) for e in elements}
    maximum = max(abs(v) for v in residuals.values())
    h_residual = h-target["h_Jkg"]
    if maximum > 1e-7 or abs(h_residual) > max(10.0, abs(target["h_Jkg"])*1e-5):
        raise ValueError(f"CEA 热排气入口元素/焓未守恒：元素 {maximum:g}，焓 {h_residual:g} J/kg。")
    raw = cea.get_full_cea_output(**args, **flags, short_output=0, show_transport=0, show_mass_frac=True)
    echoed = re.search(r"\bonly\s+(.+?)(?=\s+prob\b)", raw, flags=re.I | re.S)
    if not echoed or tuple(echoed.group(1).split()) != COMMON_CHO_SPECIES:
        raise ValueError("CEA 未正确回显共同物种限定卡。")
    outside = {name: metadata[name] | {"mass_fraction": fraction} for name, fraction in composition.items()
               if not metadata[name]["min_K"] <= temperature <= metadata[name]["max_K"]}
    return {"status": "ok", "temperature_K": temperature, "enthalpy_Jkg": h,
            "enthalpy_residual_Jkg": h_residual, "element_max_residual": maximum,
            "element_residuals": residuals, "composition_mass_fractions": composition,
            "temperature_range_evidence": {"temperature_K": temperature, "outside_species": outside},
            "thermochemical_range_status": "extrapolated" if outside else "within_declared_ranges",
            "reactant_cards": cards, "only_card_verified": True}


def compare_chamber_handoff(source_result, handoff=None, progress=None):
    result = {"schema_version": 1, "status": "unavailable",
              "scope": "本次 FFSC 主室：保存涡轮排气的元素与化学焓交接",
              "source_ports": [], "target": None, "production_reproduction": None,
              "matched_cho_comparison": None,
              "notes": ["主室生产路径接收完整分子组成；共同 CHO16 对照只用等效元素卡表示相同入口预算。",
                        "纯推进剂理论基线、实际循环主室复核、非理想物性筛查是三种不同问题。",
                        "此处两端均为理想气相 HP 平衡，不验证高压真实气体、混合速率或燃烧稳定性。"]}
    try:
        checkpoint()
        if handoff is None:
            from gas_handoff import capture_gas_handoff
            handoff = capture_gas_handoff(source_result)
        gas, ports, target, mixed, outputs = _budgets(source_result, handoff)
        result.update(source_ports=ports, target=target)
        production = _equilibrate(gas, target, mixed)
        saved_temperature = _finite(outputs.get("T_chamber_ideal_K"), "保存的主室平衡温度", True)
        production.update(saved_temperature_K=saved_temperature,
                          temperature_difference_K=production["temperature_K"]-saved_temperature)
        if abs(production["temperature_difference_K"]) > max(0.01, saved_temperature*1e-5):
            production.update(status="inconsistent", reason="保存主室温度不能由保存入口元素与焓复现。")
        result["production_reproduction"] = production
        result["status"] = "ok" if production["status"] == "ok" else "inconsistent"
        if any(v > 1e-14 for e, v in target["element_mass_fractions"].items() if e not in CEA_ATOMIC_WEIGHTS):
            result["matched_cho_comparison"] = {"status": "unavailable", "reason": "保存排气含 CHO 以外元素，禁止丢弃后套用 CHO16。"}
            return result
        mechanism = ports[0]["model"]["mechanism"]
        dataset = next((key for key, value in thermo.METHANE_DATASETS.items() if value["file"] == mechanism), None)
        if dataset is None:
            raise ValueError("保存燃气模型不在共同 CHO 对照支持范围。")
        matched = {"status": "partial", "species_policy": "两端共同 CHO16 中性理想气相；不含凝聚相和离子；热化学数据库独立。",
                   "allowed_product_species": list(COMMON_CHO_SPECIES),
                   "common_atomic_weights_kg_kmol": dict(CEA_ATOMIC_WEIGHTS)}
        result["matched_cho_comparison"] = matched
        request = {"product_policy": "matched_cho_gas", "cantera_dataset": "production",
                   "methane_thermo_dataset": dataset}
        common_gas = _comparison_gas(request, "CH4")
        elemental_initial = {species: target["element_mass_fractions"].get(element, 0.0)
                             for element, species in (("C", "C"), ("H", "H2"), ("O", "O2"))}
        matched["cantera"] = _equilibrate(common_gas, target, elemental_initial)
        checkpoint()
        with _LOCK:
            try:
                matched["cea"] = _cea_actual_ports(ports, target)
            except Exception as exc:
                matched["cea"] = {"status": "error", "reason": str(exc)}
        if matched["cea"]["status"] == "ok":
            delta = matched["cea"]["temperature_K"]-matched["cantera"]["temperature_K"]
            matched.update(status="ok", temperature_difference_K=delta,
                           relative_temperature_difference_percent=100*delta/matched["cantera"]["temperature_K"])
    except Exception as exc:
        result.update(status="unavailable", reason=str(exc))
    return result
