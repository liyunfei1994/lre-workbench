"""Saved valve requirements stay distinct from unprovided valve capabilities."""
from copy import deepcopy
import json
import unittest

from lre_workbench.systems.steady_network import BRANCHES
from webapp.valve_requirements import valve_requirements_view, MISSING_HARDWARE_FIELDS


def fixture():
    branches, states = [], {}
    for index, (key, label, propellant, source, target) in enumerate(BRANCHES):
        branches.append({"id": key, "label": label, "propellant": propellant,
                         "source": source, "target": target, "mass_flow_kg_s": index + 1,
                         "source_pressure_Pa": 10e6, "destination_pressure_Pa": 8e6,
                         "line_drop_Pa": .2e6, "injector_drop_Pa": 1e6,
                         "valve_drop_Pa": 0 if index % 2 else .8e6,
                         "loss_source": "specified_design_pressure_budget",
                         "density_kg_m3": 999., "temperature_K": 999.,
                         "temperature_location": "after_adiabatic_supply_loss"})
        states[source] = {"pressure": 10e6, "temperature": 100., "density": 500.,
                          "enthalpy": -100000., "composition": {"CH4" if propellant == "fuel" else "O2": 1.},
                          "metadata": {"reference_id": "saved-reference"}}
        states["branch_" + key] = {"pressure": 8e6, "temperature": 110.}
    return {"calculation": {"inputs": {"cycle_type": "ffsc"}, "states": states,
                            "steady_closure": {"feed_network": {"status": "pass", "branches": branches}}}}


class ValveRequirementsTests(unittest.TestCase):
    def test_exact_saved_values_locations_and_paths_without_capability_claim(self):
        result = fixture()
        before = deepcopy(result)
        view = valve_requirements_view(result, "saved-job")
        self.assertEqual(view["record_id"], "saved-job")
        self.assertEqual(view["status"], "review_required")
        self.assertEqual([row["id"] for row in view["rows"]], [row[0] for row in BRANCHES])
        first = view["rows"][0]["quantities"]
        self.assertEqual(first["mass_flow"]["value"], 1)
        self.assertEqual(first["mass_flow"]["source_path"],
                         "calculation.steady_closure.feed_network.branches[0].mass_flow_kg_s")
        self.assertEqual(first["source_tap_temperature"]["value"], 100.)
        self.assertEqual(first["density"]["value"], 500.)
        self.assertEqual(first["source_tap_enthalpy"]["value"], -100000.)
        self.assertEqual(first["supply_end_temperature"]["value"], 110.)
        self.assertEqual(first["source_tap_temperature"]["source_path"],
                         "calculation.states.fuel_after_cooling.temperature")
        self.assertEqual(view["rows"][1]["quantities"]["extra_valve_drop"]["value"], 0)
        self.assertEqual(view["rows"][1]["quantities"]["extra_valve_drop"]["status"], "recorded")
        self.assertIn("零", "".join(view["limitations"]))
        self.assertEqual(view["missing_fields"], list(MISSING_HARDWARE_FIELDS))
        self.assertEqual(result, before)
        view["rows"][0]["source_composition"]["CH4"] = 0
        self.assertEqual(result, before)
        json.dumps(view, allow_nan=False)

    def test_missing_and_invalid_values_are_not_filled_from_branch_or_summary(self):
        result = fixture()
        result["calculation"]["summary"] = {"valve_line_dp_MPa": 8}
        branch = result["calculation"]["steady_closure"]["feed_network"]["branches"][0]
        tap = result["calculation"]["states"]["fuel_after_cooling"]
        for key in ("temperature", "density", "enthalpy"):
            tap.pop(key)
        result["calculation"]["states"].pop("branch_fuel_to_opb")
        for field, bad in (("mass_flow_kg_s", True), ("source_pressure_Pa", float("nan")),
                           ("destination_pressure_Pa", float("inf")), ("line_drop_Pa", -1),
                           ("injector_drop_Pa", 10**1000), ("valve_drop_Pa", None)):
            branch[field] = bad
        view = valve_requirements_view(result)
        self.assertEqual(view["status"], "incomplete")
        for quantity in view["rows"][0]["quantities"].values():
            self.assertEqual(quantity["status"], "missing")
            self.assertIsNone(quantity["value"])
            self.assertTrue(quantity["reason"])
        json.dumps(view, allow_nan=False)

    def test_unknown_missing_duplicate_and_wrong_topology_are_not_invented_valves(self):
        for mutation in ("missing", "duplicate", "wrong_port", "wrong_fluid"):
            result = fixture()
            branches = result["calculation"]["steady_closure"]["feed_network"]["branches"]
            if mutation == "missing":
                branches.pop(0)
            elif mutation == "duplicate":
                branches.append(deepcopy(branches[0]))
            elif mutation == "wrong_port":
                branches[0]["source"] = "unregistered_port"
            else:
                branches[0]["propellant"] = "other"
            with self.subTest(mutation=mutation):
                view = valve_requirements_view(result)
                self.assertEqual(view["status"], "incomplete")
                self.assertEqual(len(view["rows"]), 4)
                self.assertIsNone(view["rows"][0]["source"])
                self.assertTrue(all(q["status"] == "missing" for q in view["rows"][0]["quantities"].values()))
        result = fixture()
        result["calculation"]["steady_closure"]["feed_network"]["branches"].append({"id": "another"})
        self.assertEqual(len(valve_requirements_view(result)["rows"]), 4)
        self.assertTrue(valve_requirements_view(result)["warnings"])

    def test_no_network_or_other_cycle_is_explicit_and_failed_source_stays_blocked(self):
        for result in (None, {}, {"calculation": {}}, {"calculation": {"inputs": {"cycle_type": "ffsc"}}}):
            view = valve_requirements_view(result)
            self.assertEqual(view["status"], "unavailable")
            self.assertEqual(view["rows"], [])
        result = fixture()
        result["calculation"]["inputs"]["cycle_type"] = "gg"
        self.assertEqual(valve_requirements_view(result)["status"], "not_applicable")
        result = fixture()
        result["calculation"]["steady_closure"]["feed_network"]["status"] = "fail"
        self.assertEqual(valve_requirements_view(result)["status"], "blocked")

    def test_invalid_optional_metadata_cannot_leak_nonfinite_json(self):
        result = fixture()
        tap = result["calculation"]["states"]["fuel_after_cooling"]
        tap["composition"] = {"CH4": float("nan")}
        tap["metadata"]["reference_id"] = float("inf")
        result["calculation"]["steady_closure"]["feed_network"]["branches"].append({"id": []})
        view = valve_requirements_view(result)
        self.assertEqual(view["rows"][0]["source_composition"], {})
        self.assertIsNone(view["rows"][0]["source_reference_id"])
        json.dumps(view, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
