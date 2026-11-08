"""Read-only, explicitly constrained Euler/Zweifel turbine design scenarios.

This is a constant-mean-radius, constant-axial-velocity preliminary calculation.
It does not solve static states, losses, choking, metal angles or blade surfaces.
Angles are signed from the axial direction; positive swirl follows rotation.
No external fluid library or current project draft is used.
"""
from copy import deepcopy
import hashlib
import json
import math


METHOD = "constant_radius_euler_zweifel_v1"
REFERENCES = [
    {"title": "CFturbo · Axial turbine main dimensions", "url": "https://manual.cfturbo.com/en/md_dimensions_axturbine.html"},
    {"title": "CFturbo · Blade count and Zweifel coefficient", "url": "https://manual.cfturbo.com/en/bladeprop.html"},
    {"title": "CFturbo · Rotor/stator blade-count interaction", "url": "https://manual.cfturbo.com/en/bladeprop_stator_blades.html"},
    {"title": "NASA · Blading models for TURBAN and CSPAN", "url": "https://ntrs.nasa.gov/api/citations/19940006731/downloads/19940006731.pdf"},
]
LIMITATIONS = [
    "本方案独立于保存的循环结果；不会回写历史记录、改变轴系转速或重新优化循环效率。",
    "各级采用相同中径和轴向速度；实际总比功按用户指定比例分配，各级压力比未求解。",
    "角度自正轴向量起，正切向与转动同向；全部是流角，不是叶片金属角，录入 CFturbo 前须核对角度约定。",
    "输入反动度目标定义为动叶静焓降/级欧拉比功；整级静焓降反动度另算，只有重复级 c0=c2 时二者相同。",
    "静叶按绝热无轴功建立运动学能量关系，不代表静叶无总压损失；未求静叶损失或出口总压。",
    "Zweifel 叶片数仅为不可压二维叶栅初估；未核对亚声速适用性、叶片厚度、堵塞、入射/落后角和三维损失。",
    "未求静压、静温、密度、声速、马赫数、喉部、壅塞和通流面积；不生成真实环道或叶片实体。",
    "叶片数取最近整数后回算实际 Zweifel 系数；动静叶数仅筛查 1–3 阶周期差，未完成模态、强度、疲劳、热载荷与材料验证。",
]
SCENARIO_KEYS = {
    "stages", "mean_diameter_m", "flow_coefficient", "reaction", "work_split",
    "work_fractions", "inlet_swirl_m_s", "stator_axial_chord_m", "rotor_axial_chord_m",
    "stator_zweifel", "rotor_zweifel",
}


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须显式给出数值")
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{label}须为有限数值") from None
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{label}须为有限{'正' if positive else ''}数值")
    return value


def _optional(value, label):
    return None if value is None else _number(value, label, positive=True)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _check(output, key, label, status, note, **values):
    output["checks"].append({"id": key, "label": label, "status": status, "note": note, **values})


def _blade_row(chord, target, inlet_tangent, outlet_tangent, ca, diameter):
    # Signed axial angles avoid the unsigned 'sum of tangents' ambiguity.
    # Z = 2(s/cx) cos²(theta_out) |tan(theta_in)-tan(theta_out)|.
    result = {"status": "missing", "axial_chord_m": chord, "zweifel_target": target,
              "blade_count_continuous": None, "blade_count_candidate": None,
              "pitch_m": None, "axial_solidity": None, "zweifel_candidate": None,
              "turning_deg": math.degrees(math.atan2(outlet_tangent, ca) - math.atan2(inlet_tangent, ca)),
              "note": "轴向弦长和 Zweifel 系数均需显式给定；缺失项不自动补值。"}
    if chord is None or target is None:
        return result
    force_factor = 2 * ca * abs(inlet_tangent - outlet_tangent) / (ca**2 + outlet_tangent**2)
    if force_factor <= 1e-14:
        result.update(status="unavailable", note="叶排转向为零或数值上退化，Zweifel 关系不能给出有限有效叶片数。")
        return result
    continuous = math.pi * diameter * force_factor / (target * chord)
    if not math.isfinite(continuous) or continuous > 2**53 - 1:
        result.update(status="unavailable", note="叶片数超出可可靠表示的数值范围；请核对弦长、流角与系数。")
        return result
    candidate = math.floor(continuous + .5)
    result["blade_count_continuous"] = continuous
    if candidate < 2:
        result.update(status="unavailable", note="最近整数候选小于 2，不能构成当前模型的环形叶栅；未截断为人为有效值。")
        return result
    pitch = math.pi * diameter / candidate
    result.update(status="candidate", blade_count_candidate=candidate, pitch_m=pitch,
                  axial_solidity=chord / pitch, zweifel_candidate=force_factor * pitch / chord,
                  note="Zweifel 二维叶栅初选；已按最近整数回算，尚未验证可压缩流动、厚度与动静干涉。")
    return result


