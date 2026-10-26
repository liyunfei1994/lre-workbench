"""Prescribed-igniter, gas-fed well-stirred chamber research model.

Cantera integrates finite-rate chemistry.  Boundary enthalpy, igniter heat and
wall heat are added to its conservative control-volume equations; chemical
formation energy is already in the species internal energies.  This is not a
liquid-injection model or a prediction of an engine's ignition success.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path

import numpy as np
import cantera as ct

from task_control import checkpoint


def default_settings():
    return dict(duration_s=3., sample_count=301, rtol=1e-7,
        chemistry_enabled=True, volume_m3=.001, initial_pressure_Pa=1e5,
        initial_temperature_K=300., initial_mixture='nitrogen', initial_of_ratio=4.,
        wall_temperature_K=300., wall_heat_capacity_J_K=100., wall_UA_W_K=2.,
        ambient_temperature_K=300., ambient_UA_W_K=0.,
        supply_ox_pressure_Pa=1e6, supply_fuel_pressure_Pa=1e6,
        supply_ox_temperature_K=300., supply_fuel_temperature_K=300.,
        inlet_ox_CdA_m2=4e-7, inlet_fuel_CdA_m2=4e-8, outlet_CdA_m2=3e-7,
        back_pressure_Pa=1e5, ox_open_time_s=.05, fuel_open_time_s=.05,
        valve_ramp_s=.05, valve_tau_s=.02, valve_rate_per_s=20.,
        initial_ox_opening=0., initial_fuel_opening=0.,
        final_ox_opening=1., final_fuel_opening=1.,
        ignition_time_s=.3, ignition_duration_s=.02, ignition_energy_J=1500.,
        min_pressure_Pa=1e4, max_pressure_Pa=3e6,
        min_temperature_K=300., max_temperature_K=3000.,
        max_wall_temperature_K=1500., reference_pressure_Pa=3e5)


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('点火与建压设置必须是对象。')
    s = deepcopy(settings)
    expected = set(default_settings())
    if set(s) != expected:
        raise ValueError('点火与建压设置字段不完整或存在未知字段：' + ', '.join(sorted(set(s)^expected)))
    if not isinstance(s['chemistry_enabled'], bool):
        raise ValueError('chemistry_enabled 必须为布尔值。')
    if s['initial_mixture'] not in ('nitrogen', 'reactants'):
        raise ValueError('初始组分必须为 nitrogen 或 reactants。')
    if isinstance(s['sample_count'], bool) or not isinstance(s['sample_count'], int) or not 2 <= s['sample_count'] <= 2001:
        raise ValueError('sample_count 必须为 2 至 2001 的整数。')
    ranges = {
        'duration_s':(.001,30.), 'rtol':(1e-10,1e-4), 'volume_m3':(1e-7,1.),
        'initial_of_ratio':(.01,100.), 'wall_heat_capacity_J_K':(.001,1e7),
        'wall_UA_W_K':(0.,1e5), 'ambient_UA_W_K':(0.,1e5),
        'inlet_ox_CdA_m2':(0.,.01), 'inlet_fuel_CdA_m2':(0.,.01), 'outlet_CdA_m2':(0.,.01),
        'ox_open_time_s':(0.,30.), 'fuel_open_time_s':(0.,30.),
        'valve_ramp_s':(1e-5,30.), 'valve_tau_s':(1e-5,30.), 'valve_rate_per_s':(.001,1e5),
        'ignition_time_s':(0.,30.), 'ignition_duration_s':(1e-6,30.), 'ignition_energy_J':(0.,1e7),
        'initial_ox_opening':(0.,1.), 'initial_fuel_opening':(0.,1.),
        'final_ox_opening':(0.,1.), 'final_fuel_opening':(0.,1.),
        'initial_temperature_K':(300.,3000.), 'supply_ox_temperature_K':(300.,3000.),
        'supply_fuel_temperature_K':(300.,3000.), 'min_temperature_K':(300.,2999.),
        'max_temperature_K':(301.,3000.), 'wall_temperature_K':(100.,3000.),
        'ambient_temperature_K':(100.,3000.), 'max_wall_temperature_K':(101.,3000.),
    }
    for key in expected - {'sample_count','chemistry_enabled','initial_mixture'}:
        value = s[key]
        try:
            valid_number=not isinstance(value,bool) and isinstance(value,(float,int)) and math.isfinite(value)
        except (OverflowError,ValueError,TypeError):
            valid_number=False
        if not valid_number:
            raise ValueError(f'{key}: 必须为有限数值。')
        lo, hi = ranges.get(key,(1.,1e8))
        if not lo <= value <= hi:
            raise ValueError(f'{key}: 超出允许范围 [{lo}, {hi}]。')
        s[key] = float(value)
    if not s['min_pressure_Pa'] < s['initial_pressure_Pa'] < s['max_pressure_Pa']:
        raise ValueError('初始压力必须严格位于压力保护边界内。')
    if not s['min_temperature_K'] <= s['initial_temperature_K'] < s['max_temperature_K']:
        raise ValueError('初始温度必须位于气相热物性温度边界内。')
    if not s['wall_temperature_K'] < s['max_wall_temperature_K']:
        raise ValueError('初始壁温必须小于壁温上限。')
    if s['ignition_time_s'] + s['ignition_duration_s'] > s['duration_s'] + 1e-12:
        raise ValueError('点火热脉冲必须在积分时间范围内结束。')
    if max(s['ox_open_time_s'],s['fuel_open_time_s']) > s['duration_s']:
        raise ValueError('阀门动作起始时刻不能晚于积分结束。')
    return s


def gas_mass_flux(pressure, temperature, downstream, gamma, gas_constant):
    """Calorically-perfect frozen-property restriction, kg/m²/s.

    The caller updates gamma and R from the instantaneous gas composition.
    Reverse pressure difference gives zero flow through the declared check
    valve.  This is a restriction model, not a full de Laval nozzle solution.
    """
    # An EOS round-trip of an exactly equal pressure can differ by a few ULPs.
    # Removing ONLY that machine round-off avoids an artificial tiny outflow
    # and square-root chatter at a closed check valve; physical states are not
    # clipped or changed.
    if pressure-downstream <= 16*math.ulp(max(abs(pressure),abs(downstream),1.)):
        return 0., False
    if not (pressure > 0 and temperature > 0 and downstream >= 0 and gamma > 1 and gas_constant > 0):
        raise ValueError('可压缩流量公式收到无效气体状态。')
    ratio = downstream/pressure
    critical = (2/(gamma+1))**(gamma/(gamma-1))
    choked = ratio <= critical
    factor = (math.sqrt(gamma)*(2/(gamma+1))**((gamma+1)/(2*(gamma-1))) if choked else
        math.sqrt(max(0.,2*gamma/(gamma-1)*ratio**(2/gamma)*(-math.expm1((gamma-1)/gamma*math.log(ratio))))))
    return pressure/math.sqrt(gas_constant*temperature)*factor, choked


def _gas():
    path = Path(ct.__file__).resolve().parent/'data'/'gri30.yaml'
    return ct.Solution(yaml=path.read_text(encoding='utf-8'),transport_model=None)


class _Chamber(ct.ExtensibleIdealGasReactor):
    """Use Cantera's chemistry equations and add physical boundary terms."""
    def after_initialize(self, t0):
        self.base_size = self.n_vars
        self.n_vars += len(self.extra)

    def after_get_state(self, y):
        for j,value in enumerate(self.extra):
            y[self.base_size+j] = value

    def after_update_state(self, y):
        self.extra[:] = y[self.base_size:]

    def before_component_name(self, index):
        if index >= self.base_size:
            return 'boundary_'+self.owner.extra_names[index-self.base_size]

    def after_eval(self, t, LHS, RHS):
        model = self.owner
        gas = self.phase
        flow = model._flows(t)
        mo, mf, out = flow['ox'], flow['fuel'], flow['out']
        minlet = mo+mf
        u = gas.partial_molar_int_energies/gas.molecular_weights
        hin = mo*model.sources['ox']['h']+mf*model.sources['fuel']['h']
        uin = mo*u[model.ox_index]+mf*u[model.fuel_index]
        qw = model.settings['wall_UA_W_K']*(self.T-self.extra[0])
        qa = model.settings['ambient_UA_W_K']*(model.settings['ambient_temperature_K']-self.extra[0])
        qign = model.segment_igniter_power
        # Base LHS: m*cv for T, m for each Y. Base RHS already includes
        # finite-rate chemical species sources and -sum(u_k*omega_k*W_k*V).
        RHS[0] += minlet-out
        RHS[2] += hin-uin-(gas.P*self.volume/self.mass)*out+qign-qw
        for k,Y in enumerate(gas.Y):
            RHS[3+k] -= minlet*Y
        RHS[3+model.ox_index] += mo
        RHS[3+model.fuel_index] += mf
        tail = self.base_size
        for j in range(len(self.extra)):
            LHS[tail+j] = 1.
        RHS[tail] = (qw+qa)/model.settings['wall_heat_capacity_J_K']
        for j, side in enumerate(('ox','fuel'),1):
            target = model.command(t,side)
            RHS[tail+j] = np.clip((target-self.extra[j])/model.settings['valve_tau_s'],
                -model.settings['valve_rate_per_s'],model.settings['valve_rate_per_s'])
        for j,value in enumerate([minlet,out,hin,out*gas.enthalpy_mass,qign,qw,qa],3):
            RHS[tail+j] = value
        for j, element in enumerate(model.elements):
            RHS[tail+10+j] = mo*model.sources['ox']['elements'][j]+mf*model.sources['fuel']['elements'][j]
            RHS[tail+10+len(model.elements)+j] = out*gas.elemental_mass_fraction(element)


