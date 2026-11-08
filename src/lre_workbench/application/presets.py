# -*- coding: utf-8 -*-
"""
engine_presets.py — 工程示例工况与参考机型资料

选定循环方式后，把左侧"发动机设计输入"面板一键填成对应参考机型的工况。
每个预设 = 基准 EngineInputs() 默认值 + overrides 覆盖字典：
    · overrides 只列与默认不同的字段（其余沿用 core.EngineInputs 默认）；
    · FFSC 示例采用有日期的公开推力，并显式选择不附加经验损失的焓平衡；
    · 示例包含建模假设与既有标定值，不代表厂商完整设计或实测复现；
    · ENGINE_REFERENCES 单独存放公开资料。只有有 preset_key 的条目才能加载，
      每个示例的推进剂、物性模型和适用范围必须显式匹配。

扩展方法：往 ENGINE_PRESETS 里加一个 dict 即可（GUI 下拉自动出现）：
    "tq15a_gg": {"label": "...", "cycle_type": "gg", "source": "...", "overrides": {...}}
"""

from __future__ import annotations

from typing import Any, Dict


ENGINE_PRESETS: Dict[str, Dict[str, Any]] = {

    # 仅加载参考示例时更新推力；EngineInputs 的兼容默认值和已有方案不迁移。
    # SpaceX, Introducing Starship V3, 2026-05-12: sea-level variant 250 tf.
    # 标准吨力换算不是性能精度声明：250 * 1000 * 9.80665 = 2451662.5 N。
    "raptor3_ffsc": {
        "label": "液氧／甲烷全流量补燃循环（250 吨力级设计示例）",
        "display_description": "参考构型：Raptor 系列全流量补燃循环。本示例用于总体概念设计，室压、氧燃质量比、部件效率及内部支路参数包含工程假设，未作为真实机型的完整参数集验证。",
        "cycle_type": "ffsc",
        "source": "工程示例：目标推力采用 SpaceX 2026-05-12 公布的海平面型 250 tf，换算为 2451.6625 kN；氧燃质量比 3.6、面积比 34.34 与 Sierra 2019-001b 历史报告一致，在本示例中采用，不代表已核实的 Raptor 3 值。33 MPa 室压和内部参数为项目假设，不代表完整实机设计或实测复现。既有方案不自动修改。",
        "overrides": {"thrust": 2451662.5, "combustion_loss_model": "adiabatic",
                      "chamber_inner_wall_material": "grcop42",
                      "wall_thermal_dataset_id": "grcop42_chen2023_lpbf_v1",
                      "methane_thermo_dataset": "gri30_highT",
                      "ox_preburner_temp_limit": 800.0,
                      "fuel_preburner_temp_limit": 900.0,
                      "ox_preburner_temp_limit_status": "provisional",
                      "fuel_preburner_temp_limit_status": "provisional"},
    },

    # GG 工程示例：保留既有标定，公开参考资料另列 ENGINE_REFERENCES。
    # 当前官网混合比为 2.9；本示例仍为原有 3.4，不能视为真实机型复现。
    # 以下全部为本示例的建模口径：
    #   thrust  84.6 tf = 829.64 kN（海平面）
    #   Pc      ≈10.1 MPa（TQ-12 系公开量级）
    #   氧燃质量比     3.4（甲烷 GG 典型）
    #   ε       15（海平面型，出口径约 0.95 m，与官网高度 2.2 m 自洽）
    #   η_comb  0.983 —— 标定值：使模型海平面比冲 = 293.0 s（官网实测口径）
    #   GG      单轴布局、温度目标 850 K（顶富燃涡轮材料极限）、背压 4 bar、排气 ε=4
    #   泵转速  override=0（按比转速自动反推；TQ-12 未公开转速）
    "tq12a_gg": {
        "label": "天鹊 TQ-12A 类 GG（液氧/甲烷 · 工程示例）",
        "cycle_type": "gg",
        "source": "工程示例：沿用 829.64 kN 目标推力、10.1 MPa 室压及 η_comb=0.983 既有标定；模型混合比为 3.4，蓝箭公开值为 2.9。内部参数属于模型假设，不代表厂商完整设计或实测复现。",
        "overrides": {
            "chamber_inner_wall_material": "grcop84",
            "wall_thermal_dataset_id": "grcop84_ellis2000_extruded_high_fe_v1",
            "thrust": 829.64e3,             # 84.6 tf 海平面
            "pc": 10.1e6,
            "of_ratio": 3.4,
            "expansion_ratio": 15.0,
            "target_isp": 293.0,            # 流量初猜用官网比冲
            "chamber_temp_guess": 3400.0,   # Pc=10 MPa 量级平衡温度初猜
            "combustion_efficiency": 0.983, # 标定：模型 Isp_sl → 293.0 s
            "cooling_dp_pa": 2.0e6,         # 10 MPa 级再生冷却典型压降
            # 泵转速：按 Ns 自动反推（Raptor 的 CFTurbo 锁定值不适用）
            "lox_pump_speed_rpm_override": 0.0,
            "fuel_pump_speed_rpm_override": 0.0,
            # 燃气发生器参数（与 core 默认一致，显式列出便于查阅）
            "gg_layout": "single_shaft",
            "use_target_gg_temp": 1.0,
            "gg_temp_target": 850.0,
            "gg_temp_limit": 950.0,
            "gg_mr_target": 0.30,
            "gg_fraction_guess": 0.02,
            "gg_injector_dp_ratio": 0.15,
            "gg_turbine_backpressure_pa": 0.40e6,
            "gg_exhaust_expansion_ratio": 4.0,
            "gg_mech_efficiency": 0.97,
            # 主燃烧室液-液喷注压降比
            "injector_dp_ratio_ox": 0.20,
            "injector_dp_ratio_fuel": 0.20,
        },
    },
    "merlin1d_gg": {
        "label": "Merlin 1D 类 GG（液氧/煤油替代工质 · 工程示例）",
        "cycle_type": "gg",
        "source": "工程示例：845 kN 为 SpaceX 公布的海平面最高推力量级；9.7 MPa 室压、氧燃质量比=2.36、面积比 16、入口温度、20 MW 冷却吸热量及支路参数均为本示例假设。煤油采用正十二烷替代工质，不代表真实 RP-1 或 Merlin 1D 实测性能。",
        "overrides": {
            "chamber_inner_wall_material": "grcop84",
            "wall_thermal_dataset_id": "grcop84_ellis2000_extruded_high_fe_v1",
            "propellant_pair": "lox_kerosene",
            "thrust": 845e3, "pc": 9.7e6, "of_ratio": 2.36,
            "expansion_ratio": 16.0, "target_isp": 282.0,
            "chamber_temp_guess": 3400.0, "combustion_efficiency": 0.983,
            "fuel_inlet_temp": 290.0, "ox_inlet_temp": 90.0,
            "cooling_bartz_mode": 0.0, "cooling_heat_load": 20e6,
            "cooling_dp_auto": 0.0, "cooling_dp_pa": 2e6,
            "cooling_marching_enable": 0.0,
            "lox_pump_speed_rpm_override": 0.0,
            "fuel_pump_speed_rpm_override": 0.0,
            "gg_layout": "single_shaft", "use_target_gg_temp": 1.0,
            "gg_temp_target": 850.0, "gg_temp_limit": 950.0,
            "gg_mr_target": 0.30, "gg_fraction_guess": 0.02,
            "gg_injector_dp_ratio": 0.15,
            "gg_turbine_backpressure_pa": 0.40e6,
            "gg_exhaust_expansion_ratio": 4.0, "gg_mech_efficiency": 0.97,
            "injector_dp_ratio_ox": 0.20, "injector_dp_ratio_fuel": 0.20,
        },
    },
}


