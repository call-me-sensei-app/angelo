# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No window frame waits on a worker (design A′, U6 of the 2026-10-27 UI review, and the concurrency review's note on
main-thread blocking). No Blender.

A wedged worker (its requests never answered until the test lets them) behind the real transport
(``worker_chat_wiring.send_op`` → ``worker_chat_lifecycle.send_op``) and the lanes' real threads. Pinned: Send, the
option ops, a model pick, the consent toggle, a needs-approval Send and the merge's release all return at once; a
late answer is reconciled by its request id and applied once; a Send whose answer never came is shown sending, never
as failed, until the worker confirms it or ends (then it comes back unsent, with its attachments); a Stop before the
Send left its lane returns it at once and the worker never gets it; merge holds share one deadline.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (worker_chat_lifecycle as lifecycle, worker_chat_wiring as wiring,  # noqa: E402
                          worker_consent_lead, worker_mirror_events, worker_mirror_ops)
from angelo_agent.worker_process import WorkerUnavailable  # noqa: E402
from test_native_worker_mirror import Fixture, TURN  # noqa: E402

AT_ONCE = 0.05   # Seconds: far below any worker bound (5 s ops, 2 s consent, 10 s holds).


def until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class WedgedWorker:
    """A live worker whose requests block until ``release`` (then answered ``answer``), each one recorded."""

    def __init__(self):
        self.gate, self.lock, self.requests, self.answer, self.code = threading.Event(), threading.Lock(), [], None, None
        self.process = SimpleNamespace(pid=9, poll=lambda: self.code)

    def alive(self):
        return self.code is None

    def request(self, op, timeout, **fields):
        with self.lock:
            self.requests.append(dict(fields, op=op))
        if not self.gate.wait(10):
            raise WorkerUnavailable("no answer within %g s" % timeout, timed_out=True)
        answer = self.answer(op) if callable(self.answer) else self.answer
        if isinstance(answer, BaseException):
            raise answer
        return answer or {"ok": True}

    def ops(self):
        with self.lock:
            return [request["op"] for request in self.requests]

    def release(self):
        self.gate.set()


def timed(function, *args, **kwargs):
    begun = time.perf_counter()
    result = function(*args, **kwargs)
    return time.perf_counter() - begun, result


