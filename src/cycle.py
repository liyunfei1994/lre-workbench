"""
cycle.py

FFSC 全流量液氧甲烷分级燃烧循环系统级模型 —— V7 / PySide6 桌面版后端

V7 改动（对标 Raptor 3）：
─────────────────────────────────────────────────────────
1. 压力网络重新建模：LOX 泵和 CH4 泵的出口压力分别计算，
   含喷注器压降、冷却通道压降、阀门管路压降。
2. 配合 pump.py V7 物理化温升、cooling.py V7 压降。
3. 默认参数全部对标 Raptor 3。

本版保留两种求解模式：
1. 目标预燃室混合比模式（推荐）：
   - 输入富氧预燃室目标 O/F、富燃预燃室目标 O/F；
   - 由目标 O/F 反求两股预燃室分流量；
   - 求解变量改为 mdot_total、富氧预燃室压力比、富燃预燃室压力比；
   - 残差仍为推力闭合、LOX 涡轮泵功率闭合、CH4 涡轮泵功率闭合。

2. 自由分流模式（调试保留）：
   - 求解变量为 mdot_total、燃料进入富氧预燃室流量、氧进入富燃预燃室流量；
   - 适合快速观察数学闭合，但可能得到不合理的预燃室当量比。

注意：预燃室温度、混合比/当量比范围作为后验可行性检查，不直接混入功率残差。
"""

from __future__ import annotations

from task_control import checkpoint

import copy
import math
import warnings
from typing import Callable, Optional, List

from core import CycleResults, EngineInputs, FlowState, TopologyEdge, TopologyNode
from cooling import CoolingChannel, estimate_regen_dp
from chamber import MainChamber
from chamber_sizing import size_combustion_chamber
from pump import Pump, fluid_density
from nozzle import Nozzle
from preburner import Preburner
from turbine import Turbine
from injector import Injector
from thermo import CANTERA_AVAILABLE, vapor_pressure


STOICH_OF_CH4_O2 = 4.0  # CH4 + 2O2, mass O/F = 64/16 = 4


def _resolve_pump_speed(
    override_rpm: float,
    ns_target: float,
    n_stages: int,
    psi: float,
    rho: float,
    mdot: float,
    dp: float,
    default_rpm: float,
) -> float:
    """
    决定泵（也就是同侧涡轮）的最终转速 [rpm]。

    优先级：
      1. override_rpm > 0    → 直接使用 override（用于 CFTurbo 设计点锁定）
      2. 否则按比转速反推    → N = Ns_target · H_per_stage^0.75 / sqrt(Q)
      3. Q 或 H 无效         → 回退 default_rpm（避免 NaN）

    这是数字样机里 *唯一* 决定转速的入口；pump.py、turbine.py、
    geometry.py 三处全部从这里取值，保证同轴一致。
    """
    if override_rpm and override_rpm > 0:
        return float(override_rpm)
    g = 9.80665
    if rho <= 0 or mdot <= 0 or dp <= 0:
        return float(default_rpm)
    Q = mdot / rho
    H = dp / (rho * g)
    H_per_stage = H / max(n_stages, 1)
    if Q <= 0 or H_per_stage <= 0:
        return float(default_rpm)
    N = ns_target * (H_per_stage ** 0.75) / math.sqrt(Q)
    # 工程合理范围（5k–80k rpm）：超出按上下界 clip，但留 warning 由调用方处理
    return float(max(5000.0, min(80000.0, N)))



