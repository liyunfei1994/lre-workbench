"""Independent geometry/flow closure checks for preliminary coaxial elements."""
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import ValidationError
from injector import Injector


class InjectorGeometryClosureTests(unittest.TestCase):
    def injector(self, **overrides):
        args = dict(mdot_ox=580.0, mdot_fuel=180.0, pc=33e6,
                    dp_ratio_ox=.20, dp_ratio_fuel=.10,
                    chamber_radius=.183, rho_ox=250.0, rho_fuel=100.0)
        args.update(overrides)
        return Injector(**args)

    def from_dimensions(self, d_ox_m, gap_m, **overrides):
        """Create a case with analytically chosen apertures, independent of solver."""
        injector = self.injector(**overrides)
        n = 20
        d_post = d_ox_m + .001
        area_ox = math.pi * d_ox_m ** 2 / 4
        area_fuel = math.pi * gap_m * (d_post + gap_m)
        injector.mdot_ox = n * injector.cd_ox * area_ox * math.sqrt(
            2 * injector.rho_ox * injector.dp_ratio_ox * injector.pc)
        injector.mdot_fuel = n * injector.cd_fuel * area_fuel * math.sqrt(
            2 * injector.rho_fuel * injector.dp_ratio_fuel * injector.pc)
        with patch.object(injector, "_num_elements", return_value=n):
            return injector, injector.evaluate()

    def assert_returned_geometry_closes(self, injector, result):
        out = result.outputs
        n = out["num_elements"]
        d_ox, d_post, d_outer = (out[k] * 1e-3 for k in ("d_ox_mm", "d_post_mm", "d_outer_mm"))
        # Intentionally use diameter-squared annulus, independent of inverse code.
        areas = {"ox": math.pi * d_ox ** 2 / 4,
                 "fuel": math.pi * (d_outer ** 2 - d_post ** 2) / 4}
        for side in ("ox", "fuel"):
            rho = getattr(injector, f"rho_{side}")
            cd = getattr(injector, f"cd_{side}")
            dp = getattr(injector, f"dp_ratio_{side}") * injector.pc
            required = getattr(injector, f"mdot_{side}")
            actual = n * areas[side] * cd * math.sqrt(2 * rho * dp)
            self.assertAlmostEqual(actual / required, 1, delta=1e-12)
            self.assertAlmostEqual(out[f"mdot_{side}_geometry_kgs"] / required, 1, delta=1e-12)
            self.assertAlmostEqual(out[f"a_{side}_geometry_per_element_m2"] / areas[side], 1, delta=1e-12)
        self.assertEqual(out["geometry_closure_passed"], 1.0)
        self.assertTrue(all(math.isfinite(v) for v in out.values()))

    def test_saved_case_area_regression(self):
        # Previously the 15.315 mm2 requested annulus became 17.540 mm2.
        area = 15.315e-6
        old_area = 17.540e-6
        d_post = math.sqrt(area / (math.pi * (old_area / area - 1)))
        gap_exact = (math.sqrt(d_post ** 2 + 4 * area / math.pi) - d_post) / 2
        injector, result = self.from_dimensions(d_post - .001, gap_exact)
        old_gap = area / (math.pi * d_post)
        self.assertAlmostEqual(math.pi * old_gap * (d_post + old_gap) * 1e6, 17.540, places=9)
        self.assertAlmostEqual(result.outputs["a_fuel_geometry_per_element_m2"] * 1e6, 15.315, places=9)
        self.assertLess(result.outputs["delta_fuel_mm"], old_gap * 1e3)
        self.assert_returned_geometry_closes(injector, result)

    def test_non_thin_annulus_and_wall_definition(self):
        injector, result = self.from_dimensions(.004, .003)
        out = result.outputs
        self.assertAlmostEqual(out["d_ox_mm"], 4, places=12)
        self.assertAlmostEqual(out["d_post_mm"], 5, places=12)
        self.assertAlmostEqual(out["delta_fuel_mm"], 3, places=12)
        self.assertAlmostEqual(out["d_outer_mm"], 11, places=12)
        self.assertEqual(out["post_wall_thickness_mm"], .5)
        self.assert_returned_geometry_closes(injector, result)

    def test_out_of_range_sizes_are_reported_without_clipping(self):
        for d_ox, gap in ((.0004, .00005), (.07, .008)):
            with self.subTest(d_ox=d_ox, gap=gap):
                injector, result = self.from_dimensions(d_ox, gap)
                out = result.outputs
                self.assertAlmostEqual(out["d_ox_mm"], d_ox * 1e3, places=11)
                self.assertAlmostEqual(out["delta_fuel_mm"], gap * 1e3, places=11)
                self.assertEqual(out["geometry_range_warning_count"], 2)
                self.assertEqual(result.profile["geometry_contract"]["status"], "closed_with_warnings")
                self.assertTrue(any("保留质量连续性计算值" in n for n in result.notes))
                self.assert_returned_geometry_closes(injector, result)

    def test_impossible_face_packing_is_visible(self):
        injector, result = self.from_dimensions(.02, .003, chamber_radius=.01)
        self.assertGreater(result.outputs["element_envelope_area_fraction"], 1)
        self.assertGreater(result.outputs["geometry_layout_warning_count"], 0)
        self.assertTrue(any("不能直接布置" in w for w in result.profile["geometry_contract"]["warnings"]))
        self.assert_returned_geometry_closes(injector, result)

    def test_discharge_coefficient_is_counted_once(self):
        results = []
        for cd in (.35, .70):
            injector = self.injector(cd_ox=cd, cd_fuel=cd)
            with patch.object(injector, "_num_elements", return_value=300):
                result = injector.evaluate()
            results.append(result.outputs)
            self.assert_returned_geometry_closes(injector, result)
        for side in ("ox", "fuel"):
            self.assertAlmostEqual(results[0][f"a_{side}_geometry_per_element_m2"] /
                                   results[1][f"a_{side}_geometry_per_element_m2"], 2, places=12)

    def test_nonfinite_inputs_and_bad_element_targets_are_rejected(self):
        keys = ("mdot_ox", "mdot_fuel", "pc", "dp_ratio_ox", "dp_ratio_fuel",
                "chamber_radius", "rho_ox", "rho_fuel", "cd_ox", "cd_fuel", "mdot_per_element")
        for key in keys:
            for value in (float("nan"), float("inf"), -float("inf"), 0, -1):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValidationError):
                        self.injector(**{key: value})

    def test_numerically_unrepresentable_gap_fails_explicitly(self):
        injector = self.injector(mdot_fuel=1e-300)
        with self.assertRaisesRegex(ValidationError, "环缝"):
            injector.evaluate()

    def test_derived_nonfinite_flow_or_velocity_fails_explicitly(self):
        for overrides in (dict(mdot_ox=1e308, mdot_fuel=1e308), dict(pc=1.7e308, dp_ratio_ox=.6)):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.injector(**overrides).evaluate()

    def test_contract_does_not_claim_resolved_swirl_or_layout(self):
        injector = self.injector()
        result = injector.evaluate()
        self.assert_returned_geometry_closes(injector, result)
        contract = result.profile["geometry_contract"]
        self.assertEqual(contract["version"], 1)
        self.assertEqual(contract["type"], "coaxial_circular_annular")
        self.assertEqual(contract["flow_model"], "incompressible_orifice")
        self.assertFalse(contract["swirl_geometry_resolved"])
        self.assertFalse(contract["layout_resolved"])
        self.assertEqual(contract["assumptions"]["post_wall_thickness_mm"], .5)
        self.assertEqual(result.outputs["mdot_ox_kgs"], injector.mdot_ox)
        self.assertEqual(result.outputs["mdot_fuel_kgs"], injector.mdot_fuel)


if __name__ == "__main__":
    unittest.main()
