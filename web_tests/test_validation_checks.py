"""Validation must distinguish internally consistent, unavailable and independent evidence."""
from copy import deepcopy
import json
import math
import unittest
from unittest.mock import patch

from webapp import validation_checks as v
from web_tests.convergence_fixture import converged_evidence


def source(cycle="gg", layout="single_shaft"):
    inputs = {"propellant_pair": "lox_methane", "cycle_type": cycle, "gg_layout": layout,
              "pc": 10e6, "of_ratio": 3, "fuel_inlet_temp": 100, "ox_inlet_temp": 90,
              "ambient_pressure": 101325}
    summary = {"solver_residual_norm": 1e-8, "mdot_total_kg_s": 100,
               "mdot_fuel_kg_s": 25, "mdot_ox_kg_s": 75,
               "ox_pump_power_MW": 1.8, "fuel_pump_power_MW": .9,
               "thrust_N": 300000, "Isp_s": 305, "Isp_vac_s": 330,
               "thrust_vac_N": 323000, "turbine_power_MW": 3}
    if cycle == "gg":
        summary.update(gg_mdot_kg_s=5, gg_mdot_fuel_kg_s=4, gg_mdot_ox_kg_s=1,
                       mdot_chamber_kg_s=95, mdot_coolant_kg_s=21,
                       gg_mech_efficiency=.9, gg_turbine_power_MW=3,
                       ox_turbine_power_MW=2, fuel_turbine_power_MW=1)
    else:
        summary.update(mdot_fuel_to_ox_preburner_kg_s=2, mdot_fuel_to_fuel_preburner_kg_s=23,
                       mdot_ox_to_ox_preburner_kg_s=65, mdot_ox_to_fuel_preburner_kg_s=10,
                       mdot_LO_ox_exhaust_kg_s=67, mdot_LF_fuel_exhaust_kg_s=33,
                       ox_turbine_power_MW=1.8, fuel_turbine_power_MW=.9, turbine_power_MW=2.7)
    return {"calculation": {"inputs": inputs, "summary": summary, "convergence": converged_evidence(1e-8), "states": {}, "components": {
        "ox_pump": {"outputs": {"power_W": 1.8e6}}, "fuel_pump": {"outputs": {"power_W": .9e6}}}},
        "assessment": {"status": "pass", "numerical": {"status": "pass"}}, "nonfinite_fields": []}


def experiment():
    return {"role": "holdout", "conditions": {
        "propellant_pair": "lox_methane", "cycle_type": "gg", "gg_layout": "single_shaft",
        "pc": {"value": 10, "unit": "MPa", "tolerance": .01},
        "of_ratio": {"value": 3, "unit": "1", "tolerance": .01},
        "fuel_inlet_temp": {"value": 100, "unit": "K", "tolerance": .2},
        "ox_inlet_temp": {"value": 90, "unit": "K", "tolerance": .2},
        "ambient_pressure": {"value": 101.325, "unit": "kPa", "tolerance": .1},
    }, "measurements": [{"metric": "thrust_N", "value": 300, "unit": "kN", "tolerance": 1, "uncertainty": .5}]}


def checks(result):
    return {row["id"]: row for row in result["checks"]}


class ConservationTests(unittest.TestCase):
    def test_three_architectures_recompute_mass_and_shaft_closure(self):
        for cycle, layout in (("gg", "single_shaft"), ("gg", "dual_shaft"), ("ffsc", "dual_shaft")):
            with self.subTest(cycle=cycle, layout=layout):
                record = source(cycle, layout)
                before = deepcopy(record)
                result = v.conservation_checks(record)
                self.assertEqual(result["status"], "partial")
                rows = checks(result)
                self.assertEqual(rows["total_mass"]["status"], "pass")
                self.assertEqual(rows["shaft_power"]["status"], "pass")
                self.assertAlmostEqual(rows["shaft_power"]["left_si"], 2.7e6)
                self.assertEqual(rows["element_balance"]["status"], "unavailable")
                self.assertEqual(rows["whole_system_energy"]["status"], "unavailable")
                self.assertEqual(record, before)
                json.dumps(result, allow_nan=False)

    def test_perturbed_mass_and_power_do_not_trust_reported_ratios(self):
        record = source()
        record["calculation"]["summary"].update(mdot_chamber_kg_s=96, gg_turbine_power_MW=2, power_balance_ratio=1)
        result = v.conservation_checks(record)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(checks(result)["chamber_gg_split"]["difference_si"], -1)
        self.assertEqual(checks(result)["shaft_power"]["status"], "fail")

    def test_dual_shaft_detects_compensating_side_errors(self):
        record = source("gg", "dual_shaft")
        record["calculation"]["summary"].update(ox_turbine_power_MW=1, fuel_turbine_power_MW=2)
        rows = checks(v.conservation_checks(record))
        self.assertEqual(rows["shaft_power"]["status"], "pass")
        self.assertEqual(rows["ox_shaft_power"]["status"], "fail")
        self.assertEqual(rows["fuel_shaft_power"]["status"], "fail")

    def test_missing_nonfinite_and_failed_records_never_pass(self):
        self.assertEqual(v.conservation_checks({})["status"], "unavailable")
        for key, value in (("solver_residual_norm", .2), ("solver_warning", 1)):
            record = source()
            record["calculation"]["summary"][key] = value
            self.assertEqual(v.conservation_checks(record)["status"], "fail")
        record = source()
        record["assessment"]["status"] = "fail"
        self.assertEqual(v.conservation_checks(record)["status"], "fail")
        for value in (None, float("nan"), float("inf"), True, -1, "100", 10**1000):
            record = source()
            record["calculation"]["summary"]["mdot_total_kg_s"] = value
            rows = checks(v.conservation_checks(record))
            self.assertEqual(rows["total_mass"]["status"], "unavailable")


