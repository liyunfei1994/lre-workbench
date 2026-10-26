"""Continuous gaseous start/close/heat-soak/reopen/prescribed-pulse experiment.

The existing conservative reactor is never replaced or reset at a phase change.
No liquid interface, full-engine coupling or reignition-success criterion is
implied by the event labels.
"""
from copy import deepcopy
import math

from ignition import IgnitionModel, default_settings as ignition_defaults, validate_settings as validate_ignition


def default_settings():
    return dict(ignition_defaults(), duration_s=6., sample_count=301,
        wall_temperature_K=600., wall_UA_W_K=3., shutdown_time_s=2.,
        restart_time_s=4., reignition_time_s=4.3, reignition_duration_s=.02,
        reignition_energy_J=500.)


EXTRA = {'shutdown_time_s','restart_time_s','reignition_time_s',
         'reignition_duration_s','reignition_energy_J'}


def validate_settings(settings):
    if not isinstance(settings,dict) or set(settings)!=set(default_settings()):
        raise ValueError('气相关机研究设置不完整或含未知字段。')
    base=validate_ignition({k:v for k,v in settings.items() if k not in EXTRA})
    for k in EXTRA:
        v=settings[k]
        try: ok=not isinstance(v,bool) and isinstance(v,(float,int)) and math.isfinite(v) and 0<=v<= (1e7 if k=='reignition_energy_J' else 30.)
        except (OverflowError,ValueError): ok=False
        if not ok: raise ValueError(f'{k} 必须为范围内有限数值。')
        base[k]=float(v)
    s=base; ramp=s['valve_ramp_s']
    if max(s['ox_open_time_s']+ramp,s['fuel_open_time_s']+ramp,
           s['ignition_time_s']+s['ignition_duration_s'])>=s['shutdown_time_s']:
        raise ValueError('首次供给与热脉冲结束后才能开始关阀。')
    if not s['shutdown_time_s']+ramp<s['restart_time_s']<s['duration_s']-ramp:
        raise ValueError('关阀斜坡、停留、再开阀斜坡必须按时间顺序且位于仿真范围内。')
    if not s['restart_time_s']<=s['reignition_time_s'] or s['reignition_duration_s']<1e-6 or s['reignition_time_s']+s['reignition_duration_s']>s['duration_s']:
        raise ValueError('再次给定热脉冲必须位于再次供给之后并在积分终点前结束。')
    return s


class ShutdownGasModel(IgnitionModel):
    def __init__(self,settings):
        checked=validate_settings(settings)
        self.sequence={k:checked[k] for k in EXTRA}
        super().__init__({k:v for k,v in checked.items() if k not in EXTRA})
        self.model_description.update(name='气相关机、热浸与给定再点火研究单元',
            state_continuity='首次供给、关阀、停留与再次供给共用同一 m/U/Y/Twall/阀位及累计账目；事件不重置状态。',
            restart_model='再次开启入口并施加独立给定热脉冲；不能据此推断自动再点火成功或熄火。',
            reverse_flow='本单元入口与出口仍有理想止回；液压反流在独立轴系研究单元中计算。')

    def command(self,t,side):
        s=self.settings; q=self.sequence
        initial=super().command(t,side)
        down=min(1.,max(0.,(t-q['shutdown_time_s'])/s['valve_ramp_s']))
        up=min(1.,max(0.,(t-q['restart_time_s'])/s['valve_ramp_s']))
        return initial-s[f'final_{side}_opening']*down+s[f'final_{side}_opening']*up

    def _power(self,t):
        q=self.sequence
        second=q['reignition_energy_J']/q['reignition_duration_s'] if round(q['reignition_time_s'],14)<=t<round(q['reignition_time_s']+q['reignition_duration_s'],14) else 0.
        return super()._power(t)+second

    def scheduled_events(self):
        q=self.sequence; r=self.settings['valve_ramp_s']
        return super().scheduled_events()+[
            ('双入口关阀指令开始',q['shutdown_time_s']),('关阀指令结束／停留开始',q['shutdown_time_s']+r),
            ('再次供给指令开始',q['restart_time_s']),('再开阀指令结束',q['restart_time_s']+r),
            ('再次给定热脉冲开始',q['reignition_time_s']),('再次给定热脉冲结束',q['reignition_time_s']+q['reignition_duration_s'])]

    def integrate(self,publish=None):
        emitted=0
        def wrapped(rows,meta):
            nonlocal emitted
            if publish:
                for row in rows[emitted:]: publish(self.wrap_row(row))
            emitted=len(rows)
        rows,meta=super().integrate(wrapped)
        if publish:
            for row in rows[emitted:]: publish(self.wrap_row(row))
        for key in ('initialization_snapshot','endpoint_snapshot'):
            st=meta[key]
            meta[key]=dict(schema='shutdown-gas-state-v1',time_s=st['time_s'],
                circuits=[dict(st,id='chamber',label='气相反应容腔')],engine_initialization_supported=False)
        # Observations are differences along a single integrated trajectory,
        # not prescribed empty inventories or a newly manufactured restart state.
        before=max((r for r in rows if r['time_s']<=self.sequence['shutdown_time_s']+1e-12),key=lambda r:r['time_s'])
        restart=max((r for r in rows if r['time_s']<=self.sequence['restart_time_s']+1e-12),key=lambda r:r['time_s'])
        reached=meta['endpoint_snapshot']['time_s']>=self.sequence['restart_time_s']-1e-12
        meta['metrics'].update(restart_event_observed=reached,reignition_success=None,
            restart_residual_mass_kg=restart['mass_kg'] if reached else None,
            restart_wall_temperature_K=restart['wall_temperature_K'] if reached else None,
            shutdown_to_restart_mass_change_kg=restart['mass_kg']-before['mass_kg'] if reached else None)
        return [self.wrap_row(row) for row in rows],meta

    @staticmethod
    def wrap_row(row):
        return dict(time_s=row['time_s'],circuits=[dict(deepcopy(row),id='chamber',label='气相反应容腔')])
