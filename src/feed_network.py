"""Single-phase liquid feed-network dynamics for component research.

Each independent circuit has a fixed-pressure reservoir, an inertive pump
branch, a compressible liquid inventory, and an inertive outlet/actuated valve
branch ending at fixed back pressure. Pump speed is prescribed, not obtained
from a rotor/start-device torque balance. No ignition or two-phase filling is
implied by this model.
"""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import minimize_scalar

from task_control import checkpoint


def _number(value, label, minimum=None, maximum=None, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label}: 必须是有限数值。')
    value = float(value)
    if positive and value <= 0 or minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{label}: 超出允许范围。')
    return value


def _table_loss(theta, points, efficiency):
    """Dimensionless shaft input minus hydraulic output on the unit circle."""
    angle = math.radians(theta)
    head = np.interp(theta, [p['theta_deg'] for p in points], [p['head'] for p in points])
    torque = np.interp(theta, [p['theta_deg'] for p in points], [p['torque'] for p in points])
    return float(torque * math.cos(angle) / efficiency - head * math.sin(angle))


def validate_settings(settings):
    """Return a validated deep copy; never infer or repair an invalid map."""
    if not isinstance(settings, dict):
        raise ValueError('供给网络设置必须是对象。')
    result = deepcopy(settings)
    for key, lo, hi in [('duration_s', .001, 60.), ('command_time_s', 0., 60.),
                        ('command_ramp_s', 1e-5, 60.), ('rtol', 1e-9, 1e-3)]:
        result[key] = _number(result.get(key), key, lo, hi)
    count = result.get('sample_count')
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 5001:
        raise ValueError('sample_count: 必须是 2 至 5001 的整数。')
    if result['command_time_s'] > result['duration_s']:
        raise ValueError('扰动起始时刻不能晚于积分终点。')
    circuits = result.get('circuits')
    if not isinstance(circuits, list) or not 1 <= len(circuits) <= 4:
        raise ValueError('必须提供 1 至 4 个独立工质回路。')
    identifiers = set()
    for c in circuits:
        if not isinstance(c, dict):
            raise ValueError('回路必须是对象。')
        cid = c.get('id')
        if not isinstance(cid, str) or not cid or cid in identifiers:
            raise ValueError('回路 id 必须是非空且不重复的字符串。')
        identifiers.add(cid)
        c['label'] = str(c.get('label') or cid)
        for key in ['rho_kg_m3', 'bulk_modulus_Pa', 'volume_m3', 'pump_head_Pa',
                    'pump_flow_m3_s', 'pump_speed_rpm', 'inlet_length_m', 'inlet_diameter_m',
                    'outlet_length_m', 'outlet_diameter_m', 'valve_tau_s', 'valve_rate_per_s']:
            c[key] = _number(c.get(key), f'{cid}.{key}', positive=True)
        for key in ['tank_pressure_Pa', 'outlet_pressure_Pa', 'initial_manifold_pressure_Pa',
                    'pressure_floor_Pa']:
            c[key] = _number(c.get(key), f'{cid}.{key}', 0.)
        c['max_compression_fraction'] = _number(c.get('max_compression_fraction'), f'{cid}.max_compression_fraction', .000001, .1)
        for key in ['inlet_loss_Pa', 'outlet_loss_Pa', 'valve_loss_Pa']:
            c[key] = _number(c.get(key), f'{cid}.{key}', 0.)
        c['pump_efficiency'] = _number(c.get('pump_efficiency'), f'{cid}.pump_efficiency', .01, 1.)
        c['leak_fraction'] = _number(c.get('leak_fraction'), f'{cid}.leak_fraction', 1e-5, 1.)
        for key in ['initial_valve_opening', 'final_valve_opening']:
            c[key] = _number(c.get(key), f'{cid}.{key}', 0., 1.)
        for key in ['initial_speed_ratio', 'final_speed_ratio', 'initial_pump_flow_ratio', 'initial_outlet_flow_ratio']:
            c[key] = _number(c.get(key), f'{cid}.{key}')
        if c['initial_manifold_pressure_Pa'] <= c['pressure_floor_Pa']:
            raise ValueError(f'{cid}: 初始容腔压力必须高于单相压力下限。')
        if min(c['tank_pressure_Pa'], c['outlet_pressure_Pa']) < c['pressure_floor_Pa']:
            raise ValueError(f'{cid}: 固定压力边界低于指定的单相压力下限。')
        pm = c.get('pump_map')
        if not isinstance(pm, dict) or pm.get('kind') not in ('analytic_demo', 'suter_table'):
            raise ValueError(f'{cid}: 泵特性类型必须为 analytic_demo 或 suter_table。')
        for key in ['max_speed_ratio', 'max_flow_ratio']:
            pm[key] = _number(pm.get(key), f'{cid}.pump_map.{key}', positive=True)
        if abs(c['initial_speed_ratio']) >= pm['max_speed_ratio'] or abs(c['initial_pump_flow_ratio']) >= pm['max_flow_ratio']:
            raise ValueError(f'{cid}: 初始泵工作点必须位于泵图适用域内部。')
        if pm['kind'] == 'analytic_demo':
            pm['slope'] = _number(pm.get('slope'), f'{cid}.pump_map.slope', positive=True)
            if pm['slope'] > 1 / c['pump_efficiency'] - 1 + 1e-14:
                raise ValueError(f'{cid}: 解析演示特性要求 0 < slope ≤ 1/η − 1，确保所有象限泵损失非负。')
            pm['source'] = str(pm.get('source') or '解析演示特性，未实测标定')
        else:
            points = pm.get('points')
            if not isinstance(points, list) or not 5 <= len(points) <= 1441:
                raise ValueError(f'{cid}: Suter 表需要 5 至 1441 个数据点。')
            for p in points:
                if not isinstance(p, dict):
                    raise ValueError(f'{cid}: Suter 表行必须是对象。')
                for key in ['theta_deg', 'head', 'torque']:
                    p[key] = _number(p.get(key), f'{cid}.pump_map.{key}')
            angles = [p['theta_deg'] for p in points]
            if angles[0] != 0. or angles[-1] != 360. or any(b <= a for a, b in zip(angles, angles[1:])):
                raise ValueError(f'{cid}: Suter 角度必须严格递增，并包含 0° 和 360°。')
            for key in ['head', 'torque']:
                if not math.isclose(points[0][key], points[-1][key], rel_tol=1e-7, abs_tol=1e-8):
                    raise ValueError(f'{cid}: Suter 表 0°/360° 必须闭合。')
                nominal = float(np.interp(45., angles, [p[key] for p in points]))
                if not math.isclose(nominal, .5, rel_tol=1e-5, abs_tol=1e-7):
                    raise ValueError(f'{cid}: Suter 表在 θ=45° 的 WH 与 WB 必须均为 0.5；请核对额定量和归一化定义。')
            # A table can be passive at its vertices but become active between
            # them. Check the actual piecewise-linear interpolation as well.
            worst = min(_table_loss(x, points, c['pump_efficiency']) for x in angles)
            for a, b in zip(angles, angles[1:]):
                for left, right in zip(np.linspace(a, b, max(2, math.ceil((b-a)/15)+1))[:-1],
                                       np.linspace(a, b, max(2, math.ceil((b-a)/15)+1))[1:]):
                    fit = minimize_scalar(lambda x: _table_loss(x, points, c['pump_efficiency']),
                                          bounds=(left, right), method='bounded')
                    worst = min(worst, float(fit.fun))
            if worst < -1e-8:
                raise ValueError(f'{cid}: Suter 表及其插值存在负耗散（最小无量纲损失 {worst:.6g}）；不能截断或隐藏。')
            if not isinstance(pm.get('source'), str) or not pm['source'].strip():
                raise ValueError(f'{cid}: 导入泵图必须填写来源及标定说明。')
    return result


