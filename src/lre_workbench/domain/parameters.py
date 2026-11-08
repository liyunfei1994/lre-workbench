"""Shared task and presentation semantics; no changes to cycle equations.

The browser mode selector is an adapter over the two legacy flags. It must not
be persisted as a third, potentially contradictory solver input.
"""
from copy import deepcopy
from lre_workbench.domain.analysis_roles import parameter_analysis_roles


ROLE_LABELS = {
    "design_input": "设计输入", "boundary": "边界条件",
    "model_assumption": "模型假设", "constraint": "运行约束",
    "initial_guess": "数值初值", "component_sizing": "概念选型",
    "solver_setting": "数值设置", "legacy": "历史兼容参数",
}

PREBURNER_MODE_CHOICES = [
    {"value": "temperature", "label": "指定出口燃气温度（求解氧燃质量比）"},
    {"value": "mixture_ratio", "label": "指定氧燃质量比（计算出口燃气温度）"},
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
    "of_ratio": _meta("design_input", "氧燃质量比 = 氧化剂质量流量 ÷ 燃料质量流量（无量纲），不是体积比、摩尔比或当量比。此项指定整机总供给配比；预燃室、燃气发生器及其主室的局部配比分别求解或指定。3.6 为示例输入。"),
    "expansion_ratio": _meta("design_input", "主喷管出口面积 Ae / 喉部面积 At；循环、推力室轮廓和冷却使用同一个输入。它不同于燃烧室收缩比及冷却终点面积比，也不同于 GG 排气喷管的面积比。34.34 是示例几何假设。"),
    "gg_exhaust_expansion_ratio": _meta("design_input", "GG 涡轮排气所用独立喷管的出口面积 / 喉部面积；参与 GG 排气推力计算，不定义主推力室型面或主喷管冷却覆盖。"),
    "ambient_pressure": _meta("boundary", "用于压力推力项；目标推力必须与这一环境压力使用相同口径。", source="方案边界"),
    "target_isp": _meta("initial_guess", "仅通过 F/(g₀ Isp) 构造总流量初值；最终比冲由模型计算。", source=None),
    "fuel_inlet_temp": _meta("boundary", "贮箱出口液体的接口温度，由贮存、过冷及供给条件确定。100 K 是液甲烷示例假设，不是通用推荐值或实机实测值。结合该处绝压检查相态、饱和温度与可用熔化线；后续温度由泵焓升和管路等焓过程计算。", source="方案供给边界；示例值待接口数据确认"),
    "ox_inlet_temp": _meta("boundary", "贮箱出口液体的接口温度。70 K 是过冷液氧示例假设，不是氧的常压沸点，也不是实机实测值。按该处绝压检查相态与过冷度；加注和贮存系统能否维持此温度需另行确认。主泵入口温度由供给链计算。", source="方案供给边界；示例值待接口数据确认"),
    "tank_pressure_pa": _meta("boundary", "当前用一个绝对压力描述两路贮箱出口液体边界，不是表压，也不是已求解的气枕压力。自生增压、液柱/加速度和贮箱至前置泵的吸入管损未单独求解；工程交接应分别给出燃料、氧化剂接口条件。", source="两路共用的集总边界假设"),
    "inlet_pressure": _meta("design_input", "当前供给方案显式包含前置低压增压泵，位置在贮箱与主泵之间，用于提高主泵入口压力。出口绝压为设计要求，扣除短管预算后得到主泵入口压力；尚未证明泵能实现该压升。增压功按外部功源记入整机能量账，不计入两根主涡轮轴。增压泵不是 FFSC 循环的必备构型，也不等同于贮箱气体增压系统或主泵诱导轮。", source="前置增压方案假设；驱动及吸入性能待设计"),
    "feed_line_dp_pa": _meta("model_assumption", "初步设计用给定压降闭合压力预算；该段按绝热等焓计算出口温度，不能当作等温。固定硬件变工况需由管径、长度、粗糙度和局部阻力或实测曲线求压降；当前短管仍用固定预算，不是管路能力验证。", source="设计点管路压降预算；待水力模型或试验替换"),
    "methane_thermo_dataset": _meta("model_assumption", "高温版保留 GRI30 的 53 个物种并使用其高温热化学数据；CHO 物种共同温区为 300–5000 K，仍逐物种检查实际温区。气相仍为理想气体混合物，高压状态方程适用性尚未验证；高温热化学数据不构成高温反应动力学认证。历史方案沿用原数据，不自动切换。", source="Cantera 随附 GRI30 / GRI30 high-T 数据；版本随计算记录保存", propellants=["lox_methane"]),
    "combustion_loss_model": _meta("model_assumption", "基础模型均为给定压力与焓的化学平衡。选择温升折减时，在平衡计算后保持组分不变，按历史系数修正温升，焓差计入经验损失。该修正仅用于旧方案复现，未经试验标定，不代表特征速度效率或燃料燃尽率。", source="项目历史兼容实现"),
    "combustion_efficiency": _meta("legacy", "仅在温升折减模式下生效。修正温度等于入口混合温度加上本系数与平衡温升的乘积，修正后保持平衡组分不变。该系数不等同于特征速度效率或燃料燃尽率。", visibility={"all": [{"key": "combustion_loss_model", "op": "eq", "value": "legacy_temperature_rise"}]}),
    "chamber_temp_guess": _meta("initial_guess", "尚无热侧沿程解时，用于首轮冷却热侧与喉部尺寸估算。当前 Cantera 主室按实际入口组成、焓和压力求温度，不以此值指定燃烧温度。启动估计会影响收敛路径、耗时及成功率；过差时应检查失败原因。它不是设计温度上限或焓参考态。", source=None),
    "chamber_gamma": _meta("initial_guess", "γ=cp/cv 的启动估计，与温度和平均摩尔质量共同初始化冷却热侧及喉部尺寸。随后使用当前热化学及热侧沿程解，不固定整个喷管的比热比。同一物理解分支充分收敛后，合理初值改变不应显著改变结果，须以多初值复核确认。", source=None),
    "molar_mass": _meta("initial_guess", "用于首轮气体常数及冷却、喉部尺寸估算；单位 kg/kmol。最终平均摩尔质量由产物完整组成计算，不是设计约束或化学焓基准。改变它可能影响数值收敛，不能用启动估计调节最终性能。预算冷却不使用这三个冷却启动估计。", source=None),
    "use_target_preburner_temp": _meta("legacy", "由网页预燃室设计方式统一设置，保留旧方案字段。", source=None, hidden=True),
    "use_target_preburner_mr": _meta("legacy", "温度模式优先；由网页设计方式统一设置，保留旧方案字段。", source=None, hidden=True),
    "cooling_heat_load": _meta("model_assumption", "仅给定 Q 与 Δp 的预算模式生效；该模式计算端口焓差，不生成沿程曲线。准一维模式的吸热量由统一几何、Bartz 热侧关联和冷却侧换热计算。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]}),
    "coolant_cp": _meta("legacy", "科学运行使用 Cantera / CoolProp 焓反算出口温度，此常数不控制温升；仅为旧模型兼容保留，仍需满足旧方案的有效性校验。", source=None, hidden=True),
    "cooling_wall_conductivity": _meta("legacy", "循环冷却使用所选内衬材料的温度相关导热函数；此手填常数被材料函数覆盖，仅保留旧方案兼容，不作为当前有效输入。", source=None, hidden=True),
    "cooling_dp_pa": _meta("model_assumption", "预算模式：给定压降直接进入泵压需求与冷却出口状态，不是初值。准一维模式：此值仅作耦合压降初猜，最终压降由通道动量方程计算并替换。示例 5 MPa 未由几何或实测验证。", source="项目压降设定；非已核实实机数据；预算或初值身份取决于冷却模式", visibility={"all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]}),
    "cooling_bartz_mode": _meta("model_assumption", "沿推力室和通道几何联立冷却剂压力与焓。燃气侧采用 Bartz 型对流关联及灰体辐射近似，壁面采用温变导热率积分，冷却剂侧采用分流态换热关联；吸热、压降及出口状态参与整机循环迭代。历史预算模式仅采用给定吸热量与压降。", source="当前计算方法；模型适用范围随结果记录"),
    "cooling_dp_auto": _meta("legacy", "历史兼容字段，不再选择另一套水力模型。准一维模式始终联立压力与焓；预算模式只使用给定 Δp。", source=None, hidden=True),
    "cooling_marching_enable": _meta("legacy", "仅为历史方案兼容保留；不再控制独立后处理或物理计算。准一维模式总是保存同源沿程结果。", source=None, hidden=True),
    "cooling_grid_points": _meta("solver_setting", "准一维冷却几何网格节点数；属于数值分辨率，不是物理设计输入。改变分辨率后应检查网格收敛，固定硬件工况沿用相同几何。", source=None, advanced=True, visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]}),
    "preburner_injector_dp_ratio": _meta("model_assumption", "四条供给支路未单独设置喷注器压降比时，沿用本值。喷注器设计压降为压降比乘以该支路目的预燃室压力。示例值 0.12 为工程假设。", source="设计压降预算；待部件数据校核"),
    "ox_turbine_exhaust_dp_ratio": _meta("model_assumption", "富氧涡轮出口至主燃烧室的热气侧压降预算：Δp = 本参数 × pc；涡轮出口压力为 pc + Δp。不是液氧直接进入主室的液路喷注压降，也不等于涡轮压比。当前预算未由热气喷注器几何或实测流量特性验证。", source="设计压降预算；非已核实实机数据"),
    "fuel_turbine_exhaust_dp_ratio": _meta("model_assumption", "富燃涡轮出口至主燃烧室的热气侧压降预算：Δp = 本参数 × pc；涡轮出口压力为 pc + Δp。不是液态燃料直接进入主室的喷注压降，也不等于涡轮压比。当前预算未由热气喷注器几何或实测流量特性验证。", source="设计压降预算；非已核实实机数据"),
    "valve_line_dp_ratio": _meta("model_assumption", "基础管路及全开阀的设计压降与主燃烧室压力之比。全流量补燃循环中，未单独设置的支路沿用本值；供给压力匹配得到的附加调节压降单列。燃气发生器循环保留原供给预算定义。", source="设计压降预算；待部件数据校核"),
    "nonlinear_solver": _meta("solver_setting", "默认缩放牛顿法；布罗伊登法复用雅可比矩阵并在必要时重建；有界信赖域法用于同一方程的对照。牛顿法与布罗伊登法使用变量级步长限制，信赖域法使用其自身的步长控制。停止条件不能替代逐项残差与物理验收。", source=None),
    "steady_ox_power_tolerance_W": _meta("solver_setting", "全流量补燃概念设计的富氧侧轴功率绝对残差容差，单位 W；随任务保存，独立于初猜和残差数值缩放。默认 1 W 是数值精度设置，不是部件工程允许功率差。", source=None, visibility={"cycles": ["ffsc"]}),
    "steady_fuel_power_tolerance_W": _meta("solver_setting", "全流量补燃概念设计的富燃侧轴功率绝对残差容差，单位 W；随任务保存，独立于初猜和残差数值缩放。默认 1 W 是数值精度设置，不是部件工程允许功率差。", source=None, visibility={"cycles": ["ffsc"]}),
}

for key in ("boost_pump_efficiency", "pump_efficiency_ox", "pump_efficiency_fuel"):
    PARAMETER_CONTRACT[key] = _meta("model_assumption", "定义 ηp=(h出口等熵−h入口)/(h出口−h入口)，轴功与出口状态使用同一焓升。设计点常效率可作为初选，需用同类部件或试验数据定标并做敏感性分析；该数值不是预测结果或性能图。轴承、密封等损失未独立分解，应与外部效率数据的功率口径核对。", source="设计点常效率假设；未由部件特性图校准")

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
    PARAMETER_CONTRACT[key] = _meta("design_input", "初步冷却几何，用于沿程换热与压降计算。调整后重新计算整机设计点，依据收敛结果校核。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_cover_eps"]["help"] = "主喷管轮廓上冷却终点的局部截面积 / 喉部面积；0 表示覆盖至主喷管出口。用于确定同一型面的冷却范围，不改变主喷管出口面积比；结果记录实际覆盖位置。"
PARAMETER_CONTRACT["cooling_channel_height_m"]["visibility"]["all"].append({"key": "cooling_channel_auto_size", "op": "lte", "value": 0})
PARAMETER_CONTRACT["cooling_n_channels"].update(label="冷却通道数量 N", help="周向通道数量，与局部半径共同确定通道节距；肋宽由节距减去通道宽度得到。300 为可修改的初步设计值，需结合壁温、压降及制造要求校核。")
PARAMETER_CONTRACT["cooling_channel_width_m"].update(label="喉部通道宽度 w", help="未指定轴向分布表时，以喉部宽度为基准，沿程宽度按局部半径比例变化；各截面宽度应小于通道节距。1.5 mm 为默认初步设计值。")
PARAMETER_CONTRACT["cooling_channel_height_m"].update(label="通道深度 h", help="给定几何模式下采用的通道深度，未指定轴向分布表时沿程保持不变。8 mm 为默认初步设计值，需通过循环与冷却耦合计算校核。")
PARAMETER_CONTRACT["cooling_wall_thickness_m"].update(label="内衬热壁厚度 t", help="燃气侧壁面与冷却通道之间的壁厚，参与壁面导热计算；未指定轴向分布表时沿程保持不变。0.5 mm 为默认初步设计值，承载能力与寿命另行校核。")
PARAMETER_CONTRACT["cooling_channel_auto_size"] = _meta("component_sizing", "给定深度时直接计算该几何方案；指定参考截面速度时，在给定通道数量和宽度下反算深度。两种方式均需进行沿程热工与水力校核。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_channel_auto_size"].update(label="通道深度确定方式", type="select", choices=[
    {"value": 0.0, "label": "给定通道深度"},
    {"value": 1.0, "label": "按参考截面速度计算深度"}])
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
    PARAMETER_CONTRACT[key] = _meta("model_assumption", "当前冷却方案的换热与阻力计算参数，随几何方案保存。", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_htc_enhancement"].update(label="喉部冷却侧换热标定系数", help="仅作用于喉部轴向前后各一个喉半径范围内的冷却剂侧换热系数。1 表示不额外修正，非 1 值需登记试验或部件计算依据。该系数不显式增加摩擦阻力，不能代替真实扰流结构；燃气侧 Bartz 曲率修正另行计算。")
PARAMETER_CONTRACT["cooling_htc_enhancement_basis"] = _meta("model_assumption",
    "记录试验或部件计算来源、工况与几何适用范围。留空表示未登记标定依据；旧方案的系数仍按原值计算。",
    source=None, label="换热标定依据与适用范围", multiline=True,
    visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_channel_roughness_m"] = _meta("model_assumption",
    "选填。填写后用绝对等效砂粒粗糙度 ks 与各截面的水力直径计算 ks/Dh；0 表示光滑壁。它不等同于表面粗糙度 Ra，数值应有加工表面测量或摩阻标定依据。留空时沿用下方相对粗糙度。",
    source=None, label="绝对等效粗糙度 ks（选填）", nullable=True,
    placeholder="留空则采用相对粗糙度", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
PARAMETER_CONTRACT["cooling_channel_rel_roughness"].update(label="沿程相对粗糙度 ks/Dh",
    help="绝对粗糙度留空时生效：沿程使用给定的 ks/Dh 计算 Darcy 摩擦系数，意味着 Dh 变化时隐含的 ks 也变化。0.003 为未标定示例，不是某种制造工艺的通用值；不直接修正冷却侧换热关联式。")
PARAMETER_CONTRACT["cooling_channel_rel_roughness"]["visibility"]["all"].append(
    {"key": "cooling_channel_roughness_m", "op": "empty"})
PARAMETER_CONTRACT["cooling_channel_k_minor"].update(label="进出口局部阻力系数之和（均分）",
    help="两端独立系数均留空时采用此合计值，入口与出口各取一半；各自使用当地通道动压，因此压降不一定均分。3 为未标定示例。分布摩擦及流动加速另行计算。")
for key in ("cooling_k_minor_inlet", "cooling_k_minor_outlet"):
    PARAMETER_CONTRACT["cooling_channel_k_minor"]["visibility"]["all"].append({"key": key, "op": "empty"})
for key, label, position in (("cooling_k_minor_inlet", "入口局部阻力系数 K入（选填）", "入口"),
                             ("cooling_k_minor_outlet", "出口局部阻力系数 K出（选填）", "出口")):
    PARAMETER_CONTRACT[key] = _meta("model_assumption",
        f"与另一端系数一起填写；按{position}通道截面的当地动压计算附加损失，0 有效。两端均留空时沿用原合计值均分。应根据实际连接结构或试验确定，不能代表未建模的分支流量分配。",
        source=None, label=label, nullable=True, placeholder="两端均留空则沿用合计值均分",
        visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
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
for key, label in (("cooling_wall_temp_limit_status", "热面壁温限值依据状态"),
                   ("cooling_wall_temp_limit_K", "燃气侧热壁温度上限"),
                   ("cooling_wall_temp_limit_basis", "壁温限值来源与适用条件")):
    PARAMETER_CONTRACT[key].update(label=label, role_label="校核约束")
for key in ("turbine_efficiency_ox", "turbine_efficiency_fuel"):
    PARAMETER_CONTRACT[key] = _meta("model_assumption", "定义 ηt=(h入口−h出口)/(h入口−h出口等熵)，按同一出口压力计算等熵焓降。设计点采用常效率假设，需部件性能图或试验定标；总对总/总对静口径必须与涡轮交接中的状态定义一致，不能直接互换。该值不是叶型计算结果，也不自动包含独立传动损失。", source="设计点常效率假设；未由部件特性图校准")

for key in ("chamber_temp_guess", "chamber_gamma", "molar_mass"):
    PARAMETER_CONTRACT[key]["visibility"] = {"all": [
        {"key": "cooling_bartz_mode", "op": "gt", "value": 0}]}

for side, label in (("ox", "富氧"), ("fuel", "富燃")):
    PARAMETER_CONTRACT[f"{side}_preburner_temp_target"] = _meta(
        "design_input", "预燃室出口平均燃气温度设计目标，用于反求氧燃质量比并参与循环匹配。当前模型将预燃室出口状态直接作为涡轮入口状态。",
        source="本项目概念设计目标；非 Raptor 3 实测值",
        visibility={"preburner_modes": ["temperature"]})
    PARAMETER_CONTRACT[f"{side}_preburner_temp_limit"] = _meta(
        "constraint", "出口平均燃气温度的方案约束，启用后参与设计输入检查与结果校核。该限值不等同于壁面或涡轮金属的允许温度。",
        source="按当前方案登记；设计示例中的 800 / 900 K 为暂定值",
        visibility={"any": [
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "provisional"},
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "documented"}]})
    PARAMETER_CONTRACT[f"{side}_preburner_temp_limit_status"] = _meta(
        "constraint", "未设置：不进行温度上限校核。设计暂定：按方案暂定值比较。依据已登记：按登记的限值与适用条件比较。",
        source=None, type="select", choices=[
            {"value": "unset", "label": "未设置"},
            {"value": "provisional", "label": "设计暂定"},
            {"value": "documented", "label": "依据已登记"}])
    PARAMETER_CONTRACT[f"{side}_preburner_temp_limit_basis"] = _meta(
        "constraint", "登记限值来源及其适用的燃气组成、压力、部件材料、冷却方式和工作时长。依据登记不代表材料或寿命验证通过。",
        source=None, multiline=True,
        visibility={"any": [
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "provisional"},
            {"key": f"{side}_preburner_temp_limit_status", "op": "eq", "value": "documented"}]})
    PARAMETER_CONTRACT[f"{side}_preburner_mr_target"] = _meta(
        "design_input", "该预燃室的氧化剂质量流量 ÷ 燃料质量流量（无量纲）。指定温度时仅为反求氧燃质量比的初值；指定氧燃质量比时为设计输入。来源输入中的初值不是结果中的实际配比。",
        roles_by_mode={"temperature": "initial_guess", "mixture_ratio": "design_input"},
        help_by_mode={"temperature": "该预燃室的氧化剂质量流量 ÷ 燃料质量流量。此处仅为反求初值，实际氧燃质量比从计算结果读取；温度反求结束后输入初值恢复原值。", "mixture_ratio": "该预燃室的氧化剂质量流量 ÷ 燃料质量流量。作为设计输入固定并计算出口温度；这是概念设计方式，不是固定机组匹配。"},
        labels_by_mode={"temperature": f"{label}预燃室 氧燃质量比 初值", "mixture_ratio": f"{label}预燃室指定 氧燃质量比"},
        visibility={"preburner_modes": ["temperature", "mixture_ratio"]})
    PARAMETER_CONTRACT[f"{side}_preburner_pressure_ratio"] = _meta(
        "initial_guess", "pPB/pc 不等于涡轮进出口压比。目标模式中只构造压比初猜，可能被初值下限覆盖；实际涡轮压比由功率匹配求得。历史自由分流模式将其作为压力输入，但仍可能被该模式的最小涡轮压比下界抬升。",
        source=None, roles_by_mode={"legacy_free_split": "design_input"},
        labels_by_mode={"legacy_free_split": f"{label}预燃室压力 / 主室压力（历史输入）"})

for key in ("fuel_split_to_ox_preburner", "ox_split_to_ox_preburner"):
    PARAMETER_CONTRACT[key] = _meta("initial_guess", "仅历史自由分流模式生效；指定温度或混合比时，分流由相应方程确定。", source=None,
                                   visibility={"preburner_modes": ["legacy_free_split"]})

for side in ("lox", "fuel"):
    pump_label = "液氧泵" if side == "lox" else "燃料泵"
    speed_key = f"{side}_pump_speed_rpm_override"
    PARAMETER_CONTRACT[f"{side}_pump_speed_rpm_override"] = _meta(
        "component_sizing", "本轮计算采用的初选转速，同轴涡轮共用；求解器不会调整它。可在部件校核后修改并重新计算。它不是数值迭代初值，也不是通过泵 / 涡轮特性图匹配得到的运行转速。",
        label=f"{pump_label}初选转速", role_label="初选假设",
        visibility={"all": [{"key": speed_key, "op": "neq", "value": 0}]})
    PARAMETER_CONTRACT[f"{side}_pump_specific_speed_target"] = _meta(
        "component_sizing", "本轮用于估算转速的目标比转速，不是求解初值。采用 nq = N√Q/H级^0.75 的单位制定义：N 为 rpm、Q 为 m³/s、H级为 m；由循环流量和单级扬程反推转速，不代表已完成泵 / 涡轮特性匹配。",
        role_label="初选假设",
        visibility={"all": [{"key": speed_key, "op": "eq", "value": 0}]})
    PARAMETER_CONTRACT[f"{side}_pump_n_stages"] = _meta(
        "component_sizing", "本轮固定采用的级数方案；程序不自动选级或优化级数。用于单级扬程、比转速与尺寸初估；当前总泵功仍由给定效率和总焓升计算。",
        label=f"{pump_label}初选级数", role_label="初选假设")
    PARAMETER_CONTRACT[f"{side}_pump_head_coefficient_psi"] = _meta(
        "component_sizing", "ψ = gH级/u2²；用于叶尖速度和外径初选，部件结果与几何共用此值。CFturbo 的 Ψ = 2ψ。H级按入口密度和压差定义，不改变真实流体泵功。", source="示例初选值；待详细水力设计确认", role_label="初选假设")
    for suffix, help_text in {
        "inlet_flow_coefficient": "cm1/u2：入口子午速度除以叶轮出口圆周速度。以入口密度、流量和轮毂比求泵眼环形面积；不等于 cm1/u1，也不验证汽蚀。",
        "outlet_flow_coefficient": "cm2/u2：出口子午速度除以叶轮出口圆周速度。按恒入口密度、无叶片堵塞和无泄漏假设求出口宽度 b2。",
        "eye_hub_ratio": "D1h/D1s：泵眼轮毂直径与轮盖直径之比。只定义流道入口环形面积，不是轴强度计算所得轴径。",
        "axial_length_ratio": "Lz/D2：代表级子午流道轴向长度与叶轮外径之比。用于初始轮廓构造，不是多级泵总长或优化结论。",
    }.items():
        PARAMETER_CONTRACT[f"{side}_pump_{suffix}"] = _meta(
            "component_sizing", help_text + " 本轮按给定值构造几何，不参与循环求解器的变量调整。", source="可编辑的示例几何假设；不代表已验证的推荐值", role_label="初选假设")


PARAMETER_CONTRACT["turbine_boundary_basis"] = _meta("model_assumption",
    "未指定时保留系统节点量。显式采用总态接口近似后，新记录将涡轮两端节点解释为总态储腔条件，压比为总总压比；这是状态量定义的模型假设，不进行总静转换，也不更改循环数值或历史记录。后续部件设计仍需根据实际截面求静态。", source="须按项目接口定义选择", label="涡轮状态量约定")
PARAMETER_CONTRACT["turbine_meanline_mode"] = _meta("component_sizing",
    "默认关闭，仍计算整体尺寸初估。启用后按中径、同轴转速、初选反动度、轴向速度系数和实际比功分配构造均径速度三角形；仅作运动学校核，不求逐级压力、损失或叶型，也不反馈预测效率。", label="均径速度三角形校核", role_label="初选假设")

for side, label in (("ox", "氧侧"), ("fuel", "燃料侧／共轴")):
    visibility = ({"any": [{"key": "cycle_type", "op": "eq", "value": "ffsc"},
                            {"key": "gg_layout", "op": "eq", "value": "dual_shaft"}]}
                  if side == "ox" else {})
    meanline_visibility = deepcopy(visibility)
    meanline_visibility["all"] = [{"key": "turbine_meanline_mode", "op": "neq", "value": "disabled"}]
    PARAMETER_CONTRACT[f"turb_{side}_meanline_flow_coefficient"] = _meta("component_sizing",
        "ca/U：启用均径校核时采用的轴向速度系数，本轮按给定值使用。动叶进出口采用相同轴向速度，与概念流道的端面速度假设相互独立。", visibility=deepcopy(meanline_visibility), role_label="初选假设")
    PARAMETER_CONTRACT[f"turb_{side}_stage_work_fractions"] = _meta("component_sizing",
        "自定义模式使用此JSON数组，例如 [0.6,0.4]；各项为实际比功比例，数量必须等于本记录级数、和为1。不自动归一化，不将比例当压比或效率分配。", visibility=deepcopy(visibility) | {"all": [{"key": "turbine_meanline_mode", "op": "eq", "value": "custom_work"}]}, role_label="初选假设")
    for suffix, help_text in {
        "inlet_flow_coefficient": "cm1/U：入口轴向速度与保存节圆速度之比。与保存流量及参考静态密度共同确定入口环形面积。",
        "outlet_flow_coefficient": "cm2/U：出口轴向速度与保存节圆速度之比。出口密度由保存出口压力、温度和冻结组成分子量估算；不沿用入口密度。",
        "stator_axial_chord_ratio": "Ls/Dm：各静叶排的轴向弦长包络与保存中径之比。只定位叶排，不产生叶型、叶片数或安装角。",
        "rotor_axial_chord_ratio": "Lr/Dm：各动叶排的轴向弦长包络与保存中径之比。只用于轴向布局，不是优化后的叶片弦长。",
        "row_gap_ratio": "gap/Dm：相邻叶排轴向间距与中径之比；包括级内静动叶间距和级间间距，不包含轴承、密封或进排气蜗壳。",
        "geometry_state_basis": "本概念几何把保存系统节点 P/T 当作静态参考量计算密度，采用冻结组成理想气体；系统未解析端口速度，尚不能确认实际总压／静压。CFD 前需重新确认边界口径。",
    }.items():
        PARAMETER_CONTRACT[f"{side}_turbine_{suffix}"] = _meta(
            "model_assumption" if suffix == "geometry_state_basis" else "component_sizing", help_text,
            source="可编辑的轴流几何初算假设；不代表已验证的实机尺寸", visibility=deepcopy(visibility),
            role_label="模型假设" if suffix == "geometry_state_basis" else "初选假设")
    for suffix, title, help_text in (
        ("u_over_c_target", "初选速比 U/C₀", "按整台涡轮总等熵焓降定义 C₀=√(2Δh_s)，结合本轮转速初估中径；不是求解器优化目标，也不是单级速比。当前中径有 0.05–0.80 m 限幅，实际速比可能偏离初选值；不据此更新涡轮效率。"),
        ("reaction_degree", "初选反动度", "仅在启用均径校核时采用，本轮按给定值构造速度三角形；不是求得的最优反动度。动叶静焓降/级比功的解释依赖重复级假设，不据此求级内压力或叶型。"),
        ("n_stages_user", "初选级数（0=按规则估算）", "正整数作为本轮初选级数；0 按现有速比规则估算，不是最优级数。启用均径校核时用于分配比功；不代表逐级压力、损失和气动性能已匹配。"),
        ("u_max_ms", "节圆速度参考校核上限", "用于比较初估节圆速度与给定参考上限，不是优化目标或材料许用叶尖速度。超限时需调整方案并复核；几何页另列叶尖速度供后续结构分析。"),
    ):
        PARAMETER_CONTRACT[f"turb_{side}_{suffix}"] = _meta(
            "constraint" if suffix == "u_max_ms" else "component_sizing", help_text,
            label=f"{label}涡轮 {title}",
            visibility=deepcopy(meanline_visibility if suffix == "reaction_degree" else visibility),
            role_label="参考约束" if suffix == "u_max_ms" else "初选假设")


for _branch in ("ox_to_opb", "fuel_to_opb", "ox_to_fpb", "fuel_to_fpb"):
    for _kind, _reference in (("line", "主燃烧室压力"), ("injector", "该支路目的预燃室压力")):
        PARAMETER_CONTRACT[f"{_branch}_{_kind}_dp_ratio"] = _meta(
            "model_assumption",
            f"设计压降与{_reference}之比。留空时沿用统一预算；填写后仅对本支路生效。"
            + ("包含基础管路及全开阀损失，附加调节压降单独计算。" if _kind == "line" else "对应本推进剂入口喷注器。"),
            source="设计压降预算；待部件数据校核")


def parameter_metadata(key):
    metadata = deepcopy(PARAMETER_CONTRACT.get(key, {}))
    modes = parameter_analysis_roles(key)
    if modes:
        metadata["analysis_modes"] = modes
    return metadata


def concept_parameter_role(key, values):
    """Resolve the role in this concept submode, independently of field names."""
    if key in {"cooling_channel_table_json", "wall_thermal_dataset_id",
               "wall_manufacturing_state", "wall_material_zones_json"} or key.endswith("_material"):
        return "design_input"
    if key in {"gg_temp_limit", "chamber_wall_allow_pa", "chamber_wall_safety_factor"}:
        return "constraint"
    metadata = PARAMETER_CONTRACT.get(key, {})
    mode = preburner_design_mode(values)
    role = metadata.get("roles_by_mode", {}).get(mode, metadata.get("role"))
    if isinstance(role, dict):
        role = role.get("role")
    if key == "gg_mr_target":
        return "initial_guess" if float(values.get("use_target_gg_temp", 1)) >= .5 else "design_input"
    if key == "gg_fraction_guess":
        return "initial_guess"
    if key == "gg_temp_target":
        return "design_input" if float(values.get("use_target_gg_temp", 1)) >= .5 else "legacy"
    if key in {"ox_preburner_temp_target", "fuel_preburner_temp_target"} and mode != "temperature":
        return "legacy"
    if key in {"ox_preburner_mr_target", "fuel_preburner_mr_target"} and mode == "legacy_free_split":
        return "legacy"
    return role


def preburner_mode_field():
    return {"key": "preburner_design_mode", "label": "热力设计条件", "type": "select",
            "default": "temperature", "virtual": True, "cycles": ["ffsc"],
            "unit": "", "factor": 1, "choices": deepcopy(PREBURNER_MODE_CHOICES),
            **_meta("design_input", "选择预燃室热力计算的给定条件：出口平均燃气温度或氧燃质量比。流量、压力和涡轮功率仍由发动机循环联合匹配。", source="概念设计输入")}


def pump_speed_mode_field(side):
    """Presentation adapter only; the existing numeric speed remains canonical."""
    if side not in ("lox", "fuel"):
        raise ValueError("泵侧必须为 lox 或 fuel。")
    speed_key = f"{side}_pump_speed_rpm_override"
    visibility = deepcopy(PARAMETER_CONTRACT[speed_key].get("visibility", {}))
    # The selector must stay visible in both modes, including invalid imported
    # numeric values. Keep only the component/cycle availability conditions.
    visibility.pop("all", None)
    return {"key": f"{side}_pump_speed_mode", "label": "转速初选方式", "type": "select",
            "default": "specified", "virtual": True, "pump_speed_key": speed_key,
            "unit": "", "factor": 1, "cycles": ["ffsc", "gg"], "visibility": visibility,
            "choices": [{"value": "specified", "label": "给定初选转速"},
                        {"value": "estimated", "label": "按目标比转速估算"}],
            **_meta("component_sizing", "选择本轮转速的来源。两种方式都属于概念初选，均不求解固定硬件的轴系运行转速。",
                    source=None, role_label="初选假设")}

def turbine_stage_mode_field(side):
    """Presentation-only adapter for the existing zero/positive stage input."""
    if side not in ("ox", "fuel"):
        raise ValueError(f"未知涡轮侧：{side}")
    stages_key = f"turb_{side}_n_stages_user"
    return {"key": f"turb_{side}_stage_mode", "label": "级数初选方式", "type": "select",
            "default": "estimated", "virtual": True, "turbine_stages_key": stages_key,
            "unit": "", "factor": 1, "cycles": ["ffsc", "gg"],
            "visibility": deepcopy(PARAMETER_CONTRACT[stages_key].get("visibility", {})),
            "choices": [{"value": "estimated", "label": "按现有规则估算"},
                        {"value": "specified", "label": "给定初选级数"}],
            **_meta("component_sizing", "选择本轮级数的来源。按规则估算仅沿用现有速比判据，不进行级数优化；给定级数也需后续部件校核。", source=None, role_label="初选假设")}


# In parallel dual-shaft GG the oxygen-side turbine also receives fuel-rich gas.
# Single-shaft GG uses the oxygen-side speed and the fuel-side turbine efficiency.
for _key in ("turbine_efficiency_ox", "fuel_pump_speed_rpm_override", "fuel_pump_specific_speed_target"):
    PARAMETER_CONTRACT[_key].setdefault("visibility", {})["any"] = [
        {"key": "cycle_type", "op": "eq", "value": "ffsc"},
        {"key": "gg_layout", "op": "eq", "value": "dual_shaft"}]
