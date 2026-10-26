"""Conservation, event, validity and control-flow tests for the gas-phase bench."""
from copy import deepcopy
import json
import math
import unittest

from ignition import IgnitionModel, default_settings, gas_mass_flux, validate_settings
from task_control import CancellationToken, TaskCancelled, cancellation_scope


def closed(**changes):
    s=default_settings()
    s.update(duration_s=.2,sample_count=21,initial_temperature_K=500.,
        wall_temperature_K=500.,wall_UA_W_K=0.,ambient_UA_W_K=0.,
        inlet_ox_CdA_m2=0.,inlet_fuel_CdA_m2=0.,outlet_CdA_m2=0.,
        ox_open_time_s=0.,fuel_open_time_s=0.,ignition_time_s=.037,
        ignition_duration_s=.011,ignition_energy_J=0.)
    s.update(changes)
    return s


class IgnitionPhysicsTests(unittest.TestCase):
    def test_validation_rejects_nonphysical_and_nonfinite_inputs(self):
        for key,value in [('volume_m3',0.),('chemistry_enabled',1),('initial_mixture','liquid'),
            ('supply_ox_temperature_K',70.),('supply_fuel_temperature_K',100.),
            ('initial_temperature_K',float('nan')),('ignition_energy_J',float('inf')),
            ('max_pressure_Pa',10**1000),('sample_count',True)]:
            with self.subTest(key=key,value=str(value)[:20]):
                s=default_settings();s[key]=value
                with self.assertRaises(ValueError):validate_settings(s)
        s=default_settings();s['extra']=1
        with self.assertRaises(ValueError):validate_settings(s)

    def test_validation_does_not_mutate_supplied_settings(self):
        s=default_settings();copy=deepcopy(s)
        actual=validate_settings(s);actual['volume_m3']=.1
        self.assertEqual(s,copy)

    def test_choked_threshold_continuous_and_lower_back_pressure_saturates(self):
        p,T,gamma,R=1e6,500.,1.35,300.
        pc=p*(2/(gamma+1))**(gamma/(gamma-1))
        sonic,_=gas_mass_flux(p,T,pc,gamma,R)
        above,above_flag=gas_mass_flux(p,T,pc*(1+1e-7),gamma,R)
        below,below_flag=gas_mass_flux(p,T,pc*.2,gamma,R)
        self.assertFalse(above_flag);self.assertTrue(below_flag)
        self.assertAlmostEqual(above/sonic,1.,delta=1e-12)
        self.assertEqual(below,sonic)
        self.assertEqual(gas_mass_flux(p,T,p,gamma,R),(0.,False))
        self.assertEqual(gas_mass_flux(p,T,p*1.01,gamma,R),(0.,False))

    def test_check_valve_equal_pressure_round_trip_removes_only_ulp_noise(self):
        p=1e5
        self.assertEqual(gas_mass_flux(math.nextafter(p,math.inf),300.,p,1.4,300.),(0.,False))
        self.assertGreater(gas_mass_flux(p+1e-6,300.,p,1.4,300.)[0],0.)

    def test_default_reactive_pressure_response_and_conservation(self):
        rows,m=IgnitionModel(default_settings()).integrate()
        self.assertTrue(m['complete'],m['termination_reason'])
        self.assertEqual(m['audits']['status'],'pass')
        self.assertGreater(rows[-1]['pressure_Pa'],rows[0]['pressure_Pa']*5)
        self.assertGreater(rows[-1]['composition']['H2O'],.001)
        self.assertAlmostEqual(rows[-1]['integrated_igniter_energy_J'],1500.,delta=1e-4)
        self.assertIsNone(m['metrics']['ignition_success'])
        self.assertIsNone(m['metrics']['ignition_delay_s'])
        self.assertIsNone(m['metrics']['extinction_detected'])
        self.assertGreater(m['integrator']['accepted_steps'],len(rows))
        self.assertFalse(m['endpoint_snapshot']['full_engine_initialization_supported'])
        for e,error in m['audits']['max_abs_element_error_kg'].items():
            self.assertLess(error,m['audits']['mass_tolerance_kg'],e)

    def test_chemistry_disabled_preserves_reactive_species_despite_external_heat(self):
        s=closed(chemistry_enabled=False,initial_mixture='reactants',ignition_energy_J=50.)
        rows,m=IgnitionModel(s).integrate()
        self.assertTrue(m['complete'],m['termination_reason'])
        self.assertGreater(rows[-1]['temperature_K'],rows[0]['temperature_K'])
        self.assertAlmostEqual(rows[-1]['composition']['CH4'],.2,delta=1e-12)
        self.assertAlmostEqual(rows[-1]['composition']['O2'],.8,delta=1e-12)
        self.assertEqual(rows[-1]['composition']['H2O'],0.)
        self.assertTrue(all(row['chemical_heat_release_W']==0. for row in rows))

    def test_closed_inert_no_igniter_stays_at_initial_state(self):
        rows,m=IgnitionModel(closed()).integrate()
        self.assertTrue(m['complete'])
        self.assertAlmostEqual(rows[-1]['temperature_K'],500.,delta=1e-9)
        self.assertAlmostEqual(rows[-1]['internal_energy_J'],rows[0]['internal_energy_J'],delta=1e-9)
        self.assertEqual(rows[-1]['integrated_igniter_energy_J'],0.)

    def test_output_density_does_not_move_prescribed_pulse_or_its_energy(self):
        results=[]
        for count in (9,201):
            rows,m=IgnitionModel(closed(sample_count=count,ignition_energy_J=37.)).integrate()
            self.assertTrue(m['complete'],m['termination_reason'])
            times={row['time_s'] for row in rows}
            self.assertIn(.037,times);self.assertIn(.048,times)
            for event in m['events']:
                self.assertTrue(event['observed'])
            self.assertAlmostEqual(rows[-1]['integrated_igniter_energy_J'],37.,delta=2e-6)
            results.append(rows[-1])
        self.assertAlmostEqual(results[0]['temperature_K'],results[1]['temperature_K'],delta=2e-4)

    def test_tighter_tolerance_preserves_open_reactive_endpoint(self):
        endpoints=[]
        for tolerance in (1e-7,2e-8):
            s=default_settings();s.update(duration_s=1.,sample_count=31,rtol=tolerance)
            rows,metadata=IgnitionModel(s).integrate()
            self.assertTrue(metadata['complete'],metadata['termination_reason'])
            self.assertEqual(metadata['audits']['status'],'pass')
            endpoints.append(rows[-1])
        for key in ('pressure_Pa','temperature_K','mass_kg'):
            self.assertAlmostEqual(endpoints[0][key]/endpoints[1][key],1.,delta=2e-5)
        self.assertAlmostEqual(endpoints[0]['composition']['H2O'],endpoints[1]['composition']['H2O'],delta=2e-5)

    def test_fast_temperature_limit_caught_between_sparse_output_points(self):
        s=closed(sample_count=2,initial_temperature_K=400.,wall_temperature_K=400.,
            max_temperature_K=600.,ignition_time_s=.05,ignition_duration_s=.0001,
            ignition_energy_J=1000.)
        rows,m=IgnitionModel(s).integrate()
        self.assertFalse(m['complete'])
        self.assertIn('温度',m['termination_reason'])
        self.assertGreater(rows[-1]['time_s'],.05)
        self.assertLess(rows[-1]['time_s'],.0501)
        self.assertLessEqual(rows[-1]['temperature_K'],600.+1e-6)
        self.assertGreater(m['termination_observation']['temperature_K'],600.)
        self.assertEqual(m['endpoint_snapshot']['time_s'],rows[-1]['time_s'])

    def test_accepted_valve_motion_respects_rate_and_does_not_jump(self):
        s=closed(initial_ox_opening=0.,final_ox_opening=1.,ox_open_time_s=.02,
            valve_ramp_s=.01,valve_tau_s=.02,valve_rate_per_s=3.)
        rows,m=IgnitionModel(s).integrate()
        self.assertTrue(m['complete'])
        self.assertGreater(rows[-1]['ox_opening'],0.)
        self.assertLess(rows[-1]['ox_opening'],1.)
        for a,b in zip(rows,rows[1:]):
            self.assertGreaterEqual(b['ox_opening'],-1e-10)
            self.assertLessEqual(b['ox_opening'],1.+1e-10)
            self.assertLessEqual(b['ox_opening']-a['ox_opening'],3*(b['time_s']-a['time_s'])+2e-7)

    def test_no_reverse_gas_injection_above_source_pressures(self):
        s=closed(initial_pressure_Pa=2e6,inlet_ox_CdA_m2=1e-7,inlet_fuel_CdA_m2=1e-7,
            initial_ox_opening=1.,initial_fuel_opening=1.)
        rows,m=IgnitionModel(s).integrate()
        self.assertTrue(m['complete'])
        self.assertTrue(all(row['ox_mass_flow_kg_s']==0. and row['fuel_mass_flow_kg_s']==0. for row in rows))
        self.assertAlmostEqual(rows[-1]['mass_kg'],rows[0]['mass_kg'],delta=1e-12)

    def test_progress_and_final_are_strict_json_not_numpy_scalars(self):
        s=default_settings();s.update(duration_s=.1,sample_count=5,ignition_time_s=.03,
            ignition_duration_s=.01,ignition_energy_J=0.,ox_open_time_s=0.,fuel_open_time_s=0.)
        seen=[]
        def publish(rows,metadata):
            json.dumps(dict(samples=rows,metadata=metadata),allow_nan=False)
            seen.append(len(rows))
        rows,metadata=IgnitionModel(s).integrate(publish)
        self.assertTrue(metadata['complete'])
        self.assertGreater(len(seen),1)
        json.dumps(dict(samples=rows,metadata=metadata),allow_nan=False)

    def test_cancel_preserves_published_valid_initial_sample(self):
        token=CancellationToken();published=[]
        def publish(rows,metadata):
            published.extend(deepcopy(rows));token.cancel()
        with cancellation_scope(token):
            with self.assertRaises(TaskCancelled):IgnitionModel(closed()).integrate(publish)
        self.assertEqual(len(published),1)
        self.assertEqual(published[0]['time_s'],0.)

    def test_separate_fluid_and_wall_errors_cannot_cancel_in_audit(self):
        model=IgnitionModel(closed())
        original=model.sample
        def corrupt_display(t):
            row=original(t)
            row['fluid_energy_balance_error_J']+=1.
            row['wall_energy_balance_error_J']-=1.
            return row
        model.sample=corrupt_display
        _,metadata=model.integrate()
        self.assertEqual(metadata['audits']['status'],'review')
        self.assertLess(metadata['audits']['max_abs_total_energy_error_J'],1e-8)
        self.assertGreater(metadata['audits']['max_abs_fluid_energy_error_J'],.99)
        self.assertGreater(metadata['audits']['max_abs_wall_energy_error_J'],.99)


if __name__=='__main__':
    unittest.main()
