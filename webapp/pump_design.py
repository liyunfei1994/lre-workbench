"""Read-only CFturbo handoff of a saved pump point, not a blade design solver.

No fluid-property lookup, current defaults or inferred blade counts are used here.
The geometry snapshot remains unchanged; additional recorded outputs are checked
against it before they are presented together as one modelling reference.
"""
from copy import deepcopy
import math


REFERENCES = [
    {"title": "CFturbo · Global setup / design point", "url": "https://manual.cfturbo.com/en/global_setup.html"},
    {"title": "CFturbo · Fluid properties", "url": "https://manual.cfturbo.com/en/preffluids.html"},
    {"title": "CFturbo · Stage designer", "url": "https://manual.cfturbo.com/en/stage-designer.html"},
    {"title": "CFturbo · Main dimensions / design parameters", "url": "https://manual.cfturbo.com/en/md_parameters_pumpvent.html"},
    {"title": "CFturbo · Meridional contour", "url": "https://manual.cfturbo.com/en/mercon.html"},
    {"title": "CFturbo · Blade properties", "url": "https://manual.cfturbo.com/en/bl_setup.html"},
    {"title": "CFturbo · Inducer", "url": "https://manual.cfturbo.com/en/inducer.html"},
    {"title": "CFturbo · Import 2D point data", "url": "https://manual.cfturbo.com/en/import_2dpointdata.html"},
    {"title": "CFturbo · Multi-stage energy distribution", "url": "https://manual.cfturbo.com/en/md_multistage.html"},
]

WORKFLOW = [
    {"title": "1 · 整泵设计点", "note": "核对工质、入口状态、体积流量 Q、转速 n，以及采用一致总能量定义的整泵扬程或总压差。当前 H 为入口密度参考值。", "url": REFERENCES[0]["url"]},
    {"title": "2 · 级数与部件配置", "note": "分配各级能量增量并使其总和等于整泵目标；明确诱导轮、级间静子/回流道及末级集流器。当前仅有等参考扬程代表级。", "url": REFERENCES[2]["url"]},
    {"title": "3 · 主尺寸和子午面", "note": "结合功系数、流量、效率和经验关联初估主尺寸，再调整轮毂/轮盖子午轮廓；平台椭圆流道仅是可追溯的几何起点。", "url": REFERENCES[4]["url"]},
    {"title": "4 · 叶型与静止部件", "note": "设置叶片数、厚度、进口攻角、出口滑移模型和角度分布，设计叶片曲面及扩压器/蜗壳；这些量不能由 Q、H、n 唯一确定。", "url": REFERENCES[5]["url"]},
    {"title": "5 · 几何交接与验证", "note": "轮毂和轮盖分别按 z,r 二维点导入，复核单位；完成详细叶型后再开展汽蚀、CFD、结构与制造校核。", "url": REFERENCES[7]["url"]},
]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _close(a, b):
    return _finite(a) and _finite(b) and math.isclose(a, b, rel_tol=2e-7, abs_tol=1e-10)


def _field(key, label, value=None, unit="—", state="missing", source="未记录", target="", note=""):
    if value is not None and not isinstance(value, str) and not _finite(value):
        value, state = None, "conflict"
    return dict(id=key, label=label, value=value, unit=unit, state=state,
                source=source, target=target, note=note)


