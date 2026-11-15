"""EOS validation keeps its source immutable and only compares closed runs."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.test_steady_closure import balanced_loop
from tests import test_steady_network as network_fixture
from web_tests.convergence_fixture import converged_evidence
from webapp.eos_comparison import GAS_STATIONS, PR_BM_EOS, compare_gas_eos
from webapp.solver import platform_defaults
from webapp.validation import calculate_validation, normalize_request


def _source():
    return {"id": "saved-job", "project_id": "project-a", "revision": 7, "created": 0,
            "inputs": platform_defaults() | {"thrust": 300000, "pc": 6e6}, "result": {}}


def _cycle(values, *, progress_callback=None, wrong_hot_side=False, species_hash="shared-species", energy_error=False):
    eos = values["gas_eos"]
    if progress_callback:
        progress_callback(17, 100, .031 if eos == "ideal-gas" else float("inf"))
    states, components = balanced_loop()
    _, pressure_states, pressure_inputs = network_fixture.PressureContractTests().fixture()
    for key, pressure_state in pressure_states.items():
        states[key].update(pressure_state)
    for key, _ in GAS_STATIONS:
        use_ideal = key in ("main_chamber", "nozzle_exit") and (
            wrong_hot_side if eos == PR_BM_EOS else not wrong_hot_side)
        states[key].update(temperature=1000 if eos == "ideal-gas" else 1005,
                           composition={"O2": .9, "H2O": .1} if eos == "ideal-gas" else {"O2": .89, "H2O": .11})
        states[key]["metadata"].update(model={
            "eos": "ideal-gas" if use_ideal else eos, "species_policy": values["gas_species_policy"],
            "species_names": ["O2", "H2O"], "species_count": 2, "species_policy_sha256": species_hash,
            "eos_parameter_sha256": "same-parameters", "eos_model_sha256": "bm-model-v1" if eos == PR_BM_EOS else None,
            "enthalpy_reference_id": "same-reference", "entropy_reference_id": "same-reference",
        }, composition_complete=True, state_basis={"pressure": "reservoir", "temperature": "reservoir",
                                                    "enthalpy": "thermodynamic"})
    if eos == "Peng-Robinson":
        states["main_chamber"]["metadata"].update(
            source_ports=["ox_turbine_exhaust", "fuel_turbine_exhaust"],
            eos_transition={"kind": "conservative_enthalpy_species_handoff", "source_eos": eos,
                            "target_eos": "ideal-gas", "enthalpy_reference_id": "same-reference"})
    if energy_error:
        components["fuel_pump"]["outputs"]["power_W"] *= 2
    components["cooling_channel"]["outputs"].update(delta_P_source=0, delta_P_MPa=2)
    if values.get("hot_side_thermo_method") == "same_eos" and values.get("cooling_bartz_mode"):
        components["nozzle"] = {"outputs": {"heat_loss_W": 600}}
        components["main_chamber"]["outputs"]["heat_loss_W"] = 0
        states["main_chamber"]["enthalpy"] += 100
        components["nozzle"]["profile"] = {"thermo_method": "same_eos", "model_id": (
            "heated_frozen_pr_bm_quasi1d_v1" if eos == PR_BM_EOS else "heated_frozen_ideal_same_eos_quasi1d_v1")}
    elif values.get("hot_side_thermo_method") == "same_eos":
        components["nozzle"] = {"outputs": {}, "profile": {
            "model_id": "adiabatic_frozen_same_eos_v1", "gas_model": {"eos": eos}}}
    summary = {"solver_residual_norm": 1e-5, "solve_mode": 2, "mdot_total_kg_s": 6,
               "mdot_fuel_kg_s": 2, "mdot_ox_kg_s": 4, "ox_preburner_mr_target": 6, "fuel_preburner_mr_target": 2/3,
               "Isp_s": 311 if eos == "ideal-gas" else 310}
    return {"inputs": values | pressure_inputs, "summary": summary, "states": states, "components": components,
            "convergence": converged_evidence(), "steady_closure": {"feed_network": network_fixture.solve(
                total_fuel=2, total_ox=4, mr_ox=6, mr_fuel=2/3)}}, {}, []


class EOSComparisonTests(unittest.TestCase):
    def test_bm_source_pairs_same_species_ideal_with_full_bm_and_records_model_scope(self):
        source = _source()
        source["inputs"]["gas_eos"] = PR_BM_EOS
        before = deepcopy(source)
        with patch("webapp.eos_comparison._evaluate", side_effect=_cycle) as solve_cycle, \
                patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
            section = compare_gas_eos(source)
        ideal, bm = (call.args[0] for call in solve_cycle.call_args_list)
        self.assertEqual(bm, ideal | {"gas_eos": PR_BM_EOS})
        self.assertEqual(ideal["hot_side_thermo_method"], "same_eos")
        self.assertEqual(section["shared_inputs"]["hot_side_thermo_method"], "same_eos")
        self.assertEqual(section["model_scope"]["hot_side_thermo_method"], "same_eos")
        self.assertEqual(set(section["runs"]), {"ideal-gas", PR_BM_EOS})
        self.assertEqual(section["status"], "complete")
        self.assertEqual(section["source_model"]["scope"], "full_pr_bm")
        self.assertEqual(section["model_scope"]["candidate_eos"], PR_BM_EOS)
        self.assertEqual(section["candidate_eos"], PR_BM_EOS)
        self.assertEqual(section["candidate_selection"], "source_default")
        self.assertEqual(section["model_scope"]["main_chamber_nozzle"], PR_BM_EOS)
        self.assertEqual(section["model_scope"]["cooling_gas_side"], PR_BM_EOS)
        self.assertEqual(section["model_scope"]["eos_model_sha256"], "bm-model-v1")
        self.assertTrue(all(row["actual_eos"] == PR_BM_EOS and row["match"]
                            for row in section["runs"][PR_BM_EOS]["station_models"].values()))
        self.assertEqual(source, before)

    def test_bm_pair_requires_actual_common_heated_method_evidence_for_both_runs(self):
        source = _source()
        source["inputs"].update(gas_eos=PR_BM_EOS, cooling_bartz_mode=1)
        for bad_eos in ("ideal-gas", PR_BM_EOS):
            for failure in ("missing", "wrong_profile", "wrong_method"):
                def evaluate(values):
                    result = _cycle(values)
                    if values["gas_eos"] == bad_eos:
                        profile = result[0]["components"]["nozzle"]["profile"]
                        if failure == "missing":
                            profile.clear()
                        else:
                            profile["model_id" if failure == "wrong_profile" else "thermo_method"] = "legacy"
                    return result
                with self.subTest(eos=bad_eos, failure=failure), \
                        patch("webapp.eos_comparison._evaluate", side_effect=evaluate), \
                        patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
                    report = compare_gas_eos(source)
                self.assertEqual(report["runs"][bad_eos]["status"], "unavailable")
                self.assertEqual(report["runs"][bad_eos]["closure"]["hot_side_method"]["status"], "fail")
                self.assertIn("实际热侧", report["runs"][bad_eos]["reason"])
                self.assertTrue(all(row["difference"] is None for row in report["metrics"]))

    def test_unheated_bm_pair_records_common_method_as_inapplicable(self):
        source = _source()
        source["inputs"].update(gas_eos=PR_BM_EOS, cooling_bartz_mode=0)
        with patch("webapp.eos_comparison._evaluate", side_effect=_cycle), \
                patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
            report = compare_gas_eos(source)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(all(run["closure"]["hot_side_method"]["status"] == "not_applicable"
                            for run in report["runs"].values()))
        self.assertTrue(all(run["closure"]["hot_side_method"]["adiabatic_same_eos_verified"]
                            for run in report["runs"].values()))

        def missing_adiabatic(values):
            calculation, assessment, warnings = _cycle(values)
            if values["gas_eos"] == "ideal-gas":
                calculation["components"]["nozzle"]["profile"] = {}
            return calculation, assessment, warnings

        with patch("webapp.eos_comparison._evaluate", side_effect=missing_adiabatic), \
                patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
            incomplete = compare_gas_eos(source)
        self.assertEqual(incomplete["runs"]["ideal-gas"]["status"], "unavailable")
        self.assertIn("绝热喷管", incomplete["runs"]["ideal-gas"]["reason"])
        self.assertTrue(all(row["difference"] is None for row in incomplete["metrics"]))

    def test_bm_source_rejects_ideal_hot_side_or_missing_bm_identity(self):
        for failure in ("hot_side", "model_hash", "parameters"):
            def evaluate(values):
                result = _cycle(values, wrong_hot_side=failure == "hot_side" and values["gas_eos"] == PR_BM_EOS)
                if failure == "model_hash" and values["gas_eos"] == PR_BM_EOS:
                    result[0]["states"]["main_chamber"]["metadata"]["model"].pop("eos_model_sha256")
                if failure == "parameters" and values["gas_eos"] == PR_BM_EOS:
                    result[0]["states"]["nozzle_exit"]["metadata"]["model"]["eos_parameter_sha256"] = "other-parameters"
                return result
            source = _source()
            source["inputs"]["gas_eos"] = PR_BM_EOS
            with self.subTest(failure=failure), patch("webapp.eos_comparison._evaluate", side_effect=evaluate), \
                    patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
                section = compare_gas_eos(source)
            self.assertEqual(section["status"], "partial")
            self.assertEqual(section["runs"][PR_BM_EOS]["status"], "unavailable")
            self.assertTrue(all(row["difference"] is None for row in section["metrics"]))

    def test_request_takes_only_flag_and_saved_source(self):
        source = _source()
        self.assertEqual(normalize_request({"gas_eos_comparison": {"enabled": True}}, source),
                         {"source_job_id": source["id"], "gas_eos_comparison": {"enabled": True}})
        for request in ({"enabled": 1}, {"enabled": False}, {"enabled": True, "pc": 10e6}, True):
            with self.subTest(request=request), self.assertRaises(ValueError):
                normalize_request({"gas_eos_comparison": request}, source)
        with self.assertRaisesRegex(ValueError, "来源设计记录"):
            normalize_request({"source_job_id": "another-job", "gas_eos_comparison": {"enabled": True}}, source)

    def test_explicit_candidate_selection_is_normalized_and_can_choose_bm_from_ideal_source(self):
        source = _source()
        before = deepcopy(source)
        for eos in ("Peng-Robinson", PR_BM_EOS):
            with self.subTest(candidate=eos):
                request = normalize_request({"gas_eos_comparison": {"enabled": True, "candidate_eos": eos}}, source)
                self.assertEqual(request["gas_eos_comparison"], {"enabled": True, "candidate_eos": eos})
                with patch("webapp.eos_comparison._evaluate", side_effect=_cycle) as solve, \
                        patch("webapp.eos_comparison.capabilities", return_value={"available": True}), \
                        patch("webapp.solver.require_runnable_gas_model", side_effect=AssertionError("Research must use its own protocol")):
                    result = calculate_validation(request, source)["gas_eos_comparison"]
                self.assertEqual(result["candidate_eos"], eos)
                self.assertEqual(result["candidate_selection"], "explicit")
                self.assertEqual(result["source_model"]["eos"], "ideal-gas")
                self.assertEqual(result["status"], "complete")
                self.assertEqual(set(result["runs"]), {"ideal-gas", eos})
                self.assertEqual(solve.call_count, 2)
                self.assertTrue(all(call.args[0]["gas_species_policy"] == "pr_supported_v1" for call in solve.call_args_list))
                self.assertEqual(source, before)
        normalized = normalize_request({"gas_eos_comparison": {"enabled": True, "candidate_eos": None}}, source)
        self.assertEqual(normalized["gas_eos_comparison"], {"enabled": True})
        for candidate in ("ideal-gas", "", True, [], {}, "other"):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, "candidate_eos"):
                normalize_request({"gas_eos_comparison": {"enabled": True, "candidate_eos": candidate}}, source)
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, "candidate_eos"):
                compare_gas_eos(source, candidate_eos=candidate)

    def test_legacy_temperature_correction_cannot_enter_or_execute_a_new_comparison(self):
        for missing in (False, True):
            source = _source()
            if missing:
                del source["inputs"]["combustion_loss_model"]
            else:
                source["inputs"]["combustion_loss_model"] = "legacy_temperature_rise"
            before = deepcopy(source)
            with self.subTest(missing=missing), patch("webapp.eos_comparison._evaluate", side_effect=AssertionError("Retired model must not execute")):
                with self.assertRaisesRegex(ValueError, "旧版本温升折减"):
                    normalize_request({"gas_eos_comparison": {"enabled": True}}, source)
                with self.assertRaisesRegex(ValueError, "旧版本温升折减"):
                    compare_gas_eos(source, candidate_eos=PR_BM_EOS)
                # A queued request created before this rule is checked again by
                # the worker; source records stay readable and unchanged.
                with self.assertRaisesRegex(ValueError, "旧版本温升折减"):
                    calculate_validation({"gas_eos_comparison": {"enabled": True}}, source)
            self.assertEqual(source, before)

    def test_paired_solve_changes_only_eos_and_checks_actual_segment_models(self):
        source = _source()
        before = deepcopy(source)
        request = normalize_request({"gas_eos_comparison": {"enabled": True}}, source)
        progress = []
        with patch("webapp.eos_comparison._evaluate", side_effect=_cycle) as solve_cycle, \
                patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
            validation = calculate_validation(request, source, lambda done, total, point: progress.append((done, total, point)))
        section = validation["gas_eos_comparison"]
        self.assertEqual(section["status"], "complete", {eos: run["status"] for eos, run in section["runs"].items()})
        ideal, pr = (call.args[0] for call in solve_cycle.call_args_list)
        self.assertEqual(pr, ideal | {"gas_eos": "Peng-Robinson"})
        self.assertEqual(ideal["gas_eos"], "ideal-gas")
        self.assertEqual(ideal["gas_species_policy"], "pr_supported_v1")
        self.assertEqual(ideal["pc"], source["inputs"]["pc"])
        self.assertEqual(ideal.get("hot_side_thermo_method", "legacy"), source["inputs"].get("hot_side_thermo_method", "legacy"))
        self.assertEqual([(done, total, point["stage"]) for done, total, point in progress],
                         [(0, 2, "initializing"), (0, 2, "solving"), (1, 2, "initializing"),
                          (1, 2, "solving"), (2, 2, "reviewed")])
        self.assertEqual(progress[1][2], {"phase": "整循环燃气 EOS 对比", "stage": "solving", "eos": "ideal-gas",
                                          "solver_iteration": 17, "solver_maximum": 100, "residual": .031})
        self.assertEqual(progress[3][2]["eos"], "Peng-Robinson")
        self.assertIsNone(progress[3][2]["residual"])
        self.assertEqual(source, before)
        self.assertEqual(validation["source"]["job_id"], source["id"])
        self.assertIsNone(validation["gas_screening"])
        models = section["runs"]["Peng-Robinson"]["station_models"]
        self.assertEqual(models["ox_turbine_exhaust"]["actual_eos"], "Peng-Robinson")
        self.assertEqual(models["main_chamber"]["actual_eos"], "ideal-gas")
        self.assertEqual(section["model_scope"]["candidate_eos"], "Peng-Robinson")
        self.assertEqual(section["model_scope"]["main_chamber_nozzle"], "ideal-gas")
        chamber = section["runs"]["Peng-Robinson"]["stations"]["main_chamber"]
        self.assertEqual(chamber["source_ports"], ["ox_turbine_exhaust", "fuel_turbine_exhaust"])
        self.assertEqual(chamber["eos_transition"]["enthalpy_reference_id"], "same-reference")
        self.assertEqual(next(row for row in section["metrics"] if row["id"] == "Isp_s")["difference"], -1)
        self.assertAlmostEqual(section["stations"][0]["composition"][0]["difference"], .01)

    def test_unclosed_or_wrong_station_model_retains_diagnostics_without_differences(self):
        for failure in ("error", "energy", "model", "different_species"):
            def evaluate(values):
                if values["gas_eos"] == "ideal-gas":
                    return _cycle(values)
                if failure == "error":
                    raise ValueError("PR domain boundary")
                return _cycle(values, wrong_hot_side=failure == "model", energy_error=failure == "energy",
                              species_hash="other-species" if failure == "different_species" else "shared-species")

            with self.subTest(failure=failure), patch("webapp.eos_comparison._evaluate", side_effect=evaluate), \
                    patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
                result = compare_gas_eos(_source())
                self.assertEqual(result["status"], "unavailable" if failure == "different_species" else "partial")
                self.assertEqual(result["runs"]["ideal-gas"]["status"], "computed")
                pr = result["runs"]["Peng-Robinson"]
                self.assertEqual(pr["status"], {"error": "error", "energy": "not_converged", "model": "unavailable",
                                                "different_species": "computed"}[failure])
                self.assertTrue(all(row["difference"] is None for row in result["metrics"]))
                self.assertTrue(all(row["difference"] is None for station in result["stations"]
                                    for row in station["properties"] + station["composition"]))
                if failure == "error":
                    self.assertIn("PR domain boundary", pr["reason"])

    def test_missing_shared_basis_suppresses_station_differences(self):
        def evaluate(values):
            calculation, assessment, warnings = _cycle(values)
            calculation["states"]["main_chamber"]["metadata"]["state_basis"].pop("enthalpy")
            return calculation, assessment, warnings

        with patch("webapp.eos_comparison._evaluate", side_effect=evaluate), \
                patch("webapp.eos_comparison.capabilities", return_value={"available": True}):
            result = compare_gas_eos(_source())
        self.assertEqual(result["status"], "complete")
        station = next(row for row in result["stations"] if row["id"] == "main_chamber")
        self.assertTrue(all(row["difference"] is None for row in station["properties"] + station["composition"]))
