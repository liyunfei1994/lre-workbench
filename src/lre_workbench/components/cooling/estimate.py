"""Conceptual hot-wall load at a prescribed reference wall temperature.

Coarse geometry and the iterated gas state determine local flux and integrated
heat. The coolant uses a port enthalpy balance with a separately estimated
pressure loss. There is no channel, wall-conduction or coolant-HTC solution.
"""
from __future__ import annotations

import math
from lre_workbench.components.chambers.sizing import size_combustion_chamber
from lre_workbench.components.cooling.geometry import build_hot_wall_geometry
from lre_workbench.components.cooling.marching import _hot_state, hot_wall_flux, remap_hot_side
from lre_workbench.components.cooling.model import _throat_area, _bartz_gas_props
from lre_workbench.domain.models import ComponentResult, FlowState
from lre_workbench.runtime.tasks import checkpoint

MODEL_ID = "regen_concept_heat_v1"


def evaluate_concept_heat(model):
    """Evaluate a CoolingChannel wrapper in mode 2 without accessing channels."""
    m = model
    tw = float(m.reference_wall_temperature_K)
    if not math.isfinite(tw) or tw <= 0:
        raise ValueError("概略热负荷估算需要有限正值的热面参考壁温。")
    if not math.isfinite(m.gas_emissivity) or not 0 <= m.gas_emissivity <= 1:
        raise ValueError("燃气有效辐射系数必须在 0 至 1 之间。")
    if m.film_fraction:
        raise ValueError("概略热负荷估算尚不支持液膜冷却；不能由液膜比例推定热流削减。")
    at = m.throat_area_m2 or _throat_area(m.mdot_total,m.pc,m.t_chamber,m.gamma_c,m.molar_mass_c)
    sized = size_combustion_chamber(At=at,pc=m.pc,mdot_total=m.mdot_total,
        T_c=m.t_chamber,molar_mass=m.molar_mass_c,gamma=m.gamma_c,
        l_star=m.l_star_m,contraction_ratio=m.contraction_ratio,
        conv_half_angle_deg=m.conv_half_angle_deg)
    if sized["cc_cyl_section_clamped"] or sized["cc_length_m"] <= 0:
        raise ValueError("概略特征容积不足以容纳收缩轮廓；请调整 L* 或收缩几何。")
    geom = build_hot_wall_geometry(r_t=math.sqrt(at/math.pi),r_c=sized["cc_diameter_m"]/2,
        L_cyl=sized["cc_length_m"],expansion_ratio=m.expansion_ratio,cover_eps=m.cover_eps,
        bell_fraction=m.bell_fraction,conv_half_angle_deg=m.conv_half_angle_deg,
        n_points=m.n_points,nozzle_contour_method=m.nozzle_contour_method,
        nozzle_initial_angle_deg=m.nozzle_initial_angle_deg,nozzle_exit_angle_deg=m.nozzle_exit_angle_deg)
    supplied = m.hot_side_profile
    if supplied is not None and m.remap_hot_side_trial:
        supplied = remap_hot_side(supplied, geom["cells"])
    if supplied is not None and len(supplied) != len(geom["cells"]):
        raise ValueError("概略热负荷与燃气反馈单元未对齐。")
    mu, cp, pr = _bartz_gas_props(m.gamma_c,8314.462618/m.molar_mass_c,m.t_chamber)
    cells = []
    for index, cell in enumerate(geom["cells"]):
        checkpoint()
        hot = _hot_state(cell,gamma=m.gamma_c,T_c=m.t_chamber,pc=m.pc,mu_g=mu,cp_g=cp,Pr_g=pr,
                         supplied=None if supplied is None else supplied[index])
        hg, conv, rad = hot_wall_flux(cell,hot,tw,mdot_total=m.mdot_total,r_t=geom["r_t_m"],
            pc=m.pc,gas_emissivity=m.gas_emissivity,
            throat_upstream_radius_m=geom["throat_upstream_radius_m"])
        q = conv+rad
        if not math.isfinite(q) or q < 0:
            raise ValueError(f"概略热负荷在 x={cell['x_m']:.5g} m 出现反向热流；检查参考壁温及覆盖范围，当前热侧分支不支持反向加热。")
        cells.append(dict(cell,kind="heat_estimate",hot_side=hot,
            reference_wall_temperature_K=tw,h_g_W_m2K=hg,q_W_m2=q,
            q_conv_W_m2=conv,q_rad_W_m2=rad,Q_W=q*cell["dA_hot_m2"]))
    heat = sum(c["Q_W"] for c in cells)
    nozzle_heat = sum(c["Q_W"] for c in cells if c["x_m"] >= geom["x_throat_m"])
    pout = m.p_inlet-m.dp_pa
    tout,hout,rho,hin,basis = m._coolant_state(heat,pout)
    peak = max(cells,key=lambda c:c["q_W_m2"])
    throat = min(cells,key=lambda c:abs(c["x_m"]-geom["x_throat_m"]))
    outputs = dict(mode=2.,heat_load_W=heat,heat_load_MW=heat/1e6,
        heat_load_nozzle_W=nozzle_heat,heat_load_chamber_W=heat-nozzle_heat,
        A_throat_m2=at,A_hot_m2=geom["hot_area_m2"],
        estimated_q_peak_MW_m2=peak["q_W_m2"]/1e6,estimated_q_peak_x_m=peak["x_m"],
        estimated_q_near_throat_MW_m2=throat["q_W_m2"]/1e6,
        reference_wall_temperature_K=tw,T_in_K=m.t_inlet,T_out_K=tout,
        delta_T_K=tout-m.t_inlet,P_in_Pa=m.p_inlet,P_out_Pa=pout,
        delta_P_MPa=m.dp_pa/1e6,delta_P_source=0.,h_in_Jkg=hin,h_out_Jkg=hout,
        chemical_enthalpy_basis_available=basis,energy_residual_W=m.mdot_fuel*(hout-hin)-heat)
    notes = ["概略热负荷：在给定热面参考壁温下，按概略型面及覆盖范围积分 Bartz 对流与简化辐射热流；热量随当前燃气状态和尺寸迭代。",
        "参考壁温是估算假设，不是求得壁温或材料允许温度。没有求解通道、壁面导热、冷却侧换热及水力能力。",
        "冷却剂出口由估算吸热功率、实际流量与给定压降估计的单相 HEOS 焓平衡确定；不生成沿程冷却剂状态。",
        "数值闭合仅证明本概略模型的自洽性；总吸热功率与局部峰值须交由部件模型进一步校核。"]
    if tout >= tw:
        notes.append("估算冷却出口温度已达到或超过参考热面壁温；该壁温假设与冷却能力存在矛盾，需重新选择假设并开展详细冷却校核。")
    result = ComponentResult("cooling_channel",outputs=outputs,notes=notes)
    result.profile = dict(version=1,model_id=MODEL_ID,scope="concept_heat_estimate",
        flow_direction="gas_flow",geometry=geom,cells=cells,
        assumptions=dict(reference_wall_temperature_K=tw,estimated_pressure_drop_Pa=m.dp_pa,
            basis=m.estimate_basis,gas_emissivity=m.gas_emissivity,
            radiation_model="grey_gas_local_pressure_weighted",wall_temperature_solved=False,
            channel_hydraulics_solved=False,coolant_spatial_states_solved=False),
        summary=dict(heat_load_W=heat,hot_area_m2=geom["hot_area_m2"],
            peak_heat_flux_W_m2=peak["q_W_m2"],peak_x_m=peak["x_m"]),
        balance=dict(energy_residual_W=outputs["energy_residual_W"]),
        validity=dict(status="review",cooling_capability="not_evaluated",notes=notes))
    state = FlowState("fuel_after_cooling",pout,tout,m.mdot_fuel,enthalpy=hout,density=rho,
        composition={m.fuel_species:1.})
    return state,result
