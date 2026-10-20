"""Create the project-local Windows launcher using Unicode Shell interfaces."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from version import APP_NAME, APP_VERSION_LABEL
from windows_identity import create_workbench_shortcut


if __name__ == "__main__":
    path = create_workbench_shortcut(ROOT / f"{APP_NAME} {APP_VERSION_LABEL}.lnk", ROOT)
    print(f"Created: {path}")
