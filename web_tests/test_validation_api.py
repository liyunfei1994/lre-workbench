"""Validation records retain source snapshots and run in isolated workers."""
from copy import deepcopy
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from web_tests.test_workspace import client, TEST_PASSWORD
from webapp.app import create_app
from webapp.jobs import JobQueue
from webapp.solver import presets
from webapp.store import Store
from webapp.validation import fingerprint
from web_tests.convergence_fixture import converged_evidence


class ValidationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "workspace.sqlite3"
        self.app = create_app(self.db, queue_enabled=False)
        self.admin = client(self.app)
        self.addCleanup(self.admin.close)
        self.admin.post("/api/auth/setup", json={"username": "admin", "display_name": "测试管理员", "password": TEST_PASSWORD})
        values = next(p["inputs"] for p in presets() if p["key"] == "tq12a_gg")
        self.project = self.admin.post("/api/projects", json={"name": "合成校核测试", "inputs": values}).json()["project"]
        self.source_id = self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).json()["id"]
        self.source = {"application_version": "test", "submitted_inputs": values, "environment": {"test": True},
                       "calculation": {"inputs": values, "summary": {"solver_residual_norm": 1e-5,
                       "mdot_total_kg_s": 100.0, "mdot_fuel_kg_s": 25.0, "mdot_ox_kg_s": 75.0},
                       "components": {}, "states": {}, "convergence": converged_evidence()}, "assessment": {"status": "pass"}}
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                                     (json.dumps(self.source), self.source_id))
        self.endpoint = f"/api/projects/{self.project['id']}/validations"

    def submit(self, **parts):
        return self.admin.post(self.endpoint, json={"source_job_id": self.source_id, **parts})

    def test_snapshot_separate_history_and_revision(self):
        payload = {key: deepcopy(self.project[key]) for key in ("name", "description", "revision", "inputs")}
        payload["inputs"]["thrust"] *= 1.01
        self.assertEqual(self.admin.put(f"/api/projects/{self.project['id']}", json=payload).status_code, 200)
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["id"]
        record = self.admin.get("/api/validations/" + key).json()
        self.assertEqual(record["kind"], "validation")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["request"], {"source_job_id": self.source_id})
        self.assertEqual([r["id"] for r in self.admin.get(self.endpoint).json()], [key])
        self.assertNotIn(key, [r["id"] for r in self.admin.get("/api/jobs").json()])
        self.assertEqual(self.admin.get("/api/jobs/" + key).status_code, 404)
        self.assertEqual(self.admin.get("/api/validations/" + self.source_id).status_code, 404)
        self.assertEqual(self.admin.get("/api/jobs/" + self.source_id).json()["result"], self.source)
        self.assertEqual(self.admin.get("/api/projects").json()[0]["job_count"], 1)

    def test_request_and_source_boundaries(self):
        for parts in ({"unknown": True}, {"properties": {"points": []}},
                      {"thermochemistry": {"propellant_pair": "lox_kerosene"}},
                      {"thermochemistry": {"of_values": [3] * 21}},
                      {"thermochemistry": {"pc_pa": 10**1000}},
                      {"experiment": {"role": "holdout", "measurements": [{"metric": "thrust_N", "value": True, "unit": "N", "tolerance": 1}]}}):
            with self.subTest(parts=parts):
                self.assertEqual(self.submit(**parts).status_code, 422)
        bad = self.admin.post(self.endpoint, content='{"source_job_id":"'+self.source_id+'","thermochemistry":{"pc_pa":NaN}}', headers={"Content-Type": "application/json"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, json={"source_job_id": "missing"}).status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, content='{"source_job_id":"\\ud800"}', headers={"Content-Type": "application/json"}).status_code, 422)
        malformed_key = '{"source_job_id":"'+self.source_id+'","thermochemistry":{"\\ud800":1}}'
        self.assertEqual(self.admin.post(self.endpoint, content=malformed_key, headers={"Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.admin.post(self.endpoint, content='['*20000+'0'+']'*20000, headers={"Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.admin.post("/api/projects/other/validations", json={"source_job_id": self.source_id}).status_code, 422)
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (self.source_id,))
        self.assertEqual(self.submit().status_code, 422)

    def test_auth_csrf_cancel_and_shared_queue(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        for path in ("/api/validation/capabilities", self.endpoint, "/api/validations/no-record"):
            self.assertEqual(guest.get(path).status_code, 401)
        self.assertEqual(guest.post(self.endpoint, json={"source_job_id": self.source_id}).status_code, 401)
        self.assertEqual(self.admin.post(self.endpoint, json={"source_job_id": self.source_id}, headers={"Origin": "https://invalid.example"}).status_code, 403)
        self.admin.post("/api/members", json={"username": "member", "display_name": "测试成员", "password": TEST_PASSWORD})
        guest.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD})
        keys = [self.submit().json()["id"] for _ in range(3)]
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).status_code, 429)
        self.assertEqual(guest.get("/api/validations/"+keys[0]).status_code, 200)
        self.assertEqual(guest.post("/api/validations/"+keys[0]+"/cancel", json={}).status_code, 403)
        self.assertEqual(self.admin.post("/api/validations/"+keys[0]+"/cancel", json={}).status_code, 200)
        self.assertEqual(self.admin.post("/api/validations/"+keys[0]+"/cancel", json={}).status_code, 409)
        for filename in ("validation.js", "thermo-stations.js", "validation.css", "validation-charts.js", "validation-charts.css", "property-comparison.js", "property-comparison.css", "component-analysis.js", "component-analysis.css", "flow-model.js", "flow-analysis.js", "flow-analysis.css", "cycle-model.js", "cycle-schematic.js", "cycle-schematic.css"):
            self.assertEqual(self.admin.get("/static/"+filename).status_code, 200)

    def test_actual_worker_preserves_source_and_returns_real_backends(self):
        capabilities = self.admin.get("/api/validation/capabilities").json()
        thermo = {"propellant_pair": "lox_methane", "pc_pa": 10e6, "of_values": [3.4],
                  "eps_values": [40], "T_fuel_K": 110, "T_ox_K": 90, "mode": "equilibrium"}
        response = self.submit(thermochemistry=thermo, properties={"points": [
            {"fluid": "Methane", "temperature_K": 110, "pressure_Pa": 3e5}], "compare_refprop": True})
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["id"]
        queue = JobQueue(self.app.state.store, timeout=90)
        queue.start()
        try:
            end = time.monotonic() + 100
            while time.monotonic() < end:
                record = self.admin.get("/api/validations/"+key).json()
                if record["status"] not in ("queued", "running"):
                    break
                time.sleep(.2)
            log = self.db.parent / "job-logs" / (key+".log")
            self.assertEqual(record["status"], "succeeded", log.read_text(encoding="utf-8", errors="replace") if log.exists() else record)
        finally:
            queue.stop()
        result = record["result"]
        self.assertEqual(result["source"]["job_id"], self.source_id)
        self.assertEqual(result["source"]["result_sha256"], fingerprint(self.source))
        self.assertEqual(result["source"]["inputs_sha256"], fingerprint(self.project["inputs"]))
        self.assertEqual(result["experiment"]["status"], "not_provided")
        self.assertNotEqual(result["conservation"]["status"], "pass")
        self.assertEqual(result["thermochemistry"]["counts"]["cantera_success"], 1)
        self.assertEqual(result["thermochemistry"]["counts"]["cea_success"], int(capabilities["cea"]["available"]))
        self.assertGreater(result["thermochemistry"]["points"][0]["cantera"]["metrics"]["Isp_vac_s"], 100)
        self.assertTrue(result["properties"]["checks"])
        refprop = result["properties"]["checks"][0]["refprop"]
        if capabilities["refprop"]["available"]:
            self.assertEqual(refprop["status"], "pass", refprop)
            self.assertIn("density_kg_m3", refprop["differences"])
        else:
            self.assertEqual(refprop["status"], "unavailable")
        self.assertEqual(self.admin.get("/api/jobs/"+self.source_id).json()["result"], self.source)
        json.dumps(result, allow_nan=False)


class ValidationMigrationTests(unittest.TestCase):
    def test_legacy_job_defaults_to_design_without_changing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            store = Store(database)
            with closing(sqlite3.connect(database)) as db:
                with db:
                    db.execute("ALTER TABLE jobs DROP COLUMN kind")
                    db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,result) VALUES('old','p',1,'u','succeeded','{\"test\":1}',1,'{\"answer\":2}')")
            migrated = Store(database).one("SELECT kind,inputs,result FROM jobs WHERE id='old'")
            self.assertEqual(migrated, {"kind": "design", "inputs": '{"test":1}', "result": '{"answer":2}'})


if __name__ == "__main__":
    unittest.main()
