"""Desktop/browser presentation parity and SI serialization regressions."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import unittest

from webapp.solver import EngineInputs, input_values, presets
from core import ValidationError
from webapp.ui_schema import design_ui_schema
from ui_metadata import DISPLAY_UNIT_MAP, PARAM_GROUPS, CHAMBER_INPUTS
from materials import MATERIALS

ROOT = Path(__file__).resolve().parent.parent


class UiSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = design_ui_schema()
        self.fields = {field["key"]: field for group in self.schema["groups"]
                       for field in group["fields"]}

    def test_every_input_has_one_field_and_si_default(self):
        defaults = asdict(EngineInputs())
        all_fields = [field for group in self.schema["groups"] for field in group["fields"]]
        self.assertEqual(len(all_fields), len(self.fields))
        self.assertEqual(set(self.fields), set(defaults))
        self.assertEqual(self.schema["defaults"], defaults)
        self.assertEqual({key: field["default"] for key, field in self.fields.items()}, defaults)
        json.dumps(self.schema, allow_nan=False)

    def test_labels_units_and_numeric_types_follow_desktop(self):
        for title, items in PARAM_GROUPS:
            for key, label, unit in items:
                with self.subTest(key=key):
                    field = self.fields[key]
                    self.assertEqual(field["full_label"], label)
                    self.assertEqual((field["factor"], field["unit"]),
                                     DISPLAY_UNIT_MAP.get(key, (1, unit)))
        for key, (label, unit, tooltip) in CHAMBER_INPUTS.items():
            self.assertEqual(self.fields[key]["full_label"], label)
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
                if field["type"] in ("number", "int"):
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
        self.assertEqual(groups["cycle"]["cycles"], ["ffsc", "gg"])
        self.assertEqual(self.fields["cycle_type"]["cycles"], ["ffsc", "gg"])
        self.assertEqual(self.fields["propellant_pair"]["type"], "select")
        self.assertEqual({c["value"] for c in self.fields["propellant_pair"]["choices"]},
                         {"lox_methane", "lox_kerosene"})
        models = {m["id"]: m for m in self.schema["propellant_models"]}
        self.assertEqual(models["lox_kerosene"]["supported_cycles"], ("gg",))
        self.assertEqual(models["lox_kerosene"]["fuel_species"], "c12h26")
        self.assertEqual(self.fields["gg_layout"]["cycles"], ["gg"])
        self.assertEqual(groups["gas_generator"]["cycles"], ["gg"])
        self.assertEqual(groups["pressure_ffsc"]["cycles"], ["ffsc"])
        for key, field in self.fields.items():
            if field["type"] != "select":
                continue
            values = [choice["value"] for choice in field["choices"]]
            self.assertIn(field["default"], values)
            if key.startswith("turb_ox_") or key == "lox_pump_impeller_material":
                self.assertTrue(all(MATERIALS[value].oxygen_compatible for value in values))

    def test_server_schema_import_does_not_load_qt_or_desktop(self):
        code = (
            "from webapp.ui_schema import design_ui_schema; import sys; "
            "design_ui_schema(); "
            "assert 'gui_pyside6' not in sys.modules; "
            "assert not any(n.startswith(('PySide6', 'matplotlib')) for n in sys.modules)"
        )
        process = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_system_editor_keeps_cycle_boundaries_and_preserves_compatibility(self):
        groups = self.schema["system_groups"]
        visible = {f["key"] for g in groups for f in g["fields"]}
        self.assertTrue({"pc", "thrust", "cooling_dp_pa", "cooling_heat_load",
                         "cooling_bartz_mode", "cooling_grid_points", "gg_mech_efficiency"} <= visible)
        self.assertFalse({"cooling_marching_enable", "chamber_wall_allow_pa",
                          "cooling_dp_auto", "coolant_cp", "turb_ox_rotor_material",
                          "throttle_steps"} & visible)
        self.assertFalse({"chamber_structure", "turbines",
                          "turbine_materials"} & {g["id"] for g in groups})
        self.assertTrue({"chamber_contour", "chamber_channels"} <= {g["id"] for g in groups})
        # Restricting the editor must not discard old assumptions on a save.
        self.assertEqual(set(self.fields), set(asdict(EngineInputs())))
        cooling = next(g for g in groups if g["id"] == "cooling")
        mode = next(f for f in cooling["fields"] if f["key"] == "cooling_bartz_mode")
        self.assertEqual([c["value"] for c in mode["choices"]], [1, 0])
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
                self.assertEqual({f["key"] for f in contour["fields"]}, geometry_keys)
                for key in geometry_keys:
                    owners = [g["id"] for g in groups.values()
                              for field in g["fields"] if field["key"] == key]
                    self.assertEqual(owners, ["chamber_contour"], key)
                # The GG exhaust nozzle is independent hardware, not another
                # editor for the main nozzle's expansion ratio.
                exhaust_owners = [g["id"] for g in groups.values()
                                  for f in g["fields"] if f["key"] == "gg_exhaust_expansion_ratio"]
                self.assertEqual(exhaust_owners, ["gas_generator"])
        all_system_keys = [f["key"] for g in self.schema["system_groups"] for f in g["fields"]]
        self.assertEqual(len(all_system_keys), len(set(all_system_keys)))

    def test_solver_selection_roundtrip_and_validation(self):
        self.assertEqual(self.fields["nonlinear_solver"]["type"], "select")
        solver = next(g for g in self.schema["system_groups"] if g["id"] == "solver")
        self.assertEqual([f["key"] for f in solver["fields"]], ["nonlinear_solver", "cooling_grid_points"])
        self.assertEqual(input_values({})[0]["nonlinear_solver"], "newton")
        self.assertEqual(input_values({"nonlinear_solver": "trf"})[0]["nonlinear_solver"], "trf")
        with self.assertRaisesRegex(ValidationError, "nonlinear_solver"):
            input_values({"nonlinear_solver": "silently_fallback"})

    def test_preburner_design_has_one_home_and_explicit_limit_provenance(self):
        groups = {g["id"]: g for g in self.schema["system_groups"]}
        self.assertNotIn("preburners", groups)
        group = groups["preburner_design"]
        self.assertEqual(group["title"], "预燃室设计")
        self.assertEqual([section["id"] for section in group["sections"]], ["targets", "limits"])
        keys = [f["key"] for f in group["fields"]]
        self.assertEqual(len(keys), len(set(keys)))
        for side in ("ox", "fuel"):
            for suffix in ("temp_target", "mr_target", "pressure_ratio", "temp_limit", "temp_limit_status", "temp_limit_basis"):
                key = f"{side}_preburner_{suffix}"
                self.assertEqual([g["id"] for g in groups.values() for f in g["fields"] if f["key"] == key], ["preburner_design"])
            state = self.fields[f"{side}_preburner_temp_limit_status"]
            self.assertEqual([c["value"] for c in state["choices"]], ["unset", "provisional", "documented"])
            self.assertEqual(state["default"], "provisional")
            self.assertTrue(self.fields[f"{side}_preburner_temp_limit_basis"]["multiline"])
            for suffix in ("", "_basis"):
                visible = self.fields[f"{side}_preburner_temp_limit{suffix}"]["visibility"]["any"]
                self.assertEqual({c["value"] for c in visible}, {"provisional", "documented"})
        # The complete storage schema retains desktop group IDs and all fields.
        self.assertIn("preburners", {g["id"] for g in self.schema["groups"]})

    def test_wall_thermal_editor_exposes_only_source_supported_equation_data(self):
        from wall_materials import list_wall_datasets
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


if __name__ == "__main__":
    unittest.main()
