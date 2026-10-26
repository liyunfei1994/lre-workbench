"""Persist thermodynamic model scope separately from numerical convergence.

This audit inspects final gas states without changing any solution or input.
No pressure threshold or extrapolated mass fraction is treated as an error bound.
"""
from __future__ import annotations

import math

from propellants import get_propellant
from thermo_data import methane_dataset_scope, METHANE_DATASETS
from fluid_properties import state_tp as pure_state_tp


GAS_STATIONS = {
    "ox_preburner_outlet": "富氧预燃室出口 / 涡轮入口",
    "fuel_preburner_outlet": "富燃预燃室出口 / 涡轮入口",
    "ox_turbine_exhaust": "富氧涡轮排气",
    "fuel_turbine_exhaust": "富燃涡轮排气",
    "gg_outlet": "燃气发生器出口",
    "gg_turbine_exhaust": "燃气发生器涡轮排气",
    "main_chamber": "主燃烧室",
    "nozzle_throat": "主喷管喉部",
    "nozzle_exit": "主喷管出口",
}


def build_thermodynamic_applicability(result):
    with methane_dataset_scope(getattr(result.inputs, "methane_thermo_dataset", "gri30")):
        return _build_thermodynamic_applicability(result)


def _build_thermodynamic_applicability(result):
    import thermo

    inputs = result.inputs
    fuel = get_propellant(inputs.propellant_pair)
    mode = inputs.combustion_loss_model
    chamber_component = result.components.get("main_chamber")
    chamber_outputs = chamber_component.outputs if chamber_component else {}
    nozzle = result.components.get("nozzle")
    nozzle_model = nozzle.outputs.get("flow_model") if nozzle else None
    kerosene = fuel.fuel_species == "c12h26"
    dataset = METHANE_DATASETS[inputs.methane_thermo_dataset]
    filename = fuel.mechanism if kerosene else dataset["file"]
    T_ref, p_ref = (600.0, 1000.0) if kerosene else (298.15, 101325.0)
    report = {
        "schema_version": 2, "status": "review_required", "states": [], "pure_fluid_states": [],
        "gas_model": {"id": "unknown", "label": "燃气模型待识别",
                      "validation_status": "not_validated", "mechanism": filename,
                      "description": "高压混合燃气 EOS 的误差尚未独立量化；纯推进剂物性对照不能代替混合燃气验证。"},
        "enthalpy_reference": {
            "id": thermo.enthalpy_reference_id(fuel.fuel_species),
            "temperature_K": T_ref, "pressure_Pa": p_ref,
            "formula": "h_reactant(T,p) = h_Cantera_ideal_species(T_ref) + h_HEOS(T,p) - [h_HEOS(T_ref,p_ref) - h_HEOS_residual(T_ref,p_ref)]",
            "description": "去除有限压力参考点的 HEOS 残余焓，再对齐理想物种化学生成焓；正算、反算及 CEA 入口使用同一 v2 基准。主室温度、γ、摩尔质量初值不是焓零点。"},
        "nozzle": {"id": "frozen_chamber", "label": "从主室组成冻结的等熵喷管",
                   "description": "生产主喷管固定主室组成；不在喉部才冻结，不沿程恢复化学平衡。再生冷却总热量集中在主室扣除，喷管本身按绝热等熵膨胀。"},
        "combustion": {
            "id": mode,
            "label": "焓平衡（不附加经验燃烧损失）" if mode == "adiabatic" else "历史温升折减（兼容）",
            "description": ("按入口总焓及单独记账的冷却抽热求 HP 平衡；不乘温升系数。化学平衡含解离，不等同于燃尽率 100%。"
                if mode == "adiabatic" else "先求 HP 平衡，再冻结组成折减温升；焓差显式列为经验损失。这不是 c* 效率、燃尽率或已验证的损失模型。"),
            "legacy_factor": inputs.combustion_efficiency if mode == "legacy_temperature_rise" else None,
            "loss_W": chamber_outputs.get("combustion_loss_W"),
            "cooling_heat_transfer_W": chamber_outputs.get("heat_loss_W"),
        },
        "notes": [
            "本表只检查保存工况的声明范围与模型假设，独立于方程收敛、供给约束和能量闭合。",
            "多项式外推可能影响性质与平衡组成；外推质量占比不是误差估计，不强行截温。",
            "Cantera 是计算工具；CEA 对照也不能自动验证高压理想气体近似。",
            "CoolProp HEOS 与 REFPROP 的纯流体对照见热力学校核；生产物性后端仍为 HEOS。",
        ],
    }
    if inputs.cycle_type == "gg":
        has_port_ledger = all(isinstance(chamber_outputs.get(key),(float,int))
                              and math.isfinite(chamber_outputs[key]) for key in
                              ("h_in_Jkg","heat_loss_W","combustion_loss_W","energy_residual_W"))
        if has_port_ledger:
            report["nozzle"]["description"] = "本记录的主喷管固定主室组成，按绝热等熵膨胀；给定冷却预算热汇在主室 HP 目标中单独扣除，不在喷管再次扣除。"
            report["combustion"]["description"] = (
                "使用实际泵/冷却出口的共同基准焓求主室 HP 平衡，热汇独立记账；不附加温升折减。"
                if mode == "adiabatic" else
                "使用实际泵/冷却出口焓求 HP 平衡后冻结组成折减温升；预算热汇和经验折减焓差分别记账。后者不是 c* 效率或燃尽率。")
            report["notes"].append("GG 主室端口焓与热汇记录不等于全系统独立能量审核；GG 排气、轴功及冷却路径仍须按本循环范围核对。")
        else:
            report["nozzle"]["description"] = "本历史 GG 记录固定主室组成并按绝热等熵膨胀；未保存完整冷却热端与冷却剂吸热的全局能量证据。"
            report["combustion"]["description"] = "本历史 GG 记录未保存完整实际端口焓、热汇及经验损失诊断，不能推定主室或全系统能量闭合；请重新计算。"
            report["notes"].append("FFSC 的冷却总焓闭合不能套用给历史 GG 记录。")
    if nozzle_model != 0.0:
        report["nozzle"] = {"id": "constant_gamma" if nozzle_model == -1.0 else "unavailable",
                            "label": "固定比热比近似" if nozzle_model == -1.0 else "膨胀假设未识别",
                            "description": "本记录不是已确认的 Cantera 主室组成冻结喷管；不以缺失证据推定膨胀模型。"}
    cooling = result.components.get("cooling_channel")
    cooling_profile = (getattr(cooling, "profile", None) or {}) if cooling else {}
    from copy import deepcopy
    report["solid_wall_thermal"] = deepcopy(cooling_profile.get("material_applicability") or {
        "status": "missing_evidence", "manufacturing_match": "unknown",
        "description": "历史记录或预算模式未保存逐单元材料热物性证据；不以当前数据集回填。"})
    nozzle_profile = (getattr(nozzle, "profile", None) or {}) if nozzle else {}
    if cooling_profile.get("model_id") == "regen_quasi1d_ph_v1":
        report["cooling"] = {
            "id": "regen_quasi1d_ph_v1", "label": "统一准一维冷却：压力与焓沿程积分",
            "total_heat_transfer_W": cooling.outputs.get("heat_load_W"),
            "description": "冷却沿程节点、总吸热、压降和出口状态来自同一模型；纯流体检查声明温压域和单相，传热相关式及高压输运物性精度仍待独立验证。",
            "validation_status": "correlations_not_independently_validated"}
    if nozzle_profile.get("model_id") == "heated_frozen_quasi1d_v1":
        report["nozzle"] = {
            "id": "heated_frozen_quasi1d_v1", "label": "固定主室组成的准一维有热损喷管",
            "description": "冻结主室组成，从主室平衡参考状态起按冷却覆盖沿程扣除全部再生吸热；声速位置由面积变化与沿程热流的跨声速临界条件求解，不强制位于几何喉部。循环室压和温度是零速储室参考总态，空间热侧返回局部静压与静温；有限燃烧室喷注和压力预算与该总态的对应关系仍属系统级假设。不再假定全喷管等熵，也不把再生热量集中扣在主室平衡计算中。凝结、温区覆盖、高压混合燃气 EOS 与传热相关式仍须分别核查，数值闭合不代表适用性已验证。"}
        report["combustion"]["description"] = (
            "主室先按入口化学焓求未扣再生热的 HP 平衡参考状态；全部再生热在随后准一维热侧模型的对应位置扣除，不在主室平衡中重复扣热。化学平衡含解离，不等于燃尽率 100%。"
            if mode == "adiabatic" else
            "兼容温升折减的焓差仍单独列为经验损失；全部再生吸热在随后准一维热侧模型中按空间位置扣除。该经验因子不是 c* 效率或燃尽率。")
    for key, state in result.states.items():
        if key in GAS_STATIONS:
            continue
        positive = {name: value for name, value in (state.composition or {}).items() if value > 0}
        if len(positive) != 1:
            continue
        species, fraction = next(iter(positive.items()))
        if species not in ("CH4", "O2", "o2", "c12h26") or abs(fraction - 1.0) > 1e-8:
            continue
        from ui_metadata import STATE_LABELS
        row = {"id": key, "label": STATE_LABELS.get(key, state.name or key),
               "species": species, "T_K": state.temperature, "p_Pa": state.pressure}
        try:
            checked = pure_state_tp(species, state.temperature, state.pressure)
            row.update(status=checked["range_status"], phase=checked["phase"], limits=checked["limits"],
                       melting_boundary=checked["melting_boundary"],
                       description="已检查声明温压范围、单相与可用熔化线；未认证实验精度、完整相平衡或汽蚀裕度。")
        except Exception as exc:
            row.update(status="invalid_or_unavailable", reason=str(exc))
        report["pure_fluid_states"].append(row)
    report["pure_fluid_status"] = ("invalid_or_unavailable" if any(
        row["status"] != "within_declared_ranges" for row in report["pure_fluid_states"])
        else "within_declared_ranges" if report["pure_fluid_states"] else "unavailable")
    if not thermo.CANTERA_AVAILABLE or result.summary.get("thermo_source") != 1.0:
        report["status"] = "unavailable"
        report["notes"].append("缺少 Cantera 气相来源，不能用理想混合物数据范围替经验结果背书。")
        return report
    gas = thermo._get_gas(fuel.fuel_species)
    import hashlib
    from pathlib import Path
    source = Path(thermo.ct.__file__).resolve().parent / "data" / filename
    report["thermochemical_data"] = {
        "id": filename, "label": filename if kerosene else dataset["label"],
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "cantera_version": thermo.ct.__version__, "phase": gas.name,
        "species_count": gas.n_species,
        "common_min_temperature_K": gas.min_temp, "common_max_temperature_K": gas.max_temp,
        "description": "气相平衡与冻结膨胀使用所选物种热化学数据；未验证有限速率反应、高压非理想性、凝聚相或积碳。",
    }
    saved = gas.state.copy()
    report["gas_model"].update(id=gas.thermo_model, label="理想气体混合物" if gas.thermo_model == "ideal-gas" else gas.thermo_model)
    stations = [(key, label, state.temperature, state.pressure, state.composition)
                for key, label in GAS_STATIONS.items()
                if (state := result.states.get(key)) is not None]
    # GG results do not always carry nozzle FlowStates; inspect the same recorded
    # frozen composition with the nozzle's saved T/P. Do not solve a new nozzle.
    chamber = result.states.get("main_chamber")
    if nozzle and chamber and nozzle_model == 0.0:
        for key, label, tk, pk in (("nozzle_throat", "主喷管喉部", "T_throat_K", "p_throat_Pa"),
                                  ("nozzle_exit", "主喷管出口", "T_exit_K", "p_exit_Pa")):
            if key not in result.states and nozzle.outputs.get(tk) is not None and nozzle.outputs.get(pk) is not None:
                stations.append((key, label, nozzle.outputs[tk], nozzle.outputs[pk], chamber.composition))
    stations.sort(key=lambda row: list(GAS_STATIONS).index(row[0]))
    try:
        for key, label, temperature, pressure, composition in stations:
            row = {"id": key, "label": label, "T_K": temperature, "p_Pa": pressure,
                   "gas_model": gas.thermo_model}
            try:
                if not composition or not math.isfinite(temperature) or not math.isfinite(pressure):
                    raise ValueError("缺失或无效的气相状态")
                gas.TPY = temperature, pressure, thermo._dict_to_Y(gas, composition)
                row["thermo_range"] = thermo.gas_temperature_applicability(gas)
            except Exception as exc:
                row["thermo_range"] = {"status": "unavailable", "reason": str(exc)}
            report["states"].append(row)
    finally:
        gas.state = saved
    report["temperature_status"] = ("unavailable" if not report["states"] or any(
        s["thermo_range"]["status"] == "unavailable" for s in report["states"])
        else "extrapolated" if any(s["thermo_range"]["status"] == "extrapolated" for s in report["states"])
        else "within_declared_ranges")
    return report


