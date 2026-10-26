"""Shared presentation declarations for desktop and browser workbenches.

No Qt imports, UI construction, solver logic, or unit conversions happen here.
The declarations were moved unchanged from gui_pyside6.py.
"""
from typing import Dict, List, Tuple


_MATERIAL_FIELD_META: Dict[str, Tuple[str, bool]] = {
    # 富氧侧：所有材料必须 oxygen_compatible=True（ASTM G124）
    "turb_ox_rotor_material":   ("rotor_blade", True),
    "turb_ox_stator_material":  ("stator_vane", True),
    "turb_ox_disk_material":    ("disk",        True),
    "turb_ox_housing_material": ("housing",     True),
    # 富燃侧：可用含 Co 高温合金，无氧兼容硬约束
    "turb_fuel_rotor_material":   ("rotor_blade", False),
    "turb_fuel_stator_material":  ("stator_vane", False),
    "turb_fuel_disk_material":    ("disk",        False),
    "turb_fuel_housing_material": ("housing",     False),
    # 推力室壁（V18）：内壁=铜合金燃气侧、外壳=镍基结构层；都不强制氧兼容
    "chamber_inner_wall_material": ("chamber_inner_wall", False),
    "chamber_outer_wall_material": ("chamber_outer_wall", False),
}


DISPLAY_UNIT_MAP: Dict[str, Tuple[float, str]] = {
    "thrust":               (1e-3,  "kN"),    # N  → kN
    "pc":                   (1e-6,  "MPa"),   # Pa → MPa
    "ambient_pressure":     (1e-6,  "MPa"),   # Pa → MPa
    "tank_pressure_pa":     (1e-5,  "bar"),   # Pa → bar（贮箱真实压力）
    "inlet_pressure":       (1e-6,  "MPa"),   # Pa → MPa（增压泵出口）
    "feed_line_dp_pa":      (1e-5,  "bar"),   # Pa → bar
    "cooling_heat_load":    (1e-6,  "MW"),    # W  → MW
    "cooling_dp_pa":        (1e-6,  "MPa"),   # Pa → MPa
    "chamber_wall_allow_pa":(1e-6,  "MPa"),   # Pa → MPa
    # V19 推力室设计工作区：通道几何用 mm 显示（内部 SI m）
    "cooling_channel_width_m":  (1e3, "mm"),
    "cooling_channel_height_m": (1e3, "mm"),
    "cooling_wall_thickness_m": (1e3, "mm"),
    # V20 燃气发生器循环
    "gg_turbine_backpressure_pa": (1e-5, "bar"),   # Pa → bar
}


CYCLE_TYPE_CHOICES: List[Tuple[str, str]] = [
    ("ffsc", "全流量分级燃烧循环（FFSC，闭式）"),
    ("gg",   "燃气发生器循环（GG，开式）"),
]

PROPELLANT_CHOICES: List[Tuple[str, str]] = [
    ("lox_methane", "液氧 / 甲烷（LOX / CH₄）"),
    ("lox_kerosene", "液氧 / 煤油（正十二烷替代模型）"),
]


GG_LAYOUT_CHOICES: List[Tuple[str, str]] = [
    ("single_shaft", "单轴：一台涡轮同轴驱动双泵"),
    ("dual_shaft",   "双轴：两台涡轮分别驱泵（燃气并联）"),
]

NONLINEAR_SOLVER_CHOICES: List[Tuple[str, str]] = [
    ("newton", "缩放 Newton · 回溯线搜索"),
    ("trf", "有界信赖域 TRF · 对照求解"),
]

COMBUSTION_LOSS_CHOICES: List[Tuple[str, str]] = [
    ("adiabatic", "焓平衡（不附加经验燃烧损失）"),
    ("legacy_temperature_rise", "历史兼容：温升折减模型"),
]

THERMO_DATASET_CHOICES: List[Tuple[str, str]] = [
    ("gri30", "GRI30（历史数据）"),
    ("gri30_highT", "GRI30 高温热化学数据"),
]


FFSC_ONLY_GROUPS = {
    "预燃室(FFSC) / Preburners",
    "预燃室设计(FFSC) / Preburner design",
    "压力网络(FFSC) / Pressure network",
}


GG_ONLY_GROUPS = {
    "燃气发生器(GG) / Gas generator",
}


