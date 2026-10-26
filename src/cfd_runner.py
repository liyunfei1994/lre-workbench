"""Run an explicitly configured CFD adapter without a shell or UI blocking."""
from __future__ import annotations

import codecs
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import uuid

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _owned_output_path(directory, name):
    """Reject redirects before writing runner-owned log and status artifacts."""
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory or path.resolve() != path:
        raise ValueError(f"任务运行文件 {name} 不能是符号链接或重定向到任务目录之外。")
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError(f"任务运行文件 {name} 不能是共享其它文件的硬链接。")
    return path


class CfdProcessRunner(QObject):
    output = Signal(str)
    state_changed = Signal(str)
    finished = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._active = False
        self._finalized = True
        self._stop_status = None
        self._log = None
        self._status = None
        self._directory = None
        self._native_handle = None
        self._bootstrapping = False
        self._io_failed = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._kill_deadline = QTimer(self)
        self._kill_deadline.setSingleShot(True)
        self._kill_deadline.timeout.connect(self._force_stop)

    @property
    def is_running(self):
        return self._active

    def start(self, job_directory, executable, arguments, timeout_seconds=3600):
        if self.is_running:
            raise ValueError("已有 CFD 适配器正在运行，请先等待完成或取消。")
        if (not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
                or not 1 <= timeout_seconds <= 86400):
            raise ValueError("超时必须为 1～86400 秒的整数。")
        if not isinstance(executable, (str, os.PathLike)) or not str(executable).strip():
            raise ValueError("请先选择 CFD 适配器的可执行文件。")
        executable = str(executable)
        if "\x00" in executable or not Path(executable).is_absolute():
            raise ValueError("请使用用户选定可执行文件的绝对路径。")
        if Path(executable).suffix.lower() in {
                ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe",
                ".js", ".jse", ".wsf", ".wsh", ".sh", ".bash", ".py"}:
            raise ValueError("不能隐式启动脚本。请明确选择 python.exe / powershell.exe 等解释器，并逐参数传入脚本路径。")
        if (not isinstance(arguments, list) or
                any(not isinstance(argument, str) or "\x00" in argument for argument in arguments)):
            raise ValueError("适配器参数必须是字符串数组，不能包含空字符。")

        from cfd_exchange import load_cfd_job
        directory = Path(job_directory).expanduser().resolve()
        manifest = load_cfd_job(directory)
        settings = manifest["settings"]
        if settings.get("setup_reviewed") is not True:
            raise ValueError("任务尚未复核 CFD 设置，不能自动运行。请复核后新建任务。")
        if any(settings.get(key) not in ("static", "total")
               for key in ("pressure_basis", "temperature_basis")):
            raise ValueError("压力与温度口径必须明确为 static 或 total，不能为 unresolved。")
        result_path = directory / manifest["files"]["result"]
        if os.path.lexists(result_path):
            raise ValueError("此任务已存在 result.json；为避免误用旧结果，请新建 CFD 任务后运行。")
        replacements = {"{job_dir}": str(directory),
                        "{manifest}": str(directory / "manifest.json"),
                        "{result}": str(result_path)}
        resolved = []
        for argument in arguments:
            for placeholder, value in replacements.items():
                argument = argument.replace(placeholder, value)
            resolved.append(argument)

        self._directory = directory
        log_path = _owned_output_path(directory, "adapter.log")
        _owned_output_path(directory, "run_status.json")
        self._log = log_path.open("a", encoding="utf-8", newline="")
        self._status = {
            "format": "lre.cfd.run", "schema_version": 1, "run_id": str(uuid.uuid4()),
            "job_id": manifest["job_id"], "input_fingerprint": manifest["input_fingerprint"],
            "task": manifest["task"], "status": "starting", "started_at": _timestamp(),
            "process_started_at": None, "ended_at": None, "exit_code": None, "pid": None,
            "configuration": {"executable": executable, "arguments": list(arguments),
                              "resolved_arguments": resolved, "timeout_seconds": timeout_seconds},
            "log_file": "adapter.log", "diagnostics": [],
        }
        self._active = True
        self._finalized = False
        self._stop_status = None
        self._io_failed = False
        self._decoders = {name: codecs.getincrementaldecoder("utf-8")("replace")
                          for name in ("stdout", "stderr")}
        process = QProcess(self)
        self._process = process
        process.setProgram(executable)
        process.setArguments(resolved)
        process.setWorkingDirectory(str(directory))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8:replace")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.SeparateChannels)
        if os.name != "nt":
            parameters = QProcess.UnixProcessParameters()
            parameters.flags = QProcess.UnixProcessFlag.CreateNewSession
            process.setUnixProcessParameters(parameters)
        process.started.connect(lambda p=process: self._on_started(p))
        process.readyReadStandardOutput.connect(lambda p=process: self._drain(p, "stdout"))
        process.readyReadStandardError.connect(lambda p=process: self._drain(p, "stderr"))
        process.errorOccurred.connect(lambda error, p=process: self._on_error(p, error))
        process.finished.connect(lambda code, status, p=process: self._on_finished(p, code, status))
        try:
            self._bootstrapping = True
            self._set_state("starting")
            self._diagnostic("info", "启动用户配置的适配器；未使用 shell，也未读取任务内的执行命令。")
            self._bootstrapping = False
            self._timeout.start(timeout_seconds * 1000)
            process.start()
        except Exception:
            self._active = False
            self._finalized = True
            self._timeout.stop()
            if self._log is not None:
                self._log.close()
                self._log = None
            process.deleteLater()
            self._process = None
            raise
        finally:
            self._bootstrapping = False

    def _set_state(self, state):
        self._status["status"] = state
        if not self._write_status() and state == "completed":
            state = "failed"
            self._status["status"] = state
        self.state_changed.emit(state)
        return state

    def _write_status(self):
        temporary = None
        try:
            target = _owned_output_path(self._directory, "run_status.json")
            temporary = _owned_output_path(self._directory, f".run_status.{self._status['run_id']}.tmp")
            # Exclusive creation also refuses a redirected temporary file that
            # appears between the path check and opening the file.
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(self._status, stream, ensure_ascii=False, indent=2)
            temporary.replace(target)
            return True
        except (OSError, ValueError) as exc:
            if self._bootstrapping:
                raise
            self._fail_io(exc)
            return False
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _fail_io(self, error):
        if not self._io_failed:
            self._io_failed = True
            message = f"无法持久化运行日志或状态：{error}；已停止适配器，请检查任务目录。"
            self._status["diagnostics"].append({"at": _timestamp(), "level": "error", "message": message})
            self.output.emit("[error] " + message + "\n")
        if self._active:
            self._stop_status = self._stop_status or "failed"
            self._timeout.stop()
            self._stop_owned_process()
            self._kill_deadline.start(3000)

    def _diagnostic(self, level, message):
        self._status["diagnostics"].append({"at": _timestamp(), "level": level, "message": message})
        self._write_status()
        self._emit_output(f"[{level}] {message}\n")

    def _emit_output(self, text):
        if not text:
            return
        if self._log is not None:
            try:
                self._log.write(text)
                self._log.flush()
            except OSError as exc:
                if self._bootstrapping:
                    raise
                self._fail_io(exc)
        self.output.emit(text)

    def _on_started(self, process):
        if process is not self._process or self._finalized:
            return
        self._status["pid"] = int(process.processId())
        self._status["process_started_at"] = _timestamp()
        self._capture_windows_handle()
        if self._stop_status is not None:
            self._stop_owned_process()
        else:
            self._set_state("running")

    def _drain(self, process, stream, final=False):
        if process is not self._process:
            return
        raw = (process.readAllStandardOutput() if stream == "stdout"
               else process.readAllStandardError())
        text = self._decoders[stream].decode(bytes(raw), final=final)
        self._emit_output(("[stderr] " if stream == "stderr" and text else "") + text)

    def _on_error(self, process, error):
        if process is not self._process or self._finalized:
            return
        self._diagnostic("error", process.errorString())
        if error == QProcess.FailedToStart:
            self._finalize(-1, self._stop_status or "failed")

    def _on_finished(self, process, code, exit_status):
        if process is not self._process or self._finalized:
            return
        self._drain(process, "stdout", final=True)
        self._drain(process, "stderr", final=True)
        status = (self._stop_status or
                  ("completed" if code == 0 and exit_status == QProcess.NormalExit else "failed"))
        self._finalize(int(code), status)

    def cancel(self):
        self._request_stop("cancelled")

    def _on_timeout(self):
        self._request_stop("timed_out")

    def _request_stop(self, status):
        if not self.is_running or self._stop_status is not None:
            return
        self._stop_status = status
        self._timeout.stop()
        self._set_state("cancelling" if status == "cancelled" else "timing_out")
        self._diagnostic("warning", "用户取消适配器运行。" if status == "cancelled" else "适配器运行已超时。")
        self._stop_owned_process()
        # QProcess kill is asynchronous. Never let a missing OS completion
        # notification leave the UI in a permanently running state.
        self._kill_deadline.start(3000)

    def _capture_windows_handle(self):
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        self._native_handle = kernel.OpenProcess(0x1000, False, self._status["pid"])

    @staticmethod
    def _close_handle(handle):
        if handle:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)

    def _stop_owned_process(self):
        process = self._process
        if process is None or process.state() == QProcess.NotRunning:
            return
        pid = int(process.processId())
        if not pid or pid != self._status.get("pid"):
            process.kill()  # Starting; still restricted to this QProcess.
            return
        if os.name == "nt" and self._native_handle:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            code = wintypes.DWORD()
            if kernel.GetExitCodeProcess(self._native_handle, ctypes.byref(code)) and code.value == 259:
                # Holding the original process handle prevents PID reuse while
                # taskkill addresses only the tree started by this runner.
                handle = self._native_handle
                self._native_handle = None
                helper = QProcess(self)
                helper._owned_root_handle = handle
                kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
                kernel.GetSystemDirectoryW.restype = wintypes.UINT
                system_directory = ctypes.create_unicode_buffer(32768)
                length = kernel.GetSystemDirectoryW(system_directory, len(system_directory))
                if not 0 < length < len(system_directory):
                    self._close_handle(handle)
                    process.kill()
                    return
                # Resolve the OS helper through the OS API, never an inherited
                # PATH or SystemRoot value that might name an untrusted file.
                executable = Path(system_directory.value) / "taskkill.exe"
                helper.setProgram(str(executable))
                helper.setArguments(["/PID", str(pid), "/T", "/F"])
                helper.finished.connect(lambda _code, _status, h=helper, p=process: self._tree_stop_done(h, p))
                helper.errorOccurred.connect(lambda _error, h=helper, p=process: self._tree_stop_done(h, p))
                timer = QTimer(helper)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda h=helper, p=process: self._tree_stop_done(h, p))
                helper._deadline = timer
                timer.start(1500)
                helper.start()
                return
        if os.name != "nt":
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                    return
            except ProcessLookupError:
                return
        process.kill()

    def _tree_stop_done(self, helper, process):
        # Timeout, error and finished can all arrive for the same helper.
        if getattr(helper, "_tree_stop_handled", False):
            return
        helper._tree_stop_handled = True
        helper._deadline.stop()
        timed_out = helper.state() != QProcess.NotRunning
        if timed_out:
            helper.kill()
        if timed_out or helper.exitCode() != 0 or helper.error() == QProcess.FailedToStart:
            encoding = "utf-8"
            if os.name == "nt":
                import ctypes
                encoding = f"cp{ctypes.WinDLL('kernel32').GetOEMCP()}"
            diagnostic = bytes(helper.readAllStandardError()).decode(encoding, errors="replace").strip()
            reason = "等待超时" if timed_out else f"退出码 {helper.exitCode()}"
            if diagnostic:
                reason += "; " + diagnostic[:2048]
            self._diagnostic("warning", "Windows 进程树停止未获确认（" + reason +
                             "）；仅尝试终止本次启动的适配器主进程，子进程可能仍在运行。")
        if process.state() != QProcess.NotRunning:
            process.kill()
        handle = getattr(helper, "_owned_root_handle", None)
        helper._owned_root_handle = None
        self._close_handle(handle)

    def _force_stop(self):
        if not self._active:
            return
        self._process.kill()
        self._diagnostic("warning", "已强制停止适配器；停止等待已达到上限。")
        self._finalize(-1, self._stop_status or "failed")

    def _finalize(self, code, status):
        if self._finalized:
            return
        self._finalized = True
        self._active = False
        self._timeout.stop()
        self._kill_deadline.stop()
        self._status["exit_code"] = code
        self._status["ended_at"] = _timestamp()
        self._emit_output(f"[status] {status}; exit_code={code}\n")
        if self._log is not None:
            try:
                self._log.close()
            except OSError as exc:
                self._fail_io(exc)
            self._log = None
        if self._io_failed and status == "completed":
            status = "failed"
        self._status["diagnostics"].append({"at": _timestamp(),
            "level": "info" if status == "completed" else "warning", "message": f"{status}; exit_code={code}"})
        status = self._set_state(status)
        self._close_handle(self._native_handle)
        self._native_handle = None
        self.finished.emit(code, status)
