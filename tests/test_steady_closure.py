"""Saved-state perturbations exercise independent energy conservation checks."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import ComponentResult, FlowState
from steady_closure import audit_energy, regenerative_heat_assumption


def balanced_loop():
    """Analytic 6 kg/s loop; main shaft work and regenerative heat are internal."""
    states = {}
    def state(key, mass, enthalpy, **metadata):
        states[key] = dict(name=key, mass_flow=mass, enthalpy=enthalpy,
                           metadata={"reference_id": "test_common_formation_reference", **metadata})
    state("fuel_tank", 2, -1000)
    state("lox_tank", 4, 200)
    state("fuel_boost_out", 2, -950)
    state("lox_boost_out", 4, 225)
    state("fuel_pump_inlet", 2, -950)
    state("lox_pump_inlet", 4, 225)
    state("fuel_after_pump", 2, -750)
    state("lox_after_pump", 4, 425)
    state("fuel_after_cooling", 2, -450)
    state("branch_fuel_to_opb", .5, -450)
    state("branch_fuel_to_fpb", 1.5, -450)
    state("branch_ox_to_opb", 3, 425)
    state("branch_ox_to_fpb", 1, 425)
    state("ox_preburner_outlet", 3.5, 300)
    state("fuel_preburner_outlet", 2.5, -100)
    state("ox_turbine_exhaust", 3.5, 250/3.5)
    state("fuel_turbine_exhaust", 2.5, -260)
    state("main_chamber", 6, -175)
    state("nozzle_exit", 6, -225, velocity_m_s=10)
    components = {key: {"name": key, "outputs": outputs} for key, outputs in {
        "ox_boost_pump": {"power_W": 100}, "fuel_boost_pump": {"power_W": 100},
        "ox_pump": {"power_W": 800}, "fuel_pump": {"power_W": 400},
        "ox_preburner_turbine": {"turbine_power_W": 800},
        "fuel_preburner_turbine": {"turbine_power_W": 400},
        "cooling_channel": {"heat_load_W": 600},
        "main_chamber": {"heat_loss_W": 600, "combustion_loss_W": 50},
    }.items()}
    return states, components


def by_id(ledger):
    return {row["id"]: row for row in ledger["checks"]}


class EnergyAuditTests(unittest.TestCase):
    def test_regenerative_heat_description_follows_saved_component_models(self):
        _, components = balanced_loop()
        components["cooling_channel"]["outputs"]["mode"] = 0
        components["nozzle"] = {"outputs": {"flow_model": 0}}
        self.assertIn("明确给定吸热量与压降", regenerative_heat_assumption(components))
        self.assertIn("集中热汇", regenerative_heat_assumption(components))
        components["cooling_channel"]["profile"] = {"version": 1, "model_id": "regen_quasi1d_ph_v1"}
        components["nozzle"]["profile"] = {"model_id": "heated_frozen_quasi1d_v1"}
        components["main_chamber"]["outputs"]["heat_loss_W"] = 0
        before = deepcopy(components)
        description = regenerative_heat_assumption(components)
        self.assertIn("按同一组单元热量沿程扣热", description)
        self.assertIn("主室不再重复集中扣除", description)
        self.assertNotIn("零维等效热回路", description)
        self.assertEqual(components, before)
        components["main_chamber"]["outputs"]["heat_loss_W"] = 600
        self.assertIn("需核查是否重复扣热", regenerative_heat_assumption(components))
        components["nozzle"]["profile"]["model_id"] = "unknown_future_model"
        self.assertIn("组合未识别", regenerative_heat_assumption(components))

    def test_spatial_hot_gas_heat_removal_is_counted_once(self):
        states, components = balanced_loop()
        # Move the same 600 W from the chamber control volume to the nozzle.
        components["main_chamber"]["outputs"]["heat_loss_W"] = 0
        states["main_chamber"]["enthalpy"] += 600 / 6
        components["nozzle"] = {"outputs": {"heat_loss_W": 600}}
        self.assertEqual(audit_energy(states, components)["status"], "pass")
        # Adding that heat sink twice must fail both the heat-transfer and gas accounts.
        components["main_chamber"]["outputs"]["heat_loss_W"] = 600
        ledger = audit_energy(states, components)
        self.assertEqual(ledger["status"], "fail")
        self.assertEqual(by_id(ledger)["regenerative_heat_exchange"]["status"], "fail")

    def test_analytic_loop_closes_with_negative_formation_enthalpy(self):
        states, components = balanced_loop()
        before = deepcopy((states, components))
        ledger = audit_energy(states, components)
        self.assertEqual(ledger["status"], "pass")
        self.assertAlmostEqual(ledger["residual_W"], 0)
        self.assertGreater(len(ledger["checks"]), 20)
        self.assertTrue(all(row["status"] == "pass" for row in ledger["checks"]))
        self.assertEqual(ledger["missing_evidence"], [])
        self.assertEqual((states, components), before)

    def test_global_boundary_counts_booster_work_but_not_regeneration_or_main_work_twice(self):
        states, components = balanced_loop()
        ledger = audit_energy(states, components)
        terms = {term["id"]: term for term in ledger["terms"]}
        self.assertEqual(terms["fuel_boost_power"]["role"], "in")
        self.assertEqual(terms["ox_pump_power"]["role"], "internal")
        self.assertEqual(terms["coolant_heat"]["role"], "internal")
        self.assertEqual(terms["hot_gas_heat"]["role"], "internal")
        incoming = sum(term["value_W"] for term in terms.values() if term["role"] == "in")
        outgoing = sum(term["value_W"] for term in terms.values() if term["role"] == "out")
        self.assertAlmostEqual(outgoing-incoming, ledger["residual_W"])

    def test_internal_saved_state_perturbation_fails_even_when_global_boundary_is_unchanged(self):
        states, components = balanced_loop()
        states["fuel_after_cooling"]["enthalpy"] += 100
        ledger = audit_energy(states, components)
        rows = by_id(ledger)
        self.assertEqual(ledger["status"], "fail")
        self.assertEqual(rows["global_energy"]["status"], "pass")
        self.assertEqual(rows["regenerative_coolant_energy"]["status"], "fail")
        self.assertEqual(rows["fuel_split_energy"]["status"], "fail")

    def test_one_sided_or_double_counted_cooling_heat_is_detected(self):
        for component, field in (("cooling_channel", "heat_load_W"), ("main_chamber", "heat_loss_W")):
            with self.subTest(component=component):
                states, components = balanced_loop()
                components[component]["outputs"][field] *= 2
                ledger = audit_energy(states, components)
                self.assertEqual(ledger["status"], "fail")
                self.assertEqual(by_id(ledger)["regenerative_heat_exchange"]["status"], "fail")

    def test_saved_nozzle_velocity_is_needed_and_changes_global_energy(self):
        states, components = balanced_loop()
        states["nozzle_exit"]["metadata"]["velocity_m_s"] = 20
        ledger = audit_energy(states, components)
        self.assertEqual(ledger["status"], "fail")
        self.assertAlmostEqual(ledger["residual_W"], 900)
        self.assertEqual(by_id(ledger)["nozzle_energy"]["status"], "fail")
        del states["nozzle_exit"]["metadata"]["velocity_m_s"]
        ledger = audit_energy(states, components)
        self.assertEqual(ledger["status"], "unavailable")
        self.assertIsNone(ledger["residual_W"])

    def test_external_efficiency_sink_and_booster_work_are_not_inferred(self):
        for component, field in (("main_chamber", "combustion_loss_W"), ("fuel_boost_pump", "power_W")):
            states, components = balanced_loop()
            components[component]["outputs"][field] += 100
            self.assertEqual(audit_energy(states, components)["status"], "fail")
            del components[component]["outputs"][field]
            ledger = audit_energy(states, components)
            self.assertEqual(ledger["status"], "unavailable")
            self.assertIsNone(ledger["residual_W"])

    def test_common_reference_must_be_explicit_and_identical_for_every_state(self):
        for reference in (None, "unrelated_reference"):
            states, components = balanced_loop()
            states["branch_fuel_to_opb"]["metadata"]["reference_id"] = reference
            ledger = audit_energy(states, components)
            self.assertEqual(ledger["status"], "unavailable")
            self.assertIsNone(ledger["reference_id"])
            self.assertIsNone(ledger["residual_W"])
            self.assertTrue(all(row["status"] == "unavailable" for row in ledger["checks"]))

    def test_missing_nonfinite_and_boolean_values_never_become_zero(self):
        for invalid in (None, float("nan"), float("inf"), True, "0"):
            states, components = balanced_loop()
            states["fuel_tank"]["enthalpy"] = invalid
            ledger = audit_energy(states, components)
            self.assertEqual(ledger["status"], "unavailable")
            self.assertIsNone(ledger["residual_W"])
        self.assertEqual(audit_energy({}, {})["status"], "unavailable")

    def test_tolerance_uses_declared_si_policy_and_unrounded_values(self):
        states, components = balanced_loop()
        ledger = audit_energy(states, components)
        self.assertAlmostEqual(ledger["tolerance_W"], 10 + 1e-5*2000)
        states["nozzle_exit"]["enthalpy"] += 2
        ledger = audit_energy(states, components)
        self.assertEqual(ledger["status"], "fail")
        self.assertAlmostEqual(ledger["residual_W"], 12)

    def test_dataclass_and_serialized_audits_are_identical(self):
        states, components = balanced_loop()
        state_objects = {key: FlowState(pressure=1e6, temperature=300, **value) for key, value in states.items()}
        component_objects = {key: ComponentResult(**value) for key, value in components.items()}
        expected = audit_energy(states, components)
        self.assertEqual(audit_energy(state_objects, component_objects), expected)
        self.assertEqual(audit_energy({key: asdict(value) for key, value in state_objects.items()},
                                      {key: asdict(value) for key, value in component_objects.items()}), expected)


if __name__ == "__main__":
    unittest.main()
