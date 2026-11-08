"""Task-mode boundaries must stop before any design or sizing calculation."""
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from webapp.solver import EngineInputs, calculate, calculate_task, calculate_design_job
from lre_workbench.domain.analysis_contract import concept_design_task


class AnalysisDispatchTests(unittest.TestCase):
    def setUp(self):
        self.values = asdict(EngineInputs())
        self.task = concept_design_task(self.values)

    def test_explicit_fixed_mode_never_enters_design(self):
        with patch("webapp.solver._calculate") as design:
            with self.assertRaises(ValueError):
                calculate(self.values, mode="fixed_hardware_steady")
            with self.assertRaises(ValueError):
                calculate_task({"mode": "fixed_hardware_steady", "readiness": {"can_run": True}})
            design.assert_not_called()

    def test_unknown_task_mode_never_enters_design(self):
        with patch("webapp.solver.calculate") as design:
            for mode in (None, "fixed_hardware_transient", "typo"):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    calculate_task(self.task | {"mode": mode})
            design.assert_not_called()

    def test_design_task_roundtrip_and_legacy_job(self):
        job = {"kind": "design", "inputs": json.dumps(self.values),
               "analysis_task": json.dumps(self.task)}
        with patch("webapp.solver.calculate", return_value={"ok": True}) as design:
            self.assertEqual(calculate_design_job(job), {"ok": True})
            design.assert_called_once_with(self.values, None, saved_snapshot=True, mode="concept_design")
            design.reset_mock()
            calculate_design_job(job | {"analysis_task": None})
            design.assert_called_once_with(self.values, None, saved_snapshot=True)

    def test_mismatched_or_cross_mode_job_is_rejected(self):
        job = {"kind": "design", "inputs": json.dumps(self.values),
               "analysis_task": json.dumps(self.task)}
        mismatched = self.values | {"thrust": self.values["thrust"] * .5}
        with patch("webapp.solver.calculate") as design:
            for record in (job | {"kind": "fixed_hardware_steady"},
                           job | {"kind": "unknown"},
                           job | {"inputs": json.dumps(mismatched)},
                           job | {"analysis_task": json.dumps({"mode": "fixed_hardware_steady"})}):
                with self.subTest(record=record), self.assertRaises(ValueError):
                    calculate_design_job(record)
            design.assert_not_called()

    def test_older_typed_snapshot_without_new_tolerances_remains_readable(self):
        from copy import deepcopy
        original = deepcopy(self.task)
        values = dict(self.values)
        for key in ("steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"):
            del original["solver_settings"][key]
            del values[key]
        from tests.test_analysis_contract import legacy_task
        for task in (original, legacy_task(values)):
            job = {"kind": "design", "inputs": json.dumps(values), "analysis_task": json.dumps(task)}
            before = deepcopy(job)
            with self.subTest(version=task["schema_version"]), \
                    patch("webapp.solver._calculate", return_value={"ok": True}) as solve:
                self.assertEqual(calculate_design_job(job), {"ok": True})
                used = solve.call_args.args[0]
                self.assertEqual(used["steady_ox_power_tolerance_W"], 1.0)
                self.assertEqual(used["steady_fuel_power_tolerance_W"], 1.0)
            self.assertEqual(job, before)

    def test_compatibility_does_not_accept_other_missing_fields_or_partial_new_settings(self):
        from copy import deepcopy
        from lre_workbench.domain.analysis_contract import concept_inputs
        for missing in (("steady_ox_power_tolerance_W",), ("nonlinear_solver",),
                        ("steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W", "nonlinear_solver")):
            task = deepcopy(self.task)
            for key in missing:
                del task["solver_settings"][key]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                concept_inputs(task)


if __name__ == "__main__":
    unittest.main()
