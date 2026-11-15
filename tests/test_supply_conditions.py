"""Independent feed branches and historical shared-input replay."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.domain.models import (EngineInputs, ValidationError, supply_conditions,
                                        supply_summary, SUPPLY_FIELD_FALLBACKS)
from lre_workbench.domain.analysis_contract import concept_design_task, concept_inputs
from lre_workbench.properties import thermochemistry as thermo
from lre_workbench.systems.ffsc import FFSCCycleModel
from lre_workbench.legacy.gg_cycle import GGCycleModel
from webapp.solver import input_values, platform_defaults, design_readiness


class SupplyInputTests(unittest.TestCase):
    def test_legacy_custom_settings_survive_normalization_and_null_defaults(self):
        old = platform_defaults()
        for key in SUPPLY_FIELD_FALLBACKS:
            old.pop(key)
        old.update(tank_pressure_pa=4e5, inlet_pressure=1.3e6,
                   feed_line_dp_pa=7e4, boost_pump_efficiency=.72)
        before = deepcopy(old)
        values, _ = input_values(old, saved_snapshot=True)
        self.assertEqual(old, before)
        for key in SUPPLY_FIELD_FALLBACKS:
            self.assertIsNone(values[key])
        for side in ("fuel", "ox"):
            row = supply_conditions(values, side)
            self.assertEqual(row, dict(tank_pressure_pa=4e5, boost_outlet_pressure_pa=1.3e6,
                                      feed_line_dp_pa=7e4, boost_pump_efficiency=.72,
                                      pump_inlet_pressure_pa=1.23e6))
        self.assertTrue(design_readiness(old)["valid"])

    def test_zero_loss_and_one_branch_override_do_not_modify_other_branch(self):
        inputs = EngineInputs(fuel_feed_line_dp_pa=0., ox_tank_pressure_pa=4e5,
                              fuel_boost_outlet_pressure_pa=1.2e6, fuel_boost_pump_efficiency=.8)
        fuel, ox = [supply_conditions(inputs, side) for side in ("fuel", "ox")]
        self.assertEqual(fuel["pump_inlet_pressure_pa"], 1.2e6)
        self.assertEqual(ox["pump_inlet_pressure_pa"], .95e6)
        self.assertEqual(ox["boost_pump_efficiency"], .7)
        self.assertEqual(fuel["tank_pressure_pa"], 3e5)
        self.assertEqual(ox["tank_pressure_pa"], 4e5)
        self.assertIsNone(inputs.ox_feed_line_dp_pa)
        summary = supply_summary(inputs)
        self.assertIsNone(summary["main_pump_inlet_pressure_bar"])
        self.assertAlmostEqual(summary["fuel_main_pump_inlet_pressure_bar"], 12.)
        self.assertAlmostEqual(summary["ox_main_pump_inlet_pressure_bar"], 9.5)

    def test_invalid_overrides_are_not_treated_as_fallback(self):
        for value in (False, "", "3", float("nan"), float("inf"), -1, 0):
            with self.subTest(value=value), self.assertRaises((ValueError, ValidationError)):
                input_values(platform_defaults() | {"fuel_tank_pressure_pa": value})
        for overrides in ({"ox_boost_pump_efficiency": 1.01},
                          {"fuel_feed_line_dp_pa": 1e6},
                          {"ox_boost_outlet_pressure_pa": 2e5}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                EngineInputs(**overrides)

    def test_saved_task_replays_old_shape_without_adding_branch_evidence(self):
        task = concept_design_task(platform_defaults())
        for group in task.values():
            if isinstance(group, dict):
                for key in SUPPLY_FIELD_FALLBACKS:
                    group.pop(key, None)
        before = deepcopy(task)
        restored = concept_inputs(task)
        self.assertTrue(set(restored).isdisjoint(SUPPLY_FIELD_FALLBACKS))
        self.assertEqual(task, before)
        # A partly missing new additive contract is not a historical snapshot.
        partial = concept_design_task(platform_defaults())
        partial["boundaries"].pop("fuel_tank_pressure_pa")
        with self.assertRaises(ValueError):
            concept_inputs(partial)


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "scientific libraries required")
class SupplyPhysicsTests(unittest.TestCase):
    def evaluate(self, cycle, **changes):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            inputs = EngineInputs(cycle_type=cycle, cooling_bartz_mode=0.,
                                  use_target_preburner_temp=0., use_target_gg_temp=0., **changes)
            model = (FFSCCycleModel if cycle == "ffsc" else GGCycleModel)(inputs)
            return model._evaluate_once(*model._initial_guess_vector())

    def test_shared_and_explicit_equal_branches_produce_identical_feed_states(self):
        legacy = dict(tank_pressure_pa=4e5, inlet_pressure=1.2e6,
                      feed_line_dp_pa=6e4, boost_pump_efficiency=.73)
        explicit = {key: legacy[value] for key, value in SUPPLY_FIELD_FALLBACKS.items()}
        for cycle in ("ffsc", "gg"):
            with self.subTest(cycle=cycle):
                old = self.evaluate(cycle, **legacy)
                new = self.evaluate(cycle, **legacy, **explicit)
                for prefix in ("fuel", "lox"):
                    for station in ("tank", "boost_out", "pump_inlet"):
                        key = prefix + "_" + station
                        for name in ("pressure", "temperature", "enthalpy", "density"):
                            self.assertEqual(getattr(old.states[key], name), getattr(new.states[key], name))
                for key in ("boost_pump_ox_power_MW", "boost_pump_fuel_power_MW", "ox_pump_power_MW", "fuel_pump_power_MW"):
                    self.assertEqual(old.summary[key], new.summary[key])

    def test_distinct_pressures_reach_both_main_pumps_and_keep_enthalpy_ledger(self):
        for cycle in ("ffsc", "gg"):
            with self.subTest(cycle=cycle):
                result = self.evaluate(cycle, fuel_tank_pressure_pa=3e5, ox_tank_pressure_pa=4e5,
                    fuel_boost_outlet_pressure_pa=1.2e6, ox_boost_outlet_pressure_pa=1.5e6,
                    fuel_feed_line_dp_pa=0., ox_feed_line_dp_pa=8e4,
                    fuel_boost_pump_efficiency=.75, ox_boost_pump_efficiency=.8)
                for prefix, side, pressure in (("fuel", "fuel", 1.2e6), ("lox", "ox", 1.42e6)):
                    tank, boost, inlet = [result.states[prefix + "_" + key] for key in ("tank", "boost_out", "pump_inlet")]
                    self.assertAlmostEqual(inlet.pressure, pressure, delta=1e-5)
                    self.assertAlmostEqual(boost.enthalpy, inlet.enthalpy)
                    self.assertAlmostEqual(tank.mass_flow * (boost.enthalpy - tank.enthalpy),
                        result.summary[f"boost_pump_{side}_power_MW"] * 1e6, delta=.01)
                    pump = result.components[f"{side}_pump"]
                    self.assertAlmostEqual(pump.outputs["T_in_K"], inlet.temperature, delta=1e-9)
                    expected_npsh = thermo.available_npsh_pressure("CH4" if side == "fuel" else "O2", inlet.temperature, pressure) / (inlet.density * 9.80665)
                    self.assertAlmostEqual(pump.outputs["npsh_available_m"], expected_npsh)
                self.assertIsNone(result.summary["tank_pressure_bar"])
                self.assertIsNone(result.summary["boost_pump_efficiency"])


if __name__ == "__main__":
    unittest.main()
