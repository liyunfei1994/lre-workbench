"""Conservation and state isolation for accelerated PR state recovery."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import cantera
    import numpy as np
    from scipy.optimize import brentq
    SCIENCE_AVAILABLE = True
except ImportError:
    SCIENCE_AVAILABLE = False

from lre_workbench.properties.gas_eos import build_gas, equilibrate_hp


class CountingPhase:
    """Transparent instrumentation of actual native/candidate chemistry."""
    def __init__(self, gas):
        object.__setattr__(self, "gas", gas)
        object.__setattr__(self, "evaluated_temperatures", [])

    def __getattr__(self, key):
        return getattr(self.gas, key)

    def __setattr__(self, key, value):
        setattr(self.gas, key, value)

    def equilibrate(self, *args, **kwargs):
        self.evaluated_temperatures.append(self.gas.T)
        return self.gas.equilibrate(*args, **kwargs)


@unittest.skipUnless(SCIENCE_AVAILABLE, "scientific libraries required")
class PRSolverPerformanceTests(unittest.TestCase):
    def test_hp_revisits_do_not_repeat_chemistry_or_leak_between_calls(self):
        for eos in ("Peng-Robinson", "Peng-Robinson-Boston-Mathias"):
            gas, _ = build_gas(eos=eos)
            probe = CountingPhase(gas)
            for pressure, enthalpy, composition in (
                    (60e6, -4e6, {"CH4": 1., "O2": .2}),
                    (35e6, -3.8e6, {"CH4": 1., "O2": .3})):
                probe.evaluated_temperatures.clear()
                gas.TPY = 1000., pressure, composition
                elements = [gas.elemental_mass_fraction(e) for e in gas.element_names]
                equilibrate_hp(probe, enthalpy, pressure, composition)
                with self.subTest(eos=eos, pressure=pressure):
                    calls = probe.evaluated_temperatures
                    self.assertEqual(len(calls), len(set(calls)))
                    self.assertGreater(len(calls), 2)
                    self.assertAlmostEqual(gas.enthalpy_mass, enthalpy, delta=.05)
                    self.assertAlmostEqual(gas.P, pressure, delta=.1)
                    np.testing.assert_allclose(
                        [gas.elemental_mass_fraction(e) for e in gas.element_names],
                        elements, rtol=0, atol=1e-8)

    def test_bm_frozen_recovery_agrees_with_independent_bracket_after_state_changes(self):
        gas, _ = build_gas(eos="Peng-Robinson-Boston-Mathias")
        reference = gas.clone()
        # Alternate composition, temperature, pressure and density setters to
        # exercise cache invalidation through real public state transitions.
        for composition in ({"O2": 1.}, {"CH4": .6, "H2": .2, "H2O": .2},
                            {"H2O": .5, "CO": .3, "CO2": .2}):
            for temperature, pressure in ((400., 2e6), (800., 60e6), (3100., 8e6)):
                reference.TPY = temperature, pressure, composition
                gas.TPY = temperature, pressure, composition
                gas.TDY = temperature, reference.density, composition
                for kind, prop in (("HP", "enthalpy_mass"), ("SP", "entropy_mass")):
                    target = getattr(reference, prop)
                    target_pressure = pressure*.65
                    def residual(t):
                        reference.TPY = t, target_pressure, composition
                        return getattr(reference, prop)-target
                    expected = brentq(residual, temperature*.5, temperature*1.5,
                                      xtol=1e-9, rtol=1e-12)
                    # A distant initial state also exercises the safeguarded
                    # fallback when the limited Newton iterations cannot finish.
                    gas.TPY = 5000., pressure, composition
                    setattr(gas, kind, (target, target_pressure))
                    with self.subTest(composition=composition, temperature=temperature, kind=kind):
                        self.assertAlmostEqual(gas.T, expected, delta=2e-7)
                        self.assertAlmostEqual(gas.P, target_pressure, delta=.1)
                        np.testing.assert_allclose(gas.Y, reference.Y, rtol=0, atol=1e-12)
                        self.assertAlmostEqual(gas.cp_mass, reference.cp_mass, delta=1e-5)
                    reference.TPY = temperature, pressure, composition


if __name__ == "__main__":
    unittest.main()