def evaluate_pump_map(circuit, n, q):
    """Evaluate a validated map at signed normalized speed and volume flow.

    Suter theta=atan2(q,n); WH=dp/(dp0*(n²+q²)),
    WB=tau/(tau_nominal*(n²+q²)), tau_nominal=dp0*Q0/(eta0*omega0).
    Both normalized head and torque equal .5 at theta=45 degrees.
    The zero-speed value never divides by speed. Domain checking is performed
    with continuous events by the integrator, not by clipping trial states.
    """
    n, q = float(n), float(q)
    p, Q, eta = circuit['pump_head_Pa'], circuit['pump_flow_m3_s'], circuit['pump_efficiency']
    omega0 = circuit['pump_speed_rpm'] * math.pi / 30
    pm = circuit['pump_map']
    if pm['kind'] == 'analytic_demo':
        s = pm['slope']
        head = p*((1+s)*n*abs(n)-s*q*abs(q))
        torque = p*Q/omega0*((1+s)*abs(n)*q+(1/eta-1-s)*n*abs(n))
    else:
        radius2 = n*n+q*q
        theta = math.degrees(math.atan2(q, n)) % 360 if radius2 else 0.
        angles = [row['theta_deg'] for row in pm['points']]
        wh = float(np.interp(theta, angles, [row['head'] for row in pm['points']]))
        wb = float(np.interp(theta, angles, [row['torque'] for row in pm['points']]))
        head = p*radius2*wh
        torque = p*Q/(eta*omega0)*radius2*wb
    shaft = torque * n*omega0
    hydraulic = head*q*Q
    result = dict(pump_head_Pa=head, pump_torque_Nm=torque,
                  pump_hydraulic_power_W=hydraulic, pump_shaft_power_W=shaft,
                  pump_loss_W=shaft-hydraulic)
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError(f"{circuit.get('id', 'pump')}：泵图计算产生非有限值，请核对泵图归一化、系数幅值和额定量。")
    return result


