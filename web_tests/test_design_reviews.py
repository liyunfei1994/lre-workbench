"""Persist parameter review decisions without treating defaults as confirmed."""
from pathlib import Path
import tempfile
import unittest

from webapp.app import create_app
from webapp.solver import presets
from web_tests.test_workspace import client, TEST_PASSWORD


class DesignReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name) / "workspace.sqlite3"
        self.app = create_app(self.database, queue_enabled=False)
        self.admin = client(self.app)
        self.addCleanup(self.admin.close)
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "管理员", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.inputs = next(p["inputs"] for p in presets() if p["key"] == "raptor3_ffsc")
        self.project = self.create_project()
        self.url = f"/api/projects/{self.project['id']}"

    def create_project(self, reviews=None):
        payload = {"name": "参数确认测试", "inputs": self.inputs}
        if reviews is not None:
            payload["design_reviews"] = reviews
        response = self.admin.post("/api/projects", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def review(self, group="cycle", signature="[1,[],[]]", kind="confirmed"):
        return {group: {"kind": kind, "signature": signature}}

    def confirm(self, reviews, revision=1):
        return self.admin.put(self.url + "/design-reviews", json={
            "revision": revision, "design_reviews": reviews})

    def save(self, reviews=None, revision=1):
        payload = {key: self.project[key] for key in ("name", "description", "inputs")}
        payload["revision"] = revision
        if reviews is not None:
            payload["design_reviews"] = reviews
        return self.admin.put(self.url, json=payload)

    def test_review_survives_reopen_without_changing_inputs_or_project_revision(self):
        reviews = self.review()
        self.assertTrue(self.admin.get("/api/info").json()["design_review_persistence"])
        self.assertEqual(self.project["design_reviews"], {})
        result = self.confirm(reviews)
        self.assertEqual(result.status_code, 200, result.text)
        reviewed = result.json()["project"]
        self.assertEqual(reviewed, self.project | {"design_reviews": reviews})
        with client(create_app(self.database, queue_enabled=False)) as reopened:
            self.assertEqual(reopened.post("/api/auth/login", json={
                "username": "admin", "password": TEST_PASSWORD}).status_code, 200)
            self.assertEqual(reopened.get(self.url).json(), reviewed)
            self.assertEqual(len(reopened.get(self.url + "/revisions").json()), 1)
            self.assertEqual(reopened.get(self.url + "/revisions/1").json()["design_reviews"], reviews)
        other = self.create_project()
        self.assertEqual(other["design_reviews"], {})

    def test_independent_confirmations_merge_and_group_can_be_reconfirmed(self):
        first = self.review()
        second = self.review("design", "[1,[],[5]]")
        self.assertEqual(self.confirm(first).status_code, 200)
        self.assertEqual(self.confirm(second).json()["project"]["design_reviews"], first | second)
        updated = self.review(signature="[2,[],[]]")
        self.assertEqual(self.confirm(updated).json()["project"]["design_reviews"], updated | second)

    def test_full_save_replaces_reviews_and_old_revisions_keep_their_record(self):
        old = self.review()
        new = self.review("design", "[1,[],[10]]", "preset")
        self.assertEqual(self.confirm(old).status_code, 200)
        result = self.save(new)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["project"]["revision"], 2)
        self.assertEqual(result.json()["project"]["design_reviews"], new)
        self.assertEqual(self.admin.get(self.url + "/revisions/1").json()["design_reviews"], old)
        self.assertEqual(self.admin.get(self.url + "/revisions/2").json()["design_reviews"], new)
        # Old clients omitting metadata and explicit custom resets both leave
        # the next input revision unconfirmed rather than carrying stale flags.
        result = self.save(revision=2)
        self.assertEqual(result.json()["project"]["design_reviews"], {})
        self.assertEqual(self.confirm(new, revision=3).status_code, 200)
        self.assertEqual(self.save({}, revision=3).json()["project"]["design_reviews"], {})
        seeded = self.create_project(new)
        self.assertEqual(seeded["design_reviews"], new)

    def test_stale_revision_rejected_without_mutating_saved_reviews(self):
        self.assertEqual(self.save(self.review()).status_code, 200)
        before = self.admin.get(self.url).json()
        result = self.confirm(self.review("design"), revision=1)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.admin.get(self.url).json(), before)
        result = self.admin.put("/api/projects/missing/design-reviews", json={
            "revision": 1, "design_reviews": self.review()})
        self.assertEqual(result.status_code, 404)

    def test_existing_database_upgrades_to_empty_reviews(self):
        # Model a database written before review metadata existed.
        self.app.state.store.execute("DROP TABLE design_group_reviews")
        with client(create_app(self.database, queue_enabled=False)) as upgraded:
            upgraded.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
            self.assertEqual(upgraded.get(self.url).json()["design_reviews"], {})
            self.assertEqual(upgraded.get(self.url + "/revisions/1").json()["design_reviews"], {})
            result = upgraded.put(self.url + "/design-reviews", json={
                "revision": 1, "design_reviews": self.review()})
            self.assertEqual(result.status_code, 200, result.text)

    def test_invalid_review_payloads_are_rejected_and_merge_size_is_bounded(self):
        valid = {"revision": 1, "design_reviews": self.review()}
        bad_reviews = [None, [], {"bad key": self.review()["cycle"]},
                       self.review(signature=""), self.review(signature="x" * 100_001),
                       self.review(kind="ready"), {"cycle": {"kind": "confirmed", "signature": 12}},
                       {"cycle": {"kind": "confirmed", "signature": "s", "extra": True}},
                       {f"group_{i}": {"kind": "confirmed", "signature": "s"} for i in range(101)}]
        for reviews in bad_reviews:
            with self.subTest(review_type=type(reviews).__name__):
                response = self.admin.put(self.url + "/design-reviews", json=valid | {"design_reviews": reviews})
                self.assertEqual(response.status_code, 422, response.text)
        for revision in (None, 0, True, "1"):
            self.assertEqual(self.admin.put(self.url + "/design-reviews", json=valid | {
                "revision": revision}).status_code, 422)
        self.assertEqual(self.admin.get(self.url).json()["design_reviews"], {})
        first = self.review(signature="a" * 100_000)
        self.assertEqual(self.confirm(first).status_code, 200)
        self.assertEqual(self.confirm(self.review("design", "b" * 100_000)).status_code, 422)
        self.assertEqual(self.admin.get(self.url).json()["design_reviews"], first)

    def test_write_requires_authentication_and_respects_private_project_access(self):
        with client(self.app) as guest:
            self.assertEqual(guest.put(self.url + "/design-reviews", json={
                "revision": 1, "design_reviews": self.review()}).status_code, 401)
        self.assertEqual(self.admin.post("/api/members", json={
            "username": "member", "display_name": "成员", "password": TEST_PASSWORD}).status_code, 200)
        private = create_app(self.database, queue_enabled=False, private_workspace=True)
        with client(private) as member:
            member.post("/api/auth/login", json={"username": "member", "password": TEST_PASSWORD})
            self.assertEqual(member.get(self.url).status_code, 404)
            self.assertEqual(member.put(self.url + "/design-reviews", json={
                "revision": 1, "design_reviews": self.review()}).status_code, 404)
        self.assertEqual(self.admin.get(self.url).json()["design_reviews"], {})


if __name__ == "__main__":
    unittest.main()