PARAM_GROUPS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    (
        "循环方式 / Engine cycle",
        [
            ("propellant_pair", "推进剂组合 / Propellants", ""),
            ("cycle_type", "循环方式 / Cycle", ""),
            ("gg_layout",  "GG 轴系布局 / GG layout", ""),
        ],
    ),
    (
        "基本性能 / Design point",
        [
            ("thrust",           "目标推力（给定环境压力） / Target thrust", "kN"),
            ("pc",               "燃烧室压力 / Chamber press.", "MPa"),
            ("of_ratio",         "氧燃比 O/F",                  "—"),
            ("expansion_ratio",  "喷管膨胀比 / Nozzle ε",       "—"),
            ("target_isp",       "比冲初值(仅流量初猜) / Isp init.", "s"),
            ("ambient_pressure", "环境压力 / Ambient press.",   "MPa"),
        ],
    ),
    (
        "入口与效率 / Inlet & efficiency",
        [
            ("fuel_inlet_temp",        "燃料贮箱出口温度 / Fuel tank outlet T",     "K"),
            ("ox_inlet_temp",          "液氧贮箱出口温度 / LOX tank outlet T",      "K"),
            ("tank_pressure_pa",       "贮箱压力边界 / Tank pressure",  "bar"),
            ("inlet_pressure",         "增压泵出口压力 / Boost outlet pressure",  "MPa"),
            ("feed_line_dp_pa",        "增压泵→主泵短管压降 / Boost→pump duct ΔP",         "bar"),
            ("boost_pump_efficiency",  "增压泵等熵效率 / Boost pump η",                "—"),
            ("pump_efficiency_ox",     "液氧泵等熵效率 / LOX pump η",                "—"),
            ("pump_efficiency_fuel",   "燃料泵等熵效率 / Fuel pump η",         "—"),
            ("turbine_efficiency_ox",  "氧侧涡轮效率 / Ox-side turbine η", "—"),
            ("turbine_efficiency_fuel","燃料侧涡轮效率 / Fuel-side turbine η", "—"),
            ("methane_thermo_dataset", "甲烷燃气热化学数据", ""),
            ("combustion_loss_model",  "主室燃烧损失模型",  ""),
            ("combustion_efficiency",  "历史温升折减系数（非 c* 效率）",  "—"),
        ],
    ),
    (
        "热力反馈初值（高级） / Thermo initial guesses",
        [
            ("chamber_temp_guess",  "主燃烧室温度猜测 / T_c guess",        "K"),
            ("chamber_gamma",       "比热比初值 / γ initial",             "—"),
            ("molar_mass",          "平均摩尔质量初值 / MW initial",          "kg/kmol"),
        ],
    ),
    (
        "预燃室(FFSC) / Preburners",
        [
            # 此参数为 pPB/pc；涡轮出口有热气喷注压损，不能等同于涡轮进出口压比。
            # 目标混合比模式下为求解初值，自由模式下为固定输入
            ("ox_preburner_pressure_ratio",
             "富氧预燃室 pPB/pc 初猜 / O-rich PB pressure seed",               "—"),
            ("fuel_preburner_pressure_ratio",
             "富燃预燃室 pPB/pc 初猜 / F-rich PB pressure seed",               "—"),
            # 注：涡轮出口热气压降比（=主喷注器压降比）只在「压力网络」组注册一次。
            # 旧版此处重复注册同一字段 → 两个可见输入框指向一个 key，前一个的编辑
            # 被静默忽略（V19 审查修复：同一 key 只允许一个控件）。
            ("ox_preburner_temp_limit",   "富氧预燃室运行温度上限 / T_lim O-rich",  "K"),
            ("fuel_preburner_temp_limit", "富燃预燃室运行温度上限 / T_lim F-rich",  "K"),
        ],
    ),
    (
        "预燃室设计(FFSC) / Preburner design",
        [
            # 指定出口温度的设计模式；运行上限独立验收，不代表材料寿命已验证。
            # 开启时由目标涡轮入口温度反求 MR；下面的"目标 O/F"成为外环初值。
            ("use_target_preburner_temp", "温度目标开关（历史兼容字段）", "—"),
            ("ox_preburner_temp_target", "富氧预燃室出口目标温度 / T_in O-rich", "K"),
            ("fuel_preburner_temp_target", "富燃预燃室出口目标温度 / T_in F-rich", "K"),
            ("use_target_preburner_mr", "混合比目标开关（历史兼容字段）", "—"),
            ("ox_preburner_mr_target", "富氧预燃室目标 O/F（温度模式下为初值）", "—"),
            ("fuel_preburner_mr_target", "富燃预燃室目标 O/F（温度模式下为初值）", "—"),
            ("fuel_split_to_ox_preburner", "燃料→富氧预燃室初始比例", "—"),
            ("ox_split_to_ox_preburner", "氧化剂→富氧预燃室初始比例", "—"),
        ],
    ),
    (
        "燃气发生器(GG) / Gas generator",
        [
            # GG 指定出口温度模式；温度上限为独立运行约束。
            ("use_target_gg_temp",  "GG 温度目标开关（历史兼容字段）", "—"),
            ("gg_temp_target",      "GG 出口目标温度 / GG T target",     "K"),
            ("gg_temp_limit",       "GG 运行温度上限 / GG T limit", "K"),
            ("gg_mr_target",        "GG 混合比 O/F（温度模式下为初值）",  "—"),
            ("gg_fraction_guess",   "GG 流量分数初猜 / GG flow fraction", "—"),
            ("gg_injector_dp_ratio","GG 喷注器压降比 ΔP/p_gg",           "—"),
            ("gg_turbine_backpressure_pa", "涡轮排气背压 / Turbine backpress.", "bar"),
            ("gg_exhaust_expansion_ratio", "排气喷管膨胀比 / Exhaust ε",  "—"),
            ("gg_mech_efficiency",  "轴系机械效率 / Mech. η",             "—"),
            ("injector_dp_ratio_ox",   "主喷注器压降比(LOX 液路) ΔP/pc",  "—"),
            ("injector_dp_ratio_fuel", "主喷注器压降比(燃料液路) ΔP/pc",  "—"),
        ],
    ),
    (
        "压力网络(FFSC) / Pressure network",
        [
            ("preburner_injector_dp_ratio", "预燃室喷注压降预算 ΔP/pPB / Preburner inj. budget", "—"),
            ("ox_turbine_exhaust_dp_ratio",   "富氧涡轮出口→主室热气压降预算 ΔP/pc / Ox hot-gas budget",   "—"),
            ("fuel_turbine_exhaust_dp_ratio", "富燃涡轮出口→主室热气压降预算 ΔP/pc / Fuel hot-gas budget", "—"),
        ],
    ),
    (
        "压力网络·公共 / Pressure network (shared)",
        [
            ("cooling_dp_pa",          "再生冷却给定压降预算 / Cooling ΔP budget",              "MPa"),
            ("valve_line_dp_ratio",    "支路基础管路/全开阀压降预算 ΔP/pc / Base line budget",      "—"),
        ],
    ),
    (
        # V19：冷却的循环闭合级开关留在左面板；通道几何/膜冷却/型面等推力室
        # 详细参数全部移入「推力室设计」工作区（同一 input_edits 数据源）。
        "再生冷却·循环闭合 / Regen (cycle closure)",
        [
            ("cooling_bartz_mode",  "冷却模式 1=统一准一维 0=Q与压降预算", "—"),
            ("cooling_heat_load",   "再生冷却给定吸热预算 / Heat budget", "MW"),
            ("coolant_cp",          "冷却剂常比热（旧模型兼容） / Legacy cp", "J/(kg·K)"),
            ("cooling_dp_auto",     "冷却压降开关（历史兼容，不再控制计算）", "—"),
            ("cooling_marching_enable", "沿程显示开关（历史兼容）", "—"),
        ],
    ),
    (
        "求解与节流 / Solver & throttle",
        [
            ("nonlinear_solver", "非线性求解方法 / Nonlinear solver", ""),
            ("cooling_grid_points", "冷却沿程网格节点数 / Cooling grid nodes", "—"),
            ("throttle_min", "最低节流比例", "—"),
            ("throttle_steps", "节流扫描点数", "—"),
        ],
    ),
    (
        "涡轮泵转速 / Turbopump speed",
        [
            ("lox_pump_speed_rpm_override",
             "液氧泵指定转速 (0=按 nq 估算) / LOX speed override", "rpm"),
            ("lox_pump_specific_speed_target",
             "液氧泵比转速 nq 目标 / LOX nq target",                      "rpm·(m³/s)^½/m^¾"),
            ("lox_pump_n_stages",
             "LOX 泵级数 / LOX stages",                             "—"),
            ("lox_pump_head_coefficient_psi",
             "LOX 泵压头系数 ψ / LOX ψ",                            "—"),
            ("fuel_pump_speed_rpm_override",
             "燃料泵指定转速 (0=按 nq 估算) / Fuel speed override",  "rpm"),
            ("fuel_pump_specific_speed_target",
             "燃料泵比转速 nq 目标 / Fuel nq target",                      "rpm·(m³/s)^½/m^¾"),
            ("fuel_pump_n_stages",
             "燃料泵级数 / Fuel stages",                             "—"),
            ("fuel_pump_head_coefficient_psi",
             "燃料泵压头系数 ψ / Fuel ψ",                            "—"),
            ("pump_tip_speed_limit_ms",
             "泵叶尖速度上限 / Pump u_tip max",                     "m/s"),
        ],
    ),
    (
        "涡轮气动设计 / Turbine aero design",
        [
            ("turb_ox_u_over_c_target",
             "富氧涡轮 U/C0 目标 / O-rich U/C0 target",   "—"),
            ("turb_ox_reaction_degree",
             "富氧涡轮反动度 / O-rich reaction",            "—"),
            ("turb_ox_n_stages_user",
             "富氧涡轮级数 (0=自动) / O-rich stages",       "—"),
            ("turb_ox_u_max_ms",
             "富氧涡轮 U_max / O-rich U max",               "m/s"),
            ("turb_fuel_u_over_c_target",
             "富燃涡轮 U/C0 目标 / F-rich U/C0 target",   "—"),
            ("turb_fuel_reaction_degree",
             "富燃涡轮反动度 / F-rich reaction",            "—"),
            ("turb_fuel_n_stages_user",
             "富燃涡轮级数 (0=自动) / F-rich stages",       "—"),
            ("turb_fuel_u_max_ms",
             "富燃涡轮 U_max / F-rich U max",               "m/s"),
        ],
    ),
    # V19：原「燃烧室几何」「燃烧室壁面」「推力室壁材料」三组已整体迁入
    # 顶层「推力室设计」工作区（字段仍注册进同一 input_edits，求解读数不变）。
    (
        "涡轮材料 / Turbine materials",
        [
            # 富氧侧：必须 oxygen_compatible（含 Co > 5% 禁用，ASTM G124）
            ("turb_ox_rotor_material",
             "富氧涡轮动叶（限富氧兼容材料）",  ""),
            ("turb_ox_stator_material",
             "富氧涡轮静叶（限富氧兼容材料）",  ""),
            ("turb_ox_disk_material",
             "富氧涡轮轮盘（限富氧兼容材料）",  ""),
            ("turb_ox_housing_material",
             "富氧涡轮壳体（限富氧兼容材料）",  ""),
            # 富燃侧：可用含 Co 高温合金
            ("turb_fuel_rotor_material",
             "富燃涡轮动叶",                    ""),
            ("turb_fuel_stator_material",
             "富燃涡轮静叶",                    ""),
            ("turb_fuel_disk_material",
             "富燃涡轮轮盘",                    ""),
            ("turb_fuel_housing_material",
             "富燃涡轮壳体",                    ""),
        ],
    ),
]


