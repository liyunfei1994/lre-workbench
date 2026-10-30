"""Saved views must expose divergent copies, never silently choose a winner."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.result_consistency import result_consistency_view
from webapp.pump_geometry import pump_geometry_view
from webapp.turbine_geometry import turbine_geometry_view
from webapp.nozzle_geometry import nozzle_geometry_view
from web_tests.test_pump_geometry import fixture as pump_fixture
from web_tests.test_turbine_geometry import fixture as turbine_fixture
from web_tests.test_nozzle_geometry import fixture as nozzle_fixture


def fixture(cycle="ffsc", dual=False):
    result = pump_fixture()
    calc = result["calculation"]
    pump_inputs = deepcopy(calc["inputs"])
    for other in (turbine_fixture(), nozzle_fixture()):
        calc["inputs"].update(other["calculation"]["inputs"])
        calc["components"].update(other["calculation"]["components"])
    calc["inputs"].update(pump_inputs)
    calc["inputs"].update(cycle_type=cycle, gg_layout="dual_shaft" if dual else "single_shaft")
    states, summary = {}, {}
    components = calc["components"]
    for key, inlet, outlet, prefix in (("ox_pump", "lox_pump_inlet", "lox_after_pump", "lox"),
                                       ("fuel_pump", "fuel_pump_inlet", "fuel_after_pump", "fuel")):
        out = components[key]["outputs"]
        out.update(p_in_Pa=out["p_in_MPa"] * 1e6, p_out_Pa=out["p_out_MPa"] * 1e6,
                   p_in_bar=out["p_in_MPa"] * 10, p_out_bar=out["p_out_MPa"] * 10,
                   power_W=1e6, power_MW=1, T_in_K=100, T_out_K=125)
        states[inlet] = {"pressure": out["p_in_Pa"], "temperature": 100, "mass_flow": 200}
        states[outlet] = {"pressure": out["p_out_Pa"], "temperature": 125, "mass_flow": 200}
        summary[prefix + "_pump_out_pressure_Pa"] = out["p_out_Pa"]
        summary[prefix + "_pump_out_pressure_MPa"] = out["p_out_MPa"]
        summary[key + "_power_MW"] = 1
    if cycle == "gg":
        template = components.pop("ox_preburner_turbine")
        components.pop("fuel_preburner_turbine")
        if dual:
            specs = [("gg_turbine_ox", "gg_outlet", "gg_turbine_ox_exhaust", "ox"),
                     ("gg_turbine_fuel", "gg_outlet", "gg_turbine_fuel_exhaust", "fuel")]
        else:
            specs = [("gg_turbine", "gg_outlet", "gg_turbine_exhaust", "gg")]
        for key, *_ in specs:
            components[key] = deepcopy(template)
        if dual:
            # Deliberately different branch temperatures and mass flows.
            out = components["gg_turbine_fuel"]["outputs"]
            out.update(T_exhaust_K=730, mass_flow_kgs=10, turbine_power_W=1.5e6)
    else:
        specs = [(side + "_preburner_turbine", side + "_preburner_outlet", side + "_turbine_exhaust", side)
                 for side in ("ox", "fuel")]
    for key, inlet, outlet, side in specs:
        out = components[key]["outputs"]
        out["turbine_power_MW"] = out["turbine_power_W"] / 1e6
        states[inlet] = {"pressure": out["p_in_Pa"], "temperature": out["T_in_K"], "mass_flow": 30 if dual else 20}
        states[outlet] = {"pressure": out["p_out_Pa"], "temperature": out["T_exhaust_K"], "mass_flow": out["mass_flow_kgs"]}
        summary[side + "_turbine_power_MW"] = out["turbine_power_MW"]
        burner = "gas_generator" if cycle == "gg" else side + "_preburner"
        components[burner] = {"outputs": {"preburner_pressure_Pa": out["p_in_Pa"],
                                         "preburner_pressure_MPa": out["p_in_Pa"] / 1e6,
                                         "T_combustion_K": out["T_in_K"]}}
        if cycle == "gg":
            summary.update(gg_T_combustion_K=out["T_in_K"], gg_pressure_Pa=out["p_in_Pa"])
        else:
            summary.update({side + "_preburner_T_combustion_K": out["T_in_K"],
                            side + "_preburner_pressure_Pa": out["p_in_Pa"],
                            side + "_turbine_exhaust_T_K": out["T_exhaust_K"],
                            side + "_turbine_exhaust_pressure_Pa": out["p_out_Pa"]})
    if cycle == "gg":
        if dual:
            states["gg_turbine_exhaust"] = {**states["gg_turbine_ox_exhaust"], "mass_flow": 30}
        summary.update(gg_turbine_exhaust_T_K=states["gg_turbine_exhaust"]["temperature"],
                       gg_turbine_backpressure_Pa=states["gg_turbine_exhaust"]["pressure"])
    states["main_chamber"] = {"pressure": 1e6, "temperature": 3000}
    summary.update(chamber_pressure_Pa=1e6, chamber_temperature_K=3000)
    calc.update(states=states, summary=summary)
    result["submitted_inputs"] = deepcopy(calc["inputs"])
    result["pump_geometry"] = pump_geometry_view(result, "saved")
    result["turbine_geometry"] = turbine_geometry_view(result, "saved")
    result["nozzle_geometry"] = nozzle_geometry_view(result, "saved")
    return result


def checks(view):
    return {row["id"]: row for row in view["checks"]}


class ResultConsistencyTests(unittest.TestCase):
    def test_complete_saved_ffsc_record_passes_without_mutation(self):
        result = fixture()
        before = deepcopy(result)
        view = result_consistency_view(result, "record-7")
        self.assertEqual(view["status"], "pass", view["conflicts"] or [r for r in view["checks"] if r["status"] != "pass"])
        self.assertEqual(view["record_id"], "record-7")
        self.assertGreater(view["checked_count"], 50)
        self.assertEqual(view["conflicts"], [])
        self.assertEqual(result, before)
        json.dumps(view, allow_nan=False)

    def test_si_pressure_changed_without_display_alias_is_reported(self):
        result = fixture()
        result["calculation"]["components"]["ox_pump"]["outputs"]["p_out_Pa"] *= 1.03
        view = result_consistency_view(result)
        row = checks(view)["alias.ox_pump.p_out"]
        self.assertEqual(row["status"], "conflict")
        self.assertIn("calculation.components.ox_pump.outputs.p_out_MPa", row["source_paths"])
        self.assertEqual(view["status"], "conflict")

    def test_power_alias_and_turbine_temperature_alias_conflicts_are_explicit(self):
        result = fixture()
        out = result["calculation"]["components"]["fuel_preburner_turbine"]["outputs"]
        out.update(turbine_power_MW=99, T_out_K=850)
        rows = checks(result_consistency_view(result))
        self.assertEqual(rows["alias.fuel_preburner_turbine.turbine_power"]["status"], "conflict")
        self.assertEqual(rows["alias.fuel_preburner_turbine.outlet_temperature"]["status"], "conflict")

    def test_station_only_change_and_summary_only_change_are_detected(self):
        for path, check_id in ((["states", "fuel_after_pump", "temperature"], "station.fuel_pump.out.temperature"),
                               (["states", "ox_preburner_outlet", "pressure"], "station.ox_preburner_turbine.in.pressure"),
                               (["summary", "fuel_turbine_exhaust_T_K"], "summary.fuel_turbine_exhaust_T_K")):
            with self.subTest(path=path):
                result = fixture()
                target = result["calculation"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] *= 1.1
                self.assertEqual(checks(result_consistency_view(result))[check_id]["status"], "conflict")

    def test_gg_single_and_distinct_parallel_branches_are_not_conflated(self):
        for dual in (False, True):
            with self.subTest(dual=dual):
                view = result_consistency_view(fixture("gg", dual))
                self.assertEqual(view["status"], "pass", view["conflicts"] or [r for r in view["checks"] if r["status"] != "pass"])
        result = fixture("gg", True)
        result["calculation"]["states"]["gg_turbine_fuel_exhaust"]["temperature"] = 800
        row = checks(result_consistency_view(result))["station.gg_turbine_fuel.out.temperature"]
        self.assertEqual(row["status"], "conflict")

    def test_historical_gg_missing_branch_states_is_partial_not_false_conflict(self):
        result = fixture("gg", True)
        for key in ("gg_turbine_ox_exhaust", "gg_turbine_fuel_exhaust"):
            del result["calculation"]["states"][key]
        view = result_consistency_view(result)
        self.assertEqual(view["status"], "partial")
        self.assertEqual(view["conflicts"], [])
        self.assertEqual(checks(view)["station.gg_turbine_fuel.out.temperature"]["status"], "partial")

    def test_total_power_is_sum_of_actual_branches_and_shared_turbine_counts_once(self):
        for dual, total in ((False, 3), (True, 4.5)):
            result = fixture("gg", dual)
            summary = result["calculation"]["summary"]
            summary.update(turbine_power_MW=total, gg_turbine_power_MW=total, pump_power_MW=2)
            self.assertEqual(result_consistency_view(result)["status"], "pass")
            summary["turbine_power_MW"] += .5
            row = checks(result_consistency_view(result))["summary.turbine_power_MW.total"]
            self.assertEqual(row["status"], "conflict")

    def test_saved_geometry_detects_changed_system_point_without_redrawing(self):
        cases = (("ox_pump", "T_out_K", "geometry.pump_geometry.ox.outlet_temperature_k"),
                 ("fuel_preburner_turbine", "mass_flow_kgs", "geometry.turbine_geometry.fuel.mass_flow_kg_s"),
                 ("nozzle", "At_m2", "geometry.nozzle.throat_diameter_m"))
        for component, field, check_id in cases:
            with self.subTest(component=component):
                result = fixture()
                before = deepcopy(result["nozzle_geometry"])
                result["calculation"]["components"][component]["outputs"][field] *= 1.1
                self.assertEqual(checks(result_consistency_view(result))[check_id]["status"], "conflict")
                self.assertEqual(result["nozzle_geometry"], before)

    def test_geometry_snapshot_corruption_and_profile_divergence_are_found(self):
        result = fixture()
        result["pump_geometry"]["pumps"]["ox"]["dimensions"]["outlet_width_m"] *= 2
        row = checks(result_consistency_view(result))["geometry.integrity.pump_geometry.pumps.ox"]
        self.assertEqual(row["status"], "conflict")
        result = fixture()
        result["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]["full_nodes"][-1]["r_m"] *= 2
        rows = checks(result_consistency_view(result))
        self.assertEqual(rows["geometry.nozzle.profile.r_m"]["status"], "conflict")
        self.assertEqual(rows["geometry.integrity.calculation.components.cooling_channel.profile.geometry"]["status"], "conflict")

    def test_common_input_change_conflicts_but_added_key_and_metadata_do_not(self):
        result = fixture()
        result["submitted_inputs"]["future_field"] = "new"
        result["calculation"]["states"]["main_chamber"]["metadata"] = {"new_note": "anything"}
        result["pump_geometry"]["pumps"]["ox"]["source"]["comment"] = "later annotation"
        result["nozzle_geometry"]["source"]["record_id"] = "label-only"
        result["some_transient_study"] = {"pressure_Pa": 999}
        self.assertEqual(result_consistency_view(result)["status"], "pass")
        result["submitted_inputs"]["pc"] *= 1.1
        self.assertEqual(checks(result_consistency_view(result))["inputs.pc"]["status"], "conflict")

    def test_missing_and_nonfinite_data_stay_partial_and_json_safe(self):
        for result in (None, {}, {"calculation": {"components": None, "inputs": []}}):
            with self.subTest(result=result):
                view = result_consistency_view(result)
                self.assertEqual(view["status"], "partial")
                self.assertEqual(view["conflicts"], [])
                json.dumps(view, allow_nan=False)
        result = fixture()
        result["calculation"]["states"]["ox_turbine_exhaust"]["temperature"] = float("nan")
        view = result_consistency_view(result)
        self.assertEqual(view["status"], "partial")
        json.dumps(view, allow_nan=False)

    def test_no_geometry_reconstruction_and_total_state_unavailable_is_partial(self):
        result = fixture()
        for family in ("pump_geometry", "turbine_geometry", "nozzle_geometry"):
            del result[family]
        with patch("webapp.nozzle_geometry.nozzle_geometry_view", side_effect=AssertionError("must not rebuild")):
            self.assertEqual(result_consistency_view(result)["status"], "partial")
        result = fixture()
        result["turbine_geometry"]["turbines"]["ox"] = {
            "available": False, "source": {"component_key": "ox_preburner_turbine", "assumption_prefix": "ox"},
            "reason": "本记录采用显式总态接口。未解析静态密度，不使用旧静态参考假设生成环道。",
            "design_point": {}, "dimensions": {}}
        view = result_consistency_view(result)
        self.assertEqual(view["status"], "partial")
        self.assertEqual(view["conflicts"], [])


if __name__ == "__main__":
    unittest.main()
