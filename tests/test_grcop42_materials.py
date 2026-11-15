"""Published GRCop-42 regressions, SI conversion and applicability limits."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.materials.wall import (CUZR_DATASET_ID, GRCOP42_DATASET_ID, GRCOP84_DATASET_ID,
                            MaterialPropertyRangeError, default_dataset_for_material,
                            get_wall_dataset)
from lre_workbench.components.cooling.wall_thermal import audit_cell, validate_manufacturing_state


class GRCop42SourceTests(unittest.TestCase):
    def setUp(self):
        self.dataset = get_wall_dataset(GRCOP42_DATASET_ID)

    def test_conductivity_equation_uses_kelvin_divided_by_1000(self):
        # Chen 2023 p.5 Eq.(3), independently evaluated in source coordinates.
        for celsius in (25, 100, 200, 300, 400, 500, 600, 700):
            kelvin = celsius + 273.15
            x = kelvin / 1000
            published = -39.86*x**3 + 4.17*x**2 + 0.97*x + 329.14
            self.assertAlmostEqual(self.dataset.conductivity(kelvin, policy="strict"),
                                   published, places=10)
        self.assertAlmostEqual(self.dataset.conductivity(298.15), 328.74345861543856)
        self.assertAlmostEqual(self.dataset.conductivity(973.15), 297.29831939509234)
        self.assertEqual(default_dataset_for_material("grcop42"), GRCOP42_DATASET_ID)

    def test_heat_capacity_is_an_estimate_consistent_with_independent_table(self):
        # Table 3 is rounded to 0.001 J/(g K). Eq.(7) is a separate rounded
        # regression; its residual to those rows is <1 J/(kg K), not zero.
        table = ((25, .384), (100, .394), (200, .406), (300, .416),
                 (400, .424), (500, .431), (600, .439), (700, .449))
        for celsius, published_j_g_k in table:
            value = self.dataset.heat_capacity(celsius+273.15, policy="strict")
            self.assertAlmostEqual(value, 1000*published_j_g_k, delta=1.0)
        self.assertEqual(self.dataset.heat_capacity_property.statistic,
                         "composition_weighted_estimate")
        self.assertIsNone(self.dataset.reference_density_kg_m3)

    def test_integrated_conductivity_preserves_source_polynomial_and_endpoints(self):
        # Simpson is exact for the source cubic; no reliance on the primitive.
        a, b = 298.15, 973.15
        def source_k(t):
            x = t/1000
            return -39.86*x**3 + 4.17*x**2 + .97*x + 329.14
        expected = (b-a)/6 * (source_k(a)+4*source_k((a+b)/2)+source_k(b))
        self.assertAlmostEqual(self.dataset.conductivity_integral(a, b, policy="strict"),
                               expected, places=8)
        continued = (a-250)*source_k(a) + expected + (1100-b)*source_k(b)
        self.assertAlmostEqual(self.dataset.conductivity_integral(250, 1100),
                               continued, places=8)

    def test_data_limits_do_not_become_allowable_temperatures(self):
        self.assertEqual(self.dataset.conductivity_range_K, (298.15, 973.15))
        self.assertEqual(self.dataset.heat_capacity_range_K, (298.15, 973.15))
        for temperature in (298.14, 973.16):
            with self.assertRaises(MaterialPropertyRangeError):
                self.dataset.conductivity(temperature, policy="strict")
            with self.assertRaises(MaterialPropertyRangeError):
                self.dataset.heat_capacity(temperature, policy="strict")
        cell = {"T_wall_hot_K": 1100, "T_wall_cold_K": 290,
                "fin_reference_temperature_K": 650,
                "wall_conductivity_WmK": self.dataset.conductivity(650),
                "x_m": .1, "s_m": .2}
        state = validate_manufacturing_state("lpbf_hip_chen2023")
        audit = audit_cell(cell, self.dataset, state)
        self.assertEqual(audit["status"], "out_of_range")
        self.assertEqual(audit["actual_temperature_range_K"], [290., 1100.])
        self.assertEqual(audit["manufacturing_match"], "matched")
        self.assertEqual(audit["structural_qualification"], "not_assessed")
        self.assertEqual(audit["action"], "constant_endpoint_continuation")
        self.assertEqual(audit_cell(cell, self.dataset, "unspecified")["manufacturing_match"],
                         "unknown")
        self.assertEqual(audit_cell(cell, self.dataset, "other")["manufacturing_match"],
                         "mismatch")

    def test_provenance_and_historical_dataset_snapshots(self):
        metadata = self.dataset.metadata()
        self.assertEqual(metadata["sources"][0]["doi"], "10.1016/j.mtcomm.2023.106665")
        self.assertEqual(metadata["manufacturing_state"]["state_id"], "lpbf_hip_chen2023")
        # Adding the new default must not rewrite any existing dataset's audit
        # snapshot. These hashes were captured immediately before registration.
        expected_hashes = {
            GRCOP84_DATASET_ID: "7630714ab84c1ea040fe29f2f088ea10394685512d4b6fbe93ccb03f3c9658ee",
            CUZR_DATASET_ID: "71d3f945a673157ac282ba127d82222e0a48695ece30f40f1ea492941091c738",
        }
        for identity, expected in expected_hashes.items():
            self.assertEqual(get_wall_dataset(identity).metadata()["data_sha256"], expected)


if __name__ == "__main__":
    unittest.main()
