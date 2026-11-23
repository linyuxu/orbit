"""One version: orbit/__init__.py, read by pyproject (dynamic), the app and the page; the CHANGELOG has its section."""

import json
import re
import unittest

from orbit import API_VERSION, ROOT, __version__


class VersionTest(unittest.TestCase):
    def test_one_version_everywhere(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertIn('dynamic = ["version"]', (ROOT / "pyproject.toml").read_text())
        self.assertEqual(json.loads((ROOT / "app" / "tauri.conf.json").read_text())["version"], __version__)
        cargo = re.search(r'^version = "([^"]+)"', (ROOT / "app" / "Cargo.toml").read_text(), re.M)
        self.assertEqual(cargo and cargo.group(1), __version__)
        self.assertIn(f"## {__version__}", (ROOT / "CHANGELOG.md").read_text())
        self.assertEqual(len(API_VERSION), 2)


if __name__ == "__main__":
    unittest.main()
