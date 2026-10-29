"""Saved-record injector views: independent closure, provenance and safe JSON."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from injector import Injector
from webapp.injector_design import injector_design_view


def fixture():
    component = asdict(Injector(580., 180., 33e6, .243, .093, .183,
                               rho_ox=250., rho_fuel=100.).evaluate())
    outputs = component["outputs"]
    states = {"main_chamber": {"pressure": 33e6, "temperature": 3500., "mass_flow": 760.}}
    for side, mass, temperature in (("ox", 580., 680.), ("fuel", 180., 800.)):
        states[side + "_turbine_exhaust"] = {
            "pressure": 33e6 + outputs["dp_" + side + "_Pa"],
            "mass_flow": mass, "temperature": temperature,
        }
    branches = []
    for side, suffix, pressure in (("ox", "opb", 60e6), ("fuel", "fpb", 65e6)):
        states[side + "_preburner_outlet"] = {"pressure": pressure, "temperature": 800., "mass_flow": 30.}
        for propellant, flow, temperature in (("ox", 20., 90.), ("fuel", 10., 350.)):
            branch_id = propellant + "_to_" + suffix
            branches.append({"id": branch_id, "mass_flow_kg_s": flow, "temperature_K": temperature,
                             "destination_pressure_Pa": pressure, "injector_drop_Pa": .12*pressure,
                             "source_pressure_Pa": 1.12*pressure + 3e6,
                             "line_drop_Pa": 1e6, "valve_drop_Pa": 2e6})
            states["branch_" + branch_id] = {"pressure": pressure, "mass_flow": flow, "temperature": temperature}
    return {"calculation": {
        "inputs": {"cycle_type": "ffsc", "propellant_pair": "lox_methane", "pc": 33e6},
        "components": {"injector": component, "main_chamber": {"outputs": {
            "cc_diameter_m": .366, "cc_area_m2": math.pi*.183**2}}},
        "states": states,
        "steady_closure": {"feed_network": {"branches": branches, "mode": "design",
                                            "design_loss_inputs": {"injector_ratio": .12}}},
    }}


class InjectorDesignTests(unittest.TestCase):
    def main(self, result):
        return injector_design_view(result, "saved-record")["stations"][0]

    def outputs(self, result):
        return result["calculation"]["components"]["injector"]["outputs"]

    def test_complete_current_record_independently_closes(self):
        result = fixture()
        before = deepcopy(result)
        view = injector_design_view(result, "saved-record")
        main = view["stations"][0]
        self.assertEqual(view["record_id"], "saved-record")
        self.assertEqual(main["status"], "consistent")
        self.assertTrue(main["available"])
        self.assertTrue(all(c["status"] == "pass" for c in main["checks"]))
        for port, expected in zip(main["ports"], (580., 180.)):
            self.assertAlmostEqual(port["geometry_mass_flow_kg_s"], expected, places=9)
            self.assertAlmostEqual(port["relative_flow_error"], 0., places=12)
        self.assertEqual(result, before)
        json.dumps(view, allow_nan=False)

    def test_missing_current_fields_cannot_silently_pass(self):
        fields = ("mdot_ox_kgs", "mdot_fuel_per_element_kgs", "rho_ox_kgm3", "cd_fuel",
                  "a_ox_required_per_element_m2", "a_fuel_geometry_per_element_m2",
                  "mdot_ox_geometry_kgs", "mass_flow_relative_error_fuel", "geometry_closure_passed",
                  "post_wall_thickness_mm", "face_area_m2", "element_spacing_mm", "dp_ox_ratio")
        for field in fields:
            with self.subTest(field=field):
                result = fixture()
                self.outputs(result).pop(field)
                main = self.main(result)
                self.assertEqual(main["status"], "incomplete")
                self.assertTrue(any(c["status"] == "unavailable" for c in main["checks"]))

    def test_missing_source_evidence_is_incomplete(self):
        for key in ("main_chamber", "ox_turbine_exhaust", "fuel_turbine_exhaust"):
            with self.subTest(key=key):
                result = fixture()
                result["calculation"]["states"].pop(key)
                self.assertEqual(self.main(result)["status"], "incomplete")
        result = fixture()
        result["calculation"]["components"]["main_chamber"] = {}
        self.assertEqual(self.main(result)["status"], "incomplete")

    def test_legacy_record_keeps_old_dimensions_without_default_fill(self):
        result = fixture()
        injector = result["calculation"]["components"]["injector"]
        injector.pop("profile")
        out = injector["outputs"]
        out["delta_fuel_mm"] *= 1.2
        out["d_outer_mm"] = out["d_post_mm"] + 2*out["delta_fuel_mm"]
        for key in list(out):
            if key.startswith(("a_", "mdot_ox_geometry", "mdot_fuel_geometry", "mass_flow_relative_error", "geometry_", "rho_")):
                out.pop(key)
        before = deepcopy(result)
        main = self.main(result)
        self.assertEqual(main["status"], "legacy")
        self.assertEqual(main["dimensions"]["delta_fuel_mm"], out["delta_fuel_mm"])
        self.assertIsNone(main["ports"][0]["geometry_mass_flow_kg_s"])
        self.assertEqual(main["geometry_contract"], {})
        self.assertEqual(before, result)

    def test_unknown_contract_version_does_not_claim_current(self):
        for version in (None, True, 2, "1"):
            result = fixture()
            result["calculation"]["components"]["injector"]["profile"]["geometry_contract"]["version"] = version
            self.assertEqual(self.main(result)["status"], "legacy")

    def test_declared_closure_cannot_mask_tampered_geometry(self):
        result = fixture()
        self.outputs(result)["delta_fuel_mm"] *= 1.1
        self.assertEqual(self.main(result)["status"], "inconsistent")
        self.assertEqual(self.outputs(result)["geometry_closure_passed"], 1.0)

    def test_stored_flow_area_and_error_are_independently_audited(self):
        for field in ("a_ox_geometry_per_element_m2", "a_fuel_required_per_element_m2",
                      "mdot_fuel_geometry_kgs", "mass_flow_relative_error_ox"):
            with self.subTest(field=field):
                result = fixture()
                self.outputs(result)[field] += .01
                self.assertEqual(self.main(result)["status"], "inconsistent")

    def test_source_mass_flow_pressure_and_chamber_size_disagreements_fail(self):
        for state, field in (("ox_turbine_exhaust", "mass_flow"), ("fuel_turbine_exhaust", "pressure"),
                             ("main_chamber", "mass_flow"), ("main_chamber", "pressure")):
            with self.subTest(state=state, field=field):
                result = fixture()
                result["calculation"]["states"][state][field] *= 1.01
                self.assertEqual(self.main(result)["status"], "inconsistent")
        result = fixture()
        result["calculation"]["components"]["main_chamber"]["outputs"]["cc_diameter_m"] *= 1.01
        self.assertEqual(self.main(result)["status"], "inconsistent")

    def test_optional_nozzle_and_cooling_geometry_checked_when_present(self):
        result = fixture()
        self.assertEqual(self.main(result)["status"], "consistent")
        result["nozzle_geometry"] = {"dimensions": {"chamber_diameter_m": .4}}
        self.assertEqual(self.main(result)["status"], "inconsistent")
        result.pop("nozzle_geometry")
        result["calculation"]["components"]["cooling_channel"] = {"profile": {"geometry": {"r_c_m": .2}}}
        self.assertEqual(self.main(result)["status"], "inconsistent")

    def test_available_source_density_is_checked(self):
        result = fixture()
        result["calculation"]["states"]["ox_turbine_exhaust"]["density"] = 500.
        self.assertEqual(self.main(result)["status"], "inconsistent")

    def test_pressure_tolerates_sub_pascal_roundoff(self):
        result = fixture()
        result["calculation"]["states"]["fuel_turbine_exhaust"]["pressure"] += .5
        self.assertEqual(self.main(result)["status"], "consistent")

    def test_noninteger_or_inverted_geometry_fails(self):
        for changes in ({"num_elements": 760.5}, {"d_outer_mm": .1}):
            result = fixture()
            self.outputs(result).update(changes)
            self.assertEqual(self.main(result)["status"], "inconsistent")

    def test_nonfinite_huge_or_malformed_numbers_never_escape_json(self):
        for key in ("face_diameter_m", "num_elements", "d_ox_mm", "rho_ox_kgm3", "dp_fuel_Pa", "mdot_ox_kgs"):
            for value in (float("nan"), float("inf"), -float("inf"), 1e308, 10**1000, [], {}):
                with self.subTest(key=key, kind=type(value).__name__):
                    result = fixture()
                    self.outputs(result)[key] = value
                    view = injector_design_view(result, "record")
                    self.assertNotEqual(view["stations"][0]["status"], "consistent")
                    json.dumps(view, allow_nan=False)

    def test_metadata_is_sanitized_and_detached(self):
        result = fixture()
        contract = result["calculation"]["components"]["injector"]["profile"]["geometry_contract"]
        contract["extra"] = {"bad": float("nan"), "nested": [float("inf")], "huge": 10**1000}
        main = self.main(result)
        self.assertIsNone(main["geometry_contract"]["extra"]["bad"])
        json.dumps(main, allow_nan=False)
        main["geometry_contract"]["assumptions"]["post_wall_thickness_mm"] = 99
        self.assertEqual(contract["assumptions"]["post_wall_thickness_mm"], .5)

    def test_bad_container_shapes_are_safe(self):
        for value in (None, [], True, "broken", {"calculation": []},
                      {"calculation": {"inputs": {"cycle_type": "ffsc"}, "components": [], "states": []}}):
            json.dumps(injector_design_view(value, float("nan")), allow_nan=False)
        result = fixture()
        for field in ("components", "states", "steady_closure"):
            broken = deepcopy(result)
            broken["calculation"][field] = []
            json.dumps(injector_design_view(broken), allow_nan=False)

    def test_preburners_only_expose_saved_boundary_values(self):
        view = injector_design_view(fixture())
        for station in view["stations"][1:]:
            self.assertEqual(station["status"], "boundary_only")
            self.assertEqual(station["boundary_status"], "consistent")
            self.assertTrue(station["boundary_available"])
            self.assertNotIn("dimensions", station)
            self.assertTrue(station["missing_geometry"])
            self.assertTrue(all(c["status"] == "pass" for c in station["checks"]))
            self.assertFalse(any("diameter" in key for p in station["ports"] for key in p))

    def test_missing_preburner_records_do_not_borrow_main_geometry(self):
        result = fixture()
        result["calculation"].pop("steady_closure")
        for station in injector_design_view(result)["stations"][1:]:
            self.assertEqual(station["status"], "boundary_only")
            self.assertEqual(station["boundary_status"], "incomplete")
            self.assertFalse(station["boundary_available"])
            self.assertIsNone(station["ports"][0]["pressure_drop_pa"])

    def test_duplicate_and_mismatched_preburner_branches_fail(self):
        result = fixture()
        branches = result["calculation"]["steady_closure"]["feed_network"]["branches"]
        branches.append(deepcopy(branches[0]))
        self.assertEqual(injector_design_view(result)["stations"][1]["boundary_status"], "inconsistent")
        result = fixture()
        result["calculation"]["states"]["branch_fuel_to_fpb"]["mass_flow"] *= 2
        self.assertEqual(injector_design_view(result)["stations"][2]["boundary_status"], "inconsistent")

    def test_preburner_drop_cannot_pass_by_retaining_saved_success_status(self):
        result = fixture()
        branch = result["calculation"]["steady_closure"]["feed_network"]["branches"][1]
        branch.update(status="pass", pressure_residual_Pa=0.)
        branch["injector_drop_Pa"] *= 100
        before = deepcopy(result)
        station = injector_design_view(result)["stations"][1]
        self.assertEqual(station["boundary_status"], "inconsistent")
        self.assertTrue(any("独立压力预算" in c["label"] and c["status"] == "fail"
                            for c in station["checks"]))
        self.assertEqual(result, before)

    def test_missing_historical_pressure_budget_is_incomplete_not_defaulted(self):
        for key in ("source_pressure_Pa", "line_drop_Pa", "valve_drop_Pa"):
            with self.subTest(key=key):
                result = fixture()
                result["calculation"]["steady_closure"]["feed_network"]["branches"][0].pop(key)
                station = injector_design_view(result)["stations"][1]
                self.assertEqual(station["boundary_status"], "incomplete")
                self.assertTrue(any("独立压力预算" in c["label"] and c["status"] == "unavailable"
                                    for c in station["checks"]))

    def test_design_preburner_component_pressure_drop_is_audited_when_saved(self):
        result = fixture()
        result["calculation"]["components"]["ox_preburner"] = {
            "outputs": {"injector_dp_Pa": 999e6}}
        self.assertEqual(injector_design_view(result)["stations"][1]["boundary_status"], "inconsistent")

    def test_fixed_resistance_uses_branch_law_not_preburner_design_ratio(self):
        result = fixture()
        network = result["calculation"]["steady_closure"]["feed_network"]
        network.update(mode="fixed_resistance", design_loss_inputs=None, hardware_snapshot={"branches": {}})
        result["calculation"]["components"]["ox_preburner"] = {
            "outputs": {"injector_dp_Pa": 999e6}}
        for branch in network["branches"]:
            branch["density_kg_m3"] = 400.
            network["hardware_snapshot"]["branches"][branch["id"]] = {
                "K_injector": branch["injector_drop_Pa"] * 400. / branch["mass_flow_kg_s"] ** 2}
        self.assertEqual(injector_design_view(result)["stations"][1]["boundary_status"], "consistent")
        # Preserve pressure arithmetic while corrupting the hydraulic law.
        branch = network["branches"][0]
        branch["injector_drop_Pa"] += 1e6
        branch["source_pressure_Pa"] += 1e6
        station = injector_design_view(result)["stations"][1]
        self.assertEqual(station["boundary_status"], "inconsistent")
        self.assertTrue(any("保存固定阻力" in c["label"] and c["status"] == "fail"
                            for c in station["checks"]))

    def test_zero_line_and_valve_loss_are_valid_pressure_budget_terms(self):
        result = fixture()
        for branch in result["calculation"]["steady_closure"]["feed_network"]["branches"]:
            branch.update(line_drop_Pa=0., valve_drop_Pa=0.,
                          source_pressure_Pa=branch["destination_pressure_Pa"] + branch["injector_drop_Pa"])
        for station in injector_design_view(result)["stations"][1:]:
            self.assertEqual(station["boundary_status"], "consistent")

    def test_unrelated_draft_does_not_change_saved_view(self):
        result = fixture()
        expected = injector_design_view(result)
        result["current_inputs"] = {"pc": 1e6, "chamber_radius": .9}
        result["submitted_inputs"] = {"pc": 1e6}
        self.assertEqual(injector_design_view(result), expected)

    def test_non_ffsc_layout_is_not_misidentified(self):
        result = fixture()
        result["calculation"]["inputs"]["cycle_type"] = "gg"
        self.assertFalse(injector_design_view(result)["available"])


if __name__ == "__main__":
    unittest.main()
