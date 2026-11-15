"""HTTP runtime for the single-host web service.

The Windows Proactor transport in our pinned Python can raise WinError 10054
inside connection_lost before detaching the transport from its server. That
leaves server.wait_closed pending even after the listening port has closed.
Use the selector transport for this small socket-only HTTP service, and bound
Uvicorn's drain phase independently so application/worker cleanup can still run.
Worker processes use subprocess.Popen, not asyncio subprocess transports.
"""
import asyncio
import sys

import uvicorn


CONNECTION_DRAIN_TIMEOUT = 5


def windows_http_loop():
    """Per-service loop factory; do not change the system event-loop policy."""
    return asyncio.SelectorEventLoop()


def create_server(app, port):
    return uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, workers=1,
        access_log=False, proxy_headers=False,
        loop="webapp.server_runtime:windows_http_loop" if sys.platform == "win32" else "auto",
        timeout_graceful_shutdown=CONNECTION_DRAIN_TIMEOUT,
    ))
