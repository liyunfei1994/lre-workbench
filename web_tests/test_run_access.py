"""The launcher must not silently reuse a service with different permissions."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp import run


class RunAccessTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name) / "workspace.sqlite3"

    def invoke(self, *options, existing=None):
        arguments = ["webapp.run", "--database", str(self.database), *options]
        with patch("sys.argv", arguments), patch("webapp.run.urllib.request.build_opener") as opener:
            if existing is not None:
                response = io.StringIO(json.dumps({
                    "service": "lre-workbench-web",
                    "workspace_id": hashlib.sha256(str(self.database.resolve()).encode()).hexdigest(),
                    "public_origin": None, "workspace_access": existing}))
                opener.return_value.open.return_value.__enter__.return_value = response
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                run.main()

    def test_personal_request_does_not_reuse_shared_or_private_service(self):
        for existing in ("shared", "private"):
            with self.subTest(existing=existing), self.assertRaises(SystemExit) as error:
                self.invoke("--personal-workspace", existing=existing)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.database.exists())

    def test_matching_personal_service_is_reused_without_database_mutation(self):
        self.invoke("--personal-workspace", existing="personal")
        self.invoke(existing="personal")
        self.assertFalse(self.database.exists())

    def test_conflicting_flags_are_rejected_before_database_creation(self):
        for options in (("--personal-workspace", "--private-workspace"),
                        ("--personal-workspace", "--public-origin", "https://pilot.example.org")):
            with self.subTest(options=options), self.assertRaises(SystemExit) as error:
                self.invoke(*options)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.database.exists())


if __name__ == "__main__":
    unittest.main()
