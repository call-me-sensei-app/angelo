# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's ``chat.open`` never holds Blender's main thread (design A', C10), without Blender.

Pinned here (``worker_chat_lifecycle.opened(defer=True)``, ``when_open``, ``finish_opens``, ``worker_sessions._adopt``
and ``worker_queue``'s relaunch): the main thread's call returns at once however wedged the worker is; the chat is
bound and counted as hosted from the start; its brief (or a relaunch's waiting message) goes only once the worker
answered ``ok``; a refusal, a timeout or a lost worker ends the start on the main thread: the chat leaves its worker
(which stops), says why, and its status says why; a failure of the local phase refuses at once; with the switch off
nothing is deferred and the brief goes at once, as before.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_worker_chat_lifecycle as base  # noqa: E402
from angelo_agent import (parallel_sessions, worker_chat_lifecycle as lifecycle, worker_queue,  # noqa: E402
                          worker_routing, worker_sessions)
from angelo_agent.worker_process import WorkerUnavailable  # noqa: E402

AT_ONCE = 0.5   # Seconds: far less than OPEN_SECONDS, far more than a local prepare.


class WedgedWorker(base.OpWorker):
    """A worker whose ``chat.open`` waits until ``release``, then answers ``answer`` (as a request's bound would)."""

    def __init__(self, answer=None):
        super().__init__()
        self.gate, self.answer, self.entered = threading.Event(), answer, threading.Event()

    def request(self, op, timeout, **fields):
        if op == "chat.open":
            self.requests.append(dict(fields, op=op))
            self.entered.set()
            self.gate.wait(10)
            if isinstance(self.answer, BaseException):
                raise self.answer
            return self.answer or {"ok": True}
        return super().request(op, timeout, **fields)

    def release(self):
        self.gate.set()


class Chat(SimpleNamespace):
    def __init__(self, session="w1"):
        super().__init__(**vars(base.chat(session)))
        self.said = []

    def emit(self, kind, data=None):
        self.said.append((kind, dict(data or {})))


def until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class Deferred(base.Base):
    def setUp(self):
        super().setUp()
        self.left = []
        for patcher in (mock.patch.object(lifecycle, "_opening", {}),
                        mock.patch.object(worker_routing, "unbind",
                                          lambda controller, reason: self.left.append((controller, reason)))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def open(self, controller, worker, resume=False):
        begun = time.perf_counter()
        lifecycle.opened(controller, worker, resume=resume, defer=True)
        return time.perf_counter() - begun

    def finished(self, worker):
        worker.release()
        self.assertTrue(until(lambda: all(o.done.is_set() for o in lifecycle._opening.values())))
        lifecycle.finish_opens()


class OpenTests(Deferred):
    def test_a_wedged_worker_never_holds_the_main_thread(self):
        controller, worker, heard = Chat(), WedgedWorker(WorkerUnavailable("no answer", timed_out=True)), []
        self.assertLess(self.open(controller, worker), AT_ONCE)
        self.assertTrue(worker.entered.wait(5))   # Sent, and still unanswered.
        self.assertIs(lifecycle.hosted("w1"), worker)   # Hosted from the send on: a stop closes it.
        self.assertTrue(lifecycle.when_open(controller, heard.append))
        begun = time.perf_counter()
        lifecycle.finish_opens()   # The start timer's tick while the worker is wedged: nothing waits.
        self.assertLess(time.perf_counter() - begun, AT_ONCE)
        self.assertEqual((heard, self.left, lifecycle.opening()), ([], [], True))
        self.finished(worker)
        self.assertEqual(self.left, [(controller, worker_routing.END_NOT_BRIEFED)])   # Its worker stops.
        self.assertEqual(len(heard), 1)
        self.assertIsInstance(heard[0], lifecycle.ChatOpFailed)
        self.assertTrue(heard[0].timed_out)
        self.assertFalse(lifecycle.opening())

    def test_an_open_that_is_answered_runs_its_waiters_on_the_main_thread_with_the_lease_it_carried(self):
        controller, worker, heard = Chat(), WedgedWorker(), []
        self.open(controller, worker, resume=True)
        lifecycle.when_open(controller, lambda error: heard.append((error, threading.current_thread())))
        self.finished(worker)
        self.assertEqual(heard, [(None, threading.current_thread())])
        self.assertEqual(self.left, [])
        sent = worker.requests[0]
        self.assertEqual((sent["op"], sent["session"], sent["consent"], sent["resume"]),
                         ("chat.open", "w1", self.LEASE, True))

    def test_a_refused_open_and_a_lost_worker_end_the_start(self):
        for answer in ({"ok": False, "error": "This chat's file has another writer"},
                       WorkerUnavailable("The worker has ended")):
            with self.subTest(answer=answer):
                self.left.clear()
                lifecycle._hosted.pop("w1", None)   # The previous subtest's worker stopped (unbind is a fake here).
                controller, worker, heard = Chat(), WedgedWorker(answer), []
                self.open(controller, worker)
                lifecycle.when_open(controller, heard.append)
                self.finished(worker)
                self.assertEqual(self.left, [(controller, worker_routing.END_NOT_BRIEFED)])
                self.assertIsInstance(heard[0], lifecycle.ChatOpFailed)

    def test_the_local_phase_refuses_at_once_and_leaves_its_worker(self):
        refused = lifecycle.ChatOpFailed("chat.open", "w1", "another worker writes it")
        controller, worker = Chat(), WedgedWorker()
        with mock.patch.object(lifecycle, "_prepare", side_effect=refused), \
                self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.opened(controller, worker, resume=False, defer=True)
        self.assertEqual((worker.requests, self.left, lifecycle.opening()),
                         ([], [(controller, worker_routing.END_NOT_BRIEFED)], False))
        self.assertFalse(lifecycle.when_open(controller, lambda error: None))

    def test_off_nothing_is_sent_or_deferred(self):
        controller, worker = Chat(), WedgedWorker()
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"}):
            self.assertLess(self.open(controller, worker), AT_ONCE)
        self.assertEqual((worker.requests, lifecycle.opening()), ([], False))
        self.assertFalse(lifecycle.when_open(controller, lambda error: None))

    def test_a_waiter_that_fails_never_stops_the_others(self):
        first, second = (Chat("a"), WedgedWorker()), (Chat("b"), WedgedWorker())
        heard = []
        for controller, worker in (first, second):
            self.open(controller, worker)
        lifecycle.when_open(first[0], lambda error: 1 / 0)
        lifecycle.when_open(second[0], heard.append)
        first[1].release()
        self.finished(second[1])
        self.assertEqual(heard, [None])


class StartTests(Deferred):
    """``worker_sessions._adopt``: bound and shown at once, briefed once its open was answered."""

    def adopt(self, worker, chat):
        item = {"worker": SimpleNamespace(part="/w/1/part.blend", process=worker.process), "lead": self.lead,
                "part": {"name": "Sofa", "brief": "A sofa."}, "number": 1, "error": None, "references": []}
        with mock.patch.object(worker_sessions, "_new_chat", return_value=chat), \
                mock.patch.object(worker_routing, "adopt", lambda controller, process, defer=False: lifecycle.opened(
                    controller, worker, resume=False, defer=defer)), \
                mock.patch.object(parallel_sessions, "note_active"), \
                mock.patch.object(worker_queue, "current", return_value=True):
            begun = time.perf_counter()
            worker_sessions._adopt(item)
            return time.perf_counter() - begun

    def setUp(self):
        super().setUp()
        self.lead, self.sent = SimpleNamespace(state={"id": "lead-1", "messages": []}, references=None), []
        for patcher in (mock.patch.object(worker_sessions, "_send",
                                          lambda chat, text, key: self.sent.append((chat, text, key))),
                        mock.patch.dict(worker_sessions._started), mock.patch.dict(worker_sessions._parts),
                        mock.patch.dict(worker_sessions.failures), mock.patch.dict(parallel_sessions.names),
                        mock.patch.object(worker_sessions, "_released", [False])):
            patcher.start()
            self.addCleanup(patcher.stop)
        worker_sessions._started.pop("lead-1", None)

    def test_the_start_returns_at_once_and_briefs_once_the_worker_opened_the_chat(self):
        chat, worker = Chat(), WedgedWorker()
        self.assertLess(self.adopt(worker, chat), AT_ONCE)
        self.assertEqual(worker_sessions._started["lead-1"], ["w1"])   # Its tile and status show it starting.
        self.assertEqual(self.sent, [])   # No brief before the worker has the chat.
        self.finished(worker)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][1].startswith("Part: Sofa.\n\nA sofa."))
        self.assertEqual((self.sent[0][2], worker_sessions.failures), ("lead-1/1", {}))

    def test_a_start_whose_open_timed_out_is_stopped_told_and_reported_never_briefed(self):
        chat, worker = Chat(), WedgedWorker(WorkerUnavailable("no answer", timed_out=True))
        self.assertLess(self.adopt(worker, chat), AT_ONCE)
        self.finished(worker)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.left, [(chat, worker_routing.END_NOT_BRIEFED)])
        self.assertIn("no answer", worker_sessions.failures["lead-1/1"])   # Status: failed_to_start, with why.
        (kind, said), = chat.said
        self.assertEqual((kind, said["role"]), ("message", "error"))
        self.assertIn("could not start in its worker", said["text"])
        self.assertEqual(said["i18n"][0], lifecycle.OPEN_FAILED)   # Shown in the person's language.


