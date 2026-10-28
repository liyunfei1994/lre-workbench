"""
cycle.py

FFSC 全流量液氧甲烷分级燃烧循环系统级模型 —— V7 / PySide6 桌面版后端

本模型按显式供给拓扑、损失预算和热力学假设求解，默认示例不代表已验证的
Raptor 3 实机。预燃室指定温度与指定混合比是两种不同的设计约束：
1. 指定温度：外环在当前入口焓与压力下反求混合比，检查全部耦合层的收敛。
2. 指定混合比：由 O/F 反求交叉分流，内层求总流量与两侧实际涡轮压比。
两种目标模式的内层均闭合推力和两根轴的功率；温度上限另外校核。
历史自由分流模式保留兼容，以总流量及两股交叉分流为未知量。
"""

from __future__ import annotations

from task_control import checkpoint

import copy
import math
import warnings
from typing import Callable, Optional, List

from core import (CycleResults, EngineInputs, FlowState, TopologyEdge, TopologyNode,
                  ValidationError, preburner_temperature_constraint)
from cooling import CoolingChannel
from regen_coupling import close_regen_cycle, cooling_arguments, fixed_contour_inputs
from chamber import MainChamber
from chamber_sizing import size_combustion_chamber
from pump import Pump, fluid_density
from nozzle import Nozzle
from preburner import Preburner
from turbine import Turbine
from injector import Injector
from thermo_data import model_thermo_scope
from thermo import CANTERA_AVAILABLE, COOLPROP_AVAILABLE, available_npsh_pressure, reactant_enthalpy, reactant_temperature_from_enthalpy
from steady_network import solve_design, audit_network, pressure_contract


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

    Ns 的单位约定为 N[rpm]、Q[m³/s]、每级 H[m]；不是无量纲比转速。
    同轴部件采用同一转速不等于已通过泵/涡轮特性图匹配；psi 不参与此公式。
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
        if inputs.propellant_pair != "lox_methane":
            raise ValidationError("FFSC 当前仅支持液氧/甲烷；液氧/煤油代理模型请使用 GG 循环。")
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

    def _feed_reference_densities(self):
        hardware = getattr(self, "_feed_hardware", None)
        if not hardware:
            return None
        return {"fuel": hardware["branches"]["fuel_to_opb"]["reference_density_kg_m3"],
                "ox": hardware["branches"]["ox_to_opb"]["reference_density_kg_m3"]}

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
        if not (0 < mr_fuel < self.inputs.of_ratio < mr_ox):
            raise ValidationError("总 O/F 必须严格位于两预燃室目标 O/F 之间；不能裁剪分流冒充质量/MR 闭合。")
        denom = mr_ox - mr_fuel
        x = (O - mr_fuel * F) / denom
        y = mr_fuel * (F - x)
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
            if COOLPROP_AVAILABLE:
                # Both PBs draw oxidizer from the same pure-O2 pump. Keep the
                # numerical seed within its declared property domain; this is
                # not permission to extrapolate a converged pump requirement.
                from fluid_properties import fluid_spec
                supply_limit=fluid_spec("O2")["pmax_Pa"]-self.inputs.valve_line_dp_ratio*self.inputs.pc
                factor=self.inputs.pc*(1+self.inputs.preburner_injector_dp_ratio)
                pi_t_ox_init=min(pi_t_ox_init,.95*supply_limit/(factor*(1+ox_exh_ratio)))
                pi_t_fuel_init=min(pi_t_fuel_init,.95*supply_limit/(factor*(1+fuel_exh_ratio)))
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

    @model_thermo_scope
    def _evaluate_once(self, x0: float, x1: float, x2: float) -> CycleResults:
        """Close current-density hydraulic losses inside each physical evaluation.

        This local block has no cache across nonlinear trial evaluations. Its
        failure propagates, rather than silently returning a guessed pressure.
        """
        return close_regen_cycle(self,self._evaluate_once_core,x0,x1,x2)

    def _evaluate_once_core(self, x0: float, x1: float, x2: float) -> CycleResults:
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
        trial = getattr(self, "_network_trial", None)
        if trial:
            mdot_fuel_to_ox_preburner = trial["flows"]["fuel_to_opb"]
            mdot_fuel_to_fuel_preburner = trial["flows"]["fuel_to_fpb"]
            mdot_ox_to_ox_preburner = trial["flows"]["ox_to_opb"]
            mdot_ox_to_fuel_preburner = trial["flows"]["ox_to_fpb"]

        states: dict[str, FlowState] = {}
        components: dict[str, object] = {}

        # ── 增压输送系统：贮箱 → 增压泵 → 短管 → 主泵入口 ─────────────────
        #   贮箱(自生增压,~3 bar) → 增压泵抬到 ~10 bar → 短管(−0.5 bar) → 主泵入口 9.5 bar
        # 三个压力节点分开，避免把贮箱压力当成主泵入口/增压泵出口。
        p_tank        = self.inputs.tank_pressure_pa            # 用户给定贮箱压力边界
        p_boost_out   = self.inputs.inlet_pressure             # 增压泵出口 = 主泵供给压力
        feed_dp       = getattr(self.inputs, "feed_line_dp_pa", 0.5e5)
        p_pump_inlet = p_boost_out - feed_dp
        if p_pump_inlet <= 0:
            raise ValidationError("供给短管压降达到增压泵出口压力，不能生成有效主泵入口。")

        # 贮箱出口状态（给定温度与压力边界）
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

        # Boundary states carry chemical-reference enthalpy. An adiabatic
        # feed-line pressure loss conserves h (bulk kinetic energy neglected).
        # Booster work is external to the main shafts, but IS in the energy ledger.
        for side, species, fluid, mdot, temp, tank_key, boost_key, inlet_key in (
            ("fuel", "CH4", "CH4", mdot_fuel, self.inputs.fuel_inlet_temp,
             "fuel_tank", "fuel_boost_out", "fuel_pump_inlet"),
            ("ox", "O2", "LOX", mdot_ox, self.inputs.ox_inlet_temp,
             "lox_tank", "lox_boost_out", "lox_pump_inlet"),
        ):
            states[tank_key].enthalpy = reactant_enthalpy(species, temp, p_tank)
            if p_boost_out < p_tank or p_pump_inlet <= 0:
                raise ValidationError("增压泵出口不得低于贮箱，且扣除供给管损后压力必须为正。")
            if p_boost_out > p_tank:
                boosted, booster = Pump(f"{side}_boost_pump", fluid, mdot,
                    p_tank, p_boost_out, temp, eta_boost, nr_rpm=6000.0, n_stages=1).evaluate()
                states[boost_key] = boosted
                components[booster.name] = booster
            else:
                from core import ComponentResult
                states[boost_key].enthalpy = states[tank_key].enthalpy
                components[f"{side}_boost_pump"] = ComponentResult(
                    f"{side}_boost_pump", outputs={"power_W": 0.0})
            inlet_h = states[boost_key].enthalpy
            inlet_t = reactant_temperature_from_enthalpy(species, inlet_h, p_pump_inlet)
            states[inlet_key].temperature = inlet_t
            states[inlet_key].enthalpy = inlet_h
            states[inlet_key].density = fluid_density(fluid, inlet_t, p_pump_inlet)
        p_boost_lox = components["ox_boost_pump"].outputs["power_W"]
        p_boost_fuel = components["fuel_boost_pump"].outputs["power_W"]

        # ── 增压泵吸入空化校核（FS-01）──────────────────────────────────
        # 增压泵从贮箱(3 bar)抽吸，是整条链里 NPSH 最低、最易空化的一级。
        # 在贮箱处算 NPSHa = (p_tank − p_vapor)/(ρ·g)，并按"增压泵典型慢转速"
        # 给一个吸入比转速量级，便于诊断（真实增压泵靠诱导轮+慢转速吸收低 NPSH）。
        _g0 = 9.80665
        delta_lox=available_npsh_pressure("O2",self.inputs.ox_inlet_temp,p_tank)
        delta_fuel=available_npsh_pressure("CH4",self.inputs.fuel_inlet_temp,p_tank)
        npshA_boost_lox = delta_lox/(rho_lox_tank*_g0) if delta_lox is not None else None
        npshA_boost_fuel = delta_fuel/(rho_fuel_tank*_g0) if delta_fuel is not None else None
        # 增压泵慢转速假定（低压前置泵典型 ~6000 rpm 量级），仅供 nss 量级判断
        n_boost = 6000.0
        Q_lox_tank  = mdot_ox   / max(rho_lox_tank,  1.0)
        Q_fuel_tank = mdot_fuel / max(rho_fuel_tank, 1.0)
        nss_boost_lox_eu = n_boost*math.sqrt(Q_lox_tank)/npshA_boost_lox**.75 if npshA_boost_lox is not None and npshA_boost_lox>0 else None
        nss_boost_fuel_eu = n_boost*math.sqrt(Q_fuel_tank)/npshA_boost_fuel**.75 if npshA_boost_fuel is not None and npshA_boost_fuel>0 else None

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

        # Four branches share two supply taps. No unmodelled pressure-raising
        # device is assumed: a valve can dissipate head, never generate it.
        # Design sizing uses exact elimination of mass/MR/pressure equations
        # with minimum-head complementarity. Pump work couples BOTH PB demands
        # back into the nonlinear cycle solve (see docs/STEADY_CLOSURE_MODEL.md).
        # 唯一准一维内核返回实际压降，供给/热侧内环共同闭合。
        _cool_auto_size = bool(getattr(self.inputs, "cooling_channel_auto_size", 1.0))
        _cool_dp_auto = bool(self.inputs.cooling_bartz_mode)
        # A numerical seed only in quasi-1D mode; the same p/h circuit returns
        # the actual drop and the inner coupling converges its pump requirement.
        dp_cooling = self.inputs.cooling_dp_pa
        hydraulic_trial = getattr(self, "_hydraulic_trial", None) or {}
        if _cool_dp_auto and "cooling_dp" in hydraulic_trial:
            dp_cooling = hydraulic_trial["cooling_dp"]

        dp_inj_ox   = self.inputs.preburner_injector_dp_ratio * p_pre_ox
        dp_inj_fuel = self.inputs.preburner_injector_dp_ratio * p_pre_fuel
        branch_flows = dict(fuel_to_opb=mdot_fuel_to_ox_preburner,
                            fuel_to_fpb=mdot_fuel_to_fuel_preburner,
                            ox_to_opb=mdot_ox_to_ox_preburner,
                            ox_to_fpb=mdot_ox_to_fuel_preburner)
        network = solve_design(total_fuel=mdot_fuel, total_ox=mdot_ox,
            mr_ox=self.inputs.ox_preburner_mr_target, mr_fuel=self.inputs.fuel_preburner_mr_target,
            p_pre_ox=p_pre_ox, p_pre_fuel=p_pre_fuel,
            injector_ratio=self.inputs.preburner_injector_dp_ratio, line_dp=dp_valve,
            densities=hydraulic_trial.get("densities", self._feed_reference_densities()),
            fixed_hardware=getattr(self, "_feed_hardware", None),
            flows_override=branch_flows if trial or not target_mode else None,
            allow_unclosed_flows=bool(trial))
        if trial:
            network["tap_pressures_Pa"] = dict(trial["tap_pressures_Pa"])
        p_lox_pump_out = network["tap_pressures_Pa"]["ox"]
        p_ch4_pump_out = network["tap_pressures_Pa"]["fuel"] + dp_cooling

        # ── V10：转速一次性解析，泵 / 涡轮 / geometry 三处共用 ──
        # 先用 fluid_density 算入口密度（pump 内部也会再算一遍，但这里
        # 必须先算才能根据 Ns_target 反推 N）。
        rho_lox_inlet = states["lox_pump_inlet"].density
        rho_fuel_inlet = states["fuel_pump_inlet"].density
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
        npshA_lox = available_npsh_pressure("O2",states["lox_pump_inlet"].temperature,p_pump_inlet)
        npshA_fuel = available_npsh_pressure("CH4",states["fuel_pump_inlet"].temperature,p_pump_inlet)

        lox_pump = Pump("ox_pump", "LOX", mdot_ox,
                        p_pump_inlet, p_lox_pump_out,
                        states["lox_pump_inlet"].temperature, self.inputs.pump_efficiency_ox,
                        nr_rpm=n_lox_rpm,
                        n_stages=self.inputs.lox_pump_n_stages,
                        head_coefficient_psi=self.inputs.lox_pump_head_coefficient_psi,
                        tip_speed_limit_ms=self.inputs.pump_tip_speed_limit_ms,
                        npsh_available_pa=npshA_lox,
                        impeller_material=getattr(self.inputs, "lox_pump_impeller_material", None),
                        side="ox")
        fuel_pump = Pump("fuel_pump", "CH4", mdot_fuel,
                         p_pump_inlet, p_ch4_pump_out,
                         states["fuel_pump_inlet"].temperature, self.inputs.pump_efficiency_fuel,
                         nr_rpm=n_fuel_rpm,
                         n_stages=self.inputs.fuel_pump_n_stages,
                         head_coefficient_psi=self.inputs.fuel_pump_head_coefficient_psi,
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
        _im_k = None
        try:
            from materials import MATERIALS as _MATS_K
            _im_k = _MATS_K.get(getattr(self.inputs, "chamber_inner_wall_material", "grcop84"))
            _liner_k = (float(_im_k.thermal_conductivity_at(600.0)) if _im_k
                        else float(getattr(self.inputs, "cooling_wall_conductivity", 320.0)))
        except Exception:
            _liner_k = float(getattr(self.inputs, "cooling_wall_conductivity", 320.0))
        cooling = CoolingChannel(
            mdot_fuel=mdot_fuel,
            inlet_enthalpy=fuel_pump_out.enthalpy,
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
            **cooling_arguments(self, _im_k),
        )
        cooled_fuel_state, cooling_result = cooling.evaluate()
        states["fuel_after_cooling"] = cooled_fuel_state
        components[cooling_result.name] = cooling_result

        # Reconstruct the same equations using actual tap states, preserving
        # pressure/mass residuals and design hydraulic coefficients for export.
        network = solve_design(total_fuel=mdot_fuel, total_ox=mdot_ox,
            mr_ox=self.inputs.ox_preburner_mr_target, mr_fuel=self.inputs.fuel_preburner_mr_target,
            p_pre_ox=p_pre_ox, p_pre_fuel=p_pre_fuel,
            injector_ratio=self.inputs.preburner_injector_dp_ratio, line_dp=dp_valve,
            densities={"fuel": fluid_density("CH4", cooled_fuel_state.temperature, cooled_fuel_state.pressure),
                       "ox": lox_pump_out.density},
            temperatures={"fuel": cooled_fuel_state.temperature, "ox": lox_pump_out.temperature},
            fixed_hardware=getattr(self, "_feed_hardware", None),
            flows_override=branch_flows if trial or not target_mode else None,
            allow_unclosed_flows=bool(trial))
        network["hydraulic_iteration_error_Pa"] = max(
            abs((fuel_pump_out.pressure - cooled_fuel_state.pressure) - dp_cooling),
            0.0 if trial else abs(network["tap_pressures_Pa"]["fuel"] - cooled_fuel_state.pressure),
            0.0 if trial else abs(network["tap_pressures_Pa"]["ox"] - lox_pump_out.pressure))
        if trial:
            network["tap_pressures_Pa"] = dict(trial["tap_pressures_Pa"])
        for branch in network["branches"]:
            supply = states[branch["source"]]
            branch["source_pressure_Pa"] = supply.pressure
            if trial:
                branch["valve_drop_Pa"] = trial["valve_drops_Pa"][branch["id"]]
            species = "CH4" if branch["id"].startswith("fuel") else "O2"
            temperature = reactant_temperature_from_enthalpy(species, supply.enthalpy,
                                                            branch["destination_pressure_Pa"])
            states["branch_" + branch["id"]] = FlowState(
                name="branch_" + branch["id"], pressure=branch["destination_pressure_Pa"],
                temperature=temperature, mass_flow=branch["mass_flow_kg_s"],
                enthalpy=supply.enthalpy, composition=dict(supply.composition),
                metadata={"process": "adiabatic_isenthalpic_supply_loss"})
            branch["temperature_K"] = temperature
            branch["temperature_location"] = "after_adiabatic_supply_loss"
        network.update(audit_network(network, total_fuel=mdot_fuel, total_ox=mdot_ox,
            mr_ox=self.inputs.ox_preburner_mr_target if target_mode else None,
            mr_fuel=self.inputs.fuel_preburner_mr_target if target_mode else None))

        mr_ox_preburner, phi_ox_preburner = self._preburner_mr_phi(mdot_fuel_to_ox_preburner, mdot_ox_to_ox_preburner)
        mr_fuel_preburner, phi_fuel_preburner = self._preburner_mr_phi(mdot_fuel_to_fuel_preburner, mdot_ox_to_fuel_preburner)

        # ── 预燃室（V9：只做 HP 化学平衡，输出热燃气状态） ─────
        ox_preburner = Preburner(
            name="ox_preburner", pressure=p_pre_ox,
            mdot_fuel=mdot_fuel_to_ox_preburner, mdot_ox=mdot_ox_to_ox_preburner,
            phi=phi_ox_preburner,
            fuel_temp=cooled_fuel_state.temperature, ox_temp=lox_pump_out.temperature,
            fuel_enthalpy=cooled_fuel_state.enthalpy, ox_enthalpy=lox_pump_out.enthalpy,
            injector_dp_ratio=self.inputs.preburner_injector_dp_ratio,
        )
        fuel_preburner = Preburner(
            name="fuel_preburner", pressure=p_pre_fuel,
            mdot_fuel=mdot_fuel_to_fuel_preburner, mdot_ox=mdot_ox_to_fuel_preburner,
            phi=phi_fuel_preburner,
            fuel_temp=cooled_fuel_state.temperature, ox_temp=lox_pump_out.temperature,
            fuel_enthalpy=cooled_fuel_state.enthalpy, ox_enthalpy=lox_pump_out.enthalpy,
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
            t_in_limit_K=preburner_temperature_constraint(self.inputs, "ox")["limit_K"],
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
            t_in_limit_K=preburner_temperature_constraint(self.inputs, "fuel")["limit_K"],
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
            combustion_efficiency=(self.inputs.combustion_efficiency
                if self.inputs.combustion_loss_model == "legacy_temperature_rise" else 1.0),
            heat_loss_W=0.0 if bartz_mode else cooling_result.outputs["heat_load_W"],
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
        if bartz_mode:
            from heated_nozzle import heated_nozzle
            nozzle_result = heated_nozzle(chamber.gas_state,mdot_total,self.inputs.pc,
                self.inputs.expansion_ratio,self.inputs.ambient_pressure,cooling_result.profile)
        else:
            nozzle_result = nozzle.evaluate()
        components[nozzle_result.name] = nozzle_result
        if nozzle_result.outputs.get("h_exit_Jkg") is not None:
            states["nozzle_exit"] = FlowState("nozzle_exit",
                nozzle_result.outputs["p_exit_Pa"], nozzle_result.outputs["T_exit_K"], mdot_total,
                enthalpy=nozzle_result.outputs["h_exit_Jkg"], composition=dict(chamber_state.composition),
                metadata={"velocity_m_s": nozzle_result.outputs["Ve_ms"]})

        # ── 主燃烧室几何参数（从喉部面积反推）──────────────
        # 理论依据：
        #   NASA SP-125 (Huzel & Huang, "Design of Liquid Propellant Rocket Engines")
        #   Sutton & Biblarz, "Rocket Propulsion Elements", 9th Ed.
        #
        # 1. 本模型采用圆柱段 + 收缩段 + 喉部圆弧过渡的几何近似；非实机轮廓。
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
            At=cooling_result.outputs.get("A_throat_m2", nozzle_result.outputs["At_m2"]),
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
        # 模块的分层壁厚（已给内衬 + 已给通道深 + 外壳薄壁设计估算）同时对外
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
                liner_thickness_mm=self.inputs.cooling_wall_thickness_m*1000.0,
                cooling_profile=getattr(cooling_result, "profile", None),
                conv_half_angle_deg=getattr(self.inputs, "chamber_conv_half_angle_deg", 30.0),
            )
            _cc["cc_wall_thickness_mm"] = _ch_geo.wall_thickness_mm
            _cc["cc_wall_liner_mm"] = _ch_geo.inner_wall_thickness_mm
            _cc["cc_wall_channel_mm"] = _ch_geo.cooling_channel_height_mm
            _cc["cc_wall_jacket_mm"] = _ch_geo.outer_wall_thickness_mm
            chamber_result.notes.append(
                f"  分层壁厚 t_wall ≈ {_ch_geo.wall_thickness_mm:.1f} mm = "
                f"内衬 {_ch_geo.inner_wall_thickness_mm:.1f}（输入几何）+ "
                f"通道 {_ch_geo.cooling_channel_height_mm:.1f} + "
                f"外壳 {_ch_geo.outer_wall_thickness_mm:.1f}（Barlow 对冷却剂压力）"
                "（内衬与通道采用已解尺寸；外壳仅为当前压力下的设计估算）")
        except Exception as exc:
            chamber_result.notes.append(f"分层几何信息未生成：{exc}")
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

        for side, label, temperature in (("ox", "富氧", t_comb_ox), ("fuel", "富燃", t_comb_fuel)):
            constraint = preburner_temperature_constraint(self.inputs, side)
            limit = constraint["limit_K"]
            if constraint["status"] == "unset":
                cycle_warnings.append(f"{label}预燃室出口温度上限未设定，该项尚未评估。")
            elif temperature > limit:
                qualifier = "暂定" if constraint["status"] == "provisional" else "方案指定"
                cycle_warnings.append(
                    f"⚠ {label}预燃室出口温度 {temperature:.0f} K 超过{qualifier}上限 {limit:.0f} K。")
            elif constraint["status"] == "provisional":
                cycle_warnings.append(
                    f"{label}预燃室出口温度处于暂定上限 {limit:.0f} K 内；限值依据尚待核实。")

        # Available head before EXTRA valve throttling, using this evaluation's
        # actual branch losses (including frozen K in offdesign).
        branch_by_id = {row["id"]: row for row in network["branches"]}
        def available_head(key):
            row = branch_by_id[key]
            return row["source_pressure_Pa"] - row["destination_pressure_Pa"] - row["line_drop_Pa"] - row["injector_drop_Pa"]
        crossflow_margin_fuel = available_head("fuel_to_opb")
        crossflow_margin_lox = available_head("ox_to_fpb")
        if network["status"] != "pass":
            cycle_warnings.append("四支路供给方程未闭合；请查看压力、质量与阻力残差。")

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
            # Compatibility values are design budgets, NOT actual four-branch
            # losses in fixed-resistance runs. The authoritative per-branch
            # values are exported in steady_closure.feed_network.branches.
            "injector_dp_ox_MPa": dp_inj_ox / 1e6,
            "injector_dp_fuel_MPa": dp_inj_fuel / 1e6,
            "pressure_loss_summary_role": "legacy_design_budget_only",
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
            # 组分取自本次所选热化学模型；输出物种更多不能证明精度更高。
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

        from steady_closure import audit_energy, regenerative_heat_assumption
        network["pressure_contract"] = pressure_contract(network, states, self.inputs, cooling_component=cooling_result)
        if CANTERA_AVAILABLE and COOLPROP_AVAILABLE:
            for state in states.values():
                if state.enthalpy is not None:
                    from thermo import enthalpy_reference_id
                    state.metadata["reference_id"] = enthalpy_reference_id()
                    state.metadata["thermochemical_data"] = self.inputs.methane_thermo_dataset + ".yaml"
        closure = dict(schema_version=1, scope="ffsc_lumped_fixed_resistance" if getattr(self, "_feed_hardware", None) else "ffsc_lumped_design",
            assumptions=[
                "模型流路假设：全部甲烷先冷却再分流；液氧在主泵后分流。并非 Raptor 实际管路的已验证数据。",
                "设计点按两支路中较高需求确定共用取流压力，多余压头由非负调节阀压降消耗；不设置未计功补压泵。",
                "最低供给压力闭合不等于具有抗扰动压力储备；主喷注器压降仍为指定预算。",
                regenerative_heat_assumption(components),
                "固定效率、理想混合气及冻结喷管假设仍在；方程闭合不等于试验验证。"],
            feed_network=network, energy_balance=audit_energy(states, components))
        return CycleResults(
            inputs=self.inputs, states=states, components=components,
            summary=summary, topology_nodes=topology_nodes,
            topology_edges=topology_edges, warnings=cycle_warnings, steady_closure=closure,
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

    @staticmethod
    def _convergence_layer(enabled=False, status="disabled", residual=None,
                           tolerance=None, unit="1", iterations=0,
                           termination_reason="not_applicable", **extra):
        return dict(enabled=enabled, status=status, residual=residual,
                    tolerance=tolerance, unit=unit, iterations=iterations,
                    termination_reason=termination_reason, **extra)

    @classmethod
    def _empty_convergence(cls):
        return {
            "schema_version": 1,
            "layers": {name: cls._convergence_layer()
                       for name in ("inner", "thermal", "preburner", "continuation")},
            "inner_runs": [], "thermal_history": [], "preburner_history": [],
            "continuation_history": [],
        }

    @staticmethod
    def _merge_convergence_history(target, source):
        """Keep every evaluated outer solve, without joining independent Newton runs."""
        for source_run in source.get("inner_runs", []):
            run = copy.deepcopy(source_run)
            run["id"] = len(target["inner_runs"]) + 1
            target["inner_runs"].append(run)
        for key in ("thermal_history", "preburner_history", "continuation_history"):
            target[key].extend(copy.deepcopy(source.get(key, [])))

    @model_thermo_scope
    def solve_cycle(
        self,
        tol_rel: float = 1e-6,
        max_iter: int = 50,
        verbose: bool = False,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        x_init: Optional[list[float]] = None,
    ):
        """Solve identical bounded equations with Newton or TRF globalization.

        Scales stay fixed during one solve. The tighter default also resolves
        shaft work below the independent 1e-5 energy-ledger acceptance policy.
        Free-split variables are fractions, so rectangular solver bounds do not
        silently clip mass-flow-dependent cross-flow limits inside a Jacobian.
        """
        from nonlinear_solver import solve_nonlinear
        checkpoint()
        if not math.isfinite(tol_rel) or tol_rel <= 0 or max_iter < 0:
            raise ValueError("非线性求解容差必须为有限正数，最大步数不能为负。")
        x = list(self._initial_guess_vector() if x_init is None else x_init)
        if len(x) != 3 or not all(math.isfinite(float(v)) for v in x):
            raise ValueError("FFSC 初值必须含 3 个有限数。")
        x = self._sanitize_vector([float(v) for v in x])
        target_mode = self._target_mr_mode()
        if target_mode:
            z = x[:]
            lower, upper = [1e-3, 1.02, 1.02], [math.inf, 10.0, 10.0]
            variable_scales = [max(x[0], 1.0), 2.0, 2.0]
            diff_steps = [max(abs(x[0]) * 5e-4, 1e-5), .02, .02]
            def physical(v):
                return list(v)
        else:
            fuel, ox = self._flows_from_total(x[0])
            z = [x[0], x[1] / fuel, x[2] / ox]
            lower, upper = [1e-3, .005, .001], [math.inf, .80, .80]
            variable_scales = [max(x[0], 1.0), .1, .1]
            diff_steps = [max(abs(x[0]) * 5e-4, 1e-5), 2e-4, 2e-4]
            def physical(v):
                fuel, ox = self._flows_from_total(float(v[0]))
                return [float(v[0]), float(v[1]) * fuel, float(v[2]) * ox]

        # Division can put an exact physical split boundary one ulp outside
        # its fraction bound. Project before the cached initial evaluation,
        # matching the generic backend and keeping the function count exact.
        z = [max(lo, min(hi, value)) for value, lo, hi in zip(z, lower, upper)]
        initial_residuals, initial_result = self.residual_vector(*physical(z))
        def pump_scale(name):
            component = initial_result.components.get(name)
            power = component.outputs.get("power_W", 0.0) if component is not None else 0.0
            return max(abs(float(power)), 1e5) if math.isfinite(float(power)) else 1e5
        scales = [max(abs(self.inputs.thrust), 100.0), pump_scale("ox_pump"), pump_scale("fuel_pump")]
        # Reuse the initial evaluation without hiding a physical model call in
        # the reported nfev. Only this exact first point may use the cache.
        first_call = True
        def function(v):
            nonlocal first_call
            if first_call and list(v) == z:
                first_call = False
                return initial_residuals, initial_result
            first_call = False
            return self.residual_vector(*physical(v))
        backend = getattr(self.inputs, "nonlinear_solver", "newton")
        answer = solve_nonlinear(
            function, z, lower, upper, residual_scales=scales,
            variable_scales=variable_scales, diff_steps=diff_steps,
            tol=tol_rel, max_iter=max_iter, backend=backend,
            names=["thrust", "ox_power", "fuel_power"],
            progress_callback=(lambda point: progress_callback(
                point["iteration"], max_iter, point["residual_norm"])) if progress_callback else None)
        if answer.payload is None:
            answer.payload = initial_result
            answer.residuals = list(initial_residuals)
            answer.reason = "invalid_state"
            answer.points = [dict(iteration=0, residual_norm=None, accepted=False,
                termination_reason="invalid_initial_state",
                residuals={name: float(value) / scale if math.isfinite(float(value)) else None
                           for name, value, scale in zip(
                               ("thrust", "ox_power", "fuel_power"), initial_residuals, scales)})]
        results = answer.payload
        x = physical(answer.x)
        norm = max(abs(float(r)) / scale for r, scale in zip(answer.residuals, scales))
        if not all(math.isfinite(float(r)) for r in answer.residuals):
            norm = float("inf")
        context = getattr(self, "_convergence_context", {})
        convergence = self._empty_convergence()
        iterations = max(len(answer.points) - 1, 0)
        run = dict(id=1, stage_ratio=context.get("stage_ratio", 1.0),
                   thermal_iteration=context.get("thermal_iteration", 0),
                   preburner_iteration=context.get("preburner_iteration", 0),
                   continuation_attempt=context.get("continuation_attempt", 1),
                   warm_start=context.get("warm_start", False), backend=backend,
                   tolerance=tol_rel, termination_reason=answer.reason,
                   points=answer.points, nfev=answer.nfev, njev=answer.njev,
                   rejected_steps=answer.rejected_steps,
                   evaluation_budget=getattr(answer, "evaluation_budget", None),
                   error=answer.error, backend_status=answer.backend_status,
                   residual_scales=list(answer.residual_scales),
                   variable_scales=list(answer.variable_scales),
                   physical_tolerances=[tol_rel * value for value in scales])
        convergence["inner_runs"].append(run)
        convergence["layers"]["inner"] = self._convergence_layer(
            True, "pass" if answer.passed else "fail",
            norm if math.isfinite(norm) else None, tol_rel, "1", iterations, answer.reason,
            checks=[dict(name=name, residual=abs(float(value)) if math.isfinite(float(value)) else None,
                         tolerance=tol_rel * scale, unit=unit)
                    for name, value, scale, unit in zip(
                        ("thrust", "ox_power", "fuel_power"), answer.residuals, scales, ("N", "W", "W"))])
        results.summary.update(solver_iterations=float(iterations), solver_residual_norm=norm,
                               solver_warning=float(not answer.passed),
                               inner_solver_warning=float(not answer.passed),
                               solver_stalled=float(answer.reason in (
                                   "stalled", "line_search_failed", "step_tolerance", "step_stagnation",
                                   "residual_stagnation", "stationary_nonzero_residual")),
                               solver_function_evaluations=float(answer.nfev),
                               solver_jacobian_evaluations=float(answer.njev),
                               solver_rejected_steps=float(answer.rejected_steps))
        results.convergence = convergence
        if verbose:
            print(f"[{backend}] residual={norm:.3e}, iterations={iterations}, nfev={answer.nfev}, {answer.reason}")
        return x, results

    def evaluate_forward(self):
        checkpoint()
        results = self._evaluate_once(*self._initial_guess_vector())
        self._attach_cooling_marching(results)
        return results

    @model_thermo_scope
    def evaluate(self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
                 *, include_cooling_marching: bool = True, warm_state=None,
                 preburner_mr_targets=None):
        """
        设计点求解入口（V11）。

        · 温度目标模式（use_target_preburner_temp=1）：外层一维反求 MR，使两侧
          预燃室出口温度（本模型同涡轮入口温度）命中给定设计目标。
        · 否则：直接走 V8 continuation 核心求解（_evaluate_design_core）。
        · include_cooling_marching 仅控制已保存沿程解的历史格式兼容视图，
          不改变输入快照或循环中的再生冷却热量、压降与出口状态。
        """
        checkpoint()
        if self._temp_target_mode():
            results = self._evaluate_temp_targeted(progress_callback, warm_state=warm_state,
                                                   preburner_mr_targets=preburner_mr_targets)
        else:
            results = self._evaluate_design_core(progress_callback, warm_state=warm_state)
        from convergence import attach_convergence_status
        attach_convergence_status(results)
        from thermo_applicability import attach_thermodynamic_applicability
        attach_thermodynamic_applicability(results)
        # 沿程解已在循环内闭合；此处只复制到历史格式，不再计算热量或压降。
        if include_cooling_marching:
            self._attach_cooling_marching(results)
        return results

    def _attach_cooling_marching(self, results) -> None:
        """将循环内已保存的冷却剖面复制到历史格式的兼容视图。"""
        checkpoint()
        if float(getattr(self.inputs, "cooling_marching_enable", 1.0)) < 0.5:
            return
        if not bool(getattr(self.inputs, "cooling_bartz_mode", True)):
            return   # 固定热载荷模式没有喉部几何推算，不做剖面
        from cooling_marching import run_marching_postpass
        run_marching_postpass(results, self.inputs)

    # =====================================================
    # 5b. 温度目标外环：反求 MR 使涡轮入口温度达到给定设计目标
    # =====================================================

    def _mr_for_target_temp(
        self,
        side: str,
        T_target: float,
        T_fuel_in: float,
        T_ox_in: float,
        p_pre: float,
        *, fuel_enthalpy=None, ox_enthalpy=None,
    ) -> float:
        """
        一维反求预燃室混合比 MR(=O/F)，使绝热火焰温度 = T_target。

        在指定的富氧或富燃 MR 区间内构造有效温度括区，再对与预燃室相同的
        thermo.combustion_equilibrium 做二分。入口焓与压力均参与热平衡；
        入口条件随循环改变后，由外环继续验证目标误差。
        有效域内无法构造目标温度括区时明确失败，不以端点截断代替目标解。
        """
        checkpoint()
        from thermo import combustion_equilibrium

        if side not in ("ox", "fuel"):
            raise ValueError("预燃室侧必须为 ox 或 fuel。")
        if not all(math.isfinite(float(value)) and value > 0
                   for value in (T_target, T_fuel_in, T_ox_in, p_pre)):
            raise ValueError("预燃室温度反求要求有限正的目标温度、入口温度与压力。")

        def temp_at(mr: float) -> float:
            comb = combustion_equilibrium(
                mdot_fuel=1.0, mdot_ox=max(mr, 1e-6),
                T_fuel=T_fuel_in, T_ox=T_ox_in, pressure=p_pre,
                fuel_enthalpy=fuel_enthalpy, ox_enthalpy=ox_enthalpy,
            )
            temperature = float(comb.temperature)
            if not math.isfinite(temperature) or temperature <= 0:
                raise ValueError("预燃室温度反求遇到非有限或非正温度。")
            return temperature

        if side == "ox":
            lo, hi = 15.0, 160.0     # 富氧：O/F 大→温度低
        else:
            lo, hi = 0.02, 0.80      # 富燃：O/F 大→温度高
        try:
            T_lo, T_hi = temp_at(lo), temp_at(hi)
        except (ValueError, RuntimeError, ArithmeticError):
            # Out-of-domain HP trial points are excluded, never fabricated by
            # clipping to a temperature floor. Find a genuine valid bracket.
            samples = []
            for i in range(33):
                mr = lo + (hi - lo) * i / 32
                try:
                    samples.append((mr, temp_at(mr)))
                except (ValueError, RuntimeError, ArithmeticError):
                    continue
            bracket = next(((a, b) for a, b in zip(samples, samples[1:])
                            if (a[1] - T_target) * (b[1] - T_target) <= 0), None)
            if bracket is None:
                raise ValueError("预燃室温度反求：物性有效域内没有目标温度括区。")
            (lo, T_lo), (hi, T_hi) = bracket
        if abs(T_lo - T_target) <= 0.3:
            return lo
        if abs(T_hi - T_target) <= 0.3:
            return hi

        # 目标落在端点温度之外 → 告警并失败
        if (T_target - T_lo) * (T_target - T_hi) > 0.0:
            msg = (
                f"⚠ {side} 预燃室目标温度 {T_target:.0f} K 超出 MR∈[{lo:g},{hi:g}] "
                f"可达温度 [{min(T_lo, T_hi):.0f},{max(T_lo, T_hi):.0f}] K，"
                "无法构造目标温度解。"
            )
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
            raise ValueError(msg)

        # 单调性无关的括号二分（用相对 T_target 的符号维持括号）
        for _ in range(40):
            checkpoint()
            mid = 0.5 * (lo + hi)
            T_mid = temp_at(mid)
            # Return the point which was actually evaluated and passed. Updating
            # the bracket first used to return a different, untested midpoint.
            if abs(T_mid - T_target) <= 0.3:
                return mid
            if (T_mid - T_target) * (T_lo - T_target) > 0.0:
                lo, T_lo = mid, T_mid
            else:
                hi, T_hi = mid, T_mid
        raise RuntimeError("预燃室温度二分反求达到最大步数，未满足 0.3 K 容差。")

    def _initialize_preburner_temperature_guess(self):
        """Domain-valid numerical seed; never a replacement for final checks."""
        x0=self._initial_guess_vector()
        seed=self._evaluate_once(*x0)
        fuel=seed.states["fuel_after_cooling"]
        ox=seed.states["lox_after_pump"]
        initialized={}
        for side in ("ox","fuel"):
            target=getattr(self.inputs,f"{side}_preburner_temp_target")
            value=self._mr_for_target_temp(side,target,fuel.temperature,ox.temperature,
                seed.summary[f"{side}_preburner_pressure_Pa"],
                fuel_enthalpy=fuel.enthalpy,ox_enthalpy=ox.enthalpy)
            setattr(self.inputs,f"{side}_preburner_mr_target",value)
            initialized[side]=value
        return {"method":"physical_forward_enthalpy_inversion",
            "cycle_evaluations":1,"mixture_ratios":initialized,"accepted_as_final":False,
            "warm_state":dict(x=list(x0),chamber_temp_guess=seed.summary["chamber_temperature_K"],
                chamber_gamma=seed.summary["chamber_gamma"],molar_mass=seed.summary["chamber_molar_mass"])}

    def _evaluate_temp_targeted(
        self,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        *, warm_state=None, preburner_mr_targets=None,
    ):
        """Solve each MR before reporting it, retaining every nested iteration."""
        checkpoint()
        T_ox_tgt = float(self.inputs.ox_preburner_temp_target)
        T_fu_tgt = float(self.inputs.fuel_preburner_temp_target)
        saved_mr_ox = self.inputs.ox_preburner_mr_target
        saved_mr_fu = self.inputs.fuel_preburner_mr_target
        saved_context = getattr(self, "_convergence_context", {})
        MR_OUTER_MAX, TEMP_TOL, RELAX = 12, 3.0, 0.7
        history = self._empty_convergence()
        results = None
        reason = "max_iterations"
        solved_mr_ox, solved_mr_fu = saved_mr_ox, saved_mr_fu
        residual = None
        ox_error = fuel_error = None
        warm_state = copy.deepcopy(warm_state)
        try:
            if preburner_mr_targets is not None:
                mr_ox = float(preburner_mr_targets["ox"])
                mr_fuel = float(preburner_mr_targets["fuel"])
                if (not all(math.isfinite(v) for v in (mr_ox, mr_fuel))
                    or not .01 <= mr_fuel <= 2.0 or not 5.0 <= mr_ox <= 200.0
                    or not mr_fuel < self.inputs.of_ratio < mr_ox):
                    raise ValueError("预燃室热启动混合比必须有限、在支持范围内并满足四支路正流量条件。")
                self.inputs.ox_preburner_mr_target = mr_ox
                self.inputs.fuel_preburner_mr_target = mr_fuel
            elif bool(self.inputs.cooling_bartz_mode):
                # Seed the temperature block from a domain-valid physical
                # evaluation, before demanding shaft balance at an arbitrary
                # guessed MR. A guessed low temperature may have no shaft root
                # inside the pure-fluid pressure domain although the requested
                # temperature state does. This changes initial guesses only.
                history["temperature_initialization"]=self._initialize_preburner_temperature_guess()
                if warm_state is None:
                    warm_state=copy.deepcopy((history["temperature_initialization"] or {}).get("warm_state"))
            for outer in range(MR_OUTER_MAX):
                checkpoint()
                self._convergence_context = {"preburner_iteration": outer + 1}
                # These are the MR values entering this solve, never the next
                # damped proposal which has not yet been evaluated.
                solved_mr_ox = self.inputs.ox_preburner_mr_target
                solved_mr_fu = self.inputs.fuel_preburner_mr_target
                callback = None
                if progress_callback is not None:
                    def callback(it, max_it, norm, outer_idx=outer):
                        progress_callback(int((outer_idx + it / max(max_it, 1))
                                              / MR_OUTER_MAX * 100), 100, norm)
                results = self._evaluate_design_core(callback, warm_state=warm_state)
                self._merge_convergence_history(history, results.convergence)
                history["layers"] = copy.deepcopy(results.convergence["layers"])
                history["solution"] = copy.deepcopy(results.convergence.get("solution"))
                s = results.summary
                ox_temp = s.get("ox_preburner_T_combustion_K")
                fuel_temp = s.get("fuel_preburner_T_combustion_K")
                valid = all(isinstance(v, (int, float)) and math.isfinite(v)
                            and v > 0 for v in (ox_temp, fuel_temp, T_ox_tgt, T_fu_tgt))
                ox_error = ox_temp - T_ox_tgt if valid else None
                fuel_error = fuel_temp - T_fu_tgt if valid else None
                residual = max(abs(ox_error), abs(fuel_error)) if valid else None
                history["preburner_history"].append(dict(
                    iteration=outer + 1, ox_error_K=ox_error, fuel_error_K=fuel_error,
                    residual_K=residual, ox_mr=solved_mr_ox, fuel_mr=solved_mr_fu))
                required_layers = (history["layers"][name]
                                   for name in ("inner", "thermal", "continuation"))
                if any(layer["enabled"] and layer["status"] != "pass"
                       for layer in required_layers):
                    reason = "dependent_solver_failed"
                    break
                warm_state = copy.deepcopy(results.convergence.get("solution"))
                if not valid:
                    reason = "invalid_state"
                    break
                if residual <= TEMP_TOL:
                    reason = "tolerance_met"
                    break
                if outer + 1 == MR_OUTER_MAX:
                    break
                try:
                    T_fuel_in = results.states["fuel_after_cooling"].temperature
                    T_ox_in = results.states["lox_after_pump"].temperature
                    p_pre_ox = s["ox_preburner_pressure_ratio"] * self.inputs.pc
                    p_pre_fu = s["fuel_preburner_pressure_ratio"] * self.inputs.pc
                    new_mr_ox = self._mr_for_target_temp(
                        "ox", T_ox_tgt, T_fuel_in, T_ox_in, p_pre_ox,
                        fuel_enthalpy=results.states["fuel_after_cooling"].enthalpy,
                        ox_enthalpy=results.states["lox_after_pump"].enthalpy)
                    new_mr_fu = self._mr_for_target_temp(
                        "fuel", T_fu_tgt, T_fuel_in, T_ox_in, p_pre_fu,
                        fuel_enthalpy=results.states["fuel_after_cooling"].enthalpy,
                        ox_enthalpy=results.states["lox_after_pump"].enthalpy)
                except (ValueError, RuntimeError, ArithmeticError) as exc:
                    reason = "temperature_inversion_failed"
                    results.warnings.append(f"预燃室温度反求失败：{exc}")
                    break
                self.inputs.ox_preburner_mr_target = (
                    RELAX * new_mr_ox + (1.0 - RELAX) * solved_mr_ox)
                self.inputs.fuel_preburner_mr_target = (
                    RELAX * new_mr_fu + (1.0 - RELAX) * solved_mr_fu)
        finally:
            self.inputs.ox_preburner_mr_target = saved_mr_ox
            self.inputs.fuel_preburner_mr_target = saved_mr_fu
            self._convergence_context = saved_context

        s = results.summary
        s.update({
            "preburner_temp_target_mode": 1.0,
            "solve_mode_text": "指定预燃室温度（反求混合比）",
            "ox_preburner_temp_target_K": T_ox_tgt,
            "fuel_preburner_temp_target_K": T_fu_tgt,
            "ox_preburner_temp_error_K": ox_error,
            "fuel_preburner_temp_error_K": fuel_error,
            "ox_preburner_mr_solved_for_temp": solved_mr_ox,
            "fuel_preburner_mr_solved_for_temp": solved_mr_fu,
            "preburner_temp_outer_iters": float(len(history["preburner_history"])),
            "solver_iterations": float(sum(max(len(run["points"]) - 1, 0)
                                           for run in history["inner_runs"])),
            "solver_inner_solves": float(len(history["inner_runs"])),
            "tc_outer_iterations_total": float(sum(row.get("picard_iteration", 1)
                for row in history["thermal_history"])),
            "solver_function_evaluations": float(sum(run.get("nfev", 0) for run in history["inner_runs"])
                + (history.get("temperature_initialization") or {}).get("cycle_evaluations", 0)),
            "solver_initialization_evaluations": float((history.get("temperature_initialization") or {}).get("cycle_evaluations", 0)),
            "solver_jacobian_evaluations": float(sum(run.get("njev", 0) for run in history["inner_runs"])),
            "solver_rejected_steps": float(sum(run.get("rejected_steps", 0) for run in history["inner_runs"])),
        })
        history["layers"]["preburner"] = self._convergence_layer(
            True, "pass" if reason == "tolerance_met" else "fail",
            residual, TEMP_TOL, "K", len(history["preburner_history"]), reason,
            checks=[dict(name="ox_temperature", residual=abs(ox_error) if ox_error is not None else None,
                         tolerance=TEMP_TOL, unit="K"),
                    dict(name="fuel_temperature", residual=abs(fuel_error) if fuel_error is not None else None,
                         tolerance=TEMP_TOL, unit="K")])
        results.convergence = history
        s["solver_warning"] = float(any(layer["enabled"] and layer["status"] != "pass"
                                         for layer in history["layers"].values()))
        if reason != "tolerance_met":
            results.warnings.insert(0, f"预燃室温度外环未收敛：{reason}；当前结果仅供诊断。")
        return results

    def _evaluate_design_core(self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
                              *, warm_state=None):
        """Adaptive continuation and safeguarded thermal fixed-point iteration.

        A warm state is explicitly passed by the current PB loop. It is never
        cached on the model, so independent evaluate calls cannot share stale
        guesses, and every temporary input is restored on all exit paths.
        """
        checkpoint()
        self._warnings = []
        base_pc, base_thrust = self.inputs.pc, self.inputs.thrust
        original_properties = (self.inputs.chamber_temp_guess, self.inputs.chamber_gamma,
                               self.inputs.molar_mass)
        saved_context = getattr(self, "_convergence_context", {})
        pb_iteration = saved_context.get("preburner_iteration", 0)
        saved_thermal_epoch = getattr(self, "_thermal_feedback_epoch", None)
        self._thermal_feedback_epoch = object()
        TC_OUTER_MAX, TC_TOL, PROPERTY_TOL = 20, 5.0, 1e-4
        MAX_ATTEMPTS, MIN_STEP = 20, 1.0 / 64
        thermal_enabled = bool(self.inputs.cooling_bartz_mode)
        convergence = self._empty_convergence()
        if thermal_enabled:
            convergence["layers"]["thermal"] = self._convergence_layer(
                True, "fail", None, TC_TOL, "K", 0, "not_reached")
        else:
            convergence["layers"]["thermal"]["termination_reason"] = "fixed_heat_and_pressure_loss"

        def set_properties(values):
            self.inputs.chamber_temp_guess, self.inputs.chamber_gamma, self.inputs.molar_mass = values

        def properties():
            return (self.inputs.chamber_temp_guess, self.inputs.chamber_gamma, self.inputs.molar_mass)

        def scaled_seed(seed, ratio):
            if seed is None:
                return None
            # Free-mode cross flows scale with total mass; pressure ratios do not.
            return [seed[0] * ratio,
                    seed[1] if self._target_mr_mode() else seed[1] * ratio,
                    seed[2] if self._target_mr_mode() else seed[2] * ratio]

        def valid_warm(value):
            if not isinstance(value, dict):
                return False
            try:
                vector = list(value["x"])
                props = [value[name] for name in ("chamber_temp_guess", "chamber_gamma", "molar_mass")]
                return (len(vector) == 3 and all(math.isfinite(float(v)) and float(v) > 0
                        for v in vector + props) and float(props[1]) > 1)
            except (KeyError, TypeError, ValueError):
                return False

        warm_attempt = valid_warm(warm_state)
        warm_requested = warm_attempt
        x_seed = list(warm_state["x"]) if warm_attempt else None
        if warm_attempt:
            set_properties(tuple(warm_state[name] for name in
                                 ("chamber_temp_guess", "chamber_gamma", "molar_mass")))
        ratio, step = (1.0 if warm_attempt else .5), .25
        reached_ratio, evaluated_ratio, completed_stages = 0.0, 0.0, 0
        accepted_x, accepted_properties = None, original_properties
        results, x_solved = None, []
        stage_reason = "not_reached"
        attempt = 0

        def run_stage(seed, current_ratio, attempt_number, is_warm):
            """Return the last evaluated payload and whether this stage passed."""
            nonlocal results, x_solved
            last_stage = current_ratio == 1.0
            max_thermal = TC_OUTER_MAX if last_stage and thermal_enabled else 1
            relaxation = .5
            accepted_thermal = None
            next_seed = seed
            for tc_iter in range(max_thermal):
                checkpoint()
                self._convergence_context = dict(
                    stage_ratio=current_ratio, thermal_iteration=tc_iter + 1 if last_stage and thermal_enabled else 0,
                    preburner_iteration=pb_iteration, continuation_attempt=attempt_number, warm_start=is_warm)
                callback = None
                if progress_callback is not None:
                    def callback(it, max_it, norm):
                        progress_callback(min(99, int(current_ratio * 90)), 100, norm)
                try:
                    x_solved, results = self.solve_cycle(progress_callback=callback, x_init=next_seed)
                except ValidationError:
                    # Invalid submitted inputs are not a continuation failure.
                    # Preserve the actionable validation exception for callers.
                    raise
                except (ValueError, RuntimeError, ArithmeticError) as exc:
                    # A failed initial evaluation has no payload; do not label an
                    # earlier stage's state as the attempted target conditions.
                    results = CycleResults(self.inputs, {
                        "solver_residual_norm": math.inf, "solver_warning": 1.0,
                        "inner_solver_warning": 1.0, "chamber_pressure_Pa": self.inputs.pc}, {}, {}, [str(exc)])
                    x_solved = list(next_seed if next_seed is not None else self._initial_guess_vector())
                    results.convergence = self._empty_convergence()
                    results.convergence["layers"]["inner"] = self._convergence_layer(
                        True, "fail", None, 1e-4, "1", 0, "evaluation_failed")
                    results.convergence["inner_runs"] = [dict(
                        id=1, **self._convergence_context,
                        backend=getattr(self.inputs, "nonlinear_solver", "newton"),
                        tolerance=1e-4, termination_reason="evaluation_failed", points=[],
                        nfev=1, njev=0, rejected_steps=0, error=str(exc))]
                self._merge_convergence_history(convergence, results.convergence)
                convergence["layers"]["inner"] = copy.deepcopy(results.convergence["layers"]["inner"])
                if convergence["layers"]["inner"]["status"] != "pass":
                    # If an accelerated thermal proposal left the valid basin,
                    # retry from the last accepted fixed-point state with damping.
                    if accepted_thermal is not None and relaxation > .1 and tc_iter + 1 < max_thermal:
                        relaxation = max(.1, relaxation * .5)
                        q, output, previous_norm, previous_x = accepted_thermal
                        convergence["thermal_history"].append(dict(
                            iteration=tc_iter + 1, preburner_iteration=pb_iteration,
                            continuation_attempt=attempt_number, residual_K=None,
                            accepted=False, relaxation=relaxation,
                            termination_reason="inner_failed_relaxation_reduced"))
                        set_properties(tuple(a + relaxation * (b - a) for a, b in zip(q, output)))
                        next_seed = previous_x[:]
                        continue
                    if last_stage and thermal_enabled:
                        convergence["layers"]["thermal"].update(
                            status="fail", termination_reason="inner_solver_failed")
                    return False, "inner_solver_failed"
                if not last_stage or not thermal_enabled:
                    return True, "target_reached" if last_stage else "stage_converged"
                summary = results.summary
                output = (summary.get("chamber_temperature_K"), summary.get("chamber_gamma"),
                          summary.get("chamber_molar_mass"))
                guesses = properties()
                valid = (all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                             for v in (*output, *guesses)) and output[1] > 1.0)
                tc_r = abs(output[0] - guesses[0]) if valid else None
                gamma_r = abs(output[1] - guesses[1]) / abs(guesses[1]) if valid else None
                mw_r = abs(output[2] - guesses[2]) / abs(guesses[2]) if valid else None
                # The physical quasi-1D evaluator already closes current gas
                # properties, spatial heat, geometry and hydraulic feedback.
                # Repeating the old Picard loop would only relax unused initial
                # guesses toward that solved state. Serialized "pass" flags are
                # insufficient: require this solve's private production evidence.
                from regen_coupling import (current_thermal_evidence, coupling_checks,
                                            THERMAL_FEEDBACK_SOURCE)
                coupled = current_thermal_evidence(self, results) if valid else None
                if coupled is not None:
                    temperature_residual = coupled["hot_side_temperature_residual_K"]
                    convergence["layers"]["thermal"] = self._convergence_layer(
                        True, "pass", temperature_residual, .001, "K", coupled["iterations"],
                        "coupled_feedback_tolerance_met", source=THERMAL_FEEDBACK_SOURCE,
                        checks=coupling_checks(coupled),
                        initialization_difference=dict(temperature_K=tc_r,
                            gamma_relative=gamma_r, molar_mass_relative=mw_r),
                        description="本次循环内统一冷却/热气/供给六项残差共同验收；主室物性由当前焓与组成求得，初值差不再作为额外物理方程。")
                    convergence["thermal_history"].extend(dict(
                        row, preburner_iteration=pb_iteration,
                        continuation_attempt=attempt_number, accepted=True,
                        source=THERMAL_FEEDBACK_SOURCE, picard_iteration=0,
                        residual_K=row["hot_side_temperature_residual_K"])
                        for row in coupled["history"])
                    summary.update(tc_outer_iterations=0., tc_outer_residual_K=temperature_residual,
                        tc_outer_gamma_relative_residual=None,
                        tc_outer_molar_mass_relative_residual=None,
                        thermal_feedback_source=THERMAL_FEEDBACK_SOURCE,
                        chamber_temp_guess_used_K=guesses[0])
                    # Carry the actual thermodynamic solution into the next
                    # PB/continuation warm start without another model call.
                    set_properties(output)
                    return True, "target_reached"
                checks = [dict(name="temperature", residual=tc_r, tolerance=TC_TOL, unit="K"),
                          dict(name="gamma", residual=gamma_r, tolerance=PROPERTY_TOL, unit="1"),
                          dict(name="molar_mass", residual=mw_r, tolerance=PROPERTY_TOL, unit="1")]
                feedback_norm = max(tc_r / TC_TOL, gamma_r / PROPERTY_TOL, mw_r / PROPERTY_TOL) if valid else math.inf
                passed = valid and feedback_norm <= 1
                reject = (not passed and accepted_thermal is not None and
                          (not valid or feedback_norm > 1.25 * accepted_thermal[2]))
                trace = dict(iteration=tc_iter + 1, preburner_iteration=pb_iteration,
                             continuation_attempt=attempt_number, residual_K=tc_r,
                             gamma_relative_residual=gamma_r, molar_mass_relative_residual=mw_r,
                             temperature_input_K=guesses[0], temperature_output_K=output[0] if valid else None,
                             gamma_input=guesses[1], gamma_output=output[1] if valid else None,
                             molar_mass_input=guesses[2], molar_mass_output=output[2] if valid else None,
                             accepted=bool(valid and not reject), relaxation=relaxation,
                             normalized_feedback_residual=feedback_norm if math.isfinite(feedback_norm) else None)
                convergence["thermal_history"].append(trace)
                reason = "tolerance_met" if passed else "max_iterations" if valid else "invalid_state"
                convergence["layers"]["thermal"] = self._convergence_layer(
                    True, "pass" if passed else "fail", tc_r, TC_TOL, "K", tc_iter + 1, reason, checks=checks)
                summary.update(tc_outer_iterations=float(tc_iter + 1), tc_outer_residual_K=tc_r,
                               tc_outer_gamma_relative_residual=gamma_r,
                               tc_outer_molar_mass_relative_residual=mw_r,
                               chamber_temp_guess_used_K=guesses[0])
                if passed:
                    return True, "target_reached"
                if tc_iter + 1 == max_thermal:
                    return False, "thermal_solver_failed"
                if reject:
                    if relaxation <= .1:
                        convergence["layers"]["thermal"]["termination_reason"] = "feedback_step_rejected"
                        return False, "thermal_solver_failed"
                    relaxation = max(.1, relaxation * .5)
                    q, previous_output, previous_norm, previous_x = accepted_thermal
                    trace["termination_reason"] = "feedback_growth_relaxation_reduced"
                    set_properties(tuple(a + relaxation * (b - a) for a, b in zip(q, previous_output)))
                    next_seed = previous_x[:]
                    continue
                if not valid:
                    return False, "thermal_solver_failed"
                if accepted_thermal is not None:
                    previous_q, previous_output, previous_norm, _ = accepted_thermal
                    current_feedback = [(output[0] - guesses[0]) / TC_TOL,
                        (output[1] - guesses[1]) / abs(guesses[1]) / PROPERTY_TOL,
                        (output[2] - guesses[2]) / abs(guesses[2]) / PROPERTY_TOL]
                    previous_feedback = [(previous_output[0] - previous_q[0]) / TC_TOL,
                        (previous_output[1] - previous_q[1]) / abs(previous_q[1]) / PROPERTY_TOL,
                        (previous_output[2] - previous_q[2]) / abs(previous_q[2]) / PROPERTY_TOL]
                    oscillating = (sum(a * b for a, b in zip(current_feedback, previous_feedback)) < 0
                                   and feedback_norm >= .5 * previous_norm)
                    if oscillating:
                        relaxation = max(.1, relaxation * .5)
                        trace["relaxation_action"] = "oscillation_damped"
                    elif feedback_norm < .8 * previous_norm:
                        relaxation = min(1.0, relaxation * 1.5)
                        trace["relaxation_action"] = "contraction_accelerated"
                accepted_thermal = (guesses, output, feedback_norm, x_solved[:])
                trace["next_relaxation"] = relaxation
                set_properties(tuple(a + relaxation * (b - a) for a, b in zip(guesses, output)))
                next_seed = x_solved[:]
            return False, "thermal_solver_failed"

        try:
            while attempt < MAX_ATTEMPTS:
                checkpoint()
                attempt += 1
                self.inputs.pc, self.inputs.thrust = base_pc * ratio, base_thrust * ratio
                evaluated_ratio = ratio
                start_properties = properties()
                passed, reason = run_stage(x_seed, ratio, attempt, warm_attempt)
                convergence["continuation_history"].append(dict(
                    attempt=attempt, preburner_iteration=pb_iteration, stage_ratio=ratio,
                    from_ratio=reached_ratio, step=ratio - reached_ratio,
                    accepted=passed, warm_start=warm_attempt, termination_reason=reason,
                    temperature_start_K=start_properties[0]))
                if passed:
                    reached_ratio, completed_stages = ratio, completed_stages + 1
                    accepted_x, accepted_properties = x_solved[:], properties()
                    if ratio == 1.0:
                        stage_reason = "target_reached"
                        break
                    next_ratio = min(1.0, ratio + step)
                    step = min(.5, step * 1.5)
                    x_seed = scaled_seed(accepted_x, next_ratio / ratio)
                    ratio = next_ratio
                    continue
                if warm_attempt:
                    # A converged solution for the preceding MR is only a seed,
                    # never evidence for this MR. Retry the cold continuation.
                    warm_attempt = False
                    ratio, step, reached_ratio = .5, .25, 0.0
                    x_seed, accepted_x = None, None
                    accepted_properties = original_properties
                    set_properties(original_properties)
                    continue
                if reason == "thermal_solver_failed":
                    stage_reason = reason
                    break  # thermal damping already exhausted its own bounded recovery
                failed_increment = ratio - reached_ratio
                if failed_increment <= MIN_STEP + 1e-12:
                    stage_reason = "minimum_step_reached"
                    break
                step = .5 * failed_increment
                ratio = reached_ratio + step
                set_properties(accepted_properties)
                x_seed = scaled_seed(accepted_x, ratio / reached_ratio) if accepted_x is not None else None
                stage_reason = "attempt_limit"
            solution = dict(x=x_solved[:], chamber_temp_guess=properties()[0],
                            chamber_gamma=properties()[1], molar_mass=properties()[2])
        finally:
            self.inputs.pc, self.inputs.thrust = base_pc, base_thrust
            set_properties(original_properties)
            self._convergence_context = saved_context
            self._thermal_feedback_epoch = saved_thermal_epoch

        continuation_passed = stage_reason == "target_reached" and reached_ratio == 1.0
        convergence["layers"]["continuation"] = self._convergence_layer(
            True, "pass" if continuation_passed else "fail", 1.0 - reached_ratio, 0.0,
            "1", completed_stages, stage_reason, reached_ratio=reached_ratio,
            evaluated_ratio=evaluated_ratio, completed_stages=completed_stages,
            attempts=attempt, rejected_attempts=attempt - completed_stages)
        convergence["warm_start"] = dict(requested=warm_requested,
            accepted=bool(warm_requested and convergence["continuation_history"][0]["accepted"]),
            fallback=bool(warm_requested and not convergence["continuation_history"][0]["accepted"]))
        convergence["solution"] = solution if continuation_passed else None
        results.convergence = convergence
        summary = results.summary
        summary.update(solver_iterations_last=summary.get("solver_iterations", 0),
            solver_iterations=float(sum(max(len(run["points"]) - 1, 0) for run in convergence["inner_runs"])),
            solver_inner_solves=float(len(convergence["inner_runs"])),
            solver_function_evaluations=float(sum(run.get("nfev", 0) for run in convergence["inner_runs"])),
            solver_jacobian_evaluations=float(sum(run.get("njev", 0) for run in convergence["inner_runs"])),
            solver_rejected_steps=float(sum(run.get("rejected_steps", 0) for run in convergence["inner_runs"])),
            tc_outer_iterations_total=float(sum(row.get("picard_iteration", 1)
                for row in convergence["thermal_history"])),
            continuation_reached_ratio=reached_ratio,
            evaluated_chamber_pressure_Pa=base_pc * evaluated_ratio, requested_chamber_pressure_Pa=base_pc,
            evaluated_thrust_target_N=base_thrust * evaluated_ratio, requested_thrust_N=base_thrust)
        if self._target_mr_mode():
            summary.update(mdot_solved_kg_s=x_solved[0], ox_turbine_pi_t_solved=x_solved[1],
                fuel_turbine_pi_t_solved=x_solved[2],
                mdot_fuel_to_ox_preburner_solved=summary.get("mdot_fuel_to_ox_preburner_kg_s"),
                mdot_ox_to_fuel_preburner_solved=summary.get("mdot_ox_to_fuel_preburner_kg_s"),
                ox_preburner_pressure_ratio_solved=summary.get("ox_preburner_pressure_ratio", 0.0),
                fuel_preburner_pressure_ratio_solved=summary.get("fuel_preburner_pressure_ratio", 0.0))
        summary["solver_warning"] = float(any(layer["enabled"] and layer["status"] != "pass"
                                               for layer in convergence["layers"].values()))
        if summary["solver_warning"]:
            results.warnings.insert(0, "稳态嵌套求解未完全收敛，当前结果仅供诊断；详见分层收敛状态。")
        elif progress_callback is not None:
            progress_callback(100, 100, summary["solver_residual_norm"])
        return results

    def throttle_sweep(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        record_callback: Optional[Callable[[dict], None]] = None,
        *,
        design_baseline: Optional[dict] = None,
        include_cooling_marching: bool = True,
        max_point_evaluations: int = 240,
    ) -> list[dict]:
        """
        Quasi-steady target-thrust scan with fixed main-nozzle throat area.
        Chamber pressure is iterated to close the throat constraint. An explicit
        design_baseline reuses saved hardware without recalculating the nominal
        point. The old postprocessing flag only controls a compatibility view;
        physical cooling always participates in the cycle evaluation.
        """
        checkpoint()
        if isinstance(max_point_evaluations, bool) or int(max_point_evaluations) != max_point_evaluations or max_point_evaluations < 1:
            raise ValueError("每个节流点的循环评价预算必须为正整数。")
        max_point_evaluations = int(max_point_evaluations)
        steps = max(2, int(getattr(self.inputs, "throttle_steps", 5)))
        throttle_min = self._clamp(float(getattr(self.inputs, "throttle_min", 0.4)), 0.05, 0.99)
        sweep: list[dict] = []
        warm_seed = None

        def seed_from_result(result):
            """Reuse only a complete local physical solution, never failed trials."""
            from convergence import numerical_convergence
            closure = getattr(result, "steady_closure", {}) or {}
            if (numerical_convergence(result)["status"] != "pass"
                or (closure.get("feed_network") or {}).get("status") != "pass"
                or (closure.get("energy_balance") or {}).get("status") != "pass"):
                return None
            state = copy.deepcopy((result.convergence or {}).get("solution"))
            if not state:
                return None
            targets = None
            if self._temp_target_mode():
                targets = {"ox": result.summary["ox_preburner_mr_target"],
                           "fuel": result.summary["fuel_preburner_mr_target"]}
            return {"warm_state": state, "preburner_mr_targets": targets,
                    "thrust_N": float(result.summary.get("thrust_N", result.inputs.thrust)),
                    "pc_Pa": float(result.summary.get("chamber_pressure_Pa", result.inputs.pc))}

        # Fixed selected geometry and feed resistance; component maps are absent,
        # so this is not a complete fixed-engine operating-envelope model.
        # 旧版（含 V15.1）每个节流点把"推力"和"室压"双双当目标各乘 ratio，再由
        # evaluate() 反算喉部 At=ṁ·c*/Pc。节流时比冲下降 → ṁ 并不随 Pc 同比例缩小，
        # 导出的 At（喉部金属！）漂移 ~10%（40% 点喉部反而比 100% 点更大），等于
        # "每个节流点是另一台尺寸不同的发动机"。
        #
        # 现在：先解 100% 设计点确定固定硬件基准——喉部面积 At_design 与冷却通道
        # 深度。节流只用"推力"一个指令（节流% ≡ 推力%），再反解出令 At 保持 =
        # At_design 的室压 Pc（不动点 Pc←Pc·At_cur/At_design，因 At 对 Pc 单调，
        # 数轮即收敛）。于是 Pc% ≠ 推力%，二者之差即真实节流物理（过膨胀 / 比冲下降）。
        # Web studies use an immutable source design. Never silently fall back
        # to proportional pressure when that explicitly supplied baseline fails.
        if design_baseline is not None:
            frozen_contour = fixed_contour_inputs(design_baseline)
            At_design = float(design_baseline.get("throat_area_m2", 0))
            if not math.isfinite(At_design) or At_design <= 0:
                raise ValueError("固定喉部基准面积必须为有限正数。")
            frozen_h_m = design_baseline.get("cooling_height_m")
            from steady_network import validate_hardware
            feed_hardware = validate_hardware(design_baseline.get("feed_hardware"))
            if design_baseline.get("warm_state") is not None:
                warm_seed = {"warm_state": copy.deepcopy(design_baseline["warm_state"]),
                             "preburner_mr_targets": copy.deepcopy(design_baseline.get("preburner_mr_targets")),
                             "thrust_N": float(design_baseline.get("warm_state_thrust_N",
                                               design_baseline.get("thrust_N", self.inputs.thrust))),
                             "pc_Pa": float(design_baseline.get("warm_state_pc_Pa",
                                            design_baseline.get("pc_Pa", self.inputs.pc)))}
                if not all(math.isfinite(warm_seed[key]) and warm_seed[key] > 0 for key in ("thrust_N", "pc_Pa")):
                    raise ValueError("热启动来源推力与室压必须为有限正数。")
            if frozen_h_m is not None:
                frozen_h_m = float(frozen_h_m)
                if not math.isfinite(frozen_h_m) or frozen_h_m <= 1e-4:
                    raise ValueError("冷却通道冻结高度无效。")
        else:
            At_design = None
            frozen_h_m = None
            feed_hardware = None
            try:
                _design = FFSCCycleModel(copy.deepcopy(self.inputs)).evaluate(
                    include_cooling_marching=include_cooling_marching)
                from convergence import numerical_convergence
                if (numerical_convergence(_design)["status"] != "pass"
                    or _design.steady_closure["feed_network"]["status"] != "pass"
                    or _design.steady_closure["energy_balance"]["status"] != "pass"):
                    raise ValueError("设计点数值、四支路或能量闭合未通过，不能冻结为节流基准。")
                feed_hardware = copy.deepcopy(_design.steady_closure["feed_network"]["hardware_snapshot"])
                warm_seed = seed_from_result(_design)
                profile = getattr(_design.components.get("cooling_channel"), "profile", None) or {}
                frozen_contour = fixed_contour_inputs({
                    "cooling_hardware": {"geometry": profile.get("geometry", {})},
                    "contour_inputs": {key: getattr(_design.inputs, key) for key in (
                        "nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg",
                        "nozzle_bell_fraction", "chamber_conv_half_angle_deg", "expansion_ratio")}})
                At_design = float(_design.components["nozzle"].outputs.get("At_m2", 0.0)) or None
                if (bool(getattr(self.inputs, "cooling_channel_auto_size", 1.0))
                        and bool(getattr(self.inputs, "cooling_bartz_mode", True))):
                    frozen_h_m = float(
                        _design.components["cooling_channel"].outputs.get("channel_height_mm", 0.0)
                    ) / 1000.0
            except Exception as exc:
                raise ValueError(f"无法建立经闭合校核的节流基准：{exc}") from exc

        rated_seed = copy.deepcopy(warm_seed)

        class _ThrottleEvaluationBudgetExceeded(BaseException):
            """Escape all nested retries while retaining cancellation semantics."""

        point_evaluations = 0

        def _solve_at(thrust_target: float, pc_val: float):
            """固定设计喉面积与冷却通道几何，求解一个目标推力/室压工况。

            每次循环评价均闭合唯一冷却内核；关闭旧兼容视图不省略物理计算。
            """
            pi = copy.deepcopy(self.inputs)
            for key, value in frozen_contour.items():
                setattr(pi, key, value)
            pi.thrust = thrust_target
            pi.pc = pc_val
            pi.cooling_marching_enable = 0.0
            if frozen_h_m is not None and frozen_h_m > 1e-4:
                pi.cooling_channel_auto_size = 0.0
                pi.cooling_channel_height_m = frozen_h_m
            model = FFSCCycleModel(pi)
            model._fixed_cooling_throat_area = At_design
            model._feed_hardware = copy.deepcopy(feed_hardware)
            state = copy.deepcopy(warm_seed["warm_state"]) if warm_seed else None
            targets = copy.deepcopy(warm_seed["preburner_mr_targets"]) if warm_seed else None
            if state is not None:
                scale = thrust_target / warm_seed["thrust_N"]
                state["x"][0] *= scale
                if not model._target_mr_mode():
                    state["x"][1] *= scale
                    state["x"][2] *= scale
            original_evaluate = model._evaluate_once
            def budgeted_evaluate(*args):
                nonlocal point_evaluations
                checkpoint()
                if point_evaluations >= max_point_evaluations:
                    raise _ThrottleEvaluationBudgetExceeded(
                        f"本节流点达到 {max_point_evaluations} 次循环评价预算；保留失败，不继续重复延续或喉部迭代。")
                point_evaluations += 1
                return original_evaluate(*args)
            model._evaluate_once = budgeted_evaluate
            return pi, model.evaluate(include_cooling_marching=False, warm_state=state,
                                      preburner_mr_targets=targets)

        for i in range(steps):
            checkpoint()
            point_evaluations = 0
            ratio = throttle_min + (1.0 - throttle_min) * i / max(steps - 1, 1)
            thrust_target = self.inputs.thrust * ratio
            # An ascending two-point scan can jump from 40% directly to 100%.
            # A previous complete point is then a worse seed than the immutable
            # rated source. Compare actual source thrusts, not requested labels.
            candidates = [seed for seed in (rated_seed, warm_seed) if seed is not None]
            if candidates:
                warm_seed = copy.deepcopy(min(candidates,
                    key=lambda seed: abs(math.log(thrust_target / seed["thrust_N"]))))

            if progress_callback is not None:
                try:
                    progress_callback(i + 1, steps, f"{ratio * 100.0:.0f}%")
                except Exception:
                    pass

            try:
                # Keep requested points ascending; use the preceding complete
                # solution (rated source for the first point) as an explicit seed.
                pc = (warm_seed["pc_Pa"] * thrust_target / warm_seed["thrust_N"]
                      if warm_seed else self.inputs.pc * ratio)
                point_inputs, point_results = _solve_at(thrust_target, pc)
                current_seed = seed_from_result(point_results)
                if current_seed is not None:
                    warm_seed = current_seed
                # ── 反解 Pc 使喉部 At 钉死在设计值（固定硬件）──
                if At_design and At_design > 0.0:
                    for _ in range(8):
                        checkpoint()
                        if current_seed is None:
                            break  # A failed physical state cannot guide a throat correction.
                        At_cur = float(
                            point_results.components["nozzle"].outputs.get("At_m2", 0.0))
                        if At_cur <= 0.0 or abs(At_cur / At_design - 1.0) < 5e-4:
                            break
                        pc = pc * (At_cur / At_design)
                        point_inputs, point_results = _solve_at(thrust_target, pc)
                        current_seed = seed_from_result(point_results)
                        if current_seed is not None:
                            warm_seed = current_seed
                # 只复制同一个已求解剖面到历史格式；不执行独立冷却后处理。
                if include_cooling_marching and bool(getattr(self.inputs, "cooling_marching_enable", 1.0)):
                    try:
                        from cooling_marching import run_marching_postpass
                        run_marching_postpass(point_results, point_inputs)
                    except Exception:
                        pass
                residual = float(point_results.summary.get("solver_residual_norm", 1.0))
                from convergence import numerical_convergence
                converged = numerical_convergence(point_results)["status"] == "pass"
                sweep.append({
                    "throttle_ratio": ratio,
                    "thrust_target_N": thrust_target,
                    "pc_Pa": point_inputs.pc,
                    "converged": converged,
                    "results": point_results,
                    "error": "",
                    "cycle_evaluations": point_evaluations,
                    "cycle_evaluation_budget": max_point_evaluations,
                })
            except (Exception, _ThrottleEvaluationBudgetExceeded) as exc:
                sweep.append({
                    "throttle_ratio": ratio,
                    "thrust_target_N": thrust_target,
                    "pc_Pa": self.inputs.pc * ratio,
                    "converged": False,
                    "results": None,
                    "error": str(exc),
                    "cycle_evaluations": point_evaluations,
                    "cycle_evaluation_budget": max_point_evaluations,
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
