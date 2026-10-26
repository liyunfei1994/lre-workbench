"""Independent physical checks on shutdown / soak / refill inventory history."""
from copy import deepcopy
import unittest

import CoolProp.CoolProp as CP
import numpy as np
from scipy.integrate import trapezoid
from scipy.optimize import brentq

from shutdown_inventory import ShutdownInventoryModel, default_settings, validate_settings


class ShutdownInventoryTests(unittest.TestCase):
    def test_default_discharge_then_refill_keeps_continuous_inventory(self):
        s = default_settings()
        rows, meta = ShutdownInventoryModel(s).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        self.assertEqual(meta['audits']['status'], 'pass')
        a = rows[0]['circuits'][0]
        b = min(rows, key=lambda r: abs(r['time_s']-s['restart_time_s']))['circuits'][0]
        z = rows[-1]['circuits'][0]
        self.assertLess(b['mass_kg'], a['mass_kg']*.8)
        self.assertGreater(z['mass_kg'], b['mass_kg'])
        self.assertLess(z['wall_temperature_K'], b['wall_temperature_K'])
        self.assertGreater(b['mass_kg'], 0.)
        self.assertGreater(b['wall_temperature_K'], 110.)
        self.assertGreater(z['integrated_outlet_mass_kg'], b['integrated_outlet_mass_kg'])
        self.assertGreater(z['integrated_inlet_mass_kg'], b['integrated_inlet_mass_kg'])
        self.assertEqual(meta['endpoint_snapshot']['schema'], 'shutdown-inventory-state-v1')
        self.assertFalse(meta['readiness']['restart_ready'])
        self.assertFalse(meta['model_description']['reverse_flow_supported'])
        self.assertTrue(all(e['observed'] for e in meta['events']))
        self.assertIsNone(meta['metrics']['circuits'][0]['complete_drain'])

    def test_closed_thermal_soak_matches_independent_equilibrium_energy(self):
        s = default_settings()
        s.update(duration_s=15., restart_time_s=10., sample_count=101)
        c = s['circuits'][0]
        c.update(initial_mode='PT', initial_pressure_Pa=2e5, initial_temperature_K=150.,
                 wall_temperature_K=180., wall_heat_capacity_J_K=.1, wall_UA_W_K=2.,
                 inlet_CdA_m2=0., outlet_CdA_m2=0.)
        rho = CP.PropsSI('D', 'P', c['initial_pressure_Pa'], 'T', c['initial_temperature_K'], c['fluid'])
        mass = rho*c['volume_m3']
        U0 = mass*CP.PropsSI('U', 'D', rho, 'T', c['initial_temperature_K'], c['fluid'])
        Cw = c['wall_heat_capacity_J_K']
        target = U0+Cw*c['wall_temperature_K']
        Teq = brentq(lambda T: mass*CP.PropsSI('U', 'D', rho, 'T', T, c['fluid'])+Cw*T-target,
                      c['initial_temperature_K'], c['wall_temperature_K'])
        rows, meta = ShutdownInventoryModel(s).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        final = rows[-1]['circuits'][0]
        self.assertAlmostEqual(final['mass_kg'], mass, places=13)
        self.assertAlmostEqual(final['temperature_K'], Teq, places=5)
        self.assertAlmostEqual(final['wall_temperature_K'], Teq, places=5)
        self.assertLess(abs(final['fluid_energy_change_J']+final['wall_energy_change_J']), 1e-7)
        self.assertTrue(meta['events'][2]['observed'])

    def test_three_phase_commands_are_continuous_and_not_state_resets(self):
        s = default_settings()
        model = ShutdownInventoryModel(s)
        c = model.circuits[0]
        for port in ('inlet', 'outlet'):
            self.assertEqual(model.command_for(0., c, port), c[f'initial_{port}_opening'])
            self.assertAlmostEqual(model.command_for(.35, c, port),
                .5*(c[f'initial_{port}_opening']+c[f'shutdown_{port}_opening']))
            self.assertEqual(model.command_for(1., c, port), c[f'shutdown_{port}_opening'])
            self.assertAlmostEqual(model.command_for(1.85, c, port),
                .5*(c[f'shutdown_{port}_opening']+c[f'restart_{port}_opening']))
            self.assertEqual(model.command_for(2., c, port), c[f'restart_{port}_opening'])
        self.assertEqual(model.event_times(), [.3, .4, 1.8, 1.9000000000000001])
        # At the same physical state, changing the command affects valve d/dt,
        # not inventory, wall temperature, or actual opening instantaneously.
        first = model.sample(0., model.initial)['circuits'][0]
        later = model.sample(1., model.initial)['circuits'][0]
        for key in ('mass_kg', 'fluid_internal_energy_J', 'wall_temperature_K', 'inlet_opening', 'outlet_opening'):
            self.assertEqual(first[key], later[key])
        self.assertNotEqual(first['inlet_command'], later['inlet_command'])

    def test_off_grid_events_execute_with_output_quadrature_crosscheck(self):
        s = default_settings()
        s.update(shutdown_time_s=.307, restart_time_s=1.823, command_ramp_s=.117, sample_count=1001)
        rows, meta = ShutdownInventoryModel(s).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        times = np.array([r['time_s'] for r in rows])
        data = [r['circuits'][0] for r in rows]
        dm = trapezoid([x['inlet_mass_flow_kg_s']-x['outlet_mass_flow_kg_s'] for x in data], times)
        energy = trapezoid([x['inlet_enthalpy_flow_W']-x['outlet_enthalpy_flow_W']+x['heat_from_ambient_W'] for x in data], times)
        self.assertLess(abs(data[-1]['mass_kg']-data[0]['mass_kg']-dm), 2e-6)
        self.assertLess(abs(data[-1]['fluid_energy_change_J']+data[-1]['wall_energy_change_J']-energy), .25)
        self.assertTrue(all(e['observed'] for e in meta['events']))
        for event in meta['events']:
            self.assertIn(event['time_s'], times)

    def test_domain_stop_preserves_valid_state_and_unobserved_restart(self):
        s = default_settings()
        c = s['circuits'][0]
        c.update(min_pressure_Pa=3.9e5, back_pressure_Pa=3.95e5,
                 wall_temperature_K=70., wall_UA_W_K=20.,
                 initial_inlet_opening=0., shutdown_inlet_opening=0.,
                 inlet_CdA_m2=0.)
        rows, meta = ShutdownInventoryModel(s).integrate()
        self.assertFalse(meta['complete'])
        self.assertIn('最低允许压力', meta['termination_reason'])
        self.assertGreater(rows[-1]['time_s'], 0.)
        self.assertLess(rows[-1]['time_s'], s['restart_time_s'])
        self.assertGreater(rows[-1]['circuits'][0]['mass_kg'], 0.)
        self.assertFalse(meta['events'][2]['observed'])
        self.assertFalse(meta['metrics']['circuits'][0]['restart_event_observed'])
        self.assertIsNone(meta['metrics']['circuits'][0]['last_sample_before_restart_mass_kg'])
        self.assertAlmostEqual(rows[-1]['circuits'][0]['pressure_Pa'], c['min_pressure_Pa'], places=2)

    def test_two_independent_circuits_preserve_separate_audits(self):
        s = default_settings()
        s.update(duration_s=1., restart_time_s=.8, sample_count=21)
        b = deepcopy(s['circuits'][0])
        b.update(id='fuel', label='甲烷独立研究单元', fluid='Methane',
                 initial_temperature_K=125., wall_temperature_K=140., supply_temperature_K=105.)
        s['circuits'].append(b)
        rows, meta = ShutdownInventoryModel(s).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        self.assertEqual(len(rows[-1]['circuits']), 2)
        self.assertEqual(meta['audits']['status'], 'pass')
        self.assertNotEqual(rows[0]['circuits'][0]['temperature_K'], rows[0]['circuits'][1]['temperature_K'])

    def test_strict_inputs_do_not_silently_substitute_or_overlap_events(self):
        s = default_settings()
        self.assertNotIn('command_time_s', s)
        self.assertNotIn('final_inlet_opening', s['circuits'][0])
        cases = []
        for key, value in [('restart_time_s', .35), ('restart_time_s', float('nan')),
                           ('shutdown_time_s', 10**1000), ('command_time_s', 0.),
                           ('restart_time_s', 3.)]:
            item = deepcopy(s); item[key] = value; cases.append(item)
        for key, value in [('fluid', 'RP-1'), ('shutdown_inlet_opening', 1.1),
                           ('restart_outlet_opening', True), ('final_outlet_opening', 0.)]:
            item = deepcopy(s); item['circuits'][0][key] = value; cases.append(item)
        for item in cases:
            with self.subTest(item=str(item)[:100]), self.assertRaises(ValueError):
                validate_settings(item)
        with self.assertRaisesRegex(ValueError, '单液相'):
            wrong_phase = deepcopy(s)
            wrong_phase['circuits'][0]['supply_temperature_K'] = 300.
            ShutdownInventoryModel(wrong_phase)

    def test_backpressure_blocks_reverse_flow_in_inventory_mode(self):
        s = default_settings()
        s['circuits'][0].update(supply_pressure_Pa=2e5, back_pressure_Pa=5e5)
        model = ShutdownInventoryModel(s)
        first = model.sample(0., model.initial)['circuits'][0]
        self.assertEqual(first['inlet_mass_flow_kg_s'], 0.)
        self.assertEqual(first['outlet_mass_flow_kg_s'], 0.)

    def test_tolerance_and_output_grid_refinement_preserve_state_and_integrals(self):
        results = {}
        keys = ('mass_kg', 'pressure_Pa', 'temperature_K', 'wall_temperature_K',
                'fluid_internal_energy_J', 'integrated_inlet_mass_kg', 'integrated_outlet_mass_kg',
                'integrated_inlet_energy_J', 'integrated_outlet_energy_J', 'integrated_wall_heat_J')
        for tolerance in (1e-6, 2e-7):
            for count in (41, 501):
                s = default_settings()
                s.update(rtol=tolerance, sample_count=count)
                rows, meta = ShutdownInventoryModel(s).integrate()
                self.assertTrue(meta['complete'], meta['termination_reason'])
                self.assertEqual(meta['audits']['status'], 'pass')
                results[(tolerance, count)] = (rows[-1]['circuits'][0], meta)
            sparse, dense = results[(tolerance, 41)], results[(tolerance, 501)]
            self.assertEqual(sparse[1]['integrator']['nfev'], dense[1]['integrator']['nfev'])
            for key in keys:
                self.assertEqual(sparse[0][key], dense[0][key], key)
        nominal, refined = results[(1e-6, 501)][0], results[(2e-7, 501)][0]
        for key in keys:
            self.assertLess(abs(nominal[key]-refined[key])/max(abs(refined[key]), 1e-6), 1e-5, key)


if __name__ == '__main__':
    unittest.main()
