"""Transient history shares the bounded queue but never replaces design jobs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from web_tests.test_transient_physics import source_fixture
from web_tests.test_throttle_analysis import source_fixture as design_fixture
from web_tests.test_workspace import client, TEST_PASSWORD
from webapp.app import create_app


class TransientAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name) / "workspace.sqlite3", queue_enabled=False)
        self.admin = client(self.app)
        self.addCleanup(self.admin.close)
        self.admin.post("/api/auth/setup", json={"username": "admin", "display_name": "测试管理员", "password": TEST_PASSWORD})
        self.source = source_fixture()
        self.project = self.admin.post("/api/projects", json={"name": "合成瞬态测试", "inputs": design_fixture()["inputs"]}).json()["project"]
        self.source_id = self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).json()["id"]
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                                     (json.dumps(self.source["result"]), self.source_id))
        self.endpoint = f"/api/projects/{self.project['id']}/transients"

    def submit(self, **parts):
        return self.admin.post(self.endpoint, json={"source_job_id": self.source_id, **parts})

    def test_snapshot_source_and_separate_history(self):
        edit = {key: deepcopy(self.project[key]) for key in ("name", "description", "revision", "inputs")}
        edit["inputs"]["pc"] *= 1.1
        self.assertEqual(self.admin.put(f"/api/projects/{self.project['id']}", json=edit).status_code, 200)
        response = self.submit(fuel_area_ratio=.97, sample_count=41)
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["id"]
        record = self.admin.get("/api/transients/"+key).json()
        self.assertEqual(record["kind"], "transient")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["request"]["source_job_id"], self.source_id)
        self.assertEqual(record["request"]["fuel_area_ratio"], .97)
        self.assertEqual(record["request"]["sample_count"], 41)
        self.assertEqual([r["id"] for r in self.admin.get(self.endpoint).json()], [key])
        self.assertNotIn(key, [r["id"] for r in self.admin.get("/api/jobs").json()])
        self.assertEqual(self.admin.get("/api/jobs/"+key).status_code, 404)
        self.assertEqual(self.admin.get("/api/transients/"+self.source_id).status_code, 404)
        self.assertEqual(self.admin.get("/api/jobs/"+self.source_id).json()["result"], self.source["result"])
        self.assertEqual(self.admin.get("/api/projects").json()[0]["job_count"], 1)
        self.assertEqual(self.admin.get(f"/api/projects/{self.project['id']}/validations").json(), [])

    def test_validation_and_cross_project_source_rejection(self):
        for parts in ({"sample_count": 1}, {"sample_count": 2002}, {"sample_count": 25.1},
                      {"fuel_area_ratio": 0}, {"fuel_area_ratio": True}, {"ox_area_ratio": 1.2},
                      {"duration_s": 11}, {"command_time_s": 2}, {"inputs": {}}, {"sample_count": 10**1000}):
            with self.subTest(parts=parts):
                self.assertEqual(self.submit(**parts).status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, json={"source_job_id": "missing"}).status_code, 422)
        self.assertEqual(self.admin.post("/api/projects/other/transients", json={"source_job_id": self.source_id}).status_code, 422)
        nan = '{"source_job_id":"'+self.source_id+'","fuel_area_ratio":NaN}'
        self.assertEqual(self.admin.post(self.endpoint, content=nan, headers={"Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, content='['*20000+'0'+']'*20000, headers={"Content-Type": "application/json"}).status_code, 422)
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (self.source_id,))
        self.assertEqual(self.submit().status_code, 422)

    def test_readiness_rejects_unphysical_source_and_static_assets_have_module_mime(self):
        setup = self.admin.get(self.endpoint+'/setup',params={"source_job_id":self.source_id})
        self.assertEqual(setup.status_code,200)
        self.assertTrue(setup.json()["ready"])
        broken=deepcopy(self.source["result"])
        broken["assessment"]["status"]="fail"
        self.app.state.store.execute("UPDATE jobs SET result=? WHERE id=?",(json.dumps(broken),self.source_id))
        self.assertFalse(self.admin.get(self.endpoint+'/setup',params={"source_job_id":self.source_id}).json()["ready"])
        self.assertEqual(self.submit().status_code,422)
        self.assertIn("javascript",self.admin.get('/static/transient.js').headers['content-type'])
        self.assertIn("text/css",self.admin.get('/static/transient.css').headers['content-type'])

    def test_partial_result_survives_cancel_and_is_exportable(self):
        key = self.submit().json()["id"]
        self.assertEqual(self.admin.get(f"/api/transients/{key}/download").status_code, 409)
        partial = {"schema_version": 1, "complete": False, "samples": [{"time_s": 0, "pc_Pa": 8000000}]}
        progress = {"completed": 1, "maximum": 5, "samples": partial["samples"]}
        self.app.state.store.execute("UPDATE jobs SET status='running',result=?,progress=? WHERE id=?",
                                     (json.dumps(partial), json.dumps(progress), key))
        self.assertEqual(self.admin.post(f"/api/transients/{key}/cancel", json={}).status_code, 200)
        record = self.admin.get(f"/api/transients/{key}").json()
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["result"], partial)
        self.assertEqual(record["progress"], progress)
        self.assertEqual(self.admin.get(f"/api/transients/{key}/download").json()["result"], partial)
        self.assertEqual(self.admin.post(f"/api/transients/{key}/cancel", json={}).status_code, 409)

    def test_auth_csrf_owner_cancellation_and_shared_queue_limit(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        for path in (self.endpoint, "/api/transients/no-record", "/api/transients/no-record/download"):
            self.assertEqual(guest.get(path).status_code, 401)
        self.assertEqual(guest.post(self.endpoint, json={"source_job_id": self.source_id}).status_code, 401)
        self.assertEqual(self.admin.post(self.endpoint, json={"source_job_id": self.source_id}, headers={"Origin": "https://invalid.example"}).status_code, 403)
        self.admin.post("/api/members", json={"username": "member", "display_name": "测试成员", "password": TEST_PASSWORD})
        guest.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD})
        keys = [self.submit().json()["id"] for _ in range(3)]
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).status_code, 429)
        self.assertEqual(guest.get("/api/transients/"+keys[0]).status_code, 200)
        self.assertEqual(guest.post("/api/transients/"+keys[0]+"/cancel", json={}).status_code, 403)
        self.assertEqual(self.admin.post("/api/transients/"+keys[0]+"/cancel", json={}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
