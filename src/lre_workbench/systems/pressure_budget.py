"""Design pressure-loss budgets for the four FFSC preburner feed branches.

None means inherit the historical common budget. Explicit branch values are
independent design inputs; these are not frozen component characteristics.
"""
from collections.abc import Mapping
import math


BRANCH_BUDGET_FIELDS = {
    branch: {kind: f"{branch}_{kind}_dp_ratio" for kind in ("line", "injector")}
    for branch in ("fuel_to_opb", "fuel_to_fpb", "ox_to_opb", "ox_to_fpb")
}
COMMON_BUDGET_FIELDS = {"line": "valve_line_dp_ratio", "injector": "preburner_injector_dp_ratio"}


def resolve_branch_pressure_budgets(inputs):
    """Return effective ratios and their input keys without mutating inputs.

    The numeric ranges are input-domain limits retained from the common
    budgets, not evidence of injector stability or hardware capability.
    """
    read = inputs.get if isinstance(inputs, Mapping) else lambda key, default=None: getattr(inputs, key, default)
    result = {}
    for branch, fields in BRANCH_BUDGET_FIELDS.items():
        row = {}
        for kind, key in fields.items():
            value = read(key)
            source = key
            if value is None:
                source = COMMON_BUDGET_FIELDS[kind]
                value = read(source)
            lower, upper = (0., .20) if kind == "line" else (.03, .30)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not lower <= value <= upper):
                raise ValueError(f"{source}: 压降比必须为 {lower:g} 至 {upper:g} 范围内的有限数值。")
            row[f"{kind}_dp_ratio"] = float(value)
            row[f"{kind}_source"] = source
        result[branch] = row
    return result


def network_branch_budgets(inputs, chamber_pressure):
    """Convert ratios to the existing SI network interface."""
    return {branch: dict(injector_ratio=row["injector_dp_ratio"],
                         line_dp_Pa=row["line_dp_ratio"] * chamber_pressure,
                         line_dp_ratio=row["line_dp_ratio"],
                         reference_chamber_pressure_Pa=chamber_pressure,
                         line_source=row["line_source"], injector_source=row["injector_source"])
            for branch, row in resolve_branch_pressure_budgets(inputs).items()}
