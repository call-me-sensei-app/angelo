# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's mirror of a worker-hosted chat (design A′, C9): its state is the chat file's and its worker's
forwarded events, its actions are chat ops, and it never does a chat's work itself. No Blender.

The "worker" here is a plain ``Controller`` on the same chat file, the single writer: each scripted step emits on
it, pumps and saves it as a worker's main loop does, and forwards what it pumped, then a journal frame, to the mirror.
"""

import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import worker_consent_lead, worker_mirror, worker_mirror_events, worker_mirror_ops  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.worker_mirror import LocalWork, MirrorController, MirrorRefused  # noqa: E402

TURN = {"_epoch": 0, "_generation": 1}


class Ops:
    """The ``send_op`` seam's fake: every op recorded; answers ok unless told otherwise."""

    def __init__(self):
        self.calls, self.answer = [], {"ok": True}

    def __call__(self, session, op, **fields):
        fields.pop("request_id", None)   # An asynchronous op's id (worker_mirror_ops): checked in its own tests.
        fields.pop("lead_seq", None)     # The person's order of the chat's ops: checked in its own tests too.
        self.calls.append((session, op, fields))
        return self.answer(op) if callable(self.answer) else self.answer

    def ops(self):
        return [op for _session, op, _fields in self.calls]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.session = str(uuid.uuid4())
        self.worker = Controller(self.directory, session_id=self.session)
        self.worker.save()
        self.ops = Ops()
        worker_mirror_ops.bind(spawn=lambda run: run())   # Lanes run inline here; their threads in their own tests.
        self.addCleanup(worker_mirror_ops.reset)
        self.mirror = MirrorController(self.directory, self.session, self.ops)
        self.router = worker_mirror.EventRouter()
        self.router.open(self.mirror)
        self.seq = 0
        # Approve goes only through the consent lane (``worker_consent_lead.approve``), inline, to the same fake.
        worker_consent_lead.reset()
        self.addCleanup(worker_consent_lead.reset)
        worker_consent_lead.bind(spawn=lambda run: run(), connection=lambda controller: True, send=lambda session,
                                 request, timeout: self.ops(session, request["op"], **{
                                     key: value for key, value in request.items() if key not in ("op", "session")}))

    def lane(self):
        """The mirror registered with the consent lane, as its ``chat.open`` registers it (its cards are noted)."""
        worker_consent_lead.register(self.mirror)

    def approve(self, call_id):
        """The person's Approve of the card as drawn (``chat_card_draw`` calls ``drew``)."""
        worker_consent_lead.drew(self.mirror, call_id)
        return self.mirror.approve_call(call_id)

    def frame(self, kind, data):
        self.router({"session": self.session, "kind": kind, "data": data, "t": 0.0})

    def worker_step(self, *emitted, save=True):
        """The worker emits, pumps and saves; each event goes out as it is pumped, then the journal frame."""
        self.worker._request_generation, self.worker.busy = 1, True
        for kind, data in emitted:
            self.worker.emit(kind, dict(data, **TURN))
            self.frame(kind, dict(data, **TURN))
        self.worker.pump_events()
        if save:
            self.worker.save()
            self.seq += 1
            self.frame("journal", {"seq": self.seq, "path": str(self.worker.store.path)})

    def pending(self, *calls):
        self.frame("pending", {"calls": list(calls)})

    def loaded(self):
        return Controller(self.directory, session_id=self.session)


