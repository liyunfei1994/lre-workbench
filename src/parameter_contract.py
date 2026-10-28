"""Presentation semantics for existing inputs; no changes to cycle equations.

The browser mode selector is an adapter over the two legacy flags. It must not
be persisted as a third, potentially contradictory solver input.
"""
from copy import deepcopy


ROLE_LABELS = {
    "design_input": "设计输入", "boundary": "边界条件",
    "model_assumption": "模型假设", "constraint": "运行约束",
    "initial_guess": "数值初值", "component_sizing": "概念选型",
    "solver_setting": "数值设置", "legacy": "历史兼容参数",
}

PREBURNER_MODE_CHOICES = [
    {"value": "temperature", "label": "指定出口温度（反求 O/F）"},
    {"value": "mixture_ratio", "label": "指定 O/F（计算出口温度）"},
]


def preburner_design_mode(values):
    """Match FFSCCycleModel's >= 0.5 precedence without rewriting old inputs."""
    if float(values.get("use_target_preburner_temp", 1.0)) >= 0.5:
        return "temperature"
    if float(values.get("use_target_preburner_mr", 1.0)) >= 0.5:
        return "mixture_ratio"
    return "legacy_free_split"


def preburner_mode_updates(mode):
    if mode == "temperature":
        return {"use_target_preburner_temp": 1.0, "use_target_preburner_mr": 1.0}
    if mode == "mixture_ratio":
        return {"use_target_preburner_temp": 0.0, "use_target_preburner_mr": 1.0}
    raise ValueError("请选择指定温度或指定混合比；历史模式只保留，不自动转换。")


def _meta(role, help_text, *, source="默认值为工程假设", **extra):
    result = {"role": role, "role_label": ROLE_LABELS[role], "help": help_text}
    if source:
        result["provenance"] = {"kind": "default_assumption", "label": source,
                                "detail": "来源说明针对默认示例；当前方案取值以保存的输入为准。"}
    return result | extra


