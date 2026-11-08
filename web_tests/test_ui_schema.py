"""Desktop/browser presentation parity and SI serialization regressions."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import unittest

from webapp.solver import EngineInputs, input_values, presets, platform_defaults
from lre_workbench.domain.models import ValidationError
from webapp.ui_schema import design_ui_schema
from lre_workbench.domain.metadata import DISPLAY_UNIT_MAP, PARAM_GROUPS, CHAMBER_INPUTS
from lre_workbench.materials.catalog import MATERIALS

ROOT = Path(__file__).resolve().parent.parent


class UiSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = design_ui_schema()
        self.fields = {field["key"]: field for group in self.schema["groups"]
                       for field in group["fields"]}

    def test_every_input_has_one_field_and_si_default(self):
        defaults = platform_defaults()
        all_fields = [field for group in self.schema["groups"] for field in group["fields"]]
        self.assertEqual(len(all_fields), len(self.fields))
        self.assertEqual(set(self.fields), set(defaults))
        self.assertEqual(self.schema["defaults"], defaults)
        self.assertEqual({key: field["default"] for key, field in self.fields.items()}, defaults)
        json.dumps(self.schema, allow_nan=False)

    def test_labels_units_and_numeric_types_follow_desktop(self):
        for title, items in PARAM_GROUPS:
            for key, label, unit in items:
                if key not in platform_defaults():
                    self.assertNotIn(key, self.fields)
                    continue
                with self.subTest(key=key):
                    field = self.fields[key]
                    self.assertTrue(field["full_label"])
                    self.assertNotIn("GG", field["full_label"])
                    self.assertEqual((field["factor"], field["unit"]),
                                     DISPLAY_UNIT_MAP.get(key, (1, unit)))
        for key, (label, unit, tooltip) in CHAMBER_INPUTS.items():
            self.assertTrue(self.fields[key]["full_label"])
            self.assertEqual(self.fields[key]["unit"], unit)
        self.assertEqual(self.fields["throttle_steps"]["type"], "int")
        self.assertEqual(self.fields["pc"]["type"], "number")
        self.assertEqual(self.fields["cooling_channel_table_json"]["type"], "text")
        self.assertTrue(self.fields["cooling_wall_conductivity"]["hidden"])
        self.assertEqual(self.fields["cooling_wall_conductivity"]["role"], "legacy")

    def test_preset_display_roundtrip_retains_si_values(self):
        for preset in presets():
            values = dict(preset["inputs"])
            for key, field in self.fields.items():
                if field["type"] in ("number", "int") and values[key] is not None:
                    shown = values[key] * field["factor"]
                    values[key] = shown / field["factor"]
            normalized, _ = input_values(values)
            for key, expected in preset["inputs"].items():
                with self.subTest(preset=preset["key"], key=key):
                    if isinstance(expected, (float, int)):
                        self.assertAlmostEqual(normalized[key], expected,
                                               delta=max(1e-12, abs(expected) * 1e-12))
                    else:
                        self.assertEqual(normalized[key], expected)

    def test_cycle_availability_and_material_choices(self):
        groups = {group["id"]: group for group in self.schema["groups"]}
        self.assertEqual(groups["cycle"]["cycles"], ["ffsc"])
        self.assertEqual(self.fields["cycle_type"]["cycles"], ["ffsc"])
        self.assertEqual(self.fields["propellant_pair"]["type"], "select")
        self.assertEqual({c["value"] for c in self.fields["propellant_pair"]["choices"]},
                         {"lox_methane"})
        models = {m["id"]: m for m in self.schema["propellant_models"]}
        self.assertEqual(set(models), {"lox_methane"})
        self.assertEqual(models["lox_methane"]["supported_cycles"], ("ffsc",))
        self.assertNotIn("gg_layout", self.fields)
        self.assertNotIn("gas_generator", groups)
        self.assertEqual(groups["pressure_ffsc"]["cycles"], ["ffsc"])
        for key, field in self.fields.items():
            if field["type"] != "select":
                continue
            values = [choice["value"] for choice in field["choices"]]
            self.assertIn(field["default"], values)
            if key.startswith("turb_ox_") or key == "lox_pump_impeller_material":
                self.assertTrue(all(MATERIALS[value].oxygen_compatible for value in values))

    def test_pump_sizing_sections_put_mode_before_its_dependent_inputs(self):
        group = next(g for g in self.schema["system_groups"] if g["id"] == "pumps")
        self.assertEqual(group["title"], "泵方案与尺寸初估")
        for side in ("lox", "fuel"):
            section = next(s for s in group["sections"] if s["id"] == side)
            self.assertEqual(section["keys"][:3], [f"{side}_pump_speed_mode",
                                                  f"{side}_pump_speed_rpm_override",
                                                  f"{side}_pump_specific_speed_target"])
        # Virtual selectors must never become new stored physical parameters.
        self.assertEqual(set(self.fields), set(platform_defaults()))
        self.assertEqual(self.schema["defaults"], platform_defaults())

    def test_server_schema_import_does_not_load_qt_or_desktop(self):
        code = (
            "from webapp.ui_schema import design_ui_schema; import sys; "
            "design_ui_schema(); "
            "assert 'lre_workbench.desktop.legacy_window' not in sys.modules; "
            "assert not any(n.startswith(('PySide6', 'matplotlib')) for n in sys.modules)"
        )
        process = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_turbine_sizing_separates_state_definition_and_stage_selection(self):
        group = next(g for g in self.schema["system_groups"] if g["id"] == "turbines")
        self.assertEqual(group["title"], "涡轮方案与尺寸初估")
        self.assertEqual(group["sections"][0]["keys"], ["turbine_boundary_basis"])
        for side in ("ox", "fuel"):
            section = next(s for s in group["sections"] if s["id"] == side)
            self.assertEqual(section["keys"][1:3], [f"turb_{side}_stage_mode", f"turb_{side}_n_stages_user"])
        self.assertEqual(set(self.fields), set(platform_defaults()))
        self.assertEqual(self.schema["defaults"], platform_defaults())

    def test_system_editor_keeps_cycle_boundaries_and_preserves_compatibility(self):
        groups = self.schema["system_groups"]
        visible = {f["key"] for g in groups for f in g["fields"]}
        self.assertTrue({"pc", "thrust", "cooling_dp_pa", "cooling_heat_load",
                         "cooling_bartz_mode", "cooling_grid_points"} <= visible)
        self.assertFalse({"cooling_marching_enable", "chamber_wall_allow_pa",
                          "cooling_dp_auto", "coolant_cp", "turb_ox_rotor_material",
                          "throttle_steps", "gg_mech_efficiency", "gg_layout"} & visible)
        self.assertFalse({"chamber_structure",
                          "turbine_materials"} & {g["id"] for g in groups})
        self.assertTrue({"chamber_contour", "chamber_channels", "turbines"} <= {g["id"] for g in groups})
        # Restricting the editor must not discard old assumptions on a save.
        self.assertEqual(set(self.fields), set(platform_defaults()))
        cooling = next(g for g in groups if g["id"] == "cooling")
        mode = next(f for f in cooling["fields"] if f["key"] == "cooling_bartz_mode")
        self.assertTrue(mode["read_only"])
        self.assertNotIn("choices", mode)
        self.assertTrue(all(f["advanced"] for f in cooling["fields"] if f["key"] in ("cooling_heat_load", "cooling_dp_pa")))
        self.assertEqual(self.fields["cooling_bartz_mode"]["type"], "number")

    def test_design_conditions_and_geometry_have_single_editor_locations(self):
        geometry_keys = {
            "expansion_ratio", "chamber_contraction_ratio", "chamber_l_star_m",
            "chamber_conv_half_angle_deg", "nozzle_bell_fraction", "cooling_cover_eps",
            "nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg",
        }
        for collection in ("groups", "system_groups"):
            with self.subTest(collection=collection):
                groups = {g["id"]: g for g in self.schema[collection]}
                design = groups["design"]
                contour = groups["chamber_contour"]
                self.assertEqual(design["title"], "设计目标与工况")
                self.assertEqual({f["key"] for f in design["fields"]},
                                 {"thrust", "pc", "of_ratio", "ambient_pressure", "target_isp"})
                self.assertEqual({f["key"] for f in contour["fields"]},
                                 geometry_keys - {"cooling_cover_eps"} if collection == "system_groups" else geometry_keys)
                for key in geometry_keys:
                    owners = [g["id"] for g in groups.values()
                              for field in g["fields"] if field["key"] == key]
                    owner = "chamber_cooling_layout" if collection == "system_groups" and key == "cooling_cover_eps" else "chamber_contour"
                    self.assertEqual(owners, [owner], key)
                # The GG exhaust nozzle is independent hardware, not another
                # editor for the main nozzle's expansion ratio.
                exhaust_owners = [g["id"] for g in groups.values()
                                  for f in g["fields"] if f["key"] == "gg_exhaust_expansion_ratio"]
                self.assertEqual(exhaust_owners, [])
        # A shared storage field may have separate, mutually exclusive editors
        # for the full-flow and gas-generator cycles.
        for cycle in ("ffsc", "gg"):
            all_system_keys = [f["key"] for g in self.schema["system_groups"]
                               if cycle in g.get("cycles", ("ffsc", "gg"))
                               for f in g["fields"] if cycle in f.get("cycles", ("ffsc", "gg"))]
            self.assertEqual(len(all_system_keys), len(set(all_system_keys)), cycle)

    def test_ffsc_editor_has_fixed_architecture_without_relabeling_historic_inputs(self):
        groups = {g["id"]: g for g in self.schema["system_groups"]}
        fields = {f["key"]: f for g in groups.values() for f in g["fields"]}
        self.assertFalse({"gas_generator", "pressure"} & set(groups))
        self.assertFalse(any(key.startswith("gg_") for key in fields))
        for key, value in (("cycle_type", "ffsc"), ("propellant_pair", "lox_methane")):
            self.assertTrue(fields[key]["read_only"])
            self.assertEqual([c["value"] for c in fields[key]["choices"]], [value])
            self.assertEqual(fields[key]["default"], self.schema["defaults"][key])
            self.assertTrue(self.fields[key].get("read_only", False))
            self.assertEqual(len(self.fields[key]["choices"]), 1)
        for group in groups.values():
            self.assertEqual(group["cycles"], ["ffsc"])
            self.assertEqual(group["propellants"], ["lox_methane"])
            for field in group["fields"]:
                self.assertEqual(field["cycles"], ["ffsc"])
                self.assertEqual(field["propellants"], ["lox_methane"])
                for operator in ("all", "any"):
                    self.assertNotIn("gg_layout", {c["key"] for c in field.get("visibility", {}).get(operator, [])})
        # The complete case schema and original fields are retained for historical
        # records. Narrowing the editor must not migrate the underlying values.
        self.assertNotIn("gg_layout", self.fields)
        self.assertEqual(set(self.fields), set(platform_defaults()))
        self.assertEqual(self.schema["defaults"], platform_defaults())
        self.assertEqual(self.fields["turb_fuel_u_over_c_target"]["label"], "富燃侧涡轮 初选速比 U/C₀")
        self.assertIn("富燃侧", fields["turb_fuel_u_over_c_target"]["label"])
        self.assertIn("富氧侧", fields["turb_ox_u_over_c_target"]["label"])
        self.assertIn("甲烷泵", fields["pump_efficiency_fuel"]["label"])
        self.assertIn("富氧侧", fields["turbine_efficiency_ox"]["label"])
        self.assertIn("富燃侧", fields["turbine_efficiency_fuel"]["label"])
        self.assertEqual([s["title"] for s in groups["pumps"]["sections"]], ["液氧泵", "甲烷泵"])
        self.assertEqual([s["title"] for s in groups["turbines"]["sections"]][-2:],
                         ["富氧涡轮（液氧泵轴系）", "富燃涡轮（甲烷泵轴系）"])
        text = json.dumps(self.schema["system_groups"], ensure_ascii=False)
        self.assertNotIn("GG", text)
        self.assertNotIn("共轴", text)
        self.assertNotIn("燃气发生器", text)
        self.assertNotIn("煤油", text)

    def test_cooling_channel_editor_owns_complete_geometry_and_optional_overrides(self):
        groups = self.schema["system_groups"]
        group = next(g for g in groups if g["id"] == "chamber_channels")
        fields = {f["key"]: f for f in group["fields"]}
        self.assertEqual(group["title"], "再生冷却通道设计")
        self.assertEqual([s["id"] for s in group["sections"]], ["conditions", "geometry", "models", "limits"])
        self.assertEqual(fields["cooling_channel_table_json"]["custom_editor"], "channel_distribution")
        self.assertFalse(fields["cooling_channel_table_json"]["hidden"])
        keys = ("cooling_channel_table_json", "cooling_channel_roughness_m", "cooling_k_minor_inlet",
                "cooling_k_minor_outlet", "cooling_htc_enhancement_basis")
        for key in keys:
            self.assertEqual([g["id"] for g in groups for f in g["fields"] if f["key"] == key], ["chamber_channels"])
        for key in keys[1:4]:
            self.assertIsNone(fields[key]["default"])
            self.assertTrue(fields[key]["nullable"])
        self.assertEqual(fields["cooling_channel_roughness_m"]["factor"], 1e6)
        self.assertEqual(fields["cooling_channel_roughness_m"]["unit"], "μm")
        self.assertIn({"key":"cooling_channel_roughness_m", "op":"empty"},
                      fields["cooling_channel_rel_roughness"]["visibility"]["all"])
        self.assertEqual(fields["cooling_wall_temp_limit_status"]["role_label"], "校核约束")

    def test_cooling_methods_keep_geometry_and_material_inputs_in_separate_groups(self):
        groups = {group["id"]: group for group in self.schema["system_groups"]}
        methods = {field["key"]: field for field in groups["cooling"]["fields"]}
        # The default physical method is explanatory. Only old budget cases
        # expose editable values in this group.
        self.assertEqual(set(methods), {"cooling_bartz_mode", "cooling_heat_load", "cooling_dp_pa"})
        self.assertEqual(methods["cooling_bartz_mode"]["default"], 1.0)
        self.assertTrue(methods["cooling_bartz_mode"]["read_only"])
        for key in ("cooling_heat_load", "cooling_dp_pa"):
            self.assertEqual(methods[key]["visibility"],
                             {"all": [{"key": "cooling_bartz_mode", "op": "eq", "value": 0}]})
            self.assertFalse(methods[key].get("read_only", False))

        # Explanation links must lead to the existing, unique editors. A UI
        # reorganization must not load a new physical design or duplicate it.
        defaults_by_group = {
            "chamber_contour": {"expansion_ratio": 34.34},
            "chamber_cooling_layout": {"cooling_cover_eps": 0.0},
            "chamber_channels": {
                "cooling_channel_auto_size": 0.0, "cooling_n_channels": 300.0,
                "cooling_channel_width_m": 0.0015, "cooling_channel_height_m": 0.008,
                "cooling_wall_thickness_m": 0.0005, "cooling_htc_enhancement": 1.0,
                "cooling_channel_rel_roughness": 0.003, "cooling_channel_k_minor": 3.0,
            },
        }
        for group_id, defaults in defaults_by_group.items():
            fields = {field["key"]: field for field in groups[group_id]["fields"]}
            for key, expected in defaults.items():
                with self.subTest(key=key):
                    owners = [group["id"] for group in groups.values()
                              for field in group["fields"] if field["key"] == key]
                    self.assertEqual(owners, [group_id])
                    self.assertFalse(fields[key].get("read_only", False))
                    self.assertEqual(fields[key]["default"], expected)
                    self.assertEqual(self.schema["defaults"][key], expected)
        self.assertIn("wall_thermal", groups)
        self.assertIn("wall_thermal_dataset_id", {f["key"] for f in groups["wall_thermal"]["fields"]})

    def test_solver_selection_roundtrip_and_validation(self):
        self.assertEqual(self.fields["nonlinear_solver"]["type"], "select")
        solver = next(g for g in self.schema["system_groups"] if g["id"] == "solver")
        self.assertEqual([f["key"] for f in solver["fields"]], ["nonlinear_solver",
                         "steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W", "cooling_grid_points",
                         "chamber_temp_guess", "chamber_gamma", "molar_mass"])
        for key in ("steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"):
            self.assertEqual(self.fields[key]["role"], "solver_setting")
            self.assertEqual(self.fields[key]["unit"], "W")
            self.assertEqual(self.fields[key]["cycles"], ["ffsc"])
        self.assertNotIn("thermo", {g["id"] for g in self.schema["system_groups"]})
        self.assertTrue(solver["initials_inline"])
        self.assertTrue(solver["compact"])
        # The old case/desktop group and saved values remain lossless.
        self.assertIn("thermo", {g["id"] for g in self.schema["groups"]})
        for key in ("chamber_temp_guess", "chamber_gamma", "molar_mass"):
            self.assertEqual([g["id"] for g in self.schema["system_groups"]
                              for f in g["fields"] if f["key"] == key], ["solver"])
            self.assertEqual(self.fields[key]["role"], "initial_guess")
            self.assertIn("启动估计", self.fields[key]["label"])
        self.assertEqual(input_values({})[0]["nonlinear_solver"], "newton")
        self.assertEqual(input_values({"nonlinear_solver": "trf"})[0]["nonlinear_solver"], "trf")
        self.assertEqual(input_values({"nonlinear_solver": "broyden"})[0]["nonlinear_solver"], "broyden")
        with self.assertRaisesRegex(ValidationError, "nonlinear_solver"):
            input_values({"nonlinear_solver": "silently_fallback"})

    def test_supply_efficiencies_and_chemistry_have_separate_unique_homes(self):
        groups = {g["id"]: g for g in self.schema["system_groups"]}
        self.assertEqual(groups["chamber_chemistry"]["title"], "主燃烧室热力学模型")
        self.assertEqual(groups["chamber_chemistry"]["advanced_title"], "历史兼容设置")
        self.assertTrue(all(f["advanced"] for f in groups["chamber_chemistry"]["fields"]))
        self.assertEqual(groups["inlet"]["title"], "推进剂供给边界")
        expected = {"inlet": {"fuel_inlet_temp", "ox_inlet_temp", "tank_pressure_pa",
                              "inlet_pressure", "feed_line_dp_pa", "boost_pump_efficiency"},
                    "component_efficiencies": {"pump_efficiency_ox", "pump_efficiency_fuel",
                                               "turbine_efficiency_ox", "turbine_efficiency_fuel"},
                    "chamber_chemistry": {"combustion_loss_model", "combustion_efficiency"}}
        for owner, keys in expected.items():
            self.assertTrue(groups[owner]["compact"])
            self.assertEqual({f["key"] for f in groups[owner]["fields"]}, keys)
            for key in keys:
                self.assertEqual([g["id"] for g in groups.values() for f in g["fields"] if f["key"] == key], [owner])
        # Presentation reorganizes existing inputs without silently changing a case.
        values = {key: self.schema["defaults"][key] for keys in expected.values() for key in keys}
        self.assertEqual(values["fuel_inlet_temp"], 100.)
        self.assertEqual(values["ox_inlet_temp"], 70.)
        self.assertEqual(values["combustion_loss_model"], "legacy_temperature_rise")

    def test_preburner_design_has_one_home_and_explicit_limit_provenance(self):
        groups = {g["id"]: g for g in self.schema["system_groups"]}
        self.assertNotIn("preburners", groups)
        group = groups["preburner_design"]
        self.assertEqual(group["title"], "预燃室热力设计")
        self.assertEqual([section["id"] for section in group["sections"]], ["targets", "interfaces", "results", "limits"])
        self.assertTrue(group["sections"][-1]["collapsed"])
        self.assertEqual(len(group["sections"][-1]["field_groups"]), 2)
        keys = [f["key"] for f in group["fields"]]
        self.assertEqual(len(keys), len(set(keys)))
        for side in ("ox", "fuel"):
            for suffix in ("temp_target", "mr_target", "pressure_ratio", "temp_limit", "temp_limit_status", "temp_limit_basis"):
                key = f"{side}_preburner_{suffix}"
                self.assertEqual([g["id"] for g in groups.values() for f in g["fields"] if f["key"] == key], ["preburner_design"])
            state = self.fields[f"{side}_preburner_temp_limit_status"]
            self.assertEqual([c["value"] for c in state["choices"]], ["unset", "provisional", "documented"])
            self.assertEqual(state["default"], "unset")
            self.assertTrue(self.fields[f"{side}_preburner_temp_limit_basis"]["multiline"])
            for suffix in ("", "_basis"):
                visible = self.fields[f"{side}_preburner_temp_limit{suffix}"]["visibility"]["any"]
                self.assertEqual({c["value"] for c in visible}, {"provisional", "documented"})
        # The complete storage schema retains desktop group IDs and all fields.
        self.assertIn("preburners", {g["id"] for g in self.schema["groups"]})

    def test_wall_thermal_editor_exposes_only_source_supported_equation_data(self):
        from lre_workbench.materials.wall import list_wall_datasets
        group = next(g for g in self.schema["system_groups"] if g["id"] == "wall_thermal")
        fields = {field["key"]: field for field in group["fields"]}
        self.assertEqual(set(fields), {"wall_thermal_dataset_id", "wall_manufacturing_state", "wall_material_zones_json"})
        catalog = fields["wall_thermal_dataset_id"]["dataset_catalog"]
        self.assertEqual(catalog, [d.metadata() for d in list_wall_datasets()])
        self.assertEqual({c["value"] for c in fields["wall_thermal_dataset_id"]["choices"]},
                         {d.dataset_id for d in list_wall_datasets()})
        self.assertTrue(fields["wall_material_zones_json"]["advanced"])
        self.assertEqual(fields["wall_material_zones_json"]["role"], "design_input")
        self.assertEqual({item["key"] for item in group["configuration_context"]},
                         {"chamber_inner_wall_material", "chamber_outer_wall_material"})
        for field in fields.values():
            self.assertEqual(field["visibility"], {"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
        self.assertNotIn("CuCrZr", " ".join(c["label"] for c in fields["wall_thermal_dataset_id"]["choices"]))

    def test_grcop42_source_metadata_survives_real_editor_rendering(self):
        from lre_workbench.materials.wall import GRCOP42_DATASET_ID, get_wall_dataset
        group = next(g for g in self.schema["system_groups"] if g["id"] == "wall_thermal")
        defaults = self.schema["defaults"]
        self.assertEqual(defaults["wall_thermal_dataset_id"], GRCOP42_DATASET_ID)
        self.assertEqual(defaults["wall_manufacturing_state"], "unspecified")
        dataset = get_wall_dataset(GRCOP42_DATASET_ID).metadata()
        self.assertEqual(dataset["heat_capacity_property"]["statistic"], "composition_weighted_estimate")
        self.assertIsNone(dataset["reference_density_kg_m3"])
        # Exercise the real Python catalog -> JSON -> browser renderer boundary.
        # The evidence must be visible without turning a literature choice into
        # an assertion that the user's hardware manufacturing process matches.
        script = """
import fs from 'node:fs';
import {renderParameterGroups} from './webapp/static/parameter-editor.js';
const {group, defaults} = JSON.parse(fs.readFileSync(0, 'utf8'));
const original = JSON.stringify(defaults);
process.stdout.write(renderParameterGroups([group], defaults));
if (JSON.stringify(defaults) !== original) throw Error('Renderer mutated saved inputs');
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                                input=json.dumps({"group": group, "defaults": defaults}),
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("推力室内衬材料与热物性", "298.15–973.15 K", "方案制造状态未确认",
                         "10.1016/j.mtcomm.2023.106665", "成分加权估算", "非该合金的独立实测比热",
                         "未登记参考密度", "不扩展数据有效温区", "equation (3)"):
            self.assertIn(expected, result.stdout)
        self.assertIn(f'value="{GRCOP42_DATASET_ID}" selected', result.stdout)


if __name__ == "__main__":
    unittest.main()
