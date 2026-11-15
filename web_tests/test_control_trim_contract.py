"""Outer task definitions cannot silently acquire physics or control authority."""
from copy import deepcopy
import math
import unittest

from webapp import control_trim
from webapp.fixed_steady import configuration as fixed_configuration
from webapp.validation import fingerprint
from web_tests.test_throttle_analysis import source_fixture


class ControlTrimContractTests(unittest.TestCase):
    def setUp(self):
        self.source = source_fixture()
        self.source["result"]["calculation"]["summary"]["thrust_N"] = 912345.
        self.record = self.record_for(self.source)

    @staticmethod
    def record_for(source):
        return {"id": "fixed-task", "task": fixed_configuration(source)}

    def define(self, **parts):
        return control_trim.normalize_request({"thrust_percent": 40, **parts}, self.record, self.source)

    def test_percent_is_bound_to_saved_output_and_never_inherits_other_targets(self):
        original = deepcopy((self.record, self.source))
        definition = self.define()
        self.assertEqual(definition["mode"], "fixed_hardware_steady_trim")
        self.assertEqual(definition["scope"], "task_definition_only")
        reference = definition["reference_thrust"]
        self.assertEqual(reference["value_N"], 912345.)
        self.assertEqual(reference["basis"], "saved_concept_output")
        self.assertFalse(reference["validated_fixed_hardware"])
        self.assertEqual(len(definition["targets"]), 1)
        self.assertEqual(definition["targets"][0]["unit"], "N")
        self.assertAlmostEqual(definition["targets"][0]["value"], .4 * 912345.)
        binding = definition["binding"]
        self.assertEqual(binding["task_id"], self.record["id"])
        self.assertEqual(binding["task_sha256"], fingerprint(self.record["task"]))
        self.assertEqual(binding["hardware_id"], self.record["task"]["hardware"]["id"])
        self.assertEqual(binding["source_result_sha256"], fingerprint(self.source["result"]))
        self.assertEqual((self.record, self.source), original)

    def test_optional_targets_are_explicit_and_control_count_is_not_independence(self):
        definition = self.define(additional_targets={
            "overall_of": {"value": 3.5, "tolerance": .01},
            "ox_preburner_temperature": {"value": 760., "tolerance": 2.},
            "fuel_preburner_temperature": {"value": 860., "tolerance": 2.},
        })
        self.assertEqual(len(definition["targets"]), 4)
        self.assertEqual(definition["controls"], [])
        dof = definition["control_dof"]
        self.assertEqual(dof["control_count"], 0)
        self.assertEqual(dof["target_count"], 4)
        self.assertFalse(dof["count_sufficient"])
        self.assertEqual(dof["independence"], "not_checked")
        self.assertIsNone(dof["rank"])
        self.assertFalse(definition["readiness"]["can_run"])
        self.assertEqual(self.record["task"]["operating_conditions"]["control_targets"], {})

    def test_missing_output_has_no_fallback_to_design_target(self):
        for bad in (None, 0, -1, True, "1000000"):
            with self.subTest(value=bad):
                source = deepcopy(self.source)
                if bad is None:
                    source["result"]["calculation"]["summary"].pop("thrust_N")
                else:
                    source["result"]["calculation"]["summary"]["thrust_N"] = bad
                record = self.record_for(source)
                definition = control_trim.normalize_request({"thrust_percent": 40}, record, source)
                self.assertIsNone(definition["reference_thrust"]["value_N"])
                self.assertIsNone(definition["targets"][0]["value"])
                self.assertIsNone(definition["targets"][0]["tolerance"])
                self.assertFalse(definition["readiness"]["can_run"])

    def test_source_or_hardware_identity_changes_are_rejected(self):
        source = deepcopy(self.source)
        source["result"]["calculation"]["summary"]["thrust_N"] += 1
        with self.assertRaises(ValueError):
            control_trim.configuration(self.record, source)
        changed = deepcopy(self.record)
        changed["task"]["hardware"]["payload"]["nozzle"]["throat_area_m2"] *= 2
        with self.assertRaises(ValueError):
            control_trim.configuration(changed, self.source)

    def test_client_cannot_invent_valves_design_targets_or_readiness(self):
        invalid = (
            {"controls": [{"name": "fuel_to_opb", "lower": 0, "upper": 1, "initial": .4}]},
            {"controls": ["fuel_to_opb"]},
            {"hardware": {"valves": {"invented": {}}}},
            {"readiness": {"can_run": True}},
            {"reference_thrust": {"value_N": 1e6}},
            {"ox_preburner_temp_target": 750},
            {"additional_targets": {"ox_preburner_mr": {"value": 60, "tolerance": .1}}},
            {"additional_targets": {"overall_of": {"value": 3.5, "tolerance": .01, "source": "implicit"}}},
        )
        for bad in invalid:
            with self.subTest(request=bad), self.assertRaises(ValueError):
                self.define(**bad)

    def test_numeric_and_container_validation_happens_before_saved_definition(self):
        for key in ("thrust_percent", "thrust_relative_tolerance"):
            for value in (True, 0, -1, math.inf, math.nan, "40", None, []):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.define(**{key: value})
        for bad in (None, [], True, "{}", {}):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                control_trim.normalize_request(bad, self.record, self.source)
        for target in ({"value": 3.5}, {"value": True, "tolerance": .01},
                       {"value": 3.5, "tolerance": 0}, None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.define(additional_targets={"overall_of": target})

    def test_saved_flags_and_complete_data_declarations_cannot_enable_execution(self):
        definition = self.define()
        definition["readiness"] = {"can_run": True, "status": "ready", "blockers": []}
        definition["control_dof"].update(control_count=4, count_sufficient=True, rank=4,
                                         independence="full_row_rank")
        with self.assertRaises(ValueError):
            control_trim.assert_runnable(definition)
        config = control_trim.configuration(self.record, self.source)
        self.assertEqual(config["available_controls"], [])
        self.record["task"]["readiness"] = {"can_run": True, "status": "ready", "blockers": []}
        self.record["task"]["model_data"]["ready"] = True
        self.assertFalse(self.define()["readiness"]["can_run"])


if __name__ == "__main__":
    unittest.main()
