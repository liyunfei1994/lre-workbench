"""Channel sizing must use one physical section and survive a full march."""
from pathlib import Path
import math
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cooling import CoolingChannel, resolve_channel_height, resolve_inlet_velocity_height
from cooling_marching import CoolantMarchError
from core import ValidationError
from fluid_properties import state_tp


BASE = dict(mdot_fuel=166.3, t_inlet=122.35, p_inlet=74.89e6,
    mdot_total=765., pc=33e6, t_chamber=3773.34, gamma_c=1.189,
    molar_mass_c=22.6866, n_channels=300, n_points=40,
    throat_area_m2=math.pi*.11586**2)


class ChannelSizingTests(unittest.TestCase):
    def test_legacy_keeps_the_exact_saved_mixed_reference(self):
        _, result = CoolingChannel(**BASE, target_velocity_ms=100).evaluate()
        rho = state_tp("CH4", BASE["t_inlet"], BASE["p_inlet"])["rho_kgm3"]
        height, clipped = resolve_channel_height(BASE["mdot_fuel"], rho, 100., .0015, 300)
        self.assertFalse(clipped)
        self.assertAlmostEqual(result.outputs["channel_height_mm"], height*1000)
        sizing = result.profile["channel_sizing"]
        self.assertEqual(sizing["mode"], "legacy_reference")
        self.assertIsNone(sizing["actual_velocity_ms"])
        self.assertEqual(sizing["legacy_inputs"]["port_density_kgm3"], rho)
        self.assertNotAlmostEqual(result.outputs["coolant_inlet_velocity_ms"], 100., delta=1.)

    def test_actual_inlet_target_uses_entrance_width_and_local_density(self):
        for counterflow, target in ((True, 20.), (False, 50.)):
            with self.subTest(counterflow=counterflow):
                _, result = CoolingChannel(**BASE, counterflow=counterflow,
                    sizing_reference="coolant_inlet_velocity", target_velocity_ms=target).evaluate()
                profile = result.profile
                sizing = profile["channel_sizing"]
                inlet = profile["nodes"][1]
                self.assertEqual(inlet["kind"], "channel")
                self.assertEqual(profile["nodes"][0]["velocity_ms"], 0.)
                self.assertEqual(sizing["status"], "converged_and_march_verified")
                self.assertAlmostEqual(inlet["velocity_ms"], target, delta=sizing["tolerance_ms"])
                self.assertAlmostEqual(BASE["mdot_fuel"],
                    inlet["rho_kgm3"]*inlet["A_flow_m2"]*target, delta=1e-5)
                self.assertAlmostEqual(inlet["A_flow_m2"],
                    inlet["n_channels"]*inlet["width_m"]*inlet["height_m"], delta=1e-12)
                self.assertLess(inlet["p_Pa"], BASE["p_inlet"])
                self.assertNotEqual(inlet["rho_kgm3"], profile["nodes"][0]["rho_kgm3"])
                self.assertEqual(sizing["reference_section"]["x_m"], inlet["x_m"])
                self.assertNotAlmostEqual(inlet["width_m"], .0015, delta=1e-6)
                # The throat is an output, not a second prescribed velocity.
                self.assertNotAlmostEqual(result.outputs["coolant_throat_node_velocity_ms"], target, delta=1.)
                loss = profile["cells"][0]["local_loss_K"]
                self.assertAlmostEqual(BASE["p_inlet"]-inlet["p_Pa"],
                    (1+loss)*inlet["rho_kgm3"]*target**2/2, delta=.5)
                self.assertAlmostEqual(inlet["h0_Jkg"], profile["nodes"][0]["h0_Jkg"], delta=1e-5)

    def test_fixed_geometry_does_not_resize_when_flow_or_target_changes(self):
        results = []
        for flow, target in ((166.3, 20.), (140., 90.)):
            _, result = CoolingChannel(**(BASE | {"mdot_fuel":flow}), auto_size=False,
                channel_height_m=.008, sizing_reference="coolant_inlet_velocity",
                target_velocity_ms=target).evaluate()
            results.append(result)
            self.assertEqual(result.profile["channel_sizing"]["mode"], "fixed_geometry")
            self.assertIsNone(result.profile["channel_sizing"]["target_velocity_ms"])
        self.assertEqual(results[0].profile["geometry"], results[1].profile["geometry"])
        self.assertNotEqual(results[0].outputs["coolant_inlet_velocity_ms"],
                            results[1].outputs["coolant_inlet_velocity_ms"])

    def test_explicit_channel_table_takes_precedence_over_auto_sizing(self):
        from chamber_design import ChannelRow
        rows = [ChannelRow(anchor="injector", w_mm=2.372, h_mm=8.),
                ChannelRow(anchor="throat", w_mm=1.5, h_mm=8.),
                ChannelRow(anchor="exit", w_mm=8.79, h_mm=8.)]
        with patch("cooling.resolve_inlet_velocity_height", side_effect=AssertionError("must not resize")):
            _, result = CoolingChannel(**BASE, channel_table=rows, auto_size=True,
                sizing_reference="coolant_inlet_velocity", target_velocity_ms=100).evaluate()
        self.assertEqual(result.profile["channel_sizing"]["status"], "prescribed_channel_table")
        self.assertTrue(all(n["height_m"] == .008 for n in result.profile["geometry"]["nodes"]))

    def test_full_march_must_verify_the_sized_inlet_velocity(self):
        with patch("cooling.resolve_inlet_velocity_height", return_value=(.008, {})):
            with self.assertRaisesRegex(ValueError, "完整沿程计算不一致"):
                CoolingChannel(**BASE, sizing_reference="coolant_inlet_velocity",
                    target_velocity_ms=20.).evaluate()

    def test_large_inlet_target_is_not_accepted_when_downstream_is_infeasible(self):
        with self.assertRaises(CoolantMarchError):
            CoolingChannel(**BASE, sizing_reference="coolant_inlet_velocity",
                target_velocity_ms=100.).evaluate()

    def test_invalid_inlet_target_and_unknown_reference_are_rejected(self):
        port = state_tp("CH4", BASE["t_inlet"], BASE["p_inlet"])
        for target in (-1., 0., float("nan"), 10000.):
            with self.subTest(target=target), self.assertRaises(ValueError):
                resolve_inlet_velocity_height(mdot_coolant=BASE["mdot_fuel"],
                    port_state=port, target_velocity_ms=target,
                    inlet_geometry=dict(width_m=.008, n_channels=300), k_minor_inlet=1.5)
        with self.assertRaises(ValidationError):
            CoolingChannel(**BASE, sizing_reference="throat_assumed_from_inlet")

    def test_saved_design_summary_uses_the_solved_profile(self):
        _, result = CoolingChannel(**BASE, auto_size=False, channel_height_m=.008).evaluate()
        profile = result.profile
        summary = profile["channel_design_summary"]
        nodes = [n for n in profile["nodes"] if n["kind"] == "channel"]
        throat = min(nodes, key=lambda n:abs(n["x_m"]-profile["geometry"]["x_throat_m"]))
        self.assertEqual(summary["inlet"]["velocity_ms"], nodes[0]["velocity_ms"])
        self.assertEqual(summary["throat"]["velocity_ms"], throat["velocity_ms"])
        self.assertEqual(summary["maximum_velocity_node"]["velocity_ms"], max(n["velocity_ms"] for n in nodes))
        self.assertEqual(summary["geometry_ranges"]["rib_m"],
            [min(n["rib_m"] for n in nodes), max(n["rib_m"] for n in nodes)])
        self.assertEqual(result.outputs["throat_rib_mm"], throat["rib_m"]*1000)
        self.assertEqual(summary["channel_count_assessment"], "design_variable_not_optimized")


if __name__ == "__main__":
    unittest.main()