class FullTurnTests(Fixture):
    def test_a_scripted_turn_streams_shows_a_card_routes_its_digest_and_ends_equal_to_the_file(self):
        self.lane()
        self.mirror.send("Make a chair")
        self.assertEqual(self.ops.calls[-1], (self.session, "chat.send",
                                              {"text": "Make a chair", "attachments": [], "host_request": None}))
        self.assertTrue(self.mirror.busy)
        self.worker_step(("message", {"role": "user", "text": "Make a chair"}))
        self.mirror.pump_events()
        self.assertEqual(self.mirror._request_message_index, 0)   # The turn's request row ("Worked for", live row).
        self.worker_step(("delta", {"item": "a", "text": "Build"}), save=False)
        self.worker.emit("delta", dict({"item": "a", "text": "ing"}, **TURN))
        self.frame("delta", dict({"item": "a", "text": "ing"}, **TURN))   # Shown before the worker saved any of it.
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1], {"role": "assistant", "text": "Building"})
        self.pending({"id": "c1", "name": "angelo_create_cube", "digest": "d" * 64, "needs_person": True,
                      "expires": 120, "summary": {"arguments": {"name": "Seat", "size": 1}, "approval": True}})
        self.mirror.pump_events()
        call = self.mirror.pending["c1"]
        self.assertEqual((call.name, call.arguments, call.approval), ("angelo_create_cube", {"name": "Seat", "size": 1},
                                                                      True))
        worker_consent_lead.drew(self.mirror, call.id)
        self.assertTrue(self.mirror.approve_tool(call))
        self.assertEqual(self.ops.calls[-1], (self.session, "chat.approve", {"call_id": "c1", "digest": "d" * 64}))
        self.pending()
        self.worker_step(("delta", {"item": "a", "text": ", done."}),
                         ("finished", {"status": "completed"}))
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        self.assertEqual(self.mirror.status, "Completed")
        self.assertEqual(self.mirror.pending, {})
        self.assertEqual(self.mirror.state, self.loaded().state)
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Building, done.")

    def test_a_live_row_the_file_has_not_caught_up_with_keeps_its_streamed_text_while_the_turn_runs(self):
        self.mirror.begin_turn()
        self.worker_step(("delta", {"item": "a", "text": "Hel"}))
        self.frame("delta", dict({"item": "a", "text": "lo"}, **TURN))   # Emitted after the worker's pump.
        self.worker.save()
        self.frame("journal", {"seq": 2})   # Its save lacks "lo": the row keeps it until the next save does.
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Hello")

    def test_several_journal_frames_in_one_pump_read_the_file_once(self):
        for text in ("one", "two", "three"):
            self.worker_step(("message", {"role": "system", "text": text}))
        with mock.patch.object(worker_mirror_events, "reload", wraps=worker_mirror_events.reload) as reload:
            self.mirror.pump_events()
        self.assertEqual(reload.call_count, 1)
        self.assertEqual([m["text"] for m in self.mirror.state["messages"]], ["one", "two", "three"])
        self.assertEqual(self.mirror.journal_seq, 3)

    def test_an_event_of_an_older_request_is_never_shown(self):
        self.frame("status", {"text": "Now", "_epoch": 0, "_generation": 2})
        self.frame("status", {"text": "Stale", "_epoch": 0, "_generation": 1})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.status, "Now")

    def test_a_worker_turn_the_mirror_did_not_send_starts_its_live_row(self):
        self.frame("thinking", dict({"item": "t", "text": "Plan"}, **TURN))
        self.mirror.pump_events()
        self.assertTrue(self.mirror.busy)
        self.assertEqual(self.mirror.state["messages"][-1]["role"], "thinking")
        self.frame("finished", dict({"status": "completed"}, **TURN))
        self.frame("status", dict({"text": "late"}, **TURN))   # Its own late event: the turn stays ended.
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        self.assertEqual(self.mirror.state["messages"][-1]["status"], "completed")