class FeedNetworkModel:
    """Two inertive liquid branches and one finite inventory per circuit."""

    STATES_PER_CIRCUIT = 10  # p, Qin, Qout, a, plus six independent ledgers

    def __init__(self, settings):
        self.settings = validate_settings(settings)
        self.circuits = self.settings['circuits']
        self.parameters = []
        initial = []
        for c in self.circuits:
            rho, Q = c['rho_kg_m3'], c['pump_flow_m3_s']
            compliance = c['volume_m3']/c['bulk_modulus_Pa']
            li = rho*c['inlet_length_m']/(math.pi*c['inlet_diameter_m']**2/4)
            lo = rho*c['outlet_length_m']/(math.pi*c['outlet_diameter_m']**2/4)
            pscale = max(c['pump_head_Pa'], c['initial_manifold_pressure_Pa'], 1e5)
            mass0 = rho*c['volume_m3']
            energy0 = .5*compliance*c['initial_manifold_pressure_Pa']**2 + .5*li*(Q*c['initial_pump_flow_ratio'])**2 + .5*lo*(Q*c['initial_outlet_flow_ratio'])**2
            escale = max(energy0, c['pump_head_Pa']*Q*self.settings['duration_s'], 1.)
            mscale = max(mass0, rho*Q*self.settings['duration_s'], .001)
            self.parameters.append(dict(C=compliance, L_in=li, L_out=lo,
                pressure_scale=pscale, mass_scale=mscale, energy_scale=escale,
                mass0=mass0, energy0=energy0))
            initial.extend([c['initial_manifold_pressure_Pa']/pscale,
                            c['initial_pump_flow_ratio'], c['initial_outlet_flow_ratio'],
                            c['initial_valve_opening'], 0., 0., 0., 0., 0., 0.])
        self.initial = np.asarray(initial, dtype=float)
        self.model_description = dict(
            name='单相供给子系统研究台', experiment_validated=False,
            topology='每回路：固定压储箱 → 有惯性泵支路 → 有限液体容腔 → 有惯性管路与执行阀 → 固定背压；回路间不混合。',
            equations=[
                'C = V/K；C dp/dt = Q_in − Q_out；m = ρ[V + C(p − p_initial)]',
                'L = ρl/A；L_in dQ_in/dt = p_tank − p + Δp_pump − R_in Q_in|Q_in|',
                'L_out dQ_out/dt = p − p_outlet − R_out Q_out|Q_out| − Δp_valve',
                'da/dt = clip((a_cmd − a)/τ, −rate, +rate)；A_eff/A_full = leak + (1−leak)a',
                'E_hyd = Σ[½ L_in Q_in² + ½ L_out Q_out² + ½ C(p − p_ref)²]；p_ref = 0 Pa',
                'dE_hyd/dt = Σ[p_tank Q_in − p_outlet Q_out + Δp_pump Q_in − dissipative_power]'],
            pump_normalization='n=N/N0；q=Q/Q0；θ=atan2(q,n) mod 360°；WH=Δp/[Δp0(n²+q²)]；WB=τ/[τ0(n²+q²)]；τ0=Δp0Q0/(η0ω0)；θ45°锚点WH=WB=0.5。',
            analytic_map='Δp=Δp0[(1+s)n|n|−s q|q|]；τ=Δp0Q0/ω0[(1+s)|n|q+(1/η0−1−s)n|n|]；解析演示特性，未实测标定。',
            speed_model='转速时间历程为外部规定；输出泵轴功率与力矩，不求转子加速、驱动装置或自启动。',
            thermal_model='每回路冻结密度与体积模量；不求液体热状态、气液两相、汽蚀或低温预冷。',
            inventory_model='容腔采用线性小压缩库存；管段本体固定液体库存不作为动态状态。',
            valve_model='valve_loss_Pa 表示 Q=Q0、A_eff=A_full 时的阀压降；零指令保留显式泄漏，不代表完全封死。有效面积以物理行程[0,1]延拓积分器试探状态；输出保留原始行程，超过数值容差时终止，不钳制有符号流量。',
            validity='容腔到单相压力下限、压缩应变边界、泵图速度/流量域或负泵损失时停止并保留部分结果。压力下限是用户给定条件，不等于本模型已验证汽蚀裕度。',
            hydraulic_reference_pressure_Pa=0., circuits=[dict(id=c['id'], label=c['label'],
                compliance_m3_Pa=p['C'], inlet_inertance_Pa_s2_m3=p['L_in'],
                outlet_inertance_Pa_s2_m3=p['L_out'], pump_map=deepcopy(c['pump_map']))
                for c, p in zip(self.circuits, self.parameters)])

    def command(self, t, initial, final):
        s = self.settings
        fraction = min(1., max(0., (t-s['command_time_s'])/s['command_ramp_s']))
        return initial+(final-initial)*fraction

    def evaluate(self, t, y):
        checkpoint()
        derivative = np.zeros_like(y, dtype=float)
        rows = []
        for i, (c, par) in enumerate(zip(self.circuits, self.parameters)):
            j = i*self.STATES_PER_CIRCUIT
            p = y[j]*par['pressure_scale']
            Q0 = c['pump_flow_m3_s']
            qi, qo, a = float(y[j+1]), float(y[j+2]), float(y[j+3])
            Qin, Qout = qi*Q0, qo*Q0
            n = self.command(t, c['initial_speed_ratio'], c['final_speed_ratio'])
            command = self.command(t, c['initial_valve_opening'], c['final_valve_opening'])
            # The continuous actuator keeps its physical state within [0,1],
            # but Radau's trial stages / initial Euler step need an extension
            # outside that interval. Bound ONLY the area used by trial states,
            # retain raw a in output, and stop accepted travel excursions below.
            # This is not a flow clamp or an ideal fully closed valve.
            effective = c['leak_fraction']+(1-c['leak_fraction'])*min(1., max(0., a))
            pump = evaluate_pump_map(c, n, qi)
            inlet_drop = c['inlet_loss_Pa']*qi*abs(qi)
            outlet_drop = c['outlet_loss_Pa']*qo*abs(qo)
            valve_drop = c['valve_loss_Pa']*qo*abs(qo)/effective**2
            dp = (Qin-Qout)/par['C']
            dqi = (c['tank_pressure_Pa']-p+pump['pump_head_Pa']-inlet_drop)/par['L_in']
            dqo = (p-c['outlet_pressure_Pa']-outlet_drop-valve_drop)/par['L_out']
            da = float(np.clip((command-a)/c['valve_tau_s'], -c['valve_rate_per_s'], c['valve_rate_per_s']))
            mass_rate = c['rho_kg_m3']*(Qin-Qout)
            boundary = c['tank_pressure_Pa']*Qin-c['outlet_pressure_Pa']*Qout
            dissipation = inlet_drop*Qin+(outlet_drop+valve_drop)*Qout
            derivative[j:j+4] = [dp/par['pressure_scale'], dqi/Q0, dqo/Q0, da]
            derivative[j+4:j+10] = [mass_rate/par['mass_scale'], boundary/par['energy_scale'],
                pump['pump_hydraulic_power_W']/par['energy_scale'], dissipation/par['energy_scale'],
                pump['pump_shaft_power_W']/par['energy_scale'], pump['pump_loss_W']/par['energy_scale']]
            rows.append(dict(id=c['id'], label=c['label'], pump_flow_m3_s=Qin,
                outlet_flow_m3_s=Qout, pump_mass_flow_kg_s=c['rho_kg_m3']*Qin,
                outlet_mass_flow_kg_s=c['rho_kg_m3']*Qout, manifold_pressure_Pa=float(p),
                compression_fraction=float((p-c['initial_manifold_pressure_Pa'])/c['bulk_modulus_Pa']),
                inventory_kg=float(par['mass0']+c['rho_kg_m3']*par['C']*(p-c['initial_manifold_pressure_Pa'])),
                speed_ratio=float(n), speed_rpm=float(n*c['pump_speed_rpm']),
                valve_command=float(command), valve_opening=a, effective_area_ratio=float(effective),
                valve_drop_Pa=float(valve_drop), inlet_loss_Pa=float(inlet_drop), outlet_loss_Pa=float(outlet_drop),
                flow_direction='反流' if Qin < -Q0*1e-10 else '正流' if Qin > Q0*1e-10 else '零流量',
                outlet_flow_direction='反流' if Qout < -Q0*1e-10 else '正流' if Qout > Q0*1e-10 else '零流量',
                net_mass_rate_kg_s=float(mass_rate), boundary_power_W=float(boundary),
                dissipative_power_W=float(dissipation), **pump))
        return derivative, rows

    def rhs(self, t, y):
        return self.evaluate(t, y)[0]

    def sample(self, t, y):
        _, rows = self.evaluate(t, y)
        keys = ['storage_mass_kg', 'stored_hydraulic_energy_J', 'integrated_boundary_mass_kg',
                'integrated_boundary_work_J', 'integrated_pump_work_J', 'integrated_dissipation_J',
                'integrated_shaft_work_J', 'integrated_pump_loss_J', 'mass_balance_error_kg',
                'hydraulic_energy_error_J', 'pump_work_balance_error_J']
        result = dict(time_s=float(t), circuits=rows, **dict.fromkeys(keys, 0.))
        for i, (c, par, row) in enumerate(zip(self.circuits, self.parameters, rows)):
            j = i*self.STATES_PER_CIRCUIT
            energy = .5*par['C']*row['manifold_pressure_Pa']**2 + .5*par['L_in']*row['pump_flow_m3_s']**2 + .5*par['L_out']*row['outlet_flow_m3_s']**2
            mass_integral = y[j+4]*par['mass_scale']
            boundary, pump, dissipation, shaft, loss = (float(x*par['energy_scale']) for x in y[j+5:j+10])
            row.update(storage_mass_kg=row['inventory_kg'], stored_hydraulic_energy_J=float(energy),
                integrated_boundary_mass_kg=float(mass_integral), integrated_boundary_work_J=boundary,
                integrated_pump_work_J=pump, integrated_dissipation_J=dissipation,
                integrated_shaft_work_J=shaft, integrated_pump_loss_J=loss,
                mass_balance_error_kg=float(row['inventory_kg']-par['mass0']-mass_integral),
                hydraulic_energy_error_J=float(energy-par['energy0']-boundary-pump+dissipation),
                pump_work_balance_error_J=float(shaft-pump-loss))
            for key in keys:
                result[key] += row[key]
        return result

    def validity_margins(self, t, y):
        _, rows = self.evaluate(t, y)
        values = []
        for c, row in zip(self.circuits, rows):
            cid, pm = c['label'], c['pump_map']
            values.extend([
                (f'{cid}：容腔压力到达单相压力下限', (row['manifold_pressure_Pa']-c['pressure_floor_Pa'])/max(c['pump_head_Pa'], 1.)),
                (f'{cid}：容腔压缩应变到达线性模型边界', c['max_compression_fraction']-abs(row['compression_fraction'])),
                (f'{cid}：转速到达泵图适用域边界', pm['max_speed_ratio']-abs(row['speed_ratio'])),
                (f'{cid}：流量到达泵图适用域边界', pm['max_flow_ratio']-abs(row['pump_flow_m3_s']/c['pump_flow_m3_s'])),
                (f'{cid}：阀行程超出物理区间的数值容差', min(row['valve_opening'], 1.-row['valve_opening'])+max(1e-8, self.settings['rtol'])),
                (f'{cid}：泵图产生负耗散', row['pump_loss_W']/(c['pump_head_Pa']*c['pump_flow_m3_s'])+1e-8)])
        return values

    def validity_margin(self, t, y):
        return min(value for _, value in self.validity_margins(t, y))

    def audits(self, samples):
        mass_error = max((abs(r['mass_balance_error_kg']) for s in samples for r in s['circuits']), default=0.)
        energy_error = max((abs(r['hydraulic_energy_error_J']) for s in samples for r in s['circuits']), default=0.)
        mass_relative = max((abs(r['mass_balance_error_kg'])/par['mass_scale'] for s in samples for r, par in zip(s['circuits'], self.parameters)), default=0.)
        energy_relative = max((abs(r['hydraulic_energy_error_J'])/par['energy_scale'] for s in samples for r, par in zip(s['circuits'], self.parameters)), default=0.)
        return dict(status='pass' if mass_relative <= 2e-6 and energy_relative <= max(2e-6, self.settings['rtol']*20) else 'review',
            max_abs_mass_balance_error_kg=mass_error, max_abs_hydraulic_energy_error_J=energy_error,
            mass_relative_error=mass_relative, hydraulic_energy_relative_error=energy_relative,
            basis='逐回路检查库存变化与独立边界通量积分；液压储能检查边界功、泵液压功及耗散积分。回路之间误差不互相抵消；泵内损失另报。',
            experiment_validated=False)

    def integrate(self, publish=None):
        """Radau integration, continuous terminal events, cancellation checkpoints."""
        p = self.settings
        times = np.linspace(0., p['duration_s'], p['sample_count'])
        breaks = sorted(set([0., p['duration_s'], p['command_time_s'], p['command_time_s']+p['command_ramp_s']] +
                            list(np.linspace(0., p['duration_s'], min(21, p['sample_count'])))))
        breaks = [x for x in breaks if 0. <= x <= p['duration_s']]
        y = self.initial.copy()
        samples = [self.sample(0., y)]
        if publish:
            publish(samples[0])
        complete, reason, nfev = True, None, 0
        if self.validity_margin(0., y) <= 0:
            complete = False
            reason = min(self.validity_margins(0., y), key=lambda pair: pair[1])[0]
        else:
            for start, end in zip(breaks, breaks[1:]):
                checkpoint()
                def terminal_event(t, state):
                    return self.validity_margin(t, state)
                terminal_event.terminal = True
                terminal_event.direction = -1
                sol = solve_ivp(self.rhs, (start, end), y, method='Radau', dense_output=True,
                    rtol=p['rtol'], atol=p['rtol']*.01,
                    first_step=min(end-start, min(c['valve_tau_s'] for c in self.circuits)*.1),
                    max_step=max((end-start)/4, 1e-8), events=terminal_event)
                nfev += sol.nfev
                actual_end = float(sol.t[-1])
                requested = list(times[(times > start+1e-12) & (times <= actual_end+1e-12)])
                if (sol.status == 1 or not sol.success) and (not requested or abs(requested[-1]-actual_end) > 1e-10):
                    requested.append(actual_end)
                for t in requested:
                    row = self.sample(float(t), sol.sol(t))
                    if row['time_s'] > samples[-1]['time_s']+1e-12:
                        samples.append(row)
                        if publish:
                            publish(row)
                y = sol.y[:, -1]
                if not sol.success:
                    complete, reason = False, '数值积分未完成：'+sol.message
                    break
                if sol.status == 1:
                    complete = False
                    reason = min(self.validity_margins(actual_end, y), key=lambda pair: pair[1])[0]
                    break
        metadata = dict(complete=complete, termination_reason=reason,
            audits=self.audits(samples), model_description=self.model_description,
            integrator=dict(method='Radau', rtol=p['rtol'], atol_scaled=p['rtol']*.01,
                            nfev=nfev, state_count=len(y), physical_states=4*len(self.circuits),
                            conservation_ledger_states=6*len(self.circuits)))
        return samples, metadata
