"""A local personal workspace preserves data while excluding every other user."""
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from webapp.access import PUBLIC_ENDPOINTS
from webapp.app import COOKIE, create_app, digest

ORIGIN = "http://127.0.0.1:8765"
PASSWORD = "personal-workspace-test-password"
NEW_PASSWORD = "replacement-personal-test-password"


class PersonalWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "workspace.sqlite3"
        self.shared = create_app(self.db, queue_enabled=False)
        self.owner = self.client(self.shared)
        response = self.owner.post("/api/auth/setup", json={
            "username": "owner", "display_name": "Owner", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.owner_id = response.json()["id"]
        response = self.owner.post("/api/members", json={
            "username": "member", "display_name": "Member", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.member_id = response.json()["id"]
        self.member = self.client(self.shared)
        self.assertEqual(self.login(self.member, "member").status_code, 200)
        self.project = self.member.post("/api/projects", json={
            "name": "Preserved historical project", "inputs": {}}).json()["project"]
        self.job = self.member.post(f"/api/projects/{self.project['id']}/jobs", json={"revision": 1}).json()

    def client(self, app):
        result = TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 54321), headers={"Origin": ORIGIN})
        self.addCleanup(result.close)
        return result

    def login(self, client, username="owner", password=PASSWORD):
        return client.post("/api/auth/login", json={"username": username, "password": password})

    def snapshot(self):
        store = self.shared.state.store
        return {"settings": store.all("SELECT * FROM workspace_settings ORDER BY key"),
                "users": store.all("SELECT id,role FROM users ORDER BY id"),
                "sessions": store.all("SELECT user_id,COUNT(*) AS n FROM sessions GROUP BY user_id ORDER BY user_id"),
                "projects": store.all("SELECT id,revision FROM projects ORDER BY id"),
                "revisions": store.all("SELECT project_id,revision FROM revisions ORDER BY project_id,revision"),
                "jobs": store.all("SELECT id,status FROM jobs ORDER BY id")}

    def personal(self):
        return create_app(self.db, queue_enabled=False, personal_workspace=True)

    def test_transition_preserves_work_and_owner_sessions_then_persists_without_flag(self):
        before = self.snapshot()
        app = self.personal()
        after = self.snapshot()
        for key in ("users", "projects", "revisions", "jobs"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after["sessions"], [{"user_id": self.owner_id, "n": 1}])
        self.assertEqual(app.state.store.one("SELECT value FROM workspace_settings WHERE key='personal_owner_id'")["value"], self.owner_id)
        reopened = create_app(self.db, queue_enabled=False)
        owner = self.client(reopened)
        owner.cookies.update(self.owner.cookies)
        for endpoint in ("/api/health", "/api/auth/status", "/api/info"):
            self.assertEqual(owner.get(endpoint).json()["workspace_access"], "personal")
        self.assertTrue(reopened.state.personal_workspace)
        self.assertFalse(reopened.state.private_workspace)
        self.assertEqual(owner.get("/api/projects").json()[0]["id"], self.project["id"])
        self.assertEqual(owner.get(f"/api/jobs/{self.job['id']}").status_code, 200)

    def test_old_nonowner_session_and_fresh_login_cannot_access_private_apis(self):
        app = self.personal()
        visitor = self.client(app)
        visitor.cookies.update(self.member.cookies)
        self.assertIsNone(visitor.get("/api/auth/status").json()["user"])
        self.assertEqual(self.login(visitor, "member").status_code, 401)
        # Even a surviving/imported old session cannot bypass the owner check.
        app.state.store.execute("INSERT INTO sessions VALUES(?,?,?)",
                                 (digest(self.member.cookies.get(COOKIE)), self.member_id, time.time()+3600))
        checked = 0
        for route in app.routes:
            if not route.path.startswith("/api/") or route.path in PUBLIC_ENDPOINTS:
                continue
            for method in route.methods:
                with self.subTest(method=method, route=route.path):
                    response = visitor.request(method, route.path, json={})
                    self.assertEqual(response.status_code, 401, response.text)
                    checked += 1
        self.assertGreater(checked, 45)
        self.assertIsNone(visitor.get("/api/auth/status").json()["user"])

    def test_owner_can_edit_preserved_data_and_change_password_but_cannot_manage_members(self):
        app = self.personal()
        owner = self.client(app)
        self.assertEqual(self.login(owner).status_code, 200)
        self.assertEqual(owner.get("/api/members").status_code, 403)
        self.assertEqual(owner.post("/api/members", json={
            "username": "third", "display_name": "Third", "password": PASSWORD}).status_code, 403)
        self.assertIsNone(app.state.store.one("SELECT id FROM users WHERE username='third'"))
        payload = {key: self.project[key] for key in ("name", "description", "inputs", "revision")}
        payload["name"] = "Owner revised historical project"
        response = owner.put(f"/api/projects/{self.project['id']}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project"]["revision"], 2)
        self.assertEqual(owner.post("/api/projects", json={"name": "New personal work", "inputs": {}}).status_code, 200)
        self.assertEqual(owner.post("/api/auth/change-password", json={
            "current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code, 200)
        self.assertEqual(owner.get("/api/projects").status_code, 401)
        self.assertEqual(self.login(owner, password=NEW_PASSWORD).status_code, 200)
        self.assertEqual(owner.get("/api/projects").status_code, 200)

    def test_empty_personal_workspace_binds_first_setup_and_cannot_be_reinitialized(self):
        path = Path(self.tmp.name) / "empty.sqlite3"
        app = create_app(path, queue_enabled=False, personal_workspace=True)
        client = self.client(app)
        self.assertEqual(client.get("/api/auth/status").json()["workspace_access"], "personal")
        self.assertFalse(client.get("/api/auth/status").json()["initialized"])
        response = client.post("/api/auth/setup", json={
            "username": "first", "display_name": "First", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        owner_id = response.json()["id"]
        self.assertEqual(app.state.store.one("SELECT value FROM workspace_settings WHERE key='personal_owner_id'")["value"], owner_id)
        self.assertEqual(client.post("/api/auth/setup", json={
            "username": "second", "display_name": "Second", "password": PASSWORD}).status_code, 409)
        restarted = create_app(path, queue_enabled=False)
        fresh = self.client(restarted)
        self.assertEqual(self.login(fresh, "first").status_code, 200)
        self.assertEqual(fresh.get("/api/members").status_code, 403)

    def test_ambiguous_or_changed_owner_is_rejected_without_data_mutation(self):
        for mutate in (
                lambda: self.shared.state.store.execute("UPDATE users SET role='admin' WHERE id=?", (self.member_id,)),
                lambda: self.shared.state.store.execute("UPDATE users SET role='member' WHERE id=?", (self.owner_id,))):
            mutate()
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "唯一管理员"):
                self.personal()
            self.assertEqual(self.snapshot(), before)
            self.shared.state.store.execute("UPDATE users SET role=CASE WHEN id=? THEN 'admin' ELSE 'member' END", (self.owner_id,))
        self.personal()
        self.shared.state.store.execute("UPDATE workspace_settings SET value=? WHERE key='personal_owner_id'", (self.member_id,))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "所有者"):
            create_app(self.db, queue_enabled=False)
        self.assertEqual(self.snapshot(), before)

    def test_conflicting_mode_requests_never_overwrite_existing_access_or_sessions(self):
        for options in ({"personal_workspace": True, "private_workspace": True},
                        {"personal_workspace": True, "public_origin": "https://pilot.example.org"}):
            before = self.snapshot()
            with self.assertRaises(ValueError):
                create_app(self.db, queue_enabled=False, **options)
            self.assertEqual(self.snapshot(), before)
        create_app(self.db, queue_enabled=False, private_workspace=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "试用工作区"):
            self.personal()
        self.assertEqual(self.snapshot(), before)
        # Use a different local DB for the personal-to-private direction.
        path = Path(self.tmp.name) / "personal-empty.sqlite3"
        app = create_app(path, queue_enabled=False, personal_workspace=True)
        before_settings = app.state.store.all("SELECT * FROM workspace_settings")
        for options in ({"private_workspace": True}, {"public_origin": "https://pilot.example.org"}):
            with self.assertRaisesRegex(ValueError, "个人工作区"):
                create_app(path, queue_enabled=False, **options)
            self.assertEqual(app.state.store.all("SELECT * FROM workspace_settings"), before_settings)

    def test_private_pilot_keeps_member_creation_and_member_ownership(self):
        app = create_app(self.db, queue_enabled=False, private_workspace=True)
        owner, member = self.client(app), self.client(app)
        self.assertEqual(self.login(owner).status_code, 200)
        self.assertEqual(self.login(member, "member").status_code, 200)
        self.assertEqual(owner.get("/api/members").status_code, 200)
        self.assertEqual(owner.post("/api/members", json={
            "username": "third", "display_name": "Third", "password": PASSWORD}).status_code, 200)
        self.assertEqual(member.get("/api/projects").json()[0]["id"], self.project["id"])
        self.assertEqual(member.get("/api/auth/status").json()["workspace_access"], "private")
        remote = create_app(self.db, queue_enabled=False, public_origin="https://pilot.example.org")
        self.assertTrue(remote.state.private_workspace)
        self.assertFalse(remote.state.personal_workspace)


if __name__ == "__main__":
    unittest.main()
