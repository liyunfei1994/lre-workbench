"""Real loopback shutdown, isolated from the user's database and console.

The SIGINT wrapper raises a Python signal in its own asyncio main thread. It
does not send console control events to the test runner or any other process.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parent.parent

# Test-only instrumentation. The production application exposes no signal URL.
SIGNAL_WRAPPER = r"""
import asyncio
import os
from pathlib import Path
import signal
import webapp.server_runtime as runtime
from webapp.run import main

original_create = runtime.create_server
def create(app, port):
    server = original_create(app, port)
    original_loop = server.main_loop
    async def main_loop():
        async def interrupt_when_requested():
            marker = Path(os.environ['LRE_TEST_SIGNAL_MARKER'])
            while not marker.exists():
                await asyncio.sleep(0.05)
            print('TEST_MAIN_THREAD_SIGINT', flush=True)
            signal.raise_signal(signal.SIGINT)
        watcher = asyncio.create_task(interrupt_when_requested())
        try:
            await original_loop()
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    server.main_loop = main_loop
    return server
runtime.create_server = create
main()
"""


@unittest.skipUnless(sys.version_info[:2] == (3, 14), "Web runtime requires Python 3.14")
class ServerShutdownProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lre-shutdown-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.database = self.folder / "workspace.sqlite3"
        self.log_path = self.folder / "service.log"
        self.process = None
        self.log = None
        self.addCleanup(self.cleanup_process)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def cleanup_process(self):
        # This is only the exact child created by this test. Never enumerate or
        # terminate application processes by executable name.
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.log is not None:
            self.log.close()

    def logs(self):
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-12000:]

    def start(self, *, signal_wrapper=False):
        self.log = self.log_path.open("wb")
        args = [sys.executable, "-u"]
        args += ["-c", SIGNAL_WRAPPER] if signal_wrapper else ["-m", "webapp.run"]
        args += ["--database", str(self.database), "--port", str(self.port)]
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                           LRE_TEST_SIGNAL_MARKER=str(self.folder / "interrupt.request"))
        self.process = subprocess.Popen(
            args, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        until = time.monotonic() + 15
        expected_id = hashlib.sha256(str(self.database.resolve()).encode()).hexdigest()
        while time.monotonic() < until:
            if self.process.poll() is not None:
                self.fail("Service exited during startup:\n" + self.logs())
            try:
                with self.opener.open(f"http://127.0.0.1:{self.port}/api/health", timeout=.3) as response:
                    health = json.load(response)
                self.assertEqual(health.get("workspace_id"), expected_id)
                break
            except OSError:
                time.sleep(.05)
        else:
            self.fail("Service did not become healthy within 15 seconds:\n" + self.logs())
        # A record created before shutdown must survive. It is not a job and
        # cannot trigger a calculation in the running queue.
        with closing(sqlite3.connect(self.database, timeout=1)) as db, db:
            db.execute("INSERT INTO workspace_settings(key,value) VALUES(?,?)",
                       ("shutdown_test_marker", "retained"))

    def assert_clean_shutdown(self, started):
        try:
            code = self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.fail("Service failed to exit after stop request:\n" + self.logs())
        self.assertEqual(code, 0, self.logs())
        self.assertLess(time.monotonic() - started, 12, self.logs())
        output = self.logs()
        for message in ("Shutting down", "Application shutdown complete.",
                        "Finished server process", "Web service stopped."):
            self.assertIn(message, output)
        self.assertNotIn("Traceback (most recent call last)", output)
        self.assertNotIn("_ProactorBasePipeTransport", output)
        # The old failure had a closed listening port but a live process; verify
        # both process termination and the database/queue lock, not just health.
        with socket.socket() as probe:
            probe.settimeout(.3)
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", self.port)), 0)
        with closing(sqlite3.connect(self.database, timeout=1)) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual(db.execute("SELECT value FROM workspace_settings WHERE key=?",
                                        ("shutdown_test_marker",)).fetchone(), ("retained",))
        with (self.folder / "server.lock").open("r+b") as lock:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)

    def test_stop_file_closes_real_idle_service_and_releases_workspace(self):
        self.start()
        started = time.monotonic()
        (self.folder / "stop.request").write_text("stop", encoding="ascii")
        self.assert_clean_shutdown(started)
        self.assertFalse((self.folder / "stop.request").exists())

    def test_sigint_after_client_resets_and_incomplete_request_exits_cleanly(self):
        self.start(signal_wrapper=True)
        # Force reset after the server has responded, rather than merely closing
        # an unaccepted client socket. Windows uses two ushort linger members.
        linger = struct.pack("HH" if os.name == "nt" else "ii", 1, 0)
        for _ in range(4):
            with socket.create_connection(("127.0.0.1", self.port), timeout=1) as reset:
                reset.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: keep-alive\r\n\r\n")
                self.assertTrue(reset.recv(1024).startswith(b"HTTP/1.1 200"))
                reset.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        with socket.create_connection(("127.0.0.1", self.port), timeout=1) as incomplete:
            incomplete.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            started = time.monotonic()
            (self.folder / "interrupt.request").write_text("interrupt", encoding="ascii")
            self.assert_clean_shutdown(started)
        self.assertIn("TEST_MAIN_THREAD_SIGINT", self.logs())


if __name__ == "__main__":
    unittest.main()
