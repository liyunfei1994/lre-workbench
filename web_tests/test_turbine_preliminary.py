import copy
import json
import math
import unittest

from webapp.turbine_preliminary import _blade_row, _harmonic_screening, compute_turbine_preliminary


class TurbinePreliminaryTests(unittest.TestCase):
    def fixture(self):
        machine = {"available": True, "status": "incomplete", "design_sha256": "saved-digest",
                   "source": {"record_id": "record-a", "component_key": "ox_preburner_turbine"},
                   "design_point": {"mass_flow_kg_s": 20., "shaft_speed_rpm": 20000.,
                                    "actual_drop_j_kg": 180000., "power_w": 3600000., "state_basis": "unspecified"},
                   "checks": [{"id": "boundary_basis", "status": "missing"}]}
        scenario = {"stages": 2, "mean_diameter_m": .3, "flow_coefficient": .6, "reaction": .45,
                    "work_split": "equal", "inlet_swirl_m_s": 0., "stator_axial_chord_m": .02,
                    "rotor_axial_chord_m": .018, "stator_zweifel": .9, "rotor_zweifel": .9}
        return machine, scenario

    def test_conserves_work_power_torque_and_stage_interfaces(self):
        m, s = self.fixture()
        s.update(stages=3, work_split="custom", work_fractions=[.5, .3, .2])
        result = compute_turbine_preliminary(m, s)
        self.assertEqual(result["status"], "preliminary")
        rows = result["stages"]
        self.assertAlmostEqual(sum(r["work_j_kg"] for r in rows), 180000)
        self.assertAlmostEqual(sum(r["power_w"] for r in rows), 3600000)
        self.assertAlmostEqual(sum(r["torque_nm"] for r in rows), result["summary"]["total_torque_nm"])
        for i, row in enumerate(rows):
            self.assertAlmostEqual(row["u_m_s"] * (row["cu1_m_s"] - row["cu2_m_s"]), row["work_j_kg"])
            self.assertAlmostEqual((row["w2_m_s"]**2 - row["w1_m_s"]**2) / 2, row["rotor_static_drop_j_kg"])
            self.assertAlmostEqual(row["stator_static_drop_j_kg"] + row["rotor_static_drop_j_kg"], row["stage_static_drop_j_kg"])
            self.assertAlmostEqual(row["reaction_check"], .45)
            if i:
                self.assertEqual(row["cu0_m_s"], rows[i-1]["cu2_m_s"])

    def test_stage_reaction_is_not_mislabelled_for_first_stage(self):
        m, s = self.fixture()
        out = compute_turbine_preliminary(m, s)
        a, b = out["stages"]
        self.assertNotAlmostEqual(a["reaction_stage"], a["reaction_target"])
        self.assertAlmostEqual(b["reaction_stage"], b["reaction_target"])
        self.assertAlmostEqual(a["reaction_stage"], a["rotor_static_drop_j_kg"] / a["stage_static_drop_j_kg"])

    def test_zweifel_matches_signed_axial_formula_and_integer_recalculation(self):
        m, s = self.fixture()
        out = compute_turbine_preliminary(m, s)
        row = out["stages"][0]
        for kind, angle0, angle1 in (("stator", "alpha0_deg", "alpha1_deg"), ("rotor", "beta1_deg", "beta2_deg")):
            blade = row[kind]
            a, b = map(math.radians, (row[angle0], row[angle1]))
            factor = 2 * math.cos(b)**2 * abs(math.tan(a) - math.tan(b))
            expected = math.pi * s["mean_diameter_m"] * factor / (.9 * s[kind+"_axial_chord_m"])
            self.assertAlmostEqual(blade["blade_count_continuous"], expected)
            self.assertEqual(blade["blade_count_candidate"], math.floor(expected+.5))
            self.assertAlmostEqual(blade["pitch_m"]*blade["blade_count_candidate"], math.pi*.3)
            self.assertAlmostEqual(blade["zweifel_candidate"], factor/blade["axial_solidity"])

    def test_zweifel_reproduces_nasa_reference_count_with_axial_inflow_assumption(self):
        # NASA 19910010813, Table I: Dm=0.46744 m, cx=0.03556 m,
        # exit angle=75 deg, Zweifel=0.794, 26 stator blades,
        # tabulated axial solidity=0.630 (rounded). This test assumes axial
        # inflow (the table does not specify it); it anchors the coefficient
        # scale and integer rounding, not the report's complete flow solution.
        # https://ntrs.nasa.gov/api/citations/19910010813/downloads/19910010813.pdf
        blade = _blade_row(.03556, .794, 0., math.tan(math.radians(75)), 1., .46744)
        self.assertEqual(blade["blade_count_candidate"], 26)
        self.assertAlmostEqual(blade["blade_count_continuous"], 26.006, delta=.002)
        self.assertAlmostEqual(blade["axial_solidity"], .630, delta=.001)

    def test_missing_blade_design_does_not_prevent_triangles_or_invent_counts(self):
        m, s = self.fixture()
        for key in ("stator_axial_chord_m", "rotor_axial_chord_m", "stator_zweifel", "rotor_zweifel"):
            s.pop(key)
        out = compute_turbine_preliminary(m, s)
        self.assertEqual(out["status"], "preliminary")
        for row in out["stages"]:
            self.assertEqual(row["rotor"]["status"], "missing")
            self.assertIsNone(row["stator"]["blade_count_candidate"])

    def test_large_chord_does_not_clamp_invalid_blade_count(self):
        m, s = self.fixture()
        s["rotor_axial_chord_m"] = 100
        row = compute_turbine_preliminary(m, s)["stages"][0]["rotor"]
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row["blade_count_candidate"])
        self.assertLess(row["blade_count_continuous"], 1)

    def test_zero_stator_turning_reports_undefined_candidate(self):
        m, s = self.fixture()
        row = compute_turbine_preliminary(m, s)["stages"][0]
        s["inlet_swirl_m_s"] = row["cu1_m_s"]
        result = compute_turbine_preliminary(m, s)
        self.assertEqual(result["status"], "preliminary")
        self.assertEqual(result["stages"][0]["stator"]["status"], "unavailable")

    def test_negative_stage_static_drop_is_warned_and_not_clamped(self):
        m, s = self.fixture()
        s["inlet_swirl_m_s"] = 2000
        result = compute_turbine_preliminary(m, s)
        self.assertLess(result["stages"][0]["stage_static_drop_j_kg"], 0)
        self.assertIsNone(result["stages"][0]["reaction_stage"])
        self.assertTrue(any(c["status"] == "warning" for c in result["checks"]))

    def test_explicit_range_checks_and_no_numeric_coercion(self):
        for key in ("stages", "mean_diameter_m", "flow_coefficient", "reaction", "inlet_swirl_m_s", "rotor_zweifel", "stator_axial_chord_m"):
            for bad in (True, float("nan"), float("inf"), "0.5"):
                with self.subTest(key=key, bad=bad):
                    m, s = self.fixture()
                    s[key] = bad
                    result = compute_turbine_preliminary(m, s)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertEqual(result["stages"], [])
                    json.dumps(result, allow_nan=False)
        for key, bad in (("stages", 0), ("stages", 2.5), ("stages", 7), ("reaction", -.1), ("reaction", 1.1), ("flow_coefficient", 0)):
            m, s = self.fixture()
            s[key] = bad
            self.assertEqual(compute_turbine_preliminary(m, s)["status"], "unavailable")

    def test_missing_mandatory_inputs_remain_missing(self):
        for key in ("stages", "mean_diameter_m", "flow_coefficient", "reaction", "inlet_swirl_m_s", "work_split"):
            m, s = self.fixture()
            s.pop(key)
            self.assertEqual(compute_turbine_preliminary(m, s)["status"], "unavailable")

    def test_custom_allocation_validation_and_equal_mode_ambiguity(self):
        for fractions in ([.5], [.4, .5], [.4, .7], [0., 1.], [.5, True], "[0.5,0.5]"):
            m, s = self.fixture()
            s.update(work_split="custom", work_fractions=fractions)
            self.assertEqual(compute_turbine_preliminary(m, s)["status"], "unavailable")
        m, s = self.fixture()
        s["work_fractions"] = [.5, .5]
        self.assertEqual(compute_turbine_preliminary(m, s)["status"], "unavailable")

    def test_record_conflicts_and_power_identity_block_calculation(self):
        for mutate in (lambda m: m.update(status="conflict"), lambda m: m["design_point"].update(power_w=1),
                       lambda m: m.update(checks=[{"status": "conflict"}]), lambda m: m.update(available=False)):
            m, s = self.fixture()
            mutate(m)
            result = compute_turbine_preliminary(m, s)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["stages"], [])

    def test_user_cannot_override_record_values_or_assert_total_state(self):
        for key in ("shaft_speed_rpm", "mass_flow_kg_s", "actual_drop_j_kg", "state_basis", "mechanical_efficiency"):
            m, s = self.fixture()
            s[key] = 1
            self.assertEqual(compute_turbine_preliminary(m, s)["status"], "unavailable")

    def test_old_record_is_unchanged_and_no_thermodynamics_are_fabricated(self):
        m, s = self.fixture()
        before = copy.deepcopy((m, s))
        result = compute_turbine_preliminary(m, s)
        self.assertEqual((m, s), before)
        self.assertEqual(result["source"]["state_basis"], "unspecified")
        for row in result["stages"]:
            for absent in ("pressure_pa", "temperature_k", "mach", "density_kg_m3", "area_m2", "metal_angle_deg"):
                self.assertNotIn(absent, row)

    def test_deterministic_digest_binds_scenario_source_and_method(self):
        m, s = self.fixture()
        a = compute_turbine_preliminary(m, s)
        self.assertEqual(a, compute_turbine_preliminary(m, dict(reversed(list(s.items())))))
        s["mean_diameter_m"] += .01
        b = compute_turbine_preliminary(m, s)
        self.assertNotEqual(a["calculation_sha256"], b["calculation_sha256"])
        m["source"]["record_id"] = "record-b"
        self.assertNotEqual(b["calculation_sha256"], compute_turbine_preliminary(m, s)["calculation_sha256"])

    def test_zero_and_unity_rotor_reaction_remain_valid_constraints(self):
        for reaction in (0., 1.):
            m, s = self.fixture()
            s["reaction"] = reaction
            result = compute_turbine_preliminary(m, s)
            self.assertEqual(result["status"], "preliminary")
            self.assertAlmostEqual(result["stages"][0]["reaction_check"], reaction)

    def test_22_23_candidates_explicitly_warn_about_first_order_difference(self):
        result = _harmonic_screening({"blade_count_candidate": 22}, {"blade_count_candidate": 23})
        self.assertEqual(result["status"], "warning")
        self.assertEqual(len(result["pairs"]), 9)
        first = next(p for p in result["pairs"] if p["stator_order"] == p["rotor_order"] == 1)
        second = next(p for p in result["pairs"] if p["stator_order"] == p["rotor_order"] == 2)
        self.assertEqual((first["m"], first["status"]), (1, "warning"))
        self.assertEqual((second["m"], second["status"]), (2, "review"))
        self.assertIn("调整叶数/弦长/系数并做模态校核", result["note"])
        self.assertEqual(result["stator_blade_count"], 22)
        self.assertEqual(result["rotor_blade_count"], 23)

    def test_harmonic_zero_difference_missing_and_unverified_modes(self):
        equal = _harmonic_screening({"blade_count_candidate": 24}, {"blade_count_candidate": 24})
        self.assertEqual([p["m"] for p in equal["pairs"] if p["stator_order"] == p["rotor_order"]], [0, 0, 0])
        high = _harmonic_screening({"blade_count_candidate": 28}, {"blade_count_candidate": 39})
        self.assertEqual(high["status"], "review")
        self.assertTrue(all(p["m"] > 2 for p in high["pairs"]))
        self.assertIn("不代表无共振或结构安全", high["note"])
        missing = _harmonic_screening({"blade_count_candidate": None}, {"blade_count_candidate": 39})
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["pairs"], [])

    def test_full_scenario_exposes_harmonic_pairs_and_check_for_each_stage(self):
        m, s = self.fixture()
        out = compute_turbine_preliminary(m, s)
        for row in out["stages"]:
            self.assertEqual(len(row["interaction"]["pairs"]), 9)
            check = next(c for c in out["checks"] if c["id"] == f"stage_{row['stage']}_blade_interaction")
            self.assertEqual(check["status"], row["interaction"]["status"])
            self.assertNotEqual(check["status"], "pass")


if __name__ == "__main__":
    unittest.main()
