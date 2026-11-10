# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's pending messages (``steering.Inbox``): sent during a turn, steered or queued, never lost or sent twice.

Dependency-free: the inbox alone, with its providers' and UI's calls made directly (from threads where they run on
threads in the app).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import steering  # noqa: E402
from angelo_agent.steering import DELIVERING, PAUSED, QUEUE, SENDING, STEER, WAITING, Inbox  # noqa: E402

PICTURE = {"kind": "image", "path": "/refs/a.png", "name": "a.png", "mime": "image/png"}


class HoldTests(unittest.TestCase):
    def test_a_message_steers_when_the_turn_can_take_one_else_it_queues(self):
        inbox = Inbox()
        self.assertEqual(inbox.hold("make it red").mode, STEER)
        self.assertEqual(inbox.hold("then a lamp", steerable=False).mode, QUEUE, "A turn with no safe step queues")
        self.assertEqual(inbox.hold("like this", [PICTURE]).mode, QUEUE, "Steering carries text only")
        self.assertEqual([item.text for item in inbox.items()], ["make it red", "then a lamp", "like this"])

    def test_empty_oversized_and_runaway_messages_are_refused(self):
        inbox = Inbox()
        for text in ("", "   ", "x" * (steering.MAX_TEXT + 1)):
            with self.assertRaises(ValueError):
                inbox.hold(text)
        self.assertEqual(inbox.hold("", [PICTURE]).mode, QUEUE, "A picture alone is a message")
        for index in range(steering.MAX_PENDING - 1):
            inbox.hold("m%d" % index)
        with self.assertRaises(OverflowError):
            inbox.hold("one too many")

    def test_the_mode_switches_only_while_the_person_can_still_change_it(self):
        inbox = Inbox()
        item, picture = inbox.hold("make it red"), inbox.hold("this", [PICTURE])
        self.assertTrue(inbox.set_mode(item.id, QUEUE))
        self.assertTrue(inbox.set_mode(item.id, STEER))
        self.assertFalse(inbox.set_mode(picture.id, STEER), "A picture can't be steered in")
        inbox.take_steers()
        self.assertFalse(inbox.set_mode(item.id, QUEUE), "Its runtime is deciding: too late to switch")
        self.assertFalse(inbox.set_mode(999999, QUEUE))


class SteerTests(unittest.TestCase):
    def test_a_safe_step_takes_every_waiting_steer_in_order_and_nothing_else(self):
        inbox = Inbox()
        first, queued, second = inbox.hold("a"), inbox.hold("b", steerable=False), inbox.hold("c")
        self.assertEqual(inbox.take_steers(), [first, second])
        self.assertEqual([first.phase, queued.phase, second.phase], [DELIVERING, WAITING, DELIVERING])
        self.assertEqual(inbox.take_steers(), [], "Never handed out twice")
        self.assertIsNone(inbox.take(first.id), "Not editable while its runtime has it")
        self.assertFalse(inbox.set_mode(first.id, QUEUE))

    def test_delivered_steers_leave_and_returned_ones_wait_again_in_place(self):
        inbox = Inbox()
        a, b, c = inbox.hold("a"), inbox.hold("b"), inbox.hold("c")
        inbox.take_steers()
        self.assertEqual(inbox.delivered([a]), [a])
        self.assertEqual(inbox.delivered([a]), [], "Delivered once")
        inbox.returned([b, c])
        self.assertEqual([(item.text, item.phase) for item in inbox.items()], [("b", WAITING), ("c", WAITING)])
        self.assertEqual(inbox.take_steers(), [b, c])

    def test_a_steer_returned_after_stop_pauses_instead_of_sending_by_itself(self):
        inbox = Inbox()
        item = inbox.hold("a")
        inbox.take_steers()
        inbox.returned([item], "stopped")
        self.assertEqual((item.phase, item.reason), (PAUSED, "stopped"))

    def test_concurrent_safe_steps_never_take_the_same_message(self):
        inbox = Inbox()
        for index in range(steering.MAX_PENDING):
            inbox.hold(str(index))
        taken, start = [], threading.Barrier(8)

        def step():
            start.wait()
            taken.extend(inbox.take_steers())
        threads = [threading.Thread(target=step) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(item.id for item in taken), sorted(item.id for item in inbox.items()))
        self.assertEqual(len(taken), steering.MAX_PENDING)


class RestoreTests(unittest.TestCase):
    def test_messages_coming_back_are_never_cut_by_the_limit(self):
        inbox = Inbox()
        sent = [inbox.hold("in flight %d" % index) for index in range(3)]
        inbox.next_due()
        inbox.sent(sent)
        for index in range(steering.MAX_PENDING):
            inbox.hold("typed %d" % index)
        inbox.not_sent(sent, "not_started")   # A worker's lane refused them later (break-it round 2 S2).
        self.assertEqual(len(inbox.items()), steering.MAX_PENDING + 3, "None of the person's words dropped")
        again = Inbox()
        again.load(inbox.records(), "relaunch")
        self.assertEqual(len(again), steering.MAX_PENDING + 3, "Nor after a relaunch (break-it round 3)")


