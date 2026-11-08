"""Public facts and compatible engineering examples retain distinct identities."""
from dataclasses import asdict
from copy import deepcopy
import json
import unittest
from urllib.parse import urlparse

from webapp.solver import EngineInputs, presets, reference_engines
from lre_workbench.application.presets import ENGINE_PRESETS, ENGINE_REFERENCES, preset_input_values, presets_for_cycle


def retained_reference(key):
    """Historical reference evidence remains in the underlying catalog only."""
    return {"key": key, **deepcopy(ENGINE_REFERENCES[key])}


class ReferenceEngineTests(unittest.TestCase):
    def test_reference_catalog_separates_loadable_examples(self):
        rows = reference_engines()
        self.assertEqual(len({row["key"] for row in rows}), len(rows))
        available = {row["preset_key"] for row in rows if row["applicable"]}
        self.assertEqual(available, {row["key"] for row in presets()})
        self.assertEqual(available, {"raptor3_ffsc"})
        self.assertTrue(all(row["cycle_type"] == "ffsc" for row in rows))
        self.assertTrue(all(row["inputs"]["propellant_pair"] == "lox_methane" for row in presets()))
        self.assertEqual({row["key"] for row in presets()}, {"raptor3_ffsc"})
        for row in rows:
            with self.subTest(engine=row["key"]):
                self.assertNotIn("inputs", row)
                self.assertNotIn("overrides", row)
                self.assertTrue(row["compatibility_reason"])
                self.assertTrue(row["model_note"])
                self.assertTrue(row["shaft_note"])
                self.assertTrue(row["sources"])
                if row["applicable"]:
                    self.assertEqual(preset_input_values(row["preset_key"])["cycle_type"], row["cycle_type"])
                else:
                    self.assertIsNone(row["preset_key"])
        json.dumps(rows, allow_nan=False)


    def test_reference_catalog_is_independent_of_mutable_inputs(self):
        baseline = presets()
        catalog = reference_engines()
        original_catalog = reference_engines()
        catalog[0]["model_note"] = "changed by a caller"
        catalog[0]["sources"][0]["url"] = "https://example.invalid/"
        catalog[-1]["reference_facts"][0]["value"] = "incorrect methane substitution"
        self.assertEqual(reference_engines(), original_catalog)
        self.assertEqual(presets(), baseline)
        # Loading the calculable example also returns a new input dictionary.
        first = preset_input_values("raptor3_ffsc")
        first["of_ratio"] = 999
        self.assertEqual(preset_input_values("raptor3_ffsc")["of_ratio"], 3.6)

    def test_raptor_example_explicitly_sets_thrust_loss_model_and_provisional_limits(self):
        generic = asdict(EngineInputs())
        raptor = preset_input_values("raptor3_ffsc")
        self.assertEqual(generic["thrust"], 2450000.0)
        self.assertEqual(raptor["thrust"], 250 * 1000 * 9.80665)
        self.assertEqual({key: value for key, value in raptor.items() if value != generic[key]},
                         {"thrust": 2451662.5, "combustion_loss_model": "adiabatic",
                          "ox_preburner_temp_limit_status": "provisional",
                          "fuel_preburner_temp_limit_status": "provisional"})
        self.assertEqual(generic["methane_thermo_dataset"],"gri30_highT")
        self.assertEqual(raptor["methane_thermo_dataset"],"gri30_highT")
        self.assertEqual(generic["combustion_loss_model"], "legacy_temperature_rise")
        reference = next(row for row in reference_engines() if row["key"] == "raptor3_ffsc")
        evidence = {item["name"]: item for item in reference["parameter_evidence"]}
        self.assertEqual(evidence["thrust"]["classification"], "published")
        self.assertEqual(evidence["thrust"]["source_date"], "2026-05-12")
        self.assertEqual(evidence["thrust"]["checked_on"], "2026-11-05")
        self.assertEqual(evidence["pc"]["classification"], "engineering_assumption")
        self.assertIsNone(evidence["pc"]["source_url"])
        for name in ("of_ratio", "expansion_ratio"):
            self.assertEqual(evidence[name]["classification"], "historical_reference")
            self.assertEqual(evidence[name]["source_date"], "2019-06-18")
            self.assertEqual(urlparse(evidence[name]["source_url"]).hostname, "www.faa.gov")
            self.assertIn("Raptor 3", evidence[name]["scope"])
            self.assertIn("第 1 页", evidence[name]["source_locator"])
        self.assertEqual(evidence["target_isp"]["classification"], "numerical_initial_guess")
        for name, item in evidence.items():
            self.assertEqual(item["value_si"], raptor[name])
            if item["classification"] in {"engineering_assumption", "numerical_initial_guess"}:
                self.assertNotIn("checked_on", item)
                self.assertIsNone(item["source_url"])
                self.assertIn(name, item["source_path"])
        for name in ("ox_preburner_mr_target", "fuel_preburner_mr_target", "cooling_dp_pa"):
            self.assertEqual(evidence[name]["classification"], "numerical_initial_guess")
        self.assertIn("暂定", evidence["ox_preburner_temp_limit"]["note"])
        self.assertIn("设计暂定", evidence["fuel_preburner_temp_limit"]["note"])
        self.assertIn("不是通用工程上限或材料允许温度", evidence["fuel_preburner_temp_limit"]["note"])
        facts = reference["reference_facts"]
        self.assertTrue(any("海平面" in fact["label"] and "250 tf" in fact["value"] for fact in facts))
        self.assertTrue(any("真空" in fact["label"] and "275 tf" in fact["value"] for fact in facts))



    def test_reference_facts_link_to_primary_sources(self):
        for row in reference_engines():
            sources = {source["url"] for source in row["sources"]}
            for source in sources:
                parsed = urlparse(source)
                self.assertEqual(parsed.scheme, "https")
                self.assertIn(parsed.hostname, {"www.spacex.com", "new.spacex.com", "www.landspace.com", "www.govinfo.gov", "static.sse.com.cn", "www.faa.gov", "www.nist.gov"})
            for fact in row["reference_facts"]:
                self.assertIn(fact["source_url"], sources)
                self.assertTrue(fact["label"])
                self.assertTrue(fact["value"])


if __name__ == "__main__":
    unittest.main()
