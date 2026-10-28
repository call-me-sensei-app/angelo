# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Design A′ end to end after the fix loop (FA-FF merged): the real lead and worker modules over a socket pair, with
the stand-in model child (``test_native_worker_chat_e2e.Fixture``). No Blender.

Pinned, each across both sides at once (the lanes' own tests fake the other side):

* a chat closed for a park and reopened in its worker shows its next turn (C9's ``open_id`` on both sides);
* a Stop while the worker's loop is inside an edit takes effect on arrival (S2/U8): no other call starts, the turn
  ends interrupted with no kill, and the model child lives;
* Approve sends the digest of the card as drawn (S3), and a card never drawn is refused;
* a Send while the worker is wedged returns at once and shows as sending, with no error (U6); it runs once, after;
* a key replaced while a worker chat exists completes (U2): the idle chat's model child ends, the next turn launches
  a new one, and neither key reaches the worker (U13).
"""

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (session_journal, worker_chat_lifecycle as lifecycle, worker_chat_wiring,  # noqa: E402
                          worker_consent_lead, worker_journal_owner)
from test_native_worker_chat_e2e import KEY, POSIX_ONLY, Fixture, RelayedProvider, wait_for  # noqa: E402


class TurnsProvider(RelayedProvider):
    """``RelayedProvider`` for several turns: the stand-in child asks one tool call per thread it starts, so each turn
    answers only the asks not answered yet (the first turn of a connection waits for its ask)."""

    edits = calls = 0

    def connect(self):
        self.asks = getattr(self, "asks", 0) + 1   # Its thread/start asks one call.
        self.answered = getattr(self, "answered", 0)
        return super().connect()

    def _tool(self, name, arguments):
        """As ``RelayedProvider._tool``, with a call id of its own per call, as a runtime gives (an id is never
        reused for another call)."""
        done, results = threading.Event(), []
        TurnsProvider.calls += 1
        self.context.request_tool("%s-%d" % (name, TurnsProvider.calls), name, arguments,
                                  lambda result: (results.append(result), done.set()))
        if not done.wait(20):
            raise RuntimeError("The tool never answered")
        return results[0]

    def bind_request(self, context):
        self.context = context   # Each turn's own request context (``Controller._connect_runtime``).

    def run(self, native_id, text, observation=None):
        if self.asks:
            if not wait_for(lambda: len(self.requests) > self.answered, 10):
                raise RuntimeError("The runtime asked nothing")
            identity, _method, params = self.requests[self.answered]
            self.answered, self.asks = self.answered + 1, self.asks - 1
            self.rpc.respond(identity, self._tool(params["tool"], {}))
        TurnsProvider.edits += 1
        name = "Box%d" % TurnsProvider.edits   # Each turn's own edit (an equal one is replayed, with no card).
        self.edit = self._tool("angelo_create_cube", {"name": name, "size": 1.0, "location": [0, 0, 0]})
        if self.gate is not None:
            self.gate.wait(20)
        if self.context.cancelled:
            return "interrupted"
        self.context.emit("message_complete", {"item": "reply-" + name, "text": "Made it for " + text})
        return "completed"


@POSIX_ONLY
class Flows(Fixture):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch("angelo_agent.controller.CodexProvider", TurnsProvider),
                        mock.patch("angelo_agent.controller.ClaudeProvider", TurnsProvider)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def approve_drawn(self, card):
        worker_consent_lead.drew(self.mirror, card.id)
        self.assertIsNone(worker_consent_lead.approve(self.mirror, card.id))

    def turn(self, text):
        """One whole turn: Send, the card drawn and approved, the turn's end and the mirror equal to its file."""
        self.mirror.send(text)
        self.approve_drawn(self.card())
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump),
                        "The turn never ended")
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        self.assertTrue(wait_for(lambda: any(str(m.get("text")).startswith("Made it for " + text) for m in self.mirror.state[
            "messages"]), pump=self.pump), "The reply never reached the mirror")
        self.assertTrue(wait_for(lambda: self.mirror.state["messages"] == session_journal.read(
            self.mirror.store.path)[0]["messages"], pump=self.pump), "The mirror never matched its file")

    def test_a_chat_reopened_after_a_park_shows_its_next_turn(self):
        self.open()
        self.turn("a box")
        first = self.mirror.open_id
        self.assertTrue(first)
        self.assertTrue(lifecycle.close_chat(self.session, "parked"))   # The park closes it in its worker.
        self.assertFalse(worker_journal_owner.hosted(self.session))
        self.pump()
        lifecycle.open_chat(self.mirror, self.worker, resume=True)    # Its relaunch opens it again, resumed.
        self.turn("a sphere")
        self.assertNotEqual(self.mirror.open_id, first)
        self.assertIn(first, self.mirror.retired_opens)
        users = [m["text"] for m in self.mirror.state["messages"] if m.get("role") == "user"]
        self.assertEqual(users, ["a box", "a sphere"])

    def test_a_stop_during_a_blocked_edit_takes_effect_on_arrival_and_kills_nothing(self):
        self.open()
        entered, release = threading.Event(), threading.Event()
        execute = self.executor.execute

        def blocked(name, arguments):
            if name == "angelo_create_cube":
                entered.set()
                release.wait(20)   # The worker's loop is inside the edit: it reads no op until it returns.
            return execute(name, arguments)
        self.executor.execute = blocked
        self.mirror.send("a box")
        self.approve_drawn(self.card())
        self.assertTrue(entered.wait(10), "The edit never started")
        child = self.child()
        worker_chat = self.chats.hosted(self.session)
        self.mirror.stop()
        self.assertTrue(wait_for(lambda: worker_chat.cancelled, pump=worker_consent_lead.settle),
                        "The Stop waited for the edit (it must apply when it arrives)")
        release.set()
        self.assertTrue(wait_for(lambda: not self.mirror.busy, pump=self.pump), "The stopped turn never ended")
        self.assertEqual(self.mirror.turn_outcome[0], "interrupted")
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene", "angelo_create_cube"])   # Nothing after.
        self.clock[0] += worker_consent_lead.STOP_SECONDS + 0.1
        worker_consent_lead.tick()
        self.assertTrue(child.alive(), "The Stop's kill ran though the turn ended on its own")
        self.assertEqual(self.mirror.pending, {})

    def test_approve_sends_the_drawn_digest(self):
        self.open()
        self.mirror.send("a box")
        card = self.card()
        self.approve_drawn(card)
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump))
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene", "angelo_create_cube"])

    def test_an_approve_from_the_lead_on_a_card_no_chat_area_drew_runs_it(self):
        """78e17c6e1a1's Town benchmark: refused as not shown, each worker waited until its approval expired."""
        self.open()
        self.mirror.send("a box")
        card = self.card()
        self.assertIsNone(worker_consent_lead.approve(self.mirror, card.id))
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump))
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene", "angelo_create_cube"])

    def test_a_send_to_a_wedged_worker_returns_at_once_and_runs_once_after(self):
        self.open()
        self.wedge.clear()   # Its loop frozen: nothing it is sent is answered.
        started = time.perf_counter()
        self.mirror.send("a box")
        for _ in range(5):
            self.pump()
        self.assertLess(time.perf_counter() - started, 0.25, "A Send waited on a wedged worker")
        self.assertTrue(self.mirror.busy)
        self.assertEqual(self.mirror.status, "Sending")
        self.assertFalse([m for m in self.mirror.state["messages"] if m.get("role") == "error"])
        self.wedge.set()
        self.approve_drawn(self.card())
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump))
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        users = [m for m in session_journal.read(self.mirror.store.path)[0]["messages"] if m.get("role") == "user"]
        self.assertEqual([m["text"] for m in users], ["a box"])   # Sent once, never resent.
        self.assertIsNone(self.mirror.unsent_draft if hasattr(self.mirror, "unsent_draft") else None)

    def test_a_key_replaced_while_a_worker_chat_exists_completes_and_relaunches_its_child(self):
        self.open()
        self.turn("a box")
        first = self.child()
        replaced = "sk-e2e-" + "r" * 40
        with mock.patch.object(worker_chat_wiring, "key_of", lambda mirror: replaced):
            self.mirror.invalidate_runtime(self.mirror.state.get("connection_id"))   # The window's key sweep.
            self.assertTrue(wait_for(lambda: not first.alive()), "The idle chat's model child kept the old key")
            self.turn("a sphere")
            second = self.child()
        self.assertIsNot(second, first)
        seen = json.dumps([[p.initialized, p.account, p.events, p.settings] for p in RelayedProvider.instances
                           if hasattr(p, "rpc")])
        self.assertNotIn(KEY, seen)
        self.assertNotIn(replaced, seen)


if __name__ == "__main__":
    unittest.main()
