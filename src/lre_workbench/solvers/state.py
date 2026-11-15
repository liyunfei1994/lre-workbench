"""Versioned meaning of saved solver vectors; no physical model imports.

Historical anonymous vectors remain readable, but cannot be used as warm
states because their positions do not identify their physical quantities.
"""
import math
from numbers import Real


def ffsc_design_vector_schema(*, target_mixture_ratio):
    """Return the ordered variable identities for this concept parameterization."""
    if not isinstance(target_mixture_ratio, bool):
        raise ValueError("target_mixture_ratio must explicitly be a bool")
    return {
        "version": 1,
        "analysis_mode": "concept_design",
        "solver_family": "ffsc_design",
        "parameterization": "specified_preburner_mixture_ratio" if target_mixture_ratio else "free_crossflow",
        "variable_names": ["total_mass_flow_kg_s"] + (
            ["oxidizer_turbine_pressure_ratio", "fuel_turbine_pressure_ratio"]
            if target_mixture_ratio else
            ["fuel_to_oxidizer_preburner_mass_flow_kg_s", "oxidizer_to_fuel_preburner_mass_flow_kg_s"]),
        "variable_units": ["kg/s", "1", "1"] if target_mixture_ratio else ["kg/s", "kg/s", "kg/s"],
    }


def ffsc_warm_state_compatible(value, *, target_mixture_ratio):
    """Require both declared semantics and finite numeric values, never guess."""
    expected = ffsc_design_vector_schema(target_mixture_ratio=target_mixture_ratio)
    if not isinstance(value, dict):
        return False
    schema = value.get("vector_schema")
    if (not isinstance(schema, dict) or type(schema.get("version")) is not int
            or schema != expected):
        return False
    vector = value.get("x")
    if not isinstance(vector, (list, tuple)) or len(vector) != len(expected["variable_names"]):
        return False
    quantities = list(vector) + [value.get(name) for name in
                                ("chamber_temp_guess", "chamber_gamma", "molar_mass")]
    try:
        return (all(not isinstance(item, bool) and isinstance(item, Real)
                    and math.isfinite(float(item)) and item > 0 for item in quantities)
                and value["chamber_gamma"] > 1)
    except (OverflowError, TypeError, ValueError):
        return False
