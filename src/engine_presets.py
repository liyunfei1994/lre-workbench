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
        "label": "Raptor 3 类 FFSC（250 tf 级 · 工程示例）",
        "cycle_type": "ffsc",
        "source": "工程示例：海平面目标推力采用 SpaceX 2026-05-12 公布的 250 tf，按标准重力换算为 2451.6625 kN；33 MPa 室压、O/F=3.6、面积比 34.34 及内部参数仍为未核实的工程假设，不代表 Raptor 3 完整设计或实测复现。重新加载示例才应用新推力，已保存方案不自动修改。",
        "overrides": {"thrust": 2451662.5, "combustion_loss_model": "adiabatic",
                      "methane_thermo_dataset": "gri30_highT"},
    },

    # GG 工程示例：保留既有标定，公开参考资料另列 ENGINE_REFERENCES。
    # 当前官网混合比为 2.9；本示例仍为原有 3.4，不能视为真实机型复现。
    # 以下全部为本示例的建模口径：
    #   thrust  84.6 tf = 829.64 kN（海平面）
    #   Pc      ≈10.1 MPa（TQ-12 系公开量级）
    #   O/F     3.4（甲烷 GG 典型）
    #   ε       15（海平面型，出口径约 0.95 m，与官网高度 2.2 m 自洽）
    #   η_comb  0.983 —— 标定值：使模型海平面比冲 = 293.0 s（官网实测口径）
    #   GG      单轴布局、温度目标 850 K（顶富燃涡轮材料极限）、背压 4 bar、排气 ε=4
    #   泵转速  override=0（按比转速自动反推；TQ-12 未公开转速）
    "tq12a_gg": {
        "label": "天鹊 TQ-12A 类 GG（液氧/甲烷 · 工程示例）",
        "cycle_type": "gg",
        "source": "工程示例：沿用 829.64 kN 目标推力、10.1 MPa 室压及 η_comb=0.983 既有标定；模型混合比为 3.4，蓝箭公开值为 2.9。内部参数属于模型假设，不代表厂商完整设计或实测复现。",
        "overrides": {
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
        "source": "工程示例：845 kN 为 SpaceX 公布的海平面最高推力量级；9.7 MPa 室压、O/F=2.36、面积比 16、入口温度、20 MW 冷却吸热量及支路参数均为本示例假设。煤油采用正十二烷替代工质，不代表真实 RP-1 或 Merlin 1D 实测性能。",
        "overrides": {
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
_RAPTOR_TESTIMONY = "https://www.govinfo.gov/content/pkg/CHRG-114hhrg95320/pdf/CHRG-114hhrg95320.pdf"
_LANDSPACE_PAGE = "https://www.landspace.com/product.html?mao=1"
_LANDSPACE_FILING = "https://static.sse.com.cn/stock/disclosure/announcement/c/202512/002174_20251231_1QK0.pdf"

# Reference metadata has no solver input payload; surrogate examples are
# explicitly separated from the manufacturer's public engine facts.
ENGINE_REFERENCES: Dict[str, Dict[str, Any]] = {
    "raptor3_ffsc": {
        "name": "Raptor 3", "manufacturer": "SpaceX",
        "propellants": "LOX / CH₄（液氧 / 甲烷）",
        "cycle_type": "ffsc", "cycle_label": "FFSC 全流量补燃循环",
        "applicable": True, "preset_key": "raptor3_ffsc",
        "compatibility_reason": "可载入同推进剂、同循环类型的工程示例，供总体设计研究。",
        "shaft_note": "本机型采用 FFSC；GG 轴系布局选项不适用。",
        "reference_facts": [
            {"label": "系列推进剂", "value": "Raptor 系列采用液氧 / 甲烷", "source_url": _RAPTOR_TESTIMONY},
            {"label": "系列循环架构", "value": "Raptor 系列采用全流量补燃循环", "source_url": _RAPTOR_TESTIMONY},
            {"label": "Raptor 3 海平面版本公开推力", "value": "250 tf ≈ 2451.66 kN（SpaceX，2026-05-12）", "source_url": _STARSHIP_V3_UPDATE, "source_date": "2026-05-12", "checked_on": "2026-10-23"},
            {"label": "Raptor 3 真空版本公开推力", "value": "275 tf（独立真空版本；不用于海平面示例）", "source_url": _STARSHIP_V3_UPDATE, "source_date": "2026-05-12", "checked_on": "2026-10-23"},
        ],
        # 参数证据与求解输入分离；value_si 描述预设，不随用户编辑而改写。
        "parameter_evidence": [
            {"name": "thrust", "value_si": 2451662.5, "classification": "published", "source_url": _STARSHIP_V3_UPDATE, "source_date": "2026-05-12", "checked_on": "2026-10-23", "note": "海平面版本 250 tf；按 1 tf = 9806.65 N 换算。换算小数位不表示厂商公布的测量精度。"},
            {"name": "pc", "value_si": 33e6, "classification": "engineering_assumption", "source_url": None, "source_date": None, "checked_on": "2026-10-23", "note": "沿用示例设计室压；未核实为与该 250 tf 口径配套的 Raptor 3 室压。"},
            {"name": "of_ratio", "value_si": 3.6, "classification": "engineering_assumption", "source_url": None, "source_date": None, "checked_on": "2026-10-23", "note": "总氧燃质量比的设计假设；不是喷管膨胀比，未核实为 Raptor 3 实机值。"},
            {"name": "expansion_ratio", "value_si": 34.34, "classification": "engineering_assumption", "source_url": None, "source_date": None, "checked_on": "2026-10-23", "note": "主喷管出口面积与喉部面积之比的设计假设，未核实为 Raptor 3 实机值。"},
            {"name": "target_isp", "value_si": 327.0, "classification": "numerical_initial_guess", "source_url": None, "source_date": None, "checked_on": "2026-10-23", "note": "用于质量流量初始化，不是厂家公布的性能目标或验收标准。"},
        ],
        "sources": [
            {"title": "SpaceX · Introducing Starship V3 · Raptor 3 Change Highlights（2026-05-12，核对于 2026-10-23）", "url": _STARSHIP_V3_UPDATE},
            {"title": "SpaceX 国会书面材料 · Raptor 系列架构（正文第 118 页）", "url": _RAPTOR_TESTIMONY},
            {"title": "SpaceX Starship 官方设计介绍", "url": _STARSHIP_PAGE},
        ],
        "model_note": "海平面示例采用 2026-05-12 厂商公开推力 250 tf（2451.6625 kN）。33 MPa、O/F=3.6、面积比 34.34、入口状态、效率及内部支路参数仍为工程假设；327 s 仅为流量初值。厂商资料未提供本模型完整设计点，数值收敛不等于 Raptor 3 性能验证。275 tf 属于另一真空版本；不混用不同版本或日期的推力、比冲与室压。加载更新后的示例才更改输入，既有方案与计算记录保留原值。",
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
    from core import EngineInputs

    p = ENGINE_PRESETS[key]
    base = EngineInputs()
    merged: Dict[str, Any] = {f.name: getattr(base, f.name) for f in _dc_fields(base)}
    merged.update(p.get("overrides", {}))
    merged["cycle_type"] = p["cycle_type"]
    return merged
