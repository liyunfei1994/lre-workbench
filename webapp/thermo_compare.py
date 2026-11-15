"""Compatibility alias for the shared thermochemical comparison service.

The implementation belongs to the calculation package, not the HTTP layer.
The alias preserves module identity and existing API/test patch targets.
"""
import sys
from lre_workbench.properties import comparison as _implementation

sys.modules[__name__] = _implementation
