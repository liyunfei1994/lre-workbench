"""Independent reference boundaries, database isolation and real CEA invariants."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webapp import thermo_compare as comparison
import thermo


class ReferenceRequestTests(unittest.TestCase):
    def test_candidates_require_explicit_common_methane_benchmark(self):
        for request in ({"product_policy": "arbitrary"}, {"cantera_dataset": "arbitrary"},
                        {"cantera_dataset": "nasa_gas_candidate"},
                        {"product_policy": "matched_cho_gas", "propellant_pair": "lox_kerosene"}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                comparison.normalize_request(request)
        request = comparison.normalize_request({})
        self.assertEqual(request["product_policy"], "native_databases")
        self.assertEqual(request["cantera_dataset"], "production")

    @unittest.skipUnless(thermo.CANTERA_AVAILABLE, "Cantera required")
    def test_candidate_range_and_atomic_weights_do_not_mutate_production(self):
        original = thermo._get_gas("CH4")
        weights = original.atomic_weights.copy()
        request = comparison.normalize_request({"product_policy": "matched_cho_gas", "cantera_dataset": "nasa_gas_candidate"})
        gas = comparison._comparison_gas(request, "CH4")
        self.assertEqual(tuple(gas.species_names), comparison.COMMON_CHO_SPECIES)
        self.assertEqual(gas.thermo_model, "ideal-gas")
        for species in gas.species():
            self.assertEqual((species.thermo.min_temp, species.thermo.max_temp), (200.0, 6000.0))
        for element, weight in comparison.CEA_ATOMIC_WEIGHTS.items():
            self.assertEqual(gas.atomic_weight(element), weight)
        self.assertEqual(list(original.atomic_weights), list(weights))
        self.assertEqual(original.n_species, 53)


@unittest.skipUnless(thermo.ROCKETCEA_AVAILABLE and thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE,
                     "Independent check requires installed Cantera, CoolProp and real RocketCEA")
class RealReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # All mutable Fortran common-block state stays in one isolated process.
        script = '''
import json
from webapp.thermo_compare import compare_thermochemistry, MODES
reports = []
for mode in MODES:
    for policy, dataset in (("native_databases", "production"), ("matched_cho_gas", "production"),
                            ("matched_cho_gas", "nasa_gas_candidate")):
        r = compare_thermochemistry({"pc_pa": 33e6, "T_fuel_K": 100, "T_ox_K": 70,
            "of_values": [3.6], "eps_values": [34.34], "mode": mode,
            "product_policy": policy, "cantera_dataset": dataset})
        reports.append(r)
print("REFERENCE_JSON="+json.dumps(reports))
'''
        run = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.reports = json.loads(next(line.split("=", 1)[1] for line in run.stdout.splitlines()
                                     if line.startswith("REFERENCE_JSON=")))

    def test_same_species_same_inlet_enthalpy_and_cea_final_product_retained(self):
        for report in self.reports:
            request, point = report["request"], report["points"][0]
            with self.subTest(mode=request["mode"], policy=request["product_policy"], dataset=request["cantera_dataset"]):
                self.assertEqual(report["counts"]["paired_success"], 1,
                                 (point["cantera"]["reason"], point["cea"]["reason"]))
                restricted = request["product_policy"] == "matched_cho_gas"
                self.assertEqual(point["comparable"]["products_aligned"], restricted)
                self.assertEqual(point["cea"]["diagnostics"]["only_card_verified"], restricted)
                for backend in ("cantera", "cea"):
                    result = point[backend]
                    self.assertLess(abs(result["diagnostics"]["chamber_enthalpy_residual_Jkg"]), 1.0)
                    for station in result["stations"].values():
                        self.assertAlmostEqual(sum(station["mass_fractions"].values()), 1.0, places=7)
                        if restricted:
                            names = {name.lstrip("*") for name in station["mass_fractions"]}
                            self.assertTrue(names.issubset(comparison.COMMON_CHO_SPECIES))
                if restricted:
                    # O2 is the last CEA product in this ONLY set and was omitted
                    # by RocketCEA 1.2.3's public getter. It has material abundance.
                    composition = {name.lstrip("*"): value for name, value in
                                   point["cea"]["stations"]["chamber"]["mass_fractions"].items()}
                    self.assertGreater(composition["O2"], 0.01)

    def test_high_temperature_is_exposed_and_candidate_stays_in_its_range(self):
        for report in self.reports:
            point, request = report["points"][0], report["request"]
            with self.subTest(mode=request["mode"], policy=request["product_policy"], dataset=request["cantera_dataset"]):
                ct_range = point["cantera"]["diagnostics"]["station_temperature_ranges"]["chamber"]
                self.assertGreater(ct_range["temperature_K"], 3500)
                if request["cantera_dataset"] == "production":
                    self.assertIn("H2O", ct_range["outside_species"])
                    self.assertIn("CO2", ct_range["outside_species"])
                else:
                    self.assertFalse(ct_range["outside_species"])
                for station in point["cea"]["diagnostics"]["station_temperature_ranges"].values():
                    self.assertFalse(station["outside_species"])
                    self.assertFalse(station["unverified_species"])

    def test_candidate_and_native_cache_identities_are_separate(self):
        for mode in comparison.MODES:
            rows = [r for r in self.reports if r["request"]["mode"] == mode]
            matched = [r for r in rows if r["request"]["product_policy"] == "matched_cho_gas"]
            first, second = [r["points"][0] for r in matched]
            # CEA has exactly the same problem for both candidate CT datasets.
            for metric in comparison.METRICS:
                self.assertAlmostEqual(first["cea"]["metrics"][metric], second["cea"]["metrics"][metric], places=10)
            self.assertNotAlmostEqual(first["cantera"]["metrics"]["Tc_K"], second["cantera"]["metrics"]["Tc_K"], places=3)


if __name__ == "__main__":
    unittest.main()
