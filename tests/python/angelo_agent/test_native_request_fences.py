# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline production transport/routing admission; no subprocess or inference."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.providers import CodexProvider
from angelo_agent.request_fences import RequestFence


class RecordingRPC:
    def __init__(self):
        self.closed = threading.Event()
        self.responses, self.requests = [], []

    def respond(self, identity, payload):
        self.responses.append((identity, payload))

    def request(self, method, parameters, **kwargs):
        self.requests.append((method, parameters))
        return {}

    def close(self):
        self.closed.set()


class Executor:
    def __init__(self):
        self.calls = []

    def precondition(self, name, arguments):
        return "same-scene"

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return {"success": True, "objects": []}


class RequestFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 17))
        self.c.state["native_id"] = "native-thread"
        self.next_request()
        self.p = CodexProvider(self.context, {})
        self.p.rpc = RecordingRPC()
        self.p.thread_id = "native-thread"
        self.p.bind_request(self.context)
        self.p.turn_id = "turn-a"
        self.p._scope.bind_turn("turn-a")

    def tearDown(self):
        self.p.close()
        self.c.close()
        self.temp.cleanup()

    def next_request(self):
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.context = self.c.runtime_context()
        return self.context

    def bind_next(self):
        old = self.context
        self.next_request()
        self.p.bind_request(self.context)
        self.p.turn_id = "turn-b"
        self.p._scope.bind_turn("turn-b")
        return old

    def request(self, turn="turn-a", thread="native-thread", identity="native-call"):
        self.p._request(identity, "item/tool/call", {"threadId": thread, "turnId": turn,
            "callId": identity, "tool": "angelo_create_cube", "arguments": {
                "name": "Fence Cube", "size": 1, "location": [0, 0, 0]}})

    def test_old_context_cannot_revive_when_next_turn_clears_cancelled(self):
        old = self.context
        old.begin({"model": "old-model"}, [], [])
        self.c.cancelled = True
        self.next_request()
        self.assertIsNot(old, self.context)
        self.assertTrue(old.cancelled)
        self.assertEqual(old.turn_options["model"], "old-model")
        replies = []
        old.request_tool("late-new-id", "angelo_inspect_scene", {}, replies.append)
        self.assertIn("error", replies[0])
        self.assertFalse(self.c.pending)

    def test_old_native_turn_and_wrong_or_missing_ids_are_rejected(self):
        self.bind_next()
        self.request("turn-a", identity="old-unseen-id")
        self.request("turn-b", "another-thread", "wrong-thread")
        self.request(None, identity="missing-turn")
        self.assertEqual(len(self.p.rpc.responses), 3)
        self.assertTrue(all(payload["success"] is False for _, payload in self.p.rpc.responses))
        self.assertFalse(self.c.pending)
        self.assertFalse(self.c.state["invocations"]["entries"])

    def test_old_completion_cannot_finish_new_turn(self):
        self.bind_next()
        self.p._event("turn/completed", {"threadId": "native-thread", "turn": {"id": "turn-a", "status": "completed"}})
        self.assertFalse(self.p.done.is_set())
        self.assertIsNone(self.p.turn_status)
        self.p._event("turn/completed", {"threadId": "native-thread", "turn": {"id": "turn-b", "status": "completed"}})
        self.assertTrue(self.p.done.is_set())
        self.assertEqual(self.p.turn_status, "completed")

    def test_queued_old_generation_events_cannot_change_new_request_or_timing(self):
        self.context.emit("finished", {"status": "completed"})
        self.bind_next()
        self.c.emit("delta", {"item": "late", "text": "old text", "_epoch": self.c._epoch,
                              "_generation": self.c._request_generation - 1})
        self.c.pump_events()
        self.assertTrue(self.c.busy)
        self.assertNotIn("first_text_ms", self.c._request_metrics.snapshot()["phases"])
        self.assertFalse(any(message.get("text") == "old text" for message in self.c.state["messages"]))

    def test_completion_after_stop_still_wakes_only_its_cancelled_turn(self):
        self.c.cancelled = True
        self.p._event("turn/completed", {"threadId": "native-thread", "turn": {"id": "turn-a", "status": "interrupted"}})
        self.assertTrue(self.p.done.is_set())
        self.assertEqual(self.p.turn_status, "interrupted")

    def test_delayed_stop_cannot_interrupt_the_next_turn(self):
        old_fence = RequestFence.capture(self.c)
        self.bind_next()
        self.p.stop_request(old_fence)
        self.assertFalse(self.p.rpc.requests)
        self.p.stop_request(self.context.request_fence)
        self.assertEqual(self.p.rpc.requests, [("turn/interrupt", {"threadId": "native-thread", "turnId": "turn-b"})])

    def test_early_messages_wait_for_authoritative_start_reply(self):
        rpc = self.p.rpc
        def start(method, parameters, **kwargs):
            self.assertEqual(method, "turn/start")
            self.p._event("item/agentMessage/delta", {"threadId": "native-thread", "turnId": "turn-a",
                "itemId": "old", "delta": "late old text"})
            self.p._request("early-tool", "item/tool/call", {"threadId": "native-thread", "turnId": "turn-new",
                "callId": "early-tool", "tool": "angelo_inspect_scene", "arguments": {}})
            self.assertFalse(self.c.pending)
            self.p._event("item/agentMessage/delta", {"threadId": "native-thread", "turnId": "turn-new",
                "itemId": "new", "delta": "accepted new text"})
            self.p._event("turn/completed", {"threadId": "native-thread", "turn": {"id": "turn-new", "status": "completed"}})
            return {"turn": {"id": "turn-new"}}
        rpc.request = start
        self.assertEqual(self.p.run("native-thread", "Offline fixture"), "completed")
        self.assertEqual(len(self.c.pending), 1)
        executor = Executor()
        self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        self.c.pump_events()
        # Its completion also arrived before dispatch. Binding the start reply
        # admits the queued call, but that closed turn cannot execute it later.
        self.assertEqual(len(executor.calls), 0)
        self.assertFalse(rpc.responses)
        text = " ".join(message["text"] for message in self.c.state["messages"])
        self.assertIn("accepted new text", text)
        self.assertNotIn("late old text", text)

    def test_callback_captures_original_rpc_not_replacement_transport(self):
        original = self.p.rpc
        self.p._request("read", "item/tool/call", {"threadId": "native-thread", "turnId": "turn-a",
            "callId": "read", "tool": "angelo_inspect_scene", "arguments": {}})
        call = next(iter(self.c.pending.values()))
        self.bind_next()
        replacement = self.p.rpc = RecordingRPC()
        self.c.finish_tool(call, {"success": True, "objects": []})
        self.assertFalse(original.responses)  # No late write to the retired original transport either.
        self.assertFalse(replacement.responses)
        self.assertIn(call.id, self.c.state["results"])

    def test_old_start_reply_cannot_replace_a_new_binding_with_the_same_ids(self):
        successor = []
        def start(method, parameters, **kwargs):
            self.assertEqual(method, "turn/start")
            self.p._scope.begin(self.context, "native-thread", starting=False,
                                closed_with=self.p.rpc.closed)
            self.p._scope.bind_turn("same-turn")
            successor.append(self.p._scope.delivery_owner)
            return {"turn": {"id": "same-turn"}}
        self.p.rpc.request = start
        self.assertEqual(self.p.run("native-thread", "Old start"), "interrupted")
        self.assertIs(self.p._scope.delivery_owner, successor[0])
        self.assertTrue(successor[0].current(self.c))
        self.assertTrue(self.context.delivery_owner.current(self.c))

    def test_retired_transport_disconnect_cannot_fail_new_transport(self):
        old, new = object(), object()
        self.p._transport_token = new
        self.p._event("host/disconnected", {}, transport=old)
        self.assertFalse(self.p.done.is_set())
        self.p._event("host/disconnected", {}, transport=new)
        self.assertTrue(self.p.done.is_set())
        self.assertEqual(self.p.turn_status, "failed")

    def test_close_wakes_current_waiter_without_accepting_retired_disconnect_event(self):
        self.p.close()
        self.assertTrue(self.p.done.is_set())
        self.assertEqual(self.p.turn_status, "interrupted")
        self.assertIsNone(self.p.rpc)

    def test_startup_queue_is_bounded_and_overflow_tool_is_refused(self):
        self.p._scope.begin(self.context, "native-thread")
        for index in range(65):
            self.request(identity="queued-" + str(index))
        self.assertEqual(len(self.p._scope.pending), 64)
        self.assertFalse(self.c.pending)
        self.assertEqual(len(self.p.rpc.responses), 1)
        self.assertFalse(self.p.rpc.responses[0][1]["success"])

    def test_successive_realtime_turns_keep_activation_fence_and_exact_turn_tools(self):
        class Voice:
            def __init__(self):
                self.events = []
                self.ready, self.stopped = threading.Event(), threading.Event()
            def event(self, method, params):
                self.events.append((method, params))
            def stop(self):
                self.stopped.set()
        voice = Voice()
        self.context.voice = self.c.voice = voice
        generation = self.c._request_generation
        executor = Executor()
        for index in range(2):
            turn = "voice-turn-" + str(index)
            self.p._event("turn/started", {"threadId": "native-thread", "turn": {"id": turn}})
            self.p._request("voice-read-" + str(index), "item/tool/call", {"threadId": "native-thread", "turnId": turn,
                "callId": "voice-read-" + str(index), "tool": "angelo_inspect_scene", "arguments": {}})
            self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
            self.p._event("turn/completed", {"threadId": "native-thread", "turn": {"id": turn, "status": "completed"}})
            self.c.pump_events()
            self.assertFalse(self.context.cancelled)
            self.assertTrue(self.c.busy)
            self.assertEqual(self.c._request_generation, generation)
        self.assertEqual(len(executor.calls), 2)
        self.assertTrue(all(payload["success"] for _, payload in self.p.rpc.responses))
        self.p._event("thread/realtime/started", {"threadId": "different-thread"})
        self.assertFalse(voice.events)
        self.p._event("thread/realtime/started", {"threadId": "native-thread"})
        self.assertEqual(len(voice.events), 1)

    def test_voice_activation_starts_new_thread_and_rejects_old_activation_events(self):
        self.c.provider = self.p
        self.next_request()
        observed = []
        original_rpc = self.p.rpc
        def start(method, parameters, **kwargs):
            self.assertEqual(method, "thread/start")
            self.assertEqual(parameters["sandbox"], "read-only")
            self.assertTrue(parameters["dynamicTools"])
            observed.append(self.p.controller.state["native_id"])
            return {"thread": {"id": "new-voice-thread"}}
        self.p.rpc.request = start
        self.p.connect = lambda: self.fail("Voice must reuse the validated connection/process, not reconnect auth")
        self.assertEqual(self.c._connect_runtime(self.context, voice=True), "new-voice-thread")
        self.assertEqual(observed, [None])
        self.assertIs(self.p.rpc, original_rpc)
        class Voice:
            def __init__(self):
                self.events = []
            def event(self, method, params):
                self.events.append((method, params))
        voice = self.context.voice = Voice()
        self.p._event("turn/started", {"threadId": "native-thread", "turn": {"id": "late-voice-turn"}})
        self.p._event("thread/realtime/started", {"threadId": "native-thread"})
        self.request("late-voice-turn", identity="old-voice-tool")
        self.assertFalse(voice.events)
        self.assertIsNone(self.p._scope.turn_id)
        self.assertFalse(self.c.pending)
        self.p._event("turn/started", {"threadId": "new-voice-thread", "turn": {"id": "current-voice-turn"}})
        self.assertEqual(self.p._scope.turn_id, "current-voice-turn")

    def test_delayed_voice_turn_start_cannot_revive_completed_turn_or_replace_current(self):
        self.context.voice = object()
        self.p._event("turn/started", {"threadId": "native-thread", "turn": {"id": "voice-a"}})
        self.p._event("turn/completed", {"threadId": "native-thread", "turn": {"id": "voice-a", "status": "completed"}})
        self.request("voice-a", identity="after-completion")
        self.assertFalse(self.c.pending)
        self.assertFalse(self.p.rpc.responses[-1][1]["success"])
        self.p._event("turn/started", {"threadId": "native-thread", "turn": {"id": "voice-b"}})
        self.p._event("turn/started", {"threadId": "native-thread", "turn": {"id": "voice-a"}})
        self.assertEqual(self.p._scope.turn_id, "voice-b")
        self.request("voice-a", identity="revive-old-turn")
        self.assertFalse(self.c.pending)
        self.request("voice-b", identity="current-voice-tool")
        self.assertEqual(len(self.c.pending), 1)

    def test_voice_turn_identity_cache_fails_closed_at_its_bound(self):
        self.context.voice = object()
        self.p._scope.seen_turns = {"seen-" + str(index) for index in range(256)}
        self.p._event("turn/started", {"threadId": "native-thread", "turn": {"id": "overflow-voice-turn"}})
        self.assertEqual(self.p._scope.turn_id, "turn-a")
        self.request("overflow-voice-turn", identity="overflow-call")
        self.assertFalse(self.c.pending)
        self.c.pump_events()
        self.assertTrue(any("bounded identity limit" in message["text"] for message in self.c.state["messages"]))

    def test_deferred_runtime_start_records_cold_truth_and_ignores_stale_callbacks(self):
        self.c._request_metrics.warm = True
        self.context.record_runtime_start(25)
        self.assertEqual(self.c._request_metrics.snapshot()["runtime_start"], "cold")
        self.assertEqual(self.c._request_metrics.snapshot()["phases"]["runtime_start_ms"], 25)
        old = self.bind_next()
        old.record_runtime_start(999)
        self.assertNotIn("runtime_start_ms", self.c._request_metrics.snapshot()["phases"])
        self.context.record_runtime_start(float("nan"))
        self.assertNotIn("runtime_start_ms", self.c._request_metrics.snapshot()["phases"])


if __name__ == "__main__":
    unittest.main()
