# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The recovery barrier's closing reason names the pre-request checkpoint only for a chat Restore takes it for: never
for a worker chat (D10's rule, ``worker_routing.worker_snapshots``), on the lane path or hosted in its worker, and
never when that cannot be told (fail closed). No Blender.
"""

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_runtime_fixtures import program  # noqa: E402
from angelo_agent import recovery_reads, worker_routing  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402

TOOL = "angelo_scene_program"
SNAPSHOT = "pre-request"


def closed_reason(restorable):
    barrier = RecoveryBarrier(restorable=restorable)
    result = barrier.refused(TOOL, program(), {"error": "internal", "error_code": "INTERNAL_ERROR",
                                               "partial_changes_possible": True})
    description = result.get("recovery") or {}
    assert description.get("holding") is True, description
    return description["reason"]


def raising():
    raise RuntimeError("unknown")


class ClosedReason(unittest.TestCase):
    def test_a_chat_restore_takes_is_told_its_checkpoint_can_be_restored(self):
        for restorable in (None, lambda: True):
            with self.subTest(restorable=restorable):
                self.assertIn(SNAPSHOT, closed_reason(restorable))

    def test_a_worker_chat_or_an_unknown_one_is_never_sent_to_a_snapshot(self):
        for restorable in (lambda: False, raising, lambda: None):
            with self.subTest(restorable=restorable):
                reason = closed_reason(restorable)
                self.assertNotIn(SNAPSHOT, reason)
                self.assertIn("cannot be restored", reason)
                self.assertIn("read tools stay available", reason)


class ChatsOwnPredicate(unittest.TestCase):
    """``recovery_reads.restorable`` is Restore's own test of the chat (the window's chats against worker chats)."""

    def chat(self, snapshots):
        session = str(uuid.uuid4())
        return SimpleNamespace(state={"id": session}, checkpoints=SimpleNamespace(
            session_id=worker_routing.worker_snapshot_session(session) if snapshots else session))

    def test_a_window_chat_keeps_the_advice_and_a_worker_chat_never_gets_it(self):
        window, worker = self.chat(False), self.chat(True)
        self.assertFalse(worker_routing.worker_snapshots(window))
        self.assertTrue(worker_routing.worker_snapshots(worker))
        self.assertIn(SNAPSHOT, closed_reason(recovery_reads.restorable(window)))
        self.assertNotIn(SNAPSHOT, closed_reason(recovery_reads.restorable(worker)))

    def test_the_hosted_chat_is_one_restore_refuses(self):
        mirror = SimpleNamespace(state={"id": "m"}, checkpoints=None, hosted_in_worker=True)
        self.assertNotIn(SNAPSHOT, closed_reason(recovery_reads.restorable(mirror)))

    def test_every_chat_gets_its_own_predicate(self):
        source = (Path(recovery_reads.__file__).parent / "controller.py").read_text(encoding="utf-8")
        self.assertIn("RecoveryBarrier(recovery_reads.namer(self), recovery_reads.restorable(self))", source)


if __name__ == "__main__":
    unittest.main()
