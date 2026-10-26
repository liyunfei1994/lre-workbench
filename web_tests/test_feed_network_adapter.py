"""Snapshot, fluid reference and actual integration contracts of the feed bench."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.feed_network import feed_setup, normalize_request, calculate_feed_network
from web_tests.test_transient_physics import source_fixture as hot_fixture


def source_fixture(cycle='ffsc', kerosene=False):
    source=hot_fixture(cycle)
    source['status']='succeeded'
    inp=source['result']['calculation']['inputs']
    inp['propellant_pair']='lox_kerosene' if kerosene else 'lox_methane'
    inp.update(ox_inlet_temp=70.,fuel_inlet_temp=293. if kerosene else 100.,
               pump_efficiency_ox=.86,pump_efficiency_fuel=.85)
    source['inputs']=deepcopy(inp)
    return source


class FeedAdapterTests(unittest.TestCase):
    def test_real_liquid_reference_and_immutable_nominal_scales(self):
        source=source_fixture(); before=deepcopy(source)
        setup=feed_setup(source)
        self.assertTrue(setup['ready'],setup['issues'])
        request=normalize_request({'source_job_id':source['id'],**setup['defaults']},source)
        for c in request['circuits']:
            self.assertEqual(c['property_evidence']['pressure_Pa'],c['initial_manifold_pressure_Pa'])
            self.assertAlmostEqual(c['pump_flow_m3_s']*c['source_inlet_rho_kg_m3'],c['source_mdot_kg_s'])
        changed=deepcopy(request);changed['circuits'][0]['initial_manifold_pressure_Pa']*=.8
        normalized=normalize_request(changed,source)
        self.assertNotEqual(normalized['circuits'][0]['rho_kg_m3'],request['circuits'][0]['rho_kg_m3'])
        self.assertEqual(normalize_request(normalized,source),normalized)
        self.assertEqual(source,before)

    def test_independent_bench_does_not_pass_failed_engine_constraints(self):
        source=source_fixture();source['result']['assessment']={'status':'fail','checks':[{'key':'crossfeed','status':'fail','value':-4.3}]}
        setup=feed_setup(source)
        self.assertTrue(setup['ready'])
        self.assertEqual(setup['source_constraints']['status'],'fail')
        with patch('webapp.feed_network.science_environment',return_value={'test':True}):
            progress=[]
            result=calculate_feed_network({'source_job_id':source['id'],'duration_s':1.,'sample_count':61},source,
                lambda status,partial:progress.append((status,partial)))
        self.assertTrue(result['complete'],result['termination_reason'])
        self.assertEqual(result['audits']['status'],'pass')
        self.assertEqual(result['source_constraints']['status'],'fail')
        self.assertEqual(len(result['samples']),61)
        self.assertLess(result['audits']['mass_relative_error'],1e-8)
        self.assertLess(result['audits']['hydraulic_energy_relative_error'],1e-6)
        self.assertNotEqual(result['samples'][0]['circuits'][0]['valve_opening'],result['samples'][-1]['circuits'][0]['valve_opening'])
        self.assertGreater(len(progress),2)
        self.assertFalse(progress[0][1]['complete'])
        json.dumps(result,allow_nan=False)

    def test_fluid_models_and_invalid_reference_fail_closed(self):
        for kerosene in (False,True):
            source=source_fixture('gg',kerosene)
            setup=feed_setup(source)
            self.assertTrue(setup['ready'],setup['issues'])
            self.assertEqual(setup['defaults']['circuits'][1]['property_evidence']['fluid'],'n-Dodecane' if kerosene else 'Methane')
        for change in ('unknown','gas','no_eta','pending'):
            source=source_fixture()
            if change=='unknown':source['result']['calculation']['inputs']['propellant_pair']='unknown'
            elif change=='gas':source['result']['calculation']['inputs']['fuel_inlet_temp']=400.
            elif change=='no_eta':source['result']['calculation']['inputs'].pop('pump_efficiency_fuel')
            else:source['status']='running'
            self.assertFalse(feed_setup(source)['ready'],change)

    def test_request_validation_and_readonly_reference(self):
        source=source_fixture()
        for bad in ({'sample_count':20},{'sample_count':25.5},{'sample_count':True},{'sample_count':10**1000},
                    {'rtol':float('nan')},{'command_time_s':2.},{'duration_s':21.},{'inputs':{}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                normalize_request({'source_job_id':source['id'],**bad},source)
        good=normalize_request({'source_job_id':source['id']},source)
        for key in ('rho_kg_m3','bulk_modulus_Pa','pump_head_Pa','pump_flow_m3_s','pressure_floor_Pa'):
            bad=deepcopy(good);bad['circuits'][0][key]*=1.01
            with self.subTest(key=key),self.assertRaises(ValueError):normalize_request(bad,source)
        bad=deepcopy(good);bad['circuits'][0]['pump_map']['max_flow_ratio']=1e300
        with self.assertRaises(ValueError):normalize_request(bad,source)
        bad=deepcopy(good);bad['circuits'][0]['tank_pressure_Pa']=1000
        with self.assertRaises(ValueError):normalize_request(bad,source)

    def test_domain_termination_keeps_event_and_audits(self):
        source=source_fixture()
        request=normalize_request({'source_job_id':source['id'],'duration_s':1.,'sample_count':61},source)
        request['circuits'][0]['final_speed_ratio']=1.2
        request['circuits'][0]['pump_map']['max_speed_ratio']=1.1
        with patch('webapp.feed_network.science_environment',return_value={'test':True}):
            result=calculate_feed_network(request,source)
        self.assertFalse(result['complete'])
        self.assertIn('转速',result['termination_reason'])
        self.assertLess(result['samples'][-1]['time_s'],1.)
        self.assertGreater(len(result['samples']),1)
        self.assertEqual(result['audits']['status'],'pass')


if __name__=='__main__':unittest.main()
