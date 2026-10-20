"""Create a clean Windows source installer package, without local venvs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from version import APP_FOLDER_NAME, APP_VERSION


def build_release(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_files = []
    for directory in ["src", "assets", "scripts", "tests", ".github"]:
        source_files.extend(path for path in (ROOT / directory).rglob("*")
                            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    source_files.extend(ROOT / name for name in ["run_app.py", "requirements.txt", "requirements-science.txt",
                        "constraints.txt", ".python-version", ".gitignore", ".gitattributes",
                        "start_windows.bat", "start_windows.vbs", "setup_windows.bat", "diagnose_windows.bat",
                        "README.md", "CHANGELOG.md", "CONTRIBUTING.md"])
    source_files.extend((ROOT / "docs").glob("*.md"))
    manifest = {"app_version": APP_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "sha256": {}, "commit": None, "working_tree_changed": None}
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        if revision.returncode == 0:
            manifest["commit"] = revision.stdout.strip()
        if status.returncode == 0:
            manifest["working_tree_changed"] = bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_files):
            if not path.exists():
                raise FileNotFoundError(path)
            name = APP_FOLDER_NAME + "/" + path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo.from_file(path, name)
            if path.suffix == ".sh":
                info.create_system = 3
                info.external_attr = 0o100755 << 16
            content = path.read_bytes()
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)
            manifest["sha256"][path.relative_to(ROOT).as_posix()] = hashlib.sha256(content).hexdigest()
        archive.writestr(APP_FOLDER_NAME + "/SOURCE_MANIFEST.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "release" / f"{APP_FOLDER_NAME}-team-source.zip"))
    args = parser.parse_args()
    destination = build_release(Path(args.output))
    print(destination)


if __name__ == "__main__":
    main()
