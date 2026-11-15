"""Repeatable PR chemistry microbenchmark; optional JSON result and CPU profile.

Run with the project interpreter from the repository root. This measures the
reactive HP kernel, not whole-engine convergence or startup/import overhead.
"""
from __future__ import annotations

import argparse
import cProfile
import json
from pathlib import Path
import pstats
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lre_workbench.properties import thermochemistry as thermo
from lre_workbench.properties.datasets import gas_eos_scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    rows = []
    profiler = cProfile.Profile()
    for eos in ("ideal-gas", "Peng-Robinson", "Peng-Robinson-Boston-Mathias"):
        with gas_eos_scope(eos, "pr_supported_v1"):
            thermo._get_gas()  # exclude one-time phase construction
            for mr in (60., .3, 3.5):
                times = []
                thermo.combustion_equilibrium(1., mr, 150., 100., 60e6)
                profiler.enable() if args.profile else None
                for repeat in range(args.repeats):
                    start = time.perf_counter()
                    state = thermo.combustion_equilibrium(1., mr, 150., 100., 60e6)
                    times.append(time.perf_counter() - start)
                profiler.disable()
                row = dict(eos=eos, mixture_ratio=mr, seconds_median=statistics.median(times),
                           seconds=times, temperature_K=state.temperature,
                           enthalpy_J_kg=state.enthalpy, composition=state.composition)
                rows.append(row)
                print(json.dumps({k:v for k,v in row.items() if k != "composition"}), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if args.profile:
        pstats.Stats(profiler).sort_stats("cumtime").print_stats(30)


if __name__ == "__main__":
    main()
