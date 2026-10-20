"""
preburner.py

预燃室模型（V9 — 单一职责：HP 化学平衡）

V9 修改记录：
─────────────────────────────────────────────────────────
1. 把涡轮膨胀模块拆出到 turbine.py（Turbine 类），preburner.py
   只负责"绝热定压化学平衡 (HP equilibrium)"，输出预燃室出口热燃气
   状态（FlowState + GasState）。
2. 接口变化：
   · 旧 evaluate() 返回 (FlowState, ComponentResult, ComponentResult)
                     —— 涡轮入口状态 + 预燃室卡片 + 涡轮卡片
   · 新 evaluate() 返回 (FlowState, GasState, ComponentResult)
                     —— 涡轮入口状态 + 涡轮入口 GasState (供 Turbine 使用) + 预燃室卡片
   GasState 多带出来，避免 cycle.py 再额外重建 Cantera 入口状态。
3. exhaust_pressure 参数依然保留，但仅作为预燃室排到下游涡轮"前"
   的占位信息（实际涡轮在 turbine.py 中处理）。
4. 行为与 V8 等价：
   · 同一组输入流量、温度、压力，combustion_equilibrium() 输出不变。
   · cycle.py 中两侧的 T_PB、功率平衡、推力残差全部数值一致。

V5 历史记录（保留作参考）：
─────────────────────────────────────────────────────────
1. 燃烧温度、cp、γ、组分全部改由 thermo.combustion_equilibrium() 提供
   · Cantera 可用时：GRI-Mech 3.0 化学平衡，含真实入口焓
   · Cantera 不可用时：自动回退到经验公式
"""

from __future__ import annotations

from core import ComponentResult, FlowState, ValidationError, _check_range
from thermo import GasState, combustion_equilibrium


