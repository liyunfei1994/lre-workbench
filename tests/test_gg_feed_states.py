"""Real-library feed-chain enthalpy and main-pump inlet invariants."""
from pathlib import Path
import sys
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
from core import EngineInputs
from engine_presets import preset_input_values
from gg_cycle import GGCycleModel


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "scientific libraries required")
class FeedStateTests(unittest.TestCase):
    def test_main_pump_receives_computed_feed_state_not_tank_temperature(self):
        for key in ("tq12a_gg", "merlin1d_gg"):
            values = preset_input_values(key)
            values.update(cooling_bartz_mode=0., cooling_heat_load=10e6)
            with self.subTest(preset=key), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = GGCycleModel(EngineInputs(**values))
                result = model._evaluate_once(*model._initial_guess_vector())
                for prefix, pump_key, power_key in (("lox", "ox_pump", "boost_pump_ox_power_MW"),
                                                    ("fuel", "fuel_pump", "boost_pump_fuel_power_MW")):
                    tank, boost, inlet = [result.states[prefix + "_" + suffix]
                                          for suffix in ("tank", "boost_out", "pump_inlet")]
                    pump = result.components[pump_key]
                    self.assertGreater(inlet.temperature, tank.temperature)
                    self.assertAlmostEqual(pump.outputs["T_in_K"], inlet.temperature, delta=1e-10)
                    self.assertAlmostEqual(boost.enthalpy, inlet.enthalpy, delta=.01)
                    self.assertAlmostEqual(tank.mass_flow * (boost.enthalpy - tank.enthalpy),
                                           result.summary[power_key] * 1e6, delta=.01)
                    self.assertTrue(inlet.metadata["reference_id"].endswith("ideal_reference_v2"))
                    species = "O2" if prefix == "lox" else model.propellant.fuel_species
                    npsh = thermo.available_npsh_pressure(species, inlet.temperature, inlet.pressure)
                    self.assertAlmostEqual(pump.outputs["npsh_available_m"],
                                           npsh / (inlet.density * 9.80665), delta=1e-10)


if __name__ == "__main__":
    unittest.main()
