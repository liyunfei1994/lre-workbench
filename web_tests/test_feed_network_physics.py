"""Analytical, conservation and domain tests of the single-phase feed model.

All parameters below are synthetic hydraulic circuits, not engine calibration.
"""
from copy import deepcopy
import math
import unittest

import numpy as np

from feed_network import FeedNetworkModel, evaluate_pump_map, validate_settings
from task_control import CancellationToken, TaskCancelled, cancellation_scope


def network_fixture():
    return dict(duration_s=.4, sample_count=81, command_time_s=.1,
        command_ramp_s=.05, rtol=1e-7, circuits=[dict(
            id='test', label='合成液压回路', rho_kg_m3=1000., bulk_modulus_Pa=2e7,
            pressure_floor_Pa=1e5, max_compression_fraction=.05,
            tank_pressure_Pa=2e5, outlet_pressure_Pa=2e5,
            initial_manifold_pressure_Pa=1.15e6, volume_m3=.02,
            pump_head_Pa=1e6, pump_flow_m3_s=.001, pump_speed_rpm=10000.,
            pump_efficiency=.8, inlet_length_m=.5, inlet_diameter_m=.04,
            inlet_loss_Pa=5e4, outlet_length_m=.5, outlet_diameter_m=.04,
            outlet_loss_Pa=2e5, valve_loss_Pa=7.5e5,
            valve_tau_s=.05, valve_rate_per_s=5., leak_fraction=.01,
            initial_valve_opening=1., final_valve_opening=.9,
            initial_speed_ratio=1., final_speed_ratio=1.,
            initial_pump_flow_ratio=1., initial_outlet_flow_ratio=1.,
            pump_map=dict(kind='analytic_demo', slope=.2,
                source='解析演示特性，未实测标定', max_speed_ratio=1.25, max_flow_ratio=2.))])


def passive_suter_table():
    """Sample the analytic map densely in the exact documented normalization."""
    c = network_fixture()['circuits'][0]
    points = []
    for angle in range(0, 361, 5):
        theta = math.radians(angle)
        data = evaluate_pump_map(c, math.cos(theta), math.sin(theta))
        tau0 = c['pump_head_Pa']*c['pump_flow_m3_s']/(c['pump_efficiency']*c['pump_speed_rpm']*math.pi/30)
        points.append(dict(theta_deg=angle, head=data['pump_head_Pa']/c['pump_head_Pa'],
                           torque=data['pump_torque_Nm']/tau0))
    return points


