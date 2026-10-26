"""Analytic network balances, hydraulic similarity and corrupted-record checks."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from steady_network import audit_network, solve_design, split_flows, validate_hardware, pressure_contract


BASE = dict(total_fuel=100.0, total_ox=300.0, mr_ox=30.0, mr_fuel=.3,
            p_pre_ox=10e6, p_pre_fuel=8e6, injector_ratio=.1, line_dp=.2e6,
            densities={"fuel": 300.0, "ox": 1200.0},
            temperatures={"fuel": 350.0, "ox": 90.0})


def solve(**changes):
    return solve_design(**(BASE | changes))


def audit(result):
    return audit_network(result, total_fuel=100, total_ox=300, mr_ox=30, mr_fuel=.3)


class FeedNetworkTests(unittest.TestCase):
    def test_analytic_four_branch_solution_and_shared_heads(self):
        result = solve()
        self.assertEqual(result["status"], "pass")
        rows = {r["id"]: r for r in result["branches"]}
        self.assertAlmostEqual(rows["fuel_to_opb"]["mass_flow_kg_s"], 100 / 11)
        self.assertAlmostEqual(rows["fuel_to_fpb"]["mass_flow_kg_s"], 1000 / 11)
        self.assertAlmostEqual(rows["ox_to_opb"]["mass_flow_kg_s"], 3000 / 11)
        self.assertAlmostEqual(rows["ox_to_fpb"]["mass_flow_kg_s"], 300 / 11)
        self.assertEqual(result["tap_pressures_Pa"], {"fuel": 11.2e6, "ox": 11.2e6})
        self.assertEqual(rows["fuel_to_opb"]["valve_drop_Pa"], 0)
        self.assertEqual(rows["fuel_to_fpb"]["valve_drop_Pa"], 2.2e6)
        self.assertEqual(result["pressure_reserve_status"], "not_demonstrated")

    def test_higher_fuel_pb_switches_active_constraints_without_negative_valve(self):
        result = solve(p_pre_fuel=12e6)
        self.assertEqual(result["status"], "pass")
        for row in result["branches"]:
            self.assertGreaterEqual(row["valve_drop_Pa"], 0)
            self.assertEqual(row["active_minimum_head_constraint"], row["target"] == "fuel_preburner")

    def test_equal_pb_heads_have_four_active_branches(self):
        result = solve(p_pre_fuel=10e6)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(all(row["valve_drop_Pa"] == 0 for row in result["branches"]))

    def test_incompatible_mr_targets_are_rejected_not_clipped(self):
        for mr_o, mr_f in ((2, .3), (30, 4), (3, .3), (30, 3), (.3, 30)):
            with self.subTest(mr_o=mr_o, mr_f=mr_f), self.assertRaises(ValueError):
                split_flows(100, 300, mr_o, mr_f)

    def test_explicit_free_split_preserves_mass_without_imposing_mr(self):
        flows = dict(fuel_to_opb=10, fuel_to_fpb=90, ox_to_opb=290, ox_to_fpb=10)
        result = solve(flows_override=flows)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["mixture_ratio_residuals_kg_s"], {})
        self.assertEqual(result["unknown_count"], 6)
        with self.assertRaises(ValueError):
            solve(flows_override=flows | {"fuel_to_opb": 11})

    def test_frozen_design_point_reproduces_heads_and_pressure_budgets(self):
        reference = solve()
        frozen = solve(fixed_hardware=reference["hardware_snapshot"], injector_ratio=9, line_dp=9e8)
        self.assertEqual(frozen["status"], "pass")
        self.assertEqual(frozen["mode"], "fixed_resistance")
        for old, new in zip(reference["branches"], frozen["branches"]):
            for key in ("source_pressure_Pa", "line_drop_Pa", "injector_drop_Pa", "valve_drop_Pa"):
                self.assertAlmostEqual(old[key], new[key], places=6)

    def test_expanded_trial_can_expose_unclosed_mass_but_never_reports_pass(self):
        flows = dict(fuel_to_opb=11, fuel_to_fpb=90, ox_to_opb=290, ox_to_fpb=10)
        result = solve(flows_override=flows, allow_unclosed_flows=True)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["source_mass_residuals_kg_s"]["fuel"], 1)

    def test_half_flow_fixed_density_reduces_frozen_losses_to_quarter(self):
        reference = solve()
        half = solve(total_fuel=50, total_ox=150, p_pre_ox=5e6, p_pre_fuel=4e6,
                     fixed_hardware=reference["hardware_snapshot"])
        self.assertEqual(half["status"], "pass")
        for old, new in zip(reference["branches"], half["branches"]):
            self.assertAlmostEqual(new["line_drop_Pa"], old["line_drop_Pa"] / 4)
            self.assertAlmostEqual(new["injector_drop_Pa"], old["injector_drop_Pa"] / 4)
        self.assertAlmostEqual(half["tap_pressures_Pa"]["fuel"], 5.3e6)

    def test_density_change_changes_losses_and_couples_distinct_pump_heads(self):
        reference = solve()
        changed = solve(densities={"fuel": 150, "ox": 1200}, fixed_hardware=reference["hardware_snapshot"])
        self.assertEqual(changed["status"], "pass")
        self.assertAlmostEqual(changed["tap_pressures_Pa"]["fuel"], 12.4e6)
        self.assertAlmostEqual(changed["tap_pressures_Pa"]["ox"], 11.2e6)

    def test_snapshot_missing_density_cannot_be_used_as_frozen_hardware(self):
        result = solve(densities=None)
        self.assertEqual(result["status"], "pass")
        self.assertIsNone(result["hardware_snapshot"])
        with self.assertRaises(ValueError):
            solve(densities=None, fixed_hardware=solve()["hardware_snapshot"])

    def test_hardware_rejects_missing_negative_or_wrong_topology(self):
        reference = solve()["hardware_snapshot"]
        for modify in (
            lambda r: r["branches"].pop("fuel_to_opb"),
            lambda r: r["branches"]["fuel_to_opb"].update(K_injector=-1),
            lambda r: r["branches"]["fuel_to_opb"].update(source="fuel_after_pump"),
            lambda r: r.update(schema_version=99),
        ):
            bad = deepcopy(reference)
            modify(bad)
            with self.assertRaises(ValueError):
                validate_hardware(bad)

    def test_saved_pressure_corruption_fails_even_if_cached_residual_claims_zero(self):
        result = solve()
        result["branches"][0]["source_pressure_Pa"] -= 4e6
        result["branches"][0]["pressure_residual_Pa"] = 0
        checked = audit(result)
        self.assertEqual(checked["status"], "fail")
        self.assertGreaterEqual(checked["max_pressure_residual_Pa"], 4e6)

    def test_passive_pressure_gain_is_rejected_even_when_pressure_sum_closes(self):
        result = solve()
        row = result["branches"][0]
        row["source_pressure_Pa"] -= 1e6
        row["valve_drop_Pa"] = -1e6
        checked = audit(result)
        self.assertEqual(checked["status"], "fail")
        self.assertTrue(any("cannot add pressure" in error for error in checked["errors"]))

    def test_conserved_mass_with_wrong_pb_mixtures_fails_mr_equations(self):
        result = solve()
        result["branches"][0]["mass_flow_kg_s"] += 1
        result["branches"][1]["mass_flow_kg_s"] -= 1
        checked = audit(result)
        self.assertEqual(checked["status"], "fail")
        self.assertAlmostEqual(checked["source_mass_residuals_kg_s"]["fuel"], 0)
        self.assertAlmostEqual(checked["mixture_ratio_residuals_kg_s"]["ox"], -30)

    def test_pressure_closed_but_corrupted_hydraulic_loss_fails(self):
        result = solve()
        row = result["branches"][1]
        row["injector_drop_Pa"] += .5e6
        row["valve_drop_Pa"] -= .5e6
        checked = audit(result)
        self.assertEqual(checked["status"], "fail")
        self.assertAlmostEqual(checked["branches"][1]["pressure_residual_Pa"], 0)
        self.assertAlmostEqual(checked["branches"][1]["loss_law_residual_Pa"], .5e6)

    def test_nonfinite_or_boolean_inputs_and_corrupt_records_are_rejected(self):
        for value in (float("nan"), float("inf"), True, 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                solve(total_fuel=value)
        result = solve()
        result["branches"][0]["mass_flow_kg_s"] = float("nan")
        self.assertEqual(audit(result)["status"], "fail")


class PressureContractTests(unittest.TestCase):
    def fixture(self, fixed=False):
        network = solve()
        if fixed:
            network = solve(total_fuel=50, total_ox=150, fixed_hardware=network["hardware_snapshot"])
        taps = network["tap_pressures_Pa"]
        states = {key: {"pressure": value} for key, value in {
            "main_chamber": 6e6, "ox_preburner_outlet": 10e6, "fuel_preburner_outlet": 8e6,
            "ox_turbine_exhaust": 7.2e6, "fuel_turbine_exhaust": 6.6e6,
            "lox_pump_inlet": 1e6, "fuel_pump_inlet": 1e6,
            "lox_after_pump": taps["ox"], "fuel_after_pump": taps["fuel"] + 2e6,
            "fuel_after_cooling": taps["fuel"],
        }.items()}
        for branch in network["branches"]:
            states["branch_" + branch["id"]] = {"pressure": branch["destination_pressure_Pa"]}
        inputs = dict(preburner_injector_dp_ratio=.1, valve_line_dp_ratio=.2e6/6e6,
            ox_turbine_exhaust_dp_ratio=.2, fuel_turbine_exhaust_dp_ratio=.1,
            cooling_dp_pa=2e6, cooling_dp_auto=0)
        return network, states, inputs

    def test_turbine_ratio_uses_actual_exhaust_not_chamber_pressure(self):
        args = self.fixture()
        before = deepcopy(args)
        report = pressure_contract(*args)
        self.assertEqual(args, before)
        self.assertEqual(report["pressure_link_status"], "pass")
        path = report["gas_paths"][0]
        self.assertAlmostEqual(path["turbine_pressure_ratio"], 10 / 7.2)
        self.assertAlmostEqual(path["preburner_chamber_pressure_ratio"], 10 / 6)
        self.assertNotEqual(path["turbine_pressure_ratio"], path["preburner_chamber_pressure_ratio"])
        self.assertAlmostEqual(path["hot_gas_drop_Pa"], 1.2e6)
        self.assertAlmostEqual(path["pressure_residual_Pa"], 0)

    def test_solved_required_pump_head_does_not_claim_pump_capability(self):
        report = pressure_contract(*self.fixture())
        capacity = report["pump_capacity"]
        self.assertEqual(capacity["status"], "not_verified")
        fuel = capacity["pumps"][1]
        self.assertAlmostEqual(fuel["required_outlet_pressure_Pa"], 13.2e6)
        self.assertAlmostEqual(fuel["required_rise_Pa"], 12.2e6)
        self.assertEqual(fuel["pressure_link_status"], "pass")
        self.assertEqual(fuel["capacity_status"], "not_verified")
        self.assertIsNone(fuel["available_outlet_pressure_Pa"])
        self.assertIsNone(fuel["available_pressure_margin_Pa"])

    def test_corrupt_exhaust_state_exposes_budget_residual(self):
        network, states, inputs = self.fixture()
        states["ox_turbine_exhaust"]["pressure"] += .4e6
        report = pressure_contract(network, states, inputs)
        self.assertEqual(report["pressure_link_status"], "fail")
        path = report["gas_paths"][0]
        self.assertAlmostEqual(path["hot_gas_budget_drop_Pa"], 1.2e6)
        self.assertAlmostEqual(path["hot_gas_drop_Pa"], 1.6e6)
        self.assertAlmostEqual(path["pressure_residual_Pa"], .4e6)

    def test_fixed_cooling_budget_checked_against_saved_state_drop(self):
        network, states, inputs = self.fixture()
        states["fuel_after_pump"]["pressure"] += .3e6
        report = pressure_contract(network, states, inputs)
        fuel = report["pump_capacity"]["pumps"][1]
        self.assertEqual(fuel["pressure_link_status"], "fail")
        self.assertAlmostEqual(fuel["cooling_drop_Pa"], 2.3e6)
        self.assertAlmostEqual(fuel["adopted_cooling_drop_Pa"], 2e6)
        self.assertAlmostEqual(fuel["cooling_budget_residual_Pa"], .3e6)

    def test_unified_cooling_contract_uses_saved_profile_not_inactive_budget(self):
        network, states, inputs = self.fixture()
        inputs.update(cooling_dp_auto=0, cooling_dp_pa=5e6)
        actual_drop = 14.259946618e6
        states["fuel_after_pump"]["pressure"] = network["tap_pressures_Pa"]["fuel"] + actual_drop
        cooling = dict(profile=dict(version=1, model_id="regen_quasi1d_ph_v1",
                                    summary=dict(dp_total_Pa=actual_drop)),
                       outputs=dict(delta_P_source=1, delta_P_MPa=actual_drop/1e6))
        before = deepcopy((network, states, inputs, cooling))
        report = pressure_contract(network, states, inputs, cooling_component=cooling)
        fuel = report["pump_capacity"]["pumps"][1]
        self.assertEqual(report["pressure_link_status"], "pass")
        self.assertAlmostEqual(fuel["required_outlet_pressure_Pa"], states["fuel_after_pump"]["pressure"])
        self.assertAlmostEqual(fuel["adopted_cooling_drop_Pa"], actual_drop)
        self.assertEqual(fuel["cooling_drop_source"], "unified_cooling_profile")
        self.assertIsNone(fuel["cooling_budget_residual_Pa"])
        self.assertEqual(report["pump_capacity"]["status"], "not_verified")
        roles = {row["key"]: row for row in report["parameter_roles"]}
        self.assertEqual(roles["cooling_dp_pa"]["role"], "inactive_with_hydraulic_model")
        self.assertEqual((network, states, inputs, cooling), before)
        cooling["profile"]["summary"]["dp_total_Pa"] -= 1000
        self.assertEqual(pressure_contract(network, states, inputs, cooling)["pressure_link_status"], "fail")

    def test_unknown_or_incomplete_cooling_profile_never_falls_back_to_budget(self):
        network, states, inputs = self.fixture()
        for profile in ({"version": 2, "model_id": "regen_quasi1d_ph_v1"},
                        {"version": 1, "model_id": "regen_quasi1d_ph_v1", "summary": {}}):
            with self.subTest(profile=profile):
                cooling = dict(profile=profile, outputs=dict(delta_P_source=0, delta_P_MPa=2))
                report = pressure_contract(network, states, inputs, cooling)
                self.assertEqual(report["pressure_link_status"], "unavailable")
                self.assertIsNone(report["pump_capacity"]["pumps"][1]["required_outlet_pressure_Pa"])

    def test_recorded_budget_component_does_not_become_hydraulic_from_legacy_flag(self):
        network, states, inputs = self.fixture()
        inputs["cooling_dp_auto"] = 1
        cooling = dict(outputs=dict(mode=0, delta_P_source=0, delta_P_MPa=2))
        report = pressure_contract(network, states, inputs, cooling)
        self.assertEqual(report["pressure_link_status"], "pass")
        self.assertEqual(report["pump_capacity"]["pumps"][1]["cooling_drop_source"], "specified_design_budget")
        self.assertEqual(report["parameter_roles"][-1]["role"], "specified_design_budget")

    def test_frozen_loss_mode_does_not_relabel_input_ratios_as_active(self):
        args = self.fixture(fixed=True)
        report = pressure_contract(*args)
        roles = {row["key"]: row for row in report["parameter_roles"]}
        self.assertEqual(roles["preburner_injector_dp_ratio"]["role"], "inactive_with_frozen_resistance")
        self.assertEqual(roles["valve_line_dp_ratio"]["role"], "inactive_with_frozen_resistance")
        self.assertEqual(roles["ox_turbine_exhaust_dp_ratio"]["role"], "specified_design_budget")
        branch = args[0]["branches"][0]
        self.assertAlmostEqual(branch["injector_drop_Pa"], .25e6)
        self.assertNotEqual(branch["injector_drop_Pa"], args[2]["preburner_injector_dp_ratio"] * branch["destination_pressure_Pa"])
        self.assertEqual(report["legacy_summary_fields"]["status"], "design_budget_only")

    def test_missing_state_is_unavailable_never_inferred_from_input_or_zero(self):
        network, states, inputs = self.fixture()
        del states["ox_turbine_exhaust"]
        report = pressure_contract(network, states, inputs)
        self.assertEqual(report["pressure_link_status"], "unavailable")
        self.assertIsNone(report["gas_paths"][0]["turbine_pressure_ratio"])
        self.assertIn("states.ox_turbine_exhaust.pressure", report["missing_evidence"])

    def test_network_closure_cannot_hide_inconsistent_saved_branch_state(self):
        network, states, inputs = self.fixture()
        states["branch_fuel_to_opb"]["pressure"] -= .5e6
        self.assertEqual(audit(network)["status"], "pass")
        report = pressure_contract(network, states, inputs)
        self.assertEqual(report["pressure_link_status"], "fail")
        self.assertAlmostEqual(report["branch_state_links"][0]["branch_outlet_state_residual_Pa"], -.5e6)

    def test_pressure_node_accounting_recomputed_from_losses_not_cached_nodes(self):
        result = solve()
        row = result["branches"][1]
        self.assertEqual(row["pressure_nodes"][1]["kind"], "derived_pressure_accounting")
        self.assertAlmostEqual(row["pressure_nodes"][1]["pressure_Pa"], 8.8e6)
        row["pressure_nodes"][1]["pressure_Pa"] = -99
        checked = audit(result)
        self.assertAlmostEqual(checked["branches"][1]["pressure_nodes"][1]["pressure_Pa"], 8.8e6)


if __name__ == "__main__":
    unittest.main()
