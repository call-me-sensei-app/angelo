# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A host test can import the add-on however it is started: suite_isolation puts this checkout's add-on on the path.

Found on 2.0.0's candidate (VM CI 150654e953ad, 2026-11-13): test_native_studio_buttons imported ``angelo_agent``
without putting ``scripts/addons_core`` on sys.path. In the full suite an earlier test had done it, so it passed;
run alone (as every touched file is) it failed with ModuleNotFoundError. Five more files reached the add-on the same
way, through a path some other test or helper happened to set. Every host test imports ``suite_isolation`` first
(pinned by ``test_native_suite_isolation``), so that import now appends the add-on folder: a test's own earlier
``sys.path`` entries still win, and no test depends on what ran before it. Checked in a fresh interpreter started
the way a person runs one file: no PYTHONPATH, the test folder as its script folder.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import host_waits  # noqa: E402

ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"

PROBE = """
import json, sys
sys.path.insert(0, {here!r})
before = list(sys.path)
import suite_isolation
import angelo_agent
print(json.dumps({{"file": angelo_agent.__file__, "added": [p for p in sys.path if p not in before],
                   "kept_first": sys.path[:len(before)] == before}}))
"""


def fresh(code):
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT.parent), env=environment,
                          capture_output=True, text=True, timeout=host_waits.CAP_S)


class AddonPathTests(unittest.TestCase):
    def test_suite_isolation_alone_makes_this_checkouts_add_on_importable(self):
        result = fresh(PROBE.format(here=str(HERE)))
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        seen = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(Path(seen["file"]).resolve(), (ADDON / "__init__.py").resolve())

    def test_the_add_on_folder_is_appended_so_a_tests_own_entries_win(self):
        seen = json.loads(fresh(PROBE.format(here=str(HERE))).stdout.strip().splitlines()[-1])
        self.assertTrue(seen["kept_first"], "suite_isolation must append, never put its folder before a test's own")
        self.assertIn(str(ADDON.parent), seen["added"])


if __name__ == "__main__":
    unittest.main()
