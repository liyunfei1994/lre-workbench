"""The CFD exchange contract rejects wrong identity, units and fake results."""
import copy
import csv
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from cfd_exchange import (create_cfd_job, load_cfd_job, import_cfd_result, load_imported_result,
                          MAX_FILE_BYTES, METRIC_RULES)
from core import ComponentResult, CycleResults, EngineInputs, FlowState


def model_result(cycle="ffsc"):
    inputs = EngineInputs(cycle_type=cycle)
    states = {
        "ox_turbine_exhaust": FlowState("ox_turbine_exhaust", 34e6, 700, 80, composition={"O2": .9, "H2O": .1}),
        "fuel_turbine_exhaust": FlowState("fuel_turbine_exhaust", 34e6, 800, 20, composition={"CH4": .8, "H2O": .2}),
        "ox_preburner_outlet": FlowState("ox_preburner_outlet", 45e6, 750, 80),
        "fuel_preburner_outlet": FlowState("fuel_preburner_outlet", 45e6, 850, 20),
        "main_chamber": FlowState("main_chamber", 33e6, 3500, 100),
        "lox_after_pump": FlowState("lox_after_pump", 45e6, 90, 80),
        "fuel_after_pump": FlowState("fuel_after_pump", 45e6, 110, 20),
        "fuel_after_cooling": FlowState("fuel_after_cooling", 40e6, 600, 18 if cycle == "gg" else 20),
        "gg_outlet": FlowState("gg_outlet", 10e6, 850, 12),
        "gg_turbine_exhaust": FlowState("gg_turbine_exhaust", .4e6, 600, 12),
    }
    summary = {"solver_residual_norm": 1e-5, "solver_iterations": 5, "chamber_pressure_Pa": 33e6,
               "mdot_chamber_kg_s": 90, "chamber_mixture_ratio": 4,
               "gg_layout_dual": 0, "gg_turbine_backpressure_Pa": .4e6}
    components = {"cooling_channel": ComponentResult("cooling_channel", {"T_wall_hot_K": 770}),
                  "gg_turbine": ComponentResult("gg_turbine", {"mass_flow_kgs": 12, "T_exhaust_K": 600}),
                  "gg_turbine_ox": ComponentResult("gg_turbine_ox", {"mass_flow_kgs": 7, "T_exhaust_K": 550}),
                  "gg_turbine_fuel": ComponentResult("gg_turbine_fuel", {"mass_flow_kgs": 5, "T_exhaust_K": 650})}
    return CycleResults(inputs, summary, states, components, ["参考工况存在工程警告，CFD 不能替代验证。"])


def reference_geometry(full=False):
    geometry = {"nozzle": {"contour_x": [-.2, 0, .6], "contour_r": [.25, .1, .3],
                             "throat_radius_m": .1, "exit_radius_m": .3,
                             "expansion_ratio": 9, "bell_fraction": .8, "convergent_half_angle_deg": 30}}
    if full:
        geometry["chamber"] = {"cylindrical_section_length_m": .5, "radius_m": .25}
    return geometry


class CfdExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lre-cfd-接口-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.results = model_result()
        self.job = create_cfd_job(self.results, reference_geometry(), self.parent,
                                  settings={"pressure_basis": "static", "temperature_basis": "total", "setup_reviewed": True},
                                  environment={"python": "3.11", "cantera": "3.1.0"})
        self.manifest = load_cfd_job(self.job)

    def result_document(self, **overrides):
        result = {"format": "lre.cfd.result", "schema_version": 1,
                  "job_id": self.manifest["job_id"], "input_fingerprint": self.manifest["input_fingerprint"],
                  "task": self.manifest["task"], "source_kind": "cfd",
                  "solver": {"name": "ExampleSolver", "version": "1.2"}, "converged": True,
                  "metrics": {"pressure_drop_Pa": {"value": 2e5, "unit": "Pa"},
                              "max_wall_temperature_K": {"value": 780, "unit": "K"}}}
        result.update(overrides)
        return result

    def write_result(self, document):
        path = self.job / "result.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def test_job_snapshot_default_basis_and_unique_directories(self):
        other = create_cfd_job(self.results, reference_geometry(), self.parent)
        manifest = load_cfd_job(other)
        self.assertNotEqual(self.job, other)
        self.assertNotEqual(self.manifest["job_id"], manifest["job_id"])
        self.assertEqual(manifest["settings"]["pressure_basis"], "unresolved")
        self.assertEqual(manifest["settings"]["temperature_basis"], "unresolved")
        self.assertFalse(manifest["settings"]["setup_reviewed"])
        self.assertEqual(manifest["inputs"], asdict(self.results.inputs))
        self.assertEqual(manifest["initial_results"]["warnings"], self.results.warnings)
        self.assertIn("engineering_assessment", manifest["initial_results"])
        self.assertFalse(manifest["geometry_reference"]["is_mesh"])
        self.assertIsNone(manifest["native_solver_adapter"])

    def test_absolute_si_boundaries_do_not_duplicate_main_chamber_flow(self):
        with (self.job / "boundaries.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(sum(float(row["mass_flow_kg_s"]) for row in rows if row["role"] == "inlet"), 100)
        reference = next(row for row in rows if row["state_key"] == "main_chamber")
        self.assertEqual(reference["mass_flow_kg_s"], "")
        self.assertEqual(float(reference["pressure_absolute_Pa"]), 33e6)
        self.assertEqual(reference["pressure_basis"], "static")
        self.assertEqual(reference["temperature_basis"], "total")

    def test_gg_main_domain_excludes_generator_bypass(self):
        result = model_result("gg")
        job = create_cfd_job(result, {}, self.parent)
        manifest = load_cfd_job(job)
        flows = {row["state_key"]: row["mass_flow_kg_s"] for row in manifest["boundaries"] if row["role"] == "inlet"}
        self.assertEqual(flows, {"lox_after_pump": 72, "fuel_after_cooling": 18})
        self.assertNotEqual(sum(flows.values()), 100)

    def test_cooling_domain_uses_actual_coolant_flow_not_gg_pump_total(self):
        job = create_cfd_job(model_result("gg"), {}, self.parent, task="cooling_flow")
        rows = load_cfd_job(job)["boundaries"]
        self.assertEqual([row["mass_flow_kg_s"] for row in rows if row["role"] == "inlet"], [18])
        self.assertTrue(all(row["mass_flow_kg_s"] is None for row in rows if row["role"] != "inlet"))

    def test_turbine_domains_select_one_branch_and_gg_dual_output(self):
        for side, flow in (("ox", 80), ("fuel", 20)):
            job = create_cfd_job(self.results, {}, self.parent, task="turbine_flow", settings={"turbine_side": side})
            rows = load_cfd_job(job)["boundaries"]
            self.assertEqual(sum(row["mass_flow_kg_s"] or 0 for row in rows), flow)
        result = model_result("gg")
        result.summary["gg_layout_dual"] = 1
        job = create_cfd_job(result, {}, self.parent, task="turbine_flow", settings={"turbine_side": "fuel"})
        rows = load_cfd_job(job)["boundaries"]
        self.assertEqual(sum(row["mass_flow_kg_s"] or 0 for row in rows), 5)
        self.assertEqual(rows[-1]["temperature_K"], 650)

    def test_full_geometry_uses_injection_origin_and_partial_keeps_throat_origin(self):
        full_job = create_cfd_job(self.results, reference_geometry(full=True), self.parent)
        full = load_cfd_job(full_job)["geometry_reference"]["contour"]
        self.assertEqual(full["coordinate_origin"], "injection_face_origin")
        self.assertTrue(full["includes_cylindrical_chamber"])
        self.assertEqual(full["x_m"][0], 0)
        self.assertGreater(full["throat_x_m"], .5)
        # Preserve supplied coordinates, adding only the cylindrical inlet.
        self.assertEqual(len(full["x_m"]), 4)
        for actual, expected in zip(full["x_m"], [0., .5, .7, 1.3]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(full["radius_m"], [.25, .25, .1, .3])
        with (full_job / "geometry_contour.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(float(rows[0]["x_m"]), 0)
        self.assertEqual(rows[0]["coordinate_origin"], "injection_face_origin")
        partial = self.manifest["geometry_reference"]["contour"]
        self.assertEqual(partial["coordinate_origin"], "nozzle_throat_origin")
        self.assertFalse(partial["includes_cylindrical_chamber"])
        self.assertEqual(partial["x_m"], [-.2, 0, .6])

    def test_identity_changes_with_task_geometry_input_and_review_settings(self):
        altered = copy.deepcopy(self.results)
        altered.inputs.pc = 32e6
        jobs = [create_cfd_job(altered, reference_geometry(), self.parent),
                create_cfd_job(self.results, {}, self.parent, task="cooling_flow"),
                create_cfd_job(self.results, reference_geometry(full=True), self.parent),
                create_cfd_job(self.results, reference_geometry(), self.parent)]
        hashes = {load_cfd_job(job)["input_fingerprint"] for job in jobs}
        self.assertEqual(len(hashes), len(jobs))
        self.assertNotIn(self.manifest["input_fingerprint"], hashes)

    def test_submitted_and_effective_inputs_are_preserved_and_both_bound_to_hash(self):
        submitted = asdict(self.results.inputs)
        submitted["chamber_temp_guess"] = self.results.inputs.chamber_temp_guess + 12.5
        job = create_cfd_job(self.results, {}, self.parent, source_inputs=submitted)
        manifest = load_cfd_job(job)
        self.assertEqual(manifest["inputs"], submitted)
        self.assertEqual(manifest["initial_results"]["effective_inputs"], asdict(self.results.inputs))
        self.assertNotEqual(manifest["inputs"], manifest["initial_results"]["effective_inputs"])
        for path in ("submitted", "effective"):
            altered = copy.deepcopy(manifest)
            inputs = altered["inputs"] if path == "submitted" else altered["initial_results"]["effective_inputs"]
            inputs["chamber_temp_guess"] += 1
            (job / "manifest.json").write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "指纹"):
                load_cfd_job(job)
        for bad in ({**submitted, "bad_key": 1}, {**submitted, "pc": math.nan}, {**submitted, "pc": -1}, "not inputs"):
            with self.assertRaises((TypeError, ValueError)):
                create_cfd_job(self.results, {}, self.parent, source_inputs=bad)

    def test_valid_result_is_atomically_persisted_and_reloaded(self):
        self.assertIsNone(load_imported_result(self.job))
        self.write_result(self.result_document(wall_profile={"x_m": [0, .1, .3], "wall_temperature_K": [740, 750, 780]}))
        imported = import_cfd_result(self.job)
        self.assertTrue(imported["feedback_eligible"])
        self.assertFalse(imported["diagnostic_only"])
        self.assertEqual(load_imported_result(self.job), imported)
        self.assertTrue((self.job / "imported_result.json").is_file())
        self.assertFalse(list(self.job.glob(".cfd-*.tmp")))

    def test_wrong_job_fingerprint_and_task_are_rejected(self):
        for key, value in (("job_id", "wrong"), ("input_fingerprint", "0" * 64), ("task", "cooling_flow")):
            with self.subTest(key=key):
                document = self.result_document(**{key: value})
                self.write_result(document)
                with self.assertRaisesRegex(ValueError, key):
                    import_cfd_result(self.job)
        self.assertFalse((self.job / "imported_result.json").exists())

    def test_exact_metric_units_structure_and_range(self):
        invalid_metrics = [{}, {"unknown": {"value": 1, "unit": "1"}},
                           {"pressure_drop_Pa": {"value": 1, "unit": "MPa"}},
                           {"pressure_drop_Pa": {"value": "1", "unit": "Pa"}},
                           {"pressure_drop_Pa": {"value": True, "unit": "Pa"}},
                           {"max_wall_temperature_K": {"value": 0, "unit": "K"}},
                           {"efficiency": {"value": 1.1, "unit": "1"}},
                           {"heat_load_W": {"value": -1, "unit": "W"}},
                           {"thrust_N": {"value": 1, "unit": "N", "extra": 1}}]
        for metrics in invalid_metrics:
            with self.subTest(metrics=metrics):
                self.write_result(self.result_document(metrics=metrics))
                with self.assertRaises(ValueError):
                    import_cfd_result(self.job)
        for name, (unit, lo, hi) in METRIC_RULES.items():
            self.write_result(self.result_document(metrics={name: {"value": .8 if name == "efficiency" else 1, "unit": unit}}))
            self.assertIn(name, import_cfd_result(self.job)["metrics"])

    def test_nan_infinity_damage_duplicate_keys_and_boolean_contract(self):
        for number in (math.nan, math.inf, -math.inf):
            self.write_result(self.result_document(metrics={"thrust_N": {"value": number, "unit": "N"}}))
            with self.assertRaises(ValueError):
                import_cfd_result(self.job)
        for text in ('{"broken":', '{"format":"x","format":"y"}', '{"number":1e400}'):
            (self.job / "result.json").write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                import_cfd_result(self.job)
        for value in (1, 0, "true", None):
            self.write_result(self.result_document(converged=value))
            with self.assertRaisesRegex(ValueError, "bool"):
                import_cfd_result(self.job)

    def test_template_and_placeholder_solver_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "模板"):
            import_cfd_result(self.job, self.job / "result_template.json")
        self.write_result(self.result_document(solver={"name": "REPLACE_WITH_SOLVER_NAME", "version": "x"}))
        with self.assertRaisesRegex(ValueError, "solver"):
            import_cfd_result(self.job)

    def test_test_data_and_nonconverged_results_are_diagnostic_only(self):
        for overrides in ({"source_kind": "test"}, {"converged": False}, {"source_kind": "test", "converged": False}):
            self.write_result(self.result_document(**overrides))
            imported = import_cfd_result(self.job)
            self.assertFalse(imported["feedback_eligible"])
            self.assertTrue(imported["diagnostic_only"])
            self.assertTrue(imported["eligibility_reasons"])
            self.assertFalse(load_imported_result(self.job)["feedback_eligible"])

    def test_unreviewed_or_unresolved_setup_and_missing_boundary_are_not_feedback(self):
        for setup in ({}, {"setup_reviewed": True}, {"pressure_basis": "total", "temperature_basis": "static"}):
            job = create_cfd_job(self.results, {}, self.parent, settings=setup)
            manifest = load_cfd_job(job)
            document = self.result_document(job_id=manifest["job_id"], input_fingerprint=manifest["input_fingerprint"])
            path = job / "result.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertFalse(import_cfd_result(job)["feedback_eligible"])
        missing = copy.deepcopy(self.results)
        missing.states.pop("ox_turbine_exhaust")
        job = create_cfd_job(missing, {}, self.parent, settings=self.manifest["settings"])
        manifest = load_cfd_job(job)
        self.assertFalse(manifest["boundary_review"]["complete"])
        document = self.result_document(job_id=manifest["job_id"], input_fingerprint=manifest["input_fingerprint"])
        path = job / "result.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(import_cfd_result(job)["feedback_eligible"])

    def test_wall_profile_lengths_order_numbers_and_fields(self):
        profiles = [{"x_m": [0, 1], "wall_temperature_K": [800]},
                    {"x_m": [0, 0], "wall_temperature_K": [800, 810]},
                    {"x_m": [1, 0], "wall_temperature_K": [800, 810]},
                    {"x_m": [0, math.nan], "wall_temperature_K": [800, 810]},
                    {"x_m": [0, 1], "wall_temperature_K": [800, True]},
                    {"x_m": [0, 1], "wall_temperature_K": [800, 810], "unknown": [1, 2]}]
        for profile in profiles:
            with self.subTest(profile=profile):
                self.write_result(self.result_document(wall_profile=profile))
                with self.assertRaises(ValueError):
                    import_cfd_result(self.job)

    def test_manifest_cannot_redirect_result_and_tampered_snapshots_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        for filename in ("../outside.json", str(self.parent / "outside.json")):
            altered = copy.deepcopy(original)
            altered["files"]["result"] = filename
            (self.job / "manifest.json").write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "固定文件名"):
                load_cfd_job(self.job)
        altered = copy.deepcopy(original)
        altered["inputs"]["pc"] += 1000
        (self.job / "manifest.json").write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "指纹"):
            load_cfd_job(self.job)

    def test_read_write_size_limit_and_atomic_failure_preserve_old_import(self):
        self.write_result(self.result_document())
        import_cfd_result(self.job)
        old = (self.job / "imported_result.json").read_bytes()
        self.write_result(self.result_document(metrics={"efficiency": {"value": .9, "unit": "1"}}))
        with patch("cfd_exchange.os.replace", side_effect=OSError("write interrupted")):
            with self.assertRaises(OSError):
                import_cfd_result(self.job)
        self.assertEqual((self.job / "imported_result.json").read_bytes(), old)
        self.assertFalse(list(self.job.glob(".cfd-*.tmp")))
        with (self.job / "result.json").open("wb") as handle:
            handle.truncate(MAX_FILE_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "20 MiB"):
            import_cfd_result(self.job)
        huge = {"note": "x" * (MAX_FILE_BYTES + 1)}
        with self.assertRaisesRegex(ValueError, "20 MiB"):
            create_cfd_job(self.results, {}, self.parent, environment=huge)

    def test_persisted_eligibility_is_recomputed_not_trusted(self):
        self.write_result(self.result_document(source_kind="test"))
        imported = import_cfd_result(self.job)
        imported["feedback_eligible"] = True
        imported["diagnostic_only"] = False
        (self.job / "imported_result.json").write_text(json.dumps(imported), encoding="utf-8")
        self.assertFalse(load_imported_result(self.job)["feedback_eligible"])

    def test_cooling_result_requires_explicit_full_engine_domain_for_feedback(self):
        job = create_cfd_job(self.results, {}, self.parent, task="cooling_flow", settings=self.manifest["settings"])
        manifest = load_cfd_job(job)
        self.assertEqual(manifest["domain"]["flow_scope"], "full_engine")
        self.assertEqual(manifest["domain"]["heat_load_scope"], "full_engine")
        self.assertEqual(manifest["domain"]["cooling_channel_count"], 300)
        document = self.result_document(job_id=manifest["job_id"], input_fingerprint=manifest["input_fingerprint"],
                                        task="cooling_flow", metrics={"heat_load_W": {"value": 20e6, "unit": "W"}})
        path = job / "result.json"
        for domain in (None, {"flow_scope": "single_channel"},
                       {**manifest["domain"], "cooling_channel_count": 1},
                       {**manifest["domain"], "no_implicit_multiplication": 1}):
            with self.subTest(domain=domain):
                document.pop("domain", None)
                if domain is not None:
                    document["domain"] = domain
                path.write_text(json.dumps(document), encoding="utf-8")
                imported = import_cfd_result(job)
                self.assertFalse(imported["feedback_eligible"])
                self.assertTrue(any("全发动机" in reason for reason in imported["eligibility_reasons"]))
                self.assertEqual(imported["metrics"]["heat_load_W"]["value"], 20e6)
        document["domain"] = manifest["domain"]
        path.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(import_cfd_result(job)["feedback_eligible"])

    def test_bad_geometry_and_nonfinite_model_data_fail_before_job_creation(self):
        for geometry in ({"contour_x": [0, 1], "contour_r": [.1]},
                         {"contour_x": [1, 0], "contour_r": [.1, .2]},
                         {"contour_x": [0, 1], "contour_r": [.1, math.inf]}):
            with self.assertRaises(ValueError):
                create_cfd_job(self.results, geometry, self.parent)
        broken = copy.deepcopy(self.results)
        broken.summary["bad"] = math.nan
        with self.assertRaises(ValueError):
            create_cfd_job(broken, {}, self.parent)


if __name__ == "__main__":
    unittest.main()
