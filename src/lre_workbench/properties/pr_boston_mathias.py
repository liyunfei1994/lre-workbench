"""Single gas-phase PR with the published Boston--Mathias alpha extrapolation.

The NASA reference polynomials and dilute-gas transport come from Cantera; ALL
mechanical, caloric and chemical-potential properties below derive from the same
PR residual Helmholtz potential. No effective/fictitious critical parameters are
passed to Cantera. TP chemistry solves the actual fugacity KKT equations.

Reference: Aspen Plus, Physical Property Methods and Models, v10.1, ch.3,
"Boston-Mathias Extrapolation", eq.(6), pp.3-41. Original reference: Boston and
Mathias (1980), Phase Equilibria in a Third-Generation Process Simulator.
https://sites.chemengr.ucsb.edu/~ceweb/courses/che184b/aspenplus/PhysPropMeth%26Models.pdf

This is a candidate, not validated high-temperature combustion thermochemistry.
The published extension is C1 at Tc (h/s continuous, second derivatives may
jump). It is not the native Cantera PR alpha, and must have a distinct identity.
Only the largest, locally stable gas root is represented; no phase-split search.
"""
from __future__ import annotations

from copy import deepcopy
import math

import cantera as ct
import numpy as np


BM_EOS = "Peng-Robinson-Boston-Mathias"
BM_MODEL_VERSION = "pr_boston_mathias_v2"
BM_SOURCE = "https://sites.chemengr.ucsb.edu/~ceweb/courses/che184b/aspenplus/PhysPropMeth%26Models.pdf"
_R = ct.gas_constant
_SQRT2 = math.sqrt(2.0)
_OMEGA_A = .45723552892138218
_OMEGA_B = .0777960739038885
_ROOT_ROUNDING_MARGIN = 8*math.sqrt(math.ulp(1.))


def alpha_boston_mathias(temperature, critical_temperatures, kappas):
    """Return alpha and its first TWO analytic temperature derivatives.

    This implements the published BM formula, including its C1 join. It does
    not replace that formula with an undocumented smoothing or temperature cap.
    """
    tc, k = np.asarray(critical_temperatures), np.asarray(kappas)
    tr = temperature / tc
    q = 1.0 + k * (1.0 - np.sqrt(tr))
    qp = -k / (2.0 * tc * np.sqrt(tr))
    qpp = k / (4.0 * tc**2 * tr**1.5)
    alpha = q*q
    first = 2*q*qp
    second = 2*(qp*qp + q*qpp)
    supercritical = tr > 1.0
    d = 1.0 + k/2.0
    c = 1.0 - 1.0/d
    exponent = 2*c*(1.0 - tr**d)
    extrapolated = np.exp(exponent)
    log_first = -k * tr**(d-1.0)/tc
    log_second = -k*(d-1.0)*tr**(d-2.0)/tc**2
    return (np.where(supercritical, extrapolated, alpha),
            np.where(supercritical, extrapolated*log_first, first),
            np.where(supercritical, extrapolated*(log_first**2+log_second), second))