PARAMETER_CONTRACT = {
    "thrust": _meta("design_input", "在本栏给定环境压力下匹配的推力目标；FFSC 为主喷管推力，GG 为主喷管与 GG 排气喷管的总推力。命中目标不构成独立性能验证。", source="方案设计要求", role_label="设计目标"),
    "pc": _meta("design_input", "指定主燃烧室压力；33 MPa 是本项目示例假设，未核实为 Raptor 3 对应公开工况。"),
    "of_ratio": _meta("design_input", "指定发动机总供给的氧化剂 / 燃料质量流量比；预燃室、GG 及 GG 主室的局部混合比分别求解或指定。3.6 为示例输入。"),
    "expansion_ratio": _meta("design_input", "主喷管出口面积 Ae / 喉部面积 At；循环、推力室轮廓和冷却使用同一个输入。它不同于燃烧室收缩比及冷却终点面积比，也不同于 GG 排气喷管的面积比。34.34 是示例几何假设。"),
    "gg_exhaust_expansion_ratio": _meta("design_input", "GG 涡轮排气所用独立喷管的出口面积 / 喉部面积；参与 GG 排气推力计算，不定义主推力室型面或主喷管冷却覆盖。"),
    "ambient_pressure": _meta("boundary", "用于压力推力项；目标推力必须与这一环境压力使用相同口径。", source="方案边界"),
    "target_isp": _meta("initial_guess", "仅通过 F/(g₀ Isp) 构造总流量初值；最终比冲由模型计算。", source=None),
    "fuel_inlet_temp": _meta("boundary", "燃料贮箱侧温度。FFSC 与 GG 的增压泵出口及主泵入口温度均由泵焓升和管路等焓过程计算；不是在各节点沿用该温度。"),
    "ox_inlet_temp": _meta("boundary", "液氧贮箱侧温度。FFSC 与 GG 主泵入口温度均由增压泵焓升及短管压力变化计算；检查 HEOS 声明范围、相态与可用熔化线。"),
    "tank_pressure_pa": _meta("boundary", "贮箱侧压力边界；自生增压过程本身不由稳态循环模型求解。"),
    "inlet_pressure": _meta("boundary", "增压泵出口压力；扣除入口短管损失后才是主泵入口压力。"),
    "feed_line_dp_pa": _meta("model_assumption", "增压泵至主泵短管的给定压降预算；不是数值初值。"),
    "methane_thermo_dataset": _meta("model_assumption", "高温版保留 GRI30 的 53 个物种并使用其高温热化学数据；CHO 物种共同温区为 300–5000 K，仍逐物种检查实际温区。气相仍为理想气体混合物，高压状态方程适用性尚未验证；高温热化学数据不构成高温反应动力学认证。历史方案沿用原数据，不自动切换。", source="Cantera 随附 GRI30 / GRI30 high-T 数据；版本随计算记录保存", propellants=["lox_methane"]),
    "combustion_loss_model": _meta("model_assumption", "焓平衡模式按实际入口焓求化学平衡，不附加经验温升折减。预算冷却热在主室扣除，准一维模式在空间热侧扣除，不能重复计入；部件闭合不等于全系统验证。历史模式保留折减行为，不能解释为 c* 效率或燃料燃尽率。", source="模型选择；历史方案不自动切换"),
    "combustion_efficiency": _meta("legacy", "仅历史温升折减模型生效：保持平衡组成后降温，将焓差记为经验损失；它不是 c* 效率或燃料燃尽率，亦未由试验验证。", visibility={"all": [{"key": "combustion_loss_model", "op": "eq", "value": "legacy_temperature_rise"}]}),
    "chamber_temp_guess": _meta("initial_guess", "准一维冷却与循环热反馈的主室温度初值；不是指定最终燃烧温度，也不是焓参考态。", source=None),
    "chamber_gamma": _meta("initial_guess", "热反馈的比热比初值，随后更新为热化学结果；不是固定喷管比热比。", source=None),
    "molar_mass": _meta("initial_guess", "热反馈的平均摩尔质量初值，随后更新；不是化学焓基准。", source=None),
    "use_target_preburner_temp": _meta("legacy", "由网页预燃室设计方式统一设置，保留旧方案字段。", source=None, hidden=True),
    "use_target_preburner_mr": _meta("legacy", "温度模式优先；由网页设计方式统一设置，保留旧方案字段。", source=None, hidden=True),
    "cooling_heat_load": _meta("model_assumption", "仅给定 Q 与 Δp 的预算模式生效；该模式计算端口焓差，不生成沿程曲线。准一维模式的吸热量由统一几何、Bartz 热侧关联和冷却侧换热计算。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]}),
    "coolant_cp": _meta("legacy", "科学运行使用 Cantera / CoolProp 焓反算出口温度，此常数不控制温升；仅为旧模型兼容保留，仍需满足旧方案的有效性校验。", source=None, hidden=True),
    "cooling_wall_conductivity": _meta("legacy", "循环冷却使用所选内衬材料的温度相关导热函数；此手填常数被材料函数覆盖，仅保留旧方案兼容，不作为当前有效输入。", source=None, hidden=True),
    "cooling_dp_pa": _meta("model_assumption", "仅预算模式生效：给定 Δp 直接进入泵压需求与冷却出口状态；不是初值。准一维模式由动量方程计算压降，不使用此值。示例 5 MPa 未由几何或实测验证。", source="设计压降预算；非已核实实机数据", visibility={"all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]}),
    "cooling_bartz_mode": _meta("model_assumption", "准一维模式统一几何、流向和覆盖范围，沿程求压力与焓，吸热、压降、出口状态与曲线同源。Bartz 型热侧关联取规范内轮廓的上游喉部曲率半径；气侧与冷却侧相关式仍需验证。预算模式仅指定 Q 和 Δp，不生成沿程曲线。", source="模型选择"),
    "cooling_dp_auto": _meta("legacy", "历史兼容字段，不再选择另一套水力模型。准一维模式始终联立压力与焓；预算模式只使用给定 Δp。", source=None, hidden=True),
    "cooling_marching_enable": _meta("legacy", "仅为历史方案兼容保留；不再控制独立后处理或物理计算。准一维模式总是保存同源沿程结果。", source=None, hidden=True),
    "cooling_grid_points": _meta("solver_setting", "准一维冷却几何网格节点数；属于数值分辨率，不是物理设计输入。改变分辨率后应检查网格收敛，固定硬件工况沿用相同几何。", source=None, advanced=True, visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]}),
    "preburner_injector_dp_ratio": _meta("model_assumption", "设计点四条支路分别使用 Δp喷注 = 本参数 × 目的预燃室压力 pPB。固定阻力模式改用设计点冻结的阻力系数与当前流量、密度；本参数不是迭代初值。0.12 的原始文献依据尚待复核。", source="设计压降预算；非已核实实机数据"),
    "ox_turbine_exhaust_dp_ratio": _meta("model_assumption", "富氧涡轮出口至主燃烧室的热气侧压降预算：Δp = 本参数 × pc；涡轮出口压力为 pc + Δp。不是液氧直接进入主室的液路喷注压降，也不等于涡轮压比。当前预算未由热气喷注器几何或实测流量特性验证。", source="设计压降预算；非已核实实机数据"),
    "fuel_turbine_exhaust_dp_ratio": _meta("model_assumption", "富燃涡轮出口至主燃烧室的热气侧压降预算：Δp = 本参数 × pc；涡轮出口压力为 pc + Δp。不是液态燃料直接进入主室的喷注压降，也不等于涡轮压比。当前预算未由热气喷注器几何或实测流量特性验证。", source="设计压降预算；非已核实实机数据"),
    "valve_line_dp_ratio": _meta("model_assumption", "FFSC 设计点的基础管路 / 全开阀损失预算 Δp = 本参数 × pc，与供给压力匹配得到的附加调节阀压降分列。固定阻力模式使用冻结阻力系数；GG 用作主室 / 燃气发生器供给预算。不是阀门行程或已验证流量特性。", source="设计压降预算；非已核实实机数据"),
    "nonlinear_solver": _meta("solver_setting", "默认缩放 Newton；TRF 用于同一方程的对照校核。停止条件不能替代完整残差验收。", source=None),
}

for key in ("boost_pump_efficiency", "pump_efficiency_ox", "pump_efficiency_fuel"):
    PARAMETER_CONTRACT[key] = _meta("model_assumption", "给定的泵等熵效率假设，参与轴功与出口状态计算；不是初值，也未由实际泵图验证。")

for key in ("chamber_contraction_ratio", "chamber_l_star_m", "chamber_conv_half_angle_deg", "nozzle_bell_fraction"):
    PARAMETER_CONTRACT[key] = _meta("design_input", "系统级内轮廓几何假设；热侧、冷却覆盖面积与通道水力长度使用同一几何。设计点定尺寸后，固定硬件计算保留该轮廓。")
PARAMETER_CONTRACT["chamber_contraction_ratio"]["help"] = "燃烧室截面积 Acc / 主喷管喉部面积 At，定义燃烧室相对喉部的尺寸；不同于喷管出口面积比 Ae/At。主循环、型面与冷却使用同一取值。"
PARAMETER_CONTRACT["chamber_l_star_m"]["help"] = "特征长度 L*=燃烧室容积/喉部面积；不是燃烧室轴向长度或冷却通道长度。实际轮廓尺寸由同一几何模型生成。"
PARAMETER_CONTRACT["nozzle_bell_fraction"]["help"] = "主喷管轴向长度与同喉部、出口半径的 15° 半角锥形喷管长度之比；0.80 表示 80%。它不是喷管面积比，也不是冷却覆盖比例。"
PARAMETER_CONTRACT["nozzle_contour_method"] = _meta(
    "design_input", "圆弧连接二次 Bézier 抛物线；按给定长度及端点切线构造并检查几何有效性。新方案采用指定角度；历史表达式仅为旧方案复现保留，未建立 Rao 图拟合证据。",
    source="构造方法参考 NASA SP-8120；角度为本项目设计输入，非实机数据",
    type="select", choices=[
        {"value": "parabolic_bell_explicit_v1", "label": "抛物线钟形 · 指定端点角度"},
        {"value": "legacy_log_v1", "label": "历史经验角度 · 旧方案兼容"}])
for key, label in (("nozzle_initial_angle_deg", "抛物线起始角 θn"), ("nozzle_exit_angle_deg", "出口角 θe")):
    PARAMETER_CONTRACT[key] = _meta(
        "design_input", "相对于轴线的壁面切线角，直接参与型面构造。35° / 10° 是可调整的概念设计起点，非优化结果；长度与两端切线无法组成单调型面时拒绝计算。",
        source="本项目几何设计假设；未标定为任何现役发动机型面",
        label=label, visibility={"all": [{"key": "nozzle_contour_method", "op": "eq", "value": "parabolic_bell_explicit_v1"}]})
for key in ("cooling_cover_eps", "cooling_n_channels", "cooling_channel_width_m",
            "cooling_channel_height_m", "cooling_wall_thickness_m"):
    PARAMETER_CONTRACT[key] = _meta("design_input", "准一维冷却的几何输入；只在物理冷却模式生效。沿程面积、热面积和长度由同一轮廓与通道规则生成。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_cover_eps"]["help"] = "主喷管轮廓上冷却终点的局部截面积 / 喉部面积；0 表示覆盖至主喷管出口。用于确定同一型面的冷却范围，不改变主喷管出口面积比；结果记录实际覆盖位置。"
PARAMETER_CONTRACT["cooling_channel_height_m"]["visibility"]["all"].append({"key": "cooling_channel_auto_size", "op": "lte", "value": 0})
PARAMETER_CONTRACT["cooling_n_channels"]["help"] = "周向通道数量是离散设计变量；300 仅为示例初选值。应与宽度、深度、肋宽和壁厚共同进行热水力候选方案计算，不能由质量守恒唯一反求，也未证明可制造。"
PARAMETER_CONTRACT["cooling_channel_auto_size"] = _meta("component_sizing", "仅反算给定通道数量和宽度下的深度，不是整体冷却方案自动设计。默认使用给定几何；指定参考截面速度定深后仍需检查沿程热水力结果，固定硬件变工况保留该深度。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_channel_auto_size"].update(type="select", choices=[
    {"value": 0.0, "label": "给定起始几何 · 计算后筛选候选"},
    {"value": 1.0, "label": "指定截面速度 · 仅反算通道深度"}])
PARAMETER_CONTRACT["cooling_sizing_reference"] = _meta("component_sizing", "实际入口方式以冷却通道入口（集流腔之后）的流量、密度和流通面积满足目标速度。历史方式仅复现入口密度与喉部宽度组合，不能解释为任一实际截面速度。", type="select", choices=[
    {"value": "coolant_inlet_velocity", "label": "实际通道入口速度定深"},
    {"value": "legacy_inlet_density_throat_width", "label": "历史入口密度与喉宽组合 · 旧记录"}],
    visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}, {"key": "cooling_channel_auto_size", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_target_velocity_ms"] = _meta("component_sizing", "仅指定截面速度定深时生效。实际入口模式指集流腔之后的通道入口速度；历史模式仅是入口密度与喉宽组合的尺寸参数。100 m/s 为示例存值，非推荐流速，也不控制沿程速度。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}, {"key": "cooling_channel_auto_size", "op": "gt", "value": 0}]})
