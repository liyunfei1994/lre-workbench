"""Task-bound data declarations and numerical checks remain outside engine jobs."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.app import create_app
from webapp.store import Store
from webapp.validation import fingerprint
from web_tests import test_analysis_modes_api as mode_api
from web_tests.test_steady_data_contract import declared_package
from web_tests.test_workspace import TEST_PASSWORD


class SteadyFrameworkAPITests(unittest.TestCase):
    # Reuse setup helpers, not the parent TestCase (which would rerun its tests).
    setUp = mode_api.AnalysisModesAPITests.setUp
    new_client = mode_api.AnalysisModesAPITests.new_client
    save = mode_api.AnalysisModesAPITests.save
    template = mode_api.AnalysisModesAPITests.template

    def saved(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        return record, self.tasks + "/" + record["id"]

    def test_template_download_and_data_check_never_change_saved_task(self):
        record, url = self.saved()
        before = self.app.state.store.one("SELECT * FROM fixed_steady_tasks WHERE id=?", (record["id"],))
        response = self.admin.get(url + "/data-template")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("attachment", response.headers["content-disposition"])
        package = response.json()
        self.assertEqual(package["binding"]["task_sha256"], fingerprint(record["task"]))
        self.assertEqual(package["binding"]["task_id"], record["id"])
        for declaration, status in ((package, "incomplete"), (declared_package(record), "declared_unverified")):
            response = self.admin.post(url + "/data-check", json=declaration)
            self.assertEqual(response.status_code, 200, response.text)
            checked = response.json()
            self.assertEqual(checked["status"], status)
            self.assertFalse(checked["can_run"])
            self.assertFalse(checked["adopted"])
        self.assertEqual(self.admin.get(url).json(), record)
        self.assertEqual(self.app.state.store.one("SELECT * FROM fixed_steady_tasks WHERE id=?", (record["id"],)), before)
        self.assertEqual(self.admin.get(url + "/framework-checks").json(), [])
        self.assertEqual(self.admin.post(url + "/run", json={}).status_code, 409)

    def test_real_numerical_check_records_multiple_accepted_steps_and_persists(self):
        record, url = self.saved()
        jobs = self.app.state.store.all("SELECT * FROM jobs")
        with patch("webapp.solver.calculate", side_effect=AssertionError("concept solver forbidden")) as solver, \
             patch.object(self.app.state.queue, "start", side_effect=AssertionError("queue forbidden")) as queue_start, \
             patch.object(self.app.state.queue, "run", side_effect=AssertionError("queue forbidden")) as queue_run:
            response = self.admin.post(url + "/framework-checks", json={})
            self.assertEqual(response.status_code, 200, response.text)
            check = response.json()
            self.assertEqual(self.admin.post(url + "/run", json={}).status_code, 409)
            solver.assert_not_called()
            queue_start.assert_not_called()
            queue_run.assert_not_called()
        report = check["report"]
        self.assertEqual(report["analysis_type"], "numerical_framework_check")
        self.assertEqual(report["scope"], "numerical_framework_only")
        self.assertIs(report["is_engine_result"], False)
        self.assertEqual(report["status"], "pass")
        self.assertIs(report["context"]["engine_inputs_used"], False)
        self.assertEqual(report["context"]["task_id"], record["id"])
        self.assertEqual(report["context"]["task_sha256"], fingerprint(record["task"]))
        self.assertEqual(report["context"]["hardware_id"], record["task"]["hardware"]["id"])
        points = report["points"]
        self.assertGreaterEqual(len(points), 3)
        self.assertEqual(points[0]["iteration"], 0)
        self.assertEqual([point["iteration"] for point in points], list(range(len(points))))
        self.assertLess(points[-1]["residual_norm"], points[0]["residual_norm"])
        self.assertLessEqual(points[-1]["residual_norm"], report["tolerance"])
        self.assertGreater(report["nfev"], len(points))
        self.assertAlmostEqual(report["final_state"]["x"], 2., places=6)
        self.assertAlmostEqual(report["final_state"]["y"], 1., places=6)
        self.assertEqual(self.admin.get(url + "/framework-checks").json(), [check])
        downloaded = self.admin.get(url + "/framework-checks/download", params={"check_id": check["id"]})
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertIn("attachment", downloaded.headers["content-disposition"])
        self.assertEqual(downloaded.json(), check)
        restarted = Store(self.database)
        saved = restarted.one("SELECT report FROM fixed_steady_framework_checks WHERE id=?", (check["id"],))
        self.assertEqual(json.loads(saved["report"]), report)
        self.assertEqual(self.admin.get(url).json(), record)
        self.assertEqual(self.app.state.store.all("SELECT * FROM jobs"), jobs)

    def test_failed_self_check_is_preserved_but_wrong_identity_is_not(self):
        record, url = self.saved()
        failed = {"schema_version": 1, "analysis_type": "numerical_framework_check",
                  "scope": "numerical_framework_only", "is_engine_result": False,
                  "status": "fail", "reason": "iteration_limit", "points": [], "nfev": 0}
        with patch('lre_workbench.solvers.iteration.run_framework_check', return_value=deepcopy(failed)) as check_runner:
            response = self.admin.post(url + "/framework-checks", json={})
            self.assertEqual(response.status_code, 200, response.text)
            check_runner.assert_called_once_with()
        check = response.json()
        self.assertEqual(check["report"]["status"], "fail")
        self.assertEqual(check["report"]["reason"], "iteration_limit")
        self.assertFalse(check["report"]["context"]["engine_inputs_used"])
        self.assertEqual(self.admin.get(url + "/framework-checks").json(), [check])
        self.assertEqual(self.admin.get(url + "/framework-checks/download", params={"check_id": check["id"]}).json(), check)
        for field, bad in (("analysis_type", "engine"), ("scope", "engine"), ("is_engine_result", True)):
            with self.subTest(field=field), patch('lre_workbench.solvers.iteration.run_framework_check', return_value=failed | {field: bad}):
                self.assertEqual(self.admin.post(url + "/framework-checks", json={}).status_code, 500)
        self.assertEqual(self.admin.get(url + "/framework-checks").json(), [check])
        self.assertEqual(self.admin.get(url).json(), record)

    def test_self_check_accepts_only_empty_object_and_never_forwards_engine_inputs(self):
        _, url = self.saved()
        invalid = ("null", "[]", "1", "true", '""', "{", '{"settings":{}}',
                   '{"variables":[1,2]}', '{"pc":1}', '{"formula":"x+1"}')
        with patch('lre_workbench.solvers.iteration.run_framework_check', side_effect=AssertionError("invalid request must not run")) as runner:
            for body in invalid:
                with self.subTest(body=body):
                    response = self.admin.post(url + "/framework-checks", content=body,
                                               headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
            runner.assert_not_called()
        self.assertEqual(self.admin.get(url + "/framework-checks").json(), [])

    def test_cross_task_and_project_bindings_are_enforced(self):
        first, first_url = self.saved()
        _, second_url = self.saved()
        package = self.admin.get(first_url + "/data-template").json()
        self.assertEqual(self.admin.post(second_url + "/data-check", json=package).status_code, 422)
        check = self.admin.post(first_url + "/framework-checks", json={}).json()
        self.assertEqual(self.admin.get(second_url + "/framework-checks").json(), [])
        self.assertEqual(self.admin.get(second_url + "/framework-checks/download", params={"check_id": check["id"]}).status_code, 404)
        other = self.admin.post("/api/projects", json={"name": "Other project", "inputs": {}}).json()["project"]
        wrong = f"/api/projects/{other['id']}/fixed-steady/tasks/{first['id']}"
        for suffix in ("/data-template", "/framework-checks", "/framework-checks/download?check_id=" + check["id"]):
            self.assertEqual(self.admin.get(wrong + suffix).status_code, 404)
        for suffix, body in (("/data-check", package), ("/framework-checks", {})):
            self.assertEqual(self.admin.post(wrong + suffix, json=body).status_code, 404)

    def test_authentication_csrf_and_private_project_ownership_cover_new_routes(self):
        _, url = self.saved()
        check = self.admin.post(url + "/framework-checks", json={}).json()
        package = self.admin.get(url + "/data-template").json()
        reads = (url + "/data-template", url + "/framework-checks",
                 url + "/framework-checks/download?check_id=" + check["id"])
        writes = ((url + "/data-check", package), (url + "/framework-checks", {}))
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

    def test_historical_manifest_is_not_created_by_read_or_registration(self):
        record, url = self.saved()
        original_package = self.admin.get(url + "/data-template").json()
        record["task"].pop("model_data")
        serialized = json.dumps(record["task"], ensure_ascii=False, indent=2)
        self.app.state.store.execute("UPDATE fixed_steady_tasks SET task=? WHERE id=?", (serialized, record["id"]))
        self.assertEqual(self.admin.get(url + "/data-template").status_code, 409)
        self.assertEqual(self.admin.post(url + "/data-check", json=original_package).status_code, 422)
        self.assertEqual(self.admin.get(url).json(), record)
        self.assertEqual(self.app.state.store.one("SELECT task FROM fixed_steady_tasks WHERE id=?", (record["id"],))["task"], serialized)


if __name__ == "__main__":
    unittest.main()
