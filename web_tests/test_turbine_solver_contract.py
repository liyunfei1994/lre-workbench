"""Real GG solves cover the new saved total-state/capture handoff end to end."""
import json
import unittest
import warnings

from webapp.solver import calculate
from engine_presets import preset_input_values
import thermo


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE,
                     "Cantera and CoolProp are required")
class TurbineSolverContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}
        # These are complete real-library steady solves, not injected component
        # fixtures. Fixed heat/pressure-loss cooling keeps this regression small.
        for layout in ("single_shaft", "dual_shaft"):
            values = preset_input_values("tq12a_gg")
            values.update(gg_layout=layout, cooling_bartz_mode=0., cooling_heat_load=10e6,
                          turbine_boundary_basis="total_to_total", turbine_meanline_mode="disabled")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cls.results[layout] = calculate(values)

    def test_new_total_mode_never_uses_total_density_to_draw_static_annulus(self):
        for layout, result in self.results.items():
            with self.subTest(layout=layout):
                expected = {"shared"} if layout == "single_shaft" else {"ox", "fuel"}
                self.assertEqual(set(result["turbine_geometry"]["turbines"]), expected)
                for item in result["turbine_geometry"]["turbines"].values():
                    self.assertFalse(item["available"])
                    self.assertIn("总态接口", item["reason"])
                    self.assertNotIn("meridional", item)
                    self.assertNotIn("rows", item)
                for key, component in result["calculation"]["components"].items():
                    if key.startswith("gg_turbine"):
                        self.assertEqual(component["outputs"]["state_basis"], "total_to_total")

    def test_real_composition_metadata_and_each_branch_support_property_capture(self):
        for layout, result in self.results.items():
            with self.subTest(layout=layout):
                calculation = result["calculation"]
                inlet = calculation["states"]["gg_outlet"]
                self.assertEqual(inlet["metadata"]["thermo_source"], 1.)
                self.assertTrue(inlet["metadata"]["reference_id"])
                self.assertAlmostEqual(sum(inlet["composition"].values()), 1., places=7)
                for capture in result["turbine_gas_properties"]["turbines"].values():
                    self.assertEqual(capture["status"], "saved", capture.get("reason"))
                    key = capture["source"]["component_key"]
                    outlet_key = "gg_turbine_exhaust" if layout == "single_shaft" else key + "_exhaust"
                    outlet = calculation["states"][outlet_key]
                    component = calculation["components"][key]["outputs"]
                    self.assertEqual(outlet["mass_flow"], component["mass_flow_kgs"])
                    self.assertEqual(outlet["temperature"], component["T_exhaust_K"])
                    self.assertEqual(outlet["pressure"], component["p_out_Pa"])
                    self.assertEqual(capture["composition_mass_fractions"], inlet["composition"])
                    self.assertEqual(len(capture["property_table"]["rows"]), 11)
                    self.assertEqual(capture["model"]["mechanism_sha256"], result["environment"]["mechanism_sha256"])
                    endpoints = capture["endpoint_properties"]
                    enthalpy_drop = endpoints["inlet"]["enthalpy_j_kg"] - endpoints["outlet"]["enthalpy_j_kg"]
                    expected_power = component["mass_flow_kgs"] * enthalpy_drop
                    self.assertAlmostEqual(component["turbine_power_W"], expected_power,
                                           delta=max(1., abs(expected_power) * 2e-6))
                    if layout == "dual_shaft":
                        self.assertEqual(outlet["metadata"]["thermo_source"], 1.)
                        self.assertTrue(outlet["metadata"]["reference_id"])

    def test_real_solver_capture_design_and_consistency_snapshots_serialize_together(self):
        for layout, result in self.results.items():
            with self.subTest(layout=layout):
                self.assertEqual(result["calculation"]["convergence"]["status"], "pass")
                self.assertLess(result["calculation"]["summary"]["solver_residual_norm"], 1e-4)
                self.assertEqual(result["result_consistency"]["conflicts"], [])
                # No static annulus is a deliberate missing item, not conflict.
                self.assertEqual(result["result_consistency"]["status"], "partial")
                for item in result["turbine_design"]["turbines"].values():
                    self.assertEqual(item["status"], "ready", item["checks"])
                    self.assertTrue(item["ready_for_global_setup"])
                json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
