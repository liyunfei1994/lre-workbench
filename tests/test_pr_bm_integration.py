"""PR-BM must survive the complete reactive/frozen handoff, including replay."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.properties import thermochemistry as thermo
from lre_workbench.properties.datasets import gas_eos_scope, methane_dataset_scope, saved_input_identity
from lre_workbench.domain.models import EngineInputs, FlowState, ValidationError
from lre_workbench.components.chambers.model import MainChamber
from lre_workbench.components.nozzles.model import Nozzle
from lre_workbench.integrations.gas_handoff import capture_gas_handoff, load_verified_gas_model
from tests.test_gas_handoff import fixture

BM = "Peng-Robinson-Boston-Mathias"


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "property libraries required")
class PRBMIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gas_eos_scope(BM):
            cls.record = fixture()
        cls.record["calculation"]["inputs"] = {"propellant_pair": "lox_methane", "cycle_type": "ffsc"}

    def chamber(self):
        streams = [FlowState(**deepcopy(self.record["calculation"]["states"][name]))
                   for name in ("ox_turbine_exhaust", "fuel_turbine_exhaust")]
        with methane_dataset_scope("gri30_highT"), gas_eos_scope(BM):
            chamber = MainChamber(20e6, 54., 1.2, 22., 3500., heat_loss_W=1e6)
            state, component = chamber.evaluate(*streams)
        return chamber.gas_state, state, component

    def test_main_chamber_and_nozzle_retain_bm_and_close_conservation(self):
        gas, state, component = self.chamber()
        self.assertEqual(state.metadata["model"]["eos"], BM)
        self.assertIsNone(state.metadata.get("eos_transition"))
        self.assertLess(abs(component.outputs["energy_residual_W"]), 1.)
        nozzle = Nozzle(20e6, 20., gas.gamma, gas.molar_mass, gas.temperature,
                        54., 0., chamber_state=gas).evaluate()
        out = thermo.nozzle_exit_state(gas, nozzle.outputs["p_exit_Pa"])
        self.assertEqual(out.model, gas.model)
        self.assertLess(abs(nozzle.outputs["energy_residual_W"]), 1.)
        self.assertAlmostEqual(out.entropy, gas.entropy, delta=1e-5)
        self.assertLess(max(abs(out.composition.get(k, 0)-v) for k,v in gas.composition.items()), 1e-12)
        self.assertAlmostEqual(nozzle.outputs["mach_exit"], nozzle.outputs["Ve_ms"]/out.sound_speed, places=10)
        self.assertAlmostEqual(out.density*nozzle.outputs["Ve_ms"]*nozzle.outputs["Ae_m2"], 54., delta=.001)

    def test_saved_handoff_reproduces_main_chamber_without_ideal_transition(self):
        from webapp.chamber_handoff_compare import compare_chamber_handoff
        handoff = capture_gas_handoff(self.record)
        self.assertEqual(handoff["status"], "complete")
        report = compare_chamber_handoff(self.record, handoff)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["production_reproduction"]["gas_eos"], BM)
        self.assertEqual(report["matched_cho_comparison"]["status"], "unavailable")
        self.assertIsNone(report["target"]["eos_transition"])

    def test_replay_rejects_missing_or_changed_alpha_model_identity(self):
        gas, _, _ = self.chamber()
        for value in (None, "0"*64):
            model = deepcopy(gas.model)
            if value is None:
                model.pop("eos_model_sha256")
            else:
                model["eos_model_sha256"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_verified_gas_model(model)
            with self.subTest(value=value), self.assertRaises(ValueError):
                thermo.nozzle_performance(replace(gas, model=model), 20e6, 20.)

    def test_independent_diagnostics_cannot_mutate_production_phase(self):
        gas, _, _ = self.chamber()
        production = thermo._gas_for_state(gas)
        before = production.state.copy()
        diagnostic = load_verified_gas_model(gas.model)
        diagnostic.TPY = 3000., 5e6, "H2O:1"
        self.assertLess(max(abs(production.state-before)), 1e-12)
        self.assertEqual(diagnostic.thermo_model, BM)

    def test_cooled_local_properties_do_not_reset_to_chamber_entropy(self):
        gas, _, _ = self.chamber()
        isentropic = thermo.nozzle_exit_state(gas, 1e6)
        cooled = thermo.frozen_state_at_tp(gas, isentropic.temperature-100., 1e6)
        self.assertEqual(cooled.model, gas.model)
        self.assertAlmostEqual(cooled.temperature, isentropic.temperature-100., delta=1e-8)
        self.assertLess(cooled.entropy, isentropic.entropy)
        self.assertLess(cooled.enthalpy, isentropic.enthalpy)
        self.assertEqual(set(cooled.composition), set(isentropic.composition))
        self.assertLess(max(abs(v-isentropic.composition[k]) for k,v in cooled.composition.items()), 1e-12)

    def test_no_ideal_fallback_and_no_implicit_history_upgrade(self):
        gas, _, _ = self.chamber()
        for method,args in ((thermo.nozzle_performance,(20e6,20.)), (thermo.nozzle_exit_state,(1e5,))):
            with self.assertRaises(RuntimeError):
                method(replace(gas, source="empirical"), *args)
        self.assertEqual(EngineInputs(gas_eos=BM).gas_eos, BM)
        with self.assertRaises(ValidationError):
            EngineInputs(gas_eos=BM, cycle_type="gg")
        self.assertEqual(saved_input_identity({})["gas_eos"], "ideal-gas")
        self.assertEqual(saved_input_identity({"gas_eos":"Peng-Robinson"})["gas_eos"], "Peng-Robinson")


if __name__ == "__main__":
    unittest.main()
