"""Cooling options retain legacy physics and use explicit local references."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.domain.analysis_contract import concept_design_task, concept_inputs
from lre_workbench.components.cooling.model import CoolingChannel
from lre_workbench.components.cooling.marching import march_regen_circuit
from lre_workbench.domain.models import EngineInputs, ValidationError
from lre_workbench.systems.regenerative import cooling_arguments
from tests.test_cooling_sizing import BASE as COMPONENT_BASE
from tests.test_regen_circuit import BASE as MARCH_BASE


NEW_FIELDS = {"cooling_channel_roughness_m", "cooling_k_minor_inlet",
              "cooling_k_minor_outlet", "cooling_htc_enhancement_basis"}


class CoolingInputValidationTests(unittest.TestCase):
    def test_legacy_defaults_and_explicit_zero_are_distinct(self):
        old = EngineInputs.from_dict({})
        self.assertIsNone(old.cooling_channel_roughness_m)
        self.assertIsNone(old.cooling_k_minor_inlet)
        self.assertIsNone(old.cooling_k_minor_outlet)
        new = EngineInputs(cooling_channel_roughness_m=0., cooling_k_minor_inlet=0.,
                           cooling_k_minor_outlet=0., cooling_htc_enhancement=1.2)
        self.assertEqual(new.cooling_channel_roughness_m, 0.)
        self.assertEqual(new.cooling_htc_enhancement_basis, "")

    def test_optional_coefficients_require_finite_nonnegative_numbers(self):
        for field in NEW_FIELDS - {"cooling_htc_enhancement_basis"}:
            for value in (-1., math.nan, math.inf, True, "0"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    EngineInputs(**{field: value})
        for field in ("cooling_k_minor_inlet", "cooling_k_minor_outlet"):
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "同时"):
                EngineInputs(**{field: 0.})

    def test_nonempty_table_is_structurally_validated_at_input_boundary(self):
        bad_tables = ["[]", "{}", "null", '[null]',
            json.dumps({"rows": [{"w_mm": float("nan") }]}),
            json.dumps({"rows": [{"h_mm": None}]}),
            json.dumps({"rows": [{"n": 300}, {"n": 301}]})]
        for table in bad_tables:
            with self.subTest(table=table), self.assertRaises(ValidationError):
                EngineInputs(cooling_channel_table_json=table)
        # Historically sparse rows have established defaults; preserve them.
        EngineInputs(cooling_channel_table_json='{"rows":[{"anchor":"throat"}]}')

    def test_saved_task_without_exact_additive_group_replays_without_mutation(self):
        values = asdict(EngineInputs())
        task = concept_design_task(values)
        for key in NEW_FIELDS:
            del task["model_configuration"][key]
        before = deepcopy(task)
        self.assertEqual(concept_inputs(task), {k: v for k, v in values.items() if k not in NEW_FIELDS})
        self.assertEqual(task, before)
        # A partial new group is not an old schema and must still be rejected.
        task["model_configuration"]["cooling_channel_roughness_m"] = None
        with self.assertRaises(ValueError):
            concept_inputs(task)

    def test_cycle_adapter_keeps_options_on_frozen_input_copy(self):
        values = EngineInputs(cooling_channel_roughness_m=4e-6,
            cooling_k_minor_inlet=0., cooling_k_minor_outlet=2.4,
            cooling_htc_enhancement_basis="bench case 12")
        frozen = deepcopy(values)
        frozen.cooling_channel_auto_size = 0.
        actual = cooling_arguments(SimpleNamespace(inputs=frozen))
        self.assertEqual(actual["channel_roughness_m"], 4e-6)
        self.assertEqual(actual["k_minor_inlet"], 0.)
        self.assertEqual(actual["k_minor_outlet"], 2.4)
        self.assertEqual(actual["htc_enhancement_basis"], "bench case 12")


class CoolingHydraulicPhysicsTests(unittest.TestCase):
    def march(self, **changes):
        return march_regen_circuit(**(MARCH_BASE | {"n_points": 40} | changes))

    def test_legacy_relative_and_total_loss_values_are_numerically_unchanged(self):
        legacy = self.march()
        explicit = self.march(channel_roughness_m=None, k_minor_inlet=1.5, k_minor_outlet=1.5)
        self.assertEqual(legacy["summary"], explicit["summary"])
        self.assertEqual(legacy["profile"]["nodes"], explicit["profile"]["nodes"])
        hydraulics = legacy["profile"]["hydraulic_parameters"]
        self.assertEqual(hydraulics["roughness_mode"], "relative")
        self.assertEqual(hydraulics["minor_loss_source"], "legacy_total_split")
        self.assertEqual(hydraulics["relative_roughness_min"], .003)
        self.assertEqual(hydraulics["relative_roughness_max"], .003)

    def test_absolute_roughness_uses_every_local_hydraulic_diameter(self):
        result = self.march(channel_roughness_m=4e-6)
        profile = result["profile"]
        cells = [c for c in profile["cells"] if c["kind"] == "channel"]
        self.assertGreater(len({c["relative_roughness"] for c in cells}), 1)
        for cell in cells:
            self.assertEqual(cell["equivalent_roughness_m"], 4e-6)
            self.assertEqual(cell["relative_roughness"], 4e-6/cell["D_h_m"])
        self.assertEqual(profile["hydraulic_parameters"]["roughness_mode"], "absolute")
        with self.assertRaisesRegex(ValueError, "smaller than every"):
            self.march(channel_roughness_m=.02)

    def test_zero_and_distinct_end_losses_use_adjacent_channel_dynamic_pressure(self):
        profile = self.march(k_minor_inlet=0., k_minor_outlet=2.4)["profile"]
        first, last = profile["cells"][0], profile["cells"][-1]
        self.assertEqual(first["local_loss_K"], 0.)
        self.assertEqual(first["dp_local_Pa"], 0.)
        self.assertEqual(last["local_loss_K"], 2.4)
        adjacent = profile["nodes"][-2]
        self.assertEqual(last["dp_local_Pa"], 2.4*.5*adjacent["rho_kgm3"]*adjacent["velocity_ms"]**2)
        self.assertEqual(profile["hydraulic_parameters"]["minor_loss_source"], "separate")

    def test_absolute_zero_means_smooth_and_basis_does_not_change_physics(self):
        smooth = self.march(channel_roughness_m=0., htc_enhancement=1.1)
        note = self.march(channel_roughness_m=0., htc_enhancement=1.1,
                          htc_enhancement_basis="  test report A  ")
        self.assertEqual(smooth["summary"], note["summary"])
        self.assertEqual(smooth["profile"]["hydraulic_parameters"]["relative_roughness_max"], 0.)
        self.assertEqual(note["profile"]["heat_transfer_correction"]["basis"], "test report A")

    def test_inlet_velocity_sizing_uses_the_overridden_inlet_loss(self):
        for kin in (0., .8):
            with self.subTest(kin=kin):
                _, component = CoolingChannel(**COMPONENT_BASE,
                    sizing_reference="coolant_inlet_velocity", target_velocity_ms=20.,
                    k_minor_inlet=kin, k_minor_outlet=2.4, channel_roughness_m=4e-6).evaluate()
                profile = component.profile
                self.assertEqual(profile["channel_sizing"]["status"], "converged_and_march_verified")
                inlet = profile["nodes"][1]
                self.assertAlmostEqual(inlet["velocity_ms"], 20., delta=1e-4)
                self.assertAlmostEqual(COMPONENT_BASE["p_inlet"]-inlet["p_Pa"],
                    (1.+kin)*.5*inlet["rho_kgm3"]*inlet["velocity_ms"]**2, delta=.5)


if __name__ == "__main__":
    unittest.main()
