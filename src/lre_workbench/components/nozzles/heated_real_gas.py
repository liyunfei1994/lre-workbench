"""Frozen quasi-1D flow with one EOS for all local and stagnation states."""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from lre_workbench.components.nozzles.heated import _flow_geometry, _GasTrialDomainError
from lre_workbench.components.nozzles.model_ids import HEATED_IDEAL_SAME_EOS_MODEL, HEATED_REAL_GAS_MODEL
from lre_workbench.domain.models import ComponentResult, G0
from lre_workbench.properties.thermochemistry import _dict_to_Y, gas_temperature_applicability, set_frozen_hp
from lre_workbench.runtime.tasks import checkpoint


def _set_frozen_h_density(gas, enthalpy, density, bounds):
    """Invert enthalpy at the density required by the continuity equation."""
    low, high = bounds

    def residual(temperature):
        gas.TD = temperature, density
        return gas.enthalpy_mass - enthalpy

    temperature = min(max(gas.T, low), high)
    tolerance = max(1e-6, abs(enthalpy)*1e-12)
    for _ in range(8):
        error = residual(temperature)
        if abs(error) <= tolerance:
            return
        dp_dT = gas.thermal_expansion_coeff/gas.isothermal_compressibility
        slope = gas.cv_mass + dp_dT/density
        if not math.isfinite(slope) or slope <= 0:
            break
        next_temperature = temperature - error/slope
        if not low <= next_temperature <= high:
            break
        temperature = next_temperature
    temperature = brentq(residual, low, high, xtol=1e-10)
    if abs(residual(temperature)) > tolerance:
        raise ValueError("实气热侧定密度焓反算未满足能量目标。")


def _stagnation_temperature(gas, total_enthalpy, static_pressure, static_entropy):
    """Find the frozen stagnation state on the local isentrope."""
    log_pressure = math.log(static_pressure)
    for _ in range(12):
        pressure = math.exp(log_pressure)
        set_frozen_hp(gas, total_enthalpy, pressure)
        error = gas.entropy_mass - static_entropy
        if abs(error) <= 1e-7:
            return gas.T
        # At constant h, T ds = -v dp. Logarithmic pressure stays positive.
        log_pressure += error*gas.density*gas.T/pressure
    raise ValueError("实气热侧冻结总温反算未满足等熵条件。")


