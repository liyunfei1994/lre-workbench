"""Record-bound axial turbine annulus and row envelopes for concept CAD.

The cycle model has no resolved port velocities or declared total/static state
convention. An explicitly saved assumption treats its node p/T as static for
annulus sizing only. This module neither redesigns the turbine nor solves blades.
"""
from copy import deepcopy
import hashlib
import json
import math


METHOD = "axial_turbine_annulus_v1"
R_UNIVERSAL = 8314.462618  # J/(kmol K); saved molar_mass_in is kg/kmol.
ASSUMPTION_KEYS = ("inlet_flow_coefficient", "outlet_flow_coefficient",
                   "stator_axial_chord_ratio", "rotor_axial_chord_ratio",
                   "row_gap_ratio", "geometry_state_basis")
FIXED_ASSUMPTIONS = {
    "method_id": METHOD,
    "density_model": "ideal_gas_frozen_inlet_molar_mass",
    "annulus_model": "constant_mean_radius",
    "interior_model": "linear_geometric_interpolation",
    "stage_model": "saved_stage_count_row_envelopes",
    "blockage_factor": 1.0,
    "leakage_mass_flow_kg_s": 0.0,
}
SOURCES = [
    {"title": "CFturbo · Axial turbine design parameters", "url": "https://manual.cfturbo.com/en/md_parameter_axturbine.html"},
    {"title": "CFturbo · Axial turbine main dimensions", "url": "https://manual.cfturbo.com/en/md_dimensions_axturbine.html"},
    {"title": "CFturbo · Global setup and total/static definitions", "url": "https://manual.cfturbo.com/en/global_setup.html"},
    {"title": "CFturbo · Import 2D point data", "url": "https://manual.cfturbo.com/en/import_2dpointdata.html"},
]
ERRORS = (TypeError, ValueError, KeyError, OverflowError, ZeroDivisionError, AttributeError)


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _positive(value, label):
    if not _finite(value) or value <= 0:
        raise ValueError(f"{label}缺失或不是有限正数。")
    return float(value)


def _match(actual, expected, label):
    if not _finite(actual) or not _finite(expected) or not math.isclose(actual, expected, rel_tol=2e-7, abs_tol=1e-12):
        raise ValueError(f"{label}与保存的涡轮系统点或几何不一致。")


def _stages(value):
    n = _positive(value, "涡轮级数")
    if int(n) != n or not 1 <= n <= 6:
        raise ValueError("保存的涡轮级数须为 1–6 的整数。")
    return int(n)