for key in ("cooling_channel_auto_size", "cooling_sizing_reference", "cooling_target_velocity_ms", "cooling_n_channels",
            "cooling_channel_width_m", "cooling_channel_height_m", "cooling_wall_thickness_m"):
    # A saved distribution table controls all four dimensions and disables
    # automatic sizing. Keep the inactive scalar inputs in the saved case.
    PARAMETER_CONTRACT[key]["visibility"]["all"].append(
        {"key": "cooling_channel_table_json", "op": "empty"})
for key in ("cooling_htc_enhancement", "cooling_channel_rel_roughness", "cooling_channel_k_minor"):
    PARAMETER_CONTRACT[key] = _meta("model_assumption", "准一维冷却的换热/阻力模型假设；影响吸热和压降，不是初值，也未标定为 Raptor 实机参数。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_htc_enhancement"]["help"] = "仅修正喉部轴向前后各一个喉半径范围内的冷却侧换热系数；1 表示不作额外修正。它不是 Bartz 气侧曲率项，不是初值，也未由实机数据标定。气侧曲率半径另从规范内轮廓的上游喉部圆弧取得。"
PARAMETER_CONTRACT["cooling_wall_temp_limit_status"] = _meta("constraint", "未设定时该项尚未评估；暂定限值内仍需核实依据；已有依据仅表示按登记的方案条件比较，不代表材料、结构或寿命认证。", source=None, type="select", choices=[
    {"value": "unset", "label": "未设定 / 尚未评估"},
    {"value": "provisional", "label": "示例暂定 / 待补依据"},
    {"value": "documented", "label": "已有依据 / 用户登记"}],
    visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
for key, help_text in (
        ("cooling_wall_temp_limit_K", "启用后的方案热面壁温验收上限；不会截断计算壁温。800 K 是未验证的历史示例，不是铜合金通用允许温度，亦不同于导热率数据温区上限。"),
        ("cooling_wall_temp_limit_basis", "记录限值来源及适用的材料牌号、制造状态、工况、任务持续时间或复用要求。已有依据必须填写；资料登记不等于平台完成结构或寿命验证。")):
    PARAMETER_CONTRACT[key] = _meta("constraint", help_text, source=None,
        **({"multiline": True} if key.endswith("basis") else {}),
        visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}], "any": [
            {"key": "cooling_wall_temp_limit_status", "op": "eq", "value": "provisional"},
            {"key": "cooling_wall_temp_limit_status", "op": "eq", "value": "documented"}]})