_FALCON_GUIDE = "https://www.spacex.com/assets/media/falcon-users-guide-2025-05-09.pdf#page=19"
_STARSHIP_PAGE = "https://www.spacex.com/vehicles/starship/design"
_STARSHIP_V3_UPDATE = "https://new.spacex.com/updates"
_RAPTOR_TESTIMONY = "https://www.govinfo.gov/content/pkg/CHRG-114hhrg95320/pdf/CHRG-114hhrg95320.pdf#page=122"
_RAPTOR_PLUME_2019 = "https://www.faa.gov/sites/faa.gov/files/space/stakeholder_engagement/spacex_starship/Appendix_G_Exhaust_Plume_Calculations.pdf#page=1"
_NIST_FORCE_CONVERSION = "https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9"
_LANDSPACE_PAGE = "https://www.landspace.com/product.html?mao=1"
_LANDSPACE_FILING = "https://static.sse.com.cn/stock/disclosure/announcement/c/202512/002174_20251231_1QK0.pdf"

def _ffsc_project_parameter(name, value_si, note, *, classification="engineering_assumption", value_display=None):
    """Provenance for project choices; a code location is not external evidence."""
    item = {"name": name, "value_si": value_si, "classification": classification,
            "source_url": None, "source_date": None,
            "source_path": f"src/lre_workbench/domain/models.py · EngineInputs.{name}",
            "reviewed_on": "2026-11-05", "note": note}
    if value_display is not None:
        item["value_display"] = value_display
    return item


