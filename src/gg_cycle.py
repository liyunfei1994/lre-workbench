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
    ValidationError,
)
from cooling import CoolingChannel
from regen_coupling import close_regen_cycle, cooling_arguments, fixed_contour_inputs
from chamber_sizing import size_combustion_chamber
from pump import Pump, fluid_density
from nozzle import Nozzle
from preburner import Preburner
from propellants import get_propellant
from turbine import Turbine
from injector import Injector
from thermo import (CANTERA_AVAILABLE, gg_chamber_equilibrium,
                    reactant_enthalpy, enthalpy_reference_id, available_npsh_pressure)
from fluid_properties import state_tp, state_ph, state_ps
from cycle import FFSCCycleModel, _resolve_pump_speed
from nonlinear_solver import solve_nonlinear

G0 = 9.80665
STOICH_OF_CH4_O2 = 4.0  # CH4 + 2O2, 质量 O/F = 64/16 = 4


from thermo_data import model_thermo_scope


class GGCycleModel:
    """燃气发生器开式循环系统级模型（接口与 FFSCCycleModel 对齐）。"""

    def __init__(self, inputs: EngineInputs):
        self.inputs = inputs
        self.propellant = get_propellant(inputs.propellant_pair)
        if inputs.propellant_pair == "lox_kerosene" and inputs.cooling_bartz_mode >= 0.5:
            raise ValidationError("煤油代理系统计算须使用给定冷却吸热量和压降，不能调用甲烷详细冷却关联式。")
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

    @staticmethod
    def _finite_or_none(value):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError, OverflowError):
            return None

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
        fuel_label = "煤油" if self.inputs.propellant_pair == "lox_kerosene" else "甲烷"
        nodes = [
            TopologyNode("lox_tank", "液氧贮箱", (60, 110)),
            TopologyNode("fuel_tank", f"{fuel_label}贮箱", (60, 260)),
            TopologyNode("ox_pump", "液氧泵", (220, 110)),
            TopologyNode("fuel_pump", f"{fuel_label}泵", (220, 260)),
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
            TopologyEdge("fuel_pump", "fuel_cooling", f"主路 {fuel_label}"),
            TopologyEdge("fuel_cooling", "main_chamber"),
            TopologyEdge("ox_pump", "gas_generator", "少量 LOX"),
            TopologyEdge("fuel_pump", "gas_generator", f"少量 {fuel_label}"),
            TopologyEdge("gas_generator", "gg_turbine"),
            TopologyEdge("gg_turbine", "gg_exhaust", "开式排气"),
            TopologyEdge("main_chamber", "nozzle"),
        ]
        return nodes, edges

    # =====================================================
    # 3. 单次前向评估
    # =====================================================

    @model_thermo_scope
    def _evaluate_once(self, *x: float) -> CycleResults:
        return close_regen_cycle(self,self._evaluate_once_core,*x)

    def _evaluate_once_core(self, *x: float) -> CycleResults:
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
        phi_gg = self.propellant.stoich_of / max(mr_gg, 1e-9)

        states: dict[str, FlowState] = {}
        components: dict[str, object] = {}

        # ── 增压输送系统（与 FFSC 同一套三节点链）──────────────
        p_tank = self.inputs.tank_pressure_pa
        p_boost_out = self.inputs.inlet_pressure
        feed_dp = float(getattr(self.inputs, "feed_line_dp_pa", 0.5e5))
        p_pump_inlet = p_boost_out - feed_dp
        if p_pump_inlet <= 0 or p_boost_out < p_tank:
            raise ValueError("增压输送链要求出口压力不低于贮箱，且扣除管损后的主泵入口压力为正；不截压。")
        eta_boost = float(getattr(self.inputs, "boost_pump_efficiency", 0.70))
        dp_boost = p_boost_out - p_tank
        boost_powers = {}
        for prefix, species, temperature, mdot in (
            ("lox", "O2", self.inputs.ox_inlet_temp, mdot_ox),
            ("fuel", self.propellant.fuel_species, self.inputs.fuel_inlet_temp, mdot_fuel)):
            try:
                tank = state_tp(species, temperature, p_tank, require_liquid=True)
            except ValueError as exc:
                label = "燃料贮箱" if prefix == "fuel" else "液氧贮箱"
                raise ValueError(f"{label} → 增压泵入口状态无效：{exc}") from exc
            isentropic = state_ps(species, p_boost_out, tank["s_JkgK"])
            boosted = state_ph(species, p_boost_out,
                               tank["h_Jkg"] + (isentropic["h_Jkg"] - tank["h_Jkg"]) / eta_boost,
                               require_liquid=True)
            inlet = state_ph(species, p_pump_inlet, boosted["h_Jkg"], require_liquid=True)
            boost_powers[prefix] = mdot * (boosted["h_Jkg"] - tank["h_Jkg"])
            for suffix, pure in (("tank", tank), ("boost_out", boosted), ("pump_inlet", inlet)):
                key = prefix + "_" + suffix
                states[key] = FlowState(
                    name=key, pressure=pure["p_Pa"], temperature=pure["T_K"], mass_flow=mdot,
                    density=pure["rho_kgm3"], composition={species: 1.0},
                    enthalpy=reactant_enthalpy(species, pure["T_K"], pure["p_Pa"], self.propellant.fuel_species),
                    metadata={"reference_id": enthalpy_reference_id(self.propellant.fuel_species),
                              "fluid_phase": pure["phase"], "fluid_range_status": pure["range_status"]})
        rho_lox_tank = states["lox_tank"].density
        rho_fuel_tank = states["fuel_tank"].density
        p_boost_lox, p_boost_fuel = boost_powers["lox"], boost_powers["fuel"]

        def npsh_pressure(species, flow):
            return available_npsh_pressure(species, flow.temperature, flow.pressure)

        tank_npsh_ox = npsh_pressure("O2", states["lox_tank"])
        tank_npsh_fuel = npsh_pressure(self.propellant.fuel_species, states["fuel_tank"])
        npshA_boost_lox = tank_npsh_ox / (rho_lox_tank * G0) if tank_npsh_ox is not None else None
        npshA_boost_fuel = tank_npsh_fuel / (rho_fuel_tank * G0) if tank_npsh_fuel is not None else None
        n_boost = 6000.0
        Q_lox_tank = mdot_ox / max(rho_lox_tank, 1.0)
        Q_fuel_tank = mdot_fuel / max(rho_fuel_tank, 1.0)
        nss_boost_lox_eu = (n_boost * math.sqrt(Q_lox_tank) / npshA_boost_lox ** 0.75
                            if npshA_boost_lox is not None and npshA_boost_lox > 0 else None)
        nss_boost_fuel_eu = (n_boost * math.sqrt(Q_fuel_tank) / npshA_boost_fuel ** 0.75
                             if npshA_boost_fuel is not None and npshA_boost_fuel > 0 else None)

        # ── 压力网络（GG 版：主室支路决定泵出口）──────────────
        # 主燃烧室液路喷注压降（复活的 injector_dp_ratio_*，V20）
        dp_ratio_inj_ox = float(getattr(self.inputs, "injector_dp_ratio_ox", 0.20))
        dp_ratio_inj_fuel = float(getattr(self.inputs, "injector_dp_ratio_fuel", 0.20))
        dp_inj_ox = dp_ratio_inj_ox * self.inputs.pc
        dp_inj_fuel = dp_ratio_inj_fuel * self.inputs.pc
        dp_valve = self.inputs.valve_line_dp_ratio * self.inputs.pc

        # 再生冷却压降（固定 / 水力实算，与 FFSC 同机制；冷却剂 = 主路 CH4）
        _cool_auto_size = bool(getattr(self.inputs, "cooling_channel_auto_size", 1.0))
        _cool_dp_auto = bool(self.inputs.cooling_bartz_mode)
        hydraulic_trial=getattr(self,"_hydraulic_trial",None) or {}
        dp_cooling = hydraulic_trial.get("cooling_dp",self.inputs.cooling_dp_pa) if _cool_dp_auto else self.inputs.cooling_dp_pa

        p_lox_pump_out = self.inputs.pc + dp_inj_ox + dp_valve
        p_fuel_pump_out = self.inputs.pc + dp_inj_fuel + dp_cooling + dp_valve

        # GG 供应压力（派生）：取两条供应支路中较低者
        gg_inj_ratio = float(getattr(self.inputs, "gg_injector_dp_ratio", 0.15))
        p_gg_supply = min(p_lox_pump_out, p_fuel_pump_out) - dp_valve
        p_gg = max(p_gg_supply / (1.0 + gg_inj_ratio), 2.0 * 0.10e6)
        p_te = float(getattr(self.inputs, "gg_turbine_backpressure_pa", 0.40e6))
        pi_t_gg = p_gg / max(p_te, 1.0)

        # ── 涡轮泵转速 ────────────────────────────────────
        rho_lox_inlet = states["lox_pump_inlet"].density
        rho_fuel_inlet = states["fuel_pump_inlet"].density
        dp_lox = p_lox_pump_out - p_pump_inlet
        dp_fuel = p_fuel_pump_out - p_pump_inlet
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
        npshA_lox = npsh_pressure("O2", states["lox_pump_inlet"])
        npshA_fuel = npsh_pressure(self.propellant.fuel_species, states["fuel_pump_inlet"])

        lox_pump = Pump("ox_pump", "LOX", mdot_ox,
                        p_pump_inlet, p_lox_pump_out,
                        states["lox_pump_inlet"].temperature, self.inputs.pump_efficiency_ox,
                        nr_rpm=n_lox_rpm,
                        n_stages=self.inputs.lox_pump_n_stages,
                        tip_speed_limit_ms=self.inputs.pump_tip_speed_limit_ms,
                        npsh_available_pa=npshA_lox,
                        impeller_material=getattr(self.inputs, "lox_pump_impeller_material", None),
                        side="ox")
        fuel_pump = Pump("fuel_pump", self.propellant.fuel_species, mdot_fuel,
                         p_pump_inlet, p_fuel_pump_out,
                         states["fuel_pump_inlet"].temperature, self.inputs.pump_efficiency_fuel,
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
        _im_k = None
        try:
            from materials import MATERIALS as _MATS_K
            _im_k = _MATS_K.get(getattr(self.inputs, "chamber_inner_wall_material", "grcop84"))
            _liner_k = (float(_im_k.thermal_conductivity_at(600.0)) if _im_k
                        else float(getattr(self.inputs, "cooling_wall_conductivity", 320.0)))
        except Exception:
            _liner_k = float(getattr(self.inputs, "cooling_wall_conductivity", 320.0))
        cooling = CoolingChannel(
            fuel_species=self.propellant.fuel_species,
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
            **cooling_arguments(self, _im_k),
        )
        cooled_fuel_state, cooling_result = cooling.evaluate()
        states["fuel_after_cooling"] = cooled_fuel_state
        components[cooling_result.name] = cooling_result

        # ── 燃气发生器（富燃 HP 平衡；供料 = 两泵出口液体）─────────
        gas_generator = Preburner(
            fuel_species=self.propellant.fuel_species,
            name="gas_generator", pressure=p_gg,
            mdot_fuel=mdot_gg_fuel, mdot_ox=mdot_gg_ox,
            phi=phi_gg,
            fuel_temp=fuel_pump_out.temperature, ox_temp=lox_pump_out.temperature,
            injector_dp_ratio=gg_inj_ratio,
            fuel_enthalpy=fuel_pump_out.enthalpy, ox_enthalpy=lox_pump_out.enthalpy,
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

        # 主室按实际入口端口焓求平衡。物理冷却模式在空间热侧扣热；
        # Q/压降预算模式在主室控制体扣除同一份吸热量。
        chamber_energy = {}
        chamber_gas = gg_chamber_equilibrium(
            fuel_species=self.propellant.fuel_species,
            mdot_fuel=mdot_main_fuel, mdot_ox=mdot_main_ox,
            T_fuel=cooled_fuel_state.temperature, T_ox=lox_pump_out.temperature,
            pressure=self.inputs.pc,
            efficiency=(self.inputs.combustion_efficiency
                if self.inputs.combustion_loss_model == "legacy_temperature_rise" else 1.0),
            fuel_enthalpy=cooled_fuel_state.enthalpy,
            ox_enthalpy=lox_pump_out.enthalpy,
            heat_loss_W=0. if bartz_mode else cooling_result.outputs["heat_load_W"],
            energy_diagnostics=chamber_energy,
        )
        t_chamber = chamber_gas.temperature
        gamma_calc = chamber_gas.gamma
        mw_calc = chamber_gas.molar_mass
        _t_mix_ref = ((mdot_main_fuel * cooled_fuel_state.temperature
                       + mdot_main_ox * lox_pump_out.temperature)
                      / max(mdot_main, 1e-9))
        eta_c = (self.inputs.combustion_efficiency
                 if self.inputs.combustion_loss_model == "legacy_temperature_rise" else 1.0)
        t_chamber_ideal = chamber_energy["T_chamber_ideal_K"]

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
        if self.inputs.propellant_pair == "lox_kerosene":
            chamber_state.metadata.update({
                "thermo_extrapolated_mass_fraction": sum(
                    getattr(chamber_gas, "thermo_extrapolation", {}).values()),
                "thermo_common_max_temperature_K": 3000.0,
                "thermo_extrapolated_species_mass_fractions": dict(
                    getattr(chamber_gas, "thermo_extrapolation", {})),
            })
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
                **chamber_energy,
            },
            notes=[
                f"热力学来源: {chamber_gas.source}（LOX/{self.propellant.fuel_species} 喷注，CoolProp 焓修正）",
                f"T_chamber = {t_chamber:.0f} K, γ = {gamma_calc:.4f}, MW = {mw_calc:.2f}",
                f"主燃烧室混合比 O/F = {mr_main:.3f}"
                f"（整机 O/F = {self.inputs.of_ratio:.3f}；GG 抽走富燃小流量所致差异）",
                (f"历史温升折减系数 = {eta_c:.3f} → 平衡 {t_chamber_ideal:.0f} K 折减 "
                 f"{t_chamber_ideal - t_chamber:.0f} K" if eta_c < 1.0
                 else "按焓求化学平衡（含解离），未附加经验温升折减；不声明燃尽率或 c* 效率。"),
            ],
        )
        if self.inputs.propellant_pair == "lox_kerosene":
            extrapolated = getattr(chamber_gas, "thermo_extrapolation", {})
            chamber_result.outputs.update({
                "thermo_extrapolated_mass_fraction": sum(extrapolated.values()),
                "thermo_common_max_temperature_K": 3000.0,
            })
            if extrapolated:
                chamber_result.notes.append(
                    f"主燃烧室温度 {t_chamber:.3f} K；当前机理共同热物性上限 3000 K。外推物种质量分数："
                    + "，".join(f"{name}={fraction:.6g}" for name, fraction in extrapolated.items()))
        states["main_chamber"] = chamber_state
        components[chamber_result.name] = chamber_result

        # ── 主喷管 ─────────────────────────────────────────
        nozzle = Nozzle(
            pc=self.inputs.pc, expansion_ratio=self.inputs.expansion_ratio,
            gamma=gamma_calc, molar_mass=mw_calc,
            tc=t_chamber, mdot=mdot_main,
            p_amb=self.inputs.ambient_pressure, chamber_state=chamber_gas,
        )
        if bartz_mode:
            from heated_nozzle import heated_nozzle
            nozzle_result=heated_nozzle(chamber_gas,mdot_main,self.inputs.pc,
                self.inputs.expansion_ratio,self.inputs.ambient_pressure,cooling_result.profile)
        else:
            nozzle_result = nozzle.evaluate()
        components[nozzle_result.name] = nozzle_result
        f_main_sl = nozzle_result.outputs["thrust_N"]
        f_main_vac = nozzle_result.outputs.get("thrust_vac_N", f_main_sl)

        # ── 燃烧室几何 + 分层壁厚（与 FFSC 同口径）──────────────────
        _cc = size_combustion_chamber(
            At=cooling_result.outputs.get("A_throat_m2", nozzle_result.outputs["At_m2"]),
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
                coolant_pressure_pa=p_fuel_pump_out,
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
        except Exception as exc:
            chamber_result.notes.append(f"分层几何信息未生成：{exc}")
        chamber_result.outputs.update(_cc)
        chamber_result.notes.extend(_cc_notes)
        chamber_result.notes.extend(_cc_validation)

        # ── 主喷注器（液-液：真实液体密度）────────────────────────
        chamber_radius = _cc["cc_diameter_m"] / 2.0
        rho_ox_inj = (lox_pump_out.density
                      or fluid_density("LOX", lox_pump_out.temperature, lox_pump_out.pressure))
        rho_fuel_inj = (cooled_fuel_state.density
                        or fluid_density(self.propellant.fuel_species, cooled_fuel_state.temperature,
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
        if self.inputs.propellant_pair == "lox_kerosene":
            cycle_warnings.extend(self.propellant.limitations)
        gg_temp_limit = float(getattr(self.inputs, "gg_temp_limit", 950.0))
        if t_comb_gg > gg_temp_limit:
            cycle_warnings.append(
                f"⚠ 燃气发生器出口温度 {t_comb_gg:.0f} K 超过涡轮材料限值 "
                f"{gg_temp_limit:.0f} K（O/F={mr_gg:.3f}）。"
            )
        if self.inputs.propellant_pair == "lox_methane" and not (0.20 <= mr_gg <= 0.45):
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
                f"ℹ 单轴布局：LOX/{self.propellant.fuel_species} 泵与涡轮同轴同速（轴速按 LOX 泵确定），"
                f"{self.propellant.fuel_species} 泵比转速为派生诊断值。")
        cycle_warnings.append(
            "ℹ GG 模式几何页映射：预燃室栏位显示燃气发生器（富燃）几何；"
            "涡轮栏位为 GG 涡轮。")

        topology_nodes, topology_edges = self._build_topology()

        mode_text = ("燃气发生器开式循环 · GG 温度目标模式"
                     if self._temp_target_mode() else
                     "燃气发生器开式循环 · 固定 GG 混合比模式")
        summary = {
            "propellant_pair": self.inputs.propellant_pair,
            "fuel_species": self.propellant.fuel_species,
            "thermochemistry_mechanism": chamber_gas.mechanism,
            "thermochemistry_phase": self.propellant.phase,
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
            "p_pump_out_Pa": max(p_lox_pump_out, p_fuel_pump_out),
            "lox_pump_out_pressure_Pa": p_lox_pump_out,
            "fuel_pump_out_pressure_Pa": p_fuel_pump_out,
            "lox_pump_out_pressure_MPa": p_lox_pump_out / 1e6,
            "fuel_pump_out_pressure_MPa": p_fuel_pump_out / 1e6,
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
            "boost_npsh_ox_applicable": float(npshA_boost_lox is not None),
            "boost_npsh_fuel_applicable": float(npshA_boost_fuel is not None),
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

        for state in states.values():
            if state.enthalpy is not None:
                state.metadata["reference_id"] = enthalpy_reference_id(self.propellant.fuel_species)
                state.metadata["thermochemical_data"] = chamber_gas.mechanism

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

    @model_thermo_scope
    def solve_cycle(
        self,
        tol_rel: float = 1e-4,
        max_iter: int = 50,
        verbose: bool = False,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        x_init: Optional[list[float]] = None,
    ):
        """Solve identical bounded equations with either scaled Newton or TRF."""
        checkpoint()
        if (not math.isfinite(tol_rel) or tol_rel <= 0 or isinstance(max_iter, bool)
                or int(max_iter) != max_iter or max_iter < 0):
            raise ValueError("求解容差必须为有限正数，最大步数必须为非负整数。")
        raw_x = list(self._initial_guess_vector() if x_init is None else x_init)
        if len(raw_x) != self._n_vars() or not all(math.isfinite(float(v)) for v in raw_x):
            raise ValueError(f"GG 初值必须含 {self._n_vars()} 个有限数。")
        # The physical residual receives feasible variables; neither backend relies
        # on the forward model's legacy clipping to implement variable bounds.
        mr = self._gg_mr
        alpha_hi = min(.12, .999 * (1 + mr) / (1 + self.inputs.of_ratio),
                       .999 * (1 + mr) * self.inputs.of_ratio / ((1 + self.inputs.of_ratio) * mr))
        if alpha_hi <= .004:
            raise ValueError("当前混合比在 GG 流量界限内没有正的主路流量。")
        lower, upper = [1e-3, .004], [float("inf"), alpha_hi]
        if self._dual_shaft():
            lower.append(.05)
            upper.append(.95)
        x0 = [max(lo, min(hi, float(v))) for v, lo, hi in zip(raw_x, lower, upper)]
        initial = self.residual_vector(*x0)
        initial_used = False
        summary = initial[1].summary
        power_keys = (["ox_pump_power_MW", "fuel_pump_power_MW"]
                      if self._dual_shaft() else ["pump_power_MW"])
        # Fixed characteristic scales for this solve. At tol=1e-4 the floors
        # give absolute acceptance floors of 0.01 N and 10 W, respectively.
        scales = [max(abs(self.inputs.thrust), 100.0)] + [
            max(abs(self._finite_or_none(summary.get(key)) or 0.0) * 1e6, 1e5)
            for key in power_keys]
        variable_scales = [max(abs(x0[0]), 1.0), .03] + ([.5] if self._dual_shaft() else [])
        names = (["thrust", "shaft_power_ox", "shaft_power_fuel"]
                 if self._dual_shaft() else ["thrust", "shaft_power"])

        def fun(x):
            nonlocal initial_used
            # The scale-setting evaluation is also the solver's initial call.
            if not initial_used and list(x) == x0:
                initial_used = True
                return initial
            return self.residual_vector(*x)

        def progress(point):
            norm = point.get("residual_norm")
            if progress_callback is not None:
                try:
                    progress_callback(point["iteration"], max_iter, norm if norm is not None else float("inf"))
                except Exception:
                    pass
            if verbose:
                print(f"[GG {getattr(self.inputs, 'nonlinear_solver', 'newton')} "
                      f"{point['iteration']:02d}] norm={norm}")

        solved = solve_nonlinear(
            fun, x0, lower, upper, residual_scales=scales,
            variable_scales=variable_scales,
            diff_steps=[max(abs(x0[0]), 1.0) * 1e-5, 1e-6] + ([1e-5] if self._dual_shaft() else []),
            tol=tol_rel, max_iter=max_iter,
            backend=getattr(self.inputs, "nonlinear_solver", "newton"),
            names=names, progress_callback=progress,
        )
        results = solved.payload if solved.payload is not None else initial[1]
        if not solved.points:
            # Retain a gap for a physically evaluated but unusable initial
            # residual; this is diagnostic evidence, not an accepted iterate.
            solved.points.append({"iteration": 0, "residual_norm": None,
                                  "residuals": {name: None for name in names},
                                  "x": x0, "nfev": solved.nfev, "accepted": False})
        norm = max((abs(r) / scale for r, scale in zip(solved.residuals, scales)), default=float("inf"))
        if not all(math.isfinite(r) for r in solved.residuals):
            norm = float("inf")
        results.summary.update({
            "solver_iterations": float(solved.iterations), "solver_residual_norm": norm,
            "solver_warning": float(not solved.passed), "inner_solver_warning": float(not solved.passed),
            "solver_stalled": float(solved.reason in ("line_search_failed", "step_stagnation",
                                                       "residual_stagnation", "stationary_nonzero_residual")),
            "solver_backend": solved.backend, "solver_function_evaluations": float(solved.nfev),
            "solver_jacobian_evaluations": float(solved.njev), "solver_rejected_steps": float(solved.rejected_steps),
        })
        results.convergence = {
            "schema_version": 1, "backend": solved.backend,
            "layers": {
                "inner": {"enabled": True, "status": "pass" if solved.passed else "fail",
                          "residual": norm if math.isfinite(norm) else None,
                          "tolerance": tol_rel, "unit": "1", "iterations": solved.iterations,
                          "termination_reason": solved.reason,
                          "checks": [{"name": name, "residual": abs(value) if math.isfinite(value) else None,
                                      "tolerance": tol_rel * scale, "unit": "N" if index == 0 else "W"}
                                     for index, (name, value, scale) in enumerate(zip(names, solved.residuals, scales))]},
                **{key: {"enabled": False, "status": "disabled", "residual": None,
                         "tolerance": None, "unit": "1", "iterations": 0,
                         "termination_reason": "not_requested"}
                   for key in ("thermal", "preburner", "continuation")},
            },
            "inner_runs": [{"id": 1, "stage_ratio": None, "thermal_iteration": None,
                            "preburner_iteration": None, "tolerance": tol_rel,
                            "termination_reason": solved.reason, "points": solved.points,
                            "backend": solved.backend, "nfev": solved.nfev, "njev": solved.njev,
                            "evaluation_budget": solved.evaluation_budget,
                            "rejected_steps": solved.rejected_steps,
                            "residual_scales": list(scales), "residual_names": names,
                            "physical_tolerances": [tol_rel * scale for scale in scales],
                            "backend_status": solved.backend_status,
                            "variable_scales": variable_scales, "error": solved.error,
                            "warm_start": x_init is not None}],
            "thermal_history": [], "preburner_history": [], "continuation_history": [],
        }
        return solved.x, results

    # =====================================================
    # 5. 设计点求解入口（continuation + Tc Picard + GG 温度目标外环）
    # =====================================================

    def evaluate_forward(self):
        checkpoint()
        results = self._evaluate_once(*self._initial_guess_vector())
        self._attach_cooling_marching(results)
        return results

    @model_thermo_scope
    def evaluate(self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
                 *, include_cooling_marching: bool = True):
        """Solve the cycle; optionally omit the legacy saved-profile view only."""
        checkpoint()
        # Fixed boundary conditions cannot be repaired by numerical retries.
        # Reject invalid tank/feed phases before entering continuation, while
        # retaining bounded recovery for failures of changing trial states.
        feed_dp = float(getattr(self.inputs, "feed_line_dp_pa", .5e5))
        for label, species, temperature in (
            ("燃料贮箱", self.propellant.fuel_species, self.inputs.fuel_inlet_temp),
            ("液氧贮箱", "O2", self.inputs.ox_inlet_temp)):
            try:
                state_tp(species, temperature, self.inputs.tank_pressure_pa, require_liquid=True)
            except ValueError as exc:
                raise ValueError(f"{label} → 增压泵入口状态无效：{exc}") from exc
        if self.inputs.inlet_pressure - feed_dp <= 0 or self.inputs.inlet_pressure < self.inputs.tank_pressure_pa:
            raise ValueError("增压输送链的指定压力无效；不截压或用贮箱温度代替后续节点温度。")
        if self._temp_target_mode():
            results = self._evaluate_temp_targeted(progress_callback)
        else:
            results = self._evaluate_design_core(progress_callback)
        from convergence import attach_convergence_status
        attach_convergence_status(results)
        from thermo_applicability import attach_thermodynamic_applicability
        attach_thermodynamic_applicability(results)
        if include_cooling_marching:
            self._attach_cooling_marching(results)
        return results

    def _attach_cooling_marching(self, results) -> None:
        checkpoint()
        if self.inputs.propellant_pair != "lox_methane":
            return
        if float(getattr(self.inputs, "cooling_marching_enable", 1.0)) < 0.5:
            return
        if not bool(getattr(self.inputs, "cooling_bartz_mode", True)):
            return
        from cooling_marching import run_marching_postpass
        run_marching_postpass(results, self.inputs)

    def _mr_for_target_gg_temp(
        self, T_target: float, T_fuel_in: float, T_ox_in: float, p_gg: float,
        *, fuel_enthalpy=None, ox_enthalpy=None,
    ) -> float:
        """一维反求 GG 混合比使富燃火焰温度 = T_target（对 MR 单调递增）。"""
        checkpoint()
        from thermo import combustion_equilibrium
        if not all(self._finite_or_none(v) is not None and v > 0
                   for v in (T_target, T_fuel_in, T_ox_in, p_gg)):
            raise ValueError("GG 温度反求要求有限正的目标温度、入口温度与压力。")

        def temp_at(mr: float) -> float:
            comb = combustion_equilibrium(
                fuel_species=self.propellant.fuel_species,
                mdot_fuel=1.0, mdot_ox=max(mr, 1e-6),
                T_fuel=T_fuel_in, T_ox=T_ox_in, pressure=p_gg,
                fuel_enthalpy=fuel_enthalpy, ox_enthalpy=ox_enthalpy,
            )
            temperature = self._finite_or_none(comb.temperature)
            if temperature is None or temperature <= 0:
                raise ValueError("GG 温度反求遇到非有限或非正温度。")
            return temperature

        lo, hi = 0.05, 0.90
        try:
            T_lo, T_hi = temp_at(lo), temp_at(hi)
        except (ValueError, RuntimeError, ArithmeticError):
            # A low-MR gas equilibrium may fall outside the HP temperature
            # domain. Exclude it; a clipped 300 K state is not a valid bracket.
            previous = None
            bracket = None
            for index in range(33):
                checkpoint()
                mr = lo + (hi - lo) * index / 32
                try:
                    temperature = temp_at(mr)
                except (ValueError, RuntimeError, ArithmeticError):
                    previous = None
                    continue
                if abs(temperature - T_target) <= .3:
                    return mr
                if previous is not None and (previous[1] - T_target) * (temperature - T_target) < 0:
                    bracket = previous, (mr, temperature)
                    break
                previous = mr, temperature
            if bracket is None:
                raise ValueError("GG 温度反求：物性有效域内没有目标温度括区。")
            (lo, T_lo), (hi, T_hi) = bracket
        if abs(T_lo - T_target) <= 0.3:
            return lo
        if abs(T_hi - T_target) <= 0.3:
            return hi
        if (T_target - T_lo) * (T_target - T_hi) > 0.0:
            msg = (f"⚠ GG 目标温度 {T_target:.0f} K 超出 MR∈[{lo:g},{hi:g}] 可达温度 "
                   f"[{min(T_lo, T_hi):.0f},{max(T_lo, T_hi):.0f}] K，无法构造目标温度解。")
            warnings.warn(msg, stacklevel=2)
            self._warnings.append(msg)
            raise ValueError(msg)
        for _ in range(40):
            checkpoint()
            mid = 0.5 * (lo + hi)
            T_mid = temp_at(mid)
            # Return the evaluated midpoint that met the temperature tolerance.
            # Updating the bracket first and returning its new midpoint changes MR.
            if abs(T_mid - T_target) <= 0.3:
                return mid
            if (T_mid - T_target) * (T_lo - T_target) > 0.0:
                lo, T_lo = mid, T_mid
            else:
                hi, T_hi = mid, T_mid
        raise RuntimeError("GG 温度二分反求达到最大步数，未满足 0.3 K 容差。")

    def _initialize_design_guess(self):
        """Use one legal rated forward state only as a numerical initial guess.

        Reducing chamber pressure is not necessarily an easier continuation
        path for a heated, compressible coolant circuit. This optional seed
        therefore precedes continuation; it never bypasses any final residual.
        """
        if not bool(self.inputs.cooling_bartz_mode):
            return None
        record = {"method": "rated_physical_forward_enthalpy_inversion",
                  "cycle_evaluations": 1, "accepted_as_final": False,
                  "status": "failed", "warm_state": None}
        try:
            x0 = self._initial_guess_vector()
            seed = self._evaluate_once(*x0)
            feedback = [float(seed.summary[key]) for key in
                        ("chamber_temperature_K", "chamber_gamma", "chamber_molar_mass")]
            if not all(math.isfinite(v) and v > 0 for v in feedback) or feedback[1] <= 1:
                raise ValueError("额定前向初值的热反馈状态无效。")
            if self._temp_target_mode():
                fuel, ox = seed.states["fuel_after_pump"], seed.states["lox_after_pump"]
                if any(v is None or not math.isfinite(v) for v in (fuel.enthalpy, ox.enthalpy)):
                    raise ValueError("GG 初值反求缺少实际泵出口焓。")
                mr = self._mr_for_target_gg_temp(
                    float(self.inputs.gg_temp_target), fuel.temperature, ox.temperature,
                    float(seed.summary["gg_pressure_Pa"]),
                    fuel_enthalpy=fuel.enthalpy, ox_enthalpy=ox.enthalpy)
                if not math.isfinite(mr) or not .05 <= mr <= .90:
                    raise ValueError("GG 初值反求混合比无效。")
                self._gg_mr_override = mr
                record["gg_mixture_ratio"] = mr
            record.update(status="seed_ready", warm_state={
                "x": list(x0), "chamber_temp_guess": feedback[0],
                "chamber_gamma": feedback[1], "molar_mass": feedback[2]})
        except (ValueError, RuntimeError, ArithmeticError, KeyError) as exc:
            # A failed optional seed is evidence, not an excuse to change the
            # requested physical boundary conditions or suppress cancellation.
            record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    def _evaluate_temp_targeted(
        self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ):
        checkpoint()
        T_tgt = float(getattr(self.inputs, "gg_temp_target", 850.0))
        MR_OUTER_MAX = 12
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
        inner_runs: list[dict] = []
        thermal_history: list[dict] = []
        preburner_history: list[dict] = []
        continuation_history: list[dict] = []
        initialization_history: list[dict] = []
        warm_state = None
        warm_start_attempts = 0
        warm_start_successes = 0
        warm_start_fallbacks = 0
        work_totals = {"solver_function_evaluations": 0, "solver_initialization_evaluations": 0,
                       "solver_jacobian_evaluations": 0, "solver_rejected_steps": 0}
        total_newton_iters = 0
        total_inner_solves = 0
        reason = "max_iterations"
        all_warnings: list[str] = []
        original_mr_override = self._gg_mr_override
        try:
            for outer in range(MR_OUTER_MAX):
                checkpoint()
                results = self._evaluate_design_core(_outer_progress(outer), warm_state=warm_state)
                s = results.summary
                diagnostics = results.convergence
                for item in diagnostics.get("initialization_history", []):
                    item["preburner_iteration"] = outer + 1
                    initialization_history.append(item)
                for run in diagnostics["inner_runs"]:
                    run["id"] = len(inner_runs) + 1
                    run["preburner_iteration"] = outer + 1
                    inner_runs.append(run)
                for point in diagnostics["thermal_history"]:
                    point["preburner_iteration"] = outer + 1
                    thermal_history.append(point)
                for point in diagnostics["continuation_history"]:
                    point["attempt"] = len(continuation_history) + 1
                    point["preburner_iteration"] = outer + 1
                    continuation_history.append(point)
                warm_state = copy.deepcopy(diagnostics.get("solution"))
                warm_info = diagnostics.get("warm_start", {})
                warm_start_attempts += int(warm_info.get("attempted", False))
                warm_start_successes += int(warm_info.get("used", False))
                warm_start_fallbacks += int(warm_info.get("fallback", False))
                for key in work_totals:
                    work_totals[key] += int(s.get(key, 0))
                total_newton_iters += int(s.get("solver_iterations", 0))
                total_inner_solves += int(s.get("solver_inner_solves", 0))
                T_now = self._finite_or_none(s.get("gg_T_combustion_K"))
                valid = T_now is not None and T_now > 0 and math.isfinite(T_tgt) and T_tgt > 0
                error = T_now - T_tgt if valid else None
                preburner_history.append({"iteration": outer + 1, "gg_error_K": error,
                                          "residual_K": abs(error) if valid else None, "gg_mr": self._gg_mr})
                # A target-temperature match cannot hide a failed inner or thermal solve.
                if any(diagnostics["layers"][key]["enabled"] and diagnostics["layers"][key]["status"] != "pass"
                       for key in ("inner", "continuation", "thermal")):
                    reason = "dependent_layer_failed"
                    break
                if not valid:
                    reason = "nonfinite_temperature"
                    break
                if abs(error) <= TEMP_TOL:
                    reason = "converged"
                    break
                if outer + 1 >= MR_OUTER_MAX:
                    break
                try:
                    mr_new = self._mr_for_target_gg_temp(
                        T_tgt,
                        T_fuel_in=results.states["fuel_after_pump"].temperature,
                        T_ox_in=results.states["lox_after_pump"].temperature,
                        p_gg=float(s.get("gg_pressure_Pa", 0.6 * self.inputs.pc)),
                        fuel_enthalpy=results.states["fuel_after_pump"].enthalpy,
                        ox_enthalpy=results.states["lox_after_pump"].enthalpy,
                    )
                except (ValueError, RuntimeError, ArithmeticError) as exc:
                    reason = "temperature_inversion_failed"
                    results.warnings.append(f"GG 温度反求失败：{exc}")
                    break
                all_warnings.extend(self._warnings)
                mr_cur = self._gg_mr
                self._gg_mr_override = (1.0 - RELAX) * mr_cur + RELAX * mr_new
            if results is not None:
                results.summary["gg_temp_target_K"] = T_tgt
                results.summary["gg_temp_outer_error_K"] = preburner_history[-1]["gg_error_K"]
                results.summary["gg_temp_outer_iterations"] = float(len(preburner_history))
                results.summary["solver_iterations"] = float(total_newton_iters)
                results.summary["solver_inner_solves"] = float(total_inner_solves)
                results.summary["tc_outer_iterations"] = float(len(thermal_history))
                results.summary["tc_outer_iterations_total"] = float(len(thermal_history))
                results.summary.update({key: float(value) for key, value in work_totals.items()})
                results.summary.update(solver_warm_start_attempts=float(warm_start_attempts),
                                       solver_warm_start_successes=float(warm_start_successes),
                                       solver_warm_start_fallbacks=float(warm_start_fallbacks))
                results.convergence["inner_runs"] = inner_runs
                results.convergence["thermal_history"] = thermal_history
                results.convergence["preburner_history"] = preburner_history
                results.convergence["continuation_history"] = continuation_history
                results.convergence["initialization_history"] = initialization_history
                results.convergence["layers"]["continuation"]["iterations"] = len(continuation_history)
                results.convergence["layers"]["thermal"]["iterations"] = len(thermal_history)
                results.convergence["layers"]["preburner"] = {
                    "enabled": True, "status": "pass" if reason == "converged" else "fail",
                    "residual": preburner_history[-1]["residual_K"],
                    "tolerance": TEMP_TOL, "unit": "K", "iterations": len(preburner_history),
                    "termination_reason": reason,
                }
                for warning in dict.fromkeys(all_warnings):
                    if warning not in results.warnings:
                        results.warnings.append(warning)
        finally:
            self._gg_mr_override = original_mr_override
        return results

    def _evaluate_design_core(
        self, progress_callback: Optional[Callable[[int, int, float], None]] = None,
        *, warm_state: Optional[dict] = None,
    ):
        """Adaptive continuation and protected, fully checked thermal feedback.

        A complete warm state is local to one evaluate() call. Failed proposals
        stay in the diagnostics, but cannot advance the accepted continuation
        ratio or become a reported converged solution.
        """
        checkpoint()
        self._warnings = []
        original = (self.inputs.pc, self.inputs.thrust, self.inputs.chamber_temp_guess,
                    self.inputs.chamber_gamma, self.inputs.molar_mass)
        base_pc, base_thrust = original[:2]
        thermal_enabled = bool(self.inputs.cooling_bartz_mode)
        thermal_limit, temperature_tol, property_tol = 20, 5.0, 1e-4
        inner_runs, thermal_history, continuation_history = [], [], []
        results, x_solved = None, []
        reached_ratio, evaluated_ratio = 0.0, 0.0
        thermal_reason, continuation_reason = "target_stage_not_reached", "target_stage_not_reached"
        totals = {"solver_iterations": 0, "solver_function_evaluations": 0,
                  "solver_initialization_evaluations": 0,
                  "solver_jacobian_evaluations": 0, "solver_rejected_steps": 0}
        warm_attempted = bool(warm_state)
        warm_used = False
        warm_fallback = False

        def get_feedback():
            return [self.inputs.chamber_temp_guess, self.inputs.chamber_gamma, self.inputs.molar_mass]

        def set_feedback(values):
            self.inputs.chamber_temp_guess, self.inputs.chamber_gamma, self.inputs.molar_mass = values

        def solve_at(ratio, x, thermal_iteration=None):
            nonlocal results, x_solved, evaluated_ratio
            self.inputs.pc, self.inputs.thrust = base_pc * ratio, base_thrust * ratio
            evaluated_ratio = ratio
            try:
                x_solved, results = self.solve_cycle(progress_callback=progress_callback, x_init=x)
            except Exception as exc:
                # The scale-setting initial physical evaluation can fail before
                # the common backend owns a usable state. Keep this attempt and
                # let continuation retry a smaller step. Cancellation derives
                # from BaseException and deliberately propagates.
                x_solved = list(x) if x is not None else self._initial_guess_vector()
                backend = getattr(self.inputs, "nonlinear_solver", "newton")
                results = CycleResults(inputs=self.inputs, summary={
                    "solver_iterations": 0.0, "solver_residual_norm": float("inf"),
                    "solver_warning": 1.0, "inner_solver_warning": 1.0,
                    "solver_stalled": 0.0, "solver_function_evaluations": 1.0,
                    "solver_jacobian_evaluations": 0.0, "solver_rejected_steps": 0.0,
                    "solver_backend": backend}, states={}, components={}, warnings=[str(exc)])
                results.convergence = {"schema_version": 1, "backend": backend,
                    "layers": {"inner": {"enabled": True, "status": "fail", "residual": None,
                        "tolerance": 1e-4, "unit": "1", "iterations": 0,
                        "termination_reason": "initial_evaluation_failed"},
                        **{key: {"enabled": False, "status": "disabled", "residual": None,
                            "tolerance": None, "unit": "1", "iterations": 0,
                            "termination_reason": "not_requested"}
                           for key in ("thermal", "preburner", "continuation")}},
                    "inner_runs": [{"id": 1, "points": [], "tolerance": 1e-4,
                        "backend": backend, "nfev": 1, "njev": 0, "rejected_steps": 0,
                        "preburner_iteration": None, "termination_reason": "initial_evaluation_failed",
                        "error": f"{type(exc).__name__}: {exc}", "warm_start": x is not None}],
                    "thermal_history": [], "preburner_history": [], "continuation_history": []}
            for key in totals:
                totals[key] += int(results.summary.get(key, 0))
            for run in results.convergence["inner_runs"]:
                run.update(id=len(inner_runs) + 1, stage_ratio=ratio,
                           thermal_iteration=thermal_iteration)
                inner_runs.append(run)
            return results.convergence["layers"]["inner"]["status"] == "pass"

        def record_stage(ratio, accepted, step, warm, reason):
            continuation_history.append({
                "attempt": len(continuation_history) + 1, "stage_ratio": ratio,
                "from_ratio": reached_ratio, "accepted": accepted, "step": step,
                "preburner_iteration": None, "warm_start": warm, "termination_reason": reason,
            })

        def thermal_close(first_x):
            nonlocal thermal_reason
            if not thermal_enabled:
                thermal_reason = "fixed_heat_load_and_pressure_drop"
                return True
            omega, previous_metric = .5, None
            accepted_q, accepted_output, accepted_x = None, None, None
            x = list(first_x)
            for attempt in range(thermal_limit):
                checkpoint()
                # Initial target stage has already evaluated the current q and x.
                inner_passed = True if attempt == 0 else solve_at(1.0, x, attempt + 1)
                q = get_feedback()
                out = [self._finite_or_none(results.summary.get(key)) for key in
                       ("chamber_temperature_K", "chamber_gamma", "chamber_molar_mass")]
                valid = all(v is not None and v > 0 for v in out) and out[1] > 1
                differences = ([out[i] - q[i] for i in range(3)] if valid else [None] * 3)
                residuals = ([abs(differences[0]), abs(differences[1]) / max(abs(q[1]), 1e-12),
                              abs(differences[2]) / max(abs(q[2]), 1e-12)] if valid else [None] * 3)
                metric = max(residuals[0] / temperature_tol,
                             residuals[1] / property_tol, residuals[2] / property_tol) if valid else None
                grows = previous_metric is not None and metric is not None and metric > 1.25 * previous_metric
                accepted = inner_passed and valid and not grows
                point = {
                    "iteration": attempt + 1, "preburner_iteration": None,
                    "residual_K": residuals[0], "gamma_relative_residual": residuals[1],
                    "molar_mass_relative_residual": residuals[2],
                    "temperature_guess_K": q[0], "temperature_calculated_K": out[0],
                    "gamma_guess": q[1], "gamma_calculated": out[1],
                    "molar_mass_guess": q[2], "molar_mass_calculated": out[2],
                    "accepted": accepted, "relaxation": omega,
                    "normalized_feedback_residual": metric,
                }
                thermal_history.append(point)
                results.summary.update(tc_outer_iterations=float(len(thermal_history)),
                                       tc_outer_residual_K=residuals[0],
                                       gamma_outer_relative_residual=residuals[1],
                                       molar_mass_outer_relative_residual=residuals[2],
                                       chamber_temp_guess_used_K=q[0])
                if accepted and metric <= 1:
                    thermal_reason = "converged"
                    return True
                if not accepted:
                    if accepted_q is None or omega <= .1 or attempt + 1 >= thermal_limit:
                        thermal_reason = ("inner_failed" if not inner_passed else
                                          "invalid_thermal_feedback" if not valid else "thermal_step_rejected")
                        return False
                    omega = max(.1, omega * .5)
                    set_feedback([accepted_q[i] + omega * (accepted_output[i] - accepted_q[i])
                                  for i in range(3)])
                    x = list(accepted_x)
                    continue
                if attempt + 1 >= thermal_limit:
                    thermal_reason = "max_iterations"
                    return False
                oscillating = (accepted_q is not None and
                               differences[0] * (accepted_output[0] - accepted_q[0]) < 0 and
                               metric >= .5 * previous_metric)
                if oscillating:
                    omega = max(.1, omega * .5)
                elif previous_metric is not None and metric < .8 * previous_metric:
                    omega = min(1.0, 1.5 * omega)
                point["next_relaxation"] = omega
                accepted_q, accepted_output, accepted_x = q, out, list(x_solved)
                previous_metric = metric
                set_feedback([q[i] + omega * differences[i] for i in range(3)])
                x = list(x_solved)
            thermal_reason = "max_iterations"
            return False

        try:
            initialization_history = []
            if warm_state is None:
                initialization = self._initialize_design_guess()
                if initialization is not None:
                    initialization_history.append(initialization)
                    work = int(initialization.get("cycle_evaluations", 0))
                    totals["solver_function_evaluations"] += work
                    totals["solver_initialization_evaluations"] += work
                    warm_state = copy.deepcopy(initialization.get("warm_state"))
                    warm_attempted = bool(warm_state)
            # Between GG mixture-ratio iterations, try the complete previous
            # target solution once. If it fails, retain evidence and restart
            # continuation from the caller's original feedback values.
            if warm_state:
                set_feedback([warm_state[key] for key in
                              ("chamber_temp_guess", "chamber_gamma", "molar_mass")])
                ok = solve_at(1.0, list(warm_state["x"]), 1 if thermal_enabled else None)
                if ok:
                    ok = thermal_close(x_solved)
                record_stage(1.0, ok, 1.0, True, "converged" if ok else "warm_start_failed")
                if ok:
                    reached_ratio, warm_used = 1.0, True
                else:
                    warm_fallback = True
                    set_feedback(original[2:])

            if reached_ratio < 1.0:
                step, trial_ratio, accepted_x = .4, .6, None
                for attempt in range(24):
                    checkpoint()
                    x0 = (None if accepted_x is None else
                          [accepted_x[0] * trial_ratio / reached_ratio] + list(accepted_x[1:]))
                    ok = solve_at(trial_ratio, x0, 1 if trial_ratio == 1.0 and thermal_enabled else None)
                    thermal_failed = False
                    if ok and trial_ratio == 1.0:
                        ok = thermal_close(x_solved)
                        thermal_failed = not ok
                    record_stage(trial_ratio, ok, trial_ratio - reached_ratio, False,
                                 "converged" if ok else "thermal_solver_failed" if thermal_failed else
                                 results.convergence["layers"]["inner"]["termination_reason"])
                    if ok:
                        reached_ratio, accepted_x = trial_ratio, list(x_solved)
                        if reached_ratio == 1.0:
                            break
                        step = min(.5, step * 1.5)
                    elif thermal_failed:
                        continuation_reason = "thermal_solver_failed"
                        break
                    else:
                        step = (trial_ratio - reached_ratio) * .5
                        if step < 1 / 64:
                            continuation_reason = "minimum_step_reached"
                            break
                    trial_ratio = min(1.0, reached_ratio + step)
                else:
                    continuation_reason = "max_attempts"

            inner_passed = results.convergence["layers"]["inner"]["status"] == "pass"
            target_reached = reached_ratio == 1.0 and evaluated_ratio == 1.0 and inner_passed
            if target_reached:
                continuation_reason = "target_reached"
            elif continuation_reason == "target_stage_not_reached":
                continuation_reason = "inner_failed_at_target" if evaluated_ratio == 1.0 else "target_stage_not_reached"
            last = thermal_history[-1] if thermal_history else {}
            checks = [{"name": name, "residual": last.get(key), "tolerance": tolerance, "unit": unit}
                      for name, key, tolerance, unit in (
                          ("temperature", "residual_K", temperature_tol, "K"),
                          ("gamma_relative_residual", "gamma_relative_residual", property_tol, "1"),
                          ("molar_mass_relative_residual", "molar_mass_relative_residual", property_tol, "1"))]
            results.convergence["layers"]["thermal"] = {
                "enabled": thermal_enabled,
                "status": ("pass" if thermal_reason == "converged" else "fail") if thermal_enabled else "disabled",
                "residual": last.get("residual_K"), "tolerance": temperature_tol if thermal_enabled else None,
                "unit": "K", "iterations": len(thermal_history), "termination_reason": thermal_reason,
                "checks": checks if thermal_enabled else [],
            }
            results.convergence["layers"]["continuation"] = {
                "enabled": True, "status": "pass" if target_reached else "fail",
                "residual": abs(1.0 - reached_ratio), "tolerance": 0.0, "unit": "1",
                "iterations": len(continuation_history), "termination_reason": continuation_reason,
                "target_stage_ratio": 1.0, "reached_ratio": reached_ratio,
                "last_stage_ratio": reached_ratio, "attempted_ratio": evaluated_ratio,
            }
            results.convergence.update(inner_runs=inner_runs, thermal_history=thermal_history,
                                       continuation_history=continuation_history,
                                       initialization_history=initialization_history,
                                       warm_start={"attempted": warm_attempted, "used": warm_used,
                                                   "fallback": warm_fallback})
            results.summary["solver_iterations_last"] = results.summary.get("solver_iterations", 0)
            results.summary.update({key: float(value) for key, value in totals.items()})
            results.summary.update(solver_inner_solves=float(len(inner_runs)),
                                   tc_outer_iterations=float(len(thermal_history)),
                                   tc_outer_iterations_total=float(len(thermal_history)),
                                   continuation_target_reached=float(target_reached),
                                   evaluated_chamber_pressure_Pa=base_pc * evaluated_ratio,
                                   requested_chamber_pressure_Pa=base_pc,
                                   mdot_solved_kg_s=x_solved[0], gg_fraction_solved=x_solved[1])
            if self._dual_shaft():
                results.summary["gg_split_to_ox_shaft_solved"] = x_solved[2]
            complete = target_reached and (not thermal_enabled or thermal_reason == "converged")
            results.convergence["solution"] = ({"x": list(x_solved),
                "chamber_temp_guess": self.inputs.chamber_temp_guess,
                "chamber_gamma": self.inputs.chamber_gamma, "molar_mass": self.inputs.molar_mass}
                if complete else None)
            return results
        finally:
            (self.inputs.pc, self.inputs.thrust, self.inputs.chamber_temp_guess,
             self.inputs.chamber_gamma, self.inputs.molar_mass) = original

    # =====================================================
    # 6. 节流特性（固定硬件：喉部 At 与冷却通道深度冻结在设计点）
    # =====================================================

    def throttle_sweep(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        record_callback: Optional[Callable[[dict], None]] = None,
        *,
        design_baseline: Optional[dict] = None,
        include_cooling_marching: bool = True,
    ) -> list[dict]:
        checkpoint()
        steps = max(2, int(getattr(self.inputs, "throttle_steps", 5)))
        throttle_min = self._clamp(float(getattr(self.inputs, "throttle_min", 0.4)), 0.05, 0.99)
        sweep: list[dict] = []

        # Web studies use an immutable source design. Never silently fall back
        # to proportional pressure when that explicitly supplied baseline fails.
        if design_baseline is not None:
            frozen_contour = fixed_contour_inputs(design_baseline)
            At_design = float(design_baseline.get("throat_area_m2", 0))
            if not math.isfinite(At_design) or At_design <= 0:
                raise ValueError("固定喉部基准面积必须为有限正数。")
            frozen_h_m = design_baseline.get("cooling_height_m")
            if frozen_h_m is not None:
                frozen_h_m = float(frozen_h_m)
                if not math.isfinite(frozen_h_m) or frozen_h_m <= 1e-4:
                    raise ValueError("冷却通道冻结高度无效。")
        else:
            frozen_contour = {key: getattr(self.inputs, key) for key in (
                "nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg",
                "nozzle_bell_fraction", "chamber_conv_half_angle_deg", "expansion_ratio")}
            At_design = None
            frozen_h_m = None
            try:
                _design = GGCycleModel(copy.deepcopy(self.inputs)).evaluate(
                    include_cooling_marching=include_cooling_marching)
                profile = getattr(_design.components.get("cooling_channel"), "profile", None) or {}
                frozen_contour = fixed_contour_inputs({"contour_inputs": frozen_contour,
                    "cooling_hardware": {"geometry": profile.get("geometry", {})}})
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
            for key, value in frozen_contour.items():
                setattr(pi, key, value)
            pi.thrust = thrust_target
            pi.pc = pc_val
            pi.cooling_marching_enable = 0.0
            if frozen_h_m is not None and frozen_h_m > 1e-4:
                pi.cooling_channel_auto_size = 0.0
                pi.cooling_channel_height_m = frozen_h_m
            model=GGCycleModel(pi)
            model._fixed_cooling_throat_area=At_design
            return pi, model.evaluate()

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
                if include_cooling_marching and self.inputs.propellant_pair == "lox_methane" and bool(getattr(self.inputs, "cooling_marching_enable", 1.0)):
                    try:
                        from cooling_marching import run_marching_postpass
                        run_marching_postpass(point_results, point_inputs)
                    except Exception:
                        pass
                from convergence import numerical_convergence
                converged = numerical_convergence(point_results)["status"] == "pass"
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
