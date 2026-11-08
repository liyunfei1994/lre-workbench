"""A fixed task carries hardware evidence and cannot silently run a design."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from webapp.fixed_steady import assert_runnable, configuration, normalize_request
from lre_workbench.domain.analysis_roles import analysis_parameter_roles
from webapp.validation import fingerprint
from web_tests.test_throttle_analysis import source_fixture


class FixedSteadyContractTests(unittest.TestCase):
    def test_mode_roles_separate_hardware_controls_outputs_and_conditional_targets(self):
        task = configuration(source_fixture())
        self.assertEqual(task["schema_version"], 2)
        self.assertEqual(task["parameter_roles"], analysis_parameter_roles())
        rows = {row["id"]: row for row in task["parameter_roles"]["rows"]}
        # Pump structure and design-only sizing assumptions have distinct roles.
        self.assertEqual(set(rows), {"preburner_temperature", "preburner_distribution",
            "pump_outlet_pressure", "shaft_speed", "valve_hardware", "valve_opening",
            "chamber_pressure", "thrust", "overall_mixture_ratio", "geometry",
            "pump_stages", "pump_sizing_assumptions", "turbine_stages", "turbine_sizing_assumptions"})
        fixed = {key: row["fixed_hardware_steady"]["roles"] for key, row in rows.items()}
        self.assertEqual(fixed["valve_hardware"], ["hardware_definition"])
        self.assertIn("control_setting", fixed["valve_opening"])
        self.assertNotIn("hardware_definition", fixed["valve_opening"])
        self.assertEqual(fixed["geometry"], ["hardware_definition"])
        self.assertEqual(fixed["pump_stages"], ["hardware_definition"])
        self.assertEqual(fixed["pump_sizing_assumptions"], ["design_only"])
        self.assertEqual(fixed["turbine_stages"], ["hardware_definition"])
        self.assertEqual(fixed["turbine_sizing_assumptions"], ["design_only"])
        for key in ("preburner_temperature", "preburner_distribution", "pump_outlet_pressure",
                    "shaft_speed", "chamber_pressure", "thrust", "overall_mixture_ratio"):
            self.assertIn("computed_output", fixed[key])
        self.assertIn("constraint", fixed["preburner_temperature"])
        self.assertIn("conditional_external_boundary", fixed["shaft_speed"])
        for key in ("thrust", "overall_mixture_ratio"):
            self.assertIn("conditional_control_target", fixed[key])
        self.assertIn("design_target", rows["thrust"]["concept_design"]["roles"])

    def test_design_pressure_and_line_budget_remain_unadopted_source_assumptions(self):
        source = source_fixture()
        task = configuration(source)
        self.assertEqual(set(task["operating_conditions"]["boundaries"]),
                         {"ambient_pressure", "tank_pressure_pa", "fuel_inlet_temp", "ox_inlet_temp"})
        self.assertEqual(task["hardware"]["payload"]["valves"], {})
        self.assertEqual(task["operating_conditions"]["controls"], {"valve_openings": {}})
        self.assertEqual(task["operating_conditions"]["control_targets"], {})
        assumptions = task["legacy_design_assumptions"]
        self.assertEqual(set(assumptions), {"inlet_pressure", "feed_line_dp_pa"})
        for key, role in (("inlet_pressure", "design_target"), ("feed_line_dp_pa", "model_assumption")):
            self.assertEqual(assumptions[key]["value"], source["result"]["calculation"]["inputs"][key])
            self.assertEqual(assumptions[key]["role"], role)
            self.assertEqual(assumptions[key]["unit"], "Pa")
            self.assertFalse(assumptions[key]["adopted_as_boundary"])
        # None of these concept settings may become hardware, control, a
        # prescribed operating state, or even a misnamed numerical seed.
        forbidden = {"pc", "thrust", "of_ratio", "lox_pump_speed_rpm_override",
                     "fuel_pump_speed_rpm_override", "ox_preburner_temp_target",
                     "fuel_preburner_temp_target", "use_target_preburner_temp",
                     "ox_preburner_mr_target", "fuel_preburner_mr_target"}
        def keys(value):
            if isinstance(value, dict):
                return set(value).union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value))
            return set()
        for group in ("hardware", "operating_conditions", "initial_guess"):
            self.assertFalse(keys(task[group]) & forbidden, group)

    def test_freezes_only_available_hardware_and_named_actual_initial_values(self):
        source = source_fixture()
        source["inputs"]["pc"] = 999e6  # Submitted edits are not solved evidence.
        source["result"]["calculation"]["convergence"]["solution"] = {
            "x": [6, 1.8, 2], "chamber_temp_guess": 3000, "chamber_gamma": 1.2, "molar_mass": 20}
        before = deepcopy(source)
        task = configuration(source)
        self.assertEqual(task["mode"], "fixed_hardware_steady")
        self.assertEqual(task["source"], {"job_id": "design", "project_id": "project", "revision": 3,
                                         "result_sha256": fingerprint(source["result"])})
        self.assertEqual(task["hardware"]["id"], fingerprint(task["hardware"]["payload"]))
        self.assertEqual(task["hardware"]["payload"]["nozzle"]["throat_area_m2"], .05)
        self.assertEqual(task["initial_guess"], {"mode": "fixed_hardware_steady", "variables": {
            "mdot_fuel_kg_s": 2., "mdot_ox_kg_s": 4., "ox_preburner_mr": 6., "fuel_preburner_mr": 2/3}})
        for group in (task["hardware"], task["operating_conditions"], task["initial_guess"]):
            for forbidden in ("temp_target", "mr_target", "use_target_", "cooling_channel_auto_size", "\"x\""):
                self.assertNotIn(forbidden, str(group))
        self.assertFalse(task["readiness"]["can_run"])
        codes = {row["code"] for row in task["readiness"]["blockers"]}
        self.assertTrue({"fixed_steady_solver_unavailable", "pump_turbine_maps_missing",
                         "valve_characteristics_missing", "cooling_budget_only"} <= codes)
        task["hardware"]["payload"]["feed"]["branches"]["fuel_to_opb"]["K_line"] *= 2
        self.assertEqual(source, before)

    def test_operating_conditions_and_initial_guesses_do_not_redefine_hardware(self):
        source = source_fixture()
        baseline = configuration(source)
        task = normalize_request({"source_job_id": "design", "operating_conditions": {
            "boundaries": {"ambient_pressure": 0, "tank_pressure_pa": 800000},
            "controls": {"valve_openings": {}}}, "initial_guess": {
            "mode": "fixed_hardware_steady", "variables": {"mdot_fuel_kg_s": 1.2, "ox_preburner_mr": 55}}}, source)
        self.assertEqual(task["hardware"], baseline["hardware"])
        self.assertEqual(task["source"], baseline["source"])
        self.assertEqual(task["operating_conditions"]["boundaries"]["ambient_pressure"], 0.)
        self.assertEqual(task["initial_guess"]["variables"]["ox_preburner_mr"], 55)
        self.assertEqual(task["constraints"], baseline["constraints"])
        self.assertEqual(source["result"]["calculation"]["summary"]["ox_preburner_mr"], 6)

    def test_no_temperature_target_fallback_and_constraints_keep_evidence(self):
        source = source_fixture()
        calculation = source["result"]["calculation"]
        calculation["summary"].pop("ox_preburner_mr")
        calculation["summary"]["chamber_temperature_K"] = None
        calculation["inputs"].update(ox_preburner_temp_limit_status="documented",
                                     ox_preburner_temp_limit_basis="project registered limit")
        task = configuration(source)
        self.assertNotIn("ox_preburner_mr", task["initial_guess"]["variables"])
        self.assertNotIn("chamber_temperature_K", task["initial_guess"]["variables"])
        self.assertEqual(task["constraints"]["ox_preburner_temperature"]["status"], "documented")
        self.assertEqual(task["constraints"]["ox_preburner_temperature"]["basis"], "project registered limit")
        self.assertIsNone(task["constraints"]["cooling_wall_temperature"]["limit_K"])

    def test_forbidden_equalities_geometry_and_untyped_warm_vectors_are_rejected(self):
        source = source_fixture()
        rejected = [
            {"source_job_id": "wrong"},
            {"hardware": {}}, {"readiness": {"can_run": True}}, {"throttle_min": .4},
            {"design_targets": {"ox_preburner_temp_target": 750}},
            {"initial_guess": {"x": [6, 1.8, 2]}},
            {"initial_guess": {"mode": "concept_design", "variables": {}}},
            {"initial_guess": {"variables": {"ox_preburner_mr_target": 55}}},
            {"initial_guess": {"variables": {"unknown": 2}}},
            {"operating_conditions": {"controls": {"ox_preburner_temp_target": 750}}},
            {"operating_conditions": {"controls": {"valve_openings": {"ox": .5}}}},
            {"operating_conditions": {"control_targets": {"thrust": 1e6}}},
            {"operating_conditions": {"control_targets": {"of_ratio": 3.6}}},
            {"operating_conditions": {"control_targets": {"ox_preburner_temp_target": 750}}},
            {"legacy_design_assumptions": {}},
        ]
        for name in ("ox_preburner_temp_target", "fuel_preburner_temp_target", "of_ratio", "pc",
                     "ox_preburner_mr_target", "throat_area_m2", "cooling_channel_auto_size",
                     "inlet_pressure", "feed_line_dp_pa", "thrust", "lox_pump_speed_rpm_override",
                     "fuel_pump_speed_rpm_override"):
            rejected.append({"operating_conditions": {"boundaries": {name: 1}}})
        for bad in (True, None, "1", 0, -1, float("nan"), float("inf"), 10**1000):
            rejected.extend(({"initial_guess": {"variables": {"mdot_fuel_kg_s": bad}}},
                             {"operating_conditions": {"boundaries": {"tank_pressure_pa": bad}}}))
        rejected.append({"operating_conditions": {"boundaries": {"inlet_pressure": 1, "feed_line_dp_pa": 2}}})
        for changes in rejected:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_request({"source_job_id": "design", **changes}, source)

    def test_reaudits_source_and_unconditionally_blocks_execution(self):
        source = source_fixture()
        with patch("webapp.fixed_steady.source_baseline", side_effect=ValueError("bad source evidence")) as audit:
            with self.assertRaisesRegex(ValueError, "bad source evidence"):
                configuration(source)
            audit.assert_called_once_with(source)
        bad = deepcopy(source)
        bad["result"]["calculation"]["states"]["fuel_after_cooling"]["enthalpy"] = -300
        with self.assertRaisesRegex(ValueError, "能量守恒"):
            configuration(bad)
        task = configuration(source)
        task["readiness"] = {"can_run": True, "status": "ready", "blockers": []}
        with patch('lre_workbench.systems.ffsc.FFSCCycleModel') as model:
            with self.assertRaisesRegex(ValueError, "固定硬件稳态暂不可运行"):
                assert_runnable(task)
            model.assert_not_called()
        with self.assertRaises(ValueError):
            assert_runnable({"mode": "concept_design"})


if __name__ == "__main__":
    unittest.main()
