"""Material identity must agree before a new cooling calculation can run."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.domain.models import EngineInputs, ValidationError
from lre_workbench.materials.wall import (GRCOP42_DATASET_ID, GRCOP84_DATASET_ID,
    CUZR_DATASET_ID, get_wall_dataset, validate_wall_dataset_material)
from lre_workbench.systems.regenerative import cooling_arguments
from lre_workbench.systems.ffsc import FFSCCycleModel


class WallMaterialIdentityTests(unittest.TestCase):
    def test_registered_material_identity_cannot_be_relabelled(self):
        for alloy, dataset in (("grcop42", GRCOP42_DATASET_ID),
                               ("grcop84", GRCOP84_DATASET_ID), ("cuzr", CUZR_DATASET_ID)):
            with self.subTest(alloy=alloy):
                original = deepcopy(get_wall_dataset(dataset).metadata())
                self.assertEqual(validate_wall_dataset_material(alloy, dataset).material_id, alloy)
                with self.assertRaisesRegex(ValueError, "内衬材料与默认热物性数据集不一致"):
                    validate_wall_dataset_material("cucrzr", dataset)
                self.assertEqual(get_wall_dataset(dataset).metadata(), original)

    def test_new_defaults_and_explicit_historical_material_remain_distinct(self):
        current = EngineInputs()
        historical = EngineInputs.from_dict({"chamber_inner_wall_material": "grcop84"})
        self.assertEqual((current.chamber_inner_wall_material, current.wall_thermal_dataset_id),
                         ("grcop42", GRCOP42_DATASET_ID))
        self.assertEqual((historical.chamber_inner_wall_material, historical.wall_thermal_dataset_id),
                         ("grcop84", GRCOP84_DATASET_ID))
        self.assertEqual(current.wall_manufacturing_state, "unspecified")

    def test_conflicting_history_can_open_but_cannot_run_or_change_source(self):
        saved = asdict(EngineInputs(chamber_inner_wall_material="grcop84"))
        saved["wall_thermal_dataset_id"] = GRCOP42_DATASET_ID
        before = deepcopy(saved)
        with self.assertWarnsRegex(UserWarning, "当前草稿可打开编辑"):
            loaded = EngineInputs.from_dict(saved)
        self.assertEqual(asdict(loaded), before)
        with self.assertRaisesRegex(ValidationError, "chamber_inner_wall_material=.*grcop84"):
            loaded.validate_wall_material_binding()
        with self.assertRaisesRegex(ValidationError, "chamber_inner_wall_material=.*grcop84"):
            FFSCCycleModel(loaded)
        self.assertEqual(saved, before)

    def test_draft_identity_warning_does_not_bypass_other_validation(self):
        with self.assertRaises(ValidationError):
            EngineInputs(wall_thermal_dataset_id="missing-dataset")
        with self.assertWarns(UserWarning), self.assertRaises(ValidationError):
            EngineInputs(chamber_inner_wall_material="grcop84",
                         wall_thermal_dataset_id=GRCOP42_DATASET_ID,
                         wall_manufacturing_state="missing-manufacturing-state")

    def test_explicit_axial_material_override_preserves_each_identity(self):
        zone = dict(x_start_m=.1, x_end_m=.2, dataset_id=CUZR_DATASET_ID,
                    manufacturing_state="qi2024_solution_cold_work_aged")
        inputs = EngineInputs(wall_material_zones_json=json.dumps([zone]))
        args = cooling_arguments(SimpleNamespace(inputs=inputs))
        self.assertEqual(args["wall_thermal_dataset_id"], GRCOP42_DATASET_ID)
        self.assertEqual(json.loads(args["wall_material_zones"]), [zone])
        self.assertEqual(inputs.chamber_inner_wall_material, "grcop42")

    def test_mutated_inputs_are_rechecked_at_cooling_equation_boundary(self):
        inputs = EngineInputs()
        inputs.chamber_inner_wall_material = "grcop84"
        before = asdict(inputs)
        with self.assertRaisesRegex(ValidationError, "内衬材料与默认热物性数据集不一致"):
            cooling_arguments(SimpleNamespace(inputs=inputs))
        self.assertEqual(asdict(inputs), before)

    def test_mutated_model_inputs_cannot_bypass_public_run_boundaries(self):
        model = FFSCCycleModel(EngineInputs())
        model.inputs.chamber_inner_wall_material = "grcop84"
        for run in (model.evaluate, model.evaluate_forward):
            with self.assertRaisesRegex(ValidationError, "内衬材料与默认热物性数据集不一致"):
                run()

    def test_budget_mode_does_not_invent_unused_wall_data(self):
        inputs = EngineInputs(cooling_bartz_mode=0., chamber_inner_wall_material="cucrzr")
        self.assertEqual(inputs.wall_thermal_dataset_id, "")
        self.assertEqual(cooling_arguments(SimpleNamespace(inputs=inputs))["wall_thermal_dataset_id"], "")


if __name__ == "__main__":
    unittest.main()
