"""
gg_cycle.py

燃气发生器开式循环（Gas-Generator, GG）系统级模型 —— V20 新增

对标机型：蓝箭航天 天鹊-12（TQ-12，80 吨级 LOX/LCH4，朱雀二号）及其改进型
（TQ-12 改 / TQ-15 系列）；方法学交叉验证可用 ESA Prometheus（甲烷 GG，
100 吨级，Pc≈10 MPa）与 SpaceX Merlin 1D（煤油 GG，资料最全）。

循环拓扑
────────
    贮箱 → (增压泵) → 主泵 ──┬─ 主路：LOX ─────────────→ 主燃烧室（液-液喷注）
                             │        CH4 → 再生冷却套 →
                             └─ 小流量(2–5%)：LOX+CH4 → 燃气发生器(富燃)
                                    → 涡轮(高压比,驱动泵) → 排气喷管直排(开式)

与 FFSC（cycle.py）的建模差异
──────────────────────────────
1. 主燃烧室是 **液-液** 喷注：走 thermo.gg_chamber_equilibrium()
   （= 预燃室同源 combustion_equilibrium + CoolProp 真实流体焓修正 + η_comb 折减），
   不能走 FFSC 的气-气 chamber_equilibrium（会漏液态焓修正）。
2. 涡轮驱动气不进主燃烧室 → 发动机比冲 = (主室推力 + 排气喷管推力)/(总流量·g0)，
   开式循环的比冲代价被显式算出。
3. 泵出口压力只由主燃烧室支路决定（pc + 液路喷注压降 + 冷却/阀路），
   GG 供应压力为派生量：p_gg = (min(两泵出口) − 阀路压降)/(1 + GG喷注压降比)。
4. 涡轮压比大（p_gg/背压 ~ 15–30）：turbine.py 的 U/C0 选级逻辑自动落到
   "速度复合 2–3 级冲动式"，正是 GG 涡轮的真实形态。

求解变量与残差
──────────────
    single_shaft（单涡轮同轴双泵，默认，Merlin/F-1 布局）：
        x = [mdot_total, alpha_gg]          （alpha_gg = GG 流量 / 总流量）
        r = [推力闭合, 轴功率平衡(η_mech·P_turb − ΣP_pump)]
    dual_shaft（GG 燃气并联分流双涡轮泵，Vulcain 2 布局）：
        x = [mdot_total, alpha_gg, split_ox]（split_ox = 分至 LOX 轴涡轮的燃气份额）
        r = [推力闭合, LOX 轴功率平衡, CH4 轴功率平衡]

GG 混合比：默认温度目标模式（use_target_gg_temp=1），外环反求 MR 使 GG 出口
温度命中 gg_temp_target（顶着涡轮材料极限设计），与 FFSC V11 同构。
"""

from __future__ import annotations

from task_control import checkpoint

import copy
import math
import warnings
from typing import Callable, Optional

from core import (
    ComponentResult,
    CycleResults,
    EngineInputs,
    FlowState,
    TopologyEdge,
    TopologyNode,
)
from cooling import CoolingChannel, estimate_regen_dp
from chamber_sizing import size_combustion_chamber
from pump import Pump, fluid_density
from nozzle import Nozzle
from preburner import Preburner
from turbine import Turbine
from injector import Injector
from thermo import CANTERA_AVAILABLE, vapor_pressure, gg_chamber_equilibrium
from cycle import FFSCCycleModel, _resolve_pump_speed

G0 = 9.80665
STOICH_OF_CH4_O2 = 4.0  # CH4 + 2O2, 质量 O/F = 64/16 = 4


