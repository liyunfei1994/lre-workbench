"""Independent thermodynamic and unextended-reactor checks for ignition.

These checks verify the implemented equations.  Sharing GRI30/Cantera with the
model is deliberate and is NOT independent experimental mechanism validation.
"""
import unittest
from pathlib import Path

import cantera as ct
import numpy as np
from scipy.optimize import brentq

from ignition import IgnitionModel, default_settings, gas_mass_flux


def reference_gas():
    # Cantera's input-directory lookup loses Chinese characters in this Windows
    # environment.  Read the installed mechanism as Unicode text instead.
    mechanism = Path(ct.__file__).resolve().parent / 'data' / 'gri30.yaml'
    return ct.Solution(yaml=mechanism.read_text(encoding='utf-8'), transport_model=None)


def sealed_settings(**changes):
    settings = default_settings()
    settings.update(duration_s=.2, sample_count=9, rtol=1e-9,
        initial_pressure_Pa=2e5, initial_temperature_K=500.,
        wall_temperature_K=500., wall_UA_W_K=0., ambient_UA_W_K=0.,
        inlet_ox_CdA_m2=0., inlet_fuel_CdA_m2=0., outlet_CdA_m2=0.,
        ox_open_time_s=0., fuel_open_time_s=0., ignition_time_s=.037,
        ignition_duration_s=.011, ignition_energy_J=0.,
        max_pressure_Pa=1e7)
    settings.update(changes)
    return settings


