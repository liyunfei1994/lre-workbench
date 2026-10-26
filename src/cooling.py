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
    """Entrance-density sizing at the throat width, without hidden clipping."""
    for value in (mdot_fuel, rho_co, target_velocity, width_m, n_channels):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("channel sizing inputs must be positive and finite")
    return mdot_fuel/(rho_co*target_velocity*n_channels*width_m), False


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
            gas_emissivity=.2, wall_temp_limit_K=800., film_fraction=0., counterflow=True):
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
        if self.auto_size and not self.channel_table:
            h,_ = resolve_channel_height(self.mdot_fuel,raw["rho_kgm3"],self.target_velocity_ms,
                self.channel_width_m,self.n_channels)
        result = march_regen_circuit(mdot_coolant=self.mdot_fuel,T_in=self.t_inlet,p_in=self.p_inlet,
            mdot_total=self.mdot_total,pc=self.pc,T_c=self.t_chamber,gamma=self.gamma_c,
            molar_mass=self.molar_mass_c,r_t=math.sqrt(at/math.pi),r_c=sized["cc_diameter_m"]/2,
            L_cyl=sized["cc_length_m"],
            expansion_ratio=self.expansion_ratio,cover_eps=self.cover_eps,
            n_channels=self.n_channels,channel_width_m=self.channel_width_m,channel_height_m=h,
            wall_thickness_m=self.wall_thickness_m,wall_conductivity=self.wall_conductivity,
            htc_enhancement=self.htc_enhancement,k_wall_func=self.k_wall_func,
            rel_roughness=self.channel_rel_roughness,k_minor=self.channel_k_minor,
            counterflow=self.counterflow,channel_table=self.channel_table,bell_fraction=self.bell_fraction,
            film_fraction=self.film_fraction,gas_emissivity=self.gas_emissivity,
            conv_half_angle_deg=self.conv_half_angle_deg,wall_temp_limit_K=self.wall_temp_limit_K,
            n_points=self.n_points,hot_side_profile=self.hot_side_profile,inlet_enthalpy_Jkg=h_in)
        profile = result["profile"]
        s,b = result["summary"],result["balance"]
        channels = [c for c in profile["cells"] if c["kind"] == "channel"]
        rt,xt,lc = profile["geometry"]["r_t_m"],profile["geometry"]["x_throat_m"],sized["cc_length_m"]
        throat = min(channels,key=lambda c:abs(c["x_m"]-xt))
        throat_node = min(profile["geometry"]["nodes"],key=lambda n:abs(n["x_m"]-xt))
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
                "自动定深仅按入口密度、喉部宽、通道数和目标流速定尺寸；不是沿程流速控制或部件验证。",
                "分区热量是互斥几何单元求和；喉部带仅是汇总标签，不另加传热面积。",
                *profile["validity"]["warnings"]])
        component.profile = profile
        return state,component

    def evaluate(self):
        return self._evaluate_bartz() if self.bartz_mode else self._evaluate_fixed()