class PropertyTests(unittest.TestCase):
    def setUp(self):
        try:
            self.package, self.CP = v._coolprop()
        except ImportError:
            self.skipTest("CoolProp is an optional dependency")

    def test_real_heos_three_fluids_and_property_reference(self):
        points = [{"fluid": "Oxygen", "temperature_K": 90, "pressure_Pa": 1e6},
                  {"fluid": "Methane", "temperature_K": 110, "pressure_Pa": 1e6},
                  {"fluid": "n-Dodecane", "temperature_K": 300, "pressure_Pa": 1e6}]
        result = v.property_checks({"points": points})
        self.assertEqual(result["status"], "pass")
        for point, row in zip(points, result["checks"]):
            with self.subTest(fluid=point["fluid"]):
                self.assertEqual(row["phase"], "liquid")
                p = row["properties_si"]
                self.assertGreater(p["density_kg_m3"], 400)
                for key in ("cp_J_kg_K", "cv_J_kg_K", "speed_sound_m_s", "viscosity_Pa_s", "conductivity_W_m_K"):
                    self.assertGreater(p[key], 0)
                self.assertEqual(row["heos_status"], "pass")
                self.assertEqual(row["comparison"]["status"], "not_requested")
                self.assertTrue(row["model_metadata"]["backend_name"])
                self.assertGreater(row["critical_state"]["temperature_K"], 0)
                ref = row["reference_state"]
                expected_h = self.CP.PropsSI("Hmass", "T", point["temperature_K"], "P", point["pressure_Pa"], point["fluid"]) - self.CP.PropsSI("Hmass", "T", ref["temperature_K"], "P", ref["pressure_Pa"], point["fluid"])
                self.assertAlmostEqual(p["enthalpy_delta_J_kg"], expected_h, places=6)
        json.dumps(result, allow_nan=False)

    def test_range_failures_do_not_extrapolate(self):
        for point in ({"fluid": "n-Dodecane", "temperature_K": 701, "pressure_Pa": 1e6},
                      {"fluid": "Methane", "temperature_K": 80, "pressure_Pa": 1e6},
                      {"fluid": "Oxygen", "temperature_K": 90, "pressure_Pa": 81e6}):
            result = v.property_checks({"points": [point]})
            self.assertEqual(result["status"], "fail")
            self.assertNotIn("properties_si", result["checks"][0])

    def test_melting_boundary_rejects_solid_side_inside_eos_rectangle(self):
        # A T/P flash can return a metastable fluid for this point. Its T and P
        # both satisfy the EOS rectangular limits, so a separate solid check is
        # required before presenting it as a useful single-phase liquid state.
        point = {"fluid": "Methane", "temperature_K": 100, "pressure_Pa": 70e6}
        row = v.property_checks({"points": [point]})["checks"][0]
        self.assertLess(row["limits"]["temperature_min_K"], point["temperature_K"])
        self.assertGreater(row["limits"]["pressure_max_Pa"], point["pressure_Pa"])
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["melting_boundary"]["status"], "fail")
        self.assertGreater(row["melting_boundary"]["temperature_K"], 107)
        self.assertNotIn("properties_si", row)
        point["temperature_K"] = 121
        row = v.property_checks({"points": [point]})["checks"][0]
        self.assertEqual(row["status"], "pass")
        self.assertGreater(row["melting_boundary"]["margin_K"], 0)

    def test_melting_boundary_missing_evidence_is_not_fabricated(self):
        class NoMeltingLine:
            def has_melting_line(self):
                return False
        class FailingMeltingLine:
            def has_melting_line(self):
                return True
            def melting_line(self, *args):
                raise ValueError("outside melting correlation")
        for state in (NoMeltingLine(), FailingMeltingLine()):
            row = v._melting_boundary(self.CP, state, 100, 70e6)
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["temperature_K"])
            self.assertIsNone(row["margin_K"])

    def test_thermodynamic_and_transport_evidence_are_not_conflated(self):
        row = v.property_checks({"points": [{"fluid": "Methane", "temperature_K": 121, "pressure_Pa": 70e6}]})["checks"][0]
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["transport_applicability"]["status"], "not_assessed")
        metadata = row["model_metadata"]
        self.assertEqual(len(metadata["fluid_definition_sha256"]), 64)
        self.assertNotEqual(metadata["eos_reference"], metadata["viscosity_reference"])
        self.assertEqual(row["comparison"]["evidence_independence"], "not_established")

    def test_requested_refprop_missing_does_not_fabricate_comparison(self):
        capabilities = {"coolprop": {"available": True, "version": self.package.__version__}, "refprop": {"available": False, "version": None, "reason": "not installed"}, "fluids": list(v.FLUIDS)}
        with patch.object(v, "property_capabilities", return_value=capabilities):
            result = v.property_checks({"points": [{"fluid": "Oxygen", "temperature_K": 90, "pressure_Pa": 1e6}], "compare_refprop": True})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["checks"][0]["refprop"]["status"], "unavailable")
        self.assertNotIn("differences", result["checks"][0]["refprop"])

    def test_missing_coolprop_interface_exposes_both_backend_availability(self):
        capabilities = {"coolprop": {"available": False}, "refprop": {"available": False, "reason": "missing interface"}}
        with patch.object(v, "property_capabilities", return_value=capabilities), patch.object(v, "_coolprop", side_effect=AssertionError("unavailable interface must not load")):
            result = v.property_checks({"points": [{"fluid": "Oxygen", "temperature_K": 90, "pressure_Pa": 1e6}], "compare_refprop": True})
        row = result["checks"][0]
        self.assertEqual(row["heos_status"], "unavailable")
        self.assertEqual(row["refprop"]["status"], "unavailable")
        self.assertEqual(row["comparison"]["status"], "unavailable")
        self.assertNotIn("properties_si", row)
        self.assertNotIn("differences", row["refprop"])

    def test_same_reference_differences_cancel_backend_enthalpy_entropy_offsets(self):
        # Exercise the real EOS with two deliberately different h/s origins.
        # This double is never reported as a real installed REFPROP result.
        CP = self.CP
        class OffsetState:
            def __init__(self, backend, fluid):
                self.state = CP.AbstractState("HEOS", fluid)
                self.offset = 123456.0 if backend == "REFPROP" else 0
            def __getattr__(self, name):
                return getattr(self.state, name)
            def hmass(self):
                return self.state.hmass() + self.offset
            def smass(self):
                return self.state.smass() + self.offset / 100
        class Proxy:
            AbstractState = OffsetState
            def __getattr__(self, name):
                return getattr(CP, name)
        capabilities = {"coolprop": {"available": True, "version": "test"}, "refprop": {"available": True, "version": "test", "reason": "test double"}, "fluids": list(v.FLUIDS)}
        with patch.object(v, "_coolprop", return_value=(self.package, Proxy())), patch.object(v, "property_capabilities", return_value=capabilities):
            result = v.property_checks({"points": [{"fluid": "n-Dodecane", "temperature_K": 300, "pressure_Pa": 1e6}], "compare_refprop": True})
        row = result["checks"][0]
        self.assertEqual(row["status"], "pass")
        ref = row["refprop"]
        self.assertEqual(ref["agreement_status"], "not_assessed")
        self.assertNotEqual(row["properties_si"]["enthalpy_J_kg"], ref["properties_si"]["enthalpy_J_kg"])
        self.assertNotIn("enthalpy_J_kg", ref["differences"])
        for key in ("enthalpy_delta_J_kg", "entropy_delta_J_kg_K"):
            self.assertAlmostEqual(ref["differences"][key]["heos_minus_refprop"], 0, places=8)

    def _mock_comparison(self, heos, refprop):
        capabilities = {"coolprop": {"available": True, "version": "test"},
                        "refprop": {"available": True, "version": "test", "reason": "test double"}}
        def properties(_cp, backend, _point):
            outcome = heos if backend == "HEOS" else refprop
            if isinstance(outcome, Exception):
                raise outcome
            return deepcopy(outcome)
        with patch.object(v, "property_capabilities", return_value=capabilities), patch.object(v, "_properties", side_effect=properties) as evaluate:
            result = v.property_checks({"points": [{"fluid": "Oxygen", "temperature_K": 90, "pressure_Pa": 1e6}], "compare_refprop": True})
        self.assertEqual([call.args[1] for call in evaluate.call_args_list], ["HEOS", "REFPROP"])
        json.dumps(result, allow_nan=False)
        return result["checks"][0]

    @staticmethod
    def _backend_result():
        return {"status": "pass", "phase": "liquid", "reason": "test double",
                "properties_si": dict.fromkeys(v.PROPERTY_COMPARISON_KEYS, 10.0),
                "reference_state": {"temperature_K": 300, "pressure_Pa": 1000, "phase": "gas"}}

    def test_phase_mismatch_preserves_both_values_but_suppresses_differences(self):
        heos, refprop = self._backend_result(), self._backend_result()
        refprop["phase"] = "gas"
        refprop["properties_si"]["density_kg_m3"] = 1
        row = self._mock_comparison(heos, refprop)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["heos_status"], "pass")
        self.assertEqual(row["refprop"]["status"], "pass")
        self.assertEqual(row["comparison"]["status"], "not_comparable")
        self.assertFalse(row["comparison"]["phase_agreement"])
        self.assertEqual(row["refprop"]["differences"], {})
        self.assertEqual(row["refprop"]["properties_si"]["density_kg_m3"], 1)

    def test_one_backend_failure_does_not_skip_or_discard_other_backend(self):
        row = self._mock_comparison(ValueError("HEOS failure"), self._backend_result())
        self.assertEqual(row["heos_status"], "unavailable")
        self.assertEqual(row["refprop"]["status"], "pass")
        self.assertEqual(row["refprop"]["properties_si"]["density_kg_m3"], 10)
        self.assertEqual(row["comparison"]["status"], "unavailable")
        self.assertEqual(row["refprop"]["differences"], {})
        row = self._mock_comparison(self._backend_result(), {"status": "fail", "reason": "outside REFPROP bounds"})
        self.assertEqual(row["properties_si"]["density_kg_m3"], 10)
        self.assertEqual(row["refprop"]["reason"], "outside REFPROP bounds")
        self.assertEqual(row["refprop"]["status"], "fail")
        self.assertEqual(row["comparison"]["status"], "unavailable")

    def test_reference_mismatch_excludes_only_reference_dependent_metrics(self):
        for mutation in (lambda r: r["reference_state"].update(temperature_K=301),
                         lambda r: r["reference_state"].update(phase="liquid"),
                         lambda r: r.pop("reference_state")):
            refprop = self._backend_result()
            mutation(refprop)
            row = self._mock_comparison(self._backend_result(), refprop)
            self.assertEqual(row["comparison"]["status"], "partial")
            self.assertFalse(row["comparison"]["reference_agreement"])
            self.assertIn("density_kg_m3", row["refprop"]["differences"])
            for key in ("enthalpy_delta_J_kg", "entropy_delta_J_kg_K"):
                self.assertNotIn(key, row["refprop"]["differences"])
                self.assertIn(key, row["comparison"]["excluded_properties"])

    def test_missing_property_and_zero_reference_difference_remain_explicit(self):
        heos, refprop = self._backend_result(), self._backend_result()
        heos["properties_si"]["speed_sound_m_s"] = None
        refprop["properties_si"]["enthalpy_delta_J_kg"] = 0
        row = self._mock_comparison(heos, refprop)
        self.assertEqual(row["comparison"]["status"], "partial")
        self.assertNotIn("speed_sound_m_s", row["refprop"]["differences"])
        self.assertIn("speed_sound_m_s", row["comparison"]["excluded_properties"])
        delta = row["refprop"]["differences"]["enthalpy_delta_J_kg"]
        self.assertEqual(delta["heos_minus_refprop"], 10)
        self.assertIsNone(delta["relative_difference"])
        self.assertEqual(row["refprop"]["agreement_status"], "not_assessed")


