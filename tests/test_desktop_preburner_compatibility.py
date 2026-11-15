"""Execute desktop serialization boundaries without requiring a Qt runtime.

The production methods are extracted unchanged; only widgets and callbacks
outside the input-loading contract are replaced by small stubs.
"""
import ast
from copy import deepcopy
from dataclasses import asdict, fields
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lre_workbench.domain.models import EngineInputs


def desktop_method(filename, class_name, method_name):
    tree = ast.parse((ROOT / "src" / "lre_workbench" / "desktop" / filename).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    namespace = {"Path": Path, "json": json, "math": math, "asdict": asdict,
                 "fields": fields, "EngineInputs": EngineInputs, "deepcopy": deepcopy,
                 "QComboBox": type("UnusedComboBox", (), {})}
    exec(compile(ast.Module(body=[method], type_ignores=[]), filename, "exec"), namespace)
    return namespace[method_name]


class DesktopPreburnerCompatibilityTests(unittest.TestCase):
    def load_document(self, document):
        window = SimpleNamespace(
            cfd_panel=SimpleNamespace(has_running_task=lambda: False),
            _cfd_history_from_document=lambda _: [], input_edits={}, _INT_FIELDS=set(),
            _signature=lambda: (), _remember_saved_case=lambda: None,
            _refresh_group_selector=lambda: None, _refresh_preset_combo=lambda: None,
            _update_case_state=lambda: None,
        )
        load = desktop_method("workbench.py", "WorkbenchWindow", "load_case_path")
        with patch.object(Path, "read_text", return_value=json.dumps(document)):
            load(window, "compatibility-case.json")
        return window._extra_inputs

    def test_recognized_saved_document_retains_missing_legacy_status(self):
        values = {"ox_preburner_temp_limit": 820, "fuel_preburner_temp_limit": 940}
        document = {"format": "ffsc.workbench.case", "schema_version": 1, "inputs": values}
        before = deepcopy(document)
        loaded = self.load_document(document)
        self.assertEqual(document, before)
        for side, limit in (("ox", 820), ("fuel", 940)):
            self.assertEqual(loaded[f"{side}_preburner_temp_limit_status"], "provisional")
            self.assertEqual(loaded[f"{side}_preburner_temp_limit"], limit)

    def test_saved_explicit_unset_and_unidentified_input_patch_keep_new_defaults(self):
        saved = {"format": "ffsc.workbench.case", "schema_version": 1,
                 "inputs": asdict(EngineInputs())}
        for document in (saved, {"inputs": {"pc": 32e6}}):
            with self.subTest(saved="format" in document):
                loaded = self.load_document(document)
                for side in ("ox", "fuel"):
                    self.assertEqual(loaded[f"{side}_preburner_temp_limit_status"], "unset")

    def test_draft_restore_preserves_legacy_and_explicit_states_without_parsing(self):
        restore = desktop_method("case_safety.py", "CaseSafetyMixin", "_restore_case_snapshot")
        for status in (None, "unset", "documented"):
            extra = {}
            if status is not None:
                extra.update(ox_preburner_temp_limit_status=status,
                             fuel_preburner_temp_limit_status=status)
            snapshot = {"raw_inputs": {"pc": "1e", "ox_preburner_temp_limit": "820",
                                       "fuel_preburner_temp_limit": "940"}, "extra_inputs": extra,
                        "analysis_inputs": {}, "saved_inputs": {
                            "raw_inputs": {}, "extra_inputs": {}, "analysis_inputs": {}}}
            before = deepcopy(snapshot)
            window = SimpleNamespace(input_edits={}, _analysis_restore_operations=lambda _: [])
            with self.subTest(status=status):
                restore(window, snapshot)
                self.assertEqual(snapshot, before)
                self.assertTrue(window._case_recovery_forced_dirty)
                for side in ("ox", "fuel"):
                    self.assertEqual(window._extra_inputs[f"{side}_preburner_temp_limit_status"],
                                     status or "provisional")


if __name__ == "__main__":
    unittest.main()
