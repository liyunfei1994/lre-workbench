"""Immutable-source boundary tests, independent of the operating physics tests."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.operating_throttle import configuration, normalize_request, calculate_operating_throttle
from webapp.validation import fingerprint
from web_tests.test_throttle_analysis import source_fixture


def source():
    value = source_fixture()
    value["result"]["calculation"]["summary"]["thrust_N"] = 123456.
    return value


def setup(calculation):
    return {"ready": True, "issues": [], "baseline": {"thrust_N": calculation["summary"]["thrust_N"]},
            "model": {"id": "fixed_geometry_local_throttle_v1"}, "limitations": ["局部研究模型"]}


class OperatingThrottleBoundaryTests(unittest.TestCase):
    def setUp(self):
        core = patch("lre_workbench.operations.throttle.configuration", side_effect=setup)
        core.start()
        self.addCleanup(core.stop)

    def test_actual_thrust_and_saved_geometry_have_independent_provenance(self):
        saved = source()
        saved["inputs"]["thrust"] = 9e9
        saved["result"]["pump_geometry"] = {"saved": "reference mesh"}
        before = deepcopy(saved)
        result = configuration(saved)
        self.assertTrue(result["ready"])
        self.assertEqual(result["baseline"]["thrust_N"], 123456.)
        self.assertEqual(result["hardware"]["sha256"], fingerprint(result["hardware"]["payload"]))
        self.assertEqual(result["hardware"]["payload"]["saved_geometry"]["pump_geometry"], {"saved": "reference mesh"})
        self.assertEqual(result["source"]["result_sha256"], fingerprint(saved["result"]))
        result["hardware"]["payload"]["feed"]["branches"].clear()
        self.assertEqual(saved, before)

    def test_missing_reference_geometry_and_failed_source_cannot_fall_back(self):
        for mutation in (lambda c: c["summary"].pop("thrust_N"),
                         lambda c: c["components"]["nozzle"]["outputs"].update(At_m2=0),
                         lambda c: c["steady_closure"]["feed_network"].pop("hardware_snapshot")):
            saved = source()
            mutation(saved["result"]["calculation"])
            self.assertFalse(configuration(saved)["ready"])
            with self.assertRaises(ValueError):
                normalize_request({"source_job_id": "design"}, saved)

    def test_request_cannot_redefine_hardware_preburner_temperature_or_solver(self):
        saved = source()
        self.assertEqual(normalize_request({"source_job_id": "design"}, saved),
                         {"source_job_id": "design", "throttle_min": .9, "throttle_max": 1., "throttle_steps": 5})
        for extra in ({"hardware": {}}, {"inputs": {}}, {"ox_preburner_temp_target": 700},
                      {"mode": "concept_design"}, {"throttle_min": True}, {"throttle_steps": 2.5},
                      {"throttle_steps": 16}, {"throttle_min": float("nan")}, {"throttle_max": 1.1},
                      {"throttle_min": .9, "throttle_max": .8}, {"throttle_steps": 10**1000}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                normalize_request({"source_job_id": "design", **extra}, saved)
        self.assertEqual(normalize_request({"source_job_id": "design", "throttle_min": .05}, saved)["throttle_min"], .05)

    def test_saved_calculation_is_isolated_and_partials_are_bound(self):
        saved = source()
        before = deepcopy(saved)
        partial = {"schema_version": 1, "analysis_mode": "fixed_geometry_local_throttle", "points": [], "complete": False}
        def solve(calculation, request, progress):
            self.assertEqual(calculation, saved["result"]["calculation"])
            self.assertNotIn("ox_preburner_temp_target", request)
            progress({"completed": 0, "maximum": 5}, partial)
            calculation["inputs"]["thrust"] = -1
            return {**partial, "complete": True}
        seen = []
        with patch("lre_workbench.operations.throttle.calculate", side_effect=solve):
            result = calculate_operating_throttle({"source_job_id": "design"}, saved, lambda a, b: seen.append(b))
        self.assertEqual(saved, before)
        self.assertEqual(seen[0]["hardware"], result["hardware"])
        self.assertEqual(seen[0]["source"], result["source"])
        self.assertEqual(result["request"]["throttle_max"], 1.)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
