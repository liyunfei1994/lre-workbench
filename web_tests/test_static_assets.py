"""Exercise the served entry page and full JS dependency graph, not just disk files."""
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import urljoin

from fastapi.testclient import TestClient
from webapp.app import create_app


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        value = values.get("src") if tag == "script" else values.get("href") if tag == "link" else None
        if value and value.startswith("/static/"):
            self.assets.append(value)


class StaticAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.client = TestClient(create_app(Path(temporary.name) / "workspace.sqlite3", queue_enabled=False),
                                 base_url="http://127.0.0.1:8765")
        self.addCleanup(self.client.close)

    def test_entry_page_and_all_transitive_modules_are_served_with_correct_mime(self):
        entry = self.client.get("/")
        self.assertEqual(entry.status_code, 200)
        refs = References()
        refs.feed(entry.text)
        pending, visited = refs.assets[:], set()
        imports = re.compile(r'''(?:\bfrom\s*|\bimport\s*\(\s*|^\s*import\s*)["']([^"']+\.js)["']''', re.MULTILINE)
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"Missing startup dependency: {path}")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            if path.endswith(".js"):
                self.assertIn("javascript", response.headers["content-type"], path)
                for target in imports.findall(response.text):
                    self.assertTrue(target.startswith(("./", "../", "/static/")), target)
                    pending.append(urljoin(path, target))
            elif path.endswith(".css"):
                self.assertIn("text/css", response.headers["content-type"], path)
        self.assertTrue({"/static/bootstrap.js", "/static/app.js", "/static/results.js",
                         "/static/convergence.js", "/static/convergence.css",
                         "/static/cooling.js", "/static/cooling.css"} <= visited)

    def test_unknown_files_and_traversal_remain_unavailable(self):
        for path in ("/static/not-registered.js", "/static/app.py", "/static/%2E%2E%2Fapp.py",
                     "/static/%2E%2E%2F%2E%2E%2Fuser_data/web-workbench/workspace.sqlite3"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