class Wedged(Fixture):
    def setUp(self):
        super().setUp()
        worker_mirror_ops.reset()   # The lanes' real threads (the mirror fixture runs them inline).
        self.worker_process = WedgedWorker()
        self.addCleanup(self.worker_process.release)
        self.mirror.send_op = wiring.send_op(self.worker_process)
        for patcher in (mock.patch.object(lifecycle, "_hosted", {self.session: self.worker_process}),
                        mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        worker_consent_lead.reset()
        self.addCleanup(worker_consent_lead.reset)
        worker_consent_lead.bind(send=lambda session, request, timeout: self.worker_process.request(
            request["op"], timeout, **{k: v for k, v in request.items() if k != "op"}),
            kill=lambda session: None, connection=lambda controller: True)
        worker_consent_lead.register(self.mirror)

    def settle(self, predicate):
        self.assertTrue(until(lambda: (worker_mirror_ops.reconcile(), predicate())[1]))


class EveryPathReturnsAtOnce(Wedged):
    def test_send_options_a_model_pick_and_consent_never_wait_on_a_wedged_worker(self):
        self.mirror.state.update(provider="codex_api", connection_id="conn")
        self.mirror._pins.update(provider="codex_api", connection_id="conn")   # The chat's route, as Angelo pinned it.
        models = [{"id": "m1", "default": True, "efforts": [], "default_effort": "", "fast_tier": False}]
        paths = [(self.mirror.send, ("Make a chair",), {}),
                 (self.mirror.set_options, (), {"effort": "high"}),
                 (self.mirror.choose_connection, ("codex_api", {}, "", "conn", models, "m1"), {}),
                 (worker_consent_lead.changed, (), {}),
                 (worker_consent_lead.tick, (), {})]
        for function, args, kwargs in paths:
            self.mirror.state["options"]["approval"] = "ASK" if function is worker_consent_lead.changed else \
                self.mirror.state["options"]["approval"]
            seconds, _result = timed(function, *args, **kwargs)
            self.assertLess(seconds, AT_ONCE, function.__name__)
        self.assertTrue(until(lambda: "chat.send" in self.worker_process.ops()))
        self.assertTrue(self.mirror.busy)
        self.assertEqual(self.mirror.status, worker_mirror_ops.SENDING)

    def test_a_send_in_a_chat_that_needs_approval_never_waits_for_its_consent(self):
        worker_consent_lead._needs.add(self.session)
        seconds, _result = timed(self.mirror.send, "Again")
        self.assertLess(seconds, AT_ONCE)
        self.assertTrue(until(lambda: "chat.consent" in self.worker_process.ops()))
        self.assertNotIn("chat.send", self.worker_process.ops())   # Only once the consent is back.
        self.worker_process.answer = lambda op: {"ok": True, "epoch": self.worker_process.requests[-1].get("epoch")}
        self.worker_process.release()
        self.assertTrue(until(lambda: "chat.send" in self.worker_process.ops()))

    def test_a_merge_release_never_waits_and_holds_share_one_deadline(self):
        sessions = ["h1", "h2", "h3"]
        lifecycle._hosted.update({session: self.worker_process for session in sessions})
        with mock.patch.object(lifecycle, "HOLD_SECONDS", 0.2), mock.patch.object(lifecycle, "_lease",
                                                                                   lambda path, wait: None):
            begun = time.perf_counter()
            with self.assertRaises(Exception):
                with lifecycle.merge_guard(sessions, []):
                    self.fail("nothing is merged without its holds")
            refused_after = time.perf_counter() - begun
        self.assertLess(refused_after, 0.2 * 2)   # One deadline for all three, never one per chat.
        self.worker_process.release()
        self.assertTrue(until(lambda: self.worker_process.ops().count("chat.release") == 3))
        held = WedgedWorker()   # Answers its hold at once; its release is wedged.
        self.addCleanup(held.release)
        held.request = lambda op, timeout, original=held.request, **fields: (
            {"ok": True} if op == "chat.hold" else original(op, timeout, **fields))
        lifecycle._hosted["h4"] = held
        with lifecycle.merge_guard(["h4"], []):
            begun = time.perf_counter()
        self.assertLess(time.perf_counter() - begun, AT_ONCE)   # Its release is sent, not waited for.
        self.assertTrue(until(lambda: held.ops() == ["chat.release"]))


class AnswersReconciled(Wedged):
    def test_a_late_answer_confirms_the_send_once(self):
        self.mirror.send("Make a chair")
        request = self.mirror._sending
        self.assertTrue(until(lambda: "chat.send" in self.worker_process.ops()))
        self.assertEqual(self.worker_process.requests[-1]["request_id"], request.id)
        self.worker_process.release()
        self.settle(lambda: self.mirror._sending is None)
        self.assertTrue(self.mirror.busy)   # Confirmed: the worker's turn events show the rest.
        self.mirror.busy = False
        worker_mirror_ops._answers.append((request, {"error": "a duplicate, refused"}))   # The same id again.
        self.assertFalse(worker_mirror_ops.reconcile())
        self.assertIsNone(self.mirror.unsent_draft)
        self.assertFalse(any(m.get("role") == "error" for m in self.mirror.state["messages"]))

    def test_a_send_not_answered_in_time_is_never_shown_failed_and_runs_as_its_worker_shows(self):
        self.worker_process.answer = WorkerUnavailable("no answer within 300 s", timed_out=True)
        self.mirror.send("Make a chair")
        self.worker_process.release()
        self.assertTrue(until(lambda: worker_mirror_ops._answers or self.mirror._sending.settled))
        worker_mirror_ops.reconcile()
        self.assertTrue(self.mirror.busy)
        self.assertEqual(self.mirror.status, worker_mirror_ops.SENDING)
        self.assertIsNone(self.mirror.unsent_draft)
        self.assertFalse(any(m.get("role") == "error" for m in self.mirror.state["messages"]))
        self.worker_step(("message", {"role": "user", "text": "Make a chair"}))   # The worker took it after all.
        self.frame("finished", dict({"status": "completed"}, **TURN))
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        self.assertIsNone(self.mirror.unsent_draft)

    def test_a_send_whose_worker_ended_first_comes_back_with_its_attachments(self):
        reference = {"path": "/tmp/ref.png", "name": "ref.png"}
        self.mirror.attachments = [dict(reference)]
        self.mirror.send("Use this")
        self.assertTrue(until(lambda: "chat.send" in self.worker_process.ops()))
        self.worker_process.code = 1   # It ended before it answered.
        worker_mirror_events.put(self.mirror, worker_mirror_events.RECOVERED, {"state": None, "finished": None})
        self.mirror.pump_events()
        self.assertEqual((self.mirror.busy, self.mirror.unsent_draft, self.mirror.attachments),
                         (False, "Use this", [reference]))
        self.worker_process.answer = WorkerUnavailable("The worker ended before it answered")
        self.worker_process.release()
        self.assertTrue(until(lambda: bool(worker_mirror_ops._answers)))
        worker_mirror_ops.reconcile()   # Its late answer settles nothing twice.
        self.assertEqual(self.mirror.attachments, [reference])
        self.assertFalse(any(m.get("role") == "error" for m in self.mirror.state["messages"]))

    def test_a_stop_before_the_send_left_its_lane_returns_it_and_the_worker_never_gets_it(self):
        self.mirror.set_options(effort="high")   # In flight: the worker is wedged on it.
        self.assertTrue(until(lambda: "chat.options" in self.worker_process.ops()))
        self.mirror.send("Make a chair")
        seconds, _result = timed(self.mirror.stop)
        self.assertLess(seconds, AT_ONCE)
        self.assertEqual((self.mirror.busy, self.mirror.unsent_draft), (False, "Make a chair"))
        self.worker_process.release()
        self.assertTrue(until(lambda: not self.mirror._op_lane.running))
        self.assertNotIn("chat.send", self.worker_process.ops())


if __name__ == "__main__":
    unittest.main()
