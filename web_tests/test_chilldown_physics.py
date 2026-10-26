"""Conservation, phase-inventory, actuator and domain tests on synthetic cells."""
from copy import deepcopy
import math
import unittest

import CoolProp.CoolProp as CP
import numpy as np

from chilldown import ChilldownModel, PropertyDomainError, hem_mass_flux, validate_settings
from task_control import CancellationToken, TaskCancelled, cancellation_scope


def chill_fixture():
    return dict(duration_s=.5, sample_count=31, command_time_s=.1, command_ramp_s=.1,
        rtol=1e-7, circuits=[dict(id='ox', label='合成纯氧单元', fluid='Oxygen',
            volume_m3=.002, initial_mode='PT', initial_pressure_Pa=2e5,
            initial_temperature_K=110., initial_quality=.5, wall_temperature_K=110.,
            wall_heat_capacity_J_K=500., wall_UA_W_K=20., ambient_temperature_K=290.,
            ambient_UA_W_K=0., supply_pressure_Pa=8e5, supply_temperature_K=70.,
            back_pressure_Pa=1e5, inlet_CdA_m2=0., outlet_CdA_m2=0.,
            initial_inlet_opening=0., final_inlet_opening=0.,
            initial_outlet_opening=0., final_outlet_opening=0.,
            valve_tau_s=.05, valve_rate_per_s=2., min_pressure_Pa=5e4,
            max_pressure_Pa=1.5e6, max_wall_temperature_K=500.,
            target_wall_temperature_K=90., target_liquid_volume_fraction=.95)])


