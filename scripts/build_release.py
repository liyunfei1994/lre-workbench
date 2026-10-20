"""Create a clean Windows source installer package, without local venvs."""
import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from version import APP_FOLDER_NAME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "release" / f"{APP_FOLDER_NAME}-windows.zip"))
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_files = []
    for directory in ["src", "assets", "scripts", "tests"]:
        source_files.extend(path for path in (ROOT / directory).rglob("*")
                            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    source_files.extend(ROOT / name for name in ["run_app.py", "requirements.txt", "requirements-science.txt",
                        "start_windows.bat", "start_windows.vbs", "setup_windows.bat", "README.md", "CHANGELOG.md"])
    source_files.extend((ROOT / "docs").glob("*.md"))
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_files):
            if not path.exists():
                raise FileNotFoundError(path)
            name = APP_FOLDER_NAME + "/" + path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo.from_file(path, name)
            if path.suffix == ".sh":
                info.create_system = 3
                info.external_attr = 0o100755 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    print(destination)


if __name__ == "__main__":
    main()