def _harmonic_screening(stator, rotor):
    """Screen low periodicity differences; this is not a resonance solution."""
    ns, nr = stator.get("blade_count_candidate"), rotor.get("blade_count_candidate")
    result = {"method": "blade_count_periodicity_difference_orders_1_to_3_v1", "status": "missing",
              "stator_blade_count": ns, "rotor_blade_count": nr, "pairs": [],
              "note": "静叶与动叶均需有效叶片数候选，才能筛查动静周期差。"}
    if ns is None or nr is None:
        return result
    pairs = [{"stator_order": i, "rotor_order": j, "stator_periodicity": i * ns,
              "rotor_periodicity": j * nr, "m": abs(i * ns - j * nr),
              "status": "warning" if abs(i * ns - j * nr) <= 1 else "review" if abs(i * ns - j * nr) == 2 else "unverified"}
             for i in range(1, 4) for j in range(1, 4)]
    warnings = [p for p in pairs if p["m"] <= 1]
    review = [p for p in pairs if p["m"] == 2]
    if warnings:
        detail = "；".join(f"{p['stator_order']}×静叶数 − {p['rotor_order']}×动叶数：m={p['m']}" for p in warnings)
        note = f"存在低阶周期差（{detail}）；调整叶数/弦长/系数并做模态校核。候选未自动改动。"
    elif review:
        note = "1–3 阶组合中存在 m=2 周期差，仍需核对激振与模态；不能据叶数筛查判定结构安全。"
    else:
        note = "本次 1–3 阶组合未出现 m≤2，但这不代表无共振或结构安全；仍需频率、模态及工作转速范围校核。"
    result.update(status="warning" if warnings else "review", pairs=pairs, note=note)
    return result


