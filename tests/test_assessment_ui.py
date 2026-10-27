"""Check that displayed and exported engineering evidence has the same meaning."""
from dataclasses import asdict
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget, QMessageBox
from core import ComponentResult, CycleResults, EngineInputs, FlowState
from workbench import WorkbenchWindow
from workbench_fonts import configure_fonts
from web_tests.convergence_fixture import converged_evidence
from tests.cooling_profile_fixture import cooling_profile


def converged_but_infeasible_result():
    inputs = EngineInputs()
    inputs.turb_ox_rotor_material = "sx500"
    inputs.turb_ox_stator_material = "sx300"
    summary = {
        "solver_residual_norm": 3.2e-5, "solver_iterations": 16, "thermo_source": 1,
        "crossflow_fuel_to_oxpb_margin_MPa": -4.36, "crossflow_lox_to_fuelpb_margin_MPa": 4.36,
        "chamber_pressure_Pa": inputs.pc,
        "ox_turbine_exhaust_pressure_Pa": inputs.pc * (1 + inputs.ox_turbine_exhaust_dp_ratio),
        "fuel_turbine_exhaust_pressure_Pa": inputs.pc * (1 + inputs.fuel_turbine_exhaust_dp_ratio),
        "power_balance_ratio": 1.0, "ox_side_power_balance_ratio": 1.0, "fuel_side_power_balance_ratio": 1.0,
        "ox_preburner_T_combustion_K": 750, "fuel_preburner_T_combustion_K": 850,
        "thrust_N": inputs.thrust, "thrust_vac_N": inputs.thrust * 1.05,
        "thrust_error_percent": 0.01, "Isp_s": 327, "Isp_vac_s": 350,
        "mdot_total_kg_s": 800, "chamber_temperature_K": 3500,
    }
    components = {
        "cooling_channel": ComponentResult("cooling_channel", {}, profile=cooling_profile(1343)),
        "ox_preburner_turbine": ComponentResult("ox_preburner_turbine", {"T_in_K": 750, "U_mean_actual_ms": 40}),
        "fuel_preburner_turbine": ComponentResult("fuel_preburner_turbine", {"T_in_K": 850, "U_mean_actual_ms": 40}),
    }
    states = {"main_chamber": FlowState("main_chamber", inputs.pc, 3500, 800)}
    return CycleResults(inputs, summary, states, components, ["测试工况：交叉供给欠压与壁温超限"], convergence=converged_evidence(3.2e-5))


class AssessmentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        configure_fonts(cls.app)
        cls.directory = tempfile.TemporaryDirectory(prefix="lre-assessment-ui-")
        cls.window = WorkbenchWindow(recovery_directory=Path(cls.directory.name) / "recovery")
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._remember_saved_case()
        cls.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        cls.directory.cleanup()

    def setUp(self):
        self.result = converged_but_infeasible_result()
        self.window.last_results = self.result
        self.window._latest_results = self.result
        self.window.last_geometry = {}
        self.window._result_signature = self.window._signature()
        self.window._remember_saved_case()

    def test_numerical_success_does_not_hide_failed_engineering_checks(self):
        window = self.window
        window._populate_overview(self.result)
        window.navigate("overview")
        self.app.processEvents()
        table = window.overview_widget.findChild(QTableWidget, "EngineeringChecks") if hasattr(window, "overview_widget") else None
        if table is None:
            table = window.findChild(QTableWidget, "EngineeringChecks")
        self.assertIsNotNone(table)
        rows = {table.item(row, 0).text(): row for row in range(table.rowCount())}
        for label in ("CH4 → 富氧预燃室供给压力裕度", "沿程热面壁温峰值"):
            row = rows[label]
            self.assertEqual(table.item(row, 3).text(), "未通过")
            self.assertEqual(table.item(row, 3).foreground().color().name(), "#b91c1c")
        strips = window.findChildren(QLabel)
        visible_text = [label.text() for label in strips if label.isVisible()]
        self.assertIn("数值收敛", visible_text)
        self.assertIn("工程约束未通过", visible_text)

    def test_placeholder_material_rows_and_details_are_marked_pending(self):
        window = self.window
        before = asdict(self.result.inputs)
        signature = window._signature()
        window._populate_materials_tab(self.result)
        window.navigate("materials")
        self.app.processEvents()
        tree = window.materials_tree
        self.assertEqual(tree.headerItem().text(7), "数据依据")
        leaves = [tree.topLevelItem(i).child(j)
                  for i in range(tree.topLevelItemCount())
                  for j in range(tree.topLevelItem(i).childCount())]
        for key in ("sx300", "sx500"):
            rows = [item for item in leaves if (item.data(0, Qt.UserRole) or {}).get("mat_key") == key]
            self.assertGreater(len(rows), 0)
            item = rows[0]
            self.assertEqual(item.text(5), "待核实")
            self.assertIn("占位", item.text(7))
            self.assertIn("待确认", item.text(6))
            self.assertNotEqual(item.foreground(5).color().name(), "#15803d")
            window._render_material_detail(item.data(0, Qt.UserRole))
            self.app.processEvents()
            # Inspect the cards currently in the detail layout. Earlier cards
            # await Qt deferred deletion and must not affect this assertion.
            labels = [label
                      for index in range(window.materials_detail_layout.count())
                      if window.materials_detail_layout.itemAt(index).widget() is not None
                      for label in window.materials_detail_layout.itemAt(index).widget().findChildren(QLabel)]
            self.assertIn("数据依据与可信度", [label.text() for label in labels])
            pending_badges = [label for label in labels if label.text() == "氧相容性待核实"]
            self.assertGreater(len(pending_badges), 0)
            self.assertTrue(all(label.objectName() != "PillOk" for label in pending_badges),
                            "未经验证的富氧相容性仍显示为绿色通过徽章")
            self.assertFalse(any("LOX/GOX 兼容 ✓" in label.text() for label in labels))
        self.assertEqual(asdict(self.result.inputs), before)
        self.assertEqual(window._signature(), signature)

    def test_preburner_provisional_limits_are_not_green_acceptance(self):
        row = self.window._make_kpi_row(self.result)
        for title in ("富氧预燃室温度", "富燃预燃室温度"):
            label = next(label for label in row.findChildren(QLabel) if label.text() == title)
            card = label.parentWidget()
            self.assertNotEqual(card.objectName(), "KpiCard_ok")
            self.assertTrue(any("暂定上限" in hint.text() for hint in card.findChildren(QLabel)))
        self.window._populate_overview(self.result)
        table = self.window.findChild(QTableWidget, "EngineeringChecks")
        for index in range(table.rowCount()):
            if table.item(index, 0).text() in ("富氧预燃室出口温度", "富燃预燃室出口温度"):
                self.assertEqual(table.item(index, 3).text(), "依据待核实")
                self.assertNotEqual(table.item(index, 3).foreground().color().name(), "#15803d")
        row.deleteLater()

    def test_preburner_unset_limit_ignores_retained_numeric_for_badges(self):
        self.result.inputs.ox_preburner_temp_limit_status = "unset"
        self.result.inputs.ox_preburner_temp_limit = 1
        component = ComponentResult("ox_preburner", {"T_combustion_K": 750})
        badges = self.window._component_status_badges("ox_preburner", component, self.result.inputs)
        self.assertIn(("温度上限未设定 · 尚未评估", "neutral"), badges)
        self.assertFalse(any(state in ("ok", "error") for _, state in badges))
        row = self.window._make_kpi_row(self.result)
        title = next(label for label in row.findChildren(QLabel) if label.text() == "富氧预燃室温度")
        self.assertEqual(title.parentWidget().objectName(), "KpiCard_neutral")
        row.deleteLater()

    def test_actual_result_export_keeps_source_inputs_environment_and_constraints(self):
        window = self.window
        source_inputs = asdict(self.result.inputs)
        window.input_edits["pc"].setText("31.5")
        destination = Path(self.directory.name) / "result.json"
        warnings_seen = []
        with patch("workbench.QFileDialog.getSaveFileName", return_value=(str(destination), "JSON (*.json)")), \
             patch.object(QMessageBox, "warning", side_effect=lambda parent, title, text: warnings_seen.append(text)):
            window.export_json()
        self.assertEqual(warnings_seen, [])
        document = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(document["inputs"], source_inputs)
        self.assertTrue(document["input_modified_since_solve"])
        environment = document["environment"]
        self.assertTrue(environment["ready"])
        self.assertEqual(len(environment["fingerprint"]), 64)
        for distribution in ("cantera", "CoolProp", "numpy", "scipy", "scikit-fem", "PySide6", "matplotlib"):
            self.assertTrue(environment["dependencies"][distribution]["available"])
            self.assertTrue(environment["dependencies"][distribution]["version"])
        self.assertTrue(environment["functional_checks"]["cantera_mechanism"]["ready"])
        assessment = document["engineering_assessment"]
        self.assertEqual(assessment["numerical"]["status"], "pass")
        self.assertEqual(assessment["status"], "fail")
        self.assertEqual(assessment["failure_count"], 2)
        self.assertEqual(assessment["materials"]["turb_ox_rotor_material"]["status"], "placeholder")
        self.assertEqual(document["states"]["main_chamber"]["pressure"], self.result.inputs.pc)
        self.assertEqual(asdict(self.result.inputs), source_inputs)
        self.assertEqual(window.case_document()["environment"]["fingerprint"], environment["fingerprint"])

    def test_pump_material_rows_show_the_material_used_by_the_solve(self):
        window = self.window
        self.result.inputs.lox_pump_impeller_material = "monelk500_impeller"
        self.result.inputs.fuel_pump_impeller_material = "inconel718"
        window._populate_materials_tab(self.result)
        tree = window.materials_tree
        rows = [tree.topLevelItem(i).child(j)
                for i in range(tree.topLevelItemCount())
                for j in range(tree.topLevelItem(i).childCount())]
        pumps = {meta["side"]: meta["mat_key"]
                 for item in rows
                 if (meta := item.data(0, Qt.UserRole) or {}).get("part_key") == "pump_impeller"}
        self.assertEqual(pumps, {"ox": "monelk500_impeller", "fuel": "inconel718"})

    def test_environment_indicator_does_not_claim_ready_when_mechanism_fails(self):
        report = deepcopy(self.window._runtime_report)
        report["ready"] = False
        report["functional_checks"]["cantera_mechanism"].update(ready=False, error="test mechanism unavailable")
        with patch("workbench.environment_report", return_value=report):
            probe_window = WorkbenchWindow(recovery_directory=Path(self.directory.name) / "probe-recovery")
        try:
            self.assertIn("环境不完整", probe_window.environment_label.text())
            self.assertIn("test mechanism unavailable", probe_window.environment_label.toolTip())
            self.assertNotIn("完整科学环境", probe_window.environment_label.text())
        finally:
            probe_window._remember_saved_case()
            probe_window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
