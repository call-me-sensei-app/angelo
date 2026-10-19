# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open Codex turns: orphaned turns are interrupted and a new message never joins an older turn.

codex app-server steers a ``turn/start`` into a thread's open turn
(``codex_stale_call_probe.py``). These host tests drive ``codex_turns.OpenTurns``
and the real ``CodexProvider`` with an in-memory RPC: no subprocess or inference.
"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import time  # noqa: E402

from angelo_agent.codex_turns import MAX_COMPLETED, MAX_TURNS, STUCK, OpenTurns, TurnStartAbandoned  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.providers import CodexProvider  # noqa: E402

THREAD = "native-thread"


class RPC:
    def __init__(self, on_request=None):
        self.closed = threading.Event()
        self.requests, self.responses = [], []
        self.on_request = on_request or (lambda method, parameters: {})

    def request(self, method, parameters, **kwargs):
        self.requests.append((method, parameters))
        return self.on_request(method, parameters)

    def respond(self, identity, payload):
        self.responses.append((identity, payload))

    def close(self):
        self.closed.set()


def interrupt(turn):
    return ("turn/interrupt", {"threadId": THREAD, "turnId": turn})


class OpenTurnsTests(unittest.TestCase):
    def test_turns_open_and_close_from_codex_messages_whoever_owns_them(self):
        turns = OpenTurns()
        turns.observe("turn/started", {"threadId": THREAD, "turn": {"id": "t1"}})
        turns.observe("item/tool/call", {"threadId": THREAD, "turnId": "t2", "callId": "c"})
        turns.observe("turn/started", {"threadId": "other-thread", "turn": {"id": "t3"}})
        self.assertEqual(turns.open_on(THREAD), ["t1", "t2"])
        turns.observe("turn/completed", {"threadId": THREAD, "turn": {"id": "t1", "status": "interrupted"}})
        self.assertEqual(turns.open_on(THREAD), ["t2"])
        turns.observe("host/disconnected", {})
        self.assertEqual((turns.open_on(THREAD), turns.open_on("other-thread")), ([], []))

    def test_a_completion_seen_before_the_start_reply_is_not_reopened(self):
        turns = OpenTurns()
        turns.observe("turn/completed", {"threadId": THREAD, "turn": {"id": "fast"}})
        turns.opened(THREAD, "fast")
        self.assertEqual(turns.open_on(THREAD), [])

    def test_malformed_identities_and_bounds(self):
        turns = OpenTurns()
        for params in (None, {}, {"threadId": THREAD}, {"threadId": THREAD, "turnId": ""}, {"threadId": 7, "turnId": "x"}):
            turns.observe("turn/started", params)
        self.assertEqual(turns.open_on(THREAD), [])
        for index in range(MAX_TURNS + 5):
            turns.opened(THREAD, "t%d" % index)
        self.assertEqual(len(turns.open_on(THREAD)), MAX_TURNS)
        for index in range(MAX_COMPLETED + 5):
            turns.completed("done%d" % index)
        self.assertLessEqual(len(turns._completed), MAX_COMPLETED)

    def test_interrupt_is_asynchronous_once_and_only_for_open_turns(self):
        turns, rpc = OpenTurns(), RPC()
        self.assertFalse(turns.interrupt(rpc, THREAD, "unknown"))
        turns.opened(THREAD, "t1")
        self.assertTrue(turns.interrupt(rpc, THREAD, "t1"))
        self.assertFalse(turns.interrupt(rpc, THREAD, "t1"))
        self.assertFalse(turns.interrupt(rpc, "other-thread", "t1"))
        turns.opened(THREAD, "t2")
        turns.interrupting("t2")  # Stop already sent its own interrupt.
        self.assertFalse(turns.interrupt(rpc, THREAD, "t2"))
        turns.join()
        self.assertEqual(rpc.requests, [interrupt("t1")])

    def test_interrupt_failures_are_contained(self):
        turns = OpenTurns()
        turns.opened(THREAD, "t1")
        def failing(method, parameters):
            raise RpcError("Agent process has disconnected")
        self.assertTrue(turns.interrupt(RPC(failing), THREAD, "t1"))
        turns.join()

    def test_settle_interrupts_older_turns_and_waits_for_their_completion(self):
        turns = OpenTurns()
        turns.opened(THREAD, "old")
        turns.opened("other-thread", "elsewhere")
        rpc = RPC(lambda method, parameters: turns.completed(parameters["turnId"]) or {})
        self.assertTrue(turns.settle(rpc, THREAD, timeout=5))
        turns.join()
        self.assertEqual(rpc.requests, [interrupt("old")])
        self.assertEqual(turns.open_on("other-thread"), ["elsewhere"])

    def test_settle_is_bounded_when_a_turn_never_ends(self):
        turns, rpc = OpenTurns(), RPC()
        turns.opened(THREAD, "dead")
        self.assertFalse(turns.settle(rpc, THREAD, timeout=0.3))
        turns.join()
        self.assertEqual(rpc.requests, [interrupt("dead")])

    def test_start_sends_only_after_older_turns_end_and_records_the_new_turn(self):
        turns, order = OpenTurns(), []
        turns.opened(THREAD, "old")

        def answer(method, parameters):
            order.append(method)
            if method == "turn/interrupt":
                turns.completed(parameters["turnId"])
                return {}
            self.assertEqual(turns.open_on(THREAD), [], "a new message is sent only once no older turn is open")
            return {"turn": {"id": "new"}}

        provider = type("Provider", (), {"close": lambda self: order.append("close")})()
        response = turns.start(provider, RPC(answer), {"threadId": THREAD, "input": []})
        self.assertEqual(response, {"turn": {"id": "new"}})
        self.assertEqual(order, ["turn/interrupt", "turn/start"])
        self.assertEqual(turns.open_on(THREAD), ["new"])

    def test_start_closes_the_connection_instead_of_sending_into_a_dead_turn(self):
        turns, closed = OpenTurns(), []
        turns.settle_seconds = 0.3
        turns.opened(THREAD, "dead")
        rpc = RPC()
        provider = type("Provider", (), {"close": lambda self: closed.append(True)})()
        with self.assertRaisesRegex(RpcError, "did not end"):
            turns.start(provider, rpc, {"threadId": THREAD, "input": []})
        turns.join()
        self.assertEqual(closed, [True])
        self.assertNotIn("turn/start", [method for method, _ in rpc.requests])
        self.assertEqual(STUCK.count("not sent"), 1)


