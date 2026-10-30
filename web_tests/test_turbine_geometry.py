"""Axial turbine envelopes preserve saved points, continuity and provenance."""
from copy import deepcopy
import json
import math
import unittest

from webapp.turbine_geometry import ASSUMPTION_KEYS, R_UNIVERSAL, _digest, turbine_geometry_view


def fixture(mdot=20., dm=.3, rpm=20000., stages=2, pin=4e6, pout=1e6, tin=1000., tout=800.):
    dhs, eta = 200000., .75
    u = math.pi * dm * rpm / 60
    output = {"mass_flow_kgs": mdot, "D_mean_m": dm, "shaft_speed_rpm": rpm,
              "U_mean_actual_ms": u, "n_stages_recommended": stages,
              "p_in_Pa": pin, "p_out_Pa": pout, "T_in_K": tin, "T_exhaust_K": tout,
              "molar_mass_in": 20., "cp_in_Jkgk": 1700., "gamma_in": 1.32,
              "turbine_power_W": mdot * dhs * eta, "actual_drop_Jkg": dhs * eta,
              "isentropic_drop_Jkg": dhs, "turbine_pressure_ratio": pin / pout,
              "eta_turbine": eta, "C0_spouting_ms": math.sqrt(2 * dhs),
              "u_over_c_target": .55, "u_over_c_actual": u / math.sqrt(2 * dhs)}
    inputs = {"cycle_type": "ffsc", "propellant_pair": "lox_methane"}
    for prefix in ("ox", "fuel"):
        inputs.update({f"{prefix}_turbine_inlet_flow_coefficient": .6,
                       f"{prefix}_turbine_outlet_flow_coefficient": .7,
                       f"{prefix}_turbine_stator_axial_chord_ratio": .12,
                       f"{prefix}_turbine_rotor_axial_chord_ratio": .15,
                       f"{prefix}_turbine_row_gap_ratio": .04,
                       f"{prefix}_turbine_geometry_state_basis": "system_pt_as_static"})
    return {"calculation": {"inputs": inputs, "components": {
        "ox_preburner_turbine": {"outputs": deepcopy(output)},
        "fuel_preburner_turbine": {"outputs": deepcopy(output)}}}}


def ox(result):
    return turbine_geometry_view(result, "record-a")["turbines"]["ox"]