class ActionTests(Fixture):
    def card(self, digest="a" * 64, needs_person=True, **summary):
        self.pending({"id": "c1", "name": "angelo_execute_python", "digest": digest, "needs_person": needs_person,
                      "expires": 60, "summary": dict({"arguments": {"code": "print(1)"}}, **summary)})
        self.mirror.pump_events()
        return self.mirror.pending["c1"]

    def test_stop_decline_options_and_consent_route_as_chat_ops(self):
        self.mirror.begin_turn()
        self.mirror.stop(reason="ended")
        self.mirror.decline_tool("c1")
        self.mirror.set_options(model="m", approval="ASK")
        self.mirror.set_consent(3, {"auto": True})
        self.assertEqual(self.ops.calls, [
            (self.session, "chat.stop", {"reason": "ended"}), (self.session, "chat.decline", {"call_id": "c1"}),
            (self.session, "chat.options", {"options": {"model": "m", "approval": "ASK"}}),
            (self.session, "chat.consent", {"epoch": 3, "grants": {"auto": True}})])
        self.assertTrue(self.mirror.cancelled)
        self.assertEqual((self.mirror.stops, self.mirror.status), (1, "Stopping"))
        self.assertTrue(self.mirror.busy)   # Until the worker's ``finished`` says the turn ended.

    def test_a_message_the_worker_never_sent_goes_back_where_the_person_types(self):
        self.mirror.send("Make a lamp")
        self.frame("finished", {"status": "failed", "not_sent": True, "_epoch": 0, "_generation": 1})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.unsent_draft, "Make a lamp")
        self.assertEqual(self.mirror.turn_outcome[:3], ("failed", False, True))

    def test_an_op_the_worker_did_not_accept_refuses_and_nothing_is_assumed(self):
        for answer in ({"error": "gone"}, {"ok": False}):   # A Send is answered later (worker_mirror_ops).
            self.ops.answer = answer
            self.mirror.send("hi")
            worker_mirror_ops.reconcile()
            self.assertFalse(self.mirror.busy)
            self.assertEqual(self.mirror.unsent_draft, "hi")
        self.mirror.send_op = None
        with self.assertRaises(MirrorRefused):
            self.mirror.decline_tool("c1")
        self.mirror.stop()   # A Stop never fails: what the worker did not take is kept for the kill escalation.
        self.assertIn("not open", self.mirror.stop_refused)
        self.assertTrue(self.mirror.cancelled)

    def test_approve_sends_the_digest_drawn_and_never_twice(self):
        self.lane()
        call = self.card()
        self.mirror.review_script(call)
        self.assertTrue(self.mirror.script_authorized(call))   # The operator's own review gate works on the card.
        self.approve("c1")
        with self.assertRaises(MirrorRefused):
            self.approve("c1")
        self.assertEqual(self.ops.ops(), ["chat.approve"])

    def test_a_changed_digest_is_a_new_card_that_needs_a_new_review(self):
        self.lane()
        first = self.card()
        self.mirror.review_script(first)
        second = self.card(digest="b" * 64)
        self.assertIsNot(first, second)
        self.assertFalse(self.mirror.script_authorized(second))
        self.approve("c1")
        self.assertEqual(self.ops.calls[-1][2], {"call_id": "c1", "digest": "b" * 64})
        self.assertIs(self.card(digest="b" * 64), second)   # The same digest keeps the card (and its review).

    def test_a_malformed_or_unsigned_summary_is_never_a_card(self):
        self.pending({"id": "c1", "name": "x", "digest": "", "needs_person": True},
                     {"id": 2, "name": "y", "digest": "z"}, "junk")
        self.mirror.pump_events()
        self.assertEqual(self.mirror.pending, {})
        with self.assertRaises(MirrorRefused):
            self.mirror.approve_call("c1")
        # A call running (or admitted without the person) has no digest: shown, never approvable.
        self.pending({"id": "c2", "name": "x", "digest": None, "needs_person": False, "summary": {"executing": True}})
        self.mirror.pump_events()
        self.assertTrue(self.mirror.writing())
        with self.assertRaises(MirrorRefused):
            self.mirror.approve_call("c2")

    def test_an_expired_card_is_not_approved_here(self):
        call = self.card()
        call.expires = 0
        self.assertTrue(self.mirror.expire_tool(call))

    def emit_notice(self, text):
        """A notice is sent off the main thread (a wedged worker never blocks the window): wait for its op."""
        before = len(self.ops.calls)
        self.mirror.emit("message", {"role": "system", "text": text, "notice": True})
        deadline = time.monotonic() + 5
        while len(self.ops.calls) == before and time.monotonic() < deadline:
            time.sleep(0.002)
        time.sleep(0.01)

    def test_a_lead_notice_goes_to_the_worker_and_is_shown_here_only_when_refused(self):
        self.emit_notice("Stopped.")
        self.assertEqual(self.ops.calls[-1], (self.session, "chat.notice",
                                              {"message": {"role": "system", "text": "Stopped.", "notice": True}}))
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"], [])   # The worker writes it; the journal brings it.
        self.ops.answer = None
        self.emit_notice("Shown.")
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Shown.")