class StopDuringGuardTests(unittest.TestCase):
    def test_stop_ends_the_wait_at_once_and_sends_nothing(self):
        turns, rpc, closed = OpenTurns(), RPC(), []
        turns.opened(THREAD, "dead")  # Never completes: the guard would wait settle_seconds.
        context = type("Context", (), {"cancelled": False})()
        provider = type("Provider", (), {"close": lambda self: closed.append(True)})()
        outcome = []

        def guarded():
            started = time.monotonic()
            try:
                turns.start(provider, rpc, {"threadId": THREAD, "input": []}, context)
            except TurnStartAbandoned as error:
                outcome.append((time.monotonic() - started, str(error)))

        worker = threading.Thread(target=guarded)
        worker.start()
        time.sleep(.3)
        context.cancelled = True
        stopped_at = time.monotonic()
        turns.wake()  # What Stop does (CodexTurnScope.interrupt_ids).
        worker.join(5)
        turns.join()
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertLess(time.monotonic() - stopped_at, 1.0, "Stop ends the 15 s wait at once")
        self.assertNotIn("turn/start", [method for method, _ in rpc.requests])
        self.assertEqual(rpc.requests, [interrupt("dead")])  # The older turn's interrupt stands.
        self.assertEqual(closed, [], "the warm connection stays open")
        self.assertEqual(turns.open_on(THREAD), ["dead"])  # Until Codex completes it.

    def test_a_stopped_request_sends_nothing_even_with_no_older_turn(self):
        turns, rpc = OpenTurns(), RPC()
        context = type("Context", (), {"cancelled": True})()
        with self.assertRaises(TurnStartAbandoned):
            turns.start(type("Provider", (), {"close": lambda self: None})(), rpc, {"threadId": THREAD}, context)
        self.assertEqual(rpc.requests, [])


class ProviderTurnGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = Controller(self.temp.name)
        self.addCleanup(self.c.close)
        self.c.set_document(("turn-guard", 3))
        self.c.state["native_id"] = THREAD
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.context = self.c.runtime_context()
        self.p = CodexProvider(self.context, {})
        self.addCleanup(self.p.close)
        self.p.rpc = RPC()
        self.p._transport_token = object()
        self.p.thread_id = THREAD
        self.p.bind_request(self.context)

    def event(self, method, params):
        self.p._event(method, params, transport=self.p._transport_token)

    def test_next_message_never_joins_an_older_open_turn(self):
        # The previous request ended while Codex still had its turn open.
        self.event("turn/started", {"threadId": THREAD, "turn": {"id": "turn-old"}})
        order = []

        def answer(method, parameters):
            order.append((method, parameters.get("turnId")))
            if method == "turn/interrupt":
                self.event("turn/completed", {"threadId": THREAD, "turn": {"id": "turn-old", "status": "interrupted"}})
                return {}
            self.assertEqual(self.p._scope.turns.open_on(THREAD), [])
            self.event("turn/completed", {"threadId": THREAD, "turn": {"id": "turn-new", "status": "completed"}})
            return {"turn": {"id": "turn-new"}}

        self.p.rpc.on_request = answer
        self.assertEqual(self.p.run(THREAD, "A new human message"), "completed")
        self.assertEqual(order, [("turn/interrupt", "turn-old"), ("turn/start", None)])
        self.assertEqual(self.p._scope.turns.open_on(THREAD), [])

    def test_stop_during_the_guard_abandons_the_send_and_keeps_the_connection(self):
        self.event("turn/started", {"threadId": THREAD, "turn": {"id": "turn-old"}})
        rpc, outcome = self.p.rpc, []

        def run():
            try:
                outcome.append(self.p.run(THREAD, "A message the person then stops"))
            except TurnStartAbandoned as error:
                outcome.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while interrupt("turn-old") not in rpc.requests and time.monotonic() < deadline:
            time.sleep(.01)
        stopped_at = time.monotonic()
        self.c.stop()  # The real Stop: cancelled, then stop_request on a daemon thread.
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - stopped_at, 1.0)
        self.assertIsInstance(outcome[0], TurnStartAbandoned)
        self.assertNotIn("turn/start", [method for method, _ in rpc.requests])
        self.assertEqual(rpc.requests.count(interrupt("turn-old")), 1)
        self.assertIs(self.p.rpc, rpc)
        self.assertFalse(rpc.closed.is_set())
        self.assertTrue(self.p.has_warm_runtime())
        # Clean afterwards: Codex completes the interrupted turn, and the next request sends a fresh turn.
        self.event("turn/completed", {"threadId": THREAD, "turn": {"id": "turn-old", "status": "interrupted"}})
        self.c.cancelled = False
        self.c._begin_metrics()
        context = self.c.runtime_context()
        self.p.bind_request(context)

        def answer(method, parameters):
            self.event("turn/completed", {"threadId": THREAD, "turn": {"id": "turn-next", "status": "completed"}})
            return {"turn": {"id": "turn-next"}}

        rpc.requests.clear()
        rpc.on_request = answer
        self.assertEqual(self.p.run(THREAD, "The message sent again"), "completed")
        self.assertEqual([method for method, _ in rpc.requests], ["turn/start"])
        self.assertEqual(self.p.turn_id, "turn-next")

    def test_a_turn_that_never_ends_restarts_the_connection_without_sending(self):
        self.event("turn/started", {"threadId": THREAD, "turn": {"id": "turn-dead"}})
        self.p._scope.turns.settle_seconds = 0.3
        rpc = self.p.rpc
        with self.assertRaisesRegex(RpcError, "did not end"):
            self.p.run(THREAD, "A new human message")
        self.p._scope.turns.join()
        self.assertEqual(rpc.requests, [interrupt("turn-dead")])
        self.assertTrue(rpc.closed.is_set())
        self.assertIsNone(self.p.rpc)
        self.assertFalse(self.p.has_warm_runtime())
        self.assertEqual(self.p._scope.turns.open_on(THREAD), [], "the closed process's turns are forgotten")

    def test_voice_turns_are_not_recorded(self):
        self.context.voice = object()
        self.event("turn/started", {"threadId": THREAD, "turn": {"id": "voice-turn"}})
        self.assertEqual(self.p._scope.turns.open_on(THREAD), [])


if __name__ == "__main__":
    unittest.main()
