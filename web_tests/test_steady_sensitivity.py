"""Analytic fixed-throat fixtures, not validation data for a real engine."""
from copy import deepcopy
import json
import unittest

from webapp.steady_sensitivity import throttle_response_gains


def scan():
    points = []
    for index, ratio in enumerate((.4, .6, .8, 1.0), 1):
        points.append({
            "index": index, "throttle_ratio": ratio, "pc_Pa": 10e6 * ratio,
            "converged": True,
            "summary": {"thrust_N": 1e6 * ratio, "Isp_s": 300 + 20 * ratio,
                        "mdot_total_kg_s": 300 * ratio * ratio,
                        "ox_preburner_T_combustion_K": 750.0},
            "assessment": {"numerical": {"status": "pass"}, "status": "pass"},
            "geometry": {"status": "pass", "throat_area_m2": .02},
        })
    return {"request": {"throttle_min": .4, "throttle_steps": 4},
            "baseline": {"throat_area_m2": .02}, "points": points, "complete": True}


def metric(result, key):
    return next(row for row in result["metrics"] if row["key"] == key)


class SteadySensitivityTests(unittest.TestCase):
    def test_linear_quadratic_and_constant_outputs_have_analytic_gains(self):
        source = scan()
        before = deepcopy(source)
        result = throttle_response_gains(source)
        self.assertEqual(result["status"], "available")
        for key in ("thrust_N", "pc_Pa"):
            self.assertAlmostEqual(metric(result, key)["near_nominal_gain"], 1.0)
        self.assertAlmostEqual(metric(result, "Isp_s")["near_nominal_gain"], 20 / 320)
        quadratic = metric(result, "mdot_total_kg_s")
        for actual, expected in zip([s["gain"] for s in quadratic["segments"]], (1, 1.4, 1.8)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(metric(result, "ox_preburner_T_combustion_K")["gain_max"], 0)
        self.assertEqual(before, source)
        json.dumps(result, allow_nan=False)

    def test_invalid_inner_point_breaks_both_intervals_without_gap_bridging(self):
        source = scan()
        source["points"][1]["converged"] = False
        result = throttle_response_gains(source)
        slopes = metric(result, "thrust_N")["segments"]
        self.assertEqual([s["gain"] is None for s in slopes], [True, True, False])
        self.assertEqual(result["status"], "partial")
        self.assertEqual([(s["from_index"], s["to_index"]) for s in slopes], [(1, 2), (2, 3), (3, 4)])

    def test_area_or_numerical_failure_is_rejected_even_when_converged_flag_is_true(self):
        for mutation in (lambda p: p["geometry"].update(throat_area_m2=.021),
                         lambda p: p["assessment"]["numerical"].update(status="fail")):
            source = scan()
            mutation(source["points"][2])
            result = throttle_response_gains(source)
            self.assertIsNone(metric(result, "thrust_N")["near_nominal_gain"])
            self.assertEqual(metric(result, "thrust_N")["segments"][1]["status"], "unavailable")

    def test_missing_or_duplicate_points_and_wrong_ratios_are_not_selected(self):
        variants = []
        missing = scan(); del missing["points"][1]; variants.append(missing)
        duplicate = scan(); duplicate["points"].append(deepcopy(duplicate["points"][1])); variants.append(duplicate)
        wrong = scan(); wrong["points"][1]["throttle_ratio"] = .65; variants.append(wrong)
        for source in variants:
            result = throttle_response_gains(source)
            self.assertEqual([s["gain"] is None for s in metric(result, "pc_Pa")["segments"]], [True, True, False])

    def test_no_nominal_point_no_normalization(self):
        source = scan()
        source["points"].pop()
        result = throttle_response_gains(source)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("100%", result["reason"])
        self.assertEqual(result["metrics"], [])
        source = scan(); source["points"][-1]["converged"] = False
        self.assertEqual(throttle_response_gains(source)["status"], "unavailable")

    def test_system_failures_remain_diagnostic_and_nominal_failure_marks_all_intervals(self):
        source = scan(); source["points"][1]["assessment"]["status"] = "fail"
        result = throttle_response_gains(source)
        self.assertEqual([s["status"] for s in result["intervals"]], ["review", "review", "pass"])
        self.assertAlmostEqual(metric(result, "thrust_N")["segments"][0]["gain"], 1)
        source = scan(); source["points"][-1]["assessment"]["status"] = "fail"
        result = throttle_response_gains(source)
        self.assertEqual(result["reference"]["status"], "review")
        self.assertTrue(all(s["status"] == "review" for s in result["intervals"]))

    def test_missing_metric_does_not_destroy_other_metrics_or_create_zero(self):
        source = scan(); source["points"][1]["summary"]["Isp_s"] = None
        source["points"][-1]["summary"]["ox_preburner_T_combustion_K"] = 0
        result = throttle_response_gains(source)
        self.assertAlmostEqual(metric(result, "pc_Pa")["gain_min"], 1)
        self.assertIsNone(metric(result, "Isp_s")["segments"][0]["gain"])
        self.assertIsNone(metric(result, "ox_preburner_T_combustion_K")["gain_max"])
        self.assertNotIn("gg_T_combustion_K", [row["key"] for row in result["metrics"]])

    def test_nonfinite_values_and_partial_ordering_remain_json_safe(self):
        source = scan(); source["points"][0]["summary"]["Isp_s"] = float("nan")
        source["points"][1]["summary"]["mdot_total_kg_s"] = float("inf")
        source["points"].reverse()
        result = throttle_response_gains(source)
        self.assertAlmostEqual(metric(result, "thrust_N")["near_nominal_gain"], 1)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
