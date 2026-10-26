"""Full-state reuse, input restoration and bounded fixed-resistance scans."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import ComponentResult, CycleResults, EngineInputs
from cycle import FFSCCycleModel
from steady_network import solve_design
from task_control import TaskCancelled
from tests.test_ffsc_convergence import inner_result


class FullStateThrottleTests(unittest.TestCase):
    def setUp(self):
        self.model = FFSCCycleModel(EngineInputs(throttle_steps=2, throttle_min=.4,
                                                cooling_marching_enable=0))
        self.warm = dict(x=[100., 2., 3.], chamber_temp_guess=3740., chamber_gamma=1.19, molar_mass=22.)
        hardware = solve_design(total_fuel=100, total_ox=360, mr_ox=60, mr_fuel=.15,
            p_pre_ox=60e6, p_pre_fuel=55e6, injector_ratio=.14, line_dp=1e6,
            densities=dict(fuel=300, ox=1200))["hardware_snapshot"]
        self.baseline = dict(thrust_N=self.model.inputs.thrust, pc_Pa=self.model.inputs.pc,
            throat_area_m2=1., cooling_height_m=.005, feed_hardware=hardware,
            warm_state=deepcopy(self.warm), preburner_mr_targets=dict(ox=60., fuel=.15))

    def test_external_preburner_seed_reaches_core_and_original_inputs_restore(self):
        before = asdict(self.model.inputs)
        observed = []
        def core(callback, *, warm_state):
            observed.append((deepcopy(warm_state), self.model.inputs.ox_preburner_mr_target,
                             self.model.inputs.fuel_preburner_mr_target))
            _, result = inner_result(self.model)
            result.summary.update(ox_preburner_T_combustion_K=self.model.inputs.ox_preburner_temp_target,
                fuel_preburner_T_combustion_K=self.model.inputs.fuel_preburner_temp_target)
            result.convergence["solution"] = deepcopy(warm_state)
            return result
        with patch.object(self.model, "_evaluate_design_core", side_effect=core):
            self.model._evaluate_temp_targeted(warm_state=self.warm,
                                              preburner_mr_targets=dict(ox=61, fuel=.16))
        self.assertEqual(observed, [(self.warm, 61, .16)])
        self.assertEqual(asdict(self.model.inputs), before)
        self.assertEqual(self.warm["x"], [100, 2, 3])

    def test_invalid_preburner_seed_does_not_mutate_input_snapshot(self):
        before = asdict(self.model.inputs)
        with self.assertRaises(ValueError):
            self.model._evaluate_temp_targeted(warm_state=self.warm,
                                              preburner_mr_targets=dict(ox=2, fuel=.16))
        self.assertEqual(asdict(self.model.inputs), before)

    def test_same_point_reuses_full_state_and_large_jump_reuses_closer_rated_state(self):
        calls = []
        before = asdict(self.model.inputs)
        baseline_before = deepcopy(self.baseline)
        def evaluate(model, *, include_cooling_marching, warm_state, preburner_mr_targets):
            calls.append((model.inputs.pc, deepcopy(warm_state), deepcopy(preburner_mr_targets)))
            index = len(calls)
            solved_state = dict(x=[44., 2.2, 3.2], chamber_temp_guess=3741., chamber_gamma=1.195, molar_mass=22.1)
            return CycleResults(model.inputs, dict(solver_residual_norm=0, ox_preburner_mr_target=61,
                fuel_preburner_mr_target=.16), {},
                {"nozzle": ComponentResult("nozzle", {"At_m2": 1.1 if index == 1 else 1.})}, [],
                convergence={"solution": solved_state},
                steady_closure={"feed_network": {"status": "pass"}, "energy_balance": {"status": "pass"}})
        with patch.object(FFSCCycleModel, "evaluate", new=evaluate), \
             patch("convergence.numerical_convergence", return_value={"status": "pass"}):
            rows = self.model.throttle_sweep(design_baseline=self.baseline, include_cooling_marching=False)
        self.assertEqual([row["throttle_ratio"] for row in rows], [.4, 1.])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1]["x"], [40, 2, 3])
        self.assertEqual(calls[1][1]["x"], [44, 2.2, 3.2])
        self.assertEqual(calls[2][1]["x"], [100, 2, 3])
        self.assertEqual(calls[1][2], dict(ox=61, fuel=.16))
        self.assertEqual(calls[2][1]["chamber_temp_guess"], 3740)
        self.assertEqual(self.baseline, baseline_before)
        self.assertEqual(asdict(self.model.inputs), before)

    def test_nearby_successful_point_is_preferred_over_more_distant_rated_source(self):
        self.model.inputs.throttle_steps = 5
        calls = []
        def evaluate(model, *, warm_state, **kwargs):
            calls.append(deepcopy(warm_state))
            ratio = model.inputs.thrust / self.model.inputs.thrust
            state = dict(x=[100 * ratio, 2.2, 3.2], chamber_temp_guess=3700 + ratio,
                         chamber_gamma=1.195, molar_mass=22.1)
            return CycleResults(model.inputs, dict(solver_residual_norm=0, thrust_N=model.inputs.thrust,
                chamber_pressure_Pa=model.inputs.pc, ox_preburner_mr_target=61,
                fuel_preburner_mr_target=.16), {}, {"nozzle": ComponentResult("nozzle", {"At_m2": 1.})}, [],
                convergence={"solution": state}, steady_closure={"feed_network": {"status": "pass"},
                                                                  "energy_balance": {"status": "pass"}})
        with patch.object(FFSCCycleModel, "evaluate", new=evaluate), \
             patch("convergence.numerical_convergence", return_value={"status": "pass"}):
            rows = self.model.throttle_sweep(design_baseline=self.baseline, include_cooling_marching=False)
        self.assertEqual([row["throttle_ratio"] for row in rows], [.4, .55, .7, .85, 1.])
        self.assertAlmostEqual(calls[1]["x"][0], 55)
        self.assertEqual(calls[1]["chamber_temp_guess"], 3700.4)
        self.assertEqual(calls[-1], self.warm)

    def test_budget_escapes_retries_and_is_reset_for_next_requested_point(self):
        def evaluate(model, **kwargs):
            for _ in range(5):
                model._evaluate_once(1, 2, 3)
            raise AssertionError("budget did not terminate cycle evaluations")
        with patch.object(FFSCCycleModel, "evaluate", new=evaluate), \
             patch.object(FFSCCycleModel, "_evaluate_once", return_value=None) as forward:
            rows = self.model.throttle_sweep(design_baseline=self.baseline,
                                             include_cooling_marching=False, max_point_evaluations=2)
        self.assertEqual(forward.call_count, 4)
        self.assertEqual([row["cycle_evaluations"] for row in rows], [2, 2])
        self.assertTrue(all(not row["converged"] and row["results"] is None for row in rows))
        self.assertTrue(all("预算" in row["error"] for row in rows))

    def test_cancellation_is_not_converted_to_a_failed_throttle_point(self):
        with patch.object(FFSCCycleModel, "evaluate", side_effect=TaskCancelled("stop")):
            with self.assertRaises(TaskCancelled):
                self.model.throttle_sweep(design_baseline=self.baseline, include_cooling_marching=False)


if __name__ == "__main__":
    unittest.main()
