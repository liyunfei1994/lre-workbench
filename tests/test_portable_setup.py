"""Regression checks for copied environments, real launchers, and clean releases."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import bootstrap


class PortableSetupTests(unittest.TestCase):
    def test_interpreter_policy_rejects_old_or_incompatible_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python.exe"
            executable.write_bytes(b"MZ")
            for version, bits, free_threaded, accepted in [
                ([3, 14], 64, False, True),
                ([3, 11], 64, False, False),
                ([3, 12], 64, False, False),
                ([3, 15], 64, False, False),
                ([3, 14], 32, False, False),
                ([3, 14], 64, True, False),
            ]:
                with self.subTest(version=version, bits=bits, free_threaded=free_threaded):
                    result = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                        "prefix": directory, "base": "base",
                        "version": version, "bits": bits, "free_threaded": free_threaded,
                    }))
                    with patch.object(bootstrap.subprocess, "run", return_value=result):
                        self.assertEqual(bootstrap.probe_interpreter(executable), accepted)

    def test_dead_executable_is_not_a_usable_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            dead = Path(directory) / "python.exe"
            dead.write_bytes(b"not an executable")
            self.assertFalse(bootstrap.probe_interpreter(dead))

    def test_base_python_cannot_masquerade_as_project_venv(self):
        self.assertFalse(bootstrap.probe_interpreter(
            Path(sys.executable), Path(tempfile.gettempdir()) / "unrelated-venv"))

    def test_copied_environment_is_preserved_and_rebuilt(self):
        with tempfile.TemporaryDirectory(prefix="工作台 portable ") as directory:
            root = Path(directory)
            old = root / ".venv"
            old.mkdir()
            (old / "pyvenv.cfg").write_text("home = X:\\missing-python\n")
            (old / "keep.txt").write_text("do not lose this", encoding="utf-8")
            real_builder = venv.EnvBuilder(with_pip=False)
            with patch.object(bootstrap.venv, "EnvBuilder", return_value=real_builder):
                python = bootstrap.prepare_environment(old, root)
            self.assertTrue(bootstrap.probe_interpreter(python, old))
            backups = list(root.glob(".venv.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "keep.txt").read_text(), "do not lose this")
            with patch.object(bootstrap.venv, "EnvBuilder") as builder:
                self.assertEqual(bootstrap.prepare_environment(old, root), python)
            builder.assert_not_called()

    def test_failed_rebuild_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / ".venv"
            old.mkdir()
            (old / "keep.txt").write_text("preserved")
            with patch.object(bootstrap.venv.EnvBuilder, "create", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    bootstrap.prepare_environment(old, root)
            self.assertEqual(next(root.glob(".venv.backup-*/keep.txt")).read_text(), "preserved")

    def test_refuses_to_move_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other = root / "important-data"
            other.mkdir()
            with self.assertRaises(RuntimeError):
                bootstrap.prepare_environment(other, root)
            self.assertTrue(other.exists())

    def test_installation_lock_prevents_overlapping_repairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with bootstrap.setup_lock(root):
                with self.assertRaises(RuntimeError):
                    with bootstrap.setup_lock(root):
                        self.fail("second repair acquired the lock")
            with bootstrap.setup_lock(root):
                pass

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_batch_entry_point_from_another_working_directory(self):
        env = dict(os.environ, LRE_NO_PAUSE="1", LRE_PYTHON=sys.executable, PYTHONUTF8="1")
        # Use a workspace temp folder: Windows sandbox accounts can have
        # different access to the user's short-name system temp directory.
        (ROOT / ".cache").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="启动 other ", dir=ROOT / ".cache") as directory:
            # An explicit interpreter prevents this test from downloading Python.
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", str(ROOT / "diagnose_windows.bat")],
                cwd=directory, env=env, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace")
                         + result.stderr.decode("utf-8", errors="replace"))

    def test_release_contains_reproducible_inputs_but_no_machine_state(self):
        from build_release import build_release
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.zip"
            build_release(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                files = {"/".join(name.split("/")[1:]) for name in archive.namelist()}
                self.assertTrue({"constraints.txt", ".python-version", "diagnose_windows.bat",
                                 "scripts/launch_windows.ps1", "CONTRIBUTING.md",
                                 ".github/workflows/validate.yml", "SOURCE_MANIFEST.json"} <= files)
                self.assertTrue({"webapp/app.py", "webapp/static/app.js", "requirements-web.txt",
                                 "start_web_windows.bat", "stop_web_windows.bat", "docs/WEB_LOCAL.md"} <= files)
                self.assertFalse(any(".venv" in name or name.endswith(".lnk")
                                     or name.startswith((".runtime/", "diagnostics/", ".git/", "user_data/", ".cache/"))
                                     for name in files))
                manifest_name = next(name for name in archive.namelist()
                                     if name.endswith("/SOURCE_MANIFEST.json"))
                manifest = json.loads(archive.read(manifest_name))
                import hashlib
                for relative, digest in manifest["sha256"].items():
                    prefix = manifest_name.rsplit("/", 1)[0]
                    self.assertEqual(hashlib.sha256(archive.read(f"{prefix}/{relative}")).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