class Preburner:
    """
    预燃室化学平衡模型（HP equilibrium）。

    职责：
    ──────
        在给定 (mdot_fuel, mdot_ox, T_fuel, T_ox, pressure) 下，
        调用 thermo.combustion_equilibrium() 求 LOX/CH₄ 的化学平衡，
        输出预燃室出口热燃气状态。

    输出：
    ──────
        FlowState           ：预燃室出口流动状态（供下游 Turbine 使用）
        GasState            ：完整气相状态对象（cp, γ, MW, 组分, 熵 ...）
        ComponentResult     ：预燃室性能卡片（温度、混合比、组分、喷注器压降）

    注：涡轮膨胀已迁移至 turbine.Turbine。
    """

    def __init__(
        self,
        name: str,
        pressure: float,
        mdot_fuel: float,
        mdot_ox: float,
        phi: float,
        fuel_temp: float,
        ox_temp: float,
        injector_dp_ratio: float = 0.12,
    ):
        _check_range(f"{name}.phi（当量比）",
                     phi, lo=0, lo_inclusive=False)
        _check_range(f"{name}.pressure（预燃室压力）",
                     pressure, lo=0, lo_inclusive=False)
        _check_range(f"{name}.mdot_fuel（燃料流量）",
                     mdot_fuel, lo=0, lo_inclusive=False)
        _check_range(f"{name}.mdot_ox（氧化剂流量）",
                     mdot_ox, lo=0, lo_inclusive=False)
        _check_range(f"{name}.fuel_temp（燃料温度）",
                     fuel_temp, lo=0, lo_inclusive=False)
        _check_range(f"{name}.ox_temp（氧化剂温度）",
                     ox_temp, lo=0, lo_inclusive=False)

        self.name      = name
        self.pressure  = pressure
        self.mdot_fuel = mdot_fuel
        self.mdot_ox   = mdot_ox
        self.phi       = phi
        self.fuel_temp = fuel_temp
        self.ox_temp   = ox_temp
        self.injector_dp_ratio = injector_dp_ratio

    def evaluate(self) -> tuple[FlowState, GasState, ComponentResult]:
        """
        执行预燃室化学平衡。

        步骤：
        1. 调用 thermo.combustion_equilibrium() 得到 GasState
        2. 把 GasState 包装成 FlowState（保留质量流量信息）
        3. 组装 ComponentResult（含喷注器压降估算）

        历史偏差修复（V10.x）：
        · 旧版 Cantera 路径用 GRI-Mech 气相 NASA 多项式查 LOX/LCH4 入口焓,
          不含液相/超临界稠密相焓修正,系统性高估 T_PB 约 +130–160 K。
        · 现已在 thermo._cantera_combustion() 中改用 _real_fluid_enthalpy(),
          CoolProp 可用时自动从 NIST 级真实流体物性算焓修正(约 LOX -200 kJ/kg、
          LCH4 -470 kJ/kg @ 30 MPa),否则回退到固定修正常数。
        · 预期效果:T_PB 回落到合理量级,主燃烧室 Tc 同步降 70–90 K。
        """
        mdot = self.mdot_fuel + self.mdot_ox
        fuel_frac = self.mdot_fuel / max(mdot, 1e-9)

        # ── 化学平衡（HP）───────────────────────────────
        comb = combustion_equilibrium(
            mdot_fuel=self.mdot_fuel,
            mdot_ox=self.mdot_ox,
            T_fuel=self.fuel_temp,
            T_ox=self.ox_temp,
            pressure=self.pressure,
        )
        t_comb = comb.temperature
        cp     = comb.cp
        gamma  = comb.gamma
        h_out  = comb.enthalpy

        # 诊断用进口焓（采用液相 cp，与 pump.py 中 _CP_LOX/_CP_CH4 保持一致）
        # 注意：此值与 Cantera 内部用 GRI-Mech 气相焓做的 HP 平衡的 h_in
        #       存在系统差 ~180 kJ/kg（LOX 汽化潜热），仅用于能量审计显示。
        h_in_liquid_ref = (
            self.mdot_fuel * 3.5e3 * self.fuel_temp
            + self.mdot_ox  * 1.7e3 * self.ox_temp
        ) / max(mdot, 1e-9)

        combustion_flow_state = FlowState(
            name=f"{self.name}_outlet",
            pressure=self.pressure,
            temperature=t_comb,
            mass_flow=mdot,
            enthalpy=h_out,
            composition=comb.composition,
            metadata={
                "phi":           self.phi,
                "fuel_fraction": fuel_frac,
                "gamma":         gamma,
                "thermo_source": 1.0 if comb.source == "cantera" else 0.0,
            },
        )

        # ── 预燃室喷注器压降（与 cycle.py 泵出口预算同口径）──────────────────
        # 参考 Schoenman 1981（NASA TR）：富燃/富氧高压预燃室喷注器 ΔP/p_pb 范围 5.7–20%，
        # 工程取均值 14%。该压降发生在 *泵下游、预燃室上游*，由两路供应分别承担。
        # 比例由 cycle.py 经 EngineInputs.preburner_injector_dp_ratio 传入，保证一致。
        injector_dp_ratio = self.injector_dp_ratio
        injector_dp_pa    = injector_dp_ratio * self.pressure

        result = ComponentResult(
            name=self.name,
            outputs={
                "mdot_total_kgs":  mdot,
                "mdot_fuel_kgs":   self.mdot_fuel,
                "mdot_ox_kgs":     self.mdot_ox,
                "phi":             self.phi,
                "T_combustion_K":  t_comb,
                "h_in_Jkg":        h_in_liquid_ref,
                "h_combustion_Jkg": h_out,
                "cp_Jkgk":         cp,
                "gamma":           gamma,
                "fuel_fraction":   fuel_frac,
                "injector_dp_ratio": injector_dp_ratio,
                "injector_dp_Pa":    injector_dp_pa,
                "injector_dp_MPa":   injector_dp_pa / 1e6,
                "injector_dp_bar":   injector_dp_pa / 1e5,
                "preburner_pressure_Pa":  self.pressure,
                "preburner_pressure_MPa": self.pressure / 1e6,
            },
            notes=[
                f"热力学来源: {comb.source}",
                "本卡片仅表示预燃室燃烧 (HP) 状态；涡轮膨胀由独立的 Turbine 部件给出。",
            ],
        )

        # 保留接口兼容：旧代码可能从 self.combustion_state 读
        self.combustion_state = combustion_flow_state

        return combustion_flow_state, comb, result
