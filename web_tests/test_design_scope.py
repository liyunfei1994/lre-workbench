"""FFSC-only platform writes and execution, with lossless historical reads."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from webapp.app import create_app
from webapp.design_scope import DESIGN_SCOPE, require_supported_source
from webapp.solver import calculate, calculate_design_job, design_readiness, input_values, presets
from web_tests.test_workspace import client, TEST_PASSWORD
from lre_workbench.domain.analysis_contract import concept_design_task, concept_inputs, FFSC_INPUT_FIELDS, RETIRED_PLATFORM_FIELDS


class DesignScopeTests(unittest.TestCase):
    def test_platform_contract_removes_retired_fields_and_roundtrips_v3(self):
        from webapp.ui_schema import design_ui_schema
        from dataclasses import asdict
        from webapp.solver import EngineInputs
        old = asdict(EngineInputs())
        original = deepcopy(old)
        values, _ = input_values(old)
        self.assertEqual(set(values), FFSC_INPUT_FIELDS)
        self.assertEqual(old, original)
        task = concept_design_task(values)
        self.assertEqual(task['schema_version'], 3)
        self.assertEqual(concept_inputs(task), values)
        self.assertTrue(design_readiness(values)['valid'])
        schema = design_ui_schema()
        self.assertEqual(set(schema['defaults']), FFSC_INPUT_FIELDS)
        text = json.dumps(schema, ensure_ascii=False)
        for removed in ('"gg"', 'gg_layout', 'gas_generator', '燃气发生器', 'GG '):
            self.assertNotIn(removed, text)
        self.assertFalse(RETIRED_PLATFORM_FIELDS & set(schema['defaults']))
        # A v3 task must not accept foreign fields or an incomplete partition.
        bad = deepcopy(task)
        bad['model_configuration']['gg_layout'] = 'single_shaft'
        with self.assertRaises(ValueError):
            concept_inputs(bad)
        bad = deepcopy(task)
        bad['design_targets'].pop('pc')
        with self.assertRaises(ValueError):
            concept_inputs(bad)

    def test_execution_rejects_legacy_architecture_before_loading_physics(self):
        for value in ({"cycle_type": "gg"}, {"propellant_pair": "lox_kerosene"}):
            with self.subTest(value=value), patch("webapp.solver._calculate") as physics:
                with self.assertRaisesRegex(ValueError, "仅支持液氧甲烷"):
                    calculate(value)
                with self.assertRaisesRegex(ValueError, "仅支持液氧甲烷"):
                    calculate_design_job({"kind": "design", "inputs": json.dumps(value)})
                physics.assert_not_called()
        with self.assertRaisesRegex(ValueError, "仅支持液氧甲烷"):
            input_values({"cycle_type": "gg"})
        values, _ = input_values({})
        values["cycle_type"] = "gg"
        readiness = design_readiness(values)
        self.assertFalse(readiness["valid"])
        self.assertEqual(readiness["issues"][0]["fields"], ["cycle_type", "propellant_pair"])

    def test_source_checks_saved_calculation_identity_without_mutating_it(self):
        for result in ({"calculation": {"inputs": {"cycle_type": "gg"}}},
                       {"submitted_inputs": {"propellant_pair": "lox_kerosene"}}):
            source = {"inputs": {"cycle_type": "ffsc"}, "result": result}
            original = deepcopy(source)
            with self.assertRaisesRegex(ValueError, "仅支持液氧甲烷"):
                require_supported_source(source)
            self.assertEqual(source, original)

    def test_source_rejects_retired_equipment_even_without_saved_identity(self):
        for calculation in (
            {"components": {"gas_generator": {}}},
            {"states": {"gg_outlet": {}}},
            {"topology_nodes": [{"key": "gg_turbine"}]},
            {"topology_edges": [{"source": "fuel_pump", "target": "gas_generator"}]},
        ):
            source = {"inputs": {}, "result": {"calculation": calculation}}
            original = deepcopy(source)
            with self.subTest(calculation=calculation):
                with self.assertRaisesRegex(ValueError, "仅支持液氧甲烷"):
                    require_supported_source(source)
                self.assertEqual(source, original)
        require_supported_source({"inputs": {}, "result": {
            "calculation": {"components": {"fuel_preburner": {}}, "states": {}}}})


class DesignScopeAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name) / "workspace.sqlite3", queue_enabled=False)
        self.admin = client(self.app)
        self.addCleanup(self.admin.close)
        response = self.admin.post("/api/auth/setup", json={
            "username": "admin", "display_name": "范围测试", "password": TEST_PASSWORD})
        self.user_id = response.json()["id"]
        response = self.admin.post("/api/projects", json={"name": "FFSC", "inputs": {}})
        self.assertEqual(response.status_code, 200, response.text)
        self.project = response.json()["project"]
        self.url = "/api/projects/" + self.project["id"]
        self.store = self.app.state.store

    def archive(self, *, mutate_project=True, calculation_only=False):
        inputs = self.project["inputs"] | {"cycle_type": "gg"}
        key, now = str(uuid.uuid4()), time.time()
        result = {"calculation": {"inputs": inputs}, "historical_marker": "preserve"}
        with self.store.connect(immediate=True) as db:
            if mutate_project:
                db.execute("UPDATE projects SET inputs=? WHERE id=?", (json.dumps(inputs), self.project["id"]))
                db.execute("UPDATE revisions SET inputs=? WHERE project_id=?", (json.dumps(inputs), self.project["id"]))
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,result,kind) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (key, self.project["id"], 1, self.user_id, "succeeded",
                       json.dumps(self.project["inputs"] if calculation_only else inputs), now, json.dumps(result), "design"))
        return key, inputs, result

    def test_info_catalog_and_project_writes_are_ffsc_methane_only(self):
        info = self.admin.get("/api/info").json()
        self.assertEqual(info["design_scope"], DESIGN_SCOPE)
        self.assertTrue(info["presets"])
        self.assertTrue(all(p["inputs"]["cycle_type"] == "ffsc" and p["inputs"]["propellant_pair"] == "lox_methane"
                            for p in info["presets"]))
        self.assertTrue(all(p["cycle_type"] == "ffsc" for p in info["reference_engines"]))
        count = self.store.one("SELECT COUNT(*) AS n FROM projects")["n"]
        for inputs in ({"cycle_type": "gg"}, {"propellant_pair": "lox_kerosene"}):
            response = self.admin.post("/api/projects", json={"name": "unsupported", "inputs": inputs})
            self.assertEqual(response.status_code, 422, response.text)
            response = self.admin.put(self.url, json={"name": "unsupported", "revision": 1, "inputs": inputs})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM projects")["n"], count)
        self.assertEqual(self.admin.get(self.url).json()["inputs"], self.project["inputs"])
        response = self.admin.post(self.url + "/jobs", json={"revision": 1})
        self.assertEqual(response.status_code, 200, response.text)

    def test_legacy_project_is_read_only_even_when_new_payload_claims_ffsc(self):
        job, inputs, result = self.archive()
        for method, path, payload in (
            ("put", self.url, {"name": "converted", "revision": 1, "inputs": self.project["inputs"]}),
            ("put", self.url + "/design-reviews", {"revision": 1, "design_reviews": {}}),
            ("post", self.url + "/design-readiness", {"inputs": self.project["inputs"]}),
            ("post", self.url + "/jobs", {"revision": 1}),
        ):
            response = getattr(self.admin, method)(path, json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("历史方案仅供查看与导出", response.text)
        self.assertEqual(self.admin.get(self.url).json()["inputs"], inputs)
        self.assertEqual(self.admin.get(self.url + "/revisions/1").json()["inputs"], inputs)
        archived = self.admin.get("/api/jobs/" + job).json()
        self.assertEqual(archived["result"], result)
        self.assertIn("scope_issue", archived)
        self.assertNotIn("valve_requirements", archived)
        self.assertNotIn("turbine_geometry", archived)
        self.assertEqual(self.admin.get("/api/jobs/" + job + "/download").json()["result"], result)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM jobs")["n"], 1)

    def test_all_study_routes_reject_legacy_sources_inside_an_ffsc_project(self):
        for calculation_only in (False, True):
            job, _, _ = self.archive(mutate_project=False, calculation_only=calculation_only)
            for route in ("validations", "throttles", "transients", "feed-studies", "chilldown-studies",
                          "ignition-studies", "shutdown-studies", "cooling-designs", "fixed-steady/tasks"):
                with self.subTest(route=route, calculation_only=calculation_only):
                    response = self.admin.post(self.url + "/" + route, json={"source_job_id": job})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn("仅支持液氧甲烷", response.text)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM jobs")["n"], 2)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM fixed_steady_tasks")["n"], 0)

    def test_nested_fixed_and_trim_writes_reject_archived_task_sources(self):
        job, _, _ = self.archive(mutate_project=False)
        candidate = str(uuid.uuid4())
        self.store.execute("INSERT INTO fixed_steady_tasks(id,project_id,source_job_id,submitted_by,created,task) "
                           "VALUES(?,?,?,?,?,?)", (candidate, self.project["id"], job, self.user_id, time.time(), "{}"))
        path = self.url + "/fixed-steady/tasks/" + candidate
        for route in ("data-check", "framework-checks", "run", "trim/definitions", "trim/checks", "trim/run"):
            with self.subTest(route=route):
                response = self.admin.post(path + "/" + route, json={})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("仅支持液氧甲烷", response.text)
        self.assertEqual(self.admin.get(path + "/download").status_code, 200)
