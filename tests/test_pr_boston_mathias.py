"""Thermodynamic identities, chemistry and path continuity of the PR-BM candidate."""
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cantera as ct
import numpy as np
from scipy.integrate import quad

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lre_workbench.properties.gas_eos import build_gas, equilibrate_hp, set_frozen_hp, set_frozen_sp
from lre_workbench.properties.pr_boston_mathias import BM_EOS, alpha_boston_mathias


class BostonMathiasTests(unittest.TestCase):
    def setUp(self):
        self.gas, self.identity = build_gas("gri30_highT", BM_EOS, "pr_supported_v1")
        self.gas.TPY = 3300., 33e6, {"H2O": .49, "CO": .36, "CO2": .12, "H2": .03}

    def test_distinct_model_identity_same_reference_and_parameters(self):
        native, previous = build_gas("gri30_highT", "Peng-Robinson", "pr_supported_v1")
        ideal, control = build_gas("gri30_highT", "ideal-gas", "pr_supported_v1")
        self.assertEqual(self.gas.thermo_model, BM_EOS)
        self.assertNotIn("eos_model_sha256", previous)
        self.assertEqual(len(self.identity["eos_model_sha256"]), 64)
        for key in ("species_policy_sha256", "eos_parameter_sha256", "enthalpy_reference_id"):
            self.assertEqual(self.identity[key], previous[key])
            self.assertEqual(self.identity[key], control[key])
        self.assertEqual(self.gas.species_names, ideal.species_names)
        for i in range(self.gas.n_species):
            np.testing.assert_array_equal(self.gas.species(i).thermo.coeffs, ideal.species(i).thermo.coeffs)
        cloned = self.gas.clone()
        cloned.TP = 1000, 1e6
        self.assertEqual(self.gas.T, 3300)
        self.assertEqual(cloned.thermo_model, BM_EOS)

    def test_published_alpha_is_c1_not_claimed_c2(self):
        tc, k = np.array([647.096]), np.array([.874])
        left = alpha_boston_mathias(float(tc[0]), tc, k)
        right = alpha_boston_mathias(float(tc[0])*(1+1e-9), tc, k)
        self.assertAlmostEqual(left[0][0], 1)
        self.assertAlmostEqual(right[0][0], 1, places=8)
        self.assertAlmostEqual(left[1][0], -k[0]/tc[0], places=12)
        self.assertAlmostEqual(right[1][0], left[1][0], places=10)
        self.assertAlmostEqual((right[2][0]-left[2][0])*tc[0]**2, -k[0]/2, places=7)
        temperatures = [700, 2976.322797, 4000, 6000]
        alphas = [alpha_boston_mathias(t, tc, k)[0][0] for t in temperatures]
        self.assertTrue(all(a > b > 0 for a,b in zip(alphas, alphas[1:])))

    def test_caloric_and_mechanical_derivatives_from_one_potential(self):
        g=self.gas
        for temperature,pressure in [(800.,60e6),(2976.322797,10e6),(3800.,33e6)]:
            g.TP=temperature,pressure
            original=g.TPY
            cp,cv,rho,sound=g.cp_mass,g.cv_mass,g.density,g.sound_speed
            dt=.002
            g.TP=temperature+dt,pressure
            hp,sp=g.enthalpy_mass,g.entropy_mass
            g.TP=temperature-dt,pressure
            hm,sm=g.enthalpy_mass,g.entropy_mass
            self.assertAlmostEqual((hp-hm)/(2*dt)/cp,1,places=7)
            self.assertAlmostEqual((sp-sm)/(2*dt)*temperature/cp,1,places=7)
            g.TDY=temperature+dt,rho,original[2]
            up=g.int_energy_mass
            g.TDY=temperature-dt,rho,original[2]
            um=g.int_energy_mass
            self.assertAlmostEqual((up-um)/(2*dt)/cv,1,places=7)
            g.TPY=original
            entropy=g.entropy_mass
            dp=pressure*1e-5
            g.SP=entropy,pressure+dp
            rhop=g.density
            g.SP=entropy,pressure-dp
            rhom=g.density
            self.assertAlmostEqual(2*dp/(rhop-rhom)/sound**2,1,places=6)
            g.TPY=original
            self.assertAlmostEqual(float(g.X@g.chemical_potentials)/g.gibbs_mole,1,places=10)

    def test_td_preserves_the_largest_of_three_physical_roots(self):
        gas = self.gas
        temperature, pressure = 150., 1e6
        gas.TPY = temperature, pressure, {"CH4": 1}
        fields = ("density", "enthalpy_mass", "entropy_mass", "cp_mass", "cv_mass", "sound_speed")
        with patch("lre_workbench.properties.pr_boston_mathias.np.roots", wraps=np.roots) as solve_roots:
            reference = [getattr(gas, field) for field in fields]
        coefficients = solve_roots.call_args.args[0]
        roots = np.roots(coefficients)
        self.assertTrue(np.all(np.isreal(roots)))
        self.assertGreater(float(min(roots).real), coefficients[1]+1)

        with patch("lre_workbench.properties.pr_boston_mathias.np.roots", wraps=np.roots) as solve_roots:
            gas.TD = temperature, reference[0]
            actual = [getattr(gas, field) for field in fields]
            solve_roots.assert_not_called()
        np.testing.assert_allclose(actual, reference, rtol=1e-12)
        self.assertAlmostEqual(gas.P/pressure, 1., places=12)

        liquid_density = gas.mean_molecular_weight*pressure/(float(min(roots).real)*ct.gas_constant*temperature)
        with self.assertRaisesRegex(ValueError, "not the selected stable gas root"):
            gas.TD = temperature, liquid_density

    def test_nearly_multiple_critical_root_uses_tp_reference(self):
        gas = self.gas
        parameters = gas._parameters["CH4"]
        temperature = parameters["critical_temperature_K"]*(1+1e-11)
        pressure = parameters["critical_pressure_Pa"]*(1+5e-11)
        gas.TPY = temperature, pressure, {"CH4": 1}
        density = gas.density
        with patch("lre_workbench.properties.pr_boston_mathias.np.roots", wraps=np.roots) as solve_roots:
            gas.TD = temperature, density
            solve_roots.assert_called_once()
        self.assertAlmostEqual(gas.density/density, 1., places=7)
        fields = ("density", "enthalpy_mass", "entropy_mass", "cp_mass", "cv_mass", "sound_speed")
        actual = [getattr(gas, field) for field in fields]
        gas.TP = temperature, gas.P
        np.testing.assert_allclose(actual, [getattr(gas, field) for field in fields], rtol=1e-12)

    def test_no_water_alpha_enthalpy_gap_hp_sp_round_trips(self):
        g=self.gas
        g.TPY=2973.0954994527656,9193971.093208307,{"H2O":.49,"CO":.36,"CO2":.12,"H2":.03}
        set_frozen_hp(g,-2715240.583111846,9259821.057840733)
        self.assertLess(abs(g.enthalpy_mass+2715240.583111846),.01)
        original=g.TPY
        h,s=g.enthalpy_mass,g.entropy_mass
        set_frozen_sp(g,s,1e6)
        self.assertLess(abs(g.entropy_mass-s),1e-6)
        set_frozen_hp(g,h,original[1])
        self.assertLess(abs(g.T-original[0]),1e-7)
        values=[]
        for t in [2976.322797-.001,2976.322797,2976.322797+.001]:
            g.TP=t,10e6
            values.append(g.enthalpy_mass)
        self.assertTrue(values[0]<values[1]<values[2])
        self.assertLess(values[2]-values[0],10)

    def test_chemical_potentials_are_gibbs_mole_derivatives(self):
        g=self.gas
        original=g.TPY
        moles=g.X.copy()
        chemical=g.chemical_potentials.copy()
        for species in ("H2O","CO","CO2","H2"):
            index=g.species_index(species)
            epsilon=1e-6
            plus,minus=moles.copy(),moles.copy()
            plus[index]+=epsilon
            minus[index]-=epsilon
            g.TPX=original[0],original[1],plus
            gp=float(plus.sum())*g.gibbs_mole
            g.TPX=original[0],original[1],minus
            gm=float(minus.sum())*g.gibbs_mole
            self.assertAlmostEqual((gp-gm)/(2*epsilon)/chemical[index],1,places=7)
        g.TPY=original

    def test_nonideal_tp_equilibrium_conserves_elements_and_reaction_affinity(self):
        g=self.gas
        for temperature,ratio in [(750,60),(850,.13),(3800,3.6)]:
            g.TPY=temperature,60e6,{"CH4":1,"O2":ratio}
            before=np.array([g.elemental_mass_fraction(e) for e in g.element_names])
            g.equilibrate("TP")
            np.testing.assert_allclose(before,[g.elemental_mass_fraction(e) for e in g.element_names],atol=1e-9)
            mu=g.chemical_potentials
            # Water-gas-shift affinity must vanish at NONIDEAL equilibrium.
            affinity=mu[g.species_index("CO2")]+mu[g.species_index("H2")]-mu[g.species_index("CO")]-mu[g.species_index("H2O")]
            self.assertLess(abs(affinity)/(8314.46261815324*temperature),1e-7)
            # Nonzero delta-n is essential: a duplicated ln(P/P0) cancels in
            # water-gas shift and other isomolar reactions, but not these.
            for product,reactant in (("H2O","H2"),("CO2","CO")):
                affinity=mu[g.species_index(product)]-mu[g.species_index(reactant)]-.5*mu[g.species_index("O2")]
                self.assertLess(abs(affinity)/(8314.46261815324*temperature),1e-7)
        ideal,_=build_gas("gri30_highT","ideal-gas","pr_supported_v1")
        ideal.TPY=g.TPY
        self.assertGreater(max(abs(g.chemical_potentials-ideal.chemical_potentials)),1e4)

    def test_reforming_affinity_from_integrated_eos_volume(self):
        gas = self.gas
        temperature, pressure = 850., 60e6
        gas.TPY = temperature, pressure, {"CH4": 1., "O2": .13}
        gas.equilibrate("TP")
        amounts = gas.X.copy()
        rt = ct.gas_constant*temperature
        stoichiometry = {"CH4": -1, "H2O": -1, "CO": 1, "H2": 3}
        reaction = np.zeros(gas.n_species)
        ideal_affinity = 0.
        for name, coefficient in stoichiometry.items():
            index = gas.species_index(name)
            reaction[index] = coefficient
            reference = gas.species(index).thermo
            reference_gibbs = (reference.h(temperature)-temperature*reference.s(temperature))/rt
            ideal_affinity += coefficient*(reference_gibbs+math.log(pressure/reference.reference_pressure)
                                           +math.log(amounts[index]))

        def residual_gibbs(extent):
            trial = amounts+extent*reaction
            lower_pressure = 1e-3
            gas.TPX = temperature, lower_pressure, trial

            def pressure_integrand(log_pressure):
                p = math.exp(log_pressure)
                gas.TP = temperature, p
                volume = gas.mean_molecular_weight/gas.density
                return p*volume/rt-1

            # At fixed composition, d(g^R/RT)/dln(P) = Z-1 for molar g^R.
            # The total residual Gibbs follows by multiplying by total moles.
            molar_residual = quad(pressure_integrand, math.log(lower_pressure), math.log(pressure),
                                 epsabs=2e-10, epsrel=2e-9)[0]
            return float(trial.sum())*molar_residual

        step = 1e-4
        residual_affinity = (residual_gibbs(-2*step)-8*residual_gibbs(-step)
                             +8*residual_gibbs(step)-residual_gibbs(2*step))/(12*step)
        self.assertEqual(reaction.sum(), 2.)
        self.assertGreater(abs(ideal_affinity), 1e-3)
        self.assertAlmostEqual(ideal_affinity+residual_affinity, 0., delta=1e-7)

    def test_hp_equilibrium_and_transport_interfaces(self):
        g=self.gas
        y={"CH4":1,"O2":60}
        g.TPY=750,60e6,y
        g.equilibrate("TP")
        target=g.enthalpy_mass
        equilibrate_hp(g,target,60e6,y,"BM test")
        self.assertLess(abs(g.T-750),1e-6)
        self.assertLess(abs(g.enthalpy_mass-target),.05)
        self.assertTrue(g.viscosity>0 and g.thermal_conductivity>0)
        for value in (g.cp_mass,g.cv_mass,g.sound_speed,g.isothermal_compressibility):
            self.assertTrue(math.isfinite(value) and value>0)

    def test_subcritical_reference_matches_independent_native_pr(self):
        native,_=build_gas("gri30_highT","Peng-Robinson","pr_supported_v1")
        for gas in (native,self.gas): gas.TPY=300,1e5,{"CO2":1}
        for prop in ("density","enthalpy_mass","entropy_mass","cp_mass","cv_mass","sound_speed"):
            self.assertAlmostEqual(getattr(self.gas,prop)/getattr(native,prop),1,places=10)

    def test_trace_element_retained_and_high_temperature_brackets_finite(self):
        g=self.gas
        g.TPY=850,60e6,{"CH4":1,"O2":.13,"N2":1e-30}
        nitrogen=g.elemental_mass_fraction("N")
        g.equilibrate("TP")
        self.assertAlmostEqual(g.elemental_mass_fraction("N")/nitrogen,1,places=8)
        for temperature in (5500,6000):
            g.TPY=temperature,60e6,{"H2":.8,"H2O":.2}
            for prop in ("enthalpy_mass","entropy_mass","cp_mass","cv_mass","sound_speed"):
                self.assertTrue(math.isfinite(getattr(g,prop)))

    def test_cancelled_equilibrium_restores_state(self):
        g=self.gas
        old=g.state.copy()
        class Cancelled(BaseException): pass
        calls=0
        def cancel_after_guess():
            nonlocal calls
            calls+=1
            if calls>1: raise Cancelled()
        with patch("lre_workbench.runtime.tasks.checkpoint",side_effect=cancel_after_guess):
            with self.assertRaises(Cancelled): g.equilibrate("TP")
        np.testing.assert_allclose(g.state,old,rtol=1e-12,atol=1e-12)

    def test_dilute_chemistry_matches_independent_native_and_ideal_solvers(self):
        for temperature,ratio in ((850,.13),(3800,3.6)):
            compositions={}
            for eos in ("ideal-gas","Peng-Robinson",BM_EOS):
                gas,_=build_gas("gri30_highT",eos,"pr_supported_v1")
                gas.TPY=temperature,10.,{"CH4":1,"O2":ratio}
                gas.equilibrate("TP",solver="vcs")
                compositions[eos]=gas.Y.copy()
            for eos in ("ideal-gas","Peng-Robinson"):
                np.testing.assert_allclose(compositions[BM_EOS],compositions[eos],rtol=0,atol=2e-8)

    def test_independent_affinity_gate_rejects_corrupted_solver_standard_state(self):
        gas=self.gas
        gas.TPY=3800,33e6,{"CH4":1,"O2":3.6}
        original=gas._ideal
        old=gas.state.copy()
        class IncorrectStandardState:
            def __getattr__(self,name):
                value=getattr(original,name)
                if name=="standard_gibbs_RT":
                    return value+math.log(original.P/original.reference_pressure)
                return value
            def __setattr__(self,name,value): setattr(original,name,value)
        gas._ideal=IncorrectStandardState()
        try:
            with self.assertRaisesRegex(ValueError,"reaction-affinity"):
                gas.equilibrate("TP")
        finally:
            gas._ideal=original
        np.testing.assert_allclose(gas.state,old,rtol=1e-12,atol=1e-12)


if __name__ == "__main__": unittest.main()
