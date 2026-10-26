"""Integration coverage for account isolation, shared records and real workers."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from webapp.app import create_app, COOKIE
from webapp.jobs import JobQueue
from webapp.solver import input_values, presets, plain

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8765"
TEST_PASSWORD = "local-test-password-2026"


def client(app):
    return TestClient(app, base_url=URL, client=("127.0.0.1", 50000),
                      headers={"Origin": URL})


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "workspace.sqlite3"
        self.app = create_app(self.db, queue_enabled=False)
        self.admin = client(self.app)
        self.addCleanup(self.admin.close)
        response = self.admin.post("/api/auth/setup", json={
            "username":"admin", "display_name":"管理员", "password":TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.admin_id = response.json()["id"]

    def project(self, preset="tq12a_gg"):
        values = next(p["inputs"] for p in presets() if p["key"] == preset)
        response = self.admin.post("/api/projects", json={"name":"共同项目", "inputs":values})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def member(self):
        response = self.admin.post("/api/members", json={
            "username":"member", "display_name":"第二位成员", "password":TEST_PASSWORD})
        self.assertEqual(response.status_code, 200)
        other = client(self.app)
        self.addCleanup(other.close)
        response = other.post("/api/auth/login", json={"username":"MEMBER","password":TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return other

    def test_setup_cookie_password_and_logout(self):
        self.assertEqual(self.admin.post("/api/auth/setup", json={
            "username":"other","display_name":"其他人","password":TEST_PASSWORD}).status_code,409)
        row = self.app.state.store.one("SELECT * FROM users WHERE username='admin'")
        self.assertNotIn(TEST_PASSWORD,row["password_hash"])
        token = self.admin.cookies.get(COOKIE)
        self.assertNotEqual(self.app.state.store.one("SELECT token_hash FROM sessions")["token_hash"],token)
        response = self.admin.post("/api/auth/login",json={"username":"admin","password":TEST_PASSWORD})
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertEqual(self.admin.post("/api/auth/logout",json={}).status_code,200)
        self.assertEqual(self.admin.get("/api/projects").status_code,401)

    def test_shared_project_and_stale_revision_never_overwrites(self):
        member = self.member()
        original = self.project()
        self.assertEqual(member.get("/api/projects").json()[0]["id"],original["id"])
        payload = {k: original[k] for k in ("name","description","inputs","revision")}
        payload["name"] = "成员修改"
        response = member.put("/api/projects/"+original["id"],json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["project"]["revision"],2)
        payload["name"]="过期修改"
        self.assertEqual(self.admin.put("/api/projects/"+original["id"],json=payload).status_code,409)
        latest = self.admin.get("/api/projects/"+original["id"]).json()
        self.assertEqual(latest["name"],"成员修改")
        old = self.admin.get(f"/api/projects/{original['id']}/revisions/1").json()
        self.assertEqual(old["name"],"共同项目")
        self.assertEqual(len(self.admin.get(f"/api/projects/{original['id']}/revisions").json()),2)

    def test_job_snapshot_and_cancel_permissions(self):
        member = self.member()
        p = self.project()
        job = self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).json()
        payload = {k:p[k] for k in ("name","description","inputs","revision")}
        payload["inputs"]["thrust"] *= 1.1
        self.assertEqual(member.put("/api/projects/"+p["id"],json=payload).status_code,200)
        snapshot = member.get("/api/jobs/"+job["id"]).json()
        self.assertNotEqual(snapshot["inputs"]["thrust"], payload["inputs"]["thrust"])
        self.assertEqual(snapshot["revision"],1)
        self.assertEqual(member.post(f"/api/jobs/{job['id']}/cancel",json={}).status_code,403)
        self.assertEqual(self.admin.post(f"/api/jobs/{job['id']}/cancel",json={}).status_code,200)
        self.assertEqual(self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).status_code,409)
        self.assertEqual(member.get("/api/members").status_code,403)
        self.assertEqual(member.post("/api/members",json={}).status_code,403)

    def test_unauthenticated_and_csrf_host_body_boundaries(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        self.assertEqual(guest.get("/api/projects").status_code,401)
        self.assertEqual(guest.get("/api/info").status_code,401)
        self.assertEqual(self.admin.post("/api/projects",json={},headers={"Origin":"https://evil.example"}).status_code,403)
        self.assertEqual(self.admin.post("/api/projects",content="{}",headers={"Content-Type":"text/plain"}).status_code,415)
        self.assertEqual(self.admin.post("/api/projects",content='{"x":"'+("a"*200001)+'"}',
                                        headers={"Content-Type":"application/json"}).status_code,413)
        self.assertEqual(self.admin.get("/api/health",headers={"Host":"evil.example"}).status_code,400)
        response = self.admin.get("/")
        self.assertEqual(response.status_code,200)
        self.assertIn("frame-ancestors 'none'",response.headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"],"no-store")
        for name in ("app.js","results.js","style.css","workbench.css"):
            self.assertEqual(self.admin.get("/static/"+name).status_code,200)
        self.assertIn("javascript", self.admin.get("/static/app.js").headers["content-type"])
        self.assertIn("javascript", self.admin.get("/static/results.js").headers["content-type"])
        self.assertIn("text/css", self.admin.get("/static/workbench.css").headers["content-type"])
        self.assertEqual(self.admin.get("/static/missing.js").status_code, 404)

    def test_invalid_inputs_rejected_and_queue_bounded(self):
        for inputs in ({"pc":-1},{"thrust":True},{"unknown_field":1},{"gg_layout":"other"},
                       {"cycle_type":"other"},{"of_ratio":"3.4"}):
            self.assertEqual(self.admin.post("/api/projects",json={"name":"invalid","inputs":inputs}).status_code,422)
        p = self.project()
        for _ in range(3):
            self.assertEqual(self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).status_code,200)
        self.assertEqual(self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).status_code,429)

    def test_login_rate_limit_and_missing_origin(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        for _ in range(10):
            self.assertEqual(guest.post("/api/auth/login",json={"username":"admin","password":"wrong"}).status_code,401)
        self.assertEqual(guest.post("/api/auth/login",json={"username":"admin","password":TEST_PASSWORD}).status_code,429)
        guest.headers.pop("origin")
        self.assertEqual(guest.post("/api/auth/logout",json={}).status_code,403)

    def test_database_persists_across_app_restart(self):
        p = self.project()
        with client(create_app(self.db,queue_enabled=False)) as fresh:
            self.assertTrue(fresh.get("/api/auth/status").json()["initialized"])
            self.assertEqual(fresh.post("/api/auth/login",json={"username":"admin","password":TEST_PASSWORD}).status_code,200)
            self.assertEqual(fresh.get("/api/projects").json()[0]["id"],p["id"])

    def test_info_exposes_desktop_parameters_and_result_labels(self):
        response = self.admin.get("/api/info")
        self.assertEqual(response.status_code, 200, response.text)
        schema = response.json()["ui_schema"]
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        self.assertEqual(set(fields), set(schema["defaults"]))
        self.assertEqual(fields["pc"]["unit"], "MPa")
        self.assertEqual(fields["pc"]["factor"], 1e-6)
        self.assertIn("gg", schema["overview_groups"])
        self.assertEqual(schema["component_labels"]["gas_generator"], "燃气发生器（富燃）")

    def test_queue_lock_and_recovery(self):
        p = self.project()
        job = self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).json()
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?",(job["id"],))
        queue = JobQueue(self.app.state.store)
        queue.start()
        try:
            other = JobQueue(self.app.state.store)
            with self.assertRaisesRegex(RuntimeError,"已有"):
                other.start()
            self.assertEqual(self.app.state.store.one("SELECT status FROM jobs")["status"],"interrupted")
        finally:
            queue.stop()

    def test_worker_timeout_and_cancelled_job_not_executed(self):
        p = self.project()
        first = self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).json()
        self.admin.post(f"/api/jobs/{first['id']}/cancel",json={})
        second = self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).json()
        queue = JobQueue(self.app.state.store,timeout=0)
        queue.start()
        try:
            end = time.monotonic()+15
            while time.monotonic()<end:
                status = self.app.state.store.one("SELECT status FROM jobs WHERE id=?",(second["id"],))["status"]
                if status=="timed_out": break
                time.sleep(.1)
            self.assertEqual(status,"timed_out")
            self.assertEqual(self.app.state.store.one("SELECT status,started FROM jobs WHERE id=?",(first["id"],)),
                             {"status":"cancelled","started":None})
        finally: queue.stop()

    def test_worker_controller_eof_terminates_process(self):
        p = self.project()
        job = self.admin.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).json()
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?",(job["id"],))
        with subprocess.Popen([sys.executable,"-m","webapp.worker","--database",str(self.db),
                               "--job",job["id"]],cwd=ROOT,stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as process:
            process.stdin.close()
            try:
                self.assertEqual(process.wait(timeout=20),70)
            finally:
                if process.poll() is None: process.kill()

    def test_real_calculation_visible_to_two_accounts(self):
        member = self.member()
        queue = JobQueue(self.app.state.store,timeout=180)
        queue.start()
        try:
            for key in ("tq12a_gg","raptor3_ffsc","merlin1d_gg"):
                with self.subTest(preset=key):
                    p = self.project(key)
                    job = member.post(f"/api/projects/{p['id']}/jobs",json={"revision":1}).json()
                    end = time.monotonic()+190
                    while time.monotonic()<end:
                        record = self.admin.get("/api/jobs/"+job["id"]).json()
                        if record["status"] not in ("queued","running"): break
                        time.sleep(.25)
                    self.assertEqual(record["status"],"succeeded",record.get("error"))
                    result = record["result"]
                    physics = result["environment"]["physics"]
                    self.assertEqual((physics["chemistry"], physics["properties"]), ("Cantera", "CoolProp"))
                    self.assertEqual(physics["propellant_pair"], p["inputs"]["propellant_pair"])
                    self.assertEqual(result["propellant_model"]["id"], p["inputs"]["propellant_pair"])
                    self.assertEqual(physics["fuel_species"], "c12h26" if key == "merlin1d_gg" else "CH4")
                    expected_data = "nDodecane_Reitz.yaml" if key == "merlin1d_gg" else p["inputs"].get("methane_thermo_dataset", "gri30") + ".yaml"
                    self.assertEqual(physics["mechanism"], expected_data)
                    self.assertGreater(result["calculation"]["summary"]["Isp_s"],0)
                    self.assertEqual(result["submitted_inputs"],p["inputs"])
                    self.assertIn("numerical",result["assessment"])
                    self.assertIn("checks",result["assessment"])
                    self.assertEqual(member.get("/api/jobs/"+job["id"]).json()["result"],result)
                    exported = member.get("/api/jobs/"+job["id"]+"/download")
                    self.assertIn("attachment",exported.headers["content-disposition"])
                    self.assertEqual(exported.json(),record)
                    json.dumps(result,allow_nan=False)
        finally: queue.stop()


class SerializationTests(unittest.TestCase):
    def test_nonfinite_values_rejected_or_marked_missing(self):
        for v in (float("nan"),float("inf"),-float("inf")):
            with self.assertRaises(ValueError): input_values({"pc":v})
        missing = []
        self.assertEqual(plain({"bad":float("nan"),"valid":12},missing),{"bad":None,"valid":12})
        self.assertEqual(missing,["result.bad"])


if __name__ == "__main__":
    unittest.main()
