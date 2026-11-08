"""Independent balances and boundaries for the local steady throttle model."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import numpy as np

from web_tests.test_transient_physics import source_fixture
from lre_workbench.operations.throttle import (
    calculate, configuration, normalize_request, _SteadyModel, _checks,
)
from lre_workbench.operations.transient import gas_flux
from lre_workbench.runtime.tasks import CancellationToken, TaskCancelled, cancellation_scope


def calculation():
    return source_fixture()["result"]["calculation"]


class OperatingThrottlePhysicsTests(unittest.TestCase):
    def test_nominal_reproduces_actual_reference_and_all_balances_close(self):
        source = calculation()
        source["inputs"].update(thrust=999999., pc=99e6, use_target_preburner_temp=1,
            ox_preburner_temp_target=400., fuel_preburner_temp_target=500.)
        before = deepcopy(source)
        result = calculate(source, dict(throttle_min=.9, throttle_max=1., throttle_steps=5))
        self.assertEqual(source, before)
        self.assertEqual(result["baseline"]["thrust_N"], 310000.)
        self.assertEqual(result["counts"]["converged"], 5)
        self.assertEqual(result["counts"]["passed"], 0)
        nominal = result["points"][0]["summary"]
        self.assertAlmostEqual(nominal["thrust_N"], 310000., places=4)
        self.assertAlmostEqual(nominal["ox_preburner_T_combustion_K"], 800., places=6)
        self.assertAlmostEqual(nominal["fuel_preburner_T_combustion_K"], 900., places=6)
        for point in result["points"]:
            self.assertTrue(point["converged"])
            self.assertAlmostEqual(point["actual_thrust_ratio"], point["throttle_ratio"], places=7)
            self.assertAlmostEqual(point["summary"]["of_ratio"], 4., places=7)
            closure = point["steady_closure"]
            self.assertLess(max(abs(v) for v in closure["mass_residual_kg_s"].values()), 1e-5)
            self.assertLess(max(abs(v) for v in closure["energy_residual_W"].values()), 1.)
            self.assertLess(max(abs(v) for v in closure["shaft_residual_W"].values()), .01)
        self.assertGreater(abs(result["points"][-1]["summary"]["fuel_preburner_T_combustion_K"]-900), .1)
        json.dumps(result, allow_nan=False)

    def test_temperature_design_targets_never_enter_operating_equations(self):
        source = calculation()
        request = dict(throttle_min=.9, throttle_steps=3)
        a = calculate(source, request)
        source["inputs"].update(use_target_preburner_temp=1., use_target_preburner_mr=1.,
            ox_preburner_temp_target=100., fuel_preburner_temp_target=5000.,
            ox_preburner_mr_target=5., fuel_preburner_mr_target=20.)
        b = calculate(source, request)
        self.assertEqual([p["summary"] for p in a["points"]], [p["summary"] for p in b["points"]])
        with self.assertRaises(ValueError):
            normalize_request(dict(request, fuel_preburner_temp_target=700.))

    def test_source_and_all_calibration_coefficients_stay_fixed_across_scan(self):
        source = calculation()
        result = calculate(source, dict(throttle_min=.85, throttle_steps=4))
        identities = {p["hardware"]["fingerprint"] for p in result["points"]}
        self.assertEqual(identities, {result["model"]["calibration_sha256"]})
        self.assertEqual({p["geometry"]["throat_area_m2"] for p in result["points"]}, {.02})
        self.assertFalse(result["model"]["full_hardware_model"])
        self.assertFalse(result["model"]["experiment_validated"])

    def test_independent_branch_pump_turbine_and_nozzle_balances(self):
        source = calculation()
        result = calculate(source, dict(throttle_min=.9, throttle_steps=2))
        point = result["points"][-1]
        s = point["summary"]
        calibration = result["model"]["calibration"]
        for side in ("ox", "fuel"):
            pump = calibration["pumps"][side]
            speed = s[f"shaft_speed_{side}_rpm"]/(pump["omega0"]*30/np.pi)
            head = pump["dp0"]*(1.25*speed**2-.25*(s[f"mdot_{side}_kg_s"]/pump["mdot0"])**2)
            self.assertAlmostEqual(s[f"{side}_pump_outlet_pressure_Pa"]-pump["pin"], head, places=3)
            flow_sum = 0.
            for branch in calibration["liquid_branches"]:
                if branch["side"] != side:
                    continue
                destination = "ox" if branch["dest"] == 1 else "fuel"
                dp = s[f"{side}_pump_outlet_pressure_Pa"]-s[f"{destination}_preburner_pressure_Pa"]
                flow = branch["C"]*s[f"{side}_effective_area_ratio"]*np.sqrt(dp)
                self.assertAlmostEqual(flow, s[f"mdot_{side}_to_{destination}_preburner_kg_s"], places=7)
                flow_sum += flow
            self.assertAlmostEqual(flow_sum, s[f"mdot_{side}_kg_s"], places=7)
        for turbine, shaft in zip(calibration["turbines"], calibration["shafts"]):
            side = turbine["shaft"]
            volume = calibration["gas_properties_and_heat"][turbine["up"]]
            p, t = s[f"{side}_preburner_pressure_Pa"], s[f"{side}_preburner_T_combustion_K"]
            back = s["pc_Pa"]*turbine["back_ratio"]
            q = turbine["C"]*gas_flux(p,t,back,volume["gamma"],volume["R"])
            branch_sum = sum(s[f"mdot_{feed}_to_{side}_preburner_kg_s"] for feed in ("ox", "fuel"))
            self.assertAlmostEqual(q, branch_sum, places=5)
            power = q*turbine["work_factor"]*volume["cp"]*t*(1-(back/p)**((volume["gamma"]-1)/volume["gamma"]))
            self.assertAlmostEqual(power, s[f"{side}_turbine_power_W"], places=4)
            self.assertAlmostEqual(power*shaft["efficiency"], s[f"{side}_pump_power_W"], places=3)
        nozzle = calibration["nozzles"][0]
        main = calibration["gas_properties_and_heat"][0]
        q = nozzle["C"]*gas_flux(s["pc_Pa"],s["chamber_temperature_K"],100000.,main["gamma"],main["R"])
        self.assertAlmostEqual(q,s["mdot_total_kg_s"],places=5)

    def test_domain_exceeded_points_are_not_published_as_performance(self):
        result = calculate(calculation(), dict(throttle_min=.4, throttle_steps=7))
        self.assertGreater(result["counts"]["out_of_domain"], 0)
        low = result["points"][-1]
        self.assertEqual(low["status"], "out_of_domain")
        self.assertFalse(low["converged"])
        self.assertEqual(low["summary"], {})
        self.assertIsNone(low["actual_thrust_ratio"])
        self.assertNotIn("convergence", low)  # necessary pc bound avoids extrapolation

    def test_unset_temperature_limits_are_not_activated(self):
        source = calculation()
        source["inputs"].update(ox_preburner_temp_limit=1.,ox_preburner_temp_limit_status="unset",
            fuel_preburner_temp_limit=1000.,fuel_preburner_temp_limit_status="documented",
            fuel_preburner_temp_limit_basis="synthetic test")
        model = _SteadyModel(source)
        checks = _checks(model, dict(ox_preburner_T_combustion_K=800.,fuel_preburner_T_combustion_K=900.))
        self.assertEqual(checks[0]["status"], "not_evaluated")
        self.assertIsNone(checks[0]["limit"])
        self.assertEqual(checks[1]["basis"], "synthetic test")

    def test_rejects_unclosed_reference_instead_of_fitting_wrong_thrust(self):
        source = calculation()
        source["summary"]["thrust_N"] *= 1.1
        setup = configuration(source)
        self.assertFalse(setup["ready"])
        with self.assertRaises(ValueError):
            calculate(source, dict(throttle_min=.9))

    def test_request_validation_and_cancellation_preserve_partial_results(self):
        for bad in ({"throttle_steps":True},{"throttle_min":float("nan")},
                    {"throttle_max":1.1},{"throttle_min":1.}, {"geometry":{}}, {"throttle_steps":2.5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_request(bad)
        token = CancellationToken()
        partials = []
        def publish(status, result):
            partials.append(result)
            if result["points"]:
                token.cancel()
        with cancellation_scope(token), self.assertRaises(TaskCancelled):
            calculate(calculation(), dict(throttle_min=.9), publish)
        self.assertEqual(len(partials[-1]["points"]), 1)
        self.assertFalse(partials[-1]["complete"])


if __name__ == "__main__":
    unittest.main()