def _digest(turbine):
    content = {key: turbine[key] for key in ("design_point", "assumptions", "dimensions", "meridional", "rows")}
    content["source"] = {key: turbine["source"][key] for key in ("component_key", "assumption_prefix")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _point(outputs):
    fields = {
        "mass_flow_kg_s": "mass_flow_kgs", "pressure_in_pa": "p_in_Pa",
        "pressure_out_pa": "p_out_Pa", "temperature_in_k": "T_in_K",
        "temperature_out_k": "T_exhaust_K", "shaft_speed_rpm": "shaft_speed_rpm",
        "mean_diameter_m": "D_mean_m", "mean_speed_m_s": "U_mean_actual_ms",
        "molar_mass_kg_kmol": "molar_mass_in", "power_w": "turbine_power_W",
        "isentropic_drop_j_kg": "isentropic_drop_Jkg", "actual_drop_j_kg": "actual_drop_Jkg",
        "pressure_ratio": "turbine_pressure_ratio", "efficiency": "eta_turbine",
    }
    point = {key: _positive(outputs.get(source), source) for key, source in fields.items()}
    point["stages"] = _stages(outputs.get("n_stages_recommended"))
    point["gas_constant_j_kg_k"] = R_UNIVERSAL / point["molar_mass_kg_kmol"]
    for end in ("in", "out"):
        rho = point[f"pressure_{end}_pa"] / (point["gas_constant_j_kg_k"] * point[f"temperature_{end}_k"])
        point[f"density_{end}_kg_m3"] = rho
        point[f"volume_flow_{end}_m3_s"] = point["mass_flow_kg_s"] / rho
    for src, dest in (("gamma_in", "gamma_in"), ("cp_in_Jkgk", "cp_in_j_kg_k"),
                      ("u_over_c_target", "u_over_c_target"), ("u_over_c_actual", "u_over_c_actual")):
        if src in outputs:
            point[dest] = _positive(outputs[src], src)
    if "u_over_c_target" in point:
        point["target_mean_diameter_m"] = (60 * point["u_over_c_target"] * math.sqrt(2 * point["isentropic_drop_j_kg"])
                                            / (math.pi * point["shaft_speed_rpm"]))
        point["diameter_limited"] = not math.isclose(point["mean_diameter_m"], point["target_mean_diameter_m"], rel_tol=2e-7)
    _validate_point(point)
    if "C0_spouting_ms" in outputs:
        _match(outputs["C0_spouting_ms"], math.sqrt(2 * point["isentropic_drop_j_kg"]), "等熵喷出速度")
    return point


def _validate_point(point):
    required = ("mass_flow_kg_s", "pressure_in_pa", "pressure_out_pa", "temperature_in_k", "temperature_out_k",
                "shaft_speed_rpm", "mean_diameter_m", "mean_speed_m_s", "molar_mass_kg_kmol", "power_w",
                "isentropic_drop_j_kg", "actual_drop_j_kg", "pressure_ratio", "efficiency", "gas_constant_j_kg_k",
                "density_in_kg_m3", "density_out_kg_m3", "volume_flow_in_m3_s", "volume_flow_out_m3_s")
    for key in required:
        _positive(point.get(key), "保存系统点 " + key)
    _stages(point.get("stages"))
    if point["pressure_in_pa"] <= point["pressure_out_pa"] or point["efficiency"] > 1:
        raise ValueError("涡轮系统点须为正压降且效率不大于 1。")
    _match(point["pressure_ratio"], point["pressure_in_pa"] / point["pressure_out_pa"], "涡轮压比")
    _match(point["mean_speed_m_s"], math.pi * point["mean_diameter_m"] * point["shaft_speed_rpm"] / 60, "平均圆周速度")
    _match(point["actual_drop_j_kg"], point["efficiency"] * point["isentropic_drop_j_kg"], "实际焓降")
    _match(point["power_w"], point["mass_flow_kg_s"] * point["actual_drop_j_kg"], "涡轮功率")
    _match(point["gas_constant_j_kg_k"], R_UNIVERSAL / point["molar_mass_kg_kmol"], "燃气比气体常数")
    for end in ("in", "out"):
        rho = point[f"pressure_{end}_pa"] / (point["gas_constant_j_kg_k"] * point[f"temperature_{end}_k"])
        _match(point[f"density_{end}_kg_m3"], rho, "端点参考密度")
        _match(point[f"volume_flow_{end}_m3_s"], point["mass_flow_kg_s"] / rho, "端点体积流量")
    if "u_over_c_actual" in point:
        _match(point["u_over_c_actual"], point["mean_speed_m_s"] / math.sqrt(2 * point["isentropic_drop_j_kg"]), "实际速比")
    if "u_over_c_target" in point:
        ideal = 60 * _positive(point["u_over_c_target"], "目标速比") * math.sqrt(2 * point["isentropic_drop_j_kg"]) / (math.pi * point["shaft_speed_rpm"])
        _match(point.get("target_mean_diameter_m"), ideal, "速比目标对应平均直径")
        limited = not math.isclose(point["mean_diameter_m"], ideal, rel_tol=2e-7)
        if point.get("diameter_limited") is not limited:
            raise ValueError("平均直径限制标记不一致。")


def _shape(point, assumptions):
    _validate_point(point)
    for key, expected in FIXED_ASSUMPTIONS.items():
        actual = assumptions.get(key)
        if actual != expected or isinstance(actual, bool):
            raise ValueError("保存的几何方法或边界约定无法识别：" + key)
    if assumptions.get("geometry_state_basis") != "system_pt_as_static":
        raise ValueError("须明确保存将系统节点 p/T 作为静态参考的几何假设；不能自动推断总态/静态。")
    numeric = {key: _positive(assumptions.get(key), "涡轮几何假设 " + key) for key in ASSUMPTION_KEYS[:-1]}
    dm, um, n = point["mean_diameter_m"], point["mean_speed_m_s"], _stages(point["stages"])
    cin, cout = numeric["inlet_flow_coefficient"] * um, numeric["outlet_flow_coefficient"] * um
    ain, aout = point["volume_flow_in_m3_s"] / cin, point["volume_flow_out_m3_s"] / cout
    hin, hout = ain / (math.pi * dm), aout / (math.pi * dm)
    if not 0 < hin < dm or not 0 < hout < dm:
        raise ValueError("连续性要求的环道高度不小于平均直径或无效，无法形成正半径轮毂；请调整几何假设或改用其他型式，尺寸不作截断。")
    ls = numeric["stator_axial_chord_ratio"] * dm
    lr = numeric["rotor_axial_chord_ratio"] * dm
    gap = numeric["row_gap_ratio"] * dm
    length = n * (ls + lr) + (2 * n - 1) * gap
    dimensions = {"mean_diameter_m": dm, "inlet_hub_diameter_m": dm - hin, "inlet_shroud_diameter_m": dm + hin,
                  "outlet_hub_diameter_m": dm - hout, "outlet_shroud_diameter_m": dm + hout,
                  "inlet_blade_height_m": hin, "outlet_blade_height_m": hout,
                  "inlet_area_m2": ain, "outlet_area_m2": aout,
                  "inlet_axial_velocity_m_s": cin, "outlet_axial_velocity_m_s": cout,
                  "axial_length_m": length, "stator_axial_chord_m": ls, "rotor_axial_chord_m": lr, "row_gap_m": gap}
    dimensions["maximum_annulus_tip_speed_m_s"] = math.pi * max(dm + hin, dm + hout) * point["shaft_speed_rpm"] / 60
    for key, value in dimensions.items():
        _positive(value, "派生几何 " + key)
    for end, area in (("inlet", ain), ("outlet", aout)):
        dh, ds = dimensions[f"{end}_hub_diameter_m"], dimensions[f"{end}_shroud_diameter_m"]
        if not dh < dm < ds:
            raise ValueError("环道高度小于数值分辨能力，无法生成可靠轮毂与机匣边界。")
        _match(math.pi * (ds * ds - dh * dh) / 4, area, "环形截面积")

    def boundary(z, wall):
        height = hin + (hout - hin) * (z / length)
        return [z, (dm + (-height if wall == "hub" else height)) / 2]

    meridional = {wall: [boundary(length * i / 64, wall) for i in range(65)] for wall in ("hub", "shroud")}
    meridional.update(coordinate_origin="first_stator_inlet", rotation_axis="z", unit="m",
                      construction="linear_annulus_height_constant_mean_radius")
    rows = []
    z = 0.
    for stage in range(1, n + 1):
        for kind, chord, label in (("stator", ls, "静叶"), ("rotor", lr, "动叶")):
            end = z + chord
            if not 0 <= z < end <= length * (1 + 1e-12):
                raise ValueError("叶排轴向尺寸超出数值分辨能力。")
            row = {"stage": stage, "kind": kind, "label": f"第 {stage} 级{label}包络", "z_start_m": z, "z_end_m": end}
            row.update({wall: [boundary(z, wall), boundary(end, wall)] for wall in ("hub", "shroud")})
            rows.append(row)
            next_z = end + gap
            if next_z <= end:
                raise ValueError("叶排间隙超出数值分辨能力。")
            z = next_z
    _match(rows[-1]["z_end_m"], length, "多级总轴长")
    return dimensions, meridional, rows


def _specs(result):
    calculation = result.get("calculation") or {}
    if not isinstance(calculation, dict):
        raise ValueError("计算记录格式无效。")
    components = calculation.get("components") or {}
    inputs = calculation.get("inputs") or result.get("submitted_inputs") or {}
    if not isinstance(components, dict) or not isinstance(inputs, dict):
        raise ValueError("计算记录缺少有效的部件或保存参数。")
    if "gg_turbine" in components:
        if any(key in components for key in ("gg_turbine_ox", "gg_turbine_fuel", "ox_preburner_turbine", "fuel_preburner_turbine")):
            raise ValueError("记录同时含共享与独立涡轮，不能确定轴系拓扑。")
        return [("shared", "gg_turbine", "fuel", "燃气发生器共轴涡轮")], components, inputs
    if any(key in components for key in ("gg_turbine_ox", "gg_turbine_fuel")):
        if any(key in components for key in ("ox_preburner_turbine", "fuel_preburner_turbine")):
            raise ValueError("记录同时含 GG 与分级燃烧涡轮，不能确定循环拓扑。")
        return [("ox", "gg_turbine_ox", "ox", "氧泵轴涡轮（GG 燃气）"),
                ("fuel", "gg_turbine_fuel", "fuel", "燃料泵轴涡轮（GG 燃气）")], components, inputs
    if inputs.get("cycle_type") == "gg" and inputs.get("gg_layout") == "single_shaft":
        return [("shared", "gg_turbine", "fuel", "燃气发生器共轴涡轮")], components, inputs
    if inputs.get("cycle_type") == "gg" and inputs.get("gg_layout") == "dual_shaft":
        return [("ox", "gg_turbine_ox", "ox", "氧泵轴涡轮（GG 燃气）"),
                ("fuel", "gg_turbine_fuel", "fuel", "燃料泵轴涡轮（GG 燃气）")], components, inputs
    return [("ox", "ox_preburner_turbine", "ox", "富氧涡轮"),
            ("fuel", "fuel_preburner_turbine", "fuel", "富燃涡轮")], components, inputs


def _build_turbine(component, inputs, key, prefix, label, record_id):
    source = {"record_id": record_id, "component_key": key, "assumption_prefix": prefix,
              "kind": "saved_turbine_point", "label": "本次计算记录的涡轮系统点"}
    value = {"available": False, "label": label, "source": source, "design_point": {}, "dimensions": {}}
    try:
        outputs = component.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise ValueError("记录缺少有效的涡轮输出。")
        if outputs.get("state_basis") == "total_to_total":
            raise ValueError("本记录采用显式总态接口。未解析静态密度，不使用旧静态参考假设生成环道；请在 CFturbo 中设计子午面。")
        if _finite(outputs.get("D_mean_m")) and outputs["D_mean_m"] > 0:
            value["dimensions"]["mean_diameter_m"] = outputs["D_mean_m"]
        point = _point(outputs)
        value["design_point"] = point
        missing = [f"{prefix}_turbine_{key}" for key in ASSUMPTION_KEYS if f"{prefix}_turbine_{key}" not in inputs]
        if missing:
            raise ValueError("旧记录未完整保存涡轮入口/出口速度比、静动叶轴向弦长比、叶排间隙比或状态基准。请明确假设后运行新的设计点；本记录不使用当前默认值补图。")
        assumptions = {key: inputs[f"{prefix}_turbine_{key}"] for key in ASSUMPTION_KEYS} | FIXED_ASSUMPTIONS
        dimensions, meridional, rows = _shape(point, assumptions)
        checks = [
            {"id": "system_point_identity", "label": "系统点与平均直径一致性", "status": "pass",
             "note": "质量流量、转速、焓降、功率、级数与平均直径来自同一保存部件；U=πDn/60。"},
            {"id": "endpoint_continuity", "label": "进出口质量连续性", "status": "pass",
             "note": "ṁ=ρ·cm·π(Ds²−Dh²)/4；ρ=p/(R·T)，R 由保存的入口混合物分子量计算；cm=φ·U。"},
            {"id": "state_convention", "label": "节点状态作为静态参考", "status": "assumed",
             "note": "循环未定义已验证的总态/静态及端口速度；本图按明确保存的假设把节点 p/T 用作静态密度参考，没有进行总静转换。"},
            {"id": "diameter_provenance", "label": "平均直径的系统来源", "status": "warning" if point.get("diameter_limited") else "info",
             "note": "沿用系统部件保存的 D_mean；现有系统估算含 0.05–0.80 m 限幅，故可能不满足目标 U/C0。此模块未重新优化、放大或截断直径/环道高度。"},
            {"id": "row_layout", "label": "叶排轴向布置", "status": "assumed",
             "note": "按保存级数重复静叶、动叶包络和排间隙；中间轮廓为几何线性插值，不是级间流动或各级焓降计算。"},
            {"id": "detailed_design", "label": "叶片气动、CFD 与结构验证", "status": "not_evaluated",
             "note": "尚无速度三角形、反动度分配、叶型、叶片数/厚度/扭转、喉部堵塞、泄漏、间隙、强度与寿命验证；不能直接作为完整 CFD 或制造模型。"},
        ]
        value.update(available=True, source=source | {"kind": "saved_point_and_explicit_assumptions"},
                     assumptions=assumptions, dimensions=dimensions, meridional=meridional, rows=rows, checks=checks,
                     notes=["这是轴流式恒平均半径流道的概念包络；轮毂/机匣曲线是流体边界，非实体厚度、盘轴结构或真实叶片。",
                            "入口和出口分别按理想气体、冻结入口混合物分子量与保存状态计算密度；不假定两端同密度。",
                            "按每台涡轮保存的质量流量建图；GG 双轴采用各自分流，GG 单轴只显示一台共享涡轮。",
                            "轴向弦长比和所有相邻叶排间隙比是布局假设。中间截面不提供未经计算的压力、温度或马赫数。",
                            "平均直径和推荐级数来自系统估算。叶排数量不代表已完成的级负荷、速度复合/压力复合型式或效率设计。",
                            "平均圆周速度 U 不等于外缘速度；环道外缘速度上界按进出口最大机匣半径乘角速度计算，不据此声称真实叶尖强度合格。",
                            "CFturbo 需要明确入口总压/总温及总总/总静压比；请先完成状态定义和气动详细设计。可将 z,r 轮廓用作 CAD 布局起点。"],
                     references=deepcopy(SOURCES))
        value["geometry_sha256"] = _digest(value)
    except ERRORS as exc:
        value["available"] = False
        for name in ("meridional", "rows", "assumptions", "checks", "notes", "references", "geometry_sha256"):
            value.pop(name, None)
        value["dimensions"] = {k: v for k, v in value["dimensions"].items() if k == "mean_diameter_m" and _finite(v) and v > 0}
        value["reason"] = str(exc)
    return value


def _same(actual, expected, label):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError(label + "字段不完整或无法识别。")
        for key in expected:
            _same(actual[key], expected[key], label)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError(label + "坐标或叶排数量不完整。")
        for a, b in zip(actual, expected):
            _same(a, b, label)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        _match(actual, expected, label)
    elif actual != expected or type(actual) is not type(expected):
        raise ValueError(label + "约定不一致。")


def _saved_turbine(turbine, side, record_id):
    if not isinstance(turbine, dict) or not isinstance(turbine.get("source"), dict):
        raise ValueError("保存的涡轮几何缺少来源。")
    value = deepcopy(turbine)
    if type(value.get("available")) is not bool:
        raise ValueError("保存的涡轮几何状态无效。")
    valid_sources = {"ox": {( "ox_preburner_turbine", "ox"), ("gg_turbine_ox", "ox")},
                     "fuel": {("fuel_preburner_turbine", "fuel"), ("gg_turbine_fuel", "fuel")},
                     "shared": {("gg_turbine", "fuel")}}
    source = value["source"]
    if (source.get("component_key"), source.get("assumption_prefix")) not in valid_sources[side]:
        raise ValueError("保存的涡轮来源与轴系位置不一致。")
    source["record_id"] = record_id
    if not value["available"]:
        # Reject non-finite corrupt snapshots, including unavailable historical ones.
        json.dumps(value, allow_nan=False)
        return value
    if value.get("geometry_sha256") != _digest(value):
        raise ValueError("保存的涡轮几何校验值不一致；不能按当前输入补图。")
    dimensions, meridional, rows = _shape(value["design_point"], value["assumptions"])
    _same(value.get("dimensions"), dimensions, "保存的涡轮尺寸")
    _same(value.get("meridional"), meridional, "保存的子午面")
    _same(value.get("rows"), rows, "保存的叶排包络")
    json.dumps(value, allow_nan=False)
    return value


def turbine_geometry_view(result, record_id=""):
    """Create a calculation snapshot or validate/read it without current defaults."""
    source = {"record_id": record_id, "label": "本次计算记录", "kind": "saved_record"}
    response = {"schema_version": 1, "source": source, "turbines": {}}
    sides = ["ox", "fuel"]
    saved = result.get("turbine_geometry") if isinstance(result, dict) else None
    try:
        if saved is not None:
            if not isinstance(saved, dict) or saved.get("schema_version") != 1 or not isinstance(saved.get("turbines"), dict):
                raise ValueError("保存的涡轮几何快照版本或结构无效。")
            sides = list(saved["turbines"])
            if set(sides) not in ({"ox", "fuel"}, {"shared"}):
                sides = ["ox", "fuel"]
                raise ValueError("保存的涡轮几何轴系拓扑无效。")
            if set(sides) == {"ox", "fuel"}:
                components = [(saved["turbines"][side].get("source") or {}).get("component_key", "")
                              for side in ("ox", "fuel") if isinstance(saved["turbines"][side], dict)]
                if (any(key.startswith("gg_turbine_") for key in components)
                        and any(key in ("ox_preburner_turbine", "fuel_preburner_turbine") for key in components)):
                    raise ValueError("保存的涡轮几何混合了不同循环的部件来源。")
            for side in sides:
                try:
                    response["turbines"][side] = _saved_turbine(saved["turbines"][side], side, record_id)
                except ERRORS as exc:
                    response["turbines"][side] = {"available": False, "label": "共享涡轮" if side == "shared" else ("氧泵轴涡轮" if side == "ox" else "燃料泵轴涡轮"),
                                                  "source": deepcopy(source), "reason": str(exc), "design_point": {}, "dimensions": {}}
        elif isinstance(result, dict):
            specs, components, inputs = _specs(result)
            for side, key, prefix, label in specs:
                response["turbines"][side] = _build_turbine(components.get(key) or {}, inputs, key, prefix, label, record_id)
        else:
            raise ValueError("选择已完成的设计点记录后查看涡轮几何。")
    except ERRORS as exc:
        for side in sides:
            response["turbines"][side] = {"available": False, "label": "共享涡轮" if side == "shared" else ("氧泵轴涡轮" if side == "ox" else "燃料泵轴涡轮"),
                                          "source": deepcopy(source), "reason": str(exc), "design_point": {}, "dimensions": {}}
    return response
