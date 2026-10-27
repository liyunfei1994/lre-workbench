"""Unique system-level quasi-1D regenerative coolant model.

One formal wall mesh, p/h coolant states, total energy, signed momentum,
explicit manifold losses; no clipping or independent postprocessing solve.
Heat-transfer correlations remain engineering assumptions, not Raptor data.
"""
from __future__ import annotations
from copy import deepcopy
import math
import numpy as np
from scipy.optimize import brentq, root
from core import ComponentResult
from cooling import _friction_factor, _bartz_gas_props
from fluid_properties import state_tp, state_ph
from regen_geometry import build_regen_geometry
from task_control import checkpoint

MODEL_ID = "regen_quasi1d_ph_v1"
BARTZ_GEOMETRY_ID = "bartz_upstream_contour_curvature_v2"
_SIGMA_SB = 5.670374419e-8


class CoolantMarchError(ValueError):
    """A cell's fluid domain or p/h equations could not be satisfied.

    Inlet, geometry, hot-state and wall-model input errors deliberately do not
    use this type. A caller may search a feasible pressure INITIAL GUESS for
    this error, but must still close the physical pressure/enthalpy equations.
    """
    def __init__(self, kind, s_m, x_m, reason):
        self.kind, self.s_m, self.x_m = kind, float(s_m), float(x_m)
        self.reason = str(reason)
        super().__init__(f"coolant {kind} failed at s={s_m:.6g} m, x={x_m:.6g} m: {reason}")


def _mach_from_area_ratio(AR, gamma, supersonic):
    if AR <= 1 + 1e-12:
        return 1.0
    def residual(m):
        return (2/(gamma+1)*(1+(gamma-1)*m*m/2))**((gamma+1)/(2*(gamma-1)))/m-AR
    return brentq(residual, 1.000000001 if supersonic else 1e-8,
                  100.0 if supersonic else 0.999999999)


def _bartz_sigma_local(gamma, T_w, T_c, M):
    half = 1 + (gamma-1)*M*M/2
    return (0.5*(T_w/T_c)*half+0.5)**(-0.68)*half**(-0.12)


def _hot_state(cell, *, gamma, T_c, pc, mu_g, cp_g, Pr_g, supplied=None):
    M = _mach_from_area_ratio(cell["area_ratio"], gamma, cell["supersonic"])
    ratio = 1 + (gamma-1)*M*M/2
    data = dict(M=M, T_static_K=T_c/ratio, p_Pa=pc*ratio**(-gamma/(gamma-1)),
        T_aw_K=T_c*(1+Pr_g**(1/3)*(ratio-1))/ratio,
        cp_JkgK=cp_g, mu_Pas=mu_g, Pr=Pr_g, T0_K=T_c, gamma=gamma)
    if supplied is not None:
        data.update(supplied)
    for key in ("T_static_K", "p_Pa", "T_aw_K", "cp_JkgK", "mu_Pas", "Pr", "T0_K", "gamma"):
        if not math.isfinite(float(data[key])) or data[key] <= 0:
            raise ValueError(f"invalid hot-side {key}")
    if not math.isfinite(data["M"]) or data["M"] < 0 or data["gamma"] <= 1:
        raise ValueError("invalid hot-side Mach number or gamma")
    return data


