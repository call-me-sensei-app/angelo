# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's Stop and consent take effect when their frame reaches the worker (``worker_chat_arrival``).

Pinned here, without Blender, through the worker's real reader thread (``WorkerHost._read``) where it matters:

* break-it S2: a revocation that arrives while an edit runs on the worker's main thread is applied and acknowledged
  at once, and no pump starts a call after it; a revocation landing between the pump's admission of a call and the
  call's start (a deterministic interleaving) stops that call; a consent queued before its chat was hosted holds back
  every autonomous start until the loop applied it;
* break-it U8: a Stop that arrives while the main thread is busy interrupts the provider at once, on a thread of its
  own (never the reader, never under the chat's lock), so the turn ends "interrupted" with no loop step; until the
  loop's Stop has run, no call starts, not even one of a message queued ahead of the Stop, and no approval is taken.
"""

import queue
import threading
import time
import unittest
from unittest.mock import patch

from test_native_worker_chat_host import Executor, FakeProvider, Fixture

from angelo_agent.headless_pipe import PipeClosed
from angelo_agent.refusal_codes import RefusalError

EDIT = ("angelo_create_cube", {"name": "Box", "size": 1.0, "location": [0, 0, 0]})
OTHER_EDIT = ("angelo_create_cube", {"name": "Lid", "size": 0.5, "location": [0, 0, 1]})   # Not a replay of EDIT.


class SlowExecutor(Executor):
    """The first edit holds the main thread until ``release`` is set (a long edit, a script or a save)."""

    def __init__(self):
        super().__init__()
        self.running, self.release = threading.Event(), threading.Event()

    def execute(self, name, arguments):
        if not self.calls:
            self.running.set()
            self.release.wait(5)
        return super().execute(name, arguments)


class CallsProvider(FakeProvider):
    """``FakeProvider`` whose calls have ids of their own (its calls reuse the tool's name as the id)."""

    def run(self, native_id, text, observation=None):
        self.results = []
        for index, (name, arguments) in enumerate(self.tools):
            done = threading.Event()
            self.controller.request_tool("%s-%d" % (name, index), name, arguments,
                                         lambda result: (self.results.append(result), done.set()))
            if not done.wait(5):
                raise RuntimeError("The tool never answered")
        if self.gate is not None:
            self.gate.wait(5)
        return "interrupted" if self.controller.cancelled else "completed"


class ReaderEndpoint:
    """The worker's connection as its reader thread sees it: frames in, every frame out to the fixture's endpoint."""

    def __init__(self, sent_to):
        self.frames, self.sent_to = queue.Queue(), sent_to

    def receive(self):
        frame = self.frames.get()
        if frame is None:
            raise PipeClosed()
        return frame

    def send(self, value):
        self.sent_to.send(value)


class ArrivalFixture(Fixture):
    def setUp(self):
        super().setUp()
        for started in self.patches:
            started.stop()
        self.patches = [patch("angelo_agent.controller.CodexProvider", CallsProvider),
                        patch("angelo_agent.controller.ClaudeProvider", CallsProvider)]
        for started in self.patches:
            started.start()
        self.executor = SlowExecutor()
        self.bpy.ops.angelo.approve = self._approve
        self.reader = ReaderEndpoint(self.endpoint)
        self.host.endpoint = self.reader
        self.host.start_reader()
        self.addCleanup(self.reader.frames.put, None)

    def _approve(self, _mode, _undo, call_id, session_id):
        chat = self.ui._controllers[session_id]
        self.bpy.approved.append(call_id)
        chat.execute_tool(chat.pending[call_id], self.executor, self.ui.document_key())
        return {"FINISHED"}

    def frame(self, seq, op, **fields):
        self.reader.frames.put({"seq": seq, "request": dict(fields, op=op, session=self.session)})

    def answer(self, seq, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = [frame["response"] for frame in self.endpoint.responses() if frame["seq"] == seq]
            if found:
                return found[-1]
            time.sleep(0.002)
        return None

    def auto_chat(self):
        self.open(options={"approval": "AUTO_BOUNDED"}, consent={"epoch": 1, "grants": {"auto": True}})
        chat = self.chats.hosted(self.session)
        chat.checkpoint_factory = None
        return chat

    def pump_in_background(self, until):
        """The worker's main loop, pumping on its own thread until ``until()``."""
        def loop():
            deadline = time.monotonic() + 5
            while not until() and time.monotonic() < deadline:
                self.chats.pump()
                time.sleep(0.002)
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread


class ConsentArrivalTests(ArrivalFixture):
    def test_a_revocation_during_an_edit_is_acknowledged_at_once_and_no_later_call_starts(self):
        chat = self.auto_chat()
        FakeProvider.tools = (EDIT, OTHER_EDIT)
        self.ask("chat.send", text="two boxes", attachments=[])
        loop = self.pump_in_background(lambda: self.executor.release.is_set() and not chat.busy)
        self.assertTrue(self.executor.running.wait(5))   # The first edit holds the main thread now.
        self.frame(9, "chat.consent", epoch=2, grants={"auto": False})
        answer = self.answer(9)   # Answered by the reader while the edit still runs.
        self.assertEqual((answer["ok"], answer["epoch"]), (True, 2))
        self.assertFalse(self.executor.release.is_set())
        self.assertFalse(chat.consent_marks_cover("auto") and chat.consent_lease.granted_auto())   # Marked: held.
        self.executor.release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(call.approval and not call.executing
                                                      for call in chat.pending.values()):
            time.sleep(0.002)
        waiting = [call for call in chat.pending.values() if call.approval]
        self.assertEqual(len(self.executor.calls), 1)   # The second edit did not start by itself ...
        self.assertTrue(waiting and not waiting[0].automatic)   # ... it waits for the person.
        self.ask("chat.decline", call_id=waiting[0].id)
        loop.join(5)
        self.assertEqual(len(self.executor.calls), 1)

    def test_a_revocation_between_admission_and_start_stops_that_call(self):
        """Deterministic: the reader applies the revocation right after the pump admitted the call (its
        ``_auto_bounded`` said yes) and before the call ran."""
        chat = self.auto_chat()
        revoked = []

        def admit_then_revoke(admission):
            def admit(*args):
                allowed = admission(*args)
                if allowed and not revoked:   # The frame arrives now, on the reader, right after this admission.
                    revoked.append(self.chats.arrivals.arrived(4, {"op": "chat.consent", "session": self.session,
                                                                   "epoch": 2, "grants": {"auto": False}}))
                return allowed
            return admit
        chat._automatic_script = admit_then_revoke(chat._automatic_script)   # How Auto admits a bounded edit.
        chat._auto_bounded = admit_then_revoke(chat._auto_bounded)
        self.executor.release.set()
        FakeProvider.tools = (EDIT,)
        self.ask("chat.send", text="a box", attachments=[])
        self.run_turn()
        self.assertEqual(revoked, [True])
        self.assertEqual(self.executor.calls, [])
        result = FakeProvider.turned()[0].results[0]
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertEqual(self.answer(4)["epoch"], 2)

    def test_a_consent_queued_before_its_chat_was_hosted_holds_back_autonomous_starts_until_applied(self):
        chat = self.auto_chat()
        arrivals = self.chats.arrivals
        self.chats.chats.pop(self.session)   # As if the chat's open were still queued when the consent arrived.
        self.assertFalse(arrivals.arrived(5, {"op": "chat.consent", "session": self.session, "epoch": 3,
                                              "grants": {"auto": True}}))
        self.chats.chats[self.session] = chat
        call = type("Call", (), {"name": "angelo_create_cube"})()
        self.assertTrue(chat.consent_arriving())
        self.assertFalse(chat._auto_bounded(call, "AUTO_BOUNDED"))
        self.assertEqual(self.ask("chat.consent", epoch=3, grants={"auto": True})["epoch"], 3)
        arrivals.handled(5)
        self.assertTrue(chat._auto_bounded(call, "AUTO_BOUNDED"))


class StopArrivalTests(ArrivalFixture):
    def test_a_stop_while_the_main_thread_is_busy_interrupts_the_provider_and_ends_the_turn_stopped(self):
        """U8: no loop step runs after the Stop, as when an edit or a save holds the main thread."""
        self.open()
        FakeProvider.tools, FakeProvider.gate = (), threading.Event()
        self.ask("chat.send", text="long", attachments=[])
        chat = self.chats.hosted(self.session)
        self.frame(3, "chat.stop", reason="")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.endpoint.events("finished"):
            time.sleep(0.002)
        self.chats.outbox.flush()
        finished = self.endpoint.events("finished")
        self.assertEqual([event["data"]["status"] for event in finished], ["interrupted"])
        self.assertTrue(chat.cancelled)
        self.assertNotIn("error", [event["data"].get("role") for event in self.endpoint.events("message")])
        self.assertIsNone(self.answer(3, timeout=0.05))   # The loop has not even taken the request yet.
        self.assertTrue(self.chats.arrivals.stop_pending(self.session))

    def test_the_provider_is_interrupted_off_the_reader_thread_and_outside_the_chat_lock(self):
        self.open()
        FakeProvider.tools, FakeProvider.gate = (), threading.Event()
        self.ask("chat.send", text="long", attachments=[])
        chat = self.chats.hosted(self.session)
        deadline = time.monotonic() + 3
        while chat.provider is None and time.monotonic() < deadline:
            time.sleep(0.002)
        seen, unblock = {}, threading.Event()

        def stop_request(fence):
            seen["thread"] = threading.current_thread().name
            seen["lock_free"] = chat.lock.acquire(timeout=1)
            if seen["lock_free"]:
                chat.lock.release()
            seen["fence"] = fence
            unblock.wait(5)   # A runtime slow to answer its interrupt never holds the reader.
            FakeProvider.gate.set()
        chat.provider.stop_request = stop_request
        started = time.monotonic()
        self.assertFalse(self.chats.arrivals.arrived(2, {"op": "chat.stop", "session": self.session}))
        self.assertLess(time.monotonic() - started, 0.5)
        deadline = time.monotonic() + 3
        while "fence" not in seen and time.monotonic() < deadline:
            time.sleep(0.002)
        unblock.set()
        self.assertEqual(seen["thread"], "AngeloWorkerStop")
        self.assertTrue(seen["lock_free"])
        self.assertEqual((seen["fence"].epoch, seen["fence"].generation), (chat._epoch, chat._request_generation))

    def test_a_message_queued_ahead_of_an_unordered_stop_is_refused_and_runs_nothing(self):
        """Neither frame carries an order (the lead stamps every Stop; this holds whatever sent them): the Stop
        is recorded after everything that reached the reader, so the Send it overtook never starts."""
        self.open(options={"approval": "AUTO_BOUNDED"}, consent={"epoch": 1, "grants": {"auto": True}})
        self.chats.hosted(self.session).checkpoint_factory = None
        self.executor.release.set()
        FakeProvider.tools = (("angelo_inspect_scene", {}), EDIT)
        arrivals = self.chats.arrivals
        self.assertFalse(arrivals.arrived(1, {"op": "chat.send", "session": self.session}))   # Queued, not marked.
        self.assertFalse(arrivals.arrived(2, {"op": "chat.stop", "session": self.session}))   # Marked, queued.
        with self.assertRaises(RefusalError):   # The loop takes the send queued ahead of the Stop: refused.
            self.ask("chat.send", text="a box", attachments=[])
        arrivals.handled(1)
        self.assertFalse(self.chats.hosted(self.session).busy)
        self.assertEqual(self.executor.calls, [])
        self.ask("chat.stop", reason="")
        arrivals.handled(2)
        self.assertFalse(arrivals.stop_pending(self.session))

    def test_no_call_starts_while_a_stop_is_pending_whatever_reset_the_turn_cancelled_flag(self):
        """Deterministic: the provider asked for a call, the Stop arrived, and a message queued ahead of the Stop
        restarted the turn (``cancelled`` reset); until the loop's Stop runs, the pump starts nothing."""
        self.open(options={"approval": "AUTO_BOUNDED"}, consent={"epoch": 1, "grants": {"auto": True}})
        chat = self.chats.hosted(self.session)
        chat.checkpoint_factory = None
        self.executor.release.set()
        FakeProvider.tools, FakeProvider.gate = (("angelo_inspect_scene", {}), EDIT), threading.Event()
        self.ask("chat.send", text="a box", attachments=[])
        deadline = time.monotonic() + 3
        while chat.tools.empty():
            self.assertLess(time.monotonic(), deadline, "The provider never asked for its call")
            time.sleep(0.002)
        self.chats.arrivals.arrived(2, {"op": "chat.stop", "session": self.session})
        chat.cancelled = False
        for _ in range(5):
            self.chats.pump()
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(FakeProvider.turned()[0].results[0]["error_code"], "CANCELLED")
        self.ask("chat.stop", reason="")
        self.chats.arrivals.handled(2)
        self.run_turn()
        self.assertEqual(self.executor.calls, [])

    def test_an_approval_taken_after_a_stop_arrived_is_refused_and_runs_nothing(self):
        self.open(options={"approval": "ASK"})
        chat = self.chats.hosted(self.session)
        chat.checkpoint_factory = None
        self.executor.release.set()
        FakeProvider.tools, FakeProvider.gate = (EDIT,), threading.Event()
        self.ask("chat.send", text="a box", attachments=[])
        deadline = time.monotonic() + 5
        while not [call for call in chat.pending.values() if call.approval]:
            self.chats.pump()
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.002)
        self.chats.outbox.flush()
        call = [call for call in chat.pending.values() if call.approval][0]
        digest = [card for event in self.endpoint.events("pending") for card in event["data"]["calls"]
                  if card["id"] == call.id][-1]["digest"]
        self.chats.arrivals.arrived(7, {"op": "chat.stop", "session": self.session})   # Stop, then the queued Approve.
        with self.assertRaises(RefusalError) as caught:
            self.ask("chat.approve", call_id=call.id, digest=digest)
        self.assertEqual(caught.exception.code, "CANCELLED")
        self.assertEqual(self.executor.calls, [])
        self.ask("chat.stop", reason="")
        self.chats.arrivals.handled(7)
        self.run_turn()


if __name__ == "__main__":
    unittest.main()
