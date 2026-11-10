# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's steers reach a running request only at a safe step, and count as delivered only once read.

1.0.9 had no way to reach a running turn at all. Dependency-free: Claude's plan loop with mocked rounds and native
calls (``providers._run_plans``, as ``test_native_many_object_bindings``), Codex's ``turn/steer`` with a recording
transport and Codex's own events as the app-server sends them, and the turn's end (``chat_pending.turn_ended``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import steering_delivery  # noqa: E402
from angelo_agent.steering import DELIVERING, PAUSED, QUEUE, WAITING, Inbox  # noqa: E402
from angelo_agent.steering_delivery import STEER_HEADING  # noqa: E402


def inline(thread):
    thread.run()


class ClaudeRoundTests(unittest.TestCase):
    """Claude's typed-plan loop: a steer joins the next round's prompt, after every rewrite of it."""

    def provider(self, plans, results):
        from claude_protocol_fixtures import host
        from program_completion_fixtures import register_completion
        from angelo_agent.providers import ClaudeProvider
        context = host()
        context.inbox = Inbox()
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        self.prompts, rounds = [], iter(plans)

        def turn(native_id, text, references=(), *, owner, direct=None, steers=()):
            self.prompts.append(text)
            steering_delivery.shown(context, steers)   # The real round shows them once written.
            plan = next(rounds)
            if callable(plan):
                return plan()
            return dict(plan, verify=plan.get("verify", False))
        provider._turn = Mock(side_effect=turn)
        answers = iter(results)

        def native(name, arguments, **_kwargs):
            result = next(answers)
            register_completion(context, name, arguments, result)
            return result
        provider._native_call = Mock(side_effect=native)
        return provider, context

    @staticmethod
    def edit():
        from test_native_round_savings import program
        return {"reply": "", "operations": [program()], "verify": True}

    def test_a_steer_waits_for_the_next_round_and_is_read_after_the_results(self):
        from test_native_round_savings import committed
        provider, context = self.provider([self.edit(), {"reply": "Done", "operations": []}], [committed()])
        original = provider._native_call.side_effect

        def native(name, arguments, **kwargs):   # The person sends while the first plan's edit runs.
            context.inbox.hold("make the roof red too")
            return original(name, arguments, **kwargs)
        provider._native_call.side_effect = native
        self.assertEqual(provider.run(None, "Build a hut", observation={}), "completed")
        self.assertNotIn(STEER_HEADING, self.prompts[0], "Never the request's own round")
        self.assertTrue(self.prompts[1].endswith(STEER_HEADING + "\nmake the roof red too"),
                        "After the results JSON: the last thing the model reads")
        self.assertIn('"result"', self.prompts[1])
        self.assertEqual(context.inbox.items(), [], "Answered while current: delivered")
        shown = [data for kind, data in context.events if kind == "steer_shown"]
        self.assertEqual([data["text"] for data in shown], ["make the roof red too"])

    def test_a_queued_message_and_a_picture_stay_for_after_the_turn(self):
        from test_native_round_savings import committed
        provider, context = self.provider([self.edit(), {"reply": "Done", "operations": []}], [committed()])
        context.inbox.hold("then a lamp", steerable=False)
        context.inbox.hold("like this", [{"kind": "image", "path": "/r/a.png", "name": "a.png"}])
        provider.run(None, "Build a hut", observation={})
        self.assertFalse(any(STEER_HEADING in prompt for prompt in self.prompts))
        self.assertEqual([(item.mode, item.phase) for item in context.inbox.items()], [(QUEUE, WAITING)] * 2)

    def test_a_written_round_that_never_answered_keeps_the_steer_in_its_request(self):
        from test_native_round_savings import committed
        stopped = {}

        def stop():   # The person pressed Stop while the round carrying the steer ran.
            stopped["yes"] = True
            context.cancelled = True
            return None
        provider, context = self.provider([self.edit(), stop], [committed()])
        provider._current = lambda owner, transport=None: not context.cancelled
        context.inbox.hold("make the roof red too")
        provider.run(None, "Build a hut", observation={})
        self.assertTrue(stopped)
        item = context.inbox.items()[0]
        self.assertEqual((item.phase, item.shown), (DELIVERING, True), "Written into the round: the agent has it")
        self.assertEqual(context.inbox.turn_ended(False, "stopped"), [], "Its row is already up")
        self.assertEqual(context.inbox.items(), [], "Never sent again after Stop (break-it S2: twice in the context)")

    def test_the_model_gets_the_steer_redacted_as_every_message(self):
        from test_native_round_savings import committed
        provider, context = self.provider([self.edit(), {"reply": "Done", "operations": []}], [committed()])
        context.redact = lambda value: str(value).replace("sk-ant-secret", "[REDACTED]")
        original = provider._native_call.side_effect

        def native(name, arguments, **kwargs):
            context.inbox.hold("my key is sk-ant-secret")
            return original(name, arguments, **kwargs)
        provider._native_call.side_effect = native
        provider.run(None, "Build a hut", observation={})
        self.assertTrue(self.prompts[1].endswith(STEER_HEADING + "\nmy key is [REDACTED]"))
        self.assertNotIn("sk-ant-secret", self.prompts[1])

    def test_angelos_own_request_never_takes_the_persons_steer(self):
        for marker in ({"_host_request": []}, {"_external_task": True}):
            context = SimpleNamespace(**dict({"inbox": Inbox(), "cancelled": False, "_host_request": None}, **marker))
            item = context.inbox.hold("make it red")
            self.assertEqual(steering_delivery.take(context), [], marker)
            self.assertEqual(item.phase, WAITING, "It waits for the person's next request")


class FakeScope:
    def __init__(self):
        self.lock = threading.RLock()
        self.completed, self.starting, self.responses = False, False, {}
        self.turn_id, self.thread_id = "turn-1", "thread-1"
        self.context = None

    def current(self, context):
        return context is self.context


class Transport:
    def __init__(self, error=None):
        self.requests, self.error = [], error

    def request(self, method, params=None, timeout=60):
        self.requests.append((method, params))
        if self.error:
            raise self.error
        return {"turnId": params["expectedTurnId"]}


class CodexSteerTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(cancelled=False, inbox=Inbox(), events=[])
        self.context.emit = lambda kind, data=None: self.context.events.append((kind, data))
        self.scope = FakeScope()
        self.scope.context = self.context
        self.rpc = Transport()
        self.provider = SimpleNamespace(_scope=self.scope, controller=self.context, rpc=self.rpc, steer_ready=True)

    def offer(self):
        return steering_delivery.codex_offer(self.provider, start=inline)

    def test_a_steer_goes_into_the_open_turn_as_the_persons_own_message(self):
        item = self.context.inbox.hold("use oak, not pine")
        self.offer()
        method, params = self.rpc.requests[0]
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params, {"threadId": "thread-1", "expectedTurnId": "turn-1", "clientUserMessageId": item.client_id,
                                  "input": [{"type": "text", "text": "use oak, not pine"}]}, "Raw words: no wrapper")
        self.assertEqual((item.phase, item.accepted), (DELIVERING, True), "Accepted is not read yet")
        self.assertEqual(self.context.events, [])

    def test_never_while_a_host_call_is_outstanding_or_without_angelos_runtime(self):
        self.context.inbox.hold("use oak")
        self.scope.responses = {"7": object()}
        self.offer()
        self.provider.steer_ready, self.scope.responses = False, {}
        self.offer()
        self.assertEqual(self.rpc.requests, [], "A call still runs, or the person's own Codex (no hold): it waits")
        self.assertEqual(self.context.inbox.items()[0].phase, WAITING)

    def test_read_only_once_its_user_item_shows_and_the_model_goes_on(self):
        item = self.context.inbox.hold("use oak")
        self.offer()
        steering_delivery.codex_event(self.context, "item/agentMessage/delta", {"delta": "Still pine"})
        self.assertEqual(self.context.inbox.items(), [item], "Output before its user item: not read yet")
        steering_delivery.codex_event(self.context, "item/started", {"item": {
            "type": "userMessage", "id": "u1", "clientId": item.client_id, "content": []}})
        self.assertEqual(self.context.inbox.items(), [item])
        steering_delivery.codex_event(self.context, "item/started", {"item": {"type": "reasoning", "id": "r1"}})
        self.assertEqual(self.context.inbox.items(), [])
        self.assertEqual(self.context.events, [("steer_shown", {"id": item.id, "text": "use oak"})],
                         "Its row goes in just before the model's output")

    def test_a_refused_steer_waits_again_or_pauses_after_stop(self):
        from angelo_agent.protocol import RpcError
        self.rpc.error = RpcError("no active turn to steer")
        item = self.context.inbox.hold("use oak")
        self.offer()
        self.assertEqual((item.phase, item.accepted), (WAITING, False), "Sent as the next message instead")
        self.context.cancelled = True
        self.context.inbox.take_steers()   # Taken again, then the turn was stopped before Codex answered.
        self.context.inbox.returned([item], "stopped" if self.context.cancelled else None)
        self.assertEqual((item.phase, item.reason), (PAUSED, "stopped"))

    def test_the_codex_steer_is_redacted_and_a_slow_answer_is_never_offered_twice(self):
        self.context.redact = lambda value: str(value).replace("sk-secret", "[REDACTED]")
        self.rpc.error = TimeoutError()
        item = self.context.inbox.hold("key sk-secret")
        self.offer()
        self.assertEqual(self.rpc.requests[0][1]["input"], [{"type": "text", "text": "key [REDACTED]"}])
        self.assertEqual((item.phase, item.accepted), (DELIVERING, True), "Codex may hold it: settled by its echo")
        self.rpc.error = None
        self.offer()
        self.assertEqual(len(self.rpc.requests), 1, "Never written again (break-it S1: Codex does not dedupe)")

    def test_a_read_steer_marks_where_a_plan_turns_messages_were_answered(self):
        plan = SimpleNamespace(messages=[("m1", "{}")], steered=0)
        item = self.context.inbox.hold("use oak")
        self.offer()
        steering_delivery.codex_event(self.context, "item/started", {"item": {
            "type": "userMessage", "id": "u1", "clientId": item.client_id, "content": []}}, plan)
        steering_delivery.codex_event(self.context, "item/agentMessage/delta", {"delta": "{"}, plan)
        self.assertEqual(plan.steered, 1, "Only messages after it can supersede the final plan")

    def test_a_plan_correction_turn_reaches_codex_only_once_codex_echoes_its_message(self):
        provider = SimpleNamespace(controller=self.context)
        statuses = iter(["interrupted", "completed"])

        def plan_turn(native_id, text, turns):   # The first never wrote (Stop before turn/start); the second did.
            status = next(statuses)
            if status == "completed":
                steering_delivery.codex_event(self.context, "item/started", {"item": {"type": "userMessage", "id": "u"}})
                steering_delivery.codex_event(self.context, "item/agentMessage/delta", {"delta": "{"})
            return status
        provider.plan_turn = plan_turn
        item = self.context.inbox.hold("use oak")
        steering_delivery.plan_turn(provider, "thread", "Correct the plan.", 1)
        self.assertEqual((item.phase, self.context.events), (WAITING, []), "Never written: back, no row (round 2 S1)")
        steering_delivery.plan_turn(provider, "thread", "Correct the plan.", 1)
        self.assertEqual(self.context.inbox.items(), [])
        self.assertEqual(self.context.events, [("steer_shown", {"id": item.id, "text": "use oak"})])

    def test_a_carried_flag_never_outlives_its_correction_turn(self):
        """Break-it round 3: a steer stopped before Codex echoed its correction turn, later steered again, must not
        be read on another message's echo (an interrupt would then lose it)."""
        item = self.context.inbox.hold("make it blue")
        taken = self.context.inbox.take_steers()
        self.context.inbox.mark(taken, carried=True)   # steering_delivery.plan_turn
        self.context.cancelled = True
        steering_delivery.settle(self.context, taken, False)
        self.context.inbox.turn_ended(False, "stopped")
        self.assertEqual((item.phase, item.carried), (PAUSED, False))
        self.context.cancelled = False
        self.context.inbox.resume(item.id)
        self.context.inbox.mark(self.context.inbox.take_steers(), accepted=True)   # turn/steer accepted, not drained
        steering_delivery.codex_event(self.context, "item/started", {"item": {"type": "userMessage", "id": "u"}})
        steering_delivery.codex_event(self.context, "item/started", {"item": {"type": "reasoning", "id": "r"}})
        self.assertIn(item, self.context.inbox.items(), "Another message's echo is no evidence for this one")

    def test_codex_echoing_a_user_message_counts_even_after_stop(self):
        self.assertTrue(steering_delivery.echo("item/started", {"item": {"type": "userMessage", "clientId": "x"}}))
        self.assertFalse(steering_delivery.echo("item/started", {"item": {"type": "agentMessage"}}))
        self.assertFalse(steering_delivery.echo("item/completed", {"item": {"type": "userMessage"}}))
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/providers.py").read_text()
        event = source[source.index("        if self._scope.starting:\n            self._scope.defer(\"event\""):]
        echo_at = event.index("steering_delivery.echo(method, params)")
        self.assertLess(echo_at, event.index("if not self._scope.matches(context, params, allow_cancelled=method == "),
                        "Read before a stopped request's events are dropped")
        self.assertIn("allow_cancelled=True", event[echo_at:echo_at + 200])

    def test_a_write_that_failed_outright_gives_the_steer_back(self):
        self.rpc.error = UnicodeError("never written")
        item = self.context.inbox.hold("use oak")
        self.offer()
        self.assertEqual((item.phase, item.accepted), (WAITING, False))

    def test_nothing_is_offered_into_a_stopped_or_finished_turn(self):
        self.context.inbox.hold("use oak")
        self.context.cancelled = True
        self.offer()
        self.context.cancelled, self.scope.completed = False, True
        self.offer()
        self.assertEqual(self.rpc.requests, [])


