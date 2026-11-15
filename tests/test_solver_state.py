"""Saved anonymous vectors cannot cross solver parameterizations."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import math
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.solvers.state import ffsc_design_vector_schema, ffsc_warm_state_compatible
from lre_workbench.domain.models import EngineInputs
from lre_workbench.systems.ffsc import FFSCCycleModel
from tests.test_ffsc_convergence import inner_result


def state(target=True):
    return {"vector_schema": ffsc_design_vector_schema(target_mixture_ratio=target),
            "x": [100., 2., 3.], "chamber_temp_guess": 3500., "chamber_gamma": 1.2, "molar_mass": 22.}


class SolverStateTests(unittest.TestCase):
    def test_mode_identity_is_required_in_both_directions_without_migration(self):
        for target in (True, False):
            source = state(target)
            before = deepcopy(source)
            self.assertTrue(ffsc_warm_state_compatible(source, target_mixture_ratio=target))
            self.assertFalse(ffsc_warm_state_compatible(source, target_mixture_ratio=not target))
            self.assertEqual(source, before)
            anonymous = deepcopy(source)
            anonymous.pop("vector_schema")
            self.assertFalse(ffsc_warm_state_compatible(anonymous, target_mixture_ratio=target))

    def test_semantic_fields_and_nonfinite_or_nonnumeric_values_are_not_accepted(self):
        for key, value in (("version", 2), ("version", True), ("analysis_mode", "fixed_hardware_steady"),
                           ("solver_family", "other_solver"), ("variable_units", ["kg/s"] * 3),
                           ("variable_names", list(reversed(state()["vector_schema"]["variable_names"])))):
            invalid = state()
            invalid["vector_schema"][key] = value
            self.assertFalse(ffsc_warm_state_compatible(invalid, target_mixture_ratio=True))
        for value in (True, "2", 0, -1, math.inf, math.nan, None, 10**1000):
            invalid = state()
            invalid["x"][1] = value
            self.assertFalse(ffsc_warm_state_compatible(invalid, target_mixture_ratio=True))

    def test_rejected_vector_uses_cold_path_and_preserves_inputs(self):
        for target in (True, False):
            model = FFSCCycleModel(EngineInputs(use_target_preburner_temp=0,
                                  use_target_preburner_mr=int(target), cooling_bartz_mode=0))
            original = asdict(model.inputs)
            for incoming in (state(not target), {key: value for key, value in state(target).items()
                                               if key != "vector_schema"}):
                before = deepcopy(incoming)
                starts = []
                def solve(**kwargs):
                    starts.append((deepcopy(kwargs["x_init"]), deepcopy(model._convergence_context)))
                    return inner_result(model)
                with patch.object(model, "solve_cycle", side_effect=solve):
                    result = model._evaluate_design_core(warm_state=incoming)
                self.assertIsNone(starts[0][0])
                self.assertEqual(starts[0][1]["stage_ratio"], .5)
                self.assertFalse(any(context["warm_start"] for _, context in starts))
                self.assertEqual(result.convergence["warm_start"], {
                    "requested": True, "accepted": False, "fallback": True,
                    "rejection_reason": "incompatible_or_unversioned_vector"})
                self.assertEqual(incoming, before)
                self.assertEqual(asdict(model.inputs), original)
                self.assertEqual(result.convergence["solution"]["vector_schema"],
                                 ffsc_design_vector_schema(target_mixture_ratio=target))

    def test_compatible_vector_retains_existing_warm_path(self):
        model = FFSCCycleModel(EngineInputs(use_target_preburner_temp=0, cooling_bartz_mode=0))
        supplied = state()
        with patch.object(model, "solve_cycle", side_effect=lambda **kwargs: inner_result(model)) as solve:
            result = model._evaluate_design_core(warm_state=supplied)
        self.assertEqual(solve.call_count, 1)
        self.assertEqual(solve.call_args.kwargs["x_init"], supplied["x"])
        self.assertEqual(result.convergence["warm_start"], {"requested": True, "accepted": True, "fallback": False})


if __name__ == "__main__":
    unittest.main()
