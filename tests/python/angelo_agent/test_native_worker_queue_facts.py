# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long a lead's line of queued parts is, measured (``worker_facts.queue_facts``), in the start's answer
(``worker_queue.admit``) and every status answer (``worker_facts.annotate``, what ``worker_sessions.status`` returns).
No Blender.

Evidence: the C3 early town-centre diagnostic (C3-EARLY-DISSECT.md): a lead queued 23 parts at one worker at a time;
worker turns summed 2,444 s (median about 62 s) and nothing it read said what that line costs. Facts only, from live
measurements: none finished yet gives no number.
"""

import time
import unittest
from unittest import mock

from test_native_worker_queue import Fixture, item   # The queue's own fixture: its tables emptied per test.
from angelo_agent import worker_facts, worker_queue   # noqa: E402

ESTIMATE = getattr(worker_facts, "ESTIMATE", None)   # Absent before this fix: the tests then fail on the answers.


class QueueFactsTests(Fixture):
    LIMIT = 2

    def setUp(self):
        super().setUp()
        for name in ("_built", "_briefed", "_turns", "_samples"):
            patcher = mock.patch.object(worker_facts, name, {})
            patcher.start()
            self.addCleanup(patcher.stop)

    def finished(self, session, number, seconds, status="completed"):
        """A worker whose first build turn ended ``seconds`` after its brief, as the chat records it."""
        chat, _worker = self.running(session, number=number)
        self.turn(chat, seconds, status)
        return chat

    @staticmethod
    def turn(chat, seconds, status="completed"):
        worker_facts.turn_started(chat)   # A message (its brief, or a follow-up) reaches the chat ...
        worker_facts._briefed[chat.state["id"]] = time.monotonic() - seconds
        worker_facts.turn_ended(chat, status)   # ... and its request ends (Controller._finish_metrics).

    def test_with_nothing_finished_the_start_says_so_and_gives_no_number(self):
        self.running("w1")[0].busy = True   # Still at work.
        answer = worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertEqual(answer["queue"], {"queued": 2, "restarts_queued": 0, "at_once": 2, "measured": worker_facts.MEASURED,
                                           "finished": "none finished yet"})

    def test_the_start_reports_measured_turns_and_an_estimate_labelled_as_one(self):
        self.finished("w1", 1, 62.0)
        self.finished("w0", 9, 40.0)
        for session in ("w1", "w0"):   # Both places taken by workers that stay (a done worker parks after its save).
            self.ui._controllers[session].busy = False
        with mock.patch.object(worker_queue, "free", lambda: 0), \
                mock.patch("angelo_agent.worker_pool.takeable", lambda: 0):
            answer = worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertEqual(answer["queue"], {
            "queued": 3, "restarts_queued": 0, "at_once": 2, "measured": worker_facts.MEASURED,
            "finished": {"count": 2, "min_s": 40.0, "median_s": 51.0, "max_s": 62.0},
            "estimate_s": 102, "estimate": ESTIMATE})   # ceil(3 / 2) x 51 s.

    def test_status_answers_carry_it_while_parts_wait_and_nothing_otherwise(self):
        self.finished("w1", 1, 62.0)
        self.assertNotIn("queue", worker_facts.annotate("lead", {"workers": []}))   # Nothing waits: no bytes.
        worker_queue.queue.append(item(self.lead, 2))
        worker_queue.queue.append(item(self.lead, 3))
        self.assertEqual(worker_facts.annotate("lead", {"workers": []})["queue"], {
            "queued": 2, "restarts_queued": 0, "at_once": 2, "measured": worker_facts.MEASURED,
            "finished": {"count": 1, "min_s": 62.0, "median_s": 62.0, "max_s": 62.0},
            "estimate_s": 62, "estimate": ESTIMATE})

    def test_another_conversation_s_queued_parts_are_not_counted(self):
        from types import SimpleNamespace
        worker_queue.queue.append(item(SimpleNamespace(state={"id": "other"}), 2))
        self.assertIsNone(worker_facts.queue_facts("lead"))

    def test_the_label_states_what_the_estimate_leaves_out(self):
        self.assertIn("estimate_s is median_s times the rounds of at_once parts the queued new parts take", ESTIMATE)
        self.assertIn("the running parts' remaining time and queued restarts are not included", ESTIMATE)
        self.assertIn("first build turns of this conversation's workers that completed", worker_facts.MEASURED)

    def test_a_short_follow_up_never_replaces_a_first_build_whether_status_was_read_or_not(self):
        """Break-it QF-M-1: one immutable sample per (chat, turn), taken as the turn ends; only first builds count."""
        chat = self.finished("w1", 1, 120.0)
        self.turn(chat, 2.0)   # A follow-up, no status read between.
        worker_queue.queue.append(item(self.lead, 2))
        facts = worker_facts.queue_facts("lead")
        self.assertEqual(facts["finished"], {"count": 1, "min_s": 120.0, "median_s": 120.0, "max_s": 120.0})
        self.assertEqual(facts["estimate_s"], 120)

    def test_a_failed_or_stopped_first_turn_is_not_counted(self):
        self.finished("w1", 1, 5.0, status="failed")
        self.finished("w0", 9, 7.0, status="interrupted")
        worker_queue.queue.append(item(self.lead, 2))
        self.assertEqual(worker_facts.queue_facts("lead")["finished"], "none finished yet")

    def chat_at_work(self, seconds):
        """A real worker ``Controller`` whose brief reached it ``seconds`` ago, routed by Auto."""
        import tempfile
        from pathlib import Path
        from angelo_agent import model_routing, worker_sessions
        from angelo_agent.controller import Controller
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        chat = Controller(folder.name)
        self.addCleanup(chat.close)
        session = chat.state["id"]
        worker_sessions._started.setdefault("lead", []).append(session)
        worker_sessions._parts[session] = (5, "Lamp", Path("/l"))
        worker_facts.turn_started(chat)
        worker_facts._briefed[session] = time.monotonic() - seconds
        chat._turn_route = model_routing.Route("c", "small", "fast", "low", None, True, False, "auto",
                                               (model_routing.Rung("large", "strong", "low"),))
        chat._turn_escalations, chat._recent_escalations, chat._turn_mutated = 0, [], False
        chat._start_turn = mock.Mock()   # The stronger model's run of the same message (its own request).
        return chat, session

    def end(self, chat, steps, outcomes):
        """Each attempt ends (``_after_turn``, as the pump calls it) with ``outcomes``; Auto decides ``steps``."""
        from angelo_agent import controller as controller_module, model_routing
        decisions = list(steps)
        with mock.patch.object(controller_module.model_routing, "next_step", lambda *a, **k: decisions.pop(0)):
            for status in outcomes:
                chat._after_turn({"status": status, "trigger": "low_confidence"})

    def test_an_escalated_attempt_is_not_the_sample_its_completed_message_is(self):
        """Break-it QF2-M-1: one message, two attempts; the sample is the message, brief to final end."""
        from angelo_agent import model_routing
        chat, session = self.chat_at_work(60.0)
        self.end(chat, [model_routing.Step("escalate", model_routing.Rung("large", "strong", "low"), "low confidence"),
                        None], ["completed", "completed"])
        self.assertEqual(chat._start_turn.call_count, 1)
        seconds, status = worker_facts._samples[(session, 1)]
        self.assertEqual(status, "completed")
        self.assertAlmostEqual(seconds, 60.0, delta=1.0)

    def test_a_failed_attempt_that_auto_retries_on_a_stronger_model_then_completes_counts_once(self):
        from angelo_agent import model_routing
        chat, session = self.chat_at_work(45.0)
        self.end(chat, [model_routing.Step("escalate", model_routing.Rung("large", "strong", "low"), "failed"), None],
                 ["failed", "completed"])
        self.assertEqual(worker_facts._samples, {(session, 1): (worker_facts._samples[(session, 1)][0], "completed")})

    def test_an_escalated_message_stopped_by_the_person_is_not_counted(self):
        from angelo_agent import model_routing
        chat, session = self.chat_at_work(30.0)
        self.end(chat, [model_routing.Step("escalate", model_routing.Rung("large", "strong", "low"), "low confidence")],
                 ["completed"])
        self.assertEqual(worker_facts._samples, {})   # Still running on the stronger model.
        chat._after_turn({"status": "interrupted", "trigger": "low_confidence"})   # Stop: Auto continues nothing.
        self.assertEqual(worker_facts._samples[(session, 1)][1], "interrupted")
        worker_queue.queue.append(item(self.lead, 2))
        self.assertEqual(worker_facts.queue_facts("lead")["finished"], "none finished yet")

    def test_a_queued_restart_counts_as_waiting_and_apart_from_new_parts(self):
        """Break-it QF-M-2: a message to a parked worker waits for a place too (ahead of new parts)."""
        worker_queue.queue.append({"kind": worker_queue.RESUME, "lead": "lead", "session": "w9"})
        facts = worker_facts.queue_facts("lead")
        self.assertEqual((facts["queued"], facts["restarts_queued"]), (0, 1))
        worker_queue.queue.append(item(self.lead, 2))
        self.assertEqual((worker_facts.queue_facts("lead")["queued"], worker_facts.queue_facts("lead")["restarts_queued"]),
                         (1, 1))

    def test_a_stopped_wait_answers_with_the_live_queue(self):
        """Break-it QF-L-1: built when the controller reads it, after the stop."""
        from angelo_agent import worker_status
        waiting = worker_status.wait(self.lead, 60)
        worker_queue.queue.append(item(self.lead, 2))   # Queued after the wait began.
        waiting.cancel()   # Stop: the controller cancels, then reads stopped_result.
        stopped = waiting.stopped_result
        self.assertEqual((stopped["error_code"], stopped["queue"]["queued"]), ("CANCELLED", 1))
        worker_queue.queue.clear()
        self.assertNotIn("queue", waiting.stopped_result)

    def test_a_start_whose_line_drained_meanwhile_carries_no_queue(self):
        """Break-it QF-L-2: computed after drive; absent, never null, once nothing waits."""
        self.running("w1")[0].busy = True
        with mock.patch.object(worker_queue, "drive", lambda: worker_queue.queue.clear()):
            answer = worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertIn("queued", answer)
        self.assertNotIn("queue", answer)


class OneAtATimeTests(QueueFactsTests):
    """At one place (the registered benchmark's cap, or a person's Workers at once 1) the line runs one by one."""
    LIMIT = 1

    def test_with_nothing_finished_the_start_says_so_and_gives_no_number(self):
        self.running("w1")[0].busy = True
        answer = worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertEqual(answer["queue"], {"queued": 3, "restarts_queued": 0, "at_once": 1, "measured": worker_facts.MEASURED,
                                           "finished": "none finished yet"})

    def test_the_start_reports_measured_turns_and_an_estimate_labelled_as_one(self):
        self.finished("w1", 1, 62.0)
        with mock.patch.object(worker_queue, "free", lambda: 0), \
                mock.patch("angelo_agent.worker_pool.takeable", lambda: 0):
            answer = worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertEqual((answer["queue"]["queued"], answer["queue"]["estimate_s"]), (3, 186))   # 3 x 62 s.

    def test_status_answers_carry_it_while_parts_wait_and_nothing_otherwise(self):
        self.finished("w1", 1, 62.0)
        worker_queue.queue.append(item(self.lead, 2))
        worker_queue.queue.append(item(self.lead, 3))
        self.assertEqual(worker_facts.annotate("lead", {"workers": []})["queue"]["estimate_s"], 124)


class ParallelOffTests(unittest.TestCase):
    def test_no_queue_no_bytes(self):
        with mock.patch.object(worker_queue, "queue", []):
            self.assertIsNone(worker_facts.queue_facts("lead"))


if __name__ == "__main__":
    unittest.main()