class FFSCCycleModel:
    """全流量分级燃烧循环（FFSC）系统级模型。"""

    def __init__(self, inputs: EngineInputs):
        self.inputs = inputs
        self._warnings: list[str] = []

    # =====================================================
    # 1. 基础辅助函数
    # =====================================================

    def _target_mr_mode(self) -> bool:
        # 温度目标模式建立在 MR 目标的交叉分流机制之上（外环只是反求 MR），
        # 故任一开启都走"目标 MR"求解路径。
        return (
            float(getattr(self.inputs, "use_target_preburner_mr", 0.0)) >= 0.5
            or float(getattr(self.inputs, "use_target_preburner_temp", 0.0)) >= 0.5
        )

    def _temp_target_mode(self) -> bool:
        return float(getattr(self.inputs, "use_target_preburner_temp", 0.0)) >= 0.5

    def _flows_from_total(self, mdot_total: float) -> tuple[float, float]:
        mdot_fuel = mdot_total / (1.0 + self.inputs.of_ratio)
        mdot_ox = mdot_total - mdot_fuel
        return mdot_fuel, mdot_ox

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _sanitize_mdot(self, v: float) -> float:
        return max(float(v), 1e-3)

    def _sanitize_pressure_ratio(self, v: float, warn: bool = False, name: str = "pressure_ratio") -> float:
        # V8：上限从 5.0 提升至 10.0（兼容旧路径，已不在主求解器中使用）
        lo, hi = 1.005, 10.0
        clamped = self._clamp(float(v), lo, hi)
        if warn and abs(clamped - v) > 1e-6:
            msg = f"⚠ {name}={v:.4f} 被截断至 [{lo:.3f}, {hi:.1f}] → {clamped:.4f}"
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
        return clamped

    def _sanitize_turbine_pressure_ratio(self, v: float, warn: bool = False, name: str = "pi_turbine") -> float:
        """
        涡轮压比 πT = p_in / p_out 的 sanitizer。
        物理范围 [1.02, 10.0]：
          - πT < 1.02：涡轮几乎不做功，物理上无意义；
          - πT > 10 ：单级离心/轴流难以实现，需多级；FFSC 典型 1.3–3.0。
        这是 V8 重构后的主求解变量（替代原来的 p_pre/pc）。
        """
        lo, hi = 1.02, 10.0
        clamped = self._clamp(float(v), lo, hi)
        if warn and abs(clamped - v) > 1e-6:
            msg = f"⚠ {name}={v:.4f} 被截断至 [{lo:.2f}, {hi:.1f}] → {clamped:.4f}"
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
        return clamped

    def _sanitize_fuel_to_ox_preburner(self, v: float, mdot_total: float, warn: bool = False) -> float:
        """燃料进入富氧预燃室的流量。目标 MR=40–80 时通常仅占总燃料约 4%–9%。"""
        mdot_fuel, _ = self._flows_from_total(max(mdot_total, 1e-3))
        lo = 0.005 * mdot_fuel
        hi = 0.80 * mdot_fuel
        clamped = self._clamp(float(v), lo, hi)
        if warn and abs(clamped - v) > 1e-6:
            msg = (
                f"⚠ mdot_fuel_to_ox_preburner={v:.3f} kg/s 被截断至 "
                f"[{lo:.3f}, {hi:.3f}] → {clamped:.3f} kg/s "
                f"（占总燃料 {clamped / max(mdot_fuel, 1e-9) * 100:.2f}%）"
            )
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
        return clamped

    def _sanitize_ox_to_fuel_preburner(self, v: float, mdot_total: float, warn: bool = False) -> float:
        """氧化剂进入富燃预燃室的流量。目标 MR=0.05–0.30 时通常仅占总氧约 1%–8%。"""
        _, mdot_ox = self._flows_from_total(max(mdot_total, 1e-3))
        lo = 0.001 * mdot_ox
        hi = 0.80 * mdot_ox
        clamped = self._clamp(float(v), lo, hi)
        if warn and abs(clamped - v) > 1e-6:
            msg = (
                f"⚠ mdot_ox_to_fuel_preburner={v:.3f} kg/s 被截断至 "
                f"[{lo:.3f}, {hi:.3f}] → {clamped:.3f} kg/s "
                f"（占总氧化剂 {clamped / max(mdot_ox, 1e-9) * 100:.2f}%）"
            )
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
        return clamped

    def _preburner_mr_phi(self, mdot_fuel: float, mdot_ox: float) -> tuple[float, float]:
        """返回 O/F 与当量比 phi；phi = (F/O)/(F/O)_st = O/F_st / O/F。"""
        mr = mdot_ox / max(mdot_fuel, 1e-12)
        phi = STOICH_OF_CH4_O2 / max(mr, 1e-12)
        return mr, phi

    def _split_from_target_preburner_mr(self, mdot_total: float, warn: bool = False) -> tuple[float, float]:
        """
        由富氧/富燃预燃室目标 O/F 反求两股交叉分流。

        设：
          x = 燃料进入富氧预燃室流量
          y = 氧化剂进入富燃预燃室流量
          F = 总燃料流量, O = 总氧化剂流量
          MR_ox = (O-y)/x
          MR_fuel = y/(F-x)

        解得：
          x = (O - MR_fuel*F)/(MR_ox - MR_fuel)
          y = MR_fuel*(F - x)
        """
        F, O = self._flows_from_total(mdot_total)
        mr_ox = float(self.inputs.ox_preburner_mr_target)
        mr_fuel = float(self.inputs.fuel_preburner_mr_target)
        denom = max(mr_ox - mr_fuel, 1e-12)
        x = (O - mr_fuel * F) / denom
        y = mr_fuel * (F - x)
        x = self._sanitize_fuel_to_ox_preburner(x, mdot_total, warn=warn)
        y = self._sanitize_ox_to_fuel_preburner(y, mdot_total, warn=warn)
        return x, y

    # =====================================================
    # 2. 初始猜测
    # =====================================================

    def _initial_guess_vector(self) -> list[float]:
        """
        V8：在目标 MR 模式下，求解变量改为 (mdot, πT_ox, πT_fuel)。
        典型 FFSC 涡轮压比：
          · LOX 侧（无冷却压降）：πT ≈ 1.3–1.8
          · CH4 侧（含再生冷却约 8 MPa）：πT ≈ 2.0–3.0
        """
        mdot_total = self.inputs.thrust / max(self.inputs.target_isp * 9.80665, 1e-9)
        if self._target_mr_mode():
            ox_exh_ratio = float(getattr(self.inputs, "ox_turbine_exhaust_dp_ratio", 0.243))
            fuel_exh_ratio = float(getattr(self.inputs, "fuel_turbine_exhaust_dp_ratio", 0.093))
            # πT 初值：先按用户给的 p_pre/pc 折算，再加 30% 安全余量以驱动收敛
            pi_t_ox_init = max(
                1.5,
                1.3 * self.inputs.ox_preburner_pressure_ratio / (1.0 + ox_exh_ratio),
            )
            pi_t_fuel_init = max(
                2.0,
                1.3 * self.inputs.fuel_preburner_pressure_ratio / (1.0 + fuel_exh_ratio),
            )
            return [
                mdot_total,
                self._sanitize_turbine_pressure_ratio(pi_t_ox_init),
                self._sanitize_turbine_pressure_ratio(pi_t_fuel_init),
            ]

        mdot_fuel, mdot_ox = self._flows_from_total(mdot_total)
        fuel_to_ox = self.inputs.fuel_split_to_ox_preburner * mdot_fuel
        ox_to_fuel = (1.0 - self.inputs.ox_split_to_ox_preburner) * mdot_ox
        return [
            mdot_total,
            self._sanitize_fuel_to_ox_preburner(fuel_to_ox, mdot_total, warn=True),
            self._sanitize_ox_to_fuel_preburner(ox_to_fuel, mdot_total, warn=True),
        ]

    # =====================================================
    # 3. 拓扑图
    # =====================================================

    def _build_topology(self) -> tuple[list[TopologyNode], list[TopologyEdge]]:
        nodes = [
            TopologyNode("lox_tank", "液氧贮箱", (60, 110)),
            TopologyNode("fuel_tank", "甲烷贮箱", (60, 260)),
            TopologyNode("ox_pump", "液氧泵", (220, 110)),
            TopologyNode("fuel_pump", "甲烷泵", (220, 260)),
            TopologyNode("fuel_cooling", "再生冷却", (380, 260)),
            TopologyNode("ox_preburner", "富氧预燃室", (560, 90)),
            TopologyNode("fuel_preburner", "富燃预燃室", (560, 260)),
            TopologyNode("main_chamber", "主燃烧室", (760, 170)),
            TopologyNode("nozzle", "喷管", (940, 170)),
        ]
        edges = [
            TopologyEdge("lox_tank", "ox_pump", "LOX"),
            TopologyEdge("fuel_tank", "fuel_pump", "CH4"),
            TopologyEdge("fuel_pump", "fuel_cooling", "CH4 高压冷却"),
            TopologyEdge("ox_pump", "ox_preburner", "LOX 主路"),
            TopologyEdge("ox_pump", "fuel_preburner", "LOX 小量"),
            TopologyEdge("fuel_cooling", "ox_preburner", "CH4 小量"),
            TopologyEdge("fuel_cooling", "fuel_preburner", "CH4 主路"),
            TopologyEdge("ox_preburner", "main_chamber", "涡轮排气"),
            TopologyEdge("fuel_preburner", "main_chamber", "涡轮排气"),
            TopologyEdge("main_chamber", "nozzle", "燃气"),
        ]
        return nodes, edges

    # =====================================================
    # 4. 单次前向计算
    # =====================================================

    def _evaluate_once(self, x0: float, x1: float, x2: float) -> CycleResults:
        checkpoint()
        mdot_total = self._sanitize_mdot(x0)
        target_mode = self._target_mr_mode()

        # V8：在目标 MR 模式下，求解变量 (x1, x2) 改为 (πT_ox, πT_fuel)。
        # 涡轮排气压力先按固定设计参数确定，再由 πT 反推预燃室压力 p_pre = πT · p_turb_exh。
        # 这样消除了原来 p_pre 与 p_turb_exh 之间的隐式约束（max(...) 截断），
        # 让 Newton 求解的雅可比在物理范围内连续可微。
        if target_mode:
            pi_t_ox = self._sanitize_turbine_pressure_ratio(x1)
            pi_t_fuel = self._sanitize_turbine_pressure_ratio(x2)
            mdot_fuel_to_ox_preburner, mdot_ox_to_fuel_preburner = self._split_from_target_preburner_mr(mdot_total)
            # ox_pr / fuel_pr 在后面用 p_pre / pc 反算后再赋值
            ox_pr = None
            fuel_pr = None
        else:
            ox_pr = self.inputs.ox_preburner_pressure_ratio
            fuel_pr = self.inputs.fuel_preburner_pressure_ratio
            pi_t_ox = None
            pi_t_fuel = None
            mdot_fuel_to_ox_preburner = self._sanitize_fuel_to_ox_preburner(x1, mdot_total)
            mdot_ox_to_fuel_preburner = self._sanitize_ox_to_fuel_preburner(x2, mdot_total)

        mdot_fuel, mdot_ox = self._flows_from_total(mdot_total)
        mdot_ox_to_ox_preburner = mdot_ox - mdot_ox_to_fuel_preburner
        mdot_fuel_to_fuel_preburner = mdot_fuel - mdot_fuel_to_ox_preburner

        states: dict[str, FlowState] = {}
        components: dict[str, object] = {}

        # ── 增压输送系统：贮箱 → 增压泵 → 短管 → 主泵入口 ─────────────────
        #   贮箱(自生增压,~3 bar) → 增压泵抬到 ~10 bar → 短管(−0.5 bar) → 主泵入口 9.5 bar
        # 三个压力节点分开，避免把贮箱压力当成主泵入口/增压泵出口。
        p_tank        = self.inputs.tank_pressure_pa            # 贮箱自生增压压力（真实值低）
        p_boost_out   = self.inputs.inlet_pressure             # 增压泵出口 = 主泵供给压力
        feed_dp       = getattr(self.inputs, "feed_line_dp_pa", 0.5e5)
        p_pump_inlet  = max(p_boost_out - feed_dp, 0.5e5)      # 主泵入口（≥ 0.5 bar 物理下界）

        # 贮箱状态（真实低压）
        states["fuel_tank"] = FlowState(
            name="fuel_tank", pressure=p_tank,
            temperature=self.inputs.fuel_inlet_temp, mass_flow=mdot_fuel,
            density=fluid_density("CH4", self.inputs.fuel_inlet_temp, p_tank),
            composition={"CH4": 1.0},
        )
        states["lox_tank"] = FlowState(
            name="lox_tank", pressure=p_tank,
            temperature=self.inputs.ox_inlet_temp, mass_flow=mdot_ox,
            density=fluid_density("LOX", self.inputs.ox_inlet_temp, p_tank),
            composition={"O2": 1.0},
        )
        # 增压泵出口状态（= 主泵供给）
        states["fuel_boost_out"] = FlowState(
            name="fuel_boost_out", pressure=p_boost_out,
            temperature=self.inputs.fuel_inlet_temp, mass_flow=mdot_fuel,
            density=fluid_density("CH4", self.inputs.fuel_inlet_temp, p_boost_out),
            composition={"CH4": 1.0},
        )
        states["lox_boost_out"] = FlowState(
            name="lox_boost_out", pressure=p_boost_out,
            temperature=self.inputs.ox_inlet_temp, mass_flow=mdot_ox,
            density=fluid_density("LOX", self.inputs.ox_inlet_temp, p_boost_out),
            composition={"O2": 1.0},
        )
        # 短管出口（主泵入口）：温度不变（低温流动，热交换可忽略）
        states["fuel_pump_inlet"] = FlowState(
            name="fuel_pump_inlet", pressure=p_pump_inlet,
            temperature=self.inputs.fuel_inlet_temp, mass_flow=mdot_fuel,
            density=fluid_density("CH4", self.inputs.fuel_inlet_temp, p_pump_inlet),
            composition={"CH4": 1.0},
        )
        states["lox_pump_inlet"] = FlowState(
            name="lox_pump_inlet", pressure=p_pump_inlet,
            temperature=self.inputs.ox_inlet_temp, mass_flow=mdot_ox,
            density=fluid_density("LOX", self.inputs.ox_inlet_temp, p_pump_inlet),
            composition={"O2": 1.0},
        )
        # 增压泵 ΔP / 扬程 / 功率（信息量；不进入主轴功率平衡残差）
        eta_boost = float(getattr(self.inputs, "boost_pump_efficiency", 0.70))
        dp_boost  = max(p_boost_out - p_tank, 0.0)
        rho_lox_tank  = fluid_density("LOX", self.inputs.ox_inlet_temp,  p_tank)
        rho_fuel_tank = fluid_density("CH4", self.inputs.fuel_inlet_temp, p_tank)
        p_boost_lox  = mdot_ox   * dp_boost / max(rho_lox_tank  * eta_boost, 1e-9)
        p_boost_fuel = mdot_fuel * dp_boost / max(rho_fuel_tank * eta_boost, 1e-9)

        # ── 增压泵吸入空化校核（FS-01）──────────────────────────────────
        # 增压泵从贮箱(3 bar)抽吸，是整条链里 NPSH 最低、最易空化的一级。
        # 在贮箱处算 NPSHa = (p_tank − p_vapor)/(ρ·g)，并按"增压泵典型慢转速"
        # 给一个吸入比转速量级，便于诊断（真实增压泵靠诱导轮+慢转速吸收低 NPSH）。
        _g0 = 9.80665
        pv_lox_tank  = vapor_pressure("O2",  self.inputs.ox_inlet_temp)
        pv_fuel_tank = vapor_pressure("CH4", self.inputs.fuel_inlet_temp)
        npshA_boost_lox  = max(p_tank - pv_lox_tank,  1e3) / (rho_lox_tank  * _g0)
        npshA_boost_fuel = max(p_tank - pv_fuel_tank, 1e3) / (rho_fuel_tank * _g0)
        # 增压泵慢转速假定（低压前置泵典型 ~6000 rpm 量级），仅供 nss 量级判断
        n_boost = 6000.0
        Q_lox_tank  = mdot_ox   / max(rho_lox_tank,  1.0)
        Q_fuel_tank = mdot_fuel / max(rho_fuel_tank, 1.0)
        nss_boost_lox_eu  = n_boost * math.sqrt(max(Q_lox_tank, 1e-9))  / max(npshA_boost_lox,  0.01) ** 0.75
        nss_boost_fuel_eu = n_boost * math.sqrt(max(Q_fuel_tank, 1e-9)) / max(npshA_boost_fuel, 0.01) ** 0.75

        # ── V8 压力网络（分两条独立支路）────────────────
        # 涡轮排气压力先确定（必须高于 pc 才能通过主喷注器进入主燃烧室）：
        #   p_ox_turb_exh = pc · (1 + ox_turbine_exhaust_dp_ratio)
        #   p_fuel_turb_exh = pc · (1 + fuel_turbine_exhaust_dp_ratio)
        # 预燃室压力由涡轮压比 πT 反推：
        #   p_pre_X = πT_X · p_X_turb_exh
        # 这样 πT ≥ 1.02 即自然满足 p_pre > p_turb_exh，无需额外 max() 截断。
        # 求解器三个未知数 (mdot, πT_ox, πT_fuel) ↔ 三个残差（推力、氧侧功率平衡、燃侧功率平衡）。
        dp_valve    = self.inputs.valve_line_dp_ratio    * self.inputs.pc
        p_ox_turbine_exhaust = self.inputs.pc * (
            1.0 + getattr(self.inputs, "ox_turbine_exhaust_dp_ratio", 0.0)
        )
        p_fuel_turbine_exhaust = self.inputs.pc * (
            1.0 + getattr(self.inputs, "fuel_turbine_exhaust_dp_ratio", 0.0)
        )

        if target_mode:
            # 由 πT 直接确定预燃室压力，无任何隐式截断
            p_pre_ox = pi_t_ox * p_ox_turbine_exhaust
            p_pre_fuel = pi_t_fuel * p_fuel_turbine_exhaust
        else:
            # 自由分流模式（旧代码兼容）：仍用用户指定的 p_pre/pc，并保留物理下界
            p_pre_ox = max(ox_pr * self.inputs.pc, 1.02 * p_ox_turbine_exhaust)
            p_pre_fuel = max(fuel_pr * self.inputs.pc, 1.02 * p_fuel_turbine_exhaust)

        # 后验计算的 pr / πT，供 summary 输出
        ox_pr = p_pre_ox / self.inputs.pc
        fuel_pr = p_pre_fuel / self.inputs.pc
        pi_t_ox_val = p_pre_ox / max(p_ox_turbine_exhaust, 1.0)
        pi_t_fuel_val = p_pre_fuel / max(p_fuel_turbine_exhaust, 1.0)

        # ── V8 关键修正：泵出口压力 *只* 按各自的主目的地预燃室计算 ──
        #
        # 物理依据：
        # 在 FFSC 真实工程实现（Raptor / IPD 等）中，少量"交叉流量"
        # （LOX→富燃PB，CH4→富氧PB）由独立的子级喷注或调节阀承接，
        # **并不要求主泵在出口同时满足两条支路的最高压力**——
        # 否则一旦两侧预燃室压力不等，泵就会被强行抬到等高的最大值，
        # 造成 LOX 泵 / CH4 泵的压力网络强耦合（V7 实测：两泵齐齐冲到 ~180 MPa，
        # 求解器陷入伪稳态、残差 ~2.3 不可收敛）。
        #
        # 正确做法是：
        #   主泵出口压力 = 主目的预燃室入口压力 + 主路总压降
        #   交叉小流量的额外压头由独立的 booster / orifice / 节流阀提供（不在主循环建模）
        #
        # 这样两条供给支路在压力网络上 **解耦**，求解器的雅可比也变成块对角，
        # Newton-line-search 才有可能稳定收敛到 Raptor 量级（~70–80 MPa）。
        #
        # 预燃室喷注器压降引用各自预燃室压力（Schoenman 典型 ~14%），与 preburner.py
        # 卡片同口径，避免旧版 0.17×Pc（仅占 p_pre 的 ~9%）与卡片 0.14×p_pre 打架。
        # ── 再生冷却压降（V14，修 Major 1）──────────────────────────────
        # 默认（cooling_dp_auto=0）用固定 cooling_dp_pa，保持泵压预算稳定；
        # 置 1 时由通道水力学(Darcy)在主泵之前预估并反馈进燃料泵出口压力预算，
        # 与冷却卡片同公式/同几何 → 自洽（消除"固定压降 vs 真实几何"矛盾）。
        _cool_auto_size = bool(getattr(self.inputs, "cooling_channel_auto_size", 1.0))
        _cool_dp_auto   = bool(getattr(self.inputs, "cooling_dp_auto", 0.0))
        if _cool_dp_auto:
            _regen = estimate_regen_dp(
                mdot_fuel=mdot_fuel,
                t_proxy=self.inputs.fuel_inlet_temp + 15.0,
                p_proxy=1.12 * p_pre_fuel,
                mdot_total=mdot_total, pc=self.inputs.pc,
                t_chamber=self.inputs.chamber_temp_guess,
                gamma_c=self.inputs.chamber_gamma,
                molar_mass_c=self.inputs.molar_mass,
                contraction_ratio=getattr(self.inputs, "chamber_contraction_ratio", 2.5),
                l_star_m=getattr(self.inputs, "chamber_l_star_m", 1.0),
                n_channels=int(getattr(self.inputs, "cooling_n_channels", 300)),
                width_m=getattr(self.inputs, "cooling_channel_width_m", 1.5e-3),
                height_m=getattr(self.inputs, "cooling_channel_height_m", 5.0e-3),
                auto_size=_cool_auto_size,
                target_velocity=getattr(self.inputs, "cooling_target_velocity_ms", 100.0),
                rel_roughness=getattr(self.inputs, "cooling_channel_rel_roughness", 0.003),
                k_minor=getattr(self.inputs, "cooling_channel_k_minor", 3.0),
            )
            dp_cooling = min(_regen["dp_pa"], 0.5 * p_pre_fuel)   # 保护：不把泵压抬过头
        else:
            dp_cooling = self.inputs.cooling_dp_pa

        dp_inj_ox   = self.inputs.preburner_injector_dp_ratio * p_pre_ox
        dp_inj_fuel = self.inputs.preburner_injector_dp_ratio * p_pre_fuel
        p_lox_pump_out = p_pre_ox + dp_inj_ox + dp_valve
        p_ch4_pump_out = p_pre_fuel + dp_inj_fuel + dp_cooling + dp_valve

        # ── V10：转速一次性解析，泵 / 涡轮 / geometry 三处共用 ──
        # 先用 fluid_density 算入口密度（pump 内部也会再算一遍，但这里
        # 必须先算才能根据 Ns_target 反推 N）。
        rho_lox_inlet  = fluid_density("LOX", self.inputs.ox_inlet_temp,  p_pump_inlet)
        rho_fuel_inlet = fluid_density("CH4", self.inputs.fuel_inlet_temp, p_pump_inlet)
        dp_lox  = p_lox_pump_out - p_pump_inlet
        dp_fuel = p_ch4_pump_out - p_pump_inlet

        n_lox_rpm = _resolve_pump_speed(
            override_rpm=self.inputs.lox_pump_speed_rpm_override,
            ns_target=self.inputs.lox_pump_specific_speed_target,
            n_stages=self.inputs.lox_pump_n_stages,
            psi=self.inputs.lox_pump_head_coefficient_psi,
            rho=rho_lox_inlet, mdot=mdot_ox, dp=dp_lox,
            default_rpm=25000.0,
        )
        n_fuel_rpm = _resolve_pump_speed(
            override_rpm=self.inputs.fuel_pump_speed_rpm_override,
            ns_target=self.inputs.fuel_pump_specific_speed_target,
            n_stages=self.inputs.fuel_pump_n_stages,
            psi=self.inputs.fuel_pump_head_coefficient_psi,
            rho=rho_fuel_inlet, mdot=mdot_fuel, dp=dp_fuel,
            default_rpm=30000.0,
        )

        # ── NPSHa（净正吸入压头，必须在 *泵入口* 处评估）─────────────────
        # NPSHa = (p_pump_inlet − p_vapor) / (ρ·g)。
        # 必须用主泵入口压力（增压泵出口 10 bar − 0.5 bar 短管 = 9.5 bar），
        # 而非储箱/增压泵出口压力；并减去 *真实饱和蒸汽压*
        # （CoolProp：O2@70K≈6.3 kPa，CH4@100K≈34 kPa；深过冷 → pv 很低，
        #  LOX@9.5bar 饱和温度 118.7 K，过冷度 ~48.7 K，热力学抑制裕度大）。
        pv_lox  = vapor_pressure("O2",  self.inputs.ox_inlet_temp)
        pv_fuel = vapor_pressure("CH4", self.inputs.fuel_inlet_temp)
        npshA_lox  = max(p_pump_inlet - pv_lox,  1e3)
        npshA_fuel = max(p_pump_inlet - pv_fuel, 1e3)

        lox_pump = Pump("ox_pump", "LOX", mdot_ox,
                        p_pump_inlet, p_lox_pump_out,
                        self.inputs.ox_inlet_temp, self.inputs.pump_efficiency_ox,
                        nr_rpm=n_lox_rpm,
                        n_stages=self.inputs.lox_pump_n_stages,
                        tip_speed_limit_ms=self.inputs.pump_tip_speed_limit_ms,
                        npsh_available_pa=npshA_lox,
                        impeller_material=getattr(self.inputs, "lox_pump_impeller_material", None),
                        side="ox")
        fuel_pump = Pump("fuel_pump", "CH4", mdot_fuel,
                         p_pump_inlet, p_ch4_pump_out,
                         self.inputs.fuel_inlet_temp, self.inputs.pump_efficiency_fuel,
                         nr_rpm=n_fuel_rpm,
                         n_stages=self.inputs.fuel_pump_n_stages,
                         tip_speed_limit_ms=self.inputs.pump_tip_speed_limit_ms,
                         npsh_available_pa=npshA_fuel,
                         impeller_material=getattr(self.inputs, "fuel_pump_impeller_material", None),
                         side="fuel")
        lox_pump_out, lox_pump_result = lox_pump.evaluate()
        fuel_pump_out, fuel_pump_result = fuel_pump.evaluate()
        states["lox_after_pump"] = lox_pump_out
        states["fuel_after_pump"] = fuel_pump_out
        components[lox_pump_result.name] = lox_pump_result
        components[fuel_pump_result.name] = fuel_pump_result

        bartz_mode = bool(getattr(self.inputs, "cooling_bartz_mode", True))
        # V19 k(T) 口径统一：单点冷却的内衬导热与所选内壁材料联动（取 600 K 代表温度，
        # grcop84 走手册回归式、其它铜合金走通用缩放）；材料查不到才回退数值输入。
        # 旧版此处永远用 cooling_wall_conductivity=320 常数——换内壁材料不影响单点解。
        try:
            from materials import MATERIALS as _MATS_K
            _im_k = _MATS_K.get(getattr(self.inputs, "chamber_inner_wall_material", "grcop84"))
            _liner_k = (float(_im_k.thermal_conductivity_at(600.0)) if _im_k
                        else float(getattr(self.inputs, "cooling_wall_conductivity", 320.0)))
        except Exception:
            _liner_k = float(getattr(self.inputs, "cooling_wall_conductivity", 320.0))
        cooling = CoolingChannel(
            mdot_fuel=mdot_fuel,
            t_inlet=fuel_pump_out.temperature,
            p_inlet=fuel_pump_out.pressure,
            dp_pa=dp_cooling,
            # 固定模式参数（bartz_mode=False 时使用）
            heat_load=self.inputs.cooling_heat_load,
            cp=self.inputs.coolant_cp,
            # Bartz 模式开关
            bartz_mode=bartz_mode,
            # Bartz 模式：发动机热力参数（CoolingChannel 内部推算喉部面积）
            mdot_total=mdot_total,
            pc=self.inputs.pc,
            t_chamber=self.inputs.chamber_temp_guess,
            gamma_c=self.inputs.chamber_gamma,
            molar_mass_c=self.inputs.molar_mass,
            contraction_ratio=self.inputs.chamber_contraction_ratio,
            l_star_m=self.inputs.chamber_l_star_m,
            # 再生冷却通道几何（V14：已正式纳入 EngineInputs，不再是锁死默认）
            n_channels=int(getattr(self.inputs, "cooling_n_channels", 300)),
            channel_width_m=getattr(self.inputs, "cooling_channel_width_m", 1.5e-3),
            channel_height_m=getattr(self.inputs, "cooling_channel_height_m", 5.0e-3),
            wall_thickness_m=getattr(self.inputs, "cooling_wall_thickness_m", 0.5e-3),
            wall_conductivity=_liner_k,
            htc_enhancement=getattr(self.inputs, "cooling_htc_enhancement", 1.0),
            # V14：自动定尺寸 + 水力压降（修 Major 1）
            auto_size=_cool_auto_size,
            target_velocity_ms=getattr(self.inputs, "cooling_target_velocity_ms", 100.0),
            dp_auto=_cool_dp_auto,
            channel_rel_roughness=getattr(self.inputs, "cooling_channel_rel_roughness", 0.003),
            channel_k_minor=getattr(self.inputs, "cooling_channel_k_minor", 3.0),
            # V15.1：循环闭合热载荷覆盖到与 marching 同口径（默认全喷管 ε）
            cover_eps=(getattr(self.inputs, "cooling_cover_eps", 0.0)
                       or self.inputs.expansion_ratio),
            conv_half_angle_deg=getattr(self.inputs, "chamber_conv_half_angle_deg", 30.0),
        )
        cooled_fuel_state, cooling_result = cooling.evaluate()
        states["fuel_after_cooling"] = cooled_fuel_state
        components[cooling_result.name] = cooling_result

        mr_ox_preburner, phi_ox_preburner = self._preburner_mr_phi(mdot_fuel_to_ox_preburner, mdot_ox_to_ox_preburner)
        mr_fuel_preburner, phi_fuel_preburner = self._preburner_mr_phi(mdot_fuel_to_fuel_preburner, mdot_ox_to_fuel_preburner)

        # ── 预燃室（V9：只做 HP 化学平衡，输出热燃气状态） ─────
        ox_preburner = Preburner(
            name="ox_preburner", pressure=p_pre_ox,
            mdot_fuel=mdot_fuel_to_ox_preburner, mdot_ox=mdot_ox_to_ox_preburner,
            phi=phi_ox_preburner,
            fuel_temp=cooled_fuel_state.temperature, ox_temp=lox_pump_out.temperature,
            injector_dp_ratio=self.inputs.preburner_injector_dp_ratio,
        )
        fuel_preburner = Preburner(
            name="fuel_preburner", pressure=p_pre_fuel,
            mdot_fuel=mdot_fuel_to_fuel_preburner, mdot_ox=mdot_ox_to_fuel_preburner,
            phi=phi_fuel_preburner,
            fuel_temp=cooled_fuel_state.temperature, ox_temp=lox_pump_out.temperature,
            injector_dp_ratio=self.inputs.preburner_injector_dp_ratio,
        )

        ox_pb_outlet_flow,  ox_pb_gas_state,  ox_preburner_result  = ox_preburner.evaluate()
        fuel_pb_outlet_flow, fuel_pb_gas_state, fuel_preburner_result = fuel_preburner.evaluate()

        # ── 涡轮（V10：贯通转速 + U/C0 + 材料）─────────
        # 关键不变量：
        #   n_lox_rpm  ↔ lox_pump.nr_rpm  ↔ ox_turbine.shaft_speed_rpm
        #   n_fuel_rpm ↔ fuel_pump.nr_rpm ↔ fuel_turbine.shaft_speed_rpm
        ox_turbine = Turbine(
            name="ox_preburner_turbine",
            inlet_state=ox_pb_gas_state,
            exhaust_pressure=p_ox_turbine_exhaust,
            eta_turbine=self.inputs.turbine_efficiency_ox,
            mass_flow=ox_pb_outlet_flow.mass_flow,
            # V10 新增：
            shaft_speed_rpm=n_lox_rpm,
            u_over_c_target=self.inputs.turb_ox_u_over_c_target,
            reaction_degree=self.inputs.turb_ox_reaction_degree,
            n_stages_user=self.inputs.turb_ox_n_stages_user,
            rotor_material=self.inputs.turb_ox_rotor_material,
            side="ox",
            t_in_limit_K=self.inputs.ox_preburner_temp_limit,
            u_max_ms=self.inputs.turb_ox_u_max_ms,
        )
        fuel_turbine = Turbine(
            name="fuel_preburner_turbine",
            inlet_state=fuel_pb_gas_state,
            exhaust_pressure=p_fuel_turbine_exhaust,
            eta_turbine=self.inputs.turbine_efficiency_fuel,
            mass_flow=fuel_pb_outlet_flow.mass_flow,
            # V10 新增：
            shaft_speed_rpm=n_fuel_rpm,
            u_over_c_target=self.inputs.turb_fuel_u_over_c_target,
            reaction_degree=self.inputs.turb_fuel_reaction_degree,
            n_stages_user=self.inputs.turb_fuel_n_stages_user,
            rotor_material=self.inputs.turb_fuel_rotor_material,
            side="fuel",
            t_in_limit_K=self.inputs.fuel_preburner_temp_limit,
            u_max_ms=self.inputs.turb_fuel_u_max_ms,
        )
        ox_turbine_out,   ox_turbine_result   = ox_turbine.evaluate()
        fuel_turbine_out, fuel_turbine_result = fuel_turbine.evaluate()

        ox_preburner_result.outputs["mixture_ratio_OF"]   = mr_ox_preburner
        fuel_preburner_result.outputs["mixture_ratio_OF"] = mr_fuel_preburner

        states["ox_preburner_outlet"]   = ox_pb_outlet_flow
        states["fuel_preburner_outlet"] = fuel_pb_outlet_flow
        states["ox_turbine_exhaust"]    = ox_turbine_out
        states["fuel_turbine_exhaust"]  = fuel_turbine_out
        components[ox_preburner_result.name]   = ox_preburner_result
        components[fuel_preburner_result.name] = fuel_preburner_result
        components[ox_turbine_result.name]     = ox_turbine_result
        components[fuel_turbine_result.name]   = fuel_turbine_result

        # 下游主燃烧室仍然消费涡轮排气流，命名兼容：
        ox_preburner_out   = ox_turbine_out
        fuel_preburner_out = fuel_turbine_out

        chamber = MainChamber(
            pressure=self.inputs.pc, mdot_total=mdot_total,
            gamma=self.inputs.chamber_gamma, molar_mass=self.inputs.molar_mass,
            chamber_temp_guess=self.inputs.chamber_temp_guess,
            combustion_efficiency=self.inputs.combustion_efficiency,
        )
        chamber_state, chamber_result = chamber.evaluate(ox_preburner_out, fuel_preburner_out)
        states["main_chamber"] = chamber_state
        components[chamber_result.name] = chamber_result

        gamma_nozzle = chamber_state.metadata.get("gamma", self.inputs.chamber_gamma)
        mw_nozzle = chamber_state.metadata.get("molar_mass", self.inputs.molar_mass)
        nozzle = Nozzle(
            pc=self.inputs.pc, expansion_ratio=self.inputs.expansion_ratio,
            gamma=gamma_nozzle, molar_mass=mw_nozzle,
            tc=chamber_state.temperature, mdot=mdot_total,
            p_amb=self.inputs.ambient_pressure, chamber_state=chamber.gas_state,
        )
        nozzle_result = nozzle.evaluate()
        components[nozzle_result.name] = nozzle_result

        # ── 主燃烧室几何参数（从喉部面积反推）──────────────
        # 理论依据：
        #   NASA SP-125 (Huzel & Huang, "Design of Liquid Propellant Rocket Engines")
        #   Sutton & Biblarz, "Rocket Propulsion Elements", 9th Ed.
        #
        # 1. 燃烧室形状：工程上统一采用圆柱形（cylindrical）。
        #    圆柱段 + 收缩段（收半角 β≈30–45°）+ 喉部圆弧过渡。
        #    Raptor 与主流高压发动机（SSME、Vulcain-2）均如此。
        #
        # 2. 收缩比 ε_c = A_cc / A_t：
        #    典型值 2.5–4.0，高压发动机偏低（减小散热损失）。
        #    论文（Piunti et al.）通过 Mcc=0.2 的流量连续方程反算得 ε_c ≈ 3.1；
        #    本程序采用 2.5（保守设计点），可在 geometry.py 中调整。
        #
        # 3. 特征长度 L* = V_cc / A_t：
        #    LOX/LCH4：典型 0.8–1.2 m（NASA SP-125 Table 4-2）
        #    L* 反映推进剂在室内的停留时间，过短导致燃烧不完全。
        #
        # 4. 燃烧室圆柱段长度：L_cc = L* / ε_c
        #
        # 5. 截面马赫数 M_cc（等熵流反算）：
        #    由 A/A* = ε_c 用二分法求亚声速根，M_cc ≈ 0.15–0.25
        #    M_cc 越低，驻压损失越小，但室长更长。

        # 几何 + 总和特性参数一次算齐（特征长度 L*、停留时间、流量密度、收缩比、
        # 室直径/容积、圆筒长[扣收缩段锥台]、长径比、室截面马赫数），并对经验区间
        # 软校核。详见 chamber_sizing.size_combustion_chamber（教材设计方法）。
        _cc = size_combustion_chamber(
            At=nozzle_result.outputs["At_m2"],
            pc=self.inputs.pc,
            mdot_total=mdot_total,
            T_c=chamber_state.temperature,
            molar_mass=mw_nozzle,
            gamma=gamma_nozzle,
            l_star=getattr(self.inputs, "chamber_l_star_m", 1.0),
            contraction_ratio=getattr(self.inputs, "chamber_contraction_ratio", 2.5),
            conv_half_angle_deg=getattr(self.inputs, "chamber_conv_half_angle_deg", 30.0),
            c_star=nozzle_result.outputs.get("c_star_ms"),
        )

        # ── 壁厚（V19 口径统一）：直接用 geometry/chamber.py 的 V12 分层定厚 ──
        # 旧版此处还留着一套"单壁 Barlow(0.25·pc)+4mm"估算（5–25mm 钳位），与几何
        # 模块的分层壁厚（内衬传热限 + 通道深 + 外壳 Barlow 对冷却剂压力）同时对外
        # 展示、数字互相矛盾。现删除旧算法，部件卡与几何页显示**同一个**分层总厚。
        _cc_notes      = _cc.pop("_notes", [])
        _cc_validation = _cc.pop("_validation", [])
        try:
            from geometry.chamber import compute_chamber as _geo_chamber
            _q_thr_W = float(cooling_result.outputs.get("q_throat_MW_m2", 0.0) or 0.0) * 1e6
            try:
                from materials import MATERIALS as _MATS
                _im = _MATS.get(getattr(self.inputs, "chamber_inner_wall_material", "grcop84"))
                _om = _MATS.get(getattr(self.inputs, "chamber_outer_wall_material", "inconel718_wall"))
                _k_liner = float(_im.thermal_conductivity_WmK) if _im else 303.0
                _sig_jacket = float(_om.sigma_yield_hot_MPa) * 1e6 if _om else 950e6
            except Exception:
                _k_liner, _sig_jacket = 303.0, 950e6
            _ch_geo = _geo_chamber(
                nozzle_result.outputs["At_m2"],
                getattr(self.inputs, "chamber_contraction_ratio", 2.5),
                getattr(self.inputs, "chamber_l_star_m", 1.0),
                self.inputs.pc,
                getattr(self.inputs, "chamber_wall_allow_pa", 120e6),
                getattr(self.inputs, "chamber_wall_safety_factor", 1.5),
                q_throat_W_m2=_q_thr_W,
                coolant_pressure_pa=p_ch4_pump_out,
                k_liner_WmK=_k_liner,
                sigma_jacket_pa=_sig_jacket,
                channel_height_mm=float(cooling_result.outputs.get("channel_height_mm", 0.0) or 0.0),
                n_channels=int(cooling_result.outputs.get("n_channels", 0) or 0),
                channel_width_mm=float(getattr(self.inputs, "cooling_channel_width_m", 0.0) or 0.0) * 1000.0,
                conv_half_angle_deg=getattr(self.inputs, "chamber_conv_half_angle_deg", 30.0),
            )
            _cc["cc_wall_thickness_mm"] = _ch_geo.wall_thickness_mm
            _cc["cc_wall_liner_mm"] = _ch_geo.inner_wall_thickness_mm
            _cc["cc_wall_channel_mm"] = _ch_geo.cooling_channel_height_mm
            _cc["cc_wall_jacket_mm"] = _ch_geo.outer_wall_thickness_mm
            chamber_result.notes.append(
                f"  分层壁厚 t_wall ≈ {_ch_geo.wall_thickness_mm:.1f} mm = "
                f"内衬 {_ch_geo.inner_wall_thickness_mm:.1f}（传热限）+ "
                f"通道 {_ch_geo.cooling_channel_height_mm:.1f} + "
                f"外壳 {_ch_geo.outer_wall_thickness_mm:.1f}（Barlow 对冷却剂压力）"
                "（与几何估算页同口径，V19 起唯一壁厚）")
        except Exception:
            pass   # 分层定厚失败不影响主结果（壁厚仅信息量输出）
        chamber_result.outputs.update(_cc)
        chamber_result.notes.extend(_cc_notes)
        chamber_result.notes.extend(_cc_validation)

        # ── 喷注器设计（复用上方 chamber_sizing 算出的室直径）──
        chamber_radius = _cc["cc_diameter_m"] / 2.0

        # ── 气-气喷注（FFSC）：主燃室入口是两股热气，不是液体 ──────────────
        # 富氧热气（富氧涡轮排气）+ 富燃热气（富燃涡轮排气），各用其真实气密度
        # （理想气体 ρ=P·MW/(R·T)，MW 取自涡轮入口气体、T/P 取排气态）。
        # 这样喷射速度/动量比/孔径才与气-气工质一致，而非按液氧/液甲烷雾化估算。
        _R_UNIV = 8314.462  # J/(kmol·K)
        _T_ox_inj  = max(float(ox_preburner_out.temperature),   50.0)
        _T_fu_inj  = max(float(fuel_preburner_out.temperature),  50.0)
        _MW_ox_inj = float(ox_turbine_result.outputs.get("molar_mass_in", 30.0) or 30.0)
        _MW_fu_inj = float(fuel_turbine_result.outputs.get("molar_mass_in", 16.0) or 16.0)
        rho_ox_gas_inj   = max(ox_preburner_out.pressure   * _MW_ox_inj / (_R_UNIV * _T_ox_inj), 1.0)
        rho_fuel_gas_inj = max(fuel_preburner_out.pressure * _MW_fu_inj / (_R_UNIV * _T_fu_inj), 1.0)

        # 主喷注器压降 = 涡轮排气压力 − Pc = turbine_exhaust_dp_ratio · Pc。
        # 必须与压力网络同口径（cycle 里 p_turb_exh = Pc·(1+ratio)），否则会出现
        # "排气压力 < Pc + 喷注器压降" 的假性亏空（旧版燃侧用 0.13×Pc 但排气只给
        # 0.093×Pc，差 ~12 bar）。这里直接用排气压降比，保证喷注器孔径与真实可用
        # 压降一致；Piunti 2022 Table 14/15：富氧≈24.3%、富燃≈9.3%（密度差异所致）。
        dp_ratio_ox_inj   = float(getattr(self.inputs, "ox_turbine_exhaust_dp_ratio", 0.243))
        dp_ratio_fuel_inj = float(getattr(self.inputs, "fuel_turbine_exhaust_dp_ratio", 0.093))
        injector = Injector(
            mdot_ox         = ox_preburner_out.mass_flow,    # 富氧热气流(ox-rich gas)
            mdot_fuel       = fuel_preburner_out.mass_flow,  # 富燃热气流(fuel-rich gas)
            pc              = self.inputs.pc,
            dp_ratio_ox     = dp_ratio_ox_inj,
            dp_ratio_fuel   = dp_ratio_fuel_inj,
            chamber_radius  = chamber_radius,
            rho_ox          = rho_ox_gas_inj,
            rho_fuel        = rho_fuel_gas_inj,
        )
        injector_result = injector.evaluate()
        components[injector_result.name] = injector_result

        w_turb_ox = ox_turbine_result.outputs["turbine_power_W"]
        w_turb_fuel = fuel_turbine_result.outputs["turbine_power_W"]
        w_pump_ox = lox_pump_result.outputs["power_W"]
        w_pump_fuel = fuel_pump_result.outputs["power_W"]
        w_turb_total = w_turb_ox + w_turb_fuel
        w_pump_total = w_pump_ox + w_pump_fuel

        ox_side_balance = w_turb_ox / max(w_pump_ox, 1e-9)
        fuel_side_balance = w_turb_fuel / max(w_pump_fuel, 1e-9)
        total_balance = w_turb_total / max(w_pump_total, 1e-9)

        thrust_calc = nozzle_result.outputs["thrust_N"]
        isp_calc = nozzle_result.outputs["Isp_s"]
        isp_vac_calc    = nozzle_result.outputs.get("Isp_vac_s", isp_calc)
        thrust_vac_calc = nozzle_result.outputs.get("thrust_vac_N", thrust_calc)
        fuel_split_solved = mdot_fuel_to_ox_preburner / max(mdot_fuel, 1e-9)
        ox_split_solved = mdot_ox_to_ox_preburner / max(mdot_ox, 1e-9)
        ox_to_fuel_split_solved = mdot_ox_to_fuel_preburner / max(mdot_ox, 1e-9)

        t_comb_ox = ox_preburner_result.outputs["T_combustion_K"]
        t_comb_fuel = fuel_preburner_result.outputs["T_combustion_K"]

        # ── 有效注射 O/F 诊断（对应论文 3.5.1 的 O2_mf / CH4_mf 分析）──────
        # 论文用两个系数近似：O2_mf=0.91, CH4_mf=0.72（来自 NASA CEA 简化输出）
        # 本程序 Cantera 直接给出完整组分，此处从排气组分中提取，等价但更精确。
        #
        # 符号对应关系（论文 Table 10 命名）：
        #   ṁ_LO = 富氧预燃室排气总质量流量（进入主燃烧室氧化剂侧）
        #   ṁ_LF = 富燃预燃室排气总质量流量（进入主燃烧室燃料侧）
        mdot_lo = ox_preburner_out.mass_flow    # ṁ_LO [kg/s]
        mdot_lf = fuel_preburner_out.mass_flow  # ṁ_LF [kg/s]

        # 从 Cantera 组分字典提取未反应的纯推进剂质量分数
        comp_ox_exhaust   = ox_preburner_out.composition   or {}
        comp_fuel_exhaust = fuel_preburner_out.composition or {}
        o2_mf   = comp_ox_exhaust.get("O2",  0.0)   # 富氧排气中 O2 质量分数
        ch4_mf  = comp_fuel_exhaust.get("CH4", 0.0) # 富燃排气中 CH4 质量分数

        # 到达主燃烧室喷注面的有效反应物质量流量
        effective_o2_mdot  = mdot_lo * o2_mf
        effective_ch4_mdot = mdot_lf * ch4_mf
        # 有效注射 O/F（主燃烧室实际参与燃烧的 O2/CH4 质量比）
        effective_of_cc = effective_o2_mdot / max(effective_ch4_mdot, 1e-9)

        # 经验模式（Cantera 不可用时 composition 为空或纯推进剂）：
        # o2_mf=0 则说明 Cantera 未提供，改用名义 O/F 估算
        if o2_mf < 1e-6 and ch4_mf < 1e-6:
            effective_of_cc = self.inputs.of_ratio  # 回退到整机 O/F

        cycle_warnings = list(self._warnings)

        if not (40.0 <= mr_ox_preburner <= 80.0):
            cycle_warnings.append(f"⚠ 富氧预燃室 O/F={mr_ox_preburner:.2f} 超出建议范围 40–80。")
        if not (0.05 <= mr_fuel_preburner <= 0.30):
            cycle_warnings.append(f"⚠ 富燃预燃室 O/F={mr_fuel_preburner:.3f} 超出建议范围 0.05–0.30。")
        if t_comb_ox > self.inputs.ox_preburner_temp_limit:
            cycle_warnings.append(
                f"⚠ 富氧预燃室燃烧温度 {t_comb_ox:.0f} K 超过涡轮材料限值 "
                f"{self.inputs.ox_preburner_temp_limit:.0f} K（O/F={mr_ox_preburner:.2f}, phi={phi_ox_preburner:.3f}）。"
            )
        if t_comb_fuel > self.inputs.fuel_preburner_temp_limit:
            cycle_warnings.append(
                f"⚠ 富燃预燃室燃烧温度 {t_comb_fuel:.0f} K 超过涡轮材料限值 "
                f"{self.inputs.fuel_preburner_temp_limit:.0f} K（O/F={mr_fuel_preburner:.3f}, phi={phi_fuel_preburner:.2f}）。"
            )

        # ── 交叉流供给压力可行性后验校核（V14）──────────────────────────
        # FFSC 必要不等式：交叉小流量必须能"流到"对侧预燃室喷注面上游
        # （供给压力 ≥ p_pre + Δp_inj）。V8 把两条泵支路按主目的地解耦后，
        # 该约束不再由构造保证，需显式校核；欠压时节流阀/孔板无法补头，
        # 只能靠独立 kick-pump（功率未计入主轴平衡）或把取流点前移。
        # 口径与主路压力预算对称：供给侧扣 dp_valve，需求侧 = p_pre + dp_inj。
        crossflow_margin_fuel = (cooled_fuel_state.pressure - dp_valve) - (p_pre_ox + dp_inj_ox)
        crossflow_margin_lox = (p_lox_pump_out - dp_valve) - (p_pre_fuel + dp_inj_fuel)
        if crossflow_margin_fuel < 0.0:
            _rho_cf = cooled_fuel_state.density if (cooled_fuel_state.density or 0.0) > 0.0 else 460.0
            _kick_w_fuel = mdot_fuel_to_ox_preburner * (-crossflow_margin_fuel) / (_rho_cf * 0.70)
            cycle_warnings.append(
                f"⚠ 交叉流欠压：CH4→富氧预燃室 可用 {(cooled_fuel_state.pressure - dp_valve)/1e6:.2f} MPa "
                f"< 喷注需求 {(p_pre_ox + dp_inj_ox)/1e6:.2f} MPa"
                f"（亏 {-crossflow_margin_fuel/1e6:.2f} MPa @ {mdot_fuel_to_ox_preburner:.1f} kg/s）。"
                f"需独立 kick-pump ≈{_kick_w_fuel/1e6:.3f} MW（未计入主轴平衡），"
                f"或将取流点移到冷却套上游（CH4 泵出口 {p_ch4_pump_out/1e6:.2f} MPa）。"
            )
        if crossflow_margin_lox < 0.0:
            _rho_cl = lox_pump_out.density if (lox_pump_out.density or 0.0) > 0.0 else 1200.0
            _kick_w_lox = mdot_ox_to_fuel_preburner * (-crossflow_margin_lox) / (_rho_cl * 0.70)
            cycle_warnings.append(
                f"⚠ 交叉流欠压：LOX→富燃预燃室 可用 {(p_lox_pump_out - dp_valve)/1e6:.2f} MPa "
                f"< 喷注需求 {(p_pre_fuel + dp_inj_fuel)/1e6:.2f} MPa"
                f"（亏 {-crossflow_margin_lox/1e6:.2f} MPa @ {mdot_ox_to_fuel_preburner:.1f} kg/s）。"
                f"需独立 kick-pump ≈{_kick_w_lox/1e6:.3f} MW（未计入主轴平衡）。"
            )

        topology_nodes, topology_edges = self._build_topology()

        summary = {
            "solve_mode": 2.0 if target_mode else 1.0,
            "solve_mode_text": "目标预燃室混合比模式" if target_mode else "自由分流功率闭合模式",
            "thermo_source": 1.0 if CANTERA_AVAILABLE else 0.0,
            "mdot_total_kg_s": mdot_total,
            "mdot_fuel_kg_s": mdot_fuel,
            "mdot_ox_kg_s": mdot_ox,
            "mdot_fuel_to_ox_preburner_kg_s": mdot_fuel_to_ox_preburner,
            "mdot_ox_to_ox_preburner_kg_s": mdot_ox_to_ox_preburner,
            "mdot_fuel_to_fuel_preburner_kg_s": mdot_fuel_to_fuel_preburner,
            "mdot_ox_to_fuel_preburner_kg_s": mdot_ox_to_fuel_preburner,
            # 保留旧键，避免 GUI/导出兼容性问题
            "mdot_fuel_to_ox_pb_kg_s": mdot_fuel_to_ox_preburner,
            "mdot_ox_to_ox_pb_kg_s": mdot_ox_to_ox_preburner,
            "mdot_fuel_to_fuel_pb_kg_s": mdot_fuel_to_fuel_preburner,
            "mdot_ox_to_fuel_pb_kg_s": mdot_ox_to_fuel_preburner,
            "ox_preburner_mr": mr_ox_preburner,
            "fuel_preburner_mr": mr_fuel_preburner,
            "ox_preburner_mr_target": self.inputs.ox_preburner_mr_target,
            "fuel_preburner_mr_target": self.inputs.fuel_preburner_mr_target,
            "phi_ox_preburner": phi_ox_preburner,
            "phi_fuel_preburner": phi_fuel_preburner,
            "fuel_split_to_ox_preburner": fuel_split_solved,
            "ox_split_to_ox_preburner": ox_split_solved,
            "ox_split_to_fuel_preburner": ox_to_fuel_split_solved,
            "ox_preburner_pressure_ratio": ox_pr,
            "fuel_preburner_pressure_ratio": fuel_pr,
            # 预燃室绝对压力 [Pa]（= ratio × pc；供拓扑图等显示，避免显示 "—"）
            "ox_preburner_pressure_Pa": p_pre_ox,
            "fuel_preburner_pressure_Pa": p_pre_fuel,
            # V8：新增涡轮压比 πT = p_pre / p_turb_exh（求解主变量）
            "ox_turbine_pi_t": pi_t_ox_val,
            "fuel_turbine_pi_t": pi_t_fuel_val,
            "ox_turbine_exhaust_dp_ratio": getattr(self.inputs, "ox_turbine_exhaust_dp_ratio", 0.0),
            "fuel_turbine_exhaust_dp_ratio": getattr(self.inputs, "fuel_turbine_exhaust_dp_ratio", 0.0),
            "ox_turbine_exhaust_pressure_Pa": p_ox_turbine_exhaust,
            "fuel_turbine_exhaust_pressure_Pa": p_fuel_turbine_exhaust,
            "p_pump_out_Pa": max(p_lox_pump_out, p_ch4_pump_out),
            "lox_pump_out_pressure_Pa": p_lox_pump_out,
            "fuel_pump_out_pressure_Pa": p_ch4_pump_out,
            "lox_pump_out_pressure_MPa": p_lox_pump_out / 1e6,
            "fuel_pump_out_pressure_MPa": p_ch4_pump_out / 1e6,
            "injector_dp_ox_MPa": dp_inj_ox / 1e6,
            "injector_dp_fuel_MPa": dp_inj_fuel / 1e6,
            "cooling_dp_MPa": dp_cooling / 1e6,
            "valve_line_dp_MPa": dp_valve / 1e6,
            # ── 增压输送系统（贮箱 → 增压泵 → 主泵）─────────────
            "tank_pressure_bar":        p_tank / 1e5,
            "boost_out_pressure_bar":   p_boost_out / 1e5,
            "main_pump_inlet_pressure_bar": p_pump_inlet / 1e5,
            "boost_pump_dp_bar":        dp_boost / 1e5,
            "boost_pump_ox_power_MW":   p_boost_lox / 1e6,
            "boost_pump_fuel_power_MW": p_boost_fuel / 1e6,
            "boost_pump_total_power_MW": (p_boost_lox + p_boost_fuel) / 1e6,
            "boost_pump_efficiency":    eta_boost,
            # 增压泵吸入空化校核（贮箱处，全链最低 NPSH）
            "boost_npsha_ox_m":         npshA_boost_lox,
            "boost_npsha_fuel_m":       npshA_boost_fuel,
            "boost_nss_ox_eu":          nss_boost_lox_eu,
            "boost_nss_fuel_eu":        nss_boost_fuel_eu,
            "boost_pump_speed_assumed_rpm": n_boost,
            # 交叉流供给压力裕度（V14 后验校核；负值=欠压，详见 warnings）
            "crossflow_fuel_to_oxpb_margin_MPa": crossflow_margin_fuel / 1e6,
            "crossflow_lox_to_fuelpb_margin_MPa": crossflow_margin_lox / 1e6,
            "pressure_network_note": (
                f"贮箱 {p_tank/1e5:.1f} bar → 增压泵 → {p_boost_out/1e5:.1f} bar "
                f"→ 短管 −{feed_dp/1e5:.1f} bar → 主泵入口 {p_pump_inlet/1e5:.1f} bar；"
                f"增压泵总功率 {(p_boost_lox+p_boost_fuel)/1e6:.2f} MW（不计入主轴平衡）。"
            ),
            "ox_preburner_T_combustion_K": t_comb_ox,
            "fuel_preburner_T_combustion_K": t_comb_fuel,
            # ── 有效注射 O/F 诊断（论文 3.5.1 等价量） ─────────────────
            # 对应论文符号：ṁ_LO, ṁ_LF, O2_mf, CH4_mf
            "mdot_LO_ox_exhaust_kg_s": mdot_lo,
            "mdot_LF_fuel_exhaust_kg_s": mdot_lf,
            "o2_mass_frac_in_ox_exhaust": o2_mf,
            "ch4_mass_frac_in_fuel_exhaust": ch4_mf,
            "effective_o2_to_cc_kg_s": effective_o2_mdot,
            "effective_ch4_to_cc_kg_s": effective_ch4_mdot,
            "effective_injector_OF": effective_of_cc,
            # 注：论文 O2_mf=0.91, CH4_mf=0.72 是 NASA CEA 的简化输出；
            # 本程序 Cantera 给出完整物种组分，精度更高。
            "ox_turbine_power_MW": w_turb_ox / 1e6,
            "fuel_turbine_power_MW": w_turb_fuel / 1e6,
            "ox_pump_power_MW": w_pump_ox / 1e6,
            "fuel_pump_power_MW": w_pump_fuel / 1e6,
            "turbine_power_MW": w_turb_total / 1e6,
            "pump_power_MW": w_pump_total / 1e6,
            "ox_side_power_balance_ratio": ox_side_balance,
            "fuel_side_power_balance_ratio": fuel_side_balance,
            "power_balance_ratio": total_balance,
            "ox_side_power_error_percent": 100.0 * (ox_side_balance - 1.0),
            "fuel_side_power_error_percent": 100.0 * (fuel_side_balance - 1.0),
            "power_balance_error_percent": 100.0 * (total_balance - 1.0),
            "chamber_temperature_K": chamber_state.temperature,
            "chamber_gamma": gamma_nozzle,
            "chamber_molar_mass": mw_nozzle,
            "thrust_N": thrust_calc,                 # 海平面推力(与目标推力同口径)
            "Isp_s": isp_calc,                       # 海平面比冲(测试口径)
            "Isp_vac_s": isp_vac_calc,               # 真空比冲(发动机内禀属性)
            "thrust_vac_N": thrust_vac_calc,         # 真空推力
            "nozzle_flow_model": nozzle_result.outputs.get("flow_model", 0.0),
            "target_thrust_N": self.inputs.thrust,
            "thrust_error_percent": 100.0 * (thrust_calc - self.inputs.thrust) / max(self.inputs.thrust, 1e-9),
            # ── 补 GUI 拓扑图所需的三个键(原来缺失,导致图上显示 "—") ──
            "chamber_pressure_Pa":      self.inputs.pc,
            "ox_turbine_exhaust_T_K":   ox_turbine_result.outputs["T_exhaust_K"],
            "fuel_turbine_exhaust_T_K": fuel_turbine_result.outputs["T_exhaust_K"],
        }

        return CycleResults(
            inputs=self.inputs, states=states, components=components,
            summary=summary, topology_nodes=topology_nodes,
            topology_edges=topology_edges, warnings=cycle_warnings,
        )

    # =====================================================
    # 5. 残差与求解器
    # =====================================================

    def residual_vector(self, x0: float, x1: float, x2: float):
        checkpoint()
        results = self._evaluate_once(x0, x1, x2)
        r1 = results.summary["thrust_N"] - self.inputs.thrust
        r2 = results.components["ox_preburner_turbine"].outputs["turbine_power_W"] - results.components["ox_pump"].outputs["power_W"]
        r3 = results.components["fuel_preburner_turbine"].outputs["turbine_power_W"] - results.components["fuel_pump"].outputs["power_W"]
        return [r1, r2, r3], results

    @staticmethod
    def _solve_linear_3x3(a, b):
        m = [row[:] + [rhs] for row, rhs in zip(a, b)]
        n = 3
        for i in range(n):
            checkpoint()
            pivot = max(range(i, n), key=lambda k: abs(m[k][i]))
            if abs(m[pivot][i]) < 1e-14:
                raise RuntimeError("Jacobian 奇异")
            if pivot != i:
                m[i], m[pivot] = m[pivot], m[i]
            piv = m[i][i]
            for j in range(i, n + 1):
                checkpoint()
                m[i][j] /= piv
            for k in range(n):
                checkpoint()
                if k == i:
                    continue
                fac = m[k][i]
                for j in range(i, n + 1):
                    checkpoint()
                    m[k][j] -= fac * m[i][j]
        return [m[i][n] for i in range(n)]

    def _sanitize_vector(self, x: list[float]) -> list[float]:
        x0 = self._sanitize_mdot(x[0])
        if self._target_mr_mode():
            # V8：x[1], x[2] 是涡轮压比 πT_ox / πT_fuel
            return [
                x0,
                self._sanitize_turbine_pressure_ratio(x[1]),
                self._sanitize_turbine_pressure_ratio(x[2]),
            ]
        return [
            x0,
            self._sanitize_fuel_to_ox_preburner(x[1], x0),
            self._sanitize_ox_to_fuel_preburner(x[2], x0),
        ]

    def _finite_difference_jacobian(self, x, r):
        jac = [[0.0] * 3 for _ in range(3)]
        for j in range(3):
            checkpoint()
            x_pert = x[:]
            base = abs(x[j]) if abs(x[j]) > 1e-9 else 1.0
            # V8 修正：原 1e-3 太小，πT 量级 ~2 时实际步长 ~2e-3，被数值噪声淹没。
            # 改用 mdot 5e-4 相对 / πT 0.02 绝对 (~1% πT)，足以分辨真实斜率。
            dx = (5e-4 * base) if j == 0 else 0.02
            if self._target_mr_mode() and j > 0:  # 强制 πT 微扰 ≥ 0.02
                dx = max(dx, 0.02)
            x_pert[j] += dx
            x_pert = self._sanitize_vector(x_pert)
            r_pert, _ = self.residual_vector(*x_pert)
            actual_dx = x_pert[j] - x[j]
            actual_dx = actual_dx if abs(actual_dx) > 1e-12 else dx
            for i in range(3):
                checkpoint()
                jac[i][j] = (r_pert[i] - r[i]) / actual_dx
        return jac

    def _normalized_residual_norm(self, residuals):
        scales = [max(abs(self.inputs.thrust), 1e-9), 10.0e6, 10.0e6]
        return max(abs(residuals[i]) / scales[i] for i in range(3))

    def solve_cycle(
        self,
        tol_rel: float = 1e-4,
        max_iter: int = 50,
        verbose: bool = False,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        x_init: Optional[list[float]] = None,
    ):
        # V8：支持 warm start（用于 continuation）
        checkpoint()
        if x_init is None:
            x = self._initial_guess_vector()
        else:
            x = self._sanitize_vector([float(v) for v in x_init])
        residuals, results = self.residual_vector(*x)

        # 停滞早退:跟踪 8 步前的残差,如果 norm 没下降 ≥ 10×,认定发散,提前终止。
        # 用于 sweep 扫描中物理上闭合不了的极端工况(如 ox-PB O/F>80,涡轮焓
        # 降不足以驱动泵),避免 Newton 撞 max_iter × 多轮 Picard 浪费 30 倍时间。
        STAGNATION_WINDOW = 8
        STAGNATION_RATIO  = 0.1   # 要求 norm 在 8 步内至少降到 10% 才算"有进展"
        norm_history: list[float] = []

        for it in range(max_iter):
            checkpoint()
            norm0 = self._normalized_residual_norm(residuals)
            norm_history.append(norm0)
            if progress_callback is not None:
                try:
                    progress_callback(it, max_iter, norm0)
                except Exception:
                    pass

            if verbose:
                if self._target_mr_mode():
                    print(f"[Iter {it:02d}] mdot={x[0]:.2f} πT_ox={x[1]:.4f} πT_fuel={x[2]:.4f} norm={norm0:.3e}")
                else:
                    print(f"[Iter {it:02d}] mdot={x[0]:.2f} f→ox={x[1]:.3f} ox→fuel={x[2]:.3f} norm={norm0:.3e}")

            # 停滞检测(在 STAGNATION_WINDOW 步后,且当前 norm 已远超容差时启用)
            if len(norm_history) > STAGNATION_WINDOW and norm0 > tol_rel * 100:
                norm_baseline = norm_history[-STAGNATION_WINDOW - 1]
                if norm0 > STAGNATION_RATIO * norm_baseline:
                    # 8 步内 norm 没掉到 10%,认定停滞 → 提前终止
                    results.summary["solver_iterations"] = float(it)
                    results.summary["solver_residual_norm"] = norm0
                    results.summary["solver_warning"] = 1.0
                    results.summary["solver_stalled"]  = 1.0
                    return x, results

            if norm0 < tol_rel:
                results.summary["solver_iterations"] = float(it)
                results.summary["solver_residual_norm"] = norm0
                if progress_callback is not None:
                    try:
                        progress_callback(it, max_iter, norm0)
                    except Exception:
                        pass
                return x, results

            jac = self._finite_difference_jacobian(x, residuals)
            try:
                dx = self._solve_linear_3x3(jac, [-ri for ri in residuals])
            except RuntimeError:
                # 退化步：只做保守修正
                dx = [
                    -0.08 * x[0] * residuals[0] / max(abs(self.inputs.thrust), 1e-9),
                    -0.05 * (residuals[1] / 10.0e6) * (x[1] if self._target_mr_mode() else 1.0),
                    -0.05 * (residuals[2] / 10.0e6) * (x[2] if self._target_mr_mode() else 1.0),
                ]

            # V8：trust-region 单步幅度限制。
            # Newton 在 πT 上容易因 Jacobian 局部不准产生大步长，撞 sanitizer
            # 上下界（1.02 或 10.0）后无法恢复。给每个变量加单步最大相对/绝对
            # 步长上限，强制每一步只走"一小段"，配合后面的 line-search 即可稳定。
            if self._target_mr_mode():
                # mdot 单步 ±15%，πT 单步 ±0.4（约 πT 量级的 20–30%）
                max_step = [0.15 * abs(x[0]), 0.4, 0.4]
            else:
                max_step = [0.15 * abs(x[0]), 0.2 * abs(x[1]) + 1.0, 0.2 * abs(x[2]) + 1.0]
            dx = [self._clamp(dx[i], -max_step[i], max_step[i]) for i in range(3)]

            accepted = False
            for alpha in [1.0, 0.5, 0.25, 0.1, 0.05, 0.02]:
                checkpoint()
                x_trial = self._sanitize_vector([x[i] + alpha * dx[i] for i in range(3)])
                try:
                    r_trial, res_trial = self.residual_vector(*x_trial)
                except Exception:
                    continue
                if self._normalized_residual_norm(r_trial) < norm0:
                    x, residuals, results = x_trial, r_trial, res_trial
                    accepted = True
                    break

            if not accepted:
                x_trial = self._sanitize_vector([x[i] + 0.01 * dx[i] for i in range(3)])
                try:
                    residuals, results = self.residual_vector(*x_trial)
                    x = x_trial
                except Exception:
                    break

        results.summary["solver_iterations"] = float(max_iter)
        results.summary["solver_residual_norm"] = self._normalized_residual_norm(residuals)
        results.summary["solver_warning"] = 1.0
        return x, results

    def evaluate_forward(self):
        checkpoint()
        results = self._evaluate_once(*self._initial_guess_vector())
        self._attach_cooling_marching(results)
        return results

    def evaluate(self, progress_callback: Optional[Callable[[int, int, float], None]] = None):
        """
        设计点求解入口（V11）。

        · 温度目标模式（use_target_preburner_temp=1）：外层一维反求 MR，使两侧
          预燃室出口温度（= 涡轮入口温度）命中目标温度——"顶着材料极限设计"。
        · 否则：直接走 V8 continuation 核心求解（_evaluate_design_core）。
        """
        checkpoint()
        if self._temp_target_mode():
            results = self._evaluate_temp_targeted(progress_callback)
        else:
            results = self._evaluate_design_core(progress_callback)
        # V15：在收敛设计点上跑沿程 marching 后处理（不进 Newton 环，失败仅警告）
        self._attach_cooling_marching(results)
        return results

    def _attach_cooling_marching(self, results) -> None:
        """收敛后运行再生冷却沿程 marching（T_wall(x)/q(x)/p_co(x) 剖面）。"""
        checkpoint()
        if float(getattr(self.inputs, "cooling_marching_enable", 1.0)) < 0.5:
            return
        if not bool(getattr(self.inputs, "cooling_bartz_mode", True)):
            return   # 固定热载荷模式没有喉部几何推算，不做剖面
        from cooling_marching import run_marching_postpass
        run_marching_postpass(results, self.inputs)

    # =====================================================
    # 5b. 温度目标外环（V11）：反求 MR 使涡轮入口温度 = 材料目标
    # =====================================================

    def _mr_for_target_temp(
        self,
        side: str,
        T_target: float,
        T_fuel_in: float,
        T_ox_in: float,
        p_pre: float,
    ) -> float:
        """
        一维反求预燃室混合比 MR(=O/F)，使绝热火焰温度 = T_target。

        预燃室火焰温度对 MR 单调（富氧侧随 O/F 增大而降，富燃侧随 O/F 增大而升），
        且几乎只由 MR 与进口温度决定（强度量、低温段无离解 → 对压力近乎不敏感）。
        故直接对 thermo.combustion_equilibrium 做二分，与预燃室内部同一函数、
        同一进口条件 → 反求出的 MR 喂回循环必复现目标温度。

        端点不跨越 T_target 时（目标超出 MR 区间可达温度跨度），截断到最近端点并告警。
        """
        checkpoint()
        from thermo import combustion_equilibrium

        def temp_at(mr: float) -> float:
            comb = combustion_equilibrium(
                mdot_fuel=1.0, mdot_ox=max(mr, 1e-6),
                T_fuel=T_fuel_in, T_ox=T_ox_in, pressure=p_pre,
            )
            return comb.temperature

        if side == "ox":
            lo, hi = 15.0, 160.0     # 富氧：O/F 大→温度低
        else:
            lo, hi = 0.02, 0.80      # 富燃：O/F 大→温度高
        T_lo, T_hi = temp_at(lo), temp_at(hi)

        # 目标落在端点温度之外 → 截断 + 告警
        if (T_target - T_lo) * (T_target - T_hi) > 0.0:
            nearest = lo if abs(T_target - T_lo) < abs(T_target - T_hi) else hi
            msg = (
                f"⚠ {side} 预燃室目标温度 {T_target:.0f} K 超出 MR∈[{lo:g},{hi:g}] "
                f"可达温度 [{min(T_lo, T_hi):.0f},{max(T_lo, T_hi):.0f}] K，"
                f"MR 截断到 {nearest:g}"
            )
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
            return nearest

        # 单调性无关的括号二分（用相对 T_target 的符号维持括号）
        for _ in range(40):
            checkpoint()
            mid = 0.5 * (lo + hi)
            T_mid = temp_at(mid)
            if (T_mid - T_target) * (T_lo - T_target) > 0.0:
                lo, T_lo = mid, T_mid
            else:
                hi, T_hi = mid, T_mid
            if abs(hi - lo) < 1e-4 * max(abs(hi), 1.0) or abs(T_mid - T_target) < 0.3:
                break
        return 0.5 * (lo + hi)

    def _evaluate_temp_targeted(
        self,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ):
        """
        温度目标外环：反复 [解循环 → 反求 MR 命中目标温度 → 阻尼更新 MR]，
        直到两侧预燃室出口温度均落在目标 ±TEMP_TOL 内。

        因 T_PB 对 mdot/πT 只弱依赖（进口温度随 mdot 微动），外环 2–3 轮即收敛。
        MR 目标在本方法内临时改写，结束时还原，避免污染用户输入。
        """
        checkpoint()
        T_ox_tgt = float(self.inputs.ox_preburner_temp_target)
        T_fu_tgt = float(self.inputs.fuel_preburner_temp_target)

        saved_mr_ox = self.inputs.ox_preburner_mr_target
        saved_mr_fu = self.inputs.fuel_preburner_mr_target

        MR_OUTER_MAX = 6
        TEMP_TOL     = 3.0    # K
        RELAX        = 0.7    # MR 阻尼，防止外环震荡

        # 外环进度包装：把每轮内层 0–100 映射到 [outer/MAX, (outer+1)/MAX]，
        # 避免温度模式下进度条反复归零（看起来像卡死）。
        def _outer_progress(outer_idx: int):
            if progress_callback is None:
                return None
            lo = outer_idx / MR_OUTER_MAX * 100.0
            hi = (outer_idx + 1) / MR_OUTER_MAX * 100.0
            def cb(it: int, max_it: int, norm: float):
                frac = it / max(max_it, 1)
                try:
                    progress_callback(int(lo + (hi - lo) * frac), 100, norm)
                except Exception:
                    pass
            return cb

        results = None
        outer = 0
        try:
            for outer in range(MR_OUTER_MAX):
                checkpoint()
                results = self._evaluate_design_core(_outer_progress(outer))
                s = results.summary
                T_ox_now = s.get("ox_preburner_T_combustion_K", 0.0)
                T_fu_now = s.get("fuel_preburner_T_combustion_K", 0.0)
                if (abs(T_ox_now - T_ox_tgt) < TEMP_TOL
                        and abs(T_fu_now - T_fu_tgt) < TEMP_TOL):
                    break

                # 反求所需进口条件（与预燃室实际进口同源）
                T_fuel_in = results.states["fuel_after_cooling"].temperature
                T_ox_in   = results.states["lox_after_pump"].temperature
                p_pre_ox  = s.get("ox_preburner_pressure_ratio", 1.5) * self.inputs.pc
                p_pre_fu  = s.get("fuel_preburner_pressure_ratio", 2.0) * self.inputs.pc

                new_mr_ox = self._mr_for_target_temp("ox",   T_ox_tgt, T_fuel_in, T_ox_in, p_pre_ox)
                new_mr_fu = self._mr_for_target_temp("fuel", T_fu_tgt, T_fuel_in, T_ox_in, p_pre_fu)

                # 阻尼更新 MR 目标（下一轮 _evaluate_design_core 会读取）
                self.inputs.ox_preburner_mr_target = (
                    RELAX * new_mr_ox + (1.0 - RELAX) * self.inputs.ox_preburner_mr_target
                )
                self.inputs.fuel_preburner_mr_target = (
                    RELAX * new_mr_fu + (1.0 - RELAX) * self.inputs.fuel_preburner_mr_target
                )

            if results is not None:
                s = results.summary
                T_ox_now = s.get("ox_preburner_T_combustion_K", 0.0)
                T_fu_now = s.get("fuel_preburner_T_combustion_K", 0.0)
                s["preburner_temp_target_mode"]        = 1.0
                s["solve_mode_text"]                   = "温度目标模式（反求 MR 顶材料极限）"
                s["ox_preburner_temp_target_K"]        = T_ox_tgt
                s["fuel_preburner_temp_target_K"]      = T_fu_tgt
                s["ox_preburner_temp_error_K"]         = T_ox_now - T_ox_tgt
                s["fuel_preburner_temp_error_K"]       = T_fu_now - T_fu_tgt
                s["ox_preburner_mr_solved_for_temp"]   = self.inputs.ox_preburner_mr_target
                s["fuel_preburner_mr_solved_for_temp"] = self.inputs.fuel_preburner_mr_target
                s["preburner_temp_outer_iters"]        = float(outer + 1)
                if (abs(T_ox_now - T_ox_tgt) > TEMP_TOL
                        or abs(T_fu_now - T_fu_tgt) > TEMP_TOL):
                    msg = (
                        f"⚠ 温度目标外环未完全收敛（{outer + 1} 轮）："
                        f"T_ox={T_ox_now:.0f}/{T_ox_tgt:.0f} K，"
                        f"T_fuel={T_fu_now:.0f}/{T_fu_tgt:.0f} K"
                    )
                    if msg not in results.warnings:
                        results.warnings.insert(0, msg)
        finally:
            # 还原 MR 目标，避免污染用户输入；solved 值已存入 summary 供显示
            self.inputs.ox_preburner_mr_target   = saved_mr_ox
            self.inputs.fuel_preburner_mr_target = saved_mr_fu

        return results

    def _evaluate_design_core(self, progress_callback: Optional[Callable[[int, int, float], None]] = None):
        """
        V8 设计点求解 —— 采用 thrust/pc 同步缩放的 continuation。

        Continuation 思路：把 Pc 和 thrust 同步缩成 50% → 75% → 100% 分三步解，
        用上一步的解作为下一步的 warm start。高压级燃发动机（Pc=33 MPa）下，
        冷启动 Newton 容易陷入伪稳态；continuation 是标准做法（参考 RPA/ESPSS）。
        """
        checkpoint()
        self._warnings = []
        base_pc = self.inputs.pc
        base_thrust = self.inputs.thrust
        # 第二步修复:保存 chamber_temp_guess。Picard 外环过程中会被临时改写,
        # 结束时还原,避免污染用户输入框里的初值。
        original_tc_guess = self.inputs.chamber_temp_guess
        # V6.1:同时把真实 γ/MW 也纳入 Picard 反馈。冷却 Bartz 的喉部面积
        # A* ∝ √(R/γ-项),原来固定用 inputs.chamber_gamma/molar_mass(fallback
        # 1.18/21),而喷管用 Cantera 实算值(~1.19/22.6),导致冷却卡喉部直径
        # 比喷管/几何卡大 ~3.9%(250 vs 245.5 mm)。反馈后两者收敛到一致。
        original_gamma   = self.inputs.chamber_gamma
        original_mw      = self.inputs.molar_mass

        ratios = [0.5, 0.75, 1.0]
        x_warm: Optional[list[float]] = None
        results = None
        x_solved: list[float] = []

        # ── 冷却 ↔ 主燃室 Tc Picard 外环参数 ─────────────────────────
        # 现状:_evaluate_once 用 inputs.chamber_temp_guess 作冷却 Bartz 热流
        # 的 T_aw 基准,而真实 chamber_state.temperature 是 Newton 收敛后才算出。
        # 修法:最后一段 continuation 上加阻尼 Picard 外环,使二者自洽。
        # 通常 2-3 轮收敛;阻尼 0.5 防震荡;Newton 用上一轮解热启动,内层收敛飞快。
        TC_OUTER_MAX  = 5
        TC_OUTER_TOL  = 5.0   # K
        TC_RELAX      = 0.5
        tc_outer_iters    = 0
        tc_outer_residual = float("nan")

        # ── 进度映射:连续感优化版 ─────────────────────────────────
        # 思路:Newton 典型 2–3 步收敛,如果按 it/max_iter(=50) 算,每段只
        # 填 2–6% 就跳到下一段,视觉上很跳。改成两条:
        #   (a) **段内按残差对数映射**:用 -log10(norm)/4 当填充比例,每步
        #       残差降一个数量级,进度就走 25%,而不是 2%。
        #   (b) **Picard 轮权重不均分**:第 1 轮 Picard 真正干活(占 stage 3
        #       的 60%),后续轮微调(25/10/3/2%),避免末几轮把空间留空。
        n_stages_total = len(ratios)
        # Stage 3 内部 Picard 各轮权重(求和=1.0,长度对齐 TC_OUTER_MAX=5)
        _picard_weights = [0.60, 0.25, 0.10, 0.03, 0.02]

        def _wrap_progress(stage_idx: int, picard_idx: int):
            if progress_callback is None:
                return None
            stage_lo = stage_idx * 100.0 / n_stages_total
            stage_hi = (stage_idx + 1) * 100.0 / n_stages_total
            if stage_idx == n_stages_total - 1:
                # 最后一段:Picard 加权分配
                idx = min(picard_idx, len(_picard_weights) - 1)
                cum_lo = sum(_picard_weights[:idx])
                cum_hi = sum(_picard_weights[:idx + 1])
                lo = stage_lo + cum_lo * (stage_hi - stage_lo)
                hi = stage_lo + cum_hi * (stage_hi - stage_lo)
            else:
                lo, hi = stage_lo, stage_hi

            def cb(it: int, max_it: int, norm: float):
                # 段内填充比例:基于残差对数。
                # norm >= 1.0 → 0%, norm <= tol(1e-4) → 100%, 中间 log 线性。
                import math
                if norm <= 1.0e-4:
                    frac = 1.0
                elif norm >= 1.0:
                    frac = 0.0
                else:
                    frac = -math.log10(max(norm, 1e-12)) / 4.0
                    frac = max(0.0, min(1.0, frac))
                mapped = lo + (hi - lo) * frac
                progress_callback(int(mapped), 100, norm)
            return cb

        # 累计所有 solve_cycle 调用的 Newton 步数。solve_cycle 单次只报告
        # 本轮 it(被后面 Picard/continuation 轮覆盖),如果直接显示就只看到
        # 最后一轮(暖启动,~1 步)的值,严重低估实际工作量。
        total_newton_iters = 0
        n_inner_solves = 0

        try:
            stalled = False    # 任意一次 solve_cycle 停滞早退,后续 stage/Picard 都跳过
            for stage, ratio in enumerate(ratios):
                checkpoint()
                if stalled:
                    break
                self.inputs.pc = base_pc * ratio
                self.inputs.thrust = base_thrust * ratio
                is_last = (stage == len(ratios) - 1)

                if not is_last:
                    # 中间 continuation 阶段:单次求解,Tc 不反馈
                    x_solved, results = self.solve_cycle(
                        progress_callback=_wrap_progress(stage, 0),
                        x_init=x_warm,
                    )
                    total_newton_iters += int(results.summary.get("solver_iterations", 0))
                    n_inner_solves += 1
                    if results.summary.get("solver_stalled", 0.0) > 0.5:
                        stalled = True
                        break
                else:
                    # 最后阶段:Picard 外环让 chamber_temp_guess 与真实 Tc 自洽
                    for tc_iter in range(TC_OUTER_MAX):
                        checkpoint()
                        x_solved, results = self.solve_cycle(
                            progress_callback=_wrap_progress(stage, tc_iter),
                            x_init=x_warm,
                        )
                        total_newton_iters += int(results.summary.get("solver_iterations", 0))
                        n_inner_solves += 1
                        # Newton 停滞 → 物理上闭合不了,后续 Picard 无意义
                        if results.summary.get("solver_stalled", 0.0) > 0.5:
                            stalled = True
                            break
                        new_tc = results.summary.get(
                            "chamber_temperature_K", self.inputs.chamber_temp_guess
                        )
                        tc_outer_residual = abs(new_tc - self.inputs.chamber_temp_guess)
                        tc_outer_iters = tc_iter + 1
                        # 在 summary 里记录本轮 Picard 用的 guess 和残差(诊断用)
                        results.summary["tc_outer_iterations"]      = float(tc_outer_iters)
                        results.summary["tc_outer_residual_K"]      = tc_outer_residual
                        results.summary["chamber_temp_guess_used_K"] = self.inputs.chamber_temp_guess
                        # 把真实 γ/MW 反馈给下一轮冷却(直接更新,二者稳定)
                        new_gamma = results.summary.get("chamber_gamma")
                        new_mw    = results.summary.get("chamber_molar_mass")
                        if new_gamma and new_gamma > 1.0:
                            self.inputs.chamber_gamma = float(new_gamma)
                        if new_mw and new_mw > 0:
                            self.inputs.molar_mass = float(new_mw)
                        if tc_outer_residual < TC_OUTER_TOL:
                            break
                        # 阻尼 Picard 更新:next = (1-r)*old + r*new
                        self.inputs.chamber_temp_guess = (
                            (1.0 - TC_RELAX) * self.inputs.chamber_temp_guess
                            + TC_RELAX * new_tc
                        )
                        x_warm = x_solved  # 热启动下一轮内层 Newton

                next_ratio = ratios[stage + 1] / ratio if stage + 1 < len(ratios) else 1.0
                x_warm = [x_solved[0] * next_ratio, x_solved[1], x_solved[2]]

            # 用累计步数覆盖 summary 里最末轮的局部值,保留最末轮值作诊断
            results.summary["solver_iterations_last"]    = results.summary.get("solver_iterations", 0)
            results.summary["solver_iterations"]         = float(total_newton_iters)
            results.summary["solver_inner_solves"]       = float(n_inner_solves)
        finally:
            self.inputs.pc = base_pc
            self.inputs.thrust = base_thrust
            # 还原 chamber_temp_guess。results.summary 里 chamber_temperature_K 是
            # Picard 收敛后的真实 Tc,所以 GUI/导出/拓扑图不受还原影响。
            self.inputs.chamber_temp_guess = original_tc_guess
            self.inputs.chamber_gamma      = original_gamma
            self.inputs.molar_mass         = original_mw

        s = results.summary
        if self._target_mr_mode():
            s["mdot_solved_kg_s"] = x_solved[0]
            s["ox_turbine_pi_t_solved"] = x_solved[1]
            s["fuel_turbine_pi_t_solved"] = x_solved[2]
            s["mdot_fuel_to_ox_preburner_solved"] = s.get("mdot_fuel_to_ox_preburner_kg_s")
            s["mdot_ox_to_fuel_preburner_solved"] = s.get("mdot_ox_to_fuel_preburner_kg_s")
            s["ox_preburner_pressure_ratio_solved"] = s.get("ox_preburner_pressure_ratio", 0.0)
            s["fuel_preburner_pressure_ratio_solved"] = s.get("fuel_preburner_pressure_ratio", 0.0)
        if bool(s.get("solver_warning", 0.0)):
            msg = (
                "⚠ 求解器未完全收敛：当前结果只适合作为诊断，不应作为设计点。"
                f" 残差范数={s.get('solver_residual_norm', 0.0):.3e}"
            )
            if msg not in results.warnings:
                results.warnings.insert(0, msg)
        return results

    def throttle_sweep(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        record_callback: Optional[Callable[[dict], None]] = None,
    ) -> list[dict]:
        """
        Quasi-steady throttle scan.
        Scales thrust and pc together, re-solves each operating point with the
        same cycle assumptions as the design point.
        """
        checkpoint()
        steps = max(2, int(getattr(self.inputs, "throttle_steps", 5)))
        throttle_min = self._clamp(float(getattr(self.inputs, "throttle_min", 0.4)), 0.05, 0.99)
        sweep: list[dict] = []

        # ── V16（对抗校核 critical 修复）：节流 = 真·固定硬件 ─────────────
        # 旧版（含 V15.1）每个节流点把"推力"和"室压"双双当目标各乘 ratio，再由
        # evaluate() 反算喉部 At=ṁ·c*/Pc。节流时比冲下降 → ṁ 并不随 Pc 同比例缩小，
        # 导出的 At（喉部金属！）漂移 ~10%（40% 点喉部反而比 100% 点更大），等于
        # "每个节流点是另一台尺寸不同的发动机"。
        #
        # 现在：先解 100% 设计点确定固定硬件基准——喉部面积 At_design 与冷却通道
        # 深度。节流只用"推力"一个指令（节流% ≡ 推力%），再反解出令 At 保持 =
        # At_design 的室压 Pc（不动点 Pc←Pc·At_cur/At_design，因 At 对 Pc 单调，
        # 数轮即收敛）。于是 Pc% ≠ 推力%，二者之差即真实节流物理（过膨胀 / 比冲下降）。
        At_design = None
        frozen_h_m = None
        try:
            _design = FFSCCycleModel(copy.deepcopy(self.inputs)).evaluate()
            At_design = float(_design.components["nozzle"].outputs.get("At_m2", 0.0)) or None
            if (bool(getattr(self.inputs, "cooling_channel_auto_size", 1.0))
                    and bool(getattr(self.inputs, "cooling_bartz_mode", True))):
                frozen_h_m = float(
                    _design.components["cooling_channel"].outputs.get("channel_height_mm", 0.0)
                ) / 1000.0
        except Exception:
            At_design = None
            frozen_h_m = None

        def _solve_at(thrust_target: float, pc_val: float):
            """以给定推力目标 + 室压求解一个工况点，冷却通道深度冻结到设计值。

            V19 性能修复：At 反解不动点每工况要跑最多 9 次 evaluate()，中间解全部
            被丢弃——这里强制关 marching 后处理（V19 marching 站数 ~4×，每次白跑
            数百站），收敛后在保留的最终解上补跑**一次** postpass（见循环末尾）。
            """
            pi = copy.deepcopy(self.inputs)
            pi.thrust = thrust_target
            pi.pc = pc_val
            pi.cooling_marching_enable = 0.0
            if frozen_h_m is not None and frozen_h_m > 1e-4:
                pi.cooling_channel_auto_size = 0.0
                pi.cooling_channel_height_m = frozen_h_m
            return pi, FFSCCycleModel(pi).evaluate()

        for i in range(steps):
            checkpoint()
            ratio = throttle_min + (1.0 - throttle_min) * i / max(steps - 1, 1)
            thrust_target = self.inputs.thrust * ratio

            if progress_callback is not None:
                try:
                    progress_callback(i + 1, steps, f"{ratio * 100.0:.0f}%")
                except Exception:
                    pass

            try:
                # 初值：旧的等比例 Pc 作为不动点起点
                pc = self.inputs.pc * ratio
                point_inputs, point_results = _solve_at(thrust_target, pc)
                # ── 反解 Pc 使喉部 At 钉死在设计值（固定硬件）──
                if At_design and At_design > 0.0:
                    for _ in range(8):
                        checkpoint()
                        At_cur = float(
                            point_results.components["nozzle"].outputs.get("At_m2", 0.0))
                        if At_cur <= 0.0 or abs(At_cur / At_design - 1.0) < 5e-4:
                            break
                        pc = pc * (At_cur / At_design)
                        point_inputs, point_results = _solve_at(thrust_target, pc)
                # 最终解补跑一次 marching 后处理（用户开关开启时）——沿程壁温/压降
                # 剖面与警告只算这一次，节流表/深节流壁温分析仍拿到完整数据
                if bool(getattr(self.inputs, "cooling_marching_enable", 1.0)):
                    try:
                        from cooling_marching import run_marching_postpass
                        run_marching_postpass(point_results, point_inputs)
                    except Exception:
                        pass
                residual = float(point_results.summary.get("solver_residual_norm", 1.0))
                converged = residual < 1e-3 and not bool(point_results.summary.get("solver_warning", 0.0))
                sweep.append({
                    "throttle_ratio": ratio,
                    "thrust_target_N": thrust_target,
                    "pc_Pa": point_inputs.pc,
                    "converged": converged,
                    "results": point_results,
                    "error": "",
                })
            except Exception as exc:
                sweep.append({
                    "throttle_ratio": ratio,
                    "thrust_target_N": thrust_target,
                    "pc_Pa": self.inputs.pc * ratio,
                    "converged": False,
                    "results": None,
                    "error": str(exc),
                })

            if record_callback is not None:
                record_callback(sweep[-1])
            checkpoint()

        return sweep

    def altitude_sweep(
        self,
        z_max_m: float = 80000.0,
        n_points: int = 60,
        design_results=None,
    ) -> dict:
        """
        高度特性扫描：固定喷管（设计点几何）下推力 / 比冲随飞行高度变化。

        物理依据：对满流（未分离）喷管，出口压力 Pe 与出口面积 Ae 仅由喷管几何及
        燃烧室状态决定，与飞行高度无关，故推力对环境压力严格线性：
            F(z) = F_vac − P_a(z)·A_e ,   Isp(z) = F(z) / (ṁ·g0)
        其中 F_vac、A_e、P_e、ṁ 均取自一次设计点求解（无需逐点重算燃烧/膨胀）。
        过膨胀分离区（低空、Pe < 0.4·P_a）内真实推力高于此线性外推（分离抑制了过膨胀
        损失），该区间按附着流理想值给出并单独标注。
        """
        checkpoint()
        from atmosphere import (
            pressure_at_altitude, altitude_for_pressure, altitude_grid,
        )
        g0 = 9.80665
        SEP = 0.40   # Summerfield p_sep/p_amb，与 geometry/separation.py 同口径
        P_SL = 101325.0

        res = design_results if design_results is not None else self.evaluate()
        nz = res.components["nozzle"].outputs
        F_vac   = float(nz.get("thrust_vac_N", 0.0))
        Ae      = float(nz.get("Ae_m2", 0.0))
        pe      = float(nz.get("p_exit_Pa", 0.0))
        eps     = float(nz.get("expansion_ratio", 0.0))
        isp_vac = float(nz.get("Isp_vac_s", 0.0))
        p_amb_d = float(nz.get("p_amb_Pa", P_SL))
        mdot    = float(res.summary.get("mdot_total_kg_s", 0.0))
        if mdot <= 0.0:
            mdot = F_vac / max(isp_vac * g0, 1e-9)

        # 关键高度
        z_match   = altitude_for_pressure(pe) if pe > 0.0 else 0.0      # Pe=Pa（完全膨胀）
        sep_at_sl = bool(pe < SEP * P_SL)                              # 海平面是否分离
        z_sep     = altitude_for_pressure(pe / SEP) if sep_at_sl else 0.0  # 此高度以下分离

        points = []
        for z in altitude_grid(z_max_m, n_points):
            checkpoint()
            pa = pressure_at_altitude(z)
            F  = F_vac - pa * Ae
            isp = F / max(mdot * g0, 1e-9)
            if pe > 1.01 * pa:
                state = "欠膨胀"
            elif pe < 0.99 * pa:
                state = "过膨胀"
            else:
                state = "完全膨胀"
            points.append({
                "altitude_m":  z,
                "altitude_km": z / 1000.0,
                "p_amb_Pa":    pa,
                "thrust_N":    F,
                "thrust_kN":   F / 1e3,
                "Isp_s":       isp,
                "pe_over_pa":  (pe / pa) if pa > 1e-9 else float("inf"),
                "expansion_state": state,
                "separated":   bool(pe < SEP * pa),
            })

        F_sl = F_vac - P_SL * Ae
        info = {
            "F_vac_N": F_vac, "F_sl_N": F_sl, "Ae_m2": Ae, "pe_Pa": pe,
            "eps": eps, "mdot_kg_s": mdot,
            "Isp_vac_s": isp_vac, "Isp_sl_s": F_sl / max(mdot * g0, 1e-9),
            "p_amb_design_Pa": p_amb_d,
            "match_altitude_m": z_match,
            "separation_altitude_m": z_sep,
            "separated_at_sea_level": sep_at_sl,
            "z_max_m": z_max_m,
        }
        return {"points": points, "info": info}
