"""CFturbo handoffs use one saved system point and solve-time gas evidence."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import thermo
from core import FlowState
from turbine import Turbine
from webapp.turbine_design import capture_turbine_gas_properties, turbine_design_view, _hash
from webapp.turbine_geometry import turbine_geometry_view


def fixture(*, basis="total_to_total", layout="ffsc", capture=True):
    gas = thermo._get_gas("CH4", methane_dataset="gri30")
    before = gas.state.copy()
    try:
        gas.TPY = 950., 5e6, {"O2": .8, "H2O": .2}
        state = thermo._gas_to_state(gas, 5e6)
        inputs = {"cycle_type": "ffsc" if layout == "ffsc" else "gg", "methane_thermo_dataset": "gri30",
                  "propellant_pair": "lox_methane", "turbine_boundary_basis": basis,
                  "turbine_meanline_mode": "disabled"}
        if layout != "ffsc":
            inputs.update(gg_layout=layout, gg_mech_efficiency=.96)
        for side in ("ox", "fuel"):
            inputs.update({f"{side}_turbine_inlet_flow_coefficient": .6, f"{side}_turbine_outlet_flow_coefficient": .7,
                           f"{side}_turbine_stator_axial_chord_ratio": .12, f"{side}_turbine_rotor_axial_chord_ratio": .15,
                           f"{side}_turbine_row_gap_ratio": .04, f"{side}_turbine_geometry_state_basis": "system_pt_as_static"})
        components, states = {}, {}
        specs = [("ox_preburner_turbine", "ox_preburner_outlet", "ox_turbine_exhaust", 20., "ox"),
                 ("fuel_preburner_turbine", "fuel_preburner_outlet", "fuel_turbine_exhaust", 20., "fuel")]
        if layout == "single_shaft":
            specs = [("gg_turbine", "gg_outlet", "gg_turbine_exhaust", 20., "shared")]
        elif layout == "dual_shaft":
            specs = [("gg_turbine_ox", "gg_outlet", "gg_turbine_ox_exhaust", 8., "ox"),
                     ("gg_turbine_fuel", "gg_outlet", "gg_turbine_fuel_exhaust", 12., "fuel")]
        for component, inlet_key, outlet_key, mdot, side in specs:
            outlet, turbine = Turbine(component, state, 1e6, .8, mdot, shaft_speed_rpm=20000.,
                                      n_stages_user=2, state_basis=basis).evaluate()
            components[component] = asdict(turbine)
            states[inlet_key] = asdict(FlowState(inlet_key, state.pressure, state.temperature,
                20. if layout == "dual_shaft" else mdot, enthalpy=state.enthalpy,
                composition=deepcopy(state.composition), metadata={"thermo_source": 1.0}))
            states[outlet_key] = asdict(outlet)
            delivered = turbine.outputs["turbine_power_W"] * (1 if layout == "ffsc" else .96)
            if side == "shared":
                components["ox_pump"] = {"outputs": {"power_W": .4 * delivered}}
                components["fuel_pump"] = {"outputs": {"power_W": .6 * delivered}}
            else:
                components["ox_pump" if side == "ox" else "fuel_pump"] = {"outputs": {"power_W": delivered}}
        mechanism = "gri30.yaml"
        environment = {"physics": {"chemistry": "Cantera", "mechanism": mechanism, "phase": "gri30", "fuel_species": "CH4"},
                       "mechanism_sha256": hashlib.sha256((Path(thermo.ct.__file__).parent / "data" / mechanism).read_bytes()).hexdigest(),
                       "dependencies": {"cantera": thermo.ct.__version__},
                       "thermodynamic_scope": {"gas_eos": "ideal-gas", "high_pressure_mixture_validation": "not_validated"}}
        result = {"calculation": {"inputs": inputs, "components": components, "states": states}, "environment": environment}
        # Complete, internally consistent four-layer evidence for this fixture;
        # individual turbine evaluation alone is deliberately not acceptance.
        result["calculation"]["summary"] = {"solver_residual_norm": 1e-8, "solver_warning": 0.,
            "numerical_converged": 1., "solver_evidence_incomplete": 0.}
        result["calculation"]["convergence"] = {"schema_version": 1, "status": "pass", "layers": {
            "inner": {"enabled": True, "status": "pass", "residual": 1e-8, "tolerance": 1e-4,
                      "termination_reason": "converged", "checks": []},
            "thermal": {"enabled": False, "status": "disabled"},
            "preburner": {"enabled": False, "status": "disabled"},
            "continuation": {"enabled": True, "status": "pass", "residual": 0., "tolerance": 0.,
                             "reached_ratio": 1., "termination_reason": "target_reached", "checks": []}}}
        if capture:
            result["turbine_gas_properties"] = capture_turbine_gas_properties(result, thermo)
        return result
    finally:
        gas.state = before


def first(result):
    return next(iter(turbine_design_view(result, "record-1")["turbines"].values()))


def field(turbine, key, group="global_fields"):
    fields = turbine[group] if group == "global_fields" else turbine[group]["fields"]
    return next(f for f in fields if f["key"] == key)


class TurbineDesignTests(unittest.TestCase):
    def test_explicit_total_interface_and_saved_real_properties_make_global_data_ready(self):
        result = fixture()
        original = deepcopy(result)
        turbines = turbine_design_view(result, "record-1")["turbines"]
        self.assertEqual(set(turbines), {"ox", "fuel"})
        for t in turbines.values():
            self.assertEqual(t["status"], "ready", t["checks"])
            self.assertTrue(t["ready_for_global_setup"])
            self.assertEqual(t["source"]["record_id"], "record-1")
            self.assertEqual(field(t, "total_pressure_in_pa")["value"], 5e6)
            self.assertEqual(field(t, "total_pressure_in_pa")["status"], "assumed")
            self.assertEqual(field(t, "total_to_total_pressure_ratio")["value"], 5)
            self.assertIsNone(field(t, "total_to_static_pressure_ratio")["value"])
            self.assertIsNone(field(t, "additional_efficiency")["value"])
            self.assertTrue(field(t, "mass_flow_kg_s")["source"].endswith("mass_flow_kgs"))
            self.assertEqual(t["gas"]["composition"], [{"species": "H2O", "mass_fraction": .2}, {"species": "O2", "mass_fraction": .8}])
            self.assertEqual(t["gas"]["model"]["gas_eos"], "ideal-gas")
            self.assertTrue(t["gas"]["model"]["mechanism_sha256"])
            self.assertTrue(t["gas"]["model"]["participating_species_thermo_sha256"])
            self.assertTrue(t["stage_handoff"]["missing_requirements"])
            self.assertEqual(t["meanline"]["status"], "disabled")
        self.assertEqual(result, original)
        json.dumps(turbines, allow_nan=False)

    def test_solve_time_sampling_restores_cached_gas_and_keeps_frozen_mass_fractions(self):
        result = fixture(capture=False)
        gas = thermo._get_gas("CH4", methane_dataset="gri30")
        gas.TPY = 1200., 2e6, {"N2": 1}
        before = gas.state.copy()
        captured = capture_turbine_gas_properties(result, thermo)
        self.assertTrue((gas.state == before).all())
        for item in captured["turbines"].values():
            self.assertEqual(item["status"], "saved", item)
            self.assertEqual(item["composition_mass_fractions"], {"O2": .8, "H2O": .2})
            table = item["property_table"]
            self.assertEqual(len(table["rows"]), 11)
            self.assertEqual(table["composition_policy"], "frozen_saved_inlet")
            self.assertTrue(all(math.isclose(row["pressure_pa"], 5e6, rel_tol=1e-12) for row in table["rows"]))
            self.assertNotEqual(table["rows"][0]["cp_j_kg_k"], table["rows"][-1]["cp_j_kg_k"])
            for row in table["rows"]:
                self.assertAlmostEqual(row["gamma"], row["cp_j_kg_k"] / row["cv_j_kg_k"])
                self.assertGreater(row["viscosity_pa_s"], 0)
                self.assertGreater(row["thermal_conductivity_w_m_k"], 0)
            self.assertAlmostEqual(item["endpoint_properties"]["outlet"]["pressure_pa"], 1e6)
        self.assertNotIn("turbine_gas_properties", result)

    def test_get_never_calls_thermo_or_recaptures_historical_samples(self):
        result = fixture()
        with patch.object(thermo, "_get_gas", side_effect=AssertionError("must not compute on GET")):
            self.assertTrue(first(result)["ready_for_global_setup"])
            self.assertEqual(capture_turbine_gas_properties(result, thermo), result["turbine_gas_properties"])
            legacy = deepcopy(result)
            del legacy["turbine_gas_properties"]
            self.assertEqual(first(legacy)["gas"]["property_table"]["rows"], [])

    def test_legacy_state_never_becomes_total_from_parameter_defaults(self):
        result = fixture(capture=False)
        calc = result["calculation"]
        for comp in ("ox_preburner_turbine", "fuel_preburner_turbine"):
            calc["components"][comp]["outputs"].pop("state_basis")
            calc["components"][comp]["outputs"].pop("state_assumption")
        t = first(result)
        self.assertEqual(t["status"], "incomplete")
        self.assertFalse(t["ready_for_global_setup"])
        self.assertIsNone(field(t, "total_pressure_in_pa")["value"])
        self.assertIsNone(field(t, "total_temperature_in_k")["value"])
        self.assertEqual(t["design_point"]["pressure_in_pa"], 5e6)
        self.assertIsNone(field(t, "total_to_static_pressure_ratio")["value"])
        self.assertEqual(t["gas"]["property_table"]["rows"], [])

    def test_unspecified_new_result_keeps_samples_without_promoting_boundary(self):
        t = first(fixture(basis="unspecified"))
        self.assertEqual(t["status"], "incomplete")
        self.assertEqual(t["gas"]["status"], "saved")
        self.assertIsNone(field(t, "total_to_total_pressure_ratio")["value"])

    def test_actual_power_is_not_pq_or_additional_efficiency(self):
        t = first(fixture())
        pq = field(t, "isentropic_power_w")["value"]
        actual = field(t, "actual_gas_power_w", "power_matching")["value"]
        self.assertAlmostEqual(actual / pq, .8)
        self.assertIsNone(field(t, "mechanical_efficiency", "power_matching")["value"])
        self.assertIsNone(field(t, "additional_efficiency", "power_matching")["value"])
        self.assertEqual(field(t, "pump_power_required_w", "power_matching")["value"], actual)

    def test_system_and_saved_node_conflicts_block_ready(self):
        mutations = [("outputs", "turbine_power_W"), ("outputs", "actual_drop_Jkg"), ("outputs", "turbine_pressure_ratio"),
                     ("outputs", "U_mean_actual_ms"), ("inlet", "pressure"), ("outlet", "temperature"), ("outlet", "enthalpy")]
        for target, key in mutations:
            result = fixture()
            calc = result["calculation"]
            obj = calc["components"]["ox_preburner_turbine"]["outputs"] if target == "outputs" else calc["states"]["ox_preburner_outlet" if target == "inlet" else "ox_turbine_exhaust"]
            obj[key] *= 1.05
            with self.subTest(target=target, key=key):
                t = first(result)
                self.assertEqual(t["status"], "conflict", t["checks"])
                self.assertFalse(t["ready_for_global_setup"])

    def test_no_zero_or_default_molar_mass_for_missing_data(self):
        result = fixture(capture=False)
        out = result["calculation"]["components"]["ox_preburner_turbine"]["outputs"]
        for key in ("molar_mass_in", "cp_in_Jkgk", "gamma_in"):
            out.pop(key)
        t = first(result)
        for key in ("molar_mass_kg_kmol", "cp_in_j_kg_k", "gamma_in", "gas_constant_j_kg_k"):
            self.assertIsNone(field(t, key, "gas")["value"])
        self.assertEqual(t["status"], "incomplete")

    def test_composition_is_not_invented_normalized_or_silently_dropped(self):
        for composition in ({}, {"O2": .8}, {"O2": .9, "fake": .1}, {"O2": 1.1, "H2O": -.1}):
            result = fixture(capture=False)
            result["calculation"]["states"]["ox_preburner_outlet"]["composition"] = composition
            captured = capture_turbine_gas_properties(result, thermo)
            self.assertNotEqual(captured["turbines"]["ox"]["status"], "saved")
            self.assertFalse(first(result)["ready_for_global_setup"])

    def test_mechanism_identity_must_be_saved_and_match_actual_file(self):
        for change in ("missing_hash", "bad_hash", "bad_phase", "no_source"):
            result = fixture(capture=False)
            if change == "missing_hash":
                result["environment"].pop("mechanism_sha256")
            elif change == "bad_hash":
                result["environment"]["mechanism_sha256"] = "0" * 64
            elif change == "bad_phase":
                result["environment"]["physics"]["phase"] = "another_phase"
            else:
                result["calculation"]["states"]["ox_preburner_outlet"]["metadata"].pop("thermo_source")
            with self.subTest(change=change):
                captured = capture_turbine_gas_properties(result, thermo)
                self.assertNotEqual(captured["turbines"]["ox"]["status"], "saved")
                self.assertEqual(captured["turbines"]["ox"]["property_table"]["rows"], [])

    def test_tampered_property_table_cannot_be_used(self):
        result = fixture()
        result["turbine_gas_properties"]["turbines"]["ox"]["property_table"]["rows"][0]["cp_j_kg_k"] *= 2
        t = first(result)
        self.assertEqual(t["status"], "conflict")
        self.assertEqual(t["gas"]["status"], "conflict")

    def test_recomputed_capture_hash_does_not_hide_broken_thermodynamic_identities(self):
        for key in ("cp_j_kg_k", "density_kg_m3", "pressure_pa", "temperature_k"):
            result = fixture()
            capture = result["turbine_gas_properties"]["turbines"]["ox"]
            capture["property_table"]["rows"][4][key] *= 2
            capture["capture_sha256"] = _hash({k: v for k, v in capture.items() if k != "capture_sha256"})
            with self.subTest(key=key):
                t = first(result)
                self.assertEqual(t["status"], "conflict")
                self.assertEqual(t["gas"]["status"], "conflict")

    def test_power_imbalance_and_conflicting_boundary_basis_block_ready(self):
        result = fixture()
        result["calculation"]["components"]["ox_pump"]["outputs"]["power_W"] *= 1.2
        t = first(result)
        self.assertEqual(t["status"], "conflict")
        self.assertTrue(any(c["id"] == "shaft_power_balance" and c["status"] == "conflict" for c in t["checks"]))
        result = fixture()
        result["calculation"]["inputs"]["turbine_boundary_basis"] = "unspecified"
        self.assertEqual(first(result)["status"], "conflict")

    def test_actual_library_version_must_match_recorded_environment(self):
        result = fixture(capture=False)
        result["environment"]["dependencies"]["cantera"] = "0.0"
        captured = capture_turbine_gas_properties(result, thermo)
        self.assertTrue(all(c["status"] == "conflict" for c in captured["turbines"].values()))

    def test_snapshot_round_trip_and_tamper_detection(self):
        result = fixture()
        result["turbine_design"] = turbine_design_view(result, "old-id")
        expected = deepcopy(result["turbine_design"]["turbines"]["ox"])
        expected["source"]["record_id"] = "record-1"
        self.assertEqual(first(result), expected)
        # Extra fields on one side can belong to a newer input schema.
        result["submitted_inputs"] = {"future_input_field": 17}
        reread = first(result)
        self.assertEqual(reread["status"], "ready")
        self.assertEqual(reread["global_fields"], expected["global_fields"])
        self.assertTrue(all(c["status"] != "conflict" for c in reread["checks"]))
        result["turbine_design"]["turbines"]["ox"]["global_fields"][0]["value"] *= 2
        self.assertEqual(first(result)["status"], "conflict")

    def test_json_save_read_preserves_machine_names_sources_and_hashes(self):
        names = {"ffsc": {"ox": "富氧涡轮", "fuel": "富燃涡轮"},
                 "single_shaft": {"shared": "燃气发生器共轴涡轮"},
                 "dual_shaft": {"ox": "氧泵轴涡轮（GG 燃气）", "fuel": "燃料泵轴涡轮（GG 燃气）"}}
        for layout, expected in names.items():
            result = fixture(layout=layout)
            result["turbine_geometry"] = turbine_geometry_view(result)
            result["turbine_design"] = turbine_design_view(result, "before-save")
            frozen = deepcopy(result)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "saved-result.json"
                path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                loaded = json.loads(path.read_text(encoding="utf-8"))
            reread = turbine_design_view(loaded, "after-load")
            with self.subTest(layout=layout):
                self.assertEqual(reread["source_fingerprint_version"], 1)
                self.assertEqual(reread["source_sha256"], result["turbine_design"]["source_sha256"])
                self.assertEqual(set(reread["turbines"]), set(expected))
                for side, label in expected.items():
                    item = reread["turbines"][side]
                    original = result["turbine_design"]["turbines"][side]
                    self.assertEqual(item["label"], label)
                    self.assertEqual(item["status"], "ready", item.get("reason"))
                    self.assertEqual(item["source"]["record_id"], "after-load")
                    self.assertEqual(item["source"]["component_key"], original["source"]["component_key"])
                    self.assertEqual(item["design_sha256"], original["design_sha256"])
                self.assertEqual(result, frozen)
                self.assertEqual(loaded, frozen)

    def test_geometry_record_id_rebinding_is_presentation_only(self):
        result = fixture()
        result["turbine_geometry"] = turbine_geometry_view(result, "old-display-id")
        result["turbine_design"] = turbine_design_view(result, "old-display-id")
        expected_hash = result["turbine_design"]["source_sha256"]
        result["turbine_geometry"] = turbine_geometry_view(result, "new-display-id")
        view = turbine_design_view(result, "new-display-id")
        self.assertEqual(view["source_sha256"], expected_hash)
        self.assertTrue(all(t["status"] == "ready" for t in view["turbines"].values()))
        # A scientific source change still invalidates the source fingerprint.
        result["turbine_geometry"]["turbines"]["ox"]["dimensions"]["mean_diameter_m"] = 999.
        self.assertTrue(all(not t["ready_for_global_setup"] for t in turbine_design_view(result)["turbines"].values()))

    def test_unconverged_or_failed_sources_never_become_ready(self):
        for failure in ("warning", "inner_residual", "continuation", "required_thermal", "assessment", "assessment_numerical", "stale_summary"):
            result = fixture()
            calc = result["calculation"]
            if failure == "warning":
                calc["summary"]["solver_warning"] = 1.
            elif failure == "inner_residual":
                calc["convergence"]["layers"]["inner"]["checks"] = [{"name": "thrust", "residual": 11., "tolerance": 1.}]
            elif failure == "continuation":
                calc["convergence"]["layers"]["continuation"]["reached_ratio"] = .7
            elif failure == "required_thermal":
                calc["inputs"]["cooling_bartz_mode"] = 1.
            elif failure == "assessment":
                result["assessment"] = {"status": "fail", "numerical": {"status": "pass"}}
            elif failure == "assessment_numerical":
                result["assessment"] = {"status": "unknown", "numerical": {"status": "fail"}}
            else:
                calc["summary"]["numerical_converged"] = 0.
            with self.subTest(failure=failure):
                t = first(result)
                self.assertEqual(t["status"], "conflict", t["checks"])
                self.assertFalse(t["ready_for_global_setup"])

    def test_missing_full_convergence_evidence_is_incomplete_even_with_success_flag(self):
        result = fixture()
        calc = result["calculation"]
        del calc["convergence"]
        # A low inner residual and a stale positive summary do not certify
        # outer loops or reaching the requested full operating point.
        t = first(result)
        self.assertEqual(t["status"], "incomplete")
        self.assertFalse(t["ready_for_global_setup"])
        calc["summary"]["numerical_converged"] = 0.
        calc["summary"]["solver_evidence_incomplete"] = 1.
        self.assertEqual(first(result)["status"], "incomplete")

    def test_shared_input_keys_are_audited_but_single_side_extras_are_not_conflicts(self):
        result = fixture()
        result["submitted_inputs"] = {"turbine_boundary_basis": "total_to_total", "future_extra": 3}
        t = first(result)
        self.assertEqual(t["status"], "ready", t["checks"])
        result["submitted_inputs"]["turbine_boundary_basis"] = "unspecified"
        t = first(result)
        self.assertEqual(t["status"], "conflict")
        check = next(c for c in t["checks"] if c["id"] == "input_snapshot_consistency")
        self.assertEqual(check["conflicting_keys"], ["turbine_boundary_basis"])
        self.assertEqual(t["design_point"]["state_basis"], "total_to_total")
        self.assertEqual(field(t, "total_pressure_in_pa")["value"], 5e6)

    def test_existing_snapshot_cannot_bypass_current_failure_or_input_conflict_gate(self):
        for change in ("input", "convergence", "assessment"):
            result = fixture()
            result["turbine_design"] = turbine_design_view(result)
            if change == "input":
                result["submitted_inputs"] = {"turbine_boundary_basis": "unspecified"}
            elif change == "convergence":
                result["calculation"]["summary"]["solver_warning"] = 1.
            else:
                # Engineering assessment is separate from the saved numerical
                # fingerprint; the ready gate must still be re-applied on GET.
                result["assessment"] = {"status": "fail"}
            with self.subTest(change=change):
                t = first(result)
                self.assertEqual(t["status"], "conflict")
                self.assertFalse(t["ready_for_global_setup"])
                self.assertTrue(any(c["id"].startswith("source_") or c["id"] == "input_snapshot_consistency" for c in t["checks"]))

    def test_stage_handoff_distinguishes_enabled_kinematics_from_missing_aerodynamics(self):
        result = fixture()
        inputs = result["calculation"]["inputs"]
        inputs.update(turbine_meanline_mode="equal_work", turb_ox_meanline_flow_coefficient=.7,
                      turb_ox_reaction_degree=.45, turb_fuel_meanline_flow_coefficient=.7, turb_fuel_reaction_degree=.45)
        t = first(result)
        self.assertEqual(t["meanline"]["status"], "kinematic_only")
        self.assertEqual(t["status"], "ready", t["checks"])
        self.assertIn("已按", t["stage_handoff"]["note"])
        self.assertIn("速度三角形", t["stage_handoff"]["note"])
        self.assertIn("运动学", t["stage_handoff"]["note"])
        self.assertIn("逐级气动解", " ".join(t["stage_handoff"]["missing_requirements"]))
        self.assertIsNone(next(f for f in t["stage_handoff"]["fields"] if f["key"] == "per_stage_pressure_ratio")["value"])

    def test_saved_design_cannot_mask_changed_source_record(self):
        result = fixture()
        result["turbine_design"] = turbine_design_view(result)
        result["calculation"]["states"]["ox_preburner_outlet"]["pressure"] *= 2
        self.assertTrue(all(t["status"] == "conflict" for t in turbine_design_view(result)["turbines"].values()))

    def test_geometry_is_checked_against_the_same_saved_point(self):
        result = fixture(basis="unspecified")
        result["turbine_geometry"] = turbine_geometry_view(result)
        self.assertTrue(result["turbine_geometry"]["turbines"]["ox"]["available"])
        # Keep geometry and capture snapshots frozen; mutate a source output.
        result["calculation"]["components"]["ox_preburner_turbine"]["outputs"]["shaft_speed_rpm"] *= 1.1
        t = first(result)
        self.assertEqual(t["status"], "conflict")
        self.assertTrue(any(c["id"] == "geometry_shaft_speed_rpm" and c["status"] == "conflict" for c in t["checks"]))

    def test_malformed_and_nonfinite_records_serialize_safely(self):
        for result in (None, {}, {"calculation": {"components": []}}, {"turbine_design": {}},
                       {"calculation": {"inputs": {"cycle_type": "gg", "gg_layout": "single_shaft"}}}):
            view = turbine_design_view(result)
            self.assertTrue(view["turbines"])
            self.assertTrue(all(not t["ready_for_global_setup"] for t in view["turbines"].values()))
            json.dumps(view, allow_nan=False)
        result = fixture(capture=False)
        result["calculation"]["components"]["ox_preburner_turbine"]["outputs"]["mass_flow_kgs"] = float("nan")
        self.assertIsNone(first(result)["design_point"]["mass_flow_kg_s"])
        json.dumps(turbine_design_view(result), allow_nan=False)


class GasGeneratorDesignTests(unittest.TestCase):
    def test_single_shaft_has_one_real_turbine_and_both_pump_demands(self):
        result = fixture(layout="single_shaft")
        turbines = turbine_design_view(result)["turbines"]
        self.assertEqual(set(turbines), {"shared"})
        t = turbines["shared"]
        self.assertEqual(t["status"], "ready", t["checks"])
        self.assertEqual(set(t["power_matching"]["pump_component_keys"]), {"ox_pump", "fuel_pump"})
        self.assertAlmostEqual(field(t, "delivered_power_w", "power_matching")["value"], .96 * field(t, "actual_gas_power_w", "power_matching")["value"])
        self.assertIsNone(field(t, "additional_efficiency")["value"])

    def test_parallel_uses_branch_flows_and_each_branch_exhaust(self):
        result = fixture(layout="dual_shaft")
        turbines = turbine_design_view(result)["turbines"]
        self.assertEqual(set(turbines), {"ox", "fuel"})
        for side, mdot in (("ox", 8), ("fuel", 12)):
            t = turbines[side]
            self.assertEqual(t["status"], "ready", t["checks"])
            self.assertEqual(field(t, "mass_flow_kg_s")["value"], mdot)
            self.assertEqual(t["source"]["outlet_state_key"], "gg_turbine_" + side + "_exhaust")

    def test_historical_parallel_mixed_exhaust_cannot_replace_missing_branch(self):
        result = fixture(layout="dual_shaft", capture=False)
        states = result["calculation"]["states"]
        states["gg_turbine_exhaust"] = deepcopy(states.pop("gg_turbine_ox_exhaust"))
        states["gg_turbine_exhaust"]["mass_flow"] = 20
        t = first(result)
        self.assertEqual(t["status"], "incomplete")
        self.assertIsNone(t["design_point"]["outlet_enthalpy_j_kg"])

    def test_missing_parallel_branch_flow_is_missing_not_assumed_zero(self):
        result = fixture(layout="dual_shaft", capture=False)
        result["calculation"]["components"]["gg_turbine_fuel"]["outputs"].pop("mass_flow_kgs")
        t = first(result)
        check = next(c for c in t["checks"] if c["id"] == "parallel_mass")
        self.assertEqual(check["status"], "missing")

    def test_tampered_shared_snapshot_keeps_single_machine_topology(self):
        result = fixture(layout="single_shaft")
        result["turbine_design"] = turbine_design_view(result)
        result["calculation"]["inputs"]["gg_mech_efficiency"] = .5
        turbines = turbine_design_view(result)["turbines"]
        self.assertEqual(set(turbines), {"shared"})
        self.assertEqual(turbines["shared"]["status"], "conflict")


@unittest.skipUnless(thermo.CANTERA_AVAILABLE and thermo.COOLPROP_AVAILABLE, "Real scientific libraries are required")
class RealSavedPipelineTests(unittest.TestCase):
    def test_complete_calculate_consistency_json_save_get_roundtrip(self):
        from engine_presets import preset_input_values
        from webapp.solver import calculate
        from webapp.result_consistency import result_consistency_view
        values = preset_input_values("tq12a_gg")
        values.update(gg_layout="single_shaft", cooling_bartz_mode=0., cooling_heat_load=10e6,
                      turbine_boundary_basis="total_to_total", turbine_meanline_mode="disabled")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = calculate(values)
        before = deepcopy(result)
        self.assertEqual(result["turbine_design"]["turbines"]["shared"]["status"], "ready")
        # The downstream audit and each view must not modify source evidence.
        result_consistency_view(result, "audit-id")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "real-saved-result.json"
            path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
        reread = turbine_design_view(loaded, "real-record-id")
        item = reread["turbines"]["shared"]
        self.assertEqual(item["status"], "ready", item.get("reason", item.get("checks")))
        self.assertEqual(item["label"], "燃气发生器共轴涡轮")
        self.assertEqual(item["source"]["component_key"], "gg_turbine")
        self.assertEqual(item["source"]["record_id"], "real-record-id")
        self.assertEqual(reread["source_sha256"], result["turbine_design"]["source_sha256"])
        self.assertEqual(result, before)
        self.assertEqual(loaded, before)


if __name__ == "__main__":
    unittest.main()
