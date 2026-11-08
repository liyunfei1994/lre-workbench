"""Immutable trim definitions and isolated nested numerical checks over HTTP."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.app import create_app
from webapp.store import Store
from webapp.validation import fingerprint
from web_tests import test_analysis_modes_api as mode_api
from web_tests.test_workspace import TEST_PASSWORD


class ControlTrimAPITests(unittest.TestCase):
    new_client = mode_api.AnalysisModesAPITests.new_client
    save = mode_api.AnalysisModesAPITests.save

    def setUp(self):
        mode_api.AnalysisModesAPITests.setUp(self)
        self.source["result"]["calculation"]["summary"]["thrust_N"] = 912345.
        self.app.state.store.execute("UPDATE jobs SET result=? WHERE id=?",
                                     (json.dumps(self.source["result"]), self.source_id))

    def saved(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        fixed = response.json()
        return fixed, self.tasks + "/" + fixed["id"]

    def definition(self, url, percent=40):
        response = self.admin.post(url + "/trim/definitions", json={"thrust_percent": percent})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_definitions_are_immutable_bound_records_without_engine_queue_side_effects(self):
        fixed, url = self.saved()
        jobs_before = self.app.state.store.all("SELECT * FROM jobs")
        response = self.admin.get(url + "/trim/setup")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["available_controls"], [])
        self.assertEqual(response.json()["binding"]["task_sha256"], fingerprint(fixed["task"]))
        with patch("webapp.solver.calculate", side_effect=AssertionError("concept solver forbidden")) as solver, \
             patch.object(self.app.state.queue, "start", side_effect=AssertionError("queue forbidden")) as start, \
             patch.object(self.app.state.queue, "run", side_effect=AssertionError("queue forbidden")) as run:
            first = self.definition(url)
            second = self.definition(url, 60)
            blocked = self.admin.post(url + "/trim/run", params={"trim_id": first["id"]}, json={})
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertEqual(blocked.json()["detail"]["status"], "blocked")
            self.assertFalse(blocked.json()["detail"]["can_run"])
            self.assertEqual(blocked.json()["detail"]["reason"], "inner_model_unavailable")
            self.assertTrue(blocked.json()["detail"]["blockers"])
            with patch("webapp.control_trim.assert_runnable", return_value=None):
                bypass = self.admin.post(url + "/trim/run", params={"trim_id": first["id"]}, json={})
                self.assertEqual(bypass.status_code, 409, bypass.text)
                self.assertFalse(bypass.json()["detail"]["can_run"])
            solver.assert_not_called()
            start.assert_not_called()
            run.assert_not_called()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["task_id"], fixed["id"])
        self.assertAlmostEqual(first["definition"]["targets"][0]["value"], .4 * 912345.)
        self.assertEqual(self.admin.get(url + "/trim/definitions").json(), [second, first])
        downloaded = self.admin.get(url + "/trim/definitions/download", params={"trim_id": first["id"]})
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertIn("attachment", downloaded.headers["content-disposition"])
        self.assertEqual(downloaded.json(), first)
        self.assertEqual(self.admin.put(url + "/trim/definitions", json={}).status_code, 405)
        self.assertEqual(self.admin.get(url).json(), fixed)
        self.assertEqual(self.app.state.store.all("SELECT * FROM jobs"), jobs_before)
        self.assertEqual(self.admin.get(self.project_url + "/throttles").json(), [])
        persisted = Store(self.database).one("SELECT definition FROM fixed_steady_trim_definitions WHERE id=?", (first["id"],))
        self.assertEqual(json.loads(persisted["definition"]), first["definition"])

    def test_numerical_checks_keep_scope_binding_inner_history_and_failure_statuses(self):
        fixed, url = self.saved()
        jobs_before = self.app.state.store.all("SELECT * FROM jobs")
        histories = []
        with patch("webapp.solver.calculate", side_effect=AssertionError("concept solver forbidden")) as solver:
            for scenario in ("reachable", "bound_limited", "inner_failure", "rank_deficient"):
                with self.subTest(scenario=scenario):
                    response = self.admin.post(url + "/trim/checks", json={"scenario": scenario})
                    self.assertEqual(response.status_code, 200, response.text)
                    record = response.json()
                    report = record["report"]
                    self.assertEqual(record["task_id"], fixed["id"])
                    self.assertEqual(report["analysis_type"], "control_trim_framework_check")
                    self.assertEqual(report["scope"], "numerical_framework_only")
                    self.assertIs(report["is_engine_result"], False)
                    self.assertIs(report["context"]["engine_inputs_used"], False)
                    self.assertEqual(report["context"]["task_sha256"], fingerprint(fixed["task"]))
                    self.assertTrue(report["evaluations"])
                    if scenario == "reachable":
                        self.assertGreaterEqual(len(report["points"]), 2)
                        self.assertLessEqual(report["points"][-1]["residual_norm"], 1)
                    if scenario == "inner_failure":
                        self.assertEqual(report["status"], "numerical_failure")
                        self.assertNotEqual(report["status"], "target_unreachable")
                    if scenario == "rank_deficient":
                        self.assertEqual(report["dof"]["independence"], "rank_deficient")
                    self.assertEqual(self.admin.get(url + "/trim/checks/download", params={"check_id": record["id"]}).json(), record)
                    histories.insert(0, record)
            solver.assert_not_called()
        self.assertEqual(self.admin.get(url + "/trim/checks").json(), histories)
        self.assertEqual(self.admin.get(url).json(), fixed)
        self.assertEqual(self.app.state.store.all("SELECT * FROM jobs"), jobs_before)
        self.assertEqual(self.admin.get(url + "/trim/definitions").json(), [])

    def test_request_validation_prevents_arbitrary_equations_controls_and_models(self):
        _, url = self.saved()
        invalid = ({}, {"scenario": "engine"}, {"scenario": "reachable", "thrust_percent": 40},
                   {"scenario": "reachable", "settings": {}}, {"scenario": "reachable", "formula": "x+1"},
                   {"scenario": None}, [], None, True)
        with patch('lre_workbench.systems.control_trim.run_trim_framework_check', side_effect=AssertionError("invalid payload must not execute")) as runner:
            for body in invalid:
                with self.subTest(body=body):
                    response = self.admin.post(url + "/trim/checks", content=json.dumps(body),
                                               headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
            runner.assert_not_called()
        for body in ({"thrust_percent": 40, "controls": ["invented"]},
                     {"thrust_percent": 40, "readiness": {"can_run": True}},
                     {"thrust_percent": 40, "hardware": {}},
                     {"thrust_percent": True}, {"thrust_percent": 0},
                     {"thrust_percent": 40, "additional_targets": {"preburner_mr": 60}}):
            with self.subTest(body=body):
                self.assertEqual(self.admin.post(url + "/trim/definitions", json=body).status_code, 422)
        for suffix in ("/trim/checks", "/trim/definitions"):
            response = self.admin.post(url + suffix, content="{", headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.admin.get(url + suffix).json(), [])

    def test_wrong_report_identity_is_not_saved_but_numerical_failure_is(self):
        _, url = self.saved()
        failed = {"schema_version": 1, "analysis_type": "control_trim_framework_check",
                  "scope": "numerical_framework_only", "is_engine_result": False,
                  "status": "numerical_failure", "reason": "inner_failure", "points": [],
                  "evaluations": [], "nfev": 0}
        with patch('lre_workbench.systems.control_trim.run_trim_framework_check', return_value=deepcopy(failed)):
            response = self.admin.post(url + "/trim/checks", json={"scenario": "inner_failure"})
            self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["report"]["status"], "numerical_failure")
        for key, value in (("scope", "engine"), ("is_engine_result", True), ("analysis_type", "engine")):
            with self.subTest(field=key), patch('lre_workbench.systems.control_trim.run_trim_framework_check', return_value=failed | {key: value}):
                response = self.admin.post(url + "/trim/checks", json={"scenario": "inner_failure"})
                self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(self.admin.get(url + "/trim/checks").json(), [saved])

    def test_cross_task_and_cross_project_access_never_leaks_records(self):
        first, first_url = self.saved()
        _, second_url = self.saved()
        definition = self.definition(first_url)
        check = self.admin.post(first_url + "/trim/checks", json={"scenario": "reachable"}).json()
        self.assertEqual(self.admin.get(second_url + "/trim/definitions").json(), [])
        self.assertEqual(self.admin.get(second_url + "/trim/checks").json(), [])
        for suffix, params in (("/trim/definitions/download", {"trim_id": definition["id"]}),
                               ("/trim/checks/download", {"check_id": check["id"]})):
            self.assertEqual(self.admin.get(second_url + suffix, params=params).status_code, 404)
        self.assertEqual(self.admin.post(second_url + "/trim/run", params={"trim_id": definition["id"]}, json={}).status_code, 404)
        other = self.admin.post("/api/projects", json={"name": "Other", "inputs": {}}).json()["project"]
        wrong = f"/api/projects/{other['id']}/fixed-steady/tasks/{first['id']}"
        reads, writes = self.paths(wrong, definition["id"], check["id"])
        for path in reads:
            self.assertEqual(self.admin.get(path).status_code, 404)
        for path, body in writes:
            self.assertEqual(self.admin.post(path, json=body).status_code, 404)

    @staticmethod
    def paths(url, trim_id, check_id):
        reads = (url + "/trim/setup", url + "/trim/definitions", url + "/trim/checks",
                 url + "/trim/definitions/download?trim_id=" + trim_id,
                 url + "/trim/checks/download?check_id=" + check_id)
        writes = ((url + "/trim/definitions", {"thrust_percent": 40}),
                  (url + "/trim/checks", {"scenario": "reachable"}),
                  (url + "/trim/run?trim_id=" + trim_id, {}))
        return reads, writes

    def test_authentication_csrf_and_private_ownership_cover_every_route(self):
        _, url = self.saved()
        definition = self.definition(url)
        check = self.admin.post(url + "/trim/checks", json={"scenario": "reachable"}).json()
        reads, writes = self.paths(url, definition["id"], check["id"])
        guest = self.new_client(self.app)
        for path in reads:
            self.assertEqual(guest.get(path).status_code, 401)
        for path, body in writes:
            self.assertEqual(guest.post(path, json=body).status_code, 401)
            self.assertEqual(self.admin.post(path, json=body, headers={"Origin": "https://invalid.example"}).status_code, 403)
        self.assertEqual(self.admin.post("/api/members", json={"username": "member", "display_name": "Member",
                                                               "password": TEST_PASSWORD}).status_code, 200)
        private = create_app(self.database, queue_enabled=False, private_workspace=True)
        member = self.new_client(private)
        self.assertEqual(member.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD}).status_code, 200)
        for path in reads:
            self.assertEqual(member.get(path).status_code, 404)
        for path, body in writes:
            self.assertEqual(member.post(path, json=body).status_code, 404)

    def test_source_result_change_blocks_new_definitions_without_rewriting_history(self):
        fixed, url = self.saved()
        definition = self.definition(url)
        modified = deepcopy(self.source["result"])
        modified["calculation"]["summary"]["thrust_N"] *= 1.01
        self.app.state.store.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(modified), self.source_id))
        self.assertIn(self.admin.get(url + "/trim/setup").status_code, (409, 422))
        self.assertIn(self.admin.post(url + "/trim/definitions", json={"thrust_percent": 40}).status_code, (409, 422))
        self.assertEqual(self.admin.get(url + "/trim/definitions").json(), [definition])
        self.assertEqual(self.admin.get(url + "/trim/definitions/download", params={"trim_id": definition["id"]}).json(), definition)
        self.assertEqual(self.admin.get(url).json(), fixed)


if __name__ == "__main__":
    unittest.main()
