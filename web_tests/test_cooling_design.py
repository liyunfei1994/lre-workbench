"""Bounded channel screening keeps source physics and uncertainty explicit."""
from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
import math
import unittest
from unittest.mock import patch

from webapp.solver import EngineInputs
from webapp.cooling_design import (assess, calculate_cooling_design, configuration,
                                   normalize_request, pareto, source_data)
from cooling import CoolingChannel
from task_control import CancellationToken, TaskCancelled, cancellation_scope


@lru_cache(maxsize=1)
def _source_fixture():
    """A real coolant march with synthetic, explicitly fixed gas boundaries.

    This fixture is not a full-engine validation result. It supplies the saved
    data contract needed to test the one-way thermal-boundary study itself.
    """
    inputs = EngineInputs(cooling_grid_points=40, cooling_channel_auto_size=0.,
        cooling_channel_height_m=.008, cooling_wall_temp_limit_status="unset")
    _, component = CoolingChannel(mdot_fuel=166.3, t_inlet=122.35, p_inlet=74.89e6,
        mdot_total=765., pc=33e6, t_chamber=3773.34, gamma_c=1.189,
        molar_mass_c=22.6866, n_channels=300, n_points=40,
        throat_area_m2=math.pi*.11586**2, channel_height_m=.008, auto_size=False,
        wall_thermal_dataset_id=inputs.wall_thermal_dataset_id,
        wall_manufacturing_state=inputs.wall_manufacturing_state,
        wall_temp_limit_status=inputs.cooling_wall_temp_limit_status,
        nozzle_contour_method=inputs.nozzle_contour_method,
        nozzle_initial_angle_deg=inputs.nozzle_initial_angle_deg,
        nozzle_exit_angle_deg=inputs.nozzle_exit_angle_deg).evaluate()
    # Synthetic eligibility flags exercise the record contract. They do not
    # upgrade this fixed gas-boundary fixture into a full-cycle solution.
    component.profile["hot_side_coupling"] = {"status":"pass", "synthetic_test_fixture":True}
    return {"id":"source-design", "project_id":"source-project", "revision":1,
        "kind":"design", "status":"succeeded", "inputs":asdict(inputs),
        "result":{"environment":{"fixture":"real_cooling_march_synthetic_cycle_boundary"},
            "calculation":{"inputs":asdict(inputs),
                "summary":{"numerical_converged":1., "mdot_total_kg_s":765., "chamber_pressure_Pa":33e6,
                    "chamber_temperature_K":3773.34, "chamber_gamma":1.189,
                    "chamber_molar_mass":22.6866},
                "components":{"cooling_channel":{"outputs":component.outputs,
                    "notes":component.notes, "profile":component.profile}}}}}


def source_fixture():
    return deepcopy(_source_fixture())


def request_fixture(source=None, **changes):
    source = source or source_fixture()
    return {"source_job_id":source["id"], "channel_counts":[300], "widths_mm":[1.5],
            "heights_mm":[8.], "wall_thicknesses_mm":[.5], "constraints":{}, **changes}


