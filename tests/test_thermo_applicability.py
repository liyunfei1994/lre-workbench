"""Scientific range/energy invariants, rather than snapshots of UI labels."""
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
from core import EngineInputs, FlowState, CycleResults, ComponentResult, ValidationError
from chamber import MainChamber
from engine_presets import preset_input_values
from thermo_applicability import build_thermodynamic_applicability


class ContractTests(unittest.TestCase):
    def test_missing_mode_preserves_legacy_new_reference_uses_energy_balance(self):
        self.assertEqual(EngineInputs().combustion_loss_model, "legacy_temperature_rise")
        self.assertEqual(preset_input_values("raptor3_ffsc")["combustion_loss_model"], "adiabatic")
        with self.assertRaises(ValidationError):
            EngineInputs(combustion_loss_model="unburned_fraction")


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "scientific libraries required")
class PhysicsTests(unittest.TestCase):
    def test_gri_major_species_extrapolation_is_exposed_without_clipping(self):
        gas = thermo._get_gas()
        gas.TPY = 3743.0, 33e6, {"H2O": .5, "CO2": .5}
        before = gas.state.copy()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            state = thermo._gas_to_state(gas, gas.P)
        self.assertEqual(state.temperature, 3743.0)
        self.assertEqual(state.gas_model, "ideal-gas")
        self.assertEqual(state.thermo_range["status"], "extrapolated")
        self.assertAlmostEqual(state.thermo_range["outside_mass_fraction"], 1.0)
        self.assertEqual(set(state.thermo_extrapolation), {"H2O", "CO2"})
        self.assertTrue(any(issubclass(w.category, thermo.ThermoRangeWarning) for w in caught))
        self.assertTrue((gas.state == before).all())

    def test_only_present_species_count_and_low_bound_is_checked(self):
        gas = thermo._get_gas()
        gas.TPX = 3400.0, 33e6, "O2:1"
        info = thermo.gas_temperature_applicability(gas)
        self.assertEqual(info["status"], "within_declared_ranges")
        self.assertEqual(info["valid_temperature_max_K"], 3500.0)
        self.assertEqual(info["common_temperature_max_K"], 3000.0)
        gas.TP = 199.0, gas.P
        info = thermo.gas_temperature_applicability(gas)
        self.assertEqual(info["status"], "extrapolated")
        self.assertEqual(info["species_outside"][0]["min_temperature_K"], 200.0)

    def test_aligned_enthalpy_increment_matches_same_provider(self):
        gas = thermo._get_gas()
        for sp, fluid, low, high in (("CH4", "Methane", 100., 380.), ("O2", "Oxygen", 70., 83.)):
            with self.subTest(species=sp):
                h1 = thermo._real_fluid_enthalpy(sp, low, 1e6, gas)
                h2 = thermo._real_fluid_enthalpy(sp, high, 60e6, gas)
                delta = thermo._CP_PropsSI("H", "T", high, "P", 60e6, fluid) - thermo._CP_PropsSI("H", "T", low, "P", 1e6, fluid)
                self.assertAlmostEqual(h2-h1, delta, places=7)

    def test_energy_balance_has_no_temperature_factor_and_accounts_heat_once(self):
        gas = thermo._get_gas()
        gas.TPY = 750, 60e6, {"O2": 1}
        ox = FlowState("ox", 60e6, 750, 3.6, enthalpy=gas.enthalpy_mass, composition={"O2": 1})
        gas.TPY = 850, 60e6, {"CH4": 1}
        fuel = FlowState("fuel", 60e6, 850, 1, enthalpy=gas.enthalpy_mass, composition={"CH4": 1})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            balanced = MainChamber(33e6, 4.6, 1.2, 22, 3500, 1.0, heat_loss_W=1e5)
            state, component = balanced.evaluate(ox, fuel)
            legacy = MainChamber(33e6, 4.6, 1.2, 22, 3500, .99, heat_loss_W=1e5)
            legacy_state, legacy_component = legacy.evaluate(ox, fuel)
        inlet = 3.6 * ox.enthalpy + fuel.enthalpy
        self.assertAlmostEqual(component.outputs["combustion_loss_W"], 0.0)
        self.assertLess(abs(4.6 * state.enthalpy + 1e5 - inlet), 1.0)
        self.assertLess(legacy_state.temperature, state.temperature)
        self.assertGreater(legacy_component.outputs["combustion_loss_W"], 0)
        self.assertLess(abs(legacy_component.outputs["energy_residual_W"]), 1.0)

    def test_final_audit_does_not_mutate_solution_or_equate_range_with_validation(self):
        gas = thermo._get_gas()
        gas.TPX = 800, 60e6, "O2:1"
        saved = gas.state.copy()
        states = {"main_chamber": FlowState("main_chamber", 33e6, 3743, 1, composition={"H2O": 1})}
        result = CycleResults(EngineInputs(combustion_loss_model="adiabatic",methane_thermo_dataset="gri30"), {"thermo_source": 1.}, states,
                              {"main_chamber": ComponentResult("main_chamber", {"combustion_loss_W": 0., "heat_loss_W": 90e6})}, [])
        before = asdict(result)
        report = build_thermodynamic_applicability(result)
        self.assertEqual(before, asdict(result))
        self.assertTrue((saved == gas.state).all())
        self.assertEqual(report["temperature_status"], "extrapolated")
        self.assertEqual(report["gas_model"]["validation_status"], "not_validated")
        self.assertEqual(report["combustion"]["loss_W"], 0.)
        self.assertIsNone(report["combustion"]["legacy_factor"])

    def test_pure_fluid_extrapolation_and_spatial_heat_model_are_not_hidden(self):
        result = CycleResults(
            EngineInputs(combustion_loss_model="adiabatic", methane_thermo_dataset="gri30_highT"),
            {"thermo_source": 1.},
            {"fuel_after_cooling": FlowState("fuel_after_cooling", 30e6, 700., 1., composition={"CH4": 1.}),
             "main_chamber": FlowState("main_chamber", 33e6, 3700., 1., composition={"H2O": 1.})},
            {"main_chamber": ComponentResult("main_chamber", {"heat_loss_W": 0.}),
             "cooling_channel": ComponentResult("cooling_channel", {}, profile={"model_id": "regen_quasi1d_ph_v1"}),
             "nozzle": ComponentResult("nozzle", {"heat_loss_W": 100e6}, profile={"model_id": "heated_frozen_quasi1d_v1"})}, [])
        report = build_thermodynamic_applicability(result)
        self.assertEqual(report["pure_fluid_status"], "invalid_or_unavailable")
        self.assertEqual(report["pure_fluid_states"][0]["T_K"], 700.)
        self.assertIn("声明范围", report["pure_fluid_states"][0]["reason"])
        self.assertEqual(report["nozzle"]["id"], "heated_frozen_quasi1d_v1")
        self.assertIn("跨声速临界条件求解", report["nozzle"]["description"])
        self.assertIn("凝结", report["nozzle"]["description"])
        self.assertIn("零速储室参考总态", report["nozzle"]["description"])
        self.assertNotIn("几何喉部声速约束近似", report["nozzle"]["description"])
        self.assertEqual(report["cooling"]["validation_status"], "correlations_not_independently_validated")
        self.assertIn("ideal_reference_v2", report["enthalpy_reference"]["id"])
        self.assertIn("主室平衡中重复扣热", report["combustion"]["description"])

    def test_gg_heat_description_requires_saved_port_ledger(self):
        component = ComponentResult("main_chamber",{})
        result = CycleResults(EngineInputs(cycle_type="gg",combustion_loss_model="adiabatic"),
            {},{}, {"main_chamber":component,"nozzle":ComponentResult("nozzle",{"flow_model":0.})},[])
        legacy = build_thermodynamic_applicability(result)
        self.assertIn("本历史 GG 记录",legacy["combustion"]["description"])
        component.outputs.update(h_in_Jkg=-1e6,heat_loss_W=1e6,combustion_loss_W=0.,energy_residual_W=0.)
        new = build_thermodynamic_applicability(result)
        self.assertIn("实际泵/冷却出口",new["combustion"]["description"])
        self.assertIn("主室 HP 目标",new["nozzle"]["description"])
        self.assertTrue(any("不等于全系统独立能量审核" in note for note in new["notes"]))


if __name__ == "__main__":
    unittest.main()
