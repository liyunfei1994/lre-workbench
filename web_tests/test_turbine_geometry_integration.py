"""Turbine geometry connects real component outputs, saved jobs and the editor."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import CycleResults, EngineInputs, ValidationError
from thermo import GasState
from turbine import Turbine
from webapp.solver import calculate
from webapp.turbine_geometry import ASSUMPTION_KEYS, turbine_geometry_view
from webapp.ui_schema import design_ui_schema


def component_result(cycle="ffsc", layout="single_shaft"):
    inputs = EngineInputs(cycle_type=cycle, gg_layout=layout)
    # A controlled ideal-gas component test point, not a real engine measurement.
    gas = GasState(temperature=850., pressure=8e6, cp=1200., gamma=1.33,
                   molar_mass=8314.462618 / (1200. * (1 - 1 / 1.33)),
                   enthalpy=1200. * 850., source="empirical")
    keys = (["gg_turbine"] if layout == "single_shaft" else ["gg_turbine_ox", "gg_turbine_fuel"]) if cycle == "gg" else ["ox_preburner_turbine", "fuel_preburner_turbine"]
    components = {}
    for i, key in enumerate(keys):
        _, component = Turbine(key, gas, 4e6, .85, 10. + i,
                               shaft_speed_rpm=26500. + 11500. * i,
                               n_stages_user=2).evaluate()
        components[key] = component
    return CycleResults(inputs=inputs, summary={}, states={}, components=components, warnings=[])


class TurbineIntegrationTests(unittest.TestCase):
    def test_real_turbine_outputs_and_solver_snapshot_for_every_layout(self):
        for cycle, layout, expected in (("ffsc", "single_shaft", {"ox", "fuel"}),
                                         ("gg", "single_shaft", {"shared"}),
                                         ("gg", "dual_shaft", {"ox", "fuel"})):
            with self.subTest(cycle=cycle, layout=layout):
                result = component_result(cycle, layout)
                before = asdict(result)
                with patch("gg_cycle.make_cycle_model", return_value=Mock(evaluate=Mock(return_value=result))), \
                     patch("webapp.solver.science_environment", return_value={}), \
                     patch("engineering_assessment.assess_engineering", return_value={}):
                    saved = calculate(asdict(result.inputs))
                self.assertEqual(asdict(result), before)
                view = saved["turbine_geometry"]
                self.assertEqual(set(view["turbines"]), expected)
                for turbine in view["turbines"].values():
                    self.assertTrue(turbine["available"], turbine.get("reason"))
                    key = turbine["source"]["component_key"]
                    point = turbine["design_point"]
                    self.assertEqual(point["mass_flow_kg_s"], result.components[key].outputs["mass_flow_kgs"])
                    self.assertEqual(point["shaft_speed_rpm"], result.components[key].outputs["shaft_speed_rpm"])
                    self.assertEqual(len(turbine["rows"]), 2 * point["stages"])
                json.dumps(saved, allow_nan=False)
                # Changing a draft or the defaults cannot rewrite a saved geometry.
                copy = deepcopy(saved)
                copy["calculation"]["inputs"]["fuel_turbine_inlet_flow_coefficient"] = 20.
                self.assertEqual(turbine_geometry_view(copy), view)

    def test_bad_geometry_assumptions_and_fractional_stages_rejected_before_submission(self):
        for side in ("ox", "fuel"):
            for suffix in ASSUMPTION_KEYS[:-1]:
                for bad in (True, 0, -1, float("nan"), float("inf")):
                    with self.subTest(side=side, suffix=suffix, bad=bad), self.assertRaises(ValidationError):
                        EngineInputs(**{f"{side}_turbine_{suffix}": bad})
            with self.assertRaises(ValidationError):
                EngineInputs(**{f"{side}_turbine_geometry_state_basis": "total"})
            for bad in (True, 1.2, -1, 7, float("nan"), float("inf")):
                with self.subTest(side=side, stages=bad), self.assertRaises(ValidationError):
                    EngineInputs(**{f"turb_{side}_n_stages_user": bad})

    def test_geometry_has_one_editor_owner_and_gg_single_hides_unused_ox_inputs(self):
        schema = design_ui_schema()
        group = next(g for g in schema["system_groups"] if g["id"] == "turbines")
        keys = [f["key"] for g in schema["system_groups"] for f in g["fields"]]
        for side in ("ox", "fuel"):
            for suffix in ASSUMPTION_KEYS:
                key = f"{side}_turbine_{suffix}"
                self.assertEqual(keys.count(key), 1)
                field = next(f for f in group["fields"] if f["key"] == key)
                self.assertEqual(field["role"], "component_sizing")
                if side == "ox":
                    self.assertEqual(field["visibility"]["any"], [
                        {"key": "cycle_type", "op": "eq", "value": "ffsc"},
                        {"key": "gg_layout", "op": "eq", "value": "dual_shaft"}])
                if suffix == "geometry_state_basis":
                    self.assertEqual(field["choices"][0]["value"], "system_pt_as_static")


class TurbineGeometryAPITests(unittest.TestCase):
    def test_old_and_new_record_reads_preserve_original_results(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(Path(temporary) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as api:
                api.post("/api/auth/setup", json={"username": "admin", "display_name": "测试", "password": TEST_PASSWORD})
                project = api.post("/api/projects", json={"name": "涡轮几何测试", "inputs": asdict(EngineInputs())}).json()["project"]
                job_id = api.post(f"/api/projects/{project['id']}/jobs", json={"revision": 1}).json()["id"]
                new = {"calculation": asdict(component_result())}
                old = deepcopy(new)
                for side in ("ox", "fuel"):
                    old["calculation"]["inputs"].pop(f"{side}_turbine_geometry_state_basis")
                new["turbine_geometry"] = turbine_geometry_view(new)
                for record, available in ((old, False), (new, True)):
                    raw = json.dumps(record)
                    app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (raw, job_id))
                    for _ in range(2):
                        response = api.get("/api/jobs/" + job_id)
                        self.assertEqual(response.status_code, 200)
                        body = response.json()
                        self.assertEqual(body["result"], record)
                        self.assertEqual(body["turbine_geometry"]["source"]["record_id"], job_id)
                        for turbine in body["turbine_geometry"]["turbines"].values():
                            self.assertEqual(turbine["available"], available)
                        self.assertEqual(app.state.store.one("SELECT result FROM jobs WHERE id=?", (job_id,))["result"], raw)
                for filename in ("turbine-geometry.js", "turbine-geometry.css"):
                    self.assertEqual(api.get("/static/" + filename).status_code, 200)


if __name__ == "__main__":
    unittest.main()
