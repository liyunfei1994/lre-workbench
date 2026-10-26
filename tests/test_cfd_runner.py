"""Real subprocess tests for the solver-neutral, explicitly configured runner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from cfd_runner import CfdProcessRunner
from cfd_exchange import create_cfd_job, load_cfd_job, import_cfd_result
from core import CycleResults, EngineInputs, FlowState


class CfdRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lre-cfd-runner-")
        self.addCleanup(self.temporary.cleanup)
        self.parent_directory = Path(self.temporary.name) / "中文 任务目录"
        self.runner = CfdProcessRunner()
        self.outputs, self.states, self.finished = [], [], []
        self.runner.output.connect(self.outputs.append)
        self.runner.state_changed.connect(self.states.append)
        self.runner.finished.connect(lambda code, status: self.finished.append((code, status)))
        self.addCleanup(self.stop_runner)

    def stop_runner(self):
        if self.runner.is_running:
            self.runner.cancel()
            end = time.monotonic() + 4
            while self.runner.is_running and time.monotonic() < end:
                self.app.processEvents()
                time.sleep(0.005)
        self.app.processEvents()

    def job(self, settings=None):
        inputs = EngineInputs()
        result = CycleResults(inputs, {"solver_residual_norm": 1e-5}, {
            "ox_turbine_exhaust": FlowState("oxide", 30e6, 750, 100, composition={"O2": 1}),
            "fuel_turbine_exhaust": FlowState("fuel", 30e6, 850, 20, composition={"CH4": 1}),
        }, {}, [])
        reviewed = {"setup_reviewed": True, "pressure_basis": "static", "temperature_basis": "static"}
        if settings:
            reviewed.update(settings)
        return create_cfd_job(result, {"nozzle": {"contour_x": [0, 0.1], "contour_r": [0.03, 0.08]}},
                              self.parent_directory, settings=reviewed)

    def wait_for(self, condition, seconds=6):
        end = time.monotonic() + seconds
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(condition(), f"Process wait timed out; outputs={self.outputs}")

    def run_python(self, directory, code, arguments=None, timeout=10):
        self.runner.start(directory, sys.executable, ["-c", code, *(arguments or [])], timeout)

    def redirected_path(self, link, target):
        try:
            link.symlink_to(target)
            return
        except (OSError, NotImplementedError) as error:
            if os.name != "nt":
                self.skipTest(f"Host does not permit file redirects: {error}")
        # Windows file symlinks require a privilege this host lacks. Directory
        # junctions use the same real filesystem redirect machinery without
        # that privilege, so still exercise an actual outside-root redirect.
        import ctypes
        import struct
        from ctypes import wintypes
        link.mkdir()
        destination = str(target.parent.resolve())
        substitute = ("\\??\\" + destination).encode("utf-16-le")
        printable = destination.encode("utf-16-le")
        buffer = substitute + b"\0\0" + printable + b"\0\0"
        data = struct.pack("<IHHHHHH", 0xA0000003, len(buffer) + 8, 0,
                           0, len(substitute), len(substitute) + 2, len(printable)) + buffer
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
        handle = kernel.CreateFileW(str(link), 0x40000000, 0, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            link.rmdir()
            self.skipTest(f"Cannot create a test junction: {ctypes.WinError(ctypes.get_last_error())}")
        returned = wintypes.DWORD()
        raw = ctypes.create_string_buffer(data)
        try:
            success = kernel.DeviceIoControl(handle, 0x000900A4, raw, len(data),
                                             None, 0, ctypes.byref(returned), None)
            failure = ctypes.get_last_error()
        finally:
            kernel.CloseHandle(handle)
        if not success:
            link.rmdir()
            self.skipTest(f"Cannot set a test junction: {ctypes.WinError(failure)}")
        # RemoveDirectory deletes this junction itself, never its target tree.
        self.addCleanup(lambda: kernel.RemoveDirectoryW(str(link)))
        self.assertEqual(link.resolve(), target.parent.resolve())

    def test_streams_unicode_stdout_stderr_and_persists_configuration(self):
        directory = self.job()
        code = "import sys,time; print('首条流式日志',flush=True); time.sleep(.3); print('结束'); print('错误流文本',file=sys.stderr)"
        self.run_python(directory, code)
        self.wait_for(lambda: "首条流式日志" in "".join(self.outputs))
        self.assertTrue(self.runner.is_running, "Output must arrive before process completion")
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished, [(0, "completed")])
        log = (directory / "adapter.log").read_text(encoding="utf-8")
        self.assertIn("首条流式日志", log)
        self.assertIn("[stderr] 错误流文本", log)
        status = json.loads((directory / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["job_id"], load_cfd_job(directory)["job_id"])
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["exit_code"], 0)
        self.assertTrue(status["started_at"])
        self.assertTrue(status["ended_at"])
        self.assertEqual(status["configuration"]["executable"], sys.executable)
        self.assertTrue(status["diagnostics"])

    def test_replaces_only_finite_placeholders_as_individual_arguments(self):
        directory = self.job()
        arguments = ["{job_dir}", "{manifest}", "{result}", "有 空格的参数", "literal {unknown}"]
        self.run_python(directory, "import sys,json;print(json.dumps(sys.argv[1:],ensure_ascii=False))", arguments)
        self.wait_for(lambda: bool(self.finished))
        arrays = [json.loads(line) for text in self.outputs for line in text.splitlines() if line.startswith("[") and line.startswith('["')]
        self.assertEqual(arrays[0], [str(directory), str(directory / "manifest.json"),
                                     str(directory / "result.json"), "有 空格的参数", "literal {unknown}"])

    def test_shell_metacharacters_and_manifest_commands_are_never_executed(self):
        directory = self.job()
        sentinel = directory / "UNEXPECTED_EXECUTION.txt"
        # This extra field is deliberately outside the execution contract.
        path = directory / "manifest.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["native_solver_adapter"] = {"command": f"echo bad > {sentinel}"}
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        argument = f"& echo bad > {sentinel} ; $(echo bad) `echo bad` |"
        self.run_python(directory, "import sys,json;print(json.dumps(sys.argv[1]))", [argument])
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished[0], (0, "completed"))
        self.assertFalse(sentinel.exists())
        self.assertIn(json.dumps(argument), "".join(self.outputs))

    def test_failure_and_invalid_utf8_are_recorded_without_decoder_errors(self):
        directory = self.job()
        self.run_python(directory, "import sys;sys.stdout.buffer.write(b'\\xffbad\\n');print('solver rejected input',file=sys.stderr);sys.exit(7)")
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished, [(7, "failed")])
        log = (directory / "adapter.log").read_text(encoding="utf-8")
        self.assertIn("\ufffdbad", log)
        self.assertIn("solver rejected input", log)
        self.assertEqual(json.loads((directory / "run_status.json").read_text(encoding="utf-8"))["exit_code"], 7)

    def test_nonexistent_executable_finishes_failed(self):
        directory = self.job()
        self.runner.start(directory, str(directory / "does_not_exist.exe"), [], 10)
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished, [(-1, "failed")])
        self.assertFalse(self.runner.is_running)
        self.assertEqual(json.loads((directory / "run_status.json").read_text(encoding="utf-8"))["status"], "failed")

    def test_status_write_failure_does_not_start_or_leak_a_process(self):
        directory = self.job()
        # An existing directory cannot be atomically replaced with a status file.
        (directory / "run_status.json").mkdir()
        with self.assertRaises(OSError):
            self.run_python(directory, "print('should not execute')")
        self.assertFalse(self.runner.is_running)
        self.assertIsNone(self.runner._process)
        self.assertIsNone(self.runner._log)
        self.assertFalse(self.finished)

    def test_cancel_restricts_itself_to_its_process_and_late_success_is_ignored(self):
        directory = self.job()
        foreign = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: foreign.poll() is None and foreign.kill())
        self.run_python(directory, "import time;print('running',flush=True);time.sleep(20)")
        self.wait_for(lambda: "running" in "".join(self.outputs))
        process = self.runner._process
        self.runner.cancel()
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished[0][1], "cancelled")
        self.assertIsNone(foreign.poll(), "Cancellation affected another process")
        self.runner._on_finished(process, 0, QProcess.NormalExit)
        self.assertEqual(len(self.finished), 1)
        self.assertEqual(self.states[-1], "cancelled")
        foreign.kill()
        foreign.wait(timeout=3)

    def test_timeout_is_bounded_and_has_a_distinct_persisted_status(self):
        directory = self.job()
        self.run_python(directory, "import time;time.sleep(20)", timeout=1)
        began = time.monotonic()
        self.wait_for(lambda: bool(self.finished), seconds=5)
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(self.finished[0][1], "timed_out")
        status = json.loads((directory / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "timed_out")
        self.assertTrue(any("超时" in entry["message"] for entry in status["diagnostics"]))

    def test_refuses_old_results_without_deleting_or_overwriting_them(self):
        directory = self.job()
        result = directory / "result.json"
        result.write_text("existing user result", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "新建"):
            self.run_python(directory, "print('should not run')")
        self.assertEqual(result.read_text(encoding="utf-8"), "existing user result")
        self.assertFalse(self.runner.is_running)
        self.assertFalse((directory / "adapter.log").exists())

    def test_log_and_status_symlinks_cannot_redirect_writes_outside_the_job(self):
        for filename in ("adapter.log", "run_status.json"):
            with self.subTest(filename=filename):
                directory = self.job()
                outside = Path(self.temporary.name) / (filename + ".outside")
                outside.write_text("DO NOT CHANGE", encoding="utf-8")
                self.redirected_path(directory / filename, outside)
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    self.run_python(directory, "print('should not execute')")
                self.assertEqual(outside.read_text(encoding="utf-8"), "DO NOT CHANGE")
                self.assertFalse(self.runner.is_running)

    def test_status_redirect_appearing_during_execution_prevents_success_publication(self):
        directory = self.job()
        outside = Path(self.temporary.name) / "foreign_status.json"
        outside.write_text("KEEP", encoding="utf-8")
        self.run_python(directory, "import time;print('ready',flush=True);time.sleep(.5)")
        self.wait_for(lambda: "ready" in "".join(self.outputs))
        target = directory / "run_status.json"
        target.unlink()
        self.redirected_path(target, outside)
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished[0][1], "failed")
        self.assertEqual(outside.read_text(encoding="utf-8"), "KEEP")
        self.assertIn("无法持久化", "".join(self.outputs))

    def test_hardlinked_log_cannot_modify_an_outside_file(self):
        directory = self.job()
        outside = Path(self.temporary.name) / "foreign_user_log.txt"
        outside.write_text("ORIGINAL USER DATA", encoding="utf-8")
        os.link(outside, directory / "adapter.log")
        with self.assertRaisesRegex(ValueError, "硬链接"):
            self.run_python(directory, "print('must not run')")
        self.assertEqual(outside.read_text(encoding="utf-8"), "ORIGINAL USER DATA")
        self.assertFalse(self.runner.is_running)

    def test_unreviewed_or_unresolved_tasks_and_invalid_timeouts_do_not_start(self):
        for settings in ({"setup_reviewed": False}, {"pressure_basis": "unresolved"}, {"temperature_basis": "unresolved"}):
            with self.subTest(settings=settings):
                directory = self.job(settings)
                with self.assertRaises(ValueError):
                    self.run_python(directory, "print('should not run')")
                self.assertFalse((directory / "run_status.json").exists())
        directory = self.job()
        for timeout in (0, -1, 86401, 1.5, True, "10"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    self.run_python(directory, "pass", timeout=timeout)
        with self.assertRaises(ValueError):
            self.runner.start(directory, "python", [], 10)
        with self.assertRaises(ValueError):
            self.runner.start(directory, sys.executable, "one string", 10)
        for suffix in (".bat", ".CMD", ".ps1", ".vbs", ".py", ".sh"):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, "解释器"):
                    self.runner.start(directory, str(directory / ("script" + suffix)), [], 10)

    def test_example_normal_mode_refuses_to_fabricate_cfd(self):
        directory = self.job()
        adapter = ROOT / "scripts" / "cfd_adapter_example.py"
        self.runner.start(directory, sys.executable,
                          [str(adapter), "--manifest", "{manifest}", "--result", "{result}"], 10)
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished[0], (2, "failed"))
        self.assertFalse((directory / "result.json").exists())
        self.assertIn("未配置真实 CFD", "".join(self.outputs))

    def test_example_self_test_is_explicitly_marked_and_never_feedback_eligible(self):
        directory = self.job()
        adapter = ROOT / "scripts" / "cfd_adapter_example.py"
        self.runner.start(directory, sys.executable,
                          [str(adapter), "--self-test", "--manifest", "{manifest}", "--result", "{result}"], 10)
        self.wait_for(lambda: bool(self.finished))
        self.assertEqual(self.finished[0], (0, "completed"))
        result = import_cfd_result(directory)
        self.assertEqual(result["source_kind"], "test")
        self.assertFalse(result["feedback_eligible"])
        self.assertTrue(result["diagnostic_only"])

    def test_example_describe_reports_that_it_contains_no_solver(self):
        directory = self.job()
        adapter = ROOT / "scripts" / "cfd_adapter_example.py"
        self.runner.start(directory, sys.executable, [str(adapter), "--describe"], 10)
        self.wait_for(lambda: bool(self.finished))
        descriptions = [json.loads(line) for text in self.outputs for line in text.splitlines()
                        if line.startswith('{"protocol"')]
        self.assertFalse(descriptions[0]["contains_cfd_solver"])
        self.assertFalse((directory / "result.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows process-tree ownership regression")
    def test_windows_cancel_stops_the_adapters_owned_child_process(self):
        import ctypes
        from ctypes import wintypes
        directory = self.job()
        marker = directory / "owned_child_pid.txt"
        code = ("import subprocess,sys,time;from pathlib import Path;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);"
                f"Path({str(marker)!r}).write_text(str(child.pid));time.sleep(20)")
        self.run_python(directory, code)
        self.wait_for(marker.exists)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1001, False, int(marker.read_text()))
        self.assertTrue(handle)
        def cleanup():
            kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
        self.addCleanup(cleanup)
        def child_stopped():
            result = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(result)) and result.value != 259)
        self.runner.cancel()
        self.wait_for(lambda: bool(self.finished))
        self.wait_for(child_stopped)
        self.assertEqual(self.finished[0][1], "cancelled")

    def test_tree_stop_helper_failure_is_visible_and_handled_once(self):
        # A denied OS tree-stop must not be silently represented as confirmed
        # cancellation of all descendants. The direct child is still stopped.
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                helper = Mock()
                helper._tree_stop_handled = False
                helper._owned_root_handle = None
                helper.state.return_value = QProcess.Running if timed_out else QProcess.NotRunning
                helper.exitCode.return_value = 1
                helper.error.return_value = QProcess.UnknownError
                helper.readAllStandardError.return_value = b"Access is denied."
                process = Mock()
                process.state.return_value = QProcess.Running
                with patch.object(self.runner, "_diagnostic") as diagnostic:
                    self.runner._tree_stop_done(helper, process)
                    self.runner._tree_stop_done(helper, process)
                diagnostic.assert_called_once()
                self.assertIn("子进程可能仍在运行", diagnostic.call_args.args[1])
                self.assertIn("等待超时" if timed_out else "退出码 1", diagnostic.call_args.args[1])
                process.kill.assert_called_once()
                self.assertEqual(helper.kill.call_count, int(timed_out))


if __name__ == "__main__":
    unittest.main()
