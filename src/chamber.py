"""
chamber.py

主燃烧室模型（V5 — Cantera 集成版）

V5 修改记录：
─────────────────────────────────────────────────────────
1. evaluate() 改由 thermo.chamber_equilibrium() 计算平衡状态
   · Cantera 可用时：两股涡轮排气真实混合 + HP 化学平衡
   · 经验模式：V3 兼容的焓混合 + 温度猜测
2. FlowState.metadata 中增加 Cantera 计算的 gamma 和 molar_mass，
   供下游 nozzle 使用（替代 EngineInputs 中的固定值）
3. 保留 V4 的参数校验
"""

from __future__ import annotations

from core import ComponentResult, FlowState, ValidationError, _check_range
from thermo import chamber_equilibrium


class MainChamber:
    def __init__(self, pressure: float, mdot_total: float, gamma: float,
                 molar_mass: float, chamber_temp_guess: float,
                 combustion_efficiency: float = 1.0, heat_loss_W: float = 0.0):
        _check_range("MainChamber.pressure（燃烧室压力）",
                     pressure, lo=0, lo_inclusive=False)
        _check_range("MainChamber.mdot_total（总质量流量）",
                     mdot_total, lo=0, lo_inclusive=False)
        _check_range("MainChamber.gamma（比热比）",
                     gamma, lo=1.0, lo_inclusive=False)
        _check_range("MainChamber.molar_mass（摩尔质量）",
                     molar_mass, lo=0, lo_inclusive=False)
        _check_range("MainChamber.chamber_temp_guess（燃烧室温度猜测值）",
                     chamber_temp_guess, lo=0, lo_inclusive=False)
        _check_range("MainChamber.combustion_efficiency（燃烧效率）",
                     combustion_efficiency, lo=0, hi=1.0, lo_inclusive=False)

        self.pressure = pressure
        self.mdot_total = mdot_total
        self.gamma = gamma
        self.molar_mass = molar_mass
        self.chamber_temp_guess = chamber_temp_guess
        self.combustion_efficiency = combustion_efficiency
        _check_range("MainChamber.heat_loss_W", heat_loss_W, lo=0)
        self.heat_loss_W = heat_loss_W
        self.gas_state = None   # evaluate() 后由 cycle.py 传给 Nozzle

    def evaluate(self, ox_stream: FlowState, fuel_stream: FlowState) -> tuple[FlowState, ComponentResult]:
        """
        计算主燃烧室平衡状态。

        Cantera 可用时：
            · 从两股涡轮排气的 composition（质量分数）和温度出发
            · 质量加权混合 → HP 化学平衡
            · 输出真实 T_chamber、γ、MW

        经验模式：
            · 焓混合 + 温度猜测（与 V3 一致）
        """
        # 热端冷却抽热与可选历史温升折减分别记账。
        energy_diagnostics = {}
        chamber_gas = chamber_equilibrium(
            ox_exhaust_T=ox_stream.temperature,
            ox_exhaust_mdot=ox_stream.mass_flow,
            ox_exhaust_composition=ox_stream.composition,
            fuel_exhaust_T=fuel_stream.temperature,
            fuel_exhaust_mdot=fuel_stream.mass_flow,
            fuel_exhaust_composition=fuel_stream.composition,
            pressure=self.pressure,
            gamma_fallback=self.gamma,
            molar_mass_fallback=self.molar_mass,
            temp_guess=self.chamber_temp_guess,
            efficiency=self.combustion_efficiency,
            ox_exhaust_enthalpy=ox_stream.enthalpy,
            fuel_exhaust_enthalpy=fuel_stream.enthalpy,
            heat_loss_W=self.heat_loss_W,
            energy_diagnostics=energy_diagnostics,
        )

        t_chamber  = chamber_gas.temperature          # 所选模型计算温度，非实测值
        gamma_calc = chamber_gas.gamma
        mw_calc    = chamber_gas.molar_mass

        # 反推理想平衡温度供展示：T_ideal = T_mix + (T_real − T_mix)/η
        # T_mix=两股排气质量加权掺混温（与 thermo 折减口径一致，Cantera 路径精确）
        _mdot_sum = max(ox_stream.mass_flow + fuel_stream.mass_flow, 1e-9)
        t_mix = (ox_stream.temperature * ox_stream.mass_flow
                 + fuel_stream.temperature * fuel_stream.mass_flow) / _mdot_sum
        eta = self.combustion_efficiency
        t_chamber_ideal = energy_diagnostics.get("T_chamber_ideal_K",
            (t_mix + (t_chamber - t_mix) / eta) if eta < 1.0 else t_chamber)

        # 保存 GasState，供 cycle.py 传给 Nozzle 做 Cantera 膨胀
        self.gas_state = chamber_gas

        state = FlowState(
            name="main_chamber",
            pressure=self.pressure,
            temperature=t_chamber,
            mass_flow=self.mdot_total,
            enthalpy=chamber_gas.enthalpy,
            composition=chamber_gas.composition,
            metadata={
                "gamma":        gamma_calc,
                "molar_mass":   mw_calc,
                "cp":           chamber_gas.cp,
                "thermo_source": 1.0 if chamber_gas.source == "cantera" else 0.0,
            },
        )

        result = ComponentResult(
            name="main_chamber",
            outputs={
                "T_chamber_K":            t_chamber,            # 真实主流温度(已折减)
                "T_chamber_ideal_K":      t_chamber_ideal,     # 理想平衡温度(η=1)
                "combustion_efficiency":  eta,
                "gamma":                  gamma_calc,
                "molar_mass_kg_per_kmol": mw_calc,
                "cp_Jkgk":               chamber_gas.cp,
                "h_chamber_Jkg":          chamber_gas.enthalpy,
                **energy_diagnostics,
            },
            notes=[
                f"热力学来源: {chamber_gas.source}",
                "再生冷却热端采用集中等效抽热；仅闭合零维总焓账，不代表分布式喷管换热模型。",
                "可选历史温升折减的焓亏损单独列为 combustion_loss_W；不与再生冷却吸热合并。",
                f"T_chamber = {t_chamber:.0f} K, γ = {gamma_calc:.4f}, MW = {mw_calc:.2f}",
                (f"历史温升折减系数 = {eta:.3f} → 平衡 {t_chamber_ideal:.0f} K 折减 "
                 f"{t_chamber_ideal - t_chamber:.0f} K" if eta < 1.0
                 else "按焓求化学平衡（含解离），未附加经验温升折减；不声明燃尽率或 c* 效率。"),
            ],
        )

        return state, result
