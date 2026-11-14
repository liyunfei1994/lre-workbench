"""Durable live observations, terminal races, cursor replay and access control."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from webapp import worker
from webapp.app import create_app
from webapp.jobs import JobQueue
from webapp.monitor import append_event, monitor_page
from webapp.store import Store
from web_tests.test_workspace import client, TEST_PASSWORD


def point(iteration=0):
    return {"kind": "point", "layer": "inner", "run_id": "inner-1", "iteration": iteration,
            "context": {"continuation_attempt": 1, "preburner_iteration": 1},
            "elapsed_seconds": iteration * .1, "values": {"acceptance_norm": 10.0 / (iteration + 1)},
            "units": {"acceptance_norm": "1"}}


class MonitorStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.store = Store(self.path)
        self.store.execute("INSERT INTO users VALUES('owner','owner','Owner','unused','admin',0)")
        self.store.execute("INSERT INTO projects VALUES('project','Project','',7,'{}','owner',0,0)")
        self.store.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,started) "
                           "VALUES('job','project',7,'owner','running','{}',0,1)")
        self.job = self.store.one("SELECT * FROM jobs WHERE id='job'")

    def test_immediate_commit_finite_values_and_immutable_snapshot(self):
        event = point()
        event["values"].update({"missing": float("nan"), "overflow": float("inf")})
        event["seq"] = 900
        self.assertEqual(append_event(self.store, "job", event), 1)
        event["values"]["acceptance_norm"] = -1
        # Independent connection sees the point before any final result exists.
        page = monitor_page(Store(self.path), "job")
        self.assertEqual(page["events"][0]["values"],
                         {"acceptance_norm": 10., "missing": None, "overflow": None})
        self.assertEqual(page["events"][0]["seq"], 1)
        self.assertIsInstance(page["events"][0]["recorded"], float)
        self.assertIsNone(self.store.one("SELECT result FROM jobs WHERE id='job'")["result"])
        json.dumps(page, allow_nan=False)

    def test_cursor_replay_is_ordered_isolated_and_preserves_input_revision(self):
        for index in range(5):
            append_event(self.store, "job", point(index))
        self.store.execute("UPDATE projects SET revision=8 WHERE id='project'")
        pages, cursor = [], 0
        while True:
            page = monitor_page(self.store, "job", after=cursor, limit=2)
            self.assertEqual(page["job"]["revision"], 7)
            pages.extend(page["events"])
            cursor = page["next_seq"]
            if not page["has_more"]:
                break
        self.assertEqual([event["seq"] for event in pages], list(range(1, 6)))
        self.assertEqual([event["iteration"] for event in pages], list(range(5)))
        self.assertEqual(monitor_page(self.store, "job", after=cursor)["events"], [])
        self.assertIsNone(monitor_page(self.store, "missing"))
        for after, limit in ((-1, 1), (True, 1), (2**63, 1), (0, 0), (0, 2001)):
            with self.subTest(after=after, limit=limit), self.assertRaises(ValueError):
                monitor_page(self.store, "job", after=after, limit=limit)

    def test_terminal_races_preserve_points_and_reject_late_callbacks(self):
        append_event(self.store, "job", point())
        for status in ("cancelled", "timed_out", "interrupted", "failed", "succeeded"):
            with self.subTest(status=status):
                self.store.execute("UPDATE jobs SET status=? WHERE id='job'", (status,))
                self.assertIsNone(append_event(self.store, "job", point(1)))
                self.assertEqual(worker.persist_success(self.store, self.job, {"calculation": {}}), 0)
                self.assertEqual(worker.persist_failure(self.store, self.job, ValueError("late")), 0)
                page = monitor_page(self.store, "job")
                self.assertEqual(page["job"]["status"], status)
                self.assertEqual(len(page["events"]), 1)

    def test_worker_failure_retains_partial_result_and_atomic_terminal_observation(self):
        append_event(self.store, "job", point())
        self.store.execute("UPDATE jobs SET result=? WHERE id='job'", ('{"partial":true}',))
        worker.persist_failure(self.store, self.job, ValueError("controlled failure"))
        page = monitor_page(self.store, "job")
        self.assertEqual(page["job"]["status"], "failed")
        self.assertIn("controlled failure", page["job"]["error"])
        self.assertEqual([event["kind"] for event in page["events"]], ["point", "end"])
        self.assertEqual(page["events"][-1]["status"], "failed")
        self.assertEqual(self.store.one("SELECT result FROM jobs WHERE id='job'")["result"], '{"partial":true}')

    def test_success_is_task_completion_not_a_fabricated_converged_point(self):
        append_event(self.store, "job", point())
        worker.persist_success(self.store, self.job, {"calculation": {"converged": False}})
        page = monitor_page(self.store, "job")
        self.assertEqual(page["job"]["status"], "succeeded")
        self.assertEqual(page["events"][-1]["layer"], "task")
        self.assertEqual(page["events"][-1]["values"], {})

    def test_abrupt_process_exit_and_server_recovery_keep_committed_observations(self):
        script = ("import os,sys; from webapp.store import Store; from webapp.monitor import append_event; "
                  "append_event(Store(sys.argv[1]),'job',{'kind':'point','layer':'inner','run_id':'inner-1',"
                  "'iteration':0,'values':{'acceptance_norm':3.0}}); os._exit(17)")
        result = subprocess.run([sys.executable, "-c", script, str(self.path)],
                                cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 17, result.stderr.decode(errors="replace"))
        queue = JobQueue(Store(self.path))
        with patch("webapp.jobs.threading.Thread"):
            queue.start()
            queue.stop()
        page = monitor_page(Store(self.path), "job")
        self.assertEqual(page["job"]["status"], "interrupted")
        self.assertEqual(page["events"][0]["values"]["acceptance_norm"], 3.)


class MonitorAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.app = create_app(self.path, queue_enabled=False, private_workspace=True)
        self.store = self.app.state.store
        self.admin = self.new_client()
        response = self.admin.post("/api/auth/setup", json={"username": "admin", "display_name": "Monitor",
                                                          "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.admin.post("/api/projects", json={"name": "监控测试", "inputs": {}})
        self.assertEqual(response.status_code, 200, response.text)
        self.project = response.json()["project"]
        response = self.admin.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.job_id = response.json()["id"]
        self.store.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), self.job_id))
        self.url = f"/api/jobs/{self.job_id}/monitor"

    def new_client(self):
        api = client(self.app)
        self.addCleanup(api.close)
        return api

    def test_empty_old_record_and_incremental_terminal_replay(self):
        self.assertEqual(self.admin.get(self.url).json()["events"], [])
        for index in range(3):
            append_event(self.store, self.job_id, point(index))
        self.store.execute("UPDATE jobs SET status='timed_out',error='time limit' WHERE id=?", (self.job_id,))
        response = self.admin.get(self.url, params={"limit": 2})
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual(page["job"]["status"], "timed_out")
        self.assertTrue(page["has_more"])
        second = self.admin.get(self.url, params={"after": page["next_seq"]}).json()
        self.assertEqual([event["seq"] for event in second["events"]], [3])
        self.assertFalse(second["has_more"])
        self.assertEqual(second["job"]["revision"], 1)
        for query in ({"after": -1}, {"after": "NaN"}, {"after": 2**63}, {"limit": 0}, {"limit": 2001}):
            self.assertEqual(self.admin.get(self.url, params=query).status_code, 422)
        self.store.execute("UPDATE jobs SET kind='validation' WHERE id=?", (self.job_id,))
        self.assertEqual(self.admin.get(self.url).status_code, 404)

    def test_private_monitor_requires_login_and_project_ownership(self):
        anonymous = self.new_client()
        self.assertEqual(anonymous.get(self.url).status_code, 401)
        self.assertEqual(self.admin.post("/api/members", json={"username": "member", "display_name": "Member",
                                                            "password": TEST_PASSWORD}).status_code, 200)
        member = self.new_client()
        self.assertEqual(member.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD}).status_code, 200)
        append_event(self.store, self.job_id, point())
        self.assertEqual(member.get(self.url).status_code, 404)
        project = member.post("/api/projects", json={"name": "Owned", "inputs": {}}).json()["project"]
        owned_id = member.post(f"/api/projects/{project['id']}/jobs", json={"revision": 1}).json()["id"]
        owned = member.get(f"/api/jobs/{owned_id}/monitor")
        self.assertEqual(owned.status_code, 200, owned.text)
        self.assertEqual(owned.json()["job"]["id"], owned_id)
        self.assertEqual(owned.json()["events"], [])

    def run_worker(self, calculate):
        with patch.object(worker.sys, "argv", ["worker", "--database", str(self.path), "--job", self.job_id]), \
             patch.object(worker.threading, "Thread"), \
             patch.object(worker, "science_environment", return_value={}), \
             patch.object(worker, "calculate_design_job", side_effect=calculate), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return worker.main()

    def test_worker_streams_before_failure_and_preserves_original_version(self):
        def failing_calculation(job, progress, *, monitor_callback):
            monitor_callback(point(0))
            monitor_callback(point(1))
            observed = monitor_page(Store(self.path), self.job_id)
            self.assertEqual(observed["job"]["status"], "running")
            self.assertEqual([event["kind"] for event in observed["events"]], ["start", "point", "point"])
            raise ValueError("failure after accepted points")
        self.assertEqual(self.run_worker(failing_calculation), 1)
        page = self.admin.get(self.url).json()
        self.assertEqual(page["job"]["status"], "failed")
        self.assertEqual([event["kind"] for event in page["events"]], ["start", "point", "point", "end"])
        self.assertIn("failure after accepted points", page["job"]["error"])
        self.assertEqual(page["job"]["revision"], 1)

    def test_worker_cannot_overwrite_cancellation_during_callback(self):
        def cancelled_calculation(job, progress, *, monitor_callback):
            monitor_callback(point())
            self.store.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (self.job_id,))
            monitor_callback(point(1))
            raise RuntimeError("late worker failure")
        self.assertEqual(self.run_worker(cancelled_calculation), 1)
        page = self.admin.get(self.url).json()
        self.assertEqual(page["job"]["status"], "cancelled")
        self.assertEqual([event["kind"] for event in page["events"]], ["start", "point"])


if __name__ == "__main__":
    unittest.main()
