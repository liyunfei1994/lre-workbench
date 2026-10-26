"""Source-table, conservation and applicability checks for solid thermal data."""
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wall_materials import (CUZR_DATASET_ID, GRCOP84_DATASET_ID,
                            MaterialPropertyRangeError, default_dataset_for_material,
                            get_wall_dataset, list_wall_datasets)


class SourceTableTests(unittest.TestCase):
    def test_cuzr_published_rows_and_units(self):
        """Qi 2024 p.4 Tables3/4; cp is published in J/(g K), not SI."""
        dataset = get_wall_dataset(CUZR_DATASET_ID)
        for temperature, expected in ((298, 383), (500, 398), (700, 369),
                                      (800, 366), (1000, 346)):
            self.assertEqual(dataset.conductivity(temperature, policy="strict"), expected)
        for temperature, expected in ((298, 376), (373, 404), (474, 421),
                                      (573, 418), (673, 422), (773, 434),
                                      (873, 452), (973, 450), (1000, 454)):
            self.assertEqual(dataset.heat_capacity(temperature, policy="strict"), expected)
        self.assertEqual(dataset.conductivity(600), 383.5)
        # Do not accidentally turn the published nonmonotone cp table into a
        # monotone fit, or silently regularize the 474 K row to 473 K.
        self.assertLess(dataset.heat_capacity(573), dataset.heat_capacity(474))
        self.assertLess(dataset.heat_capacity(473), 421)

    def test_grcop84_uses_regression_mean_and_separate_property_ranges(self):
        dataset = get_wall_dataset(GRCOP84_DATASET_ID)
        # NASA handbook section2.1.5 polynomial, converted J/g/K -> J/kg/K.
        self.assertAlmostEqual(dataset.heat_capacity(300), 382.2514, places=9)
        self.assertEqual(dataset.conductivity_range_K, (80.15, 1173.15))
        self.assertEqual(dataset.heat_capacity_range_K, (173.15, 1173.15))
        dataset.conductivity(100, policy="strict")
        with self.assertRaises(MaterialPropertyRangeError):
            dataset.heat_capacity(100, policy="strict")
        self.assertEqual(dataset.reference_density_kg_m3, 8620)
        self.assertIn("Room-temperature", dataset.density_basis)
        self.assertIsNone(get_wall_dataset(CUZR_DATASET_ID).reference_density_kg_m3)


class ConductionIntegralTests(unittest.TestCase):
    def test_tabulated_conduction_exact_piecewise_balance(self):
        dataset = get_wall_dataset(CUZR_DATASET_ID)
        # Independent trapezoids over the original tabulated k values.
        expected = (202*(383+398)+200*(398+369)+100*(369+366)+200*(366+346))/2
        self.assertEqual(dataset.conductivity_integral(298, 1000), expected)
        self.assertEqual(dataset.conductivity_integral(1000, 298), -expected)
        self.assertEqual(dataset.conductivity_integral(1000, 1000), 0)
        # Constant endpoints are included in the energy integral; clipping the
        # integration bounds would lose these contributions.
        self.assertEqual(dataset.conductivity_integral(200, 1100),
                         98*383 + expected + 100*346)

    def test_grcop84_integral_against_independent_composite_simpson(self):
        dataset = get_wall_dataset(GRCOP84_DATASET_ID)
        a, b, n = 80.15, 1173.15, 8192
        step = (b-a)/n
        weights = [1] + [4 if i % 2 else 2 for i in range(1, n)] + [1]
        quadrature = step/3 * math.fsum(
            weight*dataset.conductivity(a+i*step, policy="strict")
            for i, weight in enumerate(weights))
        integral = dataset.conductivity_integral(a, b, policy="strict")
        self.assertAlmostEqual(integral, quadrature, delta=2e-5)
        self.assertAlmostEqual(dataset.conductivity_integral(a, 500) +
                               dataset.conductivity_integral(500, b), integral, delta=1e-7)

    def test_positive_conduction_potential_and_near_isothermal_precision(self):
        for dataset in list_wall_datasets():
            for temperature in (50., 298., 400., 773., 1000., 1300.):
                delta = 1e-6
                integral = dataset.conductivity_integral(temperature, temperature+delta)
                self.assertGreater(integral, 0)
                self.assertAlmostEqual(integral/(temperature+delta-temperature),
                                       dataset.conductivity(temperature), delta=1e-5)
            previous = dataset.conductivity_integral(50, 50)
            for temperature in range(75, 1401, 25):
                current = dataset.conductivity_integral(50, temperature)
                self.assertGreater(current, previous)
                previous = current


