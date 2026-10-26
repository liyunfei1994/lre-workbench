"""Parameter presentation must preserve the selected physical problem."""
from copy import deepcopy
from dataclasses import asdict
import unittest
import warnings

from webapp.solver import EngineInputs, input_values
from webapp.ui_schema import design_ui_schema
from cycle import FFSCCycleModel
from parameter_contract import preburner_design_mode, preburner_mode_updates


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
        self.assertEqual(normalized, before)
        self.assertEqual(values, before)
        with self.assertRaisesRegex(ValueError, "preburner_design_mode"):
            input_values(values | {"preburner_design_mode": "temperature"})

    def test_schema_has_one_virtual_selector_and_retains_canonical_contract(self):
        schema = design_ui_schema()
        canonical = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        system = [f for g in schema["system_groups"] for f in g["fields"]]
        virtual = [f for f in system if f.get("virtual")]
        self.assertEqual([f["key"] for f in virtual], ["preburner_design_mode"])
        self.assertEqual(set(canonical), set(schema["defaults"]))
        self.assertNotIn("preburner_design_mode", schema["defaults"])
        self.assertTrue(all(f["key"] in canonical or f.get("virtual") for f in system))
        self.assertEqual([c["value"] for c in virtual[0]["choices"]], ["temperature", "mixture_ratio"])
        self.assertTrue(canonical["use_target_preburner_temp"]["hidden"])
        self.assertTrue(canonical["use_target_preburner_mr"]["hidden"])
        self.assertEqual(canonical["ox_preburner_mr_target"]["roles_by_mode"],
                         {"temperature": "initial_guess", "mixture_ratio": "design_input"})

    def test_names_do_not_confuse_pressure_ratio_and_thermodynamic_reference(self):
        schema = design_ui_schema()
        fields = {f["key"]: f for g in schema["groups"] for f in g["fields"]}
        self.assertIn("pPB/pc", fields["ox_preburner_pressure_ratio"]["label"])
        self.assertNotIn("涡轮压比", fields["ox_preburner_pressure_ratio"]["label"])
        self.assertIn("贮箱出口", fields["fuel_inlet_temp"]["label"])
        self.assertIn("温升折减", fields["combustion_efficiency"]["label"])
        self.assertNotEqual(fields["lox_pump_specific_speed_target"]["unit"], "—")
        thermo = next(g for g in schema["system_groups"] if g["id"] == "thermo")
        self.assertIn("初值", thermo["title"])

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
                self.assertIn("非已核实实机数据", fields[key]["provenance"]["label"])
        self.assertIn("目的预燃室压力 pPB", fields["preburner_injector_dp_ratio"]["help"])
        self.assertIn("附加调节阀", fields["valve_line_dp_ratio"]["help"])
        self.assertIn("不是初值", fields["cooling_dp_pa"]["help"])
        self.assertEqual(fields["ox_preburner_pressure_ratio"]["role"], "initial_guess")

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
