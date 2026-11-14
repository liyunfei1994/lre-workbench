"""Built-in design-point controls shared by execution and its read-only UI.

These constants retain the existing numerical policy. They are not additional
case inputs, and must never be substituted into historical calculation records.
"""

THRUST_RELATIVE_TOLERANCE = 1e-6
INNER_MAX_ITERATIONS = 50
PREBURNER_MAX_ITERATIONS = 12
PREBURNER_TEMPERATURE_TOLERANCE_K = 3.0
PREBURNER_RELAXATION = 0.7
THERMAL_MAX_ITERATIONS = 20
THERMAL_TEMPERATURE_TOLERANCE_K = 5.0
THERMAL_PROPERTY_RELATIVE_TOLERANCE = 1e-4
COUPLING_MAX_ITERATIONS = 40
COUPLING_CRITERIA = (
    ("pressure_residual_Pa", .5, "Pa"),
    ("hot_side_temperature_residual_K", .001, "K"),
    ("geometry_relative_residual", 2e-8, "1"),
    ("heat_relative_residual", 2e-8, "1"),
    ("hot_side_relative_residual", 5e-7, "1"),
    ("cell_heat_relative_residual", 5e-7, "1"),
)


def design_solver_controls():
    """Describe current defaults, not settings of a selected historical run."""
    labels = ("冷却压降反馈", "热侧温度反馈", "几何反馈", "总吸热量反馈",
              "热侧沿程状态反馈", "分段热量反馈")
    return {
        "scope": "concept_design", "thrust_relative_tolerance": THRUST_RELATIVE_TOLERANCE,
        "inner_max_iterations": INNER_MAX_ITERATIONS,
        "preburner_temperature_tolerance_K": PREBURNER_TEMPERATURE_TOLERANCE_K,
        "preburner_max_iterations": PREBURNER_MAX_ITERATIONS,
        "preburner_relaxation": PREBURNER_RELAXATION,
        "thermal_max_iterations": THERMAL_MAX_ITERATIONS,
        "thermal_temperature_tolerance_K": THERMAL_TEMPERATURE_TOLERANCE_K,
        "thermal_property_relative_tolerance": THERMAL_PROPERTY_RELATIVE_TOLERANCE,
        "coupling_max_iterations": COUPLING_MAX_ITERATIONS,
        "coupling_criteria": [dict(key=key, label=label, tolerance=value, unit=unit)
                              for (key, value, unit), label in zip(COUPLING_CRITERIA, labels)],
    }
