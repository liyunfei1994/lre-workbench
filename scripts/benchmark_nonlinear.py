"""Repeatable, model-independent comparison of the steady algebraic solvers.

These analytic systems test numerical behavior, not an engine or component map.
Every method receives identical equations, starting points, bounds and physical
acceptance limits. Failed and rejected model calls are included in nfev.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter

import numpy as np
import scipy
from scipy.optimize import least_squares  # Load before timing any method.

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lre_workbench.solvers.nonlinear import solve_nonlinear


@dataclass
class Case:
    name: str
    fun: object
    seeds: list
    lower: list
    upper: list
    variable_scales: list
    residual_scales: list
    expected_root: bool = True


def cases():
    count = 12
    def coupled(x):
        a = np.asarray(x)
        return (a * a - 1 + .05 * (np.roll(a, 1) - 1)).tolist(), None

    def changed_units(x):
        values, _ = coupled(np.asarray(x) / 1e6)
        return (np.asarray(values) * 1e-3).tolist(), None

    def restricted(x):
        if x[0] <= 0:
            raise ValueError("logarithm component: input must be positive")
        return [np.log(x[0]), x[1] - x[0]], None

    return [
        Case("coupled_12", coupled, [[v] * count for v in (.3, .7, 1.8)],
             [.01] * count, [3] * count, [1] * count, [1] * count),
        Case("coupled_12_changed_units", changed_units,
             [[v * 1e6] * count for v in (.3, .7, 1.8)],
             [1e4] * count, [3e6] * count, [1e6] * count, [1e-3] * count),
        Case("curved_valley", lambda x: ([10 * (x[1] - x[0] ** 2), 1 - x[0]], None),
             [[-1.2, 1], [.5, .8]], [-3, -3], [3, 3], [1, 1], [1, 1]),
        Case("dependent_consistent", lambda x: ([x[0] + x[1] - 4, 2 * (x[0] + x[1] - 4)], None),
             [[1, 1]], [-10, -10], [10, 10], [1, 1], [1, 1]),
        Case("restricted_domain", restricted, [[.1, .2], [2.5, 2]],
             [-3, -3], [3, 3], [1, 1], [1, 1]),
        Case("boundary_nonzero_residual", lambda x: ([x[0] - 2], None),
             [[.5]], [0], [1], [1], [1], expected_root=False),
    ]


def benchmark(repeats=3):
    rows = []
    for case in cases():
        for seed_id, seed in enumerate(case.seeds):
            for backend in ("newton", "broyden", "trf"):
                timings, results = [], []
                for _ in range(repeats):
                    started = perf_counter()
                    result = solve_nonlinear(
                        case.fun, seed, case.lower, case.upper,
                        variable_scales=case.variable_scales,
                        residual_scales=case.residual_scales,
                        tol=1e-8, max_iter=100, max_nfev=4000, backend=backend)
                    timings.append(perf_counter() - started)
                    results.append(result)
                result = results[-1]
                physical_tolerances = (np.asarray(case.residual_scales) * 1e-8).tolist()
                independently_passed = bool(result.residuals) and all(
                    abs(value) <= limit for value, limit in zip(result.residuals, physical_tolerances))
                if independently_passed != result.passed:
                    raise AssertionError("solver acceptance disagrees with independent residual audit")
                rows.append({
                    "case": case.name, "seed_id": seed_id, "initial_guess": seed,
                    "expected_root": case.expected_root, "backend": backend,
                    "passed": result.passed, "reason": result.reason,
                    "repeat_outcomes_consistent": len({(r.passed, r.reason, r.nfev) for r in results}) == 1,
                    "elapsed_seconds_median": median(timings), "elapsed_seconds_samples": timings,
                    "nfev": result.nfev, "njev": result.njev,
                    "iterations": result.iterations, "rejected_steps": result.rejected_steps,
                    "raw_residuals": result.residuals, "physical_tolerances": physical_tolerances,
                    "scaled_max_residual": max((abs(r / s) for r, s in zip(result.residuals, case.residual_scales)), default=None),
                    "result": asdict(result),
                })
    summary = []
    for backend in ("newton", "broyden", "trf"):
        selected = [r for r in rows if r["backend"] == backend]
        feasible = [r for r in selected if r["expected_root"]]
        summary.append({
            "backend": backend, "solvable_cases": len(feasible),
            "solvable_passed": sum(r["passed"] for r in feasible),
            "false_passes_on_negative_cases": sum(r["passed"] for r in selected if not r["expected_root"]),
            "total_model_calls": sum(r["nfev"] for r in selected),
            "total_median_seconds": sum(r["elapsed_seconds_median"] for r in selected),
        })
    return {"schema_version": 1, "scope": "analytic_numerical_benchmark_not_engine_validation",
            "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
            "repeats": repeats, "tolerance": 1e-8, "max_iterations": 100,
            "max_model_calls": 4000, "summary": summary, "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "diagnostics" / "nonlinear_benchmark.json")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    report = benchmark(args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for row in report["summary"]:
        print(f'{row["backend"]:8} success={row["solvable_passed"]}/{row["solvable_cases"]} '
              f'false_pass={row["false_passes_on_negative_cases"]} '
              f'model_calls={row["total_model_calls"]} time={row["total_median_seconds"]:.6f}s')
    print("Analytic numerical cases only; no engine or map validation.")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
