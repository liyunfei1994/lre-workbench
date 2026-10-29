"""
geometry —— 发动机结构参数计算包

从热力学计算结果推导各部件几何尺寸。原 geometry.py 已按部件拆分为本包，
对外接口完全保持不变：

    from geometry import compute_geometry, geometry_to_dict

各部件子模块：
    geometry.types       —— 数据类
    geometry.nozzle      —— 喷管（抛物线钟形系统级工程近似）
    geometry.chamber     —— 主燃烧室（含分层壁/冷却通道）
    geometry.injector    —— 喷注器
    geometry.pumps       —— 涡轮泵校核
    geometry.turbines    —— 涡轮校核
    geometry.preburners  —— 预燃室

理论依据：
    喷管：给定设计角度的圆弧与二次 Bézier 相切构造；未进行推力优化。
    燃烧室 L*：Huzel & Huang；NASA SP-125（LOX/CH₄ 典型 0.8–1.2 m）。
    涡轮泵：NASA SP-8109 / SP-8110。
"""

from __future__ import annotations

import math
from dataclasses import asdict

from .types import (
    NozzleGeometry,
    ChamberGeometry,
    InjectorGeometry,
    PumpGeometry,
    TurbineGeometry,
    PreburnerGeometry,
    NozzleFlow,
    EngineGeometry,
)
from .nozzle import compute_nozzle, _rao_bell_contour, full_hot_gas_contour
from .chamber import compute_chamber
from .injector import compute_injector
from .pumps import estimate_pump
from .turbines import estimate_turbine
from .preburners import compute_preburner
from .separation import analyze_nozzle_flow

__all__ = [
    "compute_geometry",
    "geometry_to_dict",
    "full_hot_gas_contour",
    "NozzleGeometry",
    "ChamberGeometry",
    "InjectorGeometry",
    "PumpGeometry",
    "TurbineGeometry",
    "PreburnerGeometry",
    "NozzleFlow",
    "EngineGeometry",
    "analyze_nozzle_flow",
]


