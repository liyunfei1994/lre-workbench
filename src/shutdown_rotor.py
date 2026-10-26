"""Independent, full-liquid hydraulic/rotor stop-and-restart research unit.

The rotor speed is a signed dynamic state, never a prescribed speed ramp.
This model cannot empty a liquid volume: its inventory is only the small
compressibility of an initially full, single-phase manifold.
"""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
from scipy.integrate import Radau
from scipy.optimize import brentq

from feed_network import evaluate_pump_map, validate_settings as validate_feed
from task_control import checkpoint


def _circuit(cid, label, rho, bulk, flow, inertia):
    return dict(id=cid, label=label, rho_kg_m3=rho, bulk_modulus_Pa=bulk,
        volume_m3=.002, pump_head_Pa=1e6, pump_flow_m3_s=flow,
        pump_speed_rpm=6000., pump_efficiency=.8,
        tank_pressure_Pa=2e5, outlet_pressure_Pa=2e5,
        initial_manifold_pressure_Pa=1.175e6, pressure_floor_Pa=1e5,
        max_compression_fraction=.02,
        inlet_length_m=2., inlet_diameter_m=.025,
        outlet_length_m=2., outlet_diameter_m=.025,
        inlet_loss_Pa=2.5e4, outlet_loss_Pa=2.5e4, valve_loss_Pa=9.5e5,
        leak_fraction=.02, valve_tau_s=.025, valve_rate_per_s=20.,
        initial_valve_opening=1., initial_speed_ratio=1.,
        initial_pump_flow_ratio=1., initial_outlet_flow_ratio=1.,
        rotor_inertia_kg_m2=inertia, rotor_linear_friction_Nm_s=.0005,
        rotor_quadratic_friction_Nm_s2=0., drive_torque_scale=1.,
        pump_map=dict(kind='analytic_demo', slope=.1, max_speed_ratio=2.5,
                      max_flow_ratio=3., source='解析演示四象限特性；未实测标定'))


def default_settings():
    return dict(duration_s=5., sample_count=301, rtol=1e-6,
        shutdown_time_s=1., restart_time_s=3., command_ramp_s=.15,
        circuits=[_circuit('oxygen', '氧侧独立液压轴系', 1140., 1e9, .001, .01),
                  _circuit('methane', '燃料侧独立液压轴系', 422., 6e8, .0007, .007)])


