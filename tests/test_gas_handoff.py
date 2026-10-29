"""Gas provenance is part of the physical boundary, not display metadata."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import FlowState
from chamber import MainChamber
from preburner import Preburner
from turbine import Turbine
from gas_handoff import capture_gas_handoff, load_verified_gas_model
from thermo import validate_gas_streams, chamber_equilibrium
from thermo_data import methane_dataset_scope


def fixture():
    states, components = {}, {}
    with methane_dataset_scope("gri30_highT"):
        for side, fuel, oxygen, suffix in (("ox", 1.0, 40.0, "opb"), ("fuel", 9.0, 4.0, "fpb")):
            pb = Preburner(f"{side}_preburner", 40e6, fuel, oxygen, 1.0, 350.0, 90.0)
            state, gas, component = pb.evaluate()
            states[f"{side}_preburner_outlet"] = state
            components[component.name] = component
            for kind, mdot, h in (("fuel", fuel, component.outputs["h_fuel_in_Jkg"]),
                                  ("ox", oxygen, component.outputs["h_ox_in_Jkg"])):
                model = deepcopy(gas.model)
                model.update(eos="HEOS", chemical_reference_eos="ideal-gas")
                key = f"branch_{kind}_to_{suffix}"
                states[key] = FlowState(key, 40e6, 350.0 if kind == "fuel" else 90.0, mdot,
                    enthalpy=h, composition={"CH4" if kind == "fuel" else "O2": 1.0},
                    metadata={"model": model, "state_basis": {"kind": "reservoir_approximation"},
                              "reference_id": model["enthalpy_reference_id"], "composition_complete": True})
            turbine = Turbine(f"{side}_preburner_turbine", gas, 25e6, 0.85, state.mass_flow,
                               state_basis="total_to_total")
            exhaust, turbine_result = turbine.evaluate()
            states[f"{side}_turbine_exhaust"] = exhaust
            components[turbine_result.name] = turbine_result
        chamber = MainChamber(20e6, 54.0, 1.2, 22.0, 3500.0, heat_loss_W=1e6)
        outlet, result = chamber.evaluate(states["ox_turbine_exhaust"], states["fuel_turbine_exhaust"])
        states["main_chamber"] = outlet
        components[result.name] = result
    return {"calculation": {"states": {k: asdict(v) for k, v in states.items()},
                            "components": {k: asdict(v) for k, v in components.items()}}}


class GasHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = fixture()

    def streams(self):
        saved = self.source["calculation"]["states"]
        return [FlowState(**deepcopy(saved[key])) for key in ("ox_turbine_exhaust", "fuel_turbine_exhaust")]

    def test_chamber_hp_solution_is_not_prescribed_by_cooling_startup_estimates(self):
        states = []
        with methane_dataset_scope("gri30_highT"):
            for temperature, gamma, molar_mass in ((3200, 1.12, 18), (3550, 1.18, 21), (3900, 1.26, 25)):
                chamber = MainChamber(20e6, 54., gamma, molar_mass, temperature, heat_loss_W=1e6)
                outlet, component = chamber.evaluate(*self.streams())
                states.append(outlet)
                self.assertLess(abs(component.outputs["energy_residual_W"]), 1.)
            # A real energy change must still affect the result; only the
            # numerical estimates are independent of this HP problem.
            cooler, _ = MainChamber(20e6, 54., 1.18, 21., 3550., heat_loss_W=2e6).evaluate(*self.streams())
        for state in states[1:]:
            self.assertAlmostEqual(state.temperature, states[0].temperature, delta=1e-7)
            for key in ("gamma", "molar_mass"):
                self.assertAlmostEqual(state.metadata[key], states[0].metadata[key], delta=1e-9)
            for key, fraction in states[0].composition.items():
                self.assertAlmostEqual(state.composition[key], fraction, delta=1e-10)
        self.assertLess(cooler.temperature, states[0].temperature)
        self.assertAlmostEqual(states[0].enthalpy-cooler.enthalpy, 1e6/54., delta=.1)

    def test_actual_two_stream_cycle_preserves_identity_and_closes(self):
        before = json.dumps(self.source, sort_keys=True)
        result = capture_gas_handoff(self.source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["stations"]), 9)
        self.assertEqual(len(result["component_balances"]), 5)
        self.assertTrue(all(check["status"] == "pass" for check in result["element_checks"]))
        self.assertTrue(all(abs(row["enthalpy_residual_W"]) < 1 for row in result["component_balances"]))
        self.assertEqual(before, json.dumps(self.source, sort_keys=True))
        for row in result["stations"]:
            self.assertEqual(len(row["model"]["mechanism_sha256"]), 64)
            self.assertAlmostEqual(sum(row["element_mass_fractions"].values()), 1.0)
        station = next(row for row in result["stations"] if row["id"] == "ox_turbine_exhaust")
        self.assertGreater(station["density_kg_m3"], 0)
        self.assertIsNotNone(station["entropy_JkgK"])
        self.assertEqual(station["state_basis"]["kind"], "total")

    def test_missing_composition_is_not_replaced_by_reactants(self):
        streams = self.streams()
        streams[0].composition = {}
        with methane_dataset_scope("gri30_highT"), self.assertRaisesRegex(ValueError, "完整入口组成缺失"):
            validate_gas_streams(streams, expected_mass_flow=54)

    def test_nonfinite_enthalpy_and_flow_mismatch_rejected(self):
        for value in (None, math.inf, math.nan):
            streams = self.streams()
            streams[0].enthalpy = value
            with self.assertRaises(ValueError):
                validate_gas_streams(streams)
        with self.assertRaisesRegex(ValueError, "质量流量之和"):
            validate_gas_streams(self.streams(), expected_mass_flow=53)

    def test_known_reference_or_model_conflict_rejected(self):
        for field, value in (("enthalpy_reference_id", "different_reference"), ("eos", "Peng-Robinson"),
                             ("mechanism_sha256", "0" * 64)):
            streams = self.streams()
            streams[0].metadata["model"][field] = value
            with methane_dataset_scope("gri30_highT"), self.assertRaises(ValueError):
                validate_gas_streams(streams)

    def test_missing_legacy_identity_remains_incomplete(self):
        streams = self.streams()
        for stream in streams:
            stream.metadata = {}
        with methane_dataset_scope("gri30_highT"):
            self.assertEqual(validate_gas_streams(streams)["status"], "incomplete")

    def test_no_silent_model_switch_when_active_dataset_changes(self):
        with methane_dataset_scope("gri30"), self.assertRaisesRegex(ValueError, "主室所选模型"):
            validate_gas_streams(self.streams())

    def test_tampered_element_or_flow_is_detected_independently(self):
        for mutation in ("composition", "mass_flow", "saved_element"):
            source = deepcopy(self.source)
            state = source["calculation"]["states"]["ox_turbine_exhaust"]
            if mutation == "composition":
                state["composition"] = {"O2": 0.5, "CH4": 0.5}
            elif mutation == "mass_flow":
                state["mass_flow"] *= 1.1
            else:
                state["metadata"]["element_mass_fractions"]["C"] = 0.2
            result = capture_gas_handoff(source)
            self.assertEqual(result["status"], "inconsistent", mutation)
            if mutation != "saved_element":
                self.assertTrue(any(check["status"] == "fail" for check in result["element_checks"]), mutation)

    def test_tampered_enthalpy_breaks_independent_energy_balance(self):
        source = deepcopy(self.source)
        source["calculation"]["states"]["main_chamber"]["enthalpy"] += 1000
        result = capture_gas_handoff(source)
        balance = next(row for row in result["component_balances"] if row["id"] == "main_chamber")
        self.assertAlmostEqual(balance["enthalpy_residual_W"], -54000, delta=1)
        self.assertEqual(balance["status"], "inconsistent")

    def test_missing_or_changed_file_identity_blocks_reconstruction(self):
        for digest in (None, "0" * 64):
            source = deepcopy(self.source)
            model = source["calculation"]["states"]["main_chamber"]["metadata"]["model"]
            model["mechanism_sha256"] = digest
            with self.assertRaises(ValueError):
                load_verified_gas_model(model)
            result = capture_gas_handoff(source)
            chamber = next(row for row in result["stations"] if row["id"] == "main_chamber")
            self.assertEqual(chamber["element_fluxes_kg_s"], {})
            self.assertNotEqual(result["status"], "complete")

    def test_legacy_saved_environment_reconstruction_requires_exact_hash(self):
        source = deepcopy(self.source)
        reference = source["calculation"]["states"]["main_chamber"]["metadata"]["model"]
        source["environment"] = {"physics": {"mechanism": reference["mechanism"], "phase": reference["phase"]},
            "mechanism_sha256": reference["mechanism_sha256"], "dependencies": {"cantera": reference["cantera_version"]},
            "thermodynamic_scope": {"gas_eos": "ideal-gas", "pure_fluid_backend": "HEOS"}}
        for state in source["calculation"]["states"].values():
            state["metadata"].pop("model")
            state["metadata"]["thermochemical_data"] = reference["mechanism"]
            state["metadata"].pop("state_basis", None)
        result = capture_gas_handoff(source)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(all(check["status"] == "pass" for check in result["element_checks"]))
        source["environment"]["mechanism_sha256"] = "0" * 64
        self.assertFalse(any(check["status"] == "pass" for check in capture_gas_handoff(source)["element_checks"]))

    def test_bad_inputs_do_not_invent_zero_residual(self):
        result = capture_gas_handoff({})
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(all(check["difference_si"] is None for check in result["element_checks"]))

    def test_gg_does_not_borrow_ffsc_topology(self):
        result = capture_gas_handoff({"calculation": {"inputs": {"cycle_type": "gg"}}})
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["stations"], [])
        self.assertEqual(result["element_checks"], [])

    def test_same_arbitrary_reference_cannot_pass_by_matching_itself(self):
        source = deepcopy(self.source)
        for state in source["calculation"]["states"].values():
            state["metadata"]["model"]["enthalpy_reference_id"] = "invented_common_reference"
            state["metadata"]["reference_id"] = "invented_common_reference"
        self.assertEqual(capture_gas_handoff(source)["status"], "inconsistent")

    def test_direct_chamber_api_rejects_bad_energy_boundary(self):
        kwargs = dict(ox_exhaust_T=700, ox_exhaust_mdot=1, ox_exhaust_composition={"O2": 1},
                      fuel_exhaust_T=800, fuel_exhaust_mdot=1, fuel_exhaust_composition={"CH4": 1}, pressure=20e6)
        with self.assertRaises(ValueError):
            chamber_equilibrium(**kwargs, ox_exhaust_enthalpy=math.nan)
        kwargs["fuel_exhaust_mdot"] = -1
        with self.assertRaises(ValueError):
            chamber_equilibrium(**kwargs)


if __name__ == "__main__":
    unittest.main()
