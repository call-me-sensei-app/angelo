# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pending capture and delivery belong to one live request and native turn.

Actual Controller/RuntimeContext/CodexProvider admission and callbacks run with
temporary synthetic state, StudioPending capture spies and an in-memory RPC.
There is no Blender, runtime subprocess, image decoding or model delivery here.
"""

import json
import queue
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_delivery, codex_plan, delegation, providers, request_fences, request_path  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402

VIEW = {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "j1"}
CUBE = {"name": "Synthetic Cube", "size": 1, "location": [0, 0, 0]}


class RecordingRPC:
    def __init__(self):
        self.closed = threading.Event()
        self.responses, self.requests = [], []
        self.on_response = lambda identity, payload: None

    def respond(self, identity, payload):
        self.responses.append((identity, payload))
        self.on_response(identity, payload)

    def request(self, method, parameters, **kwargs):
        self.requests.append((method, parameters))
        return {}

    def close(self):
        self.closed.set()


class PendingDeliveryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-pending-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.host = Controller(self.temp.name)
        self.addCleanup(self.host.close)
        self.host.busy = True
        self.host.set_document(("synthetic-pending-document",))
        self.host.state["options"]["approval"] = "ASK"
        self.context = self.host.runtime_context()
        self.provider = providers.CodexProvider(self.context, {})
        self.addCleanup(self.provider.close)
        self.rpc = self.provider.rpc = RecordingRPC()
        self.provider._transport_token = object()
        self.provider.thread_id = "synthetic-thread"
        self.provider.bind_request(self.context)
        self.provider.turn_id = "turn-a"
        self.provider._scope.bind_turn("turn-a")
        self.captures, self.packing, self.receipts, self.cancellations = [], [], [], []
        self.pendings = {}
        self.ready = True
        self.on_capture = lambda identity: None
        self.on_pack = lambda result: None
        self.executor = SimpleNamespace(execute=self.execute, precondition=lambda *args: "unchanged")
        packing = patch.object(providers, "tool_content", self.pack)
        packing.start()
        self.addCleanup(packing.stop)
        receipts = patch.object(providers.request_path, "record_codex_result",
                                lambda *args, **kwargs: self.receipts.append(args))
        receipts.start()
        self.addCleanup(receipts.stop)

    def execute(self, name, arguments):
        identity = self.host.executing_call.id.rsplit("/", 1)[-1]

        def capture():
            if not self.ready:
                return None
            self.captures.append(identity)
            self.on_capture(identity)
            return {"success": True, "read_only": True, "scene_changed": False,
                    "views": [{"id": "synthetic-" + identity, "synthetic_spy": identity}], "probe_result": identity}

        pending = StudioPending(capture, lambda: self.cancellations.append(identity))
        pending.timeout = 630
        pending.stopped_result = {"error": "Synthetic monitoring stopped; submitted work may remain",
                                  "probe_result": identity}
        self.pendings[identity] = pending
        return pending

    def pack(self, context, name, result):
        self.packing.append(result)
        self.on_pack(result)
        content = [{"type": "inputText", "text": json.dumps(result)}]
        if result.get("views") and not result.get("error"):
            content.append({"type": "inputImage", "imageUrl": "MOCK_IMAGE_NO_REAL_PIXELS"})
        return content

    def request(self, identity="call-1", *, call_id=None, name="angelo_view", arguments=None):
        self.provider._request(identity, "item/tool/call", {
            "threadId": self.provider.thread_id, "turnId": self.provider.turn_id,
            "callId": call_id or identity, "tool": name,
            "arguments": VIEW if arguments is None else arguments},
            transport=self.provider._transport_token)

    def dispatch(self):
        self.host.pump_tools(self.executor, self.host.document, budget=ToolDispatchBudget(seconds=60))

    def start(self, identity="call-1"):
        self.request(identity)
        self.dispatch()
        self.assertIn(identity, self.pendings)
        call = next(call for call in self.host.pending.values() if call.id.endswith("/" + identity))
        self.assertTrue(call.executing)
        self.assertFalse(call.mutation_started)
        return call

    def native_finished(self):
        self.provider._event("turn/completed", {
            "threadId": self.provider.thread_id,
            "turn": {"id": self.provider.turn_id, "status": "completed"}},
            transport=self.provider._transport_token)

    def bind_turn(self, identity):
        self.provider.turn_id = identity
        self.provider._scope.bind_turn(identity)

    def assert_settled(self):
        self.assertFalse(self.host.pending)
        self.assertFalse(self.host.media_jobs)
        self.assertTrue(all(pending.closed for pending in self.pendings.values()))

    def assert_no_delivery(self):
        self.assertEqual(self.packing, [])
        self.assertEqual(self.rpc.responses, [])
        self.assertEqual(self.receipts, [])
        self.assertEqual(self.provider.turn_calls(), [])

    def complete_from_worker(self):
        """Close the exact native turn while a main-thread phase is active."""
        done = threading.Event()
        errors = []

        def complete():
            try:
                self.native_finished()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=complete, daemon=True)
        worker.start()
        completed_inside_phase = done.wait(2)
        return worker, completed_inside_phase, errors


class PendingDeliveryTests(PendingDeliveryCase):
    def immediate_answer_unlocked(self, identity, name, arguments, **options):
        """Both observation and delivery must permit another thread to take the host lock."""
        phases, barriers, answers, workers = [], [], [], []

        def phase(label, result):
            entered = threading.Event()

            def acquire():
                with self.host.lock:
                    entered.set()

            worker = threading.Thread(target=acquire, daemon=True)
            workers.append(worker)
            worker.start()
            barriers.append(entered.wait(2))
            phases.append(label)
            if label == "callback":
                answers.append(result)

        self.host.request_tool(identity, name, arguments, lambda result: phase("callback", result),
            result_observer=lambda result: phase("observer", result), **options)
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(phases, ["observer", "callback"])
        self.assertEqual(barriers, [True, True], "an immediate answer retained the admission lock")
        self.assertEqual(len(answers), 1)
        return answers[0]

    def test_stale_owner_answer_observer_and_callback_run_outside_host_lock(self):
        owner = request_fences.DeliveryOwner(self.context.request_fence)
        owner.close()
        result = self.immediate_answer_unlocked("stale", "angelo_view", VIEW, delivery_owner=owner)
        self.assertIn("error", result)
        self.assertFalse(self.host.pending)

    def test_schema_refusal_observer_and_callback_run_outside_host_lock(self):
        result = self.immediate_answer_unlocked("invalid", "angelo_create_cube", {"size": "invalid"})
        self.assertIn("error", result)
        self.assertFalse(self.host.pending)

    def test_replayed_result_observer_and_callback_run_outside_host_lock(self):
        self.host.request_tool("saved", "angelo_inspect_scene", {}, lambda result: None, enqueue=False)
        call = next(iter(self.host.pending.values()))
        self.host.finish_tool(call, {"success": True, "read_only": True, "scene_changed": False})
        result = self.immediate_answer_unlocked("saved", "angelo_inspect_scene", {})
        self.assertTrue(result["replayed"])
        self.assertTrue(result["success"])
        self.assertFalse(self.host.pending)

    def test_duplicate_argument_refusal_answer_runs_outside_host_lock(self):
        self.host.request_tool("pending", "angelo_view", VIEW, lambda result: None, enqueue=False)
        original = next(iter(self.host.pending.values()))
        result = self.immediate_answer_unlocked("pending", "angelo_view", dict(VIEW, image="j2"))
        self.assertIn("different operations or arguments", result["error"])
        self.assertIs(self.host.pending[original.id], original)
        self.assertEqual(len(original.callbacks), 1)

    def path_metrics(self):
        self.host._begin_metrics()
        self.context = self.host.runtime_context()
        self.provider.bind_request(self.context)
        self.bind_turn("turn-accounting")
        return self.host._request_metrics.path

    def test_joined_rpc_mutation_records_one_execution_and_one_edit(self):
        path, executions = self.path_metrics(), []
        self.executor.execute = lambda name, arguments: (
            executions.append(name) or {"success": True, "scene_changed": True,
                "changes": {"added": [{"name": "Synthetic Cube", "type": "MESH"}], "removed": [], "modified": []}})
        self.request("rpc-a", call_id="shared-edit", name="angelo_create_cube", arguments=CUBE)
        self.request("rpc-b", call_id="shared-edit", name="angelo_create_cube", arguments=CUBE)
        self.dispatch()
        call = next(iter(self.host.pending.values()))
        self.host.execute_tool(call, self.executor, self.host.document)
        self.assertEqual(executions, ["angelo_create_cube"])
        self.assertEqual(path.edits, 1)
        self.assertEqual(path.steps[0][-1], 1)
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["rpc-a", "rpc-b"])
        self.assertTrue(self.host.state["results"][call.id]["result"]["scene_changed"])
        self.request("rpc-replay", call_id="shared-edit", name="angelo_create_cube", arguments=CUBE)
        self.assertEqual(executions, ["angelo_create_cube"])
        self.assertEqual(path.edits, 1, "a replay is not a second committed edit")
        replay = json.loads(self.rpc.responses[-1][1]["contentItems"][0]["text"])
        self.assertTrue(replay["replayed"])
        self.assertTrue(self.host.state["results"][call.id]["result"]["success"])

    def test_joined_read_callbacks_record_returned_picture_once(self):
        path = self.path_metrics()
        self.request("rpc-a", call_id="shared-view")
        self.request("rpc-b", call_id="shared-view")
        self.dispatch()
        self.host.pump_events()
        self.assertEqual(self.captures, ["shared-view"])
        self.assertEqual(path.images_returned, 1)
        self.assertEqual(len(path._known), 1)
        self.assertEqual(len(self.rpc.responses), 2)

    def test_admitted_observer_runs_once_outside_host_lock_before_callbacks(self):
        order, workers, barriers = [], [], []

        def observer(result):
            entered = threading.Event()

            def acquire():
                with self.host.lock:
                    entered.set()

            worker = threading.Thread(target=acquire, daemon=True)
            workers.append(worker)
            worker.start()
            barriers.append(entered.wait(2))
            order.append("observer")

        self.host.request_tool("observed", "angelo_inspect_scene", {}, lambda result: order.append("first"),
                               result_observer=observer)
        self.host.request_tool("observed", "angelo_inspect_scene", {}, lambda result: order.append("joined"),
                               result_observer=lambda result: order.append("joined-observer"))
        call = next(iter(self.host.pending.values()))
        self.host.finish_tool(call, {"success": True, "read_only": True, "scene_changed": False})
        self.host.finish_tool(call, {"success": True})
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(barriers, [True])
        self.assertEqual(order, ["observer", "first", "joined"])

    def test_early_refusal_observer_precedes_its_callback(self):
        order = []
        self.host.request_tool("invalid", "angelo_create_cube", {"size": "invalid"},
            lambda result: order.append(("callback", bool(result.get("error")))),
            result_observer=lambda result: order.append(("observer", bool(result.get("error")))))
        self.assertEqual(order, [("observer", True), ("callback", True)])
        self.assertFalse(self.host.pending)

    def test_post_admission_queue_failure_observes_outside_admission_lock(self):
        entered, observed, answers = threading.Event(), [], []
        workers = []

        def observer(result):
            def acquire():
                with self.host.lock:
                    entered.set()

            worker = threading.Thread(target=acquire, daemon=True)
            workers.append(worker)
            worker.start()
            observed.append(entered.wait(2))

        with patch.object(self.host.tools, "put_nowait", side_effect=queue.Full):
            self.host.request_tool("queue-full", "angelo_inspect_scene", {}, answers.append,
                                   result_observer=observer)
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(observed, [True])
        self.assertEqual(len(answers), 1)
        self.assertIn("error", answers[0])
        self.assertFalse(self.host.pending)

    def test_live_pending_captures_and_delivers_once(self):
        self.start()
        self.host.pump_events()
        self.assertEqual(self.captures, ["call-1"])
        self.assertEqual(len(self.packing), 1)
        self.assertEqual(len(self.rpc.responses), 1)
        self.assertEqual([row["type"] for row in self.rpc.responses[0][1]["contentItems"]],
                         ["inputText", "inputImage"])
        self.assert_settled()

    def test_completed_before_ready_poll_does_not_capture_or_deliver(self):
        self.start()
        self.native_finished()
        self.context.emit("finished", {"status": "completed"})
        self.assertTrue(self.host.busy, "the UI has not processed finished yet")
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assert_settled()
        self.assertFalse(self.host.busy)

    def test_native_completion_alone_closes_its_wait(self):
        self.start()
        self.native_finished()
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assert_settled()
        self.assertTrue(self.host.busy, "native turn completion is not host request completion")

    def test_failed_cleanup_settles_the_call_without_polling_or_delivering(self):
        call = self.start()
        def fail():
            raise RuntimeError("synthetic cleanup failure")
        self.host.media_jobs[call.id]._cancel = fail
        self.native_finished()
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assert_settled()
        result = self.host.state["results"][call.id]["result"]
        self.assertEqual(result["cleanup_error"], "synthetic cleanup failure")

    def test_completion_during_capture_preserves_result_but_suppresses_delivery(self):
        call = self.start()
        self.on_capture = lambda identity: self.native_finished()
        self.host.pump_events()
        self.assertEqual(self.captures, ["call-1"], "already admitted capture may finish")
        self.assert_no_delivery()
        self.assert_settled()
        self.assertTrue(self.host.state["results"][call.id]["result"]["success"])

    def test_completion_during_packing_suppresses_response(self):
        self.start()
        self.on_pack = lambda result: self.native_finished()
        self.host.pump_events()
        self.assertEqual(self.captures, ["call-1"])
        self.assertEqual(len(self.packing), 1)
        self.assertEqual(self.rpc.responses, [])
        self.assertEqual(self.receipts, [])
        self.assertEqual(self.provider.turn_calls(), [])
        self.assert_settled()

    def test_completion_during_started_response_suppresses_later_accounting(self):
        self.start()
        self.rpc.on_response = lambda identity, payload: self.native_finished()
        self.host.pump_events()
        self.assertEqual(len(self.rpc.responses), 1, "an already-started response is not retracted")
        self.assertEqual(self.receipts, [])
        self.assertEqual(self.provider.turn_calls(), [])
        self.assert_settled()

    def test_finished_not_ready_wait_is_cancelled_without_capture(self):
        self.ready = False
        self.start()
        self.native_finished()
        self.context.emit("finished", {"status": "completed"})
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(self.cancellations, ["call-1"])
        self.assert_no_delivery()
        self.assert_settled()

    def test_explicit_stop_prevents_capture(self):
        self.start()
        self.host.stop()
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(self.cancellations, ["call-1"])
        self.assertFalse(any(result.get("views") for result in self.packing))
        self.assert_settled()

    def test_response_callback_stop_prevents_next_snapshotted_wait_capture(self):
        self.start("call-1")
        self.start("call-2")
        self.rpc.on_response = lambda identity, payload: self.host.stop() if identity == "call-1" else None
        self.host.pump_events()
        self.assertEqual(self.captures, ["call-1"])
        self.assertEqual(self.cancellations, ["call-2"])
        self.assert_settled()

    def test_closed_exact_turn_does_not_cancel_next_turn_with_same_host_fence(self):
        self.start("old")
        old_fence = self.context.request_fence
        self.native_finished()
        self.bind_turn("turn-b")
        self.start("new")
        self.assertEqual(self.context.request_fence, old_fence)
        self.host.pump_events()
        self.assertEqual(self.captures, ["new"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["new"])
        self.assert_settled()

    def test_voice_turn_replacement_keeps_next_turn_live_on_same_host_fence(self):
        self.context.voice = SimpleNamespace()
        self.start("old-voice")
        self.native_finished()
        self.provider._event("turn/started", {"threadId": self.provider.thread_id,
            "turn": {"id": "voice-b"}}, transport=self.provider._transport_token)
        self.start("new-voice")
        self.host.pump_events()
        self.assertEqual(self.captures, ["new-voice"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["new-voice"])
        self.assert_settled()

    def test_transport_replacement_never_delivers_old_result_to_either_rpc(self):
        self.start()
        old_rpc = self.rpc
        self.provider.close()
        self.provider.rpc = replacement = RecordingRPC()
        self.provider._transport_token = object()
        self.provider.bind_request(self.context)
        self.bind_turn("turn-a")  # Even reused string IDs cannot revive old ownership.
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(old_rpc.responses, [])
        self.assertEqual(replacement.responses, [])
        self.assertEqual(self.packing, [])
        self.assert_settled()

    def test_transport_eof_closes_wait_before_disconnect_callback(self):
        self.start()
        self.rpc.closed.set()
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assert_settled()

    def test_disconnect_event_closes_wait_without_finishing_new_request(self):
        self.start()
        self.provider._event("host/disconnected", {}, transport=self.provider._transport_token)
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assert_settled()

    def test_targeted_stop_closes_current_turn_owner_before_next_poll(self):
        self.start()
        self.provider.stop_request(self.context.request_fence)
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assertEqual(self.rpc.requests, [("turn/interrupt", {
            "threadId": "synthetic-thread", "turnId": "turn-a"})])
        self.assert_settled()

    def test_delayed_stop_with_old_host_fence_does_not_close_new_request(self):
        old_fence = self.context.request_fence
        self.host._request_generation += 1
        self.context = self.host.runtime_context()
        self.provider.bind_request(self.context)
        self.bind_turn("turn-b")
        self.start("new")
        self.provider.stop_request(old_fence)
        self.host.pump_events()
        self.assertEqual(self.captures, ["new"])
        self.assertEqual(self.rpc.requests, [])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["new"])
        self.assert_settled()

    def test_duplicate_pending_in_same_owner_joins_one_capture(self):
        self.request("rpc-1", call_id="shared")
        self.request("rpc-2", call_id="shared")
        self.assertEqual(len(self.host.pending), 1)
        self.assertEqual(len(next(iter(self.host.pending.values())).callbacks), 2)
        self.dispatch()
        self.host.pump_events()
        self.assertEqual(self.captures, ["shared"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["rpc-1", "rpc-2"])
        self.assert_settled()

    def test_one_arrival_callback_cannot_respond_or_pack_twice(self):
        call = self.start()
        callback = call.callbacks[0]
        self.host.pump_events()
        callback(self.host.state["results"][call.id]["result"])
        self.assertEqual(self.captures, ["call-1"])
        self.assertEqual(len(self.packing), 1)
        self.assertEqual(len(self.rpc.responses), 1)
        self.assertEqual(len(self.receipts), 1)
        self.assertEqual(len(self.provider.turn_calls()), 1)

    def test_fresh_rpc_arrival_may_reuse_response_id(self):
        for call_id in ("first-invocation", "second-invocation"):
            self.request("same-rpc-id", call_id=call_id)
            self.dispatch()
            self.host.pump_events()
        self.assertEqual(self.captures, ["first-invocation", "second-invocation"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["same-rpc-id", "same-rpc-id"])
        self.assertEqual(len(self.packing), 2)
        self.assert_settled()

    def reuse_during_response(self, phase):
        triggered = []

        def reuse(*args, **kwargs):
            if triggered:
                return
            triggered.append(phase)
            self.assertEqual(len(self.rpc.responses), 1)
            if phase == "write":
                # Same logical envelope is still the original invocation until
                # this admitted write returns, even if its peer can see bytes.
                self.request("reused", call_id="first")
                self.assertFalse(self.host.pending)
            self.request("reused", call_id="second")
            successor = self.provider._scope.responses['"reused"']
            self.assertEqual(successor.call_id, "second")
            self.assertFalse(successor.writing)
            self.request("reused", call_id="first")  # The old invocation cannot revive across its successor.
            self.request("reused", call_id="third")  # The successor is pending, so it cannot be displaced.
            self.assertIs(self.provider._scope.responses['"reused"'], successor)
            old_callback(self.host.state["results"][first.id]["result"])
            self.assertIs(self.provider._scope.responses['"reused"'], successor)

        if phase == "write":
            self.rpc.on_response = reuse
        else:
            observer = patch.object(providers.request_path, "record_codex_result", side_effect=reuse)
            observer.start()
            self.addCleanup(observer.stop)
        self.request("reused", call_id="first")
        first = next(iter(self.host.pending.values()))
        old_callback = first.callbacks[0]
        self.dispatch()
        self.host.pump_events()
        self.assertEqual(triggered, [phase])
        self.assertEqual(self.captures, ["first"])
        self.assertEqual(self.provider._scope.responses['"reused"'].call_id, "second",
                         "The old callback's final retirement cannot remove its successor")
        self.dispatch()
        self.host.pump_events()
        self.assertEqual(self.captures, ["first", "second"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["reused", "reused"])
        self.assertEqual(len(self.packing), 2)
        self.assertFalse(self.provider._scope.responses)
        self.assert_settled()

    def test_distinct_call_may_reuse_rpc_id_during_observed_write_without_reviving_old_envelope(self):
        self.reuse_during_response("write")

    def test_answered_rpc_id_is_reusable_during_postwrite_accounting(self):
        self.reuse_during_response("accounting")

    def test_same_logical_call_may_replay_after_write_returns_before_accounting_finishes(self):
        triggered = []
        def replay(*args, **kwargs):
            if not triggered:
                triggered.append(True)
                self.request("reused", call_id="first")
        with patch.object(providers.request_path, "record_codex_result", side_effect=replay):
            self.request("reused", call_id="first")
            self.dispatch()
            self.host.pump_events()
        self.assertTrue(triggered)
        self.assertEqual(self.captures, ["first"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], ["reused", "reused"])
        self.assertEqual(len(self.packing), 2)
        self.assertTrue(self.packing[1]["replayed"])
        self.assertFalse(self.provider._scope.responses)
        self.assert_settled()

    def test_rpc_claim_still_rejects_distinct_overlap_during_packing(self):
        self.request("reused", call_id="first")
        self.on_pack = lambda result: self.request("reused", call_id="second")
        self.dispatch()
        self.host.pump_events()
        self.assertEqual(self.captures, ["first"])
        self.assertEqual(len(self.rpc.responses), 1)
        self.assert_settled()

    def test_missing_call_id_uses_the_existing_rpc_identity_fallback(self):
        triggered = []
        params = {"threadId": self.provider.thread_id, "turnId": self.provider.turn_id,
                  "tool": "angelo_view", "arguments": VIEW}
        def duplicate(*args):
            if not triggered:
                triggered.append(True)
                self.provider._request(7, "item/tool/call", params, transport=self.provider._transport_token)
        self.rpc.on_response = duplicate
        self.provider._request(7, "item/tool/call", params, transport=self.provider._transport_token)
        self.dispatch()
        self.host.pump_events()
        self.assertEqual(self.captures, ["7"])
        self.assertEqual([identity for identity, _ in self.rpc.responses], [7])
        self.assert_settled()

    def test_writing_claim_replacement_keeps_the_bound_and_exact_retirement(self):
        scope = self.provider._scope
        tickets = [scope.claim_response(index, "first-%d" % index)
                   for index in range(request_fences.MAX_PENDING_RESPONSES)]
        first = tickets[0]
        self.assertTrue(scope.begin_response(first))
        self.assertFalse(scope.begin_response(first), "A lease never re-enters its write phase")
        self.assertIsNone(scope.claim_response(0, "first-0"))
        successor = scope.claim_response(0.0, "second")  # Equivalent RPC number, distinct logical call.
        self.assertIsNotNone(successor)
        scope.retire_response(first)
        self.assertEqual(len(scope.responses), request_fences.MAX_PENDING_RESPONSES)
        self.assertIs(scope.responses['0'], successor[-1])
        with self.assertRaises(OverflowError):
            scope.claim_response("new-key", "other")
        self.native_finished()
        self.assertFalse(scope.responses)
        self.assertFalse(scope.begin_response(successor))
        scope.retire_response(first)
        scope.retire_response(successor)
        self.assertFalse(scope.responses)

    def test_outstanding_rpc_id_owns_only_one_call_and_response(self):
        for index, second_call_id in enumerate(("shared", "different")):
            with self.subTest(second_call_id=second_call_id):
                identity = "rpc-%d" % index
                first = "shared-%d" % index
                second = first if second_call_id == "shared" else "different-%d" % index
                self.request(identity, call_id=first)
                self.request(identity, call_id=second)
                self.assertEqual(len(self.host.pending), 1)
                self.assertEqual(len(next(iter(self.host.pending.values())).callbacks), 1)
                self.dispatch()
                self.host.pump_events()
                self.assertEqual(self.captures[-1], first)
                self.assertEqual(len(self.rpc.responses), index + 1)
                self.assertFalse(self.provider._scope.responses)
        self.assert_settled()

    def test_response_claim_released_after_owner_close(self):
        self.start()
        self.assertTrue(self.provider._scope.responses)
        self.native_finished()
        self.host.pump_events()
        self.assertFalse(self.provider._scope.responses)
        self.assertEqual(self.rpc.responses, [])
        self.assert_settled()

    def test_response_claim_released_after_packing_exception_allows_fresh_arrival(self):
        self.start()

        def fail(result):
            raise RuntimeError("Synthetic packing failure")

        self.on_pack = fail
        self.host.pump_events()
        self.assertFalse(self.provider._scope.responses)
        self.assertEqual(self.rpc.responses, [])
        self.on_pack = lambda result: None
        self.request("call-1")  # A fresh arrival replays the already completed invocation.
        self.assertEqual(len(self.rpc.responses), 1)
        self.assertFalse(self.provider._scope.responses)
        self.assertEqual(self.captures, ["call-1"])

    def test_response_claim_released_after_transport_write_failure(self):
        self.start()
        with patch.object(self.rpc, "respond", side_effect=OSError("Synthetic write failure")):
            self.host.pump_events()
        self.assertFalse(self.provider._scope.responses)
        self.assertEqual(self.receipts, [])
        self.request("call-1")
        self.assertEqual(len(self.rpc.responses), 1)
        self.assertFalse(self.provider._scope.responses)

    def test_owner_closing_at_postwrite_lock_prevents_receipt_accounting(self):
        self.start()
        scope, owner = self.provider._scope, self.provider._scope.delivery_owner
        original_lock = scope.lock

        class ClosingLock:
            def __enter__(self):
                original_lock.acquire()
                owner.close()
                return self

            def __exit__(self, *args):
                original_lock.release()

        self.rpc.on_response = lambda identity, payload: setattr(scope, "lock", ClosingLock())
        try:
            self.host.pump_events()
        finally:
            scope.lock = original_lock
        self.assertEqual(len(self.rpc.responses), 1)
        self.assertEqual(self.receipts, [])
        self.assertEqual(self.provider.turn_calls(), [])
        self.assertFalse(scope.responses)

    def test_old_deferred_request_owner_cannot_match_reused_native_ids(self):
        old_owner = self.provider._scope.delivery_owner
        self.bind_turn("turn-a")
        self.assertFalse(old_owner.current(self.host))
        self.assertIsNot(self.provider._scope.delivery_owner, old_owner)
        self.provider._request("deferred-old", "item/tool/call", {
            "threadId": self.provider.thread_id, "turnId": "turn-a",
            "callId": "deferred-old", "tool": "angelo_view", "arguments": VIEW},
            transport=self.provider._transport_token, expected_owner=old_owner)
        # A stale replay cannot answer through the current scope; it gets the stale refusal only.
        self.assert_stale_refusal("deferred-old")
        # Its reused turn ID names the current live turn, which is never interrupted.
        self.provider._scope.turns.join()
        self.assertEqual(self.rpc.requests, [])

    def assert_stale_refusal(self, identity):
        """One refusal on the arriving transport; nothing admitted, captured, packed or recorded."""
        self.assertEqual(self.rpc.responses, [(identity, {"success": False, "contentItems": [
            {"type": "inputText", "text": codex_delivery.STALE_CALL}]})])
        self.assertFalse(self.host.pending)
        self.assertEqual((self.captures, self.packing, self.receipts, self.provider.turn_calls()), ([], [], [], []))
        self.assertNotIn("tool_invocations", (self.host._request_metrics.snapshot()["counts"]
                                              if self.host._request_metrics else {}))

    def test_late_call_after_its_request_finished_is_refused_without_admission(self):
        # codex app-server waits for every tool-call answer (the unbilled stale-call probe).
        self.context.emit("finished", {"status": "completed"})  # Closes the request's delivery owner.
        self.assertFalse(self.provider._scope.delivery_owner.current())
        with patch.object(self.context, "request_tool", side_effect=AssertionError("a stale call reached admission")):
            self.request("late-call", name="angelo_create_cube", arguments=CUBE)
        self.assert_stale_refusal("late-call")
        # Its turn is still open in Codex: it is interrupted so no later message can join it.
        self.provider._scope.turns.join()
        self.assertEqual(self.rpc.requests, [("turn/interrupt", {"threadId": "synthetic-thread", "turnId": "turn-a"})])

    def test_admitted_call_whose_request_ended_withholds_its_reply_and_interrupts_its_turn(self):
        # The request ended without Stop or an overdue interrupt (e.g. it failed after turn/start).
        self.start("call-1")
        self.context.emit("finished", {"status": "failed"})
        self.host.pump_events()
        self.provider._scope.turns.join()
        self.assertEqual(self.rpc.responses, [], "never a 'nothing was run' refusal for an admitted call")
        self.assertEqual(self.rpc.requests, [("turn/interrupt", {"threadId": "synthetic-thread", "turnId": "turn-a"})])
        key = next(key for key in self.host.state["results"] if key.endswith("/call-1"))
        result = self.host.state["results"][key]["result"]
        self.assertIn("submitted work may remain", result["error"])  # Its own honest stopped result.
        self.assertNotIn(codex_delivery.STALE_CALL, json.dumps(result))
        self.assertEqual((self.packing, self.receipts, self.provider.turn_calls()), ([], [], []))
        self.assert_settled()

    def test_admitted_mutation_that_ran_after_its_request_ended_keeps_its_real_result(self):
        outcome = {"success": True, "scene_changed": True, "probe_mutation": "actually-completed",
                   "changes": {"added": [{"name": "Synthetic Cube", "type": "MESH"}], "removed": [], "modified": []}}

        def mutate(name, arguments):
            self.context.emit("finished", {"status": "failed"})  # Owner closes mid-mutation, no Stop.
            return outcome

        self.executor.execute = mutate
        self.request(name="angelo_create_cube", arguments=CUBE)
        self.dispatch()
        call = next(iter(self.host.pending.values()))
        self.host.execute_tool(call, self.executor, self.host.document)
        self.provider._scope.turns.join()
        result = self.host.state["results"][call.id]["result"]
        self.assertTrue(result["success"] and result["scene_changed"])
        self.assertEqual(result["probe_mutation"], "actually-completed")
        self.assertEqual(self.rpc.responses, [])
        self.assertEqual(self.rpc.requests, [("turn/interrupt", {"threadId": "synthetic-thread", "turnId": "turn-a"})])

    def test_late_call_after_stop_is_refused_and_its_turn_interrupted(self):
        self.provider.stop_request(self.context.request_fence)
        with patch.object(self.context, "request_tool", side_effect=AssertionError("a stale call reached admission")):
            self.request("late-call", name="angelo_create_cube", arguments=CUBE)
        self.assert_stale_refusal("late-call")
        self.assertEqual(self.rpc.requests, [("turn/interrupt", {"threadId": "synthetic-thread", "turnId": "turn-a"})])

    def test_stale_refusal_never_answers_an_rpc_id_a_live_call_owns(self):
        self.start("shared-rpc")  # Admitted and pending: this call owns the ID's one response.
        codex_delivery.refuse_stale_call(self.provider, "shared-rpc", self.rpc,
                                         {"threadId": "synthetic-thread", "turnId": "turn-a"})
        self.assertEqual(self.rpc.responses, [])

    def test_old_deferred_event_owner_cannot_complete_reused_native_ids(self):
        old_owner = self.provider._scope.delivery_owner
        self.bind_turn("turn-a")
        current_owner = self.provider._scope.delivery_owner
        self.assertFalse(old_owner.current(self.host))
        self.provider._event("turn/completed", {"threadId": self.provider.thread_id,
            "turn": {"id": "turn-a", "status": "completed"}},
            transport=self.provider._transport_token, expected_owner=old_owner)
        self.assertFalse(self.provider._scope.completed)
        self.assertTrue(current_owner.current(self.host))
        self.start("current")
        self.host.pump_events()
        self.assertEqual(self.captures, ["current"])
        self.assert_settled()

    def test_duplicate_pending_from_different_owner_cannot_join_or_replace(self):
        self.request("old-rpc", call_id="shared")
        original = next(iter(self.host.pending.values()))
        self.dispatch()
        self.bind_turn("turn-b")
        self.request("new-rpc", call_id="shared")
        self.assertIs(next(iter(self.host.pending.values())), original)
        self.assertEqual(len(original.callbacks), 1)
        self.assertEqual(len(self.rpc.responses), 1)
        identity, payload = self.rpc.responses[0]
        self.assertEqual(identity, "new-rpc")
        self.assertFalse(payload["success"])
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(len(self.rpc.responses), 1)
        self.assert_settled()

    def test_non_codex_request_finish_closes_pending_before_ui_event(self):
        replies = []
        self.context.request_tool("local-wait", "angelo_view", VIEW, replies.append)
        self.dispatch()
        self.context.emit("finished", {"status": "completed"})
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(len(replies), 1, "host waiter must still be released")
        self.assertIn("error", replies[0])
        self.assert_settled()

    def test_local_plan_dispatch_remains_live_after_native_completion(self):
        self.native_finished()
        admitted = threading.Event()
        answers, errors = [], []
        request_tool = self.context.request_tool

        def request(*args, **kwargs):
            request_tool(*args, **kwargs)
            admitted.set()

        def run_plan_operation():
            try:
                answers.append(codex_plan._native_call(self.provider, self.context,
                    self.context.request_fence, "angelo_view", VIEW))
            except BaseException as error:
                errors.append(error)

        with patch.object(self.context, "request_tool", request), patch.object(codex_plan, "DISPATCH_SECONDS", 3):
            worker = threading.Thread(target=run_plan_operation, daemon=True)
            worker.start()
            try:
                self.assertTrue(admitted.wait(2), "plan did not admit its call")
                self.dispatch()
                self.host.pump_events()
            finally:
                worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(answers), 1)
        self.assertTrue(answers[0]["success"])
        self.assertEqual(len(self.captures), 1)
        self.assert_settled()

    def test_started_atomic_mutation_keeps_real_result_after_native_completion(self):
        outcome = {"success": True, "scene_changed": True, "probe_mutation": "actually-completed",
                   "changes": {"added": [{"name": "Synthetic Cube", "type": "MESH"}],
                               "removed": [], "modified": []}}

        def mutate(name, arguments):
            self.assertTrue(self.host.executing_call.mutation_started)
            self.native_finished()
            return outcome

        self.executor.execute = mutate
        self.request(name="angelo_create_cube", arguments=CUBE)
        self.dispatch()
        call = next(iter(self.host.pending.values()))
        self.assertTrue(call.approval)
        self.host.execute_tool(call, self.executor, self.host.document)
        result = self.host.state["results"][call.id]["result"]
        self.assertTrue(result["success"])
        self.assertTrue(result["scene_changed"])
        self.assertEqual(result["probe_mutation"], "actually-completed")
        self.assertNotIn("error", result)
        self.assert_no_delivery()
        self.assert_settled()

    def test_closed_native_turn_cannot_execute_its_queued_approval(self):
        self.request(name="angelo_create_cube", arguments=CUBE)
        self.dispatch()
        call = next(iter(self.host.pending.values()))
        self.assertTrue(call.approval)
        self.native_finished()
        self.host.execute_tool(call, self.executor, self.host.document)
        self.assertFalse(self.pendings, "the executor must not run the old approved operation")
        self.assertIn("error", self.host.state["results"][call.id]["result"])
        self.assert_no_delivery()
        self.assert_settled()

    def test_scope_lock_is_not_held_through_capture(self):
        barriers = []
        self.start()
        self.on_capture = lambda identity: barriers.append(self.complete_from_worker())
        self.host.pump_events()
        for worker, closed_inside_phase, errors in barriers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertTrue(closed_inside_phase, "native completion was blocked by capture")
            self.assertEqual(errors, [])
        self.assertEqual(len(barriers), 1)
        self.assert_no_delivery()

    def test_scope_lock_is_not_held_through_image_packing(self):
        barriers = []
        self.start()
        self.on_pack = lambda result: barriers.append(self.complete_from_worker())
        self.host.pump_events()
        for worker, closed_inside_phase, errors in barriers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertTrue(closed_inside_phase, "native completion was blocked by image packing")
            self.assertEqual(errors, [])
        self.assertEqual(len(barriers), 1)
        self.assertEqual(len(self.packing), 1)
        self.assertEqual(self.rpc.responses, [])
        self.assertEqual(self.receipts, [])

    def test_closed_owner_poll_needs_no_scope_lock(self):
        self.start()
        self.provider._scope.delivery_owner.close()
        held, release = threading.Event(), threading.Event()
        released_by_main = []

        def hold_scope():
            with self.provider._scope.lock:
                held.set()
                released_by_main.append(release.wait(2))

        worker = threading.Thread(target=hold_scope, daemon=True)
        worker.start()
        try:
            self.assertTrue(held.wait(2))
            self.host.pump_events()
        finally:
            release.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(released_by_main, [True], "polling waited on the provider scope lock")
        self.assertEqual(self.captures, [])
        self.assert_no_delivery()
        self.assert_settled()


class DelegatedDeliveryTests(PendingDeliveryCase):
    """Construct real helpers, but never start their provider thread or timer."""

    BINDING = {"provider": "claude_subscription", "settings": {}, "name": "Synthetic helper",
               "models": [{"id": "synthetic-helper"}]}
    ARGUMENTS = {"connection_id": "2b4a48a4-4cf7-4a7e-aa2d-049af7df5c8e", "model": "synthetic-helper",
                 "task": "Read the synthetic scene and report its shape."}

    def execute(self, name, arguments):
        if name == "angelo_inspect_scene":
            return {"objects": [], "scene": "Synthetic scene"}
        return super().execute(name, arguments)

    def helper(self, identity="parent"):
        replies = []
        self.host.delegation_resolver = lambda connection_id: self.BINDING
        self.context.request_tool(identity, "angelo_delegate", self.ARGUMENTS, replies.append)
        self.dispatch()
        call = next(call for call in self.host.pending.values() if call.id.endswith("/" + identity))
        self.assertTrue(call.approval)
        with patch.object(delegation.NativeDelegate, "start") as start:
            self.host.execute_tool(call, self.executor, self.host.document)
            start.assert_called_once()
        job = self.host.delegate_jobs[call.id]
        self.assertIsInstance(job, delegation.NativeDelegate)
        self.assertFalse(job.timer.is_alive())
        return call, job, replies

    def child(self, job, identity="child-wait", *, dispatch=True):
        replies = []
        job.context.request_tool(identity, "angelo_view", VIEW, replies.append)
        call = next(call for call in self.host.pending.values() if call.id.endswith("/child/" + identity))
        if dispatch:
            self.dispatch()
        return call, replies

    def replacement(self, parent):
        """Adversarial replacement: same key/fields, different actual admission."""
        new_parent = replace(parent, callbacks=[])
        new_job = delegation.NativeDelegate(self.host, new_parent, self.BINDING, {})
        self.host.pending[parent.id] = new_parent
        self.host.delegate_jobs[parent.id] = new_job
        return new_parent, new_job

    def test_helper_child_keeps_exact_parent_fence_and_persisted_key(self):
        parent, job, _ = self.helper()
        child, replies = self.child(job)
        self.assertEqual(job.request_fence, request_fences.RequestFence(parent.epoch, parent.generation))
        self.assertIs(job.context.request_fence, job.request_fence)
        self.assertIs(job.context.delivery_owner, job.delivery_owner)
        self.assertIs(job.delivery_owner.parent, parent.delivery_owner)
        self.assertIs(child.parent_call, parent)
        self.assertIs(child.delivery_owner, job.delivery_owner)
        self.assertEqual(child.id, self.host.state["id"] + "/" + parent.id + "/child/child-wait")
        self.host.pump_events()
        self.assertTrue(replies[0]["success"])
        self.assertIn(child.id, self.host.state["results"])

    def test_closed_executing_helper_stops_timer_provider_and_pending_children(self):
        parent, job, parent_replies = self.helper()
        self.child(job)
        stopped, closed = threading.Event(), threading.Event()
        job.provider = SimpleNamespace(stop=stopped.set, close=closed.set)
        self.assertTrue(parent.executing)
        self.assertFalse(parent.mutation_started)
        self.assertFalse(job.timer.finished.is_set())
        parent.delivery_owner.close()
        self.host.pump_events()
        self.assertTrue(closed.wait(2), "the bounded fake provider did not receive close")
        self.assertTrue(stopped.is_set())
        self.assertTrue(job.timer.finished.is_set())
        self.assertTrue(job.cancelled.is_set())
        self.assertFalse(job.delivery_owner.current(self.host))
        self.assertEqual(self.captures, [])
        self.assertFalse(self.host.delegate_jobs)
        self.assert_settled()
        self.assertEqual(len(parent_replies), 1)
        self.assertIn("error", parent_replies[0])
        self.assertIn("provider", parent_replies[0]["error"])
        self.assertNotIn("no operation was applied", parent_replies[0]["error"])

    def test_same_key_replacement_parent_cannot_revive_old_helper(self):
        parent, job, _ = self.helper()
        replacement, _new_job = self.replacement(parent)
        replies = []
        job.context.request_tool("late-child", "angelo_view", VIEW, replies.append)
        self.assertTrue(job.context.cancelled)
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])
        self.assertIs(self.host.pending[parent.id], replacement)
        self.assertFalse(any(call.parent_call is parent for call in self.host.pending.values()))
        job.cancel()

    def assert_replacement_stops_old_child(self, *, dispatched):
        parent, job, _ = self.helper()
        self.addCleanup(job.cancel)
        child, replies = self.child(job, dispatch=dispatched)
        replacement, new_job = self.replacement(parent)
        if not dispatched:
            self.dispatch()
        self.host.pump_events()
        self.assertEqual(self.captures, [], "an admitted child must still own its exact parent before polling")
        self.assertNotIn(child.id, self.host.pending)
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])
        self.assertIs(self.host.pending[parent.id], replacement)
        self.assertIs(self.host.delegate_jobs[parent.id], new_job)

    def test_same_key_parent_replacement_stops_already_pending_child_capture(self):
        self.assert_replacement_stops_old_child(dispatched=True)

    def test_same_key_parent_replacement_stops_queued_child_execution(self):
        self.assert_replacement_stops_old_child(dispatched=False)

    def test_changed_request_generation_refuses_old_helper_and_child_capture(self):
        parent, job, _ = self.helper()
        child, replies = self.child(job)
        self.host._request_generation += 1
        refused = []
        job.context.request_tool("late-child", "angelo_view", VIEW, refused.append)
        self.assertTrue(job.context.cancelled)
        self.assertIn("error", refused[0])
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])
        self.assertNotIn(child.id, self.host.pending)
        self.assertEqual(job.request_fence.generation, parent.generation)

    def test_helper_completion_closes_pending_child_before_event_is_processed(self):
        parent, job, parent_replies = self.helper()
        child, child_replies = self.child(job)
        job._complete({"reply": "Finished synthetic review", "read_only": True})
        self.assertFalse(job.delivery_owner.current(self.host))
        self.assertIn(parent.id, self.host.pending)
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(len(child_replies), 1)
        self.assertIn("error", child_replies[0])
        self.assertEqual(parent_replies[0]["reply"], "Finished synthetic review")
        self.assertNotIn(child.id, self.host.pending)
        self.assert_settled()

    def test_parent_close_cancels_its_pending_child_without_capture(self):
        parent, _job, _ = self.helper()
        child, child_replies = self.child(self.host.delegate_jobs[parent.id])
        self.host.finish_tool(parent, {"error": "Synthetic parent ended"})
        self.host.pump_events()
        self.assertEqual(self.captures, [])
        self.assertEqual(len(child_replies), 1)
        self.assertIn("error", child_replies[0])
        self.assertNotIn(child.id, self.host.pending)
        self.assert_settled()

    def test_old_completion_event_cannot_finish_same_key_replacement(self):
        parent, old_job, old_replies = self.helper()
        old_job._complete({"reply": "Old result", "read_only": True})
        replacement, new_job = self.replacement(parent)
        self.host.pump_events()
        self.assertIs(self.host.pending[parent.id], replacement)
        self.assertIs(self.host.delegate_jobs[parent.id], new_job)
        self.assertEqual(old_replies, [])
        self.assertFalse(new_job.context.cancelled)

    def test_old_generation_completion_cannot_finish_replacement_parent(self):
        parent, old_job, _ = self.helper()
        old_job._complete({"reply": "Old generation", "read_only": True})
        self.host._request_generation += 1
        replacement = replace(parent, generation=self.host._request_generation, callbacks=[],
                              delivery_owner=request_fences.DeliveryOwner(request_fences.RequestFence.capture(self.host)))
        new_job = delegation.NativeDelegate(self.host, replacement, self.BINDING, {})
        self.host.pending[parent.id] = replacement
        self.host.delegate_jobs[parent.id] = new_job
        self.host.pump_events()
        self.assertIs(self.host.pending[parent.id], replacement)
        self.assertIs(self.host.delegate_jobs[parent.id], new_job)
        self.assertTrue(new_job.delivery_owner.current(self.host))

    def test_duplicate_child_cannot_join_different_parent_even_with_same_owner(self):
        parent, old_job, _ = self.helper()
        child, _ = self.child(old_job, dispatch=False)
        replacement, _new_job = self.replacement(parent)
        replies = []
        self.host.request_tool(parent.id + "/child/child-wait", "angelo_view", VIEW, replies.append,
            request_fence=old_job.request_fence, delivery_owner=child.delivery_owner, parent_call=replacement)
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])
        self.assertIs(self.host.pending[child.id], child)
        self.assertIs(child.parent_call, parent)
        self.assertEqual(len(child.callbacks), 1)
        old_job.cancel()


class ClosedDeliveryAccountingTests(unittest.TestCase):
    def context(self):
        path = request_path.PathFacts()
        host = SimpleNamespace(_epoch=1, _request_generation=2, _closing=False,
                               _request_metrics=SimpleNamespace(path=path))
        fence = request_fences.RequestFence.capture(host)
        return SimpleNamespace(host=host, request_fence=fence), path, request_fences.DeliveryOwner(fence)

    def test_closed_read_callbacks_wake_waiters_without_image_or_step_facts(self):
        read = {"success": True, "read_only": True, "scene_changed": False,
                "views": [{"id": "synthetic-view", "framing": "FINISHED_IMAGE", "shading": "RENDERED"}]}
        for closed in (False, True):
            with self.subTest(closed=closed):
                context, path, owner = self.context()
                answers = []
                callbacks = [request_path.dispatched(context, "angelo_view", VIEW, answers.append,
                                                     delivery_owner=owner),
                             request_path.dispatched(context, "angelo_agent_capabilities", {"family": "SUMMARY"},
                                                     answers.append, delivery_owner=owner)]
                if closed:
                    owner.close()
                callbacks[0](read)
                callbacks[1]({"success": True, "capabilities": []})
                self.assertEqual(len(answers), 2, "closed delivery must still wake host waiters")
                self.assertIs(answers[0], read)
                self.assertEqual(path.edits, 0)
                self.assertEqual(path.images_returned, 0 if closed else 1)
                self.assertEqual(len(path.steps), 0 if closed else 1)
                if closed:
                    self.assertEqual(path._known, {})

    def test_closed_completed_mutation_keeps_edit_facts_without_attached_images(self):
        context, path, owner = self.context()
        outcome = {"success": True, "scene_changed": True,
                   "views": [{"id": "synthetic-result-view", "framing": "AXIS", "shading": "SOLID"}]}
        answers = []
        callback = request_path.dispatched(context, "angelo_create_cube", CUBE, answers.append,
                                           delivery_owner=owner)
        owner.close()
        callback(outcome)
        self.assertEqual(answers, [outcome])
        self.assertIs(answers[0], outcome)
        self.assertEqual(path.edits, 1)
        self.assertEqual(path.images_returned, 0)
        self.assertEqual(path._known, {})
        self.assertEqual(len(path.steps), 1)
        self.assertEqual(path.steps[0][1:3], ["ok", "angelo_create_cube"])


class DeliveryOwnerTests(unittest.TestCase):
    def test_frozen_identity_fields_cannot_be_rebound_to_revive_closed_owner(self):
        host = SimpleNamespace(_epoch=3, _request_generation=7, _closing=False)
        owner = request_fences.DeliveryOwner(request_fences.RequestFence.capture(host))
        owner.close()
        for field, value in (("fence", None), ("parent", None), ("closed_with", threading.Event())):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(owner, field, value)
        self.assertFalse(owner.current(host))

    def test_transport_closed_marker_does_not_wait_for_event_callback(self):
        closed = threading.Event()
        owner = request_fences.DeliveryOwner(closed_with=closed)
        self.assertTrue(owner.current())
        closed.set()
        self.assertFalse(owner.current())

    def test_parent_close_cannot_be_revived_by_a_same_fence_child(self):
        host = SimpleNamespace(_epoch=3, _request_generation=7, _closing=False)
        fence = request_fences.RequestFence.capture(host)
        parent = request_fences.DeliveryOwner(fence=fence)
        child = request_fences.DeliveryOwner(fence=fence, parent=parent)
        self.assertTrue(child.current(host))
        parent.close()
        self.assertFalse(child.current(host))
        later_child = request_fences.DeliveryOwner(fence=fence, parent=parent)
        self.assertFalse(later_child.current(host))

    def test_owner_identity_and_request_fence_are_independent(self):
        host = SimpleNamespace(_epoch=3, _request_generation=7, _closing=False)
        fence = request_fences.RequestFence.capture(host)
        first = request_fences.DeliveryOwner(fence=fence)
        second = request_fences.DeliveryOwner(fence=fence)
        self.assertIsNot(first, second)
        first.close()
        first.close()
        self.assertFalse(first.current(host))
        self.assertTrue(second.current(host))
        host._request_generation += 1
        self.assertFalse(second.current(host))


if __name__ == "__main__":
    unittest.main()
