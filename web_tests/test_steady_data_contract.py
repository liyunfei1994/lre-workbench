"""Registration documents cannot become executable component characteristics."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from webapp.fixed_steady import configuration
from webapp.steady_model_contract import data_template, model_data_manifest, validate_data_package
from webapp.validation import fingerprint
from web_tests.test_throttle_analysis import source_fixture


def record_fixture():
    return {"id": "fixed-task", "task": configuration(source_fixture())}


def declared_package(record):
    """Declaration-only fixture: the references intentionally do not exist."""
    package = data_template(record)
    for component in package["components"]:
        component.update(revision="declaration-r1", validity="Declared scope, not verified",
                         source={"reference": "https://invalid.example/not-opened", "sha256": "a" * 64})
        for field in component["fields"]:
            field["value"] = ({"reference": "missing-artifact:" + component["id"] + "/" + field["key"],
                               "sha256": "b" * 64} if field["kind"] == "reference" else "Declaration text")
    return package


class SteadyDataContractTests(unittest.TestCase):
    def setUp(self):
        self.record = record_fixture()

    def test_new_task_records_missing_manifest_and_design_evidence_cannot_be_ready(self):
        manifest = self.record["task"]["model_data"]
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["status"], "missing_data")
        self.assertFalse(manifest["ready"])
        self.assertFalse(self.record["task"]["readiness"]["can_run"])
        required = {"pump_characteristics", "turbine_characteristics", "valve_characteristics", "feed_network",
                    "injectors", "shaft_connections", "thermal_fluid_properties", "combustion_mixing",
                    "cooling", "nozzle", "constraints"}
        self.assertEqual({item["id"] for item in manifest["items"]}, required)
        self.assertEqual(manifest["assembly"]["status"], "not_connected")
        self.assertIsNone(manifest["assembly"]["unknown_count"])
        self.assertIsNone(manifest["assembly"]["equation_count"])
        self.assertTrue(any(item["status"] == "design_evidence_only" for item in manifest["items"]))
        for item in manifest["items"]:
            self.assertIn(item["status"], ("missing", "design_evidence_only"))
            for field in item["fields"]:
                self.assertIsNone(field["value"])
                self.assertEqual(field["status"], "missing")
        first = model_data_manifest({"payload": {}})
        first["conventions"]["missing"] = "altered"
        self.assertNotEqual(model_data_manifest({"payload": {}})["conventions"]["missing"], "altered")

    def test_template_binds_exact_task_and_keeps_values_null_without_mutation(self):
        before = deepcopy(self.record)
        package = data_template(self.record)
        self.assertEqual(package["binding"], {
            "task_id": self.record["id"], "task_sha256": fingerprint(self.record["task"]),
            "hardware_id": self.record["task"]["hardware"]["id"],
            "source_result_sha256": self.record["task"]["source"]["result_sha256"]})
        for component in package["components"]:
            self.assertIsNone(component["source"])
            self.assertIsNone(component["revision"])
            self.assertIsNone(component["validity"])
            self.assertTrue(all(field["value"] is None for field in component["fields"]))
        result = validate_data_package(package, self.record)
        self.assertEqual(result["scope"], "data_declaration_only")
        self.assertTrue(result["valid"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["can_run"])
        self.assertFalse(result["adopted"])
        expected_missing = [component["id"] + "." + key for component in package["components"]
                            for key in ("revision", "source", "validity", *(field["key"] for field in component["fields"]))]
        self.assertEqual(result["missing"], expected_missing)
        package["components"][0]["fields"][0]["value"] = "not saved"
        result["binding"]["task_id"] = "not saved"
        self.assertEqual(self.record, before)

    def test_complete_declaration_never_reads_references_or_adopts_data(self):
        package = declared_package(self.record)
        before = deepcopy((package, self.record))
        with patch("builtins.open", side_effect=AssertionError("do not open artifacts")), \
             patch.object(Path, "open", side_effect=AssertionError("do not open artifacts")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("do not fetch references")):
            result = validate_data_package(package, self.record)
        self.assertEqual(result["status"], "declared_unverified")
        self.assertTrue(result["complete"])
        self.assertFalse(result["can_run"])
        self.assertFalse(result["adopted"])
        self.assertEqual(result["missing"], [])
        self.assertEqual((package, self.record), before)
        json.dumps(result, allow_nan=False)

    def test_binding_units_schema_and_unknown_fields_are_rejected(self):
        mutations = {
            "task id": lambda p: p["binding"].update(task_id="other"),
            "task hash": lambda p: p["binding"].update(task_sha256="0" * 64),
            "hardware": lambda p: p["binding"].update(hardware_id="other"),
            "source hash": lambda p: p["binding"].update(source_result_sha256="0" * 64),
            "unknown binding": lambda p: p["binding"].update(unknown=True),
            "purpose": lambda p: p.update(purpose="execute"),
            "bool version": lambda p: p.update(schema_version=True),
            "version": lambda p: p.update(schema_version=2),
            "unknown root": lambda p: p.update(execute=True),
            "unit": lambda p: p["components"][0]["fields"][0].update(unit="MPa"),
            "kind": lambda p: p["components"][0]["fields"][0].update(kind="expression"),
            "unknown component field": lambda p: p["components"][0].update(extra=1),
            "unknown value field": lambda p: p["components"][0]["fields"][0].update(extra=1),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                package = data_template(self.record)
                mutate(package)
                with self.assertRaises(ValueError):
                    validate_data_package(package, self.record)
        package = data_template(self.record)
        changed = deepcopy(self.record)
        changed["task"]["operating_conditions"]["boundaries"]["ambient_pressure"] += 1
        with self.assertRaises(ValueError):
            validate_data_package(package, changed)

    def test_duplicate_unknown_missing_components_and_fields_are_rejected(self):
        mutations = {
            "duplicate component": lambda p: p["components"][1].update(id=p["components"][0]["id"]),
            "unknown component": lambda p: p["components"][0].update(id="unknown"),
            "missing component": lambda p: p["components"].pop(),
            "extra component": lambda p: p["components"].append(deepcopy(p["components"][0])),
            "bad component id": lambda p: p["components"][0].update(id=[]),
            "duplicate field": lambda p: p["components"][0]["fields"][1].update(key=p["components"][0]["fields"][0]["key"]),
            "unknown field": lambda p: p["components"][0]["fields"][0].update(key="unknown"),
            "missing field": lambda p: p["components"][0]["fields"].pop(),
            "bad field key": lambda p: p["components"][0]["fields"][0].update(key=[]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                package = data_template(self.record)
                mutate(package)
                with self.assertRaises(ValueError):
                    validate_data_package(package, self.record)

    def test_source_digest_text_and_reference_value_types_are_checked(self):
        invalid_references = ("path-only", {}, {"reference": "artifact", "sha256": "bad"},
                              {"reference": " ", "sha256": "a" * 64},
                              {"reference": "artifact", "sha256": "g" * 64},
                              {"reference": "artifact", "sha256": 1},
                              {"reference": "artifact", "sha256": "a" * 64, "execute": True})
        for bad in invalid_references:
            for location in ("source", "reference"):
                with self.subTest(location=location, bad=bad):
                    package = declared_package(self.record)
                    component = package["components"][0]
                    if location == "source":
                        component["source"] = bad
                    else:
                        next(field for field in component["fields"] if field["kind"] == "reference")["value"] = bad
                    with self.assertRaises(ValueError):
                        validate_data_package(package, self.record)
        for bad in ("", " ", 0, False, {}, [], "x" * 4097):
            for location in ("revision", "validity", "text"):
                with self.subTest(location=location, value_type=type(bad).__name__):
                    package = declared_package(self.record)
                    component = package["components"][0]
                    if location == "text":
                        next(field for field in component["fields"] if field["kind"] == "text")["value"] = bad
                    else:
                        component[location] = bad
                    with self.assertRaises(ValueError):
                        validate_data_package(package, self.record)

    def test_historical_task_without_manifest_is_rejected_without_migration(self):
        old = deepcopy(self.record)
        old["task"].pop("model_data")
        before = deepcopy(old)
        for call in (lambda: data_template(old), lambda: validate_data_package({}, old)):
            with self.assertRaisesRegex(ValueError, "历史任务"):
                call()
        self.assertEqual(old, before)


if __name__ == "__main__":
    unittest.main()
