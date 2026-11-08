"""Optional cooling inputs survive normalization, saved jobs and API validation."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.solver import EngineInputs, input_values, design_readiness, presets
from web_tests.test_workspace import client, TEST_PASSWORD
from webapp.app import create_app
from web_tests.test_cooling_design import source_fixture, request_fixture
from webapp.cooling_design import calculate_cooling_design
from lre_workbench.components.cooling.marching import march_regen_circuit


class CoolingNormalizationTests(unittest.TestCase):
    def test_thousand_node_channel_table_roundtrips_without_truncation(self):
        table = json.dumps({"version": 1, "rows": [
            {"anchor": "x_mm", "x_mm": float(index), "n": 300,
             "w_mm": 1.5, "h_mm": 8.0, "t_mm": 0.5}
            for index in range(1000)]})
        self.assertGreater(len(table), 16000)
        values = asdict(EngineInputs()) | {"cooling_channel_table_json": table}
        normalized, _ = input_values(values)
        replayed, _ = input_values(normalized, saved_snapshot=True)
        self.assertEqual(replayed["cooling_channel_table_json"], table)
        self.assertEqual(len(json.loads(replayed["cooling_channel_table_json"])["rows"]), 1000)

    def test_channel_table_has_its_own_text_limit(self):
        table = '{"rows":[{"anchor":"throat"}]}'
        at_limit = table + " "*(256000-len(table))
        normalized, _ = input_values({"cooling_channel_table_json": at_limit})
        self.assertEqual(normalized["cooling_channel_table_json"], at_limit)
        with self.assertRaisesRegex(ValueError, "cooling_channel_table_json"):
            input_values({"cooling_channel_table_json": at_limit + " "})
        with self.assertRaisesRegex(ValueError, "cooling_htc_enhancement_basis"):
            input_values({"cooling_htc_enhancement_basis": "x"*16001})

    def test_defaults_and_presets_keep_nullable_overrides(self):
        normalized, _ = input_values({})
        self.assertIsNone(normalized["cooling_channel_roughness_m"])
        self.assertIsNone(normalized["cooling_k_minor_inlet"])
        self.assertIsNone(normalized["cooling_k_minor_outlet"])
        self.assertTrue(presets())
        for preset in presets():
            normalized, _ = input_values(preset["inputs"])
            self.assertIsNone(normalized["cooling_channel_roughness_m"])

    def test_zero_and_basis_survive_saved_input_normalization(self):
        source = asdict(EngineInputs(cooling_channel_roughness_m=0.,
            cooling_k_minor_inlet=0., cooling_k_minor_outlet=2.4,
            cooling_htc_enhancement_basis="thermal bench 12"))
        normalized, _ = input_values(source, saved_snapshot=True)
        for key in ("cooling_channel_roughness_m", "cooling_k_minor_inlet",
                    "cooling_k_minor_outlet", "cooling_htc_enhancement_basis"):
            self.assertEqual(normalized[key], source[key])
        self.assertTrue(design_readiness(normalized)["valid"])

    def test_readiness_identifies_both_fields_of_incomplete_end_loss_pair(self):
        draft = asdict(EngineInputs()) | {"cooling_k_minor_inlet": 0.}
        result = design_readiness(draft)
        self.assertFalse(result["valid"])
        self.assertEqual(set(result["issues"][0]["fields"]),
            {"cooling_k_minor_inlet", "cooling_k_minor_outlet"})
        for key in ("cooling_channel_roughness_m", "cooling_k_minor_inlet", "cooling_k_minor_outlet"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                input_values(asdict(EngineInputs()) | {key: ""})

    def test_candidate_screening_passes_saved_cooling_options_to_real_march(self):
        source = source_fixture()
        options = {"cooling_channel_roughness_m": 3e-6, "cooling_k_minor_inlet": 0.,
                   "cooling_k_minor_outlet": 2.4, "cooling_htc_enhancement_basis": "test A"}
        source["result"]["calculation"]["inputs"].update(options)
        before = deepcopy(source)
        with patch('lre_workbench.components.cooling.marching.march_regen_circuit', wraps=march_regen_circuit) as marcher:
            result = calculate_cooling_design(request_fixture(source), source)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertNotEqual(result["candidates"][0]["status"], "failed")
        kwargs = marcher.call_args.kwargs
        self.assertEqual(kwargs["channel_roughness_m"], 3e-6)
        self.assertEqual(kwargs["k_minor_inlet"], 0.)
        self.assertEqual(kwargs["k_minor_outlet"], 2.4)
        self.assertEqual(kwargs["htc_enhancement_basis"], "test A")
        self.assertEqual(source, before)


class CoolingInputAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        app = create_app(Path(self.temp.name)/"workspace.sqlite3", queue_enabled=False)
        self.api = client(app)
        self.addCleanup(self.api.close)
        response = self.api.post("/api/auth/setup", json={"username":"admin",
            "display_name":"测试", "password":TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)

    def test_api_persists_zero_overrides_and_rejects_incomplete_pair(self):
        values = asdict(EngineInputs()) | {"cooling_channel_roughness_m":0.,
            "cooling_k_minor_inlet":0., "cooling_k_minor_outlet":2.4,
            "cooling_htc_enhancement_basis":"thermal report B"}
        response = self.api.post("/api/projects", json={"name":"cooling", "inputs":values})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()["project"]
        self.assertEqual(saved["inputs"]["cooling_channel_roughness_m"], 0.)
        self.assertEqual(saved["inputs"]["cooling_k_minor_inlet"], 0.)
        self.assertEqual(saved["inputs"]["cooling_k_minor_outlet"], 2.4)
        self.assertEqual(saved["inputs"]["cooling_htc_enhancement_basis"], "thermal report B")
        values["cooling_k_minor_outlet"] = None
        response = self.api.post("/api/projects", json={"name":"invalid pair", "inputs":values})
        self.assertEqual(response.status_code, 422, response.text)
        response = self.api.post(f"/api/projects/{saved['id']}/design-readiness", json={"inputs":values})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["valid"])


if __name__ == "__main__":
    unittest.main()