class ExperimentTests(unittest.TestCase):
    def test_unit_conversion_record_immutability_and_idempotent_normalization(self):
        record, request = source(), experiment()
        before, request_before = deepcopy(record), deepcopy(request)
        normalized = v.validate_experiment_request(request)
        self.assertEqual(normalized, v.validate_experiment_request(normalized))
        self.assertEqual(normalized["measurements"][0]["value"], 300000)
        self.assertEqual(normalized["measurements"][0]["tolerance"], 1000)
        result = v.experiment_checks(record, normalized)
        self.assertEqual(result["status"], "pass")
        row = checks(result)["measurement_thrust_N"]
        self.assertEqual((row["predicted_si"], row["measured_si"], row["unit"]), (300000, 300000, "N"))
        self.assertEqual(record, before)
        self.assertEqual(request, request_before)
        json.dumps(result, allow_nan=False)

    def test_no_data_never_counts_as_success(self):
        for request in (None, {}, {"role": "holdout", "conditions": {}, "measurements": []}):
            self.assertEqual(v.experiment_checks(source(), request)["status"], "not_provided")

    def test_measurement_failure_and_uncertainty_not_assumed_to_be_confidence(self):
        request = experiment()
        request["measurements"][0] = {"metric": "thrust_N", "value": 310, "unit": "kN", "uncertainty": 1}
        result = v.experiment_checks(source(), request)
        self.assertEqual(result["status"], "fail")
        row = checks(result)["measurement_thrust_N"]
        self.assertEqual(row["criterion"], "within_reported_uncertainty")
        self.assertEqual(row["difference_si"], -10000)
        self.assertEqual(row["uncertainty_si"], 1000)

    def test_mismatched_and_missing_conditions_cannot_validate_predictions(self):
        for key, replacement in (("propellant_pair", "lox_kerosene"), ("gg_layout", "dual_shaft"),
                                 ("pc", {"value": 12, "unit": "MPa", "tolerance": .1})):
            request = experiment()
            request["conditions"][key] = replacement
            result = v.experiment_checks(source(), request)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(checks(result)["measurement_thrust_N"]["status"], "unavailable")
        for key in ("ambient_pressure", "fuel_inlet_temp", "of_ratio", "cycle_type"):
            request = experiment()
            del request["conditions"][key]
            result = v.experiment_checks(source(), request)
            self.assertNotEqual(result["status"], "pass")
            self.assertEqual(checks(result)["measurement_thrust_N"]["status"], "unavailable")

    def test_bad_source_cannot_be_rescued_by_matching_measurement(self):
        for mutate in (lambda r: r["calculation"]["summary"].update(solver_residual_norm=.1),
                       lambda r: r.update(assessment={"status": "fail"}),
                       lambda r: r["assessment"].update(numerical={"status": "fail"}),
                       lambda r: r.update(nonfinite_fields=["calculation.summary.x"])):
            record = source()
            mutate(record)
            result = v.experiment_checks(record, experiment())
            self.assertNotEqual(result["status"], "pass")
            self.assertEqual(checks(result)["measurement_thrust_N"]["status"], "unavailable")

    def test_calibration_is_identified_and_does_not_claim_holdout_validation(self):
        request = experiment()
        request["role"] = "calibration"
        result = v.experiment_checks(source(), request)
        self.assertEqual(result["role"], "calibration")
        self.assertIn("不能同时作为独立留出验证", result["notes"][0])


