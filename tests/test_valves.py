"""Entity, inverse characteristic, domain and provenance checks for valves."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.components import valves as v


def configuration(mode="requirement_sizing", topology="paper_split_before_cooling_v1"):
    config = v.template_configuration(topology, mode)
    for row in config["valves"].values():
        row["design_pressure_drop_Pa"] = 1e6
        if mode == "candidate_check":
            row["rated_cda_m2"] = 1e-3
    return config


def inlet(**changes):
    return dict(backend="CoolProp::HEOS", fluid="CH4", pressure_Pa=10e6,
                temperature_K=110., density_kg_m3=500., enthalpy_J_kg=1000.,
                entropy_J_kg_K=100., isothermal_compressibility_1_Pa=2e-10,
                phase="liquid") | changes


def assess(row, **changes):
    args = dict(mass_flow_kg_s=10., upstream_pressure_Pa=10e6,
                upstream_temperature_K=110., downstream_pressure_Pa=9e6)
    return v.evaluate_valve(row, **(args | changes))


class ValveConfigurationTests(unittest.TestCase):
    def test_legacy_is_disabled_and_disabled_cannot_hide_hardware(self):
        for value in (None, "", {"schema_version": 1, "enabled": False}):
            self.assertEqual(v.parse_configuration(value), {"schema_version": 1, "enabled": False})
        for bad in ({"schema_version": True, "enabled": False},
                    {"schema_version": 1, "enabled": 1},
                    {"schema_version": 1, "enabled": False, "valves": {}},
                    '{"schema_version":1,"enabled":false,"value":NaN}'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                v.parse_configuration(bad)

    def test_templates_preserve_installation_with_explicit_initial_reserve(self):
        catalog = v.valve_configuration_catalog()
        for topology, template in catalog["templates"].items():
            with self.subTest(topology=topology):
                parsed_template = v.parse_configuration(template)
                self.assertTrue(all(row["design_pressure_drop_Pa"] == 330000
                                    for row in parsed_template["valves"].values()))
            self.assertIsNone(template["valves"]["MFV"]["rated_cda_m2"])
            parsed = v.parse_configuration(configuration(topology=topology))
            self.assertEqual(set(parsed["valves"]), set(v.VALVE_IDS))
        paper = configuration()
        cooled = configuration(topology="all_fuel_cooled_then_split_valves_v1")
        self.assertEqual(paper["valves"]["OPFV"]["upstream_node"], "fuel_after_pump")
        self.assertEqual(cooled["valves"]["OPFV"]["upstream_node"], "fuel_after_cooling")
        paper["valves"]["OPFV"]["upstream_node"] = "fuel_after_cooling"
        with self.assertRaises(ValueError):
            v.parse_configuration(paper)

    def test_recommendation_scales_with_pressure_and_never_overwrites_saved_values(self):
        template = v.template_configuration(v.RECOMMENDED_TOPOLOGY_ID, chamber_pressure_Pa=20e6)
        self.assertEqual(template["valves"]["MFV"]["design_pressure_drop_Pa"], 200000)
        template["valves"]["MFV"]["design_pressure_drop_Pa"] = 765432
        saved = json.dumps(template)
        self.assertEqual(v.parse_configuration(saved)["valves"]["MFV"]["design_pressure_drop_Pa"], 765432)
        for pressure in (None, 0, -1, True, float("nan"), float("inf")):
            with self.subTest(pressure=pressure), self.assertRaises(ValueError):
                v.recommended_design_pressure_drop(pressure)
        catalog = v.valve_configuration_catalog()
        self.assertEqual(catalog["recommended_topology_id"], v.RECOMMENDED_TOPOLOGY_ID)
        self.assertEqual(catalog["pressure_drop_recommendation"]["chamber_pressure_ratio"], .01)
        self.assertIn("未规定此 1%", catalog["pressure_drop_recommendation"]["source_scope"])

    def test_candidate_requires_actual_area_and_strict_identity(self):
        cases = []
        config = configuration("candidate_check")
        for field, value in (("rated_cda_m2", None), ("rated_cda_m2", 0),
                             ("rated_cda_m2", float("nan")), ("id", "OPFV"),
                             ("opening_max", 0), ("reference_opening", 1.5),
                             ("leak_fraction", 1), ("max_pressure_drop_ratio", .31),
                             ("design_pressure_drop_Pa", -1), ("actuator_tau_s", 0),
                             ("actuator_rate_per_s", True), ("characteristic_version", "")):
            changed = deepcopy(config)
            changed["valves"]["MFV"][field] = value
            cases.append(changed)
        changed = deepcopy(config)
        changed["topology_id"] = []
        cases.append(changed)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                v.parse_configuration(bad)

    def test_curve_defects_rejected_and_plateau_supported(self):
        for curve in ([[0, 0], [1, .9]], [[0, .1], [1, 1]],
                      [[0, 0], [.5, .8], [.7, .6], [1, 1]],
                      [[0, 0], [.5, .2], [.5, .4], [1, 1]],
                      [[0, 0], [True, 1]], [[0, 0]], [[0, 0], [1, 1, 2]]):
            config = configuration()
            config["valves"]["MFV"]["area_curve"] = curve
            with self.subTest(curve=curve), self.assertRaises(ValueError):
                v.parse_configuration(config)
        config = configuration()
        config["valves"]["MFV"]["area_curve"] = [[0, 0], [.2, .3], [.5, .3], [1, 1]]
        self.assertEqual(len(v.parse_configuration(config)["valves"]["MFV"]["area_curve"]), 4)

    def test_candidate_accepts_no_pressure_budget_but_sizing_requires_it(self):
        config = configuration("candidate_check")
        initial_hash = v.characteristic_hash(config["valves"]["MFV"])
        config["valves"]["MFV"]["design_pressure_drop_Pa"] = None
        parsed = v.parse_configuration(config)
        self.assertIsNone(parsed["valves"]["MFV"]["design_pressure_drop_Pa"])
        self.assertEqual(initial_hash, v.characteristic_hash(parsed["valves"]["MFV"]))
        config["mode"] = "requirement_sizing"
        with self.assertRaises(ValueError):
            v.parse_configuration(config)

    def test_hashes_are_canonical_versioned_and_hardware_separates_requirements(self):
        config = configuration()
        reordered = json.loads(json.dumps(config, sort_keys=True))
        reordered["valves"]["MFV"]["area_curve"] = [[0, 0], [1, 1]]
        self.assertEqual(v.configuration_hash(config), v.configuration_hash(reordered))
        row = config["valves"]["MFV"]
        baseline = v.characteristic_hash(row)
        row["design_pressure_drop_Pa"] *= 2
        row["reference_opening"] = .8
        self.assertEqual(baseline, v.characteristic_hash(row))
        row["characteristic_version"] = "concept_linear_v2"
        self.assertNotEqual(baseline, v.characteristic_hash(row))
        self.assertNotEqual(v.topology_hash(config), v.topology_hash(configuration(topology="all_fuel_cooled_then_split_valves_v1")))


class ValvePhysicsTests(unittest.TestCase):
    def setUp(self):
        self.row = configuration("candidate_check")["valves"]["MFV"]
        self.outlet_patch = patch.object(v, "_heos_outlet", return_value=inlet(pressure_Pa=9e6, vapor_pressure_Pa=1e5))
        self.outlet_patch.start()
        self.addCleanup(self.outlet_patch.stop)

    def test_characteristic_is_monotone_and_inverse_reproduces_demand(self):
        self.row["area_curve"] = [[0, 0], [.2, .04], [.5, .25], [1, 1]]
        with patch.object(v, "_heos_inlet", return_value=inlet()):
            openings = []
            for mass in (2., 5., 10., 20.):
                point = assess(self.row, mass_flow_kg_s=mass)
                self.assertEqual(point["status"], "feasible")
                openings.append(point["opening"])
                self.assertAlmostEqual(v.valve_pressure_drop(self.row, point["opening"], mass, 500.), 1e6, delta=1e-6)
            self.assertEqual(openings, sorted(openings))

    def test_requirement_does_not_invent_candidate_or_actual_diameter(self):
        row = configuration()["valves"]["MFV"]
        with patch.object(v, "_heos_inlet", return_value=inlet()):
            point = assess(row, mode="requirement_sizing")
        self.assertEqual(point["status"], "available")
        self.assertIsNone(point["opening"])
        self.assertIsNone(point["rated_cda_m2"])
        self.assertAlmostEqual(point["required_rated_cda_m2"] * row["reference_opening"], point["required_cda_m2"])
        self.assertNotIn("diameter_m", point)

    def test_limit_is_reported_and_insufficient_area_is_not_clamped(self):
        self.row["opening_max"] = .8
        flow_at_limit = .8e-3 * (2*500*1e6)**.5
        with patch.object(v, "_heos_inlet", return_value=inlet()):
            point = assess(self.row, mass_flow_kg_s=flow_at_limit)
            self.assertEqual(point["status"], "feasible")
            self.assertEqual(point["opening"], .8)
            self.assertTrue(point["saturated"])
            self.assertEqual(point["opening_margin_upper"], 0)
            failed = assess(self.row, mass_flow_kg_s=flow_at_limit*1.01)
            self.assertEqual(failed["status"], "infeasible")
            self.assertIsNone(failed["opening"])
            self.assertLess(failed["flow_margin_kg_s"], 0)

    def test_leakage_floor_and_closed_valve(self):
        self.row["leak_fraction"] = .2
        with patch.object(v, "_heos_inlet", return_value=inlet()):
            point = assess(self.row, mass_flow_kg_s=.1)
            self.assertEqual(point["reason"], "below_minimum_flow")
            self.assertIsNone(point["opening"])
        self.row["leak_fraction"] = 0
        self.assertEqual(v.valve_pressure_drop(self.row, 0., 0., 500.), 0.)
        with self.assertRaises(ValueError):
            v.valve_pressure_drop(self.row, 0., 1., 500.)

    def test_nonpositive_pressure_and_domain_failures_are_unavailable(self):
        for pressure in (10e6, 11e6):
            self.assertEqual(assess(self.row, downstream_pressure_Pa=pressure)["reason"], "nonpositive_pressure_drop")
        self.assertEqual(assess(self.row, downstream_pressure_Pa=8e6)["reason"], "pressure_drop_ratio_exceeds_model_domain")
        for state, reason in ((inlet(phase="two_phase"), "not_single_dense_phase"),
                              (inlet(density_kg_m3=2.), "density_below_dense_candidate_domain"),
                              (inlet(isothermal_compressibility_1_Pa=2e-6), "compressibility_exceeds_model_domain")):
            with self.subTest(reason=reason), patch.object(v, "_heos_inlet", return_value=state):
                point = assess(self.row)
                self.assertEqual(point["status"], "unavailable")
                self.assertEqual(point["reason"], reason)
                self.assertIsNone(point["opening"])
        with patch.object(v, "_heos_inlet", side_effect=ValueError("no EOS")):
            self.assertEqual(assess(self.row)["reason"], "inlet_properties_unavailable")

    def test_real_heos_cryogenic_candidate(self):
        try:
            import CoolProp.CoolProp  # noqa: F401
        except ImportError:
            self.skipTest("CoolProp is required for real inlet state check")
        self.outlet_patch.stop()
        point = assess(self.row)
        self.assertEqual(point["status"], "feasible", point)
        self.assertEqual(point["inlet_state"]["backend"], "CoolProp::HEOS")
        self.assertEqual(point["inlet_state"]["enthalpy_reference_id"], "CoolProp::HEOS::default")
        self.assertEqual(point["outlet_state"]["enthalpy_reference_id"], point["inlet_state"]["enthalpy_reference_id"])
        self.assertGreater(point["inlet_state"]["density_kg_m3"], 100.)
        self.assertLess(point["opening_min"], point["opening_max"])
        self.assertAlmostEqual(point["inlet_state"]["enthalpy_J_kg"], point["outlet_state"]["enthalpy_J_kg"], delta=1e-6)
        self.assertEqual(point["outlet_state"]["pressure_Pa"], point["downstream_pressure_Pa"])

    def test_outlet_flash_and_large_density_change_are_unavailable(self):
        for outlet, reason in ((inlet(phase="two_phase"), "outlet_not_single_dense_phase"),
                               (inlet(vapor_pressure_Pa=9.1e6), "outlet_at_or_below_vapor_pressure"),
                               (inlet(density_kg_m3=400.), "outlet_density_change_exceeds_model_domain")):
            with self.subTest(reason=reason), patch.object(v, "_heos_inlet", return_value=inlet()), patch.object(v, "_heos_outlet", return_value=outlet):
                point = assess(self.row)
                self.assertEqual(point["status"], "unavailable")
                self.assertEqual(point["reason"], reason)
                self.assertIsNone(point["opening"])

    def test_actuator_reuses_first_order_and_rate_limits_without_hiding_trials(self):
        self.assertAlmostEqual(v.actuator_derivative(.2, .3, .5, 1.), .2)
        self.assertEqual(v.actuator_derivative(0., 1., .1, 2.), 2.)
        self.assertEqual(v.actuator_derivative(1., 0., .1, 2.), -2.)
        self.assertLess(v.actuator_derivative(1.001, 1., .1, 2.), 0.)
        with self.assertRaises(ValueError):
            v.actuator_derivative(.5, 1.1, .1, 2.)
        with self.assertRaises(ValueError):
            v.actuator_derivative(.5, .8, 0., 2.)


if __name__ == "__main__":
    unittest.main()
