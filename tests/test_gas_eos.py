"""Independent PR state, inventory and explicit candidate-policy checks."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    import cantera as ct
    import CoolProp
    import numpy as np
    SCIENCE_AVAILABLE = True
except ImportError:
    SCIENCE_AVAILABLE = False

from lre_workbench.properties.gas_eos import (
    GasEOSParameterError, PR_SUPPORTED_V1, build_gas, equilibrate_hp,
    gas_identity, pr_parameter_coverage, set_frozen_hp, set_frozen_sp,
)


@unittest.skipUnless(SCIENCE_AVAILABLE, "Cantera/CoolProp scientific environment required")
class GasEOSCandidateTests(unittest.TestCase):
    def test_full_mechanism_is_never_silently_reduced_for_pr(self):
        for dataset in ("gri30", "gri30_highT"):
            audit = pr_parameter_coverage(dataset)
            self.assertEqual(audit["species_count"], 53)
            self.assertEqual(audit["supported_species_count"], 16)
            self.assertIn("OH", audit["missing_species"])
            self.assertIn("CH3", audit["missing_species"])
            with self.assertRaises(GasEOSParameterError) as caught:
                build_gas(dataset, "peng_robinson", "full")
            self.assertEqual(caught.exception.coverage["missing_species"], audit["missing_species"])
        full, identity = build_gas("gri30_highT", "ideal", "auto")
        self.assertEqual(full.n_species, 53)
        self.assertFalse(identity["candidate_model"])

    def test_candidate_pair_has_exact_species_thermochemistry_and_provenance(self):
        ideal, ideal_identity = build_gas("gri30_highT", "ideal", "pr_supported_v1")
        real, real_identity = build_gas("gri30_highT", "peng_robinson", "pr_supported_v1")
        self.assertEqual(ideal.species_names, list(PR_SUPPORTED_V1))
        self.assertEqual(real.species_names, ideal.species_names)
        self.assertEqual(real_identity["species_policy_sha256"], ideal_identity["species_policy_sha256"])
        self.assertEqual(real_identity["eos_parameter_sha256"], ideal_identity["eos_parameter_sha256"])
        self.assertEqual(len(real_identity["excluded_species"]), 37)
        np.testing.assert_array_equal(real.atomic_weights, ideal.atomic_weights)
        for name in real.species_names:
            a, b = real.species(name), ideal.species(name)
            self.assertEqual(a.composition, b.composition)
            np.testing.assert_array_equal(a.thermo.coeffs, b.thermo.coeffs)
            self.assertTrue(real_identity["species_parameters"][name]["source"]["file_sha256"])
        self.assertEqual(real.transport_model, "mixture-averaged")
        self.assertIn("not validated", real_identity["transport_scope"])
        # Caller metadata edits cannot relabel the cached phase.
        real_identity["species_names"].clear()
        self.assertEqual(len(gas_identity(real)["species_names"]), 16)

    def test_pure_oxygen_density_matches_independent_pr_cubic(self):
        real, identity = build_gas(eos="peng_robinson")
        temperature, pressure = 750., 60e6
        real.TPX = temperature, pressure, {"O2": 1.}
        data = identity["species_parameters"]["O2"]
        tc, pc, omega = data["critical_temperature_K"], data["critical_pressure_Pa"], data["acentric_factor"]
        kappa = .37464 + 1.54226 * omega - .26992 * omega**2
        alpha = (1 + kappa * (1 - (temperature / tc)**.5))**2
        a = .45723552892138218 * ct.gas_constant**2 * tc**2 / pc * alpha
        b = .0777960739038885 * ct.gas_constant * tc / pc
        A = a * pressure / (ct.gas_constant * temperature)**2
        B = b * pressure / (ct.gas_constant * temperature)
        roots = np.roots([1., -(1 - B), A - 2 * B - 3 * B**2, -(A * B - B**2 - B**3)])
        z = max(float(r.real) for r in roots if abs(r.imag) < 1e-10)
        expected = pressure * real.mean_molecular_weight / (z * ct.gas_constant * temperature)
        self.assertAlmostEqual(real.density / expected, 1., delta=1e-10)

    def test_hp_equilibrium_preserves_target_pressure_enthalpy_and_elements(self):
        gas, _ = build_gas(eos="peng_robinson")
        composition = {"CH4": 1., "O2": .2}
        gas.TPY = 1000., 60e6, composition
        before = {e: gas.elemental_mass_fraction(e) for e in gas.element_names}
        equilibrate_hp(gas, -4e6, 60e6, composition)
        self.assertAlmostEqual(gas.P, 60e6, delta=.1)
        self.assertAlmostEqual(gas.enthalpy_mass, -4e6, delta=.05)
        for e, value in before.items():
            self.assertAlmostEqual(gas.elemental_mass_fraction(e), value, delta=1e-8)
        # The same equilibrium HP target reached from another starting state.
        expected_t = gas.T
        gas.TPY = 4000., 1e6, composition
        equilibrate_hp(gas, -4e6, 60e6, composition)
        self.assertAlmostEqual(gas.T, expected_t, delta=1e-6)

    def test_frozen_expansion_and_return_keep_composition_and_energy(self):
        gas, _ = build_gas(eos="peng_robinson")
        equilibrate_hp(gas, -4e6, 60e6, {"CH4": 1., "O2": .2})
        original_h, original_s, original_y, original_t = gas.enthalpy_mass, gas.entropy_mass, gas.Y.copy(), gas.T
        set_frozen_sp(gas, original_s, 30e6)
        self.assertAlmostEqual(gas.entropy_mass, original_s, delta=1e-5)
        self.assertAlmostEqual(gas.P, 30e6, delta=.1)
        self.assertLess(gas.enthalpy_mass, original_h)
        np.testing.assert_allclose(gas.Y, original_y, rtol=0, atol=1e-12)
        set_frozen_hp(gas, original_h, 60e6)
        self.assertAlmostEqual(gas.T, original_t, delta=1e-6)
        self.assertAlmostEqual(gas.enthalpy_mass, original_h, delta=.05)
        self.assertGreater(gas.viscosity, 0)
        self.assertGreater(gas.thermal_conductivity, 0)

    def test_invalid_or_unattainable_state_does_not_become_ideal_fallback(self):
        gas, _ = build_gas(eos="peng_robinson")
        with self.assertRaises(ValueError):
            equilibrate_hp(gas, -1e12, 60e6, {"CH4": 1., "O2": .2})
        with self.assertRaises(ValueError):
            set_frozen_hp(gas, float("nan"), 60e6)
        self.assertEqual(gas.thermo_model, "Peng-Robinson")
        with self.assertRaises(ValueError):
            build_gas(eos="Redlich-Kwong")
        with self.assertRaises(ValueError):
            build_gas(species_policy="drop_trace_automatically")

    def test_thread_and_eos_state_isolation(self):
        ready = threading.Barrier(2)
        def worker(temperature, pressure):
            gas, identity = build_gas(eos="peng_robinson")
            gas.TPX = temperature, pressure, {"O2": 1.}
            ready.wait(timeout=10)
            actual = (gas.T, gas.P)
            ready.wait(timeout=10)
            return gas, actual, identity
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(worker, 750., 60e6)
            b = pool.submit(worker, 850., 40e6)
            first, second = a.result(timeout=20), b.result(timeout=20)
        self.assertIsNot(first[0], second[0])
        self.assertAlmostEqual(first[1][0], 750.)
        self.assertAlmostEqual(first[1][1], 60e6)
        self.assertAlmostEqual(second[1][0], 850.)
        self.assertAlmostEqual(second[1][1], 40e6)
        ideal, _ = build_gas(eos="ideal", species_policy="pr_supported_v1")
        real, _ = build_gas(eos="peng_robinson", species_policy="pr_supported_v1")
        self.assertIsNot(real, ideal)

    def test_candidate_temperature_ranges_are_read_from_selected_species(self):
        for dataset, expected_max in (("gri30", 3500.), ("gri30_highT", 5000.)):
            gas, _ = build_gas(dataset, "peng_robinson", "pr_supported_v1")
            self.assertEqual(max(sp.thermo.min_temp for sp in gas.species()), 300.)
            self.assertEqual(min(sp.thermo.max_temp for sp in gas.species()), expected_max)
        # Major high-T species extend to 6000 K, but that does not extend the
        # common interval of all 16 allowed species past 5000 K.
        self.assertEqual(gas.species("H2O").thermo.max_temp, 6000.)
        self.assertEqual(gas.species("C3H8").thermo.max_temp, 5000.)

    def test_task_cancellation_is_control_flow_not_physics_fallback(self):
        from lre_workbench.runtime.tasks import CancellationToken, TaskCancelled, cancellation_scope
        gas, _ = build_gas(eos="peng_robinson")
        gas.TPY = 1000., 60e6, {"CH4": 1., "O2": .2}
        h, s = gas.enthalpy_mass, gas.entropy_mass
        for action in (lambda: equilibrate_hp(gas, h, 60e6, {"CH4": 1., "O2": .2}),
                       lambda: set_frozen_hp(gas, h, 30e6),
                       lambda: set_frozen_sp(gas, s, 30e6)):
            token = CancellationToken()
            with cancellation_scope(token):
                token.cancel()
                with self.assertRaises(TaskCancelled):
                    action()

    def test_high_temperature_pr_enthalpy_gap_is_not_accepted_as_hp_root(self):
        gas, _ = build_gas(eos="peng_robinson")
        gas.TPY = 2973.0954994527656, 9193971.093208307, {"H2O": .49, "CO": .36, "CO2": .12, "H2": .03}
        original = gas.state.copy()
        with self.assertRaisesRegex(ValueError, "未获得连续 EOS 状态"):
            set_frozen_hp(gas, -2715240.583111846, 9259821.057840733)
        np.testing.assert_allclose(gas.state, original, rtol=0, atol=1e-10)


if __name__ == "__main__":
    unittest.main()