class TurbineGeometryTests(unittest.TestCase):
    def test_two_turbines_are_record_bound_without_mutating_result(self):
        result = fixture()
        original = deepcopy(result)
        view = turbine_geometry_view(result, "record-a")
        self.assertEqual(view["schema_version"], 1)
        self.assertEqual(set(view["turbines"]), {"ox", "fuel"})
        for side, turbine in view["turbines"].items():
            self.assertTrue(turbine["available"], turbine)
            self.assertEqual(turbine["source"]["record_id"], "record-a")
            self.assertEqual(turbine["source"]["component_key"], side + "_preburner_turbine")
            self.assertEqual(len(turbine["meridional"]["hub"]), 65)
            self.assertEqual(turbine["meridional"]["unit"], "m")
            self.assertEqual(turbine["meridional"]["rotation_axis"], "z")
            self.assertEqual(turbine["checks"][-1]["status"], "not_evaluated")
        self.assertEqual(result, original)
        json.dumps(view, allow_nan=False)

    def test_endpoints_close_mass_continuity_with_distinct_densities(self):
        turbine = ox(fixture())
        d, p, m = turbine["dimensions"], turbine["design_point"], turbine["meridional"]
        self.assertNotEqual(p["density_in_kg_m3"], p["density_out_kg_m3"])
        self.assertAlmostEqual(p["gas_constant_j_kg_k"], R_UNIVERSAL / 20)
        for end, short, i in (("inlet", "in", 0), ("outlet", "out", -1)):
            rh, rs = m["hub"][i][1], m["shroud"][i][1]
            area = math.pi * (rs * rs - rh * rh)
            self.assertAlmostEqual(area, d[f"{end}_area_m2"])
            self.assertAlmostEqual(area * d[f"{end}_axial_velocity_m_s"] * p[f"density_{short}_kg_m3"], p["mass_flow_kg_s"])
            self.assertAlmostEqual(rs + rh, d["mean_diameter_m"])
            self.assertAlmostEqual(rs - rh, d[f"{end}_blade_height_m"])
            self.assertAlmostEqual(2 * rs, d[f"{end}_shroud_diameter_m"])
            self.assertAlmostEqual(2 * rh, d[f"{end}_hub_diameter_m"])
        self.assertAlmostEqual(d["maximum_annulus_tip_speed_m_s"], math.pi * d["outlet_shroud_diameter_m"] * p["shaft_speed_rpm"] / 60)
        self.assertGreater(d["maximum_annulus_tip_speed_m_s"], p["mean_speed_m_s"])

    def test_rows_are_nonoverlapping_envelopes_on_the_canonical_boundaries(self):
        t = ox(fixture(stages=3))
        d, rows, m = t["dimensions"], t["rows"], t["meridional"]
        self.assertEqual(len(rows), 6)
        self.assertAlmostEqual(d["axial_length_m"], 3 * (d["stator_axial_chord_m"] + d["rotor_axial_chord_m"]) + 5 * d["row_gap_m"])
        for i, row in enumerate(rows):
            self.assertEqual(row["stage"], i // 2 + 1)
            self.assertEqual(row["kind"], "stator" if i % 2 == 0 else "rotor")
            self.assertAlmostEqual(row["z_end_m"] - row["z_start_m"], d[row["kind"] + "_axial_chord_m"])
            if i:
                self.assertAlmostEqual(row["z_start_m"] - rows[i - 1]["z_end_m"], d["row_gap_m"])
            for wall in ("hub", "shroud"):
                r0, r1 = m[wall][0][1], m[wall][-1][1]
                for z, r in row[wall]:
                    self.assertAlmostEqual(r, r0 + (r1 - r0) * z / d["axial_length_m"])
        self.assertEqual(rows[0]["z_start_m"], 0)
        self.assertAlmostEqual(rows[-1]["z_end_m"], d["axial_length_m"])
        for hp, sp in zip(m["hub"], m["shroud"]):
            self.assertEqual(hp[0], sp[0])
            self.assertGreater(hp[1], 0)
            self.assertGreater(sp[1], hp[1])
        self.assertTrue(all(b[0] > a[0] for a, b in zip(m["hub"], m["hub"][1:])))

    def test_saved_mean_diameter_is_preserved_and_existing_limit_is_explained(self):
        result = fixture(dm=.8, rpm=1000.)
        t = ox(result)
        self.assertTrue(t["available"], t)
        self.assertEqual(t["dimensions"]["mean_diameter_m"], .8)
        self.assertTrue(t["design_point"]["diameter_limited"])
        self.assertGreater(t["design_point"]["target_mean_diameter_m"], .8)
        check = next(c for c in t["checks"] if c["id"] == "diameter_provenance")
        self.assertEqual(check["status"], "warning")
        self.assertIn("0.05–0.80", check["note"])

    def test_geometric_similarity_is_preserved(self):
        factor = 2.3
        a = ox(fixture())
        b = ox(fixture(mdot=20 * factor**2, dm=.3 * factor, rpm=20000 / factor))
        self.assertTrue(b["available"], b)
        for key, value in a["dimensions"].items():
            if key.endswith("_m"):
                self.assertAlmostEqual(b["dimensions"][key], factor * value)
            elif key.endswith("_m2"):
                self.assertAlmostEqual(b["dimensions"][key], factor**2 * value)
            else:
                self.assertAlmostEqual(b["dimensions"][key], value)

    def test_missing_each_assumption_leaves_historical_point_but_no_geometry(self):
        for key in ASSUMPTION_KEYS:
            result = fixture()
            del result["calculation"]["inputs"]["ox_turbine_" + key]
            t = ox(result)
            with self.subTest(key=key):
                self.assertFalse(t["available"])
                self.assertIn("旧记录", t["reason"])
                self.assertEqual(t["design_point"]["mass_flow_kg_s"], 20.)
                self.assertEqual(t["dimensions"], {"mean_diameter_m": .3})
                self.assertNotIn("meridional", t)
                self.assertNotIn("rows", t)

    def test_saved_snapshot_is_independent_of_later_inputs_and_outputs(self):
        result = fixture()
        result["turbine_geometry"] = turbine_geometry_view(result, "old-record")
        expected = deepcopy(result["turbine_geometry"]["turbines"]["ox"])
        result["calculation"]["inputs"]["ox_turbine_inlet_flow_coefficient"] = 5
        result["calculation"]["components"] = {"gg_turbine": {"outputs": {}}}
        result["submitted_inputs"] = {"cycle_type": "gg"}
        t = ox(result)
        expected["source"]["record_id"] = "record-a"
        self.assertEqual(t, expected)
        t["meridional"]["hub"][0][1] = 999
        self.assertNotEqual(result["turbine_geometry"]["turbines"]["ox"]["meridional"]["hub"][0][1], 999)

    def test_tampered_snapshot_never_falls_back_to_new_inputs(self):
        for target in ("coordinates", "dimensions", "rows", "source", "assumptions", "point"):
            result = fixture()
            result["turbine_geometry"] = turbine_geometry_view(result)
            t = result["turbine_geometry"]["turbines"]["ox"]
            if target == "coordinates":
                t["meridional"]["hub"][10][1] *= 1.1
            elif target == "dimensions":
                t["dimensions"]["inlet_area_m2"] *= 1.1
            elif target == "rows":
                t["rows"][0]["z_end_m"] *= 1.1
            elif target == "source":
                t["source"]["component_key"] = "gg_turbine_ox"
            elif target == "assumptions":
                t["assumptions"]["inlet_flow_coefficient"] *= 1.1
            else:
                t["design_point"]["pressure_in_pa"] *= 1.1
            with self.subTest(target=target):
                self.assertFalse(ox(result)["available"])
                self.assertTrue("校验值" in ox(result)["reason"] or "部件来源" in ox(result)["reason"])
                if target != "source":
                    self.assertTrue(turbine_geometry_view(result)["turbines"]["fuel"]["available"])

    def test_recomputed_hash_cannot_hide_inconsistent_or_relabelled_geometry(self):
        for target in ("point", "row", "unit", "method", "source"):
            result = fixture()
            result["turbine_geometry"] = turbine_geometry_view(result)
            t = result["turbine_geometry"]["turbines"]["ox"]
            if target == "point":
                t["design_point"]["power_w"] *= 2
            elif target == "row":
                t["rows"][1]["hub"][0][1] *= .9
            elif target == "unit":
                t["meridional"]["unit"] = "mm"
            elif target == "method":
                t["assumptions"]["annulus_model"] = "constant_shroud"
            else:
                t["source"]["component_key"] = "fuel_preburner_turbine"
            t["geometry_sha256"] = _digest(t)
            with self.subTest(target=target):
                self.assertFalse(ox(result)["available"])

    def test_inconsistent_system_identities_are_rejected(self):
        for key in ("U_mean_actual_ms", "D_mean_m", "shaft_speed_rpm", "turbine_power_W", "actual_drop_Jkg",
                    "turbine_pressure_ratio", "C0_spouting_ms", "u_over_c_actual"):
            result = fixture()
            result["calculation"]["components"]["ox_preburner_turbine"]["outputs"][key] *= 1.1
            with self.subTest(key=key):
                self.assertFalse(ox(result)["available"])
                self.assertIn("不一致", ox(result)["reason"])

    def test_nonpositive_nonfinite_or_missing_required_outputs_are_unavailable(self):
        for key in ("mass_flow_kgs", "D_mean_m", "shaft_speed_rpm", "n_stages_recommended", "p_in_Pa", "p_out_Pa",
                    "T_in_K", "T_exhaust_K", "molar_mass_in", "turbine_power_W", "actual_drop_Jkg", "isentropic_drop_Jkg"):
            for bad in (None, 0, -1, float("nan"), float("inf"), True, "1", 10**500):
                result = fixture()
                result["calculation"]["components"]["ox_preburner_turbine"]["outputs"][key] = bad
                with self.subTest(key=key, bad=bad):
                    t = ox(result)
                    self.assertFalse(t["available"])
                    self.assertNotIn("meridional", t)
                    json.dumps(t, allow_nan=False)

    def test_no_gas_constant_fallback_or_unstated_total_static_conversion(self):
        result = fixture()
        del result["calculation"]["components"]["ox_preburner_turbine"]["outputs"]["molar_mass_in"]
        self.assertFalse(ox(result)["available"])
        for basis in (None, "total", "static", True):
            result = fixture()
            result["calculation"]["inputs"]["ox_turbine_geometry_state_basis"] = basis
            self.assertFalse(ox(result)["available"])

    def test_hub_radius_failure_is_reported_not_clipped(self):
        result = fixture(mdot=1000.)
        t = ox(result)
        self.assertFalse(t["available"])
        self.assertIn("不作截断", t["reason"])
        self.assertEqual(t["design_point"]["mass_flow_kg_s"], 1000.)
        self.assertEqual(t["dimensions"]["mean_diameter_m"], .3)
        self.assertNotIn("rows", t)

    def test_invalid_and_extreme_assumptions_never_publish_partial_geometry(self):
        for key in ASSUMPTION_KEYS[:-1]:
            for bad in (0, -1, True, "1", float("nan"), float("inf"), 1e308, 1e-308, 10**500):
                result = fixture()
                result["calculation"]["inputs"]["ox_turbine_" + key] = bad
                with self.subTest(key=key, bad=bad):
                    t = ox(result)
                    self.assertFalse(t["available"], t)
                    self.assertNotIn("meridional", t)
                    self.assertNotIn("rows", t)
                    json.dumps(t, allow_nan=False)

    def test_invalid_stages_and_pressure_direction_are_rejected(self):
        for stages in (1.5, 0, 7, 1e20):
            self.assertFalse(ox(fixture(stages=stages))["available"])
        self.assertFalse(ox(fixture(pin=1e6, pout=2e6))["available"])

    def test_bad_record_shapes_are_safe(self):
        for result in (None, {}, {"calculation": []}, {"calculation": {"components": []}},
                       {"calculation": {"components": {"ox_preburner_turbine": []}}},
                       {"turbine_geometry": {}}, {"turbine_geometry": {"schema_version": 1, "turbines": None}},
                       {"turbine_geometry": {"schema_version": 1, "turbines": {"shared": {}, "ox": {}}}}):
            with self.subTest(result=result):
                view = turbine_geometry_view(result)
                self.assertTrue(view["turbines"])
                self.assertTrue(all(not t["available"] for t in view["turbines"].values()))
                json.dumps(view, allow_nan=False)


class GasGeneratorTurbineGeometryTests(unittest.TestCase):
    def single(self):
        result = fixture()
        calc = result["calculation"]
        calc["inputs"].update(cycle_type="gg", gg_layout="single_shaft", propellant_pair="lox_rp1")
        output = calc["components"]["fuel_preburner_turbine"]
        calc["components"] = {"gg_turbine": output}
        # Single-shaft source uses fuel-side geometry assumptions, no ox defaults.
        for key in ASSUMPTION_KEYS:
            del calc["inputs"]["ox_turbine_" + key]
        calc["inputs"]["n_fuel_rpm"] = 99999
        calc["inputs"]["n_lox_rpm"] = 20000
        return result

    def test_single_gg_has_one_shared_turbine_using_saved_shaft_speed(self):
        result = self.single()
        view = turbine_geometry_view(result)
        self.assertEqual(set(view["turbines"]), {"shared"})
        t = view["turbines"]["shared"]
        self.assertTrue(t["available"], t)
        self.assertEqual(t["source"]["assumption_prefix"], "fuel")
        self.assertEqual(t["design_point"]["shaft_speed_rpm"], 20000)
        self.assertEqual(t["design_point"]["mass_flow_kg_s"], 20)
        self.assertNotIn("富氧", t["label"])
        result["turbine_geometry"] = view
        self.assertEqual(set(turbine_geometry_view(result)["turbines"]), {"shared"})

    def test_gg_parallel_turbines_use_their_own_flows_not_generator_total(self):
        result = fixture()
        calc = result["calculation"]
        calc["inputs"].update(cycle_type="gg", gg_layout="dual_shaft")
        left = deepcopy(calc["components"]["ox_preburner_turbine"])
        right = deepcopy(calc["components"]["fuel_preburner_turbine"])
        left["outputs"]["mass_flow_kgs"] = 8
        left["outputs"]["turbine_power_W"] *= 8 / 20
        right["outputs"]["mass_flow_kgs"] = 12
        right["outputs"]["turbine_power_W"] *= 12 / 20
        calc["components"] = {"gg_turbine_ox": left, "gg_turbine_fuel": right,
                              "gas_generator": {"outputs": {"mass_flow_kgs": 20}}}
        turbines = turbine_geometry_view(result)["turbines"]
        self.assertEqual(set(turbines), {"ox", "fuel"})
        for side, expected in (("ox", 8), ("fuel", 12)):
            self.assertTrue(turbines[side]["available"], turbines[side])
            self.assertEqual(turbines[side]["design_point"]["mass_flow_kg_s"], expected)
            self.assertNotIn("富氧", turbines[side]["label"])
        self.assertAlmostEqual(turbines["ox"]["dimensions"]["inlet_area_m2"] / turbines["fuel"]["dimensions"]["inlet_area_m2"], 8 / 12)

    def test_ambiguous_shared_and_independent_components_are_not_duplicated(self):
        result = self.single()
        result["calculation"]["components"]["gg_turbine_ox"] = deepcopy(result["calculation"]["components"]["gg_turbine"])
        turbines = turbine_geometry_view(result)["turbines"]
        self.assertTrue(all(not t["available"] for t in turbines.values()))

    def test_mixed_gg_and_ffsc_components_and_snapshots_are_rejected(self):
        result = fixture()
        result["calculation"]["components"]["gg_turbine_ox"] = deepcopy(result["calculation"]["components"]["ox_preburner_turbine"])
        self.assertTrue(all(not t["available"] for t in turbine_geometry_view(result)["turbines"].values()))
        result = fixture()
        result["turbine_geometry"] = turbine_geometry_view(result)
        t = result["turbine_geometry"]["turbines"]["ox"]
        t["source"]["component_key"] = "gg_turbine_ox"
        t["geometry_sha256"] = _digest(t)
        self.assertTrue(all(not t["available"] for t in turbine_geometry_view(result)["turbines"].values()))

    def test_empty_gg_records_keep_requested_topology_and_labels(self):
        for layout, expected in (("single_shaft", {"shared"}), ("dual_shaft", {"ox", "fuel"})):
            result = {"calculation": {"inputs": {"cycle_type": "gg", "gg_layout": layout}}}
            turbines = turbine_geometry_view(result)["turbines"]
            self.assertEqual(set(turbines), expected)
            self.assertTrue(all(not t["available"] for t in turbines.values()))
            self.assertTrue(all("富氧" not in t["label"] for t in turbines.values()))


if __name__ == "__main__":
    unittest.main()
