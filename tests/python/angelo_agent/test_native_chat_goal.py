# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The goal chip and card's content in each state (``chat_goal``; doc/angelo/goal_mode.rst, "The chip and the card"),
without drawing: what each says, which buttons it offers and what its clicks do."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import chat_goal, goal_controls, goal_loop, goal_probes, goal_state as G, goal_store  # noqa: E402


def reference_goal(**changes):
    goal = G.new("Match the hut", G.WORDS, check=G.REFERENCE)
    goal["measure"] = {"reference": "r", "camera": {}, "targets": "VISIBLE", "locked": False}
    goal.update(changes)
    return goal


def measured(goal, iou, minutes=7):
    G.record_round(goal, kind=G.AUTOMATIC, working_ms=minutes * 60_000, measure_ms=30, tokens=40_000, usd=0.4,
                   measurement={"measured": True, "iou": iou, "drawn": 2}, scene_changed=True)


class ChipTests(unittest.TestCase):
    def parts(self, goal=None, pending=None, waiting=None):
        return chat_goal.chip_parts(None, goal, pending, waiting)

    def test_pending_reads_as_what_the_send_will_hold(self):
        pending = G.new("", G.CHAT_BAR, check=G.REFERENCE)
        pending["status"] = G.PENDING
        parts = self.parts(pending=pending)
        self.assertEqual((parts["title"], parts["close"], parts["buttons"]),
                         ("Until it matches the picture", "discard", []))
        self.assertEqual(parts["caption"], "Goal · 60 min")

    def test_active_shows_the_score_round_and_working_time(self):
        goal = reference_goal()
        measured(goal, 0.724)
        parts = self.parts(goal)
        self.assertEqual(parts["title"], "72% of 90% · round 2")
        self.assertEqual(parts["caption"], "Goal · 7 / 60 min · 40k tokens")
        self.assertEqual(parts["close"], "stop")

    def test_a_paid_goal_shows_its_spend_against_the_limit(self):
        goal = reference_goal()
        goal["budget"]["usd"] = 10.0
        measured(goal, 0.5)
        self.assertTrue(self.parts(goal)["caption"].endswith("$0.40 / $10.00"))

    def test_waiting_on_a_draft_and_an_agents_measure_request(self):
        goal = reference_goal(measure_request={"camera": {}})
        parts = self.parts(goal, waiting=goal_loop.WAITING_DRAFT)
        self.assertEqual(parts["title"], "Waiting while you write a message")
        self.assertEqual(parts["buttons"], [("Allow new measure", "allow")])

    def test_judged_proposed_paused_and_ended(self):
        judged = G.new("Tidy the shelf", G.CHAT_BAR)
        self.assertEqual(self.parts(judged)["title"], "Agent judges · round 1")
        proposed = G.new("Keep going", G.WORDS, proposed=True)
        self.assertEqual(self.parts(proposed)["buttons"], [("Start", "start")])
        paused = reference_goal()
        G.agent_stop(paused, "NEEDS_INPUT", "Thatch or shingles?")
        parts = self.parts(paused)
        self.assertEqual((parts["title"], parts["buttons"], parts["tone"]),
                         ("Paused: the agent asks you", [("Resume", "resume")], "amber"))
        passed = reference_goal()
        measured(passed, 0.5)
        measured(passed, 0.93)
        parts = self.parts(passed)
        self.assertEqual((parts["title"], parts["close"]), ("Passed · 93% from one camera", "dismiss"))
        self.assertEqual(parts["caption"], "Goal · 2 rounds · 14 min · 80k tokens")
        stalled = reference_goal()
        G.end(stalled, G.STALLED, "")
        self.assertEqual(self.parts(stalled)["title"], "Stopped: no progress")


class CardTests(unittest.TestCase):
    def kinds(self, blocks):
        return [block[0] for block in blocks]

    def test_a_pending_goal_offers_the_check_threshold_and_budget(self):
        pending = G.new("", G.CHAT_BAR, check=G.REFERENCE)
        pending["status"] = G.PENDING
        blocks = chat_goal.card_blocks(pending, pending=True, paid=True)
        segments = [block[2][0] for block in blocks if block[0] == "segment"]
        self.assertEqual(segments, ["check", "pass_at", "minutes", "usd", "stall_rounds"])
        self.assertEqual(blocks[-1], ("buttons", 38, [("Remove", "discard")]))

    def test_a_plan_goal_limits_tokens_and_a_locked_measure_offers_the_unlock(self):
        goal = reference_goal()
        measured(goal, 0.5)
        blocks = chat_goal.card_blocks(goal, paid=False)
        self.assertIn("tokens", [block[2][0] for block in blocks if block[0] == "segment"])
        self.assertIn(("action", 34, ("Measurement locked after its first round", "Let the agent change it", "unlock")),
                      blocks)
        self.assertEqual(self.kinds(blocks).count("round"), 1)
        self.assertEqual(blocks[-1][2], [("Stop goal", "stop")])

    def test_an_ended_goal_is_read_only_with_dismiss(self):
        goal = reference_goal()
        G.end(goal, G.STOPPED, "Stopped")
        blocks = chat_goal.card_blocks(goal)
        self.assertNotIn("segment", self.kinds(blocks))
        self.assertEqual(blocks[-1][2], [("Dismiss", "dismiss")])


class ClickTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        goal_loop._reset()
        goal_store._STORES.clear()
        self.addCleanup(goal_store._STORES.clear)
        self.addCleanup(lambda: [store.drain() for store in list(goal_store._STORES.values())])
        self.chat = SimpleNamespace(state={"id": "c1", "provider": "codex_subscription", "connection_id": "x",
                                           "options": {"approval": "AUTO_BOUNDED"}},
                                    telemetry=SimpleNamespace(directory=self.folder.name), busy=False, version=0,
                                    document=(1, 1), emit=lambda kind, data: None)

    def test_the_card_sets_the_budget_and_threshold_and_dismiss_forgets(self):
        with mock.patch.object(goal_probes, "paid", lambda chat: False):
            goal_controls.choose(self.chat)
        chat_goal.dispatch(SimpleNamespace(area=None), self.chat, {}, "popup_goal_set", ("minutes", 30))
        chat_goal.dispatch(SimpleNamespace(area=None), self.chat, {}, "popup_goal_set", ("pass_at", 0.95))
        chat_goal.dispatch(SimpleNamespace(area=None), self.chat, {}, "popup_goal_set", ("check", G.JUDGED))
        pending = goal_loop.pending_goal(self.chat)
        self.assertEqual((pending["budget"]["minutes"], pending["pass_at"], pending["check"]), (30, 0.95, G.JUDGED))
        chat_goal.dispatch(SimpleNamespace(area=None), self.chat, {"popover": {}}, "popup_goal_act", "discard")
        self.assertIsNone(goal_loop.pending_goal(self.chat))


if __name__ == "__main__":
    unittest.main()
