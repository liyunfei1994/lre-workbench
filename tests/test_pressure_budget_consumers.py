"""Actual branch losses remain usable when legacy scalar summaries are null."""
from dataclasses import asdict
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.domain.models import EngineInputs
from lre_workbench.analysis.assessment import assess_engineering
from lre_workbench.systems.pressure_budget import BRANCH_BUDGET_FIELDS
from tests import test_desktop_preburner_compatibility as desktop_tests
from tests.test_environment_assessment import example_result
from tests.test_steady_network import solve


class PressureBudgetConsumerTests(unittest.TestCase):
    def fixture(self):
        budgets = {key: dict(line_dp_Pa=(index + 1) * .1e6, injector_ratio=.1 + index * .02)
                   for index, key in enumerate(BRANCH_BUDGET_FIELDS)}
        network = solve(branch_budgets=budgets)
        taps = network["tap_pressures_Pa"]
        values = dict(lox_tank=1e6, fuel_tank=1e6, lox_pump_inlet=.9e6, fuel_pump_inlet=.9e6,
                      lox_after_pump=taps["ox"], fuel_after_pump=taps["fuel"] + 2e6,
                      fuel_after_cooling=taps["fuel"], ox_preburner_outlet=10e6,
                      fuel_preburner_outlet=8e6, ox_turbine_exhaust=7e6,
                      fuel_turbine_exhaust=7e6, main_chamber=6e6)
        result = SimpleNamespace(summary={"valve_line_dp_MPa": None, "injector_dp_ox_MPa": None},
                                 states={key: SimpleNamespace(pressure=value) for key, value in values.items()},
                                 steady_closure={"feed_network": network})
        window = SimpleNamespace(waterfall_fig=Mock(), waterfall_canvas=Mock(),
                                 _style_ax_base=Mock(), _wf_axes={"lox": Mock(), "ch4": Mock()})
        for axis in window._wf_axes.values():
            axis.spines = {}
        return result, window

    def draw(self, result, window):
        method = desktop_tests.desktop_method("legacy_window.py", "MainWindow", "_draw_pressure_waterfall")
        method.__globals__["MATPLOTLIB_AVAILABLE"] = True
        method(window, result)

    def test_desktop_chart_uses_actual_individual_losses_not_legacy_common_value(self):
        result, window = self.fixture()
        self.draw(result, window)
        branches = {row["id"]: row for row in result.steady_closure["feed_network"]["branches"]}
        for axis, key, position in (("lox", "ox_to_opb", 3), ("ch4", "fuel_to_fpb", 4)):
            values = window._wf_axes[axis].barh.call_args.args[1]
            branch = branches[key]
            after_line = (branch["source_pressure_Pa"] - branch["line_drop_Pa"]) / 1e5
            self.assertAlmostEqual(values[position], after_line)
            self.assertAlmostEqual(values[position + 1], after_line - branch["valve_drop_Pa"] / 1e5)

    def test_missing_branch_loss_is_unavailable_not_zero_or_a_reused_common_value(self):
        result, window = self.fixture()
        branch = next(row for row in result.steady_closure["feed_network"]["branches"] if row["id"] == "ox_to_opb")
        del branch["line_drop_Pa"]
        result.summary["valve_line_dp_MPa"] = .03
        self.draw(result, window)
        window._wf_axes["lox"].barh.assert_not_called()
        self.assertIn("缺少", window._wf_axes["lox"].text.call_args.args[2])
        window._wf_axes["ch4"].barh.assert_called_once()

    def test_ffsc_engineering_checks_do_not_depend_on_common_loss_summary(self):
        result = example_result()
        result.summary.update(valve_line_dp_MPa=.03, injector_dp_ox_MPa=1, injector_dp_fuel_MPa=2)
        before = assess_engineering(result)
        result.summary.update(valve_line_dp_MPa=None, injector_dp_ox_MPa=None, injector_dp_fuel_MPa=None)
        self.assertEqual(assess_engineering(result), before)

    def test_missing_gg_common_loss_remains_unknown(self):
        result = example_result()
        result.inputs.cycle_type = "gg"
        result.summary.update(chamber_pressure_Pa=10e6, valve_line_dp_MPa=None,
                              injector_dp_ox_MPa=2, injector_dp_fuel_MPa=1.5,
                              gg_pressure_Pa=10e6, gg_T_combustion_K=900)
        result.states = {"lox_after_pump": SimpleNamespace(pressure=13e6),
                         "fuel_after_pump": SimpleNamespace(pressure=18e6),
                         "fuel_after_cooling": SimpleNamespace(pressure=12.1e6)}
        checks = {row["key"]: row for row in assess_engineering(result)["checks"]}
        for key in ("ox_main_feed_margin_MPa", "fuel_main_feed_margin_MPa", "gg_feed_margin_MPa"):
            self.assertEqual(checks[key]["status"], "unknown")
            self.assertIsNone(checks[key]["value"])

    def test_desktop_case_loading_retains_optional_branch_fields(self):
        inputs = asdict(EngineInputs(fuel_to_opb_line_dp_ratio=.07))
        loaded = desktop_tests.DesktopPreburnerCompatibilityTests().load_document(
            {"format": "ffsc.workbench.case", "schema_version": 1, "inputs": inputs})
        for fields in BRANCH_BUDGET_FIELDS.values():
            for key in fields.values():
                self.assertEqual(loaded[key], inputs[key])


if __name__ == "__main__":
    unittest.main()