def _finite(value, name, low=None, high=None):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        valid = False
    if not valid or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f'{name}：必须为允许范围内的有限数值。')
    return float(value)


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != set(default_settings()):
        raise ValueError('轴系关机与再驱动设置字段不完整或存在未知字段。')
    s = deepcopy(settings)
    for key, low, high in [('duration_s', .001, 60.), ('rtol', 1e-9, 1e-4),
                          ('shutdown_time_s', 0., 60.), ('restart_time_s', 0., 60.),
                          ('command_ramp_s', 1e-5, 30.)]:
        s[key] = _finite(s[key], key, low, high)
    if isinstance(s['sample_count'], bool) or not isinstance(s['sample_count'], int) or not 2 <= s['sample_count'] <= 2001:
        raise ValueError('sample_count：必须为 2 至 2001 的整数。')
    if not (s['shutdown_time_s']+s['command_ramp_s'] < s['restart_time_s']
            and s['restart_time_s']+s['command_ramp_s'] <= s['duration_s']):
        raise ValueError('关机斜坡必须早于再驱动；再驱动斜坡须在积分结束前完成。')
    if not isinstance(s['circuits'], list) or not 1 <= len(s['circuits']) <= 4:
        raise ValueError('必须提供 1 至 4 个独立轴系回路。')
    expected = set(default_settings()['circuits'][0])
    for c in s['circuits']:
        if not isinstance(c, dict) or set(c) != expected:
            raise ValueError('轴系回路字段不完整或存在未知字段。')
        for key in ('id','label'):
            if not isinstance(c[key],str) or not c[key].strip() or len(c[key])>160:
                raise ValueError('回路 id 与名称必须为非空短文本。')
        for key, value in c.items():
            if key not in ('id', 'label', 'pump_map'):
                c[key] = _finite(value, key)
        bounds={'rho_kg_m3':(1.,2e4),'bulk_modulus_Pa':(1e3,1e12),'volume_m3':(1e-9,1e3),
            'pump_head_Pa':(1.,5e8),'pump_flow_m3_s':(1e-9,1e3),'pump_speed_rpm':(1.,1e6),
            'valve_tau_s':(1e-6,100.),'valve_rate_per_s':(1e-5,1e6)}
        for key in ('tank_pressure_Pa','outlet_pressure_Pa','initial_manifold_pressure_Pa','pressure_floor_Pa'):
            bounds[key]=(0.,5e8)
        for key in ('inlet_length_m','outlet_length_m'): bounds[key]=(1e-5,1e5)
        for key in ('inlet_diameter_m','outlet_diameter_m'): bounds[key]=(1e-5,100.)
        for key in ('inlet_loss_Pa','outlet_loss_Pa','valve_loss_Pa'): bounds[key]=(0.,1e10)
        for key in ('initial_speed_ratio','initial_pump_flow_ratio','initial_outlet_flow_ratio'): bounds[key]=(-100.,100.)
        for key,(low,high) in bounds.items(): c[key]=_finite(c[key],key,low,high)
        c['rotor_inertia_kg_m2'] = _finite(c['rotor_inertia_kg_m2'], '转动惯量', 1e-7, 1e4)
        c['rotor_linear_friction_Nm_s'] = _finite(c['rotor_linear_friction_Nm_s'], '线性摩擦系数', 0., 1e4)
        c['rotor_quadratic_friction_Nm_s2'] = _finite(c['rotor_quadratic_friction_Nm_s2'], '二次摩擦系数', 0., 1e4)
        c['drive_torque_scale'] = _finite(c['drive_torque_scale'], '初始平衡驱动力矩倍数', 0., 5.)
        pm = c['pump_map']
        if not isinstance(pm, dict):
            raise ValueError('泵图必须为对象。')
        mapkeys={'kind','max_speed_ratio','max_flow_ratio','source'}|({'slope'} if pm.get('kind')=='analytic_demo' else {'points'})
        if set(pm)!=mapkeys:
            raise ValueError('泵图字段不完整或存在未知字段。')
        for key in ('slope', 'max_speed_ratio', 'max_flow_ratio'):
            if key in pm:
                pm[key] = _finite(pm[key], 'pump_map.'+key, 0.,100.)
        if 'points' in pm and (not isinstance(pm['points'],list) or not 5<=len(pm['points'])<=1441):
            raise ValueError('Suter 泵图数据必须为数组。')
        for row in pm.get('points', []):
            if not isinstance(row, dict) or set(row)!={'theta_deg','head','torque'}:
                raise ValueError('泵图数据行必须为对象。')
            for key in ('theta_deg', 'head', 'torque'):
                if key in row:
                    row[key] = _finite(row[key], 'pump_map.points.'+key,
                        0. if key=='theta_deg' else -1e6,360. if key=='theta_deg' else 1e6)
    base = dict(duration_s=s['duration_s'], sample_count=s['sample_count'], rtol=s['rtol'],
        command_time_s=s['shutdown_time_s'], command_ramp_s=s['command_ramp_s'], circuits=deepcopy(s['circuits']))
    for c in base['circuits']:
        c.update(final_speed_ratio=c['initial_speed_ratio'], final_valve_opening=0.)
    # Reuse the independently checked signed pump-map and liquid-domain contract.
    validate_feed(base)
    return s


