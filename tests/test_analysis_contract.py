"""Task roles are explicit, lossless and compatible with saved version 1 jobs."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lre_workbench.domain.analysis_contract import GROUPS, concept_design_task, concept_inputs, require_concept_mode
from lre_workbench.domain.models import EngineInputs


def legacy_task(values):
    """Independent fixture for the previously persisted schema, not a migration."""
    targets = {
        "thrust", "pc", "of_ratio", "inlet_pressure",
        "use_target_preburner_temp", "use_target_preburner_mr",
        "ox_preburner_pressure_ratio", "fuel_preburner_pressure_ratio",
        "ox_preburner_temp_target", "fuel_preburner_temp_target",
        "ox_preburner_mr_target", "fuel_preburner_mr_target",
        "use_target_gg_temp", "gg_temp_target", "gg_mr_target",
    }
    boundaries = {"ambient_pressure", "tank_pressure_pa", "fuel_inlet_temp", "ox_inlet_temp"}
    initials = {"target_isp", "chamber_temp_guess", "chamber_gamma", "molar_mass",
                "fuel_split_to_ox_preburner", "ox_split_to_ox_preburner", "gg_fraction_guess"}
    return {"schema_version": 1, "mode": "concept_design",
            "design_targets": {k: v for k, v in values.items() if k in targets},
            "boundaries": {k: v for k, v in values.items() if k in boundaries},
            "initial_guess": {k: v for k, v in values.items() if k in initials},
            "model_configuration": {k: v for k, v in values.items()
                                    if k not in targets | boundaries | initials}}


class AnalysisContractTests(unittest.TestCase):
    def test_complete_roundtrip_has_no_shared_or_missing_fields(self):
        for cycle in ("ffsc", "gg"):
            with self.subTest(cycle=cycle):
                values = asdict(EngineInputs(cycle_type=cycle))
                task = concept_design_task(values)
                self.assertEqual(task["schema_version"], 2)
                self.assertEqual(concept_inputs(task), values)
                groups = [task[name] for name in GROUPS]
                self.assertEqual(sum(map(len, groups)), len(values))
                self.assertEqual(set().union(*groups), set(values))
                self.assertEqual(task["design_targets"]["thrust"], values["thrust"])
                self.assertIn("target_isp", task["initial_guess"])
                self.assertIn("nonlinear_solver", task["solver_settings"])
                self.assertIn("ambient_pressure", task["boundaries"])
                restored = concept_inputs(task)
                restored["pc"] = 1
                task["design_targets"]["thrust"] = 2
                self.assertNotEqual(values["pc"], restored["pc"])
                self.assertNotEqual(values["thrust"], task["design_targets"]["thrust"])

    def test_temperature_mode_records_temperature_targets_and_mr_initials(self):
        values = asdict(EngineInputs())
        task = concept_design_task(values)
        for side in ("ox", "fuel"):
            self.assertIn(f"{side}_preburner_temp_target", task["design_targets"])
            self.assertIn(f"{side}_preburner_mr_target", task["initial_guess"])
            self.assertIn(f"{side}_preburner_pressure_ratio", task["initial_guess"])
        for key in ("use_target_preburner_temp", "use_target_preburner_mr", "use_target_gg_temp"):
            self.assertIn(key, task["model_configuration"])
        self.assertEqual(concept_inputs(task), values)

    def test_mr_and_legacy_modes_keep_their_effective_roles(self):
        values = asdict(EngineInputs(use_target_preburner_temp=0.0, use_target_preburner_mr=1.0))
        task = concept_design_task(values)
        for side in ("ox", "fuel"):
            self.assertIn(f"{side}_preburner_mr_target", task["design_targets"])
            self.assertIn(f"{side}_preburner_temp_target", task["model_configuration"])
            self.assertIn(f"{side}_preburner_pressure_ratio", task["initial_guess"])
        self.assertEqual(concept_inputs(task), values)
        values["use_target_preburner_mr"] = 0.0
        task = concept_design_task(values)
        for side in ("ox", "fuel"):
            self.assertIn(f"{side}_preburner_pressure_ratio", task["design_targets"])
            self.assertIn(f"{side}_preburner_mr_target", task["model_configuration"])
        for key in ("fuel_split_to_ox_preburner", "ox_split_to_ox_preburner"):
            self.assertIn(key, task["initial_guess"])
        self.assertEqual(concept_inputs(task), values)

    def test_gg_mixture_ratio_changes_from_initial_to_target(self):
        values = asdict(EngineInputs(cycle_type="gg"))
        task = concept_design_task(values)
        self.assertIn("gg_mr_target", task["initial_guess"])
        self.assertIn("gg_fraction_guess", task["initial_guess"])
        self.assertIn("gg_temp_target", task["design_targets"])
        values["use_target_gg_temp"] = 0.0
        task = concept_design_task(values)
        self.assertIn("gg_mr_target", task["design_targets"])
        self.assertIn("gg_temp_target", task["model_configuration"])
        self.assertEqual(concept_inputs(task), values)

    def test_design_choices_constraints_and_budgets_are_not_initials(self):
        task = concept_design_task(asdict(EngineInputs()))
        for key in ("lox_pump_speed_rpm_override", "fuel_pump_speed_rpm_override",
                    "turb_ox_u_over_c_target", "expansion_ratio", "cooling_channel_auto_size",
                    "cooling_channel_width_m", "nozzle_contour_method"):
            with self.subTest(key=key):
                self.assertIn(key, task["design_choices"])
                self.assertNotIn(key, task["initial_guess"])
        for key in ("ox_preburner_temp_limit", "fuel_preburner_temp_limit",
                    "ox_preburner_temp_limit_status", "cooling_wall_temp_limit_K"):
            self.assertIn(key, task["constraints"])
        for key in ("feed_line_dp_pa", "valve_line_dp_ratio", "preburner_injector_dp_ratio"):
            self.assertIn(key, task["model_configuration"])

    def test_version_1_replay_preserves_the_original_partition_and_values(self):
        for flags in ((1.0, 1.0), (0.0, 1.0), (0.0, 0.0)):
            values = asdict(EngineInputs(use_target_preburner_temp=flags[0], use_target_preburner_mr=flags[1]))
            task = legacy_task(values)
            before = deepcopy(task)
            self.assertEqual(concept_inputs(task), values)
            self.assertEqual(task, before)
            self.assertIn("ox_preburner_mr_target", task["design_targets"])
            self.assertIn("cooling_channel_auto_size", task["model_configuration"])
            # Old records cannot silently acquire a current grouping just by
            # changing their version number or moving an individual parameter.
            with self.assertRaises(ValueError):
                concept_inputs(task | {"schema_version": 2})
            task["initial_guess"]["ox_preburner_mr_target"] = task["design_targets"].pop("ox_preburner_mr_target")
            with self.assertRaises(ValueError):
                concept_inputs(task)

    def test_wrong_modes_and_malformed_contracts_fail_before_adaptation(self):
        for mode in (None, "fixed_hardware_steady", "fixed_hardware_transient", "design", True):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                require_concept_mode(mode)
        require_concept_mode("concept_design")
        original = concept_design_task(asdict(EngineInputs()))
        mutations = (
            lambda t: t.update(mode="fixed_hardware_steady"),
            lambda t: t.update(schema_version=True),
            lambda t: t.update(schema_version=3),
            lambda t: t.update(hardware={}),
            lambda t: t["design_targets"].pop("ox_preburner_temp_target"),
            lambda t: t["initial_guess"].update(pc=t["design_targets"]["pc"]),
            lambda t: t["initial_guess"].update(pc=t["design_targets"].pop("pc")),
            lambda t: t["model_configuration"].update(unknown=1),
            lambda t: t.update(constraints=[]),
            lambda t: t["model_configuration"].update(use_target_preburner_temp=0.0),
        )
        for mutate in mutations:
            task = deepcopy(original)
            mutate(task)
            with self.subTest(task=task), self.assertRaises(ValueError):
                concept_inputs(task)
        with self.assertRaises(ValueError):
            concept_inputs(None)

    def test_turbine_role_clarification_preserves_complete_older_v2_records(self):
        for cycle in ("ffsc", "gg"):
            values = asdict(EngineInputs(cycle_type=cycle, turb_ox_u_max_ms=391.0,
                                        turb_fuel_u_max_ms=467.0))
            current = concept_design_task(values)
            legacy = deepcopy(current)
            for side in ("ox", "fuel"):
                limit, basis = f"turb_{side}_u_max_ms", f"{side}_turbine_geometry_state_basis"
                self.assertEqual(current["constraints"][limit], values[limit])
                self.assertEqual(current["model_configuration"][basis], values[basis])
                legacy["design_choices"][limit] = legacy["constraints"].pop(limit)
                legacy["design_choices"][basis] = legacy["model_configuration"].pop(basis)
            before = deepcopy(legacy)
            self.assertEqual(concept_inputs(legacy), values)
            self.assertEqual(legacy, before)
            self.assertEqual(concept_inputs(current), values)
            # Compatibility does not allow partial migration, duplicate values,
            # missing keys, or unrelated parameters in the wrong group.
            malformed = []
            partial = deepcopy(current)
            partial["design_choices"]["turb_ox_u_max_ms"] = partial["constraints"].pop("turb_ox_u_max_ms")
            malformed.append(partial)
            duplicate = deepcopy(legacy)
            duplicate["constraints"]["turb_ox_u_max_ms"] = values["turb_ox_u_max_ms"]
            malformed.append(duplicate)
            missing = deepcopy(legacy)
            missing["design_choices"].pop("turb_fuel_u_max_ms")
            malformed.append(missing)
            unrelated = deepcopy(legacy)
            unrelated["design_choices"]["pc"] = unrelated["design_targets"].pop("pc")
            malformed.append(unrelated)
            for task in malformed:
                with self.subTest(cycle=cycle, task=task), self.assertRaises(ValueError):
                    concept_inputs(task)

    def test_source_must_be_complete_normalized_engine_inputs(self):
        for values in ({}, None, asdict(EngineInputs()) | {"mode": "fixed_hardware_steady"}):
            with self.assertRaises(ValueError):
                concept_design_task(values)


if __name__ == "__main__":
    unittest.main()
