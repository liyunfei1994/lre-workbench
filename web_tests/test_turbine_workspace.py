"""Independent turbine scenarios retain saved evidence and API access rules."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from web_tests.test_turbine_design import fixture, thermo
from webapp.turbine_design import turbine_design_view, capture_turbine_gas_properties
from webapp.turbine_workspace import turbine_workspace_view, preliminary_result


def scenario(workspace, side="ox"):
    return {key: item["value"] for key, item in workspace["turbines"][side]["seeds"].items()}


class TurbineWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.result = fixture(basis="unspecified", capture=False)
        for side in ("ox", "fuel"):
            self.result["calculation"]["components"][side + "_preburner_turbine"]["outputs"].update(
                rotor_sigma_yield_T_MPa=600., rotor_sigma_centrif_MPa=300., rotor_safety_factor=2.)
        self.result["turbine_gas_properties"] = capture_turbine_gas_properties(self.result, thermo)
        self.design = turbine_design_view(self.result, "record")
        self.workspace = turbine_workspace_view(self.result, self.design)

    def test_workspace_keeps_frozen_handoff_and_total_fields_unchanged(self):
        result, design = deepcopy(self.result), deepcopy(self.design)
        view = turbine_workspace_view(self.result, self.design)
        self.assertEqual(self.result, result)
        self.assertEqual(self.design, design)
        self.assertEqual(set(view["turbines"]), {"ox", "fuel"})
        self.assertEqual(view["turbines"]["ox"]["seeds"]["inlet_swirl_m_s"]["status"], "assumed")
        self.assertEqual(view["turbines"]["ox"]["seeds"]["stator_axial_chord_m"]["status"], "assumed")
        self.assertFalse(self.design["turbines"]["ox"]["ready_for_global_setup"])
        total = next(f for f in self.design["turbines"]["ox"]["global_fields"] if f["key"] == "total_pressure_in_pa")
        self.assertIsNone(total["value"])

    def test_local_scenario_has_own_identity_and_does_not_edit_record(self):
        before = deepcopy(self.result)
        form = scenario(self.workspace)
        original = deepcopy(form)
        one = preliminary_result(self.result, self.design, "ox", form)
        self.assertEqual(one["status"], "preliminary", one)
        self.assertEqual(one["source"]["kind"], "saved_record_with_explicit_local_scenario")
        self.assertEqual(one["source"]["record_id"], "record")
        self.assertEqual(one["source"]["state_basis"], "unspecified")
        self.assertEqual(self.result, before)
        self.assertEqual(form, original)
        self.assertEqual(one, preliminary_result(self.result, self.design, "ox", form))
        form["mean_diameter_m"] *= 2
        two = preliminary_result(self.result, self.design, "ox", form)
        self.assertNotEqual(one["scenario_sha256"], two["scenario_sha256"])
        values = lambda r: {f["key"]: f["value"] for f in r["engineering_screening"]["fields"]}
        self.assertAlmostEqual(values(two)["centrifugal_stress_mpa"], 4 * values(one)["centrifugal_stress_mpa"])
        self.assertAlmostEqual(values(two)["safety_factor"], values(one)["safety_factor"] / 4)
        self.assertEqual(two["engineering_screening"]["checks"][0]["status"], "review")
        canonical = json.dumps({k: v for k, v in two.items() if k != "calculation_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(two["calculation_sha256"], hashlib.sha256(canonical.encode()).hexdigest())

    def test_missing_dimensions_do_not_get_synthetic_geometry_seeds(self):
        copy = deepcopy(self.design)
        copy["turbines"]["ox"]["design_point"]["mean_diameter_m"] = None
        seeds = turbine_workspace_view(self.result, copy)["turbines"]["ox"]["seeds"]
        for key in ("mean_diameter_m", "stator_axial_chord_m", "rotor_axial_chord_m"):
            self.assertIsNone(seeds[key]["value"])
            self.assertEqual(seeds[key]["status"], "missing")

    def test_source_conflict_or_wrong_side_blocks_scenario(self):
        for side in ("shared", "gg", "", None):
            with self.assertRaises(ValueError):
                preliminary_result(self.result, self.design, side, scenario(self.workspace))
        bad = deepcopy(self.design)
        bad["turbines"]["ox"]["status"] = "conflict"
        with self.assertRaises(ValueError):
            preliminary_result(self.result, bad, "ox", scenario(self.workspace))

    def test_invalid_saved_chord_ratio_is_labelled_as_new_assumption(self):
        for value in (None, 0, False, -1):
            copy = deepcopy(self.result)
            copy["calculation"]["inputs"]["ox_turbine_stator_axial_chord_ratio"] = value
            field = turbine_workspace_view(copy, self.design)["turbines"]["ox"]["seeds"]["stator_axial_chord_m"]
            self.assertEqual(field["source"], "workspace.initial_assumption")
            self.assertEqual(field["status"], "assumed")

    def test_extreme_screening_inputs_do_not_break_valid_kinematic_result(self):
        copy = deepcopy(self.result)
        outputs = copy["calculation"]["components"]["ox_preburner_turbine"]["outputs"]
        outputs["U_mean_actual_ms"] = 1e-300
        answer = preliminary_result(copy, self.design, "ox", scenario(self.workspace))
        self.assertEqual(answer["status"], "preliminary")
        self.assertTrue(all(f["value"] is None for f in answer["engineering_screening"]["fields"]))
        json.dumps(answer, allow_nan=False)


class TurbinePreliminaryAPITests(unittest.TestCase):
    def setUp(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client, TEST_PASSWORD
        from lre_workbench.domain.models import EngineInputs
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = create_app(Path(folder.name) / "workspace.sqlite3", queue_enabled=False)
        self.api = self.enterContext(client(self.app))
        self.api.post("/api/auth/setup", json={"username": "admin", "display_name": "测试", "password": TEST_PASSWORD})
        project = self.api.post("/api/projects", json={"name": "涡轮方案", "inputs": asdict(EngineInputs())}).json()["project"]
        self.job_id = self.api.post(f"/api/projects/{project['id']}/jobs", json={"revision": 1}).json()["id"]
        self.raw = json.dumps(fixture(basis="unspecified"), ensure_ascii=False)
        self.app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (self.raw, self.job_id))
        self.url = f"/api/jobs/{self.job_id}/turbine-preliminary"

    def test_get_and_scenario_post_preserve_database_and_match_source(self):
        record = self.api.get(f"/api/jobs/{self.job_id}").json()
        design = record["turbine_design"]
        for side in ("ox", "fuel"):
            form = scenario(design["workspace"], side)
            response = self.api.post(self.url, json={"turbine_key": side, "scenario": form})
            self.assertEqual(response.status_code, 200, response.text)
            answer = response.json()
            self.assertEqual(answer["status"], "preliminary", answer)
            self.assertEqual(answer["source"]["record_id"], self.job_id)
            self.assertEqual(answer["source"]["design_sha256"], design["turbines"][side]["design_sha256"])
        self.assertEqual(self.app.state.store.one("SELECT result FROM jobs WHERE id=?", (self.job_id,))["result"], self.raw)
        self.assertEqual(self.app.state.store.one("SELECT COUNT(*) AS n FROM jobs")["n"], 1)

    def test_rejects_client_owned_source_and_bad_request_shapes(self):
        for body in ([], {}, {"turbine_key": "gg", "scenario": {}},
                     {"turbine_key": "ox", "scenario": {}, "machine": {}},
                     {"turbine_key": [], "scenario": {}}, {"turbine_key": "ox", "scenario": []}):
            response = self.api.post(self.url, json=body)
            self.assertEqual(response.status_code, 422, response.text)

    def test_pending_result_cannot_start_preliminary_design(self):
        self.app.state.store.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job_id,))
        response = self.api.post(self.url, json={"turbine_key": "ox", "scenario": {}})
        self.assertEqual(response.status_code, 409)

    def test_authentication_is_required(self):
        self.assertEqual(self.api.post("/api/auth/logout", json={}).status_code, 200)
        response = self.api.post(self.url, json={"turbine_key": "ox", "scenario": {}})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