class ChilldownPhysicsTests(unittest.TestCase):
    def test_closed_isothermal_cell_is_a_fixed_point(self):
        model = ChilldownModel(chill_fixture())
        self.assertLess(max(abs(model.rhs(0., model.initial))), 1e-10)
        rows, meta = model.integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        self.assertEqual(meta['audits']['status'], 'pass')
        self.assertAlmostEqual(rows[-1]['circuits'][0]['temperature_K'], 110., places=8)
        self.assertEqual(rows[0]['circuits'][0]['mass_kg'], rows[-1]['circuits'][0]['mass_kg'])
        self.assertFalse(meta['readiness']['ignition_ready'])

    def test_two_phase_mass_and_volume_fractions_are_distinct_and_close(self):
        settings = chill_fixture()
        c = settings['circuits'][0]
        c.update(initial_mode='PQ', initial_quality=.25)
        c['wall_temperature_K'] = CP.PropsSI('T', 'P', c['initial_pressure_Pa'], 'Q', .25, c['fluid'])
        model = ChilldownModel(settings)
        row = model.sample(0., model.initial)['circuits'][0]
        self.assertEqual(row['phase'], 'twophase')
        self.assertAlmostEqual(row['quality'], .25, places=12)
        self.assertGreater(row['void_fraction'], .95)
        self.assertAlmostEqual(row['liquid_mass_kg']+row['vapor_mass_kg'], row['mass_kg'], places=13)
        V = row['liquid_mass_kg']/row['liquid_density_kg_m3']+row['vapor_mass_kg']/row['vapor_density_kg_m3']
        self.assertAlmostEqual(V, c['volume_m3'], places=13)
        self.assertLess(abs(row['phase_volume_closure_relative_error']), 1e-11)

    def test_closed_wall_heat_changes_phase_without_inventing_mass_or_latent_source(self):
        settings = chill_fixture()
        c = settings['circuits'][0]
        c.update(initial_mode='PQ', initial_quality=.1, wall_temperature_K=130., wall_UA_W_K=60.)
        rows, meta = ChilldownModel(settings).integrate()
        self.assertTrue(meta['complete'])
        first, last = rows[0]['circuits'][0], rows[-1]['circuits'][0]
        self.assertAlmostEqual(first['mass_kg'], last['mass_kg'], places=12)
        self.assertGreater(last['quality'], first['quality'])
        self.assertLess(last['wall_temperature_K'], first['wall_temperature_K'])
        self.assertGreater(last['fluid_internal_energy_J'], first['fluid_internal_energy_J'])
        self.assertLess(abs(last['fluid_energy_change_J']+last['wall_energy_change_J']), 1e-7)
        self.assertEqual(meta['audits']['status'], 'pass')

    def test_snapshot_round_trips_conservative_inventory_through_heos(self):
        settings = chill_fixture()
        settings['circuits'][0].update(initial_mode='PQ', initial_quality=.6, wall_temperature_K=140.)
        rows, meta = ChilldownModel(settings).integrate()
        snap = meta['endpoint_snapshot']['circuits'][0]
        eos = CP.AbstractState('HEOS', snap['fluid'])
        eos.update(CP.DmassUmass_INPUTS, snap['mass_kg']/snap['volume_m3'],
                   snap['fluid_internal_energy_J']/snap['mass_kg'])
        self.assertAlmostEqual(eos.p(), snap['pressure_Pa'], places=6)
        self.assertAlmostEqual(eos.T(), snap['temperature_K'], places=10)
        self.assertEqual(snap['mass_kg'], rows[-1]['circuits'][0]['mass_kg'])
        self.assertIn('不是整机', meta['endpoint_snapshot']['purpose'])

    def test_supercritical_state_does_not_invent_phase_partition(self):
        settings = chill_fixture()
        settings['circuits'][0].update(initial_pressure_Pa=6e6, initial_temperature_K=200.,
            wall_temperature_K=200., max_pressure_Pa=8e6)
        model = ChilldownModel(settings)
        row = model.sample(0., model.initial)['circuits'][0]
        self.assertEqual(row['phase'], 'supercritical')
        for key in ('quality', 'void_fraction', 'liquid_mass_kg', 'vapor_mass_kg', 'liquid_volume_fraction'):
            self.assertIsNone(row[key])
        self.assertFalse(model.readiness([model.sample(0., model.initial)], True)['checks'][0]['liquid_volume_met'])

    def test_fixed_source_inflow_uses_total_source_enthalpy_and_mixture_outflow(self):
        settings = chill_fixture()
        c = settings['circuits'][0]
        c.update(initial_mode='PQ', initial_quality=.4, inlet_CdA_m2=1e-7, outlet_CdA_m2=1e-7,
                 initial_inlet_opening=.7, final_inlet_opening=.7,
                 initial_outlet_opening=.5, final_outlet_opening=.5)
        model = ChilldownModel(settings)
        d, rows = model.evaluate(0., model.initial)
        r, p = rows[0], model.parameters[0]
        self.assertGreater(r['inlet_mass_flow_kg_s'], 0.)
        self.assertGreater(r['outlet_mass_flow_kg_s'], 0.)
        self.assertAlmostEqual(d[0]*p['mass_scale'], r['inlet_mass_flow_kg_s']-r['outlet_mass_flow_kg_s'])
        expected = r['inlet_mass_flow_kg_s']*p['source']['enthalpy_J_kg']-r['outlet_mass_flow_kg_s']*r['enthalpy_J_kg']+r['heat_to_fluid_W']
        self.assertAlmostEqual(d[1]*p['energy_scale'], expected)
        # No pressure-work add-on and no separate vapour-only outlet enthalpy.
        eos = CP.AbstractState('HEOS', 'Oxygen')
        eos.update(CP.PQ_INPUTS, r['pressure_Pa'], 1.)
        self.assertGreater(abs(eos.hmass()-r['enthalpy_J_kg']), 1e4)

    def test_check_valves_prevent_both_reverse_boundary_flows(self):
        settings = chill_fixture()
        c = settings['circuits'][0]
        c.update(supply_pressure_Pa=1.5e5, back_pressure_Pa=3e5,
                 inlet_CdA_m2=1e-6, outlet_CdA_m2=1e-6,
                 initial_inlet_opening=1., final_inlet_opening=1.,
                 initial_outlet_opening=1., final_outlet_opening=1.)
        row = ChilldownModel(settings).sample(0., ChilldownModel(settings).initial)['circuits'][0]
        self.assertEqual(row['inlet_mass_flow_kg_s'], 0.)
        self.assertEqual(row['outlet_mass_flow_kg_s'], 0.)

    def test_valve_is_dynamic_and_rate_limited_not_instant_command(self):
        settings = chill_fixture()
        settings['circuits'][0]['final_inlet_opening'] = 1.
        model = ChilldownModel(settings)
        rows, meta = model.integrate()
        self.assertTrue(meta['complete'])
        after = next(r['circuits'][0] for r in rows if r['time_s'] >= .2)
        self.assertLess(after['inlet_opening'], after['inlet_command'])
        for a, b in zip(rows, rows[1:]):
            rate = (b['circuits'][0]['inlet_opening']-a['circuits'][0]['inlet_opening'])/(b['time_s']-a['time_s'])
            self.assertLessEqual(rate, 2.00001)
            self.assertGreaterEqual(b['circuits'][0]['inlet_opening'], -1e-10)

    def test_pressure_limit_stops_with_last_valid_sample_and_incomplete_snapshot(self):
        settings = chill_fixture()
        c = settings['circuits'][0]
        c.update(initial_temperature_K=200., wall_temperature_K=400., wall_UA_W_K=100.,
                 max_pressure_Pa=2.1e5)
        rows, meta = ChilldownModel(settings).integrate()
        self.assertFalse(meta['complete'])
        self.assertIn('最高允许压力', meta['termination_reason'])
        self.assertLess(rows[-1]['time_s'], settings['duration_s'])
        self.assertAlmostEqual(rows[-1]['circuits'][0]['pressure_Pa'], c['max_pressure_Pa'], delta=.1)
        self.assertEqual(meta['readiness']['status'], 'incomplete')
        self.assertEqual(meta['audits']['status'], 'pass')

    def test_negative_mass_is_an_error_not_an_inventory_clamp(self):
        model = ChilldownModel(chill_fixture())
        y = model.initial.copy()
        y[0] = -1e-4
        with self.assertRaises(PropertyDomainError):
            model.rhs(0., y)

    def test_saturated_pt_and_noncondensable_or_vapor_supply_are_rejected(self):
        base = chill_fixture()
        for change in [dict(fluid='Nitrogen'), dict(supply_temperature_K=290.),
                       dict(initial_temperature_K=CP.PropsSI('T', 'P', 2e5, 'Q', .5, 'Oxygen'))]:
            s = deepcopy(base)
            s['circuits'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ChilldownModel(s)

    def test_hem_flux_chokes_and_is_independent_of_further_backpressure_reduction(self):
        eos = CP.AbstractState('HEOS', 'Oxygen')
        eos.update(CP.PT_INPUTS, 5e5, 290.)
        p, h, entropy = eos.p(), eos.hmass(), eos.smass()
        outputs = [hem_mass_flux('Oxygen', p, h, entropy, pd) for pd in (4e5, 2e5, 1e5, 5e4)]
        self.assertFalse(outputs[0]['choked'])
        self.assertTrue(all(v['choked'] for v in outputs[1:]))
        self.assertGreater(outputs[1]['mass_flux_kg_m2_s'], outputs[0]['mass_flux_kg_m2_s'])
        self.assertAlmostEqual(outputs[1]['mass_flux_kg_m2_s']/outputs[-1]['mass_flux_kg_m2_s'], 1., places=9)

    def test_two_phase_hem_peak_agrees_with_a_refined_pressure_search(self):
        eos = CP.AbstractState('HEOS', 'Methane')
        eos.update(CP.PQ_INPUTS, 6e5, .05)
        args = ('Methane', eos.p(), eos.hmass(), eos.smass(), 5e4)
        coarse = hem_mass_flux(*args)
        fine = hem_mass_flux(*args, grid_points=121)
        self.assertTrue(coarse['choked'])
        self.assertAlmostEqual(coarse['mass_flux_kg_m2_s']/fine['mass_flux_kg_m2_s'], 1., places=7)

    def test_cancellation_is_not_caught_as_a_property_failure(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(TaskCancelled), cancellation_scope(token):
            ChilldownModel(chill_fixture()).integrate()

    def test_audit_does_not_hide_equal_opposite_wall_and_fluid_energy_errors(self):
        model = ChilldownModel(chill_fixture())
        row = model.sample(0., model.initial)
        row['circuits'][0]['fluid_energy_balance_error_J'] = 100.
        row['circuits'][0]['wall_energy_balance_error_J'] = -100.
        self.assertEqual(row['circuits'][0]['energy_balance_error_J'], 0.)
        result = model.audits([row])
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['max_abs_fluid_energy_balance_error_J'], 100.)

    def test_independent_circuit_mass_errors_cannot_cancel(self):
        settings = chill_fixture()
        second = deepcopy(settings['circuits'][0])
        second['id'] = 'ox2'
        settings['circuits'].append(second)
        model = ChilldownModel(settings)
        row = model.sample(0., model.initial)
        row['circuits'][0]['mass_balance_error_kg'] = .1
        row['circuits'][1]['mass_balance_error_kg'] = -.1
        self.assertEqual(model.audits([row])['status'], 'review')

    def test_invalid_settings_do_not_repair_inputs(self):
        settings = chill_fixture()
        original = deepcopy(settings)
        validate_settings(settings)
        self.assertEqual(settings, original)
        for key, value in [('wall_heat_capacity_J_K', 0.), ('volume_m3', -1.),
                           ('initial_quality', 1.1), ('back_pressure_Pa', 1e4)]:
            s = deepcopy(settings)
            s['circuits'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                ChilldownModel(s)


if __name__ == '__main__':
    unittest.main()
