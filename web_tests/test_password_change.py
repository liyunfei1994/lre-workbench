"""Password changes revoke prior sessions, including concurrent login attempts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp.app import COOKIE, create_app, digest, password_hash

LOCAL = "http://127.0.0.1:8766"
PUBLIC = "https://pilot.example.org"
OLD = "previous-test-password"
NEW = "replacement-test-password"
CHANGE = "/api/auth/change-password"


class PasswordChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "pilot.sqlite3"
        self.app = create_app(self.db, queue_enabled=False, private_workspace=True)
        self.admin = self.client()
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "Administrator", "password": OLD})
        self.assertEqual(response.status_code, 200, response.text)
        self.admin_id = response.json()["id"]

    def client(self, app=None, remote=False):
        origin = PUBLIC if remote else LOCAL
        headers = {"Origin": origin}
        if remote:
            headers["X-Forwarded-Proto"] = "https"
        result = TestClient(app or self.app, base_url=origin,
                            client=("127.0.0.1", 54321), headers=headers)
        self.addCleanup(result.close)
        return result

    def login(self, client, password=OLD, username="admin"):
        return client.post("/api/auth/login", json={"username": username, "password": password})

    def change(self, client, current=OLD, new=NEW, **kwargs):
        return client.post(CHANGE, json={"current_password": current, "new_password": new}, **kwargs)

    def test_anonymous_csrf_and_invalid_requests_cannot_change_password(self):
        visitor = self.client()
        self.assertEqual(self.change(visitor).status_code, 401)
        self.assertEqual(self.change(self.admin, headers={"Origin": "https://evil.example"}).status_code, 403)
        for payload in ({"current_password": OLD, "new_password": ""},
                        {"current_password": OLD, "new_password": "a" * 129},
                        {"current_password": OLD, "new_password": NEW, "user_id": "someone-else"},
                        {"new_password": NEW}):
            with self.subTest(payload_keys=list(payload)):
                self.assertEqual(self.admin.post(CHANGE, json=payload).status_code, 422)
        self.assertEqual(self.change(self.admin, current="incorrect").status_code, 400)
        self.assertEqual(self.change(self.admin, new=OLD).status_code, 400)
        self.assertEqual(self.admin.get("/api/auth/status").json()["user"]["id"], self.admin_id)
        self.assertIsNone(self.app.state.store.one("SELECT * FROM account_security"))
        self.assertEqual(self.login(visitor).status_code, 200)

    def test_change_revokes_every_own_session_but_preserves_other_user(self):
        second = self.client()
        self.assertEqual(self.login(second).status_code, 200)
        response = self.admin.post("/api/members", json={
            "username": "member", "display_name": "Member", "password": OLD})
        self.assertEqual(response.status_code, 200)
        member = self.client()
        self.assertEqual(self.login(member, username="member").status_code, 200)
        before = time.time()
        response = self.change(self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True})
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertIsNone(self.admin.get("/api/auth/status").json()["user"])
        self.assertEqual(second.get("/api/projects").status_code, 401)
        self.assertEqual(member.get("/api/projects").status_code, 200)
        self.assertEqual(self.app.state.store.one(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (self.admin_id,))["n"], 0)
        audit = self.app.state.store.one("SELECT * FROM account_security WHERE user_id=?", (self.admin_id,))
        self.assertEqual(set(audit), {"user_id", "password_changed_at"})
        self.assertGreaterEqual(audit["password_changed_at"], before)
        self.assertEqual(self.login(second).status_code, 401)
        self.assertEqual(self.login(second, NEW).status_code, 200)
        restarted = create_app(self.db, queue_enabled=False)
        fresh = self.client(restarted)
        self.assertEqual(self.login(fresh, NEW).status_code, 200)
        self.assertEqual(restarted.state.store.one("SELECT * FROM account_security"), audit)

    def test_member_can_change_only_their_own_password(self):
        self.assertEqual(self.admin.post("/api/members", json={
            "username": "member", "display_name": "Member", "password": OLD}).status_code, 200)
        member = self.client()
        self.assertEqual(self.login(member, username="member").status_code, 200)
        self.assertEqual(self.change(member).status_code, 200)
        self.assertEqual(self.admin.get("/api/members").status_code, 200)
        self.assertIsNone(self.app.state.store.one(
            "SELECT * FROM account_security WHERE user_id=?", (self.admin_id,)))
        self.assertEqual(self.login(member, NEW, "member").status_code, 200)

    def test_wrong_current_password_limit_survives_relogin_and_expires(self):
        for _ in range(10):
            self.assertEqual(self.change(self.admin, current="incorrect").status_code, 400)
        self.assertEqual(self.change(self.admin).status_code, 429)
        second = self.client()
        self.assertEqual(self.login(second).status_code, 200)
        self.assertEqual(self.change(second).status_code, 429)
        self.app.state.store.execute("UPDATE login_limits SET until_time=? WHERE key=?",
                                     (time.time()-1, digest("password-change:" + self.admin_id)))
        self.assertEqual(self.change(second).status_code, 200)

    def test_remote_https_change_clears_secure_cookie_and_preserves_boundary(self):
        remote = create_app(self.db, queue_enabled=False, public_origin=PUBLIC)
        # The browser uses HTTPS while the trusted local proxy forwards HTTP.
        async def proxy(scope, receive, send):
            await remote({**scope, "scheme": "http"}, receive, send)
        visitor = self.client(proxy, remote=True)
        self.assertEqual(self.login(visitor).status_code, 200)
        self.assertEqual(self.change(visitor, headers={"Origin": "http://pilot.example.org"}).status_code, 403)
        response = self.change(visitor)
        self.assertEqual(response.status_code, 200, response.text)
        for attribute in ("Secure", "HttpOnly", "SameSite=strict", "Max-Age=0"):
            self.assertIn(attribute, response.headers["set-cookie"])
        self.assertNotIn(COOKIE, visitor.cookies)
        self.assertEqual(visitor.get("/api/projects").status_code, 401)
        response = self.login(visitor, NEW)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password_hash", response.json())
        self.assertEqual(visitor.get("/api/projects").status_code, 200)

    def test_password_update_and_session_revocation_roll_back_together(self):
        # Simulate an audit persistence failure after UPDATE/DELETE, requiring
        # all security mutations in the same transaction to roll back.
        with self.app.state.store.connect() as db:
            db.execute("CREATE TRIGGER fail_security BEFORE INSERT ON account_security "
                       "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
        with self.assertRaises(Exception):
            self.change(self.admin)
        self.assertEqual(self.admin.get("/api/projects").status_code, 200)
        fresh = self.client()
        self.assertEqual(self.login(fresh).status_code, 200)
        self.assertEqual(self.login(fresh, NEW).status_code, 401)

    def test_old_password_login_cannot_create_session_after_concurrent_change(self):
        paused, resume = threading.Event(), threading.Event()
        first = [True]
        lock = threading.Lock()
        def paused_hash(password, salt=None):
            result = password_hash(password, salt)
            with lock:
                should_pause = bool(first[0] and password == OLD and salt)
                if should_pause:
                    first[0] = False
            if should_pause:
                paused.set()
                if not resume.wait(15):
                    raise RuntimeError("Concurrent password-change test timed out")
            return result
        stale_login = self.client()
        with patch("webapp.app.password_hash", side_effect=paused_hash), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.login, stale_login)
            try:
                self.assertTrue(paused.wait(10))
                self.assertEqual(self.change(self.admin).status_code, 200)
            finally:
                resume.set()
            response = pending.result(timeout=10)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.app.state.store.one(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (self.admin_id,))["n"], 0)
        self.assertEqual(self.login(stale_login, NEW).status_code, 200)


if __name__ == "__main__":
    unittest.main()
