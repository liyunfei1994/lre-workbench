"""Integration of a declared PR phase with actual reactive and frozen paths."""
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.properties import thermochemistry as thermo
from lre_workbench.properties.datasets import methane_dataset_scope, gas_eos_scope
from lre_workbench.integrations.gas_handoff import load_verified_gas_model
from lre_workbench.domain.models import EngineInputs, ValidationError


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "real property libraries required")
class PRThermochemistryTests(unittest.TestCase):
    def test_liquid_chemical_reference_is_independent_of_gas_eos(self):
        values = {}
        with methane_dataset_scope("gri30_highT"):
            for eos in ("ideal-gas", "Peng-Robinson"):
                with gas_eos_scope(eos, "pr_supported_v1"):
                    values[eos] = [thermo.reactant_enthalpy(s, t, 60e6)
                                   for s, t in (("CH4", 150), ("O2", 100))]
                    for (s, t), h in zip((("CH4", 150), ("O2", 100)), values[eos]):
                        self.assertAlmostEqual(thermo.reactant_temperature_from_enthalpy(s, h, 60e6), t, places=6)
        for a, b in zip(values["ideal-gas"], values["Peng-Robinson"]):
            self.assertAlmostEqual(a, b, places=8)

    def test_reactive_state_and_frozen_turbine_retain_pr_outside_scope(self):
        with methane_dataset_scope("gri30_highT"), gas_eos_scope("Peng-Robinson"):
            state = thermo.combustion_equilibrium(10, 600, 150, 100, 60e6)
        self.assertEqual(state.model["eos"], "Peng-Robinson")
        self.assertEqual(state.model["species_policy"], "pr_supported_v1")
        output = thermo.turbine_expansion(state, 40e6, .8)
        exhaust = output["gas_state"]
        self.assertEqual(exhaust.model, state.model)
        self.assertAlmostEqual(exhaust.pressure, 40e6, delta=.1)
        self.assertGreater(output["actual_enthalpy_drop"], 0)
        self.assertAlmostEqual(state.enthalpy - exhaust.enthalpy, output["actual_enthalpy_drop"], delta=.05)
        self.assertLess(max(abs(v-exhaust.composition.get(k, 0)) for k, v in state.composition.items()), 1e-12)
        gas = load_verified_gas_model(state.model)
        gas.TPY = state.temperature, state.pressure, state.composition
        self.assertAlmostEqual(gas.enthalpy_mass, state.enthalpy, delta=.05)
        changed = dict(state.model, eos_parameter_sha256="0"*64)
        with self.assertRaises(ValueError):
            load_verified_gas_model(changed)

    def test_pr_is_not_a_generic_fallback_for_other_cycles_or_unknown_models(self):
        with self.assertRaises(ValidationError):
            EngineInputs(gas_eos="Redlich-Kwong")
        with self.assertRaises(ValidationError):
            EngineInputs(gas_eos="Peng-Robinson", cycle_type="gg")

    def test_candidate_state_recovery_requires_saved_fingerprints(self):
        for eos in ("ideal-gas", "Peng-Robinson"):
            with methane_dataset_scope("gri30_highT"), gas_eos_scope(eos, "pr_supported_v1"):
                state = thermo.combustion_equilibrium(10, 600, 150, 100, 60e6)
            for field in ("species_policy_sha256", "eos_parameter_sha256"):
                model = deepcopy(state.model)
                model.pop(field)
                with self.subTest(eos=eos, field=field), self.assertRaisesRegex(ValueError, "保存指纹"):
                    thermo.turbine_expansion(replace(state, model=model), 40e6, .8)
        with methane_dataset_scope("gri30_highT"), gas_eos_scope("ideal-gas", "full"):
            full = thermo.combustion_equilibrium(10, 600, 150, 100, 60e6)
        # Historic full ideal states remain usable without candidate-only keys.
        self.assertGreater(thermo.turbine_expansion(replace(full, model={}), 40e6, .8)["actual_enthalpy_drop"], 0)

    def test_conservative_pr_to_ideal_handoff_rejects_missing_parameter_identity(self):
        from tests.test_gas_handoff import fixture
        from lre_workbench.domain.models import FlowState
        from lre_workbench.components.chambers.model import MainChamber
        with methane_dataset_scope("gri30_highT"), gas_eos_scope("Peng-Robinson", "pr_supported_v1"):
            record = fixture()
            for field in ("species_policy_sha256", "eos_parameter_sha256"):
                streams = [FlowState(**deepcopy(record["calculation"]["states"][name]))
                           for name in ("ox_turbine_exhaust", "fuel_turbine_exhaust")]
                for stream in streams:
                    stream.metadata["model"].pop(field)
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "保存指纹"):
                    MainChamber(20e6, 54., 1.2, 22., 3500., gas_eos_override="ideal-gas").evaluate(*streams)

    def test_conflicting_gas_state_and_model_eos_cannot_downgrade_pr(self):
        for eos in ("Peng-Robinson", "Peng-Robinson-Boston-Mathias"):
            with methane_dataset_scope("gri30_highT"), gas_eos_scope(eos, "pr_supported_v1"):
                state = thermo.combustion_equilibrium(10, 600, 150, 100, 60e6)
            model = deepcopy(state.model)
            model["eos"] = "ideal-gas"
            model.pop("eos_model_sha256", None)
            with self.subTest(eos=eos), self.assertRaisesRegex(ValueError, "EOS.*冲突"):
                thermo._gas_for_state(replace(state, model=model))


if __name__ == "__main__":
    unittest.main()