def _wall_heat(cell, bulk, hot, *, mdot_coolant, mdot_total, r_t, pc,
               wall_conductivity, k_wall_func, htc_enhancement, x_throat,
               gas_emissivity, heat_transfer_enabled, throat_upstream_radius_m,
               k_wall_integral=None):
    """Variable-k slab conduction and a declared representative-k rib model."""
    rho, mu, k, cp = (bulk[key] for key in ("rho_kgm3", "mu_Pas", "k_WmK", "cp_JkgK"))
    velocity = mdot_coolant/(rho*cell["A_flow_m2"])
    re = rho*velocity*cell["D_h_m"]/mu
    pr = cp*mu/k
    blend = min(max((re-2300)/700, 0), 1)
    nu = (1-blend)*4.36 + blend*0.023*re**0.8*pr**0.4
    hc_base = nu*k/cell["D_h_m"]
    enhancement = htc_enhancement if abs(cell["x_m"]-x_throat) < r_t else 1.0
    dt = 2*r_t
    if not math.isfinite(throat_upstream_radius_m) or throat_upstream_radius_m <= 0:
        raise ValueError("Bartz requires a positive actual upstream throat curvature radius")
    # Effective Bartz curvature = the canonical UPSTREAM contour arc. It is
    # neither the chamber radius r_c nor the downstream throat arc radius.
    curvature_factor = (dt/throat_upstream_radius_m)**0.1
    hg_base = (0.026/dt**0.2)*(hot["mu_Pas"]**0.2*hot["cp_JkgK"]/hot["Pr"]**0.6)*(mdot_total/(math.pi*r_t*r_t))**0.8*curvature_factor
    eps = gas_emissivity*min(hot["p_Pa"]/pc, 1.0)
    tb = bulk["T_K"]
    def conductivity(t):
        value = float(k_wall_func(t) if k_wall_func is not None else wall_conductivity)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("nonpositive wall conductivity")
        return value
    def conduction(a, b):
        if k_wall_integral is not None:
            return float(k_wall_integral(a, b))
        if k_wall_func is None:
            return wall_conductivity*(b-a)
        # Only compatibility callers supply an arbitrary function. Production
        # datasets have their own exact, piecewise analytic integral.
        from scipy.integrate import quad
        return quad(conductivity, a, b, epsabs=1e-6, epsrel=1e-9)[0]
    def gas_heat(tw):
        hg = hg_base*cell["area_ratio"]**(-0.9)*_bartz_sigma_local(hot["gamma"], tw, hot["T0_K"], hot["M"])
        qc = hg*(hot["T_aw_K"]-tw) if heat_transfer_enabled else 0.0
        qr = eps*_SIGMA_SB*(hot["T_static_K"]**4-tw**4) if heat_transfer_enabled else 0.0
        return hg, qc, qr
    def values(tw):
        tf = .5*(tb+tw)
        kfin = conductivity(tf)
        mh = math.sqrt(2*hc_base*enhancement/(kfin*cell["rib_m"]))*cell["height_m"]
        eta = math.tanh(mh)/mh if mh else 1.0
        fin = (cell["width_m"]+2*eta*cell["height_m"])/cell["pitch_m"]
        hc = hc_base*fin*enhancement
        hg, qc, qr = gas_heat(tw)
        q = qc+qr
        tc = tb+q/hc
        potential = conduction(tc, tw)
        kw = potential/(tw-tc) if abs(tw-tc) > 1e-8 else conductivity(.5*(tw+tc))
        integral_res = potential-q*cell["wall_thickness_m"]
        res = integral_res/kw
        return res, dict(q_W_m2=q, q_conv_W_m2=qc, q_rad_W_m2=qr,
            T_wall_hot_K=tw, T_wall_cold_K=tc, h_g_W_m2K=hg, h_c_W_m2K=hc,
            wall_conductivity_WmK=kw, wall_residual_K=res, Re=re, Pr=pr, fin_efficiency=eta,
            wall_conduction_residual_W_m=integral_res, fin_conductivity_WmK=kfin,
            fin_reference_temperature_K=tf)
    if not heat_transfer_enabled:
        return values(tb)[1]
    lo, hi = tb, max(tb, hot["T_aw_K"], hot["T_static_K"])
    def net_heat(t):
        _, conv, rad = gas_heat(t)
        return conv+rad
    if net_heat(lo) < -1e-6:
        raise ValueError("reverse wall heat flow is outside this regenerative heating model")
    # Bound wall trials by zero net gas heat. This keeps the inferred cold face
    # positive even when radiation is negative near the adiabatic wall state.
    if hi > lo and net_heat(hi) < 0:
        hi = brentq(net_heat, lo, hi, xtol=1e-10)
    if hi-lo < 1e-10:
        return values(tb)[1]
    tw, info = brentq(lambda t: values(t)[0], lo, hi, xtol=1e-8, rtol=1e-12, maxiter=100, full_output=True)
    if not info.converged:
        raise ValueError("cooling wall balance did not converge")
    output = values(tw)[1]
    output["wall_iterations"] = info.iterations
    if abs(output["wall_residual_K"]) > 1e-5:
        raise ValueError("cooling wall residual exceeded tolerance")
    return output


