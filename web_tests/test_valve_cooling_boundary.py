"""MFV/cooling boundary projection must not fall back to total pump flow."""
from copy import deepcopy
import json
import unittest

from webapp.cooling_design import valve_cooling_boundary_checks
from webapp.result_consistency import result_consistency_view
from lre_workbench.components.valves import template_configuration, topology_hash


def fixture(topology="paper_split_before_cooling_v1"):
    config = template_configuration(topology)
    for row in config["valves"].values():
        row["design_pressure_drop_Pa"] = 1e6
    mass = 80 if topology == "paper_split_before_cooling_v1" else 100
    return {"inputs": {"cycle_type": "ffsc", "valve_configuration_json": json.dumps(config)},
            "states": {"cooling_inlet": {"pressure": 60e6, "temperature": 120, "mass_flow": mass},
                       "fuel_after_pump": {"pressure": 65e6, "temperature": 125, "mass_flow": 100},
                       "fuel_after_cooling": {"pressure": 50e6, "temperature": 500, "mass_flow": mass},
                       "branch_fuel_to_fpb": {"mass_flow": 80}},
            "steady_closure": {"valve_design": {"configuration": config, "topology_hash": topology_hash(config),
                                                "valves": {"MFV": {"mass_flow_kg_s": mass}}},
                               "feed_network": {"cooling_mass_flow_kg_s": mass}},
            "components": {"cooling_channel": {"profile": {"model_id": "regen_quasi1d_ph_v1",
                "nodes": [{"p_Pa": 60e6, "T_K": 120},
                          {"kind": "channel", "rho_kgm3": 500, "velocity_ms": mass/5, "A_flow_m2": .01}]}}}}


class ValveCoolingBoundaryTests(unittest.TestCase):
    def test_both_topologies_compare_their_actual_coolant_branch_without_mutation(self):
        for topology in ("paper_split_before_cooling_v1", "all_fuel_cooled_then_split_valves_v1"):
            calc = fixture(topology)
            before = deepcopy(calc)
            checks = valve_cooling_boundary_checks(calc)
            self.assertTrue(checks)
            self.assertTrue(all(check["status"] == "pass" for check in checks), checks)
            self.assertEqual(calc, before)

    def test_using_pump_state_or_total_fuel_in_paper_profile_is_a_conflict(self):
        changes = [lambda c: c["components"]["cooling_channel"]["profile"]["nodes"][0].update(p_Pa=65e6),
                   lambda c: c["components"]["cooling_channel"]["profile"]["nodes"][0].update(T_K=125),
                   lambda c: c["components"]["cooling_channel"]["profile"]["nodes"][1].update(velocity_ms=20)]
        for change in changes:
            calc = fixture(); change(calc)
            self.assertTrue(any(c["status"] == "conflict" for c in valve_cooling_boundary_checks(calc)))

    def test_missing_cooling_port_is_partial_and_never_replaced_by_pump_port(self):
        calc = fixture(); del calc["states"]["cooling_inlet"]
        self.assertTrue(all(c["status"] == "partial" for c in valve_cooling_boundary_checks(calc)))

    def test_legacy_records_do_not_acquire_valve_checks(self):
        self.assertEqual(valve_cooling_boundary_checks({"inputs": {}}), [])

    def test_result_consistency_includes_the_saved_boundary_audit(self):
        calc = fixture()
        calc["states"]["fuel_after_cooling"]["mass_flow"] = 100
        report = result_consistency_view({"calculation": calc})
        check = next(c for c in report["checks"] if c["id"] == "cooling.valve_boundary.mass_flow")
        self.assertEqual(check["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
