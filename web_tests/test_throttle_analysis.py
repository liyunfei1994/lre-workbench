"""Steady scans retain failures, explicit geometry closure and source provenance."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from webapp.solver import EngineInputs
from webapp.throttle import calculate_throttle, normalize_request, source_baseline
from webapp.validation import fingerprint
from core import ComponentResult, CycleResults, FlowState
from steady_closure import audit_energy
from steady_network import solve_design
from tests.test_steady_closure import balanced_loop
from task_control import CancellationToken, TaskCancelled, cancellation_scope
from web_tests.convergence_fixture import converged_evidence


def closure_fixture():
    states, components = balanced_loop()
    network = solve_design(total_fuel=2, total_ox=4, mr_ox=6, mr_fuel=2/3,
                           p_pre_ox=64e6, p_pre_fuel=60e6, injector_ratio=.04, line_dp=1e6,
                           densities={"fuel": 400, "ox": 1200}, temperatures={"fuel": 380, "ox": 80})
    return states, components, {"schema_version": 1, "feed_network": network,
                                "energy_balance": audit_energy(states, components)}


def source_fixture():
    # This analytic ledger uses specified heat/pressure, not a spatial solution.
    inputs = asdict(EngineInputs(cooling_channel_auto_size=0.0, cooling_bartz_mode=0.0, of_ratio=2))
    states, components, closure = closure_fixture()
    components["nozzle"] = {"outputs": {"At_m2": .05}}
    return {"id": "design", "project_id": "project", "revision": 3, "created": 1,
            "inputs": deepcopy(inputs), "result": {
                "calculation": {"inputs": inputs, "summary": {"solver_residual_norm": 1e-5,
                                    "mdot_fuel_kg_s": 2, "mdot_ox_kg_s": 4, "ox_preburner_mr": 6, "fuel_preburner_mr": 2/3,
                                    "ox_preburner_mr_target": 6, "fuel_preburner_mr_target": 2/3},
                                "convergence": converged_evidence(), "components": components,
                                "states": states, "steady_closure": closure},
                "environment": {"synthetic": True}}}


def solved_fixture(inputs, area=.05):
    states, components, closure = closure_fixture()
    states = {key: FlowState(pressure=1e6, temperature=300, **value) for key, value in states.items()}
    components = {key: ComponentResult(**value) for key, value in components.items()}
    components["nozzle"] = ComponentResult("nozzle", {"At_m2": area})
    return CycleResults(inputs=EngineInputs(**deepcopy(inputs)),
                        summary={"solver_residual_norm": 1e-5, "thrust_N": 1e6}, states=states,
                        components=components, warnings=[], convergence=converged_evidence(), steady_closure=closure)


class ThrottleAnalysisTests(unittest.TestCase):
    def test_normalization_and_fail_closed_source_baseline(self):
        source = source_fixture()
        normalized = normalize_request({"source_job_id": "design"}, source)
        self.assertEqual(normalized, {"source_job_id": "design", "throttle_min": .4, "throttle_steps": 5})
        for changes in ({"throttle_min": True}, {"throttle_min": 0.049}, {"throttle_min": 1},
                        {"throttle_min": float("nan")}, {"throttle_steps": 2.5}, {"throttle_steps": 16},
                        {"throttle_steps": True}, {"ambient_pressure": 0}, {"throttle_steps": 10**1000}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_request({"source_job_id": "design", **changes}, source)
        for field, bad in (("At_m2", None), ("At_m2", 0), ("At_m2", float("inf"))):
            broken = deepcopy(source)
            broken["result"]["calculation"]["components"]["nozzle"]["outputs"][field] = bad
            with self.assertRaisesRegex(ValueError, "喉部"):
                source_baseline(broken)
        broken = deepcopy(source)
        broken["result"]["calculation"]["summary"]["solver_residual_norm"] = .1
        with self.assertRaisesRegex(ValueError, "收敛"):
            source_baseline(broken)
        broken = deepcopy(source)
        broken["result"]["calculation"]["inputs"]["cooling_bartz_mode"] = 1
        broken["result"]["calculation"]["inputs"]["cooling_channel_auto_size"] = 1
        with self.assertRaisesRegex(ValueError, "冷却通道"):
            source_baseline(broken)

    def test_actual_solved_snapshot_controls_baseline_not_submitted_input(self):
        source = source_fixture()
        source["inputs"]["pc"] = 999e6
        source["inputs"]["thrust"] = 99e6
        before = deepcopy(source)
        values, baseline = source_baseline(source)
        self.assertEqual(baseline["pc_Pa"], source["result"]["calculation"]["inputs"]["pc"])
        self.assertNotEqual(baseline["pc_Pa"], source["inputs"]["pc"])
        values["pc"] = 1
        self.assertEqual(before, source)
        self.assertEqual(baseline["feed_hardware"], source["result"]["calculation"]["steady_closure"]["feed_network"]["hardware_snapshot"])
        baseline["feed_hardware"]["branches"]["fuel_to_opb"]["K_line"] *= 2
        self.assertEqual(before, source)

    def test_ffsc_baseline_reaudits_network_and_energy_instead_of_saved_pass(self):
        for mutation, message in (
            (lambda c: c.pop("steady_closure"), "重新计算"),
            (lambda c: c["steady_closure"]["feed_network"]["branches"][0].update(valve_drop_Pa=-1e6), "供给网络"),
            (lambda c: c["states"]["fuel_after_cooling"].update(enthalpy=-300), "能量守恒"),
            (lambda c: c["summary"].update(mdot_fuel_kg_s=2.5), "供给网络"),
            (lambda c: c["summary"].update(ox_preburner_mr_target=None), "混合比"),
            (lambda c: c["summary"].update(ox_preburner_mr_target=6.1, ox_preburner_mr=6.1), "供给网络"),
        ):
            with self.subTest(message=message):
                source = source_fixture()
                mutation(source["result"]["calculation"])
                with self.assertRaisesRegex(ValueError, message):
                    source_baseline(source)

    def test_free_split_baseline_has_no_mixture_ratio_target_constraint(self):
        source = source_fixture()
        calculation = source["result"]["calculation"]
        calculation["inputs"].update(use_target_preburner_temp=0, use_target_preburner_mr=0)
        for key in ("ox_preburner_mr_target", "fuel_preburner_mr_target", "ox_preburner_mr", "fuel_preburner_mr"):
            calculation["summary"].pop(key)
        _, baseline = source_baseline(source)
        self.assertEqual(len(baseline["feed_hardware"]["branches"]), 4)
        self.assertIsNone(baseline["preburner_mr_targets"])

    def test_baseline_preserves_complete_valid_warm_state_and_solved_mr_targets(self):
        source = source_fixture()
        solution = {"x": [6, 1.8, 2], "chamber_temp_guess": 3000, "chamber_gamma": 1.2, "molar_mass": 20}
        source["result"]["calculation"]["convergence"]["solution"] = deepcopy(solution)
        source["result"]["calculation"]["summary"].update(thrust_N=999999,chamber_pressure_Pa=12345678)
        _, baseline = source_baseline(source)
        self.assertEqual(baseline["warm_state"], solution)
        self.assertEqual(baseline["preburner_mr_targets"], {"ox": 6, "fuel": 2/3})
        self.assertEqual(baseline["warm_state_thrust_N"], 999999)
        self.assertEqual(baseline["warm_state_pc_Pa"], 12345678)
        self.assertEqual(baseline["thrust_N"], source["result"]["calculation"]["inputs"]["thrust"])
        baseline["warm_state"]["x"][0] = 99
        self.assertEqual(source["result"]["calculation"]["convergence"]["solution"], solution)
        for invalid in (None, float("nan"), 0, True, "3000"):
            source["result"]["calculation"]["convergence"]["solution"]["chamber_temp_guess"] = invalid
            self.assertIsNone(source_baseline(source)[1]["warm_state"])
        for changes in ({"x": [6,1.8]}, {"x": [6,1.8,2,3]}, {"chamber_gamma": 1}, {"chamber_gamma": .9}):
            source["result"]["calculation"]["convergence"]["solution"] = solution | changes
            self.assertIsNone(source_baseline(source)[1]["warm_state"])

    def test_ffsc_snapshot_is_required_but_gg_keeps_its_geometry_only_contract(self):
        source = source_fixture()
        source["result"]["calculation"]["steady_closure"]["feed_network"].pop("hardware_snapshot")
        with self.assertRaisesRegex(ValueError, "阻力快照"):
            source_baseline(source)
        source["result"]["calculation"]["inputs"]["cycle_type"] = "gg"
        source["result"]["calculation"].pop("steady_closure")
        _, baseline = source_baseline(source)
        self.assertNotIn("feed_hardware", baseline)

    def test_failures_not_lost_geometry_separate_from_system_constraints(self):
        source = source_fixture()
        before = deepcopy(source)
        inputs = source["result"]["calculation"]["inputs"]
        assessments = [{"status": "pass", "numerical": {"status": "pass"}},
                       {"status": "fail", "numerical": {"status": "pass"}},
                       {"status": "pass", "numerical": {"status": "pass"}}]
        def sweep(**kwargs):
            self.assertEqual(kwargs["design_baseline"]["throat_area_m2"], .05)
            self.assertEqual(kwargs["design_baseline"]["feed_hardware"]["schema_version"], 1)
            self.assertFalse(kwargs["include_cooling_marching"])
            for i, area in enumerate((.05, .05, .06, None)):
                kwargs["progress_callback"](i+1, 4, "point")
                kwargs["record_callback"]({"throttle_ratio": .4 + i*.2, "thrust_target_N": 1e6,
                                            "pc_Pa": 10e6, "converged": True,
                                            "cycle_evaluations": 12 if area else None,
                                            "cycle_evaluation_budget": 240,
                                            "error": "solver failed" if area is None else "",
                                            "results": solved_fixture(inputs, area) if area else None})
        published = []
        with patch("gg_cycle.make_cycle_model", return_value=SimpleNamespace(throttle_sweep=sweep)), \
             patch("webapp.throttle.science_environment", return_value={"test": True}), \
             patch("engineering_assessment.assess_engineering", side_effect=assessments) as assess:
            output = calculate_throttle({"source_job_id": "design", "throttle_steps": 4}, source,
                                        lambda progress, result: published.append((progress, result)))
        self.assertEqual(before, source)
        self.assertEqual([p["status"] for p in output["points"]], ["pass", "review", "failed", "failed"])
        self.assertEqual(output["counts"], {"total": 4, "completed": 4, "converged": 2,
                                           "passed": 1, "review": 1, "failed": 2})
        self.assertEqual(output["points"][2]["assessment"]["numerical"]["status"], "pass")
        self.assertEqual(output["points"][2]["geometry"]["status"], "fail")
        self.assertIsNone(output["points"][3]["pc_Pa"])
        self.assertEqual(output["points"][3]["summary"], {})
        self.assertEqual(output["points"][0]["inputs"], inputs)
        self.assertEqual(output["points"][0]["steady_closure"]["energy_balance"]["status"], "pass")
        self.assertEqual(output["points"][3]["steady_closure"], {})
        self.assertEqual(output["points"][0]["cycle_evaluations"], 12)
        self.assertEqual(output["points"][0]["cycle_evaluation_budget"], 240)
        self.assertIsNone(output["points"][3]["cycle_evaluations"])
        self.assertTrue(any("固定阻力" in line for line in output["limitations"]))
        self.assertEqual(output["source"]["result_sha256"], fingerprint(source["result"]))
        self.assertTrue(all(call.kwargs == {"scope": "system"} for call in assess.call_args_list))
        self.assertEqual(len(published[0][1]["points"]), 0)
        self.assertTrue(output["complete"])
        self.assertEqual(output["response_gains"]["status"], "unavailable")
        self.assertIn("100%", output["response_gains"]["reason"])
        self.assertTrue(all("response_gains" in row[1] for row in published))
        json.dumps(output, allow_nan=False)

    def test_cancellation_keeps_completed_points_as_incomplete_progress(self):
        source = source_fixture()
        published = []
        def sweep(**kwargs):
            kwargs["record_callback"]({"throttle_ratio": .4, "thrust_target_N": 1e6,
                                        "pc_Pa": None, "converged": False, "results": None, "error": "unavailable"})
            raise TaskCancelled()
        with patch("gg_cycle.make_cycle_model", return_value=SimpleNamespace(throttle_sweep=sweep)), \
             patch("webapp.throttle.science_environment", return_value={}), self.assertRaises(TaskCancelled):
            calculate_throttle({"source_job_id": "design"}, source,
                               lambda progress, result: published.append((progress, result)))
        self.assertEqual(published[-1][0]["completed"], 1)
        self.assertFalse(published[-1][1]["complete"])
        self.assertEqual(published[-1][1]["points"][0]["error"], "unavailable")


class NativeThrottleBaselineTests(unittest.TestCase):
    def test_legacy_default_signature_keeps_completed_record_on_cancellation(self):
        from cycle import FFSCCycleModel
        from gg_cycle import GGCycleModel
        for model in (FFSCCycleModel, GGCycleModel):
            with self.subTest(model=model.__name__):
                inputs = EngineInputs(cooling_channel_auto_size=0., cooling_marching_enable=0.)
                token, records = CancellationToken(), []
                def completed(record):
                    records.append(record)
                    token.cancel()
                with patch.object(model, "evaluate", return_value=solved_fixture(asdict(inputs))), \
                     cancellation_scope(token), self.assertRaises(TaskCancelled):
                    model(inputs).throttle_sweep(record_callback=completed)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["error"], "")

    def test_both_cycles_use_supplied_geometry_without_nominal_recalculation(self):
        from cycle import FFSCCycleModel
        from gg_cycle import GGCycleModel
        for model, cycle_type in ((FFSCCycleModel, "ffsc"), (GGCycleModel, "gg")):
            with self.subTest(cycle=cycle_type):
                inputs = EngineInputs(cycle_type=cycle_type, throttle_steps=2,
                                      cooling_channel_auto_size=1., cooling_marching_enable=1.)
                area = .05
                seen = []
                solution = {"x": [6,1.8,2], "chamber_temp_guess": 3000, "chamber_gamma": 1.2, "molar_mass": 20}
                def evaluate(instance, **kwargs):
                    seen.append(deepcopy(instance.inputs))
                    ratio = instance.inputs.thrust / inputs.thrust
                    # A nonlinear pressure relation distinguishes a solved throat
                    # constraint from simply scaling nominal chamber pressure.
                    current_area = area * inputs.pc * ratio**.9 / instance.inputs.pc
                    result = solved_fixture(asdict(instance.inputs), current_area)
                    result.convergence["solution"] = deepcopy(solution)
                    result.summary.update(ox_preburner_mr_target=6,fuel_preburner_mr_target=2/3,
                                          thrust_N=instance.inputs.thrust,chamber_pressure_Pa=instance.inputs.pc)
                    return result
                with patch.object(model, "evaluate", evaluate), \
                     patch("cooling_marching.run_marching_postpass") as marching:
                    rows = model(inputs).throttle_sweep(
                        design_baseline={"throat_area_m2": area, "cooling_height_m": .003,
                                         "warm_state": solution, "preburner_mr_targets": {"ox":6,"fuel":2/3},
                                         "thrust_N":inputs.thrust,"pc_Pa":inputs.pc,
                                         "feed_hardware": closure_fixture()[2]["feed_network"]["hardware_snapshot"]},
                        include_cooling_marching=False)
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(seen), 3)  # no extra nominal solve
                self.assertFalse(marching.called)
                self.assertNotAlmostEqual(rows[0]["pc_Pa"], inputs.pc*.4)
                self.assertAlmostEqual(rows[0]["results"].components["nozzle"].outputs["At_m2"], area)
                self.assertTrue(all(x.cooling_channel_height_m == .003 and x.cooling_channel_auto_size == 0 for x in seen))
                self.assertTrue(all(x.cooling_marching_enable == 0 for x in seen))
                with patch.object(model, "evaluate") as unused, self.assertRaises(ValueError):
                    model(inputs).throttle_sweep(design_baseline={"throat_area_m2": float("nan")})
                unused.assert_not_called()


if __name__ == "__main__":
    unittest.main()
