"""Independent invariants for the frozen, spatially heated system nozzle.

These are numerical verification checks, not validation of high-pressure
transport correlations or of a real engine's transonic flow.
"""
from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from heated_nozzle import heated_nozzle
from regen_geometry import build_regen_geometry
from thermo import _get_gas, _gas_to_state, nozzle_performance
from thermo_data import methane_dataset_scope


class HeatedNozzleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scope = methane_dataset_scope("gri30_highT")
        cls.scope.__enter__()
        gas = _get_gas()
        gas.TPY = 3773.6, 33e6, {"CH4": 1/4.6, "O2": 3.6/4.6}
        gas.equilibrate("TP")
        cls.chamber = _gas_to_state(gas, 33e6)
        cls.reference = nozzle_performance(cls.chamber, 33e6, 34.34, 101325, equilibrium=False)
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.scope.__exit__(None, None, None)

    @classmethod
    def solve(cls, n=120, q=120e6, counterflow=True, cover=0):
        key = n, q, counterflow, cover
        if key not in cls.cache:
            geom = build_regen_geometry(r_t=.116, r_c=.116*math.sqrt(2.5), L_cyl=.256,
                expansion_ratio=34.34, n_points=n, counterflow=counterflow, cover_eps=cover)
            cells = [dict(c, Q_W=q*c["dA_hot_m2"]/geom["hot_area_m2"]) for c in geom["cells"]]
            result = heated_nozzle(cls.chamber, 767.8, 33e6, 34.34, 101325,
                                   {"geometry": geom, "cells": cells})
            cls.cache[key] = result, geom, cells
        return cls.cache[key]

    def test_zero_heat_converges_to_independent_isentropic_solution(self):
        # The established SP nozzle solver has no streamwise momentum mesh.
        coarse, _, _ = self.solve(n=120, q=0)
        fine, _, _ = self.solve(n=240, q=0)
        for field, ref in (("Isp_s", "Isp_sl"), ("c_star_ms", "Cstar")):
            reference = self.reference[ref]
            a = abs(coarse.outputs[field]/reference-1)
            b = abs(fine.outputs[field]/reference-1)
            # Zero heat has the exact isentropic limit regardless of contour
            # interpolation. Tiny roundoff differences need not be monotone.
            self.assertLess(a, 1e-6)
            self.assertLess(b, 1e-6)
            self.assertLess(abs(coarse.outputs[field]/fine.outputs[field]-1), 1e-8)
        self.assertAlmostEqual(fine.profile["critical_x_m"],
                               fine.profile["geometric_throat_x_m"], places=12)

    def test_spatial_heat_is_conserved_at_every_original_cell_boundary(self):
        result, geom, cells = self.solve()
        gas_nodes = result.profile["nodes"]
        boundary = {round(n["x_m"], 12): n for n in gas_nodes}
        cold_boundaries = sorted(geom["nodes"], key=lambda n: n["x_m"])
        q = 0.
        h0 = gas_nodes[0]["h0_Jkg"]
        for idx, c in enumerate(sorted(cells, key=lambda c: c["x_m"])):
            q += c["Q_W"]
            node = boundary[round(cold_boundaries[idx+1]["x_m"], 12)]
            self.assertAlmostEqual(767.8*(h0-node["h0_Jkg"]), q, delta=1e-5)
        for node in gas_nodes:
            self.assertAlmostEqual(node["h_Jkg"]+node["velocity_ms"]**2/2,
                                   node["h0_Jkg"], delta=1e-5)
        self.assertAlmostEqual(result.outputs["heat_loss_W"], 120e6, delta=1e-5)
        self.assertLess(abs(result.outputs["energy_residual_W"]), .001)

    def test_heat_loss_moves_critical_point_upstream_without_extra_heat_cells(self):
        result, geom, cells = self.solve()
        p = result.profile
        self.assertLess(p["critical_x_m"], geom["x_throat_m"])
        self.assertLess(abs(p["critical_regularity_residual_per_m"]), 1e-6)
        self.assertLess(p["critical_launch_relative_difference"], 1e-7)
        self.assertLess(p["max_momentum_integral_residual_Pa"], .05)
        self.assertEqual(len(p["cells"]), len(cells))
        self.assertEqual([c["x_m"] for c in p["cells"]], sorted(c["x_m"] for c in cells))
        sonic = min(p["nodes"], key=lambda n: abs(n["x_m"]-p["critical_x_m"]))
        self.assertAlmostEqual(sonic["M"], 1., delta=2e-6)
        geometric = min(p["nodes"], key=lambda n: abs(n["x_m"]-geom["x_throat_m"]))
        self.assertAlmostEqual(result.outputs["T_throat_K"], geometric["T_K"])
        self.assertAlmostEqual(result.outputs["T_sonic_K"], sonic["T_K"])

    def test_grid_refinement_reduces_performance_change(self):
        values = [self.solve(n=n)[0].outputs["Isp_s"] for n in (60, 120, 240, 480)]
        self.assertLess(abs(values[2]-values[1]), abs(values[1]-values[0]))
        self.assertLess(abs(values[3]-values[2]), abs(values[2]-values[1]))
        self.assertLess(abs(values[2]/values[1]-1), 1e-4)

    def test_coolant_direction_does_not_reverse_gas_or_remove_uncooled_extension(self):
        a, geom, _ = self.solve(counterflow=False, cover=8)
        b, _, _ = self.solve(counterflow=True, cover=8)
        self.assertEqual(a.outputs, b.outputs)
        self.assertEqual(a.profile["nodes"], b.profile["nodes"])
        nodes = a.profile["nodes"]
        self.assertTrue(all(y["x_m"] > x["x_m"] for x, y in zip(nodes, nodes[1:])))
        self.assertGreater(nodes[-1]["x_m"], geom["coverage_end_x_m"])
        extension = [n for n in nodes if n["x_m"] >= geom["coverage_end_x_m"]]
        self.assertAlmostEqual(max(n["h0_Jkg"] for n in extension),
                               min(n["h0_Jkg"] for n in extension), places=9)
        self.assertAlmostEqual(nodes[-1]["area_ratio"], 34.34, places=10)

    def test_invalid_heat_geometry_and_boundary_inputs_are_rejected(self):
        _, geom, cells = self.solve()
        circuit = {"geometry": geom, "cells": cells}
        for mdot in (0., -1., float("nan")):
            with self.subTest(mdot=mdot), self.assertRaises(ValueError):
                heated_nozzle(self.chamber, mdot, 33e6, 34.34, 101325, circuit)
        with self.assertRaisesRegex(ValueError, "膨胀比"):
            heated_nozzle(self.chamber, 767.8, 33e6, 20., 101325, circuit)
        reverse_heat = [dict(c) for c in cells]
        reverse_heat[0]["Q_W"] = -1.
        with self.assertRaisesRegex(ValueError, "非负壁面热损"):
            heated_nozzle(self.chamber, 767.8, 33e6, 34.34, 101325,
                          {"geometry": geom, "cells": reverse_heat})
        shifted = [dict(c, x_m=c["x_m"]+100) for c in cells]
        with self.assertRaisesRegex(ValueError, "空间位置"):
            heated_nozzle(self.chamber, 767.8, 33e6, 34.34, 101325,
                          {"geometry": geom, "cells": shifted})


if __name__ == "__main__":
    unittest.main()
