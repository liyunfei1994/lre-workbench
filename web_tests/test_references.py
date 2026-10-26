"""Public facts and compatible engineering examples retain distinct identities."""
from dataclasses import asdict
import json
import unittest
from urllib.parse import urlparse

from webapp.solver import EngineInputs, presets, reference_engines
from engine_presets import ENGINE_PRESETS, preset_input_values, presets_for_cycle


class ReferenceEngineTests(unittest.TestCase):
    def test_reference_catalog_separates_loadable_examples(self):
        rows = reference_engines()
        self.assertEqual(len({row["key"] for row in rows}), len(rows))
        available = {row["preset_key"] for row in rows if row["applicable"]}
        self.assertEqual(available, set(ENGINE_PRESETS))
        self.assertEqual(available, {"raptor3_ffsc", "tq12a_gg", "merlin1d_gg"})
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

    def test_merlin_example_uses_explicit_kerosene_surrogate_and_compatible_boundaries(self):
        merlin = next(row for row in reference_engines() if row["key"] == "merlin1d_reference")
        self.assertTrue(merlin["applicable"])
        self.assertEqual(merlin["preset_key"], "merlin1d_gg")
        self.assertIn("RP-1", merlin["propellants"])
        self.assertIn("正十二烷", merlin["compatibility_reason"])
        self.assertEqual(merlin["cycle_type"], "gg")
        self.assertTrue(any("共用单轴" in fact["value"] for fact in merlin["reference_facts"]))
        example = preset_input_values(merlin["preset_key"])
        self.assertEqual(example["propellant_pair"], "lox_kerosene")
        self.assertEqual(example["cycle_type"], "gg")
        self.assertEqual(example["cooling_bartz_mode"], 0)
        self.assertEqual(example["cooling_dp_auto"], 0)
        self.assertGreater(example["fuel_inlet_temp"], 263.6)
        self.assertIn("不构成 Merlin 1D 性能复现", merlin["model_note"])
        for key in (merlin["key"], "merlin1d", "Merlin 1D"):
            with self.subTest(key=key):
                with self.assertRaises(KeyError):
                    preset_input_values(key)
                self.assertNotIn(key, presets_for_cycle("gg"))
                self.assertNotIn(key, {row["key"] for row in presets()})

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
        first = preset_input_values("tq12a_gg")
        first["of_ratio"] = 999
        self.assertEqual(preset_input_values("tq12a_gg")["of_ratio"], 3.4)

    def test_raptor_reference_thrust_and_explicit_loss_model_keep_legacy_defaults(self):
        generic = asdict(EngineInputs())
        raptor = preset_input_values("raptor3_ffsc")
        self.assertEqual(generic["thrust"], 2450000.0)
        self.assertEqual(raptor["thrust"], 250 * 1000 * 9.80665)
        self.assertEqual({key: value for key, value in raptor.items() if value != generic[key]},
                         {"thrust": 2451662.5, "combustion_loss_model": "adiabatic"})
        self.assertEqual(generic["methane_thermo_dataset"],"gri30_highT")
        self.assertEqual(raptor["methane_thermo_dataset"],"gri30_highT")
        self.assertEqual(generic["combustion_loss_model"], "legacy_temperature_rise")
        reference = next(row for row in reference_engines() if row["key"] == "raptor3_ffsc")
        evidence = {item["name"]: item for item in reference["parameter_evidence"]}
        self.assertEqual(evidence["thrust"]["classification"], "published")
        self.assertEqual(evidence["thrust"]["source_date"], "2026-05-12")
        self.assertEqual(evidence["thrust"]["checked_on"], "2026-10-23")
        for name in ("pc", "of_ratio", "expansion_ratio"):
            self.assertEqual(evidence[name]["classification"], "engineering_assumption")
            self.assertIsNone(evidence[name]["source_url"])
        self.assertEqual(evidence["target_isp"]["classification"], "numerical_initial_guess")
        for name, item in evidence.items():
            self.assertEqual(item["value_si"], raptor[name])
        facts = reference["reference_facts"]
        self.assertTrue(any("海平面" in fact["label"] and "250 tf" in fact["value"] for fact in facts))
        self.assertTrue(any("真空" in fact["label"] and "275 tf" in fact["value"] for fact in facts))

    def test_existing_gg_calibration_is_preserved_and_not_presented_as_official(self):
        tq = preset_input_values("tq12a_gg")
        self.assertEqual((tq["thrust"], tq["pc"], tq["of_ratio"], tq["expansion_ratio"],
                          tq["combustion_efficiency"], tq["target_isp"]),
                         (829640.0, 10100000.0, 3.4, 15.0, .983, 293.0))
        for row in presets():
            self.assertIn("工程示例", row["label"])
            self.assertIn("不代表", row["source"])
        reference = next(row for row in reference_engines() if row["key"] == "tq12a_gg")
        published_mr = next(fact for fact in reference["reference_facts"] if fact["label"] == "公开混合比")
        self.assertEqual(published_mr["value"], "2.9")
        self.assertIn("模型混合比 3.4", reference["model_note"])
        self.assertIn("官网公开值 2.9", reference["model_note"])

    def test_shaft_evidence_distinguishes_engine_fact_from_example_assumption(self):
        references = {row["key"]: row for row in reference_engines()}
        merlin = references["merlin1d_reference"]
        self.assertIn("SpaceX 已公开确认单轴", merlin["shaft_note"])
        shaft_fact = next(fact for fact in merlin["reference_facts"] if fact["label"] == "轴系布置")
        self.assertEqual(urlparse(shaft_fact["source_url"]).hostname, "www.spacex.com")

        tq = references["tq12a_gg"]
        self.assertIn("工程示例采用单轴", tq["shaft_note"])
        self.assertIn("实机轴系尚缺直接公开依据", tq["shaft_note"])
        self.assertEqual(preset_input_values(tq["preset_key"])["gg_layout"], "single_shaft")
        # Family-level turbopump evidence is not a verified TQ-12A shaft fact.
        self.assertFalse(any("轴" in fact["label"] for fact in tq["reference_facts"]))
        self.assertIn("GG 轴系布局选项不适用", references["raptor3_ffsc"]["shaft_note"])

    def test_reference_facts_link_to_primary_sources(self):
        for row in reference_engines():
            sources = {source["url"] for source in row["sources"]}
            for source in sources:
                parsed = urlparse(source)
                self.assertEqual(parsed.scheme, "https")
                self.assertIn(parsed.hostname, {"www.spacex.com", "new.spacex.com", "www.landspace.com", "www.govinfo.gov", "static.sse.com.cn"})
            for fact in row["reference_facts"]:
                self.assertIn(fact["source_url"], sources)
                self.assertTrue(fact["label"])
                self.assertTrue(fact["value"])


if __name__ == "__main__":
    unittest.main()
