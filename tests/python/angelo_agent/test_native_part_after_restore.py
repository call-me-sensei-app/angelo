# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A part built before the lead's file changed (a Restore, an Undo restore, the file opened again) is not merged,
unless the scene the person chose holds a merge of it.

Failing on main: a worker that finished and was released is held by nothing, so ``worker_ending.reloaded`` recorded
nothing about it and ``worker_merge.part_refusal`` admitted its part after a Restore to a state that never held it,
contrary to the maintainer's 2026-10-29 rule (no part built before the change merges). Kept: the documented
``again`` workflow (parallel_sessions.refuse_merged_again; agent_integration.rst): after a Restore to a state that
holds the part's merge record, ``again`` places it anew (the maintainer's choice (a), 2026-11-05). A part started
after the change has its own file and merges as usual. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import parallel_sessions, worker_ending, worker_merge, worker_resume, worker_sessions  # noqa: E402


class PartAfterRestoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.lead = SimpleNamespace(state={"id": "lead-1"})
        self.before = Path(self.folder.name) / "worker-2.blend"
        self.after = Path(self.folder.name) / "worker-3.blend"
        for path in (self.before, self.after):
            path.write_bytes(b"BLENDER")
        for target, value in ((worker_sessions, "_started"), (worker_sessions, "_parts"), (worker_ending, "_predating")):
            patcher = mock.patch.object(target, value, {}, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        worker_sessions._started["lead-1"] = ["w2"]
        worker_sessions._parts["w2"] = (2, "Chair", self.before)
        for target, name, value in ((worker_resume, "cancel", lambda lead: None),
                                    (worker_ending, "_holding", lambda: []),   # Finished and released: held by nothing.
                                    (worker_merge, "part_object_count", lambda path: 3)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def refusal(self, path, number, in_scene, again=False):
        with mock.patch.object(parallel_sessions, "part_in_file", lambda part, lead: in_scene):
            return worker_merge.part_refusal(number, None, str(path), self.lead, again=again)

    def test_a_part_built_before_a_restore_that_its_scene_does_not_hold_is_refused(self):
        self.assertIsNone(self.refusal(self.before, 2, False))   # Before the change: it merges.
        worker_ending.reloaded("lead-1")   # A Restore to a state that never held its merge.
        refused = self.refusal(self.before, 2, False)
        self.assertIsNotNone(refused)
        self.assertEqual(refused.code, "VALIDATION")
        self.assertIn("built before this chat's file changed", str(refused))
        self.assertIsNotNone(self.refusal(self.before, 2, False, again=True))   # ``again`` never bypasses it.

    def test_a_part_the_restored_scene_holds_a_merge_of_is_placed_again_as_documented(self):
        worker_ending.reloaded("lead-1")   # A Restore to a snapshot taken after the merge: its record is there.
        self.assertIsNone(self.refusal(self.before, 2, True, again=True))
        merged_before = self.refusal(self.before, 2, True)   # Without ``again``: the merged-before refusal, as ever.
        self.assertIn("merged into this scene before", str(merged_before))

    def test_a_part_started_after_the_change_merges(self):
        worker_ending.reloaded("lead-1")
        worker_sessions._started["lead-1"].append("w3")
        worker_sessions._parts["w3"] = (3, "Table", self.after)
        self.assertIsNone(self.refusal(self.after, 3, False))

    def test_another_chats_restore_changes_nothing_here(self):
        worker_ending.reloaded("lead-2")
        self.assertIsNone(self.refusal(self.before, 2, False))


if __name__ == "__main__":
    unittest.main()
