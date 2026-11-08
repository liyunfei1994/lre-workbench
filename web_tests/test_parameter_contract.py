"""Parameter presentation must preserve the selected physical problem."""
from copy import deepcopy
from dataclasses import asdict
import unittest
import warnings

from webapp.solver import EngineInputs, input_values, ffsc_input_values
from webapp.ui_schema import design_ui_schema
from lre_workbench.systems.ffsc import FFSCCycleModel
from lre_workbench.domain.models import ValidationError
from lre_workbench.domain.parameters import preburner_design_mode, preburner_mode_updates


class ParameterContractTests(unittest.TestCase):
    def test_mode_adapter_matches_real_solver_precedence_and_preserves_history(self):
        for temperature in (0.0, 0.49, 0.5, 1.0):
            for mr in (0.0, 0.49, 0.5, 1.0):
                with self.subTest(temperature=temperature, mr=mr):
                    inputs = EngineInputs(use_target_preburner_temp=temperature,
                                          use_target_preburner_mr=mr)
                    before = asdict(inputs)
                    model = FFSCCycleModel(inputs)
                    expected = ("temperature" if model._temp_target_mode() else
                                "mixture_ratio" if model._target_mr_mode() else "legacy_free_split")
                    self.assertEqual(preburner_design_mode(before), expected)
                    self.assertEqual(asdict(inputs), before)

    def test_user_selection_updates_only_two_compatible_flags(self):
        for mode, expected in (("temperature", (1.0, 1.0)), ("mixture_ratio", (0.0, 1.0))):
            before = asdict(EngineInputs(use_target_preburner_temp=0, use_target_preburner_mr=0))
            updated = before | preburner_mode_updates(mode)
            self.assertEqual((updated["use_target_preburner_temp"], updated["use_target_preburner_mr"]), expected)
            self.assertEqual(preburner_design_mode(updated), mode)
            self.assertEqual({k:v for k,v in updated.items() if not k.startswith("use_target_preburner")},
                             {k:v for k,v in before.items() if not k.startswith("use_target_preburner")})
        with self.assertRaises(ValueError):
            preburner_mode_updates("legacy_free_split")

    def test_hidden_fields_remain_lossless_on_api_roundtrip(self):
        values = asdict(EngineInputs())
        values.update(fuel_split_to_ox_preburner=0.08, ox_split_to_ox_preburner=0.95,
                      coolant_cp=2850, cooling_heat_load=71e6, lox_pump_head_coefficient_psi=0.47,
                      fuel_pump_specific_speed_target=32)
        before = deepcopy(values)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            normalized, _ = input_values(values)
        self.assertEqual(normalized, ffsc_input_values(before))
        self.assertEqual(values, before)
        with self.assertRaisesRegex(ValueError, "preburner_design_mode"):
            input_values(values | {"preburner_design_mode": "temperature"})

    def test_virtual_selectors_retain_the_canonical_storage_contract(self):
        schema = design_ui_schema()
        canonical = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        system = [f for g in schema["system_groups"] for f in g["fields"]]
        virtual = [f for f in system if f.get("virtual")]
        self.assertEqual([f["key"] for f in virtual],
                         ["preburner_design_mode", "lox_pump_speed_mode", "fuel_pump_speed_mode",
                          "turb_ox_stage_mode", "turb_fuel_stage_mode"])
        self.assertEqual(set(canonical), set(schema["defaults"]))
        self.assertNotIn("preburner_design_mode", schema["defaults"])
        self.assertTrue(all(f["key"] in canonical or f.get("virtual") for f in system))
        self.assertEqual([c["value"] for c in virtual[0]["choices"]], ["temperature", "mixture_ratio"])
        self.assertTrue(canonical["use_target_preburner_temp"]["hidden"])
        self.assertTrue(canonical["use_target_preburner_mr"]["hidden"])
        self.assertEqual(canonical["ox_preburner_mr_target"]["roles_by_mode"],
                         {"temperature": "initial_guess", "mixture_ratio": "design_input"})

    def test_pump_modes_are_presentation_only_and_keep_the_saved_speed_contract(self):
        schema = design_ui_schema()
        group = next(g for g in schema["system_groups"] if g["id"] == "pumps")
        fields = {f["key"]: f for f in group["fields"]}
        for side in ("lox", "fuel"):
            mode_key, speed_key = f"{side}_pump_speed_mode", f"{side}_pump_speed_rpm_override"
            mode = fields[mode_key]
            self.assertTrue(mode["virtual"])
            self.assertEqual(mode["pump_speed_key"], speed_key)
            self.assertEqual([c["value"] for c in mode["choices"]], ["specified", "estimated"])
            self.assertNotIn(mode_key, schema["defaults"])
            self.assertNotIn("all", mode["visibility"])
            self.assertEqual(fields[speed_key]["visibility"]["all"],
                             [{"key": speed_key, "op": "neq", "value": 0}])
            self.assertEqual(fields[f"{side}_pump_specific_speed_target"]["visibility"]["all"],
                             [{"key": speed_key, "op": "eq", "value": 0}])
            for rpm in (0, 27123):
                values = asdict(EngineInputs()) | {speed_key: rpm}
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    normalized, _ = input_values(values)
                self.assertEqual(normalized, ffsc_input_values(values))
            with self.assertRaisesRegex(ValueError, mode_key):
                input_values(asdict(EngineInputs()) | {mode_key: "estimated"})
        self.assertEqual(fields["fuel_pump_speed_mode"]["visibility"]["any"],
                         fields["fuel_pump_speed_rpm_override"]["visibility"]["any"])

    def test_pump_initial_design_assumptions_are_not_numerical_seeds(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        for side in ("lox", "fuel"):
            for suffix in ("speed_rpm_override", "specific_speed_target", "n_stages", "head_coefficient_psi",
                           "inlet_flow_coefficient", "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio"):
                field = fields[f"{side}_pump_{suffix}"]
                self.assertEqual(field["role"], "component_sizing")
                self.assertEqual(field["role_label"], "初选假设")

    def test_turbine_stage_modes_preserve_canonical_inputs_and_invalid_values_remain_editable(self):
        schema = design_ui_schema()
        canonical = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        fields = {f["key"]: f for g in schema["system_groups"] for f in g["fields"]}
        for side in ("ox", "fuel"):
            mode_key, stages_key = f"turb_{side}_stage_mode", f"turb_{side}_n_stages_user"
            mode = fields[mode_key]
            self.assertTrue(mode["virtual"])
            self.assertEqual(mode["turbine_stages_key"], stages_key)
            self.assertEqual([choice["value"] for choice in mode["choices"]], ["estimated", "specified"])
            self.assertNotIn(mode_key, canonical)
            self.assertNotIn(mode_key, schema["defaults"])
            self.assertEqual(fields[stages_key]["visibility"]["all"],
                             [{"key": stages_key, "op": "neq", "value": 0}])
            self.assertNotIn("all", canonical[stages_key]["visibility"])
            self.assertNotIn("all", mode["visibility"])
            for stages in (0, 3):
                values = asdict(EngineInputs()) | {stages_key: stages}
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    normalized, _ = input_values(values)
                self.assertEqual(normalized, ffsc_input_values(values))
            with self.assertRaisesRegex(ValueError, mode_key):
                input_values(asdict(EngineInputs()) | {mode_key: "specified"})
            for bad in (None, 1.5, -1):
                with self.assertRaises((ValueError, ValidationError)):
                    input_values(asdict(EngineInputs()) | {stages_key: bad})
        self.assertEqual(fields["turb_ox_stage_mode"]["visibility"]["any"],
                         fields["turb_ox_n_stages_user"]["visibility"]["any"])
        self.assertEqual(fields["turb_fuel_stage_mode"]["visibility"], {})

    def test_turbine_sizing_roles_and_optional_controls_do_not_claim_cycle_matching(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        self.assertEqual(fields["turbine_boundary_basis"]["role"], "model_assumption")
        for side in ("ox", "fuel"):
            for suffix in ("u_over_c_target", "n_stages_user", "reaction_degree",
                           "meanline_flow_coefficient", "stage_work_fractions"):
                field = fields[f"turb_{side}_{suffix}"]
                self.assertEqual(field["role"], "component_sizing")
                self.assertEqual(field["role_label"], "初选假设")
            self.assertEqual(fields[f"turb_{side}_u_max_ms"]["role"], "constraint")
            for suffix in ("reaction_degree", "meanline_flow_coefficient"):
                self.assertEqual(fields[f"turb_{side}_{suffix}"]["visibility"]["all"],
                                 [{"key": "turbine_meanline_mode", "op": "neq", "value": "disabled"}])
            self.assertEqual(fields[f"turb_{side}_stage_work_fractions"]["visibility"]["all"],
                             [{"key": "turbine_meanline_mode", "op": "eq", "value": "custom_work"}])
        values = asdict(EngineInputs()) | {"turbine_meanline_mode": "disabled",
            "turb_ox_reaction_degree": 0.37, "turb_fuel_meanline_flow_coefficient": 0.72,
            "turb_ox_stage_work_fractions": "[0.7, 0.3]"}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            normalized, _ = input_values(values)
        self.assertEqual(normalized, ffsc_input_values(values))

    def test_names_do_not_confuse_pressure_ratio_and_thermodynamic_reference(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        self.assertIn("pPB/pc", fields["ox_preburner_pressure_ratio"]["label"])
        self.assertNotIn("涡轮压比", fields["ox_preburner_pressure_ratio"]["label"])
        self.assertIn("贮箱出口", fields["fuel_inlet_temp"]["label"])
        self.assertIn("温升折减", fields["combustion_efficiency"]["label"])
        self.assertNotEqual(fields["lox_pump_specific_speed_target"]["unit"], "—")
        solver = next(g for g in schema["system_groups"] if g["id"] == "solver")
        self.assertIn("启动估计", solver["initials_title"])

    def test_design_targets_conditions_and_seeds_keep_distinct_roles(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        for key in ("thrust", "pc", "of_ratio"):
            self.assertEqual(fields[key]["role"], "design_input")
        self.assertIn("环境压力", fields["thrust"]["label"])
        self.assertIn("指定", fields["pc"]["label"])
        self.assertIn("指定整机氧燃质量比", fields["of_ratio"]["label"])
        self.assertEqual(fields["ambient_pressure"]["role"], "boundary")
        self.assertEqual(fields["target_isp"]["role"], "initial_guess")
        self.assertIn("初值", fields["target_isp"]["label"])

    def test_geometry_ratios_keep_independent_meanings_and_saved_values(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        self.assertIn("主喷管", fields["expansion_ratio"]["label"])
        self.assertIn("Ae/At", fields["expansion_ratio"]["label"])
        self.assertNotIn("gg_exhaust_expansion_ratio", fields)
        self.assertIn("Acc/At", fields["chamber_contraction_ratio"]["label"])
        self.assertIn("冷却终点", fields["cooling_cover_eps"]["label"])
        self.assertIn("长度比", fields["nozzle_bell_fraction"]["label"])
        for key in ("expansion_ratio",
                    "chamber_contraction_ratio", "cooling_cover_eps", "nozzle_bell_fraction"):
            self.assertEqual(fields[key]["role"], "design_input")
        # Moving controls must preserve the old SI keys/defaults and distinct
        # saved geometries, including partial coverage of the main nozzle.
        defaults = schema["defaults"]
        self.assertEqual({key: defaults[key] for key in (
            "expansion_ratio", "cooling_cover_eps")},
            {"expansion_ratio": 34.34, "cooling_cover_eps": 0.0})
        values = asdict(EngineInputs()) | {
            "expansion_ratio": 42.0, "gg_exhaust_expansion_ratio": 5.0,
            "chamber_contraction_ratio": 3.0, "cooling_cover_eps": 12.0,
            "nozzle_bell_fraction": 0.7,
        }
        before = deepcopy(values)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            normalized, _ = input_values(values)
        self.assertEqual(normalized, ffsc_input_values(before))
        self.assertEqual(values, before)

    def test_combustion_loss_selector_is_explicit_and_hides_inactive_legacy_factor(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        self.assertEqual([choice["value"] for choice in fields["combustion_loss_model"]["choices"]],
                         ["adiabatic", "legacy_temperature_rise"])
        self.assertEqual(fields["combustion_efficiency"]["visibility"],
                         {"all": [{"key": "combustion_loss_model", "op": "eq", "value": "legacy_temperature_rise"}]})
        self.assertEqual(schema["defaults"]["combustion_loss_model"], "legacy_temperature_rise")

    def test_pressure_budget_roles_are_separate_from_initial_guesses_and_valve_results(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        for key in ("preburner_injector_dp_ratio", "ox_turbine_exhaust_dp_ratio",
                    "fuel_turbine_exhaust_dp_ratio", "valve_line_dp_ratio", "cooling_dp_pa"):
            with self.subTest(key=key):
                self.assertEqual(fields[key]["role"], "model_assumption")
                self.assertIn("预算", fields[key]["label"])
                source = fields[key]["provenance"]["label"]
                self.assertIn("压降", source)
                self.assertTrue("非已核实实机数据" in source or "待部件数据校核" in source)
        self.assertIn("目的预燃室压力", fields["preburner_injector_dp_ratio"]["help"])
        self.assertIn("附加调节压降", fields["valve_line_dp_ratio"]["help"])
        self.assertIn("不是初值", fields["cooling_dp_pa"]["help"])
        self.assertEqual(fields["ox_preburner_pressure_ratio"]["role"], "initial_guess")

    def test_branch_pressure_budgets_are_optional_model_assumptions(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        for branch in ("fuel_to_opb", "fuel_to_fpb", "ox_to_opb", "ox_to_fpb"):
            for kind in ("line", "injector"):
                key = f"{branch}_{kind}_dp_ratio"
                with self.subTest(key=key):
                    field = fields[key]
                    self.assertEqual(field["role"], "model_assumption")
                    self.assertEqual(field["type"], "number")
                    self.assertTrue(field["nullable"])
                    self.assertIsNone(field["default"])
                    self.assertEqual(field["fallback_key"], "valve_line_dp_ratio" if kind == "line"
                                     else "preburner_injector_dp_ratio")
                    self.assertEqual(field["analysis_modes"]["fixed_hardware_steady"]["roles"], ["design_only"])

    def test_cooling_has_one_physical_mode_and_inactive_legacy_controls_are_hidden(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        for key in ("cooling_dp_auto", "cooling_marching_enable", "coolant_cp"):
            self.assertTrue(fields[key]["hidden"])
            self.assertEqual(fields[key]["role"], "legacy")
        self.assertEqual(fields["cooling_dp_pa"]["visibility"],
                         {"all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]})
        self.assertEqual(fields["cooling_grid_points"]["role"], "solver_setting")
        self.assertTrue(fields["cooling_grid_points"]["advanced"])
        for key in ("fuel_inlet_temp", "ox_inlet_temp"):
            self.assertNotIn("GG 仍采用", fields[key]["help"])
        for key in ("cooling_channel_width_m", "cooling_n_channels", "cooling_cover_eps"):
            self.assertEqual(fields[key]["role"], "design_input")


if __name__ == "__main__":
    unittest.main()
