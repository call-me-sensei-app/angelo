# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Design A′ end to end, round 2 (R2c): the real lead and worker over a socket pair, with the stand-in model child
(``test_native_worker_chat_e2e.Fixture``). No Blender.

Pinned, across both sides at once:

* D3: once the lead's mirror shows a turn ended, the worker takes the next message, also while the turn's thread is
  still returning and before the worker's loop applied the turn's end itself;
* D9: the lead's Trust scripts turned on after its worker chat started reaches that chat's lease in its worker
  without a wait, and turned off is acknowledged;
* D11: with the worker frozen whole, the approval menu's Ask returns at once, the lease change is sent, and 2 s later
  the chat needs approval and its model child is killed.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import worker_consent_lead, worker_trust  # noqa: E402
from angelo_agent.chat_settings import APPROVALS  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from test_native_dispatch_fixes import ui_class  # noqa: E402
from test_native_worker_chat_e2e import POSIX_ONLY, Fixture, RelayedProvider, wait_for  # noqa: E402
from test_native_worker_chat_e2e_flows import TurnsProvider  # noqa: E402


@POSIX_ONLY
class Consent(Fixture):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch("angelo_agent.controller.CodexProvider", TurnsProvider),   # Several turns.
                        mock.patch("angelo_agent.controller.ClaudeProvider", TurnsProvider)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def approve_drawn(self, card):
        worker_consent_lead.drew(self.mirror, card.id)
        self.assertIsNone(worker_consent_lead.approve(self.mirror, card.id))

    def errors(self):
        return [m.get("text") for m in self.mirror.state["messages"] if m.get("role") == "error"]

    def test_a_send_right_after_the_mirror_shows_the_turn_ended_is_taken(self):
        """D3, opened deterministically: the worker's loop runs no save step from before the turn's end is queued (as
        when an edit holds its main thread past ``TurnEnds``' bound), the end reaches the lead (the bound's release,
        made here at once) and the turn's thread is held until the person's next message reaches the worker. That
        message is taken, in order, never refused as "already running"."""
        self.open()
        worker_chat = self.chats.hosted(self.session)
        pump, pumping, gap, unwind = self.chats.pump, threading.Lock(), threading.Event(), threading.Event()
        emit = worker_chat.emit

        def ending(kind, data=None):   # The turn's thread.
            if kind != "finished" or gap.is_set():
                return emit(kind, data)
            gap.set()                  # From here no save step runs, and none is under way (``pumping``):
            with pumping:              # nothing applies the turn's end in the worker.
                pass
            emit(kind, data)
            self.chats.outbox.release()   # Its bound passed: the end goes to the lead anyway.
            unwind.wait(10)               # The turn's thread is still returning when the next message arrives.

        def pumped():
            if gap.is_set() and not self.resumed:
                return None
            with pumping:
                return pump()
        send = worker_chat.send

        def arriving(*args, **kwargs):   # The worker takes the message: the held turn's thread now returns.
            unwind.set()
            return send(*args, **kwargs)
        worker_chat.emit, worker_chat.send, self.chats.pump, self.resumed = ending, arriving, pumped, False
        self.addCleanup(unwind.set)
        self.mirror.send("a box")
        self.approve_drawn(self.card())
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.mirror.pump_events))
        self.assertTrue(worker_chat.busy, "The gap was not reached: the worker already applied the turn's end")
        self.mirror.send("a sphere")   # The person's next message, the moment the chat shows idle.
        self.assertTrue(wait_for(lambda: worker_chat.busy and worker_chat._ended != (
            worker_chat._epoch, worker_chat._request_generation), pump=self.mirror.pump_events), "Not taken")
        self.resumed = True
        self.approve_drawn(self.card())
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump))
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        self.assertEqual(self.errors(), [])   # Never "An agent turn is already running".
        users = [m["text"] for m in self.mirror.state["messages"] if m.get("role") == "user"]
        self.assertEqual(users, ["a box", "a sphere"])

    def test_the_leads_trust_turned_on_after_its_worker_started_reaches_the_chat_in_its_worker(self):
        lead = Controller(str(self.state))
        self.addCleanup(lead.close)
        lead.state["options"]["approval"] = "AUTO_BOUNDED"
        lead.set_document(("lead-doc", 1))
        mirror = self.open(approval="AUTO_BOUNDED")
        worker_trust.link(lead, mirror)   # As ``mirror_of`` links it to the lead that started it.
        mirror.remote_executor.document_key = ("worker-doc", 1)
        mirror.set_document(("worker-doc", 1))
        worker_chat = self.chats.hosted(self.session)
        self.assertIsNone(worker_chat.consent_lease.token("scripts"))
        started = time.perf_counter()
        lead.set_trusted_scripts(True)
        self.assertLess(time.perf_counter() - started, 0.25, "A widening waited on the worker")
        self.assertTrue(wait_for(lambda: worker_chat.consent_lease.token("scripts") is not None,
                                 pump=worker_consent_lead.settle), "The lead's trust never reached its worker chat")
        self.assertTrue(worker_chat.trusted_scripts)
        lead.set_trusted_scripts(False)
        self.assertTrue(wait_for(lambda: worker_chat.consent_lease.token("scripts") is None and not
                                 worker_consent_lead._inflight.get(self.session), pump=worker_consent_lead.settle))
        self.assertFalse(worker_consent_lead.needs_approval(self.session))

    def test_ask_on_a_frozen_worker_returns_at_once_and_stops_the_chat_after_the_deadline(self):
        mirror = self.open(approval="AUTO_BOUNDED")
        RelayedProvider.gate = threading.Event()
        mirror.send("a box")
        self.assertTrue(wait_for(lambda: self.worker.runtime.children(), pump=self.pump))
        child = self.child()
        self.wedge.clear()   # Frozen whole: its loop and its reader.
        self.reader_wedge.clear()
        operator = ui_class("ANGELO_OT_set_approval", {"__package__": "angelo_agent", "__name__": "angelo_agent.ui",
                                                      "get_controller": lambda: mirror, "APPROVALS": APPROVALS})()
        operator.report, operator.mode = mock.Mock(), "ASK"
        with mock.patch.object(worker_consent_lead, "changed", wraps=worker_consent_lead.changed) as changed:
            started = time.perf_counter()
            self.assertEqual(operator.execute(None), {'FINISHED'})
            self.assertLess(time.perf_counter() - started, 0.25, "Ask waited on a frozen worker")
        changed.assert_called_once()
        self.assertTrue(any(record.revoking for record in worker_consent_lead._inflight.get(self.session, ())))
        self.clock[0] += worker_consent_lead.ACK_SECONDS - 0.01
        worker_consent_lead.tick()
        self.assertFalse(worker_consent_lead.needs_approval(self.session))   # Not before the deadline.
        self.clock[0] += 0.01
        worker_consent_lead.tick()
        self.assertTrue(worker_consent_lead.needs_approval(self.session))
        self.assertTrue(worker_consent_lead.stopping(self.session))
        self.assertTrue(wait_for(lambda: not child.alive()), "The frozen worker's model child was not killed")
        self.assertTrue(wait_for(lambda: (mirror.pump_events(), any(
            m.get("text") == worker_consent_lead.NEEDS_APPROVAL for m in mirror.state["messages"]))[1]))


if __name__ == "__main__":
    unittest.main()
