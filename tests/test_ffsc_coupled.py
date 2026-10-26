"""Expanded-equation integration against an independently soluble mock cycle."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import ComponentResult, CycleResults, EngineInputs, FlowState
from ffsc_coupled import MUTATED_INPUTS, _initial_vector, _source_checks, coupled_residuals, solve_coupled
from steady_network import BRANCH_IDS, audit_network, solve_design
from task_control import TaskCancelled


class AnalyticCycle:
    """Known root: m=400, pi=(2,2.2), MR=(30,.3), T=1000,gamma=1.2,M=20."""
    def __init__(self):
        self.inputs = EngineInputs(thrust=4000, pc=1e6, of_ratio=3,
                                   ox_preburner_mr_target=30, fuel_preburner_mr_target=.3,
                                   chamber_temp_guess=1000, chamber_gamma=1.2, molar_mass=20,
                                   use_target_preburner_temp=1)
        self.calls = 0

    def _evaluate_once(self, total, pi_ox, pi_fuel):
        self.calls += 1
        inputs = self.inputs
        trial = getattr(self, "_network_trial", None)
        fuel = total / 4
        ox = total - fuel
        network = solve_design(total_fuel=fuel, total_ox=ox,
            mr_ox=inputs.ox_preburner_mr_target, mr_fuel=inputs.fuel_preburner_mr_target,
            p_pre_ox=pi_ox * inputs.pc, p_pre_fuel=pi_fuel * inputs.pc,
            injector_ratio=.1, line_dp=2e5, densities={"fuel": 300, "ox": 1200},
            flows_override=trial["flows"] if trial else None, allow_unclosed_flows=bool(trial))
        if trial:
            network["tap_pressures_Pa"] = dict(trial["tap_pressures_Pa"])
            for row in network["branches"]:
                row["source_pressure_Pa"] = trial["tap_pressures_Pa"][row["propellant"]]
                row["valve_drop_Pa"] = trial["valve_drops_Pa"][row["id"]]
        rows = {row["id"]: row for row in network["branches"]}
        flow = lambda key: rows[key]["mass_flow_kg_s"]
        mr_o = flow("ox_to_opb") / flow("fuel_to_opb")
        mr_f = flow("ox_to_fpb") / flow("fuel_to_fpb")
        summary = dict(mdot_total_kg_s=total, mdot_fuel_kg_s=fuel, mdot_ox_kg_s=ox,
            ox_turbine_pi_t=pi_ox, fuel_turbine_pi_t=pi_fuel,
            ox_preburner_mr=mr_o, fuel_preburner_mr=mr_f, thrust_N=10 * total,
            chamber_temp_guess_used=inputs.chamber_temp_guess,
            ox_preburner_T_combustion_K=inputs.ox_preburner_temp_target + 10 * (mr_o - 30),
            fuel_preburner_T_combustion_K=inputs.fuel_preburner_temp_target + 100 * (mr_f - .3))
        chamber = FlowState("main_chamber", 1e6,
            1000 + .1 * (inputs.chamber_temp_guess - 1000) + .001 * (total - 400), total,
            metadata=dict(gamma=1.2, molar_mass=20))
        components = {"ox_pump": ComponentResult("ox_pump", {"power_W": 5e6}),
            "fuel_pump": ComponentResult("fuel_pump", {"power_W": 3e6}),
            "ox_preburner_turbine": ComponentResult("ox_preburner_turbine", {"turbine_power_W": 5e6 + 1e6 * (pi_ox - 2)}),
            "fuel_preburner_turbine": ComponentResult("fuel_preburner_turbine", {"turbine_power_W": 3e6 + 1e6 * (pi_fuel - 2.2)})}
        return CycleResults(inputs, summary, {"main_chamber": chamber}, components, [],
                            steady_closure={"feed_network": network})


def source(model):
    reference = model._evaluate_once(400, 2, 2.2)
    checks = {"numeric": {"status": "pass"}, "energy": {"status": "pass"},
              "feed": audit_network(reference.steady_closure["feed_network"],
                  total_fuel=100, total_ox=300, mr_ox=30, mr_fuel=.3)}
    model.calls = 0
    return reference, checks


class CoupledTests(unittest.TestCase):
    def test_perturbed_18_variable_problem_reaches_known_root(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        before = asdict(model.inputs)
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)), \
             patch("ffsc_coupled.audit_energy", return_value={"status": "pass"}):
            report = solve_coupled(model, reference, perturbation=.02, max_nfev=400)
        self.assertEqual(report["status"], "pass", report.get("error"))
        self.assertEqual(len(report["residuals"]), 18)
        self.assertTrue(all(abs(row["residual"]) <= row["tolerance"] for row in report["residuals"]))
        self.assertAlmostEqual(report["variables"]["total_flow_kg_s"], 400, places=3)
        self.assertAlmostEqual(report["variables"]["ox_preburner_mr"], 30, places=3)
        self.assertAlmostEqual(report["variables"]["fuel_preburner_mr"], .3, places=4)
        self.assertEqual(asdict(model.inputs), before)
        self.assertFalse(hasattr(model, "_network_trial"))
        self.assertEqual(model.calls, report["nfev"])
        for field, variable in zip(MUTATED_INPUTS, list(report["variables"])[3:8]):
            self.assertEqual(report["calculation"]["inputs"][field], report["variables"][variable])

    def test_insufficient_budget_cannot_pass_and_restores_existing_trial(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        before = asdict(model.inputs)
        previous = {"sentinel": 1}
        model._network_trial = previous
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)), \
             patch("ffsc_coupled.audit_energy", return_value={"status": "pass"}):
            report = solve_coupled(model, reference, max_nfev=1)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["nfev"], 1)
        self.assertEqual(asdict(model.inputs), before)
        self.assertIs(model._network_trial, previous)

    def test_known_failed_reference_is_blocked_without_evaluating_cycle(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        checks["feed"]["status"] = "fail"
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)):
            report = solve_coupled(model, reference)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(model.calls, 0)

    def test_missing_real_source_evidence_is_not_inferred_from_low_residual(self):
        model = AnalyticCycle()
        reference, _ = source(model)
        reference.summary["solver_residual_norm"] = 0
        report = solve_coupled(model, asdict(reference))
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(model.calls, 0)

    def test_root_equations_do_not_override_failed_independent_energy_audit(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)), \
             patch("ffsc_coupled.audit_energy", return_value={"status": "fail"}):
            report = solve_coupled(model, reference, perturbation=0, max_nfev=1)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["termination_reason"], "independent_closure_failed")

    def test_cancellation_propagates_and_restores_feedback_state(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        before = asdict(model.inputs)
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)), \
             patch.object(model, "_evaluate_once", side_effect=TaskCancelled("cancelled")):
            with self.assertRaises(TaskCancelled):
                solve_coupled(model, reference)
        self.assertEqual(asdict(model.inputs), before)
        self.assertFalse(hasattr(model, "_network_trial"))

    def test_noop_expanded_trial_hook_is_detected(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        original = model._evaluate_once
        def ignores_trial(*args):
            del model._network_trial
            return original(*args)
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)), \
             patch.object(model, "_evaluate_once", side_effect=ignores_trial):
            report = solve_coupled(model, reference, max_nfev=1)
        self.assertEqual(report["status"], "fail")
        self.assertIn("did not honor", report["error"])

    def test_changed_operating_point_is_not_a_same_equations_comparison(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        source_dict = asdict(reference.inputs)
        model.inputs.thrust += 10
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, source_dict)):
            with self.assertRaises(ValueError):
                solve_coupled(model, reference)

    def test_recorded_inner_tolerances_are_reused_without_relaxation(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        checks["numeric"]["layers"] = {"inner": {"tolerance": 1e-6, "checks": [
            {"name": "thrust", "tolerance": .004}, {"name": "ox_power", "tolerance": 5},
            {"name": "fuel_power", "tolerance": 3}]}}
        with patch("ffsc_coupled._source_checks", return_value=(reference, checks, reference.inputs)), \
             patch("ffsc_coupled.audit_energy", return_value={"status": "pass"}):
            report = solve_coupled(model, reference, perturbation=0, max_nfev=1)
        self.assertEqual([row["tolerance"] for row in report["residuals"][:3]], [.004, 5, 3])

    def test_warm_start_preserves_actual_feedback_inputs_not_output_state(self):
        model = AnalyticCycle()
        reference, _ = source(model)
        reference.convergence["solution"] = dict(chamber_temp_guess=998,
                                                  chamber_gamma=1.20001, molar_mass=20.0001)
        self.assertEqual(_initial_vector(reference)[5:8], [998, 1.20001, 20.0001])

    def test_source_physical_equations_detect_stale_convergence_pass_flags(self):
        model = AnalyticCycle()
        reference, checks = source(model)
        reference.summary.update(ox_preburner_mr_target=30, fuel_preburner_mr_target=.3)
        reference.states["ox_preburner_outlet"] = FlowState("ox_pb", 2e6, 800, 100)
        reference.states["fuel_preburner_outlet"] = FlowState("fuel_pb", 2.2e6, 850, 300)
        reference.components["nozzle"] = ComponentResult("nozzle", {"thrust_N": 4000})
        reference.convergence["solution"] = dict(chamber_temp_guess=1000, chamber_gamma=1.2, molar_mass=20)
        layers = dict(inner={"checks": [{"name": "thrust", "tolerance": .004},
            {"name": "ox_power", "tolerance": 5}, {"name": "fuel_power", "tolerance": 3}]},
            preburner={"tolerance": 3}, thermal={"enabled": True, "checks": [
                {"name": "temperature", "tolerance": 5}, {"name": "gamma", "tolerance": 1e-4},
                {"name": "molar_mass", "tolerance": 1e-4}]})
        with patch("ffsc_coupled.numerical_convergence", return_value={"status": "pass", "layers": layers}), \
             patch("ffsc_coupled.audit_energy", return_value={"status": "pass"}):
            _, verified, _ = _source_checks(reference)
        self.assertEqual(verified["numeric"]["status"], "pass")
        self.assertEqual(verified["equations"]["status"], "fail")
        self.assertEqual(next(row for row in verified["equations"]["checks"]
            if row["name"] == "ox_preburner_temperature")["residual"], 50)


if __name__ == "__main__":
    unittest.main()
