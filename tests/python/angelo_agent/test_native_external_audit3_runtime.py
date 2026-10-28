# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The third external audit of A′ (7ea90a2ee3c), #1: launch records were removed by equality. Two launches for one
chat held equal records, so the one that finished first removed the other's, and a Stop then missed the launch still
under way: a paused prewarm started a live child after Stop, and its own cleanup raised ``ValueError``. Each record is
now its own (``runtime_service._Launch``, compared by identity).

The fake children never signal a real process or group: their pids are negative and ``RuntimeChild`` is patched with
``group=False`` (``test_native_runtime_bindings``).
"""

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.runtime_filter import Filter  # noqa: E402
from test_native_runtime_bindings import WS, A, Case, FakeProcess, chat  # noqa: E402


class OverlappingLaunches(Case):
    def setUp(self):
        super().setUp()
        self.spawned = []

        def spawn(command, env, cwd):
            process = FakeProcess()
            self.spawned.append(process)
            return process
        self.service.spawn = spawn
        self.hosted[A] = chat()

    def first_plan_held(self):
        """The first plan built pauses until ``gate`` opens (``entered`` once it is under way); later ones run."""
        entered, gate, plan, calls = threading.Event(), threading.Event(), self.service.plan, []

        def held(request, found):
            calls.append(request)
            if len(calls) == 1:
                entered.set()
                gate.wait(10)
            return plan(request, found)
        self.service.plan = held
        return entered, gate

    def overlap(self, slow, fast):
        """``slow`` paused in its plan while ``fast`` runs to its end, then Stop, then ``slow`` goes on: what each
        returned or raised."""
        entered, gate = self.first_plan_held()
        outcome = []

        def run():
            try:
                outcome.append(slow())
            except BaseException as error:   # noqa: BLE001 - a ValueError from cleanup is the regression.
                outcome.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(10))
        fast()
        self.service.kill_session(A)
        gate.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse([process for process in self.spawned if process.alive], "a child outlived Stop")
        self.assertFalse([child for child in self.service.children() if child.alive()])
        self.assertIsNone(self.service._warm)
        self.assertEqual(self.service._launching, [])
        self.assertEqual(self.service._starting, 0)
        return outcome[0]

    def launch(self):
        return self.service.launch({"op": "runtime.launch", "session": A}, self.hosted[A])

    def prewarm(self):
        return self.service.prewarm({"op": "runtime.launch", "session": A})

    def test_the_audit_repro_a_paused_prewarm_and_a_launch_then_stop_leaves_no_child(self):
        result = self.overlap(self.prewarm, self.launch)
        self.assertIs(result, False)   # Refused, never raised: its record was its own.
        self.assertEqual(len(self.spawned), 1)   # The launch's child, ended by Stop; the prewarm spawned none.

    def test_a_paused_launch_and_a_prewarm_then_stop_refuses_the_launch(self):
        self.assertIsInstance(self.overlap(self.launch, self.prewarm), RpcError)

    def test_two_overlapping_launches_then_stop_refuses_the_paused_one(self):
        self.assertIsInstance(self.overlap(self.launch, self.launch), RpcError)

    def test_two_overlapping_prewarms_then_stop_keeps_none_warm(self):
        self.assertIs(self.overlap(self.prewarm, self.prewarm), False)

    def test_equal_records_are_distinct(self):
        from angelo_agent.runtime_service import _Launch
        first, second = _Launch(A, ("conn-a",)), _Launch(A, ("conn-a",))
        records = [first, second]
        records.remove(second)
        self.assertEqual(records, [first])
        self.assertIs(records[0], first)
        self.assertNotIn(second, records)

    def test_without_stop_both_overlapping_launches_run(self):
        entered, gate = self.first_plan_held()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.launch()), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(10))
        results.append(self.launch())
        gate.set()
        thread.join(10)
        self.assertEqual([result["ok"] for result in results], [True, True])
        self.assertEqual(len([child for child in self.service.children() if child.alive()]), 2)
        self.assertEqual(self.service._launching, [])


class OneRequestPerIdInFlight(unittest.TestCase):
    """The same class in the relay: the worker's thread and account requests were remembered by their id, so a second
    with an id still in flight took the first one's answer (a fork's answer read as a start's could make a warm-up
    base of a forked conversation). A request reusing an id in flight is refused; once answered, the id is free."""

    def setUp(self):
        self.opened = []
        self.flt = Filter("codex", WS, model_provider="openai", recorded=lambda: ("known-thread",),
                          opened=self.opened.append)

    def send(self, message):
        return self.flt.inbound(json.dumps(message))

    def test_a_second_request_with_an_id_in_flight_is_refused(self):
        for second in ({"method": "thread/fork", "params": {"threadId": "known-thread"}},
                       {"method": "account/read", "params": {}}, {"method": "turn/start", "params": {}}):
            with self.subTest(second=second["method"]):
                self.setUp()
                self.assertTrue(self.send({"id": 1, "method": "thread/start", "params": {}}).passed)
                self.assertFalse(self.send(dict(second, id=1)).passed)
                self.flt.outbound(json.dumps({"id": 1, "result": {"thread": {"id": "fresh"}}}))
                self.assertEqual(self.opened, ["fresh"])
                self.assertIn("fresh", self.flt._started)   # The start's own answer, read as a start.

    def test_an_answered_id_may_be_used_again(self):
        self.assertTrue(self.send({"id": 1, "method": "thread/start", "params": {}}).passed)
        self.flt.outbound(json.dumps({"id": 1, "result": {"thread": {"id": "fresh"}}}))
        self.assertTrue(self.send({"id": 1, "method": "account/read", "params": {}}).passed)

    def test_ids_are_compared_as_json_values(self):
        self.assertTrue(self.send({"id": 1, "method": "thread/start", "params": {}}).passed)
        self.assertTrue(self.send({"id": "1", "method": "account/read", "params": {}}).passed)   # Another id.
        self.assertTrue(self.send({"method": "initialized"}).passed)   # A notification has no id.


if __name__ == "__main__":
    unittest.main()
