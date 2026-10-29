"""Actual hot-stream HP budgets cannot be replaced by fresh reactant defaults."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.chamber_handoff_compare import compare_chamber_handoff
from webapp.validation import normalize_request, calculate_validation, fingerprint
from webapp.validation_checks import conservation_checks
from tests.test_gas_handoff import fixture


class ChamberHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = fixture()
        cls.record["calculation"]["inputs"] = {"cycle_type": "ffsc", "propellant_pair": "lox_methane",
                                                "methane_thermo_dataset": "gri30_highT"}

    def test_hot_stream_budget_and_source_immutable(self):
        before = deepcopy(self.record)
        result = compare_chamber_handoff(self.record)
        self.assertEqual(result["status"], "ok", result.get("reason"))
        self.assertLess(abs(result["production_reproduction"]["temperature_difference_K"]), .01)
        ports = self.record["calculation"]["states"]
        hflux = sum(ports[k]["mass_flow"]*ports[k]["enthalpy"] for k in ("ox_turbine_exhaust", "fuel_turbine_exhaust"))
        self.assertAlmostEqual(result["target"]["h_Jkg"], (hflux-1e6)/54)
        self.assertEqual(result["target"]["heat_loss_W"], 1e6)
        self.assertEqual(result["source_ports"][0]["composition_mass_fractions"], ports["ox_turbine_exhaust"]["composition"])
        comparison = result["matched_cho_comparison"]
        self.assertEqual(comparison["cantera"]["status"], "ok")
        if comparison["cea"]["status"] != "unavailable":
            self.assertEqual(comparison["cea"]["status"], "ok", comparison["cea"])
            self.assertLess(comparison["cea"]["element_max_residual"], 1e-7)
            self.assertLess(abs(comparison["cea"]["enthalpy_residual_Jkg"]), 1)
        self.assertEqual(before, self.record)
        json.dumps(result, allow_nan=False)

    def test_missing_and_corrupt_source_never_use_baseline_defaults(self):
        for change in ("missing_composition", "bad_h", "bad_reference", "bad_hash", "flow", "missing_heat", "exit_h", "loss"):
            record = deepcopy(self.record)
            exhaust = record["calculation"]["states"]["ox_turbine_exhaust"]
            if change == "missing_composition": exhaust["composition"] = {}
            if change == "bad_h": exhaust["enthalpy"] += 1e5
            if change == "bad_reference": exhaust["metadata"]["model"]["enthalpy_reference_id"] = "wrong"
            if change == "bad_hash": exhaust["metadata"]["model"]["mechanism_sha256"] = "0"*64
            if change == "flow": exhaust["mass_flow"] *= 1.1
            if change == "missing_heat": record["calculation"]["components"]["main_chamber"]["outputs"].pop("heat_loss_W")
            if change == "exit_h": record["calculation"]["states"]["main_chamber"]["enthalpy"] += 1e4
            if change == "loss": record["calculation"]["components"]["main_chamber"]["outputs"]["combustion_loss_W"] = 1e5
            with self.subTest(change=change):
                result = compare_chamber_handoff(record)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["matched_cho_comparison"])
                self.assertTrue(result["reason"])

    def test_cold_feed_baseline_settings_do_not_change_actual_handoff(self):
        record = deepcopy(self.record)
        first = compare_chamber_handoff(record)
        record["calculation"]["inputs"].update(fuel_inlet_temp=110, ox_inlet_temp=90, of_ratio=99)
        second = compare_chamber_handoff(record)
        self.assertEqual(first["target"], second["target"])
        self.assertEqual(first["production_reproduction"], second["production_reproduction"])

    def test_independent_element_audit_replaces_blanket_missing_verdict(self):
        record = deepcopy(self.record)
        rows = conservation_checks(record)["checks"]
        self.assertEqual(next(r for r in rows if r["id"] == "element_balance")["status"], "pass")
        record["calculation"]["states"]["main_chamber"]["composition"] = {"O2": 1.0}
        rows = conservation_checks(record)["checks"]
        self.assertEqual(next(r for r in rows if r["id"] == "element_balance")["status"], "fail")

    def test_validation_request_is_record_bound_and_rejects_overrides(self):
        source = {"id": "saved", "inputs": self.record["calculation"]["inputs"], "result": self.record,
                  "project_id": "p", "revision": 1, "created": 1}
        request = normalize_request({"gas_screening": {"enabled": True}}, source)
        self.assertEqual(request, {"source_job_id": "saved", "gas_screening": {"enabled": True}})
        for value in ({"enabled": 1}, {"enabled": False}, {"enabled": True, "pressure": 1}, []):
            with self.assertRaises(ValueError): normalize_request({"gas_screening": value}, source)
        with patch("webapp.gas_screening.screen_gas_record", return_value={"status": "test_only"}) as screening:
            result = calculate_validation(request, source)
        screening.assert_called_once()
        self.assertIsNone(result["thermochemistry"])
        self.assertEqual(result["source"]["result_sha256"], fingerprint(self.record))
        self.assertEqual(result["gas_handoff"]["status"], "complete")
        self.assertEqual(result["chamber_handoff_comparison"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
