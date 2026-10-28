"""Pump geometry consumes the system point without changing pump thermodynamics."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import EngineInputs, ValidationError
from pump import Pump
from thermo_data import methane_dataset_scope
from webapp.pump_geometry import pump_geometry_view
from webapp.ui_schema import design_ui_schema
from web_tests.test_pump_geometry import fixture


class PumpPointIntegrationTests(unittest.TestCase):
    def test_psi_changes_geometry_but_not_real_fluid_pump_work(self):
        with warnings.catch_warnings(), methane_dataset_scope("gri30_highT"):
            warnings.simplefilter("ignore")
            inputs = asdict(EngineInputs())
            for fluid, temperature, stages, rpm, prefix, component, side in (
                ("LOX", 80., 1, 26500., "lox", "ox_pump", "ox"),
                ("CH4", 110., 2, 38000., "fuel", "fuel_pump", "fuel"),
            ):
                values = []
                for psi in (.4, .65):
                    state, result = Pump(component, fluid, 100., 1e6, 75e6, temperature, .85,
                        nr_rpm=rpm, n_stages=stages, head_coefficient_psi=psi).evaluate()
                    values.append((state, result.outputs))
                    record_inputs = inputs | {f"{prefix}_pump_head_coefficient_psi": psi,
                                               f"{prefix}_pump_n_stages": stages}
                    model = pump_geometry_view({"calculation": {"inputs": record_inputs,
                        "components": {component: {"outputs": result.outputs}}}})["pumps"][side]
                    self.assertTrue(model["available"], model)
                    d2 = model["dimensions"]["impeller_diameter_m"]
                    self.assertAlmostEqual(d2 * rpm * math.pi / 60., result.outputs["u_tip_est_ms"])
                    self.assertEqual(model["assumptions"]["head_coefficient_psi"], psi)
                for key in ("power_W", "head_m", "specific_work_isentropic_Jkg", "T_out_K", "volume_flow_m3s"):
                    self.assertEqual(values[0][1][key], values[1][1][key], key)
                self.assertAlmostEqual(values[0][1]["d_impeller_est_m"] / values[1][1]["d_impeller_est_m"], math.sqrt(.65/.4))

    def test_small_positive_head_and_speed_are_not_floored(self):
        with warnings.catch_warnings(), methane_dataset_scope("gri30_highT"):
            warnings.simplefilter("ignore")
            _, result = Pump("small", "LOX", 1., 1e6, 1e6 + 100., 80., .85,
                             nr_rpm=1., n_stages=1, head_coefficient_psi=.6).evaluate()
        o = result.outputs
        self.assertLess(o["head_m_per_stage"], 1.)
        self.assertAlmostEqual(o["u_tip_est_ms"]**2 * .6, o["delta_p_Pa"] / o["rho_in_kgm3"])
        self.assertAlmostEqual(o["d_impeller_est_m"] * math.pi / 60., o["u_tip_est_ms"])

    def test_invalid_stage_or_geometric_assumption_is_not_silently_coerced(self):
        for prefix in ("lox", "fuel"):
            for bad in (True, 1.4, 0, -1, float("nan")):
                with self.subTest(prefix=prefix, stages=bad), self.assertRaises(ValidationError):
                    EngineInputs(**{f"{prefix}_pump_n_stages": bad})
            for suffix in ("inlet_flow_coefficient", "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio"):
                for bad in (True, 0, -1, float("nan"), float("inf")):
                    with self.subTest(suffix=suffix, bad=bad), self.assertRaises(ValidationError):
                        EngineInputs(**{f"{prefix}_pump_{suffix}": bad})
            with self.assertRaises(ValidationError):
                EngineInputs(**{f"{prefix}_pump_eye_hub_ratio": 1.})
        self.assertEqual(EngineInputs(lox_pump_n_stages=2.).lox_pump_n_stages, 2)
        for kwargs in ({"nr_rpm": 0}, {"n_stages": 1.2}, {"head_coefficient_psi": float("nan")}):
            with self.assertRaises(ValidationError):
                Pump("bad", "LOX", 1., 1e6, 2e6, 80., .85, **({"nr_rpm": 20000, "n_stages": 1} | kwargs))

    def test_assumptions_have_one_visible_editor_owner(self):
        groups = design_ui_schema()["system_groups"]
        for prefix in ("lox", "fuel"):
            for suffix in ("head_coefficient_psi", "inlet_flow_coefficient", "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio"):
                fields = [(g["id"], f) for g in groups for f in g["fields"] if f["key"] == f"{prefix}_pump_{suffix}"]
                self.assertEqual(len(fields), 1)
                self.assertEqual(fields[0][0], "pumps")
                self.assertFalse(fields[0][1].get("hidden", False))


class PumpGeometryAPITests(unittest.TestCase):
    def test_new_snapshot_and_old_record_read_without_rewriting_history(self):
        from webapp.app import create_app
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Path(temp) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as api:
                api.post("/api/auth/setup", json={"username": "admin", "display_name": "测试", "password": TEST_PASSWORD})
                p = api.post("/api/projects", json={"name": "泵几何测试", "inputs": asdict(EngineInputs())}).json()["project"]
                key = api.post(f"/api/projects/{p['id']}/jobs", json={"revision": 1}).json()["id"]
                old = fixture()
                for prefix in ("lox", "fuel"):
                    old["calculation"]["inputs"].pop(f"{prefix}_pump_eye_hub_ratio")
                new = fixture()
                new["pump_geometry"] = pump_geometry_view(new)
                for result, available in ((old, False), (new, True)):
                    raw = json.dumps(result)
                    app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (raw, key))
                    for _ in range(2):
                        response = api.get("/api/jobs/" + key)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["pump_geometry"]["pumps"]["ox"]["available"], available)
                        self.assertEqual(response.json()["result"], result)
                        self.assertEqual(app.state.store.one("SELECT result FROM jobs WHERE id=?", (key,))["result"], raw)
                for filename in ("pump-geometry.js", "pump-geometry.css"):
                    self.assertEqual(api.get("/static/" + filename).status_code, 200)


if __name__ == "__main__":
    unittest.main()