class PengRobinsonBMPhase:
    """Small explicit Solution-like interface used by the engine's gas chain.

    Unimplemented Cantera methods are deliberately not silently forwarded: a
    forwarded caloric function would return ideal properties under a PR label.
    """
    thermo_model = BM_EOS
    backend_model_version = BM_MODEL_VERSION

    def __init__(self, species_data, parameters, name="gas"):
        self._species_data = deepcopy(species_data)
        self._parameters = deepcopy(parameters)
        species = [ct.Species.from_dict(deepcopy(row)) for row in species_data]
        self._ideal = ct.Solution(thermo="ideal-gas", species=species, kinetics=None,
                                  transport_model="mixture-averaged", name=name)
        self.name = name
        self.source = "explicit_NASA_species_with_PR_Boston_Mathias_residual"
        self._tc = np.array([parameters[s.name]["critical_temperature_K"] for s in species])
        pc = np.array([parameters[s.name]["critical_pressure_Pa"] for s in species])
        omega = np.array([parameters[s.name]["acentric_factor"] for s in species])
        self._k = np.where(omega <= .491, .37464+1.54226*omega-.26992*omega**2,
                           .374642+1.487503*omega-.164423*omega**2+.016666*omega**3)
        if np.any(self._k <= 0):
            raise ValueError("PR-BM candidate requires positive kappa; this parameter set needs separate validation.")
        self._a0 = _OMEGA_A * _R**2 * self._tc**2 / pc
        self._sqrt_a0 = np.sqrt(self._a0)
        self._bm_d = 1 + self._k/2
        self._bm_c = 1 - 1/self._bm_d
        self._max_tc = float(max(self._tc))
        self._b = _OMEGA_B * _R * self._tc / pc
        self._atoms = np.array([[self._ideal.n_atoms(i, e) for i in range(len(species))]
                                for e in self._ideal.element_names])
        self._T = 300.0
        self._P = 101325.0
        self._cache = None
        self._alpha_cache = None
        self._mixture_cache = None
        self._exact_volume = None
        self.TPY = 300.0, 101325.0, self._ideal.Y

    def clone(self):
        other = type(self)(self._species_data, self._parameters, self.name)
        other.state = self.state
        return other

    @property
    def T(self): return self._T
    @property
    def P(self): return self._P
    @property
    def Y(self): return self._ideal.Y
    @Y.setter
    def Y(self, value): self.TPY = self.T, self.P, value
    @property
    def X(self): return self._ideal.X
    @X.setter
    def X(self, value): self.TPX = self.T, self.P, value
    @property
    def TP(self): return self.T, self.P
    @TP.setter
    def TP(self, values): self.TPY = values[0], values[1], self.Y
    @property
    def TPY(self): return self.T, self.P, self.Y
    @TPY.setter
    def TPY(self, values):
        temperature, pressure, composition = values
        temperature = self.T if temperature is None else float(temperature)
        pressure = self.P if pressure is None else float(pressure)
        if not math.isfinite(temperature) or not math.isfinite(pressure) or min(temperature, pressure) <= 0:
            raise ValueError("PR-BM requires finite positive T and P.")
        self._ideal.TPY = temperature, pressure, composition
        self._T, self._P, self._cache = temperature, pressure, None
        self._exact_volume = None
        self._mixture_cache = None
    @property
    def TPX(self): return self.T, self.P, self.X
    @TPX.setter
    def TPX(self, values):
        self._ideal.TPX = values
        self.TPY = self._ideal.T, self._ideal.P, self._ideal.Y
    @property
    def TDY(self): return self.T, self.density, self.Y
    @TDY.setter
    def TDY(self, values):
        temperature, density, composition = values
        self.TPY = temperature, self.P, composition
        if not math.isfinite(density) or density <= 0:
            raise ValueError("PR-BM density must be finite and positive.")
        v = self.mean_molecular_weight / density
        a, _, _, b, _ = self._mix()
        if v <= b: raise ValueError("PR-BM molar volume does not exceed co-volume.")
        pressure = _R*self.T/(v-b)-a/(v*v+2*b*v-b*b)
        # Composition/T did not change: preserve their already computed mix.
        self._ideal.TP = self.T, pressure
        self._P, self._cache = pressure, None
        A, B = a*pressure/(_R*self.T)**2, b*pressure/(_R*self.T)
        z = v*pressure/(_R*self.T)
        c2, c1 = B-1, A-2*B-3*B*B
        # This Z already solves the EOS by construction. It is the largest
        # real root if the cubic is strictly increasing everywhere, or if Z
        # lies to the right of its last stationary point. This avoids a second
        # inversion even when the cubic has other real roots.
        derivative_discriminant = c2*c2-3*c1
        scale = max(1., abs(z), abs(c2), math.sqrt(abs(c1)))
        margin = _ROOT_ROUNDING_MARGIN*scale
        translated_z = 3*z+c2
        monotone = derivative_discriminant < -margin*margin
        beyond_last_turn = (derivative_discriminant > margin*margin
                            and translated_z > math.sqrt(derivative_discriminant)+3*margin)
        # Close to a multiple root, numerical roundoff can obscure the root
        # ordering even if Z appears to lie on the increasing branch.
        separated = translated_z*translated_z-derivative_discriminant > margin*scale
        if z > B and separated and (monotone or beyond_last_turn):
            self._exact_volume = v
        elif not math.isclose(self.density, density, rel_tol=1e-7):
            raise ValueError("PR-BM requested density is not the selected stable gas root.")
        self._properties()  # includes local mechanical and caloric checks
    @property
    def TD(self): return self.T, self.density
    @TD.setter
    def TD(self, values): self.TDY = values[0], values[1], self.Y
    @property
    def state(self): return np.r_[self.T, self.density, self.Y]
    @state.setter
    def state(self, value): self.TDY = float(value[0]), float(value[1]), value[2:]
    @property
    def n_species(self): return self._ideal.n_species
    @property
    def species_names(self): return self._ideal.species_names
    @property
    def element_names(self): return self._ideal.element_names
    @property
    def molecular_weights(self): return self._ideal.molecular_weights
    @property
    def mean_molecular_weight(self): return self._ideal.mean_molecular_weight
    @property
    def min_temp(self): return self._ideal.min_temp
    @property
    def max_temp(self): return self._ideal.max_temp
    @property
    def reference_pressure(self): return self._ideal.reference_pressure
    def species(self, *args): return self._ideal.species(*args)
    def species_name(self, index): return self._ideal.species_name(index)
    def species_index(self, name): return self._ideal.species_index(name)
    def n_atoms(self, *args): return self._ideal.n_atoms(*args)
    def elemental_mass_fraction(self, element): return self._ideal.elemental_mass_fraction(element)
    @property
    def viscosity(self): return self._ideal.viscosity
    @property
    def thermal_conductivity(self): return self._ideal.thermal_conductivity
    @property
    def transport_model(self): return self._ideal.transport_model

    def _mix(self, x=None):
        current_composition = x is None
        if current_composition and self._mixture_cache is not None:
            return self._mixture_cache
        if x is None: x = self.X
        if self._alpha_cache is None or self._alpha_cache[0] != self.T:
            if self.T > self._max_tc:
                # Same published BM expression, evaluated directly for sqrt(a)
                # and its derivatives. Hot gas has no subcritical species, so
                # avoid constructing unused subcritical alpha and derivatives.
                power = (self.T/self._tc)**self._bm_d
                root = self._sqrt_a0*np.exp(self._bm_c*(1-power))
                log_first = -.5*self._k*power/self.T
                root_p = root*log_first
                root_pp = root*(log_first**2+(self._bm_d-1)*log_first/self.T)
            else:
                alpha, ap, app = alpha_boston_mathias(self.T, self._tc, self._k)
                root = np.sqrt(self._a0*alpha)
                root_p = root * .5*ap/alpha
                root_pp = root * (.5*app/alpha - .25*(ap/alpha)**2)
            self._alpha_cache = self.T, root, root_p, root_pp
        _, root, root_p, root_pp = self._alpha_cache
        q, qp, qpp = float(x@root), float(x@root_p), float(x@root_pp)
        result = q*q, 2*q*qp, 2*(qp*qp+q*qpp), float(x@self._b), root*q
        if current_composition:
            self._mixture_cache = result
        return result

    def _mechanical(self, x=None):
        a, ap, app, b, a_cross = self._mix(x)
        A, B = a*self.P/(_R*self.T)**2, b*self.P/(_R*self.T)
        if x is None and self._exact_volume is not None:
            v = self._exact_volume
            z = v*self.P/(_R*self.T)
        else:
            roots = np.roots([1.0, B-1.0, A-2*B-3*B*B, B**3+B*B-A*B])
            physical = [float(root.real) for root in roots if abs(root.imag) < 1e-9 and root.real > B]
            if not physical: raise ValueError("PR-BM has no physical gas-volume root.")
            z = max(physical)
            v = z*_R*self.T/self.P
        denominator = v*v+2*b*v-b*b
        pv = -_R*self.T/(v-b)**2+2*a*(v+b)/denominator**2
        pt = _R/(v-b)-ap/denominator
        if pv >= 0: raise ValueError("PR-BM gas root fails local mechanical stability.")
        logarithm = math.log((v+(1+_SQRT2)*b)/(v+(1-_SQRT2)*b))
        integral = logarithm/(2*_SQRT2*b)
        lnphi = (self._b/b*(z-1)-math.log(z-B)
                 -(2*a_cross-a*self._b/b)*integral/(_R*self.T))
        return a, ap, app, b, z, v, pv, pt, integral, lnphi

    def _properties(self):
        if self._cache is None:
            a, ap, app, b, z, v, pv, pt, integral, lnphi = self._mechanical()
            h = self._ideal.enthalpy_mole+_R*self.T*(z-1)+(self.T*ap-a)*integral
            s = self._ideal.entropy_mole+_R*math.log(z-b*self.P/(_R*self.T))+ap*integral
            cv = self._ideal.cv_mole+self.T*app*integral
            cp = cv-self.T*pt*pt/pv
            if not all(math.isfinite(q) for q in (h,s,cp,cv)) or min(cp,cv) <= 0:
                raise ValueError("PR-BM caloric state fails finite positive heat-capacity check.")
            self._cache = dict(h=h, s=s, cp=cp, cv=cv, v=v, pv=pv, pt=pt, lnphi=lnphi)
        return self._cache
    @property
    def density(self): return self.mean_molecular_weight/self._properties()["v"]
    density_mass = density
    @property
    def enthalpy_mole(self): return self._properties()["h"]
    @property
    def enthalpy_mass(self): return self.enthalpy_mole/self.mean_molecular_weight
    @property
    def entropy_mole(self): return self._properties()["s"]
    @property
    def entropy_mass(self): return self.entropy_mole/self.mean_molecular_weight
    @property
    def cp_mole(self): return self._properties()["cp"]
    @property
    def cp_mass(self): return self.cp_mole/self.mean_molecular_weight
    @property
    def cv_mole(self): return self._properties()["cv"]
    @property
    def cv_mass(self): return self.cv_mole/self.mean_molecular_weight
    @property
    def int_energy_mass(self): return self.enthalpy_mass-self.P/self.density
    @property
    def gibbs_mole(self): return self.enthalpy_mole-self.T*self.entropy_mole
    @property
    def chemical_potentials(self):
        return self._ideal.chemical_potentials+_R*self.T*self._properties()["lnphi"]
    @property
    def isothermal_compressibility(self):
        q=self._properties(); return -1/(q["v"]*q["pv"])
    @property
    def thermal_expansion_coeff(self):
        q=self._properties(); return -q["pt"]/(q["v"]*q["pv"])
    @property
    def sound_speed(self):
        q=self._properties()
        return math.sqrt(-q["v"]**2*q["cp"]/q["cv"]*q["pv"]/self.mean_molecular_weight)

    def _frozen(self, target, pressure, kind):
        from scipy.optimize import brentq
        from lre_workbench.runtime.tasks import checkpoint
        old = self.TPY
        y = self.Y.copy()
        prop = "enthalpy_mass" if kind == "HP" else "entropy_mass"
        tolerance = max(.02, abs(target)*1e-9) if kind == "HP" else max(1e-7, abs(target)*1e-10)
        def residual(t):
            checkpoint()
            self.TPY = t, pressure, y
            return getattr(self, prop)-target
        # First use a local bracket. Avoid cold dense/unstable trial states when
        # solving a high-temperature gas path. Expand without clipping targets.
        lo, hi = max(50., self.T*.8), self.T*1.2
        try:
            # A local Newton solve uses the exact frozen derivative cp or cp/T.
            # Limit steps and retain the original bracketed method on failure;
            # no extrapolated/cached state is accepted without its residual.
            try:
                temperature = self.T
                for _ in range(8):
                    error = residual(temperature)
                    derivative = self.cp_mass if kind == "HP" else self.cp_mass/self.T
                    step = error/derivative
                    if abs(error) <= tolerance and abs(step) <= max(1e-9, temperature*1e-12):
                        return
                    temperature -= max(-.2*temperature, min(.2*temperature, step))
                    if not math.isfinite(temperature) or temperature < 20.:
                        break
            except Exception:
                pass
            for _ in range(25):
                flo, fhi = residual(lo), residual(hi)
                if flo <= 0 <= fhi: break
                if flo > 0: lo = max(20., lo*.7)
                if fhi < 0: hi *= 1.4
            else: raise ValueError(f"PR-BM {kind}: no continuous temperature bracket.")
            t = brentq(residual, lo, hi, xtol=1e-9, rtol=1e-12)
            error = residual(t)
            if abs(error) > tolerance: raise ValueError(f"PR-BM {kind}: residual {error:g} exceeds tolerance.")
        except BaseException:
            self.TPY = old
            raise
    @property
    def HP(self): return self.enthalpy_mass, self.P
    @HP.setter
    def HP(self, values): self._frozen(*values, "HP")
    @property
    def SP(self): return self.entropy_mass, self.P
    @SP.setter
    def SP(self, values): self._frozen(*values, "SP")
    @property
    def HPY(self): return self.enthalpy_mass, self.P, self.Y
    @HPY.setter
    def HPY(self, values):
        self.Y = values[2]
        self.HP = values[:2]

    def equilibrate(self, constraint, solver="auto", rtol=1e-10, max_steps=1000, **kwargs):
        """Solve TP using nonideal chemical potentials and element conservation.

        Log mole numbers retain tiny species without a fabricated concentration
        floor in the chemical potential. Species absent due to zero inventory
        are excluded exactly. Acceptance checks KKT, elements, and Gibbs descent.
        No kinetics, ideal-equilibrium fallback or phase-stability claim.
        """
        old = self.TPY
        try:
            self._equilibrate_tp(constraint, rtol=rtol, max_steps=max_steps)
        except BaseException:
            self.TPY = old
            raise

    def _equilibrate_tp(self, constraint, rtol, max_steps):
        if constraint != "TP":
            raise ValueError("PR-BM exposes TP chemistry only; use the verified HP outer solver.")
        from scipy.optimize import least_squares
        from scipy.special import logsumexp
        from lre_workbench.runtime.tasks import checkpoint
        old = self.TPY
        checkpoint()
        initial_n = self.Y/self.molecular_weights
        scale = float(initial_n.sum())
        inventory = self._atoms@initial_n/scale
        present = inventory > 0.0
        allowed = np.all(self._atoms[~present] == 0, axis=0)
        matrix = self._atoms[present][:,allowed]
        budget = inventory[present]
        if not np.any(allowed): raise ValueError("PR-BM equilibrium has no element-compatible species.")
        # Cantera is only the initial guess; final equations contain PR fugacity.
        # VCS can form a trial guess above a NASA table's declared endpoint;
        # that bracket is computational only, never an applicability claim.
        self._ideal.equilibrate("TP", solver="vcs", rtol=rtol, max_steps=max_steps)
        guess = self._ideal.Y/self.molecular_weights/scale
        initial_log = np.log(np.maximum(guess[allowed], 1e-280))
        self.TPY = old
        # Cantera's ideal-gas STANDARD chemical potential is evaluated at the
        # phase's CURRENT pressure; standard_gibbs_RT already contains ln(P/P0).
        # Adding that term again solves a different, fictitiously pressurized
        # chemistry. In particular, delta-n != 0 reaction affinities expose it.
        standard_g = self._ideal.standard_gibbs_RT[allowed]
        mu_guess = standard_g+initial_log-logsumexp(initial_log)
        lam = np.linalg.lstsq(matrix.T, mu_guess, rcond=None)[0]
        nallowed = int(np.sum(allowed))
        def residual(vector):
            checkpoint()
            logs, multiplier = vector[:nallowed], vector[nallowed:]
            logtotal = logsumexp(logs)
            x = np.zeros(self.n_species)
            x[allowed] = np.exp(logs-logtotal)
            lnphi = self._mechanical(x)[-1][allowed]
            chemistry = standard_g+logs-logtotal+lnphi-matrix.T@multiplier
            amounts = np.exp(np.clip(logs, -745, 700))
            balance = (matrix@amounts-budget)/budget
            return np.r_[chemistry, balance]
        def jacobian(vector):
            # Analytic derivative of the SAME fugacity formula at constant T/P.
            # This avoids N expensive cubic inversions per Newton iteration.
            checkpoint()
            logs=vector[:nallowed]
            x=np.zeros(self.n_species)
            x[allowed]=np.exp(logs-logsumexp(logs))
            a,_,_,b,z,v,pv,_,integral,_=self._mechanical(x)
            root=self._alpha_cache[1]
            cross=root*float(x@root)
            D=v*v+2*b*v-b*b
            db=x[allowed]*(self._b[allowed]-b)
            da=2*x[allowed]*(cross[allowed]-a)
            dcross=x[allowed][None,:]*(np.outer(root,root[allowed])-cross[:,None])
            dv=-((_R*self.T/(v-b)**2+a*(2*v-2*b)/D**2)*db-da/D)/pv
            dz=self.P/(_R*self.T)*dv
            dB=self.P/(_R*self.T)*db
            ratio=self._b/b
            dr=-ratio[:,None]*db[None,:]/b
            coefficient=2*cross-a*ratio
            dc=2*dcross-ratio[:,None]*da[None,:]-a*dr
            di=-dv/D+(v/D-integral)*db/b
            derivative=(dr*(z-1)+ratio[:,None]*dz[None,:]
                        -(dz-dB)[None,:]/(z-b*self.P/(_R*self.T))
                        -(dc*integral+coefficient[:,None]*di[None,:])/(_R*self.T))
            chem=np.eye(nallowed)-x[allowed][None,:]+derivative[allowed]
            balance=matrix*np.exp(np.clip(logs,-745,700))[None,:]/budget[:,None]
            return np.block([[chem,-matrix.T],[balance,np.zeros((len(budget),len(budget)))]])
        result = least_squares(residual, np.r_[initial_log,lam], xtol=1e-12, ftol=1e-12,
                               gtol=1e-12, max_nfev=max_steps, x_scale="jac", jac=jacobian)
        errors = residual(result.x)
        if not result.success or max(abs(errors)) > max(2e-8, rtol*100):
            self.TPY = old
            raise ValueError(f"PR-BM TP equilibrium failed KKT/element acceptance: {max(abs(errors)):.4g}.")
        n = np.zeros(self.n_species)
        n[allowed] = np.exp(np.clip(result.x[:nallowed], -745, 700))
        initial_g = float(initial_n@self.chemical_potentials)
        self.TPX = self.T,self.P,n
        final_n = self.Y/self.molecular_weights
        if max(abs(self._atoms@final_n-self._atoms@initial_n)) > 1e-9:
            self.TPY = old
            raise ValueError("PR-BM TP equilibrium did not conserve elements.")
        actual_budget = (self._atoms@final_n/scale)[present]
        if max(abs(actual_budget-budget)/budget) > 2e-8:
            self.TPY = old
            raise ValueError("PR-BM TP equilibrium did not conserve trace-element inventory.")
        # Independent gate from species reference h(T)-T*s(T), NOT the
        # optimizer's standard_gibbs_RT expression. Keep ALL element-compatible
        # species in log mole-number space, including concentrations below the
        # floating-point / Cantera chemical-potential floor. No trace-species
        # threshold is used to hide a nonzero reaction affinity.
        reference_mu = np.array([sp.thermo.h(self.T)-self.T*sp.thermo.s(self.T)
                                 for sp in self.species()])/(_R*self.T)
        log_x = result.x[:nallowed]-logsumexp(result.x[:nallowed])
        actual_mu = (reference_mu[allowed]+math.log(self.P/self.reference_pressure)
                     +log_x+self._properties()["lnphi"][allowed])
        atom_basis = matrix.T
        potentials = np.linalg.lstsq(atom_basis, actual_mu, rcond=None)[0]
        if max(abs(actual_mu-atom_basis@potentials), default=0.0) > 2e-7:
            self.TPY = old
            raise ValueError("PR-BM TP independent chemical potentials fail reaction-affinity closure.")
        if float(final_n@self.chemical_potentials) > initial_g+max(.01,abs(initial_g)*1e-8):
            self.TPY = old
            raise ValueError("PR-BM TP equilibrium did not decrease Gibbs energy.")
        self._properties()
