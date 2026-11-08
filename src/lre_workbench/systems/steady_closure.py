"""Independent energy audit of persisted FFSC states and component outputs.

The audit does not call thermodynamic functions, infer missing work, or read a
previous pass flag. Enthalpy references must be declared on every state. Values
can be dataclass objects during a calculation or dictionaries after JSON export.
"""
from __future__ import annotations

from collections.abc import Mapping
import math


ABSOLUTE_TOLERANCE_W = 10.0
RELATIVE_TOLERANCE = 1e-5
STATE_IDS = (
    "fuel_tank", "lox_tank", "fuel_boost_out", "lox_boost_out",
    "fuel_pump_inlet", "lox_pump_inlet", "fuel_after_pump", "lox_after_pump",
    "fuel_after_cooling", "branch_fuel_to_opb", "branch_fuel_to_fpb",
    "branch_ox_to_opb", "branch_ox_to_fpb", "ox_preburner_outlet",
    "fuel_preburner_outlet", "ox_turbine_exhaust", "fuel_turbine_exhaust",
    "main_chamber", "nozzle_exit",
)


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _number(value, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if abs(value) > 1e100 or not math.isfinite(value) or (nonnegative and value < 0):
        return None
    return float(value)


def _sum(values):
    values = list(values)
    if any(value is None for value in values):
        return None
    return math.fsum(values)


def regenerative_heat_assumption(components):
    """Describe saved component identities without solving or changing heat terms."""
    components = components if isinstance(components, Mapping) else {}
    cooling = components.get("cooling_channel")
    nozzle = components.get("nozzle")
    cooling_profile = _get(cooling, "profile", {}) or {}
    nozzle_profile = _get(nozzle, "profile", {}) or {}
    unified = (_get(cooling_profile, "version") == 1
               and _get(cooling_profile, "model_id") == "regen_quasi1d_ph_v1")
    distributed = _get(nozzle_profile, "model_id") == "heated_frozen_quasi1d_v1"
    if unified and distributed:
        chamber_heat = _number(_get(_get(components.get("main_chamber"), "outputs", {}), "heat_loss_W"))
        if chamber_heat != 0.0:
            return "已记录统一冷却与沿程非绝热热侧模型，但主室集中再生热汇并非已确认的零值；需核查是否重复扣热，不能仅凭模型名称判定闭合。"
        return "再生吸热、压降与出口状态来自统一准一维冷却剖面；燃气侧按同一组单元热量沿程扣热并冻结组成，主室不再重复集中扣除再生热。仍为系统级低阶模型，未获试验验证。"
    if _get(_get(cooling, "outputs", {}), "mode") == 0 and not cooling_profile and not nozzle_profile:
        flow = _get(_get(nozzle, "outputs", {}), "flow_model")
        nozzle_description = ("冻结组成的等熵喷管" if flow == 0 else
                              "定比热理想气体等熵喷管" if flow == -1 else "未确认的喷管模型")
        return f"采用明确给定吸热量与压降的系统预算；再生吸热在主燃烧室作为集中热汇，喷管为{nozzle_description}。该模式不提供冷却沿程解，也未求解沿程非绝热热侧。"
    return "冷却与热侧模型身份未完整记录或组合未识别；本报告不推断集中扣热或沿程传热假设，需核查组件记录。"


def audit_energy(states, components):
    """Return a versioned-result-compatible ledger with independently recomputed checks.

    Boundary: tank liquid inlets through nozzle static enthalpy plus kinetic
    energy. Booster work is external. Main pump/turbine work and regeneration
    heat are internal. The modeled combustion-efficiency energy sink is an
    explicit external equivalent heat loss. Internal kinetic/potential terms
    are neglected by the lumped model.
    """
    states = states if isinstance(states, Mapping) else {}
    components = components if isinstance(components, Mapping) else {}
    references = {}
    for key in STATE_IDS:
        reference = _get(_get(states.get(key), "metadata", {}), "reference_id")
        references[key] = reference if isinstance(reference, str) and reference.strip() else None
    declared = {value for value in references.values() if value is not None}
    valid_reference = len(declared) == 1 and all(value is not None for value in references.values())
    reference_id = next(iter(declared)) if len(declared) == 1 else None
    missing = []
    if not valid_reference:
        missing.append("全部流体状态必须声明相同的 metadata.reference_id；缺失或混合焓基准不能用于能量闭合。")

    def state_value(key, field, *, nonnegative=False):
        value = _number(_get(states.get(key), field), nonnegative=nonnegative)
        if value is None:
            missing.append(f"states.{key}.{field}")
        return value

    def flux(key, mass_key=None):
        # A branch throttle uses its own flow with the upstream specific enthalpy.
        mass = state_value(mass_key or key, "mass_flow", nonnegative=True)
        enthalpy = state_value(key, "enthalpy")
        return mass * enthalpy if mass is not None and enthalpy is not None and valid_reference else None

    def output(component, field):
        value = _number(_get(_get(components.get(component), "outputs", {}), field), nonnegative=True)
        if value is None:
            missing.append(f"components.{component}.outputs.{field}")
        return value

    enthalpy = {key: flux(key) for key in STATE_IDS}
    powers = {key: output(key, "power_W") for key in ("ox_boost_pump", "fuel_boost_pump", "ox_pump", "fuel_pump")}
    turbines = {side: output(f"{side}_preburner_turbine", "turbine_power_W") for side in ("ox", "fuel")}
    q_cool = output("cooling_channel", "heat_load_W")
    q_hot = output("main_chamber", "heat_loss_W")
    q_nozzle = _number(_get(_get(components.get("nozzle"), "outputs", {}), "heat_loss_W", 0.0), nonnegative=True)
    q_efficiency = output("main_chamber", "combustion_loss_W")
    velocity = _number(_get(_get(states.get("nozzle_exit"), "metadata", {}), "velocity_m_s"), nonnegative=True)
    if velocity is None:
        missing.append("states.nozzle_exit.metadata.velocity_m_s")
    nozzle_mass = state_value("nozzle_exit", "mass_flow", nonnegative=True)
    kinetic = .5 * nozzle_mass * velocity ** 2 if nozzle_mass is not None and velocity is not None else None
    checks = []

    def check(key, label, positive, negative, source):
        """Residual = sum(positive terms) - sum(negative terms), all in watts."""
        positive, negative = list(positive), list(negative)
        left, right = _sum(positive), _sum(negative)
        values = positive + negative
        available = valid_reference and left is not None and right is not None
        scale = max((abs(value) for value in values), default=0.0) if available else None
        tolerance = ABSOLUTE_TOLERANCE_W + RELATIVE_TOLERANCE * scale if scale is not None else None
        residual = math.fsum([*positive, *(-value for value in negative)]) if available else None
        status = "unavailable" if residual is None else "pass" if abs(residual) <= tolerance else "fail"
        row = dict(id=key, label=label, residual_W=residual, tolerance_W=tolerance,
                   status=status, left_W=left, right_W=right, source=source)
        checks.append(row)
        return row

    for side, prefix, label in (("fuel", "fuel", "燃料"), ("ox", "lox", "液氧")):
        check(f"{side}_boost_energy", f"{label}增压泵能量", [enthalpy[f"{prefix}_boost_out"]],
              [enthalpy[f"{prefix}_tank"], powers[f"{side}_boost_pump"]], "增压泵出口焓流 − 储箱焓流 − 外部轴功")
        check(f"{side}_feed_line_energy", f"{label}入口管路等焓", [enthalpy[f"{prefix}_pump_inlet"]],
              [enthalpy[f"{prefix}_boost_out"]], "主泵入口焓流 − 增压泵出口焓流")
        check(f"{side}_pump_energy", f"{label}主泵能量", [enthalpy[f"{prefix}_after_pump"]],
              [enthalpy[f"{prefix}_pump_inlet"], powers[f"{side}_pump"]], "主泵出口焓流 − 入口焓流 − 轴功")
    check("regenerative_coolant_energy", "冷却剂吸热与焓增", [enthalpy["fuel_after_cooling"]],
          [enthalpy["fuel_after_pump"], q_cool], "冷却出口焓流 − 冷却入口焓流 − 记录吸热量")
    for propellant, source, label in (("fuel", "fuel_after_cooling", "燃料"), ("ox", "lox_after_pump", "液氧")):
        branches = [f"branch_{propellant}_to_{pb}" for pb in ("opb", "fpb")]
        check(f"{propellant}_split_energy", f"{label}分流焓流", [enthalpy[key] for key in branches],
              [enthalpy[source]], "两支路焓流之和 − 取流点总焓流")
        for key, pb_label in zip(branches, ("富氧", "富燃")):
            check(f"{key}_throttle_energy", f"{label}至{pb_label}预燃室等焓", [enthalpy[key]],
                  [flux(source, key)], "支路入口焓流 − 同支路流量乘取流点比焓")
    for side, short, label in (("ox", "opb", "富氧"), ("fuel", "fpb", "富燃")):
        check(f"{side}_preburner_energy", f"{label}预燃室能量", [enthalpy[f"{side}_preburner_outlet"]],
              [enthalpy[f"branch_fuel_to_{short}"], enthalpy[f"branch_ox_to_{short}"]], "预燃室出口焓流 − 两推进剂入口焓流；生成焓已计入共同焓基准")
        check(f"{side}_turbine_energy", f"{label}涡轮能量", [enthalpy[f"{side}_turbine_exhaust"], turbines[side]],
              [enthalpy[f"{side}_preburner_outlet"]], "涡轮排气焓流 + 轴功 − 入口焓流")
        check(f"{side}_shaft_energy", f"{label}侧轴功率", [turbines[side]], [powers[f"{side}_pump"]],
              "涡轮记录轴功 − 主泵记录轴功；本集总 FFSC 模型不另设机械损失")
    check("main_chamber_energy", "主燃烧室能量", [enthalpy["main_chamber"], q_hot, q_efficiency],
          [enthalpy["ox_turbine_exhaust"], enthalpy["fuel_turbine_exhaust"]], "主室出口焓流 + 冷却热汇 + 效率等效热损失 − 两路涡轮排气焓流")
    check("regenerative_heat_exchange", "再生冷却热量两侧一致性", [q_hot, q_nozzle], [q_cool],
          "主室集中热汇 + 热气沿程热汇 − 冷却剂吸热；同一传热只计一次")
    check("nozzle_energy", "热气沿程焓与动能", [enthalpy["nozzle_exit"], kinetic, q_nozzle], [enthalpy["main_chamber"]],
          "出口静焓流 + 动能流 + 沿程传热 − 主室总焓流")
    global_check = check("global_energy", "储箱至喷管出口能量", [enthalpy["nozzle_exit"], kinetic, q_efficiency],
          [enthalpy["fuel_tank"], enthalpy["lox_tank"], powers["fuel_boost_pump"], powers["ox_boost_pump"]],
          "出口静焓与动能 + 效率等效热损失 − 储箱入口焓 − 外部增压泵轴功；再生热与主轴功为内部传递")

    terms = []
    def term(key, label, value, role, source):
        terms.append(dict(id=key, label=label, value_W=value, role=role, source=source))
    for key, label in (("fuel_tank", "燃料储箱入口焓流"), ("lox_tank", "液氧储箱入口焓流")):
        term(key, label, enthalpy[key], "in", f"states.{key}.mass_flow × enthalpy")
    for side, label in (("fuel", "燃料"), ("ox", "液氧")):
        term(f"{side}_boost_power", f"{label}增压泵外部轴功", powers[f"{side}_boost_pump"], "in", f"components.{side}_boost_pump.outputs.power_W")
        term(f"{side}_pump_power", f"{label}主泵轴功", powers[f"{side}_pump"], "internal", f"components.{side}_pump.outputs.power_W")
        term(f"{side}_turbine_power", f"{label}侧涡轮轴功", turbines[side], "internal", f"components.{side}_preburner_turbine.outputs.turbine_power_W")
    term("nozzle_static_enthalpy", "喷管出口静焓流", enthalpy["nozzle_exit"], "out", "states.nozzle_exit.mass_flow × enthalpy")
    term("nozzle_kinetic", "喷管出口动能流", kinetic, "out", "0.5 × states.nozzle_exit.mass_flow × metadata.velocity_m_s²")
    term("combustion_loss", "历史温升折减等效热损失", q_efficiency, "out", "components.main_chamber.outputs.combustion_loss_W；焓平衡模式此项为零，历史模型为显式经验热汇")
    term("coolant_heat", "冷却剂吸收热量", q_cool, "internal", "components.cooling_channel.outputs.heat_load_W")
    term("hot_gas_heat", "燃气侧集总冷却热汇", q_hot, "internal", "components.main_chamber.outputs.heat_loss_W")
    term("distributed_hot_gas_heat", "热气侧沿程传热", q_nozzle, "internal", "components.nozzle.outputs.heat_loss_W")
    statuses = {row["status"] for row in checks}
    status = "fail" if "fail" in statuses else "pass" if statuses == {"pass"} else "unavailable"
    return dict(
        status=status,
        reference=f"统一生成焓与显热基准：{reference_id}" if valid_reference else "焓基准声明缺失或不一致，无法独立比较能量。",
        reference_id=reference_id if valid_reference else None,
        boundary="两种推进剂储箱入口至喷管出口；出口包含静焓与动能，增压泵轴功为外部输入。",
        terms=terms, checks=checks, residual_W=global_check["residual_W"], tolerance_W=global_check["tolerance_W"],
        absolute_tolerance_W=ABSOLUTE_TOLERANCE_W, relative_tolerance=RELATIVE_TOLERANCE,
        missing_evidence=list(dict.fromkeys(missing)),
        limitations=[
            "本检查采用保存的状态与部件输出重新核算控制体能量，不构成独立试验验证。",
            ("准一维热气按冷却单元位置扣热，主室入口平衡不再集中扣除全部再生热；关联式仍需独立验证。" if q_nozzle else
             "预算模式的再生热集中从主室总焓扣除；不验证沿程传热分布。"),
            "内部流路动能、位能、对外壁面散热及轴承机械损失在本集总模型中忽略；燃烧效率以显式等效热汇表示。",
            "每项容差为 10 W + 1e-5 × 该控制体最大绝对能量项，参考焓为负不表示热量流向反转。",
        ],
    )
