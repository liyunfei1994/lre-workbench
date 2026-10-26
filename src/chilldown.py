"""Pure-fluid thermal inventory / filling research model.

One rigid, well-mixed HEOS control volume and one lumped wall per circuit.
The independent conservative states are fluid mass and TOTAL internal energy.
Phase equilibrium is obtained from density and specific internal energy.  The
outlet removes the homogeneous mixture, not selectively the vapour phase.
This is not a spatial chill-front, noncondensable-gas or whole-engine model.
"""
from __future__ import annotations

from copy import deepcopy
import math

import CoolProp
import CoolProp.CoolProp as CP
import numpy as np
from scipy.integrate import Radau
from scipy.optimize import brentq, minimize_scalar

from task_control import checkpoint


FLUIDS = ('Oxygen', 'Methane', 'n-Dodecane')
PHASES = {CP.iphase_liquid: 'liquid', CP.iphase_gas: 'gas',
          CP.iphase_twophase: 'twophase', CP.iphase_supercritical: 'supercritical',
          CP.iphase_supercritical_liquid: 'supercritical_liquid',
          CP.iphase_supercritical_gas: 'supercritical_gas',
          CP.iphase_critical_point: 'critical_point'}


def _number(value, label, minimum=None, maximum=None, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label}：必须是有限数值。')
    value = float(value)
    if positive and value <= 0 or minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{label}：超出允许范围。')
    return value


def validate_settings(settings):
    """Validate physical inputs without repairing an inconsistent initial state."""
    if not isinstance(settings, dict):
        raise ValueError('预冷与充填设置必须是对象。')
    out = deepcopy(settings)
    for key, low, high in [('duration_s', .001, 300.), ('command_time_s', 0., 300.),
                           ('command_ramp_s', 1e-5, 300.), ('rtol', 1e-9, 1e-3)]:
        out[key] = _number(out.get(key), key, low, high)
    count = out.get('sample_count')
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 5001:
        raise ValueError('sample_count：必须是 2 至 5001 的整数。')
    if out['command_time_s'] > out['duration_s']:
        raise ValueError('阀门指令起点不能晚于积分终点。')
    circuits = out.get('circuits')
    if not isinstance(circuits, list) or not 1 <= len(circuits) <= 2:
        raise ValueError('必须提供 1 至 2 个独立预冷单元。')
    seen = set()
    for c in circuits:
        if not isinstance(c, dict):
            raise ValueError('预冷单元必须是对象。')
        cid = c.get('id')
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ValueError('预冷单元 id 必须非空且不重复。')
        seen.add(cid)
        c['label'] = str(c.get('label') or cid)
        if c.get('fluid') not in FLUIDS:
            raise ValueError(f'{cid}：只支持 Oxygen、Methane 或 n-Dodecane 纯工质；不支持不凝气体。')
        if c.get('initial_mode') not in ('PT', 'PQ'):
            raise ValueError(f'{cid}：初态只支持 PT 单相或 PQ 饱和混合物。')
        for key in ['volume_m3', 'initial_pressure_Pa', 'initial_temperature_K',
                    'wall_temperature_K', 'wall_heat_capacity_J_K', 'ambient_temperature_K',
                    'supply_pressure_Pa', 'supply_temperature_K', 'back_pressure_Pa',
                    'valve_tau_s', 'valve_rate_per_s', 'min_pressure_Pa',
                    'max_pressure_Pa', 'max_wall_temperature_K', 'target_wall_temperature_K']:
            c[key] = _number(c.get(key), f'{cid}.{key}', positive=True)
        for key in ['wall_UA_W_K', 'ambient_UA_W_K', 'inlet_CdA_m2', 'outlet_CdA_m2']:
            c[key] = _number(c.get(key), f'{cid}.{key}', 0.)
        for key in ['initial_quality', 'initial_inlet_opening', 'final_inlet_opening',
                    'initial_outlet_opening', 'final_outlet_opening', 'target_liquid_volume_fraction']:
            c[key] = _number(c.get(key), f'{cid}.{key}', 0., 1.)
        if not c['min_pressure_Pa'] < c['initial_pressure_Pa'] < c['max_pressure_Pa']:
            raise ValueError(f'{cid}：初始压力必须位于允许压力区间内部。')
        if c['back_pressure_Pa'] < c['min_pressure_Pa']:
            raise ValueError(f'{cid}：背压不能低于本页的最低允许压力。')
        if c['wall_temperature_K'] >= c['max_wall_temperature_K']:
            raise ValueError(f'{cid}：初始壁温必须低于最高允许壁温。')
    return out


