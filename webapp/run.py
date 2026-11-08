"""One loopback service, optionally behind an explicitly configured HTTPS proxy."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=ROOT / "user_data/web-workbench/workspace.sqlite3")
    parser.add_argument("--open-browser", action="store_true")
    access_mode = parser.add_mutually_exclusive_group()
    access_mode.add_argument("--personal-workspace", action="store_true",
                             help="Persist owner-only access for a local personal workspace.")
    access_mode.add_argument("--private-workspace", action="store_true",
                        help="Persist per-owner project isolation in a separate pilot database.")
    parser.add_argument("--public-origin", help="Exact HTTPS origin served by a local reverse proxy.")
    args = parser.parse_args()
    from webapp.access import https_origin
    try:
        args.public_origin = https_origin(args.public_origin)
    except ValueError as exc:
        parser.error(str(exc))
    if args.personal_workspace and args.public_origin:
        parser.error("A personal workspace cannot have a public origin.")
    if sys.version_info[:2] != (3, 14):
        parser.error("This edition requires Python 3.14. Run setup_web_windows.bat.")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    args.database = args.database.resolve()
    url = f"http://127.0.0.1:{args.port}"
    browser_url = args.public_origin or url
    identity = hashlib.sha256(str(args.database).encode()).hexdigest()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/health", timeout=2) as response:
            existing = json.load(response)
        if existing.get("service") == "lre-workbench-web" and existing.get("workspace_id") == identity:
            if (existing.get("public_origin") != args.public_origin or
                    (args.private_workspace and existing.get("workspace_access") != "private") or
                    (args.personal_workspace and existing.get("workspace_access") != "personal")):
                parser.error("This workspace is running with different access settings. Stop it before changing the entry point.")
            print("Workspace already running: " + browser_url)
            if args.open_browser:
                webbrowser.open(browser_url)
            return
    except (OSError, ValueError):
        pass
    # Fail before opening the database or recovering jobs if the port belongs
    # to some other application.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"Port {args.port} is occupied. Close that service or use --port.")
    from webapp.solver import science_environment
    science_environment()
    from webapp.server_runtime import create_server
    from webapp.app import create_app
    try:
        app = create_app(args.database, private_workspace=args.private_workspace,
                         personal_workspace=args.personal_workspace,
                         public_origin=args.public_origin)
    except ValueError as exc:
        parser.error(str(exc))
    stop_file = args.database.parent / "stop.request"
    stop_file.unlink(missing_ok=True)
    server = create_server(app, args.port)
    done = threading.Event()

    def lifecycle():
        opened = False
        while not done.wait(0.5):
            if stop_file.exists():
                stop_file.unlink(missing_ok=True)
                server.should_exit = True
                return
            if server.started and args.open_browser and not opened:
                webbrowser.open(browser_url)
                opened = True
    thread = threading.Thread(target=lifecycle, daemon=True)
    thread.start()
    access = ("HTTPS pilot via local proxy; ordinary users see only their own projects."
              if args.public_origin else
              "Personal workspace; only the owner can sign in."
              if app.state.workspace_access == "personal" else "Local computer only.")
    print(f"\nLRE Workbench Web: {browser_url}\nData: {args.database.parent}\n"
          f"{access}\nStop this instance with Ctrl+C or {stop_file}.\n", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:
        # Uvicorn replays SIGINT after its graceful shutdown. This is a normal
        # user-requested stop, not a startup failure for the batch launcher.
        pass
    finally:
        done.set()
        thread.join(timeout=2)
    print("Web service stopped.", flush=True)


if __name__ == "__main__":
    main()
