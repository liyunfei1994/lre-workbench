"""Independent geometry and coefficient checks for the Bartz curvature correction."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chamber_contour import convergent_geometry, convergent_contour
from geometry.nozzle import compute_nozzle, full_hot_gas_geometry
from regen_geometry import build_regen_geometry
from chamber_design import ChannelRow
import cooling_marching as cm
from tests.test_regen_circuit import BASE, GEOM


class BartzGeometryTests(unittest.TestCase):
    def test_small_chamber_uses_effective_arc_in_contour_and_nozzle_metadata(self):
        rt, rc, beta = .1, .11, 70.
        expected = (rc-rt)/(1-math.cos(math.radians(beta)))
        cg = convergent_geometry(rc, rt, beta)
        self.assertLess(expected, 1.5*rt)
        self.assertAlmostEqual(cg["Ru_m"], expected, places=14)
        xs, rs, length = convergent_contour(rc, rt, beta)
        # Every upstream arc point must lie on the reported circle. This
        # measures the generated contour, not just equality of two metadata fields.
        for x, r in zip(xs, rs):
            self.assertAlmostEqual((x-length)**2+(r-(rt+expected))**2,
                                   expected**2, places=13)
        nozzle = compute_nozzle(rt, .4, rc, 16., (rc/rt)**2, .3, theta_conv_deg=beta)
        self.assertAlmostEqual(nozzle.throat_upstream_radius_m, expected, places=14)
        self.assertAlmostEqual(nozzle.throat_downstream_radius_m, .382*rt, places=14)

    def test_curvature_is_independent_of_mesh_flow_direction_and_coverage(self):
        for n, reverse, coverage in ((40, True, 0), (160, False, 8), (320, True, 16)):
            g = build_regen_geometry(**(GEOM | dict(n_points=n)),
                                     counterflow=reverse, cover_eps=coverage)
            self.assertAlmostEqual(g["throat_upstream_radius_m"], 1.5*BASE["r_t"])
            self.assertAlmostEqual(g["L_conv_m"], g["x_throat_m"]-g["L_cyl_m"])

    def test_geometry_changes_propagate_to_bartz_radius(self):
        g = build_regen_geometry(**(GEOM | dict(r_t=.1, r_c=.11)), conv_half_angle_deg=70.)
        self.assertLess(g["throat_upstream_radius_m"], .15)
        self.assertAlmostEqual(g["throat_upstream_radius_m"],
                               (.11-.1)/(1-math.cos(math.radians(70.))))

    def test_frozen_state_curvature_coefficient_ratio(self):
        g = build_regen_geometry(**GEOM)
        c = min(g["cells"], key=lambda c: abs(c["x_m"]-g["x_throat_m"]))
        bulk = dict(rho_kgm3=450., mu_Pas=1e-4, k_WmK=.2, cp_JkgK=2400., T_K=200.)
        hot = cm._hot_state(c, gamma=1.2, T_c=3800., pc=33e6,
                            mu_g=8e-5, cp_g=2800., Pr_g=.7)
        kwargs = dict(mdot_coolant=166., mdot_total=765., r_t=BASE["r_t"], pc=33e6,
            wall_conductivity=320., k_wall_func=None, htc_enhancement=1.,
            x_throat=g["x_throat_m"], gas_emissivity=.2, heat_transfer_enabled=False)
        new = cm._wall_heat(c, bulk, hot, **kwargs,
                            throat_upstream_radius_m=g["throat_upstream_radius_m"])
        old = cm._wall_heat(c, bulk, hot, **kwargs, throat_upstream_radius_m=.35*2*BASE["r_t"])
        self.assertAlmostEqual(new["h_g_W_m2K"]/old["h_g_W_m2K"], .926617884289, places=11)
        for invalid in (0., -1., float("nan")):
            with self.assertRaises(ValueError):
                cm._wall_heat(c, bulk, hot, **kwargs, throat_upstream_radius_m=invalid)

    def test_length_conflict_is_rejected_before_fluid_solve(self):
        with self.assertRaisesRegex(ValueError, "L_conv conflicts"):
            cm.march_regen_circuit(**BASE, L_conv=.01)

    def test_angle_is_used_without_hidden_five_degree_floor(self):
        g = convergent_geometry(.2, .1, 3.)
        xs, rs, length = convergent_contour(.2, .1, 3.)
        slopes = [(b-a)/(y-x) for x, y, a, b in zip(xs, xs[1:], rs, rs[1:]) if y>x]
        self.assertAlmostEqual(min(slopes), -math.tan(math.radians(3.)), places=12)
        self.assertEqual(length, g["L_conv_m"])
        for bad in (0., 80., -5., float("nan")):
            with self.assertRaises(ValueError):
                convergent_geometry(.2, .1, bad)

    def test_inconsistent_nozzle_radii_cannot_be_labeled_with_different_expansion(self):
        with self.assertRaisesRegex(ValueError, "area ratios"):
            full_hot_gas_geometry(.1, .4, .15, 25., .3)

    def test_insufficient_chamber_volume_is_not_reported_as_zero_length(self):
        from chamber_sizing import size_combustion_chamber
        with self.assertRaisesRegex(ValueError, "特征容积不足"):
            size_combustion_chamber(At=.04, pc=33e6, mdot_total=765., T_c=3800.,
                molar_mass=22., gamma=1.2, l_star=.001, contraction_ratio=2.5)

    def test_distributed_throat_dimensions_are_not_global_minima_or_input_defaults(self):
        table = [ChannelRow(anchor="injector", w_mm=.7, h_mm=15., t_mm=1.2, n=300),
                 ChannelRow(anchor="throat", w_mm=1.5, h_mm=15., t_mm=.8, n=300),
                 ChannelRow(anchor="exit", w_mm=3., h_mm=15., t_mm=1.2, n=300)]
        r = cm.march_regen_circuit(**BASE, channel_table=table, heat_transfer_enabled=False)
        self.assertAlmostEqual(r["A_flow_throat_m2"], 300*.0015*.015, places=14)
        self.assertAlmostEqual(r["t_liner_throat_m"], .0008, places=14)
        self.assertLess(min(n["A_flow_m2"] for n in r["geometry"]["nodes"]), r["A_flow_throat_m2"])
        audit = r["profile"]["gas_heat_transfer"]
        self.assertEqual(audit["curvature_radius_m"], r["geometry"]["throat_upstream_radius_m"])
        self.assertEqual(audit["geometry_model_id"], cm.BARTZ_GEOMETRY_ID)


if __name__ == "__main__":
    unittest.main()
