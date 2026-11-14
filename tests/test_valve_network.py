"""Independent pressure/mass checks and real-property valve topology handoffs."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.components.valves import evaluate_valve, parse_configuration, template_configuration
from lre_workbench.systems.steady_network import BRANCH_IDS, audit_network, solve_design
from lre_workbench.systems.valve_network import audit_valve_network


PAPER = "paper_split_before_cooling_v1"
ALL_COOLED = "all_fuel_cooled_then_split_valves_v1"


def valve_config(topology=PAPER, mode="requirement_sizing"):
    result = template_configuration(topology, mode)
    for valve in result["valves"].values():
        valve["design_pressure_drop_Pa"] = 2e6 if valve["id"] == "MFV" else 1e6
        valve["max_pressure_drop_ratio"] = .3
        if mode == "candidate_check":
            valve["rated_cda_m2"] = 1e-4
    return parse_configuration(result)


def network(config=None, **changes):
    budgets = {key: dict(injector_ratio=.1, line_dp_Pa=.5e6) for key in BRANCH_IDS}
    # A different fuel-main injector budget makes the all-cooled installation
    # feasible without placing an invented fourth valve on oxygen's main leg.
    if config and config.get("topology_id") == ALL_COOLED:
        budgets["fuel_to_fpb"]["injector_ratio"] = .3
    args = dict(total_fuel=10., total_ox=30., mr_ox=9., mr_fuel=.5,
                p_pre_ox=50e6, p_pre_fuel=45e6, injector_ratio=None, line_dp=None,
                branch_budgets=budgets, densities=dict(fuel=500., ox=1100.),
                pump_densities=dict(fuel=500., ox=1100.), temperatures=dict(fuel=110., ox=80.),
                cooling_drop_Pa=5e6, valve_configuration=config)
    return solve_design(**(args | changes))


def branches(result):
    return {row["id"]: row for row in result["branches"]}


class ExplicitValveNetworkTests(unittest.TestCase):
    def test_initial_reserve_is_a_lower_bound_not_equal_drop_for_every_valve(self):
        config = parse_configuration(template_configuration(PAPER, chamber_pressure_Pa=33e6))
        result = network(config)
        rows = branches(result)
        self.assertEqual(result["status"], "pass", result.get("errors"))
        # The OPFV branch sets fuel pump pressure: 50*1.1 + .5 + .33 = 55.83 MPa.
        # MFV gets the remaining .83 MPa after 50 MPa main demand and 5 MPa cooling.
        self.assertAlmostEqual(result["pump_pressures_Pa"]["fuel"], 55.83e6)
        self.assertAlmostEqual(rows["fuel_to_opb"]["valve_drop_Pa"], .33e6)
        self.assertAlmostEqual(result["mfv_drop_Pa"], .83e6)
        self.assertAlmostEqual(rows["ox_to_fpb"]["valve_drop_Pa"], 5.5e6)
        self.assertEqual(rows["ox_to_opb"]["valve_drop_Pa"], 0)

    def test_paper_pressure_chain_and_exact_mass_partition(self):
        result = network(valve_config())
        rows = branches(result)
        self.assertEqual(result["status"], "pass", result.get("errors"))
        # Hand elimination: fuel_OPB = (30 - .5*10)/(9 - .5).
        for key, expected in {"fuel_to_opb": 50/17, "fuel_to_fpb": 120/17,
                              "ox_to_opb": 450/17, "ox_to_fpb": 60/17}.items():
            self.assertAlmostEqual(rows[key]["mass_flow_kg_s"], expected)
        self.assertEqual(rows["fuel_to_opb"]["source"], "fuel_after_pump")
        self.assertEqual(rows["fuel_to_fpb"]["source"], "fuel_after_cooling")
        self.assertAlmostEqual(result["cooling_mass_flow_kg_s"], 120/17)
        # Fuel: max(45*1.1+.5 + 5 + 2, 50*1.1+.5 + 1) = 57 MPa.
        self.assertAlmostEqual(result["pump_pressures_Pa"]["fuel"], 57e6)
        self.assertAlmostEqual(result["tap_pressures_Pa"]["fuel"], 50e6)
        self.assertAlmostEqual(result["mfv_drop_Pa"], 2e6)
        self.assertAlmostEqual(rows["fuel_to_opb"]["valve_drop_Pa"], 1.5e6)
        self.assertAlmostEqual(rows["fuel_to_fpb"]["valve_drop_Pa"], 0.)
        self.assertAlmostEqual(result["pump_pressures_Pa"]["ox"], 55.5e6)
        self.assertAlmostEqual(rows["ox_to_opb"]["valve_drop_Pa"], 0.)
        self.assertAlmostEqual(rows["ox_to_fpb"]["valve_drop_Pa"], 5.5e6)

    def test_all_cooled_mfv_carries_total_fuel_and_opfv_uses_cooled_tap(self):
        result = network(valve_config(ALL_COOLED))
        rows = branches(result)
        self.assertEqual(result["status"], "pass", result.get("errors"))
        self.assertEqual(result["cooling_mass_flow_kg_s"], 10.)
        self.assertEqual(rows["fuel_to_opb"]["source"], "fuel_after_cooling")
        # Fuel-main demand = 45*1.3+.5 = 59; pump = 59+5+2 = 66 MPa.
        self.assertAlmostEqual(result["tap_pressures_Pa"]["fuel"], 59e6)
        self.assertAlmostEqual(result["pump_pressures_Pa"]["fuel"], 66e6)
        self.assertAlmostEqual(rows["fuel_to_opb"]["valve_drop_Pa"], 3.5e6)
        self.assertAlmostEqual(rows["fuel_to_fpb"]["valve_drop_Pa"], 0.)
        self.assertAlmostEqual(rows["ox_to_opb"]["valve_drop_Pa"], 0.)
        self.assertAlmostEqual(rows["ox_to_fpb"]["valve_drop_Pa"], 5.5e6)

    def test_oxygen_main_head_is_not_increased_to_hide_negative_fpov_loss(self):
        result = network(valve_config(), p_pre_fuel=60e6)
        rows = branches(result)
        self.assertEqual(result["status"], "fail")
        self.assertAlmostEqual(result["pump_pressures_Pa"]["ox"], 55.5e6)
        self.assertAlmostEqual(rows["ox_to_opb"]["valve_drop_Pa"], 0.)
        self.assertAlmostEqual(rows["ox_to_fpb"]["valve_drop_Pa"], -11e6)
        self.assertTrue(any("ox_to_fpb" in reason for reason in result["errors"]))

    def test_mfv_candidate_area_changes_pump_head_and_opfv_opening(self):
        config = valve_config(mode="candidate_check")
        before = network(config)
        wider = deepcopy(config)
        wider["valves"]["MFV"]["rated_cda_m2"] *= 1.4
        after = network(wider)
        self.assertEqual(before["status"], "pass")
        self.assertEqual(after["status"], "pass")
        # Independent incompressible CdA law at the supplied pump density.
        expected_loss = ((120/17) / (1e-4*.7))**2 / (2*500.)
        self.assertAlmostEqual(before["mfv_drop_Pa"], expected_loss)
        self.assertAlmostEqual(after["mfv_drop_Pa"], expected_loss/1.4**2)
        self.assertAlmostEqual(before["pump_pressures_Pa"]["fuel"], 55e6+expected_loss)
        self.assertLess(after["pump_pressures_Pa"]["fuel"], before["pump_pressures_Pa"]["fuel"])
        points = []
        for state in (before, after):
            branch = branches(state)["fuel_to_opb"]
            point = evaluate_valve(config["valves"]["OPFV"], mass_flow_kg_s=50/17,
                                   upstream_pressure_Pa=state["pump_pressures_Pa"]["fuel"],
                                   upstream_temperature_K=110., downstream_pressure_Pa=55.5e6)
            self.assertEqual(point["status"], "feasible", point)
            self.assertAlmostEqual(point["pressure_drop_Pa"], branch["valve_drop_Pa"])
            points.append(point)
        self.assertGreater(points[1]["opening"], points[0]["opening"])
        self.assertEqual(before["source_totals_kg_s"], after["source_totals_kg_s"])

    def test_disabled_legacy_results_are_identical_and_no_inferred_entities(self):
        legacy = network()
        disabled = network(parse_configuration(""))
        self.assertEqual(legacy, disabled)
        self.assertEqual(legacy["mode"], "design")
        self.assertEqual(legacy["topology"], "ffsc_all_fuel_cooled_then_split_v1")
        self.assertNotIn("valve_configuration", legacy)
        self.assertNotIn("pump_pressures_Pa", legacy)

    def test_saved_equations_reject_source_loss_mass_and_cooling_tampering(self):
        baseline = network(valve_config())
        mutations = {
            "source": lambda n: n["branches"][0].update(source="fuel_after_cooling"),
            "source_pressure": lambda n: n["branches"][0].update(source_pressure_Pa=58e6),
            "valve_loss": lambda n: n["branches"][0].update(valve_drop_Pa=2.5e6),
            "total_mass": lambda n: n["source_totals_kg_s"].update(fuel=11.),
            "cooling_mass": lambda n: n.update(cooling_mass_flow_kg_s=10.),
            "oxygen_pump_pressure": lambda n: n["pump_pressures_Pa"].update(ox=56e6),
            "nan_mfv": lambda n: n.update(mfv_drop_Pa=float("nan")),
            "configuration_hash": lambda n: n["valve_configuration"]["valves"]["MFV"].update(characteristic_version="changed"),
        }
        for label, mutate in mutations.items():
            altered = deepcopy(baseline)
            mutate(altered)
            with self.subTest(label=label):
                self.assertEqual(audit_valve_network(altered)["status"], "fail")

    def test_external_mass_and_mixture_ratio_contracts_are_not_ignored(self):
        result = network(valve_config())
        for values in (dict(total_fuel=11., total_ox=30., mr_ox=9., mr_fuel=.5),
                       dict(total_fuel=10., total_ox=31., mr_ox=9., mr_fuel=.5),
                       dict(total_fuel=10., total_ox=30., mr_ox=10., mr_fuel=.5),
                       dict(total_fuel=10., total_ox=30., mr_ox=9., mr_fuel=.6)):
            with self.subTest(values=values):
                self.assertEqual(audit_network(result, **values)["status"], "fail")


class RealValveCycleHandoffTests(unittest.TestCase):
    def test_two_topologies_deliver_correct_bypass_enthalpy_and_cooling_mass(self):
        from lre_workbench.properties.thermochemistry import CANTERA_AVAILABLE, COOLPROP_AVAILABLE
        if not (CANTERA_AVAILABLE and COOLPROP_AVAILABLE):
            self.skipTest("Cantera and CoolProp are required for the real physical handoff")
        from lre_workbench.domain.models import EngineInputs
        from lre_workbench.systems.ffsc import FFSCCycleModel
        for topology in (PAPER, ALL_COOLED):
            config = valve_config(topology)
            inputs = EngineInputs(cooling_bartz_mode=0, cooling_heat_load=1e6,
                                  use_target_preburner_temp=0, use_target_preburner_mr=1,
                                  fuel_to_fpb_injector_dp_ratio=.3,
                                  valve_configuration_json=json.dumps(config))
            with self.subTest(topology=topology), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = FFSCCycleModel(inputs)
                result = model._evaluate_once(model._initial_guess_vector()[0], 1.6, 1.6)
                states = result.states
                network = result.steady_closure["feed_network"]
                valve_design = result.steady_closure["valve_design"]
                self.assertEqual(network["status"], "pass", network.get("errors"))
                self.assertEqual(valve_design["status"], "pass", valve_design)
                pump, cooling_in, cooled = (states[k] for k in ("fuel_after_pump", "cooling_inlet", "fuel_after_cooling"))
                opb, fpb = (states[k] for k in ("branch_fuel_to_opb", "branch_fuel_to_fpb"))
                expected_mass = fpb.mass_flow if topology == PAPER else pump.mass_flow
                self.assertAlmostEqual(cooling_in.mass_flow, expected_mass)
                self.assertAlmostEqual(cooled.mass_flow, expected_mass)
                self.assertAlmostEqual(cooling_in.enthalpy, pump.enthalpy)
                self.assertAlmostEqual(opb.enthalpy, pump.enthalpy if topology == PAPER else cooled.enthalpy)
                self.assertAlmostEqual(fpb.enthalpy, cooled.enthalpy)
                self.assertAlmostEqual(expected_mass*(cooled.enthalpy-cooling_in.enthalpy), 1e6, delta=.001)
                self.assertAlmostEqual(opb.mass_flow*opb.enthalpy + fpb.mass_flow*fpb.enthalpy,
                                       pump.mass_flow*pump.enthalpy+1e6, delta=.001)
                from lre_workbench.components.chambers.design import snapshot_from_results
                snapshot = snapshot_from_results(result)
                self.assertIsNotNone(snapshot)
                self.assertAlmostEqual(snapshot.coolant_in_T_K, cooling_in.temperature)
                self.assertAlmostEqual(snapshot.coolant_in_p_Pa, cooling_in.pressure)
                self.assertAlmostEqual(snapshot.mdot_coolant, expected_mass)
                self.assertAlmostEqual(snapshot.mdot_fuel, pump.mass_flow)
                missing_port = deepcopy(result)
                del missing_port.states["cooling_inlet"]
                self.assertIsNone(snapshot_from_results(missing_port))
                audits = {row["id"]: row for row in result.steady_closure["energy_balance"]["checks"]}
                for key in ("regenerative_coolant_energy", "MFV_throttle_energy", "fuel_split_energy",
                            "branch_fuel_to_opb_throttle_energy", "branch_fuel_to_fpb_throttle_energy"):
                    self.assertEqual(audits[key]["status"], "pass", audits[key])
                # An arbitrary one-pass operating trial is not asserted to have
                # closed the two shafts; the test checks physical handoffs only.
                for point in valve_design["valves"].values():
                    source_key = point["inlet_state_source"].removeprefix("states.")
                    self.assertIn(source_key, states)
                    self.assertAlmostEqual(point["upstream_pressure_Pa"], states[source_key].pressure)
                    self.assertIn(point["upstream_node"], states)
                    self.assertIn(point["downstream_node"], states)
                    self.assertAlmostEqual(point["downstream_pressure_Pa"], states[point["downstream_node"]].pressure)
                json.dumps(asdict(result), allow_nan=False)
                from webapp.design_traceability import capture_design_traceability
                from webapp.valve_design import valve_design_view
                saved = {"calculation": asdict(result), "submitted_inputs": asdict(inputs)}
                record = {"id": "one-pass-"+topology, "project_id": "valve-integration-test", "revision": 1,
                          "mode": "concept_design", "inputs": asdict(inputs), "result": saved}
                capture_design_traceability(saved, record)
                report = valve_design_view(saved, record)
                self.assertEqual(report["status"], "recorded", report["issues"])
                self.assertEqual(report["traceability_status"], "verified", report["issues"])


if __name__ == "__main__":
    unittest.main()
