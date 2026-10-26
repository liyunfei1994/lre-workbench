"""Mechanical and conservation checks for the independent shutdown rotor unit."""
import copy
import math
import unittest

import numpy as np

from shutdown_rotor import ShutdownRotorModel, default_settings, validate_settings
from feed_network import evaluate_pump_map
from task_control import CancellationToken, TaskCancelled, cancellation_scope


class ShutdownRotorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=ShutdownRotorModel(default_settings())
        cls.samples,cls.metadata=cls.model.integrate()

    def test_default_sequence_is_complete_continuous_and_conservative(self):
        meta=self.metadata
        self.assertTrue(meta['complete'])
        self.assertEqual(meta['audits']['status'],'pass')
        self.assertTrue(all(e['observed'] for e in meta['events']))
        self.assertLess(meta['audits']['max_abs_mass_error_kg'],1e-12)
        self.assertLess(meta['audits']['max_abs_total_energy_error_J'],1e-3)
        for index in range(2):
            initial=self.samples[0]['circuits'][index]
            coast=next(r['circuits'][index] for r in self.samples if abs(r['time_s']-3.)<1e-12)
            final=self.samples[-1]['circuits'][index]
            self.assertLess(coast['speed_ratio'],.9)
            self.assertGreater(final['speed_ratio'],coast['speed_ratio'])
            self.assertLess(coast['valve_opening'],1e-8)
            self.assertGreater(coast['inventory_kg'],.99*initial['inventory_kg'])
            self.assertGreater(coast['effective_area_ratio'],0.)
            self.assertLess(coast['drive_torque_Nm'],1e-12)
        self.assertFalse(meta['endpoint_snapshot']['full_engine_initialization_supported'])
        for c in meta['metrics']['circuits']:
            self.assertTrue(c['restart_event_observed'])
            self.assertIsNone(c['restart_success'])
            self.assertLess(c['minimum_sampled_speed_rpm'],c['initial_speed_rpm'])

    def test_exact_initial_operating_equilibrium(self):
        # The pressures, hydraulic losses and torque are independently chosen
        # to balance at n=q=a=1; no fitted integrator result defines this test.
        for row in self.samples:
            if row['time_s']>1.: break
            for c in row['circuits']:
                self.assertAlmostEqual(c['manifold_pressure_Pa'],1.175e6,places=5)
                self.assertAlmostEqual(c['speed_ratio'],1.,places=10)
                self.assertAlmostEqual(c['valve_opening'],1.,places=10)

    def test_instantaneous_energy_identity_in_all_flow_rotation_signs(self):
        m=self.model
        for n,qi,qo in [(1.,.8,.6),(.2,-.5,-.3),(-.4,.6,.2),(-.5,-.8,-.7),(0.,-.2,.1)]:
            y=m.initial.copy()
            for i in range(2):
                j=i*m.STATES_PER_CIRCUIT
                y[j+1:j+5]=[qi,qo,.4,n]
            derivative,rows=m.evaluate(1.08,y)
            for i,(c,p,row) in enumerate(zip(m.circuits,m.parameters,rows)):
                j=i*m.STATES_PER_CIRCUIT
                # Differentiate E=1/2 Cp²+1/2 LQ²+1/2 Jω² directly,
                # independently of every integrated energy ledger.
                actual=p['C']*row['manifold_pressure_Pa']*p['pressure_scale']*derivative[j]
                actual+=p['L_in']*row['pump_flow_m3_s']*c['pump_flow_m3_s']*derivative[j+1]
                actual+=p['L_out']*row['outlet_flow_m3_s']*c['pump_flow_m3_s']*derivative[j+2]
                actual+=c['rotor_inertia_kg_m2']*row['omega_rad_s']*p['omega0']*derivative[j+4]
                expected=row['boundary_power_W']+row['drive_power_W']-row['hydraulic_dissipation_W']-row['pump_loss_W']-row['friction_power_W']
                self.assertAlmostEqual(actual,expected,delta=1e-10*max(1.,abs(expected)))
                self.assertGreaterEqual(row['friction_power_W'],0.)
                self.assertGreaterEqual(row['pump_loss_W'],-1e-10)

    def test_independent_angular_impulse_quadrature(self):
        # Angular impulse is not one of the solver's eight ledgers; this
        # validates the torque/sign/unit interface from exported samples.
        times=np.array([r['time_s'] for r in self.samples])
        for i,c in enumerate(self.model.circuits):
            rows=[r['circuits'][i] for r in self.samples]
            torque=np.array([r['drive_torque_Nm']-r['pump_torque_Nm']-r['friction_torque_Nm'] for r in rows])
            expected=np.trapezoid(torque,times)
            actual=c['rotor_inertia_kg_m2']*(rows[-1]['omega_rad_s']-rows[0]['omega_rad_s'])
            self.assertAlmostEqual(actual,expected,delta=.002)

    def test_backpressure_driven_reverse_flow_is_not_clamped(self):
        s=default_settings()
        s.update(duration_s=.5,shutdown_time_s=.05,restart_time_s=.3,command_ramp_s=.02,sample_count=101)
        s['circuits']=s['circuits'][:1]
        s['circuits'][0].update(initial_speed_ratio=.3,initial_pump_flow_ratio=-.2,
            initial_outlet_flow_ratio=-.2,outlet_pressure_Pa=1.5e6,
            initial_manifold_pressure_Pa=1.2e6,drive_torque_scale=0.)
        rows,meta=ShutdownRotorModel(s).integrate()
        self.assertTrue(meta['complete'])
        self.assertEqual(meta['audits']['status'],'pass')
        self.assertTrue(all(r['circuits'][0]['pump_flow_m3_s']<0 for r in rows))
        self.assertTrue(all(r['circuits'][0]['outlet_flow_m3_s']<0 for r in rows))
        self.assertTrue(any(r['circuits'][0]['pump_shaft_power_W']<0 for r in rows))
        self.assertGreater(rows[-1]['circuits'][0]['speed_ratio'],rows[0]['circuits'][0]['speed_ratio'])
        self.assertTrue(meta['metrics']['circuits'][0]['sampled_pump_reverse_flow_observed'])
        self.assertTrue(meta['metrics']['circuits'][0]['sampled_outlet_reverse_flow_observed'])

    def test_zero_speed_and_negative_rotation_are_finite(self):
        for n in [0.,-1e-12,-.5]:
            state=self.model.initial.copy()
            state[4]=n
            derivative,rows=self.model.evaluate(2.,state)
            self.assertTrue(np.all(np.isfinite(derivative)))
            self.assertEqual(rows[0]['speed_ratio'],n)
            self.assertAlmostEqual(rows[0]['omega_rad_s'],n*6000*math.pi/30)
            if n< -1e-10: self.assertEqual(rows[0]['rotation_direction'],'反转')

    def test_periodic_suter_map_supported_and_missing_data_rejected(self):
        s=default_settings();s['circuits']=s['circuits'][:1]
        c=s['circuits'][0];points=[]
        torque0=c['pump_head_Pa']*c['pump_flow_m3_s']/(c['pump_efficiency']*c['pump_speed_rpm']*math.pi/30)
        for angle in range(0,361,5):
            state=evaluate_pump_map(c,math.cos(math.radians(angle)),math.sin(math.radians(angle)))
            points.append(dict(theta_deg=angle,head=state['pump_head_Pa']/c['pump_head_Pa'],torque=state['pump_torque_Nm']/torque0))
        c['pump_map']=dict(kind='suter_table',points=points,source='synthetic unit test, not measured',max_speed_ratio=2.5,max_flow_ratio=3.)
        model=ShutdownRotorModel(s)
        state=model.sample(0.,model.initial)['circuits'][0]
        self.assertAlmostEqual(state['pump_head_Pa'],1e6,places=6)
        self.assertAlmostEqual(state['pump_torque_Nm'],torque0,places=9)
        c['pump_map']['points']=None
        with self.assertRaises(ValueError): validate_settings(s)

    def test_pump_map_domain_terminates_with_valid_partial_state(self):
        s=default_settings();s['circuits']=s['circuits'][:1]
        c=s['circuits'][0];c['drive_torque_scale']=4.;c['pump_map']['max_speed_ratio']=1.01
        rows,meta=ShutdownRotorModel(s).integrate()
        self.assertFalse(meta['complete'])
        self.assertIn('转速',meta['termination_reason'])
        self.assertLess(rows[-1]['time_s'],1.)
        self.assertAlmostEqual(rows[-1]['circuits'][0]['speed_ratio'],1.01,places=8)
        self.assertEqual(meta['endpoint_snapshot']['time_s'],rows[-1]['time_s'])
        self.assertFalse(any(e['observed'] for e in meta['events']))
        self.assertIsNone(meta['metrics']['circuits'][0]['last_sample_before_restart_speed_rpm'])
        self.assertFalse(meta['metrics']['circuits'][0]['restart_event_observed'])

    def test_event_endpoints_present_with_coarse_sample_grid(self):
        s=default_settings();s['sample_count']=3;s['circuits']=s['circuits'][:1]
        rows,meta=ShutdownRotorModel(s).integrate()
        self.assertTrue(meta['complete'])
        times=[r['time_s'] for r in rows]
        for e in meta['events']:
            self.assertTrue(any(abs(t-e['time_s'])<1e-12 for t in times))
        self.assertLess(abs(rows[-1]['circuits'][0]['speed_ratio']-self.samples[-1]['circuits'][0]['speed_ratio']),1e-6)

    def test_tolerance_tightening_and_output_grid_independence(self):
        results=[]
        for tolerance,count in [(1e-6,31),(1e-6,1001),(1e-8,1001)]:
            s=default_settings();s.update(rtol=tolerance,sample_count=count)
            rows,meta=ShutdownRotorModel(s).integrate()
            self.assertTrue(meta['complete'])
            self.assertEqual(meta['audits']['status'],'pass')
            results.append((rows,meta))
        coarse,dense,tight=results
        # Output interpolation has no feedback into the accepted ODE steps.
        self.assertEqual(coarse[0][-1],dense[0][-1])
        self.assertEqual(coarse[1]['audits'],dense[1]['audits'])
        for ordinary,refined in zip(dense[0],tight[0]):
            self.assertAlmostEqual(ordinary['time_s'],refined['time_s'],places=12)
            for left,right in zip(ordinary['circuits'],refined['circuits']):
                self.assertAlmostEqual(left['speed_rpm'],right['speed_rpm'],delta=.002)
                self.assertAlmostEqual(left['manifold_pressure_Pa'],right['manifold_pressure_Pa'],delta=2.)
        self.assertLess(tight[1]['audits']['max_abs_total_energy_error_J'],dense[1]['audits']['max_abs_total_energy_error_J']/10)

    def test_cancellation_escapes_physics_and_preserves_progress(self):
        token=CancellationToken();rows=[]
        def progress(row):
            rows.append(row)
            if row['time_s']>=.1: token.cancel()
        with self.assertRaises(TaskCancelled):
            with cancellation_scope(token):
                ShutdownRotorModel(default_settings()).integrate(progress)
        self.assertGreater(len(rows),1)
        self.assertLess(rows[-1]['time_s'],5.)

    def test_strict_inputs_and_physical_ranges(self):
        mutations=[lambda s:s.update(unknown=True),
            lambda s:s.update(restart_time_s=1.1),lambda s:s.update(sample_count=True),
            lambda s:s.update(duration_s=10**1000),
            lambda s:s['circuits'][0].update(rotor_inertia_kg_m2=0.),
            lambda s:s['circuits'][0].update(rotor_linear_friction_Nm_s=-1.),
            lambda s:s['circuits'][0].update(initial_outlet_flow_ratio=1e300),
            lambda s:s['circuits'][0].update(leak_fraction=0.),
            lambda s:s['circuits'][0]['pump_map'].update(slope=1.),
            lambda s:s['circuits'][0]['pump_map'].update(unknown=1)]
        for mutate in mutations:
            s=default_settings();mutate(s)
            with self.subTest(settings=s),self.assertRaises(ValueError): validate_settings(s)

    def test_settings_and_output_snapshots_are_independent(self):
        source=default_settings();original=copy.deepcopy(source)
        model=ShutdownRotorModel(source)
        self.assertEqual(source,original)
        source['circuits'][0]['pump_map']['slope']=999
        self.assertEqual(model.settings['circuits'][0]['pump_map']['slope'],.1)
        self.assertIn('不是残余液体排空',model.model_description['inventory_model'])


if __name__=='__main__': unittest.main()
