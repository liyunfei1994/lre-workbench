"""FEA must leave the UI responsive and never publish cancelled native work."""
from copy import deepcopy
from pathlib import Path
import os
import sys
import tempfile
from threading import Event, get_ident
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication
from auxiliary_worker import CallableWorker
from task_control import CancellationToken, TaskCancelled, cancellation_scope
from ui_chamber_design import _PreviewWorker
from core import EngineInputs
from workbench import WorkbenchWindow
import fea_chamber


class AuxiliaryTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.directory = tempfile.TemporaryDirectory(prefix="lre-auxiliary-tests-")
        cls.window = WorkbenchWindow(recovery_directory=cls.directory.name)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._remember_saved_case()
        cls.window.close()
        cls.app.processEvents()
        cls.directory.cleanup()

    def setUp(self):
        self.window._remember_saved_case()
        self.window.last_results = None
        self.window._busy = False
        self.window._last_wallfield = SimpleNamespace(x=[0, 0.1], throat_index=1, sigma_eq=[20, 30])
        self.window._last_geometry_for_fea = {"chamber": {"radius_m": 0.2}}
        self.window.fea_btn.setEnabled(True)
        self.window.matcmp_btn.setEnabled(True)

    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(condition(), "Background worker or queued UI result timed out")

    def collect(self, worker):
        events = {"finished": [], "cancelled": [], "error": []}
        for name, values in events.items():
            getattr(worker.signals, name).connect(values.append)
        return events

    def test_generic_native_call_returns_after_cancel_without_publishing_result(self):
        entered, release = Event(), Event()
        def native_call():
            entered.set()
            release.wait(5)
            return "unwanted late result"
        worker = CallableWorker(native_call, "fea")
        worker.setAutoDelete(False)
        events = self.collect(worker)
        pool = QThreadPool()
        pool.start(worker)
        self.wait_for(entered.is_set)
        worker.request_cancel()
        release.set()
        self.wait_for(lambda: bool(events["cancelled"]))
        pool.waitForDone(5000)
        self.assertEqual(events["cancelled"], [{"kind": "fea", "records": []}])
        self.assertFalse(events["finished"])
        self.assertFalse(events["error"])

    def test_preview_worker_discards_cancelled_preview_without_error(self):
        entered, release = Event(), Event()
        def preview(*args):
            entered.set()
            release.wait(5)
            return {"wallfield": "late preview"}
        worker = _PreviewWorker(object(), EngineInputs())
        worker.setAutoDelete(False)
        events = self.collect(worker)
        pool = QThreadPool()
        with patch("ui_chamber_design.cd.marching_preview", side_effect=preview):
            pool.start(worker)
            self.wait_for(entered.is_set)
            worker.request_cancel()
            release.set()
            self.wait_for(lambda: bool(events["cancelled"]))
            pool.waitForDone(5000)
        self.assertEqual(events["cancelled"][0]["kind"], "preview")
        self.assertFalse(events["finished"])
        self.assertFalse(events["error"])

    def test_actual_sparse_solve_cancel_escapes_fea_failure_handler(self):
        import skfem
        token = CancellationToken()
        native_solve = skfem.solve
        calls = []
        def solve_and_cancel(*args, **kwargs):
            result = native_solve(*args, **kwargs)
            calls.append(result)
            token.cancel()
            return result
        with patch("skfem.solve", side_effect=solve_and_cancel):
            with cancellation_scope(token):
                with self.assertRaises(TaskCancelled):
                    fea_chamber.solve_throat_channel_2d(
                        t_liner_m=0.5e-3, h_channel_m=4e-3, w_channel_m=1.5e-3,
                        pitch_m=2.5e-3, q_gas_W_m2=124e6, h_c_W_m2K=1.47e5,
                        T_coolant_K=300, p_coolant_Pa=66e6, p_gas_Pa=18e6,
                        E_Pa=100e9, nu=0.33, alpha_1perK=17e-6, k_WmK=235)
        self.assertEqual(len(calls), 1, "Cancellation must stop before the elasticity solve")

    def test_throat_fea_snapshots_inputs_and_finished_render_runs_on_ui_thread(self):
        w = self.window
        entered, release = Event(), Event()
        received, thread_ids = [], []
        def solve(wf, geometry, **kwargs):
            received.append((deepcopy(wf), deepcopy(geometry), kwargs))
            thread_ids.append(get_ident())
            entered.set()
            release.wait(5)
            return fea_chamber.FEAResult(ok=False)
        rendered = []
        def render(payload):
            rendered.append((get_ident(), payload))
        main_thread = get_ident()
        with patch("fea_chamber.solve_from_wallfield", side_effect=solve):
            with patch.object(w, "_show_throat_fea_result", side_effect=render):
                w._run_throat_fea()
                self.wait_for(entered.is_set)
                self.assertTrue(w._busy)
                self.assertTrue(w.cancel_button.isEnabled())
                self.assertFalse(w.header_run.isEnabled())
                tick = Event()
                QTimer.singleShot(0, tick.set)
                self.wait_for(tick.is_set)
                w._last_wallfield.sigma_eq[1] = 9000
                w._last_geometry_for_fea["chamber"]["radius_m"] = 9000
                release.set()
                self.wait_for(lambda: bool(rendered))
        self.assertNotEqual(thread_ids[0], main_thread)
        self.assertEqual(rendered[0][0], main_thread)
        self.assertEqual(received[0][0].sigma_eq[1], 30)
        self.assertEqual(received[0][1]["chamber"]["radius_m"], 0.2)
        self.assertFalse(w._busy)
        self.assertTrue(w.header_run.isEnabled())

    def test_material_comparison_cancels_and_restores_controls_without_dialog(self):
        w = self.window
        entered, release = Event(), Event()
        def compare(*args):
            entered.set()
            release.wait(5)
            return [fea_chamber.FEAResult(ok=False)]
        with patch("fea_chamber.compare_liner_materials_2d", side_effect=compare):
            with patch.object(w, "_show_material_comparison_result") as render:
                w._run_material_comparison_2d()
                self.wait_for(entered.is_set)
                self.assertTrue(w._busy)
                w.cancel_task()
                release.set()
                self.wait_for(lambda: not w._busy)
                render.assert_not_called()
        self.assertTrue(w.matcmp_btn.isEnabled())
        self.assertEqual(w.matcmp_btn.text(), "▶ 对比主流内衬材料 (2D 温度场云图)")
        self.assertTrue(w.parameter_pane.isEnabled())

    def test_station_fea_cancels_without_opening_a_late_dialog(self):
        w, panel = self.window, self.window.chamber_panel
        panel._wf_current = deepcopy(w._last_wallfield)
        panel._cursor_idx = 0
        entered, release = Event(), Event()
        indices = []
        def solve(wf, geom, **kwargs):
            indices.append(kwargs["station_index"])
            entered.set()
            release.wait(5)
            return fea_chamber.FEAResult(ok=False)
        with patch.object(panel, "_get_geom", return_value=w._last_geometry_for_fea):
            with patch("fea_chamber.solve_from_wallfield", side_effect=solve):
                with patch.object(panel, "_show_station_fea_result") as render:
                    panel._run_station_fea()
                    self.wait_for(entered.is_set)
                    panel._cursor_idx = 1
                    w.cancel_task()
                    release.set()
                    self.wait_for(lambda: not w._busy)
                    render.assert_not_called()
        self.assertEqual(indices, [0])
        self.assertTrue(w.parameter_pane.isEnabled())


if __name__ == "__main__":
    unittest.main()