class PropertyDomainError(ValueError):
    """HEOS could not close a requested physical or trial state."""


def _state_values(state):
    data = dict(pressure_Pa=float(state.p()), temperature_K=float(state.T()),
                density_kg_m3=float(state.rhomass()), enthalpy_J_kg=float(state.hmass()),
                specific_internal_energy_J_kg=float(state.umass()), entropy_J_kg_K=float(state.smass()),
                phase=PHASES.get(state.phase(), 'unknown'))
    if not all(math.isfinite(v) for v in data.values() if isinstance(v, float)):
        raise PropertyDomainError('HEOS 返回非有限物性。')
    if data['pressure_Pa'] <= 0 or data['temperature_K'] <= 0 or data['density_kg_m3'] <= 0:
        raise PropertyDomainError('HEOS 返回非物理压力、温度或密度。')
    phase = data['phase']
    if phase == 'unknown':
        raise PropertyDomainError('HEOS 相态未确定，不能构造气液库存。')
    if phase == 'twophase':
        x = float(state.Q())
        if not 0 <= x <= 1:
            raise PropertyDomainError('两相质量分数超出 [0,1]。')
        rho_l = float(state.saturated_liquid_keyed_output(CP.iDmass))
        rho_v = float(state.saturated_vapor_keyed_output(CP.iDmass))
        v_mix = (1-x)/rho_l+x/rho_v
        alpha = (x/rho_v)/v_mix
        data.update(quality=x, void_fraction=alpha, liquid_volume_fraction=1-alpha,
                    liquid_density_kg_m3=rho_l, vapor_density_kg_m3=rho_v,
                    quality_basis='饱和两相蒸气质量分数', phase_partition_available=True,
                    phase_volume_closure_relative_error=data['density_kg_m3']*v_mix-1.)
    elif phase in ('liquid', 'supercritical_liquid'):
        data.update(quality=0., void_fraction=0., liquid_volume_fraction=1.,
                    quality_basis='单液相库存记号 0；不是两相干度', phase_partition_available=True,
                    phase_volume_closure_relative_error=0.)
    elif phase in ('gas', 'supercritical_gas'):
        data.update(quality=1., void_fraction=1., liquid_volume_fraction=0.,
                    quality_basis='单气相库存记号 1；不是两相干度', phase_partition_available=True,
                    phase_volume_closure_relative_error=0.)
    else:
        data.update(quality=None, void_fraction=None, liquid_volume_fraction=None,
                    quality_basis='临界或超临界状态不划分气液库存', phase_partition_available=False,
                    phase_volume_closure_relative_error=None)
    return data


def _isentropic_flux_at_pressure(eos, fluid, pu, h0, s0, pressure):
    """One HEOS isentropic state; no approximate fluid-property substitute."""
    if abs(pressure/pu-1) < 1e-12:
        return 0.
    try:
        eos.update(CP.PSmass_INPUTS, pressure, s0)
        h, rho, temperature = eos.hmass(), eos.rhomass(), eos.T()
    except (ValueError, OverflowError) as exc:
        raise PropertyDomainError(f'{fluid} 等熵节流孔物性闭合失败：{exc}') from exc
    if not eos.Ttriple() <= temperature <= eos.Tmax() or not math.isfinite(rho) or rho <= 0:
        raise PropertyDomainError(f'{fluid} 等熵通道超出纯流体温度/密度有效域。')
    dh = h0-h
    if dh < -max(1e-5, abs(h0)*1e-10):
        raise PropertyDomainError(f'{fluid} 等熵降压产生负焓降；不能以零流量隐藏。')
    return rho*math.sqrt(2*max(0., dh))  # Only roundoff in a zero enthalpy drop.


