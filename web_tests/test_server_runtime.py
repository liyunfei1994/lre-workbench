"""Shutdown must reach application cleanup even with a leaked connection."""
import asyncio
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from webapp import run, server_runtime


class ServerRuntimeTests(unittest.TestCase):
    def test_bounded_drain_and_windows_loop_are_local_to_this_service(self):
        server = server_runtime.create_server(Mock(), 8765)
        self.assertEqual(server.config.timeout_graceful_shutdown, 5)
        self.assertEqual(server.config.host, "127.0.0.1")
        self.assertFalse(server.config.proxy_headers)
        if sys.platform == "win32":
            # Do not install a global event-loop policy or monkeypatch Python.
            before = asyncio.new_event_loop()
            loop = server.config.get_loop_factory()()
            after = asyncio.new_event_loop()
            try:
                self.assertIsInstance(loop, asyncio.SelectorEventLoop)
                self.assertIs(type(before), type(after))
                self.assertIsInstance(after, asyncio.ProactorEventLoop)
            finally:
                for item in (before, loop, after):
                    item.close()
        else:
            self.assertEqual(server.config.loop, "auto")

    def test_run_treats_replayed_ctrl_c_as_success_and_joins_monitor(self):
        with tempfile.TemporaryDirectory() as folder:
            server = Mock(started=False)
            server.run.side_effect = KeyboardInterrupt
            output = io.StringIO()
            args = ["webapp.run", "--database", str(Path(folder) / "workspace.sqlite3")]
            app = SimpleNamespace(state=SimpleNamespace(workspace_access="personal"))
            with patch.object(sys, "argv", args), \
                    patch("webapp.run.urllib.request.build_opener") as opener, \
                    patch("webapp.run.socket.socket"), \
                    patch("webapp.solver.science_environment"), \
                    patch("webapp.app.create_app", return_value=app), \
                    patch("webapp.server_runtime.create_server", return_value=server), \
                    patch("webapp.run.threading.Thread") as thread, redirect_stdout(output):
                opener.return_value.open.side_effect = OSError("no existing service")
                self.assertIsNone(run.main())
            server.run.assert_called_once()
            thread.return_value.join.assert_called_once_with(timeout=2)
            self.assertIn("Web service stopped.", output.getvalue())

    def test_run_does_not_disguise_a_server_failure_as_a_normal_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            server = Mock(started=False)
            server.run.side_effect = RuntimeError("unrelated failure")
            output = io.StringIO()
            args = ["webapp.run", "--database", str(Path(folder) / "workspace.sqlite3")]
            app = SimpleNamespace(state=SimpleNamespace(workspace_access="personal"))
            with patch.object(sys, "argv", args), \
                    patch("webapp.run.urllib.request.build_opener") as opener, \
                    patch("webapp.run.socket.socket"), \
                    patch("webapp.solver.science_environment"), \
                    patch("webapp.app.create_app", return_value=app), \
                    patch("webapp.server_runtime.create_server", return_value=server), \
                    patch("webapp.run.threading.Thread") as thread, redirect_stdout(output):
                opener.return_value.open.side_effect = OSError("no existing service")
                with self.assertRaisesRegex(RuntimeError, "unrelated failure"):
                    run.main()
            thread.return_value.join.assert_called_once_with(timeout=2)
            self.assertNotIn("Web service stopped.", output.getvalue())


class DrainFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_closed_that_never_finishes_does_not_skip_lifespan_cleanup(self):
        server = server_runtime.create_server(Mock(), 8765)
        server.config.timeout_graceful_shutdown = .02
        reached_wait = asyncio.Event()

        async def stuck_wait_closed():
            reached_wait.set()
            await asyncio.Future()

        listener = Mock(wait_closed=AsyncMock(side_effect=stuck_wait_closed))
        server.servers = [listener]
        server.lifespan = SimpleNamespace(shutdown=AsyncMock())
        # This reproduces the post-RST state: the protocol has been removed,
        # but the asyncio listener still counts an attached transport.
        self.assertFalse(server.server_state.connections)
        with self.assertLogs("uvicorn.error", level="ERROR") as logs:
            await asyncio.wait_for(server.shutdown(), timeout=1)
        self.assertTrue(reached_wait.is_set())
        listener.close.assert_called_once()
        server.lifespan.shutdown.assert_awaited_once()
        self.assertIn("timeout graceful shutdown exceeded", " ".join(logs.output))

    async def test_pending_http_task_is_cancelled_before_application_cleanup(self):
        server = server_runtime.create_server(Mock(), 8765)
        server.config.timeout_graceful_shutdown = .02
        server.servers = []
        server.lifespan = SimpleNamespace(shutdown=AsyncMock())
        task = asyncio.create_task(asyncio.Event().wait())
        server.server_state.tasks.add(task)
        try:
            with self.assertLogs("uvicorn.error", level="ERROR"):
                await asyncio.wait_for(server.shutdown(), timeout=1)
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(task.cancelled())
            server.lifespan.shutdown.assert_awaited_once()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
