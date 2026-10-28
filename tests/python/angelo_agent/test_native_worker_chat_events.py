# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker tells the lead unasked (``worker_chat_events``), and a worker's progress reports in a provider's
plan (``worker_spec.note_reports``).

Pinned here: an event's data reaches the lead as plain JSON values only, every string redacted; events from many
threads leave in the order they were put; a frame too large for the channel becomes a ``dropped`` event, and a closed
channel stops the sender without blocking anyone. ``note_reports`` finds the chat through a provider's
``RuntimeContext`` (whose request state names no chat), so a worker's cube batch with progress reports no longer
raises ``KeyError`` after its cubes committed; a context naming no chat notes nothing.
"""

import sys
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_chat_events as events  # noqa: E402
from angelo_agent import worker_spec  # noqa: E402
from angelo_agent import worker_consent  # noqa: E402


class Endpoint:
    def __init__(self, fail=None):
        self.frames, self.fail = [], fail

    def send(self, value):
        if self.fail is not None:
            self.fail(value)
        self.frames.append(value)


class PlainTests(unittest.TestCase):
    def test_only_plain_values_leave_and_every_string_is_redacted(self):
        redact = lambda text: text.replace("sk-1", "[REDACTED]")
        value = {"text": "key sk-1", "n": 3, "x": 1.5, "inf": float("inf"), "ok": True, "none": None,
                 "list": ("a", object()), "call": lambda: None, "sk-1": 1, "nested": {"deep": ["sk-1"]}}
        self.assertEqual(events.plain(value, redact), {
            "text": "key [REDACTED]", "n": 3, "x": 1.5, "inf": None, "ok": True, "none": None, "list": ["a", None],
            "call": None, "[REDACTED]": 1, "nested": {"deep": ["[REDACTED]"]}})

    def test_an_endless_nesting_is_cut(self):
        value = []
        value.append(value)
        self.assertIsInstance(events.plain(value), list)


class OutboxTests(unittest.TestCase):
    def test_events_leave_in_the_order_they_were_put_from_many_threads(self):
        endpoint = Endpoint()
        outbox = events.Outbox(endpoint)
        barrier = threading.Barrier(4)

        def put(thread):
            barrier.wait()
            for index in range(50):
                outbox.put({"kind": "delta", "data": [thread, index]})
        threads = [threading.Thread(target=put, args=(thread,)) for thread in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(outbox.flush())
        sent = [frame["event"]["data"] for frame in endpoint.frames]
        self.assertEqual(len(sent), 200)
        for thread in range(4):   # Each thread's own events in its order.
            self.assertEqual([index for owner, index in sent if owner == thread], list(range(50)))
        outbox.close()

    def test_a_frame_too_large_becomes_a_dropped_event(self):
        from angelo_agent import external_cli_protocol as protocol

        def fail(value):
            if value["event"]["kind"] == "huge":
                raise protocol.FrameError("too large")
        endpoint = Endpoint(fail)
        outbox = events.Outbox(endpoint)
        outbox.put({"session": "s", "kind": "huge", "data": "x", "t": 1.0})
        outbox.put({"session": "s", "kind": "small", "data": "y", "t": 2.0})
        outbox.flush()
        self.assertEqual([frame["event"]["kind"] for frame in endpoint.frames], ["dropped", "small"])
        self.assertEqual(endpoint.frames[0]["event"]["data"], {"kind": "huge", "reason": "too_large"})
        outbox.close()

    def test_a_closed_channel_stops_the_sender_and_blocks_no_one(self):
        def fail(value):
            raise BrokenPipeError()
        outbox = events.Outbox(Endpoint(fail))
        outbox.put({"kind": "a"})
        self.assertTrue(outbox.flush(1.0))
        self.assertTrue(outbox.broken)
        outbox.put({"kind": "b"})   # Never queued once broken.
        self.assertTrue(outbox.flush(0.1))


class PendingTests(unittest.TestCase):
    def chat(self, *calls):
        import threading
        return SimpleNamespace(pending={call.id: call for call in calls}, state={"id": "chat"}, redact=str,
                               lock=threading.RLock())

    def call(self, **fields):
        values = dict(id="chat/" + uuid.uuid4().hex, name="angelo_create_cube", arguments={"name": "Box"},
                      document=("doc", 1), approval=True, executing=False, authorized=False, automatic=False,
                      approved=False, revoked=None, expires=0.0, usage_label="", precondition=None,
                      invocation_digest="d", program_arguments=None)
        values.update(fields)
        return SimpleNamespace(**values)

    def test_a_card_carries_the_digest_an_approval_must_send_back(self):
        waiting, running = self.call(), self.call(approval=False, executing=True, name="angelo_inspect_scene")
        chat = self.chat(waiting, running)
        cards = {card["id"]: card for card in events.pending_calls(chat, worker_consent)}
        self.assertTrue(cards[waiting.id]["needs_person"])
        self.assertEqual(cards[waiting.id]["digest"], worker_consent.approval_digest(waiting))
        self.assertEqual((cards[running.id]["needs_person"], cards[running.id]["digest"]), (False, None))
        # The lead draws the card from the call's own data, in its own language: no worker-built words.
        self.assertEqual(cards[waiting.id]["summary"], {"arguments": {"name": "Box"}, "program_arguments": None,
                                                        "approval": True, "executing": False, "usage_label": "",
                                                        "recovery_review": False, "precondition": None,
                                                        "recovery_evidence": None})
        self.assertTrue(cards[running.id]["summary"]["executing"])

    def test_expiry_is_seconds_left_never_a_clock_reading(self):
        import time
        call = self.call(expires=time.monotonic() + 30)
        expires = events.pending_calls(self.chat(call), worker_consent)[0]["expires"]
        self.assertTrue(25 < expires <= 30)
        self.assertEqual(events.pending_calls(self.chat(self.call(expires=0.0)), worker_consent)[0]["expires"], 0.0)

    def test_the_fingerprint_moves_when_a_call_or_its_binding_changes(self):
        call = self.call()
        chat = self.chat(call)
        before = events.fingerprint(chat)
        call.precondition = object()
        self.assertNotEqual(events.fingerprint(chat), before)
        self.assertNotEqual(events.fingerprint(self.chat()), events.fingerprint(chat))


class NoteReportsTests(unittest.TestCase):
    REPORT = {"tool": "angelo_workers_report", "arguments": {"done": 1, "steps": ["Legs"]}}

    def notes(self, controller):
        noted = []
        with patch("angelo_agent.worker_spec.validate_worker", lambda name, arguments: arguments), \
                patch("angelo_agent.parallel_sessions.note_progress", lambda *args: noted.append(args), create=True), \
                patch.object(worker_spec, "PART_NAMES", frozenset({"angelo_workers_report"})):
            worker_spec.note_reports([self.REPORT, {"tool": "angelo_create_cube", "arguments": {}}], controller)
        return noted

    def test_a_providers_runtime_context_finds_its_chat_through_its_host(self):
        from angelo_agent.routing import RuntimeContext
        host = SimpleNamespace(state={"id": "chat-7", "native_id": None, "options": {}}, _request_generation=0,
                               store=None, workspace=None, references=None, models=[])
        context = RuntimeContext(host, "codex_subscription", {}, "", None, 0)
        self.assertNotIn("id", context.state)   # The latent KeyError's cause, at the base commit.
        self.assertEqual(self.notes(context), [("chat-7", 1, ["Legs"])])

    def test_the_chat_itself_and_a_context_naming_no_chat(self):
        self.assertEqual(self.notes(SimpleNamespace(state={"id": "chat-8"})), [("chat-8", 1, ["Legs"])])
        for controller in (None, SimpleNamespace(state={}), SimpleNamespace(host=None, state={"provider": "x"}),
                           SimpleNamespace(host=SimpleNamespace(state={"id": ""}))):
            with self.subTest(controller=controller):
                self.assertEqual(self.notes(controller), [])


if __name__ == "__main__":
    unittest.main()
