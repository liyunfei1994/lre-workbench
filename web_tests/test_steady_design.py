from copy import deepcopy
import json
import unittest

from tests.test_steady_closure import balanced_loop
from web_tests.convergence_fixture import converged_evidence
from webapp.steady_design import steady_design_view


def fixture():
    states, components = balanced_loop()
    return {"calculation": {"inputs": {"cycle_type": "ffsc", "propellant_pair": "lox_methane"},
                            "states": states, "components": components, "summary": {},
                            "convergence": converged_evidence(),
                            "steady_closure": {"energy_balance": {"status": "pass", "residual_W": 0}}},
            "assessment": {"checks": [{"label": "暂定温限", "status": "pass", "value": 750,
                                       "limit": 800, "limit_status": "provisional", "criterion": "maximum"}]}}


def gates(report):
    return {g["id"]: g["status"] for g in report["gates"]}


class SteadyDesignTests(unittest.TestCase):
    def test_projection_is_immutable_and_separates_closure_from_validation(self):
        r = fixture(); before = deepcopy(r)
        v = steady_design_view(r, "record-a")
        self.assertEqual(r, before)
        self.assertEqual(v["record_id"], "record-a")
        self.assertEqual(gates(v)["energy"], "pass")
        self.assertEqual(gates(v)["constraints"], "review")
        self.assertEqual(gates(v)["handoff"], "review")
        self.assertEqual(v["validation_status"], "not_certified")
        self.assertEqual(v["status"], "review_required")
        json.dumps(v, allow_nan=False)

    def test_energy_counterexample_ignores_saved_pass(self):
        r = fixture()
        r["calculation"]["states"]["fuel_after_cooling"]["enthalpy"] += 100
        v = steady_design_view(r)
        self.assertEqual(gates(v)["energy"], "fail")
        self.assertEqual(v["status"], "blocked")

    def test_absent_models_do_not_borrow_current_defaults(self):
        v = steady_design_view(fixture())
        self.assertTrue(all(m["value"] == "未记录" for m in v["models"]))
        self.assertEqual(gates(v)["range"], "review")

    def test_unset_constraint_and_stale_pass_remain_visible(self):
        r = fixture()
        r["assessment"]["checks"].append({"label": "壁温", "status": "pass", "value": 1200, "limit": None})
        self.assertEqual(gates(steady_design_view(r))["constraints"], "review")
        r["assessment"]["checks"][0]["value"] = 900
        self.assertEqual(gates(steady_design_view(r))["constraints"], "fail")

    def test_explicit_range_violation_and_conflict_are_not_green(self):
        r = fixture()
        r["calculation"]["thermodynamic_applicability"] = {"states": [{"T_K": 5200,
            "thermo_range": {"valid_temperature_min_K": 300, "valid_temperature_max_K": 5000, "outside_mass_fraction": 0}}]}
        v = steady_design_view(r, consistency={"status": "pass", "conflict_count": 1})
        self.assertEqual(gates(v)["range"], "fail")
        self.assertEqual(gates(v)["consistency"], "fail")

    def test_other_cycles_and_absent_calculation_have_no_ffsc_claim(self):
        self.assertEqual(steady_design_view(None)["status"], "unavailable")
        r = fixture(); r["calculation"]["inputs"]["cycle_type"] = "gg"
        self.assertEqual(steady_design_view(r)["status"], "not_applicable")

    def test_pure_fluid_range_evidence_overrides_stale_pass(self):
        r = fixture()
        row = {"T_K": 100, "p_Pa": 20e6, "limits": {"Tmin_K": 90, "Tmax_K": 500, "pmax_Pa": 10e6},
               "status": "within_declared_ranges", "phase": "liquid"}
        r["calculation"]["thermodynamic_applicability"] = {"pure_fluid_states": [row]}
        self.assertEqual(gates(steady_design_view(r))["range"], "fail")
        row.update(p_Pa=1e6, phase="twophase")
        self.assertEqual(gates(steady_design_view(r))["range"], "fail")

    def test_handoff_cannot_drift_from_canonical_state(self):
        r = fixture()
        r["calculation"]["states"]["main_chamber"]["temperature"] = 3700
        r["gas_handoff"] = {"status": "complete", "stations": [{"id": "main_chamber", "T_K": -1,
            "checks": [{"status": "pass"}]}]}
        self.assertEqual(gates(steady_design_view(r))["handoff"], "fail")


if __name__ == "__main__":
    unittest.main()
