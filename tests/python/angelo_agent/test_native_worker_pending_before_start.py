# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's every call reaches the lead as a ``pending`` event before it starts (``worker_chat_host``), however
the provider thread's admission falls against the worker's pump, and announcing it costs at most one send per call.

The pump looks at each chat's pending calls (``WorkerChatHost.observe``) before and after its tool step. A call the
provider thread admits after that first look and before the tool step used to start, finish and leave within the one
pump, so the lead never heard it pending: no card, nothing to approve against (the full host suite on 5da3e1c and
19011dd, ``test_a_hosted_chat_runs_a_whole_turn_in_the_worker_and_the_lead_hears_all_of_it``, 3 runs of 3; the
instrumented run showed Strict script protection off and no ``_bpy`` stand-in, so the timing, not Strict's flag).
Every start of a call goes through ``_tool_available`` last, so a hosted chat announces the call there, but only one
the lead has not heard of: the first fix (842cda7) sent the whole pending list at every start check whose fingerprint
had moved, several full sends per call and O(n^2) for a batch (the break-it read of 842cda7, SHOULD-1: 134 events for
32 automatic edits against 36 before it). If announcing fails, the call is refused with nothing run, never started
unannounced nor left dequeued (NIT-1, NIT-2).

The interleaving is forced, not waited for: the provider thread is held until the pump's tool step begins, then
admits its call, and only then does the tool step run. Both routes: the read-only queue drain and an automatic edit
(Auto with the lead's lease, through the approve operator).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import contextlib
import io
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from test_native_worker_chat_host import FakeProvider, Fixture  # noqa: E402

AUTO = dict(options={"approval": "AUTO_BOUNDED"}, consent={"epoch": 1, "grants": {"auto": True}})
INSPECT = ("angelo_inspect_scene", {})


def edit(index=0):
    return ("angelo_create_cube", {"name": "Box%d" % index, "size": 1.0, "location": [index, 0, 0]})


class HeldProvider(FakeProvider):
    """``FakeProvider`` whose tool requests wait until the test lets them go."""
    release = None

    def run(self, native_id, text, observation=None):
        if self.release is not None:
            self.release.wait(CAP_S)
        return super().run(native_id, text, observation)


class BatchProvider(FakeProvider):
    """Every call requested at once, each with its own id (a model's parallel tool calls), then all waited for; held
    as ``HeldProvider`` is."""

    def run(self, native_id, text, observation=None):
        if HeldProvider.release is not None:
            HeldProvider.release.wait(CAP_S)
        self.results, done = [], threading.Semaphore(0)
        for index, (name, arguments) in enumerate(self.tools):
            self.controller.request_tool("%s-%d" % (name, index), name, arguments,
                                         lambda result: (self.results.append(result), done.release()))
        for _ in self.tools:
            if not done.acquire(timeout=CAP_S):
                raise RuntimeError("A tool never answered")
        self.controller.emit("message_complete", {"item": "reply", "text": "done"})
        return "completed"


class PendingFixture(Fixture):
    provider, hold = HeldProvider, True   # ``hold``: the provider thread waits until the test lets it go.

    def setUp(self):
        super().setUp()
        for started in self.patches:
            started.stop()
        self.patches = [patch("angelo_agent.controller.CodexProvider", self.provider),
                        patch("angelo_agent.controller.ClaudeProvider", self.provider)]
        for started in self.patches:
            started.start()
        HeldProvider.release = threading.Event()
        self.addCleanup(setattr, HeldProvider, "release", None)
        if not self.hold:
            HeldProvider.release.set()
        self.log = []   # What the worker sent, in the order it was put, and when the executor ran a call.
        outbox, put = self.chats.outbox, self.chats.outbox.put
        outbox.put = lambda event: (self.log.append(("event", event)), put(event))[1]
        execute = self.executor.execute
        self.executor.execute = lambda name, arguments: (self.log.append(("ran", name)), execute(name, arguments))[1]

    def announced_before_each_run(self):
        """For each run, in order: how many distinct calls a pending event had named before it."""
        named, counts = set(), []
        for kind, value in self.log:
            if kind == "ran":
                counts.append(len(named))
            elif value.get("kind") == "pending":
                named.update(call["id"] for call in value["data"]["calls"])
        return counts

    def needs_person_when_run(self, name):
        """What the last pending event naming ``name``'s call said of it (``needs_person``) when it ran."""
        last = None
        for kind, value in self.log:
            if kind == "ran" and value == name:
                return last
            if kind == "event" and value.get("kind") == "pending":
                for call in value["data"]["calls"]:
                    if call["name"] == name:
                        last = call["needs_person"]
        self.fail("%s never ran" % name)

    def admit_inside_the_pump(self):
        """The pump's tool step: first the provider thread admits its call (after the pump's look at the pending
        calls), then the step runs."""
        from angelo_agent.tool_pump import pump_controller_tools
        held = {"done": False}

        def step(*args, **kwargs):
            chat = self.chats.hosted(self.session)
            if not held["done"] and chat.busy:
                held["done"] = True
                HeldProvider.release.set()
                deadline = time.monotonic() + CAP_S
                while chat.tools.qsize() == 0 and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertGreater(chat.tools.qsize(), 0, "the provider thread never admitted its call")
            return pump_controller_tools(*args, **kwargs)
        self.chats.pump_tools = step

    def pending_names_before(self, marker):
        names = []
        for kind, value in self.log:
            if kind == "ran" and value == marker:
                return names
            if kind == "event" and value.get("kind") == "pending":
                names += [call["name"] for call in value["data"]["calls"]]
        self.fail("%s never ran: %r" % (marker, [(k, v if k == "ran" else v.get("kind")) for k, v in self.log]))

    def pending_events(self):
        return [event["data"] for event in self.endpoint.events("pending") if event["session"] == self.session]

    def turn(self, tools, **opened):
        self.provider.tools = tools
        self.assertTrue(self.open(**opened)["ok"])
        self.assertEqual(self.ask("chat.send", text="go", attachments=[])["ok"], True)
        return self.run_turn(timeout=30)


class AdmittedDuringThePumpTests(PendingFixture):
    def test_a_read_only_call_admitted_after_the_pumps_look_is_pending_at_the_lead_before_it_runs(self):
        self.admit_inside_the_pump()
        self.turn((INSPECT,))
        self.assertEqual(self.executor.calls, [INSPECT])
        self.assertIn("angelo_inspect_scene", self.pending_names_before("angelo_inspect_scene"))
        self.assertEqual(self.pending_events()[-1]["calls"], [])   # And none once it ran.

    def test_an_automatic_edit_admitted_after_the_pumps_look_is_pending_at_the_lead_before_it_runs(self):
        self.admit_inside_the_pump()
        self.turn((edit(),), **AUTO)
        self.assertEqual(self.executor.calls, [edit()])   # Auto, with the lease: through the approve operator.
        self.assertEqual([call_id.rsplit("/", 1)[-1] for call_id in self.bpy.approved], ["angelo_create_cube"])
        self.assertIn("angelo_create_cube", self.pending_names_before("angelo_create_cube"))
        self.assertEqual(self.pending_events()[-1]["calls"], [])

    def test_a_call_admitted_before_the_pumps_look_gains_no_repeated_event(self):
        """The usual order (admitted before the pump looks): announced as before, never the same event twice."""
        HeldProvider.release.set()
        self.turn((INSPECT,))
        self.assertIn("angelo_inspect_scene", self.pending_names_before("angelo_inspect_scene"))
        sent = self.pending_events()
        self.assertTrue(all(a != b for a, b in zip(sent, sent[1:])), "a pending event sent twice in a row: %r" % sent)


class AnnouncingCostTests(PendingFixture):
    """At most one send per call from the start checks; the pump's own sends follow the calls' changes. Ceiling: one
    per call plus 6 (36 for 32 calls before 842cda7, 134 with it; a send per start check is a second per call)."""
    provider, hold = BatchProvider, False

    def ceiling(self, tools, **opened):
        self.turn(tools, **opened)
        self.assertEqual(len(self.executor.calls), len(tools))
        sent = len(self.pending_events())
        self.assertLessEqual(sent, len(tools) + 6, "%d pending events for %d calls" % (sent, len(tools)))
        self.assertEqual(self.pending_events()[-1]["calls"], [])

    def test_a_batch_of_automatic_edits_costs_at_most_two_events_a_call(self):
        self.ceiling(tuple(edit(index) for index in range(16)), **AUTO)

    def test_a_batch_of_read_only_calls_costs_at_most_two_events_a_call(self):
        self.ceiling((INSPECT,) * 16)


class AnnouncingFailsTests(PendingFixture):
    def test_a_failed_announcement_refuses_the_call_with_nothing_run_and_says_so(self):
        """NIT-1 and NIT-2: a host whose announcing raises (a missing observe raises the same way) refuses the call:
        it never starts unannounced, is never left dequeued, and the defect is printed."""
        real = self.chats.observe

        def failing(chat, starting=None):
            if starting is not None:
                raise AttributeError("observe")
            return real(chat)
        self.chats.observe = failing
        HeldProvider.release.set()
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            self.turn((edit(),), **AUTO)
        self.assertEqual(self.executor.calls, [])
        result = FakeProvider.turned()[0].results[0]
        self.assertEqual((result["error_code"], result["scene_changed"]), ("INTERNAL_ERROR", False))
        self.assertIn("HostedController._tool_available", printed.getvalue())
        self.assertEqual(self.endpoint.events("finished")[-1]["data"]["status"], "completed")



class AnnouncingRecordTests(PendingFixture):
    """The record of what the lead heard is written only once it was sent (the break-it read of 9598efc, MUST): a
    send that fails records nothing, so no other call is taken as heard and starts unannounced."""
    provider = BatchProvider

    def failing_once(self):
        from angelo_agent import worker_chat_events
        real, state = worker_chat_events.pending_calls, {"armed": False, "raised": 0}

        def pending_calls(*args, **kwargs):
            if state["armed"] and not state["raised"]:
                state["raised"] += 1
                raise RuntimeError("a card could not be built")
            return real(*args, **kwargs)
        started = patch.object(worker_chat_events, "pending_calls", pending_calls)
        started.start()
        self.addCleanup(started.stop)
        return state

    def test_a_failed_announcement_at_a_start_leaves_the_other_calls_unheard(self):
        state = self.failing_once()
        from angelo_agent.tool_pump import pump_controller_tools

        def step(*args, **kwargs):   # Both calls admitted after the pump's look; the next send fails, once.
            chat = self.chats.hosted(self.session)
            if not state["armed"] and chat.busy:
                HeldProvider.release.set()
                deadline = time.monotonic() + CAP_S
                while chat.tools.qsize() < 2 and time.monotonic() < deadline:
                    time.sleep(0.001)
                state["armed"] = True
            return pump_controller_tools(*args, **kwargs)
        self.chats.pump_tools = step
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            self.turn((INSPECT, INSPECT))
        self.assertEqual(state["raised"], 1)
        results = FakeProvider.turned()[0].results
        self.assertEqual(sorted(result.get("error_code", "ran") for result in results), ["INTERNAL_ERROR", "ran"])
        counts = self.announced_before_each_run()
        self.assertEqual(len(counts), 1)
        self.assertGreaterEqual(counts[0], 1, "a call ran that no pending event had named")

    def test_a_failed_send_by_the_pump_records_nothing(self):
        state = self.failing_once()
        HeldProvider.release.set()
        self.assertTrue(self.open()["ok"])
        chat = self.chats.hosted(self.session)
        from angelo_agent.controller import ToolCall
        call = ToolCall("c-1", "angelo_inspect_scene", {}, chat.document, [lambda result: None])
        chat.pending[call.id] = call   # A change to send: the pump's look builds the event.
        self.addCleanup(chat.pending.pop, call.id, None)
        before = dict(self.chats._pending)
        state["armed"] = True
        with self.assertRaises(RuntimeError):
            self.chats.observe(chat)
        self.assertEqual(self.chats._pending, before)
        self.chats.observe(chat)   # The next look sends it: nothing was taken as sent.
        self.assertIn("c-1", [call["id"] for kind, event in self.log if kind == "event" and event["kind"] == "pending"
                               for call in event["data"]["calls"]])


class AnnouncedStateTests(PendingFixture):
    def test_a_call_the_lead_shows_waiting_for_the_person_is_sent_again_before_it_starts_by_itself(self):
        """The break-it read of 9598efc, SHOULD-1: a call the lead last heard as waiting for the person (its Approve
        drawn) that then starts by itself (the chat turned to Auto) is sent again first, so the lead never shows
        Approve on a call that runs."""
        HeldProvider.release.set()
        self.provider.tools, FakeProvider.gate = (edit(),), threading.Event()
        self.addCleanup(setattr, FakeProvider, "gate", None)
        self.assertTrue(self.open(options={"approval": "ASK"}, consent={"epoch": 1, "grants": {"auto": True}})["ok"])
        self.ask("chat.send", text="go", attachments=[])
        chat, deadline = self.chats.hosted(self.session), time.monotonic() + CAP_S
        while not any(call.approval for call in chat.pending.values()) and time.monotonic() < deadline:
            self.chats.pump()
            time.sleep(0.002)
        self.chats.pump()
        waiting = [call for event in self.pending_events() for call in event["calls"]]
        self.assertTrue(waiting and waiting[-1]["needs_person"], waiting)
        self.assertTrue(self.ask("chat.options", options={"approval": "AUTO_BOUNDED"})["ok"])
        FakeProvider.gate.set()
        self.run_turn(timeout=30)
        self.assertEqual(self.executor.calls, [edit()])
        self.assertIs(self.needs_person_when_run("angelo_create_cube"), False)


class RecordUnderTheLockTests(PendingFixture):
    """The third break-it read (ca31e83): ``_pending`` changed off the chat's lock. A refusal's resend (``resend_cards``,
    from the consent thread or after a refused request) landing between a send and its record was overwritten, so the
    lead's spent card was never sent again; a close racing a start check left a record for a chat no longer hosted."""

    def ready(self):
        HeldProvider.release.set()
        self.assertTrue(self.open()["ok"])
        chat = self.chats.hosted(self.session)
        from angelo_agent.controller import ToolCall
        call = ToolCall("c-1", "angelo_inspect_scene", {}, chat.document, [lambda result: None])
        chat.pending[call.id] = call   # A change for the next look to send.
        self.addCleanup(chat.pending.pop, call.id, None)
        return chat

    def during_the_next_pending_send(self, action):
        """Run ``action`` while the next pending event is being put (between its send and its record)."""
        put, state = self.chats.outbox.put, {"done": False}

        def hooked(event):
            put(event)
            if not state["done"] and event.get("kind") == "pending":
                state["done"] = True
                action()
        self.chats.outbox.put = hooked

    def test_a_resend_asked_on_another_thread_during_a_send_is_never_lost(self):
        chat = self.ready()
        started, thread = threading.Event(), {}

        def resend_elsewhere():
            def run():
                started.set()
                self.chats.resend_cards(chat)
            thread["t"] = threading.Thread(target=run)
            thread["t"].start()
            started.wait(CAP_S)
            time.sleep(0.05)   # Unlocked, the pop lands now, before the record; locked, it waits for the record.
        self.during_the_next_pending_send(resend_elsewhere)
        self.chats.observe(chat)
        thread["t"].join(CAP_S)
        self.assertNotIn(self.session, self.chats._pending, "the resend was overwritten by the send's record")

    def test_a_close_during_a_send_leaves_no_record(self):
        chat = self.ready()
        self.during_the_next_pending_send(lambda: self.chats._end(chat, "closed by the lead"))
        self.chats.observe(chat)
        self.assertIsNone(self.chats.hosted(self.session))
        self.assertNotIn(self.session, self.chats._pending, "a closed chat kept a record of what the lead heard")

    def test_a_start_is_refused_while_the_leads_connection_has_ended(self):
        """The break-it read of 9598efc, SHOULD-2 (NIT-2 of ca31e83): no call starts with no lead to hear it."""
        HeldProvider.release.set()
        self.chats.outbox.outbox.broken = True
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            self.turn((INSPECT,))
        self.assertEqual(self.executor.calls, [])
        result = FakeProvider.turned()[0].results[0]
        self.assertEqual((result["error_code"], result["scene_changed"]), ("INTERNAL_ERROR", False))


class FrameTextTests(unittest.TestCase):
    def test_a_lone_surrogate_never_makes_an_event_unsendable(self):
        """The third break-it read (ca31e83), SHOULD-2: a lone surrogate in a call's arguments (a file name, a model's
        output) survived ``plain`` and made every pending event of its chat ``dropped`` (not finite JSON)."""
        from angelo_agent import external_cli_protocol as protocol, worker_chat_events as events
        value = {"path": "scene\ud800.blend", "pair": "\ud83d\ude00", "k\udfff": ["\udc80"]}
        cleaned = events.plain(value)
        self.assertEqual(cleaned, {"path": "scene\ufffd.blend", "pair": "\U0001f600", "k\ufffd": ["\ufffd"]})
        protocol.encode_frame({"event": events.event("s", "pending", {"calls": [{"summary": cleaned}]})}, 1 << 24)
        self.assertIs(events.text("plain ascii"), "plain ascii")   # The common case is untouched (no copy).


class AnyTextAndEachChatTests(PendingFixture):
    """The fourth break-it read (845d7e9): a call awaiting the person with a lone surrogate in what its digest binds (a
    surrogate-escaped file name in its binding) made its approval digest raise, so ``observe`` raised, the pump
    stopped before its tool step, no tool ran for any chat, and the lead never saw that chat's cards (MUST); and one
    chat's failure in the pump stopped every chat's (SHOULD)."""

    def test_a_call_bound_to_a_lone_surrogate_is_carded_and_approved_with_its_drawn_digest(self):
        """Admission refuses arguments that are not valid UTF-8, but a call's binding (its precondition) is read from
        the scene and the file system, where an undecodable file name arrives surrogate-escaped."""
        HeldProvider.release.set()
        binding = {"file": "scene\udc80.blend", "objects": ["Cube"]}
        self.executor.precondition = lambda name, arguments: binding
        self.provider.tools, FakeProvider.gate = (edit(),), threading.Event()
        self.addCleanup(setattr, FakeProvider, "gate", None)
        self.assertTrue(self.open(options={"approval": "ASK"})["ok"])
        self.ask("chat.send", text="go", attachments=[])
        chat, deadline = self.chats.hosted(self.session), time.monotonic() + CAP_S
        while not any(call.approval for call in chat.pending.values()) and time.monotonic() < deadline:
            self.chats.pump()
            time.sleep(0.002)
        self.chats.pump()
        call = next(call for call in chat.pending.values() if call.approval)
        self.assertEqual(call.precondition, binding)
        from angelo_agent import worker_consent
        digest = worker_consent.approval_digest(call)
        self.assertIsNotNone(digest)
        self.chats.outbox.flush()
        cards = [card for event in self.pending_events() for card in event["calls"] if card["id"] == call.id]
        self.assertTrue(cards and cards[-1]["needs_person"] and cards[-1]["digest"] == digest, cards)
        self.assertEqual(self.ask("chat.approve", call_id=call.id, digest=digest), {"ok": True, "approved": True})
        self.assertEqual(self.executor.calls, [edit()])
        FakeProvider.gate.set()
        self.run_turn(timeout=30)

    def test_one_chat_failing_in_the_pump_never_stalls_another(self):
        HeldProvider.release.set()
        self.assertTrue(self.open()["ok"])
        broken = self.chats.hosted(self.session)
        other = str(uuid.uuid4())
        self.assertTrue(self.ask("chat.open", session=other, store_directory=str(self.state),
                                 settings={"model": "", "reuse_codex_login": True})["ok"])
        damage = " ".join(("one", "chat's", "damaged", "event"))   # Not in any source line a traceback shows.
        broken.pump_events = lambda: (_ for _ in ()).throw(RuntimeError(damage))
        self.provider.tools = (INSPECT,)
        self.assertTrue(self.ask("chat.send", session=other, text="go", attachments=[])["ok"])
        printed = io.StringIO()
        working, deadline = self.chats.hosted(other), time.monotonic() + CAP_S
        with contextlib.redirect_stderr(printed):
            while (working.busy or (working._worker and working._worker.is_alive())) and time.monotonic() < deadline:
                self.chats.pump()
                time.sleep(0.002)
            self.chats.pump()
        self.assertEqual(self.executor.calls, [INSPECT])   # The other chat's call ran.
        self.assertFalse(working.busy)
        self.assertEqual(printed.getvalue().count(damage), 1, printed.getvalue()[-800:])   # Reported once, not per pump.


if __name__ == "__main__":
    unittest.main()