for key in ("turbine_efficiency_ox", "turbine_efficiency_fuel"):
    PARAMETER_CONTRACT[key] = _meta("model_assumption", "给定的涡轮效率假设，参与焓降和轴功计算；不是初值，需部件数据支持。")

for key in ("chamber_temp_guess", "chamber_gamma", "molar_mass"):
    PARAMETER_CONTRACT[key]["visibility"] = {"all": [
        {"key": "cooling_bartz_mode", "op": "gt", "value": 0}]}

for side, label in (("ox", "富氧"), ("fuel", "富燃")):
    PARAMETER_CONTRACT[f"{side}_preburner_temp_target"] = _meta(
        "design_input", "概念设计中选定预燃室出口的整体燃气温度，反求局部 O/F 并匹配循环；它不是局部热点、金属温度或材料允许上限。当前模型将此状态直接用作涡轮入口。",
        source="本项目概念设计目标；非 Raptor 3 实测值",
        visibility={"preburner_modes": ["temperature"]})
    PARAMETER_CONTRACT[f"{side}_preburner_temp_limit"] = _meta(
        "constraint", "仅在约束状态为示例暂定或已有依据时生效。800 / 900 K 是旧示例取值，不是通用工程上限；燃气温度与金属温度不同。",
        source="旧示例限值未登记材料、寿命或氧相容性依据",
        visibility={"any": [
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "provisional"},
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "documented"}]})
    PARAMETER_CONTRACT[f"{side}_preburner_temp_limit_status"] = _meta(
        "constraint", "未设定：该项尚未评估；示例暂定：检查数值但合格仍待依据；已有依据：按登记的方案条件检查，不自动认证材料或寿命。",
        source=None, type="select", choices=[
            {"value": "unset", "label": "未设定 / 尚未评估"},
            {"value": "provisional", "label": "示例暂定 / 待补依据"},
            {"value": "documented", "label": "已有依据 / 用户登记"}])
    PARAMETER_CONTRACT[f"{side}_preburner_temp_limit_basis"] = _meta(
        "constraint", "记录依据文件及适用条件，例如燃气组成、压力、涡轮材料与冷却方式、任务寿命和温度裕度。填入资料不等于平台完成了资格验证。",
        source=None, multiline=True,
        visibility={"any": [
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "provisional"},
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "documented"}]})
    PARAMETER_CONTRACT[f"{side}_preburner_mr_target"] = _meta(
        "design_input", "指定温度时仅为反求混合比的初值；指定混合比时为设计输入。初值不应作为性能标定手段。",
        roles_by_mode={"temperature": "initial_guess", "mixture_ratio": "design_input"},
        help_by_mode={"temperature": "用于反求混合比的迭代初值，最终 O/F 由出口温度目标确定。", "mixture_ratio": "作为设计输入固定，计算出口温度并检查运行上限；目标温度在此模式不生效。"},
        labels_by_mode={"temperature": f"{label}预燃室 O/F 初值", "mixture_ratio": f"{label}预燃室指定 O/F"},
        visibility={"preburner_modes": ["temperature", "mixture_ratio"]})
    PARAMETER_CONTRACT[f"{side}_preburner_pressure_ratio"] = _meta(
        "initial_guess", "pPB/pc 不等于涡轮进出口压比。目标模式中只构造压比初猜，可能被初值下限覆盖；实际涡轮压比由功率匹配求得。历史自由分流模式将其作为压力输入，但仍可能被该模式的最小涡轮压比下界抬升。",
        source=None, roles_by_mode={"legacy_free_split": "design_input"},
        labels_by_mode={"legacy_free_split": f"{label}预燃室压力 / 主室压力（历史输入）"})

