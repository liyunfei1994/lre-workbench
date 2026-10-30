"""The routine cooling editor must not migrate saved budget calculations."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from webapp.app import create_app
from webapp.solver import EngineInputs, input_values, presets
from webapp.ui_schema import design_ui_schema
from web_tests.test_workspace import client, TEST_PASSWORD


class CoolingEntryContractTests(unittest.TestCase):
    def test_routine_presets_use_spatial_cooling_and_budget_catalog_is_retained(self):
        self.assertEqual(EngineInputs().cooling_bartz_mode, 1)
        catalog = {item["key"]: item for item in presets()}
        for key in ("raptor3_ffsc", "tq12a_gg"):
            with self.subTest(preset=key):
                self.assertTrue(catalog[key]["routine_design"])
                self.assertEqual(catalog[key]["inputs"]["cooling_bartz_mode"], 1)
                self.assertEqual(catalog[key]["inputs"]["propellant_pair"], "lox_methane")
        legacy = catalog["merlin1d_gg"]
        self.assertFalse(legacy["routine_design"])
        self.assertEqual(legacy["inputs"]["cooling_bartz_mode"], 0)
        self.assertEqual(legacy["inputs"]["propellant_pair"], "lox_kerosene")
        self.assertTrue(all(item["inputs"]["cooling_bartz_mode"] == 1
                            for item in catalog.values() if item["routine_design"]))

    def test_saved_budget_input_normalization_keeps_complete_parameters(self):
        saved = asdict(EngineInputs(cooling_bartz_mode=0, cooling_heat_load=21e6,
                                   cooling_dp_pa=2.3e6, cooling_dp_auto=1,
                                   coolant_cp=2700, methane_thermo_dataset="gri30"))
        before = deepcopy(saved)
        normalized, _ = input_values(saved, saved_snapshot=True)
        self.assertEqual(normalized, before)
        self.assertEqual(saved, before)
        self.assertEqual(asdict(EngineInputs.from_dict(saved)), before)

    def test_read_only_mode_only_affects_routine_editor_not_api_contract(self):
        schema = design_ui_schema()
        self.assertGreaterEqual(schema["version"], 13)
        complete = {field["key"]: field for group in schema["groups"]
                    for field in group["fields"]}
        self.assertEqual(set(complete), set(asdict(EngineInputs())))
        cooling = next(group for group in schema["system_groups"] if group["id"] == "cooling")
        fields = {field["key"]: field for field in cooling["fields"]}
        self.assertTrue(fields["cooling_bartz_mode"]["read_only"])
        self.assertNotIn("choices", fields["cooling_bartz_mode"])
        self.assertEqual(complete["cooling_bartz_mode"]["type"], "number")
        for key in ("cooling_heat_load", "cooling_dp_pa"):
            with self.subTest(field=key):
                owners = [group["id"] for group in schema["system_groups"]
                          if any(field["key"] == key for field in group["fields"])]
                self.assertEqual(owners, ["cooling"])
                self.assertTrue(fields[key]["advanced"])
                self.assertEqual(fields[key]["visibility"], {
                    "all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]})


class CoolingEntryAPITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = create_app(Path(temp.name) / "workspace.sqlite3", queue_enabled=False)
        self.api = client(self.app)
        self.addCleanup(self.api.close)
        response = self.api.post("/api/auth/setup", json={
            "username": "admin", "display_name": "冷却兼容测试", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)

    def project(self, inputs):
        response = self.api.post("/api/projects", json={"name": "历史冷却方案", "inputs": inputs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def save(self, project, inputs):
        response = self.api.put("/api/projects/" + project["id"], json={
            "name": project["name"], "description": project["description"],
            "revision": project["revision"], "inputs": inputs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def test_budget_save_and_explicit_spatial_revision_keep_old_record_unchanged(self):
        budget = asdict(EngineInputs(cooling_bartz_mode=0, cooling_heat_load=22e6,
                                    cooling_dp_pa=2.5e6, coolant_cp=2800,
                                    methane_thermo_dataset="gri30"))
        original = self.project(budget)
        self.assertEqual(original["inputs"], budget)
        submitted = self.api.post(f"/api/projects/{original['id']}/jobs", json={"revision": 1})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        job_id = submitted.json()["id"]
        # A recorded budget fixture exercises storage only, without claiming a
        # new scientific calculation or asking a worker to recompute history.
        result = {"environment": {"synthetic_fixture": True}, "calculation": {
            "inputs": deepcopy(budget), "components": {"cooling_channel": {
                "outputs": {"mode": 0, "heat_load_W": 22e6, "delta_P_MPa": 2.5},
                "notes": ["指定吸热量与压降的历史记录，无沿程解。"]}}}}
        raw_result = json.dumps(result, ensure_ascii=False)
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",
                                     (raw_result, job_id))
        original_job = self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))

        unchanged = self.save(original, deepcopy(budget))
        self.assertEqual(unchanged["revision"], 2)
        self.assertEqual(unchanged["inputs"], budget)
        self.assertEqual(self.api.get("/api/projects/" + original["id"]).json()["inputs"], budget)

        spatial_draft = deepcopy(budget)
        spatial_draft["cooling_bartz_mode"] = 1
        # Constructing a new draft has no effect on the saved scheme or result.
        self.assertEqual(self.api.get("/api/projects/" + original["id"]).json()["inputs"], budget)
        upgraded = self.save(unchanged, spatial_draft)
        self.assertEqual(upgraded["revision"], 3)
        expected_spatial, _ = input_values(spatial_draft)
        # Enabling the physical model resolves the existing material fallback;
        # that normalization applies only to the explicitly saved new version.
        self.assertEqual(upgraded["inputs"], expected_spatial)
        self.assertEqual(upgraded["inputs"]["cooling_heat_load"], budget["cooling_heat_load"])
        self.assertEqual(upgraded["inputs"]["cooling_dp_pa"], budget["cooling_dp_pa"])
        for revision in (1, 2):
            old = self.api.get(f"/api/projects/{original['id']}/revisions/{revision}")
            self.assertEqual(old.status_code, 200)
            self.assertEqual(old.json()["inputs"], budget)
        viewed = self.api.get("/api/jobs/" + job_id)
        self.assertEqual(viewed.status_code, 200, viewed.text)
        self.assertEqual(viewed.json()["inputs"], budget)
        self.assertEqual(viewed.json()["result"], result)
        self.assertNotIn("profile", viewed.json()["result"]["calculation"]["components"]["cooling_channel"])
        self.assertEqual(self.app.state.store.one("SELECT * FROM jobs WHERE id=?", (job_id,)), original_job)

        new_job = self.api.post(f"/api/projects/{original['id']}/jobs", json={"revision": 3})
        self.assertEqual(new_job.status_code, 200, new_job.text)
        self.assertEqual(self.api.get("/api/jobs/" + new_job.json()["id"]).json()["inputs"], expected_spatial)

    def test_kerosene_budget_remains_available_to_api_but_spatial_conversion_is_rejected(self):
        response = self.api.get("/api/info")
        self.assertEqual(response.status_code, 200, response.text)
        item = next(item for item in response.json()["presets"] if item["key"] == "merlin1d_gg")
        self.assertFalse(item["routine_design"])
        old = self.project(item["inputs"])
        saved = self.save(old, deepcopy(old["inputs"]))
        self.assertEqual(saved["inputs"], old["inputs"])
        invalid = deepcopy(saved["inputs"])
        invalid["cooling_bartz_mode"] = 1
        response = self.api.put("/api/projects/" + saved["id"], json={
            "name": saved["name"], "description": saved["description"],
            "revision": saved["revision"], "inputs": invalid})
        self.assertEqual(response.status_code, 422)
        retained = self.api.get("/api/projects/" + saved["id"]).json()
        self.assertEqual(retained["revision"], 2)
        self.assertEqual(retained["inputs"], old["inputs"])


if __name__ == "__main__":
    unittest.main()
