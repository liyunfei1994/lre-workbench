"""Operating throttle history shares the queue; legacy rematches are read only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from web_tests.test_throttle_analysis import source_fixture
from web_tests.test_workspace import client, TEST_PASSWORD
from webapp.app import create_app


class ThrottleAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name) / "workspace.sqlite3", queue_enabled=False)
        self.admin = client(self.app)
        self.addCleanup(self.admin.close)
        self.admin.post("/api/auth/setup", json={"username": "admin", "display_name": "测试管理员", "password": TEST_PASSWORD})
        self.source = source_fixture()
        self.source["result"]["calculation"]["summary"]["thrust_N"] = 123456.
        core = patch("lre_workbench.operations.throttle.configuration", side_effect=lambda c: {
            "ready": True, "issues": [], "baseline": {"thrust_N": c["summary"]["thrust_N"]},
            "model": {"id": "fixed_geometry_local_throttle_v1"}, "limitations": []})
        core.start()
        self.addCleanup(core.stop)
        self.project = self.admin.post("/api/projects", json={"name": "合成节流测试", "inputs": self.source["inputs"]}).json()["project"]
        self.source_id = self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).json()["id"]
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                                     (json.dumps(self.source["result"]), self.source_id))
        self.endpoint = f"/api/projects/{self.project['id']}/operating-throttle"

    def submit(self, **parts):
        return self.admin.post(self.endpoint, json={"source_job_id": self.source_id, **parts})

    def test_snapshot_source_and_separate_history(self):
        edit = {key: deepcopy(self.project[key]) for key in ("name", "description", "revision", "inputs")}
        edit["inputs"]["pc"] *= 1.1
        self.assertEqual(self.admin.put(f"/api/projects/{self.project['id']}", json=edit).status_code, 200)
        response = self.submit(throttle_min=.55, throttle_steps=4)
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["id"]
        record = self.admin.get("/api/operating-throttle/"+key).json()
        self.assertEqual(record["kind"], "operating_throttle")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["request"], {"source_job_id": self.source_id, "throttle_min": .55, "throttle_max": 1., "throttle_steps": 4})
        self.assertEqual([r["id"] for r in self.admin.get(self.endpoint).json()], [key])
        self.assertNotIn(key, [r["id"] for r in self.admin.get("/api/jobs").json()])
        self.assertEqual(self.admin.get("/api/jobs/"+key).status_code, 404)
        self.assertEqual(self.admin.get("/api/operating-throttle/"+self.source_id).status_code, 404)
        config = self.admin.get(f"/api/jobs/{self.source_id}/operating-throttle-config").json()
        self.assertTrue(config["ready"])
        self.assertEqual(config["baseline"]["thrust_N"], 123456.)
        self.assertEqual(self.admin.get("/api/jobs/"+self.source_id).json()["result"], self.source["result"])
        self.assertEqual(self.admin.get("/api/projects").json()[0]["job_count"], 1)
        self.assertEqual(self.admin.get(f"/api/projects/{self.project['id']}/validations").json(), [])

    def test_validation_and_cross_project_source_rejection(self):
        for parts in ({"throttle_steps": 1}, {"throttle_steps": 16}, {"throttle_steps": 4.1},
                      {"throttle_min": 0}, {"throttle_min": True}, {"throttle_min": 1.01},
                      {"throttle_max": 1.1}, {"inputs": {}}, {"throttle_steps": 10**1000}):
            with self.subTest(parts=parts):
                self.assertEqual(self.submit(**parts).status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, json={"source_job_id": "missing"}).status_code, 422)
        self.assertEqual(self.admin.post("/api/projects/other/operating-throttle", json={"source_job_id": self.source_id}).status_code, 422)
        nan = '{"source_job_id":"'+self.source_id+'","throttle_min":NaN}'
        self.assertEqual(self.admin.post(self.endpoint, content=nan, headers={"Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, content='['*20000+'0'+']'*20000, headers={"Content-Type": "application/json"}).status_code, 422)
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (self.source_id,))
        self.assertEqual(self.submit().status_code, 422)

    def test_partial_result_survives_cancel_and_is_exportable(self):
        key = self.submit().json()["id"]
        self.assertEqual(self.admin.get(f"/api/operating-throttle/{key}/download").status_code, 409)
        partial = {"schema_version": 1, "complete": False, "points": [{"index": 1, "status": "failed"}]}
        progress = {"completed": 1, "maximum": 5, "points": partial["points"]}
        self.app.state.store.execute("UPDATE jobs SET status='running',result=?,progress=? WHERE id=?",
                                     (json.dumps(partial), json.dumps(progress), key))
        self.assertEqual(self.admin.post(f"/api/operating-throttle/{key}/cancel", json={}).status_code, 200)
        record = self.admin.get(f"/api/operating-throttle/{key}").json()
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["result"], partial)
        self.assertEqual(record["progress"], progress)
        self.assertEqual(self.admin.get(f"/api/operating-throttle/{key}/download").json()["result"], partial)
        self.assertEqual(self.admin.post(f"/api/operating-throttle/{key}/cancel", json={}).status_code, 409)

    def test_auth_csrf_owner_cancellation_and_shared_queue_limit(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        for path in (self.endpoint, "/api/operating-throttle/no-record", "/api/operating-throttle/no-record/download"):
            self.assertEqual(guest.get(path).status_code, 401)
        self.assertEqual(guest.post(self.endpoint, json={"source_job_id": self.source_id}).status_code, 401)
        self.assertEqual(self.admin.post(self.endpoint, json={"source_job_id": self.source_id}, headers={"Origin": "https://invalid.example"}).status_code, 403)
        self.admin.post("/api/members", json={"username": "member", "display_name": "测试成员", "password": TEST_PASSWORD})
        guest.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD})
        keys = [self.submit().json()["id"] for _ in range(3)]
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).status_code, 429)
        self.assertEqual(guest.get("/api/operating-throttle/"+keys[0]).status_code, 200)
        self.assertEqual(guest.post("/api/operating-throttle/"+keys[0]+"/cancel", json={}).status_code, 403)
        self.assertEqual(self.admin.post("/api/operating-throttle/"+keys[0]+"/cancel", json={}).status_code, 200)

    def test_legacy_submission_retired_and_queued_history_not_executed(self):
        endpoint = f"/api/projects/{self.project['id']}/throttles"
        self.assertEqual(self.admin.post(endpoint, json={"source_job_id": self.source_id}).status_code, 410)
        partial = {"analysis_mode": "concept_design_rematch", "complete": False, "points": [{"status": "failed"}]}
        self.app.state.store.execute(
            "INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind,result) "
            "SELECT 'legacy-scan',project_id,revision,submitted_by,'queued',?,created,'throttle',? FROM jobs WHERE id=?",
            (json.dumps({"source_job_id": self.source_id}), json.dumps(partial), self.source_id))
        queue = self.app.state.queue
        with patch.object(queue.stop_event, "wait", side_effect=[False, True]), patch("webapp.jobs.subprocess.Popen") as launch:
            queue.run()
        launch.assert_not_called()
        record = self.admin.get("/api/throttles/legacy-scan").json()
        self.assertEqual(record["status"], "failed")
        self.assertIn("停止执行", record["error"])
        self.assertEqual(record["result"], partial)
        self.assertEqual(self.admin.get("/api/throttles/legacy-scan/download").json()["result"], partial)
        self.assertEqual(self.admin.get("/api/operating-throttle/legacy-scan").status_code, 404)
        self.assertEqual([row["id"] for row in self.admin.get(endpoint).json()], ["legacy-scan"])


if __name__ == "__main__":
    unittest.main()
