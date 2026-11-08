"""Task identity, immutable snapshots and fail-closed mode separation at the API."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.app import create_app
from webapp.store import Store
from web_tests.test_throttle_analysis import source_fixture
from web_tests.test_workspace import client, TEST_PASSWORD


class AnalysisModesAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "workspace.sqlite3"
        self.app = create_app(self.database, queue_enabled=False)
        self.admin = self.new_client(self.app)
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "模式测试管理员", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.source = source_fixture()
        response = self.admin.post("/api/projects", json={
            "name": "独立任务测试", "inputs": self.source["inputs"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.project = response.json()["project"]
        self.project_url = f"/api/projects/{self.project['id']}"
        response = self.admin.post(self.project_url + "/jobs", json={"revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.source_id = response.json()["id"]
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                                     (json.dumps(self.source["result"]), self.source_id))
        self.base = self.project_url + "/fixed-steady"
        self.tasks = self.base + "/tasks"

    def new_client(self, app):
        result = client(app)
        self.addCleanup(result.close)
        return result

    def save(self, **parts):
        return self.admin.post(self.tasks, json={"source_job_id": self.source_id, **parts})

    def template(self):
        response = self.admin.get(self.base + "/setup", params={"source_job_id": self.source_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_concept_entry_records_partition_and_rejects_fixed_mode(self):
        from lre_workbench.domain.analysis_contract import concept_inputs

        original = deepcopy(self.project["inputs"])
        for options in ({}, {"mode": "concept_design"}):
            with self.subTest(options=options):
                response = self.admin.post(self.project_url + "/jobs", json={"revision": 1, **options})
                self.assertEqual(response.status_code, 200, response.text)
                record = self.admin.get("/api/jobs/" + response.json()["id"]).json()
                self.assertEqual(record["analysis_task"]["mode"], "concept_design")
                self.assertEqual(concept_inputs(record["analysis_task"]), original)
                self.assertEqual(record["inputs"], original)
        count = self.app.state.store.one("SELECT COUNT(*) AS n FROM jobs")["n"]
        for mode in ("fixed_hardware_steady", "fixed_hardware_transient", "unknown"):
            with self.subTest(mode=mode):
                response = self.admin.post(self.project_url + "/jobs", json={"revision": 1, "mode": mode})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.app.state.store.one("SELECT COUNT(*) AS n FROM jobs")["n"], count)

    def test_fixed_task_freezes_source_and_has_separate_persistent_history(self):
        template = self.template()
        edit = {key: deepcopy(self.project[key]) for key in ("name", "description", "revision", "inputs")}
        edit["inputs"]["pc"] *= 1.1
        self.assertEqual(self.admin.put(self.project_url, json=edit).status_code, 200)
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        self.assertEqual(record["source_job_id"], self.source_id)
        self.assertEqual(record["task"], template)
        self.assertEqual(record["task"]["source"]["revision"], 1)
        self.assertEqual(record["task"]["mode"], "fixed_hardware_steady")
        self.assertEqual(record["task"]["schema_version"], 2)
        self.assertEqual(set(record["task"]["operating_conditions"]["boundaries"]),
                         {"ambient_pressure", "tank_pressure_pa", "fuel_inlet_temp", "ox_inlet_temp"})
        self.assertFalse(record["task"]["readiness"]["can_run"])
        self.assertTrue(record["task"]["readiness"]["blockers"])
        url = self.tasks + "/" + record["id"]
        self.assertEqual(self.admin.get(url).json(), record)
        self.assertEqual(self.admin.get(self.tasks).json(), [record])
        exported = self.admin.get(url + "/download")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertEqual(exported.json(), record)
        self.assertEqual(self.admin.put(url, json={"task": {}}).status_code, 405)
        self.assertEqual(self.admin.get("/api/jobs/" + self.source_id).json()["result"], self.source["result"])
        self.assertEqual([row["id"] for row in self.admin.get("/api/jobs").json()], [self.source_id])
        self.assertEqual(self.admin.get(self.project_url + "/throttles").json(), [])
        restarted = Store(self.database)
        saved = restarted.one("SELECT * FROM fixed_steady_tasks WHERE id=?", (record["id"],))
        self.assertEqual(json.loads(saved["task"]), template)
        self.assertEqual(restarted.one("SELECT COUNT(*) AS n FROM jobs")["n"], 1)

    def test_boundaries_and_named_guesses_do_not_change_hardware_identity(self):
        template = self.template()
        response = self.save(
            operating_conditions={"boundaries": {"ambient_pressure": 50000}},
            initial_guess={"mode": "fixed_hardware_steady", "variables": {"mdot_fuel_kg_s": 1.5}})
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()["task"]
        self.assertEqual(task["hardware"], template["hardware"])
        self.assertEqual(task["source"], template["source"])
        self.assertEqual(task["operating_conditions"]["boundaries"]["ambient_pressure"], 50000)
        self.assertEqual(task["initial_guess"]["variables"]["mdot_fuel_kg_s"], 1.5)
        self.assertEqual(task["legacy_design_assumptions"], template["legacy_design_assumptions"])
        self.assertEqual(self.template(), template)

    def test_fixed_entry_rejects_design_targets_geometry_and_anonymous_guess(self):
        invalid = [
            {"inputs": self.project["inputs"]},
            {"mode": "concept_design"},
            {"design_targets": {"ox_preburner_temp_target": 750}},
            {"ox_preburner_temp_target": 750},
            {"use_target_preburner_temp": 1},
            {"preburner_mr_targets": {"ox": 60, "fuel": .16}},
            {"hardware": {"nozzle": {"throat_area_m2": .04}}},
            {"operating_conditions": {"boundaries": {"ox_preburner_temp_target": 750}}},
            {"operating_conditions": {"boundaries": {"pc": 30e6}}},
            {"operating_conditions": {"boundaries": {"inlet_pressure": 1e6}}},
            {"operating_conditions": {"boundaries": {"feed_line_dp_pa": 50000}}},
            {"operating_conditions": {"boundaries": {"lox_pump_speed_rpm_override": 26500}}},
            {"operating_conditions": {"boundaries": {"fuel_pump_speed_rpm_override": 38000}}},
            {"operating_conditions": {"controls": {"preburner_mr_targets": {"ox": 60}}}},
            {"operating_conditions": {"controls": {"valve_openings": {"invented_valve": .4}}}},
            {"operating_conditions": {"control_targets": {"thrust": 1e6}}},
            {"operating_conditions": {"control_targets": {"of_ratio": 3.6}}},
            {"legacy_design_assumptions": {"inlet_pressure": {"value": 1e6}}},
            {"operating_conditions": {"boundaries": {"ambient_pressure": True}}},
            {"initial_guess": {"mode": "concept_design", "variables": {"mdot_fuel_kg_s": 2}}},
            {"initial_guess": {"x": [6, 1.8, 2]}},
            {"initial_guess": {"variables": {"x": [6, 1.8, 2]}}},
        ]
        for parts in invalid:
            with self.subTest(parts=parts):
                response = self.save(**parts)
                self.assertEqual(response.status_code, 422, response.text)
        for bad in ("null", "[]", "{", '['*20000+'0'+']'*20000):
            with self.subTest(json_prefix=bad[:20]):
                response = self.admin.post(self.tasks, content=bad, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.admin.get(self.tasks).json(), [])

    def test_saved_v1_fixed_task_is_returned_without_role_or_boundary_migration(self):
        record = self.save().json()
        new = record["task"]
        inputs = self.source["result"]["calculation"]["inputs"]
        # Explicit original schema, not a v2 definition merely relabeled as v1.
        old_hardware = deepcopy(new["hardware"])
        old_hardware["schema_version"] = 1
        old_hardware["payload"].pop("valves")
        from webapp.validation import fingerprint
        old_hardware["id"] = fingerprint(old_hardware["payload"])
        old_task = {"schema_version": 1, "mode": "fixed_hardware_steady",
            "source": deepcopy(new["source"]), "hardware": old_hardware,
            "operating_conditions": {"boundaries": {key: inputs[key] for key in (
                "ambient_pressure", "tank_pressure_pa", "inlet_pressure", "feed_line_dp_pa",
                "fuel_inlet_temp", "ox_inlet_temp")}, "controls": {"valve_openings": {}}},
            "initial_guess": deepcopy(new["initial_guess"]), "constraints": deepcopy(new["constraints"]),
            "readiness": {"can_run": False, "status": "blocked", "blockers": [
                {"code": "fixed_steady_solver_unavailable", "label": "旧任务未实现求解器",
                 "detail": "保留创建时的数据与说明。"}]}}
        serialized = json.dumps(old_task, ensure_ascii=False, indent=2)
        self.app.state.store.execute("UPDATE fixed_steady_tasks SET task=? WHERE id=?", (serialized, record["id"]))
        record["task"] = old_task
        url = self.tasks + "/" + record["id"]
        self.assertEqual(self.admin.get(url).json(), record)
        self.assertEqual(self.admin.get(url + "/download").json(), record)
        self.assertEqual(self.admin.get(self.tasks).json(), [record])
        self.assertEqual(self.admin.post(url + "/run", json={}).status_code, 409)
        saved = self.app.state.store.one("SELECT task FROM fixed_steady_tasks WHERE id=?", (record["id"],))
        self.assertEqual(saved["task"], serialized)
        self.assertNotIn("parameter_roles", old_task)
        self.assertNotIn("legacy_design_assumptions", old_task)

    def test_legacy_design_without_task_partition_is_not_rewritten(self):
        self.app.state.store.execute("UPDATE jobs SET analysis_task=NULL WHERE id=?", (self.source_id,))
        before = self.app.state.store.one("SELECT inputs,result FROM jobs WHERE id=?", (self.source_id,))
        record = self.admin.get("/api/jobs/" + self.source_id)
        self.assertEqual(record.status_code, 200, record.text)
        self.assertIsNone(record.json()["analysis_task"])
        self.assertEqual(record.json()["result"], self.source["result"])
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["task"]["source"]["job_id"], self.source_id)
        self.assertEqual(self.app.state.store.one("SELECT inputs,result FROM jobs WHERE id=?", (self.source_id,)), before)

    def test_run_never_queues_or_falls_back_even_when_readiness_claims_ready(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        before = self.app.state.store.all("SELECT * FROM jobs")
        with patch("webapp.solver.calculate", side_effect=AssertionError("must not invoke design solver")) as solver:
            response = self.admin.post(self.tasks + "/" + record["id"] + "/run", json={})
            self.assertEqual(response.status_code, 409, response.text)
            solver.assert_not_called()
        record["task"]["readiness"].update(can_run=True, status="ready", blockers=[])
        self.app.state.store.execute("UPDATE fixed_steady_tasks SET task=? WHERE id=?",
                                     (json.dumps(record["task"]), record["id"]))
        with patch("webapp.fixed_steady.assert_runnable", return_value=None):
            response = self.admin.post(self.tasks + "/" + record["id"] + "/run", json={})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.app.state.store.all("SELECT * FROM jobs"), before)

    def test_source_must_be_successful_design_in_the_same_project(self):
        other = self.admin.post("/api/projects", json={"name": "其他方案", "inputs": {}}).json()["project"]
        other_base = f"/api/projects/{other['id']}/fixed-steady"
        response = self.admin.post(other_base + "/tasks", json={"source_job_id": self.source_id})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.admin.get(other_base + "/setup", params={"source_job_id": self.source_id}).status_code, 422)
        record = self.save().json()
        for suffix in ("", "/download", "/run"):
            url = other_base + "/tasks/" + record["id"] + suffix
            response = self.admin.post(url, json={}) if suffix == "/run" else self.admin.get(url)
            self.assertEqual(response.status_code, 404, response.text)
        for status, kind in (("running", "design"), ("failed", "design"), ("succeeded", "throttle")):
            with self.subTest(status=status, kind=kind):
                self.app.state.store.execute("UPDATE jobs SET status=?,kind=? WHERE id=?", (status, kind, self.source_id))
                self.assertEqual(self.save().status_code, 422)
                self.assertEqual(self.admin.get(self.base + "/setup", params={"source_job_id": self.source_id}).status_code, 422)

    def test_authentication_csrf_and_private_project_ownership(self):
        record = self.save().json()
        url = self.tasks + "/" + record["id"]
        guest = self.new_client(self.app)
        for path in (self.base + "/setup?source_job_id=" + self.source_id, self.tasks, url, url + "/download"):
            self.assertEqual(guest.get(path).status_code, 401)
        for path in (self.tasks, url + "/run"):
            self.assertEqual(guest.post(path, json={"source_job_id": self.source_id}).status_code, 401)
            response = self.admin.post(path, json={"source_job_id": self.source_id}, headers={"Origin": "https://invalid.example"})
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.admin.post("/api/members", json={
            "username": "member", "display_name": "测试成员", "password": TEST_PASSWORD}).status_code, 200)
        private = create_app(self.database, queue_enabled=False, private_workspace=True)
        member = self.new_client(private)
        self.assertEqual(member.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD}).status_code, 200)
        for path in (self.base + "/setup?source_job_id=" + self.source_id, self.tasks, url, url + "/download"):
            self.assertEqual(member.get(path).status_code, 404)
        for path in (self.tasks, url + "/run"):
            self.assertEqual(member.post(path, json={"source_job_id": self.source_id}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
