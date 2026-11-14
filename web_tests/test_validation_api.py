"""Validation records retain source snapshots and run in isolated workers."""
from copy import deepcopy
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        values = next(p["inputs"] for p in presets() if p["key"] == "raptor3_ffsc")
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

    def run_worker(self, key):
        from webapp import worker

        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (key,))
        with (patch.object(worker, "threading", SimpleNamespace(Thread=Mock())),
              patch.object(worker, "science_environment", return_value={}),
              patch.object(worker.traceback, "print_exc"),
              patch.object(worker.sys, "argv", ["worker", "--database", str(self.db), "--job", key])):
            return worker.main()

    def test_gas_eos_comparison_worker_preserves_scope_and_source(self):
        source_row = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,))
        eos_request = {"source_job_id": self.source_id, "gas_eos_comparison": {"enabled": True}}
        other = self.admin.post("/api/projects", json={"name": "其他方案", "inputs": self.project["inputs"]}).json()["project"]
        other_endpoint = f"/api/projects/{other['id']}/validations"
        self.assertEqual(self.admin.post(other_endpoint, json=eos_request).status_code, 422)
        self.assertEqual(self.submit(gas_eos_comparison={"enabled": True, "pc": 1e6}).status_code, 422)

        response = self.admin.post(self.endpoint, json=eos_request)
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()["id"]
        forged_id = self.admin.post(self.endpoint, json=eos_request).json()["id"]
        self.app.state.store.execute("UPDATE jobs SET project_id=? WHERE id=?", (other["id"], forged_id))
        wrong_revision = self.admin.post(self.endpoint, json=eos_request).json()["id"]
        self.app.state.store.execute("UPDATE jobs SET revision=revision+1 WHERE id=?", (wrong_revision,))

        def calculate(source, progress):
            self.assertEqual(source["id"], self.source_id)
            self.assertEqual(source["inputs"], json.loads(source_row["inputs"]))
            self.assertEqual(source["result"], self.source)
            event = {"phase": "整循环燃气 EOS 对比", "stage": "solving", "eos": "Peng-Robinson",
                     "solver_iteration": 17, "solver_maximum": 100, "residual": .031}
            progress(1, 2, event)
            running_row = self.app.state.store.one("SELECT status,progress,result FROM jobs WHERE id=?", (job_id,))
            self.assertEqual(running_row["status"], "running")
            self.assertIsNone(running_row["result"])
            self.assertEqual(json.loads(running_row["progress"]), {"iteration": 1, "maximum": 2, **event})
            progress(2, 2, {"phase": "整循环燃气 EOS 对比", "stage": "reviewed"})
            return {"status": "complete"}

        with patch("webapp.eos_comparison.compare_gas_eos", side_effect=calculate) as compare:
            for key, expected in ((job_id, 0), (forged_id, 1), (wrong_revision, 1)):
                self.assertEqual(self.run_worker(key), expected)
            compare.assert_called_once()

        record = self.admin.get(f"/api/validations/{job_id}").json()
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["request"], eos_request)
        self.assertEqual(record["progress"], {"iteration": 2, "maximum": 2, "phase": "整循环燃气 EOS 对比",
                                              "stage": "reviewed"})
        self.assertEqual(record["result"]["source"]["project_id"], self.project["id"])
        self.assertEqual(record["result"]["source"]["inputs_sha256"], fingerprint(json.loads(source_row["inputs"])))
        self.assertEqual(record["result"]["gas_eos_comparison"]["status"], "complete")
        for key in (forged_id, wrong_revision):
            rejected = self.admin.get(f"/api/validations/{key}").json()
            self.assertEqual(rejected["status"], "failed")
            self.assertIsNone(rejected["result"])
        self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,)), source_row)

    def test_gas_eos_comparison_rejects_invalid_saved_input_before_queueing(self):
        values = self.project["inputs"] | {"pc": -1.}
        self.app.state.store.execute("UPDATE jobs SET inputs=? WHERE id=?", (json.dumps(values), self.source_id))
        response = self.submit(gas_eos_comparison={"enabled": True})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("pc", response.json()["detail"])
        self.assertEqual(self.admin.get(self.endpoint).json(), [])

    def test_explicit_bm_candidate_survives_queue_worker_and_result_identity(self):
        candidate = "Peng-Robinson-Boston-Mathias"
        before = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,))
        submitted = self.submit(gas_eos_comparison={"enabled": True, "candidate_eos": candidate})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        key = submitted.json()["id"]
        with patch("webapp.eos_comparison.compare_gas_eos", return_value={
                "status": "complete", "candidate_eos": candidate, "candidate_selection": "explicit"}) as compare:
            self.assertEqual(self.run_worker(key), 0)
        self.assertEqual(compare.call_args.kwargs, {"candidate_eos": candidate})
        record = self.admin.get(f"/api/validations/{key}").json()
        self.assertEqual(record["request"]["gas_eos_comparison"], {"enabled": True, "candidate_eos": candidate})
        self.assertEqual(record["result"]["gas_eos_comparison"]["candidate_eos"], candidate)
        self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,)), before)

    def test_legacy_correction_source_is_readable_but_cannot_queue_eos_recalculation(self):
        for missing in (False, True):
            values = self.project["inputs"].copy()
            if missing:
                del values["combustion_loss_model"]
            else:
                values["combustion_loss_model"] = "legacy_temperature_rise"
            self.app.state.store.execute("UPDATE jobs SET inputs=? WHERE id=?", (json.dumps(values), self.source_id))
            before = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,))
            response = self.submit(gas_eos_comparison={"enabled": True, "candidate_eos": "Peng-Robinson-Boston-Mathias"})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("旧版本温升折减", response.json()["detail"])
            saved = self.admin.get(f"/api/jobs/{self.source_id}")
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["inputs"], values)
            self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,)), before)
        self.assertEqual(self.admin.get(self.endpoint).json(), [])

    def test_cancelled_gas_eos_comparison_cannot_publish_late_result(self):
        key = self.submit(gas_eos_comparison={"enabled": True}).json()["id"]
        endpoint = f"/api/validations/{key}"
        event = {"phase": "整循环燃气 EOS 对比", "stage": "initializing", "eos": "Peng-Robinson"}

        def calculate(_source, progress):
            progress(1, 2, event)
            cancelled = self.admin.post(endpoint + "/cancel", json={})
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            progress(2, 2, {"phase": "整循环燃气 EOS 对比", "stage": "reviewed"})
            return {"status": "complete"}

        with patch("webapp.eos_comparison.compare_gas_eos", side_effect=calculate):
            self.assertEqual(self.run_worker(key), 0)
        record = self.admin.get(endpoint).json()
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["progress"], {"iteration": 1, "maximum": 2, **event})
        self.assertIsNone(record["result"])

    def test_design_basis_projection_is_record_bound_and_does_not_write_history(self):
        source = deepcopy(self.source)
        source["calculation"]["inputs"].update(cycle_type="ffsc", propellant_pair="lox_methane")
        saved = json.dumps(source)
        self.app.state.store.execute("UPDATE jobs SET result=? WHERE id=?", (saved, self.source_id))
        response = self.admin.get("/api/jobs/" + self.source_id)
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()["steady_design"]
        self.assertEqual(view["record_id"], self.source_id)
        self.assertEqual(view["schema_version"], 1)
        self.assertEqual(view["validation_status"], "not_certified")
        self.assertTrue(all(m["value"] == "未记录" for m in view["models"]))
        after = self.app.state.store.one("SELECT result FROM jobs WHERE id=?", (self.source_id,))
        self.assertEqual(after["result"], saved)

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

    def test_gg_source_remains_readable_but_cannot_start_a_new_validation(self):
        for identity_location in ("inputs", "result"):
            with self.subTest(identity_location=identity_location):
                source = deepcopy(self.source)
                values = deepcopy(self.project["inputs"])
                if identity_location == "inputs":
                    values["cycle_type"] = "gg"
                else:
                    source["calculation"]["inputs"]["cycle_type"] = "gg"
                self.app.state.store.execute("UPDATE jobs SET inputs=?,result=? WHERE id=?",
                    (json.dumps(values), json.dumps(source), self.source_id))
                before = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,))
                response = self.submit()
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("FFSC", response.json()["detail"])
                self.assertEqual(self.admin.get(self.endpoint).json(), [])
                viewed = self.admin.get("/api/jobs/" + self.source_id)
                self.assertEqual(viewed.status_code, 200, viewed.text)
                self.assertEqual(viewed.json()["inputs"], values)
                self.assertEqual(viewed.json()["result"], source)
                self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (self.source_id,)), before)

    def test_gas_handoff_worker_preserves_source_and_keeps_baseline_separate(self):
        from tests.test_gas_handoff import fixture
        source = fixture()
        source["calculation"]["inputs"] = {"cycle_type": "ffsc", "propellant_pair": "lox_methane",
                                             "methane_thermo_dataset": "gri30_highT"}
        source["submitted_inputs"] = source["calculation"]["inputs"]
        self.app.state.store.execute("UPDATE jobs SET result=?,inputs=? WHERE id=?",
            (json.dumps(source), json.dumps(source["submitted_inputs"]), self.source_id))
        response = self.submit(gas_screening={"enabled": True})
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["id"]
        queue = JobQueue(self.app.state.store, timeout=90)
        queue.start()
        try:
            end = time.monotonic()+95
            while time.monotonic() < end:
                record = self.admin.get("/api/validations/"+key).json()
                if record["status"] not in ("queued", "running"): break
                time.sleep(.2)
            self.assertEqual(record["status"], "succeeded", record)
        finally:
            queue.stop()
        result = record["result"]
        self.assertIsNone(result["thermochemistry"])
        self.assertEqual(result["gas_handoff"]["status"], "complete")
        self.assertEqual(result["chamber_handoff_comparison"]["status"], "ok")
        self.assertEqual(len(result["gas_screening"]["stations"]), 4)
        self.assertTrue(all(s["status"] == "screened_with_limitations" for s in result["gas_screening"]["stations"]))
        self.assertEqual(self.admin.get("/api/jobs/"+self.source_id).json()["result"], source)
        for filename in ("gas-handoff.js", "gas-handoff.css"):
            self.assertEqual(self.admin.get("/static/"+filename).status_code, 200)

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