class ShutdownRotorModel:
    STATES_PER_CIRCUIT = 13  # p, Qin, Qout, a, n; eight independent ledgers

    def __init__(self, settings):
        self.settings = s = validate_settings(settings)
        self.circuits = s['circuits']
        self.parameters = []
        initial = []
        for c in self.circuits:
            Q, rho = c['pump_flow_m3_s'], c['rho_kg_m3']
            C = c['volume_m3']/c['bulk_modulus_Pa']
            Li = rho*c['inlet_length_m']/(math.pi*c['inlet_diameter_m']**2/4)
            Lo = rho*c['outlet_length_m']/(math.pi*c['outlet_diameter_m']**2/4)
            w0 = c['pump_speed_rpm']*math.pi/30
            n = c['initial_speed_ratio']
            pump = evaluate_pump_map(c, n, c['initial_pump_flow_ratio'])
            friction = self._friction(c, n*w0)
            drive = c['drive_torque_scale']*(pump['pump_torque_Nm']+friction)
            Ehyd = .5*C*c['initial_manifold_pressure_Pa']**2+.5*Li*(Q*c['initial_pump_flow_ratio'])**2+.5*Lo*(Q*c['initial_outlet_flow_ratio'])**2
            Ekin = .5*c['rotor_inertia_kg_m2']*(n*w0)**2
            pscale = max(c['pump_head_Pa'], c['initial_manifold_pressure_Pa'], 1e5)
            escale = max(Ehyd+Ekin, c['pump_head_Pa']*Q*s['duration_s'], 1.)
            mscale = max(rho*c['volume_m3'], rho*Q*s['duration_s'], .001)
            self.parameters.append(dict(C=C,L_in=Li,L_out=Lo,omega0=w0,drive_torque_Nm=drive,
                pressure_scale=pscale,energy_scale=escale,mass_scale=mscale,
                initial_hydraulic_energy_J=Ehyd,initial_rotor_energy_J=Ekin,mass0=rho*c['volume_m3']))
            initial.extend([c['initial_manifold_pressure_Pa']/pscale,c['initial_pump_flow_ratio'],
                c['initial_outlet_flow_ratio'],c['initial_valve_opening'],n,*([0.]*8)])
        self.initial = np.asarray(initial, dtype=float)
        self.events = [dict(name=name,time_s=t,kind='prescribed',observed=False) for name,t in [
            ('撤除驱动力矩与关阀指令开始',s['shutdown_time_s']),
            ('关机指令斜坡结束',s['shutdown_time_s']+s['command_ramp_s']),
            ('恢复驱动力矩与再开阀指令开始',s['restart_time_s']),
            ('再驱动指令斜坡结束',s['restart_time_s']+s['command_ramp_s'])]]
        self.model_description = dict(name='满液液压轴系关机、惰转与再驱动研究台',experiment_validated=False,
            topology='每回路：固定压储源 → 有惯性泵支路 → 满液小压缩容腔 → 有惯性执行阀支路 → 固定背压；每泵独立转子。',
            equations=['C dp/dt=Qin−Qout；L dQ/dt=ΣΔp',
                'J dω/dt=τdrive−τpump−bω−cω|ω|',
                'Ehyd=½Cp²+½LinQin²+½LoutQout²；Erotor=½Jω²',
                'Δ(Ehyd+Erotor)=∫(Pboundary+Pdrive−Phyd.loss−Ppump.loss−Pfriction)dt'],
            speed_model='转速为有符号动力学状态；驱动力矩为外给撤除/恢复指令，不是涡轮或启动装置的实际力矩模型。',
            drive_model='初始驱动力矩=drive_torque_scale×[初始泵图力矩+初始摩擦力矩]；关机斜坡降至0，再驱动斜坡恢复原值。',
            valve_model='阀执行机构一阶滞后并限速；关阀指令为0，显式保留leak_fraction有效面积。',
            inventory_model='只求满液容腔的小压缩库存；不是残余液体排空，气液转换、热状态与壁面余热在其他研究单元求解。',
            reverse_flow_model='管路流量、泵转速和泵力矩均保留符号；反流/反转使用同一四象限图，不剪裁成0。',
            coupling='回路间不混合；不与燃烧、泄放/充填或气相点火单元交换状态。',
            limitations=['冻结密度与体积模量；不含气蚀、两相流、水击波传播或空间管路声学。',
                '解析泵图为被动演示曲线；真实再启动能力须有完整低速/反流泵图、转动惯量及摩擦/驱动力矩数据。',
                '指令恢复和转速恢复不是发动机再点火或再启动成功判据。'],
            circuits=[dict(id=c['id'],label=c['label'],drive_torque_Nm=p['drive_torque_Nm'],pump_map=deepcopy(c['pump_map'])) for c,p in zip(self.circuits,self.parameters)])
        self.initialization_snapshot = self.snapshot(0.,self.initial)

    @staticmethod
    def _friction(c, omega):
        return c['rotor_linear_friction_Nm_s']*omega+c['rotor_quadratic_friction_Nm_s2']*omega*abs(omega)

    def command_factor(self,t):
        s=self.settings
        close=min(1.,max(0.,(t-s['shutdown_time_s'])/s['command_ramp_s']))
        reopen=min(1.,max(0.,(t-s['restart_time_s'])/s['command_ramp_s']))
        return 1.-close+reopen

    def evaluate(self,t,y):
        checkpoint()
        dy=np.zeros_like(y)
        rows=[]
        f=self.command_factor(t)
        for i,(c,p) in enumerate(zip(self.circuits,self.parameters)):
            j=i*self.STATES_PER_CIRCUIT
            pressure=float(y[j]*p['pressure_scale'])
            qi,qo,a,n=map(float,y[j+1:j+5])
            Q0,w0=c['pump_flow_m3_s'],p['omega0']
            Qin,Qout,omega=qi*Q0,qo*Q0,n*w0
            pump=evaluate_pump_map(c,n,qi)
            effective=c['leak_fraction']+(1-c['leak_fraction'])*min(1.,max(0.,a))
            di=c['inlet_loss_Pa']*qi*abs(qi)
            do=c['outlet_loss_Pa']*qo*abs(qo)
            dv=c['valve_loss_Pa']*qo*abs(qo)/effective**2
            drive=f*p['drive_torque_Nm']
            friction=self._friction(c,omega)
            valve_command=f*c['initial_valve_opening']
            boundary=c['tank_pressure_Pa']*Qin-c['outlet_pressure_Pa']*Qout
            dissipation=di*Qin+(do+dv)*Qout
            massrate=c['rho_kg_m3']*(Qin-Qout)
            drivepower,frictionpower=drive*omega,friction*omega
            dy[j:j+5]=[(Qin-Qout)/p['C']/p['pressure_scale'],
                (c['tank_pressure_Pa']-pressure+pump['pump_head_Pa']-di)/p['L_in']/Q0,
                (pressure-c['outlet_pressure_Pa']-do-dv)/p['L_out']/Q0,
                float(np.clip((valve_command-a)/c['valve_tau_s'],-c['valve_rate_per_s'],c['valve_rate_per_s'])),
                (drive-pump['pump_torque_Nm']-friction)/c['rotor_inertia_kg_m2']/w0]
            dy[j+5:j+13]=[massrate/p['mass_scale'],boundary/p['energy_scale'],
                pump['pump_hydraulic_power_W']/p['energy_scale'],dissipation/p['energy_scale'],
                pump['pump_shaft_power_W']/p['energy_scale'],pump['pump_loss_W']/p['energy_scale'],
                drivepower/p['energy_scale'],frictionpower/p['energy_scale']]
            rows.append(dict(id=c['id'],label=c['label'],manifold_pressure_Pa=pressure,
                pump_flow_m3_s=Qin,outlet_flow_m3_s=Qout,pump_mass_flow_kg_s=c['rho_kg_m3']*Qin,
                outlet_mass_flow_kg_s=c['rho_kg_m3']*Qout,speed_ratio=n,speed_rpm=n*c['pump_speed_rpm'],
                omega_rad_s=omega,rotor_acceleration_rad_s2=float(dy[j+4]*w0),
                valve_opening=a,valve_command=valve_command,effective_area_ratio=effective,
                drive_command_factor=f,drive_torque_Nm=drive,friction_torque_Nm=friction,
                drive_power_W=drivepower,friction_power_W=frictionpower,boundary_power_W=boundary,
                hydraulic_dissipation_W=dissipation,
                inventory_kg=p['mass0']+c['rho_kg_m3']*p['C']*(pressure-c['initial_manifold_pressure_Pa']),
                compression_fraction=(pressure-c['initial_manifold_pressure_Pa'])/c['bulk_modulus_Pa'],
                flow_direction='反流' if Qin < -Q0*1e-10 else '正流' if Qin > Q0*1e-10 else '零流量',
                outlet_flow_direction='反流' if Qout < -Q0*1e-10 else '正流' if Qout > Q0*1e-10 else '零流量',
                rotation_direction='反转' if n < -1e-10 else '正转' if n > 1e-10 else '停转',**pump))
        return dy,rows

    def rhs(self,t,y):
        return self.evaluate(t,y)[0]

    def sample(self,t,y):
        _,rows=self.evaluate(t,y)
        for i,(c,p,row) in enumerate(zip(self.circuits,self.parameters,rows)):
            j=i*self.STATES_PER_CIRCUIT
            mass=float(y[j+5]*p['mass_scale'])
            boundary,pump,dissipation,shaft,loss,drive,friction=(float(v*p['energy_scale']) for v in y[j+6:j+13])
            hyd=.5*p['C']*row['manifold_pressure_Pa']**2+.5*p['L_in']*row['pump_flow_m3_s']**2+.5*p['L_out']*row['outlet_flow_m3_s']**2
            rotor=.5*c['rotor_inertia_kg_m2']*row['omega_rad_s']**2
            herr=hyd-p['initial_hydraulic_energy_J']-boundary-pump+dissipation
            rerr=rotor-p['initial_rotor_energy_J']-drive+shaft+friction
            row.update(stored_hydraulic_energy_J=hyd,rotor_energy_J=rotor,
                integrated_boundary_mass_kg=mass,integrated_boundary_work_J=boundary,
                integrated_pump_work_J=pump,integrated_dissipation_J=dissipation,
                integrated_shaft_work_J=shaft,integrated_pump_loss_J=loss,
                integrated_drive_work_J=drive,integrated_friction_work_J=friction,
                mass_balance_error_kg=row['inventory_kg']-p['mass0']-mass,
                hydraulic_energy_error_J=herr,rotor_energy_error_J=rerr,
                total_energy_error_J=herr+rerr,pump_work_balance_error_J=shaft-pump-loss)
        return dict(time_s=float(t),circuits=rows)

    def snapshot(self,t,y):
        return dict(schema='shutdown-rotor-state-v1',time_s=float(t),
            purpose='独立满液液压轴系连续末态；不是整机再启动初值',
            full_engine_initialization_supported=False,circuits=deepcopy(self.sample(t,y)['circuits']))

    def validity_margins(self,t,y):
        _,rows=self.evaluate(t,y)
        result=[]
        for c,row in zip(self.circuits,rows):
            label=c['label']; pm=c['pump_map']
            result.extend([(label+'：单相压力下限',(row['manifold_pressure_Pa']-c['pressure_floor_Pa'])/c['pump_head_Pa']),
                (label+'：满液小压缩适用域',c['max_compression_fraction']-abs(row['compression_fraction'])),
                (label+'：转速超过泵图适用域',pm['max_speed_ratio']-abs(row['speed_ratio'])),
                (label+'：流量超过泵图适用域',pm['max_flow_ratio']-abs(row['pump_flow_m3_s']/c['pump_flow_m3_s'])),
                (label+'：阀门行程越域',min(row['valve_opening'],1-row['valve_opening'])+max(1e-8,self.settings['rtol'])),
                (label+'：泵图负耗散',row['pump_loss_W']/(c['pump_head_Pa']*c['pump_flow_m3_s'])+1e-8)])
        return result

    def _margin(self,t,y):
        return min(value for _,value in self.validity_margins(t,y))

    def integrate(self,publish=None):
        s=self.settings
        events=deepcopy(self.events)
        output=sorted({round(float(t),14) for t in np.linspace(0,s['duration_s'],s['sample_count'])}|{round(e['time_s'],14) for e in events})
        breaks=sorted({0.,s['duration_s'],*(round(e['time_s'],14) for e in events)})
        y=self.initial.copy(); t=0.; index=1
        samples=[self.sample(t,y)]
        maxima=dict(mass=0.,hydraulic=0.,rotor=0.,total=0.,pump=0.)
        relative=dict(mass=0.,hydraulic=0.,rotor=0.,total=0.,pump=0.)
        keys=dict(mass='mass_balance_error_kg',hydraulic='hydraulic_energy_error_J',rotor='rotor_energy_error_J',total='total_energy_error_J',pump='pump_work_balance_error_J')
        accepted,nfev=0,0
        reason=None
        if publish: publish(samples[0])
        def audit(when,state):
            for row,p in zip(self.sample(when,state)['circuits'],self.parameters):
                for name,key in keys.items():
                    value=abs(row[key]); scale=p['mass_scale'] if name=='mass' else p['energy_scale']
                    maxima[name]=max(maxima[name],value); relative[name]=max(relative[name],value/scale)
        def append(when,state):
            row=self.sample(when,state)
            if row['time_s']>samples[-1]['time_s']+1e-12:
                samples.append(row)
                if publish: publish(row)
        if self._margin(0.,y)<=0:
            reason=min(self.validity_margins(0.,y),key=lambda v:v[1])[0]
        maxstep=min(.02,s['duration_s']/100,s['command_ramp_s']/4)
        for start,end in zip(breaks,breaks[1:]):
            if reason: break
            try:
                solver=Radau(self.rhs,t,y,end,rtol=s['rtol'],atol=s['rtol']*.001,max_step=maxstep,
                    first_step=min(end-t,min(c['valve_tau_s'] for c in self.circuits)*.02))
            except (ValueError,OverflowError,FloatingPointError) as exc:
                reason='积分器无法从最后有效状态继续：'+str(exc)[:240]
                break
            while solver.status=='running':
                checkpoint()
                previous_t=t
                try:
                    message=solver.step()
                except (ValueError,OverflowError,FloatingPointError) as exc:
                    reason='积分器无法在当前适用域内继续；保留最后有效状态：'+str(exc)[:240]
                    break
                if solver.status=='failed':
                    reason='数值积分未完成：'+str(message); break
                new_t,new_y=float(solver.t),solver.y.copy()
                accepted+=1
                dense=solver.dense_output()
                if self._margin(new_t,new_y)<=0:
                    new_t=float(brentq(lambda when:self._margin(when,dense(when)),previous_t,new_t,xtol=1e-11,rtol=1e-12))
                    new_y=dense(new_t)
                    reason=min(self.validity_margins(new_t,new_y),key=lambda v:v[1])[0]
                audit(new_t,new_y)
                while index<len(output) and output[index]<=new_t+1e-12:
                    when=output[index]
                    append(when,dense(min(when,new_t)))
                    index+=1
                t,y=new_t,new_y
                if reason: break
            nfev+=solver.nfev
        append(t,y)
        complete=reason is None and t>=s['duration_s']-1e-10
        for e in events: e['observed']=e['time_s']<=t+1e-12
        tolerance=max(100*s['rtol'],2e-6)
        audits=dict(status='pass' if max(relative.values())<=tolerance else 'review',
            max_abs_mass_error_kg=maxima['mass'],max_abs_hydraulic_energy_error_J=maxima['hydraulic'],
            max_abs_rotor_energy_error_J=maxima['rotor'],max_abs_total_energy_error_J=maxima['total'],
            max_abs_pump_work_error_J=maxima['pump'],relative_errors=relative,relative_tolerance=tolerance,
            basis='每个内部接受步逐回路校核质量、液压储能、转动能与总能量；回路误差不相互抵消。',experiment_validated=False)
        metrics=[]
        restart_observed=t>=s['restart_time_s']-1e-12
        for i,c in enumerate(self.circuits):
            series=[row['circuits'][i] for row in samples]
            before=[row for row in samples if row['time_s']<=s['restart_time_s']+1e-12]
            at_restart=before[-1]['circuits'][i] if restart_observed else None
            minimum=min(range(len(series)),key=lambda k:series[k]['speed_rpm'])
            flow_tolerance=c['rho_kg_m3']*c['pump_flow_m3_s']*1e-10
            metrics.append(dict(id=c['id'],label=c['label'],initial_speed_rpm=series[0]['speed_rpm'],
                final_speed_rpm=series[-1]['speed_rpm'],minimum_sampled_speed_rpm=series[minimum]['speed_rpm'],
                minimum_sampled_speed_time_s=samples[minimum]['time_s'],
                restart_event_observed=restart_observed,
                last_sample_before_restart_speed_rpm=None if at_restart is None else at_restart['speed_rpm'],
                minimum_sampled_pump_mass_flow_kg_s=min(row['pump_mass_flow_kg_s'] for row in series),
                minimum_sampled_outlet_mass_flow_kg_s=min(row['outlet_mass_flow_kg_s'] for row in series),
                sampled_pump_reverse_flow_observed=any(row['pump_mass_flow_kg_s'] < -flow_tolerance for row in series),
                sampled_outlet_reverse_flow_observed=any(row['outlet_mass_flow_kg_s'] < -flow_tolerance for row in series),
                reverse_flow_observation_tolerance_kg_s=flow_tolerance,
                sampled_reverse_rotation_observed=any(row['speed_ratio'] < -1e-10 for row in series),
                initial_rotor_energy_J=series[0]['rotor_energy_J'],final_rotor_energy_J=series[-1]['rotor_energy_J'],
                restart_success=None))
        return samples,dict(complete=complete,termination_reason=reason,audits=audits,
            model_description=self.model_description,events=events,
            metrics=dict(circuits=metrics,basis='极值与反流/反转标记仅来自输出采样；无标记不证明采样间未反流。再驱动事件到达和转速恢复均不是发动机再启动成功。'),
            initialization_snapshot=self.initialization_snapshot,endpoint_snapshot=self.snapshot(t,y),
            integrator=dict(method='Radau',rtol=s['rtol'],atol_scaled=s['rtol']*.001,nfev=nfev,accepted_steps=accepted,
                state_count=len(y),physical_states=5*len(self.circuits),conservation_ledger_states=8*len(self.circuits),
                event_policy='每个给定指令端点重新开始积分；所有物理状态和账目连续，不重置转速或库存。',
                boundary_policy='逐接受步检测适用域，定位连续边界并终止；不裁剪有符号流量与转速。'))
