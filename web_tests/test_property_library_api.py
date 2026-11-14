"""Read-only inspection is authenticated and cannot overwrite project history."""
from pathlib import Path
import tempfile
import unittest

from webapp.app import create_app
from web_tests.test_workspace import client, TEST_PASSWORD


class PropertyLibraryAPITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = create_app(Path(temp.name) / "properties.sqlite3", queue_enabled=False)
        self.api = client(self.app)
        self.addCleanup(self.api.close)
        response = self.api.post("/api/auth/setup", json={"username": "admin", "display_name": "物性资料测试", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)

    def test_requires_authentication_for_both_inspection_routes(self):
        guest = client(self.app)
        self.addCleanup(guest.close)
        self.assertEqual(guest.get("/api/property-library").status_code, 401)
        self.assertEqual(guest.post("/api/property-library/curve", json={}).status_code, 401)

    def test_catalog_and_curves_leave_saved_project_and_jobs_unchanged(self):
        response = self.api.post("/api/projects", json={"name": "资料只读检查", "inputs": {}})
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()["project"]
        route = f"/api/projects/{project['id']}"
        before = self.api.get(route).json()
        revisions = self.api.get(route + "/revisions").json()
        catalog = self.api.get("/api/property-library")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        data = next(d for d in catalog.json()["gas_datasets"] if d["id"] == "gri30_highT")
        response = self.api.post("/api/property-library/curve", json={"kind": "species", "dataset": data["id"],
            "member": "H2O", "start_K": 300, "end_K": 5000, "expected_sha256": data["sha256"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["rows"]), 61)
        self.assertEqual(self.api.get(route).json(), before)
        self.assertEqual(self.api.get(route + "/revisions").json(), revisions)
        self.assertEqual(self.api.get(f"/api/jobs?project_id={project['id']}").json(), [])

    def test_curve_requests_are_bounded_and_do_not_accept_files_or_extra_parameters(self):
        base = {"kind": "species", "dataset": "gri30_highT", "member": "OH", "start_K": 300, "end_K": 3000}
        for change in ({"points": 2000}, {"points": True}, {"start_K": "300"}, {"end_K": 9000},
                       {"dataset": "../../private.yaml"}, {"file": "private.yaml"}, {"pressure_Pa": 1e6},
                       {"expected_sha256": "0" * 64}, {"kind": "material", "dataset": "missing"}):
            with self.subTest(change=change):
                response = self.api.post("/api/property-library/curve", json=base | change)
                self.assertEqual(response.status_code, 422, response.text)
