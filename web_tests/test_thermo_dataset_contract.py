"""Historical identity, source-bound comparison and saved scientific provenance."""
from dataclasses import asdict
import unittest

from webapp.solver import EngineInputs, input_values, science_environment
from webapp.validation import normalize_request
from webapp.ui_schema import design_ui_schema
from engine_presets import preset_input_values


class DatasetContractTests(unittest.TestCase):
    def test_old_snapshot_is_not_migrated_but_new_reference_is_explicit(self):
        old = asdict(EngineInputs())
        old.pop("methane_thermo_dataset")
        self.assertEqual(input_values(old, saved_snapshot=True)[0]["methane_thermo_dataset"], "gri30")
        self.assertNotIn("methane_thermo_dataset", old)
        for patch in ({}, {"pc": 33e6}, old):
            self.assertEqual(input_values(patch)[0]["methane_thermo_dataset"], "gri30_highT")
        for dataset in ("gri30", "gri30_highT"):
            for snapshot in (True, False):
                self.assertEqual(input_values({"methane_thermo_dataset": dataset}, saved_snapshot=snapshot)[0]["methane_thermo_dataset"], dataset)
        self.assertEqual(preset_input_values("raptor3_ffsc")["methane_thermo_dataset"], "gri30_highT")

    def test_science_fingerprint_changes_with_species_data_not_eos(self):
        old = science_environment("lox_methane", "gri30")
        new = science_environment("lox_methane", "gri30_highT")
        self.assertNotEqual(old["mechanism_sha256"], new["mechanism_sha256"])
        self.assertNotEqual(old["fingerprint"], new["fingerprint"])
        self.assertEqual(new["physics"]["mechanism"], "gri30_highT.yaml")
        self.assertEqual(new["thermodynamic_scope"]["gas_eos"], "ideal-gas")
        self.assertEqual(new["thermodynamic_scope"]["high_pressure_mixture_validation"], "not_validated")

    def test_validation_inherits_immutable_source_even_when_request_omits_field(self):
        source = {"id": "source", "inputs": {"methane_thermo_dataset": "gri30_highT"}}
        result = normalize_request({"thermochemistry": {}}, source)
        self.assertEqual(result["thermochemistry"]["methane_thermo_dataset"], "gri30_highT")
        with self.assertRaisesRegex(ValueError, "来源"):
            normalize_request({"thermochemistry": {"methane_thermo_dataset": "gri30"}}, source)
        self.assertEqual(source["inputs"], {"methane_thermo_dataset": "gri30_highT"})

    def test_new_selector_is_methane_only_and_defaults_to_high_temperature_data(self):
        schema = design_ui_schema()
        field = next(f for g in schema["system_groups"] for f in g["fields"] if f["key"] == "methane_thermo_dataset")
        self.assertEqual(field["default"], "gri30_highT")
        self.assertEqual(field["propellants"], ["lox_methane"])
        self.assertEqual({choice["value"] for choice in field["choices"]}, {"gri30", "gri30_highT"})


if __name__ == "__main__":
    unittest.main()