class TurnEndTests(unittest.TestCase):
    """``chat_pending.turn_ended``: what reached the agent stays the request's (with its row), the rest comes back."""

    def controller(self):
        controller = SimpleNamespace(inbox=Inbox(), lock=threading.RLock(), turn_running=lambda: True, busy=False,
                                     state={"messages": []}, redact=str)   # Its turn thread is still closing.
        controller._change_row = lambda index, **fields: controller.state["messages"][index].update(fields)
        controller._append_row = lambda row: controller.state["messages"].append(row)
        return controller

    def test_only_a_steer_codex_echoed_reached_it_an_accepted_one_goes_first_after_the_turn(self):
        from angelo_agent import chat_pending
        controller = self.controller()
        accepted, echoed = controller.inbox.hold("use oak"), controller.inbox.hold("use elm")
        queued = controller.inbox.hold("then a lamp", steerable=False)
        taken = controller.inbox.take_steers()
        controller.inbox.mark(taken, accepted=True)
        controller.inbox.mark(taken[1:], seen=True)   # Codex echoed only this one (drained or recorded).
        chat_pending.turn_ended(controller, "completed", cancelled=False, not_sent=False)
        self.assertEqual(controller.inbox.next_due(), [accepted, queued],
                         "No echo, no evidence: it goes first after the turn (break-it round 2 B1: never lost)")
        self.assertEqual([(m["text"], m["steer"]) for m in controller.state["messages"]], [("use elm", True)])

    def test_after_a_failure_an_accepted_steer_never_echoed_pauses_and_a_queued_one_waits(self):
        from angelo_agent import chat_pending
        controller = self.controller()   # The turn thread is still closing its runtime (B2): handled all the same.
        accepted, queued = controller.inbox.hold("use oak"), controller.inbox.hold("then a lamp", steerable=False)
        controller.inbox.mark(controller.inbox.take_steers(), accepted=True)
        chat_pending.turn_ended(controller, "failed", cancelled=False, not_sent=False)
        self.assertEqual([(i.phase, i.reason) for i in (accepted, queued)], [(PAUSED, "failed")] * 2)
        self.assertEqual(controller.inbox.next_due(), [], "Nothing sends by itself after a failure")

    def test_a_relaunch_brings_back_what_codex_saw_but_never_answered(self):
        inbox = Inbox()
        written, seen = inbox.hold("use oak"), inbox.hold("use elm")
        taken = inbox.take_steers()
        inbox.mark(taken[:1], shown=True)
        inbox.mark(taken[1:], accepted=True, seen=True)
        inbox.turn_ended(False, "relaunch")
        self.assertEqual([(i.text, i.phase) for i in inbox.items()], [("use elm", PAUSED)],
                         "Its row was never shown: the person decides; a written Claude round keeps its row")

    def test_stop_keeps_what_reached_the_agent_and_pauses_what_codex_cleared(self):
        from angelo_agent import chat_pending
        controller = self.controller()
        written, cleared, drained = (controller.inbox.hold(text) for text in ("use oak", "use ash", "use elm"))
        taken = controller.inbox.take_steers()
        controller.inbox.mark(taken[:1], shown=True)   # A Claude round carried it (its row is up).
        controller.inbox.mark(taken[1:], accepted=True)   # Codex took both; it drained only the last.
        controller.inbox.mark(taken[2:], seen=True)
        controller.state["messages"] = [{"role": "user", "text": "Build"},
                                        {"role": "user", "text": "use oak", "steer": True, "steer_id": written.id}]
        chat_pending.turn_ended(controller, "interrupted", cancelled=True, not_sent=False)
        self.assertEqual(controller.inbox.items(), [cleared], "Only what Stop cleared from Codex comes back")
        self.assertEqual((cleared.phase, cleared.reason), (PAUSED, "stopped"))
        self.assertEqual([m["text"] for m in controller.state["messages"] if m.get("steer")], ["use oak", "use elm"])
        self.assertFalse(any(m.get("withdrawn") for m in controller.state["messages"]))
        self.assertEqual(controller.inbox.next_due(), [], "Nothing sends by itself after Stop")

    def test_a_failure_pauses_too_and_an_escalation_rerun_keeps_them(self):
        from angelo_agent import chat_pending
        controller = self.controller()
        item = controller.inbox.hold("use oak")
        chat_pending.turn_ended(controller, "failed", cancelled=False, not_sent=False)
        self.assertEqual((item.phase, item.reason), (PAUSED, "failed"))
        other = self.controller()
        waiting = other.inbox.hold("use oak")
        other.busy = True   # Auto asked a stronger model: the request runs again.
        chat_pending.turn_ended(other, "escalated", cancelled=False, not_sent=False)
        self.assertEqual(waiting.phase, WAITING)


if __name__ == "__main__":
    unittest.main()
