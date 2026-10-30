# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Design A′ end to end after round 2 (R2a-R2g merged), on the e2e fixture (``test_native_worker_chat_e2e.Fixture``:
the real lead and worker modules over a socket pair, the stand-in model child a real process of this test's own). No
Blender, and no signal reaches a fake pid: the worker's group kill is off (``_killpg``) and it was never launched.

Pinned, each across both sides at once:

* a connection removed while its worker chat's turn runs, with only that chat's mirror registered: the window's own
  operator ends the turn, ends the chat's model child at once, and the card's edit never runs;
* a worker that crashes while an approved edit runs ends the turn interrupted with the card settled, no row says the
  edit did not run (it may have), the model child ends, and a late answer changes nothing;
* a parked chat's next message relaunches it (R2g ``worker_mirror_park``), reopens it resumed and reaches the model,
  though the park's close was not applied before the message (R2f's turn-keyed close; the native R2g finding);
* an Approve whose frame was lost stays Approving (R2c: unknown is never "nothing ran") until the lead asks the
  worker (R2g ``chat.calls``), which still waits for the person: the card is approvable again, and a second
  Approve runs the edit once.
"""

import sys
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (session_journal, worker_chat_lifecycle as lifecycle, worker_consent_lead,  # noqa: E402
                          worker_journal_owner, worker_mirror_ops, worker_mirror_settle, worker_queue)
from angelo_agent import worker_mirror_events as events  # noqa: E402
from test_native_runtime_connection_kill import _namespace, _source  # noqa: E402
from test_native_worker_chat_e2e import POSIX_ONLY, Fixture, wait_for  # noqa: E402
from test_native_worker_chat_e2e_flows import TurnsProvider  # noqa: E402


@POSIX_ONLY
class RoundTwo(Fixture):
    def setUp(self):
        super().setUp()
        self.worker._killpg = None   # Its process is a stand-in: nothing here ever signals a group.
        for patcher in (mock.patch("angelo_agent.controller.CodexProvider", TurnsProvider),
                        mock.patch("angelo_agent.controller.ClaudeProvider", TurnsProvider)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def approve_drawn(self, card):
        worker_consent_lead.drew(self.mirror, card.id)
        self.assertIsNone(worker_consent_lead.approve(self.mirror, card.id))

    def turn(self, text):
        """One whole turn: Send, its card drawn and approved, its end, its reply and the mirror equal to its file."""
        self.mirror.send(text)
        self.approve_drawn(self.card())
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump),
                        "The turn never ended")
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        self.assertTrue(wait_for(lambda: any(str(m.get("text")).startswith("Made it for " + text)
                                             for m in self.mirror.state["messages"]), pump=self.pump),
                        "The model never answered this message")
        self.assertTrue(wait_for(lambda: self.mirror.state["messages"] == session_journal.read(
            self.mirror.store.path)[0]["messages"], pump=self.pump), "The mirror never matched its file")

    def errors(self):
        return [m.get("text") for m in self.mirror.state["messages"] if m.get("role") == "error"]

    def test_a_connection_removed_mid_turn_ends_it_and_its_child_with_only_the_mirror_registered(self):
        self.open()
        self.mirror.send("a box")
        card = self.card()   # The turn waits on the person; its child serves the chat.
        child = self.child()
        identity = self.mirror.state["connection_id"]
        namespace = _namespace({self.session: self.mirror}, {identity: {"id": identity, "mode": "codex_api",
                                                                        "reuse_native": False}})
        namespace["worker_consent_lead"] = worker_consent_lead   # The real consent lane: the key's consent ends.
        exec(textwrap.dedent(_source("execute", "ANGELO_OT_connection_remove")), namespace)
        self.assertEqual(namespace["execute"](mock.Mock(connection_id=identity), None), {'FINISHED'})
        self.assertTrue(wait_for(lambda: not child.alive()), "The removed connection's model child kept running")
        self.assertTrue(wait_for(lambda: not self.mirror.busy, pump=self.pump), "The removed connection's turn ran on")
        self.assertEqual(self.mirror.turn_outcome[0], "interrupted")
        self.assertNotIn(card.id, [c.id for c in self.mirror.pending.values()])
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])   # The card's edit never ran.

    def test_a_crash_while_an_approved_edit_runs_ends_the_turn_and_claims_nothing_about_the_edit(self):
        self.open()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        execute = self.executor.execute

        def blocked(name, arguments):
            if name == "angelo_create_cube":
                entered.set()
                release.wait(20)   # The worker is inside the approved edit when it dies.
            return execute(name, arguments)
        self.executor.execute = blocked
        self.mirror.send("a box")
        card = self.card()
        self.approve_drawn(card)
        self.assertTrue(entered.wait(10), "The approved edit never started")
        child = self.child()
        self.wedge.clear()   # Frozen, then gone: it answers and writes nothing more.
        self.reader_wedge.clear()
        self.worker.process.code = 1
        self.worker.stop(grace=0.1)
        self.assertTrue(wait_for(lambda: not self.mirror.busy, pump=self.pump), "The crashed turn never ended")
        self.assertEqual(self.mirror.turn_outcome[0], "interrupted")
        self.assertEqual(self.mirror.pending, {})
        self.assertFalse(worker_journal_owner.hosted(self.session))
        self.assertTrue(wait_for(lambda: not child.alive()), "The crashed worker's model child kept running")
        nothing = [text for text in self.errors() if "nothing changed" in str(text) or "did not run" in str(text)]
        self.assertEqual(nothing, [], "A row claimed the approved edit did not run, though it may have")
        before = list(self.mirror.state["messages"])
        worker_mirror_ops.reconcile()   # A late answer of the dead worker: nothing it settled changes.
        self.pump()
        self.assertEqual(self.mirror.state["messages"], before)
        self.assertFalse(self.mirror.busy)

    def test_a_parked_chat_relaunches_reopens_resumed_and_its_message_reaches_the_model(self):
        self.open()
        self.turn("a box")
        self.assertTrue(lifecycle.close_chat(self.session, "parked"))   # The park's close; nothing pumps after it.
        self.assertIsNone(self.mirror.send_op)
        # What a park's hand-back can leave queued (its stop path: closed, or ended, while the file was still the
        # worker's: ``worker_chat_wiring.handed_back``), keyed to the turn it found; applied only after the next Send.
        found = {"directory": str(self.mirror.store.directory), "generation": self.mirror._request_generation}
        events.put(self.mirror, events.CLOSED, dict(found))
        events.put(self.mirror, events.RECOVERED, dict(found, state=session_journal.read(self.mirror.store.path)[0],
                                                      finished={"status": "interrupted"}))
        relaunched = []

        def resume(controller, deliver, on_error):   # ``worker_queue.resume``: its worker again, the chat resumed.
            relaunched.append(controller)
            lifecycle.open_chat(controller, self.worker, resume=True)
            deliver()
            return True
        ends, finish, before = [], events.finish, self.mirror._request_generation

        def ending(mirror, data):   # Every end the mirror's turns get, in order.
            ends.append((mirror._request_generation, dict(data).get("status")))
            return finish(mirror, data)
        with mock.patch.object(worker_queue, "parked", {self.session}), \
                mock.patch.object(worker_queue, "resume", resume), mock.patch.object(events, "finish", ending):
            self.turn("a sphere")
        self.assertEqual([status for generation, status in ends if generation > before], ["completed"],
                         "The resumed message's turn was ended by a frame of the park (or a later one began)")
        self.assertEqual(relaunched, [self.mirror])
        users = [m["text"] for m in self.mirror.state["messages"] if m.get("role") == "user"]
        self.assertEqual(users, ["a box", "a sphere"])

    def test_a_lost_approve_is_settled_from_the_workers_account_and_runs_once_when_approved_again(self):
        self.open()
        now = [0.0]
        worker_mirror_settle.bind(now=lambda: now[0])
        self.addCleanup(worker_mirror_settle.reset)
        self.mirror.send("a box")
        card = self.card()
        real = worker_consent_lead._hooks["send"]

        def lost(session, request, timeout):   # The Approve's frame never reaches the worker; its sender cannot tell.
            if request.get("op") == "chat.approve":
                raise TimeoutError("no answer within %s s" % timeout)
            return real(session, request, timeout)
        with mock.patch.dict(worker_consent_lead._hooks, {"send": lost}):
            self.approve_drawn(card)   # Its request returns unanswered: the lead asks the worker at once (R3 H2),
            self.assertTrue(wait_for(lambda: card.id in self.mirror.pending and not self.mirror.pending[
                card.id].approved, pump=self.pump), "The lost Approve was not settled without the clock moving")
            self.assertEqual(now[0], 0.0)   # ... never after ANSWER_SECONDS.
        errors = [m for m in self.mirror.state["messages"] if m.get("role") == "error"]
        self.assertEqual(len(errors), 1)   # Says the Approve never reached the worker; nothing ran.
        self.assertIn("didn't reach this chat's worker", errors[0]["text"])
        self.assertEqual(errors[0]["i18n"][0], worker_mirror_settle.LOST)   # Translated whole where shown.
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])
        self.approve_drawn(self.card())
        self.assertTrue(wait_for(lambda: not self.mirror.busy and self.mirror.turn_outcome, pump=self.pump))
        self.assertEqual(self.mirror.turn_outcome[0], "completed")
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene", "angelo_create_cube"])   # Once.


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
