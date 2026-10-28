# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Design A′ end to end after round 3 (R3a-R3c merged), on the e2e fixture (``test_native_worker_chat_e2e.Fixture``:
the real lead and worker modules over a socket pair). No Blender, and no signal reaches anything but this test's own
stand-in model child: the worker's group kill is off (``_killpg``), its fake process has a pid that is never real,
and the model child is killed by pid, never by group (``RuntimeChild(group=False)``).

Pinned, each across both sides at once:

* a worker that records a ``set`` of another provider, connection or approval in its chat's file changes no lead
  decision (route, launch, consent, the approval shown): the chat is marked, says so once, and its model child ends
  (R3a ``worker_mirror_pins``);
* a parked chat marked as needing approval: the person's first message after it relaunches reaches the model (R3c
  N2: its consent hook is the reopened chat's);
* a RECOVERED of the open a relaunch replaced, applied after the new open's turn drew its card, leaves that card and
  turn alone (R3c N1: hand-backs carry their ``open_id``);
* an Approve whose frame was lost while its request still waits is shown as lost, with ``WRK-APPROVAL-LOST``, once
  ``ASK_SECONDS`` (5 s) passed on the settle clock, never before (R3b H2).
"""

import sys
import threading
import unittest
import uuid
from functools import partial
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (runtime_child, session_journal, worker_chat_lifecycle as lifecycle,  # noqa: E402
                          worker_chat_wiring, worker_consent_lead, worker_mirror_pins, worker_mirror_settle,
                          worker_queue, worker_reasons)
from angelo_agent import worker_mirror_events as events  # noqa: E402
from test_native_worker_chat_e2e import POSIX_ONLY, Fixture, wait_for  # noqa: E402
from test_native_worker_chat_e2e_flows import TurnsProvider  # noqa: E402

NEVER_A_PID = -(10 ** 6 + 7)


@POSIX_ONLY
class RoundThree(Fixture):
    def setUp(self):
        super().setUp()
        self.worker._killpg = None   # Its process is a stand-in: nothing here ever signals a group.
        self.worker.process.pid = NEVER_A_PID
        for patcher in (mock.patch("angelo_agent.controller.CodexProvider", TurnsProvider),
                        mock.patch("angelo_agent.controller.ClaudeProvider", TurnsProvider),
                        mock.patch("angelo_agent.runtime_service.RuntimeChild",
                                   partial(runtime_child.RuntimeChild, group=False))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def approve_drawn(self, card):
        worker_consent_lead.drew(self.mirror, card.id)
        self.assertIsNone(worker_consent_lead.approve(self.mirror, card.id))

    def ended(self, outcome="completed"):
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump),
                        "The turn never ended")
        self.assertEqual(self.mirror.turn_outcome[0], outcome)

    def turn(self, text):
        self.mirror.send(text)
        self.approve_drawn(self.card())
        self.ended()
        self.assertTrue(wait_for(lambda: self.mirror.state["messages"] == session_journal.read(
            self.mirror.store.path)[0]["messages"], pump=self.pump), "The mirror never matched its file")

    def errors(self):
        return [m for m in self.mirror.state["messages"] if m.get("role") == "error"]

    def park_and_resume(self):
        """The chat parked (closed in its worker), and ``worker_queue``'s relaunch stood in for: reopened resumed."""
        self.assertTrue(lifecycle.close_chat(self.session, "parked"))
        self.pump()
        relaunched = []

        def resume(controller, deliver, on_error):
            relaunched.append(controller)
            lifecycle.open_chat(controller, self.worker, resume=True)
            deliver()
            return True
        return relaunched, (mock.patch.object(worker_queue, "parked", {self.session}),
                            mock.patch.object(worker_queue, "resume", resume))

    def test_a_forged_journal_set_changes_no_lead_decision_marks_the_chat_and_ends_its_child(self):
        self.open()
        route = (self.mirror.state["provider"], self.mirror.state["connection_id"])
        self.mirror.send("a box")
        card = self.card()   # The turn waits on the person; its model child serves the chat.
        child = self.child()
        launched = worker_chat_wiring.launch_chat(self.worker, self.session)
        self.assertEqual((launched.mode, launched.connection_id), route)
        worker_chat = self.chats.hosted(self.session)
        # The worker, its file's one writer, records another route and a wider approval.
        worker_chat.state.update(provider="codex_subscription", connection_id=str(uuid.uuid4()),
                                 options=dict(worker_chat.state["options"], approval="AUTO_BOUNDED"))
        with mock.patch.object(worker_mirror_pins, "print", create=True):
            worker_chat.save()
            self.assertTrue(wait_for(lambda: worker_mirror_pins.tampered(self.mirror), pump=self.pump),
                            "The forged record was adopted unmarked")
        self.assertEqual(worker_mirror_pins.tampered(self.mirror), "provider")
        self.assertTrue(wait_for(lambda: not child.alive()), "The marked chat's model child kept running")
        # No lead decision moved: shown, launched, consented and chosen on the route and approval Angelo pinned.
        self.assertEqual((self.mirror.state["provider"], self.mirror.state["connection_id"]), route)
        self.assertEqual(self.mirror.state["options"]["approval"], "ASK")
        self.assertIsNone(worker_chat_wiring.launch_chat(self.worker, self.session))
        self.assertEqual(worker_consent_lead.grants(self.mirror), {"auto": False})
        with self.assertRaises(RuntimeError):
            self.mirror.choose_connection("codex_subscription", {}, "", worker_chat.state["connection_id"])
        marked = [row for row in self.errors() if "did not choose" in str(row.get("text"))]
        self.assertEqual(len(marked), 1)
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])   # The card's edit never ran.
        self.assertIn(card.id, self.mirror.pending)

    def test_a_parked_chat_needing_approval_sends_its_first_message(self):
        self.open()
        self.turn("a box")
        relaunched, patches = self.park_and_resume()
        worker_consent_lead._needs.add(self.session)   # Marked: a revocation went unacknowledged (``settle``).
        self.assertTrue(worker_consent_lead.needs_approval(self.session))
        with patches[0], patches[1]:
            self.turn("a sphere")   # The first message after the relaunch: never refused.
        self.assertEqual(relaunched, [self.mirror])
        self.assertFalse(worker_consent_lead.needs_approval(self.session))
        users = [m["text"] for m in self.mirror.state["messages"] if m.get("role") == "user"]
        self.assertEqual(users, ["a box", "a sphere"])
        self.assertEqual(self.errors(), [])

    def test_a_stale_recovered_after_a_relaunch_leaves_the_new_opens_cards(self):
        self.open()
        self.turn("a box")
        first = self.mirror.announced_open
        stale = session_journal.read(self.mirror.store.path)[0]   # The file as the first open handed it back.
        relaunched, patches = self.park_and_resume()
        with patches[0], patches[1]:
            self.mirror.send("a sphere")
            card = self.card()   # The new open's turn waits on its card.
        self.assertNotEqual(self.mirror.open_id, first)
        # The first open's hand-back, read before the relaunch but applied only now, with the turn it finds.
        events.put(self.mirror, events.RECOVERED, {"state": stale, "finished": {"status": "interrupted"},
                                                   "generation": self.mirror._request_generation,
                                                   "open_id": first})
        self.pump()
        self.assertTrue(self.mirror.busy)
        self.assertIn(card.id, self.mirror.pending)
        self.assertIn("a sphere", [m.get("text") for m in self.mirror.state["messages"] if m.get("role") == "user"])
        self.approve_drawn(card)   # The card still works: its edit runs once and the turn completes.
        self.ended()
        self.assertEqual(relaunched, [self.mirror])
        self.assertEqual(self.executor.calls.count("angelo_create_cube"), 2)

    def test_a_lost_approve_is_shown_with_its_code_once_five_seconds_passed(self):
        self.open()
        now = [0.0]
        worker_mirror_settle.bind(now=lambda: now[0])
        self.addCleanup(worker_mirror_settle.reset)
        self.mirror.send("a box")
        card = self.card()
        real, release = worker_consent_lead._hooks["send"], threading.Event()
        self.addCleanup(release.set)

        def lost(session, request, timeout):   # The Approve's frame is lost; its request is still waiting.
            if request.get("op") == "chat.approve":
                release.wait(30)
                raise TimeoutError("no answer within %s s" % timeout)
            return real(session, request, timeout)
        with mock.patch.dict(worker_consent_lead._hooks, {"send": lost}):
            self.approve_drawn(card)
            now[0] = 4.9   # The contract's bound is about 5 s (R3b H2), whatever the constant says.
            for _ in range(20):
                self.pump()
            self.assertTrue(self.mirror.pending[card.id].approved)   # Approving: unknown is never "nothing ran".
            self.assertEqual(self.errors(), [])
            now[0] = 5.0
            self.assertTrue(wait_for(lambda: not self.mirror.pending[card.id].approved, pump=self.pump),
                            "The lost Approve was not settled once its bound passed")
        errors = self.errors()
        self.assertEqual(len(errors), 1)
        self.assertIn(worker_reasons.APPROVAL_LOST, errors[0]["text"])
        self.assertEqual(worker_reasons.APPROVAL_LOST, "WRK-APPROVAL-LOST")
        self.assertEqual(errors[0]["i18n"][0], worker_mirror_settle.LOST)
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])   # Nothing ran.
        release.set()
        self.approve_drawn(self.card())   # Approved again: the edit runs once.
        self.ended()
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene", "angelo_create_cube"])


if __name__ == "__main__":
    unittest.main()
