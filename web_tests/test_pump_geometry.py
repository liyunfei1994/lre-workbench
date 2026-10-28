"""Concept pump geometry is continuous, record-bound and never silently clipped."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from geometry.pumps import estimate_pump
from webapp.pump_geometry import G, pump_geometry_view


def fixture(q=.2, rho=1000., dp=36e6, rpm=20000., stages=2, psi=.5):
    head = dp / rho / G
    u2 = math.sqrt(G * head / stages / psi)
    outputs = {"volume_flow_m3s": q, "rho_in_kgm3": rho, "delta_p_Pa": dp,
               "nr_rpm": rpm, "n_stages": stages, "psi_head_coefficient": psi,
               "head_m": head, "head_m_per_stage": head / stages,
               "u_tip_est_ms": u2, "d_impeller_est_m": 60 * u2 / (math.pi * rpm),
               "specific_speed_Ns": rpm * math.sqrt(q) / (head / stages)**.75,
               "p_in_MPa": 1., "p_out_MPa": 1. + dp / 1e6}
    inputs = {"propellant_pair": "lox_methane"}
    for prefix in ("lox", "fuel"):
        inputs.update({f"{prefix}_pump_inlet_flow_coefficient": .10,
                       f"{prefix}_pump_outlet_flow_coefficient": .08,
                       f"{prefix}_pump_eye_hub_ratio": .35,
                       f"{prefix}_pump_axial_length_ratio": .3,
                       f"{prefix}_pump_head_coefficient_psi": psi,
                       f"{prefix}_pump_n_stages": stages})
    return {"calculation": {"inputs": inputs, "components": {
        "ox_pump": {"outputs": deepcopy(outputs)}, "fuel_pump": {"outputs": deepcopy(outputs)}}}}


class PumpGeometryTests(unittest.TestCase):
    def test_two_pumps_have_saved_point_dimensions_and_continuous_meridional_surfaces(self):
        data = fixture()
        before = deepcopy(data)
        view = pump_geometry_view(data, "point-1")
        self.assertEqual(view["schema_version"], 1)
        for side in ("ox", "fuel"):
            p = view["pumps"][side]
            self.assertTrue(p["available"], p)
            self.assertEqual(p["source"]["record_id"], "point-1")
            d, m = p["dimensions"], p["meridional"]
            self.assertEqual(len(m["hub"]), 65)
            self.assertEqual(len(m["shroud"]), 65)
            self.assertEqual(m["hub"][0], [0., d["eye_hub_diameter_m"] / 2])
            self.assertEqual(m["shroud"][0], [0., d["eye_shroud_diameter_m"] / 2])
            for wall in ("hub", "shroud"):
                self.assertAlmostEqual(m[wall][-1][1], d["impeller_diameter_m"] / 2)
                self.assertTrue(all(b[0] > a[0] and b[1] > a[1]
                                    for a, b in zip(m[wall], m[wall][1:])))
            self.assertAlmostEqual(m["hub"][-1][0] - m["shroud"][-1][0], d["outlet_width_m"])
            self.assertEqual(p["checks"][-1]["status"], "not_evaluated")
        self.assertEqual(data, before)
        json.dumps(view, allow_nan=False)

    def test_continuity_closes_at_actual_inlet_annulus_and_radial_outlet(self):
        p = pump_geometry_view(fixture())["pumps"]["ox"]
        d = p["dimensions"]
        q = p["design_point"]["volume_flow_m3_s"]
        q1 = math.pi * (d["eye_shroud_diameter_m"]**2 - d["eye_hub_diameter_m"]**2) / 4 * d["inlet_meridional_velocity_m_s"]
        q2 = math.pi * d["impeller_diameter_m"] * d["outlet_width_m"] * d["outlet_meridional_velocity_m_s"]
        self.assertAlmostEqual(q1, q)
        self.assertAlmostEqual(q2, q)

    def test_similarity_scaling_changes_all_lengths_consistently(self):
        a = pump_geometry_view(fixture())["pumps"]["ox"]
        scale = 2.5
        b = pump_geometry_view(fixture(q=.2 * scale**2, rpm=20000 / scale))["pumps"]["ox"]
        self.assertTrue(b["available"], b)
        for key, value in a["dimensions"].items():
            if key.endswith("_m"):
                self.assertAlmostEqual(b["dimensions"][key], value * scale)
            elif key.endswith("_m2"):
                self.assertAlmostEqual(b["dimensions"][key], value * scale**2)
        for wall in ("hub", "shroud"):
            for old, new in zip(a["meridional"][wall], b["meridional"][wall]):
                for x, y in zip(old, new):
                    self.assertAlmostEqual(y, x * scale)

    def test_diameter_uses_each_stage_head_not_total_head(self):
        one = pump_geometry_view(fixture(stages=1))["pumps"]["ox"]
        two = pump_geometry_view(fixture(stages=2))["pumps"]["ox"]
        self.assertTrue(one["available"], one)
        self.assertAlmostEqual(one["dimensions"]["impeller_diameter_m"] / two["dimensions"]["impeller_diameter_m"], math.sqrt(2))
        self.assertNotIn("total_assembly_length_m", two["dimensions"])
        self.assertEqual(two["assumptions"]["stage_model"], "equal_head_representative_stage")

    def test_cfturbo_factor_two_is_explicit_not_a_different_hydraulic_design(self):
        p = pump_geometry_view(fixture(psi=.6))["pumps"]["ox"]
        self.assertTrue(p["available"], p)
        self.assertEqual(p["assumptions"]["head_coefficient_psi"], .6)
        self.assertEqual(p["assumptions"]["cfturbo_work_coefficient"], 1.2)
        self.assertEqual(p["assumptions"]["inlet_flow_coefficient"], .1)
        self.assertAlmostEqual(p["dimensions"]["inlet_meridional_velocity_m_s"], .1 * p["design_point"]["tip_speed_m_s"])

    def test_old_records_retain_saved_point_but_do_not_get_current_default_curves(self):
        result = fixture()
        del result["calculation"]["inputs"]["lox_pump_inlet_flow_coefficient"]
        before = deepcopy(result)
        p = pump_geometry_view(result)["pumps"]["ox"]
        self.assertFalse(p["available"])
        self.assertIn("旧记录", p["reason"])
        self.assertEqual(p["design_point"]["volume_flow_m3_s"], .2)
        self.assertEqual(set(p["dimensions"]), {"impeller_diameter_m"})
        self.assertNotIn("meridional", p)
        self.assertEqual(result, before)

    def test_frozen_snapshot_is_unaffected_by_later_draft_or_inputs(self):
        result = fixture()
        result["pump_geometry"] = pump_geometry_view(result, "old-id")
        expected = deepcopy(result["pump_geometry"]["pumps"]["ox"])
        result["calculation"]["inputs"].update(lox_pump_inlet_flow_coefficient=.9, lox_pump_head_coefficient_psi=.7)
        result["calculation"]["components"]["ox_pump"]["outputs"]["nr_rpm"] = 1
        p = pump_geometry_view(result, "new-id")["pumps"]["ox"]
        expected["source"]["record_id"] = "new-id"
        self.assertEqual(p, expected)
        p["meridional"]["hub"][0][1] = 999
        self.assertNotEqual(result["pump_geometry"]["pumps"]["ox"]["meridional"]["hub"][0][1], 999)

    def test_broken_snapshot_is_not_rebuilt_from_inputs(self):
        result = fixture()
        result["pump_geometry"] = pump_geometry_view(result)
        result["pump_geometry"]["pumps"]["ox"]["meridional"]["hub"][1][1] *= 2
        p = pump_geometry_view(result)["pumps"]["ox"]
        self.assertFalse(p["available"])
        self.assertIn("校验值", p["reason"])
        self.assertTrue(pump_geometry_view(result)["pumps"]["fuel"]["available"])

    def test_inconsistent_system_results_are_rejected_not_repaired(self):
        for key in ("head_m", "head_m_per_stage", "u_tip_est_ms", "d_impeller_est_m", "specific_speed_Ns", "p_out_MPa"):
            result = fixture()
            result["calculation"]["components"]["ox_pump"]["outputs"][key] *= 1.1
            p = pump_geometry_view(result)["pumps"]["ox"]
            self.assertFalse(p["available"], key)
            self.assertIn("不一致", p["reason"])
            self.assertNotIn("meridional", p)
        result = fixture()
        result["calculation"]["inputs"]["lox_pump_head_coefficient_psi"] = .6
        self.assertFalse(pump_geometry_view(result)["pumps"]["ox"]["available"])

    def test_missing_or_invalid_saved_points_do_not_invent_dimensions(self):
        for key in ("rho_in_kgm3", "volume_flow_m3s", "delta_p_Pa", "nr_rpm", "n_stages", "psi_head_coefficient"):
            for bad in (None, 0, -1, float("nan"), float("inf"), True, "1"):
                result = fixture()
                result["calculation"]["components"]["ox_pump"]["outputs"][key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertFalse(pump_geometry_view(result)["pumps"]["ox"]["available"])
        for data in (None, {}, {"calculation": []}, {"calculation": {"components": {"ox_pump": []}}},
                     {"pump_geometry": {}}, {"pump_geometry": {"schema_version": 1, "pumps": None}}):
            for pump in pump_geometry_view(data)["pumps"].values():
                self.assertFalse(pump["available"])

    def test_invalid_geometric_assumptions_do_not_draw_or_clip(self):
        for key, bad, reason in (("eye_hub_ratio", 1, "轮毂比"), ("inlet_flow_coefficient", .001, "入口眼径"),
                                 ("axial_length_ratio", .001, "出口宽度"), ("outlet_flow_coefficient", 0, "出口速度比")):
            result = fixture()
            result["calculation"]["inputs"]["lox_pump_" + key] = bad
            p = pump_geometry_view(result)["pumps"]["ox"]
            self.assertFalse(p["available"], p)
            self.assertIn(reason, p["reason"])
            self.assertNotIn("meridional", p)

    def test_extreme_finite_inputs_cannot_publish_partial_or_nonfinite_geometry(self):
        for key in ("inlet_flow_coefficient", "outlet_flow_coefficient", "axial_length_ratio"):
            for value in (1e308, 1e-308, 10**500):
                result = fixture()
                result["calculation"]["inputs"]["lox_pump_" + key] = value
                view = pump_geometry_view(result)
                p = view["pumps"]["ox"]
                with self.subTest(key=key, value=value):
                    self.assertFalse(p["available"], p)
                    self.assertNotIn("meridional", p)
                    json.dumps(view, allow_nan=False)

    def test_non_methane_fuel_is_not_relabelled_as_methane(self):
        result = fixture()
        result["calculation"]["inputs"]["propellant_pair"] = "lox_rp1"
        view = pump_geometry_view(result)
        self.assertTrue(view["pumps"]["ox"]["available"])
        self.assertFalse(view["pumps"]["fuel"]["available"])
        self.assertEqual(view["pumps"]["fuel"]["label"], "燃料主泵")


class ExistingPumpGeometryConsistencyTests(unittest.TestCase):
    def estimate(self, **changes):
        args = dict(name="pump", fluid="CH4", mdot=50., rho=450., dp=40e6,
                    power=5e6, shaft_speed_rpm=20000., n_stages=2, psi=.5)
        return estimate_pump(**(args | changes))

    def test_large_and_small_diameters_are_not_clipped(self):
        for rpm, expected in ((1000., "large"), (500000., "small")):
            p = self.estimate(shaft_speed_rpm=rpm)
            self.assertAlmostEqual(p.tip_speed_ms, math.pi * p.impeller_diameter_m * rpm / 60)
            if expected == "large":
                self.assertGreater(p.impeller_diameter_m, .8)
            else:
                self.assertLess(p.impeller_diameter_m, .03)

    def test_density_head_and_coefficient_are_not_floored(self):
        p = self.estimate(rho=50., dp=100., psi=.01, shaft_speed_rpm=1.)
        self.assertEqual(p.volume_flow_m3s, 1.)
        self.assertAlmostEqual(p.head_m, 100 / 50 / G)
        self.assertAlmostEqual(p.tip_speed_ms**2 * .01, 100 / 50 / 2)
        self.assertAlmostEqual(p.impeller_diameter_m * math.pi / 60, p.tip_speed_ms)

    def test_invalid_arguments_raise_instead_of_becoming_plausible_geometry(self):
        for changes in ({"rho": 0}, {"rho": float("nan")}, {"dp": -1}, {"mdot": 0}, {"psi": 0},
                        {"shaft_speed_rpm": 0}, {"n_stages": 0}, {"n_stages": 1.2}, {"n_stages": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.estimate(**changes)


if __name__ == "__main__":
    unittest.main()