OVERVIEW_GROUPS = [
    # "总体性能"小卡片已移除——推力/比冲/流量/燃烧室温度均已在顶部大KPI卡片显示，无需重复
    ("流量分配 / Flow Split", [
        ("mdot_fuel_kg_s", "燃料总流量",   "kg/s"),
        ("mdot_ox_kg_s",   "液氧总流量",   "kg/s"),
        ("fuel_split_to_ox_preburner", "燃料→富氧预燃室比例", "—"),
        ("ox_split_to_ox_preburner",   "氧化剂→富氧预燃室比例", "—"),
        ("ox_preburner_mr",            "富氧预燃室 O/F 混合比", "—"),
        ("phi_ox_preburner",           "富氧预燃室当量比 φ", "—"),
        ("fuel_preburner_mr",          "富燃预燃室 O/F 混合比", "—"),
        ("phi_fuel_preburner",         "富燃预燃室当量比 φ", "—"),
    ]),
    # 按 泵↔泵 / 涡轮↔涡轮 / 功率比↔功率比 顺序排列，左右两列自然对仗
    ("功率平衡 / Power Balance", [
        ("ox_pump_power_MW",             "液氧泵功率",         "MW"),
        ("fuel_pump_power_MW",           "燃料泵功率",         "MW"),
        ("ox_turbine_power_MW",          "富氧预燃室涡轮功率", "MW"),
        ("fuel_turbine_power_MW",        "富燃预燃室涡轮功率", "MW"),
        ("ox_side_power_balance_ratio",  "液氧侧功率平衡比",   "—"),
        ("fuel_side_power_balance_ratio","燃料侧功率平衡比",   "—"),
    ]),
    # 预燃室温度已在顶部大KPI卡片显示；压力比在部件结果→涡轮卡片显示；此处只保留求解诊断信息
    ("求解状态 / Solver Status", [
        ("solver_iterations",       "内层累计接受步数（全部温度外环与延续阶段）", "—"),
        ("solver_function_evaluations", "循环残差求值次数（含差分与试探）", "—"),
        ("solver_jacobian_evaluations", "雅可比计算次数", "—"),
        ("solver_rejected_steps", "未接受试探次数", "—"),
        ("solver_inner_solves",     "内层 solve_cycle 累计调用次数",                     "—"),
        ("solver_iterations_last",  "末次内层求解接受步数",                       "—"),
        ("tc_outer_iterations",     "最终温度目标轮次的热反馈迭代数",                     "—"),
        ("tc_outer_iterations_total", "全部热反馈累计迭代数",                              "—"),
        ("tc_outer_residual_K",     "Tc Picard 末轮残差",                                  "K"),
        ("solver_residual_norm",    "归一化残差范数",                                      "—"),
        ("solver_warning",          "求解警告标志",                                        "—"),
        ("thermo_source",           "热力学库（1=Cantera）",                              "—"),
    ]),
]


