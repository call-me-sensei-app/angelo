# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A held goal's decision table (``goal_state``; doc/angelo/goal_mode.rst, "The goal", "Checks", "Stopping").

Without Blender: what a finished round means (passed, progress, no progress, unmeasured), each limit of the budget,
measurement locking, the agent's own stop, pause and resume.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import goal_state as G  # noqa: E402


def outline(iou, drawn=3):
    return {"measured": True, "iou": iou, "mean_px": 10.0, "drawn": drawn}


UNMEASURED = {"measured": False, "reason": "no subject found in the picture"}


class Base(unittest.TestCase):
    def reference_goal(self, **budget):
        goal = G.new("Match the hut", G.WORDS, check=G.REFERENCE, now=0.0)
        goal["measure"] = {"reference": "r", "camera": {}, "targets": "VISIBLE", "locked": False}
        goal["budget"].update(budget)
        return goal

    def round(self, goal, measurement, *, working_ms=60_000, changed=True, tokens=1000, usd=0.0,
              measure_changed=False):
        return G.record_round(goal, kind=G.AUTOMATIC, working_ms=working_ms, measure_ms=40, tokens=tokens, usd=usd,
                              measurement=measurement, scene_changed=changed, measure_changed=measure_changed)


class DefaultsTests(Base):
    def test_defaults_are_the_documented_ones_and_spend_follows_billing(self):
        paid, plan = G.new("x", G.CHAT_BAR, paid=True), G.new("x", G.CHAT_BAR, paid=False)
        self.assertEqual((paid["budget"]["minutes"], paid["budget"]["stall_rounds"], paid["budget"]["max_rounds"],
                          paid["budget"]["unmeasured_rounds"], paid["budget"]["usd"]), (60, 3, 20, 2, 10.0))
        self.assertIsNone(plan["budget"]["usd"])   # A plan's usage is included: time bounds it.
        self.assertIsNone(plan["budget"]["tokens"])
        self.assertEqual(paid["pass_at"], 0.90)
        self.assertEqual(paid["status"], G.ACTIVE)

    def test_the_objective_is_bounded(self):
        self.assertEqual(len(G.new("x" * 900, G.WORDS)["objective"]), G.OBJECTIVE_MAX)

    def test_pass_at_is_the_persons_and_bounded(self):
        goal = G.new("x", G.CHAT_BAR)
        G.set_pass_at(goal, 0.95)
        self.assertEqual(goal["pass_at"], 0.95)
        for bad in (0.2, 1.0, float("nan"), "0.9", True):
            with self.assertRaises(ValueError, msg=bad):
                G.set_pass_at(goal, bad)