class AfterTurnTests(unittest.TestCase):
    def test_after_a_normal_end_everything_waiting_is_due_as_one_request_oldest_first(self):
        inbox = Inbox()
        a, b = inbox.hold("steer the turn never reached"), inbox.hold("queued", steerable=False)
        self.assertEqual(inbox.next_due(), [a, b])
        self.assertEqual({a.phase, b.phase}, {SENDING})
        inbox.hold("typed while it sends")
        self.assertEqual(inbox.next_due(), [], "One send at a time")
        inbox.sent([a, b])
        self.assertEqual([item.text for item in inbox.next_due()], ["typed while it sends"])

    def test_angelos_own_follow_up_goes_alone_after_the_persons_words_and_is_never_theirs(self):
        inbox = Inbox()
        a = inbox.hold("make it red")
        go_on = inbox.hold("Continue", label="Continue", send_options={"host_request": []})
        b = inbox.hold("then a lamp")
        self.assertEqual((go_on.mode, go_on.can_steer), (QUEUE, False), "Never steered into the turn")
        self.assertEqual(inbox.take_steers(), [a, b])
        inbox.returned([a, b])
        self.assertEqual(inbox.next_due(), [a], "The person's words first, never joined to Angelo's own")
        inbox.sent([a])
        self.assertEqual(inbox.next_due(), [go_on], "Angelo's follow-up is a request of its own")
        inbox.sent([go_on])
        self.assertEqual(inbox.next_due(), [b])
        self.assertTrue(inbox.has_due())
        inbox.sent([b])
        self.assertFalse(inbox.has_due())
        kept = inbox.hold("Continue", label="Continue")
        self.assertIsNone(inbox.take_newest(), "Up arrow never pulls Angelo's own follow-up into the message box")
        self.assertEqual(inbox.records(), [], "Never saved with the chat")
        inbox.turn_ended(False, "failed")
        self.assertFalse(inbox.has_due(), "Paused messages wait for the person")
        self.assertEqual(kept.phase, PAUSED)

    def test_an_abnormal_end_pauses_and_send_resumes_one(self):
        inbox = Inbox()
        a, b = inbox.hold("a"), inbox.hold("b", steerable=False)
        inbox.turn_ended(False, "stopped")
        self.assertEqual(inbox.next_due(), [], "Nothing is sent by itself after Stop")
        self.assertEqual({a.reason, b.reason}, {"stopped"})
        self.assertTrue(inbox.resume(b.id))
        self.assertEqual(inbox.next_due(), [b])
        self.assertEqual(a.phase, PAUSED)

    def test_a_refused_send_pauses_with_its_reason_and_a_request_never_started_comes_back_first(self):
        inbox = Inbox()
        a, b = inbox.hold("a"), inbox.hold("b")
        due = inbox.next_due()
        inbox.not_sent(due, {"text": "This chat has no connection."})
        self.assertEqual([item.phase for item in (a, b)], [PAUSED, PAUSED])
        self.assertEqual(a.reason, {"text": "This chat has no connection."})
        inbox.resume(a.id)
        inbox.resume(b.id)
        due = inbox.next_due()
        inbox.sent(due)
        later = inbox.hold("c")
        inbox.restore(due, "not_started")
        self.assertEqual(inbox.items(), [a, b, later], "Back first, in their order")
        self.assertEqual((a.phase, a.reason), (PAUSED, "not_started"))

    def test_edit_takes_the_text_and_pictures_back(self):
        inbox = Inbox()
        a, b, c = inbox.hold("a"), inbox.hold("b", [PICTURE]), inbox.hold("c")
        self.assertIs(inbox.take(b.id), b)
        self.assertEqual(b.references, [PICTURE])
        self.assertIs(inbox.take_newest(), c)
        inbox.take_steers()
        self.assertIsNone(inbox.take_newest(), "Nothing a runtime has")


class SavedTests(unittest.TestCase):
    def test_a_relaunch_gets_the_words_back_paused_and_skips_malformed_entries(self):
        inbox = Inbox()
        inbox.hold("make it red")
        inbox.hold("like this", [PICTURE])
        records = inbox.records()
        again = Inbox()
        again.load(records + [None, {"text": 5}, {"text": " "}, {"text": "x" * (steering.MAX_TEXT + 1)}], "relaunch")
        self.assertEqual([(item.text, item.mode, item.phase, item.reason) for item in again.items()],
                         [("make it red", STEER, PAUSED, "relaunch"), ("like this", QUEUE, PAUSED, "relaunch")])
        self.assertEqual(again.next_due(), [], "Never sent by itself after a relaunch")


if __name__ == "__main__":
    unittest.main()