OVERVIEW_GROUPS_GG = [
    ("流量分配 / Flow Split (GG)", [
        ("mdot_fuel_kg_s",      "燃料总流量",             "kg/s"),
        ("mdot_ox_kg_s",        "液氧总流量",             "kg/s"),
        ("mdot_chamber_kg_s",   "主燃烧室通流（不含 GG）", "kg/s"),
        ("chamber_mixture_ratio","主燃烧室 O/F 混合比",    "—"),
        ("gg_mdot_kg_s",        "燃气发生器流量",          "kg/s"),
        ("gg_fraction",         "GG 流量分数 α_gg",       "—"),
        ("gg_mr",               "燃气发生器 O/F 混合比",   "—"),
        ("gg_phi",              "燃气发生器当量比 φ",      "—"),
    ]),
    ("功率平衡 / Power Balance (GG)", [
        ("ox_pump_power_MW",        "液氧泵功率",           "MW"),
        ("fuel_pump_power_MW",      "燃料泵功率",           "MW"),
        ("gg_turbine_power_MW",     "GG 涡轮功率（毛）",     "MW"),
        ("gg_turbine_power_delivered_MW", "GG 涡轮输出（×机械效率）", "MW"),
        ("gg_mech_efficiency",      "轴系机械效率",          "—"),
        ("power_balance_ratio",     "总功率平衡比",          "—"),
    ]),
    ("开式循环特征 / Open-cycle metrics", [
        ("gg_pressure_MPa",        "GG 压力",                "MPa"),
        ("gg_turbine_pi_t",        "GG 涡轮压比 π_t",        "—"),
        ("gg_turbine_exhaust_T_K", "涡轮排气温度",           "K"),
        ("thrust_gg_exhaust_N",    "排气喷管推力(海平面)",    "N"),
        ("Isp_gg_exhaust_s",       "排气喷管比冲(海平面)",    "s"),
        ("Isp_main_flow_s",        "主室流比冲(海平面)",      "s"),
        ("Isp_gg_penalty_s",       "开式循环比冲代价 ΔIsp",  "s"),
    ]),
    ("求解状态 / Solver Status", [
        ("solver_iterations",       "内层累计接受步数（全部温度外环与延续阶段）", "—"),
        ("solver_function_evaluations", "循环残差求值次数（含差分与试探）", "—"),
        ("solver_jacobian_evaluations", "雅可比计算次数", "—"),
        ("solver_rejected_steps", "未接受试探次数", "—"),
        ("solver_inner_solves",     "内层 solve_cycle 累计调用次数",                "—"),
        ("tc_outer_iterations",     "最终温度目标轮次的热反馈迭代数",                "—"),
        ("tc_outer_iterations_total", "全部热反馈累计迭代数",                         "—"),
        ("tc_outer_residual_K",     "Tc Picard 末轮残差",                             "K"),
        ("gg_temp_outer_error_K",   "GG 温度目标外环残差",                            "K"),
        ("solver_residual_norm",    "归一化残差范数",                                 "—"),
        ("solver_warning",          "求解警告标志",                                   "—"),
        ("thermo_source",           "热力学库（1=Cantera）",                         "—"),
    ]),
]


COMPONENT_ORDER = [
    "ox_pump", "fuel_pump", "cooling_channel", "cooling_marching",
    "ox_preburner", "ox_preburner_turbine",
    "fuel_preburner", "fuel_preburner_turbine",
    "injector", "main_chamber", "nozzle"
]


COMPONENT_LABELS = {
    "ox_pump":               "液氧泵",
    "fuel_pump":             "燃料泵",
    "cooling_channel":       "再生冷却通道",
    "cooling_marching":      "历史独立冷却后处理（旧记录）",
    "ox_preburner":          "富氧预燃室",
    "ox_preburner_turbine":  "富氧涡轮 / 液氧泵涡轮",
    "fuel_preburner":        "富燃预燃室",
    "fuel_preburner_turbine":"富燃涡轮 / 燃料泵涡轮",
    "injector":              "喷注器",
    "main_chamber":          "主燃烧室",
    "nozzle":                "喷管",
    # ── V20：燃气发生器循环部件 ──
    "gas_generator":         "燃气发生器（富燃）",
    "gg_turbine":            "GG 涡轮（单轴驱动双泵）",
    "gg_turbine_ox":         "GG 涡轮（LOX 轴）",
    "gg_turbine_fuel":       "GG 涡轮（燃料轴）",
    "gg_exhaust_nozzle":     "GG 排气喷管（开式直排）",
}


COMPONENT_GROUPS = [
    (
        "一、推进剂供应系统",
        "（涡轮泵 + 预燃室/燃气发生器 + 再生冷却）",
        ["ox_pump", "fuel_pump",
         "ox_preburner", "ox_preburner_turbine",
         "fuel_preburner", "fuel_preburner_turbine",
         # V20 GG 循环部件（FFSC 结果中不存在，自动跳过）
         "gas_generator", "gg_turbine", "gg_turbine_ox", "gg_turbine_fuel",
         "cooling_channel", "cooling_marching"],
    ),
    (
        "二、推力室",
        "（喷注器 + 主燃烧室 + 喷管；GG 模式含开式排气喷管）",
        ["injector", "main_chamber", "nozzle", "gg_exhaust_nozzle"],
    ),
]