def _handoff(result, pump, side):
    calculation = _mapping(_mapping(result).get("calculation"))
    component = "ox_pump" if side == "ox" else "fuel_pump"
    outputs = _mapping(_mapping(_mapping(calculation.get("components")).get(component)).get("outputs"))
    point = _mapping(pump.get("design_point"))
    dimensions = _mapping(pump.get("dimensions"))
    assumptions = _mapping(pump.get("assumptions"))
    problems = []
    recorded_source = "本记录 calculation.components." + component + ".outputs."
    snapshot_source = "本记录已校验的泵系统点"
    fields = {}

    # A frozen geometry is intentionally not rebuilt from modified outputs.
    # Detect mixed revisions in this supplementary view instead.
    comparisons = (
        ("volume_flow_m3s", "volume_flow_m3_s", 1),
        ("rho_in_kgm3", "density_in_kg_m3", 1),
        ("delta_p_Pa", "pressure_rise_pa", 1),
        ("nr_rpm", "shaft_speed_rpm", 1), ("n_stages", "stages", 1),
        ("p_in_MPa", "pressure_in_pa", 1e6), ("p_out_MPa", "pressure_out_pa", 1e6),
        ("head_m", "total_head_m", 1), ("head_m_per_stage", "stage_head_m", 1),
        ("specific_speed_Ns", "specific_speed_nq", 1), ("u_tip_est_ms", "tip_speed_m_s", 1),
        ("T_in_K", "inlet_temperature_k", 1), ("T_out_K", "outlet_temperature_k", 1),
        ("specific_work_isentropic_Jkg", "isentropic_specific_work_j_kg", 1),
    )
    inconsistent = set()
    for output_key, point_key, factor in comparisons:
        if output_key in outputs and point_key in point:
            value = outputs[output_key]
            if not _finite(value) or not _close(value * factor, point[point_key]):
                inconsistent.add(point_key)
                problems.append("保存系统点与组件输出不一致：" + output_key)
    if "d_impeller_est_m" in outputs and "impeller_diameter_m" in dimensions:
        if not _close(outputs["d_impeller_est_m"], dimensions["impeller_diameter_m"]):
            problems.append("保存外径与组件输出不一致")
    reason = str(pump.get("reason", ""))
    if not point and any(word in reason for word in ("不一致", "校验", "无法识别")):
        problems.append(reason)

    def point_field(key, label, unit, state="recorded", target="", note="", factor=1):
        value = point.get(key)
        if key in inconsistent:
            field = _field(key, label, None, unit, "conflict", snapshot_source, target, "组件输出与冻结系统点冲突，待核对。")
        elif _finite(value) and value > 0:
            field = _field(key, label, value * factor, unit, state, snapshot_source, target, note)
        else:
            field = _field(key, label, unit=unit, target=target, note=note or "本记录未提供已校验的值。")
        fields[key] = field
        return field

    def output_field(key, label, unit, target="", note="", positive=True, maximum=None):
        value = outputs.get(key)
        source = recorded_source + key
        if value is None:
            field = _field(key, label, unit=unit, target=target, note=note)
        elif not _finite(value) or (positive and value <= 0) or (maximum is not None and value > maximum):
            problems.append("组件输出数值无效：" + key)
            field = _field(key, label, None, unit, "conflict", source, target, "保存值无效；不以默认值替换。")
        else:
            field = _field(key, label, value, unit, "recorded", source, target, note)
        fields[key] = field
        return field

    def derived(key, label, unit, dependencies, calculate, note, target="", state="derived"):
        values = [fields[k]["value"] for k in dependencies]
        if any(fields[k]["state"] == "conflict" for k in dependencies):
            field = _field(key, label, None, unit, "conflict", "依赖保存值", target, "依赖数据冲突，停止派生。")
        elif any(value is None for value in values):
            field = _field(key, label, unit=unit, target=target, note="缺少派生所需的保存值。" + note)
        else:
            try:
                value = calculate(*values)
            except (ValueError, OverflowError, ZeroDivisionError):
                value = float("nan")
            field = _field(key, label, value, unit, state, "由本记录 " + "、".join(dependencies) + " 换算", target, note)
            if field["state"] == "conflict":
                problems.append("派生值无效：" + key)
        fields[key] = field
        return field

    design = [
        _field("fluid", "工质", point.get("fluid") if point.get("fluid") in ("O2", "CH4") else None,
               state="recorded" if point.get("fluid") in ("O2", "CH4") else "missing", source=snapshot_source,
               target="Global setup → Fluid", note="液氧/甲烷须采用相应入口温压下物性；不能使用常温水的属性。"),
        point_field("mass_flow_kg_s", "质量流量 ṁ", "kg/s", "derived", "Mass flow", "ṁ = ρ入口 Q入口。"),
        point_field("volume_flow_m3_s", "入口体积流量 Q", "m³/s", target="Volume flow", note="按本记录入口密度定义。"),
        derived("volume_flow_m3_h", "CFturbo 体积流量 Q", "m³/h", ["volume_flow_m3_s"], lambda q: q * 3600, "Q[m³/h] = 3600 Q[m³/s]。", "Global setup → Volume flow"),
        point_field("shaft_speed_rpm", "泵轴转速 n", "rpm", target="Global setup → Rotational speed", note="同轴泵与涡轮转速需保持一致。"),
        point_field("stages", "保存的级数 Z", "级", "assumed", "Stage designer", "记录的方案级数，不是自动优化结果。"),
        point_field("pressure_in_pa", "入口节点绝对压力", "Pa", target="Inlet pressure / 口径核对", note="系统节点量；端口速度及总压尚未解析。"),
        point_field("pressure_out_pa", "出口节点绝对压力", "Pa", target="Outlet pressure / 口径核对", note="不自动视为 CFturbo 出口总压。"),
        point_field("pressure_rise_pa", "节点压升 Δp", "Pa", target="压力目标参考", note="Δp = p出口 − p入口，未作端口速度修正。"),
        point_field("total_head_m", "整泵水力参考扬程 H", "m", "derived", "Global setup → Head / 需先核对定义", "H = Δp/(ρ入口 g)，无端口速度头、位差或沿程变密度修正。"),
        point_field("stage_head_m", "等扬程代表级 H级", "m", "assumed", "Stage designer → 各级能量分配参考", "H级 = H/Z；不能把整泵扬程重复填给每一级。"),
        point_field("specific_speed_nq", "代表级比转速 nq", "rpm·√(m³/s)/m^0.75", "derived", "Specific speed nq", "nq = n √Q / H级^0.75；使用每级参考扬程。"),
    ]
    fluid = [
        point_field("density_in_kg_m3", "入口密度 ρ", "kg/m³", target="Fluid → Density"),
        output_field("T_in_K", "入口节点温度", "K", "Fluid → Temperature", "保存的系统节点温度，不能补称总温。"),
        output_field("T_out_K", "出口节点温度", "K", "出口/后续级物性参考", "多级仅保存整泵出口值，不代表各级出口状态。"),
        output_field("rho_out_kgm3", "出口密度", "kg/m³", "出口物性参考"),
        _field("dynamic_viscosity_pa_s", "动力黏度 μ", unit="Pa·s", target="Fluid properties", note="本记录未保存；在相同工质、温压及物性模型下确认。"),
        _field("kinematic_viscosity_m2_s", "运动黏度 ν", unit="m²/s", target="Fluid → Kinematic viscosity", note="ν = μ/ρ；μ 未记录时不补算。"),
        _field("vapor_pressure_pa", "入口温度下饱和蒸汽压 pv", unit="Pa", target="Fluid → Vapor pressure", note="用于汽蚀核对；本记录未保存独立 pv，不由 NPSHa 反推。"),
        _field("heat_capacity_j_kg_k", "定压比热 cp", unit="J/(kg·K)", target="Fluid → Specific heat", note="本记录未保存；按选用的 CFturbo 流体模型确认。"),
        _field("thermal_conductivity_w_m_k", "导热系数 λ", unit="W/(m·K)", target="Fluid → Thermal conductivity", note="本记录未保存；按选用的流体模型确认。"),
    ]
    power = [
        output_field("power_W", "系统泵轴功率 P", "W", "轴系功率参考", "沿用循环模型的真实流体功率；当前求解器 P = ṁ Δh_s / η系统。"),
        output_field("eta", "系统泵效率 η系统", "—", "效率定义待核对", "用于等熵焓升与实际泵功换算，不直接填作液力效率 ηh 或容积效率 ηv。", maximum=1),
        output_field("specific_work_isentropic_Jkg", "保存的等熵比功 Δh_s", "J/kg", "真实流体功率核对"),
        derived("shaft_torque_n_m", "泵轴扭矩 M", "N·m", ["power_W", "shaft_speed_rpm"], lambda p, n: p / (2 * math.pi * n / 60), "M = P/ω，ω = 2πn/60；不含额外轴系附件负载。"),
        derived("hydraulic_reference_power_w", "不可压参考液力功率 QΔp", "W", ["volume_flow_m3_s", "pressure_rise_pa"], lambda q, dp: q * dp, "仅作恒入口密度参考；不含效率，也不替换循环泵轴功率。"),
        _field("hydraulic_efficiency", "液力效率 ηh", target="Impeller → Hydraulic efficiency", note="需由泵水力设计/标定给出，不能用 η系统直接替代。"),
        _field("volumetric_efficiency", "容积效率 ηv", target="Impeller → Volumetric efficiency", note="实际泄漏尚未求解；当前流道面积以无泄漏作几何假设。"),
        _field("mechanical_efficiency", "机械效率 ηm", target="Mechanical losses", note="轴承、密封和盘摩擦等损失需按部件模型核对。"),
    ]
    cavitation = [
        output_field("npsh_available_m", "保存的可用 NPSH 参考值", "m", "吸入边界参考", "当前模型 NPSHa = (p入口 − pv)/(ρ入口 g)，未加入端口速度头；NPSHa 不等于 NPSHr。", positive=False),
        output_field("suction_specific_speed_nss_eu", "由可用 NPSH 得到的吸入比转速（EU）", "rpm·√(m³/s)/m^0.75", "吸入条件诊断", "n √Q / NPSHa^0.75；不能当作以所需 NPSHr 定义的设计能力或合格判据。"),
        output_field("suction_specific_speed_Nss", "由可用 NPSH 得到的吸入比转速（US）", "rpm·√gpm/ft^0.75", "吸入条件诊断", "采用 Q[gpm]、NPSHa[ft]，不能与 EU 数值直接比较。"),
        _field("npsh_required_m", "所需 NPSH / NPSHr", unit="m", target="叶轮/诱导轮吸入设计", note="需由具体几何、CFD/试验或经验证模型确定，并声明下降判据。"),
        _field("npsh_margin_m", "汽蚀余量 NPSHa − NPSHr", unit="m", target="吸入性能验收", note="NPSHr 和端口定义未确定，不能据当前 NPSHa 判定合格。"),
    ]
    if fields["npsh_available_m"]["value"] is not None and fields["npsh_available_m"]["value"] <= 0:
        fields["npsh_available_m"]["note"] += " 当前保存值非正，需优先复核吸入边界。"
    # Additional scalar outputs cannot silently contradict the saved point.
    p, eta, dh, mdot = (fields[k]["value"] for k in ("power_W", "eta", "specific_work_isentropic_Jkg", "mass_flow_kg_s"))
    if all(_finite(v) and v > 0 for v in (p, eta, dh, mdot)) and not _close(p, mdot * dh / eta):
        problems.append("轴功率与保存的质量流量、等熵比功及效率不一致")
    nq_a, npsha, q, n = (fields[k]["value"] for k in ("suction_specific_speed_nss_eu", "npsh_available_m", "volume_flow_m3_s", "shaft_speed_rpm"))
    if all(_finite(v) and v > 0 for v in (nq_a, npsha, q, n)) and not _close(nq_a, n * math.sqrt(q) / npsha ** .75):
        problems.append("吸入比转速与保存的 NPSHa、流量及转速不一致")
    nss_us = fields["suction_specific_speed_Nss"]["value"]
    if all(_finite(v) and v > 0 for v in (nss_us, npsha, q, n)) and not _close(nss_us, n * math.sqrt(q * 15850.3) / (npsha * 3.280839895) ** .75):
        problems.append("美制吸入比转速与保存的 NPSHa、流量及转速不一致")

    geometry = []
    for key, label, unit in (
        ("impeller_diameter_m", "叶轮外径 D2", "m"), ("eye_shroud_diameter_m", "入口轮盖直径 D1s", "m"),
        ("eye_hub_diameter_m", "入口轮毂直径 D1h", "m"), ("outlet_width_m", "出口宽度 b2", "m"),
        ("axial_length_m", "代表级轴向长度 Lz", "m"),
        ("inlet_meridional_velocity_m_s", "入口子午速度 cm1", "m/s"),
        ("outlet_meridional_velocity_m_s", "出口子午速度 cm2", "m/s"),
    ):
        value = dimensions.get(key)
        valid = _finite(value) and value > 0 and bool(point)
        geometry.append(_field(key, label, value if valid else None, unit, "derived" if valid else "missing",
                               "保存的代表级主尺寸", "Main dimensions / 仅作起点", "依赖记录中的经验系数及等扬程假设；未完成详细水力优化。"))
    geometry.append(point_field("tip_speed_m_s", "出口圆周速度 u2", "m/s", "derived", note="u2 = π D2 n/60；尚需强度及汽蚀设计。"))
    for key, label, note in (
        ("head_coefficient_psi", "平台压头系数 ψ", "ψ = gH级/u2²；记录的设计假设。"),
        ("cfturbo_work_coefficient", "CFturbo 功系数 Ψ（同参考压头）", "仅当 Y = gH级 使用同一参考压头时，Ψ = 2ψ；若使用效率修正比功须重新核对。"),
        ("inlet_flow_coefficient", "入口速度比 cm1/u2", "分母为出口圆周速度 u2，不是入口叶尖速度。"),
        ("outlet_flow_coefficient", "出口速度比 cm2/u2", "当前几何采用恒入口密度、无堵塞、无泄漏。"),
        ("eye_hub_ratio", "入口轮毂比 D1h/D1s", "需结合轴毂连接及吸入流道进一步设计。"),
        ("axial_length_ratio", "代表级长度比 Lz/D2", "几何构造假设，不代表整泵总装长度。"),
    ):
        value = assumptions.get(key)
        geometry.append(_field(key, label, value if _finite(value) else None, state="assumed" if _finite(value) else "missing",
                               source="保存的流道假设", target="Main dimensions / Design parameters", note=note))
    boundaries = [
        _field("corrected_total_head_m", "修正后的整泵总能量扬程", unit="m", target="Global setup → Head", note="需明确入口/出口截面、速度头、位差及密度处理；不可把参考扬程标为已修正值。"),
        _field("inlet_total_pressure_pa", "入口端口总压", unit="Pa", target="Inlet total pressure", note="系统节点未解析端口速度与总/静压换算。"),
        _field("outlet_total_pressure_pa", "出口端口总压", unit="Pa", target="Outlet total pressure", note="不能直接把节点出口压力标为总压。"),
        _field("rotation_direction", "旋转方向", target="Global setup / Rotation", note="需声明观察方向及正旋转约定。"),
        _field("inlet_swirl", "入口预旋 / 速度分布", target="Inlet flow / Velocity triangle", note="未求解，不能默认已满足无预旋条件。"),
    ]

    def missing(key, label, unit="—", note="详细部件设计尚未求解。", target=""):
        return _field(key, label, unit=unit, target=target, note=note)

    stages = point.get("stages")
    multistage = _finite(stages) and stages > 1
    components = [
        {"id": "inducer", "label": "诱导轮", "state": "missing",
         "note": "是否配置待吸入性能评估：比较同一基准下 NPSHa、主叶轮 NPSHr 和余量要求。不能由当前流量、扬程或经验阈值自动定案。",
         "fields": [missing("inducer_required", "是否需要诱导轮", note="尚未取得主叶轮 NPSHr，未作是/否判断。"),
                    missing("blade_count", "诱导轮叶片数", "片"), missing("head_m", "诱导轮目标扬程", "m", "需与主叶轮吸入要求及整泵能量分配联合确定。"),
                    missing("outer_diameter_m", "诱导轮外径", "m"), missing("hub_ratio", "诱导轮轮毂比"),
                    missing("inlet_angle_deg", "诱导轮进口叶片角", "°"), missing("outlet_angle_deg", "诱导轮出口叶片角", "°"),
                    missing("tip_clearance_m", "诱导轮叶尖间隙", "m")]},
        {"id": "impeller", "label": "主叶轮", "state": "assumed" if pump.get("available") else "missing",
         "note": "已显示代表级轮毂/轮盖流道；叶片尚未设计。叶片数、角度和厚度需结合速度三角形、滑移、堵塞、载荷和加工约束迭代。",
         "fields": [missing("blade_count", "主叶轮主叶片数", "片"), missing("splitter_count", "分流叶片数", "片"),
                    missing("inlet_angle_deg", "进口叶片角 β1（沿叶高）", "°"), missing("outlet_angle_deg", "出口叶片角 β2（沿叶高）", "°"),
                    missing("wrap_angle_deg", "叶片包角", "°"), missing("thickness_m", "叶片厚度分布", "m"),
                    missing("slip_model", "出口滑移模型", target="Blade properties → Slip factor"), missing("incidence_deg", "进口攻角", "°")]},
        {"id": "diffuser", "label": "扩压器 / 静子", "state": "missing",
         "note": "先选择有叶或无叶扩压；若选择无叶型，应明确为无叶，不能把未设计的叶片数写成 0。",
         "fields": [missing("type", "有叶 / 无叶型式"), missing("blade_count", "扩压器叶片数", "片"),
                    missing("inlet_diameter_m", "扩压器入口直径", "m"), missing("outlet_diameter_m", "扩压器出口直径", "m"),
                    missing("inlet_angle_deg", "扩压器进口叶片角", "°"), missing("width_m", "扩压器宽度", "m")]},
        {"id": "return_channel", "label": "级间回流道", "state": "missing",
         "note": "本记录为多级方案，需设计级间回流和下一叶轮入口状态；当前只有等扬程代表级。" if multistage else "本记录为单级方案时通常无需级间回流道；仍需在部件配置中明确其不适用状态。",
         "fields": [missing("configuration", "级间连接形式", note="需逐级定义回流道与入口预旋。" if multistage else "单级参考方案，不适用级间回流；未生成实体。"),
                    missing("blade_count", "回流器叶片数", "片")]},
        {"id": "volute", "label": "蜗壳 / 集流器", "state": "missing",
         "note": "末级需确定集流与出口组织方案；并非所有方案都必须采用同一种蜗壳，当前未给出其几何。",
         "fields": [missing("type", "蜗壳 / 环形集流器形式"), missing("area_distribution_m2", "周向截面积分布", "m²"),
                    missing("tongue_clearance_m", "隔舌间隙", "m"), missing("outlet_diameter_m", "出口截面尺寸", "m")]},
    ]
    stage_allocation = []
    if _finite(stages) and stages == int(stages) and 0 < stages <= 100 and _finite(point.get("stage_head_m")):
        for i in range(int(stages)):
            stage_allocation.append({"stage": i + 1, "reference_head_m": point["stage_head_m"],
                                     "inlet_state_note": "当前主泵等参考扬程，未分配诱导轮扬程及级间损失。首级入口沿用整泵记录；若加入诱导轮，应在相同整泵能量目标内重新分配。" if i == 0 else "当前主泵等参考扬程，未分配诱导轮扬程及级间损失。未保存本级入口温度、压力和密度；不能复制首级状态作为最终设计。"})
    elif _finite(stages) and stages > 100:
        problems.append("级数超出交接表支持范围，未生成逐级分配")

    groups = [{"id": key, "title": title, "fields": values} for key, title, values in (
        ("design_point", "整泵设计点与级分配", design), ("fluid", "工质与物性", fluid),
        ("power", "轴功率、效率与扭矩", power), ("cavitation", "吸入条件与汽蚀", cavitation),
        ("geometry", "代表级主尺寸与假设", geometry), ("boundaries", "CFturbo 边界定义待确认", boundaries))]
    all_fields = [f for group in groups for f in group["fields"]] + [f for c in components for f in c["fields"]]
    conflicts = problems or any(f["state"] == "conflict" for f in all_fields)
    status = "conflict" if conflicts else "partial" if point and point.get("fluid") in ("O2", "CH4") else "unavailable"
    missing_items = ["CFturbo 整泵总能量/总压边界定义", "入口黏度、饱和蒸汽压及所选模型所需物性", "NPSHr、汽蚀余量与诱导轮需求", "液力/容积/机械效率拆分", "叶片数、叶片角、厚度、滑移及部件配置"]
    if multistage:
        missing_items.append("各级入口状态、真实能量分配及级间回流道")
    if not pump.get("available"):
        missing_items.append(reason or "可用的代表级几何")
    if status == "conflict":
        # Keep traceable values visible for diagnosis, but never publish stage
        # allocations as an apparently valid modelling prescription.
        stage_allocation = []
    return {"status": status,
            "summary": "记录存在冲突，暂停建模交接：" + "；".join(problems or ["存在无效数值"])
            if status == "conflict" else "本记录可作为 CFturbo 设计起点；主尺寸为代表级初估，叶型及完整泵装配待设计。"
            if status == "partial" else "本记录缺少有效的泵系统点，无法生成建模设计点。",
            "groups": groups, "components": components, "stage_allocation": stage_allocation,
            "missing": missing_items}


def pump_design_handoff(result, pumps):
    """Build derived presentation data without altering the result or snapshots."""
    return {"schema_version": 1,
            "pumps": {side: _handoff(result, _mapping(_mapping(pumps).get(side)), side) for side in ("ox", "fuel")},
            "references": deepcopy(REFERENCES), "workflow": deepcopy(WORKFLOW)}
