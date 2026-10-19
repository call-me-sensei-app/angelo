# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A provider thread's admission journal and the UI event pump never interleave on a transcript row.

A tool call admitted on a provider thread (a Claude plan's turn worker, a Codex
reader) saves the conversation there: ``_journal_tool`` -> ``Controller.save``
-> ``SessionStore.save``, which encodes the request's transcript rows (every row,
before the append-only journal). The main-thread event
pump changes rows in place (a plan call's activity row gains its ``result``, a
user row its lazily captured ``checkpoint``). Without a shared lock, a thread
switch inside a row's serialization let the pump resize that row and the
provider thread raised ``RuntimeError: dictionary changed size during
iteration``; the turn failed and its admitted call was cancelled.

These tests force that interleaving without timing luck: a transcript row runs
the real pump on another thread in the middle of its own serialization, the
point where a thread switch happens. The pump either applies its event at once
(the race) or has to wait for the controller lock the save holds. The real
Controller runs; no provider, subprocess or Blender. Each in-place row change
the pump makes is covered: a plan call's result, a lazily captured or voice
checkpoint, a later delta naming its route, and the preview-accept insert,
which raised nothing but made the save write one row twice.

That failure left no traceback anywhere, so a turn that fails on an unexpected
exception now prints one (redacted) to the console; reported provider and plan
failures do not.
"""

import io
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.sessions import read_transcript  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

PLAN_CALL = "claude-web/toolu_plan"
CUBE = {"name": "Race Cube", "size": 1, "location": [0, 0, 0]}
SECRET = "sk-fixture-secret-key"


class WatchedLock:
    """The controller's lock, reporting through ``waiting`` when a thread has to wait for it."""

    def __init__(self, lock, waiting):
        self._lock, self.waiting = lock, waiting

    def acquire(self, blocking=True, timeout=-1):
        if self._lock.acquire(blocking=False):
            return True
        if not blocking:
            return False
        self.waiting.set()
        return self._lock.acquire(True, timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc_info):
        self.release()


class SwitchingRow(dict):
    """A transcript row whose serialization switches to ``other_thread`` after its first field, once."""

    def __init__(self, row, other_thread):
        super().__init__(row)
        self.other_thread = other_thread

    def items(self):
        fields = iter(dict.items(self))
        yield next(fields)
        other, self.other_thread = self.other_thread, None
        if other is not None:
            other()
        yield from fields  # A resize while switched away raises here.


class JournalTranscriptRaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = Controller(self.directory.name, session_id=str(uuid.uuid4()))
        self.addCleanup(self.c.close)
        self.c.set_document(("document", 1))
        self.c._begin_metrics()
        self.c.busy = True
        # The pump made progress: it finished, or it waits for the lock a save holds.
        self.progress = threading.Event()
        self.c.lock = WatchedLock(self.c.lock, self.progress)
        self.pump = None

    def tearDown(self):
        if self.pump is not None:
            self.pump.join(10)
            self.assertFalse(self.pump.is_alive(), "the UI pump never finished")

    def switch_to_pump(self):
        """Run the UI pump on its own thread until it is done or blocked on the controller lock."""
        def pump():
            try:
                self.c.pump_events()
            finally:
                self.progress.set()
        self.pump = threading.Thread(target=pump, name="UI pump stand-in", daemon=True)
        self.pump.start()
        self.assertTrue(self.progress.wait(10), "the UI pump neither finished nor waited")

    def admit_while_row_serializes(self, index):
        """Admit a protected call on this thread while the pump applies its queued event to row ``index``.

        Returns the admission's replies and the journal its save wrote (the first save from here on).
        """
        messages = self.c.state["messages"]
        row = messages[index] = SwitchingRow(messages[index], self.switch_to_pump)
        journals, save = [], self.c.store.save

        def recording_save(state):
            save(state)
            journals.append(read_transcript(self.c.store.path))
        self.c.store.save = recording_save
        replies = []
        self.c.request_tool("race-call", "angelo_create_cube", CUBE, replies.append)
        self.assertIsNone(row.other_thread, "the save never serialized the row")
        self.pump.join(10)
        self.assertFalse(self.pump.is_alive())
        self.c.store.save = save
        return replies, journals[0]

    def assert_admitted_and_journaled(self, replies, journal):
        self.assertEqual(replies, [])  # Admitted: nothing answered at admission.
        self.assertEqual(len(self.c.pending), 1)
        call = next(iter(self.c.pending.values()))
        self.assertEqual(journal["results"][call.id]["phase"], "queued")
        self.assertIn(call.id, journal["invocations"]["entries"])

    def test_a_completed_plan_call_row_waits_for_the_admission_journal(self):
        # The plan call's row exists; its completion is queued when the plan's first operation is admitted.
        self.c.emit("activity", {"id": PLAN_CALL, "tool": "StructuredOutput", "status": "running",
                                 "arguments": {"operations": [{"tool": "angelo_create_cube", "arguments": CUBE}]}})
        self.c.pump_events()
        self.c.emit("activity", {"id": PLAN_CALL, "status": "completed",
                                 "result": {"content": "Structured output provided successfully"}})
        self.assert_admitted_and_journaled(*self.admit_while_row_serializes(self.c._activities[PLAN_CALL]))
        row = self.c.state["messages"][self.c._activities[PLAN_CALL]]
        self.assertEqual((row["status"], row["result"]), ("completed", {"content": "Structured output provided successfully"}))

    def test_a_lazily_captured_checkpoint_waits_for_the_admission_journal(self):
        self.c.emit("message", {"role": "user", "text": "Make a cube"})
        self.c.pump_events()
        index = self.c._request_message_index
        self.assertNotIn("checkpoint", self.c.state["messages"][index])
        self.c.emit("request_checkpoint", {"id": "first-edit-checkpoint", "scope": "before_first_approved_edit",
                                           "generation": self.c._request_generation, "message_index": index})
        self.assert_admitted_and_journaled(*self.admit_while_row_serializes(index))
        self.assertEqual(self.c.state["messages"][index]["checkpoint"], "first-edit-checkpoint")

    def test_a_later_delta_naming_its_route_waits_for_the_admission_journal(self):
        # The first delta made the row; a later one from the request context adds the route keys.
        self.c.emit("delta", {"item": "reply", "text": "Adding"})
        self.c.pump_events()
        index = self.c._items["reply"]
        self.c.emit("delta", {"item": "reply", "text": " a material", "connection_id": str(uuid.uuid4()),
                              "provider": "claude_subscription"})
        self.assert_admitted_and_journaled(*self.admit_while_row_serializes(index))
        row = self.c.state["messages"][index]
        self.assertEqual((row["text"], row["provider"]), ("Adding a material", "claude_subscription"))

    def test_a_voice_request_checkpoint_waits_for_the_admission_journal(self):
        self.c.emit("message", {"role": "user", "text": "Make it red"})
        self.c.pump_events()
        index = self.c._request_message_index
        self.c.checkpoint_factory, self.c.request_checkpoint = (lambda: None), "voice-checkpoint"
        self.c.emit("voice_request", {})
        with patch.object(self.c, "_initial_request_checkpoint", lambda: self.c.request_checkpoint):
            self.assert_admitted_and_journaled(*self.admit_while_row_serializes(index))
        self.assertEqual(self.c.state["messages"][index]["checkpoint"], "voice-checkpoint")

    def test_a_preview_accept_insert_never_journals_a_row_twice(self):
        # The accept intent goes in before its own queued activity; inserting while the save walks the
        # list shifted that row under the save, which then wrote it twice (no exception: a silent duplicate).
        self.c.emit("activity", {"id": "accept-call", "tool": "angelo_accept_preview", "status": "queued", "arguments": {}})
        self.c.emit("message", {"role": "assistant", "text": "A later row"})
        self.c.pump_events()
        index = self.c._activities["accept-call"]
        self.c.emit("message", {"role": "user", "text": "Accept the reviewed preview", "request_kind": "native_preview_accept",
                                "call_id": "accept-call", "preview_id": "preview"})
        replies, journal = self.admit_while_row_serializes(index)
        self.assert_admitted_and_journaled(replies, journal)
        self.assertEqual([m.get("call_id") for m in journal["messages"]].count("accept-call"), 1)
        roles = [(m["role"], m.get("request_kind") or m.get("call_id")) for m in self.c.state["messages"]]
        self.assertEqual(roles[index:index + 3], [("user", "native_preview_accept"), ("activity", "accept-call"),
                                                  ("assistant", None)])


