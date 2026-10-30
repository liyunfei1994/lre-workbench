"""Thermochemical evidence, explicit reductions, cancellation, and native isolation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from task_control import CancellationToken, TaskCancelled, cancellation_scope
from webapp import gas_screening as screen

try:
    import cantera as ct
except ImportError:
    ct = None


def fixture(composition=None):
    data = Path(ct.__file__).resolve().parent / "data" / "gri30_highT.yaml"
    gas = ct.Solution(yaml=data.read_text(encoding="utf-8"))
    record = {"calculation": {"states": {}, "components": {}},
              "turbine_gas_properties": {"turbines": {}}}
    for side in ("ox", "fuel"):
        inlet = 747.56 if side == "ox" else 848.35
        default_y = {"O2": .91787, "H2O": .03697, "CO2": .04516} if side == "ox" else {"CH4": .8166, "H2O": .0711, "CO2": .1012, "H2": .0024, "CO": .0062, "C2H6": .0025}
        y = composition if composition is not None else default_y
        gas.TPY = inlet, 64e6, y
        if composition is None:
            gas.equilibrate("TP")
        y = screen._composition(gas)
        model = {"mechanism": data.name, "phase": gas.name, "gas_eos": "ideal-gas",
                 "mechanism_sha256": hashlib.sha256(data.read_bytes()).hexdigest()}
        capture = {"model": model, "composition_mass_fractions": y, "endpoint_properties": {}}
        for endpoint, temperature, pressure in (("inlet", inlet, 64e6), ("outlet", inlet-60, 40e6)):
            gas.TPY = temperature, pressure, y
            sid = side + ("_preburner_outlet" if endpoint == "inlet" else "_turbine_exhaust")
            record["calculation"]["states"][sid] = {
                "temperature": gas.T, "pressure": gas.P, "mass_flow": 100.,
                "enthalpy": gas.enthalpy_mass, "composition": screen._composition(gas),
                "metadata": {"reference_id": "cantera_gri30_highT_coolprop_298p15K_ideal_reference_v2",
                             "thermochemical_data": data.name}}
            capture["endpoint_properties"][endpoint] = screen._properties(gas)
        record["turbine_gas_properties"]["turbines"][side] = capture
        record["calculation"]["components"][side+"_preburner_turbine"] = {"outputs": {
            "state_basis": "total_to_total", "p_out_Pa": 40e6, "eta_turbine": .85}}
    return record


class GasScreenRequestTests(unittest.TestCase):
    def test_defaults_are_four_record_stations(self):
        value = screen.normalize_screen_request()
        self.assertEqual(len(value["stations"]), 4)
        self.assertTrue(value["compare_refprop"])

    def test_request_rejects_implicit_reduction_thresholds_and_bad_flags(self):
        for value in ({"cutoff": .1}, {"allow_reduced": "yes"}, {"stations": []},
                      {"stations": ["main_chamber"]}, {"stations": ["ox_turbine_exhaust"]*2}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                screen.normalize_screen_request(value)

    def test_cancellation_is_not_physics_failure(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(TaskCancelled), cancellation_scope(token):
            screen.screen_gas_record({})

    def test_refprop_strict_flags_are_restored_even_after_failure(self):
        try:
            import CoolProp.CoolProp as CP
        except ImportError:
            self.skipTest("CoolProp not installed")
        before = CP.get_config_as_json_string()
        with screen._PROPERTY_LOCK:
            with self.assertRaisesRegex(RuntimeError, "native failed"):
                with screen._strict_refprop_configuration(CP) as flags:
                    self.assertTrue(CP.get_config_bool(CP.REFPROP_DONT_ESTIMATE_INTERACTION_PARAMETERS))
                    self.assertFalse(CP.get_config_bool(CP.REFPROP_IGNORE_ERROR_ESTIMATED_INTERACTION_PARAMETERS))
                    self.assertFalse(CP.get_config_bool(CP.REFPROP_USE_PENGROBINSON))
                    self.assertEqual(flags["REFPROP_ERROR_THRESHOLD"], 0)
                    raise RuntimeError("native failed")
        self.assertEqual(CP.get_config_as_json_string(), before)


@unittest.skipIf(ct is None, "Cantera required for actual thermodynamic checks")
class GasScreenNumericsTests(unittest.TestCase):
    def setUp(self):
        self.record = fixture()

    def test_expansion_range_violation_is_not_reported_as_normal(self):
        gas = ct.Solution(yaml=(Path(ct.__file__).resolve().parent / "data" / "gri30.yaml").read_text(), name="gri30")
        gas.TPY = 350, 60e6, {"O2": .92, "H2O": .03, "CO2": .05}
        before = gas.state.copy()
        result = screen._expansion(gas, 1e5, .85)
        self.assertEqual(result["status"], "computed_outside_declared_range")
        self.assertTrue(any(not row["within_declared_range"] for row in result["domain_check"]["samples"]))
        self.assertTrue((gas.state == before).all())

    def test_expansion_rejects_nonphysical_efficiency(self):
        gas = ct.Solution(yaml=(Path(ct.__file__).resolve().parent / "data" / "gri30.yaml").read_text(), name="gri30")
        gas.TP = 750, 60e6
        for eta in (0, -1, 1.1, float('nan')):
            with self.subTest(eta=eta):
                self.assertEqual(screen._expansion(gas, 40e6, eta)["status"], "unavailable")

    def test_aligned_enthalpy_entropy_differences_are_reference_invariant(self):
        a = {"enthalpy_j_kg": -1e6, "entropy_j_kg_k": 5000, "density_kg_m3": 300}
        b = {"enthalpy_j_kg": -1.1e6, "entropy_j_kg_k": 4900, "density_kg_m3": 320}
        initial = screen._difference(a, b)
        for key in ("enthalpy_j_kg", "entropy_j_kg_k"):
            self.assertIsNone(initial[key]["relative_difference"])
            a[key] += 2e6; b[key] += 2e6
        shifted = screen._difference(a, b)
        self.assertEqual(initial, shifted)
        self.assertAlmostEqual(initial["density_kg_m3"]["relative_difference"], -20/320)

    def test_intermediate_expansion_domain_violation_is_retained(self):
        pressures = []
        def isentropic(p):
            pressures.append(p)
            return 1e5-p, 0
        def inspect():
            return {"temperature_k": 500, "pressure_pa": pressures[-1], "within_declared_range": len(pressures) != 4}
        result = screen._sample_expansion(1e5, 1e4, None, isentropic, None, inspect)
        self.assertEqual(result["status"], "computed_outside_declared_range")
        self.assertEqual(len(result["domain_check"]["samples"]), 9)

    def test_refprop_wrapper_phase_gap_is_explicit_and_two_phase_is_rejected(self):
        from types import SimpleNamespace
        CP = SimpleNamespace(iphase_gas=1, iphase_liquid=2, iphase_supercritical=3,
                             iphase_supercritical_gas=4, iphase_supercritical_liquid=5)
        class State:
            def T(self): return 750.
            def p(self): return 60e6
            def rhomass(self): return 300.
            def cpmass(self): return 1100.
            def cvmass(self): return 800.
            def speed_sound(self): return 500.
            def isothermal_compressibility(self): return 1e-8
            def phase(self): raise ValueError('The REFPROP backend does not implement calc_phase function for mixtures.')
            def Q(self): return 999.
        limits = dict(minimum_temperature_k=50,maximum_temperature_k=1000,maximum_pressure_pa=1e8)
        state = State()
        result = screen._refprop_domain(CP,state,{'O2':limits},limits)
        self.assertIsNone(result['phase_code'])
        self.assertIn('not_available',result['phase_classification'])
        self.assertTrue(result['within_declared_range'])
        state.Q = lambda: .5
        with self.assertRaisesRegex(ValueError,'两相'):
            screen._refprop_domain(CP,state,{'O2':limits},limits)
        state.Q = lambda: 999.
        state.cpmass = lambda: -1.
        with self.assertRaises(ValueError):
            screen._refprop_domain(CP,state,{'O2':limits},limits)

    def run_screen(self, record=None, **options):
        default = {"compare_refprop": False, "stations": ["ox_preburner_outlet"], "carbon": False}
        return screen.screen_gas_record(self.record if record is None else record, default | options)

    def test_source_immutable_complete_composition_and_element_enthalpy_preserved(self):
        before = deepcopy(self.record)
        result = self.run_screen()
        self.assertEqual(self.record, before)
        s = result["stations"][0]
        source = self.record["calculation"]["states"][s["id"]]
        self.assertEqual(s["baseline"]["composition_mass_fractions"], source["composition"])
        self.assertEqual(s["baseline"]["enthalpy_j_kg"], source["enthalpy"])
        self.assertAlmostEqual(sum(s["baseline"]["element_mass_flows_kg_s"].values()), source["mass_flow"], places=10)
        self.assertEqual(s["baseline"]["enthalpy_flow_w"], source["mass_flow"] * source["enthalpy"])
        self.assertEqual(result["provenance"]["source_result_sha256"], screen._fingerprint(before))
        json.dumps(result, allow_nan=False)

    def test_missing_radicals_never_silently_disappear(self):
        s = self.run_screen()["stations"][0]
        for eos in s["eos_comparisons"]:
            self.assertEqual(eos["status"], "computed")
            self.assertEqual(eos["full_composition_status"], "unavailable")
            self.assertEqual(eos["scope"], "reduced_composition_screening")
            reduced = eos["reduction"]
            self.assertGreater(len(reduced["removed_species_mass_fractions"]), 0)
            self.assertAlmostEqual(reduced["removed_mass_fraction"], sum(reduced["removed_species_mass_fractions"].values()), places=14)
            self.assertAlmostEqual(sum(reduced["composition_mass_fractions"].values()), 1., places=14)
            self.assertIn("enthalpy_difference_after_renormalization_j_kg", reduced)
            self.assertIn("removed_element_mass_kg_per_kg_original", reduced)
            self.assertIn("H2O", eos["parameter_coverage"]["parameter_sources"])
            self.assertIn("HEOS", eos["parameter_coverage"]["parameter_sources"]["H2O"]["source"])

    def test_reduction_can_be_disabled(self):
        s = self.run_screen(allow_reduced=False)["stations"][0]
        self.assertTrue(all(e["status"] == "unavailable" for e in s["eos_comparisons"]))
        self.assertTrue(all("candidate" not in e for e in s["eos_comparisons"]))

    def test_full_stable_composition_supported_without_reduction(self):
        record = fixture({"O2": .9, "H2O": .05, "CO2": .05})
        s = self.run_screen(record)["stations"][0]
        for eos in s["eos_comparisons"]:
            self.assertEqual(eos["scope"], "full_frozen_composition_screening")
            self.assertNotIn("reduction", eos)

    def test_consistent_property_set_and_frozen_expansion_use_same_eos(self):
        s = self.run_screen()["stations"][0]
        for eos in s["eos_comparisons"]:
            candidate, baseline = eos["candidate"], eos["paired_ideal_baseline"]
            self.assertGreater(candidate["compressibility_factor"], 1)
            self.assertLess(candidate["density_kg_m3"], baseline["density_kg_m3"])
            for key in screen._METRICS:
                self.assertAlmostEqual(eos["differences"][key]["difference"], candidate[key]-baseline[key])
            expansion = eos["frozen_expansion"]
            self.assertEqual(expansion["status"], "computed")
            self.assertAlmostEqual(expansion["actual_drop_j_kg"], .85*expansion["isentropic_drop_j_kg"])
            self.assertLess(abs(expansion["entropy_residual_j_kg_k"]), 1e-4)
            self.assertNotEqual(expansion["isentropic_drop_j_kg"], eos["paired_ideal_expansion"]["isentropic_drop_j_kg"])

    def test_large_missing_mass_is_rejected_not_pure_oxygen_substitution(self):
        record = fixture({"O2": .9, "OH": .1})
        s = self.run_screen(record)["stations"][0]
        for eos in s["eos_comparisons"]:
            self.assertEqual(eos["status"], "unavailable")
            self.assertAlmostEqual(eos["reduction"]["removed_mass_fraction"], .1)
            self.assertNotIn("candidate", eos)

    def test_no_refprop_installation_returns_reason_without_fallback(self):
        with patch.object(screen, "property_capabilities", return_value={"refprop": {"available": False, "reason": "absent"}}):
            s = self.run_screen(compare_cubic=False, compare_refprop=True)["stations"][0]
        self.assertEqual(s["eos_comparisons"][0]["status"], "unavailable")
        self.assertEqual(s["eos_comparisons"][0]["reason"], "absent")
        self.assertNotIn("candidate", s["eos_comparisons"][0])

    def test_saved_enthalpy_mismatch_does_not_get_overwritten(self):
        self.record["calculation"]["states"]["ox_preburner_outlet"]["enthalpy"] += 100
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "unavailable")
        self.assertIn("比焓不一致", s["reason"])
        self.assertIsNone(s["baseline"])

    def test_mechanism_fingerprint_mismatch_does_not_borrow_current_defaults(self):
        self.record["turbine_gas_properties"]["turbines"]["ox"]["model"]["mechanism_sha256"] = "changed"
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "unavailable")
        self.assertIn("指纹", s["reason"])

    def test_capture_and_station_mismatch_refuses_screening(self):
        self.record["turbine_gas_properties"]["turbines"]["ox"]["endpoint_properties"]["inlet"]["pressure_pa"] *= .9
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "unavailable")
        self.assertIn("捕获数据不一致", s["reason"])

    def test_new_port_model_cannot_disagree_with_capture(self):
        state = self.record["calculation"]["states"]["ox_preburner_outlet"]
        model = deepcopy(self.record["turbine_gas_properties"]["turbines"]["ox"]["model"])
        model.update(eos="ideal-gas", composition_basis="mass_fraction", enthalpy_reference_id=state["metadata"]["reference_id"])
        state["metadata"]["model"] = model
        model["mechanism_sha256"] = "0"*64
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "unavailable")
        self.assertIn("model", s["reason"])

    def test_new_verified_port_model_works_without_duplicate_turbine_capture(self):
        state = self.record["calculation"]["states"]["ox_preburner_outlet"]
        model = deepcopy(self.record["turbine_gas_properties"]["turbines"]["ox"]["model"])
        model.update(eos="ideal-gas", composition_basis="mass_fraction", enthalpy_reference_id=state["metadata"]["reference_id"])
        state["metadata"]["model"] = model
        self.record.pop("turbine_gas_properties")
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "screened_with_limitations", s.get("reason"))
        self.assertEqual(s["baseline"]["model"], model)

    def test_reference_identity_and_completeness_are_checked(self):
        for key, value in (("reference_id", "wrong"), ("composition_complete", False)):
            record = deepcopy(self.record)
            record["calculation"]["states"]["ox_preburner_outlet"]["metadata"][key] = value
            s = self.run_screen(record)["stations"][0]
            self.assertEqual(s["status"], "unavailable")

    def test_saved_entropy_mismatch_is_not_reconstructed_away(self):
        self.record["calculation"]["states"]["ox_preburner_outlet"]["metadata"]["entropy_JkgK"] = 1.
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "unavailable")
        self.assertIn("保存熵", s["reason"])

    def test_unknown_pressure_basis_prevents_expansion_not_property_query(self):
        self.record["calculation"]["components"]["ox_preburner_turbine"]["outputs"]["state_basis"] = "unknown"
        s = self.run_screen()["stations"][0]
        self.assertEqual(s["status"], "screened_with_limitations")
        self.assertTrue(all(e["frozen_expansion"]["status"] == "not_applicable" for e in s["eos_comparisons"]))

    def test_graphite_screen_preserves_original_elements_h_and_total_mass(self):
        result = self.run_screen(compare_cubic=False, carbon=True, stations=["fuel_preburner_outlet"])
        carbon = result["stations"][0]["carbon_screen"]
        self.assertEqual(carbon["status"], "computed")
        equilibrium = carbon["result"]
        self.assertTrue(carbon["not_deposition_rate"])
        self.assertGreater(equilibrium["solid_carbon_mass_fraction_total_gas_plus_solid"], .01)
        self.assertAlmostEqual(equilibrium["solid_carbon_mass_fraction_total_gas_plus_solid"],
                               equilibrium["solid_carbon_mass_kg"] / equilibrium["total_equilibrium_mass_kg"])
        self.assertLess(abs(equilibrium["enthalpy_scaled_residual"]), 1e-6)
        self.assertLess(abs(equilibrium["mass_residual_kg"]), 1e-7)
        self.assertLess(max(map(abs, equilibrium["element_mass_residual_kg"].values())), 1e-7)
        self.assertAlmostEqual(sum(equilibrium["gas_composition_mass_fractions"].values()), 1., places=12)

    def test_bad_record_returns_unavailable_station_and_progress(self):
        calls = []
        result = screen.screen_gas_record({}, {"compare_refprop": False}, lambda *args: calls.append(args))
        self.assertEqual(len(calls), 4)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(all(s["status"] == "unavailable" for s in result["stations"]))
        self.assertTrue(all(s["baseline"] is None for s in result["stations"]))

    def test_cancellation_between_stations_escapes(self):
        token = CancellationToken()
        with self.assertRaises(TaskCancelled), cancellation_scope(token):
            screen.screen_gas_record(self.record, {"compare_cubic": False, "compare_refprop": False, "carbon": False},
                                     lambda *_: token.cancel())


if __name__ == "__main__":
    unittest.main()
