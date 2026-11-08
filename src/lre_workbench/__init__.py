"""Shared engine models and application services.

``src`` is a source search root, not an alternative package namespace.
Import concrete modules explicitly to keep scientific and UI dependencies lazy.
"""
if __name__ != "lre_workbench":
    raise ImportError("Use 'lre_workbench', not 'src.lre_workbench'; add the src directory to the Python search path.")
