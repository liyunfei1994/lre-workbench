from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest

from webapp.nozzle_design import nozzle_design_view, nozzle_ambient_view
from lre_workbench.components.nozzles.performance import ambient_characteristic
from lre_workbench.domain.models import G0
from web_tests.test_nozzle_geometry import fixture as geometry_fixture


def fixture():
    result = geometry_fixture()
    cal = result['calculation']
    at = math.pi*.1**2
    ae, pc, mdot, ve, pe, pa = at*16, 10e6, 200., 2500., 50000., 101325.
    fv = mdot*ve + pe*ae
    f = fv-pa*ae
    cal['summary'] = dict(chamber_pressure_Pa=pc, mdot_total_kg_s=mdot, thrust_N=f)
    cal['components']['nozzle']['outputs'].update(Ae_m2=ae, thrust_N=f, thrust_vac_N=fv,
        c_star_ms=pc*at/mdot, Cf=f/(pc*at), Cf_vac=fv/(pc*at),
        Isp_s=f/(mdot*G0), Isp_vac_s=fv/(mdot*G0), Ve_ms=ve, p_exit_Pa=pe,
        T_exit_K=1200., mach_exit=3., p_amb_Pa=pa, flow_model=0.)
    cal['components']['main_chamber'] = {'outputs':dict(cc_diameter_m=.3,cc_length_m=.3)}
    cal['components']['cooling_channel']['outputs'] = {'A_throat_m2':at}
    return result


