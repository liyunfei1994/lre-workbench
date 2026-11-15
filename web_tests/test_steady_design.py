from copy import deepcopy
import json
import unittest

from tests.test_steady_closure import balanced_loop
from web_tests.convergence_fixture import converged_evidence
from webapp.steady_design import steady_design_view


def fixture():
    states, components = balanced_loop()
    return {"calculation": {"inputs": {"cycle_type": "ffsc", "propellant_pair": "lox_methane"},
                            "states": states, "components": components, "summary": {},
                            "convergence": converged_evidence(),
                            "steady_closure": {"energy_balance": {"status": "pass", "residual_W": 0}}},
            "assessment": {"checks": [{"label": "暂定温限", "status": "pass", "value": 750,
                                       "limit": 800, "limit_status": "provisional", "criterion": "maximum"}]}}


def gates(report):
    return {g["id"]: g["status"] for g in report["gates"]}


def thermal_range_fixture(*temperatures):
    r = fixture()
    cells = [{"dataset_id": "saved-material-v1", "property": "conductivity",
              "actual_temperature_range_K": [300., high], "data_temperature_range_K": [80.15, 1173.15],
              "status": "out_of_range" if high > 1173.15 else "within_range",
              "within_range": high <= 1173.15, "cell_index": index}
             for index, high in enumerate(temperatures)]
    outside = sum(not row["within_range"] for row in cells)
    r["calculation"]["thermodynamic_applicability"] = {
        "states": [{"T_K": 1000., "thermo_range": {"valid_temperature_min_K": 300.,
            "valid_temperature_max_K": 5000., "outside_mass_fraction": 0}}],
        "pure_fluid_states": [{"T_K": 100., "p_Pa": 1e6, "phase": "liquid",
            "status": "within_declared_ranges", "limits": {"Tmin_K": 90., "Tmax_K": 500., "pmax_Pa": 10e6}}],
        "solid_wall_thermal": {"status": "out_of_range" if outside else "within_range",
            "total_cells": len(cells), "audited_cells": len(cells), "out_of_range_cell_count": outside,
            "active_properties": ["conductivity"], "cells": cells,
            "out_of_range_policy": "constant_endpoint_continuation_for_diagnostics_not_validated",
            "wall_temperature_constraint_status": "unset", "wall_temperature_limit_declared_K": 800.}}
    return r


