"""Independent branch budgets, legacy replay, and frozen-loss isolation."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.domain.models import EngineInputs, ValidationError
from lre_workbench.systems.ffsc import FFSCCycleModel
from lre_workbench.systems.pressure_budget import BRANCH_BUDGET_FIELDS, resolve_branch_pressure_budgets
from lre_workbench.systems.steady_network import audit_network, pressure_contract
from lre_workbench.properties.thermochemistry import CANTERA_AVAILABLE, COOLPROP_AVAILABLE
from tests import test_steady_network as network_tests

BASE = network_tests.BASE
solve = network_tests.solve


class BranchBudgetInputTests(unittest.TestCase):
    def test_defaults_and_json_roundtrip_preserve_optional_fields(self):
        inputs = EngineInputs()
        for fields in BRANCH_BUDGET_FIELDS.values():
            for key in fields.values():
                self.assertIsNone(getattr(inputs, key))
        raw = json.loads(json.dumps(asdict(inputs)))
        self.assertEqual(EngineInputs.from_dict(raw), inputs)
        old = {key: value for key, value in raw.items()
               if key not in {key for fields in BRANCH_BUDGET_FIELDS.values() for key in fields.values()}}
        self.assertEqual(EngineInputs.from_dict(old), inputs)

    def test_each_optional_field_rejects_invalid_types_and_ranges(self):
        for fields in BRANCH_BUDGET_FIELDS.values():
            for kind, key in fields.items():
                bad_values = (True, False, "0.1", math.nan, math.inf, -math.inf, -1, .31)
                if kind == "injector":
                    bad_values += (0, .02)
                for bad in bad_values:
                    with self.subTest(key=key, bad=bad), self.assertRaises(ValidationError):
                        EngineInputs(**{key: bad})

    def test_none_inherits_only_corresponding_common_value(self):
        inputs = EngineInputs(fuel_to_opb_injector_dp_ratio=.18, ox_to_fpb_line_dp_ratio=0)
        before = asdict(inputs)
        budgets = resolve_branch_pressure_budgets(inputs)
        self.assertEqual(asdict(inputs), before)
        self.assertEqual(budgets["fuel_to_opb"]["injector_dp_ratio"], .18)
        self.assertEqual(budgets["fuel_to_opb"]["injector_source"], "fuel_to_opb_injector_dp_ratio")
        self.assertEqual(budgets["ox_to_opb"]["injector_source"], "preburner_injector_dp_ratio")
        self.assertEqual(budgets["ox_to_fpb"]["line_dp_ratio"], 0)
        inputs.preburner_injector_dp_ratio = .2
        inputs.valve_line_dp_ratio = .04
        changed = resolve_branch_pressure_budgets(inputs)
        self.assertEqual(changed["fuel_to_opb"]["injector_dp_ratio"], .18)
        self.assertEqual(changed["ox_to_opb"]["injector_dp_ratio"], .2)
        self.assertEqual(changed["ox_to_fpb"]["line_dp_ratio"], 0)

    def test_gas_generator_inputs_do_not_activate_ffsc_fields(self):
        inputs = EngineInputs(cycle_type="gg", ox_to_opb_injector_dp_ratio=None)
        self.assertEqual(inputs.cycle_type, "gg")
        # Optional FFSC fields have no GG physical role, including legacy JSON.
        self.assertEqual(EngineInputs(cycle_type="gg", ox_to_opb_injector_dp_ratio=-.5).cycle_type, "gg")

    def test_fully_overridden_common_fields_do_not_gate_branch_validation(self):
        overrides = {fields[kind]: value for fields in BRANCH_BUDGET_FIELDS.values()
                     for kind, value in (("line", .03), ("injector", .12))}
        inputs = EngineInputs(**overrides, valve_line_dp_ratio=9, preburner_injector_dp_ratio=9)
        self.assertTrue(all(row["injector_dp_ratio"] == .12
                            for row in resolve_branch_pressure_budgets(inputs).values()))


class IndependentNetworkBudgetTests(unittest.TestCase):
    def budgets(self):
        return {key: dict(injector_ratio=BASE["injector_ratio"], line_dp_Pa=BASE["line_dp"])
                for key in BRANCH_BUDGET_FIELDS}

    def test_explicit_equal_budgets_reproduce_legacy_solution_and_frozen_coefficients(self):
        old = solve()
        explicit = solve(branch_budgets=self.budgets(), injector_ratio=None, line_dp=None)
        self.assertEqual(explicit, old)

    def test_each_branch_uses_its_own_loss_and_controls_its_supply_head(self):
        for key in BRANCH_BUDGET_FIELDS:
            with self.subTest(branch=key):
                budgets = self.budgets()
                budgets[key] = dict(injector_ratio=.25, line_dp_Pa=2e6)
                before = deepcopy(budgets)
                result = solve(branch_budgets=budgets)
                self.assertEqual(budgets, before)
                self.assertEqual(result["status"], "pass")
                rows = {row["id"]: row for row in result["branches"]}
                row = rows[key]
                self.assertEqual(row["injector_drop_Pa"], .25 * row["destination_pressure_Pa"])
                self.assertEqual(row["line_drop_Pa"], 2e6)
                self.assertEqual(row["valve_drop_Pa"], 0)
                self.assertEqual(result["tap_pressures_Pa"][row["propellant"]], row["required_source_pressure_Pa"])
                for other in rows.values():
                    if other["id"] != key:
                        self.assertEqual(other["injector_drop_Pa"], .1 * other["destination_pressure_Pa"])
                        self.assertEqual(other["line_drop_Pa"], .2e6)
                self.assertIsNone(result["design_loss_inputs"]["injector_ratio"])
                self.assertIsNone(result["design_loss_inputs"]["line_dp_Pa"])

    def test_saved_budget_audit_checks_each_branch_with_and_without_snapshot(self):
        for densities in (None, BASE["densities"]):
            with self.subTest(densities=densities):
                budgets = self.budgets()
                budgets["fuel_to_opb"]["injector_ratio"] = .2
                result = solve(branch_budgets=budgets, densities=densities)
                self.assertEqual(result["status"], "pass")
                result["design_loss_inputs"]["branches"]["fuel_to_opb"]["injector_ratio"] = .1
                audit = audit_network(result, total_fuel=100, total_ox=300, mr_ox=30, mr_fuel=.3)
                self.assertEqual(audit["status"], "fail")
                self.assertAlmostEqual(audit["branches"][0]["loss_law_residual_Pa"], 1e6)

    def test_old_record_without_branch_budget_map_remains_auditable(self):
        result = solve(densities=None)
        del result["design_loss_inputs"]["branches"]
        self.assertEqual(audit_network(result, total_fuel=100, total_ox=300)["status"], "pass")

    def test_frozen_hardware_ignores_all_design_budgets_and_reproduces_losses(self):
        budgets = self.budgets()
        budgets["ox_to_fpb"] = dict(injector_ratio=.2, line_dp_Pa=.4e6)
        reference = solve(branch_budgets=budgets)
        frozen = solve(fixed_hardware=reference["hardware_snapshot"],
                       branch_budgets={"invalid_unused_budget": None}, injector_ratio=None, line_dp=None)
        self.assertEqual(frozen["hardware_snapshot"], reference["hardware_snapshot"])
        self.assertIsNone(frozen["design_loss_inputs"])
        for old, new in zip(reference["branches"], frozen["branches"]):
            for field in ("line_drop_Pa", "injector_drop_Pa", "source_pressure_Pa", "valve_drop_Pa"):
                self.assertAlmostEqual(old[field], new[field], places=6)
            self.assertIsNone(new["design_pressure_budget"])

    def test_partial_branch_map_is_rejected_not_silently_filled(self):
        budgets = self.budgets()
        del budgets["fuel_to_opb"]
        with self.assertRaises(ValueError):
            solve(branch_budgets=budgets)

    def test_contract_records_inheritance_without_treating_null_as_missing_evidence(self):
        network, states, inputs = network_tests.PressureContractTests().fixture()
        report = pressure_contract(network, states, inputs)
        roles = {row["key"]: row for row in report["parameter_roles"]}
        self.assertEqual(roles["fuel_to_opb_injector_dp_ratio"]["role"], "inherited_common_budget")
        self.assertEqual(roles["fuel_to_opb_injector_dp_ratio"]["effective_value"], .1)
        self.assertFalse(any("fuel_to_opb" in key for key in report["missing_evidence"]))
        for fields in BRANCH_BUDGET_FIELDS.values():
            inputs[fields["injector"]] = .2
            inputs[fields["line"]] = .04
        roles = {row["key"]: row for row in pressure_contract(network, states, inputs)["parameter_roles"]}
        self.assertEqual(roles["preburner_injector_dp_ratio"]["role"], "inactive_with_branch_budgets")
        self.assertEqual(roles["valve_line_dp_ratio"]["role"], "inactive_with_branch_budgets")


class CyclePressureBudgetTests(unittest.TestCase):
    def test_initial_pressure_domain_bound_uses_each_oxidizer_branch(self):
        inputs = EngineInputs(ox_preburner_pressure_ratio=3, fuel_preburner_pressure_ratio=3,
                              ox_to_opb_injector_dp_ratio=.2, ox_to_opb_line_dp_ratio=.04)
        model = FFSCCycleModel(inputs)
        with patch('lre_workbench.systems.ffsc.COOLPROP_AVAILABLE', True), patch('lre_workbench.properties.fluids.fluid_spec', return_value={"pmax_Pa": 60e6}):
            seed = model._initial_guess_vector()
        self.assertAlmostEqual(seed[1], .95 * (60e6 - .04 * inputs.pc) / (inputs.pc * 1.2 * (1 + inputs.ox_turbine_exhaust_dp_ratio)))
        self.assertAlmostEqual(seed[2], .95 * (60e6 - .03 * inputs.pc) / (inputs.pc * 1.12 * (1 + inputs.fuel_turbine_exhaust_dp_ratio)))

    def test_frozen_seed_is_independent_of_design_budgets(self):
        inputs = EngineInputs()
        model = FFSCCycleModel(inputs)
        model._feed_hardware = solve()["hardware_snapshot"]
        with patch('lre_workbench.systems.ffsc.COOLPROP_AVAILABLE', True), patch('lre_workbench.properties.fluids.fluid_spec', return_value={"pmax_Pa": 100e6}):
            original = model._initial_guess_vector()
            for fields in BRANCH_BUDGET_FIELDS.values():
                for key in fields.values():
                    setattr(inputs, key, .2)
            inputs.preburner_injector_dp_ratio = .29
            inputs.valve_line_dp_ratio = .19
            self.assertEqual(model._initial_guess_vector(), original)

    @unittest.skipUnless(CANTERA_AVAILABLE and COOLPROP_AVAILABLE, "Requires the thermochemical backend")
    def test_cycle_exports_actual_inlet_losses_without_overwriting_independent_values(self):
        inputs = EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0,
                              fuel_to_opb_injector_dp_ratio=.18, fuel_to_opb_line_dp_ratio=.04)
        model = FFSCCycleModel(inputs)
        result = model._evaluate_once(*model._initial_guess_vector())
        network = result.steady_closure["feed_network"]
        rows = {row["id"]: row for row in network["branches"]}
        for side, suffix in (("ox", "opb"), ("fuel", "fpb")):
            outputs = result.components[f"{side}_preburner"].outputs
            self.assertEqual(outputs["fuel_injector_dp_Pa"], rows[f"fuel_to_{suffix}"]["injector_drop_Pa"])
            self.assertEqual(outputs["oxidizer_injector_dp_Pa"], rows[f"ox_to_{suffix}"]["injector_drop_Pa"])
        self.assertEqual(network["status"], "pass")
        self.assertIsNone(result.components["ox_preburner"].outputs["injector_dp_Pa"])
        self.assertIsNone(result.summary["injector_dp_ox_MPa"])
        self.assertIsNone(result.summary["valve_line_dp_MPa"])
        json.dumps(asdict(result), allow_nan=False)

    @unittest.skipUnless(CANTERA_AVAILABLE and COOLPROP_AVAILABLE, "Requires the thermochemical backend")
    def test_fully_overridden_cycle_is_invariant_to_old_common_inputs(self):
        inputs = EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0)
        for fields in BRANCH_BUDGET_FIELDS.values():
            setattr(inputs, fields["line"], .03)
            setattr(inputs, fields["injector"], .12)
        model = FFSCCycleModel(inputs)
        seed = model._initial_guess_vector()
        before = model._evaluate_once(*seed)
        inputs.preburner_injector_dp_ratio = .25
        inputs.valve_line_dp_ratio = .19
        self.assertEqual(model._initial_guess_vector(), seed)
        after = model._evaluate_once(*seed)
        self.assertEqual(before.summary, after.summary)
        self.assertEqual(before.states, after.states)
        self.assertEqual(before.components, after.components)
        for key in ("branches", "tap_pressures_Pa", "hardware_snapshot", "design_loss_inputs", "status"):
            self.assertEqual(before.steady_closure["feed_network"][key], after.steady_closure["feed_network"][key])

    @unittest.skipUnless(CANTERA_AVAILABLE and COOLPROP_AVAILABLE, "Requires the thermochemical backend")
    def test_frozen_cycle_preserves_pressure_losses_and_preburner_recording(self):
        model = FFSCCycleModel(EngineInputs(cooling_bartz_mode=0, use_target_preburner_temp=0,
                                          fuel_to_opb_injector_dp_ratio=.18))
        seed = model._initial_guess_vector()
        reference = model._evaluate_once(*seed)
        model._feed_hardware = deepcopy(reference.steady_closure["feed_network"]["hardware_snapshot"])
        before = model._evaluate_once(*seed)
        for fields in BRANCH_BUDGET_FIELDS.values():
            for key in fields.values():
                setattr(model.inputs, key, .2)
        model.inputs.valve_line_dp_ratio = .19
        model.inputs.preburner_injector_dp_ratio = .29
        after = model._evaluate_once(*seed)
        self.assertEqual(before.summary, after.summary)
        self.assertEqual(before.states, after.states)
        self.assertEqual(before.components, after.components)
        network = after.steady_closure["feed_network"]
        self.assertEqual(network["hardware_snapshot"], model._feed_hardware)
        for branch in network["branches"]:
            component = after.components[branch["target"]].outputs
            key = "fuel_injector_dp_Pa" if branch["propellant"] == "fuel" else "oxidizer_injector_dp_Pa"
            self.assertEqual(component[key], branch["injector_drop_Pa"])


if __name__ == "__main__":
    unittest.main()