class NozzleDesignTests(unittest.TestCase):
    def test_metrics_use_one_saved_result_and_do_not_mutate(self):
        result = fixture()
        result['submitted_inputs'] = {'pc':1, 'thrust':1, 'expansion_ratio':1000}
        original = deepcopy(result)
        view = nozzle_design_view(result, 'saved-job')
        self.assertTrue(view['available'], view)
        self.assertEqual(view['source']['record_id'], 'saved-job')
        self.assertEqual(result, original)
        self.assertTrue(all(c['status']=='pass' for c in view['checks']), view['checks'])
        m = {m['key']:m for m in view['metrics']}
        self.assertEqual(m['pc_Pa']['value'],10e6)
        self.assertLess(m['pressure_thrust_N']['value'],0)
        self.assertAlmostEqual(m['c_star_ms']['value']*m['Cf']['value'],m['Isp_s']['value']*G0)
        self.assertEqual(view['status'],'review')
        self.assertFalse(view['flow_profile']['available'])
        self.assertTrue(view['ambient']['available'])
        json.dumps(view, allow_nan=False)

    def test_fixed_ambient_changes_only_pressure_thrust(self):
        result=fixture()
        before=deepcopy(result)
        curve=nozzle_ambient_view(result,dict(maximum_pressure_Pa=200000.,point_count=5),'record')
        pts=curve['points']
        out=result['calculation']['components']['nozzle']['outputs']
        self.assertAlmostEqual(pts[0]['thrust_N'],out['thrust_vac_N'])
        self.assertEqual(pts[0]['screening'],'vacuum')
        self.assertIsNone(pts[0]['pe_pa_ratio'])
        self.assertEqual(pts[1]['regime'],'matched')
        self.assertEqual(pts[-1]['screening'],'flag')
        self.assertAlmostEqual(pts[-1]['thrust_N']-pts[0]['thrust_N'],-200000*out['Ae_m2'])
        self.assertEqual(result,before)
        self.assertEqual(curve['source']['record_id'],'record')

    def test_inconsistent_performance_blocks_curve_instead_of_repairing(self):
        for key in ('thrust_N','Cf','Cf_vac','c_star_ms','Ae_m2','thrust_vac_N','Isp_s','Isp_vac_s'):
            result=fixture()
            result['calculation']['components']['nozzle']['outputs'][key]*=1.1
            with self.subTest(key=key):
                view=nozzle_design_view(result)
                self.assertEqual(view['status'],'inconsistent',view)
                self.assertFalse(view['ambient']['available'])
                with self.assertRaises(ValueError):
                    nozzle_ambient_view(result,dict(maximum_pressure_Pa=101325.,point_count=5))

    def test_missing_source_values_remain_missing(self):
        result=fixture()
        result['calculation']['summary'].pop('mdot_total_kg_s')
        view=nozzle_design_view(result)
        m={m['key']:m for m in view['metrics']}
        self.assertIsNone(m['c_star_ms']['value'])
        self.assertIsNone(m['momentum_thrust_N']['value'])
        self.assertFalse(view['ambient']['available'])
        self.assertEqual(m['mdot_kg_s']['status'],'missing')

    def test_vacuum_has_no_fake_finite_pressure_ratio(self):
        result=fixture()
        out=result['calculation']['components']['nozzle']['outputs']
        out['p_amb_Pa']=0
        out['thrust_N']=out['thrust_vac_N'];out['Isp_s']=out['Isp_vac_s'];out['Cf']=out['Cf_vac']
        result['calculation']['summary']['thrust_N']=out['thrust_N']
        view=nozzle_design_view(result)
        self.assertTrue(view['ambient']['available'])
        vals={m['key']:m['value'] for m in view['metrics']}
        self.assertIsNone(vals['npr']);self.assertIsNone(vals['pe_pa_ratio'])
        json.dumps(view, allow_nan=False)

    def test_flow_uses_saved_nodes_and_checks_endpoint(self):
        result=fixture()
        g=result['calculation']['components']['cooling_channel']['profile']['geometry']
        out=result['calculation']['components']['nozzle']['outputs']
        xe=g['full_nodes'][-1]['x_m']
        nodes=[dict(x_m=x,area_ratio=ar,M=m,p_Pa=p,T_K=t,velocity_ms=v)
               for x,ar,m,p,t,v in [(0,2.25,.2,9e6,3000,200),(xe/2,2,2,1e6,2000,1500),(xe,16,3,50000,1200,2500)]]
        result['calculation']['components']['nozzle']['profile']=dict(model_id='heated_frozen_quasi1d_v1',nodes=nodes)
        v=nozzle_design_view(result)
        self.assertEqual(v['flow_profile']['nodes'],nodes)
        self.assertEqual(v['losses'][0]['status'],'included')
        nodes[-1]['p_Pa']*=2
        v=nozzle_design_view(result)
        self.assertEqual(v['status'],'inconsistent')
        self.assertFalse(v['ambient']['available'])
        nodes[1]['x_m']=0
        v=nozzle_design_view(result)
        self.assertFalse(v['flow_profile']['available'])
        self.assertEqual(v['status'],'inconsistent')
        self.assertFalse(v['ambient']['available'])

    def test_nozzle_thermal_peak_excludes_chamber_and_zero_length_manifold(self):
        result=fixture()
        geom=nozzle_design_view(result)['geometry']['dimensions']
        xt=geom['throat_x_m']
        result['calculation']['components']['cooling_channel']['profile']['cells']=[
            dict(x_m=0,kind='channel',q_W_m2=999,T_wall_hot_K=9999),
            dict(x_m=xt+.1,kind='channel',q_W_m2=100,T_wall_hot_K=800,T_wall_cold_K=700,material={'within_range':False}),
            dict(x_m=xt+.2,kind='inlet_manifold',q_W_m2=999,T_wall_hot_K=9999)]
        v=nozzle_design_view(result)['thermal']
        m={m['key']:m['value'] for m in v['metrics']}
        self.assertEqual(m['nozzle_peak_wall_temperature_K'],800)
        self.assertEqual(m['nozzle_peak_heat_flux_W_m2'],100)
        self.assertEqual(v['material_outside_range_cells'],1)
        result['calculation']['components']['cooling_channel']['profile']['cells'].append(
            dict(x_m=1000,kind='channel',q_W_m2=1e12,T_wall_hot_K=9999))
        v=nozzle_design_view(result)['thermal']
        self.assertEqual(v['status'],'invalid')
        self.assertFalse(v['nodes'])
        self.assertIsNone(next(m['value'] for m in v['metrics'] if m['key']=='nozzle_peak_wall_temperature_K'))

    def test_only_absent_exit_area_can_be_derived(self):
        for value in (-1,0,None,float('nan')):
            r=fixture();r['calculation']['components']['nozzle']['outputs']['Ae_m2']=value
            v=nozzle_design_view(r)
            self.assertFalse(v['ambient']['available'])
            self.assertEqual(v['status'],'inconsistent')
        r=fixture();r['calculation']['components']['nozzle']['outputs'].pop('Ae_m2')
        v=nozzle_design_view(r)
        self.assertTrue(v['ambient']['available'])
        self.assertEqual(next(m['status'] for m in v['metrics'] if m['key']=='Ae_m2'),'derived')

    def test_bad_data_and_foreign_cycles_fail_without_crashing(self):
        for path in [('calculation',),('calculation','components'),('calculation','components','nozzle','outputs')]:
            r=fixture();d=r
            for key in path[:-1]:d=d[key]
            d[path[-1]]=[]
            self.assertFalse(nozzle_design_view(r)['available'])
        r=fixture();r['calculation']['inputs']['cycle_type']='gg'
        self.assertFalse(nozzle_design_view(r)['available'])
        self.assertFalse(nozzle_design_view(None)['available'])

    def test_invalid_requests_and_nonfinite_values_are_rejected(self):
        for request in [dict(maximum_pressure_Pa=-1,point_count=5),dict(maximum_pressure_Pa=float('nan'),point_count=5),
                        dict(maximum_pressure_Pa=3e6,point_count=5),dict(maximum_pressure_Pa=100,point_count=True),
                        dict(maximum_pressure_Pa=100,point_count=200),dict(maximum_pressure_Pa=100,point_count=5,pc=1)]:
            with self.subTest(request=request),self.assertRaises(ValueError):
                nozzle_ambient_view(fixture(),request)

    def test_extreme_finite_operands_cannot_emit_infinity_or_divide_by_zero(self):
        base=dict(pc=1e7,mdot=200,at=.03,ae=.5,ve=2500,pe=50000,design_pa=101325)
        for changed in [dict(mdot=1e308,ve=1e308),dict(pc=1e-200,at=1e-200),dict(ae=1e308)]:
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                ambient_characteristic(**(base|changed))


