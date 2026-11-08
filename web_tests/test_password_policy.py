"""Creation and change use one length policy without locking out older users."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from webapp.app import create_app, password_hash

ORIGIN = "http://127.0.0.1:8766"
BASE_PASSWORD = "baseline-password-2026"


class PasswordPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app, self.admin = self.workspace("admin")
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "Administrator", "password": BASE_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.admin_id = response.json()["id"]

    def workspace(self, name):
        app = create_app(Path(self.tmp.name) / f"{name}.sqlite3",
                         queue_enabled=False, private_workspace=True)
        return app, self.client(app)

    def client(self, app):
        client = TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 54321),
                            headers={"Origin": ORIGIN})
        self.addCleanup(client.close)
        return client

    def assert_length_error(self, response, password, label="密码"):
        self.assertEqual(response.status_code, 422, response.text)
        message = f"{label}不能为空。" if not password else f"{label}最多为 128 个字符。"
        self.assertEqual(response.json(), {"detail": message})
        if password:
            self.assertNotIn(password, response.text)
        self.assertNotIn("input", response.json())

    def test_administrator_creation_accepts_nonempty_up_to_128(self):
        for size in (0, 1, 11, 12, 128, 129):
            with self.subTest(length=size):
                app, visitor = self.workspace(f"setup-{size}")
                password = "A" * size
                response = visitor.post("/api/auth/setup", json={
                    "username": "owner", "display_name": "Owner", "password": password})
                if size in (0, 129):
                    self.assert_length_error(response, password)
                    self.assertIsNone(app.state.store.one("SELECT id FROM users LIMIT 1"))
                else:
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(visitor.get("/api/auth/status").json()["user"]["username"], "owner")

    def test_member_creation_uses_the_same_boundaries_and_error_message(self):
        for size in (0, 1, 11, 12, 128, 129):
            with self.subTest(length=size):
                password = "M" * size
                username = f"member{size}"
                response = self.admin.post("/api/members", json={
                    "username": username, "display_name": "Member", "password": password})
                if size in (0, 129):
                    self.assert_length_error(response, password)
                    self.assertIsNone(self.app.state.store.one("SELECT id FROM users WHERE username=?", (username,)))
                else:
                    self.assertEqual(response.status_code, 200, response.text)
                    member = self.client(self.app)
                    self.assertEqual(member.post("/api/auth/login", json={
                        "username": username, "password": password}).status_code, 200)

    def test_password_change_uses_the_same_boundaries_and_preserves_session_on_validation_error(self):
        current = BASE_PASSWORD
        for size in (0, 1, 11, 12, 128, 129):
            with self.subTest(length=size):
                password = "N" * size
                response = self.admin.post("/api/auth/change-password", json={
                    "current_password": current, "new_password": password})
                if size in (0, 129):
                    self.assert_length_error(response, password, "新密码")
                    self.assertEqual(self.admin.get("/api/auth/status").json()["user"]["id"], self.admin_id)
                else:
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIsNone(self.admin.get("/api/auth/status").json()["user"])
                    current = password
                    self.assertEqual(self.admin.post("/api/auth/login", json={
                        "username": "admin", "password": current}).status_code, 200)

    def test_existing_10_character_password_can_login_and_change(self):
        legacy = "oldpass123"
        self.assertEqual(len(legacy), 10)
        # A temporary fixture represents an existing account. Updating the
        # creation policy must not alter its hash or require a password reset.
        self.app.state.store.execute("UPDATE users SET password_hash=? WHERE id=?",
                                     (password_hash(legacy), self.admin_id))
        visitor = self.client(self.app)
        response = visitor.post("/api/auth/login", json={"username": "admin", "password": legacy})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(visitor.post("/api/auth/change-password", json={
            "current_password": legacy, "new_password": "new-password-12"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
