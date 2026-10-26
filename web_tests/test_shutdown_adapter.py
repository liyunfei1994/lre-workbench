from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from web_tests.test_feed_network_adapter import source_fixture
from webapp.shutdown import shutdown_setup,normalize_request,calculate_shutdown,CHARTS


class ShutdownAdapterTests(unittest.TestCase):
    def test_independent_defaults_do_not_replace_or_repair_design(self):
        source=source_fixture();source['result']['assessment']['status']='fail';original=deepcopy(source)
        setup=shutdown_setup(source)
        self.assertTrue(setup['ready'])
        self.assertEqual(len(setup['modes']),3)
        for mode in setup['modes']:
            self.assertTrue(mode['ready'])
            req=normalize_request(dict(source_job_id=source['id'],mode=mode['value'],settings=mode['defaults']),source)
            self.assertEqual(normalize_request(req,source),req)
            self.assertNotEqual(req['settings'].get('initial_pressure_Pa'),source['result']['calculation']['inputs']['pc'])
        self.assertEqual(original,source)

    def test_kerosene_never_runs_methane_reaction_substitute(self):
        source=source_fixture('gg',True)
        modes={m['value']:m for m in shutdown_setup(source)['modes']}
        self.assertFalse(modes['gas']['ready']);self.assertTrue(modes['inventory']['ready'])
        self.assertTrue(modes['rotor']['ready'])
        with self.assertRaises(ValueError):normalize_request(dict(source_job_id=source['id'],mode='gas'),source)

    def test_all_modes_keep_serializable_progress_events_and_frozen_charts(self):
        source=source_fixture();original=deepcopy(source)
        for mode in CHARTS:
            parts=[]
            def persist(p,r):
                json.dumps(p,allow_nan=False);json.dumps(r,allow_nan=False);parts.append(r)
            with self.subTest(mode=mode),patch('webapp.shutdown.science_environment',return_value={'test':True}):
                result=calculate_shutdown(dict(source_job_id=source['id'],mode=mode),source,persist)
            self.assertTrue(result['complete'],result['reason']);self.assertEqual(result['audits']['status'],'pass')
            self.assertFalse(result['nonfinite_fields'])
            self.assertGreater(len(parts),2)
            self.assertFalse(parts[0]['complete']);self.assertTrue(parts[-1]['complete'])
            self.assertEqual(result['charts'],CHARTS[mode])
            fields=set(result['samples'][-1]['circuits'][0])
            self.assertTrue({s['key'] for c in result['charts'] for s in c['series']}<=fields)
            for part in parts:
                for e in part['events']:self.assertEqual(e['observed'],e['time_s']<=part['samples'][-1]['time_s']+1e-12)
            self.assertTrue(result['events'])
            self.assertEqual(result['endpoint_snapshot']['source'],result['source'])
            self.assertEqual(result['endpoint_snapshot']['time_s'],result['samples'][-1]['time_s'])
            self.assertFalse(result['endpoint_snapshot']['engine_initialization_supported'])
            self.assertEqual(len(result['endpoint_snapshot']['request_sha256']),64)
        self.assertEqual(source,original)

    def test_invalid_modes_snapshots_nonfinite_and_overlapping_events_rejected(self):
        source=source_fixture()
        for extra in [{'mode':'engine'},{'mode':{}},{'mode':'gas','endpoint_snapshot':{}},
                      {'settings':{'duration_s':True}},{'settings':{'initial_temperature_K':70}},
                      {'settings':{'restart_time_s':float('inf')}}, {'settings':{'sample_count':10**1000}},
                      {'settings':{'reignition_success_temperature':1000}},
                      {'mode':'inventory','settings':{'restart_time_s':3.}},
                      {'mode':'rotor','settings':{'restart_time_s':6.}}]:
            with self.subTest(extra=str(extra)[:100]),self.assertRaises((ValueError,TypeError)):
                normalize_request(dict(source_job_id=source['id'],**extra),source)
