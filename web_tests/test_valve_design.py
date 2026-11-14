"""Saved valve demands must not acquire another scheme's identity or fluid state."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from webapp.design_traceability import fingerprint, source_context
from webapp.valve_design import valve_design_view, valve_selection_envelope, valve_actuator_preview, VALVE_IDS
from lre_workbench.components.valves import (template_configuration, parse_configuration,
                                            evaluate_valve, topology_hash, characteristic_hash)


def inlet(fluid, pressure, temperature):
    return {"backend": "fixture_only", "fluid": fluid, "pressure_Pa": pressure,
            "temperature_K": temperature, "density_kg_m3": 450 + temperature,
            "enthalpy_J_kg": 2000 * temperature, "entropy_J_kg_K": 10,
            "isothermal_compressibility_1_Pa": 1e-9, "phase": "liquid"}


def seal(record):
    result = record["result"]
    calculation = result["calculation"]
    trace = {"schema_version": 1, "source": source_context(record),
             "submitted_inputs_sha256": fingerprint(record["inputs"]),
             "solver_inputs_sha256": fingerprint(result["submitted_inputs"]),
             "calculation_inputs_sha256": fingerprint(calculation["inputs"]),
             "calculation_sha256": fingerprint(calculation)}
    trace["evidence_sha256"] = fingerprint(trace)
    result["design_traceability"] = trace
    return record


def fixture(job="job-a", project="project-a", revision=1, mass=100, temperature=100,
            topology="paper_split_before_cooling_v1", rated=None):
    mode = "candidate_check" if rated is not None else "requirement_sizing"
    config = template_configuration(topology, mode)
    for row in config["valves"].values():
        row.update(design_pressure_drop_Pa=1e6, rated_cda_m2=rated)
    config = parse_configuration(config)
    with patch("lre_workbench.components.valves._heos_inlet", side_effect=inlet), \
         patch("lre_workbench.components.valves._heos_outlet",
               side_effect=lambda fluid, pressure, enthalpy: inlet(fluid, pressure, enthalpy / 2000) | {
                   "process": "fixture_isenthalpic", "vapor_pressure_Pa": 1000}):
        valves = {key: evaluate_valve(row, mass_flow_kg_s=mass, upstream_pressure_Pa=60e6,
                                      upstream_temperature_K=temperature, downstream_pressure_Pa=59e6, mode=mode)
                  for key, row in config["valves"].items()}
    saved = {"schema_version": 1, "scope": "valve_constrained_concept_design", "configuration": config,
             "status": "pass" if all(row["status"] in ("available", "feasible") for row in valves.values()) else "infeasible",
             "topology_hash": topology_hash(config),
             "characteristic_hash": {key: characteristic_hash(row) for key, row in config["valves"].items()},
             "valves": valves, "limitations": []}
    inputs = {"cycle_type": "ffsc", "pc": 30, "valve_configuration_json": json.dumps(config)}
    result = {"submitted_inputs": deepcopy(inputs), "calculation": {
        "inputs": deepcopy(inputs), "steady_closure": {"valve_design": saved}}}
    return seal({"id": job, "project_id": project, "revision": revision, "mode": "concept_design",
                 "inputs": inputs, "result": result})


class SavedValveDesignTests(unittest.TestCase):
    def test_saved_projection_and_json_roundtrip_do_not_mutate_or_compute(self):
        row = json.loads(json.dumps(fixture()))
        before = deepcopy(row)
        with patch("lre_workbench.components.valves.evaluate_valve", side_effect=AssertionError("no solve")), \
             patch("lre_workbench.components.valves._heos_inlet", side_effect=AssertionError("no property lookup")):
            view = valve_design_view(row["result"], row)
        self.assertEqual(view["status"], "recorded", view["issues"])
        self.assertTrue(view["selection_eligible"])
        self.assertEqual(view["source"]["source_design_job_id"], "job-a")
        self.assertEqual(view["source"]["input_hash"], fingerprint(row["result"]["calculation"]["inputs"]))
        self.assertEqual(row, before)
        view["rows"][0]["result"]["inlet_state"]["temperature_K"] = 999
        self.assertEqual(row, before)

    def test_legacy_missing_is_not_filled_from_record_draft(self):
        row = fixture()
        del row["result"]["calculation"]["steady_closure"]["valve_design"]
        view = valve_design_view(row["result"], row)
        self.assertEqual(view["status"], "missing")
        self.assertEqual(view["rows"], [])
        self.assertFalse(view["selection_eligible"])

    def test_unsealed_result_cannot_be_relabelled_by_reader(self):
        row = fixture()
        del row["result"]["design_traceability"]
        self.assertEqual(valve_design_view(row["result"], row)["status"], "incomplete")
        with self.assertRaises(ValueError):
            valve_selection_envelope([row])

    def test_project_revision_job_and_inputs_relabelling_are_rejected(self):
        for key, value in (("project_id", "project-b"), ("revision", 2), ("id", "other-job"),
                           ("inputs", {"pc": 20})):
            row = fixture()
            row[key] = value
            with self.subTest(key=key):
                self.assertEqual(valve_design_view(row["result"], row)["status"], "conflict")

    def test_saved_solution_change_invalidates_completion_evidence(self):
        row = fixture()
        row["result"]["calculation"]["steady_closure"]["valve_design"]["valves"]["MFV"]["mass_flow_kg_s"] = 99
        self.assertEqual(valve_design_view(row["result"], row)["status"], "conflict")

    def test_sealed_but_internally_mixed_states_or_characteristics_are_rejected(self):
        mutations = [lambda data: data["valves"]["MFV"]["inlet_state"].update(temperature_K=999),
                     lambda data: data["valves"]["MFV"].update(characteristic_hash="other"),
                     lambda data: data["configuration"]["valves"]["MFV"].update(characteristic_version="other")]
        for mutation in mutations:
            row = fixture()
            mutation(row["result"]["calculation"]["steady_closure"]["valve_design"])
            seal(row)
            self.assertEqual(valve_design_view(row["result"], row)["status"], "conflict")

    def test_package_extrema_preserve_the_whole_driving_state_and_source(self):
        a = fixture()
        b = fixture(job="job-b", revision=2, mass=150, temperature=150)
        before = deepcopy([a, b])
        package = valve_selection_envelope([a, b])
        row = package["valves"]["MFV"]["ranges"]["mass_flow_kg_s"]["max"]
        self.assertEqual(row["value"], 150)
        self.assertEqual(row["source"]["source_design_job_id"], "job-b")
        self.assertEqual(row["point"]["result"]["inlet_state"]["temperature_K"], 150)
        self.assertEqual(row["point"]["result"]["inlet_state"]["density_kg_m3"], 600)
        self.assertEqual(package["valves"]["MFV"]["ranges"]["opening"]["status"], "missing")
        self.assertEqual([a, b], before)
        json.dumps(package, allow_nan=False)

    def test_packages_reject_different_projects_topologies_and_characteristics(self):
        alternatives = [fixture(job="job-b", project="project-b"),
                        fixture(job="job-b", topology="all_fuel_cooled_then_split_valves_v1"),
                        fixture(job="job-b", rated=.01)]
        for b in alternatives:
            with self.assertRaises(ValueError):
                valve_selection_envelope([fixture(), b])

    def test_duplicate_jobs_rejected(self):
        with self.assertRaises(ValueError):
            valve_selection_envelope([fixture(), fixture()])

    def test_saturated_candidate_is_a_failed_requirement_not_a_passed_selection(self):
        row = fixture(rated=1e-8)
        view = valve_design_view(row["result"], row)
        self.assertEqual(view["calculation_status"], "infeasible")
        package = valve_selection_envelope([row])
        self.assertFalse(package["all_points_feasible"])
        self.assertEqual(package["status"], "requirements_only")
        self.assertEqual(len(package["valves"]["MFV"]["saturated_sources"]), 1)
        self.assertEqual(package["valves"]["MFV"]["ranges"]["opening"]["status"], "missing")

    def test_actuator_step_uses_saved_limits_and_rate_without_engine_claim(self):
        definition = {"opening_min": .1, "opening_max": .9, "actuator_tau_s": .1, "actuator_rate_per_s": 1}
        preview = valve_actuator_preview(definition, {"source_design_job_id": "a"})
        self.assertEqual(preview["status"], "estimated")
        # Travel=0.8: rate limited for 0.7 s, then exponential error from 0.1 to 0.08.
        import math
        self.assertAlmostEqual(preview["rise_t90_s"], .7 + .1 * math.log(.1 / .08))
        self.assertEqual(preview["samples"][0]["rise_opening"], .1)
        self.assertEqual(preview["samples"][0]["fall_opening"], .9)
        self.assertTrue(all(abs(sample["rise_rate_per_s"]) <= 1 and abs(sample["fall_rate_per_s"]) <= 1 for sample in preview["samples"]))
        self.assertAlmostEqual(preview["samples"][-1]["rise_opening"], .892)
        self.assertEqual(preview["source"]["source_design_job_id"], "a")
        self.assertEqual(valve_actuator_preview(definition | {"actuator_tau_s": -1})["status"], "unavailable")

    def test_actuator_unlimited_first_order_t90_and_input_config_conflict(self):
        import math
        preview = valve_actuator_preview({"opening_min": 0, "opening_max": 1, "actuator_tau_s": 1, "actuator_rate_per_s": 10})
        self.assertAlmostEqual(preview["rise_t90_s"], math.log(10))
        row = fixture()
        row["result"]["calculation"]["inputs"]["valve_configuration_json"] = ""
        seal(row)
        self.assertEqual(valve_design_view(row["result"], row)["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
