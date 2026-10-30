"""Geometry views must preserve frozen hardware and historical inputs."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import tempfile
import unittest

from webapp.solver import EngineInputs, input_values, presets
from webapp.nozzle_geometry import nozzle_geometry_view
from webapp.ui_schema import design_ui_schema
from regen_geometry import build_regen_geometry


def fixture(method="parabolic_bell_explicit_v1", cover=0):
    values = asdict(EngineInputs(expansion_ratio=16, chamber_contraction_ratio=2.25,
        nozzle_contour_method=method))
    geom = build_regen_geometry(r_t=.1, r_c=.15, L_cyl=.3, expansion_ratio=16,
        cover_eps=cover, nozzle_contour_method=method, n_points=80,
        n_channels=300, channel_width_m=.001, channel_height_m=.018, wall_thickness_m=.002)
    return {"calculation": {"inputs": values, "components": {
        "cooling_channel": {"profile": {"geometry": geom}},
        "nozzle": {"outputs": {"At_m2": math.pi * .1**2, "expansion_ratio": 16}}}}}


class NozzleGeometryViewTests(unittest.TestCase):
    def test_saved_full_contour_is_used_even_when_cooling_coverage_is_partial(self):
        result = fixture(cover=4)
        before = deepcopy(result)
        view = nozzle_geometry_view(result, "design-one")
        self.assertTrue(view["available"], view)
        saved = result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
        self.assertEqual(view["contour"]["x_m"], [n["x_m"] for n in saved["full_nodes"]])
        self.assertLess(view["cooling"]["end_x_m"], view["dimensions"]["total_length_m"])
        self.assertAlmostEqual(view["dimensions"]["exit_diameter_m"], .8)
        self.assertEqual(result, before)

    def test_later_or_inconsistent_inputs_cannot_redraw_saved_hardware(self):
        result = fixture()
        first = nozzle_geometry_view(result)
        result["calculation"]["inputs"].update(nozzle_initial_angle_deg=55, nozzle_exit_angle_deg=5,
            expansion_ratio=100, nozzle_bell_fraction=.6)
        after = nozzle_geometry_view(result)
        self.assertEqual(first["contour"], after["contour"])
        self.assertEqual(first["dimensions"], after["dimensions"])

    def test_old_saved_nodes_keep_old_method_without_rewriting_result(self):
        result = fixture("legacy_log_v1")
        g = result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
        for key in ("nozzle_contour_method", "initial_expansion_angle_deg", "exit_angle_deg", "angle_source"):
            g.pop(key, None)
        result["calculation"]["inputs"].pop("nozzle_contour_method")
        before = deepcopy(result)
        view = nozzle_geometry_view(result)
        self.assertTrue(view["available"], view)
        self.assertEqual(view["method"]["id"], "legacy_log_v1")
        self.assertIn("历史", view["method"]["angle_source"])
        self.assertEqual(result, before)

    def test_corrupt_saved_geometry_is_not_replaced_with_a_fresh_contour(self):
        result = fixture()
        g = result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
        for bad in (None, [], list(reversed(g["full_nodes"]))):
            broken = deepcopy(result)
            broken["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]["full_nodes"] = bad
            self.assertFalse(nozzle_geometry_view(broken)["available"])

    def test_budget_reconstruction_is_explicit_and_uses_saved_sizes(self):
        result = fixture()
        result["calculation"]["components"]["cooling_channel"].pop("profile")
        view = nozzle_geometry_view(result)
        self.assertTrue(view["available"], view)
        self.assertEqual(view["source"]["kind"], "reconstructed_from_record")
        self.assertEqual(view["method"]["id"], "parabolic_bell_explicit_v1")
        self.assertAlmostEqual(view["dimensions"]["throat_diameter_m"], .2)
        self.assertIsNone(view["cooling"]["end_x_m"])

    def test_inconsistent_chamber_dimensions_do_not_produce_a_valid_drawing(self):
        for key, factor in (("r_c_m", 2), ("L_cyl_m", .5)):
            result = fixture()
            g = result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
            g[key] *= factor
            view = nozzle_geometry_view(result)
            self.assertFalse(view["available"], view)
            self.assertIn("直段", view["reason"])

    def test_malformed_coverage_and_snapshot_do_not_break_record_reading(self):
        result = fixture()
        result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]["nodes"] = [None]
        self.assertFalse(nozzle_geometry_view(result)["available"])
        self.assertFalse(nozzle_geometry_view({"nozzle_geometry": {"available": True, "source": None}})["available"])

    def test_missing_or_invalid_essential_data_has_no_invented_dimensions(self):
        self.assertFalse(nozzle_geometry_view(None)["available"])
        self.assertFalse(nozzle_geometry_view({"calculation": {"components": {}}})["available"])
        result = fixture()
        result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]["expansion_ratio"] = 20
        self.assertFalse(nozzle_geometry_view(result)["available"])

    def test_snapshot_and_record_identity_do_not_mutate_saved_data(self):
        snapshot = nozzle_geometry_view(fixture())
        result = {"nozzle_geometry": snapshot}
        before = deepcopy(result)
        view = nozzle_geometry_view(result, "job-2")
        self.assertEqual(view["source"]["record_id"], "job-2")
        self.assertEqual(result, before)

    def test_new_presets_explicit_old_documents_legacy(self):
        self.assertTrue(all(p["inputs"]["nozzle_contour_method"] == "parabolic_bell_explicit_v1" for p in presets()))
        old = asdict(EngineInputs())
        old.pop("nozzle_contour_method")
        normalized, _ = input_values(old, saved_snapshot=True)
        self.assertEqual(normalized["nozzle_contour_method"], "legacy_log_v1")
        self.assertNotIn("nozzle_contour_method", old)

    def test_geometry_inputs_have_one_editor_owner(self):
        schema = design_ui_schema()
        for key in ("nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg", "expansion_ratio"):
            owners = [g["id"] for g in schema["system_groups"] if any(f["key"] == key for f in g["fields"])]
            self.assertEqual(owners, ["chamber_contour"])


class NozzleGeometryAPITests(unittest.TestCase):
    def test_view_is_read_only_and_static_modules_are_served(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Path(temp) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as api:
                api.post("/api/auth/setup", json={"username": "admin", "display_name": "测试", "password": TEST_PASSWORD})
                p = api.post("/api/projects", json={"name": "几何测试", "inputs": presets()[0]["inputs"]}).json()["project"]
                key = api.post(f"/api/projects/{p['id']}/jobs", json={"revision": 1}).json()["id"]
                result = fixture()
                raw = json.dumps(result)
                app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (raw, key))
                response = api.get("/api/jobs/" + key)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["nozzle_geometry"]["available"])
                self.assertEqual(response.json()["result"], result)
                self.assertEqual(app.state.store.one("SELECT result FROM jobs WHERE id=?", (key,))["result"], raw)
                for filename in ("nozzle-geometry.js", "nozzle-geometry.css"):
                    self.assertEqual(api.get("/static/" + filename).status_code, 200)


if __name__ == "__main__":
    unittest.main()
