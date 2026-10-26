"""Actual material-to-equation, zoning and saved applicability contracts."""
import copy
import json
import math
import unittest
from webapp.solver import input_values
from tests.test_regen_circuit import BASE
from cooling_marching import march_regen_circuit, _wall_heat
from wall_materials import get_wall_dataset, GRCOP84_DATASET_ID, CUZR_DATASET_ID
from wall_thermal import parse_material_zones
from core import EngineInputs, ValidationError


class WallThermalCouplingTests(unittest.TestCase):
    def solve(self, **kwargs):
        args = dict(BASE, n_points=40, wall_thermal_dataset_id=GRCOP84_DATASET_ID,
                    wall_manufacturing_state="powder_metallurgy_extruded_high_fe")
        args.update(kwargs)
        return march_regen_circuit(**args)

    def test_material_is_in_equations_and_conserves_energy(self):
        gr = self.solve()
        cu = self.solve(wall_thermal_dataset_id=CUZR_DATASET_ID,
                        wall_manufacturing_state="qi2024_solution_cold_work_aged")
        self.assertGreater(abs(gr["Q_total_W"]-cu["Q_total_W"]), 1e5)
        for result in (gr, cu):
            self.assertLess(abs(result["balance"]["energy_residual_W"]), 1.)
            for c in result["stations"]:
                d = get_wall_dataset(c["material"]["dataset_id"])
                integral = d.conductivity_integral(c["T_wall_cold_K"], c["T_wall_hot_K"])
                self.assertAlmostEqual(integral, c["q_W_m2"]*c["wall_thickness_m"], delta=.005)

    def test_actual_range_and_state_saved_without_clipping(self):
        result = self.solve(wall_thermal_dataset_id=CUZR_DATASET_ID,
                            wall_manufacturing_state="unspecified")
        a = result["material_applicability"]
        self.assertEqual(a["manufacturing_match"], "unknown")
        self.assertEqual(a["status"], "out_of_range")
        self.assertEqual(a["total_cells"], len(result["stations"]))
        self.assertEqual(a["out_of_range_cell_count"], sum(c["status"] == "out_of_range" for c in a["cells"]))
        self.assertLess(a["min_temperature_K"], 298.)
        self.assertTrue(all(r["action"] == "constant_endpoint_continuation" for r in a["cells"] if r["status"] == "out_of_range"))
        self.assertEqual(a["active_properties"], ["conductivity"])
        self.assertIsNotNone(a["datasets"][0]["data_sha256"])
        json.dumps(result, allow_nan=False)

    def test_material_zones_use_physical_x_and_split_mesh(self):
        zones = [{"x_start_m": .1, "x_end_m": .2, "dataset_id": CUZR_DATASET_ID,
                  "manufacturing_state": "qi2024_solution_cold_work_aged"}]
        result = self.solve(wall_material_zones=json.dumps(zones))
        self.assertTrue(any(c["material"]["dataset_id"] == CUZR_DATASET_ID for c in result["stations"]))
        for c in result["stations"]:
            expected = CUZR_DATASET_ID if .1 <= c["x_m"] < .2 else GRCOP84_DATASET_ID
            self.assertEqual(c["material"]["dataset_id"], expected)
        for bound in (.1, .2):
            self.assertIn(bound, [n["x_m"] for n in result["geometry"]["nodes"]])
        self.assertEqual(result["material_applicability"]["manufacturing_match"], "matched")
        self.assertEqual(result["material_assignment"]["zones"], zones)

    def test_invalid_zones_and_legacy_material_are_not_silently_replaced(self):
        z = {"x_start_m": .1, "x_end_m": .2, "dataset_id": CUZR_DATASET_ID, "manufacturing_state": "unspecified"}
        with self.assertRaises(ValueError):
            parse_material_zones([z, z])
        with self.assertRaises(KeyError):
            parse_material_zones([dict(z, dataset_id="cucrzr")])
        with self.assertRaises(ValueError):
            self.solve(wall_material_zones=[dict(z, x_end_m=100.)])
        with self.assertRaises(ValidationError):
            EngineInputs(chamber_inner_wall_material="cucrzr")
        self.assertEqual(EngineInputs(cooling_bartz_mode=0., chamber_inner_wall_material="grcop42").wall_thermal_dataset_id, "")
        self.assertEqual(EngineInputs().wall_thermal_dataset_id, GRCOP84_DATASET_ID)
        self.assertEqual(EngineInputs(chamber_inner_wall_material="cucrzr",
            wall_thermal_dataset_id=CUZR_DATASET_ID).wall_thermal_dataset_id, CUZR_DATASET_ID)

    def test_api_does_not_inject_current_default_into_unsupported_history(self):
        with self.assertRaises(ValidationError):
            input_values({"chamber_inner_wall_material":"grcop42"})
        values, _ = input_values({"chamber_inner_wall_material":"grcop42", "cooling_bartz_mode":0.})
        self.assertEqual(values["wall_thermal_dataset_id"], "")
        values, _ = input_values({"chamber_inner_wall_material":"grcop84"})
        self.assertEqual(values["wall_thermal_dataset_id"], GRCOP84_DATASET_ID)

    def test_constant_dataset_partition_preserves_physics_on_finer_grid(self):
        # A split changes discretization, not properties; its error must be small
        # and energy conservation must still hold. This is not a new circuit.
        base = self.solve()
        z = [{"x_start_m": .1, "x_end_m": .2, "dataset_id": GRCOP84_DATASET_ID,
              "manufacturing_state": "powder_metallurgy_extruded_high_fe"}]
        split = self.solve(wall_material_zones=z)
        self.assertLess(abs(split["Q_total_W"]/base["Q_total_W"]-1), .002)
        self.assertLess(abs(split["balance"]["energy_residual_W"]), 1.)

    def test_hot_trial_regrid_is_explicit_and_does_not_skip_material_boundaries(self):
        original = self.solve()
        hot = [dict(c["hot_side"], x_m=c["x_m"]) for c in original["stations"]]
        zones = [{"x_start_m":.1,"x_end_m":.2,"dataset_id":GRCOP84_DATASET_ID,
                  "manufacturing_state":"unspecified"}]
        with self.assertRaises(ValueError):
            self.solve(wall_material_zones=zones, hot_side_profile=hot)
        updated = self.solve(wall_material_zones=zones, hot_side_profile=hot, remap_hot_side_trial=True)
        self.assertGreater(len(updated["stations"]), len(original["stations"]))
        self.assertEqual(len(updated["material_applicability"]["cells"]), len(updated["stations"]))
        self.assertLess(abs(updated["balance"]["energy_residual_W"]),1.)


if __name__ == "__main__":
    unittest.main()
