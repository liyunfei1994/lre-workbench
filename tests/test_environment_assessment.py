"""Scientific environment reproducibility and independent engineering checks."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap import ensure_runtime
from core import ComponentResult, CycleResults, EngineInputs, FlowState
from engineering_assessment import assess_engineering
from web_tests.convergence_fixture import converged_evidence
from materials import material_evidence
from tests.cooling_profile_fixture import cooling_profile
from runtime_environment import environment_fingerprint, environment_report, requirements_hashes


def example_result():
    inputs = EngineInputs()
    inputs.turb_ox_rotor_material = "inconel718"
    summary = {"solver_residual_norm": 1e-5, "solver_iterations": 8,
               "crossflow_fuel_to_oxpb_margin_MPa": 0.4, "crossflow_lox_to_fuelpb_margin_MPa": 0.3,
               "chamber_pressure_Pa": inputs.pc,
               "ox_turbine_exhaust_pressure_Pa": inputs.pc * (1 + inputs.ox_turbine_exhaust_dp_ratio) + 1e5,
               "fuel_turbine_exhaust_pressure_Pa": inputs.pc * (1 + inputs.fuel_turbine_exhaust_dp_ratio) + 1e5,
               "ox_side_power_balance_ratio": 1.0001, "fuel_side_power_balance_ratio": 0.9998,
               "power_balance_ratio": 0.9999,
               "ox_preburner_T_combustion_K": 760, "fuel_preburner_T_combustion_K": 860}
    cooling = ComponentResult("cooling_channel", {}, profile=cooling_profile())
    return CycleResults(inputs, summary, {}, {"cooling_channel": cooling}, [], convergence=converged_evidence())


class EnvironmentTests(unittest.TestCase):
    def test_live_environment_report_is_json_serializable_and_pinned(self):
        report = environment_report(ROOT)
        self.assertTrue(report["ready"], report["dependencies"])
        self.assertEqual(report["physical_path"], {"equilibrium_chemistry": "Cantera", "real_fluid_properties": "CoolProp"})
        self.assertEqual(len(report["requirements_sha256"]), 3)
        self.assertEqual(json.loads(json.dumps(report))["fingerprint"], report["fingerprint"])

    def test_fingerprint_tracks_versions_not_install_location(self):
        report = environment_report(ROOT)
        other = deepcopy(report)
        other["executable"] = "D:/copied/python.exe"
        self.assertEqual(environment_fingerprint(report), environment_fingerprint(other))
        other["dependencies"]["cantera"]["version"] = "different"
        self.assertNotEqual(environment_fingerprint(report), environment_fingerprint(other))

    def test_transitive_dependency_drift_blocks_complete_environment(self):
        import runtime_environment
        original = runtime_environment.importlib.metadata.version
        def drift(name):
            return "0.0" if name == "packaging" else original(name)
        with patch.object(runtime_environment.importlib.metadata, "version", side_effect=drift):
            report = environment_report(ROOT)
        self.assertFalse(report["ready"])
        self.assertTrue(any("packaging" in problem for problem in report["lock_problems"]))

    def test_fingerprint_tracks_locked_transitive_versions(self):
        report = environment_report(ROOT)
        other = deepcopy(report)
        other["locked_versions"]["packaging"] = "0.0"
        self.assertNotEqual(environment_fingerprint(report), environment_fingerprint(other))

    def test_basic_mode_does_not_require_science_dependency_lock(self):
        import runtime_environment
        original = runtime_environment.importlib.metadata.version
        def drift(name):
            return "0.0" if name == "scipy" else original(name)
        with patch.object(runtime_environment.importlib.metadata, "version", side_effect=drift):
            report = environment_report(ROOT, "basic")
        self.assertTrue(report["ready"], report["lock_problems"])

    def test_missing_chemistry_cannot_claim_complete_environment(self):
        import runtime_environment
        original = runtime_environment.importlib.import_module
        def missing_cantera(name):
            if name == "cantera":
                raise ImportError("test unavailable")
            return original(name)
        with patch.object(runtime_environment.importlib, "import_module", side_effect=missing_cantera):
            report = environment_report(ROOT)
            basic = environment_report(ROOT, "basic")
        self.assertFalse(report["ready"])
        self.assertTrue(basic["ready"])
        self.assertEqual(report["physical_path"]["equilibrium_chemistry"], "经验公式")

    def test_cantera_import_with_broken_mechanism_is_not_ready(self):
        with patch("thermo._get_gas", side_effect=RuntimeError("mechanism unavailable")):
            report = environment_report(ROOT)
        self.assertTrue(report["dependencies"]["cantera"]["available"])
        self.assertFalse(report["functional_checks"]["cantera_mechanism"]["ready"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["physical_path"]["equilibrium_chemistry"], "Cantera 机制未就绪")

    def test_standard_setup_ready_environment_does_not_run_pip(self):
        report = environment_report(ROOT)
        with tempfile.TemporaryDirectory() as folder:
            environment = Path(folder)
            with patch("bootstrap.probe_runtime", return_value=report) as probe, patch("bootstrap.subprocess.run") as execute:
                actual = ensure_runtime(Path(sys.executable), environment, root=ROOT)
            probe.assert_called_once_with(Path(sys.executable), ROOT, "full")
            execute.assert_not_called()
            saved = json.loads((environment / "ffsc-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["requirements_sha256"], requirements_hashes(ROOT))
            self.assertEqual(actual["fingerprint"], report["fingerprint"])

    def test_standard_setup_installs_both_requirements_and_rechecks(self):
        ready = environment_report(ROOT)
        missing = deepcopy(ready)
        missing["ready"] = False
        with tempfile.TemporaryDirectory() as folder:
            with patch("bootstrap.probe_runtime", side_effect=[missing, ready]) as probe, patch("bootstrap.subprocess.run") as execute:
                ensure_runtime(Path(sys.executable), Path(folder), root=ROOT)
        args = execute.call_args.args[0]
        self.assertIn(str(ROOT / "requirements.txt"), args)
        self.assertIn(str(ROOT / "requirements-science.txt"), args)
        self.assertEqual(probe.call_count, 2)


class EngineeringTests(unittest.TestCase):
    def _network_result(self):
        from steady_network import solve_design
        result = example_result()
        result.summary.update(mdot_fuel_kg_s=100.0, mdot_ox_kg_s=360.0)
        network = solve_design(total_fuel=100.0, total_ox=360.0,
            mr_ox=60.0, mr_fuel=0.16, p_pre_ox=60e6, p_pre_fuel=55e6,
            injector_ratio=0.1, line_dp=1e6)
        result.steady_closure = {"schema_version": 1, "feed_network": network}
        return result

    def test_audited_network_uses_explicit_sub_pascal_tolerance_without_clipping(self):
        result = self._network_result()
        result.summary["crossflow_fuel_to_oxpb_margin_MPa"] = -0.092598 / 1e6
        # A saved tolerance cannot relax the independently recalculated one.
        result.steady_closure["feed_network"]["pressure_tolerance_Pa"] = 1e9
        checks = {row["key"]: row for row in assess_engineering(result)["checks"]}
        row = checks["crossflow_fuel_to_oxpb_margin_MPa"]
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["value"], -0.092598 / 1e6)
        self.assertEqual(row["limit"], -1e-6)
        self.assertIn("1 Pa", row["detail"])
        result.summary["crossflow_fuel_to_oxpb_margin_MPa"] = -4.367
        checks = {row["key"]: row for row in assess_engineering(result)["checks"]}
        self.assertEqual(checks["crossflow_fuel_to_oxpb_margin_MPa"]["status"], "fail")

    def test_legacy_or_failed_network_does_not_borrow_closure_tolerance(self):
        for result in (example_result(), self._network_result()):
            result.summary["crossflow_fuel_to_oxpb_margin_MPa"] = -0.092598 / 1e6
            if result.steady_closure:
                result.steady_closure["feed_network"]["branches"][0]["source_pressure_Pa"] -= 10
            checks = {row["key"]: row for row in assess_engineering(result)["checks"]}
            row = checks["crossflow_fuel_to_oxpb_margin_MPa"]
            self.assertEqual(row["status"], "fail")
            self.assertEqual(row["limit"], 0.0)

    def test_energy_detail_follows_recorded_heat_allocation(self):
        for chamber, nozzle in ((0., 117.5e6), (90e6, 0.)):
            result = self._network_result()
            result.components["main_chamber"] = ComponentResult("main_chamber", {"heat_loss_W": chamber})
            result.components["nozzle"] = ComponentResult("nozzle", {"heat_loss_W": nozzle})
            row = next(row for row in assess_engineering(result, scope="system")["checks"]
                       if row["key"] == "energy_ledger_closure")
            self.assertIn(f"主室控制体扣热 {chamber/1e6:g} MW", row["detail"])
            self.assertIn(f"喷管控制体扣热 {nozzle/1e6:g} MW", row["detail"])
            self.assertNotIn("等效集中", row["detail"])

    def test_constraints_pass_independently_from_numerical_convergence(self):
        result = example_result()
        assessment = assess_engineering(result)
        self.assertEqual(assessment["numerical"]["status"], "pass")
        self.assertEqual(assessment["status"], "unknown", assessment)
        missing = {row["key"] for row in assessment["checks"] if row["status"] == "unknown"}
        self.assertEqual(missing, {"feed_network_closure", "energy_ledger_closure"})
        provisional = {"ox_preburner_T_combustion_K", "fuel_preburner_T_combustion_K"}
        self.assertTrue(all(row["status"] == ("review" if row["key"] in provisional else "pass")
                            for row in assessment["checks"] if row["key"] not in missing))
        json.dumps(assessment, allow_nan=False)

    def test_converged_result_can_fail_pressure_temperature_and_power(self):
        result = example_result()
        result.summary.update(crossflow_fuel_to_oxpb_margin_MPa=-3.4,
                              ox_preburner_T_combustion_K=850,
                              fuel_side_power_balance_ratio=0.7)
        assessment = assess_engineering(result)
        self.assertEqual(assessment["numerical"]["status"], "pass")
        self.assertEqual(assessment["status"], "fail")
        self.assertEqual(assessment["failure_count"], 3)

    def test_nonfinite_or_missing_values_are_not_passed(self):
        result = example_result()
        result.summary["crossflow_lox_to_fuelpb_margin_MPa"] = float("nan")
        result.summary.pop("solver_residual_norm")
        assessment = assess_engineering(result)
        self.assertEqual(assessment["numerical"]["status"], "unknown")
        self.assertEqual(assessment["status"], "unknown")
        self.assertEqual(assessment["unknown_count"], 3)
        json.dumps(assessment, allow_nan=False)

    def test_failed_cooling_hydraulics_invalidates_wall_temperature(self):
        result = example_result()
        result.components["cooling_channel"].profile["nodes"][2]["Mach"] = 1.1
        assessment = assess_engineering(result)
        checks = {row["key"]: row for row in assessment["checks"]}
        self.assertEqual(checks["cooling_hydraulics"]["status"], "fail")
        self.assertEqual(checks["wall_temperature"]["status"], "unknown")

    def test_peak_comes_from_all_cells_not_cached_summary_or_old_postpass(self):
        result = example_result()
        profile = result.components["cooling_channel"].profile
        profile["cells"][2]["T_wall_hot_K"] = 1252.8
        profile["validity"]["status"] = "review"
        result.components["cooling_marching"] = ComponentResult("cooling_marching", {
            "hydraulic_infeasible": 0, "T_wall_hot_max_K": 600})
        result.components["cooling_channel"].outputs["T_wall_hot_K"] = 600
        for scope in ("engineering", "system"):
            assessment = assess_engineering(result, scope=scope)
            checks = {row["key"]: row for row in assessment["checks"]}
            self.assertEqual(assessment["numerical"]["status"], "pass")
            self.assertEqual(assessment["status"], "fail")
            self.assertEqual(checks["cooling_hydraulics"]["status"], "pass")
            self.assertEqual(checks["wall_temperature"]["value"], 1252.8)
            self.assertEqual(checks["wall_temperature"]["status"], "fail")
            self.assertIn("结构强度", checks["wall_temperature"]["detail"])

    def test_incomplete_profile_and_budget_never_pass_wall_limit(self):
        for alteration in ("budget", "missing", "truncated", "missing_temperature", "wall_unclosed"):
            result = example_result()
            profile = result.components["cooling_channel"].profile
            if alteration == "budget":
                result.inputs.cooling_bartz_mode = 0
            elif alteration == "missing":
                result.components["cooling_channel"].profile = None
            elif alteration == "truncated":
                profile["cells"].pop(1)
            elif alteration == "missing_temperature":
                profile["cells"][1]["T_wall_hot_K"] = None
            else:
                profile["cells"][1]["wall_residual_K"] = .1
            for scope in ("engineering", "system"):
                checks = {row["key"]: row for row in assess_engineering(result, scope=scope)["checks"]}
                self.assertEqual(checks["wall_temperature"]["status"], "unknown", alteration)

    def test_saved_hydraulic_badge_cannot_hide_unclosed_momentum(self):
        result = example_result()
        result.components["cooling_channel"].profile["nodes"][2]["p_Pa"] -= 3
        checks = {row["key"]: row for row in assess_engineering(result, scope="system")["checks"]}
        self.assertEqual(checks["cooling_hydraulics"]["status"], "fail")
        self.assertEqual(checks["wall_temperature"]["status"], "unknown")

    def test_nonconverged_result_cannot_claim_engineering_pass(self):
        result = example_result()
        result.summary["solver_warning"] = 1
        assessment = assess_engineering(result)
        self.assertEqual(assessment["numerical"]["status"], "fail")
        self.assertEqual(assessment["status"], "unknown")

    def test_sx_placeholder_does_not_claim_validated_oxygen_compatibility(self):
        for key in ("sx300", "sx500"):
            evidence = material_evidence(key)
            self.assertEqual(evidence["status"], "placeholder")
            self.assertEqual(evidence["oxygen_compatibility_status"], "unknown")
            self.assertFalse(evidence["oxygen_test_validated"])
        result = example_result()
        result.inputs.turb_ox_rotor_material = "sx500"
        assessment = assess_engineering(result)
        self.assertEqual(assessment["status"], "unknown")
        self.assertEqual(assessment["materials"]["turb_ox_rotor_material"]["status"], "placeholder")

    def test_gg_supply_uses_cooled_main_fuel_and_pump_gg_takeoff(self):
        result = example_result()
        inputs = result.inputs
        inputs.cycle_type = "gg"
        result.summary.update(chamber_pressure_Pa=10e6, valve_line_dp_MPa=0.5,
                              injector_dp_ox_MPa=2, injector_dp_fuel_MPa=1.5,
                              gg_pressure_Pa=10e6, gg_T_combustion_K=900)
        result.states = {"lox_after_pump": SimpleNamespace(pressure=13e6),
                         "fuel_after_pump": SimpleNamespace(pressure=18e6),
                         "fuel_after_cooling": SimpleNamespace(pressure=12.1e6)}
        assessment = assess_engineering(result)
        checks = {row["key"]: row for row in assessment["checks"]}
        self.assertAlmostEqual(checks["fuel_main_feed_margin_MPa"]["value"], 0.1)
        self.assertAlmostEqual(checks["gg_feed_margin_MPa"]["value"], 1.0)
        self.assertEqual(assessment["status"], "pass")


if __name__ == "__main__":
    unittest.main()