class FailingProvider:
    """Connects warm, then raises from the request the way a host defect would."""

    def __init__(self, failure):
        self.failure, self.closed = failure, False

    def bind_request(self, context):
        self.context = context

    def run(self, native_id, text, observation=None):
        raise self.failure

    def stop_request(self, fence):
        pass

    def close(self):
        self.closed = True


class TurnDefectLogTests(unittest.TestCase):
    """The chat row keeps its text; only an unexpected exception adds a redacted console traceback."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = Controller(self.directory.name, session_id=str(uuid.uuid4()))
        self.addCleanup(self.c.close)
        self.c.set_document(("document", 1))
        self.c.state["native_id"] = "warm-thread"
        self.c.api_key = SECRET
        stand_in = patch.object(self.c.references, "prompt_context", return_value=[])
        stand_in.start()
        self.addCleanup(stand_in.stop)

    def turn(self, failure):
        self.c.provider = FailingProvider(failure)
        worker = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        console = io.StringIO()
        with patch("angelo_agent.controller.threading.Thread", return_value=worker), patch.object(sys, "stderr", console):
            self.c.send("Give the cube a material")
            self.c._run("Give the cube a material")
        self.c.pump_events()
        errors = [m["text"] for m in self.c.state["messages"] if m.get("role") == "error"]
        return errors, console.getvalue()

    def test_an_unexpected_exception_logs_its_redacted_traceback(self):
        errors, console = self.turn(RuntimeError("dictionary changed size during iteration " + SECRET))
        self.assertEqual(errors, ["dictionary changed size during iteration [REDACTED]"])  # Unchanged row text.
        self.assertIn("Traceback (most recent call last):", console)
        self.assertIn("in _run", console)
        self.assertIn("RuntimeError: dictionary changed size during iteration [REDACTED]", console)
        self.assertNotIn(SECRET, console)
        self.assertEqual(self.c.status, "Failed")
        self.assertFalse(self.c.busy)

    def test_a_report_that_fails_never_keeps_the_turn_from_ending(self):
        with patch("angelo_agent.pump_guard.report_defect", side_effect=RuntimeError("console unavailable")):
            errors, _ = self.turn(KeyError("operations"))
        self.assertEqual(errors, ["'operations'"])
        self.assertEqual(self.c.status, "Failed")
        self.assertFalse(self.c.busy)

    def test_a_reported_provider_failure_prints_nothing(self):
        errors, console = self.turn(RpcError("Claude runtime is disconnected; reconnect before continuing"))
        self.assertEqual(errors, ["Claude runtime is disconnected; reconnect before continuing"])
        self.assertEqual(console, "")


if __name__ == "__main__":
    unittest.main()
