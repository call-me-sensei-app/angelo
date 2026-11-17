# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""On a Windows host, ntfs_sharing waits out a refusal it did not cause, as the add-on's retries do. No Blender.

``shared_replace`` retries a refused replace with growing pauses (100 ms in all), because on Windows another program
(a scan, an indexer, a sync client) briefly opens a file just written. ``ntfs_sharing`` records those pauses and does
not sleep them, so a test that holds a file for good never waits. On a real Windows host that also made a moment's
outside hold fatal. On win-4 (VM CI, 9c846224cce) test_native_shared_replace_callers.ChatListTests' retry after
``let_go`` was refused within the five instant tries, while the add-on would have waited the pauses. That was once
in the 360 Windows shard logs kept so far, and never in emulation, where no outside program holds a file.

The rule: on a real Windows host, a pause while the test holds no handle of its own is slept for real. The refusal
is another program's, and the add-on's pauses are what it is for. A pause while the test holds one is recorded and
not slept (the test caused it). Emulation never sleeps.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))   # angelo_agent, which ntfs_sharing patches
import ntfs_sharing  # noqa: E402


class PauseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ntfs-pauses-")
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name, "kept.json")
        self.file.write_text("{}", encoding="utf-8")

    def pause(self, real, hold):
        """What sleeping one 10 ms pause costs, with the host real or emulated and the test holding a file or not."""
        slept = []
        with patch.object(ntfs_sharing, "REAL", real), \
                patch.object(ntfs_sharing.time, "sleep", side_effect=slept.append):
            sharing = ntfs_sharing.Sharing(self)
            if hold:
                sharing.hold(self.file)
            sharing._pause(0.01)
            sharing.let_go_all()
        self.assertEqual(sharing.pauses, [0.01], "every pause is recorded")
        return slept

    def test_on_windows_a_refusal_the_test_did_not_cause_is_waited_out(self):
        self.assertEqual(self.pause(real=True, hold=False), [0.01])

    def test_on_windows_a_refusal_the_test_holds_is_never_waited(self):
        self.assertEqual(self.pause(real=True, hold=True), [])

    def test_emulation_never_waits(self):
        self.assertEqual(self.pause(real=False, hold=False), [])
        self.assertEqual(self.pause(real=False, hold=True), [])


if __name__ == "__main__":
    unittest.main()
