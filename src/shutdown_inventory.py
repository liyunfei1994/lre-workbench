"""Continuous shutdown / soak / refill of a pure-fluid thermal inventory cell.

This module shares the tested HEOS conservative states and HEM outflow model
with chilldown. Only the prescribed valve command history changes. There is
no rotor, reverse boundary flow, phase-selective vent, combustion or ignition.
"""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np

from chilldown import ChilldownModel, validate_settings as validate_chilldown


def default_settings():
    """Independent synthetic oxygen cell; not a named engine's hardware data."""
    return dict(duration_s=3., sample_count=121, rtol=1e-6,
        shutdown_time_s=.3, restart_time_s=1.8, command_ramp_s=.1,
        circuits=[dict(id='ox', label='纯氧库存研究单元', fluid='Oxygen',
            volume_m3=.0001, initial_mode='PQ', initial_pressure_Pa=4e5,
            initial_temperature_K=106., initial_quality=.02,
            wall_temperature_K=120., wall_heat_capacity_J_K=20., wall_UA_W_K=1.,
            ambient_temperature_K=290., ambient_UA_W_K=0.,
            supply_pressure_Pa=8e5, supply_temperature_K=90., back_pressure_Pa=1e5,
            inlet_CdA_m2=2e-7, outlet_CdA_m2=2e-6,
            initial_inlet_opening=.2, initial_outlet_opening=.1,
            shutdown_inlet_opening=0., shutdown_outlet_opening=1.,
            restart_inlet_opening=1., restart_outlet_opening=0.,
            valve_tau_s=.025, valve_rate_per_s=20., min_pressure_Pa=5e4,
            max_pressure_Pa=1.5e6, max_wall_temperature_K=500.,
            target_wall_temperature_K=110., target_liquid_volume_fraction=.8)])


def _finite(value, label, low, high):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid or not low <= value <= high:
        raise ValueError(f'{label}：必须是 [{low:g}, {high:g}] 内的有限数值。')
    return float(value)


def _base_settings(settings):
    """Private compatibility values; never accepted as competing public input."""
    base = deepcopy(settings)
    base['command_time_s'] = base['shutdown_time_s']
    for c in base['circuits']:
        c['final_inlet_opening'] = c['restart_inlet_opening']
        c['final_outlet_opening'] = c['restart_outlet_opening']
    return base


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('关机库存设置必须是对象。')
    out = deepcopy(settings)
    expected = default_settings()
    if set(out)-set(expected):
        raise ValueError('存在不支持的关机库存字段：'+', '.join(sorted(set(out)-set(expected))))
    for k, low, high in [('shutdown_time_s', 0., 300.), ('restart_time_s', 0., 600.),
                          ('command_ramp_s', 1e-5, 300.)]:
        out[k] = _finite(out.get(k), k, low, high)
    if out['restart_time_s'] <= out['shutdown_time_s']+out['command_ramp_s']:
        raise ValueError('再次开启命令必须晚于关机命令斜坡结束，不能重叠。')
    duration = _finite(out.get('duration_s'), 'duration_s', .001, 300.)
    if out['restart_time_s']+out['command_ramp_s'] > duration:
        raise ValueError('仿真时长必须覆盖再次开启命令斜坡结束。')
    if not isinstance(out.get('circuits'), list) or not 1 <= len(out['circuits']) <= 2:
        raise ValueError('必须提供 1 至 2 个独立库存单元。')
    circuit_keys = set(expected['circuits'][0])
    for c in out['circuits']:
        if not isinstance(c, dict):
            raise ValueError('库存单元必须是对象。')
        if set(c)-circuit_keys:
            raise ValueError('存在不支持的库存单元字段：'+', '.join(sorted(set(c)-circuit_keys)))
        for phase in ('shutdown', 'restart'):
            for port in ('inlet', 'outlet'):
                key = f'{phase}_{port}_opening'
                c[key] = _finite(c.get(key), key, 0., 1.)
    # Delegate the existing complete physical scalar/initial-state contract.
    try:
        checked = validate_chilldown(_base_settings(out))
    except OverflowError as exc:
        raise ValueError('关机库存参数超出可表示数值范围。') from exc
    checked.pop('command_time_s')
    for c in checked['circuits']:
        c.pop('final_inlet_opening')
        c.pop('final_outlet_opening')
    return checked


