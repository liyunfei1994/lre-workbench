"""Injector display must preserve the saved design and never invent missing geometry."""

from copy import deepcopy
from dataclasses import asdict
import importlib.util
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from geometry.injector import compute_injector
from geometry import compute_geometry
from core import EngineInputs


def saved_outputs():
    return {
        "face_diameter_m": .31, "face_area_m2": math.pi * .155 ** 2,
        "num_elements": 383., "element_spacing_mm": 14.038,
        "d_ox_mm": 4.2, "d_post_mm": 5.2, "d_outer_mm": 7.8,
        "delta_fuel_mm": 1.3, "post_wall_thickness_mm": .5,
        "geometry_closure_passed": 1.,
    }


class InjectorGeometrySnapshotTests(unittest.TestCase):
    def test_full_geometry_assembler_reads_the_same_component(self):
        component = lambda **values: SimpleNamespace(outputs=values)
        inputs = EngineInputs()
        pump = component(delta_p_Pa=3e6, rho_kgm3=500., power_W=1e6)
        preburner = component(mdot_total_kgs=75., T_combustion_K=800., turbine_power_W=1e6)
        injector = component(**saved_outputs())
        injector.profile = {"geometry_contract": {"status": "closed", "warnings": []}}
        source = SimpleNamespace(inputs=inputs, components={
            "injector": injector, "ox_pump": pump, "fuel_pump": pump,
            "ox_preburner": preburner, "fuel_preburner": preburner,
            "nozzle": component(At_m2=math.pi * .1 ** 2,
                                Ae_m2=math.pi * .1 ** 2 * inputs.expansion_ratio),
        }, summary=dict(mdot_total_kg_s=150., mdot_ox_kg_s=100.,
                        mdot_fuel_kg_s=50., thrust_N=400e3))
        with patch("geometry.estimate_pump"), patch("geometry.estimate_turbine"), \
                patch("geometry.compute_preburner"):
            result = compute_geometry(source)
        self.assertEqual(result.injector.num_elements, 383)
        self.assertEqual(result.injector.face_diameter_m, .31)
        self.assertNotEqual(result.injector.face_diameter_m, result.chamber.diameter_m)
        self.assertEqual(result.injector.d_outer_mm, 7.8)
        self.assertEqual(result.injector.geometry_status, "closed")

    def test_component_geometry_wins_over_current_radius_and_flow(self):
        source = saved_outputs()
        original = deepcopy(source)
        first = asdict(compute_injector(.9, 99999., component_outputs=source))
        second = asdict(compute_injector(.02, 2., component_outputs=source))
        self.assertEqual(first, second)
        for key, value in source.items():
            self.assertEqual(first[key], value)
        self.assertEqual(source, original)
        self.assertEqual(first["geometry_source"], "components.injector.outputs")

    def test_missing_component_does_not_generate_diameter_or_count(self):
        geom = compute_injector(.15, 766.)
        self.assertIsNone(geom.num_elements)
        self.assertIsNone(geom.face_diameter_m)
        self.assertIsNone(geom.d_ox_mm)
        self.assertEqual(geom.geometry_status, "unavailable")

    def test_legacy_values_are_preserved_and_not_marked_validated(self):
        source = saved_outputs()
        del source["geometry_closure_passed"]
        del source["post_wall_thickness_mm"]
        # Historical inconsistent gap must remain an historical value, not be
        # silently recomputed when a saved record is opened.
        source["delta_fuel_mm"] = .99
        geom = compute_injector(.15, 766., component_outputs=source)
        self.assertEqual(geom.delta_fuel_mm, .99)
        self.assertIsNone(geom.post_wall_thickness_mm)
        self.assertIsNone(geom.geometry_closure_passed)
        self.assertEqual(geom.geometry_status, "legacy_unverified")
        self.assertTrue(geom.geometry_warnings)

    def test_invalid_count_and_dimensions_are_missing_without_rounding(self):
        geom = compute_injector(.15, 766., component_outputs={
            "num_elements": 383.7, "d_ox_mm": float("nan"),
            "d_post_mm": -1., "d_outer_mm": float("inf"),
            "face_diameter_m": True,
        })
        self.assertIsNone(geom.num_elements)
        self.assertIsNone(geom.d_ox_mm)
        self.assertIsNone(geom.d_post_mm)
        self.assertIsNone(geom.d_outer_mm)
        self.assertIsNone(geom.face_diameter_m)

    def test_saved_contract_warnings_are_preserved_without_mutation(self):
        profile = {"geometry_contract": {"status": "preliminary",
                                        "warnings": ["Layout exceeds available face area"]}}
        original = deepcopy(profile)
        geom = compute_injector(.15, 766., component_outputs=saved_outputs(),
                                component_profile=profile)
        self.assertEqual(geom.geometry_status, "preliminary")
        self.assertEqual(geom.geometry_warnings, profile["geometry_contract"]["warnings"])
        self.assertEqual(profile, original)


QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(QT_AVAILABLE, "Desktop drawing requires PySide6")
class InjectorDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from ui_drawings.injector import fig, explain
        cls.figure = staticmethod(fig)
        cls.explain = staticmethod(explain)

    def test_cross_section_circle_radii_match_saved_diameters(self):
        geometry = {"injector": asdict(compute_injector(.15, 766., component_outputs=saved_outputs()))}
        figure = self.figure(geometry)
        radii = [patch.radius for patch in figure.axes[1].patches]
        self.assertEqual(radii, [3.9, 2.6, 2.1])
        texts = " ".join(text.get_text() for ax in figure.axes for text in ax.texts)
        self.assertIn("383", texts)
        self.assertIn("不代表实际逐孔排布", texts)
        self.assertNotIn("L_post", texts)
        self.assertNotIn("recess", texts)

    def test_missing_diameters_do_not_draw_a_fake_element(self):
        figure = self.figure({"injector": {"face_diameter_m": .3, "num_elements": 50}})
        self.assertEqual(len(figure.axes[1].patches), 0)
        text = self.explain({"injector": {"face_diameter_m": .3}})
        self.assertIn("未记录", text)
        self.assertIn("未保存几何流量闭合校核", text)

    def test_invalid_radius_order_does_not_draw_overlapping_fluid(self):
        source = saved_outputs() | {"d_post_mm": 3.}
        figure = self.figure({"injector": source})
        self.assertEqual(len(figure.axes[1].patches), 0)


if __name__ == "__main__":
    unittest.main()
