"""Record and verify the libraries which determine the calculation path.

The report is JSON serializable. It records imports as well as distribution
versions: an installed but broken binary library must not count as available.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys


DEPENDENCIES = {
    "PySide6": ("PySide6", "6.8.3", "interface"),
    "numpy": ("numpy", "2.2.6", "interface"),
    "matplotlib": ("matplotlib", "3.10.3", "interface"),
    "cantera": ("cantera", "3.1.0", "science"),
    "CoolProp": ("CoolProp", "7.1.0", "science"),
    "scikit-fem": ("skfem", "11.0.0", "science"),
    "scipy": ("scipy", "1.15.3", "science"),
}

# The basic installer must not attempt to repair unrelated science packages.
BASIC_LOCK_PACKAGES = {
    "pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6",
    "numpy", "matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver",
    "packaging", "pillow", "pyparsing", "python-dateutil", "six",
}


def requirements_hashes(root: Path | str | None = None) -> dict[str, str]:
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("requirements.txt", "requirements-science.txt", "constraints.txt")
            if (root / name).is_file()}


def _expected_versions(root: Path) -> dict[str, str]:
    expected = {name.lower(): spec[1] for name, spec in DEPENDENCIES.items()}
    for filename in ("requirements.txt", "requirements-science.txt"):
        path = root / filename
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" in line:
                name, version = line.split("==", 1)
                expected[name.strip().lower()] = version.strip()
    return expected


def environment_fingerprint(report: dict) -> str:
    """A stable identity independent of installation directory and capture time."""
    payload = {key: report[key] for key in
               ("mode", "python", "platform", "dependencies", "requirements_sha256", "functional_checks")}
    # Import errors may contain machine-specific paths; keep only availability
    # and exact library versions in the identity.
    payload["dependencies"] = {
        name: {key: entry.get(key) for key in
               ("available", "version", "expected_version", "matches_expected")}
        for name, entry in report["dependencies"].items()}
    payload["functional_checks"] = {
        name: {key: entry.get(key) for key in ("ready", "mechanism_sha256", "n_species", "n_reactions")}
        for name, entry in report["functional_checks"].items()}
    payload["locked_versions"] = report.get("locked_versions", {})
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def environment_report(root: Path | str | None = None, mode: str = "full") -> dict:
    """Probe the actual interpreter. Full requires chemistry, properties and FEM."""
    if mode not in ("full", "basic"):
        raise ValueError("mode must be 'full' or 'basic'")
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    expected = _expected_versions(root)
    dependencies = {}
    for distribution, (module_name, default_version, group) in DEPENDENCIES.items():
        required = mode == "full" or group == "interface"
        entry = {"module": module_name, "required": required, "available": False,
                 "version": None, "expected_version": expected.get(distribution.lower(), default_version),
                 "matches_expected": False, "error": None}
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", None)
            if version is None:
                version = importlib.metadata.version(distribution)
            entry.update(available=True, version=str(version),
                         matches_expected=str(version) == entry["expected_version"])
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        dependencies[distribution] = entry
    functional_checks = {}
    mechanism = {"ready": False, "error": None, "mechanism": "gri30.yaml", "mechanism_sha256": None}
    try:
        if not dependencies["cantera"]["available"]:
            raise RuntimeError("Cantera 无法导入")
        # Test the exact getter used by the solver. Package import alone misses
        # a broken/missing mechanism and native Windows Unicode path problems.
        thermo = importlib.import_module("thermo")
        gas = thermo._get_gas()
        cantera = importlib.import_module("cantera")
        mechanism_file = Path(cantera.__file__).resolve().parent / "data" / "gri30.yaml"
        mechanism.update(ready=gas.n_species > 0 and gas.n_reactions > 0,
                         n_species=int(gas.n_species), n_reactions=int(gas.n_reactions),
                         mechanism_sha256=hashlib.sha256(mechanism_file.read_bytes()).hexdigest())
    except Exception as exc:
        mechanism["error"] = f"{type(exc).__name__}: {exc}"
    functional_checks["cantera_mechanism"] = mechanism
    real_fluid = {"ready": False, "error": None}
    try:
        if not dependencies["CoolProp"]["available"]:
            raise RuntimeError("CoolProp 无法导入")
        coolprop = importlib.import_module("CoolProp.CoolProp")
        density = float(coolprop.PropsSI("D", "T", 90.0, "P", 1e6, "Oxygen"))
        enthalpy = float(coolprop.PropsSI("H", "T", 110.0, "P", 1e6, "Methane"))
        real_fluid.update(ready=math.isfinite(density) and density > 0 and math.isfinite(enthalpy),
                          oxygen_density_kg_m3=density, methane_enthalpy_J_kg=enthalpy)
    except Exception as exc:
        real_fluid["error"] = f"{type(exc).__name__}: {exc}"
    functional_checks["real_fluid_properties"] = real_fluid
    ready = all(entry["available"] and entry["matches_expected"]
                for entry in dependencies.values() if entry["required"])
    if mode == "full":
        ready = ready and all(entry["ready"] for entry in functional_checks.values())
    locked_versions, lock_problems = {}, []
    lockfile = root / "constraints.txt"
    if lockfile.is_file():
        for line in lockfile.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            distribution, expected_version = line.split("==", 1)
            normalized = distribution.lower().replace("_", "-").replace(".", "-")
            if mode == "basic" and normalized not in BASIC_LOCK_PACKAGES:
                continue
            try:
                actual = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                actual = None
            locked_versions[distribution] = actual
            if actual != expected_version:
                lock_problems.append(f"{distribution}: {actual or '未安装'}，锁定版本 {expected_version}")
        ready = ready and not lock_problems
    report = {
        "schema_version": 1, "mode": mode, "ready": ready,
        "python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                   "architecture": platform.architecture()[0]},
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "executable": sys.executable,
        "dependencies": dependencies,
        "functional_checks": functional_checks,
        "locked_versions": locked_versions,
        "lock_problems": lock_problems,
        "requirements_sha256": requirements_hashes(root),
        "physical_path": {
            "equilibrium_chemistry": ("Cantera" if mechanism["ready"] else
                                      "Cantera 机制未就绪" if dependencies["cantera"]["available"] else "经验公式"),
            "real_fluid_properties": ("CoolProp" if real_fluid["ready"] else
                                      "CoolProp 功能未就绪" if dependencies["CoolProp"]["available"] else "经验物性"),
        },
    }
    report["fingerprint"] = environment_fingerprint(report)
    return report


def environment_problems(report: dict) -> list[str]:
    problems = [f"{name}: " + (entry["error"] or
            f"版本 {entry['version']}，要求 {entry['expected_version']}")
            for name, entry in report["dependencies"].items()
            if entry["required"] and not (entry["available"] and entry["matches_expected"])]
    if report["mode"] == "full":
        problems.extend(f"{name}: {entry['error'] or '功能检查未通过'}"
                        for name, entry in report["functional_checks"].items() if not entry["ready"])
    return problems + report.get("lock_problems", [])
