"""Prefer a verified global Python, with an isolated web environment as fallback."""
import argparse
import importlib
import importlib.metadata
from pathlib import Path
import signal
import struct
import subprocess
import sys
import sysconfig
import venv

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements-web.txt"


def pins():
    return dict(line.strip().split("==") for line in REQUIREMENTS.read_text().splitlines()
                if line.strip() and not line.startswith("#"))


def verify():
    if (sys.version_info[:2] != (3, 14) or struct.calcsize("P") != 8
            or sysconfig.get_config_var("Py_GIL_DISABLED")):
        print("Standard 64-bit Python 3.14 is required.")
        return 1
    missing = []
    for name, expected in pins().items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            missing.append(f"{name}: {actual or 'not installed'} (required {expected})")
    if missing:
        print("Environment needs packages: " + ", ".join(missing))
        return 1
    try:
        # Metadata alone does not detect a missing DLL or an unusable web stack.
        for module in ("uvicorn", "matplotlib", "rocketcea.py_cea", "webapp.app"):
            importlib.import_module(module)
        from webapp.solver import science_environment
        science_environment()
    except Exception as exc:
        print(f"Environment check failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


def probe_environment(python):
    """Check each interpreter in a fresh process before selecting it."""
    try:
        result = subprocess.run([str(python), "-m", "webapp.bootstrap", "verify"],
                                cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def prepare_project_environment():
    folder = ROOT / ".venv-web"
    python = folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(folder)
    version = subprocess.run([str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                             capture_output=True, text=True, check=True).stdout.strip()
    if version != "3.14":
        raise SystemExit(".venv-web uses another Python version. Rename that folder and run setup again.")
    ready, details = probe_environment(python)
    if not ready:
        print(details, flush=True)
        print("Preparing the project virtual environment...", flush=True)
        subprocess.check_call([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(REQUIREMENTS)], cwd=ROOT)
        subprocess.check_call([str(python), "-m", "pip", "check"], cwd=ROOT)
        subprocess.check_call([str(python), "-m", "webapp.bootstrap", "verify"], cwd=ROOT)
    return python


def select_start_environment():
    # A launch from either project venv should still prefer its base installation.
    preferred = Path(getattr(sys, "_base_executable", None) or sys.executable)
    ready, details = probe_environment(preferred)
    if ready:
        print(f"Using global Python: {preferred}", flush=True)
        return preferred
    print(f"Global Python is not ready: {preferred}\n{details}", flush=True)
    print("Falling back to the project virtual environment.", flush=True)
    python = prepare_project_environment()
    print(f"Using project virtual environment: {python}", flush=True)
    return python


def request_stop():
    """Write the local instance's cooperative stop request without science imports."""
    folder = ROOT / "user_data/web-workbench"
    try:
        if folder.exists():
            (folder / "stop.request").write_text("stop", encoding="ascii")
    except OSError as exc:
        print(f"Could not write stop request: {exc}", file=sys.stderr, flush=True)
        return False
    return True


def launch_server(python):
    """Wait through console interrupts instead of killing the server after 0.25 s.

    Windows sends console signals to both launcher and server. The server owns
    shutdown; this launcher also requests it through the existing stop file and
    waits for the real exit code. Signal handlers do no file or console I/O.
    """
    requests = 0

    def interrupted(signum, frame):
        nonlocal requests
        requests += 1

    handled = 0
    original_handlers = {}
    signals = [signal.SIGINT]
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    try:
        for signum in signals:
            original_handlers[signum] = signal.signal(signum, interrupted)
        # subprocess.call catches KeyboardInterrupt and kills its child after
        # Popen.wait's brief grace period. Explicit waiting leaves lifespan and
        # worker cleanup to the server, including repeated Ctrl+C/Ctrl+Break.
        with subprocess.Popen([str(python), "-m", "webapp.run", "--personal-workspace", "--open-browser"],
                              cwd=ROOT) as process:
            while True:
                if requests != handled:
                    handled = requests
                    if request_stop():
                        print("Stop requested; waiting for the web service to finish shutdown.", flush=True)
                    else:
                        print("Waiting for the server's console shutdown; press Ctrl+C again to retry the stop file.",
                              file=sys.stderr, flush=True)
                try:
                    return process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    continue
                except KeyboardInterrupt:
                    # Also support a wait implementation that raises directly.
                    interrupted(signal.SIGINT, None)
    finally:
        for signum, previous in original_handlers.items():
            signal.signal(signum, previous)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["start", "setup", "stop", "verify"])
    args = parser.parse_args(argv)
    if args.mode == "stop":
        if not request_stop():
            return 1
        print("Stop requested. A running calculation will be marked interrupted.")
        return 0
    if sys.version_info[:2] != (3, 14):
        raise SystemExit("Python 3.14 is required.")
    if args.mode == "verify":
        return verify()
    if args.mode == "start":
        python = select_start_environment()
        return launch_server(python)
    python = prepare_project_environment()
    print(f"Project web environment ready: {python}\nDouble-click start_web_windows.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
