"""Exercise actual API authorization, including every existing nested route."""
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from webapp.access import https_origin, JOB_PARAMETERS
from webapp.app import create_app, ROOT

LOCAL = "http://127.0.0.1:8766"
PUBLIC = "https://pilot.example.org"
PASSWORD = "isolated-pilot-test-password"


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "pilot.sqlite3"
        self.app = create_app(self.db, queue_enabled=False, private_workspace=True)
        self.admin = self.client(self.app)
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "管理员", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.admin_id = response.json()["id"]
        self.alice = self.member("alice")
        self.bob = self.member("bob")
        self.original = self.project(self.admin, "管理员原始方案")
        self.own = self.project(self.alice, "Alice 自己的方案")
        self.other = self.project(self.bob, "Bob 自己的方案")

    def client(self, app, remote=False, peer="127.0.0.1"):
        headers = {"Origin": PUBLIC if remote else LOCAL}
        if remote:
            headers["X-Forwarded-Proto"] = "https"
        result = TestClient(app, base_url=PUBLIC if remote else LOCAL,
                            client=(peer, 54321), headers=headers)
        self.addCleanup(result.close)
        return result

    def member(self, username):
        response = self.admin.post("/api/members", json={
            "username": username, "display_name": username, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        result = self.client(self.app)
        self.assertEqual(result.post("/api/auth/login", json={
            "username": username, "password": PASSWORD}).status_code, 200)
        return result

    def project(self, client, name):
        response = client.post("/api/projects", json={"name": name, "inputs": {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def remote_app(self):
        return create_app(self.db, queue_enabled=False, public_origin=PUBLIC)

    def test_ownership_is_persistent_and_independent_of_last_editor(self):
        payload = {k: self.own[k] for k in ("name", "inputs", "description", "revision")}
        payload["name"] = "管理员帮助修订"
        self.assertEqual(self.admin.put("/api/projects/" + self.own["id"], json=payload).status_code, 200)
        restarted = create_app(self.db, queue_enabled=False)
        alice = self.client(restarted)
        alice.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        projects = alice.get("/api/projects").json()
        self.assertEqual([p["id"] for p in projects], [self.own["id"]])
        self.assertEqual(projects[0]["name"], payload["name"])
        self.assertEqual(len(self.admin.get("/api/projects").json()), 3)
        self.assertEqual(self.bob.get("/api/projects").json()[0]["id"], self.other["id"])
        self.assertEqual(alice.get("/api/info").json()["workspace_access"], "private")

    def test_other_project_routes_including_mutations_and_exports_are_blocked(self):
        job = self.admin.post(f"/api/projects/{self.original['id']}/jobs", json={"revision": 1}).json()
        checked = 0
        for route in self.app.routes:
            if "{" not in route.path or route.path.startswith("/static"):
                continue
            path = route.path.replace("{project_id}", self.original["id"]).replace("{revision}", "1")
            path = path.replace("{candidate_id}", "candidate-1")
            for name in JOB_PARAMETERS:
                path = path.replace("{" + name + "}", job["id"])
            self.assertNotIn("{", path, "New resource needs an authorization regression case")
            for method in route.methods:
                with self.subTest(method=method, path=path):
                    response = self.alice.request(method, path, json={"revision": 1})
                    self.assertEqual(response.status_code, 404, response.text)
                    checked += 1
        self.assertGreater(checked, 45)
        self.assertEqual(self.app.state.store.one("SELECT revision FROM projects WHERE id=?",
                                                (self.original["id"],))["revision"], 1)
        self.assertEqual(self.app.state.store.one("SELECT status FROM jobs WHERE id=?", (job["id"],))["status"], "queued")

    def test_own_parameters_jobs_and_results_work_but_admin_actions_are_forbidden(self):
        payload = {k: self.own[k] for k in ("name", "inputs", "description", "revision")}
        payload["inputs"]["pc"] *= 0.98
        response = self.alice.put("/api/projects/" + self.own["id"], json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project"]["revision"], 2)
        own_job = self.alice.post(f"/api/projects/{self.own['id']}/jobs", json={"revision": 2}).json()
        self.admin.post(f"/api/projects/{self.original['id']}/jobs", json={"revision": 1})
        self.bob.post(f"/api/projects/{self.other['id']}/jobs", json={"revision": 1})
        self.assertEqual([r["id"] for r in self.alice.get("/api/jobs").json()], [own_job["id"]])
        self.assertEqual(self.alice.get("/api/jobs", params={"project_id": self.other["id"]}).json(), [])
        record = self.alice.get("/api/jobs/" + own_job["id"])
        self.assertEqual(record.status_code, 200, record.text)
        self.assertEqual(record.json()["inputs"]["pc"], payload["inputs"]["pc"])
        self.assertEqual(self.alice.post(f"/api/jobs/{own_job['id']}/cancel", json={}).status_code, 200)
        self.assertEqual(self.alice.get("/api/members").status_code, 403)
        self.assertEqual(self.alice.post("/api/members", json={}).status_code, 403)

    def test_remote_proxy_login_cookie_and_setup_boundaries(self):
        app = self.remote_app()
        # Simulate HTTPS at the browser but plain HTTP between proxy and Uvicorn.
        async def proxy(scope, receive, send):
            await app({**scope, "scheme": "http"}, receive, send)
        visitor = self.client(proxy, remote=True)
        self.assertEqual(visitor.get("/api/projects").status_code, 401)
        response = visitor.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertEqual(visitor.get("/api/projects").json()[0]["id"], self.own["id"])
        self.assertEqual(visitor.post("/api/auth/setup", json={}).status_code, 403)
        self.assertEqual(visitor.get("/api/auth/status").json()["remote"], True)
        for headers in ({"Origin": "https://evil.example.org"}, {"Origin": "http://pilot.example.org"},
                        {"Host": "evil.example.org"}, {"Host": "pilot.example.org:9999"},
                        {"X-Forwarded-Proto": "http"}):
            self.assertEqual(visitor.post("/api/auth/logout", json={}, headers=headers).status_code, 403)
        direct = self.client(app, remote=True, peer="203.0.113.25")
        self.assertEqual(direct.get("/").status_code, 403)
        local = self.client(app)
        self.assertEqual(local.get("/api/health").status_code, 200)
        self.assertEqual(local.get("/api/auth/status").status_code, 403)

    def test_remote_login_limits_cannot_be_reset_by_spoofed_ip_headers(self):
        app = self.remote_app()
        visitor = self.client(app, remote=True)
        for i in range(10):
            response = visitor.post("/api/auth/login", json={"username": "alice", "password": "wrong"},
                                    headers={"X-Forwarded-For": f"192.0.2.{i}", "CF-Connecting-IP": f"192.0.2.{i}"})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(visitor.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code, 429)
        self.assertEqual(visitor.post("/api/auth/login", json={"username": "bob", "password": PASSWORD}).status_code, 200)
        from webapp.app import digest
        app.state.store.execute("UPDATE login_limits SET attempts=120 WHERE key=?", (digest("remote-login-budget"),))
        self.assertEqual(visitor.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code, 429)

    def test_remote_requires_local_initialization_and_separate_database(self):
        with self.assertRaisesRegex(ValueError, "初始化"):
            create_app(Path(self.tmp.name) / "empty.sqlite3", queue_enabled=False, public_origin=PUBLIC)
        for options in ({"public_origin": PUBLIC}, {"private_workspace": True}):
            with self.assertRaisesRegex(ValueError, "独立数据库"):
                create_app(ROOT / "user_data/web-workbench/workspace.sqlite3", queue_enabled=False, **options)

    def test_startup_report_is_bounded_redacted_and_same_origin(self):
        visitor = self.client(self.remote_app(), remote=True)
        payload = {"kind": "module_load", "files": [{"path": "/static/app.js", "status": 0, "kind": "network"}]}
        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            response = visitor.post("/api/startup-report", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.json()["id"], r"^[a-f0-9]{12}$")
        self.assertIn('/static/app.js', captured.output[0])
        self.assertNotIn('password', captured.output[0])
        self.assertEqual(visitor.post("/api/startup-report", json=payload,
                                     headers={"Origin": "https://evil.example.org"}).status_code, 403)
        for bad in ({**payload, "cookie": "not-accepted"},
                    {**payload, "files": payload["files"] * 41},
                    {**payload, "files": [{"path": "/static/app.js?secret=1", "status": 200, "kind": "ok"}]}):
            self.assertEqual(visitor.post("/api/startup-report", json=bad).status_code, 422)
        with self.assertLogs("uvicorn.error", level="WARNING"):
            for _ in range(5):
                self.assertEqual(visitor.post("/api/startup-report", json=payload).status_code, 200)
        self.assertEqual(visitor.post("/api/startup-report", json=payload).status_code, 429)
        self.assertEqual(self.admin.post("/api/startup-report", json=payload).status_code, 404)


class OriginTests(unittest.TestCase):
    def test_exact_https_hostname_only(self):
        self.assertEqual(https_origin("https://Pilot.example.org:443/"), PUBLIC)
        self.assertEqual(https_origin("https://pilot.example.org:8443"), PUBLIC + ":8443")
        for value in ("http://pilot.example.org", "https://*.example.org", "https://localhost",
                      "https://127.0.0.2", "https://pilot.example.org/path", "https://pilot.example.org?x=1",
                      "https://user@pilot.example.org", "https://pilot.example.org:0", "https://bad..example.org",
                      "https://-bad.example.org", "https://pilot.example.org:99999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                https_origin(value)


if __name__ == "__main__":
    unittest.main()
