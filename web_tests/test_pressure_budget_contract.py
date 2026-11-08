"""Pressure workspace input compatibility, units and validation contracts."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest

from webapp.solver import EngineInputs, design_readiness, input_values
from webapp.ui_schema import design_ui_schema
from lre_workbench.systems.pressure_budget import resolve_branch_pressure_budgets


BRANCHES = ("fuel_to_opb", "fuel_to_fpb", "ox_to_opb", "ox_to_fpb")
BRANCH_FIELDS = tuple(f"{branch}_{kind}_dp_ratio" for branch in BRANCHES
                      for kind in ("line", "injector"))
LEGACY_FIELDS = ("preburner_injector_dp_ratio", "valve_line_dp_ratio",
                 "ox_turbine_exhaust_dp_ratio", "fuel_turbine_exhaust_dp_ratio")


def cycle_fields(schema, cycle):
    return [(group["id"], field) for group in schema["system_groups"]
            if cycle in group.get("cycles", ("ffsc",))
            for field in group["fields"] if cycle in field.get("cycles", ("ffsc",))]


class PressureBudgetContractTests(unittest.TestCase):
    def test_full_flow_pressure_inputs_share_one_workspace_without_duplicate_editors(self):
        schema = design_ui_schema()
        # Scope and field removal is versioned so stale reviews are rechecked.
        self.assertEqual(schema["version"], 18)
        group = next(group for group in schema["system_groups"] if group["id"] == "pressure_ffsc")
        self.assertEqual(group["title"], "压力分配与压降预算")
        self.assertTrue(group["pressure_workspace"])
        self.assertEqual({field["key"] for field in group["fields"]}, set(BRANCH_FIELDS + LEGACY_FIELDS))
        self.assertNotIn("pressure", {owner for owner, _ in cycle_fields(schema, "ffsc")})
        for cycle in ("ffsc",):
            fields = cycle_fields(schema, cycle)
            keys = [field["key"] for _, field in fields]
            self.assertEqual(len(keys), len(set(keys)), cycle)
            expected_owner = "pressure_ffsc"
            self.assertEqual([owner for owner, field in fields if field["key"] == "valve_line_dp_ratio"], [expected_owner])
        self.assertFalse(set(BRANCH_FIELDS) & {field["key"] for _, field in cycle_fields(schema, "gg")})

    def test_percentage_editor_roundtrip_preserves_dimensionless_api_values(self):
        schema = design_ui_schema()
        group = next(group for group in schema["system_groups"] if group["id"] == "pressure_ffsc")
        values = input_values({})[0]
        values.update({key: 0.04 if "_line_" in key else 0.15 for key in BRANCH_FIELDS})
        original = deepcopy(values)
        for field in group["fields"]:
            self.assertEqual((field["factor"], field["unit"]), (100, "%"))
            self.assertEqual(field["type"], "number")
            key = field["key"]
            shown = values[key] * field["factor"]
            if key == "preburner_injector_dp_ratio":
                self.assertEqual(shown, 12)
            elif key == "valve_line_dp_ratio":
                self.assertEqual(shown, 3)
            values[key] = shown / field["factor"]
        normalized, _ = input_values(json.loads(json.dumps(values, allow_nan=False)))
        for key, expected in original.items():
            with self.subTest(key=key):
                if isinstance(expected, (float, int)):
                    self.assertAlmostEqual(normalized[key], expected)
                else:
                    self.assertEqual(normalized[key], expected)

    def test_old_json_omitting_overrides_keeps_existing_budgets_and_null_inheritance(self):
        legacy = input_values({})[0]
        for key in BRANCH_FIELDS:
            del legacy[key]
        legacy.update(valve_line_dp_ratio=0.045, preburner_injector_dp_ratio=0.14)
        before = deepcopy(legacy)
        normalized, _ = input_values(json.loads(json.dumps(legacy)), saved_snapshot=True)
        self.assertEqual(legacy, before)
        self.assertEqual({key: normalized[key] for key in legacy}, legacy)
        self.assertTrue(all(normalized[key] is None for key in BRANCH_FIELDS))
        effective = resolve_branch_pressure_budgets(normalized)
        for branch in BRANCHES:
            self.assertEqual(effective[branch]["line_dp_ratio"], 0.045)
            self.assertEqual(effective[branch]["injector_dp_ratio"], 0.14)
            self.assertEqual(effective[branch]["line_source"], "valve_line_dp_ratio")
            self.assertEqual(effective[branch]["injector_source"], "preburner_injector_dp_ratio")
        reopened, _ = input_values(json.loads(json.dumps(normalized)))
        self.assertEqual(reopened, normalized)

    def test_null_restores_inheritance_and_zero_is_a_valid_explicit_line_budget(self):
        values = input_values({})[0]
        values["fuel_to_opb_line_dp_ratio"] = 0
        values["ox_to_fpb_injector_dp_ratio"] = 0.18
        normalized, _ = input_values(values)
        self.assertEqual(normalized["fuel_to_opb_line_dp_ratio"], 0)
        self.assertEqual(normalized["ox_to_fpb_injector_dp_ratio"], 0.18)
        effective = resolve_branch_pressure_budgets(normalized)
        self.assertEqual(effective["fuel_to_opb"]["line_dp_ratio"], 0)
        self.assertEqual(effective["fuel_to_opb"]["line_source"], "fuel_to_opb_line_dp_ratio")
        self.assertEqual(effective["ox_to_fpb"]["injector_source"], "ox_to_fpb_injector_dp_ratio")
        normalized["fuel_to_opb_line_dp_ratio"] = None
        reopened, _ = input_values(normalized)
        self.assertIsNone(reopened["fuel_to_opb_line_dp_ratio"])
        self.assertEqual(resolve_branch_pressure_budgets(reopened)["fuel_to_opb"]["line_source"], "valve_line_dp_ratio")

    def test_all_optional_budgets_reject_text_boolean_and_nonfinite_inputs(self):
        for key in BRANCH_FIELDS:
            for value in ("0.05", "", "invalid", True, False, [], {}, float("nan"), float("inf"), -float("inf")):
                with self.subTest(key=key, value=value):
                    with self.assertRaisesRegex(ValueError, key):
                        input_values({key: value})

    def test_invalid_branch_budget_is_located_by_calculation_readiness(self):
        for key in BRANCH_FIELDS:
            with self.subTest(key=key):
                report = design_readiness(asdict(EngineInputs()) | {key: "invalid"})
                self.assertFalse(report["valid"])
                self.assertTrue(any(key in issue["fields"] for issue in report["issues"]), report)


if __name__ == "__main__":
    unittest.main()
