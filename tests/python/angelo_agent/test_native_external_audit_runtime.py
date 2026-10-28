# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The external audit of A′ (c3c9a14a1fd), the relay's findings: #2 a pooled runtime moved to another chat kept the
first chat's threads, #3 a launch in flight outlived ``close`` and Stop's ``kill_session``.

Each class is one finding's reproduction and failed while the finding stood. The fake children never signal a real
process or group: their pids are negative, ``RuntimeChild`` is patched with ``group=False`` and their ``kill`` sends
no signal (``test_native_runtime_bindings``).
"""

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.protocol import RpcError  # noqa: E402
from test_native_runtime_bindings import A, B, Case, FakeProcess, chat  # noqa: E402


class ReusedRuntimeForgetsTheFirstChatsThreads(Case):
    """#2: chat A opened a thread; its pooled app-server then served chat B on the same connection, and B's resume of
    A's thread was forwarded unchanged (``_known`` took the child's own threads before B's record)."""

    def a_thread_then_b(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        self.send(child, {"id": 7, "method": "thread/start", "params": {}})
        child.filter.outbound(json.dumps({"id": 7, "result": {"thread": {"id": "a-private"}}}))
        del self.hosted[A]       # Chat A closes; the worker's pool keeps its app-server.
        self.hosted[B] = chat()  # The same connection and key: the pool hands it to chat B.
        return child

    def thread_of(self, verdict):
        self.assertTrue(verdict.passed)
        return json.loads(verdict.line)["params"]["threadId"]

    def test_the_audit_poc_bs_resume_of_as_thread_never_reaches_it(self):
        child = self.a_thread_then_b()
        for number, method in enumerate(("thread/resume", "thread/fork"), 8):   # One id in flight once each.
            with self.subTest(method=method):
                verdict = self.send(child, {"id": number, "method": method, "params": {"threadId": "a-private"}})
                self.assertNotEqual(self.thread_of(verdict), "a-private")
        self.assertEqual(child.session, B)

    def test_b_gets_no_turn_on_as_thread_still_loaded_in_the_child(self):
        child = self.a_thread_then_b()
        verdict = self.send(child, {"id": 9, "method": "turn/start", "params": {
            "threadId": "a-private", "input": [{"type": "text", "text": "hello"}]}})
        self.assertNotEqual(self.thread_of(verdict), "a-private")

    def test_an_answer_to_as_request_is_not_recorded_as_bs_thread(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        self.send(child, {"id": 7, "method": "thread/start", "params": {}})   # A's, unanswered when A closes.
        del self.hosted[A]
        self.hosted[B] = chat()
        self.send(child, {"id": 1, "method": "thread/start", "params": {}})   # B claims the child.
        child.filter.outbound(json.dumps({"id": 7, "result": {"thread": {"id": "a-late"}}}))
        verdict = self.send(child, {"id": 8, "method": "thread/resume", "params": {"threadId": "a-late"}})
        self.assertNotEqual(self.thread_of(verdict), "a-late")

    def test_b_keeps_its_own_threads_and_a_keeps_its_record(self):
        child = self.a_thread_then_b()
        self.send(child, {"id": 10, "method": "thread/start", "params": {}})
        child.filter.outbound(json.dumps({"id": 10, "result": {"thread": {"id": "b-own"}}}))
        for thread in ("b-own",):
            verdict = self.send(child, {"id": 11, "method": "thread/resume", "params": {"threadId": thread}})
            self.assertEqual(self.thread_of(verdict), thread)
        self.assertTrue(self.send(child, {"id": 12, "method": "turn/start", "params": {
            "threadId": "b-own", "input": [{"type": "text", "text": "hi"}]}}).passed)
        self.hosted[A] = chat()   # A reopens on a child of its own: its thread is still its own.
        reopened = self.launch(A, self.make())
        verdict = self.send(reopened, {"id": 5, "method": "thread/resume", "params": {"threadId": "a-private"}})
        self.assertEqual(self.thread_of(verdict), "a-private")


class LaunchInFlightEndsWithItsService(Case):
    """#3: ``close`` and ``kill_session`` ended the children registered when they ran; a launch still spawning
    registered its child after them, which lived on (a prewarm until its 10-minute expiry)."""

    def held_spawn(self):
        entered, gate, spawned = threading.Event(), threading.Event(), []

        def spawn(command, env, cwd):
            entered.set()
            gate.wait(10)
            process = FakeProcess()
            spawned.append(process)
            return process
        self.service.spawn = spawn
        return entered, gate, spawned

    def run_held(self, start, cancel):
        entered, gate, spawned = self.held_spawn()
        outcome = []

        def run():
            try:
                outcome.append(start())
            except RpcError as error:
                outcome.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(10))
        cancel()
        gate.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(spawned), 1)
        self.assertFalse(spawned[0].alive, "the child spawned during the cancel still runs")
        self.assertFalse([child for child in self.service.children() if child.alive()])
        return outcome[0]

    def test_close_during_a_launch_ends_its_child_and_refuses_the_launch(self):
        self.hosted[A] = chat()
        outcome = self.run_held(lambda: self.service.launch({"op": "runtime.launch", "session": A}, self.hosted[A]),
                                self.service.close)
        self.assertIsInstance(outcome, RpcError)

    def test_close_during_a_prewarm_leaves_no_warm_child(self):
        self.hosted[A] = chat()
        outcome = self.run_held(lambda: self.service.prewarm({"op": "runtime.launch", "session": A}),
                                self.service.close)
        self.assertFalse(outcome)
        self.assertIsNone(self.service._warm)

    def test_stop_during_a_launch_of_its_chat_ends_that_child(self):
        self.hosted[A] = chat()
        outcome = self.run_held(lambda: self.service.launch({"op": "runtime.launch", "session": A}, self.hosted[A]),
                                lambda: self.service.kill_session(A))
        self.assertIsInstance(outcome, RpcError)

    def test_a_connection_ended_during_a_launch_ends_that_child(self):
        from angelo_agent import runtime_bindings
        self.hosted[A] = chat()
        outcome = self.run_held(lambda: self.service.launch({"op": "runtime.launch", "session": A}, self.hosted[A]),
                                lambda: runtime_bindings.end_connection("conn-a"))
        self.assertIsInstance(outcome, RpcError)

    def test_another_chats_stop_leaves_the_launch_alone(self):
        self.hosted[A], self.hosted[B] = chat(), chat(connection="conn-b", key="key-of-connection-b-01")
        entered, gate, spawned = self.held_spawn()
        result = []
        thread = threading.Thread(target=lambda: result.append(self.service.launch(
            {"op": "runtime.launch", "session": A}, self.hosted[A])), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(10))
        self.service.kill_session(B)
        gate.set()
        thread.join(10)
        self.assertTrue(result and result[0]["ok"])
        self.assertTrue(spawned[0].alive)


if __name__ == "__main__":
    unittest.main()
