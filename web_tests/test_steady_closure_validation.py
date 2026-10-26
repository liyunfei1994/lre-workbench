"""Validation recomputes the saved model evidence rather than trusting its badge."""
from copy import deepcopy
import json
import unittest

from tests.test_steady_closure import balanced_loop
from webapp.validation_checks import conservation_checks


class PersistedEnergyValidationTests(unittest.TestCase):
    def record(self):
        states, components = balanced_loop()
        return {"calculation": {"inputs": {"cycle_type": "ffsc"}, "summary": {},
                                "states": states, "components": components,
                                "steady_closure": {"energy_balance": {"status": "pass", "residual_W": 0}}}}

    def test_saved_enthalpy_perturbation_cannot_hide_behind_previous_pass(self):
        source = self.record()
        source["calculation"]["states"]["fuel_after_cooling"]["enthalpy"] += 100
        before = deepcopy(source)
        result = conservation_checks(source)
        rows = {row["id"]: row for row in result["checks"]}
        self.assertEqual(rows["whole_system_energy"]["status"], "fail")
        self.assertEqual(rows["energy_global_energy"]["status"], "pass")
        self.assertEqual(rows["energy_regenerative_coolant_energy"]["status"], "fail")
        self.assertEqual(source, before)
        json.dumps(result, allow_nan=False)

    def test_boundary_can_pass_without_claiming_experimental_or_element_validation(self):
        result = conservation_checks(self.record())
        rows = {row["id"]: row for row in result["checks"]}
        self.assertEqual(rows["whole_system_energy"]["status"], "pass")
        self.assertEqual(rows["element_balance"]["status"], "unavailable")
        self.assertNotEqual(result["status"], "pass")
        self.assertEqual(result["energy_balance"]["reference_id"], "test_common_formation_reference")

    def test_legacy_states_cannot_borrow_the_saved_energy_badge(self):
        source = self.record()
        for state in source["calculation"]["states"].values():
            state["metadata"].pop("reference_id", None)
        result = conservation_checks(source)
        self.assertEqual(result["energy_balance"]["status"], "unavailable")
        self.assertIsNone(result["energy_balance"]["residual_W"])


if __name__ == "__main__":
    unittest.main()
