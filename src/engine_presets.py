# -*- coding: utf-8 -*-
"""
engine_presets.py — 参考机型工况预设库（V20.1）

选定循环方式后，把左侧"发动机设计输入"面板一键填成对应参考机型的工况。
每个预设 = 基准 EngineInputs() 默认值 + overrides 覆盖字典：
    · overrides 只列与默认不同的字段（其余沿用 core.EngineInputs 默认）；
    · FFSC 默认参数本身就对标 Raptor 3，故 Raptor 3 预设的 overrides 为空；
    · 预设数值以公开资料为准（来源写在 source 字段），
      不确定的内部参数（Pc/ε/O/F 等）按公开量级 + 工程常识取值，
      combustion_efficiency 用于把模型理想比冲折算到厂商公布的实测口径。

扩展方法：往 ENGINE_PRESETS 里加一个 dict 即可（GUI 下拉自动出现）：
    "tq15a_gg": {"label": "...", "cycle_type": "gg", "source": "...", "overrides": {...}}
"""

from __future__ import annotations

from typing import Any, Dict


ENGINE_PRESETS: Dict[str, Dict[str, Any]] = {

    # ── FFSC：SpaceX Raptor 3（EngineInputs 默认值即此机对标，无需覆盖）──
    "raptor3_ffsc": {
        "label": "Raptor 3（SpaceX · FFSC 全流量补燃 · 250 tf）",
        "cycle_type": "ffsc",
        "source": "公开资料对标：海平面 2450 kN / Isp_sl≈327 s / Pc≈33 MPa（EngineInputs 默认）",
        "overrides": {},
    },

    # ── GG：蓝箭 天鹊 TQ-12A（朱雀二号改进型 / 官网口径）──────────────
    # 官网：海平面推力 84.6 t，海平面比冲 293 s，发动机高度 2.2 m，
    #       具备二次起动 / 推力调节 / 混合比高精度控制（可重复使用）。
    # 建模口径：
    #   thrust  84.6 tf = 829.64 kN（海平面）
    #   Pc      ≈10.1 MPa（TQ-12 系公开量级）
    #   O/F     3.4（甲烷 GG 典型）
    #   ε       15（海平面型，出口径约 0.95 m，与官网高度 2.2 m 自洽）
    #   η_comb  0.983 —— 标定值：使模型海平面比冲 = 293.0 s（官网实测口径）
    #   GG      单轴布局、温度目标 850 K（顶富燃涡轮材料极限）、背压 4 bar、排气 ε=4
    #   泵转速  override=0（按比转速自动反推；TQ-12 未公开转速）
    "tq12a_gg": {
        "label": "天鹊 TQ-12A（蓝箭 · GG 开式循环 · 84.6 tf / 293 s）",
        "cycle_type": "gg",
        "source": "蓝箭官网：84.6 t / 293 s(SL) / 高 2.2 m；Pc/ε/O/F 为公开量级+工程取值，η_comb=0.983 标定到 293 s",
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
