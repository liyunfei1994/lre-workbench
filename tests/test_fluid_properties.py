"""Independent HEOS range, phase, composition and reference invariants."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
from fluid_properties import (fluid_spec, state_tp, state_ph, ideal_reference_enthalpy,
                              saturation_pressure)
from thermo_data import methane_dataset_scope
from pump import Pump, fluid_density


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "scientific libraries required")
class CheckedPropertiesTests(unittest.TestCase):
    def test_library_success_does_not_authorize_extrapolation(self):
        from CoolProp.CoolProp import PropsSI
        # CoolProp can evaluate this above its declared methane Tmax. The
        # production interface must reject it even when a number is returned.
        self.assertGreater(PropsSI("H", "T", 700, "P", 30e6, "Methane"), 0)
        for species, temperature, pressure in (("CH4", 700, 30e6), ("O2", 90, 81e6),
                                                ("CH4", 89, 1e6), ("O2", 2001, 1e6)):
            with self.subTest(species=species, T=temperature, p=pressure):
                with self.assertRaisesRegex(ValueError, "声明范围"):
                    state_tp(species, temperature, pressure)
                with self.assertRaises(ValueError):
                    thermo.reactant_enthalpy(species, temperature, pressure)
                with self.assertRaises(ValueError):
                    fluid_density(species, temperature, pressure)

    def test_transport_and_ph_states_use_same_unclipped_state(self):
        for species, temperature, pressure in (("CH4", 120, 70e6), ("CH4", 380, 60e6), ("O2", 90, 60e6)):
            first = state_tp(species, temperature, pressure, transport=True)
            second = state_ph(species, pressure, first["h_Jkg"], transport=True)
            self.assertAlmostEqual(second["T_K"], temperature, delta=1e-7)
            self.assertAlmostEqual(second["h_Jkg"], first["h_Jkg"], delta=.001)
            for key in ("cp_JkgK", "rho_kgm3", "mu_Pas", "k_WmK", "sound_speed_ms"):
                self.assertGreater(second[key], 0)
            self.assertEqual(second["range_status"], "within_declared_ranges")

    def test_available_melting_boundary_blocks_metastable_tp_queries(self):
        with self.assertRaisesRegex(ValueError, "熔化边界"):
            state_tp("CH4", 100, 70e6)
        self.assertEqual(state_tp("CH4", 120, 70e6)["melting_boundary"]["status"], "above_melting_boundary")
        self.assertEqual(state_tp("c12h26", 300, 10e6)["melting_boundary"]["status"], "not_available")

    def test_hp_rejects_two_phase_and_out_of_range_temperature(self):
        from CoolProp.CoolProp import PropsSI
        pressure = 1e5
        two_phase_h = PropsSI("H", "P", pressure, "Q", .5, "Methane")
        with self.assertRaisesRegex(ValueError, "twophase"):
            state_ph("CH4", pressure, two_phase_h)
        outside_h = PropsSI("H", "T", 700, "P", 30e6, "Methane")
        with self.assertRaises(ValueError):
            state_ph("CH4", 30e6, outside_h)

    def test_pump_rejects_vapor_but_identifies_dense_supercritical_domain(self):
        for species in ("CH4", "O2"):
            with self.subTest(species=species), self.assertRaisesRegex(ValueError, "液体泵入口"):
                Pump("invalid", species, 10, 1e6, 10e6, 300, .85, nr_rpm=20000).evaluate()
        checked = state_tp("CH4", 300, 70e6, require_liquid=True)
        self.assertEqual(checked["phase"], "supercritical")
        self.assertEqual(checked["pump_domain"], "dense_supercritical")
        _, component = Pump("dense", "CH4", 10, 70e6, 75e6, 300, .85, nr_rpm=20000).evaluate()
        self.assertEqual(component.outputs["inlet_dense_supercritical"], 1)
        self.assertTrue(any("不是液相判定或汽蚀能力证明" in note for note in component.notes))

    def test_saturation_above_critical_does_not_invent_a_npsh_value(self):
        self.assertGreater(saturation_pressure("CH4", 100), 0)
        with self.assertRaisesRegex(ValueError, "NPSH"):
            thermo.vapor_pressure("CH4", 300)
        self.assertIsNone(thermo.available_npsh_pressure("CH4", 300, 70e6))
        # A vapor feed below Tc has a negative saturation-pressure margin;
        # liquid pump validation rejects it separately rather than clipping it.
        self.assertLess(thermo.available_npsh_pressure("CH4", 180, .1e6), 0)

    def test_reference_removes_residual_and_has_dilute_gas_limit(self):
        from CoolProp.CoolProp import PropsSI
        with methane_dataset_scope("gri30_highT"):
            for species, fluid in (("CH4", "Methane"), ("O2", "Oxygen")):
                for pressure in (1000, 101325, 1e6):
                    ideal = ideal_reference_enthalpy(species, 298.15, pressure)
                    self.assertAlmostEqual(ideal, PropsSI("Hmass_idealgas", "T", 298.15, "P", pressure, fluid), delta=1e-7)
                gas = thermo._get_gas()
                gas.TPX = 298.15, 1, {species: 1}
                chemical = gas.enthalpy_mass
                aligned = thermo.reactant_enthalpy(species, 298.15, 1)
                self.assertAlmostEqual(aligned, chemical, delta=.02)
            self.assertIn("gri30_highT", thermo.enthalpy_reference_id())
            self.assertTrue(thermo.enthalpy_reference_id().endswith("ideal_reference_v2"))

    def test_shared_cea_reactants_use_identical_checked_forward_reference(self):
        with methane_dataset_scope("gri30_highT"):
            values = thermo.validation_reactants(100, 70, 33e6)
            self.assertEqual(values["reference"]["id"], thermo.enthalpy_reference_id())
            for role, species, temperature in (("fuel", "CH4", 100), ("oxidizer", "O2", 70)):
                self.assertAlmostEqual(values[role + "_enthalpy_Jkg"],
                                       thermo.reactant_enthalpy(species, temperature, 33e6), delta=1e-8)
                h = values[role + "_enthalpy_Jkg"]
                self.assertAlmostEqual(thermo.reactant_temperature_from_enthalpy(species, h, 33e6), temperature, delta=1e-6)

    def test_composition_rejects_lost_mass_and_invalid_fractions(self):
        gas = thermo._get_gas()
        for composition in ({}, {"N2": 0}, {"unknown": 1}, {"O2": .9, "unknown": .1},
                            {"O2": 1.1, "H2": -.1}, {"O2": float("nan")},
                            {"O2": float("inf")}, {"O2": .5}, {"O2": True}):
            with self.subTest(composition=composition), self.assertRaises(ValueError):
                thermo._dict_to_Y(gas, composition)
        accepted = thermo._dict_to_Y(gas, {"O2": .5, "H2": .5000000001, "unknown": 0})
        self.assertAlmostEqual(sum(accepted), 1, delta=1e-15)


if __name__ == "__main__":
    unittest.main()
