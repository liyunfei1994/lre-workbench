"""Keep numerical models independent of delivery surfaces and import aliases.

These are dependency/identity contracts, not a snapshot of the directory tree.
New modules may be added freely within the package without updating this suite.
"""
from __future__ import annotations

import ast
from importlib.util import resolve_name
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "lre_workbench"

# The removed flat module names are reserved migration boundaries. Importing
# these names would bypass the package and can create duplicate class/cache state.
LEGACY_IMPORT_ROOTS = frozenset("""
analysis_contract analysis_roles atmosphere auxiliary_worker case_recovery
case_safety cfd_exchange cfd_integration cfd_panel cfd_runner cfturbo_export
chamber chamber_contour chamber_design chamber_sizing chilldown control_trim
convergence cooling cooling_marching core cross_validate_charts cycle
engine_presets engineering_assessment fea_chamber feed_network ffsc_coupled
fluid_properties gas_handoff geometry gg_cycle grcop_properties gui_pyside6
heated_nozzle ignition injector material_spec materials nonlinear_solver nozzle
parameter_contract preburner pressure_budget propellants pump pump_speed_crosscheck
qt_chart_canvas regen_coupling regen_geometry runtime_environment scan_parameters
shutdown_gas shutdown_inventory shutdown_rotor solver_attempt solver_initialization
solver_state steady_acceptance steady_closure steady_iteration steady_network
sweep task_control thermal_structural thermo thermo_applicability thermo_data
topology_drawing transient turbine ui_chamber_design ui_drawings ui_metadata
version wall_materials wall_thermal windows_identity workbench workbench_canvas
workbench_fonts workbench_style
""".split())

COMPUTATION_LAYERS = (
    "domain", "components", "systems", "solvers", "properties", "materials",
    "analysis", "operations", "integrations",
)
DELIVERY_NAMESPACES = ("webapp", "lre_workbench.desktop", "PySide6")