KEY_LABELS = {
    "rho_in_kgm3": "入口密度 [kg/m³]",
    "rho_out_kgm3": "出口密度 [kg/m³]",
    "rho_kgm3": "密度 [kg/m³]",
    "p_in_MPa": "入口压力 [MPa]",
    "p_out_MPa": "出口压力 [MPa]",
    "p_in_bar": "入口压力 [bar]",
    "p_out_bar": "出口压力 [bar]",
    "delta_p_Pa": "压升 [Pa]",
    "power_W": "泵功率 [MW]",
    "T_out_K": "出口温度 [K]",
    "heat_load_W": "吸热量 [MW]",
    "delta_T_K": "温升 [K]",
    "delta_T_isentropic_K": "可逆压缩温升 [K]",
    "delta_T_loss_K": "损失热化温升 [K]",
    "mdot_total_kgs": "总流量 [kg/s]",
    "mdot_fuel_kgs": "燃料流量 [kg/s]",
    "mdot_ox_kgs": "氧化剂流量 [kg/s]",
    "fuel_fraction": "燃料质量分数",
    "phi": "当量比 φ",
    "mixture_ratio_OF": "混合比 O/F",
    "ox_preburner_mr": "富氧预燃室 O/F",
    "fuel_preburner_mr": "富燃预燃室 O/F",
    "T_combustion_K": "燃烧温度 [K]",
    "T_exhaust_K": "涡轮出口温度 [K]",
    "T_in_K": "入口温度 [K]",
    "p_in_Pa": "涡轮入口压力 [MPa]",
    "p_out_Pa": "涡轮出口压力 [MPa]",
    "turbine_pressure_ratio": "涡轮压比 πt=p_in/p_out",
    "pressure_recovery_ratio": "压力恢复比 p_out/p_in",
    "ox_turbine_exhaust_dp_ratio": "富氧涡轮出口到主燃烧室压降比 [-]",
    "fuel_turbine_exhaust_dp_ratio": "富燃涡轮出口到主燃烧室压降比 [-]",
    "isentropic_drop_Jkg": "等熵焓降 [J/kg]",
    "actual_drop_Jkg": "实际焓降 [J/kg]",
    "actual_temp_drop_K": "实际温降 [K]",
    "turbine_power_W": "涡轮功率 [MW]",
    "turbine_power_MW": "涡轮功率 [MW]",
    "eta_turbine": "涡轮效率",
    "h_in_Jkg": "入口混合焓 [J/kg]",
    "h_combustion_Jkg": "燃烧后焓 [J/kg]",
    "h_out_Jkg": "出口焓 [J/kg]",
    "cp_Jkgk": "定压比热 cp [J/(kg·K)]",
    "cp_Jgk": "定压比热 cp [J/(kg·K)]",
    "gamma": "比热比 γ",
    "T_chamber_K": "主燃烧室温度 [K]",
    "molar_mass_kg_per_kmol": "平均摩尔质量 [kg/kmol]",
    "h_chamber_Jkg": "燃烧室焓 [J/kg]",
    "thrust_N": "推力 [N]",
    "Isp_s": "比冲 [s]",
    "Ve_ms": "有效排气速度 [m/s]",
    "exit_velocity_ms": "出口速度 [m/s]",
    "c_star_ms": "特征速度 c* [m/s]",
    "Cf": "推力系数 Cf",
    "Cf_momentum": "动量推力系数",
    "Cf_pressure": "压力推力系数",
    "At_m2": "喉部面积 [m²]",
    "Ae_m2": "出口面积 [m²]",
    "mach_exit": "出口马赫数",
    "exit_mach": "出口马赫数",
    "p_exit_Pa": "出口压力 [Pa]",
    "exit_pressure_Pa": "出口压力 [Pa]",
    "p_amb_Pa": "环境压力 [Pa]",
    "T_exit_K": "出口温度 [K]",
    "gamma_exit": "出口比热比 γ",
    "expansion_ratio": "面积膨胀比 [-]",
    # ── 泵部件额外字段 ─────────────────────────────────────
    "delta_p_MPa":          "压升 [MPa]",
    "delta_p_bar":          "压升 [bar]",
    "power_MW":             "泵功率 [MW]",
    "eta":                  "泵等熵效率 [-]",
    "head_m":               "扬程 [m]",
    "head_m_per_stage":     "单级扬程 [m]",
    "volume_flow_m3s":      "体积流量 [m³/s]",
    "volume_flow_Ls":       "体积流量 [L/s]",
    "volume_flow_m3h":      "体积流量 [m³/h]（对标 CFTurbo）",
    "nr_rpm":               "转速 [rpm]",
    "nr_rad_s":             "转速 [rad/s]",
    "n_stages":             "泵级数",
    "specific_speed_Ns":    "比转速 nq [EU, rpm·√(m³/s)/m^¾]",
    "eta_from_Ns_curve":    "Ns 曲线估算效率 [-]",
    "eta_deviation":        "效率偏差（输入 - 曲线）[-]",
    # ── 再生冷却通道 ───────────────────────────────────────
    "heat_load_MW":         "吸热量 [MW]",
    "mode":                 "冷却模型模式",
    "Q_cc_MW":              "燃烧室段吸热 [MW]",
    "Q_conv_MW":            "收缩段吸热 [MW]",
    "Q_throat_MW":          "喉部带吸热 [MW]",
    "Q_noz_MW":             "喷管段吸热 [MW]",
    "q_throat_MW_m2":       "喉部热流密度 [MW/m²]",
    "h_g_throat_W_m2_K":    "喉部燃气侧换热系数 [W/(m²·K)]",
    "sigma":                "Bartz 修正系数 σ [-]",
    "D_throat_m":           "喉部直径 [m]",
    "r_throat_m":           "喉部半径 [m]",
    "A_throat_m2":          "喉部面积 [m²]",
    "r_cc_m":               "燃烧室半径 [m]",
    "L_cc_m":               "燃烧室等效长度 [m]",
    "A_wetted_total_m2":    "冷却受热面积 [m²]",
    "T_aw_throat_K":        "喉部绝热壁温 [K]",
    "T_wall_cold_K":        "冷却侧壁温 [K]",
    "T_wall_hot_K":         "燃气侧壁温 [K]",
    "cp_avg_J_kg_K":        "冷却剂平均 cp [J/(kg·K)]",
    "delta_P_MPa":          "冷却通道压降 [MPa]",
    "P_in_Pa":              "冷却入口压力 [MPa]",
    "P_out_Pa":             "冷却出口压力 [MPa]",
    "G_throat_kg_m2_s":     "喉部质量通量 [kg/(m²·s)]",
    "mu_Pa_s":              "燃气黏度 [Pa·s]",
    "Pr":                   "Prandtl 数 Pr [-]",
    # ── 喷管 ──────────────────────────────────────────────
    "thrust_vac_N":         "真空推力 [N]",
    "Isp_vac_s":            "真空比冲 [s]",
    # ── 预燃室 ────────────────────────────────────────────
    "mdot_total_kgs":       "总质量流量 [kg/s]",
    "mdot_fuel_kgs":        "燃料流量 [kg/s]",
    "mdot_ox_kgs":          "氧化剂流量 [kg/s]",
    # V5.3.6 新增：预燃室喷注器压降
    "injector_dp_ratio":      "预燃室喷注器压降比 ΔP/P_PB [-]",
    "injector_dp_Pa":         "预燃室喷注器压降 [MPa]",
    "injector_dp_MPa":        "预燃室喷注器压降 [MPa]",
    "injector_dp_bar":        "预燃室喷注器压降 [bar]",
    "preburner_pressure_Pa":  "预燃室压力 [MPa]",
    "preburner_pressure_MPa": "预燃室压力 [MPa]",
    # ── 主燃烧室几何 ──────────────────────────────────────
    "cc_throat_area_m2"     : "喉部面积 At [m²]",
    "cc_throat_diameter_mm" : "喉部直径 Dt [mm]",
    "cc_area_m2"            : "燃烧室截面积 Acc [m²]",
    "cc_diameter_m"         : "燃烧室直径 Dcc [m]",
    "cc_diameter_mm"        : "燃烧室直径 Dcc [mm]",
    "cc_contraction_ratio"  : "收缩比 ε_c = Acc/At [-]",
    "cc_mach_number"        : "室内截面马赫数 M_cc [-]",
    "cc_l_star_m"           : "特征长度 L* [m]",
    "cc_volume_L"           : "燃烧室容积 V_cc [L]",
    "cc_length_m"           : "圆柱段长度 L_cc [m]",
    "cc_length_mm"          : "圆柱段长度 L_cc [mm]",
    "cc_wall_thickness_mm"  : "分层壁厚合计 t_wall [mm]（内衬+通道+外壳，与几何页同口径）",
    "cc_wall_liner_mm"      : "├ 内衬厚（传热限定厚）[mm]",
    "cc_wall_channel_mm"    : "├ 冷却通道深 [mm]",
    "cc_wall_jacket_mm"     : "└ 外壳厚（Barlow 对冷却剂压力）[mm]",
    "dist_mode"             : "通道沿程分布表模式（1=启用）",
    # ── 总和特性参数 + 完整几何（chamber_sizing 新增）──────
    "cc_volume_m3"               : "燃烧室容积 V_cc [m³]",
    "cc_convergent_length_mm"    : "收缩段长度 L_conv [mm]",
    "cc_convergent_volume_L"     : "收缩段锥台体积 V_conv [L]",
    "cc_cyl_volume_L"            : "圆柱段体积 V_cyl [L]",
    "cc_total_length_mm"         : "表观室长 L_cyl+L_conv [mm]",
    "cc_LD_ratio_cyl"            : "长径比 L_cyl/D_cc [-]",
    "cc_LD_ratio_total"         : "长径比 (L_cyl+L_conv)/D_cc [-]",
    "cc_residence_time_ms"       : "推进剂平均停留时间 t_s [ms]",
    "cc_residence_time_s"        : "推进剂平均停留时间 t_s [s]",
    "cc_mass_flux_chamber_kg_m2s": "燃烧室流量密度 ṁ/A_cc [kg/(m²·s)]",
    "cc_mass_flux_throat_kg_m2s" : "喉部流量密度 ṁ/A_t [kg/(m²·s)]",
    "cc_chamber_gas_density_kgm3": "室内燃气密度 ρ_c [kg/m³]",
    "cc_conv_half_angle_deg"     : "收缩段半锥角 β [°]",
    "cc_sound_speed_ms"          : "室内声速 a_c [m/s]",
    "cc_acoustic_1T_Hz"          : "一阶切向声学模态 1T [Hz]（稳定性筛查）",
    "cc_acoustic_1L_Hz"          : "一阶纵向声学模态 1L [Hz]",
    # ── 再生冷却沿程 marching（V15 后处理）──────────────
    "n_stations"                 : "marching 站数 [-]",
    "Q_total_MW"                 : "总热载荷（沿程积分）[MW]",
    "T_coolant_out_K"            : "冷却剂出口温度 [K]",
    "delta_P_friction_MPa"       : "通道摩擦压降 [MPa]",
    "delta_P_minor_MPa"          : "集流环/弯头局部压降 [MPa]",
    "T_wall_hot_max_K"           : "热面壁温峰值 [K]",
    "x_T_wall_max_mm"            : "壁温峰值位置 x [mm]",
    "x_throat_mm"                : "喉部位置 x [mm]",
    "q_max_MW_m2"                : "热流峰值 [MW/m²]",
    "x_q_max_mm"                 : "热流峰值位置 x [mm]",
    "wall_over_limit_fraction"   : "壁温超限站点占比 [-]",
    "wall_temp_limit_K"          : "热面壁温限值 [K]",
    "cover_eps"                  : "再生冷却覆盖面积比 ε [-]",
    "film_cooling_on"            : "膜冷却开关 [-]",
    "film_cooling_fraction"      : "膜冷却流量比例 [-]",
    "V_coolant_max_ms"           : "冷却剂最高流速（喉部）[m/s]",
    "T_wall_cold_max_K"          : "冷面壁温峰值（结焦校核 <950K）[K]",
    "hydraulic_infeasible"       : "通道水力不可行标志（1=不可行）",
    "film_mdot_kgs"              : "膜冷却流量 [kg/s]",
    "Q_noz_ext_MW"               : "喷管延伸段热载荷 [MW]",
    "vs_point_Q_rel_percent"     : "对单点模型 Q 偏差 [%]",
    "vs_point_T_out_delta_K"     : "对单点模型 T_out 偏差 [K]",
    "vs_point_dp_delta_MPa"      : "对单点模型 Δp 偏差 [MPa]",
    "delta_P_hydraulic_MPa"      : "通道水力压降实算 [MPa]",
    "delta_P_source"             : "压降来源（1=水力实算 0=固定输入）",
    "channel_height_mm"          : "冷却通道深度 [mm]",
    "channel_length_m"           : "冷却通道沿程长度 [m]",
    "channel_friction_factor"    : "通道摩擦系数 f [-]",
    # ── 喷注器 ────────────────────────────────────────────
    "face_diameter_m":      "喷注面直径 [m]",
    "face_area_m2":         "喷注面面积 [m²]",
    "num_elements":         "喷注元件数",
    "element_spacing_mm":   "元件间距 [mm]",
    "dp_ox_Pa":             "氧化剂压降 [MPa]",
    "dp_ox_MPa":            "氧化剂压降 [MPa]",
    "dp_ox_ratio":          "氧化剂压降比 ΔP/pc [-]",
    "dp_fuel_Pa":           "燃料压降 [MPa]",
    "dp_fuel_MPa":          "燃料压降 [MPa]",
    "dp_fuel_ratio":        "燃料压降比 ΔP/pc [-]",
    "v_ox_ms":              "氧化剂喷射速度 [m/s]",
    "v_fuel_ms":            "燃料喷射速度 [m/s]",
    "d_ox_mm":              "氧孔直径 d_ox [mm]",
    "d_post_mm":            "氧化剂 post 外径 [mm]",
    "delta_fuel_mm":        "燃料环缝宽度 δ [mm]",
    "d_outer_mm":           "元件外径 [mm]",
    "mdot_ox_per_element_kgs":   "单元件氧化剂流量 [kg/s]",
    "mdot_fuel_per_element_kgs": "单元件燃料流量 [kg/s]",
    "momentum_flux_ratio_J":     "动量通量比 J [-]",
    "swirl_number_ox":           "旋流数 S_ox [-]",
    "swirl_number_fuel":         "旋流数 S_fuel [-]",
    "cd_ox":                     "氧化剂流量系数 Cd [-]",
    "cd_fuel":                   "燃料流量系数 Cd [-]",
    "face_heat_flux_MWm2":       "喷注面热流密度 [MW/m²]",
    # ── V10：涡轮 CFTurbo 输入参数 ────────────────────────
    "mass_flow_kgs":             "涡轮质量流量 [kg/s]",
    "cp_in_Jkgk":                "入口工质 cp [J/(kg·K)]",
    "gamma_in":                  "入口工质 γ [-]",
    "molar_mass_in":             "入口工质摩尔质量 [kg/kmol]",
    # ── V10：涡轮速度三角形 & 几何 ────────────────────────
    "C0_spouting_ms":            "等熵喷出速度 C₀ [m/s]",
    "U_mean_target_ms":          "节圆速度 U 目标 [m/s]",
    "U_mean_actual_ms":          "节圆速度 U 实际 [m/s]",
    "D_mean_m":                  "转子中径 D [m]",
    "u_over_c_target":           "速比 U/C₀ 目标 [-]",
    "u_over_c_actual":           "速比 U/C₀ 实际 [-]",
    "n_stages_recommended":      "推荐级数 [-]",
    "reaction_degree":           "反动度 R [-]",
    "shaft_speed_rpm":           "涡轮/泵 共轴转速 [rpm]",
    "u_max_ms":                  "节圆速度上限 U_max [m/s]",
    "u_mean_ok":                 "节圆速度校核（1=合格）",
    "t_in_over_limit":           "入口温度超过上限 [K]",
    # ── V10：涡轮材料校核 ────────────────────────────────
    "rotor_material_key":        "动叶材料",
    "rotor_material_name":       "动叶材料",
    "rotor_sigma_yield_T_MPa":   "动叶 σy(T_in) [MPa]",
    "rotor_sigma_centrif_MPa":   "动叶离心应力 σ=ρU² [MPa]",
    "rotor_safety_factor":       "动叶安全系数 SF [-]",
    # ── V10：泵叶尖/汽蚀诊断 ─────────────────────────────
    "u_tip_est_ms":              "叶轮叶尖速度 u_tip [m/s]",
    "tip_speed_limit_ms":        "叶尖速度上限 [m/s]",
    "impeller_sigma_yield_T_MPa":"叶轮 σy(T_in) [MPa]",
    "impeller_sigma_centrif_MPa":"叶轮离心应力 σ=ρ·u_tip² [MPa]",
    "impeller_safety_factor":    "叶轮安全系数 SF [-]",
    "impeller_oxygen_compatible":"叶轮材料氧兼容（1=是）",
    "tip_speed_ok":              "叶尖速度校核（1=合格）",
    "d_impeller_est_m":          "叶轮直径估算 [m]",
    "psi_head_coefficient":      "压头系数 ψ [-]",
    "npsh_available_m":          "可用净正吸入压头 NPSHa [m]",
    "suction_specific_speed_Nss":"吸入比转速 Nss [US 制: rpm·√gpm/ft^¾]",
    "suction_specific_speed_nss_eu":"吸入比转速 nss [EU 制: rpm·√(m³/s)/m^¾，对标 CFTurbo]",
    "thoma_cavitation_number_sigma":"Thoma 空化数 σ = NPSHa/H [-]",
    "boost_npsha_ox_m":          "液氧增压泵 NPSHa（贮箱处）[m]",
    "boost_npsha_fuel_m":        "甲烷增压泵 NPSHa（贮箱处）[m]",
    "boost_nss_ox_eu":           "液氧增压泵吸入比转速 nss [rpm·√(m³/s)/m^¾]",
    "boost_nss_fuel_eu":         "甲烷增压泵吸入比转速 nss [rpm·√(m³/s)/m^¾]",
    "boost_pump_speed_assumed_rpm":"增压泵假定转速 [rpm]",
    "boost_pump_ox_power_MW":    "液氧增压泵功率 [MW]",
    "boost_pump_fuel_power_MW":  "甲烷增压泵功率 [MW]",
    "boost_pump_total_power_MW": "增压泵总功率 [MW]",
    "boost_pump_efficiency":     "增压泵效率 [-]",
    "tank_pressure_bar":         "贮箱压力 [bar]",
    "boost_out_pressure_bar":    "增压泵出口压力 [bar]",
    "main_pump_inlet_pressure_bar":"主泵入口压力 [bar]",
    "boost_pump_dp_bar":         "增压泵压升 ΔP [bar]",
    # ── 再生冷却通道 ────────────────────────────────────
    "h_coolant_W_m2K":           "冷却剂侧换热系数 h_c [W/(m²·K)]",
    "coolant_velocity_ms":       "冷却剂流速 [m/s]",
    "coolant_Re":                "冷却剂雷诺数 Re [-]",
    "coolant_Pr":                "冷却剂普朗特数 Pr [-]",
    "channel_D_h_mm":            "通道水力直径 D_h [mm]",
    "channel_A_flow_cm2":        "通道流通总面积 [cm²]",
    "n_channels":                "冷却通道数 [-]",
    "T_coolant_throat_K":        "喉部处冷却剂温度 [K]",
    "liner_thickness_m":         "内衬壁厚 [m]",
    "wall_conductivity_WmK":     "壁面导热系数 [W/(m·K)]",
    # ── 主燃烧室 / 喷管 补充 ─────────────────────────────
    "T_chamber_ideal_K":         "理想平衡燃烧温度（η=1）[K]",
    "combustion_efficiency":     "历史温升折减系数（非 c* 效率）[-]",
    "Cf_vac":                    "真空推力系数 Cf_vac [-]",
    "Cf_sl":                     "海平面推力系数 Cf_sl [-]",
    "flow_model":                "喷管流动模型 (0=冻结/1=平衡/-1=理想气体)",
}


