"""CFD panel UI contracts; native references come from an actual cycle solve.

Imported fixtures are explicitly source_kind=test, never represented as
scientific CFD results. No external CFD solver is fabricated by these tests.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget
from cfd_exchange import load_cfd_job
from cfd_panel import CfdPanel, DEFAULT_SETTINGS, _same_fallback_inputs
from core import EngineInputs
from cycle import FFSCCycleModel


class PanelMain(QWidget):
    def __init__(self, native_results):
        super().__init__()
        self.inputs = deepcopy(native_results.inputs)
        self.last_results = deepcopy(native_results)
        self.last_geometry = None
        self._runtime_report = {"test_context": "UI test with actual native-cycle references"}
        self._busy = False
        self._result_signature = self._signature()

    def _signature(self):
        return json.dumps(asdict(self.inputs), sort_keys=True)

    def _read_inputs(self):
        return deepcopy(self.inputs)


class CfdPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Retain a legacy input for backward compatibility. It does not
            # disable the unified cooling model used by these real results.
            cls.native = FFSCCycleModel(EngineInputs(cooling_marching_enable=0.0)).evaluate()
        if cls.native.summary.get("solver_warning") or cls.native.summary.get("solver_residual_norm", 1.0) >= 1e-3:
            raise AssertionError("Actual native fixture did not converge")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.main = PanelMain(self.native)
        self.panel = CfdPanel(self.main)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.assertFalse(self.panel.has_running_task(), "UI test must not leave a process running")
        self.main.close()
        self.main.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def ready_job(self, task="cooling_flow"):
        settings = dict(DEFAULT_SETTINGS, route="external", task=task, pressure_basis="static",
                        temperature_basis="static", setup_reviewed=True)
        self.panel.restore_settings(settings)
        return self.panel.create_job(self.directory)

    def import_test_data(self, job, metrics, profile=None):
        manifest = load_cfd_job(job)
        document = {"format": "lre.cfd.result", "schema_version": 1, "job_id": manifest["job_id"],
                    "input_fingerprint": manifest["input_fingerprint"], "task": manifest["task"],
                    "source_kind": "test", "solver": {"name": "UI contract test only", "version": "1"},
                    "converged": False, "metrics": metrics,
                    "notes": "Interface fixture; no scientific CFD result is claimed."}
        if "domain" in manifest:
            document["domain"] = manifest["domain"]
        if profile is not None:
            document["wall_profile"] = profile
        path = self.directory / "test_result.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return self.panel.import_result(path)

    def test_defaults_and_partial_text_restore_without_parser_or_signal(self):
        self.assertEqual(self.panel.route_combo.currentData(), "internal")
        self.assertEqual(self.panel.snapshot_settings(), DEFAULT_SETTINGS)
        emitted = []
        self.panel.settings_changed.connect(lambda: emitted.append(True))
        values = dict(DEFAULT_SETTINGS, arguments_text='["--manifest",', timeout_seconds_text="-")
        self.panel.restore_settings(values)
        self.assertEqual(self.panel.snapshot_settings(), values)
        self.assertEqual(emitted, [])
        self.panel.arguments_edit.setText("[")
        self.assertEqual(len(emitted), 1)
        self.assertEqual(self.panel.snapshot_settings()["arguments_text"], "[")

    def test_configuration_validation_and_legacy_migration_are_atomic(self):
        before = self.panel.snapshot_settings()
        for invalid in ({"route": "auto"}, {"setup_reviewed": 1}, {"timeout_seconds_text": 5},
                        {"arguments": [1]}, {"pressure_basis": "gauge"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.panel.restore_settings(invalid)
            self.assertEqual(self.panel.snapshot_settings(), before)
        migrated = self.panel.validate_settings({"arguments": ["--manifest", "{manifest}"], "timeout_seconds": 120.0})
        self.assertEqual(json.loads(migrated["arguments_text"]), ["--manifest", "{manifest}"])
        self.assertEqual(migrated["timeout_seconds_text"], "120")
        self.panel.restore_settings({"job_path": str(self.directory / "not-present"), "arguments_text": "["})
        self.assertEqual(self.panel.arguments_edit.text(), "[")
        self.assertIsNone(self.panel._job)

    def test_export_requires_current_numerically_converged_source(self):
        self.panel.route_combo.setCurrentIndex(self.panel.route_combo.findData("external"))
        self.assertTrue(self.panel.export_button.isEnabled())
        from engineering_assessment import assess_engineering
        self.assertIn(assess_engineering(self.main.last_results)["label"], self.panel.source_status.text())
        self.main.inputs.pc *= 1.01
        self.panel.refresh_source()
        self.assertFalse(self.panel.export_button.isEnabled())
        with self.assertRaises(ValueError):
            self.panel.create_job(self.directory)
        self.main.inputs = deepcopy(self.native.inputs)
        # Status-field changes simulate the failed-solve UI branch only; they
        # are not exported or represented as a second physical calculation.
        self.main.last_results.summary["solver_warning"] = 1.0
        self.panel.refresh_source()
        self.assertFalse(self.panel.export_button.isEnabled())
        self.main.last_results = None
        self.panel.refresh_source()
        self.assertFalse(self.panel.export_button.isEnabled())

    def test_export_preserves_failed_wall_limit_without_warning_list(self):
        # UI contract fixture only: a failed engineering limit must remain
        # visible even when the solver's free-form warning list is empty.
        from tests.cooling_profile_fixture import cooling_profile
        self.main.last_results.warnings = []
        self.main.last_results.inputs.cooling_wall_temp_limit_status = "provisional"
        self.main.last_results.inputs.cooling_wall_temp_limit_K = 800.0
        self.main.inputs = deepcopy(self.main.last_results.inputs)
        self.main._result_signature = self.main._signature()
        self.main.last_results.components["cooling_channel"].profile = cooling_profile(1343)
        self.panel.route_combo.setCurrentIndex(self.panel.route_combo.findData("external"))
        self.panel.refresh_source()
        self.assertTrue(self.panel.export_button.isEnabled())
        self.assertIn("已数值收敛", self.panel.source_status.text())
        self.assertIn("工程约束未通过", self.panel.source_status.text())
        self.assertIn("沿程热面壁温峰值", self.panel.source_status.text())
        self.assertIn("导出不代表工程约束通过", self.panel.source_status.text())

    def test_fallback_accepts_only_float_display_noise(self):
        self.assertTrue(_same_fallback_inputs({"float": 3.333333333333333, "text": "same"},
                                             {"float": 3.33333333333333, "text": "same"}))
        self.assertFalse(_same_fallback_inputs({"float": 3.333333333333333}, {"float": 3.334}))
        self.assertFalse(_same_fallback_inputs({"text": "same"}, {"text": "same "}))
        self.assertFalse(_same_fallback_inputs({"integer": 1}, {"integer": True}))
        self.assertFalse(_same_fallback_inputs({"float": float("inf")}, {"float": float("inf")}))
        self.main.last_results.inputs.pc *= 1.0 + 2e-14
        self.assertTrue(self.panel._source_readiness()[0])
        self.main.last_results.inputs.pc *= 1.001
        self.assertFalse(self.panel._source_readiness()[0])

    def test_exact_submit_snapshot_handles_internal_values_without_weakening_match(self):
        submitted = asdict(self.main.inputs)
        self.main._solved_input_snapshot = submitted
        # Internal solver state can differ from the original submitted guess;
        # the stored source snapshot is authoritative for UI provenance.
        self.main.last_results.inputs.chamber_gamma += 0.01
        self.assertTrue(self.panel._source_readiness()[0])
        self.panel.restore_settings(dict(DEFAULT_SETTINGS, route="external", task="cooling_flow"))
        job = self.panel.create_job(self.directory)
        manifest = load_cfd_job(job)
        self.assertEqual(manifest["inputs"], submitted)
        self.assertNotEqual(manifest["initial_results"]["effective_inputs"]["chamber_gamma"], submitted["chamber_gamma"])
        self.main.inputs.pc += 1e-7
        # A signature can contain visible strings rather than this hidden typed
        # conversion. Check the exact snapshot guard independently as well.
        self.main._result_signature = self.main._signature()
        self.assertFalse(self.panel._source_readiness()[0])
        self.main.inputs.pc = submitted["pc"] * 1.01
        self.main._result_signature = self.main._signature()
        self.assertFalse(self.panel._source_readiness()[0])

    def test_create_opens_immutable_job_and_changed_basis_requires_new_job(self):
        job = self.ready_job()
        original = (job / "manifest.json").read_bytes()
        self.assertEqual(self.panel.job_directory, job)
        self.assertEqual(self.panel.snapshot_settings()["job_path"], str(job))
        self.assertTrue(self.panel.run_button.isEnabled())
        self.panel.pressure_basis_combo.setCurrentIndex(self.panel.pressure_basis_combo.findData("total"))
        self.assertFalse(self.panel.run_button.isEnabled())
        self.assertIn("新任务包", self.panel.job_status.text())
        self.assertEqual((job / "manifest.json").read_bytes(), original)
        self.panel.executable_edit.setText(str(ROOT / ".venv" / "Scripts" / "python.exe"))
        self.panel.open_job(job)
        self.assertEqual(self.panel.executable_edit.text(), str(ROOT / ".venv" / "Scripts" / "python.exe"))
        self.assertEqual(self.panel.pressure_basis_combo.currentData(), "static")

    def test_explicit_test_import_is_diagnostic_and_preserves_native_results(self):
        job = self.ready_job()
        before = deepcopy(self.main.last_results)
        result = self.import_test_data(job, {"pressure_drop_Pa": {"value": 0.0, "unit": "Pa"}},
                                       {"x_m": [0.0, 0.1], "wall_temperature_K": [300.0, 301.0]})
        self.assertTrue(result["diagnostic_only"])
        self.assertFalse(result["feedback_eligible"])
        self.assertIn("测试数据", self.panel.result_status.text())
        self.assertFalse(self.panel.feedback_button.isEnabled())
        self.assertIsNone(self.panel._feedback_values())
        self.assertEqual(self.panel.metric_table.item(0, 1).text(), "0")
        self.assertEqual(self.panel.metric_table.item(0, 2).text(), format(before.inputs.cooling_dp_pa, ".10g"))
        self.assertFalse(self.panel.wall_canvas.isHidden())
        self.assertEqual(len(self.panel.wall_axes.lines), 1)
        self.assertEqual(self.main.last_results, before)
        self.assertIn("单通道", self.panel.feedback_note.text())

    def test_task_comparisons_use_domain_flows_and_do_not_confuse_cooling_budget(self):
        job = self.ready_job("chamber_flow")
        self.import_test_data(job, {"pressure_drop_Pa": {"value": 0.0, "unit": "Pa"},
                                    "heat_load_W": {"value": 0.0, "unit": "W"},
                                    "mass_flow_kg_s": {"value": 0.0, "unit": "kg/s"}})
        self.assertEqual(self.panel.metric_table.item(0, 2).text(), "—")
        self.assertEqual(self.panel.metric_table.item(1, 2).text(), "—")
        manifest = load_cfd_job(job)
        flow = sum(row["mass_flow_kg_s"] for row in manifest["boundaries"] if row["role"] == "inlet")
        self.assertEqual(self.panel.metric_table.item(2, 2).text(), format(flow, ".10g"))
        self.panel.restore_settings(dict(DEFAULT_SETTINGS, route="external", task="turbine_flow", turbine_side="fuel",
                                         pressure_basis="static", temperature_basis="static", setup_reviewed=True))
        turbine_job = self.panel.create_job(self.directory)
        self.assertEqual(load_cfd_job(turbine_job)["settings"]["turbine_side"], "fuel")
        self.assertFalse(self.panel.turbine_side_combo.isHidden())
        self.import_test_data(turbine_job, {"shaft_power_W": {"value": 0.0, "unit": "W"}})
        native_power = self.native.components["fuel_preburner_turbine"].outputs["turbine_power_W"]
        self.assertEqual(self.panel.metric_table.item(0, 2).text(), format(native_power, ".10g"))

    def test_start_parses_arguments_only_at_run_and_forwards_without_shell(self):
        self.ready_job()
        self.panel.executable_edit.setText(sys.executable)
        for arguments, timeout in (("[", "5"), ('[1]', "5"), ("[]", "1.2"), ("[]", "0"), ("[]", "86401")):
            self.panel.arguments_edit.setText(arguments)
            self.panel.timeout_edit.setText(timeout)
            with self.subTest(arguments=arguments, timeout=timeout), self.assertRaises(ValueError):
                self.panel.start_task()
        arguments = ["--manifest", "{manifest}", "literal & value", "$(literal)"]
        self.panel.arguments_edit.setText(json.dumps(arguments))
        self.panel.timeout_edit.setText("12")
        with patch.object(self.panel._runner, "start") as start:
            self.panel.start_task()
        start.assert_called_once_with(self.panel.job_directory, sys.executable, arguments, timeout_seconds=12)

    def test_busy_and_existing_result_disable_execution_and_log_is_bounded(self):
        job = self.ready_job()
        self.main._busy = True
        self.panel.refresh_source()
        self.assertFalse(self.panel.run_button.isEnabled())
        self.assertFalse(self.panel.export_button.isEnabled())
        self.main._busy = False
        (job / "result.json").write_text("not yet valid", encoding="utf-8")
        self.panel.refresh_source()
        self.assertFalse(self.panel.run_button.isEnabled())
        self.assertTrue(self.panel.import_button.isEnabled())
        for index in range(2010):
            self.panel._append_log(f"log line {index}")
        self.assertLessEqual(self.panel.log_view.document().blockCount(), 2000)

    def test_feedback_signal_passes_only_guarded_values_without_modifying_model(self):
        # The positive signal-wiring check stubs the guard output rather than
        # fabricating an apparently converged scientific CFD result document.
        self.ready_job()
        received = []
        self.panel.apply_feedback.connect(received.append)
        before = deepcopy(self.main.inputs)
        with patch.object(self.panel, "_feedback_values", return_value={"cooling_dp_pa": 0.0}):
            self.panel.refresh_source()
            self.panel.feedback_button.click()
        self.assertEqual(received, [{"cooling_dp_pa": 0.0}])
        self.assertEqual(self.main.inputs, before)


if __name__ == "__main__":
    unittest.main()
