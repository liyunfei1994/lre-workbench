"""Failed initialization remains diagnosable without claiming a design result."""
from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from webapp import worker
from webapp.app import create_app
from webapp.solver import EngineInputs, initialization_failure_result
from web_tests.test_workspace import client, TEST_PASSWORD
from lre_workbench.solvers.attempt import SolverInitializationError


def failure_fixture():
    return {
        "schema_version": 1, "status": "failed", "stage": "initialization",
        "formal_iteration_started": False,
        "attempted_inputs": {"ox_preburner_mr_target": 60.0},
        "variable_names": ["总质量流量"], "variable_units": ["kg/s"], "x": [700.0],
        "counts": {"forward_attempted": 1, "forward_completed": 1,
                   "inversion_attempted": 1, "inversion_completed": 0,
                   "property_attempted": None, "property_completed": None},
        "steps": [{"id": "ox_temperature_inversion", "label": "富氧预燃室温度反求",
                   "component": "富氧预燃室", "status": "failed",
                   "attempted_inputs": {"target_temperature_K": 750.0},
                   "error_type": "ValueError", "message": "受控测试：物性温区越界",
                   "calls_attempted": 1, "calls_completed": 0}],
        "failure": {"stage": "ox_temperature_inversion", "component": "富氧预燃室",
                    "error_type": "ValueError", "message": "受控测试：物性温区越界"},
        "elapsed_seconds": .05,
    }


class InitializationFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "workspace.sqlite3"
        self.app = create_app(self.db, queue_enabled=False)
        self.api = client(self.app)
        self.addCleanup(self.api.close)
        self.assertEqual(self.api.post("/api/auth/setup", json={"username": "admin",
            "display_name": "初始化回归", "password": TEST_PASSWORD}).status_code, 200)
        response = self.api.post("/api/projects", json={"name": "初始化失败回归",
            "inputs": asdict(EngineInputs())})
        self.assertEqual(response.status_code, 200, response.text)
        self.project = response.json()["project"]
        response = self.api.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.job_id = response.json()["id"]
        self.store = self.app.state.store
        self.store.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job_id,))
        self.job = self.store.one("SELECT * FROM jobs WHERE id=?", (self.job_id,))

    def test_real_worker_persists_failure_and_api_returns_original_attempt(self):
        diagnostic = failure_fixture()
        before = deepcopy(diagnostic)
        error = SolverInitializationError("初始化失败", diagnostic)
        with patch.object(worker.sys, "argv", ["worker", "--database", str(self.db), "--job", self.job_id]), \
             patch.object(worker.threading, "Thread"), \
             patch.object(worker.faulthandler, "dump_traceback_later"), \
             patch.object(worker.faulthandler, "cancel_dump_traceback_later"), \
             patch.object(worker, "science_environment", return_value={}), \
             patch.object(worker, "calculate_design_job", side_effect=error), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(worker.main(), 1)
        # A later draft edit cannot replace the inputs actually attempted.
        draft = {k: deepcopy(self.project[k]) for k in ("name", "description", "revision", "inputs")}
        draft["inputs"]["ox_preburner_mr_target"] = 80.0
        self.assertEqual(self.api.put(f"/api/projects/{self.project['id']}", json=draft).status_code, 200)
        response = self.api.get(f"/api/jobs/{self.job_id}")
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["result"]["result_kind"], "initialization_failure")
        self.assertEqual(record["result"]["initialization_diagnostics"], before)
        self.assertNotIn("calculation", record["result"])
        self.assertNotIn("assessment", record["result"])
        self.assertEqual(record["result"]["submitted_inputs"], json.loads(self.job["inputs"]))
        self.assertEqual(diagnostic, before)
        self.assertIsNotNone(record["finished"])

    def test_cancellation_wins_race_with_failure(self):
        self.store.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (self.job_id,))
        before = self.store.one("SELECT * FROM jobs WHERE id=?", (self.job_id,))
        worker.persist_failure(self.store, self.job, SolverInitializationError("初始化失败", failure_fixture()))
        self.assertEqual(self.store.one("SELECT * FROM jobs WHERE id=?", (self.job_id,)), before)

    def test_ordinary_failure_preserves_existing_partial_result(self):
        partial = {"completed_points": [{"value": 1.0}]}
        self.store.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(partial), self.job_id))
        worker.persist_failure(self.store, self.job, ValueError("普通失败"))
        saved = self.store.one("SELECT * FROM jobs WHERE id=?", (self.job_id,))
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(json.loads(saved["result"]), partial)
        self.assertIn("ValueError", saved["error"])

    def test_no_diagnostic_solution_for_other_job_kind_or_exception(self):
        error = SolverInitializationError("初始化失败", failure_fixture())
        self.assertIsNone(initialization_failure_result(error, self.job | {"kind": "throttle"}))
        self.assertIsNone(initialization_failure_result(ValueError("普通失败"), self.job))

    def test_nonfinite_attempt_evidence_is_missing_not_json_nan(self):
        diagnostic = failure_fixture()
        diagnostic["attempted_inputs"]["pressure_Pa"] = float("nan")
        result = initialization_failure_result(SolverInitializationError("初始化失败", diagnostic), self.job)
        self.assertIsNone(result["initialization_diagnostics"]["attempted_inputs"]["pressure_Pa"])
        self.assertEqual(result["nonfinite_fields"], ["result.initialization_diagnostics.attempted_inputs.pressure_Pa"])
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
