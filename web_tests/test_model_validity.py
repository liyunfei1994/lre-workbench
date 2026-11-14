"""Known-invalid BM v1 stays readable but cannot seed new engineering work."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.model_validity import BM_EOS, saved_model_validity, require_reusable_model


def result(version="pr_boston_mathias_v1"):
    model = {"eos": BM_EOS, "eos_model": {"version": version}, "eos_model_sha256": "a" * 64}
    return {"environment": {"gas_model": model}, "calculation": {
        "inputs": {"cycle_type": "ffsc", "propellant_pair": "lox_methane", "gas_eos": BM_EOS},
        "summary": {"numerical_converged": 1, "Isp_vac_s": 374},
        "states": {"ox_preburner_outlet": {"metadata": {"model": deepcopy(model)}}}}}


class ModelValidityTests(unittest.TestCase):
    def test_withdrawn_saved_identity_is_detected_without_mutation(self):
        original = result()
        before = deepcopy(original)
        state = saved_model_validity(original)
        self.assertEqual(state["status"], "invalid_superseded")
        self.assertEqual(len(state["evidence"]), 2)
        self.assertIn("压力项被重复计入", state["reason"])
        self.assertEqual(original, before)
        self.assertTrue(saved_model_validity(json.dumps(original))["reuse_blocked"])
        station_only = deepcopy(original)
        del station_only["environment"]
        self.assertTrue(saved_model_validity(station_only)["reuse_blocked"])
        validation = {"source": {"environment": original["environment"]}}
        self.assertTrue(saved_model_validity(validation)["reuse_blocked"])

    def test_new_model_and_other_models_are_not_declared_physically_valid(self):
        for record in (None, {}, result("pr_boston_mathias_v2"), {"environment": {"gas_model": {
                "eos": "Peng-Robinson", "eos_model": "pr_boston_mathias_v1"}}}):
            state = saved_model_validity(record)
            self.assertFalse(state["reuse_blocked"])
            self.assertEqual(state["status"], "not_listed_invalid")
            require_reusable_model({"result": record})

    def test_comparison_and_handoff_entry_points_reject_before_any_recalculation(self):
        from webapp.design_scope import require_supported_source
        from webapp.eos_comparison import compare_gas_eos
        from webapp.validation import normalize_request, calculate_validation
        from webapp.cooling_design import source_data
        source = {"id": "old", "inputs": {}, "result": result()}
        with patch("webapp.eos_comparison._run") as run:
            for invoke in (lambda: require_supported_source(source), lambda: compare_gas_eos(source),
                           lambda: normalize_request({"gas_eos_comparison": {"enabled": True}}, source),
                           lambda: calculate_validation({}, source), lambda: source_data(source)):
                with self.assertRaisesRegex(ValueError, "已撤回"):
                    invoke()
            run.assert_not_called()

    def test_api_preserves_raw_history_and_blocks_new_source_requests(self):
        from webapp.app import create_app
        from webapp.store import Store
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            app = create_app(database, queue_enabled=False)
            store = Store(database)
            with client(app) as browser:
                user = browser.post("/api/auth/setup", json={"username": "admin", "display_name": "Test", "password": TEST_PASSWORD}).json()
                source_id = "10853742-55b7-4b6c-bd77-ed5ce3bf913d"
                raw = json.dumps(result(), ensure_ascii=False)
                store.execute("INSERT INTO projects VALUES('p','Model history','',16,'{}',?,0,0)", (user["id"],))
                store.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind,result) "
                              "VALUES(?,'p',16,?,'succeeded','{}',0,'design',?)", (source_id, user["id"], raw))
                before = store.one("SELECT * FROM jobs WHERE id=?", (source_id,))
                record = browser.get(f"/api/jobs/{source_id}")
                self.assertEqual(record.status_code, 200, record.text)
                self.assertTrue(record.json()["model_validity"]["reuse_blocked"])
                self.assertEqual(record.json()["result"], json.loads(raw))
                self.assertNotIn("pump_geometry", record.json())
                for endpoint, payload in (
                    ("validations", {"source_job_id": source_id, "gas_eos_comparison": {"enabled": True}}),
                    ("cooling-designs", {"source_job_id": source_id}),
                    ("fixed-steady/tasks", {"source_job_id": source_id})):
                    response = browser.post(f"/api/projects/p/{endpoint}", json=payload)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn("已撤回", response.json()["detail"])
                self.assertEqual(store.one("SELECT * FROM jobs WHERE id=?", (source_id,)), before)
                self.assertEqual(store.one("SELECT COUNT(*) AS n FROM jobs")["n"], 1)
