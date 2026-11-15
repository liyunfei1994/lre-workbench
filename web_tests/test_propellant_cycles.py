"""Platform FFSC propellant scope and real methane cycle conservation checks."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest
import warnings

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lre_workbench.domain.models import EngineInputs, ValidationError
from lre_workbench.solvers.convergence import numerical_convergence
from lre_workbench.systems.ffsc import FFSCCycleModel
from lre_workbench.application.presets import preset_input_values
from webapp.design_scope import require_supported_design








class PropellantInputTests(unittest.TestCase):
    def test_platform_accepts_only_methane_ffsc(self):
        require_supported_design({"cycle_type": "ffsc", "propellant_pair": "lox_methane"})
        for changes in ({"cycle_type": "gg"}, {"propellant_pair": "lox_kerosene"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                require_supported_design({"cycle_type": "ffsc", "propellant_pair": "lox_methane"} | changes)

    def test_direct_model_rejects_unsupported_propellant(self):
        with self.assertRaisesRegex(ValidationError, "propellant_pair"):
            EngineInputs(propellant_pair="unknown")


class MethaneRegressionTests(unittest.TestCase):
    def test_ffsc_design_point_closes_all_enabled_solver_layers(self):
        # Old snapshots ended before their thermal feedback converged. Requiring
        # those exact outputs would preserve that bug. Keep the original input
        # presets and independently enforce convergence and conservation instead.
        cases = ("raptor3_ffsc",)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for preset in cases:
                with self.subTest(preset=preset):
                    values = preset_input_values(preset)
                    result = FFSCCycleModel(EngineInputs(**values)).evaluate(include_cooling_marching=False)
                    status = numerical_convergence(result)
                    self.assertEqual(status["status"], "pass", status)
                    self.assertLessEqual(result.summary["solver_residual_norm"], status["layers"]["inner"]["tolerance"])
                    self.assertLessEqual(result.summary["tc_outer_residual_K"], 5)
                    for name, layer in status["layers"].items():
                        if layer["enabled"]:
                            self.assertEqual(layer["status"], "pass", name)
                            self.assertLessEqual(layer["residual"], layer["tolerance"], name)
                            for check in layer.get("checks", []):
                                self.assertLessEqual(check["residual"], check["tolerance"], name)
                    s = result.summary
                    self.assertAlmostEqual(s["mdot_total_kg_s"], s["mdot_fuel_kg_s"] + s["mdot_ox_kg_s"], places=8)
                    self.assertAlmostEqual(s["thrust_N"], s["Isp_s"] * s["mdot_total_kg_s"] * 9.80665,
                                           delta=1e-8 * s["thrust_N"])
                    self.assertLessEqual(abs(s["thrust_N"] / values["thrust"] - 1), 1e-4)
                    self.assertAlmostEqual(s["chamber_pressure_Pa"], values["pc"])
                    self.assertGreater(s["Isp_vac_s"], s["Isp_s"])
                    self.assertEqual(result.convergence["schema_version"], 1)
                    self.assertEqual(s["solver_inner_solves"], len(result.convergence["inner_runs"]))
                    self.assertEqual(s["solver_iterations"], sum(len(run["points"]) - 1 for run in result.convergence["inner_runs"]))
                    self.assertEqual(result.states["fuel_tank"].composition, {"CH4": 1.0})
                    self.assertAlmostEqual(result.states["fuel_tank"].density, 439.08801118210926, places=7)


if __name__ == "__main__":
    unittest.main()
