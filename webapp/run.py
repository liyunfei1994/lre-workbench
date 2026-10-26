"""One loopback-only service. Run from the checkout with python -m webapp.run."""
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
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        parser.error("This edition requires Python 3.14. Run setup_web_windows.bat.")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    args.database = args.database.resolve()
    url = f"http://127.0.0.1:{args.port}"
    identity = hashlib.sha256(str(args.database).encode()).hexdigest()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/health", timeout=2) as response:
            existing = json.load(response)
        if existing.get("service") == "lre-workbench-web" and existing.get("workspace_id") == identity:
            print("Workspace already running: " + url)
            if args.open_browser:
                webbrowser.open(url)
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
    import uvicorn
    from webapp.app import create_app
    app = create_app(args.database)
    stop_file = args.database.parent / "stop.request"
    stop_file.unlink(missing_ok=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                        workers=1, access_log=False, proxy_headers=False))
    done = threading.Event()

    def lifecycle():
        opened = False
        while not done.wait(0.5):
            if stop_file.exists():
                stop_file.unlink(missing_ok=True)
                server.should_exit = True
                return
            if server.started and args.open_browser and not opened:
                webbrowser.open(url)
                opened = True
    thread = threading.Thread(target=lifecycle, daemon=True)
    thread.start()
    print(f"\nLRE Workbench Web: {url}\nData: {args.database.parent}\n"
          "Local computer only. Close this window or run stop_web_windows.bat to stop.\n", flush=True)
    try:
        server.run()
    finally:
        done.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
