"""Frozen ideal-mixture quasi-1D flow with spatial wall heat removal.

The coolant mesh supplies conservative cell heat. PCHIP interpolation of log
area and cumulative heat defines the continuous gas model. A regular sonic
station joins two DOP853 integrations; pressure follows mass conservation and
the inlet reservoir entropy. This is a system model, not CFD validation.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq
from core import ComponentResult, G0
from task_control import checkpoint


class _GasTrialDomainError(ValueError):
    """A Runge-Kutta stage left the state domain; retry with a shorter step."""


def heated_nozzle(chamber, mdot, pc, expansion_ratio, p_amb, circuit):
    from thermo import _gas_for_state, _dict_to_Y, gas_temperature_applicability
    import cantera as ct
    if not all(math.isfinite(v) and v > 0 for v in (mdot, pc, expansion_ratio)):
        raise ValueError("热侧质量流量、室压和面积比必须为有限正数。")
    if not math.isfinite(p_amb) or p_amb < 0:
        raise ValueError("环境压力必须为有限非负数。")
    gas = _gas_for_state(chamber)
    if gas.thermo_model != "ideal-gas":
        raise ValueError("本准一维热侧仅实现冻结组成理想气体混合物。")
    gas.TPY = chamber.temperature, pc, _dict_to_Y(gas, chamber.composition)
    h0, s0, R = gas.enthalpy_mass, gas.entropy_mass, ct.gas_constant/gas.mean_molecular_weight
    Y = gas.Y.copy()
    geometry = circuit["geometry"]
    cells = sorted((c for c in circuit["cells"] if c.get("kind", "channel") == "channel"), key=lambda c: c["x_m"])
    gn = sorted(geometry["nodes"], key=lambda n: n["x_m"])
    if not cells or len(gn) != len(cells)+1:
        raise ValueError("热侧与冷却单元边界数量不一致。")
    throat_x, rt = geometry["x_throat_m"], geometry["r_t_m"]
    x = [n["x_m"] for n in gn]
    areas = [(n["r_m"]/rt)**2 for n in gn]
    cumulative = [0.]
    for i, c in enumerate(cells):
        q = float(c.get("Q_W", c.get("dQ_W", 0.)))
        if not math.isfinite(q) or q < 0:
            raise ValueError("当前热侧跨声速分支只支持非负壁面热损；反向加热需独立分支处理。")
        if not x[i] < c["x_m"] < x[i+1]:
            raise ValueError("热流单元空间位置与几何边界不一致。")
        cumulative.append(cumulative[-1]+q)
    for node in sorted(geometry.get("full_nodes", gn), key=lambda n: n["x_m"]):
        if node["x_m"] > x[-1]+1e-10:
            x.append(node["x_m"])
            areas.append((node["r_m"]/rt)**2)
            cumulative.append(cumulative[-1])
    base_x = np.asarray(x, dtype=float)
    areas = np.asarray(areas, dtype=float)
    if not np.all(np.isfinite(base_x)) or np.any(np.diff(base_x) <= 0):
        raise ValueError("热侧几何边界必须严格递增。")
    geom_th = int(np.argmin(areas))
    if (not 0 < geom_th < len(areas)-1 or abs(areas[geom_th]-1) > 1e-8
            or abs(base_x[geom_th]-throat_x) > 1e-10
            or abs(areas[-1]/expansion_ratio-1) > 1e-8):
        raise ValueError("热侧几何必须包含精确喉部及与指定膨胀比一致的喷管出口。")
    log_area = PchipInterpolator(base_x, np.log(areas))
    heat_curve = PchipInterpolator(base_x, cumulative)
    area_prime, area_second = log_area.derivative(), log_area.derivative(2)
    heat_prime, heat_second = heat_curve.derivative(), heat_curve.derivative(2)
    tmin = max(float(gas.min_temp), 300.)

    def sonic_temperature(position):
        target = h0-float(heat_curve(position))/mdot
        def energy(t):
            gas.TPY = t, pc, Y
            cp = gas.cp_mass
            return gas.enthalpy_mass+.5*cp/(cp-R)*R*t-target
        return brentq(energy, tmin, chamber.temperature, xtol=1e-8)

    def regularity(position):
        t = sonic_temperature(position)
        gas.TPY = t, pc, Y
        return float(area_prime(position))+float(heat_prime(position))/(mdot*gas.cp_mass*t)

    critical_x = throat_x
    if cumulative[-1] > 0:
        samples = sorted(set([*base_x[:geom_th+1], *np.linspace(base_x[0], throat_x, 200)]))
        candidates = []
        values = [regularity(v) for v in samples]
        for a, b, fa, fb in zip(samples, samples[1:], values, values[1:]):
            if fa < 0 and fb >= 0:
                candidates.append(brentq(regularity, a, b, xtol=1e-12))
        if len(candidates) != 1:
            raise ValueError("有热流喷管没有唯一光滑跨声速临界位置。")
        critical_x = candidates[0]
    tc = sonic_temperature(critical_x)
    gas.TPY = tc, pc, Y
    cp_c = gas.cp_mass
    gamma_c = cp_c/(cp_c-R)
    us = math.sqrt(gamma_c*R*tc)
    dt = max(.01, tc*1e-5)
    gas.TPY = tc+dt, pc, Y
    cp_plus = gas.cp_mass
    gas.TPY = tc-dt, pc, Y
    cp_derivative = (cp_plus-gas.cp_mass)/(2*dt)
    B = 1/tc - R*cp_derivative/(cp_c*(cp_c-R))
    H = float(heat_prime(critical_x))/(mdot*cp_c)
    K = us*us/cp_c
    C = H/tc*(cp_derivative/cp_c+1/tc)

    def critical_slope(side):
        # PCHIP is C1: a knot may have different one-sided second derivatives.
        pos = np.nextafter(critical_x, -np.inf if side < 0 else np.inf)
        J = float(area_second(pos))+float(heat_second(pos))/(mdot*cp_c*tc)
        aa, bb, cc = 2+B*K, B*H-C*K, -(J+C*H)
        discriminant = bb*bb-4*aa*cc
        if not math.isfinite(discriminant) or discriminant <= 0:
            raise ValueError("声速正则性方程无可分辨实斜率。")
        slopes = [(-bb+sign*math.sqrt(discriminant))/(2*aa) for sign in (-1, 1)]
        valid = [w for w in slopes if math.isfinite(w) and aa*w+B*H > 0]
        if len(valid) != 1:
            raise ValueError("声速点不存在唯一加速跨声速分支。")
        return valid[0]

    left_slope, right_slope = critical_slope(-1), critical_slope(1)
    gas.TPY = tmin, pc, Y
    hmin = gas.enthalpy_mass
    calls = 0
    accepted_steps = 0
    rejected_domain_attempts = 0

    def gas_state(position, velocity):
        total_h = h0-float(heat_curve(position))/mdot
        static_h = total_h-.5*velocity*velocity
        if not math.isfinite(velocity) or velocity <= 0 or static_h < hmin:
            raise _GasTrialDomainError(f"热侧速度或静焓超出当前单相温区模型：x={position:g} m, u={velocity:g} m/s, h={static_h:g} J/kg。")
        gas.HPY = static_h, pc, Y
        # HP inversion tolerances adequate for ordinary state tables are too
        # loose in M^2-1 near a sonic launch. Polish the scalar enthalpy solve.
        for _ in range(4):
            correction = (gas.enthalpy_mass-static_h)/gas.cp_mass
            if abs(correction) < 1e-11:
                break
            gas.TPY = gas.T-correction, pc, Y
        t, cp = gas.T, gas.cp_mass
        m2 = velocity*velocity/(cp/(cp-R)*R*t)
        return total_h, t, cp, m2

    def rhs(position, v):
        nonlocal calls
        checkpoint()
        calls += 1
        u = float(v[0])
        _, t, cp, m2 = gas_state(position, u)
        numerator = float(area_prime(position))+float(heat_prime(position))/(mdot*cp*t)
        if abs(m2-1) < 1e-14:
            raise ValueError("热侧积分再次触及未正则化的声速点。")
        return [u*numerator/(m2-1)]

    length = float(base_x[-1]-base_x[0])
    left_gap = critical_x-max(v for v in base_x if v < critical_x-1e-12)
    right_gap = min(v for v in base_x if v > critical_x+1e-12)-critical_x
    eps0 = min(length*1e-6, .05*left_gap, .05*right_gap)
    if eps0 < length*1e-11:
        raise ValueError("声速位置离网格边界过近，无法分辨双侧初始展开。")
    mesh = sorted(set([*base_x, critical_x]))

    def integrate(epsilon):
        def side(direction, slope):
            nonlocal accepted_steps, rejected_domain_attempts
            start = critical_x+direction*epsilon
            stops = sorted((v for v in base_x if direction*(v-start) > 0), reverse=direction < 0)
            u = us*math.exp(direction*slope*epsilon)
            segments = []
            for index, stop in enumerate(stops):
                # PCHIP has a discontinuous second derivative at knots. End
                # an integration segment there rather than hiding the kink
                # inside an otherwise smooth Runge-Kutta error estimate.
                width = abs(stop-start)
                for attempt in range(7):
                    try:
                        sol = solve_ivp(rhs, (start, stop), [u], method="DOP853", rtol=2e-11,
                            atol=1e-9, dense_output=True, max_step=width/(2*2**attempt),
                            first_step=min(epsilon/4, width/(4*2**attempt)) if index == 0 or attempt else None)
                        break
                    except _GasTrialDomainError:
                        # Reject an inadmissible RK stage, never clip velocity,
                        # enthalpy or temperature. Persistent domain failures
                        # remain failures after bounded step refinement.
                        rejected_domain_attempts += 1
                        if attempt == 6:
                            raise
                if not sol.success:
                    raise ValueError(f"热侧积分失败：{sol.message}")
                accepted_steps += len(sol.t)-1
                segments.append((min(start, stop), max(start, stop), sol.sol))
                u, start = float(sol.y[0, -1]), stop
            segments.sort(key=lambda v: v[0])
            ends = [v[1] for v in segments]
            def lookup(pos):
                idx = min(int(np.searchsorted(ends, pos)), len(segments)-1)
                return float(segments[idx][2](pos)[0])
            return lookup
        left, right = side(-1, left_slope), side(1, right_slope)
        def velocity(pos):
            if pos < critical_x-epsilon:
                return left(pos)
            if pos > critical_x+epsilon:
                return right(pos)
            slope = left_slope if pos < critical_x else right_slope
            return us*math.exp(slope*(pos-critical_x))
        u0 = velocity(base_x[0])
        _, t0, _, _ = gas_state(base_x[0], u0)
        inlet_p = pc*math.exp((gas.entropy_mass-s0)/R)
        At = float(mdot*R*t0/(u0*inlet_p*areas[0]))
        return velocity, At

    old_velocity, old_area = integrate(eps0)
    velocity, At = integrate(eps0/2)
    launch_error = max(abs(velocity(v)/old_velocity(v)-1) for v in mesh)
    launch_error = max(launch_error, abs(At/old_area-1))
    if not math.isfinite(launch_error) or launch_error > 2e-7:
        raise ValueError(f"声速点双侧展开未通过步长敏感性检查：{launch_error:g}。")
    nodes = []
    for pos in mesh:
        u = velocity(pos)
        ht, t, cp, m2 = gas_state(pos, u)
        ar = float(math.exp(log_area(pos)))
        p = mdot*R*t/(u*At*ar)
        gas.TPY = t, p, Y
        if gas_temperature_applicability(gas)["status"] == "extrapolated":
            raise ValueError("热侧节点超出所选燃气热化学温区。")
        nodes.append(dict(x_m=float(pos), area_ratio=ar, T_K=t, p_Pa=p,
            h_Jkg=float(gas.enthalpy_mass), h0_Jkg=ht, velocity_ms=u,
            M=math.sqrt(m2), cp_JkgK=cp, mu_Pas=float(gas.viscosity),
            Pr=float(gas.viscosity*cp/gas.thermal_conductivity)))
    if any(n["M"] >= 1 for n in nodes if n["x_m"] < critical_x-1e-10) or any(
            n["M"] <= 1 for n in nodes if n["x_m"] > critical_x+1e-10):
        raise ValueError("热侧积分进入错误的亚声速/超声速分支。")

    # Independent Gauss quadrature audits dp + integral((mdot/A) du).
    gauss_rules = [np.polynomial.legendre.leggauss(n) for n in (8, 16)]
    def momentum_integrand(pos):
        u = velocity(pos)
        du = (u*(left_slope if pos < critical_x else right_slope)
              if abs(pos-critical_x) <= eps0/2 else rhs(pos, [u])[0])
        return mdot/(At*math.exp(float(log_area(pos))))*du
    def momentum_integral(xa, xb, depth=0):
        estimates = []
        for gx, gw in gauss_rules:
            positions = .5*(xa+xb)+.5*(xb-xa)*gx
            estimates.append(.5*(xb-xa)*float(np.dot(gw,
                [momentum_integrand(pos) for pos in positions])))
        if abs(estimates[1]-estimates[0]) <= max(.002, abs(estimates[1])*1e-9):
            return estimates[1]
        if depth >= 14:
            raise ValueError("热侧独立Gauss动量积分未达到求积容差。")
        middle = .5*(xa+xb)
        return momentum_integral(xa, middle, depth+1)+momentum_integral(middle, xb, depth+1)
    momentum_residuals = []
    for a, b in zip(nodes, nodes[1:]):
        cuts = sorted(set([a["x_m"], b["x_m"], *[p for p in
            (critical_x-eps0/2, critical_x+eps0/2) if a["x_m"] < p < b["x_m"]]]))
        integral = 0.
        for xa, xb in zip(cuts, cuts[1:]):
            integral += momentum_integral(xa, xb)
        momentum_residuals.append(b["p_Pa"]-a["p_Pa"]+integral)
    momentum_error = max(abs(v) for v in momentum_residuals)
    momentum_tolerance = max(.05, pc*2e-9)
    if momentum_error > momentum_tolerance:
        raise ValueError(f"热侧独立动量积分未通过：{momentum_error:g} Pa。")
    by_x = {n["x_m"]: n for n in nodes}
    hot_cells = []
    for i, c in enumerate(cells):
        a, b = by_x[float(base_x[i])], by_x[float(base_x[i+1])]
        values = {k: .5*(a[k]+b[k]) for k in ("T_K", "p_Pa", "M", "cp_JkgK", "mu_Pas", "Pr")}
        values["T_static_K"] = values.pop("T_K")
        # Recovery temperature uses frozen-mixture enthalpy, not constant cp.
        h_static = .5*(a["h_Jkg"]+b["h_Jkg"])
        kinetic = .25*(a["velocity_ms"]**2+b["velocity_ms"]**2)
        gas.HPY = h_static+values["Pr"]**(1/3)*kinetic, values["p_Pa"], Y
        values["T_aw_K"] = float(gas.T)
        gas.HPY = .5*(a["h0_Jkg"]+b["h0_Jkg"]), values["p_Pa"], Y
        values["T0_K"] = float(gas.T)
        values["gamma"] = values["cp_JkgK"]/(values["cp_JkgK"]-R)
        values["x_m"] = c["x_m"]
        hot_cells.append(values)
    exit_node = nodes[-1]
    ve, pe, Te = exit_node["velocity_ms"], exit_node["p_Pa"], exit_node["T_K"]
    Ae, Q = At*expansion_ratio, float(cumulative[-1])
    thrust_vac = mdot*ve+pe*Ae
    thrust = thrust_vac-p_amb*Ae
    geometric, sonic = by_x[float(throat_x)], by_x[float(critical_x)]
    energy = mdot*(exit_node["h_Jkg"]+.5*ve*ve-h0)+Q
    residual = max(momentum_error/pc, launch_error, abs(energy)/max(mdot*abs(h0), abs(Q), 1.))
    result = ComponentResult("nozzle", dict(thrust_N=thrust, Isp_s=thrust/(mdot*G0),
        thrust_vac_N=thrust_vac, Isp_vac_s=thrust_vac/(mdot*G0), Ve_ms=ve,
        c_star_ms=pc*At/mdot, Cf=thrust/(pc*At), Cf_sl=thrust/(pc*At), Cf_vac=thrust_vac/(pc*At),
        Cf_momentum=mdot*ve/(pc*At), Cf_pressure=(pe-p_amb)*expansion_ratio/pc,
        At_m2=At, Ae_m2=Ae, mach_exit=exit_node["M"], p_exit_Pa=pe, p_amb_Pa=p_amb,
        expansion_ratio=expansion_ratio, T_exit_K=Te, T_throat_K=geometric["T_K"],
        p_throat_Pa=geometric["p_Pa"], T_sonic_K=sonic["T_K"], p_sonic_Pa=sonic["p_Pa"],
        h_exit_Jkg=exit_node["h_Jkg"], h_chamber_Jkg=h0, heat_loss_W=Q,
        energy_residual_W=energy, flow_model=0., hot_side_residual=residual),
        ["冻结组成理想气体准一维能量/动量；按冷却单元位置扣热。",
         "主室输入温压按零速储室总态使用，沿程节点报告局部静态温压。",
         "跨声速临界位置与双侧斜率由面积变化及壁面热损的正则性条件求解。",
         "未建模激波、分离、有限速率反应及燃气壁面摩擦；高压输运与换热仍需独立验证。"])
    result.profile = dict(model_id="heated_frozen_quasi1d_v1", nodes=nodes, cells=hot_cells,
        max_scaled_residual=residual, iterations=accepted_steps, function_evaluations=calls, heat_loss_W=Q,
        rejected_domain_attempts=rejected_domain_attempts,
        critical_x_m=critical_x, geometric_throat_x_m=throat_x,
        critical_regularity_residual_per_m=regularity(critical_x),
        critical_left_log_velocity_slope_per_m=left_slope,
        critical_right_log_velocity_slope_per_m=right_slope,
        critical_launch_distance_m=eps0/2, critical_launch_relative_difference=launch_error,
        max_momentum_integral_residual_Pa=momentum_error,
        momentum_integral_tolerance_Pa=momentum_tolerance,
        momentum_cell_residuals_Pa=momentum_residuals,
        solver="piecewise DOP853; regular sonic launch; adaptive 8/16-point Gauss momentum audit",
        critical_location_model="conservative_pchip_heat_and_log_area_regularity",
        validation_status="system_model_requires_independent_validation")
    return result