class IgnitionModel:
    def __init__(self, settings):
        self.settings = s = validate_settings(settings)
        gas = _gas()
        self.elements = list(gas.element_names)
        self.ox_index, self.fuel_index = gas.species_index('O2'), gas.species_index('CH4')
        self.sources = {}
        for side,species in (('ox','O2'),('fuel','CH4')):
            gas.TPX = s[f'supply_{side}_temperature_K'],s[f'supply_{side}_pressure_Pa'],species+':1'
            self.sources[side] = dict(p=gas.P,T=gas.T,h=gas.enthalpy_mass,
                gamma=gas.cp_mass/gas.cv_mass,R=ct.gas_constant/gas.mean_molecular_weight,
                elements=[gas.elemental_mass_fraction(e) for e in self.elements])
        if s['initial_mixture']=='nitrogen':
            gas.TPX = s['initial_temperature_K'],s['initial_pressure_Pa'],'N2:1'
        else:
            gas.TPY = s['initial_temperature_K'],s['initial_pressure_Pa'],{'O2':s['initial_of_ratio'],'CH4':1.}
        self.extra_names = ['wall_temperature','ox_opening','fuel_opening','mass_in','mass_out',
            'enthalpy_in','enthalpy_out','igniter_heat','heat_to_wall','ambient_heat'] + [
            f'element_{direction}_{e}' for direction in ('in','out') for e in self.elements]
        self.reactor = _Chamber(gas,clone=False,volume=s['volume_m3'])
        self.reactor.owner = self
        self.reactor.extra = np.r_[s['wall_temperature_K'],s['initial_ox_opening'],s['initial_fuel_opening'],np.zeros(len(self.extra_names)-3)]
        self.reactor.chemistry_enabled = s['chemistry_enabled']
        self.segment_igniter_power = 0.
        self.net = ct.ReactorNet([self.reactor])
        # The scalar absolute tolerance also applies to initially zero
        # boundary ledgers. An unnecessarily tiny value forces the very first
        # finite-energy pulse step below floating-point time resolution.
        self.net.rtol, self.net.atol = s['rtol'], max(1e-16,s['rtol']*1e-6)
        self.guard_step_s = min(.002,s['duration_s']/1000.,s['duration_s']/(s['sample_count']-1)/2)
        self.net.max_time_step = self.guard_step_s
        self.net.max_steps = 100000
        self.net.initialize()
        self.initial_mass = self.reactor.mass
        self.initial_energy = self.reactor.mass*self.reactor.phase.int_energy_mass
        self.initial_elements = {e:self.reactor.mass*self.reactor.phase.elemental_mass_fraction(e) for e in self.elements}
        self.model_description = dict(name='给定点火热脉冲下的气相容腔建压研究台',
            topology='固定气相 O2/CH4 压力源 → 独立执行阀 → 均匀混合反应容腔与集总壁 → 准稳态止回节流出口 → 固定背压',
            chemistry='Cantera GRI-Mech 3.0 有限速率反应；机理全过程启用或作为对照全过程关闭。',
            mechanism='gri30.yaml',cantera_version=ct.__version__,experiment_validated=False,
            equations=['dm_k/dt=Σṁ_in Y_k,in−ṁ_out Y_k+V W_k ω_k',
                'dU/dt=Σṁ_in h_in−ṁ_out h+Q_ign−UA(T−T_wall)',
                'C_wall dT_wall/dt=UA(T−T_wall)+UA_amb(T_amb−T_wall)',
                'da/dt=clip((a_cmd−a)/τ,−rate,+rate)'],
            energy_reference='Cantera 物种热力学基准包含生成焓；不另外添加低位热值，化学放热率仅作诊断。',
            flow_model='瞬时组分的 cp/cv 与 R 冻结于节流公式；未壅塞与壅塞分支连续，逆压差按理想止回阀处理。无扩张段、分离和推力预测。',
            inlet_phase='仅 300–3000 K 理想气相，不接收低温液体、两相预冷末态或液体泵接口。',
            limitations=['零维完全混合；无雾化、蒸发、混合速率和局部点火核。',
                '给定热脉冲向整个气体容腔输入能量；不是实际点火器沉积效率或点火成功模型。',
                'GRI-Mech 3.0 未由本平台针对高压液氧甲烷发动机启动标定。',
                '不耦合泵、涡轮、低温管网、GG/FFSC 自启动、控制器或整机推力。',
                '压力参考线仅作响应观测，未设置点火成功、延迟或熄火判据。'],
            common_thermo_range_K=[300.,3000.],ignition_success_criterion_enabled=False,
            ignition_delay_criterion_enabled=False,extinction_criterion_enabled=False)
        self.initialization_snapshot = self._snapshot(0.)

    def command(self, t, side):
        s = self.settings
        f = min(1.,max(0.,(t-s[f'{side}_open_time_s'])/s['valve_ramp_s']))
        return s[f'initial_{side}_opening']+f*(s[f'final_{side}_opening']-s[f'initial_{side}_opening'])

    def _power(self,t):
        s = self.settings
        return s['ignition_energy_J']/s['ignition_duration_s'] if round(s['ignition_time_s'],14) <= t < round(s['ignition_time_s']+s['ignition_duration_s'],14) else 0.

    def _flows(self,t):
        r,s = self.reactor,self.settings
        gas = r.phase
        result = {}
        for j,side in enumerate(('ox','fuel'),1):
            source = self.sources[side]
            flux,choked = gas_mass_flux(source['p'],source['T'],gas.P,source['gamma'],source['R'])
            # Extend only the flow area for CVODES trial states, retain raw
            # actuator states and reject accepted excursions in _invalid().
            result[side] = s[f'inlet_{side}_CdA_m2']*min(1.,max(0.,r.extra[j]))*flux
            result[side+'_choked'] = choked and result[side]>0
        flux,choked = gas_mass_flux(gas.P,r.T,s['back_pressure_Pa'],gas.cp_mass/gas.cv_mass,ct.gas_constant/gas.mean_molecular_weight)
        result.update(out=s['outlet_CdA_m2']*flux,out_choked=choked and s['outlet_CdA_m2']>0)
        return result

    def _invalid(self):
        r,s = self.reactor,self.settings
        if not math.isfinite(r.T) or not math.isfinite(r.phase.P) or r.mass<=0:
            return '容腔状态非有限或质量非正'
        if r.phase.P < s['min_pressure_Pa']*(1-1e-9) or r.phase.P > s['max_pressure_Pa']*(1+1e-9):
            return '容腔压力到达设置的适用边界'
        if r.T < s['min_temperature_K']-1e-6 or r.T > s['max_temperature_K']+1e-6:
            return '容腔温度到达设置的气相热物性适用边界'
        if r.extra[0]<=0 or r.extra[0] > s['max_wall_temperature_K']:
            return '壁面温度到达设置的适用边界'
        if min(r.extra[1:3]) < -1e-7 or max(r.extra[1:3]) > 1+1e-7:
            return '执行阀行程超过物理范围'
        if float(np.min(r.phase.Y)) < -max(1e-10,10*s['rtol']*1e-9):
            return '组分质量分数出现超过数值容差的负值'
        return None

    def sample(self,t):
        r,s=self.reactor,self.settings
        gas,e=r.phase,r.extra
        f=self._flows(t)
        U=r.mass*gas.int_energy_mass
        wall_energy=s['wall_heat_capacity_J_K']*(e[0]-s['wall_temperature_K'])
        fluid_error=U-self.initial_energy-e[5]+e[6]-e[7]+e[8]
        wall_error=wall_energy-e[8]-e[9]
        elements={name:r.mass*gas.elemental_mass_fraction(name)-self.initial_elements[name]-e[10+j]+e[10+len(self.elements)+j] for j,name in enumerate(self.elements)}
        return dict(time_s=float(t),pressure_Pa=float(gas.P),temperature_K=float(r.T),
            wall_temperature_K=float(e[0]),mass_kg=float(r.mass),internal_energy_J=float(U),
            density_kg_m3=float(gas.density),enthalpy_J_kg=float(gas.enthalpy_mass),
            ox_mass_flow_kg_s=float(f['ox']),fuel_mass_flow_kg_s=float(f['fuel']),outlet_mass_flow_kg_s=float(f['out']),
            ox_opening=float(e[1]),fuel_opening=float(e[2]),ox_command=self.command(t,'ox'),fuel_command=self.command(t,'fuel'),
            igniter_power_W=self._power(t),chemical_heat_release_W=float(gas.heat_release_rate*r.volume) if s['chemistry_enabled'] else 0.,
            heat_to_wall_W=float(s['wall_UA_W_K']*(r.T-e[0])),heat_from_ambient_W=float(s['ambient_UA_W_K']*(s['ambient_temperature_K']-e[0])),
            outlet_choked=bool(f['out_choked']),ox_inlet_choked=bool(f['ox_choked']),fuel_inlet_choked=bool(f['fuel_choked']),
            composition={name:float(y) for name,y in zip(gas.species_names,gas.Y)},
            mass_balance_error_kg=float(r.mass-self.initial_mass-e[3]+e[4]),
            energy_balance_error_J=float(fluid_error+wall_error),fluid_energy_balance_error_J=float(fluid_error),wall_energy_balance_error_J=float(wall_error),
            element_balance_error_kg={k:float(v) for k,v in elements.items()},
            integrated_inlet_mass_kg=float(e[3]),integrated_outlet_mass_kg=float(e[4]),
            integrated_inlet_enthalpy_J=float(e[5]),integrated_outlet_enthalpy_J=float(e[6]),
            integrated_igniter_energy_J=float(e[7]),integrated_heat_to_wall_J=float(e[8]),
            integrated_ambient_heat_J=float(e[9]),wall_energy_change_J=float(wall_energy),
            integrated_element_inlet_kg={name:float(e[10+j]) for j,name in enumerate(self.elements)},
            integrated_element_outlet_kg={name:float(e[10+len(self.elements)+j]) for j,name in enumerate(self.elements)})

    def _snapshot(self,t):
        row=self.sample(t)
        return self._snapshot_from_row(row)

    def _snapshot_from_row(self,row):
        t=row['time_s']
        return dict(time_s=float(t),volume_m3=self.settings['volume_m3'],pressure_Pa=row['pressure_Pa'],
            temperature_K=row['temperature_K'],density_kg_m3=row['density_kg_m3'],mass_kg=row['mass_kg'],
            internal_energy_J=row['internal_energy_J'],composition=row['composition'],wall_temperature_K=row['wall_temperature_K'],
            ox_opening=row['ox_opening'],fuel_opening=row['fuel_opening'],phase='ideal-gas',
            mechanism='gri30.yaml',cantera_version=ct.__version__,liquid_state_import_supported=False,
            full_engine_initialization_supported=False)

    def scheduled_events(self):
        s=self.settings
        return [(name,t) for name,t in [
            ('氧阀动作开始',s['ox_open_time_s']),('燃料阀动作开始',s['fuel_open_time_s']),
            ('氧阀指令斜坡结束',s['ox_open_time_s']+s['valve_ramp_s']),('燃料阀指令斜坡结束',s['fuel_open_time_s']+s['valve_ramp_s']),
            ('给定点火热脉冲开始',s['ignition_time_s']),('给定点火热脉冲结束',s['ignition_time_s']+s['ignition_duration_s'])]]

    def integrate(self,publish=None):
        s=self.settings
        # Collapse round-off duplicates (for example 0.3 and
        # 0.30000000000000004), which otherwise force a spurious 1e-17 s CVODES
        # step at a pulse boundary. This precision is far below the 1e-6 s
        # minimum supported pulse duration.
        sample_times=set(round(float(t),14) for t in np.linspace(0.,s['duration_s'],s['sample_count']))
        events=[]
        for name,t in self.scheduled_events():
            if 0<=t<=s['duration_s']:
                t=round(t,14)
                events.append(dict(name=name,time_s=float(t),kind='prescribed',observed=False))
                sample_times.add(float(t))
        requested_times=sorted(sample_times)
        targets=sorted({0.,s['duration_s'],*(event['time_s'] for event in events)})
        output_index=1
        samples=[self.sample(0.)]
        endpoint=deepcopy(self.initialization_snapshot)
        if publish: publish(samples,dict(complete=False,events=events,model_description=self.model_description))
        previous=0.
        reason=None
        termination_observation=None
        audited_keys=('mass_balance_error_kg','energy_balance_error_J','fluid_energy_balance_error_J','wall_energy_balance_error_J')
        maxima={key:abs(samples[0][key]) for key in audited_keys}
        element_errors={e:0. for e in self.elements}
        mass_scale=max(self.initial_mass,1e-9)
        energy_scale=max(abs(self.initial_energy),1.)
        peak_pressure=samples[0]['pressure_Pa']
        peak_temperature=samples[0]['temperature_K']
        first_cross=0. if peak_pressure>=s['reference_pressure_Pa'] else None
        last_valid=samples[0]
        accepted_steps=0
        time_tolerance=1e-12*max(1.,s['duration_s'])
        boundary_retries=0
        for target in targets[1:]:
            checkpoint()
            power=self._power((previous+target)/2)
            if power != self.segment_igniter_power:
                self.segment_igniter_power=power
            self.net.max_time_step=self.guard_step_s
            self.net.reinitialize()
            while self.net.time < target-time_tolerance:
                checkpoint()
                previous_time=float(self.net.time)
                previous_T,previous_density,previous_Y=self.reactor.phase.TDY
                previous_Y=previous_Y.copy()
                previous_extra=self.reactor.extra.copy()
                try:
                    when=float(self.net.step())
                    if when>target+time_tolerance:
                        # A one-step CVODES call may overshoot its requested
                        # logical boundary. Discard that entire step, restore
                        # the preceding accepted conservative state, and retry
                        # with a freshly initialized smaller hard hmax. No
                        # state interpolation or post-event old heat source is
                        # retained in the physical trajectory.
                        self.reactor.phase.TDY=previous_T,previous_density,previous_Y
                        self.reactor.syncState()
                        self.reactor.extra[:]=previous_extra
                        self.net.initial_time=previous_time
                        self.net.max_time_step=min(self.guard_step_s,(target-previous_time)/2)
                        self.net.reinitialize()
                        boundary_retries+=1
                        continue
                    accepted_steps+=1
                    reason=self._invalid()
                    if reason:
                        termination_observation=dict(time_s=when,pressure_Pa=float(self.reactor.phase.P),temperature_K=float(self.reactor.T))
                        break
                    row=self.sample(target if abs(when-target)<=time_tolerance else when)
                except (ct.CanteraError,ValueError,FloatingPointError) as exc:
                    detail=next((line.strip() for line in str(exc).splitlines() if line.strip() and not set(line.strip())=={'*'}),type(exc).__name__)
                    reason='积分或物性求解未能在当前适用域内继续：'+detail[:240]
                    break
                last_valid=row
                endpoint=self._snapshot_from_row(row)
                for key in audited_keys:
                    maxima[key]=max(maxima[key],abs(row[key]))
                for element in self.elements:
                    element_errors[element]=max(element_errors[element],abs(row['element_balance_error_kg'][element]))
                mass_scale=max(mass_scale,row['integrated_inlet_mass_kg'])
                energy_scale=max(energy_scale,abs(row['integrated_inlet_enthalpy_J'])+abs(row['integrated_outlet_enthalpy_J'])+row['integrated_igniter_energy_J']+abs(row['integrated_ambient_heat_J']))
                peak_pressure=max(peak_pressure,row['pressure_Pa'])
                peak_temperature=max(peak_temperature,row['temperature_K'])
                if first_cross is None and row['pressure_Pa']>=s['reference_pressure_Pa']:
                    first_cross=row['time_s']
                if output_index<len(requested_times) and row['time_s']>=requested_times[output_index]-time_tolerance:
                    samples.append(row)
                    while output_index<len(requested_times) and row['time_s']>=requested_times[output_index]-time_tolerance:
                        output_index+=1
                    if publish: publish(samples,dict(complete=False,events=events,model_description=self.model_description))
            if reason:
                break
            previous=target
        if last_valid['time_s']>samples[-1]['time_s']:
            samples.append(last_valid)
        complete=reason is None and abs(endpoint['time_s']-s['duration_s'])<1e-10
        for event in events:
            event['observed']=event['time_s']<=endpoint['time_s']+1e-12
        relative_tolerance=max(100*s['rtol'],1e-6)
        mass_tol=relative_tolerance*mass_scale
        energy_tol=relative_tolerance*energy_scale
        passed=maxima['mass_balance_error_kg']<=mass_tol and max(element_errors.values())<=mass_tol and all(maxima[k]<=energy_tol for k in maxima if k!='mass_balance_error_kg')
        audits=dict(status='pass' if passed else 'review',max_abs_mass_error_kg=maxima['mass_balance_error_kg'],
            max_abs_total_energy_error_J=maxima['energy_balance_error_J'],
            max_abs_fluid_energy_error_J=maxima['fluid_energy_balance_error_J'],
            max_abs_wall_energy_error_J=maxima['wall_energy_balance_error_J'],
            max_abs_element_error_kg=element_errors,mass_tolerance_kg=mass_tol,energy_tolerance_J=energy_tol,
            meaning='质量、元素及流体/壁面能量账目闭合检查；不代表试验验证或点火成功。')
        metrics=dict(peak_pressure_Pa=peak_pressure,
            peak_temperature_K=peak_temperature,
            reference_pressure_Pa=s['reference_pressure_Pa'],first_reference_pressure_time_s=first_cross,
            reference_pressure_time_resolution_s=self.guard_step_s,
            reference_pressure_meaning='内部接受步首次观察到达到用户压力参考线的时刻；不是点火延迟或成功判据。',
            integrated_igniter_energy_J=last_valid['integrated_igniter_energy_J'],
            ignition_success=None,ignition_delay_s=None,extinction_detected=None)
        metadata=dict(complete=complete,termination_reason=reason,model_description=self.model_description,
            audits=audits,events=events,metrics=metrics,initialization_snapshot=self.initialization_snapshot,
            endpoint_snapshot=endpoint,integrator=dict(name='Cantera CVODES / ExtensibleIdealGasReactor',
            rtol=s['rtol'],atol=self.net.atol,guard_step_s=self.guard_step_s,accepted_steps=accepted_steps,
            event_boundary_retries=boundary_retries,event_time_tolerance_s=time_tolerance,
            boundary_policy='检查每个 CVODES 内部接受步的适用边界；停止并保留最后有效状态，不钳制温压和物种库存。',
            sampling_policy='在目标采样时刻之后保存首个内部接受步的真实状态和时间；不插值状态。给定事件端点强制积分到浮点时间容差。',
            pulse_policy='热脉冲开始与结束均为精确积分分段；分段间重初始化 CVODES，不依赖输出采样密度。'))
        metadata['termination_observation']=termination_observation
        return samples,metadata