for key in ("fuel_split_to_ox_preburner", "ox_split_to_ox_preburner"):
    PARAMETER_CONTRACT[key] = _meta("initial_guess", "仅历史自由分流模式生效；指定温度或混合比时，分流由相应方程确定。", source=None,
                                   visibility={"preburner_modes": ["legacy_free_split"]})

for side in ("lox", "fuel"):
    PARAMETER_CONTRACT[f"{side}_pump_speed_rpm_override"] = _meta(
        "component_sizing", "大于 0 时锁定转速；设为 0 才按比转速估算。当前不是通过泵 / 涡轮特性图匹配得到的转速。")
    PARAMETER_CONTRACT[f"{side}_pump_specific_speed_target"] = _meta(
        "component_sizing", "采用 nq = N√Q/H级^0.75 的单位制定义：N 为 rpm、Q 为 m³/s、H级为 m；仅未锁定转速时用于反推。",
        visibility={"all": [{"key": f"{side}_pump_speed_rpm_override", "op": "lte", "value": 0}]})
    PARAMETER_CONTRACT[f"{side}_pump_n_stages"] = _meta(
        "component_sizing", "用于单级扬程、比转速与概念尺寸估算；当前总泵功仍由给定效率和总焓升计算。")
    PARAMETER_CONTRACT[f"{side}_pump_head_coefficient_psi"] = _meta(
        "component_sizing", "ψ = gH级/u2²；用于叶尖速度和外径初选，部件结果与几何共用此值。CFturbo 的 Ψ = 2ψ。H级按入口密度和压差定义，不改变真实流体泵功。", source="示例初选值；待详细水力设计确认")
    for suffix, help_text in {
        "inlet_flow_coefficient": "cm1/u2：入口子午速度除以叶轮出口圆周速度。以入口密度、流量和轮毂比求泵眼环形面积；不等于 cm1/u1，也不验证汽蚀。",
        "outlet_flow_coefficient": "cm2/u2：出口子午速度除以叶轮出口圆周速度。按恒入口密度、无叶片堵塞和无泄漏假设求出口宽度 b2。",
        "eye_hub_ratio": "D1h/D1s：泵眼轮毂直径与轮盖直径之比。只定义流道入口环形面积，不是轴强度计算所得轴径。",
        "axial_length_ratio": "Lz/D2：代表级子午流道轴向长度与叶轮外径之比。用于初始轮廓构造，不是多级泵总长或优化结论。",
    }.items():
        PARAMETER_CONTRACT[f"{side}_pump_{suffix}"] = _meta(
            "component_sizing", help_text, source="可编辑的示例几何假设；不代表已验证的推荐值")


def parameter_metadata(key):
    return deepcopy(PARAMETER_CONTRACT.get(key, {}))


def preburner_mode_field():
    return {"key": "preburner_design_mode", "label": "预燃室设计方式", "type": "select",
            "default": "temperature", "virtual": True, "cycles": ["ffsc"],
            "unit": "", "factor": 1, "choices": deepcopy(PREBURNER_MODE_CHOICES),
            **_meta("design_input", "新方案可选温度目标反求 O/F；已知流量配比时可指定 O/F 计算温度。两种方式都需循环匹配，不能同时独立指定温度和 O/F；这不等于固定硬件的运行控制。", source="设计任务选择")}

# In parallel dual-shaft GG the oxygen-side turbine also receives fuel-rich gas.
# Single-shaft GG uses the oxygen-side speed and the fuel-side turbine efficiency.
for _key in ("turbine_efficiency_ox", "fuel_pump_speed_rpm_override", "fuel_pump_specific_speed_target"):
    PARAMETER_CONTRACT[_key].setdefault("visibility", {})["any"] = [
        {"key": "cycle_type", "op": "eq", "value": "ffsc"},
        {"key": "gg_layout", "op": "eq", "value": "dual_shaft"}]
