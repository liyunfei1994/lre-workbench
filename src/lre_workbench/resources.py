"""Filesystem anchors for the portable source distribution.

All paths are based on this package, never on the process working directory.
The launcher and release builder preserve ``project/src/lre_workbench`` and
the project-level ``assets`` directory. Desktop-only legacy artwork lives
inside ``desktop/assets``; no UI dependency is imported to locate resources.
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
ASSET_ROOT = PROJECT_ROOT / "assets"
DESKTOP_ASSET_ROOT = PACKAGE_ROOT / "desktop" / "assets"
