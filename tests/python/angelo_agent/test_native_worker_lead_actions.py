# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's actions on a worker-hosted chat (design A′), ported from the break-it reviews' proofs (2026-10-27):
each test failed while its defect was present. No Blender.

* S3: Approve sends the digest the card showed when it was drawn, and a review is of one (id, digest).
* U2: the window's connection sweeps never raise on a mirror; a key replace completes.
* U3: a Restore never reports "partial" because of a worker chat.
* U4: a model picked on the chat's own connection is a ``chat.options`` change; another connection is refused,
  translated.
* U5: the card's Always allow scripts reaches the worker: the lease grants it and the drawn script is approved.
* U7: worker refusals reach the chat decoded (support code, no chat id or op name), the mirror's texts are
  translatable, the lead's error rows survive reloads, and attachments come back with an unsent message.
* C3 (lead side): a close the worker answers ``{ok, closed: false}`` hands the chat back, so a retried close ends.
* N1: one host per chat: a refused open never overwrites the worker that still hosts the chat.
"""

import ast
import os
import sys
import textwrap
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (worker_chat_lifecycle as lifecycle, worker_chat_wiring as wiring,  # noqa: E402
                          worker_consent_lead, worker_mirror_events, worker_mirror_ops, worker_values)
from angelo_agent import worker_reasons  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.display_text import Text  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.worker_mirror import MirrorRefused  # noqa: E402
from test_native_worker_chat_lifecycle import Base as LifecycleBase, OpWorker, chat  # noqa: E402
from test_native_worker_mirror import Fixture, TURN  # noqa: E402

UI = (ADDON / "ui.py").read_text()


def _function(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise LookupError(name)


class Hosted(Fixture):
    """The mirror registered with the consent lane, its chat ops recorded and sent inline."""

    def setUp(self):
        super().setUp()
        switch = mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"})   # A′, whatever the run's switch.
        switch.start()
        self.addCleanup(switch.stop)
        self.sent, self.killed = [], []
        worker_consent_lead.reset()
        self.addCleanup(worker_consent_lead.reset)
        worker_consent_lead.bind(send=lambda session, request, timeout: self.sent.append(request) or {
            "ok": True, "epoch": request.get("epoch")}, kill=self.killed.append, spawn=lambda run: run(),
            connection=lambda controller: True)
        worker_consent_lead.register(self.mirror)

    def card(self, call_id="c1", name="angelo_create_cube", digest="a" * 64, **summary):
        self.pending({"id": call_id, "name": name, "digest": digest, "needs_person": True, "expires": 120,
                      "summary": dict({"arguments": {"name": "Seat", "size": 1}, "approval": True}, **summary)})
        self.mirror.pump_events()
        return self.mirror.pending[call_id]

    def approvals(self):
        return [(r["call_id"], r["digest"]) for r in self.sent if r["op"] == "chat.approve"]


class ApproveTheDrawnDigest(Hosted):
    """S3."""

    def test_a_review_is_of_one_digest(self):
        mirror = SimpleNamespace(pending={}, reviewed_calls=set(), state={"id": "s"})   # The review's own PoC.
        entry = {"id": "c1", "name": "angelo_execute_python", "digest": "a" * 64, "needs_person": True,
                 "summary": {"arguments": {"code": "x"}, "approval": True}}
        mirror.pending = worker_mirror_events.pending_calls(mirror, [entry])
        mirror.reviewed_calls.add("c1")
        changed = dict(entry, digest="b" * 64, summary={"arguments": {"code": "x"}, "approval": True,
                                                         "program_arguments": {"changed": True}})
        mirror.pending = worker_mirror_events.pending_calls(mirror, [changed])
        self.assertNotIn("c1", mirror.reviewed_calls)
        kept = worker_mirror_events.pending_calls(mirror, [changed])   # The same digest keeps its review.
        mirror.reviewed_calls.add("c1")
        mirror.pending = kept
        worker_mirror_events.pending_calls(mirror, [changed])
        self.assertIn("c1", mirror.reviewed_calls)

    def test_approve_sends_the_digest_drawn_never_one_that_arrived_after(self):
        self.card(digest="a" * 64)
        worker_consent_lead.drew(self.mirror, "c1")
        self.card(digest="b" * 64, program_arguments={"rebound": True})   # Arrived; the card is not redrawn yet.
        self.assertEqual(worker_consent_lead.approve(self.mirror, "c1"), worker_consent_lead.NOT_SHOWN)
        self.assertEqual(self.approvals(), [])
        worker_consent_lead.drew(self.mirror, "c1")   # The redraw shows the new version.
        self.assertIsNone(worker_consent_lead.approve(self.mirror, "c1"))
        self.assertEqual(self.approvals(), [("c1", "b" * 64)])

    def test_a_card_never_drawn_is_approved_as_the_mirror_holds_it(self):
        """An Approve from the lead while no chat area shows the worker's chat (the rail's tile does): refused, the
        worker waited until its approval expired (78e17c6e1a1's Town benchmark)."""
        self.card()
        self.assertIsNone(worker_consent_lead.approve(self.mirror, "c1"))
        self.assertEqual(self.approvals(), [("c1", "a" * 64)])


class ConnectionSweeps(Hosted):
    """U2 and U3."""

    def test_replace_connection_key_completes_with_a_mirror_registered(self):
        lead = Controller(self.directory, session_id=str(uuid.uuid4()))
        self.addCleanup(lead.close)
        self.mirror.state["connection_id"] = self.mirror._pins["connection_id"] = "conn"   # As Angelo pinned it.
        namespace = {"connection_in_use": lambda identity: False, "clean_key": lambda s: s,
                     "_controllers": {"mirror": self.mirror, "lead": lead}, "_memory_keys": {},
                     "catalogs": lambda: SimpleNamespace(invalidate=lambda identity: None),
                     "_api_connections": SimpleNamespace(invalidate=lambda identity: None),
                     "RuntimeError": RuntimeError, "Text": str, "n_": str, "threading": __import__("threading"),
                     "runtime_bindings": SimpleNamespace(retire_connection=lambda identity: None)}
        exec(textwrap.dedent(_function(UI, "replace_connection_key")), namespace)
        namespace["replace_connection_key"]({"id": "conn"}, "sk-test")
        self.assertEqual(namespace["_memory_keys"].get("conn"), "sk-test")
        self.assertEqual(self.killed, [self.session])   # Its idle model child ends: the next launch has the new key.

    def test_invalidate_runtime_never_raises_and_spares_other_connections_and_busy_turns(self):
        self.mirror.state["connection_id"] = self.mirror._pins["connection_id"] = "conn"   # As Angelo pinned it.
        self.mirror.invalidate_runtime("other")
        self.mirror.busy = True
        self.mirror.invalidate_runtime("conn")   # The sweep's own Stop ends a busy turn; its kill follows.
        self.assertEqual(self.killed, [])
        self.mirror.busy = False
        self.mirror.invalidate_runtime()
        self.assertEqual(self.killed, [self.session])

    def test_a_restore_badges_nothing_and_never_raises_for_a_worker_chat(self):
        from angelo_agent.checkpoint_review_ui import mark_scene_history
        self.mirror.store.invalidate()   # A cache flag of its own unused writer: allowed.
        before = [dict(m) for m in self.mirror.state["messages"]]
        mark_scene_history(self.mirror, {"created": time.time()}, 0)
        self.assertEqual(self.mirror.state["messages"], before)


class ModelPick(Hosted):
    """U4."""

    MODELS = [{"id": "m1", "default": True, "efforts": ["low", "high"], "default_effort": "low", "fast_tier": False},
              {"id": "m2", "default": False, "efforts": ["high"], "default_effort": "high", "fast_tier": True}]

    def test_a_model_on_the_same_connection_is_an_options_change(self):
        self.mirror.state.update(provider="codex_api", connection_id="conn")
        self.mirror._pins.update(provider="codex_api", connection_id="conn")   # The chat's route, as Angelo pinned it.
        self.mirror.choose_connection("codex_api", {}, "", "conn", self.MODELS, "m2")
        self.assertEqual(self.ops.calls[-1][1:], ("chat.options", {"options": {"model": "m2", "effort": "high",
                                                                               "fast": False}}))
        self.assertEqual(self.mirror.state["options"]["model"], "m2")   # Shown at once; the journal confirms it.
        with self.assertRaises(ValueError):
            self.mirror.choose_connection("codex_api", {}, "", "conn", self.MODELS, "missing")

    def test_another_connection_is_refused_translated_and_nothing_is_sent(self):
        self.mirror.state.update(provider="codex_api", connection_id="conn")
        self.mirror._pins.update(provider="codex_api", connection_id="conn")   # The chat's route, as Angelo pinned it.
        sent = len(self.ops.calls)
        with self.assertRaises(RuntimeError) as caught:
            self.mirror.choose_connection("claude_api", {}, "", "other", self.MODELS, "m1")
        self.assertIsInstance(caught.exception.args[0], Text)
        self.assertEqual(len(self.ops.calls), sent)


class AlwaysAllow(Hosted):
    """U5."""

    def test_always_allow_on_a_worker_card_grants_the_lease_and_approves_the_drawn_script(self):
        from angelo_agent import always_allow, managed_policy
        self.mirror.state["options"]["approval"] = "AUTO_BOUNDED"
        call = self.card("s1", "angelo_execute_python", "e" * 64, arguments={"code": "import bpy\nprint(1)"})
        worker_consent_lead.drew(self.mirror, "s1")
        self.mirror.document, self.mirror.document_invalid = ("angelo-worker", "x"), False
        token = object()
        with mock.patch.object(always_allow, "token", return_value=token), \
                mock.patch.object(type(self.mirror), "always_allow_scripts", new=True), \
                mock.patch.object(managed_policy, "current", return_value=SimpleNamespace(
                    allow_auto_approval=True, require_ask_for_edits=False)):
            self.assertTrue(self.mirror.offers_always_allow(call))
            self.assertTrue(self.mirror.always_allow_script(call))
        leases = [r["grants"] for r in self.sent if r["op"] == "chat.consent"]
        self.assertTrue(leases and leases[-1].get("always_allow"))   # The worker's lease grants it from now on.
        self.assertEqual(self.approvals(), [("s1", "e" * 64)])        # And this script runs there.


class ErrorsAndDrafts(Hosted):
    """U7."""

    def test_an_error_row_the_lead_adds_survives_the_next_journal(self):
        self.mirror.emit("message", {"role": "error", "text": "The worker did not take it"})
        self.worker_step(("status", {"text": "Thinking"}))   # Any worker save.
        self.mirror.pump_events()
        self.assertEqual([m["text"] for m in self.mirror.state["messages"] if m.get("role") == "error"],
                         ["The worker did not take it"])
        self.worker_step(("message", {"role": "assistant", "text": "Later"}))
        self.mirror.pump_events()
        roles = [m["role"] for m in self.mirror.state["messages"]]
        self.assertEqual(roles[-2:], ["error", "assistant"])   # Where it was, before what came after it.

    def test_attachments_come_back_with_a_message_the_worker_never_sent(self):
        reference = {"path": "/tmp/ref.png", "name": "ref.png"}
        self.mirror.attachments = [dict(reference)]
        self.mirror.send("Use this")
        self.assertEqual(self.mirror.attachments, [])
        self.frame("finished", dict({"status": "failed", "not_sent": True}, **TURN))
        self.mirror.pump_events()
        self.assertEqual((self.mirror.unsent_draft, self.mirror.attachments), ("Use this", [reference]))

    def test_a_worker_refusal_reaches_the_chat_decoded_with_its_support_code(self):
        encoded = worker_values.encode_error(RefusalError("The document changed; start a new chat", code="CANCELLED"))
        worker = SimpleNamespace(request=lambda op, timeout, **fields: {"error": encoded},
                                 process=SimpleNamespace(poll=lambda: None))
        self.mirror.send_op = wiring.send_op(worker)
        self.mirror.send("hello")
        worker_mirror_ops.reconcile()
        row = self.mirror.state["messages"][-1]
        self.assertEqual(row["role"], "error")
        self.assertIn(worker_reasons.ENDED_FIRST, row["text"])   # Its code's plain reason; its words go to the log.
        self.assertNotIn("The document changed; start a new chat", row["text"])
        self.assertIn("WRK-TURN-ENDED", row["text"])   # Its support code (worker_reasons.SUPPORT).
        for leak in (self.session, "chat.send", "{'type'"):
            self.assertNotIn(leak, row["text"])
        self.assertEqual(row["i18n"][0], worker_mirror_ops.SEND_REFUSED)   # Translated as it is drawn.
        self.assertEqual(self.mirror.unsent_draft, "hello")

    def test_the_mirrors_own_texts_are_translatable(self):
        self.mirror.busy = True
        with self.assertRaises(RuntimeError) as caught:
            self.mirror.send("again")
        self.assertIsInstance(caught.exception.args[0], Text)
        self.mirror.busy = False
        with self.assertRaises(ValueError) as caught:
            self.mirror.send("   ")
        self.assertIsInstance(caught.exception.args[0], Text)
        self.mirror.send_op = None
        with self.assertRaises(MirrorRefused) as caught:
            self.mirror.decline_tool("c1")
        self.assertIsInstance(caught.exception.args[0], Text)


class CloseAndOpen(LifecycleBase):
    """C3 (lead side) and N1."""

    def test_a_close_answered_closed_false_hands_the_chat_back(self):
        worker = OpWorker(answers={"chat.close": {"ok": True, "closed": False}})
        self.host("w1", worker)
        self.assertTrue(lifecycle.close_chat("w1", "parked"))
        self.assertIsNone(lifecycle.hosted("w1"))
        self.assertEqual(self.handed, [("w1", True)])

    def test_a_close_that_timed_out_is_retried_and_ends(self):
        from angelo_agent.worker_process import WorkerUnavailable
        answers = [WorkerUnavailable("no answer within 5 s", timed_out=True), {"ok": True, "closed": False}]
        worker = OpWorker(answers={"chat.close": lambda fields: answers.pop(0)})
        self.host("w1", worker)
        with self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.close_chat("w1", "parked")
        self.assertIs(lifecycle.hosted("w1"), worker)   # Still its worker's: it may have applied it.
        self.assertTrue(lifecycle.close_chat("w1", "parked"))   # The retry: it hosts no such chat any more.
        self.assertEqual(self.handed, [("w1", True)])

    def test_an_open_on_another_worker_never_overwrites_the_one_that_hosts_the_chat(self):
        old, new = OpWorker("/p/old.blend"), OpWorker("/p/new.blend")
        lifecycle.open_chat(chat(), old, resume=False)
        with self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.open_chat(chat(), new, resume=True)
        self.assertIs(lifecycle.hosted("w1"), old)
        self.assertEqual((new.requests, lifecycle.hosted_on(new)), ([], []))
        lifecycle.ended(new)   # The refused worker's stop hands back nothing.
        self.assertEqual(self.handed, [])
        old.code = 0
        lifecycle.ended(old)   # The host's own stop still does.
        self.assertEqual(self.handed, [("w1", False)])

    def test_an_open_refused_while_preparing_is_not_hosted_there(self):
        refused = lifecycle.ChatOpFailed("chat.open", "w1", "This chat's file has another writer")
        worker = OpWorker()
        with mock.patch.object(lifecycle, "_prepare", side_effect=refused), \
                self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.open_chat(chat(), worker, resume=False)
        self.assertIsNone(lifecycle.hosted("w1"))
        lifecycle.ended(worker)
        self.assertEqual((worker.requests, self.handed), ([], []))

    def test_an_open_failure_tells_the_chat_without_its_id_or_op(self):
        encoded = worker_values.encode_error(RefusalError("No model runtime could start", code="UNSUPPORTED"))
        worker = OpWorker(answers={"chat.open": {"error": encoded}})
        said = []
        controller = SimpleNamespace(emit=lambda kind, data: said.append(data))
        with self.assertRaises(lifecycle.ChatOpFailed) as caught:
            lifecycle.open_chat(chat(), worker, resume=False)
        lifecycle.tell(controller, caught.exception)
        self.assertIn(worker_reasons.UNSUPPORTED, said[0]["text"])   # Its code's plain, translatable reason.
        self.assertNotIn("No model runtime could start", said[0]["text"])   # The worker's words: the log only.
        self.assertNotIn("chat.open", said[0]["text"])
        self.assertNotIn("w1", said[0]["text"])


if __name__ == "__main__":
    unittest.main()