# Reference metadata has no solver input payload; surrogate examples are
# explicitly separated from the manufacturer's public engine facts.
ENGINE_REFERENCES: Dict[str, Dict[str, Any]] = {
    "raptor3_ffsc": {
        "name": "Raptor 3", "manufacturer": "SpaceX",
        "propellants": "LOX / CH₄（液氧 / 甲烷）",
        "cycle_type": "ffsc", "cycle_label": "FFSC 全流量补燃循环",
        "applicable": True, "preset_key": "raptor3_ffsc",
        "compatibility_reason": "可载入同推进剂、同循环类型的工程示例，供总体设计研究。",
        "shaft_note": "参考构型为全流量补燃循环；本项目的机组参数与供给系统仍需独立定义和验证。",
        "reference_facts": [
            {"label": "系列推进剂与循环架构", "value": "液氧／甲烷；全流量补燃循环", "source_url": _RAPTOR_TESTIMONY,
             "source_title": "Jeffery Thornburg（SpaceX）国会书面陈述", "source_date": "2015-06-26", "checked_on": "2026-11-05",
             "source_locator": "正文第 118 页／PDF 第 122 页，Raptor 小节；听证会 2015-06-26，汇编印行于 2016 年",
             "scope": "支持当时 Raptor 的推进剂与循环类型，不支持 Raptor 3 的具体性能或内部参数。"},
            {"label": "Raptor 3 海平面型公开推力", "value": "250 tf（约 2452 kN）", "source_url": _STARSHIP_V3_UPDATE,
             "source_title": "SpaceX · Introducing Starship V3", "source_date": "2026-05-12", "checked_on": "2026-11-05",
             "source_locator": "Raptor 3 Change Highlights，第 1 段",
             "scope": "该日期公开的海平面型名义推力；原文未附完整设计点、入口状态、配套室压或误差范围。"},
            {"label": "Raptor 3 真空型公开推力", "value": "275 tf（独立真空型；不用于本示例推力输入）", "source_url": _STARSHIP_V3_UPDATE,
             "source_title": "SpaceX · Introducing Starship V3", "source_date": "2026-05-12", "checked_on": "2026-11-05",
             "source_locator": "Raptor 3 Change Highlights，第 1 段", "scope": "真空型与海平面型分列，不把两型参数组合成一个设计点。"},
            {"label": "历史 Raptor 尾焰分析工况", "value": "氧燃质量比 3.60；喷管面积比 34.34；喷注面总压 3669.5 psia（约 25.30 MPa）",
             "source_url": _RAPTOR_PLUME_2019, "source_title": "Sierra · Exhaust Plume Calculations for SpaceX Raptor Booster Engine · 2019-001b",
             "source_date": "2019-06-18", "checked_on": "2026-11-05", "source_locator": "报告第 1 页，§2.0 Engine Description",
             "scope": "受托为 SpaceX 编制、由 FAA 公布的历史尾焰分析基准；不是 Raptor 3 的 250 tf／33 MPa 配套设计点。"},
        ],
        # 参数证据与求解输入分离；value_si 描述预设，不随用户编辑而改写。
        "parameter_evidence": [
            {"name": "thrust", "value_si": 2451662.5, "value_display": "250 tf（约 2452 kN）", "classification": "published",
             "source_url": _STARSHIP_V3_UPDATE, "source_title": "SpaceX · Introducing Starship V3", "source_date": "2026-05-12", "checked_on": "2026-11-05",
             "source_locator": "Raptor 3 Change Highlights，第 1 段", "scope": "仅支持海平面型公开推力数值，不能由此推定内部参数。",
             "note": "本示例采用此公开数值作为目标；采用 101325 Pa 环境边界是本项目设定。1 tf = 1000 kgf = 9806.65 N，计算输入为 2451662.5 N；换算小数位不表示测量精度。"},
            _ffsc_project_parameter("pc", 33e6, "本项目指定的设计室压；未找到与 2026-05-12 的 250 tf 公告配套的原始数值依据。2019 报告的喷注面总压约为 25.30 MPa，不能用来证明此处 33 MPa。"),
            {"name": "of_ratio", "value_si": 3.6, "classification": "historical_reference", "source_url": _RAPTOR_PLUME_2019,
             "source_title": "Sierra 2019-001b · 尾焰分析报告", "source_date": "2019-06-18", "checked_on": "2026-11-05",
             "source_locator": "第 1 页，§2.0 最后一段", "scope": "2019 年海平面基准工况；尚未核实为 Raptor 3 实机值。",
             "note": "报告明确列出总氧化剂／燃料质量流量比 3.60。本示例采用同一数值作为设计输入；它不是两个预燃室各自的氧燃质量比。"},
            {"name": "expansion_ratio", "value_si": 34.34, "classification": "historical_reference", "source_url": _RAPTOR_PLUME_2019,
             "source_title": "Sierra 2019-001b · 尾焰分析报告", "source_date": "2019-06-18", "checked_on": "2026-11-05",
             "source_locator": "第 1 页，§2.0 第一段", "scope": "历史喷管构型；尚未核实为 Raptor 3 的面积比。",
             "note": "主喷管出口面积／喉部面积。本示例采用历史报告中的 34.34，不表示当前真实机型仍使用这一几何。"},
            _ffsc_project_parameter("ambient_pressure", 101325.0, "本示例采用的环境压力边界，不是厂商随 250 tf 公告给出的试验边界。", value_display="101.325 kPa"),
            _ffsc_project_parameter("ox_inlet_temp", 70.0, "液氧贮箱出口温度假设；不是主泵出口温度。"),
            _ffsc_project_parameter("fuel_inlet_temp", 100.0, "甲烷贮箱出口温度假设；不是主泵出口温度。"),
            _ffsc_project_parameter("tank_pressure_pa", 3e5, "两种推进剂的贮箱压力边界假设；不代表已经模拟自生增压过程。", value_display="0.30 MPa"),
            _ffsc_project_parameter("inlet_pressure", 1e6, "增压泵出口压力设定；主泵入口还须扣除短管压降。", value_display="1.00 MPa"),
            _ffsc_project_parameter("feed_line_dp_pa", 5e4, "增压泵到主泵短管压降预算；与上项组合得到主泵入口 0.95 MPa。尚未验证汽蚀裕度。", value_display="0.05 MPa"),
            _ffsc_project_parameter("boost_pump_efficiency", .70, "增压泵常效率假设；当前模型假设由外部功源供能，非实机驱动方式证明。"),
            _ffsc_project_parameter("pump_efficiency_ox", .86, "液氧主泵常效率假设，尚无本机组试验特性图。"),
            _ffsc_project_parameter("pump_efficiency_fuel", .85, "甲烷主泵常效率假设，尚无本机组试验特性图。"),
            _ffsc_project_parameter("turbine_efficiency_ox", .85, "富氧涡轮常效率假设，尚无本机组试验特性图。"),
            _ffsc_project_parameter("turbine_efficiency_fuel", .88, "富燃涡轮常效率假设，尚无本机组试验特性图。"),
            _ffsc_project_parameter("preburner_injector_dp_ratio", .12, "预燃室喷注压降／对应预燃室压力的预算；不是实测喷注器特性。"),
            _ffsc_project_parameter("ox_turbine_exhaust_dp_ratio", .243, "富氧涡轮出口至主室的热气段压降／主室压力，包含当前主喷注器口径。"),
            _ffsc_project_parameter("fuel_turbine_exhaust_dp_ratio", .093, "富燃涡轮出口至主室的热气段压降／主室压力，包含当前主喷注器口径。"),
            _ffsc_project_parameter("valve_line_dp_ratio", .03, "基础阀门／管路压降／主室压力的预算；不是阀门开度、尺寸或实测流量特性。"),
            _ffsc_project_parameter("ox_preburner_temp_target", 750.0, "富氧预燃室概念设计目标，用于反求氧燃质量比；不是已验证的 Raptor 温度。"),
            _ffsc_project_parameter("fuel_preburner_temp_target", 850.0, "富燃预燃室概念设计目标，用于反求氧燃质量比；不是已验证的 Raptor 温度。"),
            _ffsc_project_parameter("ox_preburner_temp_limit", 800.0, "本设计示例的出口燃气温度上限，状态为设计暂定，限值依据未登记；不是通用工程上限或材料允许温度。"),
            _ffsc_project_parameter("fuel_preburner_temp_limit", 900.0, "本设计示例的出口燃气温度上限，状态为设计暂定，限值依据未登记；不是通用工程上限或材料允许温度。"),
            _ffsc_project_parameter("lox_pump_speed_rpm_override", 26500.0, "概念设计给定转速，非部件特性匹配结果或已核实的实机转速。", value_display="26500 r/min"),
            _ffsc_project_parameter("fuel_pump_speed_rpm_override", 38000.0, "概念设计给定转速，非部件特性匹配结果或已核实的实机转速。", value_display="38000 r/min"),
            _ffsc_project_parameter("lox_pump_n_stages", 1, "液氧主泵级数假设，不是厂商构型确认。", value_display="1 级"),
            _ffsc_project_parameter("fuel_pump_n_stages", 2, "甲烷主泵级数假设，不是厂商构型确认。", value_display="2 级"),
            _ffsc_project_parameter("target_isp", 327.0, "用于总质量流量初猜，不是厂家公布性能、最终比冲目标或验收标准。", classification="numerical_initial_guess"),
            _ffsc_project_parameter("ox_preburner_mr_target", 60.0, "当前温度目标模式下为富氧预燃室氧燃质量比初值，最终配比由温度反求及循环耦合得到。", classification="numerical_initial_guess"),
            _ffsc_project_parameter("fuel_preburner_mr_target", .13, "当前温度目标模式下为富燃预燃室氧燃质量比初值，最终配比由温度反求及循环耦合得到。", classification="numerical_initial_guess"),
            _ffsc_project_parameter("ox_preburner_pressure_ratio", 1.15, "当前目标模式中用于构造富氧涡轮压比初值；不是最后计算得到的预燃室压力比。", classification="numerical_initial_guess"),
            _ffsc_project_parameter("fuel_preburner_pressure_ratio", 1.18, "当前目标模式中用于构造富燃涡轮压比初值；不是最后计算得到的预燃室压力比。", classification="numerical_initial_guess"),
            _ffsc_project_parameter("cooling_dp_pa", 5e6, "当前准一维冷却模式仅用于耦合压降初猜，最终压降由通道模型计算；作用位置见 src/lre_workbench/systems/ffsc.py 的冷却水力耦合。", classification="numerical_initial_guess", value_display="5 MPa（仅初猜）"),
        ],
        "sources": [
            {"title": "Introducing Starship V3", "url": _STARSHIP_V3_UPDATE, "publisher": "SpaceX", "published_on": "2026-05-12",
             "locator": "Raptor 3 Change Highlights，第 1 段", "scope": "支持该日期的海平面型 250 tf、真空型 275 tf；未给出完整设计点。",
             "verification_note": "2026-11-05 通过检索返回的 SpaceX 原站文本核对；直接抓取该动态页面超时。"},
            {"title": "Exhaust Plume Calculations for SpaceX Raptor Booster Engine · 2019-001b", "url": _RAPTOR_PLUME_2019,
             "publisher": "Sierra Engineering & Software（受托为 SpaceX 编制；FAA 公布）", "published_on": "2019-06-18",
             "locator": "第 1 页，§2.0 Engine Description", "scope": "历史分析基准：氧燃质量比 3.60、喷管面积比 34.34、喷注面总压 3669.5 psia。不能证明 Raptor 3 参数。"},
            {"title": "Jeffery Thornburg · SpaceX 国会书面陈述（H.A.S.C. No. 114-45）", "url": _RAPTOR_TESTIMONY,
             "publisher": "SpaceX 陈述／美国政府出版局汇编", "published_on": "2015-06-26（陈述）；2016 年（汇编）",
             "locator": "正文第 118 页 = PDF 第 122 页 = 陈述内部第 6 页，Raptor 小节", "scope": "只支持历史推进剂与循环架构声明。"},
            {"title": "NIST SP 811 · Appendix B.9", "url": _NIST_FORCE_CONVERSION, "publisher": "美国国家标准与技术研究院",
             "locator": "Force 表，kilogram-force → newton", "scope": "1 kgf = 9.80665 N；公制吨力为 1000 kgf。仅支持单位换算，不支持发动机性能。"},
        ],
        "model_note": "本示例将 2026 年公开推力、2019 年历史参数和项目假设组合为一个概念设计任务，不是一套同一版本、同一工况的 Raptor 3 实测数据。33 MPa、入口状态、效率、压降和预燃室目标仍需独立论证；数值收敛不等于实机准确性验证。下面列出主要系统参数，未列出的几何、材料或设置不因此获得验证；物性与热力学模型另见任务设置中的对应说明。当前预设采用温度目标与准一维冷却模式，初值身份按这两种模式说明。新方案内衬默认采用 GRCop-42 文献热物性，是本项目概念选材，不代表已核实的 Raptor 实机材料；已有方案与结果保留原材料数据。",
    },
    "tq12a_gg": {
        "name": "天鹊 TQ-12A", "manufacturer": "蓝箭航天",
        "propellants": "LOX / CH₄（液氧 / 甲烷）",
        "cycle_type": "gg", "cycle_label": "GG 燃气发生器开式循环",
        "applicable": True, "preset_key": "tq12a_gg",
        "compatibility_reason": "可载入液氧/甲烷 GG 工程示例；未公开内部参数采用模型假设。",
        "shaft_note": "当前工程示例采用单轴；TQ-12A 实机轴系尚缺直接公开依据。",
        "reference_facts": [
            {"label": "推进剂", "value": "液氧 / 甲烷", "source_url": _LANDSPACE_PAGE},
            {"label": "循环方式", "value": "燃气发生器循环", "source_url": _LANDSPACE_FILING},
            {"label": "公开海平面推力", "value": "84.6 t（官网标注）", "source_url": _LANDSPACE_PAGE},
            {"label": "公开海平面比冲", "value": "293 s", "source_url": _LANDSPACE_PAGE},
            {"label": "公开混合比", "value": "2.9", "source_url": _LANDSPACE_PAGE},
        ],
        "sources": [
            {"title": "蓝箭航天官方产品资料 · TQ-12A", "url": _LANDSPACE_PAGE},
            {"title": "蓝箭航天招股说明书 · TQ-12A 参数表（1-1-210 页）、天鹊系列同轴泵技术（1-1-227 页）", "url": _LANDSPACE_FILING},
        ],
        "model_note": "示例保留既有标定：目标推力 829.64 kN、室压 10.1 MPa、混合比 3.4、燃烧效率 0.983。模型混合比 3.4 与官网公开值 2.9 不同，当前工况不能作为真实 TQ-12A 性能复现。",
    },
    "merlin1d_reference": {
        "name": "Merlin 1D", "manufacturer": "SpaceX",
        "propellants": "LOX / RP-1（液氧 / 煤油）",
        "cycle_type": "gg", "cycle_label": "GG 燃气发生器开式循环",
        "applicable": True, "preset_key": "merlin1d_gg",
        "compatibility_reason": "可加载液氧/煤油 GG 工程示例；燃料采用正十二烷替代工质，冷却采用指定吸热量。模型不等同于真实 RP-1。",
        "shaft_note": "SpaceX 已公开确认单轴：液氧泵、燃料泵和涡轮共用同一根轴。",
        "reference_facts": [
            {"label": "推进剂", "value": "液氧 / RP-1 煤油", "source_url": _FALCON_GUIDE},
            {"label": "循环方式", "value": "燃气发生器开式循环", "source_url": _FALCON_GUIDE},
            {"label": "轴系布置", "value": "液氧泵、燃料泵和涡轮共用单轴", "source_url": _FALCON_GUIDE},
            {"label": "应用", "value": "Falcon 9 一级采用 9 台 Merlin 1D", "source_url": _FALCON_GUIDE},
        ],
        "sources": [{"title": "SpaceX Falcon User's Guide（2025-05，第 2.3 节）", "url": _FALCON_GUIDE}],
        "model_note": "示例采用独立的正十二烷燃烧与真实流体物性链路。845 kN 来自公开推力量级，其余室压、混合比、面积比、效率、冷却和支路参数为工程假设；未建模 RP-1 多组分组成、结焦或烟炱，不构成 Merlin 1D 性能复现。",
    },
}


def presets_for_cycle(cycle_type: str) -> Dict[str, Dict[str, Any]]:
    """按循环方式过滤预设（GUI 参考机型下拉用）。"""
    return {k: v for k, v in ENGINE_PRESETS.items()
            if v.get("cycle_type") == cycle_type}


def default_preset_for_cycle(cycle_type: str) -> str:
    """该循环方式的默认参考机型 key（切换循环时的推荐加载项）。"""
    return "tq12a_gg" if cycle_type == "gg" else "raptor3_ffsc"


def preset_input_values(key: str) -> Dict[str, Any]:
    """预设 key → 完整 EngineInputs 字段值字典（默认值 + overrides）。

    返回 {字段名: 值}，含 cycle_type；GUI 逐字段写回输入控件。
    """
    from dataclasses import fields as _dc_fields
    from lre_workbench.domain.models import EngineInputs

    p = ENGINE_PRESETS[key]
    base = EngineInputs()
    merged: Dict[str, Any] = {f.name: getattr(base, f.name) for f in _dc_fields(base)}
    merged.update(p.get("overrides", {}))
    merged["cycle_type"] = p["cycle_type"]
    return merged
