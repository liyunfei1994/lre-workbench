"""Thermal study provenance, independent initial conditions, and snapshot contracts."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from web_tests.test_feed_network_adapter import source_fixture
from webapp.chilldown import chilldown_setup, normalize_request, calculate_chilldown


class ChilldownAdapterTests(unittest.TestCase):
    def test_defaults_are_independent_and_provenance_immutable(self):
        source=source_fixture(); before=deepcopy(source)
        setup=chilldown_setup(source)
        self.assertTrue(setup['ready'],setup['issues'])
        req=normalize_request({'source_job_id':source['id'],**setup['defaults']},source)
        self.assertEqual(req,normalize_request(req,source))
        self.assertEqual(source,before)
        self.assertEqual(req['circuits'][0]['fluid'],'Oxygen')
        self.assertEqual(req['circuits'][1]['fluid'],'Methane')
        self.assertEqual(req['circuits'][0]['supply_temperature_K'],source['result']['calculation']['inputs']['ox_inlet_temp'])
        self.assertNotEqual(req['circuits'][0]['initial_temperature_K'],req['circuits'][0]['supply_temperature_K'])

    def test_historical_failure_is_retained_and_not_reclassified(self):
        source=source_fixture(); source['result']['assessment']={'status':'fail','checks':[{'key':'crossfeed','value':-4.3}]}
        setup=chilldown_setup(source)
        self.assertTrue(setup['ready'],setup['issues'])
        self.assertEqual(setup['source_constraints']['status'],'fail')
        c=deepcopy(setup['defaults']['circuits'][0]);c.update(inlet_CdA_m2=0.,outlet_CdA_m2=0.,wall_UA_W_K=0.)
        with patch('webapp.chilldown.science_environment',return_value={'test':True}):
            calls=[]
            result=calculate_chilldown({'source_job_id':source['id'],'duration_s':1.,'sample_count':21,'circuits':[c]},source,
                lambda progress,partial:calls.append((progress,partial)))
        self.assertTrue(result['complete'],result.get('termination_reason'))
        self.assertEqual(result['source_constraints']['status'],'fail')
        self.assertEqual(len(result['samples']),21)
        self.assertEqual(result['audits']['status'],'pass')
        self.assertGreater(len(calls),2)
        self.assertFalse(calls[0][1]['complete'])
        snapshot=result['endpoint_snapshot']
        self.assertEqual(snapshot['source']['job_id'],source['id'])
        self.assertEqual(len(snapshot['request_sha256']),64)
        self.assertFalse(snapshot['engine_initialization_supported'])
        self.assertFalse(result['readiness']['ignition_ready'])
        json.dumps(result,allow_nan=False)

    def test_request_rejects_invalid_species_and_ambiguous_contracts(self):
        source=source_fixture(); base=chilldown_setup(source)['defaults']
        for fragment in ({'duration_s':True},{'duration_s':float('nan')},{'sample_count':25.2},
                         {'sample_count':10**1000},{'rtol':1.},{'initial_gas':'Nitrogen'},{'circuits':[]},
                         {'command_time_s':20.}, {'circuits':[{'id':'missing'}]},
                         {'circuits':[{'id':'ox','fluid':'Nitrogen'}]},
                         {'circuits':[{'id':'ox','initial_mode':'PTQ'}]},
                         {'circuits':[{'id':'ox'},{'id':'ox'}]},
                         {'circuits':[{'id':'ox','inlet_CdA_m2':-1.}]}):
            with self.subTest(fragment=fragment),self.assertRaises(ValueError):
                normalize_request({'source_job_id':source['id'],**fragment},source)
        with self.assertRaises(ValueError): normalize_request({'source_job_id':'wrong',**base},source)

    def test_pq_initialization_and_kerosene_proxy(self):
        source=source_fixture()
        request=normalize_request({'source_job_id':source['id'],'circuits':[{'id':'ox','initial_mode':'PQ','initial_quality':.25}]},source)
        from chilldown import ChilldownModel
        model=ChilldownModel(request); sample=model.sample(0.,model.initial)['circuits'][0]
        self.assertAlmostEqual(sample['quality'],.25,places=6)
        self.assertGreater(sample['void_fraction'],sample['quality'])
        self.assertAlmostEqual(sample['liquid_mass_kg']+sample['vapor_mass_kg'],sample['mass_kg'],places=9)
        proxy=chilldown_setup(source_fixture('gg',True))
        self.assertTrue(proxy['ready'],proxy['issues'])
        self.assertEqual(proxy['defaults']['circuits'][1]['fluid'],'n-Dodecane')
        self.assertTrue(any('RP-1' in note for note in proxy['limitations']))


if __name__=='__main__': unittest.main()
