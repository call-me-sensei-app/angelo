# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-app suites that stand in for the person run with C's harness scope available (``native_suite_harness``).

The 2.0.0 Windows release run (build4, 013c161cf3e) failed 42 BACKGROUND suites, and the same suites failed on Linux:
part of it was suites calling ``grant_guard.stand_in()`` or a grant entry such as ``controller.approve_tool`` with no
harness scope. A harness scope exists only in an app started with --enable-event-simulate, and the runner passed that
only to windowed suites. So each suite that uses the harness declares it, the runner passes the flag (BACKGROUND
included), and an undeclared one is refused with the reason, never run to fail far from its cause. Blender is never
launched.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_harness as harness  # noqa: E402
import native_suite_manifest as manifest  # noqa: E402
import run_native_suites as runner  # noqa: E402


def command(suite, window=False):
    return runner.build_command(suite, Path("angelo"), window, Path(tempfile.gettempdir()))


class DeclaredTests(unittest.TestCase):
    def test_every_suite_that_uses_the_harness_declares_it(self):
        undeclared = sorted(suite.script for suite in manifest.SUITES if harness.refusal(suite) is not None)
        self.assertEqual(undeclared, [], "harness=True (or False) in native_suite_manifest")

    def test_a_declared_suite_uses_it(self):
        stale = sorted(suite.script for suite in manifest.SUITES
                       if suite.harness is not None and not harness.uses_harness(suite.script))
        self.assertEqual(stale, [], "declares harness but never uses it")

    def test_a_background_suite_with_the_harness_gets_the_flag_and_others_do_not(self):
        for suite in manifest.SUITES:
            if suite.host_args or suite.needs_window:
                continue
            with self.subTest(suite=suite.script):
                self.assertEqual(harness.FLAG in command(suite), suite.harness is True)

    def test_a_windowed_suite_with_the_harness_gets_the_flag(self):
        for suite in manifest.SUITES:
            if suite.harness and suite.needs_window and not suite.host_args:
                with self.subTest(suite=suite.script):
                    self.assertIn(harness.FLAG, command(suite, window=True))

    def test_the_guard_suite_proves_the_harness_refused_without_the_flag(self):
        suite = next(suite for suite in manifest.SUITES if suite.script == "test_blender_guard_authority.py")
        self.assertIs(suite.harness, False)
        self.assertNotIn(harness.FLAG, command(suite))


class DetectionTests(unittest.TestCase):
    def folder(self, files):
        directory = Path(tempfile.mkdtemp(prefix="suite-harness-"))
        for name, text in files.items():
            (directory / name).write_text(text, encoding="utf-8")
        return directory

    def test_through_a_helper_at_any_depth(self):
        folder = self.folder({"test_x.py": "import helper_a\n", "helper_a.py": "from helper_b import go\n",
                              "helper_b.py": "def go(c):\n    return as_person(c.approve_tool, 1)\n",
                              "test_y.py": "import helper_c\n", "helper_c.py": "x = 1\n"})
        self.assertTrue(harness.uses_harness("test_x.py", folder))
        self.assertFalse(harness.uses_harness("test_y.py", folder))

    def test_an_undeclared_suite_is_refused_with_the_reason(self):
        folder = self.folder({"test_z.py": "from angelo_agent import grant_guard\ngrant_guard.stand_in().approve()\n"})
        suite = manifest.Suite("test_z.py", manifest.BACKGROUND, "a probe")
        self.assertIn(harness.FLAG, harness.refusal(suite, folder))
        self.assertIsNone(harness.refusal(dataclasses.replace(suite, harness=True), folder))


if __name__ == "__main__":
    unittest.main()
