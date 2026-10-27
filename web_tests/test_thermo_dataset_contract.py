"""Historical identity, source-bound comparison and saved scientific provenance."""
from dataclasses import asdict
import unittest

from webapp.solver import EngineInputs, input_values, science_environment
from webapp.validation import normalize_request
from webapp.ui_schema import design_ui_schema
from engine_presets import preset_input_values
from thermo_data import methane_dataset_catalog


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

    def test_daily_editor_uses_read_only_catalog_without_losing_legacy_identity(self):
        schema = design_ui_schema()
        self.assertNotIn("methane_thermo_dataset", {
            f["key"] for g in schema["system_groups"] for f in g["fields"]})
        field = next(f for g in schema["groups"] for f in g["fields"] if f["key"] == "methane_thermo_dataset")
        self.assertEqual(field["default"], "gri30_highT")
        self.assertEqual(field["propellants"], ["lox_methane"])
        self.assertEqual({choice["value"] for choice in field["choices"]}, {"gri30", "gri30_highT"})
        catalog = {item["id"]: item for item in schema["methane_thermo_datasets"]}
        self.assertTrue(catalog["gri30"]["historical"])
        self.assertFalse(catalog["gri30_highT"]["historical"])

    def test_catalog_reads_installed_species_limits_and_file_identity(self):
        import cantera as ct
        import hashlib
        from pathlib import Path

        for item in methane_dataset_catalog():
            raw = (Path(ct.__file__).resolve().parent / "data" / item["file"]).read_bytes()
            gas = ct.Solution(yaml=raw.decode("utf-8"), name=item["phase"])
            self.assertEqual(item["common_temperature_range_K"],
                             [max(s.thermo.min_temp for s in gas.species()),
                              min(s.thermo.max_temp for s in gas.species())])
            self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(item["gas_eos"], gas.thermo_model)
            self.assertEqual(item["cantera_version"], ct.__version__)
        # Consumers cannot alter the catalog held by the server.
        catalog = methane_dataset_catalog()
        catalog[0]["common_temperature_range_K"][0] = -1
        self.assertGreater(methane_dataset_catalog()[0]["common_temperature_range_K"][0], 0)


if __name__ == "__main__":
    unittest.main()