class SteadyDesignTests(unittest.TestCase):
    def test_projection_is_immutable_and_separates_closure_from_validation(self):
        r = fixture(); before = deepcopy(r)
        v = steady_design_view(r, "record-a")
        self.assertEqual(r, before)
        self.assertEqual(v["record_id"], "record-a")
        self.assertEqual(gates(v)["energy"], "pass")
        self.assertEqual(gates(v)["constraints"], "review")
        self.assertEqual(gates(v)["handoff"], "review")
        self.assertEqual(v["validation_status"], "not_certified")
        self.assertEqual(v["status"], "review_required")
        json.dumps(v, allow_nan=False)

    def test_energy_counterexample_ignores_saved_pass(self):
        r = fixture()
        r["calculation"]["states"]["fuel_after_cooling"]["enthalpy"] += 100
        v = steady_design_view(r)
        self.assertEqual(gates(v)["energy"], "fail")
        self.assertEqual(v["status"], "blocked")

    def test_absent_models_do_not_borrow_current_defaults(self):
        v = steady_design_view(fixture())
        self.assertTrue(all(m["value"] == "未记录" for m in v["models"]))
        self.assertEqual(gates(v)["range"], "review")

    def test_unset_constraint_and_stale_pass_remain_visible(self):
        r = fixture()
        r["assessment"]["checks"].append({"label": "壁温", "status": "pass", "value": 1200, "limit": None})
        self.assertEqual(gates(steady_design_view(r))["constraints"], "review")
        r["assessment"]["checks"][0]["value"] = 900
        self.assertEqual(gates(steady_design_view(r))["constraints"], "fail")

    def test_explicit_range_violation_and_conflict_are_not_green(self):
        r = fixture()
        r["calculation"]["thermodynamic_applicability"] = {"states": [{"T_K": 5200,
            "thermo_range": {"valid_temperature_min_K": 300, "valid_temperature_max_K": 5000, "outside_mass_fraction": 0}}]}
        v = steady_design_view(r, consistency={"status": "pass", "conflict_count": 1})
        self.assertEqual(gates(v)["range"], "fail")
        self.assertEqual(gates(v)["consistency"], "fail")

    def test_other_cycles_and_absent_calculation_have_no_ffsc_claim(self):
        self.assertEqual(steady_design_view(None)["status"], "unavailable")
        r = fixture(); r["calculation"]["inputs"]["cycle_type"] = "gg"
        self.assertEqual(steady_design_view(r)["status"], "not_applicable")

    def test_pure_fluid_range_evidence_overrides_stale_pass(self):
        r = fixture()
        row = {"T_K": 100, "p_Pa": 20e6, "limits": {"Tmin_K": 90, "Tmax_K": 500, "pmax_Pa": 10e6},
               "status": "within_declared_ranges", "phase": "liquid"}
        r["calculation"]["thermodynamic_applicability"] = {"pure_fluid_states": [row]}
        self.assertEqual(gates(steady_design_view(r))["range"], "fail")
        row.update(p_Pa=1e6, phase="twophase")
        self.assertEqual(gates(steady_design_view(r))["range"], "fail")

    def test_handoff_cannot_drift_from_canonical_state(self):
        r = fixture()
        r["calculation"]["states"]["main_chamber"]["temperature"] = 3700
        r["gas_handoff"] = {"status": "complete", "stations": [{"id": "main_chamber", "T_K": -1,
            "checks": [{"status": "pass"}]}]}
        self.assertEqual(gates(steady_design_view(r))["handoff"], "fail")

    def test_fluid_and_wall_data_coverage_are_independent_gates(self):
        r = thermal_range_fixture(*([1000.] * 115 + [1179.656] * 4))
        before = deepcopy(r)
        v = steady_design_view(r)
        self.assertEqual(r, before)
        self.assertEqual(gates(v)["range"], "pass")
        self.assertEqual(gates(v)["wall_thermal_range"], "fail")
        self.assertEqual(v["status"], "blocked")
        wall = next(g for g in v["gates"] if g["id"] == "wall_thermal_range")
        self.assertEqual(wall["label"], "壁面材料热物性数据范围")
        self.assertEqual(wall["status_label"], "超出数据温区")
        for text in ("4/119", "1179.66 K", "1173.15 K", "端点常值延拓", "不代表材料失效"):
            self.assertIn(text, wall["detail"])
        self.assertEqual(wall["target"], "cooling")
        self.assertEqual(sum(a.get("id") == "wall_thermal_range" for a in v["actions"]), 1)
        self.assertFalse(any(a["label"] == "壁材热物性超出来源温区" for a in v["actions"]))
        self.assertEqual(next(g["label"] for g in v["gates"] if g["id"] == "range"), "流体物性声明范围")

    def test_wall_data_in_range_does_not_accept_unset_temperature_limit(self):
        r = thermal_range_fixture(1000., 1173.15)
        solid = r["calculation"]["thermodynamic_applicability"]["solid_wall_thermal"]
        solid["manufacturing_match"] = "unknown"
        v = steady_design_view(r)
        self.assertEqual(gates(v)["wall_thermal_range"], "pass")
        self.assertEqual(gates(v)["constraints"], "review")
        self.assertFalse(any(a.get("id") == "wall_thermal_range" for a in v["actions"]))
        wall = next(g for g in v["gates"] if g["id"] == "wall_thermal_range")
        self.assertEqual(wall["status_label"], "数据温区内")
        self.assertNotIn("800", wall["detail"])

    def test_historical_missing_wall_evidence_is_not_green(self):
        v = steady_design_view(fixture())
        wall = next(g for g in v["gates"] if g["id"] == "wall_thermal_range")
        self.assertEqual(wall["status"], "review")
        self.assertEqual(wall["status_label"], "证据不足")
        self.assertIn("不使用当前材料库", wall["detail"])
        self.assertTrue(any(a.get("id") == "wall_thermal_range" for a in v["actions"]))

    def test_partial_or_invalid_cell_evidence_cannot_pass_by_summary_flag(self):
        for case in ("missing_cell", "invalid_interval", "missing_dataset", "invalid_count"):
            with self.subTest(case=case):
                r = thermal_range_fixture(1000., 1050.)
                solid = r["calculation"]["thermodynamic_applicability"]["solid_wall_thermal"]
                if case == "missing_cell":
                    solid["cells"].pop()
                elif case == "invalid_interval":
                    solid["cells"][0]["data_temperature_range_K"] = [float("nan"), 1173.15]
                elif case == "missing_dataset":
                    solid["cells"][0].pop("dataset_id")
                else:
                    solid["audited_cells"] = True
                self.assertEqual(gates(steady_design_view(r))["wall_thermal_range"], "review")

    def test_cell_temperature_violation_overrides_stale_pass_and_zero_count(self):
        r = thermal_range_fixture(1000., 1050.)
        solid = r["calculation"]["thermodynamic_applicability"]["solid_wall_thermal"]
        solid["cells"][1]["actual_temperature_range_K"][1] = 1200.
        v = steady_design_view(r)
        self.assertEqual(gates(v)["wall_thermal_range"], "fail")
        wall = next(g for g in v["gates"] if g["id"] == "wall_thermal_range")
        self.assertIn("1/2", wall["detail"])

    def test_explicit_out_of_range_summary_survives_missing_cells(self):
        r = thermal_range_fixture(1200.)
        r["calculation"]["thermodynamic_applicability"]["solid_wall_thermal"]["cells"] = []
        self.assertEqual(gates(steady_design_view(r))["wall_thermal_range"], "fail")

    def test_each_material_zone_uses_its_own_saved_data_range(self):
        r = thermal_range_fixture(1000., 1050.)
        cells = r["calculation"]["thermodynamic_applicability"]["solid_wall_thermal"]["cells"]
        cells[1].update(dataset_id="another-saved-material", data_temperature_range_K=[200., 1000.])
        self.assertEqual(gates(steady_design_view(r))["wall_thermal_range"], "fail")


if __name__ == "__main__":
    unittest.main()
