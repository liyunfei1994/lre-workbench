"""Both analysis modes expose the same authoritative parameter identities."""
from dataclasses import asdict
import unittest

from lre_workbench.domain.analysis_roles import analysis_parameter_roles, parameter_analysis_roles
from lre_workbench.domain.models import EngineInputs
from lre_workbench.domain.parameters import concept_parameter_role, parameter_metadata
from webapp.ui_schema import design_ui_schema


class AnalysisRolesTests(unittest.TestCase):
    def test_catalog_separates_valve_definition_from_operating_opening(self):
        catalog = analysis_parameter_roles()
        rows = {row["id"]: row for row in catalog["rows"]}
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows["valve_hardware"]["fixed_hardware_steady"]["roles"], ["hardware_definition"])
        self.assertIn("control_setting", rows["valve_opening"]["fixed_hardware_steady"]["roles"])
        self.assertNotIn("hardware_definition", rows["valve_opening"]["fixed_hardware_steady"]["roles"])
        for key in ("preburner_temperature", "preburner_distribution", "pump_outlet_pressure",
                    "shaft_speed", "chamber_pressure", "thrust", "overall_mixture_ratio"):
            self.assertIn("computed_output", rows[key]["fixed_hardware_steady"]["roles"])
        self.assertIn("conditional_external_boundary", rows["shaft_speed"]["fixed_hardware_steady"]["roles"])
        for key in ("thrust", "overall_mixture_ratio"):
            self.assertIn("conditional_control_target", rows[key]["fixed_hardware_steady"]["roles"])

    def test_ui_and_field_metadata_use_shared_detached_catalog(self):
        catalog = analysis_parameter_roles()
        self.assertEqual(design_ui_schema()["analysis_parameter_roles"], catalog)
        for key in ("pc", "thrust", "of_ratio", "ox_preburner_temp_target", "valve_line_dp_ratio",
                    "lox_pump_speed_rpm_override", "expansion_ratio"):
            self.assertEqual(parameter_metadata(key)["analysis_modes"], parameter_analysis_roles(key))
        catalog["rows"][0]["fixed_hardware_steady"]["roles"].append("bad")
        self.assertNotIn("bad", analysis_parameter_roles()["rows"][0]["fixed_hardware_steady"]["roles"])

    def test_preburner_submodes_remain_distinct_from_analysis_modes(self):
        values = asdict(EngineInputs())
        self.assertEqual(concept_parameter_role("ox_preburner_mr_target", values), "initial_guess")
        values.update(use_target_preburner_temp=0, use_target_preburner_mr=1)
        self.assertEqual(concept_parameter_role("ox_preburner_mr_target", values), "design_input")
        self.assertEqual(concept_parameter_role("ox_preburner_temp_target", values), "legacy")
        values.update(use_target_preburner_mr=0)
        self.assertEqual(concept_parameter_role("ox_preburner_pressure_ratio", values), "design_input")
        metadata = parameter_metadata("ox_preburner_mr_target")
        self.assertEqual(metadata["roles_by_mode"]["temperature"], "initial_guess")
        self.assertIn("computed_output", metadata["analysis_modes"]["fixed_hardware_steady"]["roles"])

    def test_field_roles_do_not_promote_limits_or_design_budgets_into_outputs_or_hardware(self):
        for key in ("ox_preburner_temp_limit", "fuel_preburner_temp_limit_status", "ox_preburner_temp_limit_basis"):
            self.assertEqual(parameter_analysis_roles(key)["fixed_hardware_steady"]["roles"], ["constraint"])
        for key in ("valve_line_dp_ratio", "cooling_channel_auto_size", "cooling_target_velocity_ms"):
            self.assertEqual(parameter_analysis_roles(key)["fixed_hardware_steady"]["roles"], ["design_only"])

    def test_pump_stage_structure_is_distinct_from_sizing_coefficients(self):
        for side in ("lox", "fuel"):
            self.assertEqual(parameter_analysis_roles(f"{side}_pump_n_stages")
                             ["fixed_hardware_steady"]["roles"], ["hardware_definition"])
            for suffix in ("specific_speed_target", "head_coefficient_psi", "inlet_flow_coefficient",
                           "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio"):
                modes = parameter_analysis_roles(f"{side}_pump_{suffix}")
                self.assertEqual(modes["concept_design"]["roles"], ["design_choice"])
                self.assertEqual(modes["fixed_hardware_steady"]["roles"], ["design_only"])
                self.assertEqual(parameter_metadata(f"{side}_pump_{suffix}")["analysis_modes"], modes)

    def test_branch_pressure_assumptions_do_not_become_fixed_hardware_definitions(self):
        for branch in ("fuel_to_opb", "fuel_to_fpb", "ox_to_opb", "ox_to_fpb"):
            for kind in ("line", "injector"):
                key = f"{branch}_{kind}_dp_ratio"
                with self.subTest(key=key):
                    modes = parameter_analysis_roles(key)
                    self.assertEqual(modes["concept_design"]["roles"], ["model_assumption"])
                    self.assertEqual(modes["fixed_hardware_steady"]["roles"], ["design_only"])
                    self.assertEqual(parameter_metadata(key)["analysis_modes"], modes)

    def test_turbine_stages_sizing_and_speed_constraints_have_distinct_roles(self):
        values = asdict(EngineInputs())
        for side in ("ox", "fuel"):
            self.assertEqual(parameter_analysis_roles(f"turb_{side}_n_stages_user")
                             ["fixed_hardware_steady"]["roles"], ["hardware_definition"])
            for suffix in ("u_over_c_target", "reaction_degree", "meanline_flow_coefficient", "stage_work_fractions"):
                modes = parameter_analysis_roles(f"turb_{side}_{suffix}")
                self.assertEqual(modes["concept_design"]["roles"], ["design_choice"])
                self.assertEqual(modes["fixed_hardware_steady"]["roles"], ["design_only"])
            key = f"turb_{side}_u_max_ms"
            self.assertEqual(concept_parameter_role(key, values), "constraint")
            self.assertEqual(parameter_analysis_roles(key)["fixed_hardware_steady"]["roles"], ["constraint"])


if __name__ == "__main__":
    unittest.main()
