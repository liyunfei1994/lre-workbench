"""Dataset identity must survive scopes, threads and thermodynamic operations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
from core import EngineInputs, ValidationError
from thermo_data import active_methane_dataset, methane_dataset_scope, model_thermo_scope


class DatasetContractTests(unittest.TestCase):
    def test_new_inputs_default_high_temperature_data_and_explicit_legacy_is_preserved(self):
        self.assertEqual(EngineInputs().methane_thermo_dataset, "gri30_highT")
        self.assertEqual(EngineInputs(methane_thermo_dataset="gri30").methane_thermo_dataset,"gri30")
        with self.assertRaises(ValidationError):
            EngineInputs(methane_thermo_dataset="automatic")
        with self.assertRaises(ValueError):
            with methane_dataset_scope("automatic"):
                self.fail("Invalid data selection must never enter the scope")

    def test_nested_scope_and_exception_restore_the_callers_selection(self):
        previous = active_methane_dataset()
        with methane_dataset_scope("gri30_highT"):
            self.assertEqual(active_methane_dataset(), "gri30_highT")
            with self.assertRaisesRegex(RuntimeError, "trial failed"):
                with methane_dataset_scope("gri30"):
                    self.assertEqual(active_methane_dataset(), "gri30")
                    raise RuntimeError("trial failed")
            self.assertEqual(active_methane_dataset(), "gri30_highT")
        self.assertEqual(active_methane_dataset(), previous)

    def test_model_scope_uses_input_identity_and_restores_after_failure(self):
        @model_thermo_scope
        def evaluate(model, fail=False):
            selected = active_methane_dataset()
            if fail:
                raise RuntimeError(selected)
            return selected

        model = SimpleNamespace(inputs=EngineInputs(methane_thermo_dataset="gri30_highT"))
        with methane_dataset_scope("gri30"):
            self.assertEqual(evaluate(model), "gri30_highT")
            self.assertEqual(active_methane_dataset(), "gri30")
            with self.assertRaisesRegex(RuntimeError, "gri30_highT"):
                evaluate(model, fail=True)
            self.assertEqual(active_methane_dataset(), "gri30")


@unittest.skipUnless(thermo.CANTERA_AVAILABLE, "Cantera required")
class GasDatasetTests(unittest.TestCase):
    @staticmethod
    def chamber_state(dataset):
        with methane_dataset_scope(dataset):
            gas = thermo._get_gas()
            gas.TPY = 3774.0, 33e6, {"CH4": 1.0, "O2": 3.6}
            gas.equilibrate("TP")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", thermo.ThermoRangeWarning)
                return thermo._gas_to_state(gas, gas.P)

    def test_high_temperature_data_preserve_species_and_report_actual_range(self):
        historical = thermo._get_gas(methane_dataset="gri30")
        high = thermo._get_gas(methane_dataset="gri30_highT")
        self.assertIsNot(historical, high)
        self.assertEqual(historical.species_names, high.species_names)
        self.assertEqual(high.n_species, 53)
        self.assertEqual(high.thermo_model, "ideal-gas")
        self.assertEqual((high.min_temp, high.max_temp), (300.0, 5000.0))
        for name in ("H2O", "CO2", "CO", "O2", "H2", "OH", "CH4"):
            with self.subTest(species=name):
                self.assertEqual(high.species(name).thermo.max_temp, 6000.0)

    def test_main_chamber_range_status_changes_without_clipping_temperature(self):
        historical = self.chamber_state("gri30")
        high = self.chamber_state("gri30_highT")
        self.assertEqual(historical.temperature, 3774.0)
        self.assertEqual(high.temperature, 3774.0)
        self.assertEqual(historical.thermo_range["status"], "extrapolated")
        self.assertGreater(historical.thermo_range["outside_mass_fraction"], 0.99)
        self.assertEqual(high.thermo_range["status"], "within_declared_ranges")
        self.assertEqual(high.thermo_range["outside_mass_fraction"], 0.0)
        self.assertEqual(high.mechanism, "gri30_highT.yaml")

    def test_gas_states_keep_dataset_through_turbine_and_nozzle_outside_scope(self):
        state = self.chamber_state("gri30_highT")
        with methane_dataset_scope("gri30_highT"):
            expected_turbine = thermo.turbine_expansion(state, 20e6, 0.85)
            expected_nozzle = thermo.nozzle_exit_state(state, 1e6)
            expected_performance = thermo.nozzle_performance(state, state.pressure, 10.0)
        with methane_dataset_scope("gri30"):
            turbine = thermo.turbine_expansion(state, 20e6, 0.85)
            nozzle = thermo.nozzle_exit_state(state, 1e6)
            performance = thermo.nozzle_performance(state, state.pressure, 10.0)
            self.assertEqual(active_methane_dataset(), "gri30")
        self.assertEqual(turbine["mechanism"], "gri30_highT.yaml")
        self.assertEqual(nozzle.mechanism, "gri30_highT.yaml")
        self.assertAlmostEqual(turbine["h_exhaust"], expected_turbine["h_exhaust"], places=6)
        self.assertAlmostEqual(nozzle.enthalpy, expected_nozzle.enthalpy, places=6)
        self.assertIsNotNone(performance)
        self.assertIsNotNone(expected_performance)
        for key in ("Cstar", "V_exit", "p_exit", "T_exit", "Isp_vac"):
            with self.subTest(quantity=key):
                self.assertAlmostEqual(performance[key], expected_performance[key], places=6)

    def test_unknown_state_identity_is_rejected_not_reinterpreted(self):
        state = replace(self.chamber_state("gri30_highT"), mechanism="untracked.yaml")
        with self.assertRaisesRegex(ValueError, "untracked.yaml"):
            thermo.nozzle_exit_state(state, 1e6)
        with self.assertRaisesRegex(ValueError, "untracked.yaml"):
            thermo.turbine_expansion(state, 20e6, 0.85)

    def test_thread_local_gases_do_not_share_mutable_temperature_pressure(self):
        ready = threading.Barrier(2)

        def worker(temperature, pressure):
            with methane_dataset_scope("gri30_highT"):
                gas = thermo._get_gas()
                gas.TPX = temperature, pressure, {"O2": 1.0}
                ready.wait(timeout=10)
                measured = (gas.T, gas.P, active_methane_dataset())
                ready.wait(timeout=10)
                return gas, measured

        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(worker, 750.0, 60e6)
            b = executor.submit(worker, 850.0, 40e6)
            gas_a, measured_a = a.result(timeout=20)
            gas_b, measured_b = b.result(timeout=20)
        self.assertIsNot(gas_a, gas_b)
        self.assertAlmostEqual(measured_a[0], 750.0)
        self.assertAlmostEqual(measured_a[1], 60e6)
        self.assertAlmostEqual(measured_b[0], 850.0)
        self.assertAlmostEqual(measured_b[1], 40e6)
        self.assertEqual(measured_a[2], "gri30_highT")
        self.assertEqual(measured_b[2], "gri30_highT")

    def test_pressure_and_fuel_changes_do_not_relabel_previous_gas_state(self):
        state = self.chamber_state("gri30_highT")
        saved = (state.temperature, state.pressure, state.enthalpy, dict(state.composition))
        with methane_dataset_scope("gri30"):
            historical = thermo._get_gas()
            historical.TPX = 700.0, 2e6, {"O2": 1.0}
            kerosene = thermo._get_gas("c12h26")
            kerosene.TPX = 900.0, 6e6, {"o2": 1.0}
            kerosene_state = thermo._gas_to_state(kerosene, kerosene.P)
        with methane_dataset_scope("gri30_highT"):
            self.assertIs(thermo._get_gas("c12h26"), kerosene)
            self.assertIsNot(thermo._get_gas(), kerosene)
            self.assertEqual(thermo._gas_for_state(kerosene_state).name, "nDodecane_IG")
        self.assertEqual(saved, (state.temperature, state.pressure, state.enthalpy, state.composition))
        self.assertEqual(kerosene_state.mechanism, "nDodecane_Reitz.yaml")
        with methane_dataset_scope("gri30"):
            expanded = thermo.nozzle_exit_state(state, 10e6)
        self.assertEqual(expanded.mechanism, "gri30_highT.yaml")
        self.assertAlmostEqual(expanded.pressure, 10e6)


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE,
                     "Cantera and CoolProp required")
class ReactantReferenceTests(unittest.TestCase):
    def test_forward_inverse_enthalpy_use_the_same_selected_reference(self):
        for dataset in ("gri30", "gri30_highT"):
            for species, fluid, temperature, pressure in (
                ("CH4", "Methane", 110.0, 1e6),
                ("CH4", "Methane", 380.0, 60e6),
                ("O2", "Oxygen", 80.0, 1e6),
                ("O2", "Oxygen", 100.0, 60e6),
            ):
                with self.subTest(dataset=dataset, species=species, T=temperature, p=pressure):
                    with methane_dataset_scope(dataset):
                        gas = thermo._get_gas()
                        gas.TPX = 298.15, 101325.0, {species: 1.0}
                        expected = gas.enthalpy_mass + (
                            thermo._CP_PropsSI("H", "T", temperature, "P", pressure, fluid)
                            - thermo._CP_PropsSI("Hmass_idealgas", "T", 298.15, "P", 101325.0, fluid)
                        )
                        actual = thermo.reactant_enthalpy(species, temperature, pressure)
                        recovered = thermo.reactant_temperature_from_enthalpy(species, actual, pressure)
                    self.assertAlmostEqual(actual, expected, delta=1e-7)
                    self.assertAlmostEqual(recovered, temperature, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
