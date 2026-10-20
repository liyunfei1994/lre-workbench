"""Exercise CFD routing, saved drafts, and feedback in the real workbench."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtWidgets import QApplication, QMessageBox
from cfd_exchange import load_cfd_job
from core import EngineInputs
from cycle import FFSCCycleModel
from geometry import compute_geometry, geometry_to_dict
from version import APP_VERSION
from workbench import WorkbenchWindow
from windows_identity import APP_USER_MODEL_ID


class CfdIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.directory = tempfile.TemporaryDirectory(prefix="lre-cfd-integration-")
        cls.window = WorkbenchWindow(recovery_directory=Path(cls.directory.name) / "recovery")
        cls.window.show()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.source = FFSCCycleModel(EngineInputs(cooling_marching_enable=0.0)).evaluate()
            cls.geometry = geometry_to_dict(compute_geometry(cls.source))
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._remember_saved_case()
        cls.window.close()
        cls.app.processEvents()
        cls.directory.cleanup()

    def setUp(self):
        self.w = self.window
        self.w.cfd_panel.restore_settings({})
        self.w._apply_cfd_route()
        self.w._write_input_values(asdict(self.source.inputs))
        self.w.last_results = deepcopy(self.source)
        self.w.last_geometry = deepcopy(self.geometry)
        self.w._cfd_feedback_history = []
        self.w._solved_input_snapshot = None
        self.w._case_path = None
        self.w._remember_saved_case()
        self.app.processEvents()

    def prepare_job(self):
        panel = self.w.cfd_panel
        panel.route_combo.setCurrentIndex(panel.route_combo.findData("external"))
        panel.task_combo.setCurrentIndex(panel.task_combo.findData("cooling_flow"))
        panel.pressure_basis_combo.setCurrentIndex(panel.pressure_basis_combo.findData("static"))
        panel.temperature_basis_combo.setCurrentIndex(panel.temperature_basis_combo.findData("static"))
        panel.reviewed_checkbox.setChecked(True)
        self.w._result_signature = self.w._signature()
        return panel.create_job(self.directory.name)

    def write_result(self, directory, source_kind="cfd"):
        job = load_cfd_job(directory)
        result = {"format": "lre.cfd.result", "schema_version": 1, "job_id": job["job_id"],
                  "input_fingerprint": job["input_fingerprint"], "task": job["task"],
                  "source_kind": source_kind, "solver": {"name": "integration-fixture", "version": "1"},
                  "converged": True, "metrics": {"pressure_drop_Pa": {"value": 6e6, "unit": "Pa"},
                                                  "heat_load_W": {"value": 85e6, "unit": "W"}}}
        if "domain" in job:
            result["domain"] = job["domain"]
        path = Path(directory) / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return path

    def test_version_navigation_and_external_route(self):
        self.assertEqual(APP_VERSION, "7.1.0")
        self.assertEqual(APP_USER_MODEL_ID, "LRE.Workbench.7.1.0")
        self.w.navigate("cfd")
        self.assertEqual(self.w.page_title.text(), "CFD 协同")
        self.w.input_edits["cooling_marching_enable"].setText("1")
        panel = self.w.cfd_panel
        panel.route_combo.setCurrentIndex(panel.route_combo.findData("external"))
        self.assertEqual(self.w._read_inputs().cooling_marching_enable, 0)
        self.assertFalse(self.w.input_edits["cooling_marching_enable"].isEnabled())
        self.assertFalse(self.w.chamber_panel.btn_preview.isEnabled())
        self.assertFalse(self.w.chamber_panel.btn_station_fea.isEnabled())
        panel.route_combo.setCurrentIndex(panel.route_combo.findData("internal"))
        self.assertEqual(self.w.input_edits["cooling_marching_enable"].text(), "1")
        self.assertTrue(self.w.chamber_panel.btn_preview.isEnabled())

    def test_real_cycle_and_geometry_export_with_engineering_warnings(self):
        directory = self.prepare_job()
        job = load_cfd_job(directory)
        self.assertEqual(job["inputs"], asdict(self.w._read_inputs()))
        self.assertTrue(job["boundary_review"]["complete"])
        self.assertGreater(len(job["geometry_reference"]["contour"]["x_m"]), 100)
        self.assertEqual(job["geometry_reference"]["contour"]["coordinate_origin"], "injection_face_origin")
        self.assertEqual(job["initial_results"]["warnings"], self.source.warnings)
        self.assertTrue(self.w.cfd_panel.export_button.isEnabled())
        self.w.input_edits["pc"].setText("30")
        with self.assertRaisesRegex(ValueError, "输入已变化"):
            self.w.cfd_panel.create_job(self.directory.name)

    def test_saved_configuration_and_unfinished_draft_restore(self):
        panel = self.w.cfd_panel
        panel.arguments_edit.setText('["unfinished')
        panel.timeout_edit.setText("1e")
        raw = panel.snapshot_settings()
        path = Path(self.directory.name) / "带CFD配置.json"
        with patch("workbench.QFileDialog.getSaveFileName", return_value=(str(path), "JSON")):
            self.assertTrue(self.w.save_case())
        panel.restore_settings({})
        self.w.load_case_path(path)
        self.assertEqual(panel.snapshot_settings(), raw)
        panel.arguments_edit.setText('["another unfinished')
        snapshot = self.w._case_recovery_snapshot()
        panel.restore_settings({})
        self.w._restore_case_snapshot(snapshot)
        self.assertEqual(panel.arguments_edit.text(), '["another unfinished')
        self.assertTrue(self.w._has_unsaved_changes())

    def test_invalid_saved_cfd_structure_does_not_replace_inputs(self):
        before = self.w._case_edit_state()
        document = self.w.case_document()
        document["inputs"]["pc"] = 18e6
        document["analysis"]["cfd"]["route"] = "untrusted-route"
        path = Path(self.directory.name) / "无效CFD配置.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "route"):
            self.w.load_case_path(path)
        self.assertEqual(self.w._case_edit_state(), before)

    def test_external_case_restores_original_internal_marching_preference(self):
        self.w.input_edits["cooling_marching_enable"].setText("1")
        panel = self.w.cfd_panel
        panel.route_combo.setCurrentIndex(panel.route_combo.findData("external"))
        path = Path(self.directory.name) / "外部计算路线.json"
        path.write_text(json.dumps(self.w.case_document()), encoding="utf-8")
        restored = WorkbenchWindow(recovery_directory=Path(self.directory.name) / "fresh-recovery")
        try:
            restored.load_case_path(path)
            self.assertEqual(restored._read_inputs().cooling_marching_enable, 0)
            restored.cfd_panel.route_combo.setCurrentIndex(restored.cfd_panel.route_combo.findData("internal"))
            self.assertEqual(restored.input_edits["cooling_marching_enable"].text(), "1")
        finally:
            restored._remember_saved_case()
            restored.close()

    def test_feedback_uses_persisted_result_and_keeps_native_output_separate(self):
        directory = self.prepare_job()
        self.write_result(directory)
        imported = self.w.cfd_panel.import_result()
        self.assertTrue(imported["feedback_eligible"])
        original = asdict(self.w.last_results.inputs)
        with patch.object(self.w, "run_cycle") as run, patch.object(QMessageBox, "warning") as warn:
            self.w._apply_cfd_feedback({"cooling_dp_pa": 9e20})
        warn.assert_not_called()
        run.assert_called_once()
        self.assertEqual(self.w._read_inputs().cooling_dp_pa, 6e6)
        self.assertEqual(self.w._read_inputs().cooling_heat_load, 85e6)
        self.assertEqual(self.w._read_inputs().cooling_bartz_mode, 0)
        self.assertEqual(self.w._read_inputs().cooling_dp_auto, 0)
        self.assertEqual(asdict(self.w.last_results.inputs), original)
        self.assertEqual(self.w._cfd_feedback_history[0]["updates"]["cooling_dp_pa"], 6e6)
        snapshot = self.w._case_recovery_snapshot()
        self.assertEqual(snapshot["cfd_feedback_history"], self.w._cfd_feedback_history)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            replay = FFSCCycleModel(self.w._read_inputs()).evaluate()
        self.assertAlmostEqual(replay.components["cooling_channel"].outputs["heat_load_W"], 85e6)
        self.assertAlmostEqual(replay.states["fuel_after_pump"].pressure - replay.states["fuel_after_cooling"].pressure,
                               6e6, delta=0.01)

    def test_test_data_and_changed_settings_cannot_feed_back(self):
        directory = self.prepare_job()
        self.write_result(directory, "test")
        self.w.cfd_panel.import_result()
        before = asdict(self.w._read_inputs())
        with patch.object(self.w, "run_cycle") as run, patch.object(QMessageBox, "warning") as warn:
            self.w._apply_cfd_feedback({})
        warn.assert_called_once()
        run.assert_not_called()
        self.assertEqual(asdict(self.w._read_inputs()), before)
        self.write_result(directory, "cfd")
        self.w.cfd_panel.import_result()
        self.w.cfd_panel.reviewed_checkbox.setChecked(False)
        with patch.object(self.w, "run_cycle") as run, patch.object(QMessageBox, "warning") as warn:
            self.w._apply_cfd_feedback({})
        warn.assert_called_once()
        run.assert_not_called()

    def test_adapter_self_test_is_imported_as_test_and_not_cfd(self):
        self.prepare_job()
        panel = self.w.cfd_panel
        panel.executable_edit.setText(sys.executable)
        panel.arguments_edit.setText(json.dumps([str(ROOT / "scripts" / "cfd_adapter_example.py"),
                                                "--manifest", "{manifest}", "--result", "{result}", "--self-test"]))
        panel.start_task()
        self.assertFalse(self.w.header_run.isEnabled())
        self.assertFalse(self.w.btn_run_of_sweep.isEnabled())
        self.assertTrue(panel.cancel_button.isEnabled())
        with patch("workbench.legacy.OFSweepWorker") as native_scan:
            self.w._on_run_of_sweep_clicked()
        native_scan.assert_not_called()
        old_document = self.w.case_document()
        old_document.pop("analysis")
        old_case = Path(self.directory.name) / "运行期间的旧格式方案.json"
        old_case.write_text(json.dumps(old_document), encoding="utf-8")
        before = self.w._case_edit_state()
        with self.assertRaisesRegex(ValueError, "任务进行中"):
            self.w.load_case_path(old_case)
        self.assertEqual(self.w._case_edit_state(), before)
        deadline = time.monotonic() + 12
        while panel.has_running_task() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(panel.has_running_task())
        self.assertTrue(self.w.header_run.isEnabled())
        self.assertIn("测试数据", panel.result_status.text())
        self.assertFalse(panel.feedback_button.isEnabled())
        self.assertTrue((panel.job_directory / "run_status.json").exists())
        self.assertTrue((panel.job_directory / "imported_result.json").exists())


if __name__ == "__main__":
    unittest.main()
