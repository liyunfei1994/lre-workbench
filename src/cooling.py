"""Cycle wrapper for the single system-level coolant model.

Budget mode specifies Q and pressure drop and has no spatial solution. Bartz
mode delegates heat, momentum, geometry and outlet state to the unique p/h
kernel. Pure-fluid properties are checked HEOS states without fit fallbacks.
"""
from __future__ import annotations
import math
from core import ComponentResult, FlowState, ValidationError, _check_range
from propellants import normalize_fuel_species, validate_surrogate_state
from fluid_properties import state_tp, state_ph

try:
    from fluids.friction import friction_factor as _fluids_friction_factor
except ImportError:
    _fluids_friction_factor = None

_R_UNIVERSAL = 8314.462618
_WALL_THICKNESS_M = .0005
_WALL_CONDUCTIVITY = 320.


def ch4_props(T, p):
    state = state_tp("CH4", T, p, transport=True)
    return tuple(state[key] for key in ("rho_kgm3", "mu_Pas", "k_WmK", "cp_JkgK"))


def fuel_props(T, p, fuel_species="CH4"):
    fuel_species = normalize_fuel_species(fuel_species)
    if fuel_species == "c12h26":
        validate_surrogate_state(T, p)
    state = state_tp(fuel_species, T, p, transport=True)
    return tuple(state[key] for key in ("rho_kgm3", "mu_Pas", "k_WmK", "cp_JkgK"))


def _ch4_density_supercritical(T, p):
    """Legacy name; strict HEOS query, no supercritical fitting formula."""
    return state_tp("CH4", T, p)["rho_kgm3"]


def _pressure_required(*args, **kwargs):
    raise RuntimeError("旧无压力甲烷拟合已停用；请使用含实际T、p的严格物性接口或主循环冷却剖面。")


_ch4_cp_supercritical = _pressure_required
_ch4_viscosity = _pressure_required
_ch4_thermal_conductivity = _pressure_required


def _friction_factor(Re, rel_roughness):
    """Darcy friction: laminar 64/Re, otherwise Colebrook or explicit Haaland."""
    if not math.isfinite(Re) or Re <= 0 or not math.isfinite(rel_roughness) or not 0 <= rel_roughness < 1:
        raise ValueError("invalid Reynolds number or roughness")
    if Re < 2300:
        return 64/Re
    if _fluids_friction_factor is not None:
        value = float(_fluids_friction_factor(Re=Re, eD=rel_roughness))
    else:
        inverse = -1.8*math.log10((rel_roughness/3.7)**1.11+6.9/Re)
        value = 1/inverse**2
    if not math.isfinite(value) or value <= 0:
        raise ValueError("nonpositive Darcy friction factor")
    return value


def _bartz_gas_props(gamma, R_spec, T_c):
    """Declared fallback gas transport correlation, not measured engine data."""
    cp = gamma/(gamma-1)*R_spec
    mu = 8e-5*(T_c/3000)**.7
    pr = 4*gamma/(9*gamma-5)
    return mu, cp, pr


def _throat_area(mdot_total, pc, T_c, gamma, Mm):
    """Initial geometric estimate only; coupled callers supply the actual At."""
    flux = math.sqrt(gamma)*(2/(gamma+1))**((gamma+1)/(2*(gamma-1)))
    return mdot_total*math.sqrt(_R_UNIVERSAL/Mm*T_c)/(pc*flux)


def resolve_channel_height(mdot_fuel, rho_co, target_velocity, width_m, n_channels):
    """Historical mixed-reference sizing; not an actual-section velocity.

    Retained verbatim for replay of saved designs. New sizing uses
    ``resolve_inlet_velocity_height`` and the actual channel entrance width.
    """
    for value in (mdot_fuel, rho_co, target_velocity, width_m, n_channels):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("channel sizing inputs must be positive and finite")
    return mdot_fuel/(rho_co*target_velocity*n_channels*width_m), False


