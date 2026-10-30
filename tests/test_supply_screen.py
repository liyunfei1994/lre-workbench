"""Independent saturation and record provenance checks for supply interfaces."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from CoolProp.CoolProp import PropsSI
from fluid_properties import supply_liquid_screen
from webapp.steady_design import _supply_basis


class SupplyScreenTests(unittest.TestCase):
    def test_subcooling_uses_local_absolute_pressure_and_has_no_cavitation_pass(self):
        for species, fluid, temperature in (("CH4", "Methane", 100.), ("O2", "Oxygen", 70.)):
            with self.subTest(species=species):
                screen = supply_liquid_screen(species, temperature, 3e5)
                tsat = screen["saturation_temperature_K"]
                self.assertAlmostEqual(PropsSI("P", "T", tsat, "Q", 0, fluid), 3e5, delta=.001)
                self.assertAlmostEqual(screen["subcooling_K"], tsat-temperature)
                self.assertGreater(screen["subcooling_K"], 0)
                reconstructed = screen["static_npsh_component_m"] * screen["density_kg_m3"] * 9.80665 + screen["vapor_pressure_Pa"]
                self.assertAlmostEqual(reconstructed, 3e5)
                self.assertEqual(screen["cavitation_status"], "requires_npshr_and_inlet_definition")
                self.assertGreater(supply_liquid_screen(species, temperature, 1e6)["saturation_temperature_K"], tsat)
                json.dumps(screen, allow_nan=False)

    def test_invalid_inlets_are_rejected_and_supercritical_saturation_is_absent(self):
        for species, t, p in (("CH4", 80, 3e5), ("O2", 300, 3e5), ("O2", 90, 90e6)):
            with self.subTest(species=species, T=t, p=p), self.assertRaises(ValueError):
                supply_liquid_screen(species, t, p)
        screen = supply_liquid_screen("CH4", 200, 30e6)
        self.assertIsNone(screen["subcooling_K"])
        self.assertIsNone(screen["static_npsh_component_m"])
        self.assertEqual(screen["saturation_temperature_status"], "not_defined_at_pressure")
        self.assertEqual(screen["vapor_pressure_status"], "not_defined_at_temperature")
        json.dumps(screen, allow_nan=False)

    def test_view_never_recomputes_historical_properties_or_uses_stale_screen(self):
        state = {"temperature": 100., "pressure": 3e5}
        states = {"fuel_tank": state}
        historical = _supply_basis(states, {}, {})
        self.assertEqual(historical["ports"][0]["screen"], {})
        saved = {"pure_fluid_states": [{"id": "fuel_tank", "T_K": 100., "p_Pa": 3e5,
                  "supply_screen": supply_liquid_screen("CH4", 100., 3e5)}]}
        before = deepcopy(saved)
        projected = _supply_basis(states, {}, saved)
        self.assertEqual(projected["ports"][0]["screen"], saved["pure_fluid_states"][0]["supply_screen"])
        state["pressure"] = 4e5
        self.assertEqual(_supply_basis(states, {}, saved)["ports"][0]["screen"], {})
        self.assertEqual(saved, before)


if __name__ == "__main__":
    unittest.main()
