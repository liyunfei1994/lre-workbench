"""Cooling studies share history and queue rules without editing design inputs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.app import create_app
from web_tests.test_cooling_design import source_fixture, request_fixture
from web_tests.test_workspace import client, TEST_PASSWORD


class CoolingDesignAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "workspace.sqlite3"
        self.app = create_app(self.db, queue_enabled=False)
        self.api = client(self.app)
        self.addCleanup(self.api.close)
        self.assertEqual(self.api.post("/api/auth/setup", json={"username":"admin",
            "display_name":"冷却筛选回归", "password":TEST_PASSWORD}).status_code, 200)
        self.source = source_fixture()
        response = self.api.post("/api/projects", json={"name":"冷却边界夹具", "inputs":self.source["inputs"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.project = response.json()["project"]
        response = self.api.post(f"/api/projects/{self.project['id']}/jobs", json={"revision":1})
        self.assertEqual(response.status_code, 200, response.text)
        self.source_id = response.json()["id"]
        self.source.update(id=self.source_id, project_id=self.project["id"])
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
            (json.dumps(self.source["result"], ensure_ascii=False), self.source_id))
        self.endpoint = f"/api/projects/{self.project['id']}/cooling-designs"

    def submit(self, **changes):
        return self.api.post(self.endpoint, json=request_fixture(self.source, **changes))

    def worker(self, study_id):
        from webapp import worker
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (study_id,))
        # Exercise the real worker dispatch and SQLite transaction path while
        # isolating process-controller setup, not the scientific calculation.
        with patch.object(worker.sys, "argv", ["worker", "--database", str(self.db), "--job", study_id]), \
                patch.object(worker.threading, "Thread"), \
                patch.object(worker.faulthandler, "dump_traceback_later"), \
                patch.object(worker.faulthandler, "cancel_dump_traceback_later"), \
                patch.object(worker, "science_environment", return_value={}), \
                patch.object(worker, "calculate", side_effect=AssertionError("must dispatch cooling study")):
            return worker.main()

    def test_saved_source_revision_and_distinct_history_survive_newer_project(self):
        old = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,))
        edit = {key:deepcopy(self.project[key]) for key in ("name","description","revision","inputs")}
        edit["inputs"]["cooling_n_channels"] = 250.
        self.assertEqual(self.api.put(f"/api/projects/{self.project['id']}", json=edit).status_code, 200)
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        study_id = response.json()["id"]
        study = self.api.get(f"/api/cooling-designs/{study_id}").json()
        self.assertEqual(study["kind"], "cooling_design")
        self.assertEqual(study["revision"], 1)
        self.assertEqual(study["request"]["source_job_id"], self.source_id)
        setup = self.api.get(self.endpoint+"/setup", params={"source_job_id":self.source_id})
        self.assertEqual(setup.status_code, 200, setup.text)
        self.assertEqual(setup.json()["channel_counts"], [300])
        self.assertEqual([r["id"] for r in self.api.get(self.endpoint).json()], [study_id])
        self.assertNotIn(study_id, [r["id"] for r in self.api.get("/api/jobs").json()])
        self.assertEqual(self.api.get("/api/jobs/"+study_id).status_code, 404)
        self.assertEqual(self.api.get("/api/cooling-designs/"+self.source_id).status_code, 404)
        self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,)), old)

    def test_invalid_requests_are_422_and_do_not_enqueue(self):
        bad = [dict(channel_counts=[]), dict(channel_counts=[10**1000]),
            dict(channel_counts=[10,20,30,40], widths_mm=[1,2,3,4], heights_mm=[1,2,3,4], wall_thicknesses_mm=[.5,1]),
            dict(constraints={"wall_limit_status":"documented","wall_temp_limit_K":900}),
            dict(source_job_id="missing"), dict(inputs={})]
        for changes in bad:
            with self.subTest(changes=changes):
                response = self.submit(**changes)
                self.assertEqual(response.status_code, 422, response.text)
        for content in ('{', '['*20000+'0'+']'*20000,
                json.dumps(request_fixture(self.source)).replace('[300]', '[NaN]')):
            response = self.api.post(self.endpoint, content=content, headers={"Content-Type":"application/json"})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.api.get(self.endpoint).json(), [])

    def test_source_must_be_same_project_completed_design(self):
        other = self.api.post("/api/projects", json={"name":"另一个项目", "inputs":self.source["inputs"]}).json()["project"]
        endpoint = f"/api/projects/{other['id']}/cooling-designs"
        self.assertEqual(self.api.post(endpoint, json=request_fixture(self.source)).status_code, 422)
        self.assertEqual(self.api.get(endpoint+"/setup", params={"source_job_id":self.source_id}).status_code, 422)
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (self.source_id,))
        self.assertEqual(self.submit().status_code, 422)
        self.assertEqual(self.api.get(self.endpoint+"/setup", params={"source_job_id":self.source_id}).status_code, 422)

    def test_actual_worker_persists_source_and_read_only_candidate_draft(self):
        study_id = self.submit(widths_mm=[1.5,12.]).json()["id"]
        before_project = self.app.state.store.one("SELECT * FROM projects WHERE id=?", (self.project["id"],))
        before_revisions = self.app.state.store.all("SELECT * FROM revisions WHERE project_id=?", (self.project["id"],))
        before_source = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,))
        self.assertEqual(self.worker(study_id), 0)
        record = self.api.get(f"/api/cooling-designs/{study_id}").json()
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["result"]["source_job_id"], self.source_id)
        self.assertEqual(record["result"]["source_revision"], 1)
        self.assertEqual(record["progress"]["iteration"], 2)
        self.assertEqual([c["status"] for c in record["result"]["candidates"]], ["computed", "failed"])
        response = self.api.get(f"/api/cooling-designs/{study_id}/candidates/1/draft")
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        self.assertEqual(draft["project_id"], self.project["id"])
        self.assertEqual(draft["source_job_id"], self.source_id)
        self.assertEqual(draft["input_patch"]["cooling_channel_auto_size"], 0.)
        self.assertEqual(draft["input_patch"]["cooling_channel_height_m"], .008)
        self.assertEqual(draft["assessment"]["status"], "incomplete")
        self.assertEqual(self.api.get(f"/api/cooling-designs/{study_id}/candidates/2/draft").status_code, 422)
        self.assertEqual(self.api.get(f"/api/cooling-designs/{study_id}/candidates/999/draft").status_code, 422)
        self.assertEqual(self.app.state.store.one("SELECT * FROM projects WHERE id=?", (self.project["id"],)), before_project)
        self.assertEqual(self.app.state.store.all("SELECT * FROM revisions WHERE project_id=?", (self.project["id"],)), before_revisions)
        self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,)), before_source)

    def test_worker_rechecks_cross_project_source_even_for_forged_queued_row(self):
        other = self.api.post("/api/projects", json={"name":"另一个项目", "inputs":self.source["inputs"]}).json()["project"]
        study_id = self.submit().json()["id"]
        self.app.state.store.execute("UPDATE jobs SET project_id=? WHERE id=?", (other["id"], study_id))
        self.assertEqual(self.worker(study_id), 1)
        record = self.api.get(f"/api/cooling-designs/{study_id}").json()
        self.assertEqual(record["status"], "failed")
        self.assertIn("来源计算记录", record["error"])
        self.assertIsNone(record["result"])

    def test_partial_result_is_retained_after_cancellation(self):
        study_id = self.submit().json()["id"]
        partial = {"source_job_id":self.source_id, "candidates":[{"id":1,"status":"failed","error":"fixture failure"}]}
        self.app.state.store.execute("UPDATE jobs SET status='running',result=?,progress=? WHERE id=?",
            (json.dumps(partial),json.dumps({"iteration":1,"maximum":2}),study_id))
        self.assertEqual(self.api.post(f"/api/cooling-designs/{study_id}/cancel", json={}).status_code, 200)
        record = self.api.get(f"/api/cooling-designs/{study_id}").json()
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["result"], partial)
        self.assertEqual(record["progress"], {"iteration":1,"maximum":2})
        self.assertEqual(self.api.post(f"/api/cooling-designs/{study_id}/cancel", json={}).status_code, 409)

    def test_auth_origin_shared_queue_and_cancel_ownership(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        for path in (self.endpoint, self.endpoint+"/setup?source_job_id="+self.source_id,
                "/api/cooling-designs/missing", "/api/cooling-designs/missing/candidates/1/draft"):
            self.assertEqual(guest.get(path).status_code, 401)
        self.assertEqual(guest.post(self.endpoint, json=request_fixture(self.source)).status_code, 401)
        self.assertEqual(self.api.post(self.endpoint, json=request_fixture(self.source),
            headers={"Origin":"https://invalid.example"}).status_code, 403)
        self.assertEqual(self.api.post("/api/members", json={"username":"member",
            "display_name":"成员", "password":TEST_PASSWORD}).status_code, 200)
        self.assertEqual(guest.post("/api/auth/login", json={"username":"member","password":TEST_PASSWORD}).status_code, 200)
        ids = [self.submit().json()["id"] for _ in range(3)]
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.api.post(f"/api/projects/{self.project['id']}/jobs", json={"revision":1}).status_code, 429)
        # Projects are shared by the workspace; cancelling another member's
        # active work is restricted even though reading the record is allowed.
        self.assertEqual(guest.get(f"/api/cooling-designs/{ids[0]}").status_code, 200)
        self.assertEqual(guest.post(f"/api/cooling-designs/{ids[0]}/cancel", json={}).status_code, 403)
        self.assertEqual(self.api.post(f"/api/cooling-designs/{ids[0]}/cancel", json={}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
