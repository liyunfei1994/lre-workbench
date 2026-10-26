"""Real cycle regressions and conservation checks for both supported fuels."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest
import warnings

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core import EngineInputs, ValidationError
from convergence import numerical_convergence
from cycle import FFSCCycleModel
from engine_presets import preset_input_values
from gg_cycle import make_cycle_model
from propellants import get_propellant


def kerosene_example(layout="single_shaft"):
    """An engineering test case, not a reconstruction of a production engine."""
    values = preset_input_values("merlin1d_gg")
    values["gg_layout"] = layout
    return EngineInputs(**values)


class PropellantInputTests(unittest.TestCase):
    def test_old_inputs_default_to_methane(self):
        self.assertEqual(EngineInputs().propellant_pair, "lox_methane")
        self.assertEqual(EngineInputs(**{"cycle_type": "gg"}).propellant_pair, "lox_methane")

    def test_unsupported_fuel_cycle_and_cooling_fail_explicitly(self):
        with self.assertRaisesRegex(ValidationError, "propellant_pair"):
            EngineInputs(propellant_pair="unknown")
        values = asdict(kerosene_example())
        for changes, message in [({"cycle_type": "ffsc"}, "FFSC"),
                                 ({"cooling_bartz_mode": 1.0}, "Bartz"),
                                 ({"fuel_inlet_temp": 100.0}, "正十二烷")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValidationError, message):
                EngineInputs(**(values | changes))
        # Legacy switches remain readable, but cannot select a second model.
        legacy = EngineInputs(**(values | {"cooling_dp_auto": 1.0}))
        self.assertEqual(legacy.cooling_bartz_mode, 0.0)
        self.assertEqual(legacy.cooling_dp_pa, values["cooling_dp_pa"])
        # A direct class call must not accidentally use the methane FFSC path.
        with self.assertRaisesRegex(ValidationError, "仅支持液氧/甲烷"):
            FFSCCycleModel(kerosene_example())
        mutated = kerosene_example()
        mutated.cooling_bartz_mode = 1.0
        with self.assertRaisesRegex(ValidationError, "甲烷详细冷却关联式"):
            make_cycle_model(mutated)

    def test_gas_tank_cannot_pass_via_liquid_main_pump_inlet(self):
        from CoolProp.CoolProp import PhaseSI
        values = asdict(kerosene_example()) | {
            "fuel_inlet_temp": 600.0, "tank_pressure_pa": .3e6,
            "inlet_pressure": 1e6, "feed_line_dp_pa": .05e6,
        }
        self.assertEqual(PhaseSI("T", 600.0, "P", .3e6, "n-Dodecane"), "gas")
        self.assertEqual(PhaseSI("T", 600.0, "P", .95e6, "n-Dodecane"), "liquid")
        with warnings.catch_warnings(), self.assertRaisesRegex(ValueError, "燃料贮箱"):
            warnings.simplefilter("ignore")
            make_cycle_model(EngineInputs(**values)).evaluate(include_cooling_marching=False)


class PropellantCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}
        cls.snapshots = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for layout in ("single_shaft", "dual_shaft"):
                inputs = kerosene_example(layout)
                cls.snapshots[layout] = asdict(inputs)
                cls.results[layout] = make_cycle_model(inputs).evaluate(include_cooling_marching=False)

    def test_both_kerosene_shaft_layouts_close_mass_thrust_and_power(self):
        for layout, result in self.results.items():
            with self.subTest(layout=layout):
                s = result.summary
                self.assertLess(s["solver_residual_norm"], 1e-4)
                self.assertFalse(s.get("solver_warning", 0))
                self.assertLess(abs(s["thrust_error_percent"]), .01)
                self.assertAlmostEqual(s["mdot_total_kg_s"], s["mdot_chamber_kg_s"] + s["gg_mdot_kg_s"], places=8)
                self.assertAlmostEqual(s["mdot_fuel_kg_s"], s["mdot_coolant_kg_s"] + s["gg_mdot_fuel_kg_s"], places=8)
                self.assertLess(abs(s["ox_side_power_balance_ratio"] - 1), 1e-4)
                self.assertLess(abs(s["fuel_side_power_balance_ratio"] - 1), 1e-4)
                self.assertGreater(s["thrust_vac_N"], s["thrust_N"])
                self.assertEqual(result.components["nozzle"].outputs["flow_model"], 0.0)
                self.assertNotIn("cooling_marching", result.components)

    def test_kerosene_identity_reaches_all_fuel_states_and_chemistry(self):
        model = get_propellant("lox_kerosene")
        for layout, result in self.results.items():
            with self.subTest(layout=layout):
                self.assertEqual(result.inputs.propellant_pair, "lox_kerosene")
                self.assertEqual(result.summary["fuel_species"], "c12h26")
                self.assertEqual(result.summary["thermochemistry_mechanism"], "nDodecane_Reitz.yaml")
                for key in ("fuel_tank", "fuel_boost_out", "fuel_pump_inlet", "fuel_after_pump", "fuel_after_cooling"):
                    state = result.states[key]
                    self.assertEqual(state.composition, {"c12h26": 1.0}, key)
                    self.assertGreater(state.temperature, 263.6)
                self.assertGreater(result.states["fuel_tank"].density, 700)
                self.assertAlmostEqual(result.summary["gg_phi"], model.stoich_of / result.summary["gg_mr"], places=9)
                self.assertIn("co2", result.states["main_chamber"].composition)
                meta = result.states["main_chamber"].metadata
                self.assertGreater(meta["thermo_extrapolated_mass_fraction"], 0)
                self.assertLessEqual(meta["thermo_extrapolated_mass_fraction"], 1)
                self.assertEqual(meta["thermo_common_max_temperature_K"], 3000.0)
                self.assertAlmostEqual(sum(meta["thermo_extrapolated_species_mass_fractions"].values()),
                                       meta["thermo_extrapolated_mass_fraction"], places=12)
                self.assertEqual(result.components["main_chamber"].outputs["thermo_extrapolated_mass_fraction"],
                                 meta["thermo_extrapolated_mass_fraction"])
                self.assertTrue(any("物种质量分数" in text for text in result.components["main_chamber"].notes))
                self.assertTrue(any("正十二烷" in text for text in result.warnings))
                labels = " ".join(node.label for node in result.topology_nodes)
                labels += " " + " ".join(edge.label for edge in result.topology_edges)
                self.assertIn("煤油", labels)
                self.assertNotIn("甲烷", labels)
                self.assertNotIn("c12h26", labels)
                self.assertEqual(asdict(result.inputs), self.snapshots[layout])
                json.dumps(asdict(result), allow_nan=False)

    def test_cooling_uses_real_fuel_enthalpy_and_fixed_pressure_loss(self):
        from CoolProp.CoolProp import PropsSI
        for layout, result in self.results.items():
            with self.subTest(layout=layout):
                before = result.states["fuel_after_pump"]
                after = result.states["fuel_after_cooling"]
                h_in = PropsSI("H", "T", before.temperature, "P", before.pressure, "n-Dodecane")
                h_out = PropsSI("H", "T", after.temperature, "P", after.pressure, "n-Dodecane")
                heat = after.mass_flow * (h_out - h_in)
                self.assertAlmostEqual(heat, result.inputs.cooling_heat_load, delta=1e-5 * result.inputs.cooling_heat_load)
                self.assertAlmostEqual(before.pressure - after.pressure, result.inputs.cooling_dp_pa, places=6)


class MethaneRegressionTests(unittest.TestCase):
    def test_existing_three_design_points_close_all_enabled_solver_layers(self):
        # Old snapshots ended before their thermal feedback converged. Requiring
        # those exact outputs would preserve that bug. Keep the original input
        # presets and independently enforce convergence and conservation instead.
        cases = (("tq12a_gg", "single_shaft"), ("tq12a_gg", "dual_shaft"),
                 ("raptor3_ffsc", "single_shaft"))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for preset, layout in cases:
                with self.subTest(preset=preset, layout=layout):
                    values = preset_input_values(preset)
                    values["gg_layout"] = layout
                    result = make_cycle_model(EngineInputs(**values)).evaluate(include_cooling_marching=False)
                    status = numerical_convergence(result)
                    self.assertEqual(status["status"], "pass", status)
                    self.assertLessEqual(result.summary["solver_residual_norm"], 1e-4)
                    self.assertLessEqual(result.summary["tc_outer_residual_K"], 5)
                    for name, layer in status["layers"].items():
                        if layer["enabled"]:
                            self.assertEqual(layer["status"], "pass", name)
                            self.assertLessEqual(layer["residual"], layer["tolerance"], name)
                            for check in layer.get("checks", []):
                                self.assertLessEqual(check["residual"], check["tolerance"], name)
                    s = result.summary
                    self.assertAlmostEqual(s["mdot_total_kg_s"], s["mdot_fuel_kg_s"] + s["mdot_ox_kg_s"], places=8)
                    self.assertAlmostEqual(s["thrust_N"], s["Isp_s"] * s["mdot_total_kg_s"] * 9.80665,
                                           delta=1e-8 * s["thrust_N"])
                    self.assertLessEqual(abs(s["thrust_N"] / values["thrust"] - 1), 1e-4)
                    self.assertAlmostEqual(s["chamber_pressure_Pa"], values["pc"])
                    self.assertGreater(s["Isp_vac_s"], s["Isp_s"])
                    self.assertEqual(result.convergence["schema_version"], 1)
                    self.assertEqual(s["solver_inner_solves"], len(result.convergence["inner_runs"]))
                    self.assertEqual(s["solver_iterations"], sum(len(run["points"]) - 1 for run in result.convergence["inner_runs"]))
                    self.assertEqual(result.states["fuel_tank"].composition, {"CH4": 1.0})
                    self.assertAlmostEqual(result.states["fuel_tank"].density, 439.08801118210926, places=7)


if __name__ == "__main__":
    unittest.main()
