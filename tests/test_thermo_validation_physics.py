"""Physical invariants and real, isolated optional CEA integration checks."""
from pathlib import Path
import json
import math
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webapp import thermo_compare as comparison
import thermo


class ComparisonRequestTests(unittest.TestCase):
    def test_bounds_modes_and_grid_limit(self):
        invalid = ({"mode": "unknown"}, {"pc_pa": float("nan")}, {"pc_pa": 10**1000}, {"of_values": [True]},
                   {"eps_values": [0.9]}, {"T_fuel_K": -2}, {"unexpected": 2},
                   {"of_values": [3.4]*16, "eps_values": [10, 20, 40, 80]},
                   {"of_values": [3.4]*21}, {"eps_values": [40]*21})
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                comparison.normalize_request(request)
        self.assertEqual(comparison.normalize_request({"propellant_pair": "lox_kerosene"})["T_fuel_K"], 300)

    def test_desktop_scan_and_bounded_larger_grid_are_accepted(self):
        of_values = [2.5 + 2.5 * index / 7 for index in range(8)]
        request = comparison.normalize_request({"of_values": of_values, "eps_values": [40, 80, 120]})
        self.assertEqual(request["of_values"], of_values)
        self.assertEqual(request["eps_values"], [40, 80, 120])
        self.assertEqual(len(request["of_values"]) * len(request["eps_values"]), 24)
        for of_count, eps_count in ((20, 3), (3, 20)):
            with self.subTest(of_count=of_count, eps_count=eps_count):
                request = comparison.normalize_request({"of_values": [3.4]*of_count, "eps_values": [40]*eps_count})
                self.assertEqual(len(request["of_values"]) * len(request["eps_values"]), 60)
        self.assertEqual(comparison.comparison_capabilities()["max_points"], 60)


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "Cantera and CoolProp required")
class ComparisonPhysicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = {}
        with patch.object(thermo, "ROCKETCEA_AVAILABLE", False):
            for pair in ("lox_methane", "lox_kerosene"):
                for mode in comparison.MODES:
                    cls.reports[pair, mode] = comparison.compare_thermochemistry({"propellant_pair": pair, "mode": mode})

    def test_all_fuels_and_modes_close_mass_energy_entropy_and_area(self):
        for key, report in self.reports.items():
            with self.subTest(case=key):
                result = report["points"][0]["cantera"]
                self.assertEqual(result["status"], "ok", result["reason"])
                d = result["diagnostics"]
                self.assertLess(d["element_residual_max"], 1e-8)
                self.assertLess(abs(d["chamber_enthalpy_residual_Jkg"]), 1.0)
                self.assertLess(abs(d["energy_residual_Jkg"]), 1e-4)
                self.assertLess(abs(d["entropy_residual_JkgK"]), 1e-4)
                self.assertLess(abs(d["area_relative_residual"]), 1e-7)
                self.assertLess(abs(d["throat_sonic_residual"]), 2e-5)
                metrics = result["metrics"]
                self.assertAlmostEqual(metrics["Isp_vac_s"]*thermo._G0,
                                       metrics["Cstar_m_s"]*metrics["Cf_vac"], places=9)

    def test_equilibrium_throat_is_mass_flux_maximum_and_not_frozen_sonic(self):
        result = self.reports["lox_methane", "equilibrium"]["points"][0]["cantera"]
        chamber, throat = result["stations"]["chamber"], result["stations"]["throat"]
        gas = thermo._get_gas("CH4")
        fluxes = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for factor in (0.995, 1.0, 1.005):
                gas.TPY = chamber["temperature_K"], chamber["pressure_Pa"], chamber["mass_fractions"]
                gas.SP = chamber["entropy_JkgK"], throat["pressure_Pa"]*factor
                gas.equilibrate("SP")
                fluxes.append(gas.density_mass*math.sqrt(2*(chamber["enthalpy_Jkg"]-gas.enthalpy_mass)))
        self.assertGreater(fluxes[1], fluxes[0])
        self.assertGreater(fluxes[1], fluxes[2])
        self.assertLess(throat["gamma_isentropic"], throat["cp_cv_frozen"]-0.02)

    def test_expansion_modes_freeze_at_correct_section(self):
        for pair in ("lox_methane", "lox_kerosene"):
            results = {mode: self.reports[pair, mode]["points"][0]["cantera"] for mode in comparison.MODES}
            with self.subTest(pair=pair):
                self.assertGreater(results["equilibrium"]["metrics"]["Isp_vac_s"], results["frozen_throat"]["metrics"]["Isp_vac_s"])
                self.assertGreater(results["frozen_throat"]["metrics"]["Isp_vac_s"], results["frozen_chamber"]["metrics"]["Isp_vac_s"])
                self.assertAlmostEqual(results["equilibrium"]["metrics"]["Cstar_m_s"], results["frozen_throat"]["metrics"]["Cstar_m_s"], places=8)
                for mode, upstream in (("frozen_chamber", "chamber"), ("frozen_throat", "throat")):
                    states = results[mode]["stations"]
                    before, after = states[upstream]["mass_fractions"], states["exit"]["mass_fractions"]
                    self.assertLess(max(abs(value-after.get(name, 0)) for name, value in before.items()), 1e-12)

    def test_shared_real_fluid_enthalpy_and_cea_molar_card_conversion(self):
        for fuel, temperature in (("CH4", 110.023), ("c12h26", 300.023)):
            with self.subTest(fuel=fuel):
                state = thermo.validation_reactants(temperature, 90.023, 10e6, fuel)
                self.assertAlmostEqual(state["fuel_enthalpy_Jkg"], thermo.reactant_enthalpy(fuel, temperature, 10e6, fuel), places=7)
                second = thermo.validation_reactants(temperature, 90.023, 20e6, fuel)
                self.assertNotEqual(state["fuel_enthalpy_Jkg"], second["fuel_enthalpy_Jkg"])
                with patch.object(thermo, "ROCKETCEA_AVAILABLE", True), patch.dict(thermo._CEA_CUSTOM_REGISTERED, clear=True), \
                     patch.object(thermo, "_add_new_ox", create=True) as oxidizer, patch.object(thermo, "_add_new_fuel", create=True) as fuel_card:
                    first_names = thermo._ensure_cea_custom_reactants(90.023, temperature, 10e6, fuel, state)
                    for role, card in (("oxidizer", oxidizer), ("fuel", fuel_card)):
                        h_cal = float(re.search(r"h,cal=([^ ]+)", card.call_args.args[1]).group(1))
                        h_jkg = h_cal*4184/state[role+"_molecular_weight_kg_kmol"]
                        self.assertAlmostEqual(h_jkg, state[role+"_enthalpy_Jkg"], delta=1e-4)
                    second_names = thermo._ensure_cea_custom_reactants(90.023, temperature, 20e6, fuel, second)
                    self.assertNotEqual(first_names, second_names)

    def test_missing_cea_never_creates_numbers_or_paired_success(self):
        report = self.reports["lox_methane", "equilibrium"]
        point = report["points"][0]
        self.assertEqual(point["cea"]["status"], "unavailable")
        self.assertTrue(point["cea"]["reason"])
        self.assertEqual(point["cea"]["metrics"], {})
        self.assertEqual(point["differences"], {})
        self.assertEqual(report["counts"]["paired_success"], 0)
        self.assertFalse(report["scope"]["products_aligned"])

    def test_point_failure_retains_reason_and_reports_progress(self):
        progress = []
        with patch.object(thermo, "ROCKETCEA_AVAILABLE", False), patch.object(comparison, "_cantera_point", side_effect=ValueError("explicit solver failure")):
            report = comparison.compare_thermochemistry({}, lambda done, total, point: progress.append((done, total, point)))
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["points"][0]["cantera"]["reason"], "explicit solver failure")
        self.assertEqual(progress[0][:2], (1, 1))

    def test_kerosene_identity_extrapolation_and_mechanism_hash_are_explicit(self):
        report = self.reports["lox_kerosene", "equilibrium"]
        self.assertEqual(report["scope"]["propellant"]["fuel_species"], "c12h26")
        self.assertEqual(report["provenance"]["mechanism"], "nDodecane_Reitz.yaml")
        self.assertEqual(len(report["provenance"]["mechanism_sha256"]), 64)
        point = report["points"][0]["cantera"]
        self.assertTrue(point["diagnostics"]["thermo_extrapolation"])
        self.assertTrue(any("外推" in item for item in point["warnings"]))


