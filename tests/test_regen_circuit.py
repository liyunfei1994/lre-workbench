"""Conservation and limiting cases for the single p/h cooling kernel."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import ComponentResult
import cooling_marching as cm
from regen_geometry import build_regen_geometry


BASE = dict(mdot_coolant=166.3, T_in=122.35, p_in=74.89e6,
    mdot_total=765., pc=33e6, T_c=3773.34, gamma=1.189, molar_mass=22.6866,
    r_t=.11586, r_c=.18316, L_cyl=.25646,
    expansion_ratio=34.34, channel_height_m=.00805677, n_points=80)
GEOM = {k: v for k, v in BASE.items() if k in (
    "r_t", "r_c", "L_cyl", "expansion_ratio", "channel_height_m", "n_points")}


class RegenGeometryTests(unittest.TestCase):
    def test_exact_coverage_and_reversible_flow_order(self):
        backwards = build_regen_geometry(**GEOM, cover_eps=8.)
        forwards = build_regen_geometry(**GEOM, cover_eps=8., counterflow=False)
        self.assertAlmostEqual(backwards["nodes"][0]["area_ratio"], 8., places=12)
        self.assertEqual(backwards["nodes"][-1]["x_m"], 0.)
        self.assertEqual(forwards["nodes"][0]["x_m"], 0.)
        self.assertEqual([n["x_m"] for n in backwards["nodes"]],
                         list(reversed([n["x_m"] for n in forwards["nodes"]])))
        self.assertAlmostEqual(backwards["path_length_m"], forwards["path_length_m"], places=12)
        self.assertAlmostEqual(backwards["hot_area_m2"], forwards["hot_area_m2"], places=12)
        self.assertEqual(len(backwards["nodes"]), len(backwards["cells"])+1)
        full = backwards["full_nodes"]
        self.assertEqual(full[0]["x_m"], 0.)
        self.assertGreater(full[-1]["x_m"], backwards["coverage_end_x_m"])
        self.assertTrue(any(n["x_m"] == backwards["x_throat_m"] and
                            abs(n["r_m"]-backwards["r_t_m"]) < 1e-12 for n in full))
        self.assertTrue(any(abs(n["x_m"]-backwards["coverage_end_x_m"]) < 1e-12 for n in full))

    def test_geometry_failure_does_not_fall_back(self):
        for kwargs in (dict(cover_eps=100.), dict(channel_width_m=.1),
                       dict(channel_table="{bad json"), dict(channel_height_m=-1),
                       dict(n_points=1), dict(r_t=float("nan"))):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_regen_geometry(**(GEOM | kwargs))

    def test_branching_requires_explicit_model(self):
        from chamber_design import ChannelRow
        rows = [ChannelRow(anchor="injector", n=100), ChannelRow(anchor="throat", n=200)]
        with self.assertRaisesRegex(ValueError, "manifold"):
            build_regen_geometry(**GEOM, channel_table=rows)


class RegenConservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = cm.march_regen_circuit(**BASE)

    def test_record_saves_methods_used_by_the_unified_kernel(self):
        methods = self.result["profile"]["model_methods"]
        self.assertEqual(methods["version"], 1)
        self.assertTrue(methods["heat_transfer_enabled"])
        self.assertTrue(methods["friction_enabled"])
        self.assertIn("Bartz", methods["hot_side"]["label"])
        self.assertIn("Dittus", methods["cold_side"]["label"])
        self.assertIn("4.36", methods["cold_side"]["detail"])
        self.assertIn("k(T)", methods["wall"]["detail"])

    def test_static_port_energy_and_internal_total_energy(self):
        r = self.result
        self.assertEqual(r["nodes"][0]["velocity_ms"], 0.)
        self.assertEqual(r["nodes"][-1]["velocity_ms"], 0.)
        self.assertAlmostEqual(BASE["mdot_coolant"]*(r["h_out_Jkg"]-r["nodes"][0]["h_Jkg"]),
                               r["Q_total_W"], delta=.001)
        self.assertLess(r["balance"]["max_cell_energy_residual_W"], .001)
        self.assertLess(r["balance"]["max_cell_momentum_residual_Pa"], .001)
        for node in r["nodes"]:
            self.assertAlmostEqual(node["h0_Jkg"], node["h_Jkg"]+node["velocity_ms"]**2/2, delta=1e-8)

    def test_complete_nodes_include_end_local_losses(self):
        r = self.result
        self.assertEqual(len(r["nodes"]), len(r["cells"])+1)
        self.assertEqual(r["cells"][0]["kind"], "inlet_manifold")
        self.assertEqual(r["cells"][-1]["kind"], "outlet_manifold")
        self.assertGreater(r["cells"][0]["dp_local_Pa"], 0)
        self.assertGreater(r["cells"][-1]["dp_local_Pa"], 0)
        self.assertEqual(r["nodes"][0]["p_Pa"]-r["nodes"][-1]["p_Pa"], r["dp_total_Pa"])
        self.assertEqual(r["nodes"][-1]["p_Pa"], r["p_out_Pa"])
        self.assertEqual(r["nodes"][-1]["T_K"], r["T_out_K"])
        self.assertEqual(r["Q_total_W"], sum(c["Q_W"] for c in r["cells"]))
        self.assertLess(abs(r["balance"]["pressure_sum_residual_Pa"]), .001)

    def test_wall_balance_and_no_near_wall_property_extension(self):
        r = self.result
        self.assertLess(r["balance"]["max_wall_residual_K"], 1e-5)
        self.assertTrue(any(c["T_wall_cold_K"] > 620 for c in r["cells"] if c["kind"] == "channel"))
        for cell in r["cells"]:
            if cell["kind"] == "channel":
                lhs = cell["T_wall_hot_K"]-cell["T_wall_cold_K"]
                rhs = cell["q_W_m2"]*cell["wall_thickness_m"]/cell["wall_conductivity_WmK"]
                self.assertAlmostEqual(lhs, rhs, delta=1e-5)
        self.assertEqual(r["validity"]["property_domain"], "within_HEOS_declared_single_phase_ranges")

    def test_zero_heat_keeps_port_enthalpy_with_friction(self):
        r = cm.march_regen_circuit(**BASE, heat_transfer_enabled=False)
        self.assertEqual(r["Q_total_W"], 0.)
        self.assertAlmostEqual(r["h_out_Jkg"], r["nodes"][0]["h_Jkg"], delta=1e-5)
        self.assertGreater(r["dp_total_Pa"], 0.)
        self.assertGreater(r["nodes"][-1]["entropy_JkgK"], r["nodes"][0]["entropy_JkgK"])

    def test_incompressible_inviscid_variable_area_recovers_pressure(self):
        def constant_ph(fluid, p, h, **kwargs):
            return dict(p_Pa=p, T_K=h/2400, h_Jkg=h, rho_kgm3=500.,
                cp_JkgK=2400., mu_Pas=1e-4, k_WmK=.2, sound_speed_ms=1000.,
                phase="liquid", range_status="test_constant_properties", s_JkgK=0.)
        def constant_tp(fluid, t, p, **kwargs):
            return constant_ph(fluid, p, 2400*t)
        with patch.object(cm, "state_ph", side_effect=constant_ph), patch.object(cm, "state_tp", side_effect=constant_tp):
            r = cm.march_regen_circuit(**BASE, inlet_enthalpy_Jkg=2400*BASE["T_in"],
                heat_transfer_enabled=False, friction_enabled=False, k_minor=0.)
        self.assertAlmostEqual(r["p_out_Pa"], BASE["p_in"], delta=.01)
        self.assertTrue(any(c["dp_momentum_Pa"] < 0 for c in r["cells"] if c["kind"] == "channel"))
        self.assertTrue(any(c["dp_momentum_Pa"] > 0 for c in r["cells"] if c["kind"] == "channel"))

    def test_declared_domain_and_unsupported_modes_fail(self):
        for kwargs in (dict(T_in=700.), dict(p_in=-1.), dict(film_fraction=.01),
                       dict(strict=False), dict(k_minor=-1), dict(hot_side_profile=[])):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                cm.march_regen_circuit(**(BASE | kwargs))

    def test_infeasible_pressure_is_rejected_not_clamped(self):
        with self.assertRaises(cm.CoolantMarchError) as failure:
            cm.march_regen_circuit(**(BASE | dict(p_in=3e6, channel_height_m=.0005)))
        self.assertIsNotNone(failure.exception.__cause__)
        self.assertGreaterEqual(failure.exception.s_m, 0.)

    def test_input_and_wall_errors_are_not_pressure_search_errors(self):
        for kwargs in (dict(p_in=-1.), dict(channel_height_m=-1.), dict(hot_side_profile=[])):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError) as failure:
                cm.march_regen_circuit(**(BASE | kwargs))
            self.assertNotIsInstance(failure.exception, cm.CoolantMarchError)
        with patch.object(cm, "_wall_heat", side_effect=ValueError("invalid wall input")):
            with self.assertRaises(ValueError) as failure:
                cm.march_regen_circuit(**BASE)
            self.assertNotIsInstance(failure.exception, cm.CoolantMarchError)

    def test_wall_solver_failure_is_not_accepted(self):
        with patch.object(cm, "brentq", side_effect=ValueError("nonconvergence")):
            with self.assertRaises(ValueError):
                cm.march_regen_circuit(**BASE)

    def test_same_hot_side_states_reproduce_default_path(self):
        hot = [c["hot_side"] for c in self.result["cells"] if c["kind"] == "channel"]
        result = cm.march_regen_circuit(**BASE, hot_side_profile=hot)
        self.assertAlmostEqual(result["Q_total_W"], self.result["Q_total_W"], delta=.001)
        self.assertAlmostEqual(result["p_out_Pa"], self.result["p_out_Pa"], delta=.001)

    def test_grid_refinement_reduces_output_changes(self):
        a = self.result
        b = cm.march_regen_circuit(**(BASE | dict(n_points=160)))
        c = cm.march_regen_circuit(**(BASE | dict(n_points=320)))
        for key in ("Q_total_W", "dp_total_Pa"):
            coarse = abs(b[key]-a[key])
            fine = abs(c[key]-b[key])
            self.assertLess(fine, coarse)
            self.assertLess(fine/abs(c[key]), .001)

    def test_compatibility_postpass_only_reuses_solved_profile(self):
        component = ComponentResult("cooling_channel", outputs={})
        component.profile = self.result["profile"]
        result = SimpleNamespace(components={"cooling_channel": component}, summary={})
        with patch.object(cm, "march_regen_circuit", side_effect=AssertionError("second solve")):
            cm.run_marching_postpass(result, None)
        self.assertEqual(result.components["cooling_marching"].profile, component.profile)
        self.assertIsNot(result.components["cooling_marching"].profile, component.profile)


class CoolingWrapperTests(unittest.TestCase):
    @staticmethod
    def kwargs():
        return dict(mdot_fuel=166.3, t_inlet=122.35, p_inlet=74.89e6,
            mdot_total=765., pc=33e6, t_chamber=3773.34,
            gamma_c=1.189, molar_mass_c=22.6866, n_channels=300,
            n_points=80, throat_area_m2=3.141592653589793*.11586**2)

    def test_outputs_and_state_are_directly_from_profile(self):
        from cooling import CoolingChannel
        state, result = CoolingChannel(**self.kwargs()).evaluate()
        profile = result.profile
        self.assertEqual(state.enthalpy, profile["nodes"][-1]["h_Jkg"])
        self.assertEqual(state.temperature, profile["nodes"][-1]["T_K"])
        self.assertEqual(state.pressure, profile["nodes"][-1]["p_Pa"])
        self.assertEqual(result.outputs["heat_load_W"], sum(c["Q_W"] for c in profile["cells"]))
        self.assertEqual(result.outputs["A_wetted_total_m2"], profile["geometry"]["hot_area_m2"])
        self.assertEqual(result.outputs["L_cc_m"], profile["geometry"]["L_cyl_m"])
        self.assertAlmostEqual(result.outputs["heat_load_chamber_W"]+result.outputs["heat_load_nozzle_W"],
                               result.outputs["heat_load_W"], delta=1e-6)
        zone = sum(value*1e6 for key,value in result.outputs.items()
                   if key in ("Q_cc_MW","Q_conv_MW","Q_throat_MW","Q_noz_MW","Q_noz_ext_MW"))
        self.assertAlmostEqual(zone, result.outputs["heat_load_W"], delta=1e-6)

    def test_bartz_inactive_budget_values_do_not_change_solution(self):
        from cooling import CoolingChannel
        a = CoolingChannel(**self.kwargs(), dp_pa=1e6, dp_auto=False, cp=1000., heat_load=0.).evaluate()
        b = CoolingChannel(**self.kwargs(), dp_pa=200e6, dp_auto=True, cp=9000., heat_load=300e6).evaluate()
        self.assertEqual(a[0].pressure, b[0].pressure)
        self.assertEqual(a[0].temperature, b[0].temperature)
        self.assertEqual(a[1].outputs["heat_load_W"], b[1].outputs["heat_load_W"])

    def test_auto_sizing_has_no_hidden_depth_clip(self):
        from cooling import CoolingChannel, resolve_channel_height
        depth, clipped = resolve_channel_height(160., 450., 100., .0015, 150)
        self.assertGreater(depth, .015)
        self.assertFalse(clipped)
        _, result = CoolingChannel(**(self.kwargs() | dict(n_channels=150))).evaluate()
        self.assertGreater(result.outputs["channel_height_mm"], 15.)

    def test_budget_mode_no_profile_and_uses_enthalpy_not_cp(self):
        from cooling import CoolingChannel
        a, ar = CoolingChannel(160.,125.,70e6,5e6,20e6,cp=1000.,bartz_mode=False).evaluate()
        b, br = CoolingChannel(160.,125.,70e6,5e6,20e6,cp=9000.,bartz_mode=False).evaluate()
        self.assertEqual(a.temperature, b.temperature)
        self.assertIsNone(getattr(ar,"profile",None))
        self.assertLess(abs(ar.outputs["energy_residual_W"]), .001)

    def test_legacy_pressure_guess_is_not_a_second_solver(self):
        from cooling import estimate_regen_dp
        with self.assertRaisesRegex(RuntimeError,"停用"):
            estimate_regen_dp()


if __name__ == "__main__":
    unittest.main()