class RelaunchTests(Deferred):
    """A parked chat's relaunch (``worker_queue._finish_relaunches``): its messages go once it opened again."""

    def relaunch(self, worker, chat, delivered, failed):
        item = {"session": "w1", "controller": chat, "worker": worker, "lead": None, "done": threading.Event(),
                "error": None, "deliveries": [(lambda: delivered.append(True), failed.append)]}
        item["done"].set()
        with mock.patch.object(worker_queue, "_relaunching", [item]), \
                mock.patch.object(worker_queue, "current", return_value=True), \
                mock.patch.object(worker_routing, "adopt", lambda controller, process, again=False, defer=False:
                                  lifecycle.opened(controller, worker, resume=again, defer=defer)), \
                mock.patch.object(worker_queue, "_resuming", {"w1": item}), \
                mock.patch.object(worker_queue, "parked", {"w1"}), \
                mock.patch.object(worker_queue, "_changed", lambda: None):
            begun = time.perf_counter()
            worker_queue._finish_relaunches(None)
            elapsed = time.perf_counter() - begun
            self.assertEqual(delivered, [])
            self.finished(worker)
            return elapsed, set(worker_queue.parked)

    def test_a_relaunch_delivers_only_after_its_open_and_a_failed_one_tells_the_messages(self):
        delivered, failed = [], []
        elapsed, parked = self.relaunch(WedgedWorker(), Chat(), delivered, failed)
        self.assertLess(elapsed, AT_ONCE)
        self.assertEqual((delivered, failed, parked), ([True], [], set()))
        delivered, failed = [], []
        _elapsed, parked = self.relaunch(WedgedWorker(WorkerUnavailable("no answer", timed_out=True)), Chat(),
                                         delivered, failed)
        self.assertEqual((delivered, len(failed)), ([], 1))
        self.assertIn("could not start again", str(failed[0]))


if __name__ == "__main__":
    unittest.main()