def imports_in(path):
    """Yield concrete import names, including literal dynamic imports."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    package_root = SOURCE if path.is_relative_to(SOURCE) else ROOT
    parent_package = ".".join(path.relative_to(package_root).parent.parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, parent_package)
            yield node.lineno, module
            # ``from lre_workbench import desktop`` also crosses the boundary.
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, module + "." + alias.name
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            dynamic = (
                isinstance(func, ast.Name) and func.id == "__import__"
                or isinstance(func, ast.Attribute) and func.attr in ("import_module", "find_spec")
            )
            value = node.args[0]
            if dynamic and isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield node.lineno, value.value


def production_files():
    for folder in (PACKAGE, ROOT / "webapp", ROOT / "scripts"):
        yield from folder.rglob("*.py")
    yield ROOT / "run_app.py"


class SourceArchitectureTests(unittest.TestCase):
    def run_isolated(self, source, *, cwd=None):
        """Do not let an earlier test's sys.path or sys.modules hide leakage."""
        setup = "import sys\nsys.path[:0] = " + repr([str(SOURCE), str(ROOT)]) + "\n"
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-c", setup + textwrap.dedent(source)],
            cwd=str(cwd or ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def test_source_root_contains_only_package_code(self):
        misplaced = [str(p.relative_to(ROOT)) for p in SOURCE.rglob("*.py")
                     if not p.is_relative_to(PACKAGE)]
        self.assertEqual(misplaced, [], "Keep launch/build helpers outside src and business code in the package.")

    def test_production_imports_use_one_package_namespace(self):
        violations = []
        for path in production_files():
            for line, name in imports_in(path):
                first = name.split(".", 1)[0]
                if first == "src" or first in LEGACY_IMPORT_ROOTS:
                    violations.append(f"{path.relative_to(ROOT)}:{line}: {name}")
        self.assertEqual(violations, [], "Use lre_workbench imports, never flat modules or src.* aliases.")

    def test_computation_does_not_depend_on_delivery_surfaces(self):
        violations = []
        for layer in COMPUTATION_LAYERS:
            for path in (PACKAGE / layer).rglob("*.py"):
                for line, name in imports_in(path):
                    if any(name == prefix or name.startswith(prefix + ".") for prefix in DELIVERY_NAMESPACES):
                        violations.append(f"{path.relative_to(ROOT)}:{line}: {name}")
        self.assertEqual(violations, [], "Move shared calculation into the package; adapters depend on it, not vice versa.")

    def test_components_and_cycle_share_core_class_identity(self):
        report = self.run_isolated("""
            import importlib
            import json
            from lre_workbench.domain import models, analysis_contract
            from lre_workbench.components.pumps import model as pumps
            from lre_workbench.components.turbines import model as turbines
            from lre_workbench.systems import ffsc
            assert pumps.ComponentResult is models.ComponentResult
            assert turbines.FlowState is models.FlowState
            assert ffsc.EngineInputs is models.EngineInputs
            assert analysis_contract.EngineInputs is models.EngineInputs
            rejected = []
            for name in ('src.core', 'src.lre_workbench.domain.models'):
                try:
                    importlib.import_module(name)
                except ImportError:
                    rejected.append(name)
                else:
                    raise AssertionError('Duplicate package identity permitted: ' + name)
            print(json.dumps({'rejected': rejected, 'identity': models.EngineInputs.__module__}))
        """)
        self.assertEqual(report["identity"], "lre_workbench.domain.models")
        self.assertEqual(len(report["rejected"]), 2)

    def test_numerical_imports_do_not_attempt_to_load_ui_or_web(self):
        report = self.run_isolated("""
            import importlib
            import importlib.abc
            import json
            attempts = []
            blocked = ('PySide6', 'lre_workbench.desktop', 'webapp')
            class NoDeliveryImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                        attempts.append(fullname)
                        raise AssertionError('Calculation imported a delivery surface: ' + fullname)
                    return None
            sys.meta_path.insert(0, NoDeliveryImports())
            for module in (
                'lre_workbench.domain.models',
                'lre_workbench.components.pumps.model',
                'lre_workbench.components.turbines.model',
                'lre_workbench.components.chambers.model',
                'lre_workbench.components.nozzles.model',
                'lre_workbench.components.injectors.model',
                'lre_workbench.systems.ffsc',
                'lre_workbench.systems.geometry',
                'lre_workbench.integrations.cfturbo',
            ):
                importlib.import_module(module)
            loaded = [name for name in sys.modules
                      if any(name == prefix or name.startswith(prefix + '.') for prefix in blocked)]
            print(json.dumps({'attempts': attempts, 'loaded': loaded}))
        """)
        self.assertEqual(report, {"attempts": [], "loaded": []})

    def test_resource_anchors_are_independent_of_working_directory(self):
        report = self.run_isolated("""
            import json
            from lre_workbench import resources
            print(json.dumps({
                'project': str(resources.PROJECT_ROOT),
                'font': (resources.ASSET_ROOT / 'fonts/NotoSansSC.ttf').is_file(),
                'chevron': (resources.ASSET_ROOT / 'chevron.svg').is_file(),
                'desktop_icon': (resources.DESKTOP_ASSET_ROOT / 'app_icon.png').is_file(),
            }))
        """, cwd=ROOT / "tests")
        self.assertEqual(Path(report.pop("project")), ROOT)
        self.assertTrue(all(report.values()), report)

    def test_ffsc_factory_does_not_load_historical_cycle(self):
        report = self.run_isolated("""
            import json
            from lre_workbench.application.cycle_factory import make_cycle_model
            from lre_workbench.domain.models import EngineInputs
            from lre_workbench.systems.ffsc import FFSCCycleModel
            model = make_cycle_model(EngineInputs())
            assert isinstance(model, FFSCCycleModel)
            print(json.dumps({'legacy_loaded': 'lre_workbench.legacy.gg_cycle' in sys.modules}))
        """)
        self.assertFalse(report["legacy_loaded"])

    def test_web_comparison_compatibility_preserves_one_service_identity(self):
        report = self.run_isolated("""
            import json
            from lre_workbench.properties import comparison
            from webapp import thermo_compare
            assert thermo_compare is comparison
            assert thermo_compare.compare_thermochemistry is comparison.compare_thermochemistry
            print(json.dumps({'identity': thermo_compare.__name__}))
        """)
        self.assertEqual(report["identity"], "lre_workbench.properties.comparison")


if __name__ == "__main__":
    unittest.main()