STATE_LABELS = {
    "fuel_tank":               "燃料储箱",
    "lox_tank":                "液氧储箱",
    "lox_pump_inlet":          "液氧泵入口（管路后）",
    "fuel_pump_inlet":         "燃料泵入口（管路后）",
    "lox_after_pump":          "液氧泵出口",
    "fuel_after_pump":         "燃料泵出口",
    "fuel_after_cooling":      "燃料再生冷却后",
    "ox_turbine_exhaust":      "富氧涡轮出口",
    "fuel_turbine_exhaust":    "富燃涡轮出口",
    "ox_preburner_outlet":     "富氧预燃室出口（涡轮入口）",
    "fuel_preburner_outlet":   "富燃预燃室出口（涡轮入口）",
    "main_chamber":            "主燃烧室",
}


# Labels, units and tooltips shared with the desktop chamber design inputs.
CHAMBER_INPUTS = {
    'chamber_contraction_ratio': ('燃烧室收缩比 Acc/At', '—', ''),
    'chamber_l_star_m': ('特征长度 L*', 'm', ''),
    'chamber_conv_half_angle_deg': ('收缩段半锥角 β', '°', 'V19 起全链同口径：室分层几何 / 喷管轮廓 / marching / CSV'),
    'nozzle_bell_fraction': ('钟形长度比（0.80=80% bell）', '—', 'V19 新输入：旧版恒 0.80 不可改'),
    'cooling_cover_eps': ('再生冷却覆盖面积比（0=全喷管）', '—', '如填 8 表示再生冷却只到 ε=8，其外为延伸段（暂无热模型）'),
    'cooling_channel_auto_size': ('自动定通道深 1/0', '—', ''),
    'cooling_target_velocity_ms': ('目标冷却剂流速', 'm/s', ''),
    'cooling_n_channels': ('通道数 N', '—', ''),
    'cooling_channel_width_m': ('通道宽 w（喉部）', 'mm', ''),
    'cooling_channel_height_m': ('通道深 h', 'mm', ''),
    'cooling_wall_thickness_m': ('内衬热壁厚 t', 'mm', ''),
    'cooling_htc_enhancement': ('喉部曲率换热强化因子', '—', ''),
    'cooling_channel_rel_roughness': ('相对粗糙度 ε/D_h', '—', ''),
    'cooling_channel_k_minor': ('集流环/弯头局部损失（动压头数）', '—', ''),
    'cooling_wall_temp_limit_K': ('热面壁温限值', 'K', ''),
    'film_cooling_fraction': ('膜冷却流量比例（0=关）', '—', '气膜源=富燃涡轮排气（FFSC 气-气膜），Grisson 流量标度效能模型'),
    'film_effectiveness_initial': ('膜初始效能 η₀', '—', ''),
    'film_decay_length_m': ('膜效能固定衰减长（0=流量标度）', 'm', ''),
    'cooling_gas_emissivity': ('燃气辐射发射率 ε_g', '—', 'V19 新输入：旧版字段不存在、恒 0.20'),
    'chamber_wall_allow_pa': ('内壁许用应力', 'MPa', ''),
    'chamber_wall_safety_factor': ('壁厚安全系数', '—', ''),
    'chamber_inner_wall_material': ('内壁（燃气侧·铜合金）', '', ''),
    'chamber_outer_wall_material': ('外壳（结构层·镍基）', '', ''),
}
