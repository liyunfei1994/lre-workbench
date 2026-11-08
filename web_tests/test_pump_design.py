"""Saved pump conditions are a traceable handoff, not a completed pump design."""
from copy import deepcopy
import json
import math
import unittest

from webapp.pump_geometry import pump_geometry_view
from web_tests.test_pump_geometry import fixture


def enriched_fixture(stages=2):
    result = fixture(stages=stages)
    for component in result["calculation"]["components"].values():
        o = component["outputs"]
        eta, dh = .85, 48000.
        o.update(T_in_K=80., T_out_K=90., rho_out_kgm3=1080., eta=eta,
                 specific_work_isentropic_Jkg=dh,
                 power_W=o["volume_flow_m3s"] * o["rho_in_kgm3"] * dh / eta,
                 npsh_available_m=60.,
                 suction_specific_speed_nss_eu=o["nr_rpm"] * math.sqrt(o["volume_flow_m3s"]) / 60.**.75,
                 suction_specific_speed_Nss=o["nr_rpm"] * math.sqrt(o["volume_flow_m3s"] * 15850.3) / (60. * 3.280839895)**.75)
    return result


def handoff(result, side="ox"):
    return pump_geometry_view(result, "saved-record")["design_handoff"]["pumps"][side]


def fields(view, group):
    return {f["id"]: f for g in view["groups"] if g["id"] == group for f in g["fields"]}