def compute_geometry(
    cycle_results,
    contraction_ratio: float = 2.5,
    l_star: float = 1.0,
    theta_conv_deg: float = 30.0,
    bell_fraction: float = 0.80,
    wall_thickness_factor: float = 1.5,
) -> EngineGeometry:
    """
    从循环计算结果推导完整发动机几何参数（编排器）。

    参数
    ────
    cycle_results       : CycleResults 对象（来自 cycle.py 的求解结果）
    contraction_ratio   : 燃烧室/喉部面积比（缺少 inputs 对应字段时的后备值）
    l_star              : 特征长度 [m]（LOX/CH₄ 典型 0.8–1.2）
    theta_conv_deg      : 收缩段半角 [°]
    bell_fraction       : 钟形长度比（0.80 = 80% bell，工程抛物线钟形喷管）
    wall_thickness_factor : 兼容旧签名（壁厚安全系数实际取自 inputs）
    """
    s = cycle_results.summary
    comps = cycle_results.components
    inputs = cycle_results.inputs
    pi = math.pi

    # ── V19 口径统一：收缩半角 β 与钟形长度比一律从 inputs 读 ──────────
    # 旧版分裂：compute_chamber 用 inputs.chamber_conv_half_angle_deg，而
    # compute_nozzle 恒用形参默认 30°/0.80——用户改 β≠30° 时室分层几何与
    # 喷管轮廓/CSV 不一致。现统一：两处用同一个 β、同一个 bell_fraction。
    theta_conv_deg = float(getattr(inputs, "chamber_conv_half_angle_deg", theta_conv_deg))
    bell_fraction = float(getattr(inputs, "nozzle_bell_fraction", bell_fraction))
    contraction_ratio = float(getattr(inputs, "chamber_contraction_ratio", contraction_ratio))
    l_star = float(getattr(inputs, "chamber_l_star_m", l_star))
    method = getattr(inputs, "nozzle_contour_method", "legacy_log_v1")
    theta_n = getattr(inputs, "nozzle_initial_angle_deg", 35.)
    theta_e = getattr(inputs, "nozzle_exit_angle_deg", 10.)

    # ── 基本尺寸（从喷管热力学结果）──────────────────
    At = comps["nozzle"].outputs["At_m2"]
    Ae = comps["nozzle"].outputs["Ae_m2"]
    eps = inputs.expansion_ratio
    pc = inputs.pc
    cooling_component = comps.get("cooling_channel")
    cooling_profile = getattr(cooling_component, "profile", None)
    if cooling_profile is not None:
        if not isinstance(cooling_profile, dict):
            raise ValueError("已给冷却剖面必须为包含实际几何的记录。")
        cg = cooling_profile.get("geometry", {})
        if (cooling_profile.get("model_id") != "regen_quasi1d_ph_v1"
                or not isinstance(cg, dict) or not cg.get("nodes")):
            raise ValueError("冷却剖面缺少实际几何，不能用喷管所需面积重新生成展示轮廓。")
        # Off-design nozzle At is a required area being matched by an outer
        # solve. The physical fixed hardware is the area already used by cooling.
        At = pi * float(cg["r_t_m"]) ** 2
        eps = float(cg["expansion_ratio"])
        Ae = At * eps
        contraction_ratio = (float(cg["r_c_m"]) / float(cg["r_t_m"])) ** 2
        theta_conv_deg = float(cg.get("conv_half_angle_deg", theta_conv_deg))
        bell_fraction = float(cg.get("bell_fraction", bell_fraction))
        method = cg.get("nozzle_contour_method", "legacy_log_v1")
        theta_n = cg.get("initial_expansion_angle_deg", 35.)
        theta_e = cg.get("exit_angle_deg", 10.)

    rt = math.sqrt(At / pi)
    re = math.sqrt(Ae / pi)

    sigma_allow = getattr(inputs, "chamber_wall_allow_pa", 120e6)
    safety = getattr(inputs, "chamber_wall_safety_factor", 1.5)

    # ── 燃烧室 ────────────────────────────────────────
    # 内衬与通道优先保持已解剖面尺寸；外壳仅报告当前压力下的薄壁设计估算。
    _cool = comps["cooling_channel"].outputs if "cooling_channel" in comps else {}
    _q_throat_W = float(_cool.get("q_throat_MW_m2", 0.0) or 0.0) * 1.0e6
    _p_coolant = float(s.get("fuel_pump_out_pressure_Pa", pc) or pc)
    # 冷却通道几何取自冷却模块（同一口径）：通道数 N、通道高来自 cooling_channel 输出，
    # 通道宽来自冷却输入；几何估算/3D 图/FEA 由此与再生冷却物理一致，不再各算各的。
    _n_ch = int(_cool.get("n_channels", 0) or 0)
    _ch_h_mm = float(_cool.get("channel_height_mm", 0.0) or 0.0)
    _ch_w_mm = float(getattr(inputs, "cooling_channel_width_m", 0.0) or 0.0) * 1000.0 * math.sqrt(contraction_ratio)
    # V19 审查修复：材料按**用户所选**（inputs.chamber_inner/outer_wall_material）解析，
    # 与 cycle.py 的分层壁厚计算完全同口径——旧版此处恒查 DEFAULT 表，用户改选
    # GRCop-42/Hastelloy X 时几何页壁厚与部件卡壁厚会再度分裂。
    try:
        from materials import MATERIALS as _MATS
        _im = _MATS.get(getattr(inputs, "chamber_inner_wall_material", "grcop84"))
        _om = _MATS.get(getattr(inputs, "chamber_outer_wall_material", "inconel718_wall"))
        _k_liner = float(_im.thermal_conductivity_WmK) if _im else 303.0
        _sigma_jacket = float(_om.sigma_yield_hot_MPa) * 1.0e6 if _om else 950e6
    except Exception:
        _k_liner, _sigma_jacket = 303.0, 950e6
    chamber = compute_chamber(
        At, contraction_ratio, l_star, pc, sigma_allow, safety,
        q_throat_W_m2=_q_throat_W,
        coolant_pressure_pa=_p_coolant,
        k_liner_WmK=_k_liner,
        sigma_jacket_pa=_sigma_jacket,
        channel_height_mm=_ch_h_mm,
        n_channels=_n_ch, channel_width_mm=_ch_w_mm,
        conv_half_angle_deg=theta_conv_deg,
        liner_thickness_mm=float(getattr(inputs, "cooling_wall_thickness_m", 0.5e-3)) * 1000.,
        cooling_profile=cooling_profile,
    )
    rc = chamber.radius_m

    # ── 喷管轮廓 ──────────────────────────────────────
    nozzle = compute_nozzle(
        rt, re, rc, eps, contraction_ratio, chamber.length_m,
        bell_fraction=bell_fraction,
        theta_conv_deg=theta_conv_deg,
        wall_thickness_mm=chamber.wall_thickness_mm,  # 同 chamber 总壁厚
        nozzle_contour_method=method, nozzle_initial_angle_deg=theta_n, nozzle_exit_angle_deg=theta_e,
    )

    if cooling_profile is not None and cg.get("full_nodes"):
        # Display/export the exact saved computational mesh, including uncooled
        # extension. Never redraw existing fixed hardware from mutable inputs.
        stations = [row for row in cg["full_nodes"] if row["x_m"] >= cg["L_cyl_m"] - 1e-12]
        nozzle.contour_x = [row["x_m"] - cg["x_throat_m"] for row in stations]
        nozzle.contour_r = [row["r_m"] for row in stations]
        nozzle.num_contour_points = len(stations)
        nozzle.nozzle_length_m = cg["full_exit_x_m"] - cg["x_throat_m"]
        nozzle.convergent_length_m = cg["L_conv_m"]
        nozzle.total_length_m = cg["full_exit_x_m"]
        nozzle.throat_axial_position_m = cg["x_throat_m"]
        nozzle.throat_upstream_radius_m = cg["throat_upstream_radius_m"]
        nozzle.throat_downstream_radius_m = cg["throat_downstream_radius_m"]
        nozzle.inner_surface_area_m2 = sum(math.pi * (a["r_m"] + b["r_m"]) *
            math.hypot(b["x_m"]-a["x_m"], b["r_m"]-a["r_m"]) for a,b in zip(stations, stations[1:]))

    # ── 喷管过膨胀/分离/侧载校核(p_sep/p_a≈0.4 判据) ──
    _noz_out = comps["nozzle"].outputs
    _pe = float(_noz_out.get("p_exit_Pa", 0.0) or 0.0)
    _p_amb = float(_noz_out.get("p_amb_Pa", 101325.0) or 101325.0)
    _gamma = float(
        _noz_out.get("gamma", _noz_out.get("gamma_exit",
                     getattr(inputs, "gamma", 1.20))) or 1.20
    )
    nozzle_flow = analyze_nozzle_flow(nozzle, pc, _pe, _p_amb, _gamma)

    # ── 喷注器 ────────────────────────────────────────
    # 复用本次主喷注器部件结果；桌面几何不再按流量重新估算单元数量。
    # 历史结果没有保存部件时显示缺失，不根据当前输入补造。
    injector_component = comps.get("injector")
    injector = compute_injector(
        rc, s.get("mdot_chamber_kg_s") or s["mdot_total_kg_s"],
        component_outputs=getattr(injector_component, "outputs", None),
        component_profile=getattr(injector_component, "profile", None),
    )

    # ── 涡轮泵 ────────────────────────────────────────
    ox_pump_data = comps["ox_pump"]
    fuel_pump_data = comps["fuel_pump"]
    # V20：GG（燃气发生器循环）没有双预燃室——把唯一的富燃燃气发生器映射到
    # 两个预燃室栏位（几何数据结构保持不变；GUI 侧已加提示说明）。
    _gg_data = comps.get("gas_generator")
    ox_pb_data = comps.get("ox_preburner") or _gg_data
    fuel_pb_data = comps.get("fuel_preburner") or _gg_data
    if ox_pb_data is None or fuel_pb_data is None:
        raise KeyError("compute_geometry：既无 ox/fuel_preburner 也无 gas_generator 组件")
    # 涡轮参数从 *_turbine 组件读取，预燃室本体从 ox/fuel_preburner(或 GG) 读取。
    ox_turb_data = (comps.get("ox_preburner_turbine")
                    or comps.get("gg_turbine_ox")
                    or comps.get("gg_turbine")
                    or ox_pb_data)
    fuel_turb_data = (comps.get("fuel_preburner_turbine")
                      or comps.get("gg_turbine_fuel")
                      or comps.get("gg_turbine")
                      or fuel_pb_data)

    dp_ox = ox_pump_data.outputs["delta_p_Pa"]
    dp_fuel = fuel_pump_data.outputs["delta_p_Pa"]
    rho_ox = ox_pump_data.outputs["rho_kgm3"]
    rho_fuel = fuel_pump_data.outputs["rho_kgm3"]

    mdot_ox = s["mdot_ox_kg_s"]
    mdot_fuel = s["mdot_fuel_kg_s"]

    # 泵几何用 *实际生效* 的转速（从 ComponentResult 取）
    n_ox_rpm = float(ox_pump_data.outputs.get("nr_rpm", inputs.lox_pump_speed_rpm_override or 25000.0))
    n_fuel_rpm = float(fuel_pump_data.outputs.get("nr_rpm", inputs.fuel_pump_speed_rpm_override or 30000.0))
    n_ox_stages = int(ox_pump_data.outputs.get("n_stages", inputs.lox_pump_n_stages))
    n_fuel_stages = int(fuel_pump_data.outputs.get("n_stages", inputs.fuel_pump_n_stages))

    ox_pump = estimate_pump(
        "ox_pump", "LOX", mdot_ox, rho_ox, dp_ox,
        ox_pump_data.outputs["power_W"],
        shaft_speed_rpm=n_ox_rpm,
        n_stages=n_ox_stages,
        psi=ox_pump_data.outputs.get("psi_head_coefficient", inputs.lox_pump_head_coefficient_psi),
        tip_speed_limit_ms=inputs.pump_tip_speed_limit_ms,
    )

    fuel_pump = estimate_pump(
        "fuel_pump", "CH4", mdot_fuel, rho_fuel, dp_fuel,
        fuel_pump_data.outputs["power_W"],
        shaft_speed_rpm=n_fuel_rpm,
        n_stages=n_fuel_stages,
        psi=fuel_pump_data.outputs.get("psi_head_coefficient", inputs.fuel_pump_head_coefficient_psi),
        tip_speed_limit_ms=inputs.pump_tip_speed_limit_ms,
    )

    # 涡轮：优先采用循环求解后的预燃室压力比
    ox_preburner_pr = s.get("ox_preburner_pressure_ratio", inputs.ox_preburner_pressure_ratio)
    fuel_preburner_pr = s.get("fuel_preburner_pressure_ratio", inputs.fuel_preburner_pressure_ratio)

    ox_turbine = estimate_turbine(
        name="ox_turbine",
        shaft_speed_rpm=n_ox_rpm,                            # ← 与同侧泵共轴
        power=ox_turb_data.outputs["turbine_power_W"],
        mdot=ox_pb_data.outputs["mdot_total_kgs"],
        delta_h=ox_turb_data.outputs.get("isentropic_drop_Jkg", ox_turb_data.outputs.get("actual_drop_Jkg", 1.0e5)),
        p_in=ox_turb_data.outputs.get("p_in_Pa", ox_preburner_pr * pc),
        T_in=ox_turb_data.outputs.get("T_in_K", ox_pb_data.outputs["T_combustion_K"]),
        u_over_c_target=inputs.turb_ox_u_over_c_target,
        n_stages_user=inputs.turb_ox_n_stages_user,
        cp_gas=ox_turb_data.outputs.get("cp_in_Jkgk"),
        gamma_gas=ox_turb_data.outputs.get("gamma_in"),
        molar_mass_gas=ox_turb_data.outputs.get("molar_mass_in"),
        u_max_ms=inputs.turb_ox_u_max_ms,
    )

    fuel_turbine = estimate_turbine(
        name="fuel_turbine",
        shaft_speed_rpm=n_fuel_rpm,                          # ← 与同侧泵共轴
        power=fuel_turb_data.outputs["turbine_power_W"],
        mdot=fuel_pb_data.outputs["mdot_total_kgs"],
        delta_h=fuel_turb_data.outputs.get("isentropic_drop_Jkg", fuel_turb_data.outputs.get("actual_drop_Jkg", 1.0e5)),
        p_in=fuel_turb_data.outputs.get("p_in_Pa", fuel_preburner_pr * pc),
        T_in=fuel_turb_data.outputs.get("T_in_K", fuel_pb_data.outputs["T_combustion_K"]),
        u_over_c_target=inputs.turb_fuel_u_over_c_target,
        n_stages_user=inputs.turb_fuel_n_stages_user,
        cp_gas=fuel_turb_data.outputs.get("cp_in_Jkgk"),
        gamma_gas=fuel_turb_data.outputs.get("gamma_in"),
        molar_mass_gas=fuel_turb_data.outputs.get("molar_mass_in"),
        u_max_ms=inputs.turb_fuel_u_max_ms,
    )

    # ── 预燃室 ────────────────────────────────────────
    ox_preburner = compute_preburner(
        "ox_preburner",
        ox_pb_data.outputs["mdot_total_kgs"],
        ox_preburner_pr * pc,
    )
    fuel_preburner = compute_preburner(
        "fuel_preburner",
        fuel_pb_data.outputs["mdot_total_kgs"],
        fuel_preburner_pr * pc,
    )

    # ── 总体尺寸 ──────────────────────────────────────
    overall_length = nozzle.total_length_m
    overall_diameter = nozzle.exit_diameter_m

    # 干重估算（T/W ~ 180）
    thrust = s["thrust_N"]
    dry_mass = thrust / (180.0 * 9.80665)

    return EngineGeometry(
        chamber=chamber,
        nozzle=nozzle,
        injector=injector,
        ox_pump=ox_pump,
        fuel_pump=fuel_pump,
        ox_turbine=ox_turbine,
        fuel_turbine=fuel_turbine,
        ox_preburner=ox_preburner,
        fuel_preburner=fuel_preburner,
        nozzle_flow=nozzle_flow,
        overall_length_m=overall_length,
        overall_max_diameter_m=overall_diameter,
        dry_mass_estimate_kg=dry_mass,
    )


def geometry_to_dict(geom: EngineGeometry) -> dict:
    """将 EngineGeometry 转为可序列化字典（供 GUI 使用）"""
    return asdict(geom)
