"""Draft recovery survives interrupted writes and keeps open windows isolated."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from case_recovery import RecoveryStore


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def store(self):
        store = RecoveryStore(self.directory)
        self.addCleanup(store.close)
        return store

    def test_atomic_roundtrip_and_failed_replace_keeps_previous_draft(self):
        store = self.store()
        original = {"raw_inputs": {"p_c": "30"}, "saved_path": "方案.json"}
        store.save(original)
        self.assertEqual(store.read(), original)
        with patch("case_recovery.os.replace", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                store.save({"raw_inputs": {"p_c": "40"}})
        self.assertEqual(store.read(), original)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.assertEqual(list(self.directory.glob(".*.tmp")), [])
        store.save({"raw_inputs": {"p_c": "40"}})
        self.assertEqual(store.read()["raw_inputs"]["p_c"], "40")

    def test_invalid_numeric_text_and_saved_baseline_are_preserved(self):
        store = self.store()
        snapshot = {
            "raw_inputs": {"p_c": "-", "mr": "nan", "thrust": "1e", "note": "中文\n草稿"},
            "extra_inputs": {"custom_boundary": "not yet a number"},
            "saved_path": None,
            "saved_inputs": {"p_c": 30.0, "mr": 3.5},
        }
        store.save(snapshot)
        self.assertEqual(store.read(), snapshot)
        store.close()
        next_window = self.store()
        recovered = next_window.discover()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(next_window.read(recovered[0]), snapshot)
        self.assertEqual(recovered[0].session_id, store.session_id)

    def test_corrupt_json_is_logged_and_retained(self):
        crashed = self.store()
        crashed.save({"raw_inputs": {"p_c": "22"}})
        crashed.close()
        crashed.path.write_text('{"interrupted":', encoding="utf-8")
        current = self.store()
        with self.assertLogs("case_recovery", level="WARNING") as logs:
            self.assertEqual(current.discover(), [])
        self.assertIn("retained for diagnosis", "\n".join(logs.output))
        self.assertTrue(crashed.path.exists())
        self.assertEqual(crashed.path.read_text(encoding="utf-8"), '{"interrupted":')

    def test_clear_removes_only_owned_draft_and_discard_checks_liveness(self):
        first, second = self.store(), self.store()
        first.save({"window": 1})
        second.save({"window": 2})
        self.assertEqual(first.discover(), [])
        self.assertIsNone(first.read(second.path))
        self.assertFalse(first.discard(second.path))
        first.clear()
        first.clear()
        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())
        second.close()
        drafts = first.discover()
        self.assertEqual([draft.path for draft in drafts], [second.path])
        self.assertTrue(first.discard(drafts[0]))
        self.assertFalse(second.path.exists())

    def test_another_process_stays_hidden_until_it_exits(self):
        code = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
from case_recovery import RecoveryStore
store = RecoveryStore(sys.argv[2])
store.save({'raw_inputs': {'p_c': 'partial edit'}})
print(store.path, flush=True)
sys.stdin.readline()
# No explicit close: OS releases the lock on exit, as after a crash.
'''
        process = subprocess.Popen([sys.executable, "-u", "-c", code, str(ROOT), str(self.directory)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   env=dict(os.environ, PYTHONUTF8="1"))
        def stop_process():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(stop_process)
        path = Path(process.stdout.readline().strip())
        self.assertTrue(path.exists(), "Child did not create its recovery draft")
        current = self.store()
        self.assertEqual(current.discover(), [])
        self.assertIsNone(current.read(path))
        self.assertFalse(current.discard(path))
        process.kill()
        process.communicate(timeout=10)
        drafts = current.discover()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].snapshot["raw_inputs"]["p_c"], "partial edit")

    def test_corrupt_envelope_and_external_paths_cannot_remove_other_files(self):
        crashed = self.store()
        crashed.save({"raw_inputs": {}})
        crashed.close()
        envelope = json.loads(crashed.path.read_text(encoding="utf-8"))
        envelope["updated_at"] = "2026-10-12T09:00:00"
        crashed.path.write_text(json.dumps(envelope), encoding="utf-8")
        current = self.store()
        with self.assertLogs("case_recovery", level="WARNING"):
            self.assertEqual(current.discover(), [])
        unrelated = self.directory / "user_notes.json"
        unrelated.write_text("do not remove", encoding="utf-8")
        with self.assertRaises(ValueError):
            current.discard(unrelated)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "do not remove")
        with self.assertRaises(ValueError):
            current.read(self.directory.parent / crashed.path.name)


if __name__ == "__main__":
    unittest.main()
