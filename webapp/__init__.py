"""Web adapter for the shared :mod:`lre_workbench` calculation package.

Source releases can run without installing the package. Keep this single
source-layout bootstrap at the application boundary; domain modules never
modify the Python search path.
"""
from pathlib import Path
import sys

_source_root = str(Path(__file__).resolve().parents[1] / "src")
if _source_root not in sys.path:
    sys.path.insert(0, _source_root)
