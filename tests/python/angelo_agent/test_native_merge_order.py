# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""With A′ a chat's merge and its edits keep the order they were asked in (``merge_hold.deferred``).

A′'s merge gate asks its holds and leases without waiting, so a merge returns WAITING at its first execution. An edit
the chat asked for after it could then bind and hold the merge (``merge_hold.blocker``): in Ask the worker-done event's
merge, which needs no card, waited on that later edit's card (R7, parallel_resume@hosted, 7 of 8 runs). Pinned here
through the real Controller pump with the gate's two phases stood in: the later edit stays queued, unbound and without
a card, until the merge has run or been refused, then binds against the merged scene; a merge still waits for an edit
asked before it, also one that itself waits behind an earlier merge; the lanes keep their order. No Blender.
"""

import unittest
from unittest.mock import patch

from test_native_merge_hold import MERGE, SCRIPT, MergeHoldFixture

MOVE = ("angelo_set_transform", {"object": "Cube", "location": [1, 2, 3]})


class Gate:
    """The gate's two phases: ``waits`` True while its answers are out (``open``), the merge's run refused once
    ``refuse`` is set (``guard``)."""

    def __init__(self):
        self.open, self.refuse = True, None

    def waits(self, controller, call):
        return call.name == MERGE and self.open

    def asking(self, controller, call):
        return call.name == MERGE and self.open


class Fixture(MergeHoldFixture):
    hosted = True

    def setUp(self):
        super().setUp()
        self.gate = Gate()
        for patcher in (patch("angelo_agent.worker_chat_lifecycle.enabled", lambda environ=None: self.hosted),
                        patch("angelo_agent.worker_merge_gate.waits", self.gate.waits),
                        patch("angelo_agent.worker_merge_gate.asking", self.gate.asking),
                        patch("angelo_agent.worker_resume.event_merge", lambda chat, arguments: True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        real = self.scene.execute

        def execute(name, arguments):
            if name == MERGE and self.gate.refuse:
                return {"error": self.gate.refuse, "error_code": "WRITER_BUSY", "scene_changed": False}
            return real(name, arguments)
        self.scene.execute = execute


class RequestOrderTests(Fixture):
    def test_the_events_merge_in_ask_runs_before_a_later_edit_binds_and_never_waits_on_its_card(self):
        c = self.chat("ASK")
        merged, merge = self.request("merge", MERGE, {"parts": [{"worker": 4}]}, c)
        self.pump(c)   # Run at once (the event's merge, no card): the gate is asked, the merge waits for its answers.
        self.assertTrue(merge.merge_waits and not merged)
        edited, edit = self.request("stray", *MOVE, c)
        self.pump(c)
        self.assertIsNone(edit.precondition, "the later edit bound before the merge it was asked after")
        self.assertTrue(edit.binds_after_merge and not edit.approval, "no card while it waits to bind")
        self.gate.open = False   # Every answer is in.
        self.pump(c)
        self.assertTrue(merged and merged[0].get("success"), merged)
        self.assertEqual(self.scene.ran, [MERGE])
        self.assertEqual(edit.precondition, ("scene", 1), "it binds against the merged scene")
        self.assertTrue(edit.approval and not edited, "its card is drawn; nothing approves it for the person")
        self.approve(edit, c)
        self.assertEqual(self.scene.ran, [MERGE, "angelo_set_transform"])
        self.assertNotEqual(edited[0].get("error_code"), "STALE_REVISION", edited)

    def test_a_later_edit_binds_once_the_merge_is_refused(self):
        merged, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]})
        self.pump()
        edited, edit = self.request("move", *MOVE)
        self.pump()
        self.assertIsNone(edit.precondition)
        self.gate.open, self.gate.refuse = False, "Another merge holds this part"   # The gate's fail-closed path.
        self.pump()
        self.assertEqual(merged[0].get("error_code"), "WRITER_BUSY", merged)
        self.assertNotIn(MERGE, self.scene.ran)
        self.assertEqual(self.scene.ran, ["angelo_set_transform"])
        self.assertEqual(edit.precondition, ("scene", 0))
        self.assertTrue(edited and not edited[0].get("error_code"), edited)

    def test_a_merge_still_waits_for_an_edit_asked_before_it(self):
        c = self.chat("ASK")
        edited, edit = self.request("move", *MOVE, c)
        self.pump(c)
        self.assertTrue(edit.approval and edit.precondition == ("scene", 0))
        merged, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.gate.open = False
        self.pump(c)
        self.assertTrue(merge.merge_waits and not merged, "the earlier bound edit holds it, as before")
        self.approve(edit, c)
        self.pump(c)
        self.assertEqual(self.scene.ran, ["angelo_set_transform", MERGE])

    def test_a_merge_asked_after_a_waiting_edit_never_overtakes_it(self):
        first, _ = self.request("merge-1", MERGE, {"parts": [{"worker": 1}]})
        self.pump()
        edited, edit = self.request("script", "angelo_execute_python", SCRIPT)
        second, _ = self.request("merge-2", MERGE, {"parts": [{"worker": 2}]})
        self.pump()
        self.assertTrue(edit.binds_after_merge and edit.precondition is None)
        self.gate.open = False
        self.pump(times=6)
        self.assertEqual(self.scene.ran, [MERGE], "the second merge waits for the script asked before it")
        self.assertTrue(edit.approval and edit.precondition == ("scene", 1))   # A script's card, on the merged scene.
        self.approve(edit)
        self.pump(times=3)
        self.assertEqual(self.scene.ran, [MERGE, "angelo_execute_python", MERGE])
        self.assertTrue(first[0].get("success") and second[0].get("success") and edited, (first, second, edited))

    def test_reads_and_other_chats_never_wait(self):
        other = self.chat()
        self.request("merge", MERGE, {"parts": [{"worker": 1}]})
        self.pump()
        moved, edit = self.request("move", *MOVE, other)
        read, _ = self.request("inspect", "angelo_inspect_scene", {})
        self.pump(other)
        self.assertTrue(edit.precondition is not None and moved, "another chat's edit binds and runs")
        self.pump()
        self.assertTrue(read, "a read is answered while the merge's gate is asked")


class LanesOrderTests(Fixture):
    hosted = False

    def test_the_lanes_keep_their_order(self):
        c = self.chat("ASK")
        _merged, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.pump(c)
        _edited, edit = self.request("move", *MOVE, c)
        self.pump(c)
        self.assertEqual(edit.precondition, ("scene", 0), "a later edit binds at once on the lanes")
        self.assertFalse(edit.binds_after_merge)


if __name__ == "__main__":
    unittest.main()
