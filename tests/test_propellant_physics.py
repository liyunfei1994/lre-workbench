"""Fuel identity and conservation tests for the explicit n-dodecane surrogate."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
import pump
import cooling
from core import ValidationError
from propellants import get_propellant, propellant_models, require_liquid_fuel_state


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "Cantera and CoolProp required")
class PropellantPhysicsTests(unittest.TestCase):
    def test_declared_model_identity_and_supported_scope(self):
        model = get_propellant("lox_kerosene")
        self.assertEqual((model.fuel_species, model.coolprop_fluid, model.mechanism, model.phase),
                         ("c12h26", "n-Dodecane", "nDodecane_Reitz.yaml", "nDodecane_IG"))
        self.assertEqual(model.supported_cycles, ("gg",))
        self.assertAlmostEqual(model.stoich_of, 3.47518, places=4)
        self.assertEqual(len(propellant_models()), 2)
        with self.assertRaises(ValueError):
            get_propellant("unknown")

    def test_enthalpy_reference_is_gaseous_and_includes_real_liquid_heating(self):
        from CoolProp.CoolProp import PropsSI, PhaseSI
        self.assertEqual(PhaseSI("T", 600, "P", 1000, "n-Dodecane"), "gas")
        gas = thermo._get_gas("c12h26")
        gas.TPX = 600, 1000, {"c12h26": 1}
        expected = (gas.enthalpy_mass + PropsSI("H", "T", 300, "P", 10e6, "n-Dodecane")
                    - PropsSI("Hmass_idealgas", "T", 600, "P", 1000, "n-Dodecane"))
        self.assertAlmostEqual(thermo.reactant_enthalpy("c12h26", 300, 10e6, "c12h26"), expected, places=7)
        first = thermo.reactant_enthalpy("c12h26", 300, 10e6, "c12h26")
        second = thermo.reactant_enthalpy("c12h26", 500, 8e6, "c12h26")
        cp_delta = PropsSI("H", "T", 500, "P", 8e6, "n-Dodecane") - PropsSI("H", "T", 300, "P", 10e6, "n-Dodecane")
        self.assertAlmostEqual(second-first, cp_delta, places=6)

    def test_combustion_conserves_elements_and_inlet_enthalpy(self):
        for mr in (.4, 2.6):
            with self.subTest(mixture_ratio=mr), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                state = thermo.combustion_equilibrium(1, mr, 300, 90, 10e6, fuel_species="c12h26")
                self.assertEqual((state.mechanism, state.phase), ("nDodecane_Reitz.yaml", "nDodecane_IG"))
                self.assertEqual(state.source, "cantera")
                self.assertNotIn("CH4", state.composition)
                gas = thermo._get_gas("c12h26")
                gas.TPY = 600, 10e6, {"c12h26": 1, "o2": mr}
                elements = {element: gas.elemental_mass_fraction(element) for element in gas.element_names}
                gas.TPY = state.temperature, state.pressure, state.composition
                for element, fraction in elements.items():
                    self.assertAlmostEqual(gas.elemental_mass_fraction(element), fraction, delta=1e-8)
                expected_h = (thermo.reactant_enthalpy("c12h26", 300, 10e6, "c12h26")
                              + mr*thermo.reactant_enthalpy("O2", 90, 10e6, "c12h26"))/(1+mr)
                self.assertAlmostEqual(state.enthalpy, expected_h, delta=50)
                if state.temperature > 3000:
                    self.assertTrue(any("外推" in str(item.message) for item in caught))
                    self.assertIn("ch3o", state.thermo_extrapolation)
                    self.assertTrue(all(value >= 0 for value in state.thermo_extrapolation.values()))

    def test_turbine_and_nozzle_preserve_mechanism_and_energy(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            hot = thermo.combustion_equilibrium(1, 2.6, 300, 90, 10e6, fuel_species="c12h26")
            perf = thermo.nozzle_performance(hot, 10e6, 20)
            cold = thermo.nozzle_exit_state(hot, perf["p_exit"])
        self.assertEqual(cold.mechanism, hot.mechanism)
        self.assertAlmostEqual(cold.enthalpy+perf["V_exit"]**2/2, hot.enthalpy, delta=10)
        self.assertAlmostEqual(cold.entropy, hot.entropy, delta=.01)
        self.assertGreater(perf["Isp_vac"], 250)
        self.assertLess(perf["Isp_vac"], 400)
        generator = thermo.combustion_equilibrium(1, .4, 300, 90, 5e6, fuel_species="c12h26")
        expanded = thermo.turbine_expansion(generator, .4e6, .7)
        self.assertEqual(expanded["mechanism"], generator.mechanism)
        self.assertAlmostEqual(expanded["h_exhaust"]+expanded["actual_enthalpy_drop"], generator.enthalpy, delta=1)
        self.assertAlmostEqual(expanded["actual_enthalpy_drop"], .7*expanded["isentropic_enthalpy_drop"], places=7)
        with self.assertRaisesRegex(ValueError, "300–5000"):
            thermo.turbine_expansion(generator, 100, .9)

    def test_pump_uses_dodecane_isentropic_work_and_aligned_enthalpy(self):
        from CoolProp.CoolProp import PropsSI
        state, result = pump.Pump("fuel", "RP1", 30, 1e6, 14e6, 293, .8, nr_rpm=20000, n_stages=1).evaluate()
        h_in = PropsSI("H", "T", 293, "P", 1e6, "n-Dodecane")
        s_in = PropsSI("S", "T", 293, "P", 1e6, "n-Dodecane")
        expected_power = 30*(PropsSI("H", "S", s_in, "P", 14e6, "n-Dodecane")-h_in)/.8
        self.assertAlmostEqual(result.outputs["power_W"], expected_power, delta=.01)
        self.assertGreater(state.density, 700)
        self.assertEqual(state.composition, {"c12h26": 1.0})
        inlet_aligned = thermo.reactant_enthalpy("c12h26", 293, 1e6, "c12h26")
        self.assertAlmostEqual(state.enthalpy-inlet_aligned, expected_power/30, delta=.01)

    def test_fixed_heatload_uses_real_enthalpy_balance_and_preserves_pressure_budget(self):
        from CoolProp.CoolProp import PropsSI
        state, result = cooling.CoolingChannel(30, 300, 14e6, 2e6, 20e6,
                                              bartz_mode=False, fuel_species="c12h26").evaluate()
        dh = (PropsSI("H", "T", state.temperature, "P", state.pressure, "n-Dodecane")
              - PropsSI("H", "T", 300, "P", 14e6, "n-Dodecane"))
        self.assertAlmostEqual(30*dh, 20e6, delta=.01)
        self.assertEqual(state.pressure, 12e6)
        self.assertEqual(state.composition, {"c12h26": 1.0})
        self.assertEqual(result.outputs["heat_load_W"], 20e6)
        with self.assertRaisesRegex(ValidationError, "准一维"):
            cooling.CoolingChannel(30, 300, 14e6, fuel_species="c12h26")
        with self.assertRaises(ValueError):
            cooling.CoolingChannel(30, 300, 14e6, 2e6, 200e6,
                                   bartz_mode=False, fuel_species="c12h26").evaluate()

    def test_bounds_and_missing_dependencies_never_fall_back_to_methane(self):
        for temperature, pressure in ((100, 1e6), (701, 1e6), (300, 201e6)):
            with self.subTest(temperature=temperature, pressure=pressure), self.assertRaises(ValueError):
                pump.fluid_density("c12h26", temperature, pressure)
        with patch.object(pump, "_COOLPROP_OK", False), self.assertRaises(RuntimeError):
            pump.fluid_density("c12h26", 300, 1e6)
        with patch.object(thermo, "COOLPROP_AVAILABLE", False), self.assertRaises(RuntimeError):
            thermo.combustion_equilibrium(1, .4, 300, 90, 5e6, fuel_species="c12h26")
        with patch.object(thermo, "CANTERA_AVAILABLE", False), self.assertRaises(RuntimeError):
            thermo.combustion_equilibrium(1, .4, 300, 90, 5e6, fuel_species="c12h26")
        with self.assertRaises(ValueError):
            thermo._dict_to_Y(thermo._get_gas("c12h26"), {"O2": .5, "unknown_fuel": .5})

    def test_liquid_pump_rejects_gaseous_inlet_even_inside_fluid_property_bounds(self):
        # The low-density supercritical state is rejected; a dense supercritical
        # state is now a distinct, disclosed pump model domain, not called gas.
        for temperature, inlet_pressure in ((600, .1e6), (690, 2e6)):
            with self.subTest(temperature=temperature), self.assertRaisesRegex(ValueError, "液体泵入口"):
                pump.Pump("fuel", "c12h26", 30, inlet_pressure, 14e6, temperature, .8,
                          nr_rpm=20000, n_stages=1).evaluate()

    def test_extrapolation_notice_is_stable_while_final_state_retains_actual_detail(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            states = [thermo.combustion_equilibrium(1, mr, 300, 90, 10e6, fuel_species="c12h26") for mr in (2.5, 2.6)]
        notices = [str(item.message) for item in caught if issubclass(item.category, thermo.SurrogateThermoRangeWarning)]
        self.assertGreaterEqual(len(notices), 2)
        self.assertEqual(len(set(notices)), 1)
        self.assertNotEqual(states[0].temperature, states[1].temperature)
        self.assertNotEqual(states[0].thermo_extrapolation, states[1].thermo_extrapolation)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", thermo.SurrogateThermoRangeWarning)
            corrected = thermo.gg_chamber_equilibrium(1, 2.6, 400, 90, 10e6, efficiency=.983, fuel_species="c12h26")
        self.assertGreater(corrected.thermo_extrapolation["ch3o"], 0)
        self.assertIn("ch3o", corrected.composition)

    def test_feed_phase_gate_rejects_gas_tank_before_assumed_boost_pump(self):
        # Same temperature can be vapor in the tank and liquid after an assumed
        # pressure jump: checking the main pump alone would miss the bad feed.
        with self.assertRaisesRegex(ValueError, "贮箱.*液态"):
            require_liquid_fuel_state("c12h26", 600, .3e6, context="煤油贮箱")
        require_liquid_fuel_state("c12h26", 600, 1e6, context="增压泵出口")

    def test_methane_defaults_unchanged_and_not_contaminated_by_other_mechanism(self):
        before = thermo.combustion_equilibrium(1, .4, 110, 90, 5e6)
        thermo.combustion_equilibrium(1, .4, 300, 90, 5e6, fuel_species="c12h26")
        after = thermo.combustion_equilibrium(1, .4, 110, 90, 5e6, fuel_species="CH4")
        self.assertEqual(before.mechanism, "gri30.yaml")
        for key in ("temperature", "cp", "gamma", "enthalpy", "entropy"):
            self.assertAlmostEqual(getattr(before, key), getattr(after, key), places=7)
        cooled = cooling.CoolingChannel(10, 110, 10e6, 1e6, 2.4e6, cp=2400, bartz_mode=False).evaluate()[0]
        from CoolProp.CoolProp import PropsSI
        actual_heat = 10 * (PropsSI("H", "T", cooled.temperature, "P", cooled.pressure, "Methane")
                            - PropsSI("H", "T", 110, "P", 10e6, "Methane"))
        self.assertAlmostEqual(actual_heat, 2.4e6, delta=.01)
        self.assertEqual(cooled.composition, {"CH4": 1.0})


if __name__ == "__main__":
    unittest.main()
