# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hold judges a Codex call by the step that wrote it, as the bundled runtime names it (model_steps).

The runtime numbers a turn's model requests from 0 and names, in each ``item/tool/call``, the request that wrote
the call (``modelStep.writtenIn``: for an exec cell's call, the request that wrote the cell), the turn's latest
(``current``) and the cell (``execCellId``). Two classes it closes, each exposed by one recorded limit
(agent_execution.rst, B19) and checked here for every shape of it, not the exposing case:

* an exec cell that yields (``yield_control``, its yield pragma or the default 30 s) keeps running into the next
  step; its later edit was written before the model saw what its read showed, so it stays refused;
* a provider that reports no usage ended a step only at the turn's end; the runtime's announcement of each model
  request (``turn/modelRequestStarted``) and the next request's calls now end it, and a turn that announces its
  requests no longer counts usage reports (a compaction reports usage too, which made holds wait longer).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, HANDLE, program
from angelo_agent import model_steps
from angelo_agent.refusal_codes import RefusalError
from angelo_agent.request_recovery import RecoveryBarrier


def ran(handles, code="STALE_REVISION"):
    """A refusal of an edit that may have run (its commit started)."""
    return {"error": "refused", "error_code": code, "partial_changes_possible": True, "mutation_started": True,
            "refusal_details": {"handles": list(handles)}}


def refused(barrier, step=None):
    """Whether a scene edit written in ``step`` (None: the current one) is refused by the hold."""
    try:
        barrier.check("angelo_scene_program", program(), step=step)
    except RefusalError as error:
        return error.code == "RECOVERY_REQUIRED"
    return False


def held(turn="turn-1"):
    """A barrier holding after a refused edit, with the turn's request 0 having read every target (its cell)."""
    barrier = RecoveryBarrier()
    barrier.refused("angelo_scene_program", program(), ran([HANDLE]))
    barrier.call_step(turn, 0, 0)  # The read's own call: written in request 0, while it is the latest.
    barrier.read("angelo_scene_object", {}, {"object": HANDLE})
    return barrier


class YieldedCell(unittest.TestCase):
    def test_a_yielded_cells_later_edit_keeps_its_cells_step(self):
        for reason in ("yield_control", "yield pragma", "default 30 s"):  # One mechanism: the exec call resolves.
            barrier = held()
            barrier.step("turn-1")  # The cell yielded: request 0's usage report.
            self.assertTrue(refused(barrier, barrier.call_step("turn-1", 0, 1)), reason)
            self.assertTrue(refused(barrier, barrier.call_step("turn-1", 0, 4)), reason)  # However late it runs.
            self.assertFalse(refused(barrier, barrier.call_step("turn-1", 1, 1)), reason)  # Request 1 saw it.

    def test_a_cells_edit_after_the_informed_edit_closed_the_hold_is_still_refused(self):
        barrier = held()
        barrier.step("turn-1")
        self.assertFalse(refused(barrier, barrier.call_step("turn-1", 1, 1)))  # Closes the hold.
        self.assertIsNone(barrier.pending)
        with self.assertRaises(RefusalError) as caught:
            barrier.check("angelo_scene_program", program(), step=barrier.call_step("turn-1", 0, 1))
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        self.assertIn("changes", caught.exception.details["recovery"])
        self.assertFalse(refused(barrier, barrier.call_step("turn-1", 1, 1)))
        self.assertFalse(refused(barrier))  # A call the runtime names no step for: as before, the current step.

    def test_reads_and_other_tools_are_never_held_by_a_cells_step(self):
        barrier = held()
        barrier.step("turn-1")
        barrier.check("angelo_scene_object", {"object": HANDLE}, step=barrier.call_step("turn-1", 0, 1))

    def test_a_read_that_completes_after_its_cell_yielded_counts_from_the_request_then_running(self):
        barrier = RecoveryBarrier()
        barrier.refused("angelo_scene_program", program(), ran([HANDLE]))
        barrier.call_step("turn-1", 0, 0)
        barrier.step("turn-1")  # The cell yields before its read answers; request 1 runs.
        barrier.read("angelo_scene_object", {}, {"object": HANDLE})
        self.assertTrue(refused(barrier, barrier.call_step("turn-1", 1, 1)))  # Sent before the read answered.
        self.assertFalse(refused(barrier, barrier.call_step("turn-1", 2, 2)))


class NoUsage(unittest.TestCase):
    """A provider that reports no usage: app-server sends no ``thread/tokenUsage/updated``, so no ``step``."""

    def test_the_next_requests_call_ends_the_step(self):
        barrier = held()
        self.assertTrue(refused(barrier, barrier.call_step("turn-1", 0, 0)))  # Same request: refused.
        self.assertFalse(refused(barrier, barrier.call_step("turn-1", 1, 1)))  # The next request: admitted.

    def test_a_call_of_a_later_request_moves_the_count_for_every_later_check(self):
        barrier = held()
        before = barrier.steps
        barrier.call_step("turn-1", 2, 3)  # Requests up to 3 have begun.
        self.assertEqual(barrier.steps, before + 3)

    def test_usage_reports_and_the_numbering_agree(self):
        barrier = RecoveryBarrier()
        self.assertEqual(barrier.call_step("turn-1", 0, 0), 0)
        barrier.step("turn-1")  # Request 0's usage.
        self.assertEqual(barrier.call_step("turn-1", 1, 1), 1)
        self.assertEqual(barrier.steps, 1)  # Counted once, not twice.


class Turns(unittest.TestCase):
    def test_a_new_turns_requests_come_after_every_earlier_step(self):
        barrier = held()
        barrier.step("turn-1")  # Its turn ended (provider_usage on turn/completed).
        self.assertFalse(refused(barrier, barrier.call_step("turn-2", 0, 0)))

    def test_a_turn_first_heard_of_by_its_usage_report_starts_before_it(self):
        barrier = RecoveryBarrier()
        barrier.step("turn-1")
        barrier.step("turn-2")  # Request 0 of turn-2 ended.
        self.assertEqual(barrier.call_step("turn-2", 1, 1), 2)

    def test_a_late_call_of_an_ended_turn_keeps_that_turns_numbering(self):
        barrier = held()
        barrier.step("turn-1")
        barrier.call_step("turn-2", 0, 0)
        self.assertTrue(refused(barrier, barrier.call_step("turn-1", 0, 1)))

    def test_the_numbering_kept_is_bounded_and_never_forgets_a_turn(self):
        barrier = held("turn-0")
        barrier.step("turn-0")
        for index in range(1, model_steps.MAX_TURNS + 10):
            barrier.call_step("turn-%d" % index, 0, 0)
        self.assertEqual(len(barrier.turns), model_steps.MAX_TURNS)
        self.assertIsNone(barrier.call_step("turn-late", 0, 0), "past the bound: the step it is checked in")
        # The first turn is still known: its yielded cell's late edit keeps its own step (never re-registered high).
        self.assertTrue(refused(barrier, barrier.call_step("turn-0", 0, 1)))

    def test_a_new_request_forgets_every_turn_and_closed_hold(self):
        barrier = held()
        barrier.step("turn-1")
        barrier.check("angelo_scene_program", program(), step=barrier.call_step("turn-1", 1, 1))
        barrier.request_started("turn-1", 2)
        barrier.clear()
        self.assertEqual((barrier.steps, len(barrier.turns), barrier.closed_hold, barrier.announced),
                         (0, 0, None, set()))


class Announcements(unittest.TestCase):
    """``turn/modelRequestStarted``: the runtime announces each request before building its input."""

    def test_a_read_that_completes_after_a_later_request_began_counts_from_that_request(self):
        # No usage reports at all; the cell that read yielded while its read was pending, and two requests began.
        barrier = RecoveryBarrier()
        barrier.refused("angelo_scene_program", program(), ran([HANDLE]))
        barrier.request_started("turn-1", 0)
        barrier.call_step("turn-1", 0, 0)  # The read, written in request 0.
        barrier.request_started("turn-1", 1)
        barrier.request_started("turn-1", 2)
        barrier.read("angelo_scene_object", {}, {"object": HANDLE})
        self.assertTrue(refused(barrier, barrier.call_step("turn-1", 2, 2)), "request 2 began before the read")
        self.assertFalse(refused(barrier, barrier.call_step("turn-1", 3, 3)))

    def test_usage_reports_of_an_announcing_turn_end_no_step(self):
        # A compaction reports usage too: counted, it made every later hold of the turn wait longer.
        barrier = RecoveryBarrier()
        barrier.refused("angelo_scene_program", program(), ran([HANDLE]))
        barrier.request_started("turn-1", 0)
        barrier.call_step("turn-1", 0, 0)
        barrier.read("angelo_scene_object", {}, {"object": HANDLE})
        for _ in range(3):
            barrier.step("turn-1")
        self.assertTrue(refused(barrier, barrier.call_step("turn-1", 0, 0)))
        barrier.request_started("turn-1", 1)
        self.assertFalse(refused(barrier, barrier.call_step("turn-1", 1, 1)), "request 1 had the read")

    def test_a_turn_end_still_ends_a_step_and_a_runtime_that_announces_nothing_counts_usage(self):
        barrier = RecoveryBarrier()
        barrier.request_started("turn-1", 0)
        barrier.step("turn-1", ended=True)
        self.assertEqual(barrier.steps, 1)
        barrier.step("turn-2")  # Never announced: its usage report ends a step, as before.
        self.assertEqual(barrier.steps, 2)

    def test_values_the_runtime_never_sends_announce_nothing(self):
        barrier = RecoveryBarrier()
        for turn, request in ((None, 0), ("t", -1), ("t", True), ("t", "1"), ("t", 1.0)):
            barrier.request_started(turn, request)
        self.assertEqual((barrier.steps, barrier.announced), (0, set()))

    def test_only_the_lead_requests_turn_announcements_count(self):
        from angelo_agent.provider_usage import CodexThreadUsage
        host = SimpleNamespace(recovery_barrier=RecoveryBarrier())
        scope = SimpleNamespace(matches=lambda context, params: params.get("turnId") == "turn-1")
        params = {"threadId": "t", "turnId": "turn-1", "request": 2}
        lead = SimpleNamespace(host=host, cancelled=False, voice=None,
                               record_request_started=lambda turn, request: model_steps.record_request_started(
                                   lead, turn, request))
        collaborator = SimpleNamespace(host=host, cancelled=False, voice=None)  # DelegatedContext has none.
        for context, other in ((collaborator, params), (lead, dict(params, turnId="turn-0")),
                               (lead, dict(params, threadId="other"))):
            handled = CodexThreadUsage().observe_event("turn/modelRequestStarted", other, context, thread_id="t",
                                                       scope=scope, accepted_input=lambda context: None)
            self.assertTrue(handled)
            self.assertEqual(host.recovery_barrier.steps, 0)
        CodexThreadUsage().observe_event("turn/modelRequestStarted", params, lead, thread_id="t", scope=scope,
                                         accepted_input=lambda context: None)
        self.assertEqual(host.recovery_barrier.steps, 2)


class Values(unittest.TestCase):
    def test_values_the_runtime_never_sends_name_no_step(self):
        barrier = RecoveryBarrier()
        for turn, written_in, current in ((None, 0, 0), ("t", -1, 0), ("t", 2, 1), ("t", True, 1), ("t", 0, 1.0),
                                          ("t", "0", 1)):
            self.assertIsNone(barrier.call_step(turn, written_in, current), (turn, written_in, current))
        self.assertEqual((barrier.steps, len(barrier.turns)), (0, 0))

    def test_only_the_lead_requests_own_calls_name_a_step(self):
        host = SimpleNamespace(recovery_barrier=RecoveryBarrier())
        params = {"turnId": "t", "modelStep": {"writtenIn": 0, "current": 0, "execCellId": "1"}}
        lead = SimpleNamespace(host=host, cancelled=False, voice=None)
        self.assertEqual(model_steps.record_call_step(lead, params), 0)
        for context in (SimpleNamespace(host=host, cancelled=True, voice=None),
                        SimpleNamespace(host=host, cancelled=False, voice=object())):
            self.assertIsNone(model_steps.record_call_step(context, params))
        for missing in ({"turnId": "t"}, {"turnId": "t", "modelStep": None}, {"turnId": "t", "modelStep": [0, 0]}):
            self.assertIsNone(model_steps.record_call_step(lead, missing))


class RPC:
    def __init__(self):
        self.closed, self.responses = threading.Event(), []

    def request(self, method, parameters, **kwargs):
        return {}

    def respond(self, identity, value):
        self.responses.append((identity, value))

    def write(self, message):
        self.responses.append((message.get("id"), message))

    def close(self):
        self.closed.set()


class CodexDelivery(AgentRuntimeFixture, unittest.TestCase):
    """Through the real Codex delivery and admission: the step travels from ``item/tool/call`` to the check."""

    def provider(self):
        from angelo_agent.providers import CodexProvider
        self.c.state["native_id"] = "thread-1"
        context = self.c.runtime_context()
        provider = CodexProvider(context, {})
        provider.rpc, provider.thread_id = RPC(), "thread-1"
        provider.bind_request(context)
        provider._scope.bind_turn("turn-1")
        self.c.provider = provider
        return context, provider

    def call(self, provider, identity, tool, arguments, written_in, current):
        provider._request(identity, "item/tool/call", {
            "threadId": "thread-1", "turnId": "turn-1", "callId": identity, "tool": tool, "arguments": arguments,
            "modelStep": {"writtenIn": written_in, "current": current, "execCellId": "7"}})

    def test_a_yielded_cells_edit_is_refused_and_the_next_requests_edit_admitted(self):
        context, provider = self.provider()
        _answers, first = self.enqueue(identity="first")
        self.c.finish_tool(first, ran([HANDLE]))
        with patch.object(self.executor, "execute", return_value={"object": HANDLE, "object_revision": "b" * 64}):
            self.call(provider, "read", "angelo_scene_object", {"object": HANDLE}, 0, 0)
            for _ in range(10):
                self.pump()
                if provider.rpc.responses:
                    break
        self.assertEqual([identity for identity, _value in provider.rpc.responses], ["read"])
        context.record_model_step("turn-1")  # The cell yielded: request 0's usage report.
        self.call(provider, "cell-edit", "angelo_scene_program", program(), 0, 1)
        identity, value = provider.rpc.responses[-1]
        self.assertEqual(identity, "cell-edit")
        self.assertFalse(value["success"])
        self.assertIn("RECOVERY_REQUIRED", str(value["contentItems"]))
        self.assertFalse(self.executor.jobs)
        answered = len(provider.rpc.responses)
        self.call(provider, "next-edit", "angelo_scene_program", program(), 1, 1)
        self.assertEqual(len(provider.rpc.responses), answered, "admitted: no immediate refusal")
        self.assertIsNotNone(self.c.pending.get(next(key for key in self.c.pending if key.endswith("/next-edit"))))

    def test_without_usage_the_next_requests_call_ends_the_step(self):
        context, provider = self.provider()
        _answers, first = self.enqueue(identity="first")
        self.c.finish_tool(first, ran([HANDLE]))
        with patch.object(self.executor, "execute", return_value={"object": HANDLE, "object_revision": "b" * 64}):
            self.call(provider, "read", "angelo_scene_object", {"object": HANDLE}, 0, 0)
            for _ in range(10):
                self.pump()
                if provider.rpc.responses:
                    break
        self.call(provider, "same-request-edit", "angelo_scene_program", program(), 0, 0)
        self.assertIn("RECOVERY_REQUIRED", str(provider.rpc.responses[-1][1]["contentItems"]))
        answered = len(provider.rpc.responses)
        self.call(provider, "next-request-edit", "angelo_scene_program", program(), 1, 1)  # No usage report came.
        self.assertEqual(len(provider.rpc.responses), answered, "admitted: no immediate refusal")


if __name__ == "__main__":
    unittest.main()
