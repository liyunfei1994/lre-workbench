"""Task-defined numerical acceptance, independent of initial model evaluations."""
import math


def ffsc_acceptance_policy(inputs, relative_thrust_tolerance=1e-6):
    """Record fixed N/W tolerances; these are not component engineering limits."""
    thrust = float(inputs.thrust)
    relative = float(relative_thrust_tolerance)
    reference = max(abs(thrust), 100.)
    values = [relative * reference, inputs.steady_ox_power_tolerance_W,
              inputs.steady_fuel_power_tolerance_W]
    if (not math.isfinite(thrust) or not math.isfinite(relative) or relative <= 0
            or any(isinstance(v, bool) or not math.isfinite(float(v)) or v <= 0 for v in values)):
        raise ValueError("稳态数值验收容差必须为有限正数。")
    equations = [dict(name=name, label=label, unit=unit, absolute_tolerance=float(value), source=source)
        for name, label, unit, value, source in zip(
            ("thrust", "ox_power", "fuel_power"),
            ("推力平衡", "富氧侧轴功率平衡", "富燃侧轴功率平衡"), ("N", "W", "W"), values,
            ("relative_thrust_tolerance * max(abs(task_thrust_N), 100 N)",
             "steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"))]
    return dict(schema_version=1, id="ffsc_task_absolute_residuals_v1", task_thrust_N=thrust,
                thrust_reference_N=reference, relative_thrust_tolerance=relative, equations=equations)