class ApplicabilityTests(unittest.TestCase):
    def test_continuation_is_flagged_without_changing_temperature(self):
        dataset = get_wall_dataset(GRCOP84_DATASET_ID)
        audit = dataset.audit_interval(500, 1199.13733255)
        self.assertEqual(audit["status"], "out_of_range")
        self.assertEqual(audit["actual_temperature_range_K"], [500., 1199.13733255])
        self.assertEqual(audit["data_temperature_range_K"], [80.15, 1173.15])
        self.assertEqual(audit["action"], "constant_endpoint_continuation")
        self.assertEqual(audit["component_manufacturing_match"], "not_assessed")
        self.assertEqual(dataset.conductivity(1300), dataset.conductivity(1173.15))
        self.assertEqual(dataset.audit_interval(1300, 500),
                         dataset.audit_interval(500, 1300))
        strict = dataset.audit_interval(500, 1300, policy="strict")
        self.assertEqual(strict["action"], "reject")
        with self.assertRaises(MaterialPropertyRangeError):
            dataset.conductivity(1300, policy="strict")
        with self.assertRaises(MaterialPropertyRangeError):
            dataset.conductivity_integral(1300, 1300, policy="strict")

    def test_both_wall_faces_are_audited(self):
        dataset = get_wall_dataset(CUZR_DATASET_ID)
        self.assertEqual(dataset.audit_interval(250, 800)["status"], "out_of_range")
        self.assertEqual(dataset.audit_interval(298, 1000)["status"], "within_range")
        self.assertEqual(dataset.audit_interval(250, 1100)["below_range_K"], 48)
        self.assertEqual(dataset.audit_interval(250, 1100)["above_range_K"], 100)
        self.assertEqual(dataset.audit_interval(298, 1000)["structural_qualification"],
                         "not_assessed")

    def test_invalid_temperatures_and_unknown_models_never_fall_back(self):
        dataset = get_wall_dataset(GRCOP84_DATASET_ID)
        for invalid in (float("nan"), float("inf"), -float("inf"), 0, -10):
            with self.assertRaises(ValueError):
                dataset.conductivity(invalid)
            with self.assertRaises(ValueError):
                dataset.heat_capacity(invalid)
            with self.assertRaises(ValueError):
                dataset.conductivity_integral(300, invalid)
            with self.assertRaises(ValueError):
                dataset.audit_interval(invalid, 300)
        with self.assertRaises(ValueError):
            dataset.conductivity(300, policy="silent")
        with self.assertRaises(KeyError):
            dataset.audit_interval(300, 500, property="yield_strength")
        for unsupported in ("cucrzr", "c18150", "grcop42", "inconel718_wall", ""):
            with self.assertRaises(KeyError):
                default_dataset_for_material(unsupported)
        with self.assertRaises(KeyError):
            get_wall_dataset("generic_copper")
        self.assertEqual(default_dataset_for_material("grcop84"), GRCOP84_DATASET_ID)

    def test_snapshots_are_independent_immutable_and_traceable(self):
        dataset = get_wall_dataset(CUZR_DATASET_ID)
        snapshot = dataset.metadata()
        self.assertEqual(json.loads(json.dumps(snapshot, allow_nan=False)), snapshot)
        self.assertEqual(snapshot["sources"][0]["doi"], "10.1016/j.ast.2023.108782")
        self.assertEqual(len(snapshot["data_sha256"]), 64)
        self.assertNotEqual(snapshot["material_id"], snapshot["dataset_id"])
        self.assertIn("not CuCrZr", snapshot["composition"])
        original_hash = snapshot["data_sha256"]
        snapshot["conductivity_property"]["points"][0][1] = -1
        snapshot["manufacturing_state"]["preparation"].append("LPBF")
        self.assertEqual(dataset.metadata()["data_sha256"], original_hash)
        self.assertEqual(dataset.conductivity(298), 383)
        with self.assertRaises(FrozenInstanceError):
            dataset.material_id = "cucrzr"


if __name__ == "__main__":
    unittest.main()
