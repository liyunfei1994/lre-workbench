"""Geometry preservation checks; no cycle solve or FEA calculation required."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import math
import os
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import EngineInputs
from geometry import compute_geometry
from geometry.chamber import compute_chamber
from regen_geometry import build_regen_geometry
from chamber_design import (ChannelRow, default_rows_from_inputs, parse_channel_table,
                            serialize_channel_table, resolve_channel_distribution, snapshot_from_results)
from fea_chamber import _station_boundary


def profile():
    geometry = build_regen_geometry(r_t=.1, r_c=.15, L_cyl=.3, expansion_ratio=16.,
        n_points=80, n_channels=300, channel_width_m=.001,
        channel_height_m=.018, wall_thickness_m=.002)
    return {"model_id": "regen_quasi1d_ph_v1", "geometry": geometry}


class GeometryPreservationTests(unittest.TestCase):
    def test_supplied_dimensions_are_not_resized_or_clipped(self):
        g = compute_chamber(math.pi*.1**2, 2.25, 1., 33e6,
            channel_height_mm=18., liner_thickness_mm=2.5,
            q_throat_W_m2=200e6, n_channels=300, channel_width_mm=1.5)
        self.assertEqual(g.cooling_channel_height_mm, 18.)
        self.assertEqual(g.inner_wall_thickness_mm, 2.5)

    def test_saved_profile_controls_chamber_geometry_even_if_sizing_inputs_differ(self):
        p = profile()
        g = compute_chamber(math.pi*.10004**2, 4., 2., 33e6,
            channel_height_mm=5., liner_thickness_mm=.5,
            n_channels=150, channel_width_mm=1., cooling_profile=p)
        self.assertAlmostEqual(g.radius_m, .15)
        self.assertAlmostEqual(g.length_m, .3)
        self.assertAlmostEqual(g.contraction_ratio, 2.25)
        self.assertEqual(g.cooling_channel_height_mm, 18.)
        self.assertEqual(g.inner_wall_thickness_mm, 2.)
        self.assertEqual(g.num_cooling_channels, 300)
        self.assertAlmostEqual(g.cooling_channel_width_mm, 1.5)

    def test_invalid_geometry_fails_instead_of_clipping(self):
        for changes in (dict(l_star=.001), dict(channel_height_mm=-1.),
                        dict(liner_thickness_mm=0.), dict(channel_width_mm=100.),
                        dict(n_channels=300.5), dict(cooling_profile={}), dict(cooling_profile=[])):
            kwargs=dict(At=math.pi*.1**2, contraction_ratio=2.25, l_star=1., pc=33e6,
                        n_channels=300, channel_width_mm=1.5)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compute_chamber(**(kwargs | changes))

    @staticmethod
    def source(cooling=None):
        inputs=EngineInputs(chamber_contraction_ratio=4., chamber_l_star_m=2., expansion_ratio=16.)
        comp=lambda **out: SimpleNamespace(outputs=out)
        cooling_comp=comp(n_channels=300,channel_height_mm=18.)
        cooling_comp.profile=cooling
        pump=comp(delta_p_Pa=3e6,rho_kgm3=500.,power_W=1e6)
        pb=comp(mdot_total_kgs=75.,T_combustion_K=800.,turbine_power_W=1e6)
        components={"nozzle":comp(At_m2=math.pi*.1**2*1.0004,Ae_m2=math.pi*.1**2*1.0004*16.),
            "cooling_channel":cooling_comp,"ox_pump":pump,"fuel_pump":pump,
            "ox_preburner":pb,"fuel_preburner":pb}
        return SimpleNamespace(inputs=inputs,components=components,
            summary=dict(mdot_total_kg_s=150.,mdot_ox_kg_s=100.,mdot_fuel_kg_s=50.,thrust_N=400e3))

    def geometry(self, source):
        with ExitStack() as stack:
            for name in ("estimate_pump","estimate_turbine","compute_preburner","compute_injector"):
                stack.enter_context(patch("geometry."+name,return_value=None))
            return compute_geometry(source)

    def test_assembler_uses_fixed_profile_area_not_required_nozzle_area(self):
        source=self.source(profile())
        geom=self.geometry(source)
        self.assertAlmostEqual(geom.nozzle.throat_radius_m,.1)
        self.assertAlmostEqual(geom.nozzle.exit_radius_m,.4)
        self.assertAlmostEqual(geom.chamber.length_m,.3)
        self.assertEqual(source.components["nozzle"].outputs["At_m2"],math.pi*.1**2*1.0004)

    def test_assembler_reads_design_inputs_without_duplicate_arguments(self):
        source=self.source()
        geom=self.geometry(source)
        self.assertEqual(geom.chamber.contraction_ratio,4.)
        self.assertEqual(geom.chamber.l_star_m,2.)
        self.assertEqual(geom.chamber.inner_wall_thickness_mm,
                         source.inputs.cooling_wall_thickness_m*1000.)

    def test_saved_contour_angles_override_later_input_changes(self):
        p = profile()
        p["geometry"]["conv_half_angle_deg"] = 25.
        p["geometry"]["bell_fraction"] = .7
        source = self.source(p)
        source.inputs.chamber_conv_half_angle_deg = 45.
        source.inputs.nozzle_bell_fraction = .9
        with patch("geometry.compute_nozzle", wraps=__import__("geometry").compute_nozzle) as nozzle:
            g = self.geometry(source)
        self.assertEqual(nozzle.call_args.kwargs["theta_conv_deg"], 25.)
        self.assertEqual(nozzle.call_args.kwargs["bell_fraction"], .7)
        self.assertAlmostEqual(g.chamber.length_m, .3)

    def test_boundary_snapshot_retains_actual_fixed_hardware(self):
        p = profile(); source = self.source(p)
        source.components["main_chamber"] = SimpleNamespace(outputs={
            "cc_diameter_m": .4, "cc_length_m": .7, "cc_convergent_length_mm": 500.})
        source.states = {}
        source.inputs.expansion_ratio = 20.
        with patch("convergence.numerical_convergence", return_value={"status":"pass"}):
            snap = snapshot_from_results(source)
        self.assertIsNotNone(snap)
        self.assertAlmostEqual(snap.r_t_m, .1)
        self.assertEqual(snap.r_c_m, .15)
        self.assertEqual(snap.L_cyl_m, .3)
        self.assertEqual(snap.L_conv_m, p["geometry"]["L_conv_m"])
        self.assertEqual(snap.expansion_ratio, 16.)


class ChannelSnapshotTests(unittest.TestCase):
    def test_snapshot_preserves_all_original_nodes_and_actual_width_rule(self):
        p=profile(); g=p["geometry"]
        rows=default_rows_from_inputs(EngineInputs(),cooling_profile=p)
        rows=parse_channel_table(serialize_channel_table(rows))
        nodes=sorted(g["nodes"],key=lambda n:n["x_m"])
        actual=resolve_channel_distribution(rows,[n["x_m"] for n in nodes],
            [n["r_m"] for n in nodes],x_throat=g["x_throat_m"],L_cyl=g["L_cyl_m"],
            x_exit=g["full_exit_x_m"])
        self.assertEqual(len(rows),len(nodes))
        self.assertGreater(rows[-1].w_mm,rows[0].w_mm)
        for key, field in (("w_m","width_m"),("h_m","height_m"),("t_m","wall_thickness_m")):
            for value,node in zip(actual[key],nodes):
                self.assertAlmostEqual(value,node[field],delta=1e-14)

    def test_without_profile_no_fake_equivalent_table(self):
        with self.assertRaisesRegex(ValueError,"剖面"):
            default_rows_from_inputs(EngineInputs(),{"channel_height_mm":8.})

    def test_fractional_count_duplicate_and_nonfinite_coordinates_fail(self):
        with self.assertRaises(ValueError):
            parse_channel_table('[{"anchor":"throat","n":300.5}]')
        for rows in ([ChannelRow(x_mm=float("nan"))],
                     [ChannelRow(anchor="throat"),ChannelRow(anchor="x_mm",x_mm=500.)]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                resolve_channel_distribution(rows,[0.,1.],[.15,.2],x_throat=.5,L_cyl=.3,x_exit=1.)


class FEAGeometryTests(unittest.TestCase):
    @staticmethod
    def wallfield(width):
        return SimpleNamespace(throat_index=0,x=[0.],r=[.1],n_chan=[300],
            w_chan=[width],h_chan=[.018],t_liner=[.002],T_aw=[3500.],h_g=[5e4],
            h_c=[2e5],T_coolant=[300.],p_coolant=[60e6],p_gas=[20e6],t_wall_m=.002)

    def test_valid_narrow_rib_is_not_silently_widened(self):
        pitch=2*math.pi*.1/300
        data=_station_boundary(self.wallfield(.95*pitch),{"nozzle":{"throat_radius_m":.1}})
        self.assertEqual(data["w_channel_m"],.95*pitch)
        self.assertEqual(data["h_channel_m"],.018)
        self.assertEqual(data["t_liner_m"],.002)

    def test_nonpositive_rib_is_rejected_not_repaired(self):
        pitch=2*math.pi*.1/300
        with self.assertRaises(ValueError):
            _station_boundary(self.wallfield(1.01*pitch),{"nozzle":{"throat_radius_m":.1}})

    def test_invalid_saved_channel_geometry_cannot_fall_back_to_defaults(self):
        for name, value in (("w_chan",0.), ("h_chan",-1.), ("t_liner",float("nan")),
                            ("n_chan", 300.5)):
            wf = self.wallfield(.001)
            setattr(wf, name, [value])
            with self.subTest(name=name), self.assertRaises(ValueError):
                _station_boundary(wf,{"nozzle":{"throat_radius_m":.1}})


QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None
if QT_AVAILABLE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTableWidget
    from ui_chamber_design import ChamberDesignPanel


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is required for channel-table UI checks")
class ChannelTableQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_table_display_roundtrips_saved_node_precision(self):
        widget = QTableWidget(0, 6)
        fake = SimpleNamespace(chan_table=widget, _on_table_edited=Mock())
        rows = default_rows_from_inputs(EngineInputs(), cooling_profile=profile())
        for row in rows:
            ChamberDesignPanel._table_insert_row(fake, row)
        self.assertEqual(ChamberDesignPanel._table_rows(fake), rows)
        widget.deleteLater()

    def test_table_entry_does_not_truncate_fractional_channel_count(self):
        widget = QTableWidget(0, 6)
        fake = SimpleNamespace(chan_table=widget, _on_table_edited=Mock())
        ChamberDesignPanel._table_insert_row(fake, ChannelRow())
        widget.item(0,2).setText("300.5")
        row = ChamberDesignPanel._table_rows(fake)[0]
        with self.assertRaises(ValueError):
            row.validate()
        widget.deleteLater()

    def snapshot_action(self, inputs, result):
        return SimpleNamespace(_read_inputs_safe=lambda: inputs,
            main=SimpleNamespace(last_results=result), _table_set_rows=Mock(),
            _on_table_edited=Mock())

    def test_generate_action_uses_full_profile(self):
        result = GeometryPreservationTests.source(profile())
        fake = self.snapshot_action(deepcopy(result.inputs), result)
        with patch("ui_chamber_design.QMessageBox.information") as info, \
                patch("ui_chamber_design.QMessageBox.warning") as warning:
            ChamberDesignPanel._table_from_scalars(fake)
        warning.assert_not_called()
        info.assert_called_once()
        self.assertEqual(len(fake._table_set_rows.call_args.args[0]),
                         len(result.components["cooling_channel"].profile["geometry"]["nodes"]))

    def test_generate_action_rejects_stale_or_missing_profile(self):
        result = GeometryPreservationTests.source(profile())
        changed = deepcopy(result.inputs); changed.cooling_channel_width_m *= 1.1
        for inp, res in ((changed, result), (result.inputs, GeometryPreservationTests.source())):
            fake = self.snapshot_action(inp, res)
            with patch("ui_chamber_design.QMessageBox.warning") as warning:
                ChamberDesignPanel._table_from_scalars(fake)
            warning.assert_called_once()
            fake._table_set_rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
