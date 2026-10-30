"""Record-bound radial impeller flow-passage geometry for system concept design.

This is a representative equal-head stage, not blade, inducer or casing design.
Every length comes from the saved hydraulic point and explicit saved assumptions.
"""
from copy import deepcopy
import hashlib
import json
import math


G = 9.80665
METHOD = "radial_impeller_meridional_v1"
ASSUMPTION_KEYS = ("inlet_flow_coefficient", "outlet_flow_coefficient",
                   "eye_hub_ratio", "axial_length_ratio")
SOURCES = [
    {"title": "CFturbo · Impeller main dimensions", "url": "https://manual.cfturbo.com/en/md_dimensions_pumpvent.html"},
    {"title": "CFturbo · Design parameters and work coefficient", "url": "https://manual.cfturbo.com/en/md_parameters_pumpvent.html"},
    {"title": "CFturbo · Meridional contour", "url": "https://manual.cfturbo.com/en/mercon.html"},
    {"title": "CFturbo · Import 2D point data", "url": "https://manual.cfturbo.com/en/import_2dpointdata.html"},
]


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
    if not _finite(actual) or not math.isclose(actual, expected, rel_tol=2e-7, abs_tol=1e-10):
        raise ValueError(f"{label}与保存的系统点不一致，不能据此生成泵几何。")


