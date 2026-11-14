"""Selected gas models execute only with compatible, explicit species scope."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from webapp.app import create_app
from webapp.solver import (calculate, calculate_design_job, design_readiness,
                          input_values, platform_defaults, presets)
from web_tests.test_workspace import client, TEST_PASSWORD


PR_MODELS = ("Peng-Robinson", "Peng-Robinson-Boston-Mathias")


def baseline():
    return next(p["inputs"] for p in presets() if p["key"] == "raptor3_ffsc")


class GasModelReadinessTests(unittest.TestCase):
    def test_incompatible_and_retired_models_are_blocked_without_input_migration(self):
        selections = [{"gas_eos": eos, "gas_species_policy": "full"} for eos in PR_MODELS]
        selections += [{"gas_species_policy": "pr_supported_v1"},
                       {"combustion_loss_model": "legacy_temperature_rise", "combustion_efficiency": .95}]
        selections += [{"gas_eos": eos, "gas_species_policy": "pr_supported_v1",
                        "combustion_loss_model": "legacy_temperature_rise"} for eos in PR_MODELS]
        for selection in selections:
            values = baseline() | selection
            before = deepcopy(values)
            with self.subTest(selection=selection), patch(
                    "lre_workbench.properties.gas_eos.build_gas",
                    side_effect=AssertionError("Readiness must not construct thermodynamic phases")):
                normalized, _ = input_values(values, saved_snapshot=True)
                self.assertEqual(normalized, before)
                report = design_readiness(values)
                self.assertFalse(report["valid"])
                self.assertTrue(set(report["issues"][0]["fields"]) & selection.keys())
                self.assertIn("原输入与历史结果保持不变", report["issues"][0]["message"])
                with patch("webapp.solver._calculate", side_effect=AssertionError("Must reject before solve")):
                    with self.assertRaises(ValueError):
                        calculate(values)
                    with self.assertRaises(ValueError):
                        calculate_design_job({"kind": "design", "inputs": json.dumps(values)})
                self.assertEqual(values, before)

    def test_valid_model_pairs_execute_without_rewriting_and_research_scope_is_reported(self):
        combinations = [("ideal-gas", "full"), ("ideal-gas", "auto")]
        combinations += [(eos, policy) for eos in PR_MODELS for policy in ("pr_supported_v1", "auto")]
        for eos, policy in combinations:
            values = baseline() | {"gas_eos": eos, "gas_species_policy": policy}
            before = deepcopy(values)
            with self.subTest(eos=eos, policy=policy), patch(
                    "lre_workbench.properties.gas_eos.build_gas",
                    side_effect=AssertionError("Input validation must remain inexpensive")):
                readiness = design_readiness(values)
                self.assertTrue(readiness["valid"])
                if eos in PR_MODELS:
                    notes = "\n".join(readiness["warnings"])
                    for text in ("16", "H、O、OH", "高温", "高压工况验证", "迭代耗时"):
                        self.assertIn(text, notes)
                else:
                    self.assertFalse(any("受限研究模型" in text for text in readiness["warnings"]))
                normalized, _ = input_values(values, saved_snapshot=True)
                self.assertEqual(normalized, before)
                with patch("webapp.solver._calculate", return_value={"test": "accepted"}) as solve:
                    self.assertEqual(calculate(values), {"test": "accepted"})
                    self.assertEqual(solve.call_args.args[0]["gas_eos"], eos)
                    self.assertEqual(solve.call_args.args[0]["gas_species_policy"], policy)
                    self.assertEqual(solve.call_args.args[0], before)
                    self.assertEqual(solve.call_args.args[2], readiness["warnings"])
                self.assertEqual(values, before)

    def test_invalid_model_values_are_rejected_before_phase_construction(self):
        from lre_workbench.domain.models import ValidationError
        for key in ("gas_eos", "gas_species_policy", "combustion_loss_model"):
            for bad in ("unknown", "", None):
                values = baseline() | {key: bad}
                before = deepcopy(values)
                with self.subTest(key=key, value=bad), patch(
                        "lre_workbench.properties.gas_eos.build_gas",
                        side_effect=AssertionError("Invalid inputs must not construct phases")), patch(
                        "webapp.solver._calculate", side_effect=AssertionError("Must reject before solve")):
                    self.assertFalse(design_readiness(values)["valid"])
                    with self.assertRaises((ValueError, ValidationError)):
                        calculate(values)
                    with self.assertRaises((ValueError, ValidationError)):
                        calculate_design_job({"kind": "design", "inputs": json.dumps(values)})
                    self.assertEqual(values, before)

    def test_new_web_defaults_are_explicit_but_omitted_old_model_stays_legacy(self):
        from lre_workbench.domain.models import EngineInputs
        defaults = platform_defaults()
        self.assertEqual({key: defaults[key] for key in (
            "gas_eos", "gas_species_policy", "methane_thermo_dataset", "combustion_loss_model")},
            {"gas_eos": "ideal-gas", "gas_species_policy": "full", "methane_thermo_dataset": "gri30_highT",
             "combustion_loss_model": "adiabatic"})
        self.assertEqual(EngineInputs().combustion_loss_model, "legacy_temperature_rise")
        self.assertTrue(design_readiness(defaults)["valid"])
        empty = {}
        new_empty, _ = input_values(empty)
        old_empty, _ = input_values(empty, saved_snapshot=True)
        self.assertEqual(new_empty, defaults)
        self.assertEqual(old_empty["combustion_loss_model"], "legacy_temperature_rise")
        self.assertEqual(empty, {})
        for saved_snapshot in (False, True):
            values = baseline()
            del values["combustion_loss_model"]
            before = deepcopy(values)
            normalized, _ = input_values(values, saved_snapshot=saved_snapshot)
            self.assertEqual(normalized["combustion_loss_model"], "legacy_temperature_rise")
            self.assertEqual(values, before)
            with patch("webapp.solver._calculate", side_effect=AssertionError("Must reject before solve")):
                with self.assertRaisesRegex(ValueError, "combustion_loss_model"):
                    calculate(values, saved_snapshot=saved_snapshot)

    def test_missing_species_policy_keeps_existing_document_and_snapshot_identities(self):
        # The saved-record contract already identifies an omitted policy as
        # full. A current EOS parameter limitation is not evidence that the
        # historical record used 16 species, and must not rewrite its identity.
        for eos in ("ideal-gas", *PR_MODELS):
            values = baseline() | {"gas_eos": eos}
            values.pop("gas_species_policy")
            before = deepcopy(values)
            with self.subTest(eos=eos):
                document, _ = input_values(values)
                snapshot, _ = input_values(values, saved_snapshot=True)
                self.assertEqual(document["gas_species_policy"], "auto")
                self.assertEqual(snapshot["gas_species_policy"], "full")
                self.assertEqual(document["gas_eos"], eos)
                self.assertEqual(snapshot["gas_eos"], eos)
                self.assertEqual(values, before)


class GasModelQueueTests(unittest.TestCase):
    def test_unfinished_selection_can_be_saved_and_read_but_never_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as session:
                response = session.post("/api/auth/setup", json={
                    "username": "admin", "display_name": "测试管理员", "password": TEST_PASSWORD})
                self.assertEqual(response.status_code, 200, response.text)
                selections = [{"gas_eos": eos, "gas_species_policy": "full"} for eos in PR_MODELS]
                selections += [{"gas_species_policy": "pr_supported_v1"}, {"combustion_loss_model": "legacy_temperature_rise"}]
                for selection in selections:
                    values = baseline() | selection
                    with self.subTest(selection=selection), patch(
                            "lre_workbench.properties.gas_eos.build_gas",
                            side_effect=AssertionError("API must not construct thermodynamic phases")):
                        response = session.post("/api/projects", json={"name": "未完成模型设置", "inputs": values})
                        self.assertEqual(response.status_code, 200, response.text)
                        project = response.json()["project"]
                        endpoint = f"/api/projects/{project['id']}"
                        self.assertEqual(session.get(endpoint).json()["inputs"], values)
                        self.assertEqual(session.get(endpoint + "/revisions/1").json()["inputs"], values)
                        report = session.post(endpoint + "/design-readiness", json={"inputs": values})
                        self.assertEqual(report.status_code, 200, report.text)
                        self.assertFalse(report.json()["valid"])
                        response = session.post(endpoint + "/jobs", json={"revision": 1})
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertIn("原输入与历史结果保持不变", response.json()["detail"])
                        self.assertEqual(session.get("/api/jobs").json(), [])
                        after = session.get(endpoint).json()
                        self.assertEqual(after["revision"], 1)
                        self.assertEqual(after["inputs"], values)

    def test_supported_pr_choices_queue_and_reach_worker_with_exact_saved_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as session:
                session.post("/api/auth/setup", json={
                    "username": "admin", "display_name": "测试管理员", "password": TEST_PASSWORD})
                for eos in PR_MODELS:
                    for policy in ("pr_supported_v1", "auto"):
                        values = baseline() | {"gas_eos": eos, "gas_species_policy": policy}
                        before = deepcopy(values)
                        with self.subTest(eos=eos, policy=policy), patch(
                                "lre_workbench.properties.gas_eos.build_gas",
                                side_effect=AssertionError("Queue checks must not construct phases")):
                            created = session.post("/api/projects", json={"name": "物性模型研究", "inputs": values})
                            self.assertEqual(created.status_code, 200, created.text)
                            project = created.json()["project"]
                            endpoint = f"/api/projects/{project['id']}"
                            readiness = session.post(endpoint + "/design-readiness", json={"inputs": values})
                            self.assertEqual(readiness.status_code, 200, readiness.text)
                            self.assertTrue(readiness.json()["valid"])
                            self.assertIn("16", "\n".join(readiness.json()["warnings"]))
                            queued = session.post(endpoint + "/jobs", json={"revision": 1})
                            self.assertEqual(queued.status_code, 200, queued.text)
                            job_id = queued.json()["id"]
                            job = dict(app.state.store.one("SELECT * FROM jobs WHERE id=?", (job_id,)))
                            self.assertEqual(json.loads(job["inputs"]), before)
                            # Exercise the typed worker dispatch with the physics
                            # call replaced; no whole-engine solve is performed.
                            with patch("webapp.solver._calculate", return_value={"test": "accepted"}) as physics:
                                self.assertEqual(calculate_design_job(job), {"test": "accepted"})
                                self.assertEqual(physics.call_args.args[0], before)
                                self.assertIn("受限研究模型", "\n".join(physics.call_args.args[2]))
                            self.assertEqual(session.get(endpoint).json()["inputs"], before)
                            self.assertEqual(session.get(endpoint + "/revisions/1").json()["inputs"], before)
                            self.assertEqual(session.post(f"/api/jobs/{job_id}/cancel", json={}).status_code, 200)
                            self.assertEqual(values, before)

    def test_omitted_project_inputs_use_new_defaults_but_explicit_old_document_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory) / "workspace.sqlite3", queue_enabled=False)
            with client(app) as session:
                session.post("/api/auth/setup", json={
                    "username": "admin", "display_name": "测试管理员", "password": TEST_PASSWORD})
                response = session.post("/api/projects", json={"name": "新设计"})
                self.assertEqual(response.status_code, 200, response.text)
                project = response.json()["project"]
                self.assertEqual(project["inputs"]["combustion_loss_model"], "adiabatic")
                self.assertEqual(project["inputs"]["gas_species_policy"], "full")
                queued = session.post(f"/api/projects/{project['id']}/jobs", json={"revision": 1})
                self.assertEqual(queued.status_code, 200, queued.text)
                response = session.post("/api/projects", json={"name": "空白新设计", "inputs": {}})
                self.assertEqual(response.status_code, 200, response.text)
                blank = response.json()["project"]
                self.assertEqual(blank["inputs"], project["inputs"])
                queued = session.post(f"/api/projects/{blank['id']}/jobs", json={"revision": 1})
                self.assertEqual(queued.status_code, 200, queued.text)
                old = baseline()
                del old["combustion_loss_model"]
                response = session.post("/api/projects", json={"name": "旧输入文档", "inputs": old})
                self.assertEqual(response.status_code, 200, response.text)
                saved = response.json()["project"]
                self.assertEqual(saved["inputs"]["combustion_loss_model"], "legacy_temperature_rise")
                rejected = session.post(f"/api/projects/{saved['id']}/jobs", json={"revision": 1})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertIn("combustion_loss_model", rejected.json()["detail"])


if __name__ == "__main__":
    unittest.main()