class FeedNetworkPhysicsTests(unittest.TestCase):
    def test_nominal_map_anchor_and_true_steady_fixed_point(self):
        settings = network_fixture()
        settings['circuits'][0]['final_valve_opening'] = 1.
        c = settings['circuits'][0]
        pump = evaluate_pump_map(c, 1., 1.)
        self.assertAlmostEqual(pump['pump_head_Pa'], c['pump_head_Pa'])
        self.assertAlmostEqual(pump['pump_shaft_power_W'], c['pump_head_Pa']*c['pump_flow_m3_s']/c['pump_efficiency'])
        model = FeedNetworkModel(settings)
        self.assertLess(max(abs(model.rhs(0., model.initial)[:4])), 1e-10)
        rows, meta = model.integrate()
        self.assertTrue(meta['complete'])
        self.assertLess(max(abs(r['circuits'][0]['manifold_pressure_Pa']/1.15e6-1) for r in rows), 1e-12)
        self.assertEqual(meta['audits']['status'], 'pass')

    def test_zero_speed_zero_flow_and_all_four_quadrants_are_finite_and_passive(self):
        c = network_fixture()['circuits'][0]
        for n in np.linspace(-1.2, 1.2, 9):
            for q in np.linspace(-1.8, 1.8, 13):
                with self.subTest(n=n, q=q):
                    data = evaluate_pump_map(c, n, q)
                    self.assertTrue(all(math.isfinite(v) for v in data.values()))
                    self.assertGreaterEqual(data['pump_loss_W'], -1e-10)
        self.assertEqual(set(evaluate_pump_map(c, 0., 0.).values()), {0.})
        self.assertGreater(evaluate_pump_map(c, 0., -1.)['pump_head_Pa'], 0.)
        self.assertLess(evaluate_pump_map(c, 0., 1.)['pump_head_Pa'], 0.)

    def test_stationary_pump_and_positive_backpressure_allow_signed_reverse_flow(self):
        settings = network_fixture()
        c = settings['circuits'][0]
        c.update(initial_speed_ratio=0., final_speed_ratio=0., initial_pump_flow_ratio=0.,
                 initial_outlet_flow_ratio=0., initial_manifold_pressure_Pa=4e5,
                 outlet_pressure_Pa=6e5, final_valve_opening=1.)
        rows, meta = FeedNetworkModel(settings).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        final = rows[-1]['circuits'][0]
        self.assertLess(final['pump_flow_m3_s'], -.0001)
        self.assertLess(final['outlet_flow_m3_s'], -.0001)
        self.assertEqual(final['flow_direction'], '反流')
        self.assertEqual(final['pump_shaft_power_W'], 0.)
        self.assertGreater(final['pump_loss_W'], 0.)
        self.assertEqual(meta['audits']['status'], 'pass')

    def test_mass_and_hydraulic_energy_derivatives_match_independent_boundary_terms(self):
        model = FeedNetworkModel(network_fixture())
        y = model.initial.copy()
        y[0] *= 1.01
        y[1] *= 1.05
        y[2] *= .8
        y[3] = .6
        d, rows = model.evaluate(.2, y)
        r, p, c = rows[0], model.parameters[0], model.circuits[0]
        pressure_rate = d[0]*p['pressure_scale']
        pump_flow_rate = d[1]*c['pump_flow_m3_s']
        outlet_flow_rate = d[2]*c['pump_flow_m3_s']
        storage_derivative = (p['C']*r['manifold_pressure_Pa']*pressure_rate +
                              p['L_in']*r['pump_flow_m3_s']*pump_flow_rate +
                              p['L_out']*r['outlet_flow_m3_s']*outlet_flow_rate)
        boundary_power = c['tank_pressure_Pa']*r['pump_flow_m3_s'] - c['outlet_pressure_Pa']*r['outlet_flow_m3_s']
        dissipative_power = r['inlet_loss_Pa']*r['pump_flow_m3_s'] + (r['outlet_loss_Pa']+r['valve_drop_Pa'])*r['outlet_flow_m3_s']
        self.assertAlmostEqual(storage_derivative, boundary_power+r['pump_head_Pa']*r['pump_flow_m3_s']-dissipative_power, places=8)
        self.assertAlmostEqual(c['rho_kg_m3']*p['C']*pressure_rate, c['rho_kg_m3']*(r['pump_flow_m3_s']-r['outlet_flow_m3_s']), places=12)
        self.assertGreater(abs(storage_derivative), 10.)

    def test_inertia_and_compliance_change_derivatives_by_their_physical_scaling(self):
        settings = network_fixture()
        a = FeedNetworkModel(settings)
        y = a.initial.copy()
        y[0] *= 1.01
        y[1] *= 1.1
        d = a.rhs(0., y)
        altered = deepcopy(settings)
        altered['circuits'][0]['inlet_length_m'] *= 2
        altered['circuits'][0]['volume_m3'] *= 2
        b = FeedNetworkModel(altered)
        db = b.rhs(0., y)
        self.assertGreater(abs(d[0]), .01)
        self.assertGreater(abs(d[1]), .01)
        self.assertAlmostEqual(db[0]*2, d[0], places=10)
        self.assertAlmostEqual(db[1]*2, d[1], places=10)
        self.assertAlmostEqual(db[2], d[2], places=10)

    def test_actuator_matches_linear_ramp_then_first_order_analytic_solution(self):
        settings = network_fixture()
        settings.update(command_time_s=0., command_ramp_s=.02)
        c = settings['circuits'][0]
        c.update(valve_tau_s=.1, valve_rate_per_s=100., final_valve_opening=.9)
        rows, meta = FeedNetworkModel(settings).integrate()
        self.assertTrue(meta['complete'])
        for r in rows:
            t = r['time_s']
            a0, a1, ramp, tau = 1., .9, .02, .1
            if t <= ramp:
                exact = a0+(a1-a0)/ramp*(t-tau*(1-math.exp(-t/tau)))
            else:
                aramp = a1+(a0-a1)*tau/ramp*(1-math.exp(-ramp/tau))
                exact = a1+(aramp-a1)*math.exp(-(t-ramp)/tau)
            self.assertAlmostEqual(r['circuits'][0]['valve_opening'], exact, places=7)

    def test_actuator_rate_limit_and_leakage_have_explicit_effects(self):
        settings = network_fixture()
        c = settings['circuits'][0]
        c.update(final_valve_opening=0., valve_rate_per_s=.2)
        model = FeedNetworkModel(settings)
        d, rows = model.evaluate(.3, model.initial)
        self.assertAlmostEqual(d[3], -.2)
        y = model.initial.copy()
        y[3] = 0.
        row = model.sample(.3, y)['circuits'][0]
        self.assertAlmostEqual(row['effective_area_ratio'], c['leak_fraction'])
        self.assertAlmostEqual(row['valve_drop_Pa'], c['valve_loss_Pa']/c['leak_fraction']**2)
        self.assertGreater(row['pump_flow_m3_s'], 0.)  # no artificial flow clamp

    def test_valve_travel_endpoints_remain_bounded_on_closing_and_reopening(self):
        for initial, final in [(1., 0.), (0., 1.)]:
            settings = network_fixture()
            settings.update(command_time_s=0., command_ramp_s=.001, sample_count=401)
            c = settings['circuits'][0]
            c.update(initial_valve_opening=initial, final_valve_opening=final,
                     initial_speed_ratio=0., final_speed_ratio=0., initial_pump_flow_ratio=0.,
                     initial_outlet_flow_ratio=0., initial_manifold_pressure_Pa=3e5,
                     tank_pressure_Pa=3e5, outlet_pressure_Pa=3e5,
                     valve_tau_s=.01, valve_rate_per_s=100., leak_fraction=1e-5)
            rows, meta = FeedNetworkModel(settings).integrate()
            self.assertTrue(meta['complete'])
            for r in rows:
                a = r['circuits'][0]
                self.assertGreaterEqual(a['valve_opening'], -1e-10)
                self.assertLessEqual(a['valve_opening'], 1+1e-10)
                self.assertGreater(a['effective_area_ratio'], 0.)
            self.assertAlmostEqual(rows[-1]['circuits'][0]['valve_opening'], final, places=8)

    def test_two_circuits_have_independent_inventory_and_energy_audits(self):
        settings = network_fixture()
        second = deepcopy(settings['circuits'][0])
        second.update(id='second', rho_kg_m3=500., final_valve_opening=1.)
        settings['circuits'].append(second)
        original = deepcopy(settings)
        rows, meta = FeedNetworkModel(settings).integrate()
        self.assertEqual(settings, original)
        self.assertTrue(meta['complete'])
        self.assertEqual(meta['audits']['status'], 'pass')
        self.assertLess(meta['audits']['mass_relative_error'], 1e-11)
        self.assertLess(meta['audits']['hydraulic_energy_relative_error'], 1e-7)
        self.assertGreater(abs(rows[-1]['circuits'][0]['manifold_pressure_Pa']-1.15e6), 1000.)
        self.assertAlmostEqual(rows[-1]['circuits'][1]['manifold_pressure_Pa'], 1.15e6, places=6)
        for r in rows:
            self.assertAlmostEqual(r['storage_mass_kg'], sum(c['inventory_kg'] for c in r['circuits']))

    def test_suter_map_anchor_periodicity_and_rejects_unphysical_interpolation(self):
        settings = network_fixture()
        c = settings['circuits'][0]
        c['pump_map'] = dict(kind='suter_table', points=passive_suter_table(),
                            source='Synthetic analytical samples, not measured', max_speed_ratio=1.25, max_flow_ratio=2.)
        model = FeedNetworkModel(settings)
        nominal = evaluate_pump_map(model.circuits[0], 1., 1.)
        self.assertAlmostEqual(nominal['pump_head_Pa'], 1e6, places=8)
        self.assertAlmostEqual(nominal['pump_shaft_power_W'], 1250., places=8)
        for q in [-1e-8, 1e-8]:
            data = evaluate_pump_map(model.circuits[0], 1., q)
            self.assertAlmostEqual(data['pump_head_Pa'], 1.2e6, delta=.1)
        broken = deepcopy(settings)
        broken['circuits'][0]['pump_map']['points'][18]['head'] = 2.
        with self.assertRaisesRegex(ValueError, '负耗散'):
            validate_settings(broken)
        broken = deepcopy(settings)
        broken['circuits'][0]['pump_map']['points'][-1]['head'] += .1
        with self.assertRaisesRegex(ValueError, '闭合'):
            validate_settings(broken)

        # Every vertex below is dissipative, yet linearly interpolating the
        # 0--45 degree segment creates negative dissipation near zero degrees.
        broken = deepcopy(settings)
        points = passive_suter_table()[::9]
        points[0]['torque'] = points[-1]['torque'] = 0.
        broken['circuits'][0]['pump_map']['points'] = points
        for point in points:
            theta = math.radians(point['theta_deg'])
            loss = point['torque']*math.cos(theta)/.8-point['head']*math.sin(theta)
            self.assertGreaterEqual(loss, -1e-14)
        with self.assertRaisesRegex(ValueError, '插值存在负耗散'):
            validate_settings(broken)

    def test_terminal_pressure_event_preserves_exact_end_independent_of_output_grid(self):
        settings = network_fixture()
        c = settings['circuits'][0]
        c.update(initial_speed_ratio=0., final_speed_ratio=0., initial_pump_flow_ratio=0.,
                 initial_outlet_flow_ratio=1., initial_manifold_pressure_Pa=4e5,
                 pressure_floor_Pa=3e5, tank_pressure_Pa=3.1e5, outlet_pressure_Pa=3.1e5,
                 inlet_loss_Pa=0., outlet_loss_Pa=0., valve_loss_Pa=0.)
        endings = []
        for count in [5, 301]:
            settings['sample_count'] = count
            rows, meta = FeedNetworkModel(settings).integrate()
            self.assertFalse(meta['complete'])
            self.assertIn('单相压力下限', meta['termination_reason'])
            self.assertAlmostEqual(rows[-1]['circuits'][0]['manifold_pressure_Pa'], 3e5, places=5)
            self.assertTrue(all(r['circuits'][0]['manifold_pressure_Pa'] >= 3e5-1e-5 for r in rows))
            endings.append(rows[-1]['time_s'])
        self.assertAlmostEqual(endings[0], endings[1], places=8)

    def test_speed_domain_event_does_not_extrapolate_until_final_sample(self):
        settings = network_fixture()
        settings.update(sample_count=3, command_time_s=.1, command_ramp_s=.2)
        settings['circuits'][0].update(final_speed_ratio=1.5)
        rows, meta = FeedNetworkModel(settings).integrate()
        self.assertFalse(meta['complete'])
        self.assertIn('转速', meta['termination_reason'])
        self.assertAlmostEqual(rows[-1]['time_s'], .2, places=9)
        self.assertAlmostEqual(rows[-1]['circuits'][0]['speed_ratio'], 1.25, places=9)

    def test_compression_and_flow_domain_are_real_terminal_events(self):
        for kind in ['compression', 'flow']:
            settings = network_fixture()
            settings['sample_count'] = 3
            c = settings['circuits'][0]
            if kind == 'compression':
                c['max_compression_fraction'] = .0001
            else:
                c['final_speed_ratio'] = 1.2
                c['pump_map']['max_flow_ratio'] = 1.01
            rows, meta = FeedNetworkModel(settings).integrate()
            self.assertFalse(meta['complete'])
            self.assertLess(rows[-1]['time_s'], settings['duration_s'])
            final = rows[-1]['circuits'][0]
            if kind == 'compression':
                self.assertIn('压缩应变', meta['termination_reason'])
                self.assertAlmostEqual(abs(final['compression_fraction']), .0001, places=10)
            else:
                self.assertIn('流量', meta['termination_reason'])
                self.assertAlmostEqual(abs(final['pump_flow_m3_s']/c['pump_flow_m3_s']), 1.01, places=10)

    def test_tolerance_refinement_tracks_physical_states(self):
        settings = network_fixture()
        coarse, _ = FeedNetworkModel(settings | {'rtol': 1e-5}).integrate()
        fine, _ = FeedNetworkModel(settings | {'rtol': 1e-8}).integrate()
        self.assertEqual(len(coarse), len(fine))
        for key in ['manifold_pressure_Pa', 'pump_flow_m3_s', 'outlet_flow_m3_s', 'valve_opening']:
            error = max(abs(a['circuits'][0][key]/b['circuits'][0][key]-1) for a, b in zip(coarse, fine))
            self.assertLess(error, 1e-4, key)

    def test_invalid_inputs_are_rejected_without_silent_clipping(self):
        for key, value in [('leak_fraction', 0.), ('rho_kg_m3', float('nan')),
                           ('initial_valve_opening', 1.1), ('valve_tau_s', 0.),
                           ('initial_speed_ratio', 2.), ('inlet_length_m', -1.)]:
            settings = network_fixture()
            settings['circuits'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                FeedNetworkModel(settings)
        settings = network_fixture()
        settings['circuits'][0]['pump_map']['slope'] = .5
        with self.assertRaisesRegex(ValueError, 'slope'):
            FeedNetworkModel(settings)

    def test_cancellation_preserves_published_points(self):
        token = CancellationToken()
        published = []
        def publish(row):
            published.append(row)
            if len(published) >= 6:
                token.cancel()
        with cancellation_scope(token), self.assertRaises(TaskCancelled):
            FeedNetworkModel(network_fixture()).integrate(publish)
        self.assertGreaterEqual(len(published), 6)
        self.assertLess(published[-1]['time_s'], .4)
        self.assertTrue(all(a['time_s'] < b['time_s'] for a, b in zip(published, published[1:])))


if __name__ == '__main__':
    unittest.main()
