from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.components.cooling.model import CoolingChannel
from lre_workbench.domain.models import EngineInputs, ValidationError


class ConceptHeatTests(unittest.TestCase):
    def evaluate(self, **changes):
        args = dict(mdot_fuel=50,t_inlet=110,p_inlet=20e6,dp_pa=2e6,
            bartz_mode=2,mdot_total=230,pc=10e6,n_points=80)
        return CoolingChannel(**(args | changes)).evaluate()

    def test_energy_surface_integral_and_no_cooling_capability_claim(self):
        state, r = self.evaluate()
        q = sum(c["q_W_m2"]*c["dA_hot_m2"] for c in r.profile["cells"])
        self.assertAlmostEqual(q,r.outputs["heat_load_W"],delta=1e-6)
        self.assertLess(abs(r.outputs["energy_residual_W"]),1e-5)
        self.assertEqual(state.pressure,18e6)
        self.assertEqual(r.profile["validity"]["cooling_capability"],"not_evaluated")
        self.assertNotIn("nodes",r.profile)
        self.assertNotIn("T_wall_hot_max_K",r.outputs)
        self.assertTrue(all("T_wall_hot_K" not in c for c in r.profile["cells"]))
        self.assertTrue(all("width_m" not in c for c in r.profile["geometry"]["cells"]))

    def test_independent_of_fixed_heat_and_detailed_channel_material(self):
        a = self.evaluate()[1]
        b = self.evaluate(heat_load=1,n_channels=1200,channel_width_m=99,channel_height_m=.0001,
            wall_thickness_m=.05,wall_conductivity=1,htc_enhancement=5,channel_table="invalid unused draft",
            wall_thermal_dataset_id="unused material",wall_material_zones="unused")[1]
        self.assertEqual(a.outputs,b.outputs)
        self.assertEqual(a.profile,b.profile)

    def test_wall_assumption_coverage_and_size_change_load(self):
        base = self.evaluate()[1].outputs
        hot_wall = self.evaluate(reference_wall_temperature_K=1000)[1].outputs
        short = self.evaluate(cover_eps=5)[1].outputs
        long = self.evaluate(l_star_m=1.5)[1].outputs
        self.assertLess(hot_wall["heat_load_W"],base["heat_load_W"])
        self.assertLess(hot_wall["estimated_q_peak_MW_m2"],base["estimated_q_peak_MW_m2"])
        self.assertLess(short["A_hot_m2"],base["A_hot_m2"])
        self.assertLess(short["heat_load_W"],base["heat_load_W"])
        self.assertGreater(long["heat_load_W"],base["heat_load_W"])
        self.assertGreater(self.evaluate(pc=12e6)[1].outputs["estimated_q_peak_MW_m2"],base["estimated_q_peak_MW_m2"])

    def test_refinement_stabilizes_integral_and_peak(self):
        a,b = [self.evaluate(n_points=n)[1].outputs for n in (120,240)]
        for key in ("heat_load_W","estimated_q_peak_MW_m2"):
            self.assertLess(abs(a[key]/b[key]-1),.02)

    def test_invalid_reference_and_reverse_heat_are_rejected(self):
        for tw in (0,float("nan"),8000):
            with self.subTest(tw=tw),self.assertRaises(ValueError):
                self.evaluate(reference_wall_temperature_K=tw)
        with self.assertRaises(ValueError):
            self.evaluate(cover_eps=100)
        with self.assertRaises(ValueError):
            self.evaluate(film_fraction=.01)

    def test_model_validation_and_old_default_identity(self):
        self.assertEqual(EngineInputs().cooling_bartz_mode,1)
        EngineInputs(cooling_bartz_mode=2)
        with self.assertRaises(ValidationError):
            EngineInputs(cooling_bartz_mode=3)
        with self.assertRaises(ValidationError):
            EngineInputs(cooling_bartz_mode=2,cycle_type="gg")


if __name__ == "__main__":
    unittest.main()
