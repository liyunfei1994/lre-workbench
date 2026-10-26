"""Downstream analyses must not inherit an old or partial numerical pass."""
from copy import deepcopy
import unittest

from web_tests.test_throttle_analysis import source_fixture as throttle_source
from web_tests.test_transient_physics import source_fixture as transient_source
from webapp.throttle import source_baseline
from webapp.transient import _source
from webapp.validation_checks import _source_checks


class ConvergenceGateTests(unittest.TestCase):
    def test_outer_failure_blocks_throttle_and_transient_despite_inner_pass(self):
        for fixture, gate in ((throttle_source, source_baseline), (transient_source, _source)):
            source = fixture()
            source["result"]["calculation"]["convergence"]["layers"]["thermal"]["residual"] = 14.083
            before = deepcopy(source)
            with self.assertRaisesRegex(ValueError, "完整数值收敛"):
                gate(source)
            self.assertEqual(source, before)

    def test_legacy_without_complete_evidence_cannot_be_used_as_baseline(self):
        for fixture, gate in ((throttle_source, source_baseline), (transient_source, _source)):
            source = fixture()
            del source["result"]["calculation"]["convergence"]
            with self.assertRaisesRegex(ValueError, "分层证据"):
                gate(source)

    def test_validation_recomputes_complete_status_without_rewriting_source(self):
        result = throttle_source()["result"]
        result["assessment"] = {"status": "pass", "numerical": {"status": "pass"}}
        result["calculation"]["convergence"]["layers"]["continuation"].update(status="fail", residual=.5)
        before = deepcopy(result)
        self.assertEqual(_source_checks(result, result["calculation"]["summary"])[0]["status"], "fail")
        self.assertEqual(result, before)
        del result["calculation"]["convergence"]
        self.assertEqual(_source_checks(result, result["calculation"]["summary"])[0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