class ShutdownInventoryModel(ChilldownModel):
    """Three prescribed phases with continuous m/U/Twall/valve state."""

    def __init__(self, settings):
        public = validate_settings(settings)
        super().__init__(_base_settings(public))
        names = ['关机开度命令开始', '关机开度命令斜坡结束', '再次开启命令开始', '再次开启命令斜坡结束']
        self.events = [dict(name=name, time_s=float(when), kind='prescribed', observed=False)
                       for name, when in zip(names, self.event_times())]
        self.model_description.update(
            name='关机排放与再充填库存研究单元',
            lifecycle='初始工作开度 → 关机/排放开度 → 再次开启开度；质量、总内能、壁温和实际阀位始终连续积分。',
            parameter_source='独立研究示例；不是任何参考发动机的真实关机阀序、容积或热参数。',
            restart_scope='再次开启后的库存/热状态响应；不计算点火成功、涡轮泵启动或整机再启动。',
            reverse_flow_supported=False, rotor_dynamics_supported=False,
            complete_drain_prediction=False,
            limits='入口/出口理想止回，不计算反流；无转子、燃烧或非凝气体。均相排放不表示液相选择性排空，残余库存不得强制清零。')
        self.initialization_snapshot = self.snapshot(0., self.initial)

    def event_times(self):
        s = self.settings
        return [s['shutdown_time_s'], s['shutdown_time_s']+s['command_ramp_s'],
                s['restart_time_s'], s['restart_time_s']+s['command_ramp_s']]

    def output_times(self):
        # Prefer exact prescribed event times when they nearly coincide with
        # the regular grid, preventing floating-point duplicate timestamps.
        events = self.event_times()
        grid = super().output_times()
        return np.asarray(sorted(events+[float(t) for t in grid
            if not any(abs(t-e) <= 1e-11 for e in events)]))

    def command_for(self, t, circuit, port):
        s = self.settings
        initial, closed, reopened = (circuit[f'{phase}_{port}_opening']
                                    for phase in ('initial', 'shutdown', 'restart'))
        ramp = s['command_ramp_s']
        closing = min(1., max(0., (t-s['shutdown_time_s'])/ramp))
        opening = min(1., max(0., (t-s['restart_time_s'])/ramp))
        return initial+(closed-initial)*closing+(reopened-closed)*opening

    def phase_at(self, t):
        s = self.settings
        if t < s['shutdown_time_s']:
            return 'initial_operation'
        if t < s['shutdown_time_s']+s['command_ramp_s']:
            return 'shutdown_command_ramp'
        if t < s['restart_time_s']:
            return 'discharge_and_thermal_soak'
        if t < s['restart_time_s']+s['command_ramp_s']:
            return 'restart_command_ramp'
        return 'refill_response'

    def sample(self, t, y):
        result = super().sample(t, y)
        result['phase'] = self.phase_at(float(t))
        for row, par in zip(result['circuits'], self.parameters):
            row['residual_mass_fraction_of_initial'] = row['mass_kg']/par['mass0']
        return result

    def snapshot(self, t, y):
        snap = super().snapshot(t, y)
        snap.update(schema='shutdown-inventory-state-v1', phase=self.phase_at(float(t)),
            purpose='关机-停留-再充填连续库存/壁面终态；不是整机或点火初值；不自动覆盖其他模块。')
        return snap

    def readiness(self, samples, complete):
        base = super().readiness(samples, complete)
        base.update(ignition_ready=False, restart_ready=False,
            basis='仅用户壁温与液相体积分数研究阈值；不是排空合格、点火或整机再启动许可。')
        return base

    def integrate(self, publish=None):
        rows, metadata = super().integrate(publish)
        final_time = rows[-1]['time_s']
        metadata['events'] = [dict(e, observed=bool(e['time_s'] <= final_time+1e-10)) for e in self.events]
        metadata['integrator']['event_times_s'] = self.event_times()
        metadata['integrator']['continuity_policy'] = '阀序事件处重新启动积分器；连续传递 m/U/Twall/阀位和累计账本，不重置状态。'
        metrics = []
        for i, c in enumerate(self.circuits):
            series = [r['circuits'][i] for r in rows]
            minimum = min(range(len(series)), key=lambda j: series[j]['mass_kg'])
            before = [r for r in rows if r['time_s'] <= self.settings['restart_time_s']]
            observed = final_time >= self.settings['restart_time_s']-1e-10
            at_restart = before[-1]['circuits'][i] if observed and before else None
            metrics.append(dict(id=c['id'], label=c['label'], initial_mass_kg=series[0]['mass_kg'],
                final_mass_kg=series[-1]['mass_kg'], minimum_sampled_mass_kg=series[minimum]['mass_kg'],
                minimum_sampled_mass_time_s=rows[minimum]['time_s'],
                final_wall_temperature_K=series[-1]['wall_temperature_K'],
                restart_event_observed=observed,
                last_sample_before_restart_mass_kg=None if at_restart is None else at_restart['mass_kg'],
                integrated_inlet_mass_kg=series[-1]['integrated_inlet_mass_kg'],
                integrated_outlet_mass_kg=series[-1]['integrated_outlet_mass_kg'],
                complete_drain=None, ignition_success=None, restart_success=None))
        metadata['metrics'] = dict(circuits=metrics,
            basis='最小值来自输出采样；累计质量来自积分账本。再次开启不等于点火；正库存不被重置。')
        return rows, metadata
