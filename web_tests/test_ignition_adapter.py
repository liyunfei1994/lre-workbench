"""Independent gas bench inputs and source records must never be conflated."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from web_tests.test_feed_network_adapter import source_fixture
from webapp.ignition import ignition_setup, normalize_request, calculate_ignition


class IgnitionAdapterTests(unittest.TestCase):
    def test_gas_inputs_are_independent_of_cryogenic_source(self):
        source=source_fixture();before=deepcopy(source)
        setup=ignition_setup(source)
        self.assertTrue(setup['ready'],setup['issues'])
        req=normalize_request({'source_job_id':source['id'],**setup['defaults']},source)
        self.assertEqual(req,normalize_request(req,source))
        self.assertGreaterEqual(req['supply_ox_temperature_K'],300)
        self.assertGreaterEqual(req['supply_fuel_temperature_K'],300)
        self.assertNotEqual(req['initial_pressure_Pa'],source['result']['calculation']['inputs']['pc'])
        self.assertEqual(source,before)

    def test_kerosene_and_snapshot_injection_are_explicitly_rejected(self):
        source=source_fixture('gg',True)
        setup=ignition_setup(source)
        self.assertFalse(setup['ready'])
        self.assertTrue(any('煤油' in issue for issue in setup['issues']))
        with self.assertRaises(ValueError): normalize_request({'source_job_id':source['id']},source)
        source=source_fixture()
        for fragment in ({'chilldown_job_id':'snapshot'},{'initial_liquid_mass_kg':1},
                         {'ignition_success_temperature_K':1000},{'initial_mixture':'liquid_oxygen'},
                         {'chemistry_enabled':'true'},{'duration_s':True},{'sample_count':25.2},
                         {'sample_count':10**1000},{'rtol':float('nan')},{'ignition_energy_J':-1},
                         {'supply_fuel_temperature_K':100},{'ignition_duration_s':0},
                         {'ignition_time_s':10},{'max_pressure_Pa':5000}):
            with self.subTest(fragment=fragment),self.assertRaises(ValueError):
                normalize_request({'source_job_id':source['id'],**fragment},source)

    def test_failed_design_stays_failed_and_result_carries_provenance(self):
        source=source_fixture();source['result']['assessment']={'status':'fail','checks':[{'key':'crossfeed','value':-4.3}]}
        before=deepcopy(source)
        self.assertTrue(ignition_setup(source)['ready'])
        # Closed non-reacting nitrogen provides a small exact-limit integration.
        settings=dict(source_job_id=source['id'],duration_s=.5,sample_count=21,
            chemistry_enabled=False,inlet_ox_CdA_m2=0.,inlet_fuel_CdA_m2=0.,outlet_CdA_m2=0.,
            ignition_energy_J=0.,wall_UA_W_K=0.)
        calls=[]
        with patch('webapp.ignition.science_environment',return_value={'test':True}):
            result=calculate_ignition(settings,source,lambda progress,partial:calls.append((progress,partial)))
        self.assertTrue(result['complete'],result.get('termination_reason'))
        self.assertEqual(result['audits']['status'],'pass')
        self.assertEqual(result['source_constraints']['status'],'fail')
        self.assertEqual(source,before)
        self.assertGreater(len(calls),2)
        self.assertFalse(calls[0][1]['complete'])
        partials=[partial for _,partial in calls[:-1] if partial.get('samples') and partial['samples'][-1]['time_s']>=.3]
        self.assertTrue(partials)
        for partial in partials:
            for event in partial['events']:
                self.assertEqual(event['observed'],event['time_s']<=partial['samples'][-1]['time_s']+1e-12)
        self.assertEqual(result['source']['job_id'],source['id'])
        snapshot=result['endpoint_snapshot']
        self.assertEqual(snapshot['source'],result['source'])
        self.assertEqual(len(snapshot['request_sha256']),64)
        self.assertFalse(snapshot['engine_initialization_supported'])
        self.assertTrue(all(abs(s['mass_kg']-result['samples'][0]['mass_kg'])<1e-10 for s in result['samples']))
        json.dumps(result,allow_nan=False)

    def test_flowing_reacting_progress_is_persistable_before_completion(self):
        source=source_fixture()
        partials=[]
        def persist(progress,result):
            json.dumps(progress,allow_nan=False)
            json.dumps(result,allow_nan=False)
            self.assertFalse(result.get('nonfinite_fields'))
            partials.append(result)
        with patch('webapp.ignition.science_environment',return_value={'test':True}):
            result=calculate_ignition({'source_job_id':source['id']},source,persist)
        self.assertTrue(result['complete'],result.get('termination_reason'))
        self.assertTrue(any(not p['complete'] and p['samples'][-1]['ox_mass_flow_kg_s']>0 for p in partials))


if __name__=='__main__': unittest.main()
