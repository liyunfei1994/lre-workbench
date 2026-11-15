"""Frozen PR-BM heated nozzle numerical checks on a synthetic hot mixture."""
import math
from pathlib import Path
import sys
import unittest

from scipy.optimize import brentq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.components.cooling.geometry import build_regen_geometry
from lre_workbench.components.nozzles.heated import heated_nozzle
from lre_workbench.components.nozzles.model_ids import (
    HEATED_IDEAL_LEGACY_MODEL, HEATED_IDEAL_SAME_EOS_MODEL, HEATED_REAL_GAS_MODEL,
)
from lre_workbench.properties.gas_eos import BM_EOS
from lre_workbench.properties.thermochemistry import _get_gas, _gas_to_state, set_frozen_sp


class HeatedRealGasNozzleTests(unittest.TestCase):
    def setUp(self):
        self.pc, self.mdot, self.expansion = 33e6, 767.8, 34.34
        self.gas = _get_gas(methane_dataset="gri30_highT", gas_eos=BM_EOS, species_policy="pr_supported_v1")
        self.gas.TPY = 3773.6, self.pc, {"H2O": .49, "CO": .36, "CO2": .12, "H2": .03}
        self.chamber = _gas_to_state(self.gas, self.pc)
        self.geom = build_regen_geometry(r_t=.116, r_c=.116*math.sqrt(2.5), L_cyl=.256,
            expansion_ratio=self.expansion, n_points=24, counterflow=True, cover_eps=0)

    def solve(self, heat, *, thermo_method="legacy"):
        cells = [dict(cell, Q_W=heat*cell["dA_hot_m2"]/self.geom["hot_area_m2"]) for cell in self.geom["cells"]]
        return heated_nozzle(self.chamber, self.mdot, self.pc, self.expansion, 101325,
                             {"geometry": self.geom, "cells": cells}, thermo_method=thermo_method)

    def test_zero_heat_matches_independent_frozen_isentrope(self):
        result = self.solve(0.)
        profile = result.profile
        self.assertEqual(profile["model_id"], HEATED_REAL_GAS_MODEL)
        self.assertEqual(profile["gas_eos"], BM_EOS)
        self.assertEqual(profile["thermo_method"], "same_eos")
        self.assertEqual(profile["gas_model"]["eos_model_sha256"], self.chamber.model["eos_model_sha256"])
        self.assertEqual(profile["critical_x_m"], self.geom["x_throat_m"])

        def isentrope(pressure):
            set_frozen_sp(self.gas, self.chamber.entropy, pressure)
            velocity = math.sqrt(2*(self.chamber.enthalpy-self.gas.enthalpy_mass))
            return self.gas.density*velocity, velocity/self.gas.sound_speed

        p_sonic = brentq(lambda pressure: isentrope(pressure)[1]-1, .35*self.pc, .7*self.pc)
        flux = isentrope(p_sonic)[0]
        p_exit = brentq(lambda pressure: isentrope(pressure)[0]-flux/self.expansion, 1e4, p_sonic)
        self.assertAlmostEqual(result.outputs["At_m2"], self.mdot/flux, delta=1e-9)
        self.assertAlmostEqual(result.outputs["p_exit_Pa"], p_exit, delta=.01)
        self.assertLess(max(abs(n["s_JkgK"]-self.chamber.entropy) for n in profile["nodes"]), 1e-5)

    def test_heated_path_closes_real_gas_mass_momentum_and_energy(self):
        result = self.solve(120e6)
        profile = result.profile
        self.assertLess(profile["critical_x_m"], profile["geometric_throat_x_m"])
        self.assertLess(abs(profile["critical_regularity_residual_per_m"]), 1e-8)
        self.assertLess(profile["max_momentum_integral_residual_Pa"], profile["momentum_integral_tolerance_Pa"])
        self.assertLess(abs(profile["inlet_entropy_residual_JkgK"]), 1e-6)
        self.assertLess(abs(result.outputs["energy_residual_W"]), .01)
        self.assertAlmostEqual(result.outputs["heat_loss_W"], 120e6, delta=1e-6)
        at = result.outputs["At_m2"]
        for node in profile["nodes"]:
            self.gas.TPY = node["T_K"], node["p_Pa"], self.chamber.composition
            self.assertAlmostEqual(self.gas.density*at*node["area_ratio"]*node["velocity_ms"], self.mdot, delta=1e-6)
            self.assertAlmostEqual(node["M"], node["velocity_ms"]/self.gas.sound_speed, delta=1e-9)
            self.assertAlmostEqual(node["h0_Jkg"], self.gas.enthalpy_mass+.5*node["velocity_ms"]**2, delta=1e-4)
        self.assertGreater(profile["nodes"][0]["T_K"], 2976.)
        self.assertLess(profile["nodes"][-1]["T_K"], 2976.)
        self.assertTrue(all(cell["T0_K"] >= cell["T_static_K"] for cell in profile["cells"]))

    def test_ideal_common_method_is_explicit_and_preserves_legacy_solution(self):
        gas = _get_gas(methane_dataset="gri30_highT", gas_eos="ideal-gas", species_policy="pr_supported_v1")
        gas.TPY = self.chamber.temperature, self.pc, self.chamber.composition
        self.chamber = _gas_to_state(gas, self.pc)
        legacy = self.solve(120e6)
        common = self.solve(120e6, thermo_method="same_eos")
        self.assertEqual(legacy.profile["model_id"], HEATED_IDEAL_LEGACY_MODEL)
        self.assertEqual(common.profile["model_id"], HEATED_IDEAL_SAME_EOS_MODEL)
        self.assertEqual(common.profile["thermo_method"], "same_eos")
        self.assertEqual(common.profile["gas_model"]["eos"], "ideal-gas")
        for key in ("At_m2", "Isp_s", "T_exit_K", "p_exit_Pa"):
            self.assertAlmostEqual(common.outputs[key]/legacy.outputs[key], 1., delta=1e-8)
        for cell in common.profile["cells"]:
            self.assertAlmostEqual(cell["total_static_temperature_ratio"], cell["T0_K"]/cell["T_static_K"], places=12)
        self.assertNotIn("total_static_temperature_ratio", legacy.profile["cells"][-1])


if __name__ == "__main__":
    unittest.main()