def compute_turbine_preliminary(machine, scenario):
    """Return a reproducible local design without mutating either argument.

    ``machine`` is a server-produced turbine_design machine, never client data.
    Required scenario values have no implicit defaults. Optional blade inputs
    may be null, so an honest velocity-triangle study does not require a made-up
    chord. A missing total-state definition does not prevent this kinematic
    study, but cannot be upgraded into a solved thermodynamic boundary.
    """
    out = {"schema_version": 1, "method": METHOD, "status": "unavailable", "errors": [],
           "source": {}, "scenario": {}, "summary": {}, "stages": [], "checks": [],
           "limitations": list(LIMITATIONS), "references": deepcopy(REFERENCES),
           "calculation_sha256": None}
    try:
        if not isinstance(machine, dict) or not isinstance(scenario, dict):
            raise ValueError("来源记录与初选方案必须为对象")
        if machine.get("available") is False or machine.get("status") == "conflict":
            raise ValueError("来源记录不可用或存在冲突，不能开展初选")
        if any(isinstance(c, dict) and c.get("status") in ("conflict", "fail") for c in machine.get("checks", [])):
            raise ValueError("来源记录检查未通过，不能开展初选")
        unknown = set(scenario) - SCENARIO_KEYS
        if unknown:
            raise ValueError("初选方案包含不支持的字段：" + ", ".join(sorted(map(str, unknown))))
        point = machine.get("design_point")
        if not isinstance(point, dict):
            raise ValueError("来源记录缺少设计点")
        mass = _number(point.get("mass_flow_kg_s"), "保存的质量流量", positive=True)
        rpm = _number(point.get("shaft_speed_rpm"), "保存的轴转速", positive=True)
        work = _number(point.get("actual_drop_j_kg"), "保存的实际比功", positive=True)
        power = _number(mass * work, "计算气体功率", positive=True)
        if point.get("power_w") is not None:
            saved_power = _number(point["power_w"], "保存气体功率", positive=True)
            if not math.isclose(saved_power, power, rel_tol=2e-6, abs_tol=2e-5):
                raise ValueError("保存功率与质量流量×实际比功不一致")
        stages = _number(scenario.get("stages"), "初选级数", positive=True)
        if int(stages) != stages or not 1 <= stages <= 6:
            raise ValueError("初选级数须为 1–6 的整数")
        stages = int(stages)
        diameter = _number(scenario.get("mean_diameter_m"), "初选中径", positive=True)
        phi = _number(scenario.get("flow_coefficient"), "轴向流量系数", positive=True)
        reaction = _number(scenario.get("reaction"), "动叶静焓降/级欧拉比功目标")
        if not 0 <= reaction <= 1:
            raise ValueError("本初选模型的动叶静焓降/级欧拉比功目标须在 0–1 内")
        cu0 = _number(scenario.get("inlet_swirl_m_s"), "首级静叶入口切向速度")
        split = scenario.get("work_split")
        if split == "equal":
            if scenario.get("work_fractions") not in (None, []):
                raise ValueError("等比功分配不能同时指定自定义比例")
            fractions = [1 / stages] * stages
        elif split == "custom":
            fractions = scenario.get("work_fractions")
            if not isinstance(fractions, list) or len(fractions) != stages:
                raise ValueError("自定义比功比例的数量必须等于初选级数")
            fractions = [_number(x, "各级比功比例", positive=True) for x in fractions]
            if not math.isclose(math.fsum(fractions), 1, rel_tol=0, abs_tol=1e-8):
                raise ValueError("各级比功比例之和必须为 1；不自动归一化")
        else:
            raise ValueError("比功分配方式须明确选择 equal 或 custom")
        blade_inputs = {key: _optional(scenario.get(key), key) for key in
                        ("stator_axial_chord_m", "rotor_axial_chord_m", "stator_zweifel", "rotor_zweifel")}
        config = {"stages": stages, "mean_diameter_m": diameter, "flow_coefficient": phi,
                  "reaction": reaction, "work_split": split,
                  "work_fractions": fractions if split == "custom" else [], "inlet_swirl_m_s": cu0, **blade_inputs}
        omega = _number(2 * math.pi * rpm / 60, "角速度", positive=True)
        u = _number(omega * diameter / 2, "中径圆周速度", positive=True)
        ca = _number(phi * u, "轴向速度", positive=True)
        source = machine.get("source") if isinstance(machine.get("source"), dict) else {}
        out["source"] = {"record_id": source.get("record_id"), "component_key": source.get("component_key"),
                         "design_sha256": machine.get("design_sha256"), "kind": "saved_record_with_explicit_local_scenario",
                         "state_basis": point.get("state_basis", "unspecified"),
                         "mass_flow_kg_s": mass, "shaft_speed_rpm": rpm, "actual_drop_j_kg": work,
                         "power_w": power}
        out["scenario"] = config
        for i, fraction in enumerate(fractions):
            dh = work * fraction
            cu1 = u * (1 - reaction) + dh / (2 * u)
            cu2 = u * (1 - reaction) - dh / (2 * u)
            wu1, wu2 = cu1 - u, cu2 - u
            c0, c1, c2 = math.hypot(ca, cu0), math.hypot(ca, cu1), math.hypot(ca, cu2)
            rotor_drop = dh - (c1**2 - c2**2) / 2
            stator_drop = (c1**2 - c0**2) / 2
            stage_drop = dh + (c2**2 - c0**2) / 2
            row = {"stage": i + 1, "work_fraction": fraction, "work_j_kg": dh,
                   "power_w": mass * dh, "torque_nm": mass * dh / omega,
                   "loading_coefficient": dh / u**2, "u_m_s": u, "ca_m_s": ca,
                   "cu0_m_s": cu0, "cu1_m_s": cu1, "cu2_m_s": cu2, "wu1_m_s": wu1, "wu2_m_s": wu2,
                   "c0_m_s": c0, "c1_m_s": c1, "c2_m_s": c2, "w1_m_s": math.hypot(ca, wu1),
                   "w2_m_s": math.hypot(ca, wu2), "alpha0_deg": math.degrees(math.atan2(cu0, ca)),
                   "alpha1_deg": math.degrees(math.atan2(cu1, ca)), "alpha2_deg": math.degrees(math.atan2(cu2, ca)),
                   "beta1_deg": math.degrees(math.atan2(wu1, ca)), "beta2_deg": math.degrees(math.atan2(wu2, ca)),
                   "rotor_static_drop_j_kg": rotor_drop, "stator_static_drop_j_kg": stator_drop,
                   "stage_static_drop_j_kg": stage_drop, "reaction_target": reaction,
                   "reaction_check": rotor_drop / dh, "reaction_stage": rotor_drop / stage_drop if stage_drop > 0 else None,
                   "euler_residual_j_kg": u * (cu1 - cu2) - dh}
            for kind, inlet_cu, outlet_cu in (("stator", cu0, cu1), ("rotor", wu1, wu2)):
                row[kind] = _blade_row(blade_inputs[kind + "_axial_chord_m"], blade_inputs[kind + "_zweifel"],
                                       inlet_cu, outlet_cu, ca, diameter)
                b = row[kind]
                _check(out, f"stage_{i+1}_{kind}_blades", f"第 {i+1} 级{'静' if kind == 'stator' else '动'}叶数初选",
                       "missing" if b["status"] == "missing" else "warning" if b["status"] == "unavailable" else "assumed", b["note"])
                if b["zweifel_target"] is not None and not .75 <= b["zweifel_target"] <= 1.15:
                    _check(out, f"stage_{i+1}_{kind}_zweifel", "Zweifel 系数经验范围", "warning",
                           "显式系数位于 CFturbo 手册常见 0.75–1.15 之外；保留原值，须确认经验关系的适用性。")
            row["interaction"] = _harmonic_screening(row["stator"], row["rotor"])
            _check(out, f"stage_{i+1}_blade_interaction", f"第 {i+1} 级动静叶数谐波筛查",
                   row["interaction"]["status"], row["interaction"]["note"])
            if stage_drop <= 0 or stator_drop < 0:
                _check(out, f"stage_{i+1}_static_drop", f"第 {i+1} 级静焓降分配", "warning",
                       "给定入口旋流导致静叶扩压或整级静焓降非正；不能按常规加速涡轮叶栅解释，需重新核对入口和级间条件。")
            if not math.isclose(rotor_drop / dh, reaction, rel_tol=2e-7, abs_tol=1e-8) or abs(row["euler_residual_j_kg"]) > 1e-7 * dh:
                raise ValueError("给定参数引发均径欧拉功/动叶焓降闭合误差，无法可靠计算")
            out["stages"].append(row)
            cu0 = cu2
        total_work = math.fsum(row["work_j_kg"] for row in out["stages"])
        if not math.isclose(total_work, work, rel_tol=2e-8, abs_tol=1e-7):
            raise ValueError("各级比功总和未能与保存比功闭合")
        out["summary"] = {"mass_flow_kg_s": mass, "shaft_speed_rpm": rpm, "mean_diameter_m": diameter,
                          "u_m_s": u, "ca_m_s": ca, "total_work_j_kg": total_work,
                          "total_power_w": mass * total_work, "total_torque_nm": mass * total_work / omega,
                          "stages": stages, "flow_coefficient": phi, "reaction_target": reaction}
        _check(out, "work_balance", "各级实际比功之和", "pass", "与保存的循环实际比功闭合；不是热力状态或损失模型验收。", residual_j_kg=total_work - work)
        _check(out, "euler_balance", "逐级欧拉功与动叶静焓降", "pass", "给定约束下的运动学关系闭合。")
        _check(out, "stage_interface", "级间速度连续", "pass", "后级静叶入口切向速度继承前级动叶出口；轴向速度统一。")
        _check(out, "thermodynamic_states", "静态、马赫数与壅塞", "missing", "当前方法不求热力状态，不能确认通流能力或 Zweifel 的亚声速适用性。")
        _check(out, "blade_validation", "叶型、损失与结构", "missing", "尚未求叶片金属角、厚度、泄漏、振动及应力；叶片数只是候选。")
        out["status"] = "preliminary"
        out["calculation_sha256"] = _hash({key: value for key, value in out.items() if key != "calculation_sha256"})
    except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
        out.update(status="unavailable", errors=[str(exc)], scenario={}, summary={}, stages=[], checks=[], calculation_sha256=None)
    return out
