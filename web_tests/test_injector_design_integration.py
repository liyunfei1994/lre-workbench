"""API projection keeps historical results immutable and shares scalar geometry."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from core import EngineInputs
from injector import Injector
from webapp.injector_design import injector_design_view


def sample():
    component=Injector(300,100,20e6,.2,.1,.15,200,100).evaluate()
    return {"calculation":{"inputs":asdict(EngineInputs(pc=20e6)),"components":{"injector":asdict(component),
        "main_chamber":{"outputs":{"cc_diameter_m":.3}}},
        "states":{"ox_turbine_exhaust":{"pressure":24e6,"temperature":700,"mass_flow":300},
                  "fuel_turbine_exhaust":{"pressure":22e6,"temperature":800,"mass_flow":100},
                  "main_chamber":{"pressure":20e6,"mass_flow":400}}}}


class InjectorAPITests(unittest.TestCase):
    def test_new_and_historical_projection_does_not_rewrite_database(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client,TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(Path(temp)/'workspace.sqlite3',queue_enabled=False)
            with client(app) as api:
                self.assertEqual(api.post('/api/auth/setup',json={'username':'admin','display_name':'测试用户','password':TEST_PASSWORD}).status_code,200)
                project=api.post('/api/projects',json={'name':'喷注器测试','inputs':asdict(EngineInputs())}).json()['project']
                job=api.post(f"/api/projects/{project['id']}/jobs",json={'revision':1}).json()['id']
                new=sample();old=deepcopy(new)
                old['calculation']['components']['injector']['profile']=None
                for result,expected in ((new,'consistent'),(old,'legacy')):
                    raw=json.dumps(result,ensure_ascii=False)
                    app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(raw,job))
                    for _ in range(2):
                        response=api.get('/api/jobs/'+job)
                        self.assertEqual(response.status_code,200,response.text)
                        body=response.json()
                        self.assertEqual(body['result'],result)
                        self.assertEqual(body['injector_design']['record_id'],job)
                        self.assertEqual(body['injector_design']['stations'][0]['status'],expected,body['injector_design'])
                        self.assertEqual(app.state.store.one('SELECT result FROM jobs WHERE id=?',(job,))['result'],raw)
                for name in ('injector-design.js','injector-design.css'):
                    self.assertEqual(api.get('/static/'+name).status_code,200)

    def test_saved_geometry_matches_core_and_desktop_projection(self):
        from geometry.injector import compute_injector
        result=sample();component=result['calculation']['components']['injector']
        desktop=compute_injector(.15,400,component_outputs=component['outputs'],component_profile=component['profile'])
        view=injector_design_view(result)['stations'][0]
        for key in ('num_elements','face_diameter_m','d_ox_mm','d_post_mm','d_outer_mm','delta_fuel_mm'):
            self.assertEqual(view['dimensions'][key],component['outputs'][key])
            self.assertEqual(getattr(desktop,key),component['outputs'][key])

if __name__=='__main__':unittest.main()
