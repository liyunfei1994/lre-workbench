import math
import unittest
from copy import deepcopy

from webapp.turbine_meanline import meanline_view


class MeanlineTests(unittest.TestCase):
    def fixture(self, mode="equal_work", reaction=.5):
        o={"D_mean_m":.3,"shaft_speed_rpm":20000.,"U_mean_actual_ms":math.pi*.3*20000/60,
           "actual_drop_Jkg":200000.,"n_stages_recommended":2}
        p={"turbine_meanline_mode":mode,"turb_ox_meanline_flow_coefficient":.6,
           "turb_ox_reaction_degree":reaction,"turb_ox_stage_work_fractions":"[0.6,0.4]"}
        return o,p

    def test_disabled_history_does_not_acquire_triangles(self):
        self.assertEqual(meanline_view({}, {}, "ox")["status"],"disabled")

    def test_user_coefficients_reject_nonfinite_or_boolean_values(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from core import EngineInputs, ValidationError
        for side in ("ox", "fuel"):
            for bad in (True, float("nan"), float("inf"), -1, 0):
                with self.subTest(side=side, value=bad), self.assertRaises(ValidationError):
                    EngineInputs(**{f"turb_{side}_meanline_flow_coefficient":bad})

    def test_euler_work_and_rotor_static_drop_close_for_impulse_and_reaction(self):
        for reaction in (0,.45,.5,.8):
            o,p=self.fixture(reaction=reaction)
            before=deepcopy((o,p))
            m=meanline_view(o,p,"ox")
            self.assertEqual(m["status"],"kinematic_only")
            self.assertAlmostEqual(sum(r["work_j_kg"] for r in m["rows"]),o["actual_drop_Jkg"])
            for row in m["rows"]:
                self.assertAlmostEqual(row["u_m_s"]*(row["cu1_m_s"]-row["cu2_m_s"]),row["work_j_kg"])
                rotor_drop=(row["w2_m_s"]**2-row["w1_m_s"]**2)/2
                self.assertAlmostEqual(rotor_drop,row["work_j_kg"]*reaction)
                self.assertAlmostEqual(row["reaction_check"],reaction)
                self.assertNotIn("pressure_pa",row)
            self.assertEqual((o,p),before)

    def test_custom_work_and_signed_angles(self):
        o,p=self.fixture("custom_work")
        m=meanline_view(o,p,"ox")
        self.assertEqual([r["work_fraction"] for r in m["rows"]],[.6,.4])
        self.assertLess(m["rows"][0]["beta2_deg"],0)
        for row in m["rows"]:
            self.assertAlmostEqual(math.tan(math.radians(row["alpha1_deg"])),row["cu1_m_s"]/row["ca_m_s"])

    def test_bad_constraints_not_normalized_or_filled(self):
        for fractions in ('[0.6,0.6]','[1]','[0,1]','[true,false]','null','[NaN,1]'):
            o,p=self.fixture("custom_work");p["turb_ox_stage_work_fractions"]=fractions
            self.assertEqual(meanline_view(o,p,"ox")["status"],"unavailable")
        for key in ("D_mean_m","U_mean_actual_ms","actual_drop_Jkg","n_stages_recommended"):
            o,p=self.fixture();del o[key]
            self.assertEqual(meanline_view(o,p,"ox")["status"],"unavailable")
        o,p=self.fixture();o["D_mean_m"]*=2
        self.assertEqual(meanline_view(o,p,"ox")["status"],"unavailable")

    def test_shared_uses_fuel_constraints(self):
        o,p=self.fixture()
        p={k.replace("turb_ox_","turb_fuel_"):v for k,v in p.items()}
        self.assertEqual(meanline_view(o,p,"shared")["status"],"kinematic_only")


if __name__ == "__main__": unittest.main()