class GGCycleModel:
    """燃气发生器开式循环系统级模型（接口与 FFSCCycleModel 对齐）。"""

    def __init__(self, inputs: EngineInputs):
        self.inputs = inputs
        self._warnings: list[str] = []
        # 温度目标外环写入的 MR 覆盖值（None = 用 inputs.gg_mr_target）
        self._gg_mr_override: Optional[float] = None

    # =====================================================
    # 1. 基础辅助
    # =====================================================

    @property
    def _gg_mr(self) -> float:
        if self._gg_mr_override is not None:
            return float(self._gg_mr_override)
        return float(getattr(self.inputs, "gg_mr_target", 0.30))

    def _dual_shaft(self) -> bool:
        return str(getattr(self.inputs, "gg_layout", "single_shaft")) == "dual_shaft"

    def _n_vars(self) -> int:
        return 3 if self._dual_shaft() else 2

    def _temp_target_mode(self) -> bool:
        return float(getattr(self.inputs, "use_target_gg_temp", 1.0)) >= 0.5

    def _flows_from_total(self, mdot_total: float) -> tuple[float, float]:
        mdot_fuel = mdot_total / (1.0 + self.inputs.of_ratio)
        mdot_ox = mdot_total - mdot_fuel
        return mdot_fuel, mdot_ox

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _sanitize_mdot(self, v: float) -> float:
        return max(float(v), 1e-3)

    def _sanitize_alpha(self, v: float) -> float:
        return self._clamp(float(v), 0.004, 0.12)

    def _sanitize_split(self, v: float) -> float:
        return self._clamp(float(v), 0.05, 0.95)

    def _sanitize_vector(self, x: list[float]) -> list[float]:
        out = [self._sanitize_mdot(x[0]), self._sanitize_alpha(x[1])]
        if self._dual_shaft():
            out.append(self._sanitize_split(x[2]))
        return out

    def _initial_guess_vector(self) -> list[float]:
        mdot0 = self.inputs.thrust / (G0 * max(self.inputs.target_isp, 100.0))
        x = [mdot0, float(getattr(self.inputs, "gg_fraction_guess", 0.03))]
        if self._dual_shaft():
            x.append(0.55)   # LOX 泵功率通常略大（流量大、密度大但压头低）
        return x

    # =====================================================
    # 2. 拓扑（数据层节点/边；渲染由 topology_drawing.GGTopologyRenderer）
    # =====================================================

    def _build_topology(self) -> tuple[list[TopologyNode], list[TopologyEdge]]:
        nodes = [
            TopologyNode("lox_tank", "液氧贮箱", (60, 110)),
            TopologyNode("fuel_tank", "甲烷贮箱", (60, 260)),
            TopologyNode("ox_pump", "液氧泵", (220, 110)),
            TopologyNode("fuel_pump", "甲烷泵", (220, 260)),
            TopologyNode("fuel_cooling", "再生冷却", (420, 300)),
            TopologyNode("gas_generator", "燃气发生器(富燃)", (420, 110)),
            TopologyNode("gg_turbine", "涡轮", (560, 110)),
            TopologyNode("gg_exhaust", "排气喷管(直排)", (700, 60)),
            TopologyNode("main_chamber", "主燃烧室", (620, 260)),
            TopologyNode("nozzle", "喷管", (780, 260)),
        ]
        edges = [
            TopologyEdge("lox_tank", "ox_pump"),
            TopologyEdge("fuel_tank", "fuel_pump"),
            TopologyEdge("ox_pump", "main_chamber", "主路 LOX"),
            TopologyEdge("fuel_pump", "fuel_cooling", "主路 CH4"),
            TopologyEdge("fuel_cooling", "main_chamber"),
            TopologyEdge("ox_pump", "gas_generator", "少量 LOX"),
            TopologyEdge("fuel_pump", "gas_generator", "少量 CH4"),
            TopologyEdge("gas_generator", "gg_turbine"),
            TopologyEdge("gg_turbine", "gg_exhaust", "开式排气"),
            TopologyEdge("main_chamber", "nozzle"),
        ]
        return nodes, edges

    # =====================================================
    # 3. 单次前向评估
    # =====================================================

    def _evaluate_once(self, *x: float) -> CycleResults:
        checkpoint()
        mdot_total = self._sanitize_mdot(x[0])
        alpha_gg = self._sanitize_alpha(x[1])
        split_ox = self._sanitize_split(x[2]) if self._dual_shaft() else 1.0

        mdot_fuel, mdot_ox = self._flows_from_total(mdot_total)

        # ── GG / 主路流量分配 ──────────────────────────────
        mr_gg = self._gg_mr
        mdot_gg = alpha_gg * mdot_total
        mdot_gg_fuel = mdot_gg / (1.0 + mr_gg)
        mdot_gg_ox = mdot_gg - mdot_gg_fuel
        mdot_main_fuel = max(mdot_fuel - mdot_gg_fuel, 1e-3)
        mdot_main_ox = max(mdot_ox - mdot_gg_ox, 1e-3)
        mdot_main = mdot_main_fuel + mdot_main_ox
        mr_main = mdot_main_ox / max(mdot_main_fuel, 1e-9)
        phi_gg = STOICH_OF_CH4_O2 / max(mr_gg, 1e-9)

        states: dict[str, FlowState] = {}
        components: dict[str, object] = {}

        # ── 增压输送系统（与 FFSC 同一套三节点链）──────────────
        p_tank = self.inputs.tank_pressure_pa
        p_boost_out = self.inputs.inlet_pressure
        feed_dp = float(getattr(self.inputs, "feed_line_dp_pa", 0.5e5))
        p_pump_inlet = max(p_boost_out - feed_dp, 0.5e5)

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

        eta_boost = float(getattr(self.inputs, "boost_pump_efficiency", 0.70))
        dp_boost = max(p_boost_out - p_tank, 0.0)
        rho_lox_tank = fluid_density("LOX", self.inputs.ox_inlet_temp, p_tank)
        rho_fuel_tank = fluid_density("CH4", self.inputs.fuel_inlet_temp, p_tank)
        p_boost_lox = mdot_ox * dp_boost / max(rho_lox_tank * eta_boost, 1e-9)
        p_boost_fuel = mdot_fuel * dp_boost / max(rho_fuel_tank * eta_boost, 1e-9)
        pv_lox_tank = vapor_pressure("O2", self.inputs.ox_inlet_temp)
        pv_fuel_tank = vapor_pressure("CH4", self.inputs.fuel_inlet_temp)
        npshA_boost_lox = max(p_tank - pv_lox_tank, 1e3) / (rho_lox_tank * G0)
        npshA_boost_fuel = max(p_tank - pv_fuel_tank, 1e3) / (rho_fuel_tank * G0)
        n_boost = 6000.0
        Q_lox_tank = mdot_ox / max(rho_lox_tank, 1.0)
        Q_fuel_tank = mdot_fuel / max(rho_fuel_tank, 1.0)
        nss_boost_lox_eu = n_boost * math.sqrt(max(Q_lox_tank, 1e-9)) / max(npshA_boost_lox, 0.01) ** 0.75
        nss_boost_fuel_eu = n_boost * math.sqrt(max(Q_fuel_tank, 1e-9)) / max(npshA_boost_fuel, 0.01) ** 0.75

        # ── 压力网络（GG 版：主室支路决定泵出口）──────────────
        # 主燃烧室液路喷注压降（复活的 injector_dp_ratio_*，V20）
        dp_ratio_inj_ox = float(getattr(self.inputs, "injector_dp_ratio_ox", 0.20))
        dp_ratio_inj_fuel = float(getattr(self.inputs, "injector_dp_ratio_fuel", 0.20))
        dp_inj_ox = dp_ratio_inj_ox * self.inputs.pc
        dp_inj_fuel = dp_ratio_inj_fuel * self.inputs.pc
        dp_valve = self.inputs.valve_line_dp_ratio * self.inputs.pc

        # 再生冷却压降（固定 / 水力实算，与 FFSC 同机制；冷却剂 = 主路 CH4）
        _cool_auto_size = bool(getattr(self.inputs, "cooling_channel_auto_size", 1.0))
        _cool_dp_auto = bool(getattr(self.inputs, "cooling_dp_auto", 0.0))
        if _cool_dp_auto:
            _regen = estimate_regen_dp(
                mdot_fuel=mdot_main_fuel,
                t_proxy=self.inputs.fuel_inlet_temp + 15.0,
                p_proxy=1.15 * self.inputs.pc,
                mdot_total=mdot_main, pc=self.inputs.pc,
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
            dp_cooling = min(_regen["dp_pa"], 0.5 * self.inputs.pc)
        else:
            dp_cooling = self.inputs.cooling_dp_pa

        p_lox_pump_out = self.inputs.pc + dp_inj_ox + dp_valve
        p_ch4_pump_out = self.inputs.pc + dp_inj_fuel + dp_cooling + dp_valve

        # GG 供应压力（派生）：取两条供应支路中较低者
        gg_inj_ratio = float(getattr(self.inputs, "gg_injector_dp_ratio", 0.15))
        p_gg_supply = min(p_lox_pump_out, p_ch4_pump_out) - dp_valve
        p_gg = max(p_gg_supply / (1.0 + gg_inj_ratio), 2.0 * 0.10e6)
        p_te = float(getattr(self.inputs, "gg_turbine_backpressure_pa", 0.40e6))
        pi_t_gg = p_gg / max(p_te, 1.0)

        # ── 涡轮泵转速 ────────────────────────────────────
        rho_lox_inlet = fluid_density("LOX", self.inputs.ox_inlet_temp, p_pump_inlet)
        rho_fuel_inlet = fluid_density("CH4", self.inputs.fuel_inlet_temp, p_pump_inlet)
        dp_lox = p_lox_pump_out - p_pump_inlet
        dp_fuel = p_ch4_pump_out - p_pump_inlet
        if self._dual_shaft():
            n_lox_rpm = _resolve_pump_speed(
                override_rpm=self.inputs.lox_pump_speed_rpm_override,
                ns_target=self.inputs.lox_pump_specific_speed_target,
                n_stages=self.inputs.lox_pump_n_stages,
                psi=self.inputs.lox_pump_head_coefficient_psi,
                rho=rho_lox_inlet, mdot=mdot_ox, dp=dp_lox,
                default_rpm=18000.0,
            )
            n_fuel_rpm = _resolve_pump_speed(
                override_rpm=self.inputs.fuel_pump_speed_rpm_override,
                ns_target=self.inputs.fuel_pump_specific_speed_target,
                n_stages=self.inputs.fuel_pump_n_stages,
                psi=self.inputs.fuel_pump_head_coefficient_psi,
                rho=rho_fuel_inlet, mdot=mdot_fuel, dp=dp_fuel,
                default_rpm=22000.0,
            )
        else:
            # 单轴：一根轴一个转速（LOX 侧汽蚀通常更严 → 以 LOX 泵定轴速）
            n_shaft = _resolve_pump_speed(
                override_rpm=self.inputs.lox_pump_speed_rpm_override,
                ns_target=self.inputs.lox_pump_specific_speed_target,
                n_stages=self.inputs.lox_pump_n_stages,
                psi=self.inputs.lox_pump_head_coefficient_psi,
                rho=rho_lox_inlet, mdot=mdot_ox, dp=dp_lox,
                default_rpm=18000.0,
            )
            n_lox_rpm = n_fuel_rpm = n_shaft

        # ── NPSHa & 主泵 ─────────────────────────────────
        pv_lox = vapor_pressure("O2", self.inputs.ox_inlet_temp)
        pv_fuel = vapor_pressure("CH4", self.inputs.fuel_inlet_temp)
        npshA_lox = max(p_pump_inlet - pv_lox, 1e3)
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

        # ── 再生冷却（冷却剂 = 主路 CH4；Bartz 喉部按主室流量）──────
        bartz_mode = bool(getattr(self.inputs, "cooling_bartz_mode", True))
        try:
            from materials import MATERIALS as _MATS_K
            _im_k = _MATS_K.get(getattr(self.inputs, "chamber_inner_wall_material", "grcop84"))
            _liner_k = (float(_im_k.thermal_conductivity_at(600.0)) if _im_k
                        else float(getattr(self.inputs, "cooling_wall_conductivity", 320.0)))
        except Exception:
            _liner_k = float(getattr(self.inputs, "cooling_wall_conductivity", 320.0))
        cooling = CoolingChannel(
            mdot_fuel=mdot_main_fuel,
            t_inlet=fuel_pump_out.temperature,
            p_inlet=fuel_pump_out.pressure,
            dp_pa=dp_cooling,
            heat_load=self.inputs.cooling_heat_load,
            cp=self.inputs.coolant_cp,
            bartz_mode=bartz_mode,
            mdot_total=mdot_main,
            pc=self.inputs.pc,
            t_chamber=self.inputs.chamber_temp_guess,
            gamma_c=self.inputs.chamber_gamma,
            molar_mass_c=self.inputs.molar_mass,
            contraction_ratio=self.inputs.chamber_contraction_ratio,
            l_star_m=self.inputs.chamber_l_star_m,
            n_channels=int(getattr(self.inputs, "cooling_n_channels", 300)),
            channel_width_m=getattr(self.inputs, "cooling_channel_width_m", 1.5e-3),
            channel_height_m=getattr(self.inputs, "cooling_channel_height_m", 5.0e-3),
            wall_thickness_m=getattr(self.inputs, "cooling_wall_thickness_m", 0.5e-3),
            wall_conductivity=_liner_k,
            htc_enhancement=getattr(self.inputs, "cooling_htc_enhancement", 1.0),
            auto_size=_cool_auto_size,
            target_velocity_ms=getattr(self.inputs, "cooling_target_velocity_ms", 100.0),
            dp_auto=_cool_dp_auto,
            channel_rel_roughness=getattr(self.inputs, "cooling_channel_rel_roughness", 0.003),
            channel_k_minor=getattr(self.inputs, "cooling_channel_k_minor", 3.0),
            cover_eps=(getattr(self.inputs, "cooling_cover_eps", 0.0)
                       or self.inputs.expansion_ratio),
            conv_half_angle_deg=getattr(self.inputs, "chamber_conv_half_angle_deg", 30.0),
        )
        cooled_fuel_state, cooling_result = cooling.evaluate()
        states["fuel_after_cooling"] = cooled_fuel_state
        components[cooling_result.name] = cooling_result

        # ── 燃气发生器（富燃 HP 平衡；供料 = 两泵出口液体）─────────
        gas_generator = Preburner(
            name="gas_generator", pressure=p_gg,
            mdot_fuel=mdot_gg_fuel, mdot_ox=mdot_gg_ox,
            phi=phi_gg,
            fuel_temp=fuel_pump_out.temperature, ox_temp=lox_pump_out.temperature,
            injector_dp_ratio=gg_inj_ratio,
        )
        gg_outlet_flow, gg_gas_state, gg_result = gas_generator.evaluate()
        gg_result.outputs["mixture_ratio_OF"] = mr_gg
        gg_result.notes.append(
            "燃气发生器 = 富燃液-液燃烧器（开式循环）；排气驱动涡轮后直排，不进主燃烧室。")
        states["gg_outlet"] = gg_outlet_flow
        components[gg_result.name] = gg_result
        t_comb_gg = gg_result.outputs["T_combustion_K"]

        # ── 涡轮（单轴 1 台 / 双轴燃气并联分流 2 台）────────────────
        eta_mech = float(getattr(self.inputs, "gg_mech_efficiency", 0.97))
        gg_turbines: list[ComponentResult] = []
        if self._dual_shaft():
            turb_ox = Turbine(
                name="gg_turbine_ox",
                inlet_state=gg_gas_state,
                exhaust_pressure=p_te,
                eta_turbine=self.inputs.turbine_efficiency_ox,
                mass_flow=split_ox * mdot_gg,
                shaft_speed_rpm=n_lox_rpm,
                u_over_c_target=self.inputs.turb_ox_u_over_c_target,
                reaction_degree=self.inputs.turb_ox_reaction_degree,
                n_stages_user=self.inputs.turb_ox_n_stages_user,
                rotor_material=self.inputs.turb_ox_rotor_material,
                side="fuel",   # GG 燃气为富燃 → 无需富氧兼容审计
                t_in_limit_K=getattr(self.inputs, "gg_temp_limit", 950.0),
                u_max_ms=self.inputs.turb_ox_u_max_ms,
            )
            turb_fuel = Turbine(
                name="gg_turbine_fuel",
                inlet_state=gg_gas_state,
                exhaust_pressure=p_te,
                eta_turbine=self.inputs.turbine_efficiency_fuel,
                mass_flow=(1.0 - split_ox) * mdot_gg,
                shaft_speed_rpm=n_fuel_rpm,
                u_over_c_target=self.inputs.turb_fuel_u_over_c_target,
                reaction_degree=self.inputs.turb_fuel_reaction_degree,
                n_stages_user=self.inputs.turb_fuel_n_stages_user,
                rotor_material=self.inputs.turb_fuel_rotor_material,
                side="fuel",
                t_in_limit_K=getattr(self.inputs, "gg_temp_limit", 950.0),
                u_max_ms=self.inputs.turb_fuel_u_max_ms,
            )
            turb_ox_out, turb_ox_result = turb_ox.evaluate()
            turb_fuel_out, turb_fuel_result = turb_fuel.evaluate()
            components[turb_ox_result.name] = turb_ox_result
            components[turb_fuel_result.name] = turb_fuel_result
            gg_turbines = [turb_ox_result, turb_fuel_result]
            w_turb_ox = turb_ox_result.outputs["turbine_power_W"]
            w_turb_fuel = turb_fuel_result.outputs["turbine_power_W"]
            w_turb_total = w_turb_ox + w_turb_fuel
            t_te = turb_ox_result.outputs["T_exhaust_K"]
            gamma_te = float(turb_ox_result.outputs.get("gamma_in", 1.25) or 1.25)
            mw_te = float(turb_ox_result.outputs.get("molar_mass_in", 15.0) or 15.0)
            exhaust_flow = FlowState(
                name="gg_turbine_exhaust", pressure=p_te,
                temperature=t_te, mass_flow=mdot_gg,
                composition=turb_ox_out.composition,
            )
        else:
            turbine = Turbine(
                name="gg_turbine",
                inlet_state=gg_gas_state,
                exhaust_pressure=p_te,
                eta_turbine=self.inputs.turbine_efficiency_fuel,
                mass_flow=mdot_gg,
                shaft_speed_rpm=n_lox_rpm,   # 单轴：泵/涡轮同轴同速
                u_over_c_target=self.inputs.turb_fuel_u_over_c_target,
                reaction_degree=self.inputs.turb_fuel_reaction_degree,
                n_stages_user=self.inputs.turb_fuel_n_stages_user,
                rotor_material=self.inputs.turb_fuel_rotor_material,
                side="fuel",
                t_in_limit_K=getattr(self.inputs, "gg_temp_limit", 950.0),
                u_max_ms=self.inputs.turb_fuel_u_max_ms,
            )
            turb_out, turb_result = turbine.evaluate()
            components[turb_result.name] = turb_result
            gg_turbines = [turb_result]
            w_turb_ox = w_turb_fuel = 0.0
            w_turb_total = turb_result.outputs["turbine_power_W"]
            t_te = turb_result.outputs["T_exhaust_K"]
            gamma_te = float(turb_result.outputs.get("gamma_in", 1.25) or 1.25)
            mw_te = float(turb_result.outputs.get("molar_mass_in", 15.0) or 15.0)
            exhaust_flow = FlowState(
                name="gg_turbine_exhaust", pressure=p_te,
                temperature=t_te, mass_flow=mdot_gg,
                composition=turb_out.composition,
            )
        states["gg_turbine_exhaust"] = exhaust_flow

        # ── 排气喷管（开式直排，小膨胀比；理想气体路径）────────────
        eps_gg = float(getattr(self.inputs, "gg_exhaust_expansion_ratio", 4.0))
        gg_nozzle = Nozzle(
            pc=p_te, expansion_ratio=eps_gg,
            gamma=gamma_te, molar_mass=mw_te,
            tc=max(t_te, 200.0), mdot=mdot_gg,
            p_amb=self.inputs.ambient_pressure,
            chamber_state=None,   # 理想气体路径（GG 排气推力占比小，一阶足够）
        )
        gg_nozzle_result = gg_nozzle.evaluate()
        gg_nozzle_result.name = "gg_exhaust_nozzle"
        gg_nozzle_result.notes.insert(
            0, "开式循环涡轮排气喷管（直排）：贡献少量推力，计入发动机总推力/比冲。")
        components[gg_nozzle_result.name] = gg_nozzle_result
        f_gg_sl = gg_nozzle_result.outputs["thrust_N"]
        f_gg_vac = gg_nozzle_result.outputs.get("thrust_vac_N", f_gg_sl)

        # ── 主燃烧室（液-液 HP 平衡 + η_comb 折减）─────────────────
        chamber_gas = gg_chamber_equilibrium(
            mdot_fuel=mdot_main_fuel, mdot_ox=mdot_main_ox,
            T_fuel=cooled_fuel_state.temperature, T_ox=lox_pump_out.temperature,
            pressure=self.inputs.pc,
            efficiency=self.inputs.combustion_efficiency,
        )
        t_chamber = chamber_gas.temperature
        gamma_calc = chamber_gas.gamma
        mw_calc = chamber_gas.molar_mass
        _t_mix_ref = ((mdot_main_fuel * cooled_fuel_state.temperature
                       + mdot_main_ox * lox_pump_out.temperature)
                      / max(mdot_main, 1e-9))
        eta_c = self.inputs.combustion_efficiency
        t_chamber_ideal = (_t_mix_ref + (t_chamber - _t_mix_ref) / eta_c
                           if eta_c < 1.0 else t_chamber)

        chamber_state = FlowState(
            name="main_chamber",
            pressure=self.inputs.pc,
            temperature=t_chamber,
            mass_flow=mdot_main,
            enthalpy=chamber_gas.enthalpy,
            composition=chamber_gas.composition,
            metadata={
                "gamma": gamma_calc,
                "molar_mass": mw_calc,
                "cp": chamber_gas.cp,
                "thermo_source": 1.0 if chamber_gas.source == "cantera" else 0.0,
            },
        )
        chamber_result = ComponentResult(
            name="main_chamber",
            outputs={
                "T_chamber_K": t_chamber,
                "T_chamber_ideal_K": t_chamber_ideal,
                "combustion_efficiency": eta_c,
                "gamma": gamma_calc,
                "molar_mass_kg_per_kmol": mw_calc,
                "cp_Jkgk": chamber_gas.cp,
                "h_chamber_Jkg": chamber_gas.enthalpy,
                "mixture_ratio_OF": mr_main,
                "mdot_chamber_kgs": mdot_main,
            },
            notes=[
                f"热力学来源: {chamber_gas.source}（液-液 LOX/CH4 喷注，CoolProp 焓修正）",
                f"T_chamber = {t_chamber:.0f} K, γ = {gamma_calc:.4f}, MW = {mw_calc:.2f}",
                f"主燃烧室混合比 O/F = {mr_main:.3f}"
                f"（整机 O/F = {self.inputs.of_ratio:.3f}；GG 抽走富燃小流量所致差异）",
                (f"燃烧效率 η_comb = {eta_c:.3f} → 理想 {t_chamber_ideal:.0f} K 折减 "
                 f"{t_chamber_ideal - t_chamber:.0f} K" if eta_c < 1.0
                 else "燃烧效率 η_comb = 1.000（理想完全燃烧）"),
            ],
        )
        states["main_chamber"] = chamber_state
        components[chamber_result.name] = chamber_result

        # ── 主喷管 ─────────────────────────────────────────
        nozzle = Nozzle(
            pc=self.inputs.pc, expansion_ratio=self.inputs.expansion_ratio,
            gamma=gamma_calc, molar_mass=mw_calc,
            tc=t_chamber, mdot=mdot_main,
            p_amb=self.inputs.ambient_pressure, chamber_state=chamber_gas,
        )
        nozzle_result = nozzle.evaluate()
        components[nozzle_result.name] = nozzle_result
        f_main_sl = nozzle_result.outputs["thrust_N"]
        f_main_vac = nozzle_result.outputs.get("thrust_vac_N", f_main_sl)

        # ── 燃烧室几何 + 分层壁厚（与 FFSC 同口径）──────────────────
        _cc = size_combustion_chamber(
            At=nozzle_result.outputs["At_m2"],
            pc=self.inputs.pc,
            mdot_total=mdot_main,
            T_c=t_chamber,
            molar_mass=mw_calc,
            gamma=gamma_calc,
            l_star=getattr(self.inputs, "chamber_l_star_m", 1.0),
            contraction_ratio=getattr(self.inputs, "chamber_contraction_ratio", 2.5),
            conv_half_angle_deg=getattr(self.inputs, "chamber_conv_half_angle_deg", 30.0),
            c_star=nozzle_result.outputs.get("c_star_ms"),
        )
        _cc_notes = _cc.pop("_notes", [])
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
        except Exception:
            pass
        chamber_result.outputs.update(_cc)
        chamber_result.notes.extend(_cc_notes)
        chamber_result.notes.extend(_cc_validation)

        # ── 主喷注器（液-液：真实液体密度）────────────────────────
        chamber_radius = _cc["cc_diameter_m"] / 2.0
        rho_ox_inj = (lox_pump_out.density
                      or fluid_density("LOX", lox_pump_out.temperature, lox_pump_out.pressure))
        rho_fuel_inj = (cooled_fuel_state.density
                        or fluid_density("CH4", cooled_fuel_state.temperature,
                                         cooled_fuel_state.pressure))
        injector = Injector(
            mdot_ox=mdot_main_ox,
            mdot_fuel=mdot_main_fuel,
            pc=self.inputs.pc,
            dp_ratio_ox=dp_ratio_inj_ox,
            dp_ratio_fuel=dp_ratio_inj_fuel,
            chamber_radius=chamber_radius,
            rho_ox=rho_ox_inj,
            rho_fuel=rho_fuel_inj,
        )
        injector_result = injector.evaluate()
        injector_result.notes.insert(
            0, "GG 循环主喷注器为液-液喷注（离心式/互击式），密度取泵后/冷却后真实液态值。")
        components[injector_result.name] = injector_result

        # ── 功率平衡 & 推力合成 ─────────────────────────────────
        w_pump_ox = lox_pump_result.outputs["power_W"]
        w_pump_fuel = fuel_pump_result.outputs["power_W"]
        w_pump_total = w_pump_ox + w_pump_fuel
        w_turb_delivered = w_turb_total * eta_mech

        if self._dual_shaft():
            bal_ox = (w_turb_ox * eta_mech) / max(w_pump_ox, 1e-9)
            bal_fuel = (w_turb_fuel * eta_mech) / max(w_pump_fuel, 1e-9)
        else:
            bal_ox = bal_fuel = w_turb_delivered / max(w_pump_total, 1e-9)
        total_balance = w_turb_delivered / max(w_pump_total, 1e-9)

        thrust_total_sl = f_main_sl + f_gg_sl
        thrust_total_vac = f_main_vac + f_gg_vac
        isp_engine_sl = thrust_total_sl / max(mdot_total * G0, 1e-9)
        isp_engine_vac = thrust_total_vac / max(mdot_total * G0, 1e-9)
        isp_main_flow_sl = f_main_sl / max(mdot_main * G0, 1e-9)
        isp_gg_sl = f_gg_sl / max(mdot_gg * G0, 1e-9)
        isp_penalty_sl = isp_main_flow_sl - isp_engine_sl

        # ── 后验告警 ───────────────────────────────────────────
        cycle_warnings = list(self._warnings)
        gg_temp_limit = float(getattr(self.inputs, "gg_temp_limit", 950.0))
        if t_comb_gg > gg_temp_limit:
            cycle_warnings.append(
                f"⚠ 燃气发生器出口温度 {t_comb_gg:.0f} K 超过涡轮材料限值 "
                f"{gg_temp_limit:.0f} K（O/F={mr_gg:.3f}）。"
            )
        if not (0.20 <= mr_gg <= 0.45):
            cycle_warnings.append(
                f"⚠ GG 混合比 O/F={mr_gg:.3f} 超出富燃甲烷 GG 典型范围 0.20–0.45。")
        if not (0.015 <= alpha_gg <= 0.06):
            cycle_warnings.append(
                f"⚠ GG 流量分数 {alpha_gg*100:.2f}% 超出开式循环典型范围 1.5–6%。")
        if pi_t_gg < 4.0:
            cycle_warnings.append(
                f"⚠ GG 涡轮压比 π_t={pi_t_gg:.1f} 偏低（开式循环典型 10–30），"
                f"检查背压/泵出口压力设置。")
        if p_gg < 2.0 * p_te:
            cycle_warnings.append(
                f"⚠ GG 压力 {p_gg/1e6:.2f} MPa 不足背压 {p_te/1e6:.2f} MPa 的 2 倍，"
                f"涡轮喷嘴难以壅塞。")
        if not self._dual_shaft():
            cycle_warnings.append(
                "ℹ 单轴布局：LOX/CH4 泵与涡轮同轴同速（轴速按 LOX 泵确定），"
                "CH4 泵比转速为派生诊断值。")
        cycle_warnings.append(
            "ℹ GG 模式几何页映射：预燃室栏位显示燃气发生器（富燃）几何；"
            "涡轮栏位为 GG 涡轮。")

        topology_nodes, topology_edges = self._build_topology()

        mode_text = ("燃气发生器开式循环 · GG 温度目标模式"
                     if self._temp_target_mode() else
                     "燃气发生器开式循环 · 固定 GG 混合比模式")
        summary = {
            "cycle_type": 1.0,   # 0/缺省=FFSC，1=GG（数值型，供导出兼容）
            "cycle_type_text": "gg",
            "solve_mode": 3.0,
            "solve_mode_text": mode_text,
            "thermo_source": 1.0 if CANTERA_AVAILABLE else 0.0,
            # 流量
            "mdot_total_kg_s": mdot_total,
            "mdot_fuel_kg_s": mdot_fuel,
            "mdot_ox_kg_s": mdot_ox,
            "mdot_chamber_kg_s": mdot_main,          # 主燃烧室通流（≠总流量，开式）
            "mdot_coolant_kg_s": mdot_main_fuel,     # 再生冷却剂流量（主路 CH4）
            "chamber_mixture_ratio": mr_main,
            # GG
            "gg_mdot_kg_s": mdot_gg,
            "gg_fraction": alpha_gg,
            "gg_mdot_fuel_kg_s": mdot_gg_fuel,
            "gg_mdot_ox_kg_s": mdot_gg_ox,
            "gg_mr": mr_gg,
            "gg_mr_target": float(getattr(self.inputs, "gg_mr_target", 0.30)),
            "gg_phi": phi_gg,
            "gg_T_combustion_K": t_comb_gg,
            "gg_pressure_Pa": p_gg,
            "gg_pressure_MPa": p_gg / 1e6,
            "gg_turbine_pi_t": pi_t_gg,
            "gg_turbine_backpressure_Pa": p_te,
            "gg_turbine_exhaust_T_K": t_te,
            "gg_split_to_ox_shaft": split_ox if self._dual_shaft() else 1.0,
            "gg_layout_dual": 1.0 if self._dual_shaft() else 0.0,
            "gg_mech_efficiency": eta_mech,
            # 压力网络
            "p_pump_out_Pa": max(p_lox_pump_out, p_ch4_pump_out),
            "lox_pump_out_pressure_Pa": p_lox_pump_out,
            "fuel_pump_out_pressure_Pa": p_ch4_pump_out,
            "lox_pump_out_pressure_MPa": p_lox_pump_out / 1e6,
            "fuel_pump_out_pressure_MPa": p_ch4_pump_out / 1e6,
            "injector_dp_ox_MPa": dp_inj_ox / 1e6,
            "injector_dp_fuel_MPa": dp_inj_fuel / 1e6,
            "cooling_dp_MPa": dp_cooling / 1e6,
            "valve_line_dp_MPa": dp_valve / 1e6,
            # 增压输送系统（与 FFSC 同键）
            "tank_pressure_bar": p_tank / 1e5,
            "boost_out_pressure_bar": p_boost_out / 1e5,
            "main_pump_inlet_pressure_bar": p_pump_inlet / 1e5,
            "boost_pump_dp_bar": dp_boost / 1e5,
            "boost_pump_ox_power_MW": p_boost_lox / 1e6,
            "boost_pump_fuel_power_MW": p_boost_fuel / 1e6,
            "boost_pump_total_power_MW": (p_boost_lox + p_boost_fuel) / 1e6,
            "boost_pump_efficiency": eta_boost,
            "boost_npsha_ox_m": npshA_boost_lox,
            "boost_npsha_fuel_m": npshA_boost_fuel,
            "boost_nss_ox_eu": nss_boost_lox_eu,
            "boost_nss_fuel_eu": nss_boost_fuel_eu,
            "boost_pump_speed_assumed_rpm": n_boost,
            "pressure_network_note": (
                f"贮箱 {p_tank/1e5:.1f} bar → 增压泵 → {p_boost_out/1e5:.1f} bar "
                f"→ 短管 −{feed_dp/1e5:.1f} bar → 主泵入口 {p_pump_inlet/1e5:.1f} bar；"
                f"GG 供应压力 {p_gg/1e6:.2f} MPa（派生自泵出口），"
                f"涡轮压比 π_t={pi_t_gg:.1f}（开式大压比）。"
            ),
            # 功率
            "ox_turbine_power_MW": (w_turb_ox if self._dual_shaft() else w_turb_total) / 1e6,
            "fuel_turbine_power_MW": (w_turb_fuel if self._dual_shaft() else 0.0) / 1e6,
            "gg_turbine_power_MW": w_turb_total / 1e6,
            "gg_turbine_power_delivered_MW": w_turb_delivered / 1e6,
            "ox_pump_power_MW": w_pump_ox / 1e6,
            "fuel_pump_power_MW": w_pump_fuel / 1e6,
            "turbine_power_MW": w_turb_total / 1e6,
            "pump_power_MW": w_pump_total / 1e6,
            "ox_side_power_balance_ratio": bal_ox,
            "fuel_side_power_balance_ratio": bal_fuel,
            "power_balance_ratio": total_balance,
            "ox_side_power_error_percent": 100.0 * (bal_ox - 1.0),
            "fuel_side_power_error_percent": 100.0 * (bal_fuel - 1.0),
            "power_balance_error_percent": 100.0 * (total_balance - 1.0),
            # 主燃烧室
            "chamber_temperature_K": t_chamber,
            "chamber_gamma": gamma_calc,
            "chamber_molar_mass": mw_calc,
            "chamber_pressure_Pa": self.inputs.pc,
            # 推力/比冲（发动机总口径 = 主室 + GG 排气）
            "thrust_N": thrust_total_sl,
            "Isp_s": isp_engine_sl,
            "thrust_vac_N": thrust_total_vac,
            "Isp_vac_s": isp_engine_vac,
            "thrust_main_N": f_main_sl,
            "thrust_main_vac_N": f_main_vac,
            "thrust_gg_exhaust_N": f_gg_sl,
            "thrust_gg_exhaust_vac_N": f_gg_vac,
            "Isp_main_flow_s": isp_main_flow_sl,
            "Isp_gg_exhaust_s": isp_gg_sl,
            "Isp_gg_penalty_s": isp_penalty_sl,
            "nozzle_flow_model": nozzle_result.outputs.get("flow_model", 0.0),
            "target_thrust_N": self.inputs.thrust,
            "thrust_error_percent":
                100.0 * (thrust_total_sl - self.inputs.thrust) / max(self.inputs.thrust, 1e-9),
        }

        return CycleResults(
            inputs=self.inputs, states=states, components=components,
            summary=summary, topology_nodes=topology_nodes,
            topology_edges=topology_edges, warnings=cycle_warnings,
        )

    # =====================================================
    # 4. 残差与求解器（N 维阻尼牛顿，N=2/3）
    # =====================================================

    def residual_vector(self, *x: float):
        checkpoint()
        results = self._evaluate_once(*x)
        s = results.summary
        r = [s["thrust_N"] - self.inputs.thrust]
        eta_mech = s["gg_mech_efficiency"]
        if self._dual_shaft():
            r.append(s["ox_turbine_power_MW"] * 1e6 * eta_mech
                     - s["ox_pump_power_MW"] * 1e6)
            r.append(s["fuel_turbine_power_MW"] * 1e6 * eta_mech
                     - s["fuel_pump_power_MW"] * 1e6)
        else:
            r.append(s["gg_turbine_power_MW"] * 1e6 * eta_mech
                     - s["pump_power_MW"] * 1e6)
        return r, results

    def _normalized_residual_norm(self, residuals) -> float:
        scales = [max(abs(self.inputs.thrust), 1e-9)] + [5.0e6] * (len(residuals) - 1)
        return max(abs(residuals[i]) / scales[i] for i in range(len(residuals)))

    @staticmethod
    def _solve_linear(a, b):
        """高斯消元（N×N，N=2/3）。"""
        n = len(b)
        m = [row[:] + [rhs] for row, rhs in zip(a, b)]
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

    def _finite_difference_jacobian(self, x, r):
        n = len(x)
        jac = [[0.0] * n for _ in range(n)]
        steps = [5e-4 * max(abs(x[0]), 1.0), 1.5e-3, 0.02][:n]
        for j in range(n):
            checkpoint()
            x_pert = x[:]
            x_pert[j] += steps[j]
            x_pert = self._sanitize_vector(x_pert)
            r_pert, _ = self.residual_vector(*x_pert)
            actual_dx = x_pert[j] - x[j]
            actual_dx = actual_dx if abs(actual_dx) > 1e-12 else steps[j]
            for i in range(n):
                checkpoint()
                jac[i][j] = (r_pert[i] - r[i]) / actual_dx
        return jac

    def solve_cycle(
        self,
        tol_rel: float = 1e-4,
        max_iter: int = 50,
        verbose: bool = False,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        x_init: Optional[list[float]] = None,
    ):
        checkpoint()
        if x_init is None:
            x = self._initial_guess_vector()
        else:
            x = self._sanitize_vector([float(v) for v in x_init])
        residuals, results = self.residual_vector(*x)

        STAGNATION_WINDOW = 8
        STAGNATION_RATIO = 0.1
        norm_history: list[float] = []
        n = len(x)

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
                print(f"[GG Iter {it:02d}] mdot={x[0]:.2f} α_gg={x[1]:.4f} "
                      + (f"split={x[2]:.3f} " if n == 3 else "")
                      + f"norm={norm0:.3e}")

            if len(norm_history) > STAGNATION_WINDOW and norm0 > tol_rel * 100:
                norm_baseline = norm_history[-STAGNATION_WINDOW - 1]
                if norm0 > STAGNATION_RATIO * norm_baseline:
                    results.summary["solver_iterations"] = float(it)
                    results.summary["solver_residual_norm"] = norm0
                    results.summary["solver_warning"] = 1.0
                    results.summary["solver_stalled"] = 1.0
                    return x, results

            if norm0 < tol_rel:
                results.summary["solver_iterations"] = float(it)
                results.summary["solver_residual_norm"] = norm0
                return x, results

            jac = self._finite_difference_jacobian(x, residuals)
            try:
                dx = self._solve_linear(jac, [-ri for ri in residuals])
            except RuntimeError:
                dx = [-0.08 * x[0] * residuals[0] / max(abs(self.inputs.thrust), 1e-9),
                      -0.01 * (residuals[1] / 5.0e6)]
                if n == 3:
                    dx.append(-0.05 * (residuals[2] / 5.0e6))

            max_step = [0.15 * abs(x[0]), 0.010, 0.10][:n]
            dx = [self._clamp(dx[i], -max_step[i], max_step[i]) for i in range(n)]

            accepted = False
            for alpha in [1.0, 0.5, 0.25, 0.1, 0.05, 0.02]:
                checkpoint()
                x_trial = self._sanitize_vector([x[i] + alpha * dx[i] for i in range(n)])
                try:
                    r_trial, res_trial = self.residual_vector(*x_trial)
                except Exception:
                    continue
                if self._normalized_residual_norm(r_trial) < norm0:
                    x, residuals, results = x_trial, r_trial, res_trial
                    accepted = True
                    break
            if not accepted:
                x_trial = self._sanitize_vector([x[i] + 0.01 * dx[i] for i in range(n)])
                try:
                    residuals, results = self.residual_vector(*x_trial)
                    x = x_trial
                except Exception:
                    break

        results.summary["solver_iterations"] = float(max_iter)
        results.summary["solver_residual_norm"] = self._normalized_residual_norm(residuals)
        results.summary["solver_warning"] = 1.0
        return x, results

    # =====================================================
    # 5. 设计点求解入口（continuation + Tc Picard + GG 温度目标外环）
    # =====================================================

    def evaluate_forward(self):
        checkpoint()
        results = self._evaluate_once(*self._initial_guess_vector())
        self._attach_cooling_marching(results)
        return results

    def evaluate(self, progress_callback: Optional[Callable[[int, int, float], None]] = None):
        checkpoint()
        if self._temp_target_mode():
            results = self._evaluate_temp_targeted(progress_callback)
        else:
            results = self._evaluate_design_core(progress_callback)
        self._attach_cooling_marching(results)
        return results

    def _attach_cooling_marching(self, results) -> None:
        checkpoint()
        if float(getattr(self.inputs, "cooling_marching_enable", 1.0)) < 0.5:
            return
        if not bool(getattr(self.inputs, "cooling_bartz_mode", True)):
            return
        from cooling_marching import run_marching_postpass
        run_marching_postpass(results, self.inputs)

    def _mr_for_target_gg_temp(
        self, T_target: float, T_fuel_in: float, T_ox_in: float, p_gg: float,
    ) -> float:
        """一维反求 GG 混合比使富燃火焰温度 = T_target（对 MR 单调递增）。"""
        checkpoint()
        from thermo import combustion_equilibrium

        def temp_at(mr: float) -> float:
            comb = combustion_equilibrium(
                mdot_fuel=1.0, mdot_ox=max(mr, 1e-6),
                T_fuel=T_fuel_in, T_ox=T_ox_in, pressure=p_gg,
            )
            return comb.temperature

        lo, hi = 0.05, 0.90
        T_lo, T_hi = temp_at(lo), temp_at(hi)
        if (T_target - T_lo) * (T_target - T_hi) > 0.0:
            nearest = lo if abs(T_target - T_lo) < abs(T_target - T_hi) else hi
            msg = (f"⚠ GG 目标温度 {T_target:.0f} K 超出 MR∈[{lo:g},{hi:g}] 可达温度 "
                   f"[{min(T_lo, T_hi):.0f},{max(T_lo, T_hi):.0f}] K，MR 截断到 {nearest:g}")
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
            return nearest
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
        self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ):
        checkpoint()
        T_tgt = float(getattr(self.inputs, "gg_temp_target", 850.0))
        MR_OUTER_MAX = 6
        TEMP_TOL = 3.0
        RELAX = 0.7

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
        try:
            for outer in range(MR_OUTER_MAX):
                checkpoint()
                results = self._evaluate_design_core(_outer_progress(outer))
                s = results.summary
                T_now = float(s.get("gg_T_combustion_K", 0.0))
                if abs(T_now - T_tgt) < TEMP_TOL:
                    break
                if s.get("solver_stalled", 0.0) > 0.5:
                    break
                mr_new = self._mr_for_target_gg_temp(
                    T_tgt,
                    T_fuel_in=results.states["fuel_after_pump"].temperature,
                    T_ox_in=results.states["lox_after_pump"].temperature,
                    p_gg=float(s.get("gg_pressure_Pa", 0.6 * self.inputs.pc)),
                )
                mr_cur = self._gg_mr
                self._gg_mr_override = (1.0 - RELAX) * mr_cur + RELAX * mr_new
            if results is not None:
                results.summary["gg_temp_target_K"] = T_tgt
                results.summary["gg_temp_outer_error_K"] = (
                    float(results.summary.get("gg_T_combustion_K", 0.0)) - T_tgt)
        finally:
            self._gg_mr_override = None
        return results

    def _evaluate_design_core(
        self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ):
        """Continuation（0.6→1.0）+ 最后阶段 Tc Picard 外环（与 FFSC 同构）。"""
        checkpoint()
        self._warnings = []
        base_pc = self.inputs.pc
        base_thrust = self.inputs.thrust
        original_tc_guess = self.inputs.chamber_temp_guess
        original_gamma = self.inputs.chamber_gamma
        original_mw = self.inputs.molar_mass

        ratios = [0.6, 1.0]
        x_warm: Optional[list[float]] = None
        results = None
        x_solved: list[float] = []

        TC_OUTER_MAX = 5
        TC_OUTER_TOL = 5.0
        TC_RELAX = 0.5
        total_newton_iters = 0
        n_inner_solves = 0

        n_stages_total = len(ratios)

        def _wrap_progress(stage_idx: int, picard_idx: int):
            if progress_callback is None:
                return None
            stage_lo = stage_idx * 100.0 / n_stages_total
            stage_hi = (stage_idx + 1) * 100.0 / n_stages_total
            if stage_idx == n_stages_total - 1:
                w = [0.60, 0.25, 0.10, 0.03, 0.02]
                idx = min(picard_idx, len(w) - 1)
                lo = stage_lo + sum(w[:idx]) * (stage_hi - stage_lo)
                hi = stage_lo + sum(w[:idx + 1]) * (stage_hi - stage_lo)
            else:
                lo, hi = stage_lo, stage_hi

            def cb(it: int, max_it: int, norm: float):
                if norm <= 1.0e-4:
                    frac = 1.0
                elif norm >= 1.0:
                    frac = 0.0
                else:
                    frac = max(0.0, min(1.0, -math.log10(max(norm, 1e-12)) / 4.0))
                try:
                    progress_callback(int(lo + (hi - lo) * frac), 100, norm)
                except Exception:
                    pass
            return cb

        try:
            stalled = False
            for stage, ratio in enumerate(ratios):
                checkpoint()
                if stalled:
                    break
                self.inputs.pc = base_pc * ratio
                self.inputs.thrust = base_thrust * ratio
                is_last = (stage == len(ratios) - 1)

                if not is_last:
                    x_solved, results = self.solve_cycle(
                        progress_callback=_wrap_progress(stage, 0), x_init=x_warm)
                    total_newton_iters += int(results.summary.get("solver_iterations", 0))
                    n_inner_solves += 1
                    if results.summary.get("solver_stalled", 0.0) > 0.5:
                        stalled = True
                        break
                else:
                    for tc_iter in range(TC_OUTER_MAX):
                        checkpoint()
                        x_solved, results = self.solve_cycle(
                            progress_callback=_wrap_progress(stage, tc_iter), x_init=x_warm)
                        total_newton_iters += int(results.summary.get("solver_iterations", 0))
                        n_inner_solves += 1
                        if results.summary.get("solver_stalled", 0.0) > 0.5:
                            stalled = True
                            break
                        new_tc = results.summary.get(
                            "chamber_temperature_K", self.inputs.chamber_temp_guess)
                        tc_res = abs(new_tc - self.inputs.chamber_temp_guess)
                        results.summary["tc_outer_iterations"] = float(tc_iter + 1)
                        results.summary["tc_outer_residual_K"] = tc_res
                        results.summary["chamber_temp_guess_used_K"] = self.inputs.chamber_temp_guess
                        new_gamma = results.summary.get("chamber_gamma")
                        new_mw = results.summary.get("chamber_molar_mass")
                        if new_gamma and new_gamma > 1.0:
                            self.inputs.chamber_gamma = float(new_gamma)
                        if new_mw and new_mw > 0:
                            self.inputs.molar_mass = float(new_mw)
                        if tc_res < TC_OUTER_TOL:
                            break
                        self.inputs.chamber_temp_guess = (
                            (1.0 - TC_RELAX) * self.inputs.chamber_temp_guess
                            + TC_RELAX * new_tc)
                        x_warm = x_solved

                next_ratio = ratios[stage + 1] / ratio if stage + 1 < len(ratios) else 1.0
                x_warm = [x_solved[0] * next_ratio] + list(x_solved[1:])

            if results is not None:
                results.summary["solver_iterations_last"] = results.summary.get("solver_iterations", 0)
                results.summary["solver_iterations"] = float(total_newton_iters)
                results.summary["solver_inner_solves"] = float(n_inner_solves)
        finally:
            self.inputs.pc = base_pc
            self.inputs.thrust = base_thrust
            self.inputs.chamber_temp_guess = original_tc_guess
            self.inputs.chamber_gamma = original_gamma
            self.inputs.molar_mass = original_mw

        s = results.summary
        s["mdot_solved_kg_s"] = x_solved[0] if x_solved else float("nan")
        s["gg_fraction_solved"] = x_solved[1] if len(x_solved) > 1 else float("nan")
        if self._dual_shaft() and len(x_solved) > 2:
            s["gg_split_to_ox_shaft_solved"] = x_solved[2]
        if bool(s.get("solver_warning", 0.0)):
            msg = ("⚠ 求解器未完全收敛：当前结果只适合作为诊断，不应作为设计点。"
                   f" 残差范数={s.get('solver_residual_norm', 0.0):.3e}")
            if msg not in results.warnings:
                results.warnings.insert(0, msg)
        return results

    # =====================================================
    # 6. 节流特性（固定硬件：喉部 At 与冷却通道深度冻结在设计点）
    # =====================================================

    def throttle_sweep(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        record_callback: Optional[Callable[[dict], None]] = None,
    ) -> list[dict]:
        checkpoint()
        steps = max(2, int(getattr(self.inputs, "throttle_steps", 5)))
        throttle_min = self._clamp(float(getattr(self.inputs, "throttle_min", 0.4)), 0.05, 0.99)
        sweep: list[dict] = []

        At_design = None
        frozen_h_m = None
        try:
            _design = GGCycleModel(copy.deepcopy(self.inputs)).evaluate()
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
            pi = copy.deepcopy(self.inputs)
            pi.thrust = thrust_target
            pi.pc = pc_val
            pi.cooling_marching_enable = 0.0
            if frozen_h_m is not None and frozen_h_m > 1e-4:
                pi.cooling_channel_auto_size = 0.0
                pi.cooling_channel_height_m = frozen_h_m
            return pi, GGCycleModel(pi).evaluate()

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
                pc = self.inputs.pc * ratio
                point_inputs, point_results = _solve_at(thrust_target, pc)
                if At_design and At_design > 0.0:
                    for _ in range(8):
                        checkpoint()
                        At_cur = float(
                            point_results.components["nozzle"].outputs.get("At_m2", 0.0))
                        if At_cur <= 0.0 or abs(At_cur / At_design - 1.0) < 5e-4:
                            break
                        pc = pc * (At_cur / At_design)
                        point_inputs, point_results = _solve_at(thrust_target, pc)
                if bool(getattr(self.inputs, "cooling_marching_enable", 1.0)):
                    try:
                        from cooling_marching import run_marching_postpass
                        run_marching_postpass(point_results, point_inputs)
                    except Exception:
                        pass
                residual = float(point_results.summary.get("solver_residual_norm", 1.0))
                converged = (residual < 1e-3
                             and not bool(point_results.summary.get("solver_warning", 0.0)))
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

    # =====================================================
    # 7. 高度特性（主喷管 + GG 排气喷管合成）
    # =====================================================

    def altitude_sweep(
        self,
        z_max_m: float = 80000.0,
        n_points: int = 60,
        design_results=None,
    ) -> dict:
        checkpoint()
        from atmosphere import (
            pressure_at_altitude, altitude_for_pressure, altitude_grid,
        )
        SEP = 0.40
        P_SL = 101325.0

        res = design_results if design_results is not None else self.evaluate()
        nz = res.components["nozzle"].outputs
        gz = (res.components.get("gg_exhaust_nozzle").outputs
              if "gg_exhaust_nozzle" in res.components else {})

        F_vac_main = float(nz.get("thrust_vac_N", 0.0))
        Ae_main = float(nz.get("Ae_m2", 0.0))
        pe = float(nz.get("p_exit_Pa", 0.0))
        eps = float(nz.get("expansion_ratio", 0.0))
        F_vac_gg = float(gz.get("thrust_vac_N", 0.0) or 0.0)
        Ae_gg = float(gz.get("Ae_m2", 0.0) or 0.0)

        F_vac = F_vac_main + F_vac_gg
        Ae = Ae_main + Ae_gg
        mdot = float(res.summary.get("mdot_total_kg_s", 0.0))
        isp_vac = F_vac / max(mdot * G0, 1e-9)
        p_amb_d = float(nz.get("p_amb_Pa", P_SL))

        z_match = altitude_for_pressure(pe) if pe > 0.0 else 0.0
        sep_at_sl = bool(pe < SEP * P_SL)
        z_sep = altitude_for_pressure(pe / SEP) if sep_at_sl else 0.0

        points = []
        for z in altitude_grid(z_max_m, n_points):
            checkpoint()
            pa = pressure_at_altitude(z)
            F = F_vac - pa * Ae
            isp = F / max(mdot * G0, 1e-9)
            if pe > 1.01 * pa:
                state = "欠膨胀"
            elif pe < 0.99 * pa:
                state = "过膨胀"
            else:
                state = "完全膨胀"
            points.append({
                "altitude_m": z,
                "altitude_km": z / 1000.0,
                "p_amb_Pa": pa,
                "thrust_N": F,
                "thrust_kN": F / 1e3,
                "Isp_s": isp,
                "pe_over_pa": (pe / pa) if pa > 1e-9 else float("inf"),
                "expansion_state": state,
                "separated": bool(pe < SEP * pa),
            })

        F_sl = F_vac - P_SL * Ae
        info = {
            "F_vac_N": F_vac, "F_sl_N": F_sl, "Ae_m2": Ae, "pe_Pa": pe,
            "eps": eps, "mdot_kg_s": mdot,
            "Isp_vac_s": isp_vac, "Isp_sl_s": F_sl / max(mdot * G0, 1e-9),
            "p_amb_design_Pa": p_amb_d,
            "match_altitude_m": z_match,
            "separation_altitude_m": z_sep,
            "separated_at_sea_level": sep_at_sl,
            "z_max_m": z_max_m,
        }
        return {"points": points, "info": info}


# =========================================================
# 循环模型工厂（GUI / 脚本统一入口）
# =========================================================

def make_cycle_model(inputs: EngineInputs):
    """按 inputs.cycle_type 返回对应循环模型（'ffsc' → FFSCCycleModel，'gg' → GGCycleModel）。"""
    if str(getattr(inputs, "cycle_type", "ffsc")) == "gg":
        return GGCycleModel(inputs)
    return FFSCCycleModel(inputs)
