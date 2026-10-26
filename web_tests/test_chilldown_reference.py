"""Independent limiting cases for the thermal inventory research unit.

These checks do not use the ODE's cumulative conservation ledgers as references.
Synthetic parameters verify equations and numerics, not any engine hardware.
"""
from copy import deepcopy
import math
import unittest

import CoolProp.CoolProp as CP
import numpy as np
from scipy.optimize import brentq

from chilldown import ChilldownModel, hem_mass_flux


def reference_fixture():
    return dict(duration_s=2., sample_count=201, command_time_s=.1,
        command_ramp_s=.1, rtol=1e-7, circuits=[dict(
            id='reference', label='独立方程核验单元', fluid='Oxygen', volume_m3=.0002,
            initial_mode='PT', initial_pressure_Pa=2e5, initial_temperature_K=290.,
            initial_quality=1., wall_temperature_K=310., wall_heat_capacity_J_K=.5,
            wall_UA_W_K=.05, ambient_temperature_K=250., ambient_UA_W_K=0.,
            supply_pressure_Pa=8e5, supply_temperature_K=70., back_pressure_Pa=1e5,
            inlet_CdA_m2=0., outlet_CdA_m2=0.,
            initial_inlet_opening=1., final_inlet_opening=1.,
            initial_outlet_opening=1., final_outlet_opening=1.,
            valve_tau_s=.1, valve_rate_per_s=2.,
            min_pressure_Pa=5e4, max_pressure_Pa=1.5e6, max_wall_temperature_K=500.,
            target_wall_temperature_K=100., target_liquid_volume_fraction=.95)])


def open_reference_fixture():
    settings = reference_fixture()
    settings['circuits'][0].update(initial_temperature_K=110., wall_temperature_K=130.,
        wall_heat_capacity_J_K=10., wall_UA_W_K=.5, inlet_CdA_m2=.1e-6,
        outlet_CdA_m2=.5e-6)
    return settings


