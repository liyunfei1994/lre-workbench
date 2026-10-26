"""Material interfaces must split the shared physical cooling contour."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from regen_geometry import build_regen_geometry


GEOMETRY = dict(r_t=.1, r_c=.15, L_cyl=.3, expansion_ratio=16.,
                n_points=60, n_channels=300)


class MaterialZoneGeometryTests(unittest.TestCase):
    def test_interfaces_split_cells_without_changing_area_or_path(self):
        for coverage in (0., 4.):
            for reverse in (False, True):
                with self.subTest(coverage=coverage, reverse=reverse):
                    baseline = build_regen_geometry(**GEOMETRY, cover_eps=coverage,
                                                    counterflow=reverse)
                    ordered = sorted(baseline["nodes"], key=lambda n: n["x_m"])
                    # One interface in the chamber and one on the divergent
                    # section; deliberately avoid existing sampled vertices.
                    segments = [next((a, b) for a, b in zip(ordered, ordered[1:])
                                     if a["x_m"] > threshold)
                                for threshold in (.05, baseline["x_throat_m"])]
                    boundaries = [a["x_m"] + .37*(b["x_m"]-a["x_m"])
                                  for a, b in segments]
                    result = build_regen_geometry(**GEOMETRY, cover_eps=coverage,
                        counterflow=reverse, material_boundaries_m=boundaries[::-1])
                    self.assertEqual(result["material_boundaries_m"], boundaries)
                    self.assertEqual(len(result["nodes"]), len(baseline["nodes"])+2)
                    self.assertAlmostEqual(result["hot_area_m2"], baseline["hot_area_m2"], places=13)
                    self.assertAlmostEqual(result["path_length_m"], baseline["path_length_m"], places=13)
                    self.assertEqual(result["coverage_end_x_m"], baseline["coverage_end_x_m"])
                    for boundary, (a, b) in zip(boundaries, segments):
                        node = next(n for n in result["nodes"] if n["x_m"] == boundary)
                        self.assertAlmostEqual(node["r_m"], a["r_m"]+.37*(b["r_m"]-a["r_m"]), places=14)
                    for a, b, cell in zip(result["nodes"], result["nodes"][1:], result["cells"]):
                        low, high = sorted((a["x_m"], b["x_m"]))
                        self.assertFalse(any(low < x < high for x in boundaries))
                        self.assertGreater(b["s_m"], a["s_m"])
                        self.assertGreater(cell["ds_m"], 0)
                        self.assertAlmostEqual(cell["dA_hot_m2"],
                            math.pi*(a["r_m"]+b["r_m"])*cell["ds_m"], places=14)
                    gas_covered = [(n["x_m"], n["r_m"]) for n in result["full_nodes"]
                                   if n["cooling_covered"]]
                    coolant = sorted((n["x_m"], n["r_m"]) for n in result["nodes"])
                    self.assertEqual(gas_covered, coolant)
                    old_extension = [n for n in baseline["full_nodes"] if not n["cooling_covered"]]
                    new_extension = [n for n in result["full_nodes"] if not n["cooling_covered"]]
                    self.assertEqual([(n["x_m"], n["r_m"]) for n in old_extension],
                                     [(n["x_m"], n["r_m"]) for n in new_extension])

    def test_endpoints_and_existing_vertices_do_not_duplicate_nodes(self):
        baseline = build_regen_geometry(**GEOMETRY, cover_eps=4.)
        interfaces = [0., baseline["x_throat_m"], baseline["coverage_end_x_m"]]
        result = build_regen_geometry(**GEOMETRY, cover_eps=4., material_boundaries_m=interfaces)
        self.assertEqual(result["nodes"], baseline["nodes"])
        self.assertEqual(result["cells"], baseline["cells"])
        self.assertEqual(result["full_nodes"], baseline["full_nodes"])
        xs = [n["x_m"] for n in result["nodes"]]
        self.assertEqual(len(xs), len(set(xs)))

    def test_invalid_boundaries_fail_instead_of_clamping(self):
        baseline = build_regen_geometry(**GEOMETRY, cover_eps=4.)
        outside_cooled_but_on_nozzle = .5*(baseline["coverage_end_x_m"]+baseline["full_exit_x_m"])
        for boundaries in ((float("nan"),), (float("inf"),), (-.001,),
                           (outside_cooled_but_on_nozzle,), (.1, .1),
                           (0., -0.), ("invalid",), None):
            with self.subTest(boundaries=boundaries):
                with self.assertRaisesRegex(ValueError, "material boundaries"):
                    build_regen_geometry(**GEOMETRY, cover_eps=4., material_boundaries_m=boundaries)


if __name__ == "__main__":
    unittest.main()
