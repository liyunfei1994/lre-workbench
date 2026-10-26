"""Desktop readers consume one saved thermal solution, including port diagnostics."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import math
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import EngineInputs
import chamber_design as cd
import thermal_structural as ts
from material_spec import compare_chamber_liner_materials


def profile_fixture():
    # Axial array extrema intentionally differ from actual inlet/outlet ports.
    x, r = [0.0, .1, .2, .3], [.15, .12, .1, .13]
    profile = {
        "version": 1, "model_id": "regen_quasi1d_ph_v1", "flow_direction": "counterflow",
        "summary": {"dp_total_Pa": 7e6, "p_out_Pa": 63e6, "T_out_K": 315.,
                    "Q_total_W": 23e6, "Q_rad_fraction": .13},
        "nodes": [{"s_m": 0., "p_Pa": 70e6, "T_K": 120.},
                  {"s_m": .1, "p_Pa": 80e6, "T_K": 330.},
                  {"s_m": .2, "p_Pa": 50e6, "T_K": 310.},
                  {"s_m": .3, "p_Pa": 63e6, "T_K": 315.}],
        "cells": [], "validity": {"status": "review", "warnings": ["test validity warning"]},
        "x_m": x, "r_m": r, "x_throat_m": .2, "t_liner_m": .001,
        "q_W_m2": [20e6, 25e6, 30e6, 25e6],
        "q_conv_W_m2": [18e6, 22e6, 27e6, 22e6], "q_rad_W_m2": [2e6, 3e6, 3e6, 3e6],
        "T_wg_K": [600., 650., 700., 680.], "T_wc_K": [500., 550., 600., 580.],
        "T_co_K": [315., 310., 330., 125.], "p_co_Pa": [63e6, 50e6, 80e6, 68e6],
        "p_gas_Pa": [30e6, 28e6, 18e6, 5e6], "h_g_W_m2K": [4e4]*4,
        "h_c_W_m2K": [2e5]*4, "V_co_ms": [100.]*4, "rho_co": [200.]*4,
        "T_aw_K": [3000.]*4, "w_chan_m": [.001]*4, "h_chan_m": [.004]*4,
        "n_chan": [300.]*4, "t_liner_arr_m": [.001]*4,
    }
    for i in range(4):
        profile["cells"].append({"kind": "channel", "x_m": x[i], "r_m": r[i],
            "h_g_W_m2K": 4e4, "h_c_W_m2K": 2e5, "T_coolant_K": profile["T_co_K"][i],
            "wall_thickness_m": .001, "T_wall_hot_K": profile["T_wg_K"][i],
            "T_wall_cold_K": profile["T_wc_K"][i], "q_W_m2": profile["q_W_m2"][i],
            "hot_side": {"T_aw_K": 3000.}})
    return profile


def result_fixture():
    return SimpleNamespace(inputs=EngineInputs(), summary={"chamber_pressure_Pa": 30e6},
                           components={"cooling_channel": SimpleNamespace(profile=profile_fixture(), outputs={})})


class SavedCoolingViewTests(unittest.TestCase):
    def test_geometry_and_heat_inputs_invalidate_snapshot(self):
        inp = EngineInputs()
        snap = cd.BoundarySnapshot(inputs_fingerprint=asdict(inp))
        self.assertEqual(cd.snapshot_stale_fields(snap, inp), [])
        changes = {"cooling_channel_rel_roughness": .004, "cooling_wall_thickness_m": .002,
                   "cooling_gas_emissivity": .3, "cooling_cover_eps": 8., "film_cooling_fraction": .01}
        for key, value in changes.items():
            with self.subTest(key=key):
                changed = replace(inp, **{key: value})
                self.assertIn(key, cd.snapshot_stale_fields(snap, changed))

    def test_unknown_fingerprint_is_not_current(self):
        self.assertEqual(cd.snapshot_stale_fields(cd.BoundarySnapshot(), EngineInputs()),
                         ["__missing_input_fingerprint__"])

    def test_saved_reader_is_immutable_and_never_recalculates(self):
        result = result_fixture()
        before = deepcopy(result.components["cooling_channel"].profile)
        with patch.object(ts, "compute_chamber_wall_field", side_effect=AssertionError("duplicate solve")):
            saved = cd.marching_preview(result, result.inputs)
        saved["profile"]["nodes"][0]["p_Pa"] = -1
        saved["component"].profile["cells"].clear()
        self.assertEqual(result.components["cooling_channel"].profile, before)

    def test_changed_inputs_require_full_cycle_in_compatibility_entry(self):
        result = result_fixture()
        with self.assertRaisesRegex(ValueError, "完整循环"):
            cd.marching_preview(result, replace(result.inputs, cooling_wall_thickness_m=.002))

    def test_legacy_and_incomplete_profiles_cannot_trigger_fallback(self):
        for profile in ({}, {"x_m": [0, 1]}, {"version": 1, "model_id": "regen_quasi1d_ph_v1"}):
            result = result_fixture()
            result.components["cooling_channel"].profile = profile
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "完整循环"):
                cd.saved_cooling_result(result)

    def test_wall_field_keeps_saved_temperatures_and_actual_port_drop(self):
        profile = profile_fixture()
        wf = ts.wall_field_from_profile(profile, {"chamber_pressure_Pa": 30e6}, {}, domain="full")
        self.assertEqual(wf.T_wg, profile["T_wg_K"])
        self.assertEqual(wf.coolant_dp_Pa, 7e6)
        self.assertNotEqual(wf.coolant_dp_Pa, max(wf.p_coolant)-min(wf.p_coolant))
        self.assertEqual(wf.coolant_out_K, 315.)
        self.assertEqual(wf.q_rad_fraction, .13)
        self.assertIn("test validity warning", wf.notes)
        self.assertNotIn("标定", "".join(wf.notes))

    def test_wall_field_rejects_missing_thermal_arrays(self):
        profile = profile_fixture()
        profile["T_wc_K"] = []
        self.assertIsNone(ts.wall_field_from_profile(profile, {}, {}))

    def test_scorecard_does_not_label_pressure_extrema_as_drop(self):
        result = result_fixture()
        wf = ts.wall_field_from_profile(result.components["cooling_channel"].profile,
                                        result.summary, {}, domain="full")
        row = next(row for row in cd.compute_scorecard(wf, result, result.inputs)
                   if row["criterion"] == "冷却回路压降 Δp")
        self.assertIn("7.0 MPa", row["value"])
        self.assertEqual(row["status"], "na")

    def test_removed_duplicate_solver_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, "完整循环"):
            ts.compute_chamber_wall_field({}, {})

    def test_material_comparison_freezes_thermal_loads_for_every_candidate(self):
        result = result_fixture()
        rows, op = compare_chamber_liner_materials(result)
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(row["T_wg"] == 700. and row["T_wc"] == 600. for row in rows))
        self.assertTrue(all(row["q_MW"] == 30. for row in rows))
        self.assertEqual(op["source"], "saved_cooling_throat_load")
        with self.assertRaisesRegex(ValueError, "完整循环"):
            compare_chamber_liner_materials(result, h_c_throat=1e9)


if __name__ == "__main__":
    unittest.main()
