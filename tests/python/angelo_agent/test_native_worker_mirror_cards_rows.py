# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker-hosted chat's cards, the lead's own rows and their words (design A′, C3, C6), as a window's chat has them:

* a mirror's call carries the chat's document, so a typed program's Approve passes the same review check (A);
* every row the lead adds survives the next read of the chat's file (C);
* a refusal says why in a plain, translatable reason, never the worker's raw words (D);
* every path that draws an Approve records the card it shows, so a click never meets "no longer current" (E);
* Sending shows as Sending, and a Send withdrawn by Stop ends as a window's chat ends it (F);
* closing mirrors never makes the main thread wait per chat (G).

Ported from the UI re-review's proofs (review-ui, round 2). No Blender.
"""

import ast
import contextlib
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from test_native_review_preparation import call_for, drain
from test_native_worker_mirror import ADDON, Fixture

from angelo_agent import (approval_review as review, chat_guard, chat_titles, session_rail, ui_text,  # noqa: E402
                          worker_chat_lifecycle as lifecycle, worker_consent_lead as lead, worker_journal_owner,
                          worker_mirror_close, worker_mirror_ops, worker_mirror_opens as opens, worker_reasons,
                          worker_values)
from angelo_agent.display_text import Text  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402

DOC = ("angelo-worker", "part", 1)
JAPANESE = {opens.APPROVE_REFUSED: "承認は実行されませんでした（{why}、サポートコード {code}）。",
            worker_reasons.TIMED_OUT: "応答なし"}


def translating(catalogue):
    ui_text._pgettext["iface"] = lambda message, context=None: catalogue.get(message, message)


class Base(Fixture):
    def setUp(self):
        super().setUp()
        lead.reset()
        self.addCleanup(lead.reset)
        self.addCleanup(ui_text._pgettext.pop, "iface", None)
        self.answers = []
        lead.bind(send=lambda session, request, timeout: self.answer(request), spawn=lambda fn: fn(),
                  kill=lambda session: None, connection=lambda controller: True)
        lead._hosted[self.session] = self.mirror
        self.mirror.document = DOC

    def answer(self, request):
        self.answers.append(request)
        return {"ok": True}

    def program_card(self, **summary):
        call = call_for()
        self.pending({"id": "c1", "name": call.name, "digest": "d" * 64, "needs_person": True, "expires": 120,
                      "summary": dict({"arguments": call.arguments, "approval": True,
                                       "precondition": worker_values.encode(call.precondition),
                                       "recovery_evidence": worker_values.encode(None)}, **summary)})
        self.mirror.pump_events()
        return self.mirror.pending.get("c1")

    def card(self, call_id="s1", name="angelo_create_cube"):
        self.pending({"id": call_id, "name": name, "digest": "e" * 64, "needs_person": True, "expires": 120,
                      "summary": {"arguments": {"name": "A", "size": 1}, "approval": True}})
        self.mirror.pump_events()
        return self.mirror.pending[call_id]

    def reload(self):
        """The worker writes the chat's file again: the mirror reads it (as after every journal frame)."""
        self.worker_step(("status", {"text": "Thinking"}))
        self.mirror.pump_events()

    def rows(self, role=None):
        return [m for m in self.mirror.state["messages"] if role is None or m.get("role") == role]


