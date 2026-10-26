"""Numerical acceptance cannot be supplied by an inner residual alone."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from convergence import attach_convergence_status, numerical_convergence
from web_tests.convergence_fixture import converged_evidence


class ConvergenceContractTests(unittest.TestCase):
    def result(self):
        return SimpleNamespace(summary={"solver_residual_norm": 1e-5}, convergence=converged_evidence())

    def test_unified_feedback_requires_all_six_original_physical_tolerances(self):
        from regen_coupling import coupling_checks, COUPLING_CRITERIA
        r = self.result()
        thermal = r.convergence["layers"]["thermal"]
        thermal.update(source="regen_quasi1d_ph_v1", residual=1e-5, tolerance=.001,
                       checks=coupling_checks({name:limit/2 for name,limit,_ in COUPLING_CRITERIA}))
        self.assertEqual(numerical_convergence(r)["status"], "pass")
        original = deepcopy(thermal)
        thermal["checks"].pop()
        self.assertEqual(numerical_convergence(r)["status"], "unknown")
        r.convergence["layers"]["thermal"] = deepcopy(original)
        check = r.convergence["layers"]["thermal"]["checks"][0]
        check.update(residual=.6, tolerance=1e6)
        self.assertEqual(numerical_convergence(r)["status"], "fail")

    def test_all_layers_required_and_independently_checked(self):
        self.assertEqual(numerical_convergence(self.result())["status"], "pass")
        for key in ("inner", "thermal", "preburner", "continuation"):
            with self.subTest(key=key):
                r = self.result()
                r.convergence["layers"][key]["residual"] = 2 * r.convergence["layers"][key]["tolerance"]
                self.assertEqual(numerical_convergence(r)["status"], "fail")
                del r.convergence["layers"][key]
                self.assertEqual(numerical_convergence(r)["status"], "unknown")

    def test_nonfinite_negative_and_missing_evidence_never_pass(self):
        for bad in (float("nan"), float("inf"), None, -1, True):
            r = self.result()
            r.convergence["layers"]["thermal"]["checks"][1]["residual"] = bad
            self.assertEqual(numerical_convergence(r)["status"], "unknown")
        r = self.result()
        r.convergence["layers"]["thermal"]["checks"][2]["residual"] = .1
        self.assertEqual(numerical_convergence(r)["status"], "fail")

    def test_disabled_layer_must_be_explicit(self):
        r = self.result()
        r.convergence["layers"]["preburner"] = dict(enabled=False, status="disabled")
        self.assertEqual(numerical_convergence(r)["status"], "pass")
        r.convergence["layers"]["preburner"] = dict(enabled=False, status="pass")
        self.assertEqual(numerical_convergence(r)["status"], "unknown")

    def test_raw_inner_solve_and_missing_feedback_checks_are_not_complete(self):
        r = self.result()
        r.convergence["layers"]["continuation"] = dict(enabled=False, status="disabled")
        self.assertEqual(numerical_convergence(r)["status"], "unknown")
        r = self.result()
        del r.convergence["layers"]["thermal"]["checks"]
        self.assertEqual(numerical_convergence(r)["status"], "unknown")

    def test_legacy_inner_success_cannot_hide_14K_thermal_failure(self):
        old = {"calculation": {"summary": {"solver_residual_norm": 3.2e-5,
                "tc_outer_residual_K": 14.0825366}}, "assessment": {"numerical": {"status": "pass"}}}
        before = deepcopy(old)
        self.assertEqual(numerical_convergence(old)["status"], "fail")
        self.assertEqual(old, before)
        old["calculation"]["summary"]["tc_outer_residual_K"] = 1.
        self.assertEqual(numerical_convergence(old)["status"], "unknown")
        old["calculation"]["summary"]["gg_temp_outer_error_K"] = -4.
        self.assertEqual(numerical_convergence(old)["status"], "fail")

    def test_final_warning_is_idempotent_and_summary_cross_checked(self):
        r = self.result()
        r.convergence["layers"]["preburner"]["status"] = "fail"
        attach_convergence_status(r)
        before = deepcopy(r)
        attach_convergence_status(r)
        self.assertEqual(r, before)
        self.assertEqual(r.summary["inner_solver_warning"], 0)
        self.assertEqual(r.summary["solver_warning"], 1)
        r = self.result()
        r.summary["solver_residual_norm"] = 5e-4
        self.assertEqual(numerical_convergence(r)["status"], "fail")

    def test_input_required_layers_cannot_be_disabled_by_metadata(self):
        for name, inputs in (("thermal", {"cooling_bartz_mode": 1}),
                             ("thermal", {"cooling_bartz_mode": .2}),
                             ("preburner", {"use_target_preburner_temp": 1}),
                             ("preburner", {"cycle_type": "gg", "use_target_gg_temp": 1})):
            r = self.result()
            r.inputs = inputs
            r.convergence["layers"][name] = dict(enabled=False, status="disabled")
            self.assertEqual(numerical_convergence(r)["status"], "fail")

    def test_continuation_cannot_claim_success_at_an_intermediate_stage(self):
        for update in (dict(reached_ratio=.5), dict(termination_reason="max_iterations")):
            r = self.result()
            r.convergence["layers"]["continuation"].update(update)
            self.assertEqual(numerical_convergence(r)["status"], "fail")

    def test_attach_preserves_unknown_when_evidence_is_missing(self):
        r = self.result()
        del r.convergence["layers"]["thermal"]["checks"]
        attach_convergence_status(r)
        self.assertEqual(r.convergence["status"], "unknown")
        self.assertEqual(numerical_convergence(r)["status"], "unknown")
        self.assertEqual(r.summary["solver_evidence_incomplete"], 1)


if __name__ == "__main__":
    unittest.main()