def _digest(pump):
    content = {key: pump[key] for key in ("design_point", "assumptions", "dimensions", "meridional")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _point(outputs, inputs, prefix, fluid):
    rho = _positive(outputs.get("rho_in_kgm3"), "泵入口密度")
    q = _positive(outputs.get("volume_flow_m3s"), "泵入口体积流量")
    dp = _positive(outputs.get("delta_p_Pa"), "泵压升")
    rpm = _positive(outputs.get("nr_rpm"), "泵轴转速")
    stages = _positive(outputs.get("n_stages"), "泵级数")
    if int(stages) != stages:
        raise ValueError("保存的泵级数必须为正整数。")
    psi = _positive(outputs.get("psi_head_coefficient"), "泵压头系数")
    head = dp / rho / G
    stage_head = head / stages
    u2 = math.sqrt(G * stage_head / psi)
    d2 = 60 * u2 / (math.pi * rpm)
    nq = rpm * math.sqrt(q) / stage_head ** .75
    for key, expected, name in (("head_m", head, "总扬程"), ("head_m_per_stage", stage_head, "每级扬程"),
                               ("u_tip_est_ms", u2, "叶轮出口圆周速度"), ("d_impeller_est_m", d2, "叶轮外径"),
                               ("specific_speed_Ns", nq, "比转速")):
        _match(outputs.get(key), expected, name)
    for key, expected, name in ((f"{prefix}_pump_head_coefficient_psi", psi, "输入与结果的压头系数"),
                               (f"{prefix}_pump_n_stages", stages, "输入与结果的级数")):
        if key in inputs:
            _match(inputs[key], expected, name)
    pin = _positive(outputs.get("p_in_MPa"), "泵入口压力") * 1e6
    pout = _positive(outputs.get("p_out_MPa"), "泵出口压力") * 1e6
    _match(pout - pin, dp, "进出口压差")
    point = {"fluid": fluid, "mass_flow_kg_s": q * rho, "density_in_kg_m3": rho,
             "volume_flow_m3_s": q, "pressure_in_pa": pin, "pressure_out_pa": pout,
             "pressure_rise_pa": dp, "shaft_speed_rpm": rpm, "stages": int(stages),
             "total_head_m": head, "stage_head_m": stage_head,
             "specific_speed_nq": nq, "tip_speed_m_s": u2,
             "head_definition": "pressure_rise_over_inlet_density_g_no_velocity_correction",
             "head_includes_velocity_correction": False, "head_includes_elevation_correction": False}
    for key, value in point.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            _positive(value, "派生系统点 " + key)
    for source_key, view_key in (("T_in_K", "inlet_temperature_k"), ("T_out_K", "outlet_temperature_k"),
                                 ("specific_work_isentropic_Jkg", "isentropic_specific_work_j_kg")):
        if _finite(outputs.get(source_key)) and outputs[source_key] > 0:
            point[view_key] = outputs[source_key]
    return point, psi, d2


def _shape(point, assumptions):
    """Incompressible endpoint continuity with an explicitly geometric transition."""
    q = _positive(point.get("volume_flow_m3_s"), "入口体积流量")
    u2 = _positive(point.get("tip_speed_m_s"), "出口圆周速度")
    rpm = _positive(point.get("shaft_speed_rpm"), "轴转速")
    psi = _positive(assumptions.get("head_coefficient_psi"), "压头系数")
    rho = _positive(point.get("density_in_kg_m3"), "入口密度")
    total_head = _positive(point.get("total_head_m"), "总水力参考压头")
    stages = _positive(point.get("stages"), "级数")
    if int(stages) != stages:
        raise ValueError("保存的代表级数不是整数。")
    _match(point.get("mass_flow_kg_s"), rho * q, "质量流量")
    _match(point.get("pressure_rise_pa"), total_head * rho * G, "水力参考压头")
    _match(point.get("stage_head_m"), total_head / stages, "等压头代表级")
    _match(point.get("specific_speed_nq"), rpm * math.sqrt(q) / (total_head / stages)**.75, "代表级比转速")
    if (assumptions.get("blockage_factor") != 1. or assumptions.get("leakage_flow_m3_s") != 0.
            or assumptions.get("density_model") != "constant_inlet_density"
            or assumptions.get("stage_model") != "equal_head_representative_stage"):
        raise ValueError("当前子午面方法仅支持恒入口密度、无叶片堵塞、无泄漏的等压头代表级。")
    _match(u2 * u2 * psi / G, _positive(point.get("stage_head_m"), "每级扬程"), "单级压头系数")
    alpha1 = _positive(assumptions.get("inlet_flow_coefficient"), "入口速度比 cm1/u2")
    alpha2 = _positive(assumptions.get("outlet_flow_coefficient"), "出口速度比 cm2/u2")
    hub_ratio = _positive(assumptions.get("eye_hub_ratio"), "入口轮毂比 D1h/D1s")
    length_ratio = _positive(assumptions.get("axial_length_ratio"), "轴向长度比 Lz/D2")
    if hub_ratio >= 1:
        raise ValueError("入口轮毂比必须小于 1，才能形成环形入口。")
    d2 = 60 * u2 / (math.pi * rpm)
    cm1, cm2 = alpha1 * u2, alpha2 * u2
    a1, a2 = q / cm1, q / cm2
    d1s = math.sqrt(4 * a1 / (math.pi * (1 - hub_ratio * hub_ratio)))
    d1h = hub_ratio * d1s
    b2 = a2 / (math.pi * d2)
    length = length_ratio * d2
    if d1s >= d2:
        raise ValueError("入口眼径不小于叶轮外径；当前径向离心流道假设不成立，请调整假设或转入混流/轴流部件设计。")
    if b2 >= length:
        raise ValueError("出口宽度不小于指定轴向长度；当前子午面构造无效，请调整长度或流速假设。")
    if not 0 < length - b2 < length:
        raise ValueError("轴向长度与出口宽度量级差异超出数值分辨能力，不能生成可靠子午面。")
    _match(length - (length - b2), b2, "子午面出口宽度")
    dimensions = {"impeller_diameter_m": d2, "eye_shroud_diameter_m": d1s,
                  "eye_hub_diameter_m": d1h, "outlet_width_m": b2, "axial_length_m": length,
                  "inlet_area_m2": a1, "outlet_area_m2": a2,
                  "inlet_meridional_velocity_m_s": cm1, "outlet_meridional_velocity_m_s": cm2}
    for key, value in dimensions.items():
        _positive(value, "派生几何 " + key)
    # Quarter ellipses have axial inlet and radial outlet tangents.  They are
    # transparent geometric interpolation, not an Euler/blade/flow solution.
    def contour(z_end, r_start):
        return [[z_end * math.sin(math.pi * i / 128),
                 r_start + (d2 / 2 - r_start) * (1 - math.cos(math.pi * i / 128))]
                for i in range(65)]
    meridional = {"hub": contour(length, d1h / 2), "shroud": contour(length - b2, d1s / 2),
                  "coordinate_origin": "impeller_eye_inlet", "rotation_axis": "z", "unit": "m",
                  "construction": "quarter_ellipse_axial_to_radial"}
    return dimensions, meridional


def _build_pump(result, side, record_id):
    calculation = result.get("calculation") or {}
    if not isinstance(calculation, dict):
        raise ValueError("计算记录格式无效。")
    inputs = calculation.get("inputs") or result.get("submitted_inputs") or {}
    if not isinstance(inputs, dict):
        raise ValueError("计算记录缺少有效的保存参数。")
    prefix, component = ("lox", "ox_pump") if side == "ox" else ("fuel", "fuel_pump")
    fluid = "O2" if side == "ox" else ("CH4" if inputs.get("propellant_pair") == "lox_methane" else "fuel")
    label = "液氧主泵" if side == "ox" else ("甲烷主泵" if fluid == "CH4" else "燃料主泵")
    source = {"record_id": record_id, "kind": "saved_hydraulic_point", "label": "本次计算记录的主泵系统点"}
    pump = {"available": False, "label": label, "source": source, "design_point": {}, "dimensions": {}}
    try:
        outputs = ((calculation.get("components") or {}).get(component) or {}).get("outputs") or {}
        if not isinstance(outputs, dict):
            raise ValueError("记录缺少有效的主泵输出。")
        # An old record retains only values actually present; never copy current defaults.
        if _finite(outputs.get("d_impeller_est_m")) and outputs["d_impeller_est_m"] > 0:
            pump["dimensions"]["impeller_diameter_m"] = outputs["d_impeller_est_m"]
        point, psi, d2 = _point(outputs, inputs, prefix, fluid)
        pump["design_point"] = point
        if fluid not in ("O2", "CH4"):
            raise ValueError("当前流道几何仅支持液氧与甲烷主泵；本记录保留系统点，不套用甲烷模型。")
        missing = [f"{prefix}_pump_{key}" for key in ASSUMPTION_KEYS if f"{prefix}_pump_{key}" not in inputs]
        if missing:
            raise ValueError("旧记录未保存入口/出口速度比、轮毂比或轴向长度比。请在泵参数中明确这些假设后运行新的设计点；本记录不按最新默认值补图。")
        assumptions = {key: inputs[f"{prefix}_pump_{key}"] for key in ASSUMPTION_KEYS}
        assumptions.update(head_coefficient_psi=psi, stage_model="equal_head_representative_stage",
                           density_model="constant_inlet_density", blockage_factor=1.0, leakage_flow_m3_s=0.0,
                           method_id=METHOD, coefficient_definition="psi = g H_stage / u2^2",
                           cfturbo_work_coefficient=2 * psi,
                           cfturbo_coefficient_definition="Psi = 2 g H_stage / u2^2",
                           cfturbo_coefficient_scope="same_hydraulic_reference_head_only")
        dimensions, meridional = _shape(point, assumptions)
        _match(dimensions["impeller_diameter_m"], d2, "流道外径")
        nq = point["specific_speed_nq"]
        pump.update(available=True, source=source | {"kind": "saved_point_and_explicit_assumptions"},
                    assumptions=assumptions, dimensions=dimensions, meridional=meridional,
                    checks=[
                        {"id": "hydraulic_identity", "label": "系统点与主尺寸一致性", "status": "pass",
                         "note": "Q、入口密度、压升、等压头级数、转速和压头系数均来自同一记录；未截断尺寸。"},
                        {"id": "endpoint_continuity", "label": "进出口连续性", "status": "pass",
                         "note": "Q = cm1·π(D1s²−D1h²)/4 = cm2·πD2b2；采用入口密度，未计泄漏与叶片堵塞。"},
                        {"id": "radial_type_applicability", "label": "径向离心型式适用性", "status": "not_evaluated",
                         "value": nq, "note": "nq 按 N[rpm]·√Q[m³/s]/H级[m]^0.75 计算。此图采用径向出口假设，尚未验证型式或效率；高比转速可能需混流/轴流设计。"},
                        {"id": "detailed_design", "label": "叶片、汽蚀与结构验证", "status": "not_evaluated",
                         "note": "尚无叶片角度、厚度、数量与滑移计算；未据此验证汽蚀、诱导轮、强度或寿命。"}],
                    notes=["显示单级代表叶轮的流道轮毂面与轮盖面；它们是流体边界，不是实体厚度或泵外壳。",
                           "多级采用各级等水力压头及相同入口密度的概念假设；只显示一个代表级，不推算真实总装长度或级间回流器。",
                           "入口/出口速度比、轮毂比与轴向长度比是方案假设；默认值不是最优值或制造推荐。",
                           "四分之一椭圆仅保证入口轴向、出口径向的平滑几何转向，没有求解局部流动与叶片载荷。",
                           "H=Δp/(ρ入口·g) 是恒入口密度水力参考压头，未加泵端口速度头或位差修正；它不是已校正的总能量扬程，也不替换循环真实流体焓升与轴功率。",
                           "本项目 psi=gH级/u2²；CFturbo 的 Psi=2Y/u2²。仅在采用同一参考压头 Y=gH级 时，Psi=2×psi；专业设计须核对其总能量/总压与效率定义。"],
                    references=deepcopy(SOURCES))
        pump["geometry_sha256"] = _digest(pump)
    except (TypeError, ValueError, KeyError, OverflowError, ZeroDivisionError) as exc:
        # Constructing a view must never turn a successful system solve into a
        # non-finite JSON failure, nor leave a half-built available geometry.
        pump["available"] = False
        for key in ("meridional", "assumptions", "checks", "notes", "references", "geometry_sha256"):
            pump.pop(key, None)
        pump["dimensions"] = {key: value for key, value in pump["dimensions"].items()
                              if key == "impeller_diameter_m" and _finite(value) and value > 0}
        pump["reason"] = str(exc)
    return pump


def _saved_pump(pump, record_id):
    if not isinstance(pump, dict) or not isinstance(pump.get("source"), dict):
        raise ValueError("保存的泵几何缺少来源。")
    value = deepcopy(pump)
    value["source"]["record_id"] = record_id
    if not value.get("available"):
        return value
    if value.get("assumptions", {}).get("method_id") != METHOD:
        raise ValueError("保存的泵几何方法无法识别。")
    if value.get("geometry_sha256") != _digest(value):
        raise ValueError("保存的泵几何校验值不一致；不能按当前输入补图。")
    dimensions, meridional = _shape(value["design_point"], value["assumptions"])
    for key, expected in dimensions.items():
        _match(value["dimensions"].get(key), expected, "保存的" + key)
    for wall in ("hub", "shroud"):
        coords = value["meridional"].get(wall)
        if not isinstance(coords, list) or len(coords) != len(meridional[wall]):
            raise ValueError("保存的子午面坐标不完整。")
        for actual, expected in zip(coords, meridional[wall]):
            if not isinstance(actual, list) or len(actual) != 2:
                raise ValueError("保存的子午面坐标格式无效。")
            for a, b in zip(actual, expected):
                _match(a, b, "保存的子午面坐标")
    return value


def pump_geometry_view(result, record_id=""):
    """Build a snapshot for new results, or read a saved one without draft inputs."""
    source = {"record_id": record_id, "label": "本次计算记录", "kind": "saved_record"}
    response = {"schema_version": 1, "source": source, "pumps": {}}
    saved = result.get("pump_geometry") if isinstance(result, dict) else None
    for side in ("ox", "fuel"):
        try:
            if saved is not None:
                if not isinstance(saved, dict) or saved.get("schema_version") != 1:
                    raise ValueError("保存的泵几何快照版本无效。")
                pump = _saved_pump(saved.get("pumps", {}).get(side), record_id)
            elif isinstance(result, dict):
                pump = _build_pump(result, side, record_id)
            else:
                raise ValueError("选择已完成的设计点记录后查看泵几何。")
        except (TypeError, ValueError, KeyError, OverflowError, ZeroDivisionError, AttributeError) as exc:
            pump = {"available": False, "label": "液氧主泵" if side == "ox" else "燃料主泵",
                    "source": deepcopy(source), "reason": str(exc), "design_point": {}, "dimensions": {}}
        response["pumps"][side] = pump
    return response