class LocalWorkTests(Fixture):
    def test_work_a_chat_does_locally_refuses_and_nothing_runs(self):
        for name, args in (("request_tool", ("c", "angelo_create_cube", {}, print)), ("finish_tool", (None, {})),
                           ("start_voice", ()), ("runtime_context", ()),
                           ("_start_turn", ("x", None)), ("_connect_runtime", (None,))):
            with self.subTest(name=name), self.assertRaises(LocalWork):
                getattr(self.mirror, name)(*args)
        self.assertIsNone(self.mirror.provider)

    def test_it_never_writes_the_chat_file_and_its_store_refuses_writes(self):
        before = self.worker.store.path.read_bytes()
        self.mirror.state["messages"].append({"role": "system", "text": "local"})
        self.mirror.save()
        self.mirror.save(compact=True)
        for name in ("save", "compact", "leave"):
            with self.subTest(name=name), self.assertRaises(LocalWork):
                getattr(self.mirror.store, name)(self.mirror.state)
        self.assertEqual(self.worker.store.path.read_bytes(), before)

    def test_the_ui_pump_and_document_changes_find_nothing_to_do(self):
        self.assertFalse(self.mirror.pump_tools(None, None, budget=ToolDispatchBudget(seconds=60)))
        self.assertFalse(self.mirror.has_dispatchable_tools)
        self.mirror.set_document(("file", 1))
        self.mirror.cancel_tools("undo")
        self.assertEqual(self.mirror.rebind_after_restore(("file", 1), "cp"), {"warnings": []})
        self.assertIsNone(self.mirror.document)

    def test_it_holds_no_key(self):
        self.mirror.api_key = "sk-secret"
        self.assertEqual(self.mirror.api_key, "")


class FanInTests(Fixture):
    def test_frames_for_a_chat_the_worker_was_not_opened_on_are_dropped(self):
        self.router({"session": str(uuid.uuid4()), "kind": "status", "data": {"text": "x"}})
        self.router({"kind": "status"})
        self.router("junk")
        self.mirror.pump_events()
        self.assertEqual(self.router.dropped, 3)
        self.assertNotEqual(self.mirror.status, "x")

    def test_fields_beside_kind_are_read_as_data(self):
        self.router({"session": self.session, "kind": "status", "text": "flat", "t": 1})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.status, "flat")

    def test_the_channel_reader_routes_events_in_order_and_still_answers_requests(self):
        import socket
        import threading
        from angelo_agent import worker_channel
        from angelo_agent.headless_pipe import Writer
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        got, bad = [], [True]

        def on_event(body):
            if bad[0]:
                bad[0] = False
                raise ValueError("a bad callback")   # One failure never ends the reader.
            got.append(body["data"]["n"])
        client = worker_channel.Client(worker_channel.socket_endpoint(ours, 1 << 20, 1 << 20), on_event=on_event)
        self.addCleanup(client.close)
        worker = worker_channel.socket_endpoint(theirs, 1 << 20, 1 << 20)
        writer = Writer(theirs.makefile("wb", buffering=0), 1 << 20)

        def serve():
            frame = worker.receive()
            for n in range(5):
                writer.send({"event": {"session": "s", "kind": "status", "data": {"n": n}}})
            writer.send({"seq": frame["seq"], "response": {"ok": True}})
            writer.send({"event": {"session": "s", "kind": "status", "data": {"n": 5}}})
        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(client.request({"op": "hello"}, 5.0), {"ok": True})
        deadline = __import__("time").monotonic() + 5
        while len(got) < 5 and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.01)
        self.assertEqual(got, [1, 2, 3, 4, 5])