def _fluid_node(raw, geom, mdot, offset, *, kind="channel"):
    area = geom.get("A_flow_m2")
    velocity = mdot/(raw["rho_kgm3"]*area) if area is not None else 0.0
    mach = velocity/raw["sound_speed_ms"]
    if not math.isfinite(mach) or mach >= 1:
        raise ValueError("coolant sonic/choked state outside subsonic channel model")
    return dict(geom, kind=kind, p_Pa=raw["p_Pa"], T_K=raw["T_K"],
        h_Jkg=raw["h_Jkg"]+offset, h_HEOS_Jkg=raw["h_Jkg"],
        h0_Jkg=raw["h_Jkg"]+offset+velocity*velocity/2,
        rho_kgm3=raw["rho_kgm3"], cp_JkgK=raw["cp_JkgK"],
        velocity_ms=velocity, Mach=mach, phase=raw["phase"],
        range_status=raw["range_status"], entropy_JkgK=raw["s_JkgK"])


def march_regen_circuit(*, mdot_coolant, T_in, p_in, mdot_total, pc, T_c, gamma,
        molar_mass, r_t, r_c, L_cyl, expansion_ratio, L_conv=None, cover_eps=0.0,
        n_channels=300, channel_width_m=1.5e-3, channel_height_m=5e-3,
        wall_thickness_m=0.5e-3, wall_conductivity=320.0, htc_enhancement=1.0,
        k_wall_func=None, rel_roughness=0.003, k_minor=3.0, counterflow=True,
        channel_table=None, bell_fraction=0.8, film_fraction=0.0, film_T_K=850.0,
        film_eta0=0.9, film_decay_length_m=0.0, gas_emissivity=0.20,
        conv_half_angle_deg=30.0, wall_temp_limit_K=800.0, n_points=120,
        hot_side_profile=None, inlet_enthalpy_Jkg=None, k_minor_inlet=None,
        k_minor_outlet=None, heat_transfer_enabled=True, friction_enabled=True,
        strict=True, wall_thermal_dataset_id=None, wall_manufacturing_state="unspecified",
        wall_material_zones=None, remap_hot_side_trial=False,
        nozzle_contour_method="legacy_log_v1",
        nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0):
    """One conserved profile including both zero-speed cycle ports.

    hot_side_profile aligns with build_regen_geometry().cells in coolant order.
    inlet_enthalpy_Jkg uses the cycle chemical reference; its offset from HEOS
    is carried by every h/h0. The channel uses h0=h+u^2/2; large manifolds have
    u=0 so the cycle-port static-enthalpy ledger is exactly the heat balance.
    Minor losses reference the channel speed adjacent to each manifold.
    """
    checkpoint()
    from wall_thermal import (parse_material_zones, validate_manufacturing_state,
                              material_for_cell, audit_cell, material_summary)
    from wall_materials import get_wall_dataset
    zones = parse_material_zones(wall_material_zones)
    validate_manufacturing_state(wall_manufacturing_state)
    if wall_thermal_dataset_id is not None:
        get_wall_dataset(wall_thermal_dataset_id)
        if k_wall_func is not None:
            raise ValueError("Choose a wall dataset or a custom conductivity function, not both")
    elif zones:
        raise ValueError("Material zones require a default wall dataset")
    boundaries = sorted({z[key] for z in zones for key in ("x_start_m", "x_end_m")})
    if strict is not True:
        raise ValueError("non-strict coolant fallback is not supported")
    for name, value in (("mdot_coolant",mdot_coolant),("mdot_total",mdot_total),
            ("pc",pc),("T_c",T_c),("molar_mass",molar_mass),
            ("wall_conductivity",wall_conductivity),("htc_enhancement",htc_enhancement)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not math.isfinite(gamma) or gamma <= 1:
        raise ValueError("gamma must exceed one")
    if not math.isfinite(rel_roughness) or not 0 <= rel_roughness < 1:
        raise ValueError("invalid relative roughness")
    if not math.isfinite(gas_emissivity) or not 0 <= gas_emissivity <= 1:
        raise ValueError("gas emissivity must lie in [0,1]")
    if film_fraction != 0:
        raise ValueError("film cooling requires an explicit mass/enthalpy branch; legacy film switch is unsupported")
    kin = k_minor/2 if k_minor_inlet is None else k_minor_inlet
    kout = k_minor/2 if k_minor_outlet is None else k_minor_outlet
    if not all(math.isfinite(v) and v >= 0 for v in (k_minor,kin,kout)):
        raise ValueError("minor loss coefficients must be finite and nonnegative")
    geom = build_regen_geometry(r_t=r_t,r_c=r_c,L_cyl=L_cyl,expansion_ratio=expansion_ratio,
        cover_eps=cover_eps,bell_fraction=bell_fraction,conv_half_angle_deg=conv_half_angle_deg,
        n_points=n_points,counterflow=counterflow,n_channels=n_channels,
        channel_width_m=channel_width_m,channel_height_m=channel_height_m,
        wall_thickness_m=wall_thickness_m,channel_table=channel_table,
        material_boundaries_m=boundaries, nozzle_contour_method=nozzle_contour_method,
        nozzle_initial_angle_deg=nozzle_initial_angle_deg, nozzle_exit_angle_deg=nozzle_exit_angle_deg)
    # Legacy callers may supply a length, but cannot define a second contour.
    if L_conv is not None and (not math.isfinite(L_conv) or
            not math.isclose(L_conv, geom["L_conv_m"], rel_tol=1e-8, abs_tol=1e-10)):
        raise ValueError("L_conv conflicts with the canonical convergent contour")
    if hot_side_profile is not None and remap_hot_side_trial:
        # Only an iterative guess is remapped when sizing moves the contour
        # relative to fixed axial material interfaces. The final hot solution is
        # recomputed on this mesh and all coupling criteria must still pass.
        keys = ("T_static_K", "T_aw_K", "T0_K", "p_Pa", "M", "cp_JkgK", "mu_Pas", "Pr", "gamma")
        source = sorted(hot_side_profile, key=lambda row: row["x_m"])
        source_x = [row["x_m"] for row in source]
        if not source or any(not math.isfinite(x) for x in source_x) or any(a >= b for a,b in zip(source_x,source_x[1:])):
            raise ValueError("Invalid physical coordinates for hot-side trial remapping")
        hot_side_profile = [dict(x_m=cell["x_m"], **{key: float(np.interp(cell["x_m"], source_x,
            [row[key] for row in source])) for key in keys}) for cell in geom["cells"]]
    if hot_side_profile is not None and len(hot_side_profile) != len(geom["cells"]):
        raise ValueError("hot-side states must align exactly with common geometric cells")
    inlet = state_tp("CH4",T_in,p_in,transport=True)
    if inlet_enthalpy_Jkg is None:
        from thermo import reactant_enthalpy
        inlet_enthalpy_Jkg = reactant_enthalpy("CH4",T_in,p_in)
    if not math.isfinite(inlet_enthalpy_Jkg):
        raise ValueError("nonfinite inlet chemical enthalpy")
    offset = inlet_enthalpy_Jkg-inlet["h_Jkg"]
    port_geom = {**geom["nodes"][0],"A_flow_m2":None}
    nodes = [_fluid_node(inlet,port_geom,mdot_coolant,offset,kind="inlet_port")]
    cells = []
    mu_g,cp_g,pr_g = _bartz_gas_props(gamma,8314.462618/molar_mass,T_c)

    def advance(out_geom,cell,hot=None,*,kind="channel",loss_k=0.0):
        checkpoint()
        previous = nodes[-1]
        pscale = max(previous["p_Pa"],1e6)
        hscale = max(abs(previous["h_HEOS_Jkg"]),1e5)
        data = None
        wall_dataset, declared_state = ((None, wall_manufacturing_state)
            if kind != "channel" or wall_thermal_dataset_id is None else
            material_for_cell(cell["x_m"], wall_thermal_dataset_id, wall_manufacturing_state, zones))
        def checked_cell_state(p, h):
            try:
                return state_ph("CH4",p,h,transport=True)
            except ValueError as exc:
                raise CoolantMarchError(kind,cell["s_m"],cell["x_m"],exc) from exc
        def residual(z):
            nonlocal data
            pout,hout = float(z[0]*pscale),float(z[1]*hscale)
            end_raw = checked_cell_state(pout,hout)
            try:
                end = _fluid_node(end_raw,out_geom,mdot_coolant,offset,
                    kind="outlet_port" if kind == "outlet_manifold" else "channel")
            except ValueError as exc:
                raise CoolantMarchError(kind,cell["s_m"],cell["x_m"],exc) from exc
            u0,u1 = previous["velocity_ms"],end["velocity_ms"]
            g0 = mdot_coolant/previous["A_flow_m2"] if previous["A_flow_m2"] is not None else 0.0
            g1 = mdot_coolant/out_geom["A_flow_m2"] if out_geom["A_flow_m2"] is not None else 0.0
            # Integral rho*u*du: exact incompressible area-change pressure recovery.
            dp_momentum = 0.5*(g0+g1)*(u1-u0)
            thermal = dict(q_W_m2=0.,q_conv_W_m2=0.,q_rad_W_m2=0.,
                T_wall_hot_K=None,T_wall_cold_K=None,wall_residual_K=0.)
            dp_friction = heat = 0.0
            if kind == "channel":
                bulk = checked_cell_state(0.5*(previous["p_Pa"]+pout),
                    0.5*(previous["h_HEOS_Jkg"]+hout))
                velocity = mdot_coolant/(bulk["rho_kgm3"]*cell["A_flow_m2"])
                re = bulk["rho_kgm3"]*velocity*cell["D_h_m"]/bulk["mu_Pas"]
                f = _friction_factor(re,rel_roughness) if friction_enabled else 0.0
                if not math.isfinite(f) or f < 0:
                    raise ValueError("invalid Darcy friction factor")
                dp_friction = f*cell["ds_m"]/cell["D_h_m"]*0.5*bulk["rho_kgm3"]*velocity**2
                thermal = _wall_heat(cell,bulk,hot,mdot_coolant=mdot_coolant,
                    mdot_total=mdot_total,r_t=r_t,pc=pc,wall_conductivity=wall_conductivity,
                    k_wall_func=wall_dataset.conductivity if wall_dataset else k_wall_func,
                    k_wall_integral=wall_dataset.conductivity_integral if wall_dataset else None,
                    htc_enhancement=htc_enhancement,
                    x_throat=geom["x_throat_m"],gas_emissivity=gas_emissivity,
                    heat_transfer_enabled=heat_transfer_enabled,
                    throat_upstream_radius_m=geom["throat_upstream_radius_m"])
                thermal.update(friction_factor=f,T_coolant_K=bulk["T_K"],
                    rho_coolant_kgm3=bulk["rho_kgm3"],velocity_ms=velocity,p_coolant_Pa=bulk["p_Pa"])
                heat = thermal["q_W_m2"]*cell["dA_hot_m2"]
            dynamic_pressure = (0.5*end["rho_kgm3"]*u1*u1 if kind == "inlet_manifold"
                else 0.5*previous["rho_kgm3"]*u0*u0)
            dp_local = loss_k*dynamic_pressure
            re_p = pout-previous["p_Pa"]+dp_momentum+dp_friction+dp_local
            re_h = end["h0_Jkg"]-previous["h0_Jkg"]-heat/mdot_coolant
            data = end,dict(cell,**thermal,kind=kind,Q_W=heat,dp_friction_Pa=dp_friction,
                dp_momentum_Pa=dp_momentum,dp_local_Pa=dp_local,local_loss_K=loss_k,
                energy_residual_W=mdot_coolant*re_h,momentum_residual_Pa=re_p)
            if hot is not None:
                data[1]["hot_side"] = hot
            return np.array((re_p/pscale,re_h/hscale))
        guess = np.array((previous["p_Pa"]/pscale,previous["h_HEOS_Jkg"]/hscale))
        try:
            answer = root(residual,guess,method="hybr",options={"xtol":1e-9,"maxfev":100})
            residual(answer.x)
        except CoolantMarchError:
            raise
        except Exception as exc:
            raise ValueError(f"coolant {kind} failed at s={cell['s_m']:.6g} m: {exc}") from exc
        end,solved = data
        if abs(solved["momentum_residual_Pa"]) > .5 or abs(solved["energy_residual_W"]) > max(.02,abs(solved["Q_W"])*1e-8):
            raise CoolantMarchError(kind,cell["s_m"],cell["x_m"],f"p/h equations did not close: {answer.message}")
        solved["function_evaluations"] = int(answer.nfev)
        if wall_dataset is not None:
            solved["material"] = audit_cell(solved, wall_dataset, declared_state)
            solved["material"]["cell_index"] = len(cells)-1
        nodes.append(end)
        cells.append(solved)

    first = geom["nodes"][0]
    advance(first,dict(x_m=first["x_m"],s_m=0.,ds_m=0.,dA_hot_m2=0.),kind="inlet_manifold",loss_k=kin)
    for i,cell in enumerate(geom["cells"]):
        hot = _hot_state(cell,gamma=gamma,T_c=T_c,pc=pc,mu_g=mu_g,cp_g=cp_g,Pr_g=pr_g,
            supplied=hot_side_profile[i] if hot_side_profile is not None else None)
        advance(geom["nodes"][i+1],cell,hot)
    last = geom["nodes"][-1]
    advance({**last,"A_flow_m2":None},dict(x_m=last["x_m"],s_m=last["s_m"],ds_m=0.,dA_hot_m2=0.),
        kind="outlet_manifold",loss_k=kout)
    channels = [c for c in cells if c["kind"] == "channel"]
    qtotal = sum(c["Q_W"] for c in cells)
    dp = nodes[0]["p_Pa"]-nodes[-1]["p_Pa"]
    balance = dict(energy_residual_W=mdot_coolant*(nodes[-1]["h_Jkg"]-nodes[0]["h_Jkg"])-qtotal,
        max_cell_energy_residual_W=max(abs(c["energy_residual_W"]) for c in cells),
        max_cell_momentum_residual_Pa=max(abs(c["momentum_residual_Pa"]) for c in cells),
        max_wall_residual_K=max(abs(c["wall_residual_K"]) for c in cells),
        pressure_sum_residual_Pa=dp-sum(c["dp_friction_Pa"]+c["dp_momentum_Pa"]+c["dp_local_Pa"] for c in cells),
        mass_residual_kg_s=0.,definition="zero-speed manifold ports; total enthalpy inside channel")
    if abs(balance["energy_residual_W"]) > max(.1,abs(qtotal)*1e-7):
        raise ValueError("coolant overall energy ledger did not close")
    imax = max(channels,key=lambda c:c["T_wall_hot_K"])
    qmax = max(channels,key=lambda c:c["q_W_m2"])
    over = sum(c["T_wall_hot_K"] > wall_temp_limit_K for c in channels)
    warnings = []
    materials = material_summary(channels, wall_temp_limit_K)
    if materials["status"] == "out_of_range":
        warnings.append(f"壁面导热率数据越界：{materials['out_of_range_cell_count']}/{len(channels)} 个单元；端点常值延用仅保留诊断解，适用性未通过。")
    elif materials["status"] == "missing_evidence":
        warnings.append("壁面采用无数据集来源的导热率输入；材料适用性未评估。")
    if materials["manufacturing_match"] != "matched":
        warnings.append("壁面制造状态未声明或与所用热物性试样状态不匹配；不能据此验证硬件性能。")
    if over:
        warnings.append("部分热面壁温高于用户限值；热量闭合不代表壁温约束通过。")
    if any(2300 < c["Re"] < 3000 for c in channels):
        warnings.append("存在过渡雷诺数，换热采用连续插值，需独立验证。")
    throat = min(geom["nodes"], key=lambda n: abs(n["x_m"]-geom["x_throat_m"]))
    summary = dict(Q_total_W=qtotal,Q_rad_W=sum(c["q_rad_W_m2"]*c["dA_hot_m2"] for c in channels),
        T_out_K=nodes[-1]["T_K"],p_out_Pa=nodes[-1]["p_Pa"],h_out_Jkg=nodes[-1]["h_Jkg"],
        h0_out_Jkg=nodes[-1]["h0_Jkg"],dp_total_Pa=dp,
        dp_friction_Pa=sum(c["dp_friction_Pa"] for c in cells),
        dp_accel_Pa=sum(c["dp_momentum_Pa"] for c in cells),
        dp_minor_Pa=sum(c["dp_local_Pa"] for c in cells),
        T_wall_hot_max_K=imax["T_wall_hot_K"],T_wall_cold_max_K=max(c["T_wall_cold_K"] for c in channels),
        x_T_wall_max_m=imax["x_m"],x_q_max_m=qmax["x_m"],q_max_MW_m2=qmax["q_W_m2"]/1e6,
        wall_over_limit_fraction=over/len(channels),wall_temp_limit_K=wall_temp_limit_K,
        cover_eps=geom["cover_eps"],n_stations=len(channels),x_throat_m=geom["x_throat_m"],
        V_coolant_max_ms=max(n["velocity_ms"] for n in nodes),hydraulic_infeasible=False,
        pressure_floor_hit=False,film_on=False,film_mdot_kgs=0.,
        A_flow_throat_m2=throat["A_flow_m2"],
        dist_mode=bool(channel_table),t_liner_throat_m=throat["wall_thickness_m"],
        bartz_throat_curvature_radius_m=geom["throat_upstream_radius_m"],
        bartz_curvature_factor=(2*r_t/geom["throat_upstream_radius_m"])**0.1)
    summary["Q_rad_fraction"] = summary["Q_rad_W"]/qtotal if qtotal else 0.
    profile = dict(version=1,model_id=MODEL_ID,flow_direction=geom["flow_direction"],
        nodes=nodes,cells=cells,geometry=geom,summary=summary,balance=balance,
        model_methods=dict(version=1,heat_transfer_enabled=bool(heat_transfer_enabled),
            friction_enabled=bool(friction_enabled),
            hot_side=dict(id="bartz_local_transport_sigma_v1",label="Bartz 型对流与灰体辐射近似",
                detail="曲率取同一轮廓的上游喉部圆弧；采用所供热侧状态物性、面积幂与温度修正。物性参考态及修正组合尚待独立验证。"),
            wall=dict(id="integral_k_slab_representative_fin_v1",label="温变导热率积分与肋片修正",
                detail="热壁两侧之间积分 k(T)；肋片效率使用冷却剂主体与热面壁温均值处的导热率。冷侧换热按热面面积折算。"),
            cold_side=dict(id="bulk_db_transition_436_v1",label="主体物性 Dittus–Boelter 关联",
                detail="Re≤2300 使用 Nu=4.36，2300–3000 线性过渡，Re≥3000 使用 0.023 Re^0.8 Pr^0.4；叠加肋片效率及给定喉部冷侧修正。未建模局部换热恶化或结焦。"),
            hydraulics=dict(id="momentum_darcy_manifold_v1",label="一维动量、Darcy 摩擦与局部损失",
                detail="按沿程密度和流通面积计算速度与压力变化，计入摩擦及集流腔局部损失；入口、出口端口采用零速度集流腔。")),
        material_applicability=materials,
        material_assignment=dict(default_dataset_id=wall_thermal_dataset_id,
            default_manufacturing_state=wall_manufacturing_state,zones=zones,
            coordinate="axial_x_from_injector_m"),
        gas_heat_transfer=dict(geometry_model_id=BARTZ_GEOMETRY_ID,
            curvature_source="geometry.throat_upstream_radius_m",
            curvature_definition="effective upstream throat contour arc radius",
            throat_diameter_m=2*r_t,
            curvature_radius_m=geom["throat_upstream_radius_m"],
            curvature_factor=summary["bartz_curvature_factor"],
            transport_reference="supplied_hot_side_state" if hot_side_profile is not None else "chamber_reference_estimate",
            validation_status="geometry_consistent; transport_reference_and_sigma_require_validation"),
        validity=dict(status="review" if warnings else "pass",warnings=warnings,
            property_domain="within_HEOS_declared_single_phase_ranges",
            heat_transfer_validation="not_validated_for_hardware"),
        assumptions=["Zero-speed inlet/outlet manifolds; static port enthalpy equals total enthalpy.",
            "Trapezoidal 1D momentum; signed pressure recovery, Darcy friction and end-local K.",
            "Bulk-property coolant convection plus fin correction; no near-wall extrapolation.",
            "Wall conduction integrates k(T) between both faces; rib k uses mean bulk/hot-wall temperature.",
            "Single-phase CH4 without decomposition/deposition; wall limits are user assumptions.",
            "Bartz curvature uses the actual upstream throat arc; transport reference and sigma remain unvalidated.",
            "Supplied hot-side states." if hot_side_profile is not None else
            "Constant-gamma isentropic gas reference; chamber transport properties."])
    ordered = sorted(channels,key=lambda c:c["x_m"])
    aliases = {"x_m":"x_m","r_m":"r_m","AR":"area_ratio","q_W_m2":"q_W_m2",
        "T_wg_K":"T_wall_hot_K","T_wc_K":"T_wall_cold_K","T_co_K":"T_coolant_K",
        "p_co_Pa":"p_coolant_Pa","h_g_W_m2K":"h_g_W_m2K","h_c_W_m2K":"h_c_W_m2K",
        "V_co_ms":"velocity_ms","rho_co":"rho_coolant_kgm3","Re_co":"Re",
        "w_chan_m":"width_m","h_chan_m":"height_m","n_chan":"n_channels",
        "t_liner_arr_m":"wall_thickness_m","pitch_m":"pitch_m","rib_m":"rib_m",
        "q_conv_W_m2":"q_conv_W_m2","q_rad_W_m2":"q_rad_W_m2"}
    profile.update({key:[c[field] for c in ordered] for key,field in aliases.items()})
    profile.update(M=[c["hot_side"]["M"] for c in ordered],
        T_aw_K=[c["hot_side"]["T_aw_K"] for c in ordered],p_gas_Pa=[c["hot_side"]["p_Pa"] for c in ordered],
        film_eta=[0.]*len(ordered),x_throat_m=geom["x_throat_m"],t_liner_m=throat["wall_thickness_m"],
        dist_mode=bool(channel_table))
    return dict(profile,**summary,stations=channels,profile=profile)


def run_marching_postpass(results,inputs):
    """Compatibility bridge exposing the already solved main-cycle profile."""
    component = results.components.get("cooling_channel")
    profile = getattr(component,"profile",None) if component is not None else None
    if not isinstance(profile,dict) or profile.get("model_id") != MODEL_ID:
        return
    s = profile["summary"]
    output = dict(n_stations=s["n_stations"],Q_total_MW=s["Q_total_W"]/1e6,
        T_coolant_out_K=s["T_out_K"],delta_P_MPa=s["dp_total_Pa"]/1e6,
        T_wall_hot_max_K=s["T_wall_hot_max_K"],T_wall_cold_max_K=s["T_wall_cold_max_K"],
        hydraulic_infeasible=0.,vs_point_Q_rel_percent=0.,vs_point_T_out_delta_K=0.,vs_point_dp_delta_MPa=0.)
    bridge = ComponentResult("cooling_marching",outputs=output,
        notes=["复用主循环唯一准一维冷却解；未运行独立沿程后处理。"])
    bridge.profile = deepcopy(profile)
    results.components["cooling_marching"] = bridge
    results.summary.update(cooling_marching_Q_MW=s["Q_total_W"]/1e6,
        cooling_marching_dp_MPa=s["dp_total_Pa"]/1e6,
        cooling_marching_Twall_max_K=s["T_wall_hot_max_K"],cooling_marching_infeasible=0.)
