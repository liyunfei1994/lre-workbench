"""Explicit synthetic converged evidence for adapter tests, never production."""


def converged_evidence(residual=1e-5):
    layers = {}
    for name, r, tolerance, unit in (
        ("inner", residual, 1e-4, "1"), ("thermal", .5, 5., "K"),
        ("preburner", .2, 3., "K"), ("continuation", 0., 1e-12, "1"),
    ):
        layers[name] = dict(enabled=True, status="pass", residual=r, tolerance=tolerance,
                            unit=unit, iterations=2, termination_reason="converged")
    layers["continuation"].update(reached_ratio=1.0, termination_reason="target_reached")
    layers["thermal"]["checks"] = [
        dict(key="temperature", residual=.5, tolerance=5.),
        dict(key="gamma_relative_residual", residual=1e-6, tolerance=1e-4),
        dict(key="molar_mass_relative_residual", residual=1e-6, tolerance=1e-4),
    ]
    return dict(schema_version=1, status="pass", layers=layers,
                inner_runs=[], thermal_history=[], preburner_history=[])
