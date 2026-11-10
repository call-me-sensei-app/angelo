# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A held goal's rounds (``goal_loop``; doc/angelo/goal_mode.rst, "Setting a goal", "Rounds", "Stopping"), without
Blender: a turn's end marks the goal due; the window's update measures it and sends the next round as Angelo's own
request, only while the chat is idle, shown and has no draft; every way it stops; ``angelo_goal``'s refusals."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import goal_controls, goal_loop, goal_probes, goal_state as G, goal_store, goal_text  # noqa: E402
from angelo_agent.request_rows import person_request  # noqa: E402

REF = "0b5e8d1c-0000-4000-8000-000000000001"
CAMERA = {"location": [0.0, -8.0, 2.0], "look_at": [0.0, 0.0, 1.0], "lens": 50.0}


class Chat:
    """The parts of a ``Controller`` the loop reads, with ``send`` calling the loop's hook as ``Controller.send``."""

    def __init__(self, folder, chat_id="c1", approval="AUTO_BOUNDED", provider="codex_subscription"):
        self.state = {"id": chat_id, "options": {"approval": approval}, "provider": provider, "connection_id": "conn",
                      "messages": [{"role": "user", "text": "Build a hut like the picture"}]}
        self.busy, self.pending, self._worker, self.document_invalid, self.external_client = False, {}, None, False, ""
        self.setup_required, self._next_binding, self.voice, self.remote_executor = False, None, None, None
        self._turn_mutated, self.cancelled, self._host_request, self._host_kind = True, False, None, None
        self.telemetry = SimpleNamespace(directory=folder)
        self.document = (1, 1)   # Every stand-in chat works on one document.
        self.sent, self.rows, self.stops = [], [], []
        self.version = 0

    def send(self, text, observation=None, recovery_evidence=None, host_request=None, host_kind=None):
        self._host_request, self._host_kind = host_request, host_kind
        goal_loop.request_started(self, text, host_kind=host_kind, person=host_request is None)   # As Controller.send.
        self.sent.append({"text": text, "host_request": host_request, "host_kind": host_kind})
        self.state["messages"].append({"role": "user", "text": text, **(
            {"request_kind": host_kind} if host_request is not None else {})})
        self.busy = True

    def finish(self, status="completed"):
        self.busy = False
        goal_loop.turn_ended(self, {"status": status})

    def emit(self, kind, data):
        self.rows.append(data)

    def stop(self, reason=None):
        self.stops.append(reason)
        self.cancelled = True


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        goal_loop._reset()
        self.addCleanup(goal_loop._reset)
        goal_store._STORES.clear()
        self.addCleanup(goal_store._STORES.clear)
        self.addCleanup(lambda: [store.drain() for store in list(goal_store._STORES.values())])   # Before the folder goes.
        self.chat = Chat(self.folder.name)
        self.drafts, self.shown_away, self.held, self.modal = {}, False, False, False
        self.ui = SimpleNamespace(_controllers={"c1": self.chat}, prepare_controller=lambda chat=None: None,
                                  main_window=lambda: "window", document_key=lambda: (1, 1), _drafts=self.drafts)
        self.measurement = {"measured": True, "iou": 0.5, "drawn": 2, "mean_px": 9.0}
        self.spent = {"tokens": 0, "usd": 0.0}
        self.clock = [100.0]
        for patcher in (
                mock.patch("angelo_agent.goal_measure.measure", lambda chat, goal, window: dict(self.measurement)),
                mock.patch.object(goal_probes, "spent", lambda chat: (self.spent["tokens"], self.spent["usd"])),
                mock.patch.object(goal_probes, "blender_busy", lambda window: self.held or self.modal),
                mock.patch.object(goal_probes, "away", lambda chat, ui: self.shown_away),
                mock.patch.object(goal_probes, "worker_chat", lambda chat: False),
                mock.patch.object(goal_probes, "paid", lambda chat: False),
                mock.patch.object(goal_loop.time, "monotonic", lambda: self.clock[0]),
                mock.patch("angelo_agent.worker_routing.deliver", lambda chat, window, message, send, failed: send(
                    SimpleNamespace(observation={}, recovery_evidence={})))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def step(self, times=2):
        for _ in range(times):
            goal_loop.step(self.ui)

    def goal(self):
        return goal_loop.goal_of(self.chat)

    def set_from_words(self, **measure):
        args = {"action": "SET", "objective": "Match the hut in the picture"}
        if measure:
            args["measure"] = measure
        return goal_controls.execute(self.chat, "window", args)

    def run_turn(self, minutes=5, tokens=1000, status="completed"):
        self.clock[0] += minutes * 60
        self.spent["tokens"] += tokens
        self.chat.finish(status)


class FromWordsTests(Base):
    def test_set_holds_an_active_goal_measures_a_baseline_and_says_so(self):
        self.chat.send("Build a hut like the picture; don't stop until it matches")
        result = self.set_from_words(reference=REF, camera=CAMERA, targets="VISIBLE")
        self.assertEqual(result["status"], G.ACTIVE)
        self.assertEqual(result["measurement"]["iou"], 0.5)
        self.assertEqual(result["budget"]["minutes"], 60)
        self.assertEqual(self.goal()["origin"], G.WORDS)
        self.assertTrue(any("goal" in row.get("text", "").lower() for row in self.chat.rows))   # The notice row.

    def test_in_ask_a_goal_from_words_is_proposed_and_its_turn_end_sends_nothing(self):
        self.chat.state["options"]["approval"] = "ASK"
        self.chat.send("Keep going until it matches")
        self.assertEqual(self.set_from_words()["status"], G.PROPOSED)
        self.run_turn()
        self.step()
        self.assertEqual(len(self.chat.sent), 1)
        goal_controls.start(self.chat)   # The chip's Start.
        self.step()
        self.assertEqual(len(self.chat.sent), 2)
        self.assertEqual(self.chat.sent[-1]["host_kind"], goal_loop.REQUEST_KIND)

    def test_refused_where_the_person_did_not_ask(self):
        self.chat.send("x", host_request=[], host_kind="angelo_event")   # Angelo's own request.
        with self.assertRaises(ValueError):
            self.set_from_words()
        self.chat.send("x")
        self.chat.voice = object()
        with self.assertRaises(ValueError):
            self.set_from_words()
        self.chat.voice = None
        self.chat.state["options"]["approval"] = "READ_ONLY"
        with self.assertRaises(ValueError):
            self.set_from_words()
        self.chat.state["options"]["approval"] = "AUTO_BOUNDED"
        self.chat.external_client = "Claude Desktop"
        with self.assertRaises(ValueError):
            self.set_from_words()
        self.assertIsNone(self.goal())

    def test_one_active_goal_per_document(self):
        other = Chat(self.folder.name, chat_id="c2")
        self.ui._controllers["c2"] = other
        other.send("Keep going until all checks pass")
        goal_controls.execute(other, "window", {"action": "SET", "objective": "Checks pass"})
        self.chat.send("Keep going until it matches")
        with self.assertRaises(ValueError) as caught:
            self.set_from_words()
        self.assertIn("another chat", str(caught.exception))

    def test_set_with_only_a_measure_needs_a_held_goal(self):
        self.chat.send("x")
        with self.assertRaises(ValueError):
            goal_controls.execute(self.chat, "window", {"action": "SET", "measure": {
                "reference": REF, "camera": CAMERA, "targets": "VISIBLE"}})


class RoundTests(Base):
    def start_goal(self):
        self.chat.send("Build a hut like the picture; don't stop until it matches")
        self.set_from_words(reference=REF, camera=CAMERA, targets="VISIBLE")

    def test_a_turn_end_measures_and_sends_angelos_own_request(self):
        self.start_goal()
        self.measurement["iou"] = 0.62
        self.run_turn(minutes=8, tokens=40_000)
        self.step()
        sent = self.chat.sent[-1]
        self.assertEqual((sent["host_kind"], sent["host_request"]), (goal_loop.REQUEST_KIND, []))
        self.assertTrue(sent["text"].startswith("From Angelo, not the person: round 2"))
        self.assertIn("overlap 62.0%", sent["text"])
        self.assertFalse(person_request(self.chat.state["messages"][-1]))   # Angelo's row, never the person's words.
        rounds = self.goal()["rounds"]
        self.assertEqual((rounds[0]["kind"], rounds[0]["working_ms"], rounds[0]["tokens"]), (G.PERSON, 480_000, 40_000))

    def test_the_round_waits_while_busy_held_away_modal_or_with_a_draft(self):
        self.start_goal()
        self.run_turn()
        self.chat.busy = True
        self.step()
        self.chat.busy = False
        for attr in ("held", "shown_away", "modal"):
            setattr(self, attr, True)
            self.step()
            setattr(self, attr, False)
        self.drafts["c1"] = ("Actually make the roof red", 5)
        self.step()
        self.assertEqual(len(self.chat.sent), 1)
        self.assertEqual(goal_loop.waiting_reason(self.chat), goal_loop.WAITING_DRAFT)   # The chip says so.
        self.drafts["c1"] = ("", 0)
        self.step()
        self.assertEqual(len(self.chat.sent), 2)

    def test_it_passes_and_sends_nothing_more(self):
        self.start_goal()
        self.measurement["iou"] = 0.93
        self.run_turn()
        self.step()   # The round in which the agent set the measurement cannot pass: round 2 is sent.
        self.assertEqual(len(self.chat.sent), 2)
        self.assertEqual(self.goal()["rounds"][-1]["verdict"], G.MEASURE_CHANGED)
        self.run_turn()
        self.step()
        self.assertEqual(len(self.chat.sent), 2)
        self.assertEqual(self.goal()["status"], G.PASSED)
        self.assertIn(goal_text.end_notice(self.goal())["text"], [row.get("text") for row in self.chat.rows])

    def test_it_stalls_after_rounds_without_progress(self):
        self.start_goal()
        for _ in range(4):
            self.run_turn()
            self.step()
            if self.chat.busy:
                continue
        self.assertEqual(self.goal()["status"], G.STALLED)
        self.assertEqual(len(self.chat.sent), 4)   # The person's, then rounds 2-4; round 4's end stalls it.

    def test_the_working_time_budget_ends_it(self):
        self.start_goal()
        self.goal()["budget"]["minutes"] = 10
        for iou in (0.55, 0.6):
            self.measurement["iou"] = iou
            self.run_turn(minutes=6)
            self.step()
        self.assertEqual((self.goal()["status"], self.goal()["reason"]), (G.BUDGET, "minutes"))

    def test_an_automatic_round_is_stopped_at_the_budget_and_the_goal_ends_on_it(self):
        self.start_goal()
        self.goal()["budget"]["minutes"] = 10
        self.run_turn(minutes=4)
        self.step()
        self.assertTrue(self.chat.busy)   # Round 2 runs.
        self.clock[0] += 7 * 60
        self.step()
        self.assertEqual(len(self.chat.stops), 1)
        self.chat.finish("interrupted")
        self.assertEqual((self.goal()["status"], self.goal()["reason"]), (G.BUDGET, "minutes"))

    def test_the_persons_own_turn_is_never_stopped_by_the_budget(self):
        self.start_goal()
        self.goal()["budget"]["minutes"] = 1
        self.clock[0] += 5 * 60
        self.step()
        self.assertEqual(self.chat.stops, [])

    def test_the_persons_stop_ends_it(self):
        self.start_goal()
        self.run_turn()
        self.step()
        goal_controls.person_stopped(self.chat)
        self.chat.finish("interrupted")
        self.assertEqual(self.goal()["status"], G.STOPPED)
        self.step()
        self.assertEqual(len(self.chat.sent), 2)

    def test_stop_between_rounds_ends_it_at_once(self):
        self.start_goal()
        self.run_turn()
        goal_controls.person_stopped(self.chat)
        self.step()
        self.assertEqual((self.goal()["status"], len(self.chat.sent)), (G.STOPPED, 1))

    def test_another_stop_ends_it_with_that_reason_and_a_failure_ends_it(self):
        self.start_goal()
        self.chat.finish("interrupted")   # A document change or another host stop: no origin set.
        self.assertEqual((self.goal()["status"], self.goal()["reason"]), (G.STOPPED, goal_loop.INTERRUPTED))
        goal_store._STORES.clear()
        goal_loop._reset()
        self.chat = Chat(self.folder.name, chat_id="c3")
        self.ui._controllers = {"c3": self.chat}
        self.start_goal()
        self.chat.finish("failed")
        self.assertEqual(self.goal()["status"], G.FAILED)

    def test_a_persons_message_pauses_it_and_resume_continues(self):
        self.start_goal()
        self.run_turn()
        self.step()
        self.chat.finish()
        self.chat.send("Make the roof red")   # The person's own words mid-goal.
        self.assertEqual((self.goal()["status"], self.goal()["paused_by"]), (G.PAUSED, G.PAUSED_BY_MESSAGE))
        self.run_turn()
        self.step()
        self.assertEqual(len(self.chat.sent), 3)   # Nothing sent while paused.
        goal_controls.resume(self.chat)
        self.step()
        self.assertEqual(len(self.chat.sent), 4)
        self.assertEqual(self.chat.sent[-1]["host_kind"], goal_loop.REQUEST_KIND)

    def test_a_connection_change_ends_it_before_sending(self):
        self.start_goal()
        self.run_turn()
        self.chat._next_binding = {"connection_id": "another"}
        self.step()
        self.assertEqual((self.goal()["status"], len(self.chat.sent)), (G.STOPPED, 1))
        self.assertEqual(self.goal()["reason"], goal_loop.CONNECTION_CHANGED)

    def test_read_only_ends_it(self):
        self.start_goal()
        self.run_turn()
        self.chat.state["options"]["approval"] = "READ_ONLY"
        self.step()
        self.assertEqual((self.goal()["status"], len(self.chat.sent)), (G.STOPPED, 1))

    def test_a_worker_done_turn_is_a_round_and_carries_a_round_not_yet_measured(self):
        self.start_goal()
        self.run_turn(minutes=5)
        self.step()   # Round 2 is sent.
        self.run_turn(minutes=5)   # Round 2 ends; before the step measures it, the worker-done event starts a turn.
        self.chat.send("From Angelo, not the person: every worker ...", host_request=[], host_kind="angelo_event")
        self.run_turn(minutes=3)
        self.step()
        last = self.goal()["rounds"][-1]
        self.assertEqual((last["kind"], last["working_ms"]), (G.WORKER_EVENT, 8 * 60_000))

    def test_unmeasured_rounds_end_it_with_the_reason(self):
        self.start_goal()
        self.measurement = {"measured": False, "reason": "the targets are not in this view"}
        for _ in range(2):
            self.run_turn()
            self.step()
        self.assertEqual((self.goal()["status"], self.goal()["reason"]), (G.UNMEASURED, "the targets are not in this view"))


class ToolTests(Base):
    def test_measure_and_stop(self):
        self.chat.send("Keep going until it matches")
        self.set_from_words(reference=REF, camera=CAMERA)
        self.measurement["iou"] = 0.7
        self.assertEqual(goal_controls.execute(self.chat, "window", {"action": "MEASURE"})["measurement"]["iou"], 0.7)
        result = goal_controls.execute(self.chat, "window", {"action": "STOP", "reason": "NEEDS_INPUT",
                                                         "message": "Thatch or shingles?"})
        self.assertEqual(result["status"], G.PAUSED)
        self.run_turn()
        self.step()
        self.assertEqual(len(self.chat.sent), 1)   # Paused: nothing sent.

    def test_a_locked_measure_change_is_kept_as_a_request(self):
        self.chat.send("Keep going until it matches")
        self.set_from_words(reference=REF, camera=CAMERA)
        self.run_turn()
        self.step()   # Round 1 measured with something drawn: locked.
        result = goal_controls.execute(self.chat, "window", {"action": "SET", "measure": {
            "reference": REF, "camera": dict(CAMERA, lens=85.0), "targets": "VISIBLE"}})
        self.assertEqual(result["error_code"], "LOCKED")
        self.assertEqual(self.goal()["measure_request"]["camera"]["lens"], 85.0)
        self.assertTrue(G.running(self.goal()))

    def test_tool_calls_without_a_goal_are_refused(self):
        self.chat.send("x")
        for args in ({"action": "MEASURE"}, {"action": "STOP", "reason": "DONE", "message": "x"}):
            with self.assertRaises(ValueError):
                goal_controls.execute(self.chat, "window", args)


class ChatBarTests(Base):
    def test_a_pending_goal_starts_with_the_send_and_is_never_paused_by_it(self):
        goal_controls.choose(self.chat, check=G.REFERENCE)
        self.assertEqual(goal_loop.pending_goal(self.chat)["status"], G.PENDING)
        self.chat.send("Make it look like the picture")
        goal = self.goal()
        self.assertEqual((goal["status"], goal["origin"], goal["objective"]),
                         (G.ACTIVE, G.CHAT_BAR, "Make it look like the picture"))
        self.assertIsNone(goal_loop.pending_goal(self.chat))
        self.run_turn()
        self.step()
        self.assertIn("no camera is set", self.chat.sent[-1]["text"])   # Judged until the agent states its camera.

    def test_a_proposed_goal_lapses_on_the_persons_next_message(self):
        self.chat.state["options"]["approval"] = "ASK"
        self.chat.send("Keep going until it matches")
        self.set_from_words()
        self.chat.finish()
        self.chat.send("Never mind, just make the roof red")
        self.assertEqual((self.goal()["status"], self.goal()["reason"]), (G.STOPPED, G.NOT_STARTED))


class ObservationTests(Base):
    def test_a_running_goal_is_a_state_field_without_progress(self):
        self.assertEqual(goal_controls.observed(self.chat), {})
        self.chat.send("Keep going until it matches")
        self.set_from_words(reference=REF, camera=CAMERA)
        field = goal_controls.observed(self.chat)[goal_loop.OBSERVED]
        self.assertEqual(set(field), {"objective", "check", "pass_at", "limits", "measure"})
        before = dict(field)
        self.run_turn()
        self.step()
        self.assertEqual(goal_controls.observed(self.chat)[goal_loop.OBSERVED], before)   # No progress: never re-sent.


class RestartTests(Base):
    def test_a_restart_pauses_and_never_sends(self):
        self.chat.send("Keep going until it matches")
        self.set_from_words(reference=REF, camera=CAMERA)
        goal_store.GoalStore.shared(self.folder.name).flush()
        goal_store._STORES.clear()
        goal_loop._reset()
        self.assertEqual(self.goal()["status"], G.PAUSED)
        self.step()
        self.assertEqual(len(self.chat.sent), 1)


if __name__ == "__main__":
    unittest.main()
