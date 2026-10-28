# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``headless_page``: the browser GUI scenarios open a page in never shows a window or touches the person's profile."""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import headless_page  # noqa: E402


class FindTests(unittest.TestCase):
    def test_the_environment_names_the_browser_or_turns_it_off(self):
        self.assertEqual("/opt/chrome", headless_page.find({"ANGELO_QA_BROWSER": "/opt/chrome"}, "darwin",
                                                           exists=lambda path: False, which=lambda name: None))
        self.assertIsNone(headless_page.find({"ANGELO_QA_BROWSER": "None"}, "darwin",
                                             exists=lambda path: True, which=lambda name: "/usr/bin/x"))

    def test_the_usual_install_locations_then_the_path(self):
        chrome = headless_page.CANDIDATES["darwin"][0]
        self.assertEqual(chrome, headless_page.find({}, "darwin", exists=lambda path: path == chrome,
                                                    which=lambda name: None))
        self.assertEqual("/usr/bin/chromium", headless_page.find(
            {}, "linux", exists=lambda path: False,
            which=lambda name: "/usr/bin/chromium" if name == "chromium" else None))
        self.assertIsNone(headless_page.find({}, "linux", exists=lambda path: False, which=lambda name: None))


class CommandTests(unittest.TestCase):
    def test_headless_with_its_own_profile_and_the_url_alone(self):
        line = headless_page.command("chrome", "http://127.0.0.1:1/token/", "/tmp/profile")
        self.assertEqual("chrome", line[0])
        self.assertIn("--headless=new", line)
        self.assertIn("--user-data-dir=/tmp/profile", line)
        self.assertIn("--use-mock-keychain", line)   # No macOS keychain prompt from a fresh profile.
        self.assertEqual("http://127.0.0.1:1/token/", line[-1])
        self.assertEqual(1, sum(1 for argument in line if argument.startswith("http")))
        self.assertFalse([argument for argument in line if argument.startswith("--remote-debugging")])

    def test_no_browser_opens_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            page = headless_page.Page(Path(folder, "browser.log"), executable=None)
            page.executable = None
            self.assertFalse(page.open("http://127.0.0.1:1/"))
            page.close()
            self.assertIsNone(page.process)


if __name__ == "__main__":
    unittest.main()
