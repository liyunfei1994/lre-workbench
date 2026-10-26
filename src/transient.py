"""Local hot-state engine dynamics with explicit mass, energy and shaft inventories.

This is a research reduced-order model, not a cold-start or combustion-kinetics
model. All component maps are fitted at ONE supplied steady operating point.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from task_control import checkpoint


def gas_flux(pressure, temperature, downstream, gamma, gas_constant):
    """Ideal-gas isentropic mass flux, including the unchoked branch [kg/m2/s]."""
    if pressure <= downstream or pressure <= 0 or temperature <= 0:
        return 0.0
    ratio = max(downstream / pressure, 0.0)
    critical = (2 / (gamma + 1)) ** (gamma / (gamma - 1))
    if ratio <= critical:
        factor = math.sqrt(gamma) * (2 / (gamma + 1)) ** ((gamma + 1) / (2 * (gamma - 1)))
    else:
        factor = math.sqrt(max(0, 2 * gamma / (gamma - 1) *
                               (ratio ** (2 / gamma) - ratio ** ((gamma + 1) / gamma))))
    return pressure / math.sqrt(gas_constant * temperature) * factor


class HotStateModel:
    """Quasi-steady liquid feeds, finite gas volumes and finite rotor inertia.

    Liquid orifices and declining pump-head curves are solved simultaneously.
    Turbines connect actual gas inventories; their shaft power changes rotor
    kinetic energy. The gas energy reference is thermal (cv*T); effective heat
    inputs fit the nominal energy balance and have frozen local mixture slopes.
    """

    def __init__(self, calculation, settings):
        self.calculation, self.settings = calculation, settings
        self.inputs = calculation['inputs']
        self.summary = calculation['summary']
        self.components = calculation['components']
        self.states = calculation['states']
        self.gg = self.inputs['cycle_type'] == 'gg'
        self.single = self.gg and self.inputs['gg_layout'] == 'single_shaft'
        self.ambient = self.inputs['ambient_pressure']
        self.volumes, self.branches, self.turbines, self.nozzles = [], [], [], []
        self.pumps = {}

        def out(key):
            return self.components[key]['outputs']

        def volume(name, state_key, component_key, volume_m3, *, thermo=None):
            state, data = self.states[state_key], out(component_key)
            if thermo:
                cp, gamma, mw = thermo
            else:
                cp = data['cp_Jkgk']
                gamma = data['gamma']
                mw = data.get('molar_mass_kg_per_kmol')
                if mw is None:
                    mw = 8314.462618 / (cp * (1 - 1 / gamma))
            R = 8314.462618 / mw
            cv = cp - R
            if not (cp > R > 0 and 1 < gamma < 2):
                raise ValueError(f'{name}: 热容和气体常数不满足理想气体闭合。')
            T, p = state['temperature'], state['pressure']
            mass = p * volume_m3 / (R * T)
            item = dict(name=name, p0=p, T0=T, cp=cp, cv=cv, R=R,
                        gamma=cp/cv, V=volume_m3, m0=mass, U0=mass*cv*T,
                        mdot0=state['mass_flow'], heat0=0.0, heat_side='total')
            self.volumes.append(item)
            return len(self.volumes)-1

        main = volume('main', 'main_chamber', 'main_chamber', settings['chamber_volume_m3'])
        self.main = main
        if self.gg:
            gg = volume('generator', 'gg_outlet', 'gas_generator', settings['generator_volume_m3'])
            tk = 'gg_turbine' if self.single else 'gg_turbine_ox'
            td = out(tk)
            exhaust = volume('exhaust', 'gg_turbine_exhaust', tk, settings['exhaust_volume_m3'],
                             thermo=(td['cp_in_Jkgk'], td['gamma_in'], td['molar_mass_in']))
            self.generator, self.exhaust = gg, exhaust
        else:
            oxpb = volume('ox_preburner', 'ox_preburner_outlet', 'ox_preburner', settings['generator_volume_m3'])
            fpb = volume('fuel_preburner', 'fuel_preburner_outlet', 'fuel_preburner', settings['generator_volume_m3'])
            self.oxpb, self.fpb = oxpb, fpb

        for side, component, statekey in [('ox', 'ox_pump', 'lox_after_pump'), ('fuel', 'fuel_pump', 'fuel_after_pump')]:
            p = out(component)
            self.pumps[side] = dict(p0=p['p_out_MPa']*1e6, pin=p['p_in_MPa']*1e6,
                                    dp0=p['delta_p_Pa'], power0=p['power_W'],
                                    mdot0=self.summary[f'mdot_{side}_kg_s'],
                                    omega0=p['nr_rpm']*math.pi/30,
                                    T0=self.states[statekey]['temperature'])

        def branch(side, destination, flow):
            dp = self.pumps[side]['p0'] - self.volumes[destination]['p0']
            if dp <= 0 or flow <= 0:
                raise ValueError(f'{side} → {self.volumes[destination]["name"]}: 名义流量或供给压差非正。')
            # The effective restriction includes line, cooling and injector losses.
            temperature=self.pumps[side]['T0']
            if side=='fuel' and (not self.gg or destination==main):
                temperature=self.states['fuel_after_cooling']['temperature']
            self.branches.append(dict(side=side, dest=destination, m0=flow,T0=temperature,
                                      C=flow/math.sqrt(dp)))

        if self.gg:
            for side in ('ox', 'fuel'):
                gm = self.summary[f'gg_mdot_{side}_kg_s']
                branch(side, gg, gm)
                branch(side, main, self.summary[f'mdot_{side}_kg_s']-gm)
        else:
            for side in ('ox', 'fuel'):
                branch(side, oxpb, self.summary[f'mdot_{side}_to_ox_preburner_kg_s'])
                branch(side, fpb, self.summary[f'mdot_{side}_to_fuel_preburner_kg_s'])

        def turbine(key, up, down, shaft, back_ratio=1.0):
            d = out(key)
            v = self.volumes[up]
            flux = gas_flux(v['p0'], v['T0'], d['p_out_Pa'], v['gamma'], v['R'])
            ideal = v['cp']*v['T0']*(1-(d['p_out_Pa']/v['p0'])**((v['gamma']-1)/v['gamma']))
            if flux <= 0 or ideal <= 0:
                raise ValueError(f'{key}: 无法从名义压比建立涡轮流量模型。')
            self.turbines.append(dict(name=key, up=up, down=down, shaft=shaft,
                                      C=d['mass_flow_kgs']/flux, work_factor=d['actual_drop_Jkg']/ideal,
                                      back_ratio=back_ratio, power0=d['turbine_power_W'],
                                      source_power_W=d['turbine_power_W'],mass_flow0=d['mass_flow_kgs'],
                                      specific_work0=d['actual_drop_Jkg'],nominal_work_fit_relative_correction=0.0))

        if self.gg:
            if self.single:
                turbine('gg_turbine', gg, exhaust, 'ox')
            else:
                turbine('gg_turbine_ox', gg, exhaust, 'ox')
                turbine('gg_turbine_fuel', gg, exhaust, 'fuel')
        else:
            for side, cvindex in [('ox', oxpb), ('fuel', fpb)]:
                d = out(f'{side}_preburner_turbine')
                turbine(f'{side}_preburner_turbine', cvindex, main, side,
                        d['p_out_Pa']/self.volumes[main]['p0'])

        # A converged steady solver can leave a tiny positive pump-power residual.
        # Fit only that numerical residue into the local turbine work coefficient;
        # never manufacture a mechanical efficiency >1 or a hidden drive torque.
        for side in (['ox'] if self.single else ['ox','fuel']):
            load=sum(p['power0'] for s,p in self.pumps.items() if self.single or s==side)
            drive=sum(t['power0'] for t in self.turbines if t['shaft']==side)
            correction=load/drive-1
            if correction>1e-7:
                raise ValueError(f'{side}: 名义泵功率超过涡轮功率，差异 {correction:.3g} 超过 1e-7 数值拟合容差。')
            if correction>0:
                for t in self.turbines:
                    if t['shaft']==side:
                        t['work_factor']*=1+correction
                        t['specific_work0']*=1+correction
                        t['power0']*=1+correction
                        t['nominal_work_fit_relative_correction']=correction

        if self.gg:
            # The source's turbine temperature uses variable cp. Reconstruct its
            # exhaust inventory on THIS model's constant-cp energy basis. For a
            # dual shaft this also performs the required enthalpy-weighted mix.
            ex=self.volumes[exhaust]
            ex['source_temperature_K']=ex['T0']
            Hin=sum(t['mass_flow0']*
                    (self.volumes[t['up']]['cp']*self.volumes[t['up']]['T0']-t['specific_work0'])
                    for t in self.turbines)
            ex['T0']=Hin/(ex['mdot0']*ex['cp'])
            if ex['T0']<=0:
                raise ValueError('GG 涡轮排气的热内能非正。')
            ex['m0']=ex['p0']*ex['V']/(ex['R']*ex['T0'])
            ex['U0']=ex['m0']*ex['cv']*ex['T0']

        def nozzle(key, cvindex):
            d, v = out(key), self.volumes[cvindex]
            flux = gas_flux(v['p0'], v['T0'], self.ambient, v['gamma'], v['R'])
            area = d['At_m2']
            self.nozzles.append(dict(cv=cvindex, C=v['mdot0']/flux, area=area,
                                     exit_area=area*self.inputs['expansion_ratio'] if cvindex == main else area*self.inputs['gg_exhaust_expansion_ratio'],
                                     cf_vac=d['thrust_vac_N']/(v['p0']*area),
                                     discharge_coefficient=v['mdot0']/(flux*area)))
        nozzle('nozzle', main)
        if self.gg:
            nozzle('gg_exhaust_nozzle', exhaust)

        self.shafts = []
        for side in (['ox'] if self.single else ['ox', 'fuel']):
            load0 = sum(p['power0'] for s,p in self.pumps.items() if self.single or s == side)
            drive0 = sum(t['power0'] for t in self.turbines if t['shaft'] == side)
            efficiency = load0/drive0
            if not 0 < efficiency <= 1.0+1e-8:
                raise ValueError(f'{side}: 名义轴功率不平衡，无法建立热态初值。')
            efficiency=min(efficiency,1.0)
            J = settings[f'inertia_{side}_kg_m2']
            w = self.pumps[side]['omega0']
            self.shafts.append(dict(side=side, J=J, K0=.5*J*w*w, efficiency=efficiency))

        # Cold-liquid thermal enthalpy and effective heat release are separate.
        # cp values here are declared local assumptions, not chemical-reference h.
        self.liquid_cp = {'ox':1700.0, 'fuel':3500.0 if self.inputs['propellant_pair']=='lox_methane' else 2200.0}
        for vindex, v in enumerate(self.volumes):
            feeds = [b for b in self.branches if b['dest']==vindex]
            if feeds:
                coldH = sum(b['m0']*self.liquid_cp[b['side']]*b['T0'] for b in feeds)
                v['heat0'] = v['mdot0']*v['cp']*v['T0']-coldH
                mf = sum(b['m0'] for b in feeds if b['side']=='fuel')
                mo = sum(b['m0'] for b in feeds if b['side']=='ox')
                v['heat_side'] = 'fuel' if mo/mf > (4.0 if self.inputs['propellant_pair']=='lox_methane' else 3.48) else 'ox'
                v['heat_m0'] = mf if v['heat_side']=='fuel' else mo
            else:
                incoming = [t for t in self.turbines if t['down']==vindex]
                Hin = sum(t['mass_flow0']*
                          (self.volumes[t['up']]['cp']*self.volumes[t['up']]['T0']-t['specific_work0']) for t in incoming)
                v['heat0'] = v['mdot0']*v['cp']*v['T0']-Hin
                # Exhaust mixing has no combustion. A mismatch means the source's
                # merged exhaust temperature does not close its two turbine streams.
                if self.gg and vindex == exhaust:
                    if abs(v['heat0']) > max(1.0, 1e-3*abs(Hin)):
                        raise ValueError('双轴 GG 合流排气焓不闭合；请先修正稳态合流温度。')
                    v['heat0'] = 0.0
                v['heat_m0'] = v['mdot0']

        self.nv, self.ns = len(self.volumes), len(self.shafts)
        self.mass_scale = sum(v['m0'] for v in self.volumes)
        self.energy_scale = sum(v['U0'] for v in self.volumes)+sum(s['K0'] for s in self.shafts)
        self.initial = np.r_[np.ones(2*self.nv+self.ns), 0.0, 0.0]
        if self.validity_margin(0,self.initial)<=0:
            raise ValueError('名义喷管不是壅塞状态，无法使用本模型的局部固定推力系数。')

    def command(self, t, side):
        p = self.settings
        fraction = min(1.0, max(0.0, (t-p['command_time_s'])/p['command_ramp_s']))
        return 1.0 + (p[f'{side}_area_ratio']-1.0)*fraction

    def decode(self, y):
        gas=[]
        for i,v in enumerate(self.volumes):
            mass, U = y[2*i]*v['m0'], y[2*i+1]*v['U0']
            if mass <= 0 or U <= 0:
                raise ValueError('容腔质量或内能非正；已超出局部热态模型范围。')
            T=U/(mass*v['cv'])
            gas.append(dict(m=mass,U=U,T=T,p=mass*v['R']*T/v['V']))
        speed={s['side']:math.sqrt(max(y[2*self.nv+j],1e-12)) for j,s in enumerate(self.shafts)}
        if self.single:
            speed['fuel']=speed['ox']
        return gas,speed

    def evaluate(self,t,y):
        checkpoint()
        gas,speed=self.decode(y)
        dm=np.zeros(self.nv); dU=np.zeros(self.nv)
        mdot={}; pump_power={}; pump_pressure={}; feed_by_volume=[{'ox':0.0,'fuel':0.0} for _ in gas]
        external_mass=0.0; external_energy=0.0
        for side,pump in self.pumps.items():
            branches=[b for b in self.branches if b['side']==side]
            opening=self.command(t,side)
            slope=self.settings['pump_head_slope']
            shutoff=pump['dp0']*(1+slope)*speed[side]**2
            def flows(pressure):
                return [b['C']*opening*math.sqrt(max(0.0,pressure-gas[b['dest']]['p'])) for b in branches]
            def residual(pressure):
                q=sum(flows(pressure))
                return pressure-pump['pin']-shutoff+pump['dp0']*slope*(q/pump['mdot0'])**2
            lo=pump['pin']; hi=lo+max(shutoff,1.0)
            # For the bounded operating range all destinations remain above pin.
            if residual(lo)>0:
                raise ValueError('液路压力已低于所支持的热态泵工作区。')
            pressure=brentq(residual,lo,hi,xtol=1e-4)
            qvalues=flows(pressure); q=sum(qvalues)
            mdot[side]=q; pump_pressure[side]=pressure
            pump_power[side]=pump['power0']*(q/pump['mdot0'])*(pressure-pump['pin'])/pump['dp0']
            for b,qb in zip(branches,qvalues):
                i=b['dest']; h=self.liquid_cp[side]*b['T0']
                dm[i]+=qb; dU[i]+=qb*h
                external_mass+=qb; external_energy+=qb*h
                feed_by_volume[i][side]+=qb

        turbine_power={s['side']:0.0 for s in self.shafts}
        gas_in=np.zeros(self.nv)
        for turbine in self.turbines:
            i,j=turbine['up'],turbine['down']; v=self.volumes[i]
            up,down=gas[i],gas[j]; back=down['p']*turbine['back_ratio']
            q=turbine['C']*gas_flux(up['p'],up['T'],back,v['gamma'],v['R'])
            dh=turbine['work_factor']*v['cp']*up['T']*(1-min(1.0,back/up['p'])**((v['gamma']-1)/v['gamma']))
            power=q*dh; h=v['cp']*up['T']
            dm[i]-=q; dm[j]+=q; dU[i]-=q*h; dU[j]+=q*h-power
            gas_in[j]+=q
            turbine_power[turbine['shaft']]+=power
            external_energy-=power

        for i,v in enumerate(self.volumes):
            if v['heat_side']=='total':
                heat=v['heat0']*gas_in[i]/v['heat_m0']
            else:
                heat=v['heat0']*feed_by_volume[i][v['heat_side']]/v['heat_m0']
            dU[i]+=heat; external_energy+=heat

        thrust=0.0; total_nozzle_flow=0.0
        for n in self.nozzles:
            i=n['cv']; v=self.volumes[i]; g=gas[i]
            q=n['C']*gas_flux(g['p'],g['T'],self.ambient,v['gamma'],v['R'])
            dm[i]-=q; dU[i]-=q*v['cp']*g['T']
            external_mass-=q; external_energy-=q*v['cp']*g['T']
            total_nozzle_flow+=q
            thrust+=n['cf_vac']*g['p']*n['area']-self.ambient*n['exit_area']

        derivative=[]
        for i,v in enumerate(self.volumes):
            derivative.extend([dm[i]/v['m0'],dU[i]/v['U0']])
        shaft_acceleration={}
        for s in self.shafts:
            load=sum(power for side,power in pump_power.items() if self.single or side==s['side'])
            dK=s['efficiency']*turbine_power[s['side']]-load
            derivative.append(dK/s['K0']); external_energy+=dK
            shaft_acceleration[s['side']]=dK
        derivative.extend([external_mass/self.mass_scale,external_energy/self.energy_scale])
        return np.array(derivative),dict(gas=gas,speed=speed,mdot=mdot,thrust=thrust,
              pump_power=pump_power,pump_pressure=pump_pressure,turbine_power=turbine_power,
              external_mass_rate=external_mass,external_energy_rate=external_energy,
              total_nozzle_flow=total_nozzle_flow,shaft_acceleration=shaft_acceleration)

    def rhs(self,t,y):
        return self.evaluate(t,y)[0]

    def sample(self,t,y):
        _,d=self.evaluate(t,y)
        main=d['gas'][self.main]
        mass=sum(g['m'] for g in d['gas'])
        energy=sum(g['U'] for g in d['gas'])+sum(y[2*self.nv+i]*s['K0'] for i,s in enumerate(self.shafts))
        result=dict(time_s=float(t),pc_Pa=main['p'],thrust_N=d['thrust'],
                    mdot_ox_kg_s=d['mdot']['ox'],mdot_fuel_kg_s=d['mdot']['fuel'],
                    chamber_temperature_K=main['T'],
                    shaft_ox_rpm=d['speed']['ox']*self.pumps['ox']['omega0']*30/math.pi,
                    shaft_fuel_rpm=d['speed']['fuel']*self.pumps['fuel']['omega0']*30/math.pi,
                    fuel_area_ratio=self.command(t,'fuel'),ox_area_ratio=self.command(t,'ox'),
                    gas_inventory_kg=mass,total_stored_energy_J=energy,
                    integrated_net_mass_kg=float(y[-2]*self.mass_scale),
                    integrated_net_energy_J=float(y[-1]*self.energy_scale),
                    mass_balance_error_kg=float(mass-self.mass_scale-y[-2]*self.mass_scale),
                    energy_balance_error_J=float(energy-self.energy_scale-y[-1]*self.energy_scale),
                    net_mass_rate_kg_s=d['external_mass_rate'],net_energy_rate_W=d['external_energy_rate'])
        for v,g in zip(self.volumes,d['gas']):
            result[v['name']+'_pressure_Pa']=g['p']
            result[v['name']+'_temperature_K']=g['T']
        if self.gg:
            result['gg_pressure_Pa']=result['generator_pressure_Pa']
            result['gg_temperature_K']=result['generator_temperature_K']
        return result

    def validity_margin(self,t,y):
        """Continuous terminal event, independent of the selected output grid."""
        gas,speed=self.decode(y)
        margins=[]
        for g,v in zip(gas,self.volumes):
            for ratio in (g['p']/v['p0'],g['T']/v['T0']):
                margins.extend((ratio-.65,1.35-ratio))
        for ratio in speed.values():
            margins.extend((ratio-.65,1.35-ratio))
        for nozzle in self.nozzles:
            i=nozzle['cv'];v=self.volumes[i]
            # The fixed Cf thrust reduction is used only while choked.
            critical=(2/(v['gamma']+1))**(v['gamma']/(v['gamma']-1))
            margins.append(critical-self.ambient/gas[i]['p'])
        return min(margins)

    def integrate(self,on_sample=None):
        """Integrate in bounded chunks, exactly splitting at command breakpoints."""
        p=self.settings
        times=np.linspace(0,p['duration_s'],p['sample_count'])
        breaks=sorted(set([0.0,p['duration_s'],p['command_time_s'],
                          p['command_time_s']+p['command_ramp_s']]+
                         list(np.linspace(0,p['duration_s'],min(21,p['sample_count'])))))
        breaks=[x for x in breaks if 0<=x<=p['duration_s']]
        y=self.initial.copy(); samples=[]; nfev=0
        initial=self.sample(0,y); samples.append(initial)
        if on_sample: on_sample(initial)
        for start,end in zip(breaks,breaks[1:]):
            checkpoint()
            def terminal_event(t,state): return self.validity_margin(t,state)
            terminal_event.terminal=True
            terminal_event.direction=-1
            solution=solve_ivp(self.rhs,(start,end),y,method='Radau',dense_output=True,
                               rtol=p['rtol'],atol=p['rtol']*.01,max_step=max((end-start)/4,1e-6),
                               events=terminal_event)
            nfev+=solution.nfev
            if not solution.success:
                raise ValueError('瞬态积分未完成：'+solution.message)
            actual_end=float(solution.t[-1])
            requested=list(times[(times>start+1e-12)&(times<=actual_end+1e-12)])
            if solution.status==1 and (not requested or abs(requested[-1]-actual_end)>1e-10):
                requested.append(actual_end)
            for t in requested:
                state=solution.sol(t)
                sample=self.sample(float(t),state); samples.append(sample)
                if on_sample: on_sample(sample)
            if solution.status==1:
                raise ValueError('状态到达局部模型边界或喷管失去壅塞；已保留边界前时间点及终止时刻。')
            y=solution.y[:,-1]
        return samples,dict(method='Radau',rtol=p['rtol'],atol_scaled=p['rtol']*.01,nfev=nfev,
                            state_count=len(y)-2,conservation_ledger_states=2)
