"""Global-first startup must fall back without changing global packages."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import venv

from webapp import bootstrap

ROOT = Path(__file__).resolve().parents[1]


class WebBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(bootstrap, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.project_python = self.root / ".venv-web" / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        self.project_python.parent.mkdir(parents=True)
        self.project_python.touch()
        self.global_python = self.root / "global" / "python.exe"
        self.base_patch = patch.object(sys, "_base_executable", str(self.global_python))
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)

    def test_healthy_global_is_used_even_when_called_from_project_venv(self):
        with patch.object(sys, "executable", str(self.project_python)), \
                patch.object(bootstrap, "probe_environment", return_value=(True, "")) as probe, \
                patch.object(bootstrap, "prepare_project_environment") as prepare:
            self.assertEqual(bootstrap.select_start_environment(), self.global_python)
        probe.assert_called_once_with(self.global_python)
        prepare.assert_not_called()
        self.assertIn("Using global Python", self.output.getvalue())

    def test_unready_global_uses_healthy_project_without_installing(self):
        for reason in ("scikit-fem: not installed", "numpy: version mismatch", "DLL load failed"):
            with self.subTest(reason=reason), \
                    patch.object(bootstrap, "probe_environment", side_effect=[(False, reason), (True, "")]), \
                    patch.object(bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "3.14\n")), \
                    patch.object(bootstrap.subprocess, "check_call") as install:
                self.assertEqual(bootstrap.select_start_environment(), self.project_python)
                install.assert_not_called()
        self.assertIn("Falling back", self.output.getvalue())

    def test_missing_packages_are_installed_only_into_project_environment(self):
        with patch.object(bootstrap, "probe_environment", return_value=(False, "missing package")), \
                patch.object(bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "3.14\n")), \
                patch.object(bootstrap.subprocess, "check_call") as install:
            self.assertEqual(bootstrap.select_start_environment(), self.project_python)
        calls = [call.args[0] for call in install.call_args_list]
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(command[0] == str(self.project_python) for command in calls))
        self.assertIn("install", calls[0])
        self.assertEqual(calls[1][1:], ["-m", "pip", "check"])
        self.assertEqual(calls[2][1:], ["-m", "webapp.bootstrap", "verify"])

    def test_setup_always_prepares_project_environment(self):
        with patch.object(bootstrap, "select_start_environment") as select, \
                patch.object(bootstrap, "prepare_project_environment", return_value=self.project_python) as prepare:
            self.assertEqual(bootstrap.main(["setup"]), 0)
        select.assert_not_called()
        prepare.assert_called_once_with()

    def test_server_failure_is_returned_without_switching_environments(self):
        with patch.object(bootstrap, "select_start_environment", return_value=self.global_python), \
                patch.object(bootstrap.subprocess, "Popen") as launch, \
                patch.object(bootstrap, "prepare_project_environment") as prepare:
            launch.return_value.__enter__.return_value.wait.return_value = 7
            self.assertEqual(bootstrap.main(["start"]), 7)
        launch.assert_called_once_with(
            [str(self.global_python), "-m", "webapp.run", "--personal-workspace", "--open-browser"],
            cwd=self.root)
        prepare.assert_not_called()

    def test_console_interrupts_request_stop_and_wait_without_killing_child(self):
        folder = self.root / "user_data/web-workbench"
        folder.mkdir(parents=True)
        stop = folder / "stop.request"
        signals = [signal.SIGINT]
        if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        previous = {signum: signal.getsignal(signum) for signum in signals}
        for signum in signals:
            with self.subTest(signum=signum), patch.object(bootstrap.subprocess, "Popen") as launch:
                process = launch.return_value.__enter__.return_value
                calls = []

                def wait(*, timeout):
                    calls.append(timeout)
                    if len(calls) == 1:
                        signal.getsignal(signum)(signum, None)
                    elif len(calls) == 2:
                        self.assertEqual(stop.read_text(), "stop")
                        stop.unlink()  # The service consumed the first request.
                        signal.getsignal(signum)(signum, None)
                    else:
                        self.assertEqual(stop.read_text(), "stop")
                        return 0
                    raise subprocess.TimeoutExpired("server", timeout)

                process.wait.side_effect = wait
                self.assertEqual(bootstrap.launch_server(self.global_python), 0)
                self.assertEqual(len(calls), 3)
                process.kill.assert_not_called()
                process.terminate.assert_not_called()
                launch.return_value.kill.assert_not_called()
                self.assertEqual({key: signal.getsignal(key) for key in signals}, previous)

    def test_keyboard_interrupt_raised_by_wait_does_not_escape_or_kill(self):
        folder = self.root / "user_data/web-workbench"
        folder.mkdir(parents=True)
        previous = signal.getsignal(signal.SIGINT)
        with patch.object(bootstrap.subprocess, "Popen") as launch:
            process = launch.return_value.__enter__.return_value
            process.wait.side_effect = [KeyboardInterrupt, subprocess.TimeoutExpired("server", .25), 9]
            self.assertEqual(bootstrap.launch_server(self.global_python), 9)
            self.assertEqual(process.wait.call_count, 3)
            process.kill.assert_not_called()
            process.terminate.assert_not_called()
        self.assertEqual((folder / "stop.request").read_text(), "stop")
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_launch_error_restores_handlers(self):
        signals = [signal.SIGINT]
        if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        previous = {signum: signal.getsignal(signum) for signum in signals}
        with patch.object(bootstrap.subprocess, "Popen", side_effect=OSError("launch failed")):
            with self.assertRaisesRegex(OSError, "launch failed"):
                bootstrap.launch_server(self.global_python)
        self.assertEqual({key: signal.getsignal(key) for key in signals}, previous)

    def test_stop_write_failure_is_reported_and_does_not_require_science(self):
        (self.root / "user_data/web-workbench").mkdir(parents=True)
        errors = io.StringIO()
        with patch.object(Path, "write_text", side_effect=PermissionError("write denied")), \
                patch.object(bootstrap, "verify") as verify, \
                patch.object(bootstrap, "select_start_environment") as select, \
                contextlib.redirect_stderr(errors):
            self.assertEqual(bootstrap.main(["stop"]), 1)
        self.assertIn("Could not write stop request", errors.getvalue())
        self.assertIn("write denied", errors.getvalue())
        verify.assert_not_called()
        select.assert_not_called()

    def test_launcher_reports_stop_write_failure_and_still_waits(self):
        (self.root / "user_data/web-workbench").mkdir(parents=True)
        errors = io.StringIO()
        with patch.object(Path, "write_text", side_effect=PermissionError("write denied")), \
                patch.object(bootstrap.subprocess, "Popen") as launch, contextlib.redirect_stderr(errors):
            process = launch.return_value.__enter__.return_value
            process.wait.side_effect = [KeyboardInterrupt, 0]
            self.assertEqual(bootstrap.launch_server(self.global_python), 0)
            self.assertEqual(process.wait.call_count, 2)
            process.kill.assert_not_called()
            process.terminate.assert_not_called()
        self.assertIn("Could not write stop request", errors.getvalue())

    def test_real_child_finishes_cleanup_after_parent_interrupt(self):
        # Only the launcher in this test receives the injected Python signal.
        # The lightweight child lives entirely in the temporary workspace.
        folder = self.root / "user_data/web-workbench"
        folder.mkdir(parents=True)
        package = self.root / "webapp"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "run.py").write_text(
            "from pathlib import Path\nimport sys,time\n"
            "folder=Path('user_data/web-workbench')\n"
            "(folder/'ready').touch()\n"
            "deadline=time.monotonic()+5\n"
            "while not (folder/'stop.request').exists():\n"
            "    if time.monotonic()>deadline: sys.exit(5)\n"
            "    time.sleep(.01)\n"
            "time.sleep(.5)\n"
            "(folder/'cleanup-complete').touch()\n", encoding="utf-8")
        sent = threading.Event()

        def interrupt_when_ready():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (folder / "ready").exists():
                    sent.set()
                    signal.raise_signal(signal.SIGINT)
                    return
                time.sleep(.01)

        thread = threading.Thread(target=interrupt_when_ready, daemon=True)
        previous = signal.getsignal(signal.SIGINT)
        thread.start()
        try:
            self.assertEqual(bootstrap.launch_server(Path(sys.executable)), 0)
        finally:
            thread.join(timeout=4)
        self.assertTrue(sent.is_set())
        self.assertTrue((folder / "cleanup-complete").exists())
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_stop_does_not_require_working_science_packages(self):
        folder = self.root / "user_data/web-workbench"
        folder.mkdir(parents=True)
        with patch.object(bootstrap, "verify") as verify, \
                patch.object(bootstrap, "select_start_environment") as select:
            self.assertEqual(bootstrap.main(["stop"]), 0)
        verify.assert_not_called()
        select.assert_not_called()
        self.assertEqual((folder / "stop.request").read_text(), "stop")

    def test_probe_handles_broken_executable_and_timeout(self):
        for error in (OSError("missing interpreter"), subprocess.TimeoutExpired("python", 90)):
            with self.subTest(error=error), patch.object(bootstrap.subprocess, "run", side_effect=error):
                ready, detail = bootstrap.probe_environment(self.global_python)
            self.assertFalse(ready)
            self.assertTrue(detail)

    def test_verify_reports_missing_and_wrong_versions(self):
        with patch.object(bootstrap, "pins", return_value={"one": "1.0", "two": "2.0"}), \
                patch.object(bootstrap.importlib.metadata, "version", side_effect=[
                    bootstrap.importlib.metadata.PackageNotFoundError("one"), "3.0"]):
            self.assertEqual(bootstrap.verify(), 1)
        self.assertIn("one: not installed (required 1.0)", self.output.getvalue())
        self.assertIn("two: 3.0 (required 2.0)", self.output.getvalue())

    def test_verify_rejects_installed_but_broken_native_package(self):
        with patch.object(bootstrap, "pins", return_value={}), \
                patch.object(bootstrap.importlib, "import_module", side_effect=ImportError("DLL unavailable")):
            self.assertEqual(bootstrap.verify(), 1)
        self.assertIn("DLL unavailable", self.output.getvalue())


@unittest.skipUnless(os.name == "nt", "Windows batch launcher")
class WindowsWebLauncherTests(unittest.TestCase):
    def test_batch_prefers_global_python_from_another_working_directory(self):
        # A lightweight recorder proves which interpreter the real BAT/PS1 chose.
        # No server is opened and no personal workspace is touched.
        with tempfile.TemporaryDirectory(prefix="web launch 中文 ") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "webapp").mkdir()
            (root / "elsewhere").mkdir()
            shutil.copy2(ROOT / "start_web_windows.bat", root)
            shutil.copy2(ROOT / "scripts/launch_web.ps1", root / "scripts")
            (root / "webapp/__init__.py").write_text("", encoding="utf-8")
            (root / "webapp/bootstrap.py").write_text(
                "import json, sys\nprint(json.dumps({'executable': sys.executable, "
                "'global': sys.prefix == sys.base_prefix, 'mode': sys.argv[-1]}))\n", encoding="utf-8")
            venv.EnvBuilder(with_pip=False).create(root / ".venv-web")
            env = dict(os.environ, LRE_NO_PAUSE="1", PYTHONUTF8="1")
            env["PATH"] = str(Path(sys._base_executable).parent) + os.pathsep + env.get("PATH", "")
            for override in (None, str(root / "nonexistent/python.exe")):
                with self.subTest(override=override):
                    env.pop("LRE_PYTHON", None)
                    if override:
                        env["LRE_PYTHON"] = override
                    result = subprocess.run(["cmd.exe", "/d", "/c", str(root / "start_web_windows.bat")],
                                            cwd=root / "elsewhere", env=env, capture_output=True,
                                            text=True, encoding="utf-8", errors="replace", timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    record = json.loads(result.stdout.strip().splitlines()[-1])
                    self.assertTrue(record["global"], record)
                    self.assertEqual(record["mode"], "start")


if __name__ == "__main__":
    unittest.main()