class CoolingDesignTests(unittest.TestCase):
    def test_candidate_product_is_bounded_after_deduplication(self):
        source = source_fixture()
        request = request_fixture(source, channel_counts=[400, 100, 300, 200, 100],
            widths_mm=[1., 2., 3., 4.], heights_mm=[3., 6., 9., 12.])
        normalized = normalize_request(request, source)
        self.assertEqual(normalized["channel_counts"], [100, 200, 300, 400])
        self.assertEqual(math.prod(len(normalized[k]) for k in
            ("channel_counts", "widths_mm", "heights_mm", "wall_thicknesses_mm")), 64)
        with self.assertRaisesRegex(ValueError, "超过64"):
            normalize_request(request | {"wall_thicknesses_mm":[.5, 1.]}, source)

    def test_invalid_candidates_and_unknown_fields_fail_before_computing(self):
        source = source_fixture()
        for changes in ({"channel_counts":[]}, {"channel_counts":[True]},
                {"channel_counts":[299.5]}, {"channel_counts":[2001]},
                {"channel_counts":[10**1000]}, {"widths_mm":[float("nan")]},
                {"heights_mm":[float("inf")]}, {"heights_mm":[.4]},
                {"widths_mm":[1.]*17}, {"unknown":0}, {"source_job_id":"other"},
                {"constraints":{"max_pressure_drop_MPa":True}},
                {"constraints":{"wall_limit_status":"documented", "wall_temp_limit_K":900}},
                {"constraints":{"wall_limit_status":"documented", "wall_limit_basis":"test"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_request(request_fixture(source, **changes), source)

    def test_source_is_saved_calculation_and_not_current_or_submitted_draft(self):
        source = source_fixture()
        source["inputs"]["pc"] = 99e6
        before = deepcopy(source)
        values, _, _, profile, _, _ = source_data(source)
        self.assertEqual(values["pc"], 33e6)
        setup = configuration(source)
        self.assertAlmostEqual(setup["boundary"]["mdot_coolant_kg_s"], 166.3)
        self.assertEqual(setup["boundary"]["T_in_K"], profile["nodes"][0]["T_K"])
        self.assertEqual(setup["channel_counts"], [300])
        self.assertEqual(setup["heights_mm"], [8.])
        self.assertEqual(source, before)

    def test_incompatible_or_incomplete_sources_are_rejected(self):
        mutations = [lambda s:s.update(status="running"), lambda s:s.update(kind="throttle"),
            lambda s:s["result"]["calculation"]["summary"].update(numerical_converged=0.),
            lambda s:s["result"]["calculation"]["components"]["cooling_channel"]["profile"]["hot_side_coupling"].update(status="fail"),
            lambda s:s["result"]["calculation"]["inputs"].update(cooling_bartz_mode=0.),
            lambda s:s["result"]["calculation"]["inputs"].update(cooling_channel_table_json="[]"),
            lambda s:s["result"]["calculation"]["components"]["cooling_channel"]["profile"].pop("model_id"),
            lambda s:s["result"]["calculation"]["components"]["cooling_channel"]["profile"]["cells"].pop(1),
            lambda s:s["result"]["calculation"]["components"]["cooling_channel"]["profile"]["cells"][1].pop("hot_side")]
        for mutate in mutations:
            source = source_fixture()
            mutate(source)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                source_data(source)

    def test_changed_material_snapshot_cannot_replay_current_material_silently(self):
        for field in ("revision", "data_sha256"):
            source = source_fixture()
            profile = source["result"]["calculation"]["components"]["cooling_channel"]["profile"]
            profile["material_applicability"]["datasets"][0][field] = "changed-saved-dataset"
            with self.subTest(field=field), self.assertRaises(ValueError):
                source_data(source)

    def test_same_axial_coordinates_do_not_hide_changed_hot_contour(self):
        # Reuse the real baseline march so these tests isolate binding, without
        # changing the gas-state x coordinates or the material evidence.
        solved = {"profile":source_fixture()["result"]["calculation"]["components"]["cooling_channel"]["profile"]}
        for group, field in (("full_nodes", "r_m"), ("full_nodes", "s_m"),
                ("full_nodes", "area_m2"), ("nodes", "r_m"), ("nodes", "area_ratio"),
                ("cells", "r_m"), ("cells", "ds_m"), ("cells", "dA_hot_m2"),
                (None, "throat_upstream_radius_m"), (None, "coverage_end_x_m"),
                (None, "path_length_m"), (None, "hot_area_m2")):
            source = source_fixture()
            geom = source["result"]["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
            record = geom[group][2] if group else geom
            record[field] += 1e-4
            with self.subTest(group=group, field=field), patch("cooling_marching.march_regen_circuit", return_value=solved):
                candidate = calculate_cooling_design(request_fixture(source), source)["candidates"][0]
                self.assertEqual(candidate["status"], "failed")
                self.assertIn("固定热侧轮廓", candidate["error"])
                self.assertNotIn("metrics", candidate)
                self.assertFalse(candidate["pareto"])

    def test_fixed_contour_binding_allows_channel_hardware_to_change(self):
        source = source_fixture()
        candidate = calculate_cooling_design(request_fixture(source, channel_counts=[320],
            widths_mm=[1.4], heights_mm=[9.], wall_thicknesses_mm=[.6]), source)["candidates"][0]
        self.assertEqual(candidate["status"], "computed", candidate.get("error"))
        self.assertEqual(candidate["geometry"], dict(count=320, width_mm=1.4, height_mm=9., wall_mm=.6))
        self.assertEqual(candidate["assessment"]["status"], "incomplete")

    def test_missing_constraints_and_material_evidence_cannot_pass(self):
        source = source_fixture()
        limits = normalize_request(request_fixture(source), source)["constraints"]
        values = dict(wall_max_K=700., dp_MPa=5., min_rib_mm=.8, max_aspect_ratio=5., velocity_max_ms=100.)
        material = dict(status="within_range", manufacturing_match="matched", out_of_range_cell_count=0)
        self.assertEqual(assess(values, limits, material)["status"], "incomplete")
        complete = limits | dict(wall_limit_status="documented", wall_temp_limit_K=900.,
            wall_limit_basis="fixture wall requirement", max_pressure_drop_MPa=10., min_rib_mm=.5,
            max_aspect_ratio=10., basis="fixture manufacturing and pressure budgets")
        self.assertEqual(assess(values, complete, material)["status"], "meets_declared")
        self.assertEqual(assess(values, complete | {"wall_limit_status":"unset"}, material)["status"], "incomplete")
        self.assertEqual(assess(values, complete | {"wall_limit_status":"provisional"}, material)["status"], "provisional")
        self.assertEqual(assess(values, complete, material | {"status":"out_of_range"})["status"], "provisional")
        self.assertEqual(assess(values, complete, material | {"manufacturing_match":"unknown"})["status"], "provisional")
        self.assertEqual(assess(values, complete | {"max_velocity_ms":90.}, material)["status"], "rejected")
        for key in ("wall_temp_limit_K", "max_pressure_drop_MPa", "min_rib_mm", "max_aspect_ratio"):
            self.assertEqual(assess(values, complete | {key:None}, material)["status"], "incomplete")

    def test_pareto_front_does_not_promote_failures_or_rejected_points(self):
        def row(ident, temp, dp, assessment="incomplete"):
            return dict(id=ident,status="computed",metrics=dict(wall_max_K=temp,dp_MPa=dp),
                        assessment=dict(status=assessment))
        rows = [row(1, 700, 10), row(2, 800, 5), row(3, 850, 12),
                row(4, 600, 2, "rejected"), row(5, 700, 10), dict(id=6,status="failed")]
        pareto(rows)
        self.assertEqual([r["id"] for r in rows if r["pareto"]], [1, 2, 5])
        self.assertEqual(rows[0]["assessment"]["status"], "incomplete")

    def test_real_march_retains_failed_candidates_and_source_evidence(self):
        source = source_fixture()
        before = deepcopy(source)
        progress = []
        result = calculate_cooling_design(request_fixture(source, widths_mm=[1.5, 12.]), source,
            lambda status, partial:progress.append((status, partial)))
        good, failed = result["candidates"]
        self.assertEqual(good["status"], "computed")
        self.assertEqual(good["assessment"]["status"], "incomplete")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("width must be below pitch", failed["error"])
        self.assertFalse(failed["pareto"])
        original = source["result"]["calculation"]["components"]["cooling_channel"]["profile"]
        self.assertAlmostEqual(good["metrics"]["Q_MW"], original["summary"]["Q_total_W"]/1e6, delta=1e-8)
        self.assertAlmostEqual(good["metrics"]["dp_MPa"], original["summary"]["dp_total_Pa"]/1e6, delta=1e-8)
        self.assertEqual(result["source_job_id"], source["id"])
        self.assertEqual(result["source_revision"], source["revision"])
        self.assertEqual(source, before)
        self.assertEqual([p[0]["iteration"] for p in progress], [1,2])
        self.assertEqual(len(progress[0][1]["candidates"]), 1)
        self.assertEqual(len(progress[1][1]["candidates"]), 2)
        result["candidates"][0]["status"] = "modified"
        self.assertEqual(progress[1][1]["candidates"][0]["status"], "computed")

    def test_cancellation_preserves_completed_candidate_callback(self):
        token = CancellationToken()
        partials = []
        def progress(status, partial):
            partials.append(partial)
            token.cancel()
        with cancellation_scope(token), self.assertRaises(TaskCancelled):
            calculate_cooling_design(request_fixture(widths_mm=[1.5, 2.]), source_fixture(), progress)
        self.assertEqual(len(partials), 1)
        self.assertEqual(partials[0]["candidates"][0]["status"], "computed")


if __name__ == "__main__":
    unittest.main()