class ReaderTests(Fixture):
    def setUp(self):
        super().setUp()
        self.ui = SimpleNamespace(_controllers={})
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.ui": self.ui}),
                        mock.patch.object(angelo_agent, "ui", self.ui, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        worker_mirror.register(self.mirror)

    def test_registration_and_worker_chat_readers(self):
        from angelo_agent import worker_routing
        self.assertIs(self.ui._controllers[self.session], self.mirror)
        self.assertTrue(worker_routing.worker_snapshots(self.mirror))
        self.assertTrue(worker_routing.hosted(self.mirror))
        self.assertFalse(worker_routing.hosted(self.worker))
        self.assertIsInstance(worker_routing.prepare_turn(self.mirror, None, "hi"), worker_mirror.HostedTurn)

    def test_finished_and_awaiting_person_read_the_mirror(self):
        from angelo_agent import worker_news, worker_sessions
        self.assertFalse(worker_sessions.finished(self.session))   # No brief has reached it yet.
        self.worker_step(("message", {"role": "user", "text": "brief"}), ("finished", {"status": "completed"}))
        self.mirror.pump_events()
        self.assertTrue(worker_sessions.finished(self.session))
        self.pending({"id": "c1", "name": "angelo_create_cube", "digest": "d", "needs_person": True,
                      "summary": {"arguments": {}}},
                     {"id": "c2", "name": "angelo_create_cube", "digest": "e", "needs_person": False,
                      "summary": {"arguments": {}}})
        self.mirror.pump_events()
        self.assertFalse(worker_sessions.finished(self.session))
        self.assertTrue(worker_sessions.awaiting_person(self.session))
        with mock.patch.object(worker_sessions, "_started", {"lead": [self.session]}):
            self.assertEqual(worker_news.cards("lead"), [(self.session, frozenset({"c1", "c2"}))])
        self.pending({"id": "c2", "name": "angelo_create_cube", "digest": "e", "needs_person": False,
                      "summary": {"arguments": {}}})
        self.mirror.pump_events()
        self.assertFalse(worker_sessions.awaiting_person(self.session))   # Auto runs it in the worker.

    def test_deliver_sends_a_hosted_chats_message_without_a_lead_window(self):
        from angelo_agent import worker_queue, worker_routing
        sent, errors = [], []
        with mock.patch.object(worker_queue, "resume", lambda *_a: False):
            worker_routing.deliver(self.mirror, None, "brief",
                                   lambda turn: (sent.append(turn), self.mirror.send("brief",
                                                 observation=turn.observation)), errors.append)
        self.assertEqual((len(sent), errors), (1, []))
        self.assertEqual(self.ops.ops(), ["chat.send"])

    def test_live_progress_reads_forwarded_signs_never_a_provider(self):
        from angelo_agent import live_progress
        self.mirror.send("go")
        facts = live_progress.facts(self.mirror)
        self.assertEqual((facts.watched, facts.accepted, facts.last_model), (True, False, None))
        self.frame("delta", dict({"item": "a", "text": "x"}, **TURN))
        self.frame("signs", {"heard_ago": 0.5})
        self.mirror.pump_events()
        facts = live_progress.facts(self.mirror)
        self.assertTrue(facts.accepted)
        self.assertIsNotNone(facts.last_model)
        self.assertIsNotNone(facts.last_heard)
        self.assertIsNotNone(facts.started)

    def test_progress_news_reaches_the_tile(self):
        from angelo_agent import parallel_sessions
        with mock.patch.object(parallel_sessions, "note_progress") as note:
            self.frame("news", {"progress": {"done": 2, "steps": ["a", "b", "c"]}})
            self.mirror.pump_events()
        note.assert_called_once_with(self.session, 2, ["a", "b", "c"])

    def test_usage_and_telemetry_records_go_to_the_records_seam(self):
        seen = []
        self.mirror.records = lambda kind, data: seen.append((kind, data))
        self.frame("usage", {"receipt": 1})
        self.frame("telemetry", {"receipt": 2})
        self.mirror.pump_events()
        self.assertEqual(seen, [("usage", {"receipt": 1}), ("telemetry", {"receipt": 2})])


class SwitchTests(unittest.TestCase):
    def test_unset_or_one_is_a_prime_and_zero_keeps_the_lanes(self):
        for value, expected in ((None, True), ("1", True), ("0", False), (" 0 ", False)):
            environment = {k: v for k, v in os.environ.items() if k != "ANGELO_WORKER_CHAT_HOST"}
            if value is not None:
                environment["ANGELO_WORKER_CHAT_HOST"] = value
            with self.subTest(value=value), mock.patch.dict(os.environ, environment, clear=True):
                self.assertIs(worker_mirror.enabled(), expected)


if __name__ == "__main__":
    unittest.main()
