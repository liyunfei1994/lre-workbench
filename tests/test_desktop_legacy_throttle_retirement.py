"""Exercise the retired desktop commands without importing a Qt runtime."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def method(filename, class_name, name, namespace):
    path = ROOT / "src" / "lre_workbench" / "desktop" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class DesktopLegacyThrottleRetirementTests(unittest.TestCase):
    def test_both_commands_preserve_history_and_never_start_or_change_work(self):
        for filename, class_name in (("legacy_window.py", "MainWindow"), ("workbench.py", "WorkbenchWindow")):
            with self.subTest(filename=filename):
                message = Mock()
                legacy_command = method("legacy_window.py", "MainWindow", "run_throttle",
                                        {"QMessageBox": SimpleNamespace(information=message)})
                command = (legacy_command if filename == "legacy_window.py" else
                           method(filename, class_name, "run_throttle", {
                               "legacy": SimpleNamespace(MainWindow=SimpleNamespace(run_throttle=legacy_command))}))
                history = [{"throttle_ratio": .75, "converged": True}]
                partial = list(history)
                worker = object()
                window = SimpleNamespace(
                    last_throttle=history, _partial_throttle=partial, _active_worker=worker,
                    _task_kind="solve", _busy=True,
                    _read_inputs=Mock(side_effect=AssertionError("must not read design inputs")),
                    _set_busy=Mock(side_effect=AssertionError("must not alter active work")),
                    _populate_throttle=Mock(side_effect=AssertionError("must not clear history")),
                    thread_pool=SimpleNamespace(start=Mock(side_effect=AssertionError("must not start a worker"))),
                )
                command(window)
                message.assert_called_once()
                args = message.call_args.args
                self.assertIs(args[0], window)
                self.assertIn("已停用", args[1])
                self.assertIn("工况与运行 → 发动机节流分析", args[2])
                self.assertIn("历史", args[2])
                self.assertIs(window.last_throttle, history)
                self.assertIs(window._partial_throttle, partial)
                self.assertIs(window._active_worker, worker)
                self.assertEqual(window._task_kind, "solve")
                self.assertTrue(window._busy)
                window._read_inputs.assert_not_called()
                window.thread_pool.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