class IgnitionIndependentReferenceTests(unittest.TestCase):
    def test_restriction_flux_from_independent_mach_state(self):
        # Construct the local isentropic state from a selected Mach number and
        # use rho*velocity.  This does not repeat the pressure-ratio flow formula.
        stagnation_pressure, stagnation_temperature = 4e5, 520.
        gas_constant, gamma = 296.8, 1.4
        for mach in (.15, .6, 1.):
            with self.subTest(mach=mach):
                temperature = stagnation_temperature/(1.+(gamma-1.)*mach**2/2.)
                pressure = stagnation_pressure*(temperature/stagnation_temperature)**(gamma/(gamma-1.))
                density = pressure/(gas_constant*temperature)
                velocity = mach*np.sqrt(gamma*gas_constant*temperature)
                actual, _ = gas_mass_flux(stagnation_pressure,stagnation_temperature,
                    pressure,gamma,gas_constant)
                self.assertAlmostEqual(actual/(density*velocity),1.,delta=2e-12)
                if mach == 1.:
                    lower_back_pressure, choked = gas_mass_flux(stagnation_pressure,
                        stagnation_temperature,pressure*.2,gamma,gas_constant)
                    self.assertTrue(choked)
                    self.assertAlmostEqual(lower_back_pressure/actual,1.,delta=2e-12)

    def test_off_grid_external_heat_matches_fixed_uv_thermodynamic_state(self):
        # The pulse endpoints are not part of the requested uniform output grid.
        # A separate UV flash, not a time integration of the model RHS, is the
        # exact endpoint reference for this closed inert control volume.
        settings = sealed_settings(ignition_energy_J=37.)
        initial = reference_gas()
        initial.TPX = settings['initial_temperature_K'], settings['initial_pressure_Pa'], 'N2:1'
        mass = initial.density * settings['volume_m3']
        u0 = initial.int_energy_mass
        initial.UV = u0 + settings['ignition_energy_J']/mass, 1./initial.density
        rows, metadata = IgnitionModel(settings).integrate()
        self.assertTrue(metadata['complete'], metadata['termination_reason'])
        last = rows[-1]
        self.assertAlmostEqual(last['integrated_igniter_energy_J'], 37., delta=1e-6)
        self.assertAlmostEqual(last['temperature_K'], initial.T, delta=2e-5)
        self.assertAlmostEqual(last['pressure_Pa']/initial.P, 1., delta=5e-8)
        self.assertAlmostEqual(last['mass_kg'], mass, delta=1e-12)
        self.assertAlmostEqual(last['internal_energy_J']-rows[0]['internal_energy_J'], 37., delta=2e-5)
        self.assertEqual(last['composition']['N2'], 1.)

    def test_sealed_wall_exchange_matches_separate_equilibrium_energy_root(self):
        settings = sealed_settings(duration_s=3., sample_count=31,
            wall_temperature_K=700., wall_heat_capacity_J_K=3., wall_UA_W_K=10.)
        gas = reference_gas()
        gas.TPX = settings['initial_temperature_K'], settings['initial_pressure_Pa'], 'N2:1'
        density, mass, u0 = gas.density, gas.density*settings['volume_m3'], gas.int_energy_mass

        def final_energy_error(temperature):
            gas.TD = temperature, density
            return mass*(gas.int_energy_mass-u0) + settings['wall_heat_capacity_J_K']*(temperature-settings['wall_temperature_K'])

        expected_temperature = brentq(final_energy_error, 500., 700., xtol=1e-10)
        rows, metadata = IgnitionModel(settings).integrate()
        self.assertTrue(metadata['complete'], metadata['termination_reason'])
        self.assertAlmostEqual(rows[-1]['temperature_K'], expected_temperature, delta=3e-5)
        self.assertAlmostEqual(rows[-1]['wall_temperature_K'], expected_temperature, delta=3e-5)
        self.assertAlmostEqual(rows[-1]['internal_energy_J']-rows[0]['internal_energy_J'] +
            settings['wall_heat_capacity_J_K']*(rows[-1]['wall_temperature_K']-700.), 0., delta=2e-5)

    def test_closed_reactive_chamber_matches_native_unextended_reactor(self):
        # There is no supplied igniter energy.  The initially hot homogeneous
        # mixture reacts by the mechanism, so an implementation that enables
        # chemistry only at the prescribed pulse would fail this comparison.
        settings = sealed_settings(duration_s=.03, sample_count=31,
            initial_pressure_Pa=1e5, initial_temperature_K=1450.,
            initial_mixture='reactants', initial_of_ratio=1.,
            wall_temperature_K=1000., ignition_time_s=.025,
            ignition_duration_s=.001)
        gas = reference_gas()
        gas.TPY = settings['initial_temperature_K'], settings['initial_pressure_Pa'], {'O2': 1., 'CH4': 1.}
        reference = ct.IdealGasReactor(gas, clone=False, volume=settings['volume_m3'])
        network = ct.ReactorNet([reference])
        network.rtol, network.atol = 2e-10, 1e-18
        network.max_time_step = 1e-5
        rows, metadata = IgnitionModel(settings).integrate()
        self.assertTrue(metadata['complete'], metadata['termination_reason'])
        for row in rows:
            network.advance(row['time_s'])
            self.assertAlmostEqual(row['temperature_K']/reference.T, 1., delta=3e-6)
            self.assertAlmostEqual(row['pressure_Pa']/reference.phase.P, 1., delta=3e-6)
            measured = np.array([row['composition'][name] for name in reference.phase.species_names])
            self.assertLess(float(np.max(np.abs(measured-reference.phase.Y))), 2e-6)
        self.assertGreater(rows[-1]['temperature_K']-rows[0]['temperature_K'], 400.)
        self.assertLess(rows[-1]['composition']['CH4'], .02)
        self.assertEqual(rows[-1]['integrated_igniter_energy_J'], 0.)
        self.assertLess(abs(rows[-1]['internal_energy_J']-rows[0]['internal_energy_J']), 1e-4)

    def test_public_samples_close_mass_and_separate_energy_by_quadrature(self):
        # Integrate the displayed physical fluxes, not the additional ODE ledger
        # states.  This also checks the species/enthalpy convention in open flow.
        settings = sealed_settings(duration_s=.3, sample_count=1001,
            chemistry_enabled=False, initial_temperature_K=600.,
            wall_temperature_K=450., wall_UA_W_K=.2,
            ambient_temperature_K=350., ambient_UA_W_K=.1,
            inlet_ox_CdA_m2=1e-7, inlet_fuel_CdA_m2=4e-8,
            outlet_CdA_m2=2e-7, supply_ox_pressure_Pa=3e5,
            supply_fuel_pressure_Pa=3e5, supply_ox_temperature_K=600.,
            supply_fuel_temperature_K=600., ox_open_time_s=.013,
            fuel_open_time_s=.027)
        source = reference_gas()
        source.TPX = 600., 3e5, 'O2:1'
        h_ox = source.enthalpy_mass
        source.TPX = 600., 3e5, 'CH4:1'
        h_fuel = source.enthalpy_mass
        rows, metadata = IgnitionModel(settings).integrate()
        self.assertTrue(metadata['complete'], metadata['termination_reason'])
        time = np.array([row['time_s'] for row in rows])
        mass_flux = np.array([row['ox_mass_flow_kg_s']+row['fuel_mass_flow_kg_s']-
            row['outlet_mass_flow_kg_s'] for row in rows])
        fluid_energy_flux = np.array([row['ox_mass_flow_kg_s']*h_ox+
            row['fuel_mass_flow_kg_s']*h_fuel-
            row['outlet_mass_flow_kg_s']*row['enthalpy_J_kg']-
            row['heat_to_wall_W'] for row in rows])
        wall_energy_flux = np.array([row['heat_to_wall_W']+row['heat_from_ambient_W'] for row in rows])
        mass_change = rows[-1]['mass_kg']-rows[0]['mass_kg']
        fluid_energy_change = rows[-1]['internal_energy_J']-rows[0]['internal_energy_J']
        wall_energy_change = settings['wall_heat_capacity_J_K']*(rows[-1]['wall_temperature_K']-rows[0]['wall_temperature_K'])
        self.assertAlmostEqual(mass_change, float(np.trapezoid(mass_flux,time)), delta=2e-8)
        self.assertAlmostEqual(fluid_energy_change, float(np.trapezoid(fluid_energy_flux,time)), delta=.01)
        self.assertAlmostEqual(wall_energy_change, float(np.trapezoid(wall_energy_flux,time)), delta=.01)


if __name__ == '__main__':
    unittest.main()