def resolve_inlet_velocity_height(*, mdot_coolant, port_state, target_velocity_ms,
                                  inlet_geometry, k_minor_inlet):
    """Size a uniform depth from the channel inlet after its manifold loss.

    These are the same inlet-cell equations checked by ``march_regen_circuit``:
    h = h_port - u**2/2 and p + (1 + K_in) rho(p,h) u**2/2 = p_port.
    The inlet is adiabatic and has no length friction. The final full march
    independently verifies the resulting actual channel-inlet velocity.
    """
    from scipy.optimize import brentq
    p_port = port_state["p_Pa"]
    width = inlet_geometry["width_m"]
    count = inlet_geometry["n_channels"]
    for name, value in (("coolant flow", mdot_coolant), ("inlet pressure", p_port),
                        ("target inlet velocity", target_velocity_ms),
                        ("inlet width", width), ("channel count", count)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if not math.isfinite(k_minor_inlet) or k_minor_inlet < 0:
        raise ValueError("inlet local loss coefficient must be finite and nonnegative")
    target = target_velocity_ms
    h_static = port_state["h_Jkg"] - target**2 / 2
    coefficient = (1 + k_minor_inlet) * target**2 / 2
    evaluations = 0

    def residual(pressure):
        nonlocal evaluations
        evaluations += 1
        if not math.isfinite(pressure) or pressure <= 0:
            raise ValueError("目标入口速度不能建立正压冷却通道入口状态。")
        try:
            raw = state_ph("CH4", pressure, h_static, transport=True)
        except ValueError as exc:
            raise ValueError("目标入口速度定深超出单相物性范围；请降低目标或改用指定几何。") from exc
        if target >= raw["sound_speed_ms"]:
            raise ValueError("目标入口速度不是亚声速，当前通道模型不接受该定深结果。")
        return pressure + coefficient * raw["rho_kgm3"] - p_port

    # Follow the positive-pressure, subsonic branch connected to the inlet.
    # Do not accept an arbitrary distant pressure root or a best trial value.
    upper = p_port
    upper_residual = residual(upper)
    drop = upper_residual * 1.1
    lower = upper
    for _ in range(32):
        lower = upper - drop if drop < upper else upper / 2
        lower_residual = residual(lower)
        if lower_residual >= upper_residual:
            raise ValueError("入口定深压力方程出现非单调分支，不能确认唯一的入口连接解。")
        if lower_residual <= 0:
            break
        upper, upper_residual = lower, lower_residual
        drop *= 2
    else:
        raise ValueError("目标入口速度定深未找到正压亚声速根。")
    values = [residual(lower + (upper-lower)*i/8) for i in range(9)]
    if any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError("入口定深压力方程出现非单调分支，拒绝选择不确定根。")
    pressure, solved = brentq(residual, lower, upper, xtol=1e-5,
                             rtol=1e-12, maxiter=80, full_output=True)
    pressure_residual = residual(pressure)
    if not solved.converged or abs(pressure_residual) > .5:
        raise ValueError("入口速度定深压力方程未闭合，未接受近似尺寸。")
    raw = state_ph("CH4", pressure, h_static, transport=True)
    area = mdot_coolant / (raw["rho_kgm3"] * target)
    height = area / (count * width)
    return height, dict(iterations=int(solved.iterations), function_evaluations=evaluations,
                       pressure_residual_Pa=pressure_residual, converged=True,
                       predicted_inlet_p_Pa=pressure, predicted_inlet_rho_kgm3=raw["rho_kgm3"],
                       predicted_inlet_A_flow_m2=area)


def _section_summary(node):
    return {key: node[key] for key in (
        "kind", "x_m", "s_m", "rho_kgm3", "A_flow_m2", "width_m", "height_m",
        "n_channels", "p_Pa", "T_K", "velocity_ms", "pitch_m", "rib_m", "D_h_m")}


def estimate_regen_dp(*args, **kwargs):
    raise RuntimeError("独立代表态冷却压降估算已停用；请调用唯一准一维CoolingChannel模型闭合压力与热量。")


class CoolingChannel:
    def __init__(self, mdot_fuel, t_inlet, p_inlet, dp_pa=8e6, heat_load=90e6,
            cp=2400., bartz_mode=True, mdot_total=0., pc=0., t_chamber=3550.,
            gamma_c=1.18, molar_mass_c=21., contraction_ratio=2.5, l_star_m=1.,
            n_channels=150, channel_width_m=.0015, channel_height_m=.005,
            wall_thickness_m=_WALL_THICKNESS_M, wall_conductivity=_WALL_CONDUCTIVITY,
            htc_enhancement=1., auto_size=True, target_velocity_ms=100., dp_auto=False,
            channel_rel_roughness=.003, channel_k_minor=3., cover_eps=0.,
            conv_half_angle_deg=30., fuel_species="CH4", inlet_enthalpy=None,
            throat_area_m2=None, hot_side_profile=None, expansion_ratio=34.34,
            bell_fraction=.8, channel_table=None, n_points=120, k_wall_func=None,
            gas_emissivity=.2, wall_temp_limit_K=800., film_fraction=0., counterflow=True,
            wall_thermal_dataset_id=None, wall_manufacturing_state="unspecified", wall_material_zones=None,
            remap_hot_side_trial=False, nozzle_contour_method="legacy_log_v1",
            nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0,
            sizing_reference="legacy_inlet_density_throat_width",
            wall_temp_limit_status="provisional", wall_temp_limit_basis=""):
        self.fuel_species = normalize_fuel_species(fuel_species)
        if self.fuel_species == "c12h26":
            validate_surrogate_state(t_inlet, p_inlet)
            if bartz_mode:
                raise ValidationError("煤油替代模型当前采用给定吸热量与压降，不支持甲烷准一维冷却关联式。")
        for name, value in (("mdot_fuel",mdot_fuel),("t_inlet",t_inlet),("p_inlet",p_inlet)):
            _check_range("CoolingChannel."+name, value, lo=0, lo_inclusive=False)
        if not bartz_mode:
            _check_range("CoolingChannel.dp_pa",dp_pa,lo=0)
            _check_range("CoolingChannel.heat_load",heat_load,lo=0)
            if dp_pa >= p_inlet:
                raise ValidationError("CoolingChannel: 指定压降不小于入口压力")
        else:
            for name,value in (("mdot_total",mdot_total),("pc",pc),("t_chamber",t_chamber),
                ("molar_mass_c",molar_mass_c),("l_star_m",l_star_m)):
                _check_range("CoolingChannel."+name,value,lo=0,lo_inclusive=False)
            _check_range("CoolingChannel.gamma_c",gamma_c,lo=1.,lo_inclusive=False)
            _check_range("CoolingChannel.contraction_ratio",contraction_ratio,lo=1.,lo_inclusive=False)
            if throat_area_m2 is not None:
                _check_range("CoolingChannel.throat_area_m2",throat_area_m2,lo=0,lo_inclusive=False)
        if int(n_channels) != n_channels or n_channels <= 0:
            raise ValidationError("cooling channel count must be a positive integer")
        if sizing_reference not in ("legacy_inlet_density_throat_width", "coolant_inlet_velocity"):
            raise ValidationError("unknown cooling channel sizing reference")
        self.__dict__.update({key:value for key,value in locals().copy().items()
            if key not in ("self","name","value","__class__","fuel_species")})
        self.fuel_species = normalize_fuel_species(fuel_species)
        self.bartz_mode = bool(bartz_mode)
        self.auto_size = bool(auto_size)
        self.dp_auto = bool(dp_auto)
        self.n_channels = int(n_channels)

    def _coolant_state(self, heat_load_W, p_out):
        from thermo import reactant_enthalpy
        raw_in = state_tp(self.fuel_species,self.t_inlet,self.p_inlet)
        h_in = (reactant_enthalpy(self.fuel_species,self.t_inlet,self.p_inlet,self.fuel_species)
                if self.inlet_enthalpy is None else self.inlet_enthalpy)
        raw_out = state_ph(self.fuel_species,p_out,raw_in["h_Jkg"]+heat_load_W/self.mdot_fuel)
        h_out = raw_out["h_Jkg"]+h_in-raw_in["h_Jkg"]
        return raw_out["T_K"],h_out,raw_out["rho_kgm3"],h_in,1.

    def _evaluate_fixed(self):
        p_out = self.p_inlet-self.dp_pa
        t_out,h_out,rho,h_in,basis = self._coolant_state(self.heat_load,p_out)
        state = FlowState("fuel_after_cooling",p_out,t_out,self.mdot_fuel,
            enthalpy=h_out,density=rho,composition={self.fuel_species:1.})
        result = ComponentResult("cooling_channel",outputs=dict(mode=0.,heat_load_W=self.heat_load,
            heat_load_MW=self.heat_load/1e6,delta_T_K=t_out-self.t_inlet,T_in_K=self.t_inlet,T_out_K=t_out,
            P_in_Pa=self.p_inlet,P_out_Pa=p_out,delta_P_MPa=self.dp_pa/1e6,delta_P_source=0.,
            h_in_Jkg=h_in,h_out_Jkg=h_out,chemical_enthalpy_basis_available=basis,
            energy_residual_W=self.mdot_fuel*(h_out-h_in)-self.heat_load),
            notes=["指定吸热量与压降的系统预算；严格单相HEOS焓平衡，不产生沿程解或几何验证结论。",
                   "保留的cp输入不用于求温；出口由实际压力与焓确定。"])
        return state,result

    def _evaluate_bartz(self):
        from chamber_sizing import size_combustion_chamber
        from cooling_marching import march_regen_circuit
        from thermo import reactant_enthalpy
        at = (self.throat_area_m2 if self.throat_area_m2 is not None else
              _throat_area(self.mdot_total,self.pc,self.t_chamber,self.gamma_c,self.molar_mass_c))
        sized = size_combustion_chamber(At=at,pc=self.pc,mdot_total=self.mdot_total,
            T_c=self.t_chamber,molar_mass=self.molar_mass_c,gamma=self.gamma_c,
            l_star=self.l_star_m,contraction_ratio=self.contraction_ratio,
            conv_half_angle_deg=self.conv_half_angle_deg)
        if sized["cc_cyl_section_clamped"] or sized["cc_length_m"] <= 0:
            raise ValueError("特征容积不足以容纳所选收缩轮廓，不截断圆筒长度继续计算。")
        raw = state_tp("CH4",self.t_inlet,self.p_inlet)
        h_in = (reactant_enthalpy("CH4",self.t_inlet,self.p_inlet)
                if self.inlet_enthalpy is None else self.inlet_enthalpy)
        h = self.channel_height_m
        sizing = dict(version=1, mode="fixed_geometry", reference=self.sizing_reference,
            status="prescribed_channel_table" if self.channel_table else "prescribed_geometry",
            target_velocity_ms=None, actual_velocity_ms=None, velocity_residual_ms=None,
            tolerance_ms=None, iterations=0, function_evaluations=0, converged=None,
            reference_section=None,
            note="采用指定通道几何；沿程速度由流量、局部密度和流通面积计算，未按目标速度重新定深。")
        geometry_args = dict(r_t=math.sqrt(at/math.pi),r_c=sized["cc_diameter_m"]/2,
            L_cyl=sized["cc_length_m"], expansion_ratio=self.expansion_ratio,
            cover_eps=self.cover_eps, n_channels=self.n_channels,
            channel_width_m=self.channel_width_m, channel_height_m=h,
            wall_thickness_m=self.wall_thickness_m, counterflow=self.counterflow,
            channel_table=self.channel_table, bell_fraction=self.bell_fraction,
            conv_half_angle_deg=self.conv_half_angle_deg, n_points=self.n_points,
            nozzle_contour_method=self.nozzle_contour_method,
            nozzle_initial_angle_deg=self.nozzle_initial_angle_deg,
            nozzle_exit_angle_deg=self.nozzle_exit_angle_deg)
        if self.auto_size and not self.channel_table:
            if self.sizing_reference == "legacy_inlet_density_throat_width":
                h,_ = resolve_channel_height(self.mdot_fuel,raw["rho_kgm3"],self.target_velocity_ms,
                    self.channel_width_m,self.n_channels)
                sizing.update(mode="legacy_reference", status="historical_mixed_reference",
                    target_velocity_ms=self.target_velocity_ms,
                    legacy_inputs=dict(port_density_kgm3=raw["rho_kgm3"],
                        throat_width_m=self.channel_width_m, n_channels=self.n_channels),
                    note="历史定深：入口集流腔密度与喉部通道宽度组合；参考数值不代表入口或喉部实际速度。")
            else:
                from regen_geometry import build_regen_geometry
                inlet_geometry = build_regen_geometry(**geometry_args)["nodes"][0]
                h, diagnostics = resolve_inlet_velocity_height(mdot_coolant=self.mdot_fuel,
                    port_state=raw, target_velocity_ms=self.target_velocity_ms,
                    inlet_geometry=inlet_geometry, k_minor_inlet=self.channel_k_minor/2)
                sizing.update(diagnostics, mode="coolant_inlet_velocity", status="awaiting_full_march",
                    target_velocity_ms=self.target_velocity_ms,
                    tolerance_ms=max(1e-4,abs(self.target_velocity_ms)*1e-5),
                    note="以集流腔之后首个通道截面的静态密度、宽度及流量定深；入口加速和局部损失纳入动量与总焓方程。完整沿程解再次校核入口速度。")
        geometry_args["channel_height_m"] = h
        result = march_regen_circuit(mdot_coolant=self.mdot_fuel,T_in=self.t_inlet,p_in=self.p_inlet,
            mdot_total=self.mdot_total,pc=self.pc,T_c=self.t_chamber,gamma=self.gamma_c,
            molar_mass=self.molar_mass_c,**geometry_args,wall_conductivity=self.wall_conductivity,
            htc_enhancement=self.htc_enhancement,k_wall_func=self.k_wall_func,
            rel_roughness=self.channel_rel_roughness,k_minor=self.channel_k_minor,
            film_fraction=self.film_fraction,gas_emissivity=self.gas_emissivity,
            wall_temp_limit_K=self.wall_temp_limit_K, wall_temp_limit_status=self.wall_temp_limit_status,
            wall_temp_limit_basis=self.wall_temp_limit_basis,
            hot_side_profile=self.hot_side_profile,inlet_enthalpy_Jkg=h_in,
            wall_thermal_dataset_id=self.wall_thermal_dataset_id,
            wall_manufacturing_state=self.wall_manufacturing_state,
            wall_material_zones=self.wall_material_zones,remap_hot_side_trial=self.remap_hot_side_trial)
        profile = result["profile"]
        s,b = result["summary"],result["balance"]
        channels = [c for c in profile["cells"] if c["kind"] == "channel"]
        rt,xt,lc = profile["geometry"]["r_t_m"],profile["geometry"]["x_throat_m"],sized["cc_length_m"]
        throat = min(channels,key=lambda c:abs(c["x_m"]-xt))
        throat_node = min(profile["geometry"]["nodes"],key=lambda n:abs(n["x_m"]-xt))
        flow_nodes = [node for node in profile["nodes"] if node["kind"] == "channel"]
        inlet_flow_node = flow_nodes[0]
        throat_flow_node = min(flow_nodes,key=lambda n:abs(n["x_m"]-xt))
        maximum_flow_node = max(flow_nodes,key=lambda n:n["velocity_ms"])
        if sizing["mode"] == "coolant_inlet_velocity":
            sizing.update(actual_velocity_ms=inlet_flow_node["velocity_ms"],
                velocity_residual_ms=inlet_flow_node["velocity_ms"]-self.target_velocity_ms,
                reference_section=_section_summary(inlet_flow_node))
            if abs(sizing["velocity_residual_ms"]) > sizing["tolerance_ms"]:
                raise ValueError("入口速度定深与完整沿程计算不一致，未接受该通道尺寸。")
            sizing["status"] = "converged_and_march_verified"
        profile["channel_sizing"] = sizing
        profile["channel_design_summary"] = dict(version=1,
            inlet=_section_summary(inlet_flow_node), throat=_section_summary(throat_flow_node),
            maximum_velocity_node=_section_summary(maximum_flow_node),
            maximum_cell_velocity_ms=max(cell["velocity_ms"] for cell in channels),
            geometry_ranges={key:[min(n[key] for n in profile["geometry"]["nodes"]),
                max(n[key] for n in profile["geometry"]["nodes"])] for key in
                ("pitch_m","rib_m","width_m","height_m","D_h_m","wall_thickness_m")},
            sizing_mode=sizing["mode"], channel_count_assessment="design_variable_not_optimized")
        zones = {key:0. for key in ("Q_cc_MW","Q_conv_MW","Q_throat_MW","Q_noz_MW","Q_noz_ext_MW")}
        for cell in channels:
            x = cell["x_m"]
            key = ("Q_cc_MW" if x < lc else "Q_conv_MW" if x < xt-rt else
                   "Q_throat_MW" if x <= xt+rt else "Q_noz_MW" if x <= xt+1.5*rt else "Q_noz_ext_MW")
            zones[key] += cell["Q_W"]/1e6
        dt = s["T_out_K"]-self.t_inlet
        out = dict(mode=1.,heat_load_W=s["Q_total_W"],heat_load_MW=s["Q_total_W"]/1e6,
            heat_load_chamber_W=sum(c["Q_W"] for c in channels if c["x_m"] < xt),
            heat_load_nozzle_W=sum(c["Q_W"] for c in channels if c["x_m"] >= xt),
            h_in_Jkg=h_in,h_out_Jkg=s["h_out_Jkg"],energy_residual_W=b["energy_residual_W"],
            chemical_enthalpy_basis_available=1.,
            coolant_temperature_iteration_residual_K=b["max_cell_energy_residual_W"]/(self.mdot_fuel*min(n["cp_JkgK"] for n in profile["nodes"])),
            wall_temperature_iteration_residual_K=b["max_wall_residual_K"],
            thermal_iterations=float(max(c.get("wall_iterations",0) for c in channels)),
            cooling_function_evaluations=float(sum(c["function_evaluations"] for c in profile["cells"])),
            cover_eps=s["cover_eps"],q_throat_MW_m2=throat["q_W_m2"]/1e6,
            h_g_throat_W_m2_K=throat["h_g_W_m2K"],D_throat_m=2*rt,r_throat_m=rt,A_throat_m2=at,
            bartz_throat_curvature_radius_m=s["bartz_throat_curvature_radius_m"],
            bartz_curvature_factor=s["bartz_curvature_factor"],
            r_cc_m=sized["cc_diameter_m"]/2,L_cc_m=lc,A_wetted_total_m2=profile["geometry"]["hot_area_m2"],
            T_aw_throat_K=throat["hot_side"]["T_aw_K"],T_wall_cold_K=throat["T_wall_cold_K"],
            T_wall_hot_K=throat["T_wall_hot_K"],delta_T_K=dt,T_in_K=self.t_inlet,T_out_K=s["T_out_K"],
            cp_avg_J_kg_K=s["Q_total_W"]/(self.mdot_fuel*dt) if abs(dt)>1e-12 else raw["cp_JkgK"],
            P_in_Pa=self.p_inlet,P_out_Pa=s["p_out_Pa"],delta_P_MPa=s["dp_total_Pa"]/1e6,
            delta_P_hydraulic_MPa=s["dp_total_Pa"]/1e6,delta_P_source=1.,
            channel_height_mm=throat_node["height_m"]*1000,channel_length_m=profile["geometry"]["path_length_m"],
            channel_friction_factor=throat["friction_factor"],G_throat_kg_m2_s=self.mdot_total/at,
            mu_Pa_s=throat["hot_side"]["mu_Pas"],Pr=throat["hot_side"]["Pr"],
            h_coolant_W_m2K=throat["h_c_W_m2K"],coolant_velocity_ms=throat["velocity_ms"],
            coolant_inlet_velocity_ms=inlet_flow_node["velocity_ms"],
            coolant_throat_node_velocity_ms=throat_flow_node["velocity_ms"],
            coolant_max_velocity_ms=maximum_flow_node["velocity_ms"],
            throat_pitch_mm=throat_node["pitch_m"]*1000,throat_rib_mm=throat_node["rib_m"]*1000,
            coolant_Re=throat["Re"],coolant_Pr=throat["Pr"],channel_D_h_mm=throat_node["D_h_m"]*1000,
            channel_A_flow_cm2=throat_node["A_flow_m2"]*1e4,n_channels=float(throat_node["n_channels"]),
            T_coolant_throat_K=throat["T_coolant_K"],liner_thickness_m=throat_node["wall_thickness_m"],
            wall_conductivity_WmK=throat["wall_conductivity_WmK"],**zones)
        last = profile["nodes"][-1]
        state = FlowState("fuel_after_cooling",last["p_Pa"],last["T_K"],self.mdot_fuel,
            enthalpy=last["h_Jkg"],density=last["rho_kgm3"],composition={"CH4":1.})
        component = ComponentResult("cooling_channel",outputs=out,
            notes=["唯一系统级准一维冷却解：共同几何、沿程p/h、动量与总焓；首尾集流腔速度为零。",
                "Bartz模式中的吸热量、压降和出口状态均来自同一剖面；给定Q/cp/dp兼容字段不参与此模式。",
                "Bartz气侧有效曲率取实际轮廓上游喉部圆弧；物性参考态与温度修正的成套适用性仍待验证。",
                sizing["note"],
                "通道数、宽度、肋宽和壁厚是方案设计变量；本次计算给出该组合的响应，不证明数量最优或满足制造要求。",
                "分区热量是互斥几何单元求和；喉部带仅是汇总标签，不另加传热面积。",
                *profile["validity"]["warnings"]])
        component.profile = profile
        return state,component

    def evaluate(self):
        return self._evaluate_bartz() if self.bartz_mode else self._evaluate_fixed()
