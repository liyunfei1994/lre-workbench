"""One job's frozen inputs, solution and pump curves remain one evidence chain."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.design_traceability import (attach_design_traceability, capture_design_traceability,
                                       design_traceability_view, source_context)
from webapp.pump_geometry import pump_geometry_view
from web_tests.test_pump_geometry import fixture


def record(key="job-a", project="project-a", revision=1):
    result = fixture()
    result["submitted_inputs"] = deepcopy(result["calculation"]["inputs"])
    result["steady_design"] = {"status": "review_required", "gates": [{"id": "numerical", "status": "pass"}],
                               "validation_status": "not_certified"}
    row = {"id": key, "project_id": project, "revision": revision, "mode": "concept_design",
           "inputs": deepcopy(result["submitted_inputs"]), "result": result}
    capture_design_traceability(result, row)
    return row


def view(row):
    geometry = pump_geometry_view(row["result"], row["id"], source_context=source_context(row))
    return design_traceability_view(row["result"], row, geometry)


class DesignTraceabilityTests(unittest.TestCase):
    def test_saved_chain_roundtrips_without_mutation_or_engineering_certification(self):
        row = json.loads(json.dumps(record()))
        before = deepcopy(row)
        trace = view(row)
        self.assertEqual(trace["status"], "verified", trace)
        self.assertEqual(trace["source"], source_context(row))
        self.assertEqual(trace["inputs"]["submitted"], row["inputs"])
        self.assertEqual(trace["inputs"]["calculation"], row["result"]["calculation"]["inputs"])
        self.assertEqual(trace["steady"]["validation_status"], "not_certified")
        self.assertEqual(trace["geometry_sha256"]["ox"], row["result"]["pump_geometry"]["pumps"]["ox"]["geometry_sha256"])
        self.assertEqual(row, before)

    def test_identical_physics_cannot_relabel_a_different_project_revision_or_job(self):
        for key, value in (("project_id", "project-b"), ("revision", 2), ("id", "job-b")):
            row = record()
            row[key] = value
            with self.subTest(key=key):
                self.assertEqual(view(row)["status"], "conflict")

    def test_each_frozen_evidence_link_detects_modification(self):
        changes = [lambda r: r["inputs"].update(thrust=999),
                   lambda r: r["result"]["submitted_inputs"].update(thrust=999),
                   lambda r: r["result"]["calculation"]["inputs"].update(thrust=999),
                   lambda r: r["result"]["calculation"].update(summary={"thrust_N": 999}),
                   lambda r: r["result"]["calculation"]["components"]["ox_pump"]["outputs"].update(power_W=999),
                   lambda r: r["result"]["pump_geometry"]["pumps"]["ox"]["meridional"]["hub"][0].__setitem__(1, 999),
                   lambda r: r["result"]["steady_design"].update(status="pass"),
                   lambda r: r["result"]["design_traceability"]["inputs"]["submitted"].update(thrust=999)]
        for change in changes:
            row = record()
            change(row)
            with self.subTest(change=changes.index(change)):
                self.assertEqual(view(row)["status"], "conflict")

    def test_conflict_blocks_both_pumps_and_steady_handoff_without_rewriting_raw_result(self):
        row = record()
        row["project_id"] = "project-b"
        before = deepcopy(row["result"])
        row["pump_geometry"] = pump_geometry_view(row["result"], row["id"], source_context=source_context(row))
        row["steady_design"] = deepcopy(row["result"]["steady_design"])
        attach_design_traceability(row)
        self.assertEqual(row["steady_design"]["status"], "blocked")
        self.assertTrue(all(p["export_allowed"] is False for p in row["pump_geometry"]["pumps"].values()))
        self.assertTrue(all(h["status"] == "conflict" for h in row["pump_geometry"]["design_handoff"]["pumps"].values()))
        self.assertEqual(row["result"], before)

    def test_legacy_projection_is_explicit_and_never_reads_current_project(self):
        row = record()
        del row["result"]["design_traceability"]
        trace = view(row)
        self.assertEqual(trace["status"], "legacy")
        self.assertIsNone(trace["evidence_sha256"])
        self.assertIn("历史记录", trace["note"])

    def test_legal_input_normalization_is_not_a_cross_project_conflict(self):
        row = record()
        row["inputs"]["wall_thermal_dataset_id"] = ""
        row["result"]["submitted_inputs"]["wall_thermal_dataset_id"] = "grcop42_chen2023_lpbf_v1"
        capture_design_traceability(row["result"], row)
        self.assertEqual(view(row)["status"], "verified")
        del row["result"]["design_traceability"]
        self.assertEqual(view(row)["status"], "legacy")

    def test_legacy_design_targets_cannot_come_from_another_job(self):
        row = record()
        del row["result"]["design_traceability"]
        row["inputs"]["thrust"] = 1000
        row["result"]["submitted_inputs"]["thrust"] = 2000
        self.assertEqual(view(row)["status"], "conflict")
        del row["result"]["submitted_inputs"]
        row["result"]["calculation"]["inputs"]["thrust"] = 2000
        self.assertEqual(view(row)["status"], "conflict")

    def test_worker_design_entry_captures_job_identity_and_original_inputs(self):
        from webapp.solver import calculate_design_job
        row = record()
        result = fixture()
        result["submitted_inputs"] = deepcopy(row["inputs"])
        job = {k: v for k, v in row.items() if k != "result"}
        job["inputs"] = json.dumps(job["inputs"])
        with patch("webapp.solver.calculate", return_value=result):
            captured = calculate_design_job(job)
        self.assertEqual(captured["design_traceability"]["source"], source_context(row))
        self.assertEqual(captured["pump_geometry"]["source"]["project_id"], row["project_id"])
        self.assertEqual(captured["design_traceability"]["inputs"]["submitted"], row["inputs"])

    def test_api_never_rebinds_another_projects_saved_result(self):
        from webapp.app import create_app
        from webapp.solver import platform_defaults
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Path(temp) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as api:
                api.post("/api/auth/setup", json={"username": "admin", "display_name": "test", "password": TEST_PASSWORD})
                projects = [api.post("/api/projects", json={"name": name, "inputs": platform_defaults()}).json()["project"]
                            for name in ("A", "B")]
                jobs = [api.post(f"/api/projects/{p['id']}/jobs", json={"revision": 1}).json()["id"] for p in projects]
                raw_row = app.state.store.one("SELECT * FROM jobs WHERE id=?", (jobs[0],))
                result = fixture()
                result["submitted_inputs"] = json.loads(raw_row["inputs"])
                capture_design_traceability(result, raw_row)
                raw = json.dumps(result)
                for key in jobs:
                    app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (raw, key))
                a, b = [api.get("/api/jobs/" + key).json() for key in jobs]
                self.assertEqual(a["design_traceability"]["status"], "verified", a["design_traceability"]["issues"])
                self.assertEqual(b["design_traceability"]["status"], "conflict")
                self.assertEqual(b["steady_design"]["status"], "blocked")
                # Draft edits do not affect the already saved job's chain.
                inputs = platform_defaults() | {"thrust": 1234567}
                api.put(f"/api/projects/{projects[0]['id']}", json={"revision": 1, "name": "A2", "inputs": inputs})
                self.assertEqual(api.get("/api/jobs/" + jobs[0]).json()["design_traceability"], a["design_traceability"])
                self.assertEqual(app.state.store.one("SELECT result FROM jobs WHERE id=?", (jobs[0],))["result"], raw)


if __name__ == "__main__":
    unittest.main()
