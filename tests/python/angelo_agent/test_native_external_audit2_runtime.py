# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The second external audit of A′ (e837c972c40), #3: a launch was cancellable only once it registered, just before
its spawn, so a Stop or a connection's end while its plan was still being built missed it and the prewarm left a
live child. A launch is now cancellable from the moment it is asked, at every stage.

The fake children never signal a real process or group: their pids are negative and ``RuntimeChild`` is patched with
``group=False`` (``test_native_runtime_bindings``).
"""

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.protocol import RpcError  # noqa: E402
from test_native_runtime_bindings import A, B, Case, FakeProcess, chat  # noqa: E402


class CancelledWhilePlanning(Case):
    def setUp(self):
        super().setUp()
        self.spawned = []

        def spawn(command, env, cwd):
            process = FakeProcess()
            self.spawned.append(process)
            return process
        self.service.spawn = spawn

    def held_plan(self):
        """The service's plan, paused until ``gate`` opens (``entered`` once it is under way)."""
        entered, gate, plan = threading.Event(), threading.Event(), self.service.plan

        def held(request, found):
            entered.set()
            gate.wait(10)
            return plan(request, found)
        self.service.plan = held
        return entered, gate

    def run_held(self, start, cancel):
        entered, gate = self.held_plan()
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
        self.assertFalse([process for process in self.spawned if process.alive], "a child outlived its cancel")
        self.assertFalse([child for child in self.service.children() if child.alive()])
        return outcome[0]

    def launch_of(self, session):
        return lambda: self.service.launch({"op": "runtime.launch", "session": session}, self.hosted[session])

    def prewarm_of(self, session):
        return lambda: self.service.prewarm({"op": "runtime.launch", "session": session})

    def test_the_audit_poc_a_stop_during_a_prewarms_plan_leaves_no_child(self):
        self.hosted[A] = chat()
        self.assertFalse(self.run_held(self.prewarm_of(A), lambda: self.service.kill_session(A)))
        self.assertIsNone(self.service._warm)

    def test_the_audit_poc_a_connection_retired_during_a_prewarms_plan_leaves_no_child(self):
        from angelo_agent import runtime_bindings
        self.hosted[A] = chat()
        self.assertFalse(self.run_held(self.prewarm_of(A), lambda: runtime_bindings.end_connection("conn-a")))
        self.assertIsNone(self.service._warm)

    def test_every_end_during_a_launchs_plan_refuses_it(self):
        from angelo_agent import runtime_bindings
        cancels = {"stop": lambda: self.service.kill_session(A), "close": lambda: self.service.close(),
                   "retire": lambda: runtime_bindings.end_connection("conn-a"),
                   "chat closed": lambda: self.hosted.pop(A)}
        for name, cancel in cancels.items():
            with self.subTest(end=name):
                spawn, self.service = self.service.spawn, self.make()
                self.service.spawn = spawn
                self.hosted[A] = chat()
                self.assertIsInstance(self.run_held(self.launch_of(A), cancel), RpcError)

    def test_a_prewarm_whose_chat_closed_while_planning_keeps_nothing(self):
        self.hosted[A] = chat()
        self.assertFalse(self.run_held(self.prewarm_of(A), lambda: self.hosted.pop(A)))
        self.assertIsNone(self.service._warm)

    def test_another_chats_stop_leaves_a_planning_launch_alone(self):
        self.hosted[A], self.hosted[B] = chat(), chat(connection="conn-b", key="key-of-connection-b-01")
        entered, gate = self.held_plan()
        result = []
        thread = threading.Thread(target=lambda: result.append(self.launch_of(A)()), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(10))
        self.service.kill_session(B)
        gate.set()
        thread.join(10)
        self.assertTrue(result and result[0]["ok"])
        self.assertTrue(self.spawned[0].alive)

    def test_no_launch_is_left_registered_after_it_returns(self):
        self.hosted[A] = chat()
        self.launch_of(A)()
        self.assertTrue(self.prewarm_of(A)())
        with self.assertRaises(RpcError):
            self.service.launch({"op": "runtime.launch", "session": B}, chat())   # Not hosted: refused.
        self.assertEqual(self.service._launching, [])
        self.assertEqual(self.service._starting, 0)


if __name__ == "__main__":
    unittest.main()