class ChilldownIndependentReferenceTests(unittest.TestCase):
    def test_ambient_only_wall_matches_exact_exponential(self):
        settings = reference_fixture()
        settings.update(duration_s=4., sample_count=101)
        c = settings['circuits'][0]
        c.update(wall_UA_W_K=0., ambient_UA_W_K=3., wall_heat_capacity_J_K=120.)
        rows, meta = ChilldownModel(settings).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        initial = rows[0]['circuits'][0]
        for sample in rows:
            row = sample['circuits'][0]
            expected = c['ambient_temperature_K'] + (
                c['wall_temperature_K'] - c['ambient_temperature_K']) * math.exp(
                    -c['ambient_UA_W_K'] * sample['time_s'] / c['wall_heat_capacity_J_K'])
            self.assertAlmostEqual(row['wall_temperature_K'], expected, delta=2e-5)
            self.assertAlmostEqual(row['mass_kg'], initial['mass_kg'], delta=1e-12)
            self.assertAlmostEqual(row['fluid_internal_energy_J'], initial['fluid_internal_energy_J'], delta=1e-8)

    def test_sealed_wall_fluid_reaches_independent_energy_equilibrium(self):
        settings = reference_fixture()
        settings.update(duration_s=120., sample_count=121)
        c = settings['circuits'][0]
        fluid = c['fluid']
        rho0 = CP.PropsSI('Dmass', 'P', c['initial_pressure_Pa'], 'T', c['initial_temperature_K'], fluid)
        u0 = CP.PropsSI('Umass', 'P', c['initial_pressure_Pa'], 'T', c['initial_temperature_K'], fluid)
        m0 = rho0 * c['volume_m3']
        # A separate, algebraic equilibrium solve: same rho in a sealed rigid cell.
        total0 = m0 * u0 + c['wall_heat_capacity_J_K'] * c['wall_temperature_K']
        def equilibrium_energy(temperature):
            return (m0 * CP.PropsSI('Umass', 'Dmass', rho0, 'T', temperature, fluid)
                + c['wall_heat_capacity_J_K'] * temperature - total0)
        expected = brentq(equilibrium_energy, c['initial_temperature_K'], c['wall_temperature_K'], xtol=1e-10)
        rows, meta = ChilldownModel(settings).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        final = rows[-1]['circuits'][0]
        self.assertAlmostEqual(final['temperature_K'], expected, delta=.002)
        self.assertAlmostEqual(final['wall_temperature_K'], expected, delta=.002)
        # Rebuild total energy directly from each reported PT state, not the ledger.
        for sample in rows:
            row = sample['circuits'][0]
            u = CP.PropsSI('Umass', 'P', row['pressure_Pa'], 'T', row['temperature_K'], fluid)
            total = row['mass_kg'] * u + c['wall_heat_capacity_J_K'] * row['wall_temperature_K']
            self.assertAlmostEqual(total, total0, delta=2e-5)

    def test_low_pressure_gas_orifice_matches_ideal_gas_choked_limit(self):
        fluid, pressure, temperature, backpressure = 'Oxygen', 1e4, 290., 1e3
        state = CP.AbstractState('HEOS', fluid)
        state.update(CP.PT_INPUTS, pressure, temperature)
        gamma = state.cpmass() / state.cvmass()
        gas_constant = state.gas_constant() / state.molar_mass()
        expected = (pressure / math.sqrt(gas_constant * temperature) * math.sqrt(gamma)
            * (2 / (gamma + 1)) ** ((gamma + 1) / (2 * (gamma - 1))))
        expected_pressure = pressure * (2 / (gamma + 1)) ** (gamma / (gamma - 1))
        result = hem_mass_flux(fluid, pressure, state.hmass(), state.smass(), backpressure)
        self.assertTrue(result['choked'])
        # HEOS is not a constant-cp ideal gas; allow the finite ideal-gas-limit error.
        self.assertLess(abs(result['mass_flux_kg_m2_s'] / expected - 1), .003)
        self.assertLess(abs(result['critical_pressure_Pa'] / expected_pressure - 1), .003)

    def test_small_pressure_drop_liquid_matches_incompressible_limit(self):
        state = CP.AbstractState('HEOS', 'Oxygen')
        state.update(CP.PT_INPUTS, 8e5, 90.)
        expected = math.sqrt(2 * state.rhomass() * 1000.)
        result = hem_mass_flux('Oxygen', 8e5, state.hmass(), state.smass(), 7.99e5)
        self.assertFalse(result['choked'])
        self.assertLess(abs(result['mass_flux_kg_m2_s'] / expected - 1), 2e-5)

    def test_two_phase_choke_matches_separate_dense_pressure_scan(self):
        state = CP.AbstractState('HEOS', 'Oxygen')
        state.update(CP.PQ_INPUTS, 2e5, .2)
        h0, s0 = state.hmass(), state.smass()
        result = hem_mass_flux('Oxygen', 2e5, h0, s0, 5e4)
        # Independent maximum search with no production grid or optimizer.
        scan = []
        for pressure in np.geomspace(5e4, 2e5, 2001)[:-1]:
            state.update(CP.PSmass_INPUTS, pressure, s0)
            scan.append((state.rhomass() * math.sqrt(2 * (h0 - state.hmass())), pressure))
        reference_flux, reference_pressure = max(scan)
        self.assertTrue(result['choked'])
        self.assertLess(abs(result['mass_flux_kg_m2_s'] / reference_flux - 1), 1e-5)
        self.assertLess(abs(result['critical_pressure_Pa'] / reference_pressure - 1), .001)

    def test_open_two_phase_transient_closes_with_output_quadrature(self):
        settings = open_reference_fixture()
        settings['sample_count'] = 801
        rows, meta = ChilldownModel(settings).integrate()
        self.assertTrue(meta['complete'], meta['termination_reason'])
        points = [sample['circuits'][0] for sample in rows]
        times = [sample['time_s'] for sample in rows]
        self.assertIn('twophase', {row['phase'] for row in points})
        # Reconstruct flux integrals from exported samples, independent of all
        # additional integral states in the ODE, including cancellation terms.
        mass_integral = np.trapezoid([
            r['inlet_mass_flow_kg_s'] - r['outlet_mass_flow_kg_s'] for r in points], times)
        energy_integral = np.trapezoid([
            r['inlet_enthalpy_flow_W'] - r['outlet_enthalpy_flow_W'] + r['heat_from_ambient_W']
            for r in points], times)
        c = settings['circuits'][0]
        first, final = points[0], points[-1]
        mass_change = final['mass_kg'] - first['mass_kg']
        energy_change = (final['fluid_internal_energy_J'] - first['fluid_internal_energy_J']
            + c['wall_heat_capacity_J_K'] * (final['wall_temperature_K'] - first['wall_temperature_K']))
        self.assertLess(abs(mass_change - mass_integral) / max(abs(mass_change), .001), .001)
        self.assertLess(abs(energy_change - energy_integral) / max(abs(energy_change), 1.), .002)

    def test_tolerance_refinement_preserves_phase_transition_endpoint(self):
        coarse = open_reference_fixture()
        coarse['rtol'] = 1e-5
        fine = deepcopy(coarse)
        fine['rtol'] = 1e-8
        results = []
        for settings in (coarse, fine):
            rows, meta = ChilldownModel(settings).integrate()
            self.assertTrue(meta['complete'], meta['termination_reason'])
            results.append(rows[-1]['circuits'][0])
        a, b = results
        self.assertEqual(a['phase'], b['phase'])
        for key, scale in [('pressure_Pa', 1e5), ('temperature_K', 100.),
                           ('wall_temperature_K', 100.), ('mass_kg', .001)]:
            self.assertLess(abs(a[key] - b[key]) / max(abs(b[key]), scale), .0005, key)
        self.assertAlmostEqual(a['quality'], b['quality'], delta=.0002)


if __name__ == '__main__':
    unittest.main()
