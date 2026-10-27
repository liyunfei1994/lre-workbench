"""Numerical geometry and provenance checks for the explicit-angle bell method."""
import math
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from core import EngineInputs, ValidationError
from geometry.nozzle import full_hot_gas_geometry, compute_nozzle, top_bell_angles
from regen_geometry import build_regen_geometry
from regen_coupling import cooling_arguments, fixed_contour_inputs
from cooling_marching import march_regen_circuit
from tests.test_regen_circuit import BASE
from tests import test_geometry_consistency as geometry_checks

EXPLICIT = dict(nozzle_contour_method='parabolic_bell_explicit_v1',
                nozzle_initial_angle_deg=35., nozzle_exit_angle_deg=10.)


class NozzleContourMethodTests(unittest.TestCase):
    def test_new_inputs_are_explicit_and_loading_missing_method_preserves_legacy(self):
        self.assertEqual(EngineInputs().nozzle_contour_method, EXPLICIT['nozzle_contour_method'])
        raw = asdict(EngineInputs()); del raw['nozzle_contour_method']
        loaded = EngineInputs.from_dict(raw)
        self.assertEqual(loaded.nozzle_contour_method, 'legacy_log_v1')
        self.assertNotIn('nozzle_contour_method', raw)
        new = EngineInputs.from_dict(raw | EXPLICIT)
        self.assertEqual(new.nozzle_contour_method, EXPLICIT['nozzle_contour_method'])

    def test_explicit_profile_endpoints_area_ratio_and_tangents(self):
        rt, eps, lc = .1, 34.34, .3
        g = full_hot_gas_geometry(rt, rt*math.sqrt(eps), .16, eps, lc,
                                  n_points=16000, **EXPLICIT)
        x, r, xt = g['contour_x'], g['contour_r'], g['x_throat_m']
        self.assertEqual(x[0], 0.)
        self.assertEqual(r[0], .16)
        self.assertTrue(all(b > a for a,b in zip(x,x[1:])))
        throat = min(range(len(r)), key=r.__getitem__)
        self.assertEqual(x[throat], xt)
        self.assertEqual(r[throat], rt)
        self.assertAlmostEqual((r[-1]/rt)**2, eps, places=12)
        self.assertTrue(all(b >= a for a,b in zip(r[throat:],r[throat+1:])))
        rd = .382*rt
        xn = xt + rd*math.sin(math.radians(35))
        ix = min(range(len(x)), key=lambda k: abs(x[k]-xn))
        left = math.degrees(math.atan((r[ix]-r[ix-1])/(x[ix]-x[ix-1])))
        right = math.degrees(math.atan((r[ix+1]-r[ix])/(x[ix+1]-x[ix])))
        exit_angle = math.degrees(math.atan((r[-1]-r[-2])/(x[-1]-x[-2])))
        self.assertAlmostEqual(left,35.,delta=.02)
        self.assertAlmostEqual(right,35.,delta=.02)
        self.assertAlmostEqual(exit_angle,10.,delta=.02)
        self.assertEqual(g['angle_source'],'specified_design_angles')
        self.assertEqual(g['coordinate_origin'],'injector_face')

    def test_legacy_api_and_metadata_do_not_claim_optimized_or_use_new_angles(self):
        expected = top_bell_angles(34.34,.8)
        n = compute_nozzle(.1,.1*math.sqrt(34.34),.16,34.34,2.56,.3,
                           nozzle_initial_angle_deg=40.,nozzle_exit_angle_deg=8.)
        self.assertEqual((n.initial_expansion_angle_deg,n.exit_angle_deg),expected)
        self.assertEqual(n.nozzle_contour_method,'legacy_log_v1')
        self.assertEqual(n.angle_source,'historical_log_rule_unvalidated')
        self.assertNotEqual(n.nozzle_type,'TOP')

    def test_incompatible_angles_are_rejected_not_repaired(self):
        for changes in (dict(nozzle_initial_angle_deg=10.),dict(nozzle_exit_angle_deg=40.),
                        dict(nozzle_initial_angle_deg=15.,nozzle_exit_angle_deg=10.),
                        dict(nozzle_initial_angle_deg=float('nan')),
                        dict(nozzle_exit_angle_deg=-1.),dict(nozzle_contour_method='TOP')):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                EngineInputs(**(EXPLICIT | changes))
        # Tight contraction previously erased the cylinder blend arc silently.
        with self.assertRaisesRegex(ValidationError,'圆角'):
            EngineInputs(chamber_contraction_ratio=1.21,chamber_conv_half_angle_deg=60.)
        legacy = full_hot_gas_geometry(.1,.4,.11,16.,.3,theta_conv_deg=60.)
        self.assertEqual(legacy['convergent_tangent_continuity'],'legacy_cylinder_corner')

    def test_common_regen_profile_retains_uncooled_extension_and_definition(self):
        g = build_regen_geometry(r_t=.1,r_c=.16,L_cyl=.3,expansion_ratio=34.34,
                                cover_eps=8.,n_points=120,**EXPLICIT)
        self.assertTrue(any(not n['cooling_covered'] for n in g['full_nodes']))
        self.assertAlmostEqual(g['full_nodes'][-1]['area_ratio'],34.34)
        self.assertEqual(g['initial_expansion_angle_deg'],35.)
        args = cooling_arguments(SimpleNamespace(inputs=EngineInputs(**EXPLICIT)))
        for key,value in EXPLICIT.items(): self.assertEqual(args[key],value)
        solved = march_regen_circuit(**BASE, heat_transfer_enabled=False, **EXPLICIT)
        self.assertEqual(solved['profile']['geometry']['nozzle_contour_method'],EXPLICIT['nozzle_contour_method'])
        self.assertEqual(solved['profile']['geometry']['exit_angle_deg'],10.)

    def test_saved_coordinates_own_geometry_after_inputs_change(self):
        g = build_regen_geometry(r_t=.1,r_c=.15,L_cyl=.3,expansion_ratio=16.,
                                n_points=80,cover_eps=8.,**EXPLICIT)
        p = {'model_id':'regen_quasi1d_ph_v1','geometry':g}
        source = geometry_checks.GeometryPreservationTests.source(p)
        source.inputs.nozzle_initial_angle_deg=42.
        source.inputs.nozzle_exit_angle_deg=5.
        source.inputs.nozzle_contour_method='legacy_log_v1'
        actual = geometry_checks.GeometryPreservationTests().geometry(source).nozzle
        points = [n for n in g['full_nodes'] if n['x_m'] >= .3-1e-12]
        self.assertEqual(actual.contour_x,[n['x_m']-g['x_throat_m'] for n in points])
        self.assertEqual(actual.contour_r,[n['r_m'] for n in points])
        self.assertEqual(actual.initial_expansion_angle_deg,35.)
        self.assertEqual(actual.exit_angle_deg,10.)
        self.assertEqual(actual.nozzle_contour_method,EXPLICIT['nozzle_contour_method'])
        frozen = fixed_contour_inputs({'cooling_hardware':{'geometry':g},
            'contour_inputs':{'nozzle_initial_angle_deg':42.,'nozzle_exit_angle_deg':5.}})
        self.assertEqual(frozen['nozzle_initial_angle_deg'],35.)
        self.assertEqual(frozen['nozzle_exit_angle_deg'],10.)
        old = deepcopy(g); old.pop('nozzle_contour_method')
        self.assertEqual(fixed_contour_inputs({'cooling_hardware':{'geometry':old}})['nozzle_contour_method'],'legacy_log_v1')
        self.assertEqual(fixed_contour_inputs({})['nozzle_contour_method'],'legacy_log_v1')

    def test_ffsc_and_gg_throttle_use_saved_contour_not_later_edits(self):
        from unittest.mock import patch
        from cycle import FFSCCycleModel
        from gg_cycle import GGCycleModel
        from web_tests.test_throttle_analysis import source_fixture, source_baseline, solved_fixture
        for model_class, cycle in ((FFSCCycleModel, "ffsc"), (GGCycleModel, "gg")):
            with self.subTest(cycle=cycle):
                source = source_fixture()
                source["result"]["calculation"]["inputs"]["cycle_type"] = cycle
                saved, baseline = source_baseline(source)
                changed = dict(saved, nozzle_initial_angle_deg=42., nozzle_exit_angle_deg=5.,
                               nozzle_bell_fraction=.9, throttle_steps=2)
                seen = []
                def evaluate(instance, **kwargs):
                    seen.append(asdict(instance.inputs))
                    return solved_fixture(asdict(instance.inputs))
                with patch.object(model_class, "evaluate", evaluate):
                    result = model_class(EngineInputs(**changed)).throttle_sweep(
                        design_baseline=baseline, include_cooling_marching=False)
                self.assertEqual(len(result), 2)
                self.assertTrue(all(row["results"] is not None for row in result))
                self.assertGreater(len(seen), 0)
                for inputs in seen:
                    self.assertEqual(inputs["nozzle_initial_angle_deg"], saved["nozzle_initial_angle_deg"])
                    self.assertEqual(inputs["nozzle_exit_angle_deg"], saved["nozzle_exit_angle_deg"])
                    self.assertEqual(inputs["nozzle_bell_fraction"], saved["nozzle_bell_fraction"])


if __name__ == '__main__': unittest.main()
