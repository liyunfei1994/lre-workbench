"""Project-scoped selection of immutable, sealed valve design point records."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.app import create_app
from web_tests.test_valve_design import fixture, seal
from web_tests.test_workspace import client, TEST_PASSWORD


class ValveDesignAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "valve-api.sqlite3"
        self.app = create_app(self.database, queue_enabled=False)
        self.admin = self.new_client(self.app)
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "阀门接口测试", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.admin_id = response.json()["id"]
        self.project_id = "project-valve-a"
        self.seed_project(self.project_id)
        self.url = f"/api/projects/{self.project_id}/valve-selection-envelope"
        self.a = self.seed_record(fixture(job="valve-job-a", project=self.project_id))
        self.b = self.seed_record(fixture(job="valve-job-b", project=self.project_id,
                                          revision=2, mass=150, temperature=150))

    def new_client(self, app):
        result = client(app)
        self.addCleanup(result.close)
        return result

    def seed_project(self, project_id):
        # Deliberately seed a saved fixture, not an unneeded scientific solve.
        # Revision 1 records ownership for the production authorization guard.
        payload = json.dumps({"cycle_type": "ffsc", "propellant_pair": "lox_methane"})
        self.app.state.store.execute(
            "INSERT INTO projects(id,name,description,revision,inputs,updated_by,created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (project_id, "阀门方案", "接口测试", 2, payload, self.admin_id, 1, 2))
        for revision in (1, 2):
            self.app.state.store.execute(
                "INSERT INTO revisions(project_id,revision,name,description,inputs,author,created) VALUES(?,?,?,?,?,?,?)",
                (project_id, revision, "阀门方案", "接口测试", payload, self.admin_id, revision))

    def seed_record(self, record, *, status="succeeded", kind="design"):
        self.app.state.store.execute(
            "INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,result,kind,analysis_task) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (record["id"], record["project_id"], record["revision"], self.admin_id, status,
             json.dumps(record["inputs"]), record["revision"], json.dumps(record["result"]), kind,
             json.dumps({"mode": "concept_design"})))
        return record

    def select(self, *ids, **kwargs):
        return self.admin.post(self.url, json={"job_ids": list(ids)}, **kwargs)

    def test_compatible_completed_points_return_their_own_states_and_sources_without_writes(self):
        before = self.app.state.store.all("SELECT * FROM jobs ORDER BY id")
        with patch("lre_workbench.components.valves.evaluate_valve", side_effect=AssertionError("no new solve")), \
             patch("lre_workbench.components.valves._heos_inlet", side_effect=AssertionError("no new properties")):
            response = self.select(self.a["id"], self.b["id"])
        self.assertEqual(response.status_code, 200, response.text)
        package = response.json()
        self.assertEqual(package["scope"], "saved_valve_selection_envelope")
        self.assertEqual(package["project_id"], self.project_id)
        self.assertEqual(package["validation_status"], "candidate_not_certified")
        self.assertEqual([item["source_design_job_id"] for item in package["sources"]], [self.a["id"], self.b["id"]])
        self.assertEqual([item["revision"] for item in package["sources"]], [1, 2])
        maximum = package["valves"]["MFV"]["ranges"]["mass_flow_kg_s"]["max"]
        self.assertEqual(maximum["value"], 150)
        self.assertEqual(maximum["source"]["source_design_job_id"], self.b["id"])
        self.assertEqual(maximum["point"]["result"]["inlet_state"]["temperature_K"], 150)
        self.assertEqual(maximum["point"]["result"]["inlet_state"]["density_kg_m3"], 600)
        self.assertEqual(self.app.state.store.all("SELECT * FROM jobs ORDER BY id"), before)
        self.assertEqual(self.select(self.a["id"], self.b["id"]).json(), package)

    def test_foreign_project_unknown_ids_and_non_design_jobs_are_rejected(self):
        other = "project-valve-b"
        self.seed_project(other)
        foreign = self.seed_record(fixture(job="foreign-job", project=other))
        non_design = self.seed_record(fixture(job="throttle-job", project=self.project_id), kind="throttle")
        for key in (foreign["id"], non_design["id"], "missing-job"):
            with self.subTest(key=key):
                response = self.select(self.a["id"], key)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("sources", response.json())
        self.assertEqual(self.admin.post("/api/projects/missing-project/valve-selection-envelope",
                                         json={"job_ids": [self.a["id"]]}).status_code, 404)

    def test_unfinished_failed_and_cancelled_jobs_are_never_enveloped(self):
        for status in ("queued", "running", "failed", "cancelled", "timed_out", "interrupted"):
            with self.subTest(status=status):
                self.app.state.store.execute("UPDATE jobs SET status=? WHERE id=?", (status, self.b["id"]))
                response = self.select(self.a["id"], self.b["id"])
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn("sources", response.json())

    def test_different_topology_or_characteristics_cannot_join_one_package(self):
        alternatives = [fixture(job="other-topology", project=self.project_id,
                                topology="all_fuel_cooled_then_split_valves_v1"),
                        fixture(job="other-characteristic", project=self.project_id, rated=.01)]
        for record in alternatives:
            self.seed_record(record)
            with self.subTest(job=record["id"]):
                response = self.select(self.a["id"], record["id"])
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("版本", response.json()["detail"])

    def test_duplicate_empty_oversize_and_malformed_selection_are_rejected(self):
        payloads = [None, [], {}, {"job_ids": []}, {"job_ids": [self.a["id"], self.a["id"]]},
                    {"job_ids": [f"job-{n}" for n in range(51)]}, {"job_ids": [1]},
                    {"job_ids": [""]}, {"job_ids": ["x" * 101]}, {"job_ids": self.a["id"]},
                    {"job_ids": [self.a["id"]], "project_id": "foreign-project"}]
        for body in payloads:
            with self.subTest(body=body):
                response = self.admin.post(self.url, content=json.dumps(body), headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)

    def test_unsealed_tampered_and_withdrawn_results_cannot_be_reused(self):
        unsealed = fixture(job="unsealed", project=self.project_id)
        del unsealed["result"]["design_traceability"]
        tampered = fixture(job="tampered", project=self.project_id)
        tampered["result"]["calculation"]["steady_closure"]["valve_design"]["valves"]["MFV"]["mass_flow_kg_s"] = 1
        withdrawn = fixture(job="withdrawn", project=self.project_id)
        withdrawn["result"]["calculation"]["model"] = {
            "eos": "Peng-Robinson-Boston-Mathias", "eos_model": {"version": "pr_boston_mathias_v1"}}
        seal(withdrawn)
        for record in (unsealed, tampered, withdrawn):
            self.seed_record(record)
            with self.subTest(job=record["id"]):
                self.assertEqual(self.select(self.a["id"], record["id"]).status_code, 422)

    def test_authentication_csrf_and_private_project_ownership_are_enforced(self):
        guest = self.new_client(self.app)
        self.assertEqual(guest.post(self.url, json={"job_ids": [self.a["id"]]}).status_code, 401)
        self.assertEqual(self.select(self.a["id"], headers={"Origin": "https://invalid.example"}).status_code, 403)
        response = self.admin.post("/api/members", json={
            "username": "member", "display_name": "其他成员", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        private = create_app(self.database, queue_enabled=False, private_workspace=True)
        member = self.new_client(private)
        self.assertEqual(member.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD}).status_code, 200)
        self.assertEqual(member.post(self.url, json={"job_ids": [self.a["id"]]}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
