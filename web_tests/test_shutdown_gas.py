from copy import deepcopy
import unittest
from shutdown_gas import ShutdownGasModel,default_settings,validate_settings


def closed(**updates):
    s=default_settings()
    s.update(duration_s=.6,sample_count=31,chemistry_enabled=False,
        initial_temperature_K=500.,wall_temperature_K=500.,wall_UA_W_K=0.,
        inlet_ox_CdA_m2=0.,inlet_fuel_CdA_m2=0.,outlet_CdA_m2=0.,
        ox_open_time_s=.01,fuel_open_time_s=.01,valve_ramp_s=.01,
        ignition_time_s=.03,ignition_duration_s=.011,ignition_energy_J=0.,
        shutdown_time_s=.15,restart_time_s=.35,reignition_time_s=.4,
        reignition_duration_s=.017,reignition_energy_J=0.)
    s.update(updates)
    return s


class ShutdownGasTests(unittest.TestCase):
    def test_default_continuous_history_conserves_inventory_and_elements(self):
        rows,m=ShutdownGasModel(default_settings()).integrate()
        self.assertTrue(m['complete'],m['termination_reason'])
        self.assertEqual(m['audits']['status'],'pass')
        index={r['time_s']:r['circuits'][0] for r in rows}
        self.assertGreater(index[2.]['mass_kg'],index[4.]['mass_kg'])
        self.assertGreater(index[6.]['mass_kg'],index[4.]['mass_kg'])
        self.assertGreater(index[4.]['wall_temperature_K'],index[0.]['wall_temperature_K'])
        self.assertGreater(index[4.]['composition']['H2O'],0.)
        self.assertNotEqual(index[4.]['mass_kg'],index[0.]['mass_kg'])
        self.assertIsNone(m['metrics']['reignition_success'])
        self.assertAlmostEqual(index[6.]['integrated_igniter_energy_J'],2000.,delta=.001)
        snap=m['endpoint_snapshot']['circuits'][0]
        self.assertEqual(snap['mass_kg'],index[6.]['mass_kg'])
        self.assertEqual(snap['internal_energy_J'],index[6.]['internal_energy_J'])
        self.assertTrue(all(e['observed'] for e in m['events']))

    def test_closed_heat_soak_keeps_wall_and_fluid_total_energy(self):
        s=closed(wall_temperature_K=700.,wall_UA_W_K=2.)
        rows,m=ShutdownGasModel(s).integrate()
        self.assertTrue(m['complete'])
        first,last=(r['circuits'][0] for r in (rows[0],rows[-1]))
        self.assertGreater(last['temperature_K'],first['temperature_K'])
        self.assertLess(last['wall_temperature_K'],first['wall_temperature_K'])
        self.assertLess(last['heat_to_wall_W'],0.)
        self.assertAlmostEqual(first['mass_kg'],last['mass_kg'],delta=1e-12)
        self.assertAlmostEqual(last['internal_energy_J']-first['internal_energy_J']+s['wall_heat_capacity_J_K']*(last['wall_temperature_K']-first['wall_temperature_K']),0.,delta=2e-4)

    def test_two_pulses_have_grid_independent_energy_and_events(self):
        endpoints=[]
        for n in [5,101]:
            rows,m=ShutdownGasModel(closed(sample_count=n,ignition_energy_J=11.,reignition_energy_J=17.)).integrate()
            self.assertTrue(m['complete'],m['termination_reason'])
            last=rows[-1]['circuits'][0];endpoints.append(last)
            self.assertAlmostEqual(last['integrated_igniter_energy_J'],28.,delta=1e-5)
            times={r['time_s'] for r in rows}
            self.assertTrue({.03,.041,.4,.417,.15,.16,.35,.36}<=times)
        self.assertAlmostEqual(endpoints[0]['temperature_K'],endpoints[1]['temperature_K'],delta=.001)

    def test_late_event_not_reported_when_protected_stop_happens(self):
        rows,m=ShutdownGasModel(closed(max_temperature_K=600.,ignition_energy_J=1000.)).integrate()
        self.assertFalse(m['complete'])
        self.assertLess(rows[-1]['time_s'],.15)
        self.assertFalse(m['metrics']['restart_event_observed'])
        self.assertIsNone(m['metrics']['restart_residual_mass_kg'])
        self.assertFalse(next(e for e in m['events'] if e['name']=='再次供给指令开始')['observed'])
        self.assertEqual(m['endpoint_snapshot']['time_s'],rows[-1]['time_s'])

    def test_sequence_validation_and_input_immutable(self):
        s=default_settings();before=deepcopy(s);validate_settings(s);self.assertEqual(s,before)
        for changes in [{'shutdown_time_s':.1},{'restart_time_s':2.01},{'reignition_time_s':1.},
                        {'reignition_energy_J':True},{'restart_time_s':float('nan')},
                        {'reignition_duration_s':0.},{'duration_s':4.31},{'snapshot':{}}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_settings(dict(s,**changes))