class RequestValidationTests(unittest.TestCase):
    def test_strict_types_limits_units_and_unknown_fields(self):
        for value in (True, None, "90", float("nan"), float("inf"), 0, -1, 10001, 10**1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                v.validate_property_request({"points": [{"fluid": "Oxygen", "temperature_K": value, "pressure_Pa": 1e6}]})
        for request in ({"points": []}, {"points": [{}] * 21}, {"points": [{}], "compare_refprop": 1},
                        {"points": [{"fluid": "RP1", "temperature_K": 300, "pressure_Pa": 1e6}]},
                        {"points": [{}], "unknown": True}):
            with self.assertRaises(ValueError):
                v.validate_property_request(request)
        for mutation in (lambda r: r["measurements"][0].update(unit="MPa"),
                         lambda r: r["measurements"][0].update(value=True),
                         lambda r: r["measurements"][0].update(metric="arbitrary.path"),
                         lambda r: r.update(role="validated"),
                         lambda r: r.update(measurements=r["measurements"] * 21),
                         lambda r: r["conditions"]["pc"].update(tolerance=-1)):
            request = experiment()
            mutation(request)
            with self.assertRaises(ValueError):
                v.validate_experiment_request(request)
        request = experiment()
        request["measurements"][0].pop("tolerance")
        request["measurements"][0].pop("uncertainty")
        with self.assertRaises(ValueError):
            v.validate_experiment_request(request)


if __name__ == "__main__":
    unittest.main()
