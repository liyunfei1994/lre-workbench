"""Prepare a separate, pinned web environment without changing the desktop venv."""
import argparse
import importlib.metadata
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements-web.txt"


def pins():
    return dict(line.strip().split("==") for line in REQUIREMENTS.read_text().splitlines()
                if line.strip() and not line.startswith("#"))


def verify():
    missing = []
    for name, expected in pins().items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            missing.append(name)
    if missing:
        print("Environment needs packages: " + ", ".join(missing))
        return 1
    from webapp.solver import science_environment
    science_environment()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["start", "setup", "stop", "verify"])
    args = parser.parse_args()
    if args.mode == "stop":
        folder = ROOT / "user_data/web-workbench"
        if folder.exists():
            (folder / "stop.request").write_text("stop", encoding="ascii")
        print("Stop requested. A running calculation will be marked interrupted.")
        return 0
    if sys.version_info[:2] != (3, 14):
        raise SystemExit("Python 3.14 is required.")
    if args.mode == "verify":
        return verify()
    folder = ROOT / ".venv-web"
    python = folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(folder)
    version = subprocess.run([str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                             capture_output=True, text=True, check=True).stdout.strip()
    if version != "3.14":
        raise SystemExit(".venv-web uses another Python version. Rename that folder and run setup again.")
    if subprocess.call([str(python), "-m", "webapp.bootstrap", "verify"], cwd=ROOT):
        subprocess.check_call([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(REQUIREMENTS)], cwd=ROOT)
        subprocess.check_call([str(python), "-m", "pip", "check"], cwd=ROOT)
        subprocess.check_call([str(python), "-m", "webapp.bootstrap", "verify"], cwd=ROOT)
    if args.mode == "start":
        return subprocess.call([str(python), "-m", "webapp.run", "--open-browser"], cwd=ROOT)
    print("Web environment ready. Double-click start_web_windows.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
