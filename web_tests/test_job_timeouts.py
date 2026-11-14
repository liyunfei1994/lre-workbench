"""Bounded EOS budgets follow immutable jobs; explicit operator limits win."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from webapp.jobs import BM_EOS, JobQueue
from webapp.store import Store


class JobTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "workspace.sqlite3")
        self.store.execute("INSERT INTO users VALUES('u','test','Test','unused','admin',0)")
        for project in ("p1", "p2"):
            self.store.execute("INSERT INTO projects VALUES(?,?,?,1,?,'u',0,0)",
                               (project, project, "", json.dumps({"gas_eos": "ideal-gas"})))
        self.queue = JobQueue(self.store)

    def source(self, *, eos=BM_EOS, actual=None, status="succeeded"):
        result = {"calculation": {"states": {}}}
        if actual:
            result["calculation"]["states"]["ox_preburner_outlet"] = {"metadata": {"model": {"eos": actual}}}
        self.store.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,result,kind) "
                           "VALUES('source','p1',2,'u',?,?,0,?,'design')",
                           (status, json.dumps({"gas_eos": eos}), json.dumps(result)))

    def validation(self, **changes):
        return {"kind": "validation", "project_id": "p1", "revision": 2,
                "inputs": json.dumps({"source_job_id": "source", "gas_eos_comparison": {"enabled": True}}), **changes}

    def test_only_bm_design_and_source_bound_bm_comparison_receive_extended_budgets(self):
        self.assertEqual(self.queue.timeout_for_job({"kind": "design", "inputs": {"gas_eos": BM_EOS}}), 3600)
        for eos in ("ideal-gas", "Peng-Robinson", "unknown"):
            self.assertEqual(self.queue.timeout_for_job({"kind": "design", "inputs": {"gas_eos": eos}}), 600)
        for kind in ("cooling_design", "transient", "operating_throttle"):
            self.assertEqual(self.queue.timeout_for_job({"kind": kind, "inputs": {"gas_eos": BM_EOS}}), 600)
        self.source()
        saved = self.store.one("SELECT * FROM jobs WHERE id='source'")
        self.assertEqual(self.queue.timeout_for_job(self.validation()), 4200)
        for changes in ({"project_id": "p2"}, {"revision": 3}, {"inputs": "broken-json"},
                        {"inputs": {"source_job_id": "source", "gas_eos_comparison": {"enabled": 1}}}):
            self.assertEqual(self.queue.timeout_for_job(self.validation(**changes)), 600)
        self.assertEqual(self.store.one("SELECT * FROM jobs WHERE id='source'"), saved)
        self.store.execute("UPDATE jobs SET status='failed' WHERE id='source'")
        self.assertEqual(self.queue.timeout_for_job(self.validation()), 600)

    def test_comparison_budget_uses_saved_station_identity_like_its_solver(self):
        self.source(eos=BM_EOS, actual="Peng-Robinson")
        self.assertEqual(self.queue.timeout_for_job(self.validation()), 600)
        self.store.execute("UPDATE jobs SET inputs=?,result=? WHERE id='source'", (
            json.dumps({"gas_eos": "ideal-gas"}), json.dumps({"calculation": {"states": {
                "ox_preburner_outlet": {"metadata": {"model": {"eos": BM_EOS}}}}}})))
        self.assertEqual(self.queue.timeout_for_job(self.validation()), 4200)

    def test_explicit_limits_are_never_replaced_and_nonfinite_limits_are_rejected(self):
        self.source()
        for seconds in (0, .01, 90, 600, 8000):
            queue = JobQueue(self.store, timeout=seconds)
            self.assertEqual(queue.timeout_for_job({"kind": "design", "inputs": {"gas_eos": BM_EOS}}), seconds)
            self.assertEqual(queue.timeout_for_job(self.validation()), seconds)
            self.assertEqual(queue.timeout_policy()["bm_eos_comparison_seconds"], seconds)
            self.assertEqual(queue.timeout_policy()["mode"], "explicit_uniform_limit")
        for value in (-1, True, float("inf"), float("nan"), "3600"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                JobQueue(self.store, timeout=value)

    def test_dispatch_honors_explicit_zero_for_bm_and_preserves_prior_timeout_history(self):
        self.store.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind,result,error) "
                           "VALUES('old','p1',1,'u','timed_out','{}',0,'design','saved-partial','old 600s timeout')")
        old = deepcopy(self.store.one("SELECT * FROM jobs WHERE id='old'"))
        self.store.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                           "VALUES('bm','p1',2,'u','queued',?,1,'design')", (json.dumps({"gas_eos": BM_EOS}),))
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        queue = JobQueue(self.store, timeout=0)
        with patch("webapp.jobs.subprocess.Popen", return_value=process):
            queue.start()
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    result = self.store.one("SELECT status,error FROM jobs WHERE id='bm'")
                    if result["status"] == "timed_out":
                        break
                    time.sleep(.02)
                self.assertEqual(result["status"], "timed_out")
                self.assertIn("0 秒", result["error"])
            finally:
                queue.stop()
        process.terminate.assert_called_once()
        self.assertEqual(self.store.one("SELECT * FROM jobs WHERE id='old'"), old)

    def test_abrupt_worker_exit_retains_the_native_code_and_log(self):
        self.store.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                           "VALUES('crash','p1',2,'u','queued','{}',1,'design')")
        process = Mock(pid=6236, stdin=None)
        process.poll.return_value = 0xC0000005

        def spawn(*args, stdout, **kwargs):
            stdout.write(b"Interrupted traceback line ")
            stdout.flush()
            return process

        with (patch("webapp.jobs.subprocess.Popen", side_effect=spawn),
              patch.object(self.queue.stop_event, "wait", side_effect=(False, True))):
            self.queue.run()
        row = self.store.one("SELECT status,error,result FROM jobs WHERE id='crash'")
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["result"])
        self.assertIn("0xC0000005", row["error"])
        log = (Path(self.temp.name) / "job-logs" / "crash.log").read_text(encoding="utf-8")
        self.assertIn("Interrupted traceback line \nWorker PID 6236, exit code 3221225477 (0xC0000005).", log)
        process.terminate.assert_not_called()

    def test_api_capabilities_report_effective_default_and_explicit_policies(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client, TEST_PASSWORD
        for timeout in (None, 7):
            app = create_app(Path(self.temp.name) / f"api-{timeout}.sqlite3", queue_enabled=False, job_timeout=timeout)
            with client(app) as browser, patch("webapp.validation.capabilities", return_value={"gas_eos_comparison": {"available": True}}):
                browser.post("/api/auth/setup", json={"username": "admin", "display_name": "Test", "password": TEST_PASSWORD})
                info = browser.get("/api/info").json()
                capability = browser.get("/api/validation/capabilities").json()
            self.assertEqual(info["job_timeout_policy"], capability["job_timeout_policy"])
            self.assertEqual(info["job_timeout_seconds"], 600 if timeout is None else timeout)
            self.assertEqual(info["job_timeout_policy"]["bm_design_seconds"], 3600 if timeout is None else timeout)
            self.assertEqual(info["job_timeout_policy"]["bm_eos_comparison_seconds"], 4200 if timeout is None else timeout)
