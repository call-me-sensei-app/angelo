# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A step the host ended for running out of time reads "Expired · nothing ran", never a red Failed (call_ended).

The mark is the host's, set where it ends the call and only when nothing of the call ran; every other ``EXPIRED`` (a
download that failed while it ran, a job that had started) keeps reading Failed, and the model's result is unchanged.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import activity_text, chat_rows, chat_titles  # noqa: E402
from test_native_tool_deadlines import PYTHON, RevocationFixture  # noqa: E402


def row(controller, call):
    controller.pump_events()   # The activity event becomes the call's transcript row (activity_rows.store).
    return next(message for message in controller.state["messages"]
                if message.get("role") == "activity" and message.get("call_id") == call.id)


class ExpiryStateTests(RevocationFixture):
    def expire(self, call):
        call.expires = time.monotonic() - 1
        self.assertTrue(self.controller.expire_tool(call))

    def test_an_unanswered_card_reads_expired_nothing_ran(self):
        replies, call = self.awaiting_approval()
        self.expire(call)
        message = row(self.controller, call)
        self.assertEqual(message.get("ended"), "expired")
        self.assertEqual(activity_text.step_status(message), "expired")
        self.assertEqual(activity_text.status_label("expired"), "Expired · nothing ran")
        self.assertEqual(activity_text.status_tone("expired"), "neutral")
        self.assertEqual(activity_text.CHIP["expired"], ("preview", "Expired · nothing ran"))
        self.assertIn("Nobody answered in time", chat_rows._details(message, "activity"))
        self.assertIn("expired, nothing ran", chat_titles.tool_title(message))
        self.assertEqual(activity_text.group_title([message], live=False), "1 step expired")
        self.assertEqual(activity_text.group_status([message], live=False), "done")

    def test_the_models_result_is_unchanged(self):
        replies, call = self.awaiting_approval()
        self.expire(call)
        self.assertEqual((replies[0]["error_code"], replies[0]["error"], replies[0]["expired"]),
                         ("EXPIRED", "Blender operation/approval expired", True))
        self.assertNotIn("ended", replies[0])
        self.assertNotIn("ended", row(self.controller, call)["result"])

    def test_a_call_that_never_reached_its_card_timed_out(self):
        replies = []
        self.context.request_tool("queued", "angelo_execute_python", PYTHON, replies.append)
        call = next(call for key, call in self.controller.pending.items() if key.endswith("/queued"))
        self.expire(call)
        message = row(self.controller, call)
        self.assertEqual(message.get("ended"), "timed_out")
        self.assertEqual(activity_text.step_status(message), "expired")
        self.assertIn("ran out of time before it could start", chat_rows._details(message, "activity"))

    def test_an_approved_edit_waiting_on_a_private_stage_counts_as_nothing_ran(self):
        replies, call = self.awaiting_approval()
        call.approval, call.authorized, call.staging = False, True, True   # Approved, waiting for the writer lease.
        self.expire(call)
        self.assertEqual(row(self.controller, call).get("ended"), "timed_out")

    def test_a_call_that_started_is_never_marked(self):
        for flag in ("executing", "mutation_started", "preparation_effects_possible"):
            with self.subTest(flag=flag):
                replies, call = self.awaiting_approval(identity="started-" + flag)
                setattr(call, flag, True)
                self.controller.finish_tool(call, {"error_code": "EXPIRED", "error": "Blender operation/approval expired",
                                                   "expired": True})
                message = row(self.controller, call)
                self.assertNotIn("ended", message)
                self.assertEqual(activity_text.step_status(message), "failed")

    def test_a_revoked_call_still_reads_stopped(self):
        replies, call = self.awaiting_approval()
        call.revoked = "The agent turn has ended"
        self.assertTrue(self.controller.expire_tool(call))
        message = row(self.controller, call)
        self.assertNotIn("ended", message)
        self.assertEqual(activity_text.step_status(message), "stopped")


class RowTests(unittest.TestCase):
    def test_an_expired_failure_while_running_stays_failed(self):
        """A library download that failed while it ran answers EXPIRED too: no mark, so Failed."""
        message = {"role": "activity", "tool": "angelo_library_import", "status": "failed",
                   "result": {"error_code": "EXPIRED", "error": "The download was interrupted; try again."}}
        self.assertEqual(activity_text.step_status(message), "failed")

    def test_a_row_saved_before_the_mark_keeps_its_status(self):
        message = {"role": "activity", "tool": "angelo_execute_python", "status": "failed",
                   "result": {"error_code": "EXPIRED", "error": "Blender operation/approval expired", "expired": True}}
        self.assertEqual(activity_text.step_status(message), "failed")

    def test_an_unknown_ended_value_is_ignored(self):
        message = {"role": "activity", "tool": "angelo_execute_python", "status": "failed", "ended": "whatever",
                   "result": {"error": "x"}}
        self.assertEqual(activity_text.step_status(message), "failed")

    def test_mixed_groups_count_each_kind(self):
        expired = {"role": "activity", "tool": "angelo_execute_python", "status": "failed", "ended": "expired",
                   "result": {"error_code": "EXPIRED", "error": "x", "expired": True}}
        done = {"role": "activity", "tool": "angelo_view", "status": "completed", "result": {}}
        title = activity_text.group_title([done, expired], live=False)
        self.assertIn("1 step expired", title)
        self.assertNotIn("failed", title)


class UntilTests(unittest.TestCase):
    """Every card says until when it waits, so an expiry is never a surprise (approval_wait.until_text)."""

    def setUp(self):
        from types import SimpleNamespace
        from angelo_agent import approval_wait
        self.until, self.call = approval_wait.until_text, SimpleNamespace(expires=1000.0)
        self.wall = time.mktime((2026, 11, 5, 18, 30, 10, 0, 0, -1))

    def test_minutes_ahead_read_as_a_clock_time(self):
        self.assertEqual(self.until(self.call, wall=self.wall, now=1000.0 - 720), "Waits for your answer until 18:42")

    def test_within_the_current_minute_it_reads_seconds(self):
        self.assertEqual(self.until(self.call, wall=self.wall, now=1000.0 - 20), "Waits for your answer until 18:30:30")

    def test_a_held_scene_moving_the_deadline_moves_the_line(self):
        self.call.expires += 300   # document_scene.hold credits the held time to the deadline.
        self.assertEqual(self.until(self.call, wall=self.wall, now=1000.0 - 720), "Waits for your answer until 18:47")

    def test_past_or_missing_deadlines_say_nothing(self):
        from types import SimpleNamespace
        self.assertEqual(self.until(self.call, wall=self.wall, now=1001.0), "")
        self.assertEqual(self.until(SimpleNamespace(), wall=self.wall, now=0.0), "")

    def test_every_card_kind_draws_it(self):
        """The decision card's rows, the flow card's note and the plain fallback card (source pins: no bpy here)."""
        import inspect
        from angelo_agent import chat_blocks, chat_card_draw, chat_guard
        self.assertIn("approval_wait.until_rows(pending", inspect.getsource(chat_card_draw.approval_block))
        self.assertIn("approval_wait.until_text(pending)", inspect.getsource(chat_blocks.cards))
        self.assertIn("until_text(call_)", inspect.getsource(chat_guard.approval_block))

if __name__ == "__main__":
    unittest.main()