def heated_real_gas_nozzle(gas, chamber, mdot, pc, expansion_ratio, p_amb, circuit):
    if gas.thermo_model not in ("ideal-gas", "Peng-Robinson-Boston-Mathias"):
        raise ValueError("所选燃气状态方程不支持统一热侧求解。")
    gas.TPY = chamber.temperature, pc, _dict_to_Y(gas, chamber.composition)
    h0, s0 = gas.enthalpy_mass, gas.entropy_mass
    if not math.isclose(h0, chamber.enthalpy, rel_tol=1e-8, abs_tol=.05):
        raise ValueError("实气主室保存焓与同一 EOS 的温压组成不一致。")
    cells, x, _, throat_x, throat_index, cumulative, log_area, heat = _flow_geometry(circuit, expansion_ratio)
    area_prime, heat_prime = log_area.derivative(), heat.derivative()
    bounds = (max(300., float(gas.min_temp)), min(float(gas.max_temp), chamber.temperature))
    if bounds[0] >= bounds[1]:
        raise ValueError("实气热侧主室温度超出可用热化学温区。")
    length = float(x[-1]-x[0])
    calls = steps = rejected = 0

    def area(position):
        return math.exp(float(log_area(position)))

    def total_enthalpy(position):
        return h0-float(heat(position))/mdot

    def sonic_state(position, pressure):
        target = total_enthalpy(position)

        def residual(temperature):
            gas.TP = temperature, pressure
            return gas.enthalpy_mass + .5*gas.sound_speed**2-target

        # The near-chamber bracket avoids probing a dense phase needlessly.
        low = max(bounds[0], .45*chamber.temperature)
        if residual(low) >= 0:
            low = bounds[0]
        temperature = brentq(residual, low, bounds[1], xtol=1e-9)
        if abs(residual(temperature)) > 1e-4:
            raise ValueError("实气热侧声速能量方程未达到残差要求。")

    def regularity(position):
        return float(area_prime(position))+gas.thermal_expansion_coeff*float(heat_prime(position))/(mdot*gas.cp_mass)

    def critical_state(pressure):
        position = throat_x
        if cumulative[-1] > 0:
            convergent = x[:throat_index+1]
            samples = np.sort(np.concatenate((convergent, .5*(convergent[1:]+convergent[:-1]))))

            def at_sonic(position):
                if not heat_prime(position):
                    return float(area_prime(position))
                sonic_state(position, pressure)
                return regularity(position)

            values = [at_sonic(v) for v in samples]
            roots = [brentq(at_sonic, left, right, xtol=1e-12)
                     for left, right, a, b in zip(samples, samples[1:], values, values[1:]) if a < 0 <= b]
            if len(roots) != 1:
                raise ValueError("实气热侧没有唯一可解析的声速正则位置。")
            position = roots[0]
        sonic_state(position, pressure)
        speed = gas.sound_speed
        throat_area = mdot/(gas.density*speed*area(position))
        return float(position), math.log(speed), throat_area, regularity(position)

    def state(position, log_velocity, throat_area):
        velocity = math.exp(log_velocity)
        density = mdot/(throat_area*area(position)*velocity)
        enthalpy = total_enthalpy(position)-.5*velocity**2
        try:
            _set_frozen_h_density(gas, enthalpy, density, bounds)
            mach2 = (velocity/gas.sound_speed)**2
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            raise _GasTrialDomainError(f"实气热侧状态超出 EOS 单相域，x={position:g} m。{exc}") from exc
        return velocity, mach2

    def coefficients(position, log_velocity, throat_area):
        _, mach2 = state(position, log_velocity, throat_area)
        return mach2-1, regularity(position)

    def sonic_launch(critical):
        position, log_speed, throat_area, _ = critical
        left_gap = position-max(v for v in x if v < position-1e-12)
        right_gap = min(v for v in x if v > position+1e-12)-position
        epsilon = min(length*1e-6, .05*left_gap, .05*right_gap)
        if epsilon < length*1e-11:
            raise ValueError("实气热侧临界位置距网格边界过近，无法双侧展开。")
        dw = 1e-5
        dw_d, dw_n = (np.asarray(coefficients(position, log_speed+dw, throat_area))
                      - coefficients(position, log_speed-dw, throat_area))/(2*dw)
        center = np.asarray(coefficients(position, log_speed, throat_area))
        slopes = []
        for direction, gap in ((-1, left_gap), (1, right_gap)):
            dx = direction*min(length*1e-5, .02*gap)
            near = np.asarray(coefficients(position+dx, log_speed, throat_area))
            far = np.asarray(coefficients(position+2*dx, log_speed, throat_area))
            dx_d, dx_n = (-3*center+4*near-far)/(2*dx)
            # dln(u)/dx = N/D. At N=D=0 its derivative is the positive
            # Mach-crossing root of D_w U² + (D_x - N_w) U - N_x = 0.
            roots = np.roots((dw_d, dx_d-dw_n, -dx_n))
            valid = [float(v.real) for v in roots if np.isreal(v) and dx_d+dw_d*v.real > 0]
            if len(valid) != 1:
                raise ValueError("实气热侧声速点没有唯一加速跨声速斜率。")
            slopes.append(valid[0])
        return epsilon, slopes

    def integrate_side(critical, slope, epsilon, direction):
        nonlocal calls, steps, rejected
        position, log_speed, throat_area, _ = critical
        start = position+direction*epsilon
        velocity = log_speed+direction*slope*epsilon
        stops = sorted((v for v in x if direction*(v-start)>0), reverse=direction<0)
        segments = []

        def rhs(at, values):
            nonlocal calls
            checkpoint()
            calls += 1
            denominator, numerator = coefficients(at, float(values[0]), throat_area)
            if abs(denominator) < 1e-14:
                raise _GasTrialDomainError("实气热侧积分遇到未正则化的声速点。")
            return [numerator/denominator]

        for index, stop in enumerate(stops):
            width = abs(stop-start)
            for attempt in range(7):
                try:
                    answer = solve_ivp(rhs, (start, stop), [velocity], method="DOP853", rtol=2e-10,
                        atol=2e-12, dense_output=True, max_step=width/(2**attempt),
                        first_step=min(epsilon/4, width/(4*2**attempt)) if index == 0 or attempt else None)
                    break
                except _GasTrialDomainError:
                    rejected += 1
                    if attempt == 6:
                        raise
            if not answer.success:
                raise ValueError(f"实气热侧积分失败。{answer.message}")
            steps += len(answer.t)-1
            segments.append((max(start, stop), answer.sol))
            velocity, start = float(answer.y[0, -1]), stop
        segments.sort(key=lambda row: row[0])
        ends = [row[0] for row in segments]

        def lookup(at):
            if direction*(at-position) <= epsilon:
                return log_speed+slope*(at-position)
            index = min(int(np.searchsorted(ends, at)), len(segments)-1)
            return float(segments[index][1](at)[0])

        return lookup

    shot_cache = {}

    def shoot(ratio):
        checkpoint()
        if ratio not in shot_cache:
            critical = critical_state(pc*ratio)
            epsilon, slopes = sonic_launch(critical)
            left = integrate_side(critical, slopes[0], epsilon, -1)
            state(x[0], left(x[0]), critical[2])
            shot_cache[ratio] = gas.entropy_mass-s0, critical, epsilon, slopes, left
        return shot_cache[ratio][0]

    trials = []
    for ratio in (.48, .63, .32, .75, .19, .86, .09, .96):
        try:
            trials.append((ratio, shoot(ratio)))
        except (ValueError, RuntimeError, ArithmeticError):
            continue
        trials.sort()
        bracket = next(((a, b) for (a, fa), (b, fb) in zip(trials, trials[1:]) if fa*fb <= 0), None)
        if bracket is not None:
            break
    else:
        raise ValueError("实气热侧声速压力无法建立入口储室熵的求根区间。")
    ratio = brentq(shoot, *bracket, xtol=2e-11)
    _, critical, epsilon, slopes, old_left = shot_cache[ratio]
    critical_x, _, at, regularity_residual = critical
    if abs(regularity_residual)*length > 1e-7:
        raise ValueError("实气热侧临界点正则性未达到残差要求。")
    old_right = integrate_side(critical, slopes[1], epsilon, 1)
    epsilon *= .5
    left = integrate_side(critical, slopes[0], epsilon, -1)
    right = integrate_side(critical, slopes[1], epsilon, 1)

    def log_velocity(position):
        return left(position) if position <= critical_x else right(position)

    mesh = sorted(set([*x, critical_x]))
    launch_error = max(abs(math.expm1(log_velocity(v)-(old_left(v) if v <= critical_x else old_right(v)))) for v in mesh)
    if not math.isfinite(launch_error) or launch_error > 2e-7:
        raise ValueError(f"实气热侧声速双侧展开未通过步长独立性检查。{launch_error:g}")
    nodes = []
    for position in mesh:
        velocity, mach2 = state(position, log_velocity(position), at)
        if gas_temperature_applicability(gas)["status"] == "extrapolated":
            raise ValueError("实气热侧节点超出所选燃气热化学温区。")
        cp, cv = gas.cp_mass, gas.cv_mass
        viscosity, conductivity = gas.viscosity, gas.thermal_conductivity
        nodes.append(dict(x_m=float(position), area_ratio=area(position), T_K=gas.T, p_Pa=gas.P,
            rho_kg_m3=gas.density, h_Jkg=gas.enthalpy_mass, s_JkgK=gas.entropy_mass,
            h0_Jkg=total_enthalpy(position), velocity_ms=velocity, M=math.sqrt(mach2), sound_speed_m_s=gas.sound_speed,
            cp_JkgK=cp, cv_JkgK=cv, gamma=cp/cv, mu_Pas=viscosity, Pr=viscosity*cp/conductivity))
    if (any(n["M"] >= 1 for n in nodes if n["x_m"] < critical_x-1e-10)
            or any(n["M"] <= 1 for n in nodes if n["x_m"] > critical_x+1e-10)):
        raise ValueError("实气热侧节点未维持亚声速和超声速分支。")

    gauss_rules = [np.polynomial.legendre.leggauss(n) for n in (8, 16)]

    def flux(position):
        return mdot*math.exp(log_velocity(position))/(at*area(position))

    def momentum_integral(left, right, depth=0):
        # Integrate (mdot/A) du by parts. The pressure audit only reads
        # the stored velocity interpolation and independent geometric terms.
        half = .5*(right-left)
        middle = .5*(right+left)
        estimates = []
        for gx, gw in gauss_rules:
            positions = middle+half*gx
            estimates.append(half*float(np.dot(gw, [flux(v)*float(area_prime(v)) for v in positions])))
        if abs(estimates[1]-estimates[0]) <= max(.002, abs(estimates[1])*1e-9):
            return flux(right)-flux(left)+estimates[1]
        if depth >= 14:
            raise ValueError("实气热侧动量积分未达到求积容差。")
        return momentum_integral(left, middle, depth+1)+momentum_integral(middle, right, depth+1)

    momentum_residuals = []
    for a, b in zip(nodes, nodes[1:]):
        cuts = sorted({a["x_m"], b["x_m"], *(v for v in (critical_x-epsilon, critical_x+epsilon)
                       if a["x_m"] < v < b["x_m"])})
        integral = sum(momentum_integral(left, right) for left, right in zip(cuts, cuts[1:]))
        momentum_residuals.append(b["p_Pa"]-a["p_Pa"]+integral)
    momentum_error = max(map(abs, momentum_residuals))
    momentum_tolerance = max(.05, pc*2e-9)
    if momentum_error > momentum_tolerance:
        raise ValueError(f"实气热侧独立动量积分未通过。{momentum_error:g} Pa。")
    entropy_error = nodes[0]["s_JkgK"]-s0
    if abs(entropy_error) > 1e-4:
        raise ValueError(f"实气热侧入口储室熵未闭合。{entropy_error:g} J/(kg·K)。")
    mass_error = max(abs(n["rho_kg_m3"]*n["velocity_ms"]*n["area_ratio"]*at-mdot) for n in nodes)
    energy_error = max(abs(n["h_Jkg"]+.5*n["velocity_ms"]**2-n["h0_Jkg"]) for n in nodes)
    if mass_error > max(1e-8, mdot*1e-9) or energy_error > 1e-4:
        raise ValueError("实气热侧沿程质量或能量校核未通过。")
    by_x = {n["x_m"]: n for n in nodes}
    hot_cells = []
    for cell in cells:
        position = cell["x_m"]
        velocity, mach2 = state(position, log_velocity(position), at)
        pressure, enthalpy, entropy = gas.P, gas.enthalpy_mass, gas.entropy_mass
        cp, cv, viscosity = gas.cp_mass, gas.cv_mass, gas.viscosity
        prandtl = viscosity*cp/gas.thermal_conductivity
        values = dict(x_m=position, T_static_K=gas.T, p_Pa=pressure, M=math.sqrt(mach2),
                      cp_JkgK=cp, gamma=cp/cv, mu_Pas=viscosity, Pr=prandtl)
        set_frozen_hp(gas, enthalpy+prandtl**(1/3)*.5*velocity**2, pressure)
        values["T_aw_K"] = gas.T
        values["T0_K"] = _stagnation_temperature(gas, total_enthalpy(position), pressure, entropy)
        if any(not gas.min_temp <= values[key] <= gas.max_temp for key in ("T_aw_K", "T0_K")):
            raise ValueError("实气热侧恢复温度或总温超出所选燃气热化学温区。")
        values["total_static_temperature_ratio"] = values["T0_K"]/values["T_static_K"]
        hot_cells.append(values)
    exit_node = nodes[-1]
    speed, pressure, temperature = (exit_node[key] for key in ("velocity_ms", "p_Pa", "T_K"))
    exit_area = at*expansion_ratio
    thrust_vac = mdot*speed+pressure*exit_area
    thrust = thrust_vac-p_amb*exit_area
    geometric, sonic = by_x[float(throat_x)], by_x[critical_x]
    sonic_mach_residual = abs(sonic["M"]**2-1)
    if sonic_mach_residual > 1e-7:
        raise ValueError("实气热侧临界节点的马赫数未达到声速残差要求。")
    heat_loss = float(cumulative[-1])
    energy = mdot*(exit_node["h_Jkg"]+.5*speed**2-h0)+heat_loss
    residual = max(momentum_error/pc, launch_error, mass_error/mdot, sonic_mach_residual,
                   abs(regularity_residual)*length,
                   abs(energy)/max(mdot*abs(h0), abs(heat_loss), 1.))
    ideal = gas.thermo_model == "ideal-gas"
    model_id = HEATED_IDEAL_SAME_EOS_MODEL if ideal else HEATED_REAL_GAS_MODEL
    gas_label = "理想气体" if ideal else "PR–Boston–Mathias"
    result = ComponentResult("nozzle", dict(thrust_N=thrust, Isp_s=thrust/(mdot*G0),
        thrust_vac_N=thrust_vac, Isp_vac_s=thrust_vac/(mdot*G0), Ve_ms=speed,
        c_star_ms=pc*at/mdot, Cf=thrust/(pc*at), Cf_sl=thrust/(pc*at), Cf_vac=thrust_vac/(pc*at),
        Cf_momentum=mdot*speed/(pc*at), Cf_pressure=(pressure-p_amb)*expansion_ratio/pc,
        At_m2=at, Ae_m2=exit_area, mach_exit=exit_node["M"], p_exit_Pa=pressure, p_amb_Pa=p_amb,
        expansion_ratio=expansion_ratio, T_exit_K=temperature, T_throat_K=geometric["T_K"],
        p_throat_Pa=geometric["p_Pa"], T_sonic_K=sonic["T_K"], p_sonic_Pa=sonic["p_Pa"],
        h_exit_Jkg=exit_node["h_Jkg"], h_chamber_Jkg=h0, heat_loss_W=heat_loss,
        energy_residual_W=energy, flow_model=0., hot_side_residual=residual),
        [f"冻结组成 {gas_label} 准一维能量和动量，按冷却单元位置扣热。",
         "主室温压按零速储室总态使用，沿程节点报告局部静态温压和声速。",
         "以入口储室熵匹配声速压力，以真实密度和声速确定质量通量与喉面积。",
         "未建模激波、分离、有限速率反应及燃气壁面摩擦。Bartz 换热与高压输运仍需独立验证。"])
    result.profile = dict(model_id=model_id, thermo_method="same_eos", gas_eos=gas.thermo_model,
        gas_model=deepcopy(chamber.model), nodes=nodes, cells=hot_cells,
        max_scaled_residual=residual, iterations=steps, function_evaluations=calls, heat_loss_W=heat_loss,
        rejected_domain_attempts=rejected, critical_x_m=critical_x, geometric_throat_x_m=throat_x,
        critical_regularity_residual_per_m=regularity_residual,
        critical_mach_squared_residual=sonic_mach_residual,
        critical_left_log_velocity_slope_per_m=slopes[0], critical_right_log_velocity_slope_per_m=slopes[1],
        critical_launch_distance_m=epsilon, critical_launch_relative_difference=launch_error,
        max_momentum_integral_residual_Pa=momentum_error, momentum_integral_tolerance_Pa=momentum_tolerance,
        momentum_cell_residuals_Pa=momentum_residuals, inlet_entropy_residual_JkgK=entropy_error,
        max_mass_residual_kg_s=mass_error, max_specific_energy_residual_Jkg=energy_error,
        solver="piecewise DOP853; inlet entropy shooting; independent Gauss momentum integration",
        critical_location_model="conservative_pchip_heat_and_log_area_regularity",
        validation_status="system_model_requires_independent_validation")
    return result