class NozzleDesignAPITests(unittest.TestCase):
    def test_authenticated_read_only_api_and_assets(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client, TEST_PASSWORD
        from webapp.solver import presets
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(Path(temp)/'workspace.sqlite3',queue_enabled=False)
            with client(app) as api:
                self.assertEqual(api.post('/api/jobs/unknown/nozzle-ambient',json={}).status_code,401)
                api.post('/api/auth/setup',json=dict(username='admin',display_name='测试',password=TEST_PASSWORD))
                project=api.post('/api/projects',json=dict(name='喷管分析',inputs=presets()[0]['inputs'])).json()['project']
                job=api.post(f"/api/projects/{project['id']}/jobs",json={'revision':1}).json()['id']
                url=f'/api/jobs/{job}/nozzle-ambient'
                self.assertEqual(api.post(url,json={}).status_code,409)
                raw=json.dumps(fixture())
                app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(raw,job))
                view=api.get('/api/jobs/'+job).json()['nozzle_design']
                self.assertTrue(view['available'])
                req=dict(maximum_pressure_Pa=200000,point_count=5)
                response=api.post(url,json=req)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(len(response.json()['points']),5)
                self.assertEqual(api.post(url,json=req|dict(pc=1)).status_code,422)
                self.assertEqual(app.state.store.one('SELECT result FROM jobs WHERE id=?',(job,))['result'],raw)
                for asset in ['nozzle-design.js','nozzle-design.css']:
                    self.assertEqual(api.get('/static/'+asset).status_code,200)


if __name__=='__main__':unittest.main()