class RoundTests(Base):
    def test_passes_at_the_threshold_not_below(self):
        goal = self.reference_goal()
        self.assertEqual(self.round(goal, outline(0.899)), (G.ACTIVE, ""))
        self.assertEqual(self.round(goal, outline(0.90))[0], G.PASSED)

    def test_stalls_after_n_rounds_without_a_measured_gain(self):
        goal = self.reference_goal(stall_rounds=3)
        self.assertEqual(self.round(goal, outline(0.60))[0], G.ACTIVE)   # The first measured round sets the best.
        statuses = [self.round(goal, outline(iou))[0] for iou in (0.602, 0.601, 0.604)]   # Each under the step.
        self.assertEqual(statuses, [G.ACTIVE, G.ACTIVE, G.STALLED])

    def test_a_gain_resets_the_stall_count(self):
        goal = self.reference_goal(stall_rounds=2)
        for iou in (0.60, 0.60, 0.70, 0.70):
            status = self.round(goal, outline(iou))[0]
        self.assertEqual((status, goal["stall"]), (G.ACTIVE, 1))
        self.assertEqual([r["verdict"] for r in goal["rounds"]], [G.PROGRESS, G.NO_PROGRESS, G.PROGRESS, G.NO_PROGRESS])

    def test_an_unmeasured_round_is_neither_progress_nor_stall_and_has_its_own_limit(self):
        goal = self.reference_goal(stall_rounds=2, unmeasured_rounds=2)
        self.round(goal, outline(0.5))
        self.round(goal, outline(0.5))   # Stall 1.
        self.assertEqual(self.round(goal, UNMEASURED), (G.ACTIVE, ""))
        self.assertEqual(goal["stall"], 1)   # Unchanged by the unmeasured round.
        self.assertEqual(self.round(goal, UNMEASURED), (G.UNMEASURED, "no subject found in the picture"))
        self.assertEqual(goal["rounds"][-1]["verdict"], G.UNMEASURED_ROUND)

    def test_a_measured_round_ends_an_unmeasured_streak(self):
        goal = self.reference_goal(unmeasured_rounds=2)
        self.round(goal, UNMEASURED)
        self.round(goal, outline(0.4))
        self.assertEqual(self.round(goal, UNMEASURED)[0], G.ACTIVE)

    def test_each_budget_limit_ends_it_and_says_which(self):
        cases = ((dict(minutes=1), 61_000, 0, "minutes"), (dict(usd=1.0), 1000, 2.0, "usd"),
                 (dict(tokens=5000), 1000, 0, "tokens"), (dict(max_rounds=1), 1000, 0, "rounds"))
        for budget, working_ms, usd, which in cases:
            goal = self.reference_goal(**budget)
            self.assertEqual(self.round(goal, outline(0.5), working_ms=working_ms, tokens=6000, usd=usd),
                             (G.BUDGET, which), budget)

    def test_working_time_and_spend_add_up_over_rounds(self):
        goal = self.reference_goal(minutes=3)
        self.round(goal, outline(0.3), working_ms=70_000, tokens=10, usd=0.5)
        self.round(goal, outline(0.4), working_ms=70_000, tokens=20, usd=0.25)
        self.assertEqual(goal["spent"], {"working_ms": 140_000, "tokens": 30, "usd": 0.75})
        self.assertEqual(self.round(goal, outline(0.5), working_ms=41_000), (G.BUDGET, "minutes"))

    def test_passing_wins_over_a_budget_reached_in_the_same_round(self):
        goal = self.reference_goal(max_rounds=1)
        self.assertEqual(self.round(goal, outline(0.95))[0], G.PASSED)

    def test_a_judged_goal_progresses_by_scene_change_only(self):
        goal = G.new("Tidy the shelf", G.CHAT_BAR)
        goal["budget"]["stall_rounds"] = 2
        self.assertEqual(self.round(goal, None, changed=True)[0], G.ACTIVE)
        self.assertEqual(self.round(goal, None, changed=False)[0], G.ACTIVE)
        self.assertEqual(self.round(goal, None, changed=False)[0], G.STALLED)

    def test_a_reference_goal_without_a_measure_is_judged_until_one_is_set(self):
        goal = G.new("Match it", G.CHAT_BAR, check=G.REFERENCE)
        self.assertTrue(G.judged(goal))
        self.assertEqual(self.round(goal, None, changed=True)[0], G.ACTIVE)
        self.assertEqual(goal["rounds"][-1]["verdict"], G.PROGRESS)

    def test_scene_checks_pass_when_all_pass_and_progress_by_count(self):
        goal = G.new("Make it game-ready", G.WORDS, check=G.SCENE)
        goal["measure"] = {"checks": [{"kind": "x"}], "locked": False}
        self.round(goal, {"measured": True, "passed_count": 1, "total": 3, "all_passed": False})
        self.round(goal, {"measured": True, "passed_count": 1, "total": 3, "all_passed": False})
        self.assertEqual(goal["rounds"][-1]["verdict"], G.NO_PROGRESS)
        self.assertEqual(self.round(goal, {"measured": True, "passed_count": 3, "total": 3, "all_passed": True})[0],
                         G.PASSED)


class LockingTests(Base):
    def test_the_first_round_with_something_drawn_locks_the_measurement(self):
        goal = self.reference_goal()
        self.round(goal, outline(0.0, drawn=0))   # An empty scene: nothing to judge the camera by yet.
        self.assertFalse(goal["measure"]["locked"])
        self.round(goal, outline(0.3, drawn=2))
        self.assertTrue(goal["measure"]["locked"])

    def test_a_round_whose_measurement_changed_cannot_pass(self):
        goal = self.reference_goal()
        status = self.round(goal, outline(0.97), measure_changed=True)
        self.assertEqual(status, (G.ACTIVE, ""))
        self.assertEqual(goal["rounds"][-1]["verdict"], G.MEASURE_CHANGED)
        self.assertEqual(self.round(goal, outline(0.97))[0], G.PASSED)   # The next round, same measure: it can.

    def test_a_locked_measure_change_becomes_the_agents_request_until_the_person_allows_it(self):
        goal = self.reference_goal()
        goal["measure"]["locked"] = True
        wanted = {"reference": "r", "camera": {"location": [0, 0, 1]}, "targets": "VISIBLE"}
        with self.assertRaises(G.Locked):
            G.replace_measure(goal, wanted)
        self.assertEqual(goal["measure_request"], wanted)   # Kept for the chip's Allow; the goal goes on.
        self.assertTrue(G.running(goal))
        self.assertEqual(goal["measure"]["camera"], {})
        G.allow_measure_request(goal)
        self.assertEqual((goal["measure"]["camera"], goal["measure"]["locked"]), ({"location": [0, 0, 1]}, False))
        self.assertIsNone(goal["measure_request"])

    def test_unlock_lets_the_agent_state_another(self):
        goal = self.reference_goal()
        goal["measure"]["locked"] = True
        G.unlock(goal)
        G.replace_measure(goal, {"reference": "r", "camera": {"lens": 35}, "targets": {"collection": "Hut"}})
        self.assertEqual(goal["measure"]["targets"], {"collection": "Hut"})