def hem_mass_flux(fluid, upstream_pressure_Pa, upstream_enthalpy_J_kg,
                  upstream_entropy_J_kg_K, downstream_pressure_Pa, *, state=None, grid_points=25):
    """Quasi-steady homogeneous-equilibrium isentropic orifice mass flux.

    G(p)=rho(p,s0)*sqrt(2*(h0-h(p,s0))).  The flow uses the maximum over the
    interval [p_down,p_up], so it cannot decrease as backpressure is lowered
    below critical pressure.  CdA belongs to the caller.  The energy delivered
    to a resting receiving volume is source stagnation enthalpy, not h(p).
    """
    checkpoint()
    pu, pd, h0, s0 = map(float, (upstream_pressure_Pa, downstream_pressure_Pa,
                                upstream_enthalpy_J_kg, upstream_entropy_J_kg_K))
    if not all(math.isfinite(v) for v in (pu, pd, h0, s0)) or min(pu, pd) <= 0:
        raise ValueError('HEM 通量需要有限的上游状态和正绝对压力。')
    if pd >= pu:
        return dict(mass_flux_kg_m2_s=0., critical_pressure_Pa=pu, choked=False)
    eos = state if state is not None else CP.AbstractState('HEOS', fluid)
    cache = {}

    def flux(logp):
        p = math.exp(float(logp))
        if p in cache:
            return cache[p]
        value = _isentropic_flux_at_pressure(eos, fluid, pu, h0, s0, p)
        cache[p] = value
        return value

    logps = np.linspace(math.log(pd), math.log(pu), max(9, int(grid_points)))
    values = [flux(x) for x in logps]
    best_i = int(np.argmax(values))
    best_logp, best_g = float(logps[best_i]), values[best_i]
    # Refine each resolved local maximum, including a boundary-adjacent peak.
    # This also handles the kink where an isentrope first enters two phases.
    candidates = {best_i}
    candidates.update(i for i in range(1, len(values)-1)
                      if values[i] >= values[i-1] and values[i] >= values[i+1])
    for i in candidates:
        left, right = logps[max(0, i-1)], logps[min(len(logps)-1, i+1)]
        fit = minimize_scalar(lambda x: -flux(x), bounds=(left, right), method='bounded',
                              options={'xatol': 2e-7, 'maxiter': 45})
        if fit.success and -fit.fun > best_g:
            best_logp, best_g = float(fit.x), float(-fit.fun)
    pc = math.exp(best_logp)
    return dict(mass_flux_kg_m2_s=float(best_g), critical_pressure_Pa=pc,
                choked=bool(pc > pd*(1+1e-5)))


