"""New liner choice must never reinterpret an existing design snapshot."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest

from webapp.solver import input_values, ffsc_input_values
from lre_workbench.domain.models import EngineInputs
from lre_workbench.application.presets import preset_input_values
from lre_workbench.properties.datasets import saved_input_identity
from lre_workbench.materials.wall import GRCOP84_DATASET_ID, get_wall_dataset
from lre_workbench.analysis.thermal_structural import _liner_structural_scope


GRCOP42 = "grcop42_chen2023_lpbf_v1"


class WallMaterialDefaultTests(unittest.TestCase):
    def test_new_design_and_ffsc_example_select_42_without_claiming_manufacture(self):
        for values in (asdict(EngineInputs()), input_values({})[0], preset_input_values("raptor3_ffsc")):
            self.assertEqual(values["chamber_inner_wall_material"], "grcop42")
            self.assertEqual(values["wall_thermal_dataset_id"], GRCOP42)
            self.assertEqual(values["wall_manufacturing_state"], "unspecified")

    def test_old_missing_identity_loads_84_but_explicit_42_is_respected(self):
        old = {"pc": 32e6}
        before = deepcopy(old)
        for values in (asdict(EngineInputs.from_dict(old)), input_values(old, saved_snapshot=True)[0]):
            self.assertEqual(values["chamber_inner_wall_material"], "grcop84")
            self.assertEqual(values["wall_thermal_dataset_id"], GRCOP84_DATASET_ID)
        self.assertEqual(old, before)
        self.assertEqual(saved_input_identity(old)["wall_thermal_dataset_id"], "")
        explicit = {**old, "chamber_inner_wall_material": "grcop42"}
        for values in (asdict(EngineInputs.from_dict(explicit)), input_values(explicit, saved_snapshot=True)[0]):
            self.assertEqual(values["wall_thermal_dataset_id"], GRCOP42)

    def test_old_material_assignment_and_zones_survive_roundtrip(self):
        zones = json.dumps([{"x_start_m": .1, "x_end_m": .2, "dataset_id": GRCOP84_DATASET_ID,
                             "manufacturing_state": "powder_metallurgy_extruded_high_fe"}])
        old = asdict(EngineInputs(chamber_inner_wall_material="grcop84", wall_material_zones_json=zones,
                                 wall_manufacturing_state="powder_metallurgy_extruded_high_fe"))
        before = deepcopy(old)
        normalized, _ = input_values(old, saved_snapshot=True)
        self.assertEqual(normalized, ffsc_input_values(old))
        self.assertEqual(old, before)
        for key in ("chamber_inner_wall_material", "wall_thermal_dataset_id",
                    "wall_material_zones_json", "wall_manufacturing_state"):
            self.assertEqual(normalized[key], old[key])
        modern = asdict(EngineInputs())
        self.assertEqual(input_values(modern, saved_snapshot=True)[0], ffsc_input_values(modern))

    def test_historical_gg_examples_retain_the_previous_liner(self):
        for name in ("tq12a_gg", "merlin1d_gg"):
            values = preset_input_values(name)
            self.assertEqual(values["chamber_inner_wall_material"], "grcop84")
            self.assertEqual(values["wall_thermal_dataset_id"], GRCOP84_DATASET_ID)

    def test_new_thermal_dataset_does_not_activate_legacy_strength_approximations(self):
        dataset = get_wall_dataset(GRCOP42).metadata()
        profile = {"material_applicability": {"datasets": [dataset]}, "cells": [{"kind":"channel",
            "material": {"dataset_id":GRCOP42, "material_id":"grcop42",
                         "manufacturing_state_id":dataset["manufacturing_state"]["state_id"],
                         "manufacturing_match":"matched", "status":"within_range"}}]}
        self.assertEqual(_liner_structural_scope(profile, "grcop42")[0], "not_assessed")


if __name__ == "__main__":
    unittest.main()
