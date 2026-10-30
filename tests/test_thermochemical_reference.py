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
    def test_named_element_balance_rejects_unit_sum_but_wrong_species(self):
        atoms = comparison.CEA_ATOMIC_WEIGHTS
        metadata = {
            "CH4": {"elements": {"C": 1, "H": 4}, "molecular_weight_kg_kmol": atoms["C"]+4*atoms["H"]},
            "O2": {"elements": {"O": 2}, "molecular_weight_kg_kmol": 2*atoms["O"]},
            "HCO": {"elements": {"C": 1, "H": 1, "O": 1}, "molecular_weight_kg_kmol": sum(atoms.values())},
        }
        reactants = {"fuel_species": "CH4", "fuel_molecular_weight_kg_kmol": metadata["CH4"]["molecular_weight_kg_kmol"]}
        valid = {"chamber": {"mass_fractions": {"CH4": 1/4.6, "O2": 3.6/4.6}}}
        self.assertTrue(comparison._cea_element_conservation(valid, metadata, reactants, 3.6)["verified"])
        # A name/array permutation can preserve sum(Y)=1 and still violate the
        # feed's elemental inventory. It must not acquire a verified marker.
        with self.assertRaisesRegex(ValueError, "元素质量守恒"):
            comparison._cea_element_conservation({"chamber": {"mass_fractions": {"HCO": 1.0}}}, metadata, reactants, 3.6)
        missing = comparison._cea_element_conservation({"chamber": {"mass_fractions": {"unavailable": 1.0}}}, metadata, reactants, 3.6)
        self.assertFalse(missing["verified"])
        self.assertIsNone(missing["max_residual"])
        self.assertEqual(missing["residuals"], {})

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
        self.assertEqual(comparison._provenance(comparison.get_propellant("lox_methane"), request)["phase"], gas.name)
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
                verification = point["cea"]["diagnostics"]["composition_verification"]
                self.assertEqual(verification["status"], "verified")
                self.assertEqual(verification["reader_version"], 2)
                self.assertTrue(verification["name_value_readback_verified"])
                self.assertTrue(verification["element_conservation_verified"])
                self.assertLess(verification["max_element_mass_fraction_residual"], 1e-7)
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
                    self.assertGreater(composition["H2O"], 0.35)
                    self.assertLess(composition["HCO"], 0.001)

    def test_same_only_object_repeated_conditions_modes_and_name_mw_guard(self):
        script = '''
import json
from unittest.mock import patch
from webapp import thermo_compare as c
request = c.normalize_request({"pc_pa": 33e6, "T_fuel_K": 100, "T_ox_K": 70,
    "of_values": [3.6], "eps_values": [34.34], "product_policy": "matched_cho_gas",
    "methane_thermo_dataset": "gri30_highT"})
reactants = c._comparison_reactants(request, c.get_propellant(request["propellant_pair"]))
c._prepare_cea_scratch()
ox, fuel = c.thermo._ensure_cea_custom_reactants(70, 100, 33e6, "CH4", reactants)
cea = c._cea_object(request, ox, fuel)
reports = []
with c._LOCK, patch.object(c, "_cea_object", return_value=cea):
    for repeat in range(2):
        for mode in c.MODES:
            for of in (3.2, 3.6):
                args = {"Pc": 33e6/101325*c.CEA_PSIA_PER_ATM, "MR": of, "eps": 34.34}
                flags = {"frozen": int(mode != "equilibrium"), "frozenAtThroat": int(mode == "frozen_throat")}
                # The public macro getters are independent of our composition
                # reader. Repeated readback must not change their numerical result.
                isp, cstar, tc = cea.get_IvacCstrTc(**args, **flags)
                result = c._cea_point(dict(request, mode=mode), of, 34.34, reactants)
                reports.append({"repeat": repeat, "mode": mode, "of": of,
                    "macro_before": [isp, cstar*.3048, tc/1.8], "result": result})
    metadata = {name: dict(value) for name, value in c._cea_species_metadata().items()}
    metadata["H2O"]["molecular_weight_kg_kmol"] = 29.01804
    try:
        c._cea_mass_fractions(cea, args, flags, metadata)
    except ValueError as error:
        identity_error = str(error)
    else:
        identity_error = None
print("READBACK_JSON="+json.dumps({"reports": reports, "identity_error": identity_error}))
'''
        run = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(next(line.split("=", 1)[1] for line in run.stdout.splitlines() if line.startswith("READBACK_JSON=")))
        self.assertIn("名称与分子量错位", result["identity_error"])
        first = {}
        for report in result["reports"]:
            key = (report["mode"], report["of"])
            data = report["result"]
            with self.subTest(repeat=report["repeat"], mode=key[0], of=key[1]):
                self.assertEqual(data["diagnostics"]["composition_verification"]["status"], "verified")
                for metric, before in zip(("Isp_vac_s", "Cstar_m_s", "Tc_K"), report["macro_before"]):
                    self.assertAlmostEqual(data["metrics"][metric], before, places=9)
                if report["repeat"]:
                    self.assertEqual(data["stations"], first[key]["stations"])
                    self.assertEqual(data["metrics"], first[key]["metrics"])
                else:
                    first[key] = data

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