class ChilldownModel:
    """Conservative rigid-volume mass / energy and one lumped thermal wall."""

    STATES_PER_CIRCUIT = 11

    def __init__(self, settings):
        self.settings = validate_settings(settings)
        self.circuits = self.settings['circuits']
        self.parameters, self._fluid_eos, self._flow_eos = [], [], []
        self._cache_key, self._cache_value = None, None
        initial = []
        for c in self.circuits:
            eos = CP.AbstractState('HEOS', c['fluid'])
            p_triple = eos.trivial_keyed_output(CP.iP_triple)
            if min(c['min_pressure_Pa'], c['back_pressure_Pa']) <= p_triple:
                raise ValueError(f"{c['label']}：最低压力和背压须高于纯工质三相点压力，当前模型不含固相。")
            if max(c['max_pressure_Pa'], c['supply_pressure_Pa']) > eos.pmax():
                raise ValueError(f"{c['label']}：压力上限或供给压力超过 HEOS 上限。")
            try:
                if c['initial_mode'] == 'PT':
                    eos.update(CP.PT_INPUTS, c['initial_pressure_Pa'], c['initial_temperature_K'])
                    if eos.phase() == CP.iphase_twophase:
                        raise ValueError('PT 不能唯一确定饱和两相库存；请选择 PQ。')
                else:
                    eos.update(CP.PQ_INPUTS, c['initial_pressure_Pa'], c['initial_quality'])
                init = _state_values(eos)
                if not eos.Ttriple() < init['temperature_K'] < eos.Tmax():
                    raise ValueError('初温超出无固相的 HEOS 有效域。')
                eos.update(CP.PT_INPUTS, c['supply_pressure_Pa'], c['supply_temperature_K'])
                source = _state_values(eos)
                if source['phase'] not in ('liquid', 'supercritical_liquid'):
                    raise ValueError('入口固定储源必须为单液相。')
                if not eos.Ttriple() < source['temperature_K'] < eos.Tmax():
                    raise ValueError('供给温度超出无固相的 HEOS 有效域。')
            except (ValueError, OverflowError) as exc:
                raise ValueError(f"{c['label']}：初态/储源物性错误：{exc}") from exc
            mass0 = init['density_kg_m3']*c['volume_m3']
            U0 = mass0*init['specific_internal_energy_J_kg']
            # Scale by a full-liquid inventory rather than the tiny warm-gas
            # initial mass, so filling does not produce badly scaled states.
            mscale = max(mass0, source['density_kg_m3']*c['volume_m3'], 1e-6)
            escale = max(abs(U0), mscale*max(abs(source['enthalpy_J_kg']), 1e5),
                         c['wall_heat_capacity_J_K']*max(c['wall_temperature_K'], 100.), 1.)
            par = dict(mass0=mass0, U0=U0, mass_scale=mscale, energy_scale=escale,
                       temperature_scale=max(c['wall_temperature_K'], 100.), initial=init,
                       source=source, min_temperature_K=float(eos.Ttriple()),
                       max_temperature_K=float(eos.Tmax()), max_pressure_Pa=float(eos.pmax()))
            # The reservoir's state is fixed. Its critical pressure need not be
            # rediscovered inside every ODE stage; open-side points still use
            # a fresh exact HEOS PS flash (no rounded-state interpolation).
            if c['inlet_CdA_m2'] > 0 and source['pressure_Pa'] > c['min_pressure_Pa']:
                par['source_flow_limit'] = hem_mass_flux(c['fluid'], source['pressure_Pa'],
                    source['enthalpy_J_kg'], source['entropy_J_kg_K'], c['min_pressure_Pa'], state=eos)
            else:
                par['source_flow_limit'] = None
            self.parameters.append(par)
            self._fluid_eos.append(CP.AbstractState('HEOS', c['fluid']))
            self._flow_eos.append(CP.AbstractState('HEOS', c['fluid']))
            initial.extend([mass0/mscale, U0/escale, c['wall_temperature_K']/par['temperature_scale'],
                            c['initial_inlet_opening'], c['initial_outlet_opening'], 0., 0., 0., 0., 0., 0.])
        self.initial = np.array(initial, dtype=float)
        self.model_description = dict(
            name='纯工质预冷与充填研究单元', experiment_validated=False,
            topology='每回路：固定单液相储源 → 准稳态入口孔/止回阀 → 刚性均匀容腔 + 集总金属壁面 → 均相混合物出口/止回阀 → 固定背压。',
            equations=['dm/dt = ṁin − ṁout；ρ=m/V；u=U/m；(p,T,phase)=HEOS(ρ,u)',
                       'dU/dt = ṁin hin − ṁout h + UA(Twall − T)',
                       'Cwall dTwall/dt = UAambient(Tambient − Twall) − UA(Twall − T)',
                       'G(p)=ρ(p,s0)√[2(h0−h(p,s0))]；ṁ=CdA·a·max G(p), p∈[pdown,pup]',
                       'da/dt = clip[(acommand−a)/τ, −rate, +rate]',
                       'αv=(x/ρv)/[(1−x)/ρl+x/ρv]；mv=xm；ml=(1−x)m'],
            property_backend=dict(name='CoolProp HEOS', version=CoolProp.__version__,
                                  closure='DmassUmass；纯工质均相热力学平衡', reference_state='CoolProp 默认参考态；同一工质所有边界与库存使用同一参考态'),
            heat_transfer='常数 UA 与壁面热容量为用户输入；不含膜态/过渡/核态沸腾关联式或试验标定。相变潜热已由 EOS 包含，不另加潜热源项。',
            flow_model='准稳态 HEM 等熵孔口 + 最大质量通量临界限制；入口/出口带理想止回。不是惯性管段、可反流支路或经验阀特性。',
            outlet_model='出口为容腔均匀混合物，总焓随质量流携出；不是仅排蒸气的空间放空口。',
            inventory_model='m 与 U 是独立状态；气液在相同温压瞬时平衡，无滑移、分层、移动前沿或不凝气体。',
            actuator_model='开度一阶滞后并限速；全闭有效面积为零。积分器试探点的面积只在[0,1]外延拓，接受态越界则停止。',
            limits='不含固相、气蚀、泵/轴动力学、实际启动/点火/燃烧或整机初值自动覆盖。超临界状态不划分气液库存。',
            circuits=[dict(id=c['id'], label=c['label'], fluid=c['fluid'], source=deepcopy(p['source']))
                      for c, p in zip(self.circuits, self.parameters)])
        self.initialization_snapshot = self.snapshot(0., self.initial)

    def command(self, t, initial, final):
        fraction = min(1., max(0., (t-self.settings['command_time_s'])/self.settings['command_ramp_s']))
        return initial+(final-initial)*fraction

    def command_for(self, t, circuit, port):
        """Per-port command hook; ordinary chilldown keeps its one transition."""
        return self.command(t, circuit[f'initial_{port}_opening'], circuit[f'final_{port}_opening'])

    def event_times(self):
        """Known command slope changes at which the integrator must restart."""
        return [self.settings['command_time_s'],
                self.settings['command_time_s']+self.settings['command_ramp_s']]

    def output_times(self):
        """Ordinary chilldown retains its original requested output grid."""
        return np.linspace(0., self.settings['duration_s'], self.settings['sample_count'])

    def _flash(self, i, m, U):
        c, par, eos = self.circuits[i], self.parameters[i], self._fluid_eos[i]
        if not math.isfinite(m) or not math.isfinite(U) or m <= 0:
            raise PropertyDomainError(f"{c['label']}：非正质量或非有限内能，无法闭合物性。")
        try:
            eos.update(CP.DmassUmass_INPUTS, m/c['volume_m3'], U/m)
            result = _state_values(eos)
        except (ValueError, OverflowError) as exc:
            raise PropertyDomainError(f"{c['label']}：ρ-u 物性闭合失败：{exc}") from exc
        if not par['min_temperature_K'] <= result['temperature_K'] <= par['max_temperature_K']:
            raise PropertyDomainError(f"{c['label']}：温度超出 HEOS 无固相有效域。")
        if result['pressure_Pa'] > par['max_pressure_Pa']:
            raise PropertyDomainError(f"{c['label']}：压力超出 HEOS 有效域。")
        result['eos_density_relative_error'] = result['density_kg_m3']/(m/c['volume_m3'])-1.
        result['eos_internal_energy_error_J_kg'] = result['specific_internal_energy_J_kg']-U/m
        return result

    def evaluate(self, t, y):
        checkpoint()
        key = (float(t), np.asarray(y).tobytes())
        if key == self._cache_key:
            return self._cache_value
        derivative = np.zeros_like(y, dtype=float)
        rows = []
        for i, (c, par) in enumerate(zip(self.circuits, self.parameters)):
            j = i*self.STATES_PER_CIRCUIT
            m, U = float(y[j]*par['mass_scale']), float(y[j+1]*par['energy_scale'])
            Tw = float(y[j+2]*par['temperature_scale'])
            ai, ao = map(float, y[j+3:j+5])
            st = self._flash(i, m, U)
            source = par['source']
            icmd = self.command_for(t, c, 'inlet')
            ocmd = self.command_for(t, c, 'outlet')
            area_in = c['inlet_CdA_m2']*min(1., max(0., ai))
            area_out = c['outlet_CdA_m2']*min(1., max(0., ao))
            inlet = dict(mass_flux_kg_m2_s=0., choked=False, critical_pressure_Pa=source['pressure_Pa'])
            outlet = dict(mass_flux_kg_m2_s=0., choked=False, critical_pressure_Pa=st['pressure_Pa'])
            if area_in > 0 and source['pressure_Pa'] > st['pressure_Pa']:
                limit = par['source_flow_limit']
                if limit is not None and st['pressure_Pa'] >= c['min_pressure_Pa']:
                    if st['pressure_Pa'] <= limit['critical_pressure_Pa']:
                        inlet = dict(limit, choked=bool(limit['critical_pressure_Pa'] > st['pressure_Pa']*(1+1e-5)))
                    else:
                        inlet = dict(mass_flux_kg_m2_s=_isentropic_flux_at_pressure(self._flow_eos[i], c['fluid'],
                            source['pressure_Pa'], source['enthalpy_J_kg'], source['entropy_J_kg_K'], st['pressure_Pa']),
                            critical_pressure_Pa=st['pressure_Pa'], choked=False)
                else:
                    inlet = hem_mass_flux(c['fluid'], source['pressure_Pa'], source['enthalpy_J_kg'],
                                          source['entropy_J_kg_K'], st['pressure_Pa'], state=self._flow_eos[i])
            if area_out > 0 and st['pressure_Pa'] > c['back_pressure_Pa']:
                outlet = hem_mass_flux(c['fluid'], st['pressure_Pa'], st['enthalpy_J_kg'],
                                       st['entropy_J_kg_K'], c['back_pressure_Pa'], state=self._flow_eos[i])
            mi, mo = area_in*inlet['mass_flux_kg_m2_s'], area_out*outlet['mass_flux_kg_m2_s']
            Qw = c['wall_UA_W_K']*(Tw-st['temperature_K'])
            Qa = c['ambient_UA_W_K']*(c['ambient_temperature_K']-Tw)
            Ein, Eout = mi*source['enthalpy_J_kg'], mo*st['enthalpy_J_kg']
            rate = c['valve_rate_per_s']
            dai = min(rate, max(-rate, (icmd-ai)/c['valve_tau_s']))
            dao = min(rate, max(-rate, (ocmd-ao)/c['valve_tau_s']))
            derivative[j:j+5] = [(mi-mo)/par['mass_scale'], (Ein-Eout+Qw)/par['energy_scale'],
                (Qa-Qw)/c['wall_heat_capacity_J_K']/par['temperature_scale'], dai, dao]
            derivative[j+5:j+11] = [mi/par['mass_scale'], mo/par['mass_scale'],
                Ein/par['energy_scale'], Eout/par['energy_scale'], Qa/par['energy_scale'], Qw/par['energy_scale']]
            q = st['quality']
            rows.append(dict(id=c['id'], label=c['label'], fluid=c['fluid'], **st,
                mass_kg=m, fluid_internal_energy_J=U, volume_m3=c['volume_m3'], wall_temperature_K=Tw,
                liquid_mass_kg=None if q is None else (1-q)*m,
                vapor_mass_kg=None if q is None else q*m,
                inlet_mass_flow_kg_s=mi, outlet_mass_flow_kg_s=mo,
                heat_to_fluid_W=Qw, heat_from_ambient_W=Qa,
                inlet_enthalpy_flow_W=Ein, outlet_enthalpy_flow_W=Eout,
                inlet_opening=ai, outlet_opening=ao, inlet_command=icmd, outlet_command=ocmd,
                inlet_choked=inlet['choked'], outlet_choked=outlet['choked'],
                inlet_critical_pressure_Pa=inlet['critical_pressure_Pa'],
                outlet_critical_pressure_Pa=outlet['critical_pressure_Pa']))
        self._cache_key, self._cache_value = key, (derivative, rows)
        return derivative, rows

    def rhs(self, t, y):
        return self.evaluate(t, y)[0]

    def sample(self, t, y):
        _, source_rows = self.evaluate(t, y)
        rows = deepcopy(source_rows)
        for i, (c, par, row) in enumerate(zip(self.circuits, self.parameters, rows)):
            j = i*self.STATES_PER_CIRCUIT
            Mi, Mo = (float(v*par['mass_scale']) for v in y[j+5:j+7])
            Ei, Eo, Qa, Qw = (float(v*par['energy_scale']) for v in y[j+7:j+11])
            wall_delta = c['wall_heat_capacity_J_K']*(row['wall_temperature_K']-c['wall_temperature_K'])
            fluid_delta = row['fluid_internal_energy_J']-par['U0']
            row.update(integrated_inlet_mass_kg=Mi, integrated_outlet_mass_kg=Mo,
                integrated_inlet_energy_J=Ei, integrated_outlet_energy_J=Eo,
                integrated_ambient_heat_J=Qa, integrated_wall_heat_J=Qw,
                wall_energy_change_J=wall_delta, fluid_energy_change_J=fluid_delta,
                mass_balance_error_kg=row['mass_kg']-par['mass0']-Mi+Mo,
                energy_balance_error_J=fluid_delta+wall_delta-Ei+Eo-Qa,
                fluid_energy_balance_error_J=fluid_delta-Ei+Eo-Qw,
                wall_energy_balance_error_J=wall_delta-Qa+Qw)
        return dict(time_s=float(t), circuits=rows)

    def snapshot(self, t, y):
        rows = self.sample(t, y)['circuits']
        return dict(schema='chilldown-state-v1', time_s=float(t),
            purpose='本页独立容腔/壁面的可追溯一致初态；不是整机点火初值',
            property_backend='CoolProp HEOS', property_version=CoolProp.__version__,
            circuits=[dict(id=c['id'], label=c['label'], fluid=c['fluid'],
                volume_m3=c['volume_m3'], mass_kg=r['mass_kg'],
                fluid_internal_energy_J=r['fluid_internal_energy_J'],
                wall_temperature_K=r['wall_temperature_K'], wall_heat_capacity_J_K=c['wall_heat_capacity_J_K'],
                pressure_Pa=r['pressure_Pa'], temperature_K=r['temperature_K'], density_kg_m3=r['density_kg_m3'],
                specific_internal_energy_J_kg=r['specific_internal_energy_J_kg'], phase=r['phase'],
                quality=r['quality'], void_fraction=r['void_fraction'], liquid_volume_fraction=r['liquid_volume_fraction'],
                inlet_opening=r['inlet_opening'], outlet_opening=r['outlet_opening'],
                eos_density_relative_error=r['eos_density_relative_error'],
                eos_internal_energy_error_J_kg=r['eos_internal_energy_error_J_kg'])
                for c, r in zip(self.circuits, rows)])

    def validity_margins(self, t, y):
        _, rows = self.evaluate(t, y)
        margins = []
        for i, (c, par, row) in enumerate(zip(self.circuits, self.parameters, rows)):
            scale = max(c['max_pressure_Pa'], 1.)
            label = c['label']
            tol = max(1e-8, self.settings['rtol']*.1)
            margins.extend([
                (f'{label}：容腔达到最低允许压力', (row['pressure_Pa']-c['min_pressure_Pa'])/scale),
                (f'{label}：容腔达到最高允许压力', (c['max_pressure_Pa']-row['pressure_Pa'])/scale),
                (f'{label}：壁温达到上限', (c['max_wall_temperature_K']-row['wall_temperature_K'])/par['temperature_scale']),
                (f'{label}：壁温降至非物理绝对零度', row['wall_temperature_K']/par['temperature_scale']),
                (f'{label}：流体温度达到无固相物性下限', (row['temperature_K']-par['min_temperature_K'])/par['temperature_scale']),
                (f'{label}：流体温度达到物性上限', (par['max_temperature_K']-row['temperature_K'])/par['temperature_scale']),
                (f'{label}：入口阀行程越界', min(row['inlet_opening'], 1-row['inlet_opening'])+tol),
                (f'{label}：出口阀行程越界', min(row['outlet_opening'], 1-row['outlet_opening'])+tol)])
        return margins

    def validity_margin(self, t, y):
        return min(value for _, value in self.validity_margins(t, y))

    def audits(self, samples):
        mass_error = energy_error = mass_rel = energy_rel = eos_rho_error = eos_u_error = phase_error = 0.
        fluid_error = wall_error = 0.
        for s in samples:
            for row, par in zip(s['circuits'], self.parameters):
                mass_error = max(mass_error, abs(row['mass_balance_error_kg']))
                energy_error = max(energy_error, abs(row['energy_balance_error_J']))
                fluid_error = max(fluid_error, abs(row['fluid_energy_balance_error_J']))
                wall_error = max(wall_error, abs(row['wall_energy_balance_error_J']))
                mass_rel = max(mass_rel, abs(row['mass_balance_error_kg'])/par['mass_scale'])
                energy_rel = max(energy_rel, abs(row['energy_balance_error_J'])/par['energy_scale'],
                                 abs(row['fluid_energy_balance_error_J'])/par['energy_scale'],
                                 abs(row['wall_energy_balance_error_J'])/par['energy_scale'])
                eos_rho_error = max(eos_rho_error, abs(row['eos_density_relative_error']))
                eos_u_error = max(eos_u_error, abs(row['eos_internal_energy_error_J_kg']))
                phase_error = max(phase_error, abs(row['phase_volume_closure_relative_error'] or 0.))
        return dict(status='pass' if max(mass_rel, energy_rel, eos_rho_error, phase_error) <= max(2e-7, self.settings['rtol']*10) and eos_u_error <= .02 else 'review',
            max_abs_mass_balance_error_kg=mass_error, max_abs_energy_balance_error_J=energy_error,
            max_abs_fluid_energy_balance_error_J=fluid_error, max_abs_wall_energy_balance_error_J=wall_error,
            mass_relative_error=mass_rel, energy_relative_error=energy_rel,
            max_eos_density_relative_error=eos_rho_error, max_eos_specific_energy_error_J_kg=eos_u_error,
            max_phase_volume_closure_relative_error=phase_error,
            basis='逐回路检查质量库存、流体+壁面总能量与边界积分；同时检查 ρ-u 物性回算及气液体积闭合。回路误差不互相抵消。',
            experiment_validated=False)

    def readiness(self, samples, complete):
        checks = []
        for c, row in zip(self.circuits, samples[-1]['circuits']):
            wall_ok = row['wall_temperature_K'] <= c['target_wall_temperature_K']
            liquid = row['liquid_volume_fraction']
            liquid_ok = liquid is not None and liquid >= c['target_liquid_volume_fraction']
            checks.append(dict(id=c['id'], label=c['label'], wall_temperature_met=wall_ok,
                liquid_volume_met=liquid_ok, wall_temperature_K=row['wall_temperature_K'],
                target_wall_temperature_K=c['target_wall_temperature_K'], liquid_volume_fraction=liquid,
                target_liquid_volume_fraction=c['target_liquid_volume_fraction'],
                status='met' if wall_ok and liquid_ok else 'not_met'))
        return dict(status='incomplete' if not complete else 'met' if all(x['status']=='met' for x in checks) else 'not_met',
                    checks=checks, ignition_ready=False,
                    basis='仅用户壁温与液相体积分数阈值；不代表泵启动、点火或整机启动条件已满足。')

    def integrate(self, publish=None):
        """Adaptive Radau, accepted-step domain detection, preserved valid samples.

        Restarting a rejected property-domain trial uses a smaller time step;
        it never modifies m/U or substitutes a different EOS.  A persistent
        domain failure terminates at the last accepted state.
        """
        s = self.settings
        times = self.output_times()
        breaks = sorted(set([0., s['duration_s'], *self.event_times()]))
        breaks = [t for t in breaks if 0 <= t <= s['duration_s']]
        y, t = self.initial.copy(), 0.
        samples = [self.sample(t, y)]
        if publish:
            publish(samples[0])
        next_sample = 1
        complete, reason, nfev, retries = True, None, 0, 0
        max_step = min(.5, s['duration_s']/20)
        # Circuits are independent and none of the six diagnostic ledgers
        # feeds back into physics.  Coloring this exact dependency pattern
        # avoids re-flashing every fluid for each zero Jacobian column.
        sparsity = np.zeros((len(y), len(y)), dtype=bool)
        for i in range(len(self.circuits)):
            j = i*self.STATES_PER_CIRCUIT
            sparsity[j:j+self.STATES_PER_CIRCUIT, j:j+5] = True

        def append_sample(when, state):
            row = self.sample(when, state)
            if row['time_s'] > samples[-1]['time_s']+1e-12:
                samples.append(row)
                if publish:
                    publish(row)

        for start, end in zip(breaks, breaks[1:]):
            if not complete:
                break
            step_limit = min(max_step, end-start)
            failures = 0
            solver = None
            while t < end-1e-12:
                checkpoint()
                try:
                    if solver is None:
                        solver = Radau(self.rhs, t, y, end, rtol=s['rtol'], atol=s['rtol']*.001,
                            max_step=step_limit, first_step=min(step_limit, min(c['valve_tau_s'] for c in self.circuits)*.02),
                            jac_sparsity=sparsity)
                    old_t, old_y = t, y.copy()
                    message = solver.step()
                    if solver.status == 'failed':
                        complete, reason = False, '数值积分未完成：'+str(message)
                        nfev += solver.nfev
                        break
                    new_t, new_y = float(solver.t), solver.y.copy()
                    dense = solver.dense_output()
                    margin = self.validity_margin(new_t, new_y)
                    if margin <= 0:
                        root = brentq(lambda x: self.validity_margin(x, dense(x)), old_t, new_t,
                                      xtol=1e-11, rtol=1e-12)
                        new_t, new_y = float(root), dense(root)
                        reason = min(self.validity_margins(new_t, new_y), key=lambda x: x[1])[0]
                        complete = False
                    while next_sample < len(times) and times[next_sample] <= new_t+1e-12:
                        stime = float(times[next_sample])
                        append_sample(stime, dense(stime))
                        next_sample += 1
                    t, y = new_t, new_y
                    failures = 0
                    if not complete:
                        append_sample(t, y)
                        nfev += solver.nfev
                        break
                    if solver.status == 'finished':
                        nfev += solver.nfev
                        break
                except (PropertyDomainError, ValueError, OverflowError, FloatingPointError) as exc:
                    # No state or phase clamp: retry from the last accepted state.
                    failures += 1
                    retries += 1
                    if solver is not None:
                        nfev += solver.nfev
                    solver = None
                    step_limit *= .25
                    if failures >= 7 or step_limit < 1e-10:
                        complete = False
                        reason = '物性/积分适用域终止；保留最后有效状态：'+str(exc)
                        append_sample(t, y)
                        break
        # Include the true final accepted state even for output-grid gaps.
        append_sample(t, y)
        metadata = dict(complete=complete and t >= s['duration_s']-1e-9,
            termination_reason=reason, audits=self.audits(samples), model_description=self.model_description,
            initialization_snapshot=self.initialization_snapshot, endpoint_snapshot=self.snapshot(t, y),
            readiness=self.readiness(samples, complete and t >= s['duration_s']-1e-9),
            integrator=dict(method='Radau', rtol=s['rtol'], atol_scaled=s['rtol']*.001,
                nfev=nfev, property_trial_retries=retries, state_count=len(y),
                physical_states=5*len(self.circuits), conservation_ledger_states=6*len(self.circuits)),
            flow_solver=dict(model='HEM isentropic orifice', coarse_log_pressure_points=25,
                             maximum_refinement_log_pressure_tolerance=2e-7))
        return samples, metadata