class ProgramApproveTests(Base):
    """A: the drawn program card's Approve reaches ``angelo.approve``."""

    def test_a_mirror_call_carries_its_chats_document_and_its_approve_passes_the_review_check(self):
        call = self.program_card()
        drain(call)
        self.assertEqual(call.document, DOC)
        state = {"approval_review": review.review_token(call)}
        self.assertIsNotNone(state["approval_review"])
        self.assertTrue(review.approve_current_review(self.mirror, "c1", state))

    def test_a_card_of_another_document_still_refuses(self):
        call = self.program_card()
        drain(call)
        state = {"approval_review": review.review_token(call)}
        self.mirror.document = ("angelo-worker", "other", 2)
        self.assertFalse(review.approve_current_review(self.mirror, "c1", state))

    def test_no_call_the_mirror_builds_is_of_no_document(self):
        for path in sorted(ADDON.glob("worker_mirror*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ToolCall":
                    document = next((k.value for k in node.keywords if k.arg == "document"), None)
                    with self.subTest(path=path.name):
                        self.assertIsNotNone(document)
                        self.assertFalse(isinstance(document, ast.Constant) and document.value is None)


class LeadRowsReloadTests(Base):
    """C: every row the lead adds to a mirror survives the next read of the chat's file."""

    def assert_kept(self, role, text):
        self.assertTrue(any(text in m.get("text", "") for m in self.rows(role)), self.rows())
        self.reload()
        self.assertTrue(any(text in m.get("text", "") for m in self.rows(role)), self.rows())
        self.reload()
        self.assertEqual(sum(text in m.get("text", "") for m in self.rows(role)), 1)   # Kept once, never twice.

    def test_an_approve_the_worker_refused(self):
        self.answer = lambda request: {"error": "no answer within 2 s"}
        self.card()
        lead.drew(self.mirror, "s1")
        lead.approve(self.mirror, "s1")
        self.mirror.pump_events()
        self.assert_kept("error", "your approval did not go through")

    def test_a_decline_the_worker_refused(self):
        self.answer = lambda request: {"error": "closed"}
        self.card()
        lead.decline(self.mirror, "s1")
        self.mirror.pump_events()
        self.assert_kept("error", "did not apply your Decline")

    def test_a_program_whose_review_could_not_be_prepared(self):
        self.program_card(unencoded=["precondition"])
        self.assert_kept("error", "could not be prepared here")

    def test_a_notice_the_worker_did_not_take_while_it_hosts_the_chat(self):
        with mock.patch.object(worker_journal_owner, "hosted", lambda session: True):
            opens.keep_notice(self.mirror, ui_text.notice("Checked the part."))
            self.assert_kept("system", "Checked the part.")

    def test_a_send_and_an_options_change_the_worker_refused(self):
        self.ops.answer = {"error": {"type": "RefusalError", "message": "x", "code": "CANCELLED"}}
        self.mirror.send("hello")
        worker_mirror_ops.reconcile()
        self.mirror.set_options(effort="high")
        worker_mirror_ops.reconcile()
        self.assert_kept("error", "Your message was not sent")
        self.assert_kept("error", "kept its settings")


class ReasonTests(Base):
    """D: a refusal's reason is plain and translatable; the worker's own words go to the log."""

    RAW = ("no answer within 2 s", "its worker could not send precondition", "chat.approve", "Traceback")

    def assert_plain(self, row):
        for raw in self.RAW + (self.session,):
            self.assertNotIn(raw, row["text"])
        template, fields = row["i18n"]
        self.assertIn(fields["why"][0], self.reasons())   # The reason is a nested recipe of one of the reasons.

    @staticmethod
    def reasons():
        return set(worker_reasons.SUPPORT)   # Every reason has its one support code.

    def test_each_kind_of_answer_maps_to_one_reason(self):
        cases = (({"error": "no answer within 2 s", "timed_out": True}, "UNKNOWN", worker_reasons.TIMED_OUT),
                 ({"error": "x", "gone": True}, "OWNERSHIP", worker_reasons.GONE),
                 ({"error": "x", "consent": True}, "CANCELLED", worker_reasons.CONSENT),
                 ({"error": "x"}, "STALE_CONTEXT", worker_reasons.CHANGED),
                 ({"error": "x"}, "WRITER_BUSY", worker_reasons.BUSY),
                 ({"error": "x"}, "INTERNAL_ERROR", worker_reasons.FAILED),
                 ({"error": "x"}, "NOT_A_CODE", worker_reasons.FAILED),
                 (None, "UNKNOWN", worker_reasons.FAILED))
        for answer, code, expected in cases:
            with self.subTest(code=code):
                said = worker_reasons.reason(answer, code)
                self.assertIsInstance(said, Text)
                self.assertEqual(said.template, expected)

    def test_every_code_has_a_reason_and_every_reason_is_marked_for_the_extractor(self):
        from angelo_agent.refusal_codes import CODES
        self.assertTrue(all(worker_reasons.reason({}, code) in self.reasons() for code in CODES))
        marked = {node.args[0].value for node in ast.walk(ast.parse((ADDON / "worker_reasons.py").read_text()))
                  if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "n_"}
        self.assertEqual(marked, self.reasons())

    def test_a_refused_approve_reads_plainly_and_translates_whole(self):
        self.answer = lambda request: {"error": "no answer within 2 s"}
        self.card()
        lead.drew(self.mirror, "s1")
        with mock.patch("builtins.print") as logged:
            lead.approve(self.mirror, "s1")
            self.mirror.pump_events()
        row = self.rows("error")[-1]
        self.assert_plain(row)
        self.assertTrue(any("no answer within 2 s" in str(c) for c in logged.call_args_list))   # Kept for support.
        self.answer = lambda request: {"error": "x"}
        timed = ui_text.notice(opens.APPROVE_REFUSED, why=Text(worker_reasons.TIMED_OUT), code="WRK-NO-ANSWER")
        translating(JAPANESE)
        self.assertEqual(ui_text.display_notice(dict(timed, role="error")),
                         "承認は実行されませんでした（応答なし、サポートコード WRK-NO-ANSWER）。")

    def test_a_refused_send_keeps_its_support_code_but_not_the_workers_words(self):
        encoded = worker_values.encode_error(RefusalError("Traceback: its worker could not send precondition",
                                                          code="CANCELLED"))
        self.ops.answer = {"error": encoded}
        self.mirror.send("hello")
        with mock.patch("builtins.print"):
            worker_mirror_ops.reconcile()
        row = self.rows("error")[-1]
        self.assert_plain(row)
        self.assertIn("WRK-TURN-ENDED", row["text"])   # Its support code, never the raw refusal code.
        self.assertNotIn("CANCELLED", row["text"])
        self.assertEqual(row["i18n"][0], worker_mirror_ops.SEND_REFUSED)

    def test_a_chat_op_that_timed_out_says_so(self):
        failed = lifecycle.ChatOpFailed("chat.open", self.session, "no answer within 2 s", timed_out=True)
        with mock.patch("builtins.print"):
            text, code = failed.shown()
        self.assertEqual((text.template, code), (worker_reasons.TIMED_OUT, "WRK-NO-ANSWER"))

    def test_an_open_that_raised_anything_else_shows_a_reason(self):
        said = []
        chat = types.SimpleNamespace(emit=lambda kind, data: said.append(data))
        with mock.patch("builtins.print"):
            lifecycle.tell(chat, RuntimeError("Traceback (most recent call last)"))
        self.assert_plain(said[-1])
        self.assertEqual(said[-1]["i18n"][0], lifecycle.OPEN_FAILED)


class RecipeTests(unittest.TestCase):
    """D: a notice field may be one translatable sentence (a nested recipe), never deeper."""

    def tearDown(self):
        ui_text._pgettext.pop("iface", None)

    def test_a_text_field_nests_redacts_and_round_trips(self):
        row = ui_text.notice("Refused ({why}).", why=Text("its {part} ended", part="Sofa"))
        self.assertEqual(row, {"text": "Refused (its Sofa ended).", "i18n": ["Refused ({why}).",
                                                                             {"why": ["its {part} ended",
                                                                                      {"part": "Sofa"}]}]})
        self.assertEqual(ui_text.stored_recipe(row), row["i18n"])
        self.assertEqual(ui_text.clean_recipe(row["i18n"], redact=lambda s: s.replace("Sofa", "***"))[1]["why"],
                         ["its {part} ended", {"part": "***"}])
        translating({"Refused ({why}).": "拒否（{why}）", "its {part} ended": "{part}は終了"})
        self.assertEqual(ui_text.display_notice(row), "拒否（Sofaは終了）")

    def test_deeper_or_malformed_nesting_is_refused(self):
        for bad in (["x", {"a": ["y", {"b": ["z", {}]}]}], ["x", {"a": [1, {}]}], ["x", {"a": ["y", []]}],
                    ["x", {"a": ["y", {"b": object()}]}], ["x", {"a": ["y"]}]):
            with self.subTest(bad=repr(bad)[:40]):
                self.assertIsNone(ui_text.clean_recipe(bad))


class ApproveDrawingPathTests(Base):
    """E: every path that draws an Approve records the card it shows (``chat_guard.shown_approvals``)."""

    DRAWERS = {"chat_card_draw", "chat_guard", "flow_chat"}   # Register an "approve" hit in the chat's transcript.
    OTHERS = {"chat_view", "worker_chat_host", "providers_page_model"}   # Dispatch, op names, a word match.

    def test_every_module_with_an_approve_action_is_a_known_drawer_or_none(self):
        found = set()
        for path in ADDON.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and node.value == "approve":
                    found.add(path.stem)
        self.assertEqual(found, self.DRAWERS | self.OTHERS,
                         "A new Approve: draw it in the chat's transcript, whose hits go through shown_approvals")

    def test_the_transcripts_hits_go_through_the_sweep_after_every_row_drew(self):
        source = (ADDON / "chat_view.py").read_text()
        sweep = source.index("chat_guard.shown_approvals(controller, clipped_hits(state[\"hits\"], message_viewport))")
        for drawer in ("chat_card_draw.draw(role", "flow_chat.draw(ops", "chat_guard.draw_approval(state",
                       "chat_guard.draw_plain(state", "chat_rows.draw(role"):
            self.assertLess(source.index(drawer), sweep, drawer)
        self.assertNotIn("drew(", (ADDON / "chat_card_draw.py").read_text())   # One mechanism, for every path.

    def stubbed(self):
        view = types.ModuleType("angelo_agent.chat_view")
        for name in ("TEXT", "BG", "MUTED"):
            setattr(view, name, (1, 1, 1, 1))
        view.text = view.shape = lambda *args, **kwargs: None
        view.measure = lambda value, size: 10
        view.wrap = lambda value, width, size, font: [(value, 0)]
        view._font = 0
        flows = types.ModuleType("angelo_agent.flow_host")   # Flows read Blender's library: none here.
        flows.library = lambda: types.SimpleNamespace(assets={})
        import angelo_agent
        from angelo_agent import chat_card_draw, flow_chat
        modules = mock.patch.dict(sys.modules, {"angelo_agent.chat_view": view, "angelo_agent.flow_host": flows,
                                                "gpu": mock.MagicMock()})
        package = mock.patch.multiple(angelo_agent, create=True, chat_view=view, flow_host=flows)
        return modules, package, \
            mock.patch.object(chat_card_draw, "_measure", lambda value, size, font=None: 40), \
            mock.patch.object(flow_chat, "is_run_review", lambda call: False)

    def approve_after(self, draw):
        state = {"hits": [], "message_rects": {}}
        with contextlib.ExitStack() as stack:
            for patch in self.stubbed():
                stack.enter_context(patch)
            draw(state)
        self.assertIn("approve", [action for _rect, action, _value in state["hits"]])
        chat_guard.shown_approvals(self.mirror, state["hits"])
        return lead.approve(self.mirror, "p1")

    def test_a_plain_card_approves(self):
        call = self.card("p1", "angelo_set_transform")
        self.assertIsNone(self.approve_after(
            lambda state: chat_guard.draw_approval(state, call, True, [("Move", 0)], 200, 100, 14, 20)))

    def test_a_row_that_raised_and_drew_plainly_approves(self):
        self.card("p1", "angelo_set_transform")
        self.assertIsNone(self.approve_after(lambda state: chat_guard.draw_plain(
            state, (None, (0, 0, 10, 10)), 0, "approval", {}, [("Move", 0)], "p1", RuntimeError("x"), 200, 100,
            400, 14, 20, self.mirror)))

    def test_a_flow_cards_run_button_approves(self):
        self.card("p1", "angelo_flow_run")
        from angelo_agent import flow_chat
        self.assertIsNone(self.approve_after(lambda state: flow_chat.draw(
            [("hit", (0, 0, 10, 10), "approve", "p1")], state["hits"], None, None, None, None)))

    def test_a_card_never_drawn_sends_the_mirrors_digest(self):
        self.card("p1", "angelo_set_transform")   # No drawing recorded it: an Approve from the lead, not a click.
        self.assertIsNone(lead.approve(self.mirror, "p1"))


class SendingTests(Base):
    """F: the live row says Sending; a Send withdrawn by Stop ends interrupted and not sent, as in a window's chat."""

    def test_the_live_row_shows_sending(self):
        self.assertIn(worker_mirror_ops.SENDING, chat_titles.LIVE)
        self.assertEqual(chat_titles.live_label(worker_mirror_ops.SENDING), "Sending")

    def test_a_send_withdrawn_by_stop_is_interrupted_not_failed(self):
        worker_mirror_ops.bind(spawn=lambda run: None)   # The lane never runs: the Send has not left it.
        self.mirror.send("hello")
        self.assertEqual(self.mirror.status, worker_mirror_ops.SENDING)
        self.mirror.stop()
        self.assertEqual(self.mirror.status, "Interrupted")
        self.assertEqual(self.mirror.unsent_draft, "hello")
        self.assertEqual(self.mirror.turn_outcome[:3], ("interrupted", True, True))
        self.assertEqual(session_rail.turn_end([{"role": "user", "text": "hello"}], outcome=(
            self.mirror.turn_outcome[0], True, True, 1))[0], "not_sent")
        self.assertIn(worker_mirror_ops.NOT_SENT, [m.get("text") for m in self.rows("system")])
        self.reload()
        self.assertIn(worker_mirror_ops.NOT_SENT, [m.get("text") for m in self.rows("system")])

    def test_a_refused_send_still_fails(self):
        self.ops.answer = {"error": {"type": "RefusalError", "message": "x", "code": "CANCELLED"}}
        self.mirror.send("hello")
        with mock.patch("builtins.print"):
            worker_mirror_ops.reconcile()
        self.assertEqual(self.mirror.status, "Failed")


class CloseTests(Base):
    """G: closing mirrors never waits on the main thread per chat; the add-on's end waits one bound for all."""

    def test_close_returns_at_once_and_the_end_waits_one_bound_for_every_wedged_chat(self):
        release, started = threading.Event(), []

        def wedged(session, reason, timeout=lifecycle.CLOSE_SECONDS):
            started.append(session)
            release.wait(10)
        self.addCleanup(release.set)
        with mock.patch.object(lifecycle, "hosted", lambda session: object()), \
                mock.patch.object(lifecycle, "close_chat", wedged):
            began = time.monotonic()
            for _ in range(4):
                self.mirror.close()
            self.assertLess(time.monotonic() - began, 0.5)
            began = time.monotonic()
            worker_mirror_close.wait_closed(0.3)
            self.assertLess(time.monotonic() - began, 1.0)   # One shared bound, not 0.3 s per chat.
        release.set()
        worker_mirror_close.wait_closed(2)
        self.assertEqual(len(started), 4)

    def test_a_chat_nothing_hosts_sends_no_close(self):
        with mock.patch.object(lifecycle, "hosted", lambda session: None), \
                mock.patch.object(lifecycle, "close_chat", lambda *a, **k: self.fail("sent")):
            self.mirror.close()
        self.assertIsNone(worker_mirror_close.closing(self.session))

    def test_the_close_module_touches_no_blender_data_and_is_no_atexit_hook(self):
        source = (ADDON / "worker_mirror_close.py").read_text()
        for word in ("import bpy", "atexit"):
            self.assertNotIn(word, source.split('"""', 2)[2])


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
