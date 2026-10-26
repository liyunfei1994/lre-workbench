"""Independent port-energy checks for the FFSC common enthalpy basis."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
from core import FlowState
from pump import Pump, fluid_density
from cooling import CoolingChannel
from preburner import Preburner
from chamber import MainChamber
from gg_cycle import GGCycleModel
from core import EngineInputs
from types import SimpleNamespace


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE,
                     "Cantera and CoolProp are required")
class ComponentEnergyClosureTests(unittest.TestCase):
    def test_gg_chamber_preserves_actual_port_enthalpy_and_explicit_heat_sink(self):
        from thermo_data import methane_dataset_scope
        for fuel, tf, mr in (("CH4",380.,3.6),("c12h26",400.,2.6)):
            with self.subTest(fuel=fuel), methane_dataset_scope("gri30_highT"):
                hf = thermo.reactant_enthalpy(fuel,tf,60e6,fuel)
                ho = thermo.reactant_enthalpy("O2",90.,60e6,fuel)
                incoming = hf+mr*ho
                temperatures = []
                for q, factor in ((0.,1.),(2e5,1.),(2e5,.98)):
                    diagnostics = {}
                    # Explicit port enthalpy must not be reconstructed at pc.
                    with patch.object(thermo,"_real_fluid_enthalpy",side_effect=AssertionError("port h was replaced")):
                        state = thermo.gg_chamber_equilibrium(1.,mr,tf,90.,10e6,
                            efficiency=factor,fuel_species=fuel,fuel_enthalpy=hf,ox_enthalpy=ho,
                            heat_loss_W=q,energy_diagnostics=diagnostics)
                    self.assertAlmostEqual((1+mr)*state.enthalpy+q+diagnostics["combustion_loss_W"],incoming,delta=.5)
                    self.assertAlmostEqual(diagnostics["energy_residual_W"],0.,delta=.5)
                    self.assertEqual(diagnostics["heat_loss_W"],q)
                    self.assertGreaterEqual(diagnostics["combustion_loss_W"],0.)
                    temperatures.append(state.temperature)
                self.assertGreater(temperatures[0],temperatures[1])
                self.assertGreater(temperatures[1],temperatures[2])
        with self.assertRaisesRegex(ValueError,"热汇"):
            thermo.gg_chamber_equilibrium(1.,3.6,380.,90.,10e6,heat_loss_W=float("nan"))
        with patch.object(thermo,"CANTERA_AVAILABLE",False), self.assertRaisesRegex(RuntimeError,"热汇"):
            thermo.gg_chamber_equilibrium(1.,3.6,380.,90.,10e6,heat_loss_W=1.)

    def test_gg_target_inversion_excludes_invalid_low_mr_endpoint(self):
        model = GGCycleModel(EngineInputs(cycle_type="gg"))
        def equilibrium(**kwargs):
            mr = kwargs["mdot_ox"]
            if mr < .2:
                raise ValueError("below HP gas domain")
            return SimpleNamespace(temperature=1000 * mr)
        with patch.object(thermo, "combustion_equilibrium", side_effect=equilibrium):
            mr = model._mr_for_target_gg_temp(475., 110., 90., 1e6)
            self.assertAlmostEqual(mr * 1000, 475., delta=.3)
            with self.assertRaisesRegex(ValueError, "没有目标温度括区"):
                model._mr_for_target_gg_temp(150., 110., 90., 1e6)

    def test_pump_port_enthalpy_matches_exact_unrounded_shaft_work(self):
        from CoolProp.CoolProp import PropsSI
        for species, fluid, temperature in (("CH4", "Methane", 110.0037),
                                            ("LOX", "Oxygen", 80.0041)):
            with self.subTest(species=species):
                state, result = Pump("test", species, 17.3, 950123., 70123456.,
                                     temperature, .80037, nr_rpm=20000).evaluate()
                inlet_h = PropsSI("H", "T", temperature, "P", 950123., fluid)
                inlet_s = PropsSI("S", "T", temperature, "P", 950123., fluid)
                exact_work = 17.3 * (PropsSI("H", "S", inlet_s, "P", 70123456., fluid)
                                    - inlet_h) / .80037
                self.assertAlmostEqual(result.outputs["power_W"], exact_work, delta=.01)
                port_power = 17.3 * (state.enthalpy - thermo.reactant_enthalpy(
                    species, temperature, 950123.))
                self.assertAlmostEqual(port_power, exact_work, delta=.01)

    def test_density_response_has_no_quantized_flat_step(self):
        from CoolProp.CoolProp import PropsSI
        first = fluid_density("CH4", 125.001, 70123456.)
        second = fluid_density("CH4", 125.002, 70123456.)
        self.assertNotEqual(first, second)
        expected = PropsSI("D", "T", 125.002, "P", 70123456., "Methane")
        self.assertAlmostEqual(second, expected, places=10)

    def test_isenthalpic_pressure_loss_is_not_isothermal(self):
        h_in = thermo.reactant_enthalpy("CH4", 380., 70e6)
        t_out = thermo.reactant_temperature_from_enthalpy("CH4", h_in, 55e6)
        self.assertGreater(abs(t_out - 380.), .1)
        self.assertAlmostEqual(thermo.reactant_enthalpy("CH4", t_out, 55e6), h_in, delta=.01)

    def test_real_fluid_query_failure_does_not_silently_switch_models(self):
        with patch.object(thermo, "fluid_state_tp", side_effect=ValueError("invalid state: checked HEOS failed")):
            with self.assertRaisesRegex(ValueError, "checked HEOS failed"):
                thermo.reactant_enthalpy("CH4", 110., 70e6)

    def test_fixed_cooling_heat_and_pressure_perturbations_conserve_energy(self):
        from CoolProp.CoolProp import PropsSI
        h_in = thermo.reactant_enthalpy("CH4", 125., 70e6)
        temperatures = []
        for heat, dp in ((0., 5e6), (20e6, 5e6), (21e6, 5e6), (20e6, 8e6)):
            state, result = CoolingChannel(160., 125., 70e6, dp, heat, bartz_mode=False,
                                            inlet_enthalpy=h_in).evaluate()
            cp_heat = 160. * (PropsSI("H", "T", state.temperature, "P", state.pressure, "Methane")
                             - PropsSI("H", "T", 125., "P", 70e6, "Methane"))
            self.assertAlmostEqual(cp_heat, heat, delta=.05)
            self.assertAlmostEqual(160. * (state.enthalpy - h_in), heat, delta=.05)
            self.assertAlmostEqual(result.outputs["energy_residual_W"], 0., delta=.05)
            temperatures.append(state.temperature)
        self.assertGreater(temperatures[2], temperatures[1])
        self.assertNotAlmostEqual(temperatures[3], temperatures[1], places=3)

    def test_bartz_heat_is_exactly_received_by_coolant(self):
        from CoolProp.CoolProp import PropsSI
        state, result = CoolingChannel(160., 125., 70e6, 5e6, mdot_total=750., pc=33e6,
                                        t_chamber=3500., cover_eps=34.).evaluate()
        expected_heat = 160. * (PropsSI("H", "T", state.temperature, "P", state.pressure, "Methane")
                               - PropsSI("H", "T", 125., "P", 70e6, "Methane"))
        self.assertAlmostEqual(expected_heat, result.outputs["heat_load_W"], delta=.1)
        self.assertLess(result.outputs["coolant_temperature_iteration_residual_K"], 1e-4)
        self.assertLess(result.outputs["wall_temperature_iteration_residual_K"], 1e-5)
        zone_sum = sum(result.outputs[k] * 1e6 for k in
                       ("Q_cc_MW", "Q_conv_MW", "Q_throat_MW", "Q_noz_MW", "Q_noz_ext_MW"))
        self.assertAlmostEqual(zone_sum, expected_heat, delta=.1)

    def test_preburner_uses_supplied_port_enthalpy_and_records_no_chemical_heat_source(self):
        h_f = thermo.reactant_enthalpy("CH4", 380., 70e6)
        h_o = thermo.reactant_enthalpy("O2", 90., 70e6)
        state, gas, result = Preburner("test", 50e6, 1., 50., .08, 380., 90.,
                                       fuel_enthalpy=h_f, ox_enthalpy=h_o).evaluate()
        expected_h = (h_f + 50. * h_o) / 51.
        self.assertAlmostEqual(state.enthalpy, expected_h, delta=.05)
        self.assertAlmostEqual(result.outputs["h_in_Jkg"], expected_h, delta=1e-8)
        self.assertAlmostEqual(result.outputs["energy_residual_W"], 0., delta=3.)
        legacy_h = (thermo.reactant_enthalpy("CH4", 380., 50e6)
                    + 50 * thermo.reactant_enthalpy("O2", 90., 50e6)) / 51.
        self.assertGreater(abs(expected_h - legacy_h), 100.)

    def test_out_of_range_hp_trial_is_rejected_instead_of_clipped(self):
        gas = thermo._get_gas("CH4")
        with self.assertRaisesRegex(ValueError, "不截断温度"):
            thermo._equilibrate_HP_bisection(gas, -1e9, 30e6, "O2:1")
        with self.assertRaisesRegex(ValueError, "有限目标焓"):
            thermo._equilibrate_HP_bisection(gas, float("nan"), 30e6, "O2:1")

    def test_chamber_heat_sink_and_efficiency_loss_are_separate(self):
        gas = thermo._get_gas("CH4")
        streams = []
        for species, mdot in (("O2", 36.), ("CH4", 10.)):
            gas.TPX = 600., 35e6, {species: 1.}
            streams.append(FlowState(species, 35e6, 600., mdot,
                                     enthalpy=gas.enthalpy_mass, composition={species: 1.}))
        enthalpy_in_W = sum(s.mass_flow * s.enthalpy for s in streams)
        results = []
        for heat, eta in ((0., 1.), (2e6, 1.), (2e6, .98)):
            state, result = MainChamber(33e6, 46., 1.2, 22., 3500., eta,
                                        heat_loss_W=heat).evaluate(*streams)
            results.append((state, result))
            self.assertAlmostEqual(46. * state.enthalpy + heat
                                   + result.outputs["combustion_loss_W"], enthalpy_in_W, delta=3.)
            self.assertAlmostEqual(result.outputs["energy_residual_W"], 0., delta=3.)
        self.assertAlmostEqual(46. * (results[0][0].enthalpy - results[1][0].enthalpy), 2e6, delta=3.)
        self.assertGreater(results[2][1].outputs["combustion_loss_W"], 0.)
        self.assertLess(results[2][0].temperature, results[1][0].temperature)


if __name__ == "__main__":
    unittest.main()
