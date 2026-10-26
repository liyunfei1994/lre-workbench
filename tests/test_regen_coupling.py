"""Coupling acceptance must inspect local states, not just equal total heat."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from regen_coupling import close_regen_cycle, current_thermal_evidence
from cooling_marching import CoolantMarchError


class CouplingTests(unittest.TestCase):
    def model(self):
        return NS(inputs=NS(cooling_bartz_mode=1, cooling_dp_pa=5e6),
                  propellant=NS(fuel_species="CH4"), _hydraulic_trial={"sentinel": 1})

    def evaluation(self, model, heats=(40., 60.), pressure=5e6):
        hot = [dict(x_m=x, T_static_K=3000., T_aw_K=3500., T0_K=3700.,
                    p_Pa=20e6, M=.5, cp_JkgK=3000., mu_Pas=1e-4, Pr=.7, gamma=1.2)
               for x in (.25, .75)]
        profile = dict(flow_direction="coflow", cells=[
            dict(kind="channel", Q_W=q, hot_side=deepcopy(h)) for q, h in zip(heats, hot)])
        return NS(components={"cooling_channel": NS(profile=profile,
            outputs=dict(A_throat_m2=.1, heat_load_W=sum(heats))),
            "nozzle": NS(profile=dict(cells=hot), outputs=dict(At_m2=.1))},
            states={"fuel_after_cooling": NS(temperature=300., pressure=60e6-pressure),
                    "fuel_after_pump": NS(pressure=60e6), "lox_after_pump": NS(density=1200.)},
            steady_closure={})

    @patch("pump.fluid_density", return_value=400.)
    def test_current_evidence_requires_owner_and_rechecks_physical_residuals(self, _):
        model = self.model()
        model._thermal_feedback_epoch = object()
        result = close_regen_cycle(model, lambda: self.evaluation(model))
        self.assertIsNotNone(current_thermal_evidence(model, result))
        saved = NS(components=result.components)
        self.assertIsNone(current_thermal_evidence(model, saved))
        epoch, evidence = result._current_regen_thermal_evidence
        evidence["pressure_residual_Pa"] = .6
        self.assertIsNone(current_thermal_evidence(model, result))
        evidence["pressure_residual_Pa"] = 0.
        model._thermal_feedback_epoch = object()
        self.assertIsNone(current_thermal_evidence(model, result))

    @patch("pump.fluid_density", return_value=400.)
    def test_pass_requires_two_consistent_heat_fields_and_restores_trial(self, _):
        model = self.model()
        result = close_regen_cycle(model, lambda: self.evaluation(model))
        evidence = result.components["cooling_channel"].profile["hot_side_coupling"]
        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(evidence["iterations"], 2)
        self.assertEqual(model._hydraulic_trial, {"sentinel": 1})

    @patch("pump.fluid_density", return_value=400.)
    def test_equal_total_heat_cannot_hide_nonconverged_spatial_distribution(self, _):
        model = self.model()
        count = 0
        def evaluate():
            nonlocal count
            count += 1
            return self.evaluation(model, (40., 60.) if count % 2 else (60., 40.))
        with self.assertRaisesRegex(ValueError, "未闭合"):
            close_regen_cycle(model, evaluate)
        self.assertEqual(model._hydraulic_trial, {"sentinel": 1})

    @patch("pump.fluid_density", return_value=400.)
    def test_fixed_hardware_does_not_resize_to_hide_outer_area_mismatch(self, _):
        model = self.model()
        model._fixed_cooling_throat_area = .1
        def evaluate():
            result = self.evaluation(model)
            result.components["nozzle"].outputs["At_m2"] = .11
            return result
        result = close_regen_cycle(model, evaluate)
        self.assertEqual(model._fixed_cooling_throat_area, .1)
        self.assertEqual(result.components["nozzle"].outputs["At_m2"], .11)

    @patch("pump.fluid_density", return_value=400.)
    def test_pressure_initialization_rejects_failed_states_then_closes_actual_drop(self, _):
        model = self.model()
        model.inputs.cooling_dp_pa = 2e6
        def evaluate():
            trial = model._hydraulic_trial.get("cooling_dp", model.inputs.cooling_dp_pa)
            if trial < 8e6:
                raise CoolantMarchError("channel", 1., .5, "single-phase pressure domain")
            return self.evaluation(model, pressure=8e6)
        result = close_regen_cycle(model, evaluate)
        c = result.components["cooling_channel"].profile["hot_side_coupling"]
        self.assertEqual(len(c["rejected_pressure_trials"]), 2)
        self.assertEqual(c["iterations"], len(c["history"]))
        self.assertEqual(c["attempts"], c["iterations"] + 2)
        self.assertEqual(model.inputs.cooling_dp_pa, 2e6)

    @patch("pump.fluid_density", return_value=400.)
    def test_oscillatory_pressure_fixed_point_is_damped_without_changing_root(self, _):
        model = self.model()
        def evaluate():
            trial = model._hydraulic_trial.get("cooling_dp", model.inputs.cooling_dp_pa)
            return self.evaluation(model, pressure=8e6-1.8*(trial-8e6))
        result = close_regen_cycle(model, evaluate)
        c = result.components["cooling_channel"].profile["hot_side_coupling"]
        self.assertLessEqual(c["pressure_residual_Pa"], .5)
        self.assertLess(c["pressure_relaxation"], 1.)
        self.assertAlmostEqual(60e6-result.states["fuel_after_cooling"].pressure, 8e6, delta=.5)


if __name__ == "__main__":
    unittest.main()