class AgentStopTests(Base):
    def test_needs_input_pauses_it_with_the_agents_question(self):
        goal = self.reference_goal()
        G.agent_stop(goal, "NEEDS_INPUT", "Which roof: thatch or shingles?")
        self.assertEqual((goal["status"], goal["reason"]), (G.PAUSED, "Which roof: thatch or shingles?"))
        self.assertEqual(goal["paused_by"], G.PAUSED_BY_AGENT)
        G.resume(goal)
        self.assertTrue(G.running(goal))

    def test_done_and_blocked_end_it_with_the_agents_message_as_data(self):
        for reason, status in (("DONE", G.DONE), ("BLOCKED", G.BLOCKED)):
            goal = self.reference_goal()
            self.round(goal, outline(0.88))
            G.agent_stop(goal, reason, "The roof needs the person's call")
            self.assertEqual((goal["status"], goal["reason"]), (status, "The roof needs the person's call"))
            self.assertEqual(goal["ended_at_score"], 0.88)   # Shown beside it: "ended by the agent at 88% of 90%".

    def test_an_unknown_reason_is_refused(self):
        with self.assertRaises(ValueError):
            G.agent_stop(self.reference_goal(), "GIVE_UP", "")


class LifecycleTests(Base):
    def test_pause_and_resume_keep_its_rounds(self):
        goal = self.reference_goal()
        self.round(goal, outline(0.4))
        G.pause(goal, G.PAUSED_BY_MESSAGE)
        self.assertEqual((goal["status"], goal["reason"]), (G.PAUSED, G.PAUSED_BY_MESSAGE))
        G.resume(goal)
        self.assertEqual((goal["status"], len(goal["rounds"])), (G.ACTIVE, 1))

    def test_only_an_active_or_paused_goal_can_be_resumed_or_ended(self):
        goal = self.reference_goal()
        G.end(goal, G.STOPPED, "Stopped")
        for action in (lambda: G.resume(goal), lambda: G.pause(goal, "x"), lambda: G.end(goal, G.FAILED, "y")):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(goal["status"], G.STOPPED)

    def test_a_proposed_goal_starts_only_by_the_persons_start(self):
        goal = G.new("Keep going", G.WORDS, proposed=True)
        self.assertEqual(goal["status"], G.PROPOSED)
        self.assertFalse(G.running(goal))
        G.start(goal)
        self.assertTrue(G.running(goal))

    def test_a_proposed_goal_lapses_on_the_persons_next_message(self):
        goal = G.new("Keep going", G.WORDS, proposed=True)
        G.lapse(goal)
        self.assertEqual((goal["status"], goal["reason"]), (G.STOPPED, G.NOT_STARTED))
        active = self.reference_goal()
        G.lapse(active)   # Only a proposed goal lapses.
        self.assertEqual(active["status"], G.ACTIVE)

    def test_restart_pauses_an_active_goal_and_leaves_others(self):
        active, ended = self.reference_goal(), self.reference_goal()
        G.end(ended, G.PASSED, "")
        for goal in (active, ended):
            G.after_restart(goal)
        self.assertEqual((active["status"], active["reason"]), (G.PAUSED, G.PAUSED_BY_RESTART))
        self.assertEqual(ended["status"], G.PASSED)

    def test_over_budget_before_a_round_is_read_without_recording_one(self):
        goal = self.reference_goal(minutes=1)
        self.assertIsNone(G.over_budget(goal))
        goal["spent"]["working_ms"] = 60_000
        self.assertEqual(G.over_budget(goal)[0], "minutes")
        self.assertEqual(goal["rounds"], [])


if __name__ == "__main__":
    unittest.main()
