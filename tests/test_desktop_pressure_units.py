"""Exercise desktop pressure presentation and raw-draft migration without Qt."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import math
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lre_workbench.domain.metadata import KEY_LABELS
from lre_workbench.desktop.pressure_units import recovery_pressure_text


def load_methods(filename, class_name, names, extra=None):
    path = ROOT / "src/lre_workbench/desktop" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and (t.id.startswith("COMPONENT_") or t.id in ("_LOX_PATH", "_CH4_PATH"))
        for t in n.targets)] if filename == "legacy_window.py" else []
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    namespace = {"math": math, "deepcopy": deepcopy, "Path": Path,
                 "KEY_LABELS": KEY_LABELS, "MATPLOTLIB_AVAILABLE": True}
    namespace.update(extra or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class DesktopPressureUnitsTests(unittest.TestCase):
    def test_legacy_bar_only_and_pa_only_components_use_mpa_without_mutating_record(self):
        ns = load_methods("legacy_window.py", "MainWindow", ["_component_rows", "_component_row", "_fmt"])
        window = SimpleNamespace(_fmt=ns["_fmt"])
        window._component_row = lambda *args: ns["_component_row"](window, *args)
        for key, raw, expected in (("p_in_bar", 9.5, .95), ("p_out_bar", 700, 70),
                                   ("delta_p_bar", 690.5, 69.05), ("injector_dp_bar", .5, .05),
                                   ("delta_p_Pa", 50000, .05), ("injector_dp_Pa", 50000, .05),
                                   ("preburner_pressure_Pa", 40000000, 40),
                                   ("tank_pressure_bar", 3, .3), ("boost_out_pressure_bar", 10, 1),
                                   ("main_pump_inlet_pressure_bar", 9.5, .95), ("boost_pump_dp_bar", 7, .7)):
            with self.subTest(key=key):
                values = {key: raw}
                before = deepcopy(values)
                row = ns["_component_rows"](window, "ox_pump", values)[0]
                self.assertIn("[MPa]", row[0])
                self.assertAlmostEqual(float(row[1]), expected)
                self.assertEqual(values, before)

    def test_flowpath_chart_converts_values_and_retains_low_pressure_resolution(self):
        ns = load_methods("legacy_window.py", "MainWindow", ["_draw_states_flowpath"])
        axes = {key: Mock() for key in ("p_lox", "p_ch4", "t_lox", "t_ch4")}
        window = SimpleNamespace(states_flow_fig=Mock(), states_flow_canvas=Mock(),
                                 _sf_axes=axes, _style_ax_base=Mock())
        result = SimpleNamespace(states={"lox_tank": SimpleNamespace(pressure=3e5, temperature=70),
                                          "fuel_tank": SimpleNamespace(pressure=4e5, temperature=100)})
        ns["_draw_states_flowpath"](window, result)
        for axis, expected in (("p_lox", .3), ("p_ch4", .4)):
            self.assertEqual(axes[axis].plot.call_args.args[1], [expected])
            self.assertIn("MPa", axes[axis].set_ylabel.call_args.args[0])
            self.assertEqual(axes[axis].annotate.call_args.args[0], str(expected))

    def test_waterfall_uses_mpa_for_node_values_and_pressure_differences(self):
        ns = load_methods("legacy_window.py", "MainWindow", ["_draw_pressure_waterfall"])
        axes = {key: Mock(spines={}) for key in ("lox", "ch4")}
        window = SimpleNamespace(waterfall_fig=Mock(), waterfall_canvas=Mock(),
                                 _wf_axes=axes, _style_ax_base=Mock())
        values = {"lox_tank": 3e5, "lox_pump_inlet": 9.5e5, "lox_after_pump": 70e6,
                  "ox_preburner_outlet": 65e6, "ox_turbine_exhaust": 35e6,
                  "fuel_tank": 3e5, "fuel_pump_inlet": 9.5e5, "fuel_after_pump": 70e6,
                  "fuel_after_cooling": 69e6, "fuel_preburner_outlet": 65e6,
                  "fuel_turbine_exhaust": 35e6, "main_chamber": 33e6}
        branches = [{"id": name, "source_pressure_Pa": 70e6,
                     "line_drop_Pa": 50000, "valve_drop_Pa": 100000}
                    for name in ("ox_to_opb", "fuel_to_fpb")]
        result = SimpleNamespace(states={k: SimpleNamespace(pressure=v) for k, v in values.items()},
                                 steady_closure={"feed_network": {"branches": branches}})
        ns["_draw_pressure_waterfall"](window, result)
        ox_values = axes["lox"].barh.call_args.args[1]
        self.assertEqual(ox_values[:3], [.3, .95, 70])
        self.assertAlmostEqual(ox_values[3], 69.95)
        self.assertAlmostEqual(ox_values[4], 69.85)
        self.assertIn("MPa", axes["lox"].set_xlabel.call_args.args[0])
        annotations = [call.args[2] for call in axes["lox"].text.call_args_list]
        self.assertIn("0.30 MPa", annotations)
        self.assertTrue(any("0.65" in text for text in annotations))

    def test_legacy_draft_and_baseline_convert_once_and_preserve_incomplete_text(self):
        block = type("Blocker", (), {"__init__": lambda *_: None, "unblock": lambda *_: None})
        ns = load_methods("case_safety.py", "CaseSafetyMixin", ["_restore_case_snapshot", "_case_recovery_snapshot"],
                          {"QSignalBlocker": block, "QComboBox": type("Combo", (), {})})
        raw = {"tank_pressure_pa": "3", "feed_line_dp_pa": "0.5", "gg_turbine_backpressure_pa": "4",
               "pc": "33", "ambient_pressure": "0.101325"}
        snapshot = {"raw_inputs": raw, "extra_inputs": {}, "analysis_inputs": {},
                    "saved_inputs": {"raw_inputs": dict(raw), "extra_inputs": {}, "analysis_inputs": {}}}
        original = deepcopy(snapshot)
        edits = {key: Mock() for key in raw}
        window = SimpleNamespace(input_edits=edits, _analysis_restore_operations=lambda _: [])
        ns["_restore_case_snapshot"](window, snapshot)
        expected = dict(raw, tank_pressure_pa="0.3", feed_line_dp_pa="0.05", gg_turbine_backpressure_pa="0.4")
        for key, value in expected.items():
            self.assertEqual(edits[key].setText.call_args.args[0], value)
        self.assertEqual(window._case_baseline["raw_inputs"], expected)
        self.assertTrue(window._case_recovery_forced_dirty)
        self.assertEqual(snapshot, original)
        window._case_edit_state = lambda: {"raw_inputs": dict(expected), "extra_inputs": {}, "analysis_inputs": {}}
        new = ns["_case_recovery_snapshot"](window)
        self.assertEqual(new["pressure_display_unit"], "MPa")
        ns["_restore_case_snapshot"](window, new)
        self.assertEqual(edits["tank_pressure_pa"].setText.call_args.args[0], "0.3")
        incomplete = {"tank_pressure_pa": "1e", "feed_line_dp_pa": "", "pc": "33"}
        self.assertEqual(recovery_pressure_text(incomplete, "bar"), incomplete)
        with self.assertRaises(ValueError):
            recovery_pressure_text(raw, "unknown")


if __name__ == "__main__":
    unittest.main()