class PumpDesignHandoffTests(unittest.TestCase):
    def test_units_power_and_torque_follow_saved_point_without_replacing_real_fluid_power(self):
        result = enriched_fixture()
        before = deepcopy(result)
        view = handoff(result)
        design, power = fields(view, "design_point"), fields(view, "power")
        o = result["calculation"]["components"]["ox_pump"]["outputs"]
        self.assertEqual(view["status"], "partial")
        self.assertEqual(design["volume_flow_m3_h"]["value"], o["volume_flow_m3s"] * 3600)
        self.assertEqual(design["mass_flow_kg_s"]["value"], o["volume_flow_m3s"] * o["rho_in_kgm3"])
        self.assertEqual(power["power_W"]["value"], o["power_W"])
        self.assertEqual(power["hydraulic_reference_power_w"]["value"], o["volume_flow_m3s"] * o["delta_p_Pa"])
        self.assertNotEqual(power["power_W"]["value"], power["hydraulic_reference_power_w"]["value"])
        self.assertAlmostEqual(power["shaft_torque_n_m"]["value"], o["power_W"] / (2 * math.pi * o["nr_rpm"] / 60))
        self.assertEqual(result, before)
        json.dumps(view, allow_nan=False)

    def test_missing_fluid_properties_and_component_blades_are_not_invented(self):
        view = handoff(enriched_fixture())
        fluid, power, boundaries = (fields(view, group) for group in ("fluid", "power", "boundaries"))
        for key in ("dynamic_viscosity_pa_s", "kinematic_viscosity_m2_s", "vapor_pressure_pa", "heat_capacity_j_kg_k", "thermal_conductivity_w_m_k"):
            self.assertEqual(fluid[key]["state"], "missing")
            self.assertIsNone(fluid[key]["value"])
        for key in ("hydraulic_efficiency", "volumetric_efficiency", "mechanical_efficiency"):
            self.assertIsNone(power[key]["value"])
        self.assertIsNone(boundaries["corrected_total_head_m"]["value"])
        self.assertEqual({c["id"] for c in view["components"]}, {"inducer", "impeller", "diffuser", "return_channel", "volute"})
        for component in view["components"]:
            for field in component["fields"]:
                self.assertEqual(field["state"], "missing")
                self.assertIsNone(field["value"])

    def test_stage_heads_sum_to_total_without_inventing_interstage_states_or_inducer_head(self):
        view = handoff(enriched_fixture(stages=2))
        point = fields(view, "design_point")
        self.assertEqual(len(view["stage_allocation"]), 2)
        self.assertAlmostEqual(sum(stage["reference_head_m"] for stage in view["stage_allocation"]), point["total_head_m"]["value"])
        self.assertIn("诱导轮", view["stage_allocation"][0]["inlet_state_note"])
        self.assertIn("未保存本级入口", view["stage_allocation"][1]["inlet_state_note"])
        self.assertEqual(point["stage_head_m"]["state"], "assumed")

    def test_npsha_and_nss_do_not_claim_required_cavitation_performance(self):
        view = handoff(enriched_fixture())
        cavitation = fields(view, "cavitation")
        self.assertEqual(cavitation["npsh_available_m"]["value"], 60.)
        self.assertIsNone(cavitation["npsh_required_m"]["value"])
        self.assertIsNone(cavitation["npsh_margin_m"]["value"])
        self.assertIn("未加入端口速度头", cavitation["npsh_available_m"]["note"])
        self.assertIn("NPSHa^0.75", cavitation["suction_specific_speed_nss_eu"]["note"])
        inducer = next(c for c in view["components"] if c["id"] == "inducer")
        self.assertIsNone(inducer["fields"][0]["value"])
        self.assertEqual(inducer["state"], "missing")

    def test_no_new_defaults_are_added_to_old_point_or_missing_scalar_outputs(self):
        result = fixture()
        del result["calculation"]["inputs"]["lox_pump_inlet_flow_coefficient"]
        view = handoff(result)
        self.assertEqual(view["status"], "partial")
        self.assertEqual(fields(view, "design_point")["volume_flow_m3_s"]["value"], .2)
        self.assertIsNone(fields(view, "geometry")["eye_shroud_diameter_m"]["value"])
        for key in ("eta", "power_W", "shaft_torque_n_m"):
            self.assertEqual(fields(view, "power")[key]["state"], "missing")
        self.assertIsNone(fields(view, "fluid")["T_in_K"]["value"])

    def test_frozen_geometry_stays_identical_but_mixed_output_revision_blocks_handoff(self):
        result = enriched_fixture()
        result["pump_geometry"] = pump_geometry_view(result, "saved-record")
        frozen = deepcopy(result["pump_geometry"]["pumps"]["ox"])
        result["calculation"]["components"]["ox_pump"]["outputs"]["nr_rpm"] *= 2
        view = pump_geometry_view(result, "saved-record")
        self.assertEqual(view["pumps"]["ox"], frozen)
        design = view["design_handoff"]["pumps"]["ox"]
        self.assertEqual(design["status"], "conflict")
        self.assertEqual(design["stage_allocation"], [])
        self.assertIsNone(fields(design, "design_point")["shaft_speed_rpm"]["value"])
        self.assertEqual(fields(design, "power")["shaft_torque_n_m"]["state"], "conflict")
        self.assertEqual(view["design_handoff"]["pumps"]["fuel"]["status"], "partial")

    def test_conflicting_extra_outputs_are_not_accepted_as_valid_modelling_values(self):
        for key in ("power_W", "suction_specific_speed_nss_eu", "suction_specific_speed_Nss"):
            result = enriched_fixture()
            result["calculation"]["components"]["ox_pump"]["outputs"][key] *= 1.1
            self.assertEqual(handoff(result)["status"], "conflict", key)
        result = enriched_fixture()
        result["pump_geometry"] = pump_geometry_view(result)
        result["pump_geometry"]["pumps"]["ox"]["meridional"]["hub"][0][0] = 100
        self.assertEqual(handoff(result)["status"], "conflict")

    def test_invalid_scalars_stay_finite_json_and_do_not_generate_torque(self):
        for value in (float("nan"), float("inf"), -1, 0, True, "123"):
            result = enriched_fixture()
            result["calculation"]["components"]["ox_pump"]["outputs"]["power_W"] = value
            view = handoff(result)
            self.assertEqual(view["status"], "conflict")
            self.assertIsNone(fields(view, "power")["power_W"]["value"])
            self.assertIsNone(fields(view, "power")["shaft_torque_n_m"]["value"])
            json.dumps(view, allow_nan=False)
        result = enriched_fixture()
        result["calculation"]["components"]["ox_pump"]["outputs"]["eta"] = 1.2
        self.assertEqual(handoff(result)["status"], "conflict")

    def test_unavailable_records_and_unsupported_fuel_are_not_relabelled(self):
        for result in (None, {}, {"calculation": []}, {"calculation": {"components": []}}):
            view = handoff(result)
            self.assertEqual(view["status"], "unavailable")
            self.assertEqual(view["stage_allocation"], [])
            json.dumps(view, allow_nan=False)
        result = fixture()
        result["calculation"]["inputs"]["propellant_pair"] = "lox_rp1"
        self.assertEqual(handoff(result, "fuel")["status"], "unavailable")

    def test_psi_and_reference_head_definitions_are_explicit(self):
        view = handoff(enriched_fixture())
        geometry = fields(view, "geometry")
        self.assertEqual(geometry["cfturbo_work_coefficient"]["value"], 2 * geometry["head_coefficient_psi"]["value"])
        self.assertIn("同一参考压头", geometry["cfturbo_work_coefficient"]["note"])
        self.assertIn("无端口速度头", fields(view, "design_point")["total_head_m"]["note"])
        self.assertIn("不直接填作液力效率", fields(view, "power")["eta"]["note"])


if __name__ == "__main__":
    unittest.main()