def attach_thermodynamic_applicability(result):
    result.thermodynamic_applicability = build_thermodynamic_applicability(result)
    if any(s["thermo_range"]["status"] == "extrapolated"
           for s in result.thermodynamic_applicability["states"]):
        message = "最终燃气状态存在热力学数据温区外推；数值收敛不代表适用性通过，详见热力学适用性检查。"
        if message not in result.warnings:
            result.warnings.append(message)
    if result.thermodynamic_applicability.get("pure_fluid_status") == "invalid_or_unavailable":
        message = "保存的纯推进剂状态未全部通过声明温压域及单相检查；详见热力学适用性检查。"
        if message not in result.warnings:
            result.warnings.append(message)
    solid = result.thermodynamic_applicability.get("solid_wall_thermal", {})
    if solid.get("status") == "out_of_range":
        message = (f"壁面导热率数据超出来源温区：{solid.get('out_of_range_cell_count')}/{solid.get('total_cells')} 个热单元；"
                   "诊断解使用端点常值延用，未验证材料适用性。")
        if message not in result.warnings:
            result.warnings.append(message)
    if solid.get("manufacturing_match") in ("unknown", "mismatch"):
        message = "壁面制造状态未与热物性数据试样匹配；温区内不等于实物或重复使用性能已验证。"
        if message not in result.warnings:
            result.warnings.append(message)
    return result