@unittest.skipUnless(thermo.ROCKETCEA_AVAILABLE and thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE,
                     "Real optional RocketCEA integration requires all three libraries")
class RealCEAIntegrationTests(unittest.TestCase):
    def test_two_fuels_three_modes_in_isolated_process(self):
        script = """
import json
from webapp.thermo_compare import compare_thermochemistry
reports = []
for pair in ('lox_methane', 'lox_kerosene'):
    for mode in ('equilibrium', 'frozen_chamber', 'frozen_throat'):
        r = compare_thermochemistry({'propellant_pair': pair, 'mode': mode})
        p = r['points'][0]
        reports.append({'pair': pair, 'mode': mode, 'counts': r['counts'],
            'ct': p['cantera']['metrics'], 'cea': p['cea']['metrics'],
            'error': p['cea']['reason'], 'has_cea_output': bool(p['cea']['diagnostics'].get('cea_output')),
            'products_aligned': p['comparable']['products_aligned']})
print('THERMO_JSON=' + json.dumps(reports))
"""
        run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 0, run.stderr)
        line = next(line for line in run.stdout.splitlines() if line.startswith("THERMO_JSON="))
        reports = json.loads(line.split("=", 1)[1])
        self.assertEqual(len(reports), 6)
        for report in reports:
            with self.subTest(pair=report["pair"], mode=report["mode"]):
                self.assertEqual(report["counts"]["paired_success"], 1, report["error"])
                self.assertTrue(report["has_cea_output"])
                self.assertFalse(report["products_aligned"])
                for metrics in (report["ct"], report["cea"]):
                    self.assertTrue(all(math.isfinite(value) and value > 0 for value in metrics.values()))
                    self.assertAlmostEqual(metrics["Isp_vac_s"]*thermo._G0,
                                           metrics["Cstar_m_s"]*metrics["Cf_vac"], places=8)
        for pair in ("lox_methane", "lox_kerosene"):
            rows = {item["mode"]: item["cea"] for item in reports if item["pair"] == pair}
            self.assertGreater(rows["equilibrium"]["Isp_vac_s"], rows["frozen_throat"]["Isp_vac_s"])
            self.assertGreater(rows["frozen_throat"]["Isp_vac_s"], rows["frozen_chamber"]["Isp_vac_s"])
            self.assertAlmostEqual(rows["equilibrium"]["Cstar_m_s"], rows["frozen_throat"]["Cstar_m_s"], places=6)


if __name__ == "__main__":
    unittest.main()
