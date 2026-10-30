# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker chat's person reads when its request did not go through (design A′, round-3 UI review R3b):

* a Send a Stop reached the worker before ends Interrupted with the window's "wasn't sent" row (H1);
* a lost Approve says so in its own translatable row, within seconds (H2, ``worker_mirror_settle``);
* a chat op never sent because no worker hosts the chat reads as not open (H4);
* every refusal shows a ``WRK-*`` support code, never a raw refusal code, and every code is documented (D);
* every refusal template reads sensibly with every reason: no reason repeats its template's own words (D).

The mirror is real (``test_native_worker_mirror.Fixture``); the worker's answers are fakes. No Blender.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_mirror import ADDON, Fixture  # noqa: E402
from angelo_agent import (approval_review as review, ui_text, worker_chat_lifecycle as lifecycle,  # noqa: E402
                          worker_consent_lead as lead, worker_merge_gate, worker_mirror, worker_mirror_ops as ops,
                          worker_mirror_opens as opens, worker_mirror_settle as settle, worker_queue, worker_reasons,
                          worker_values)
from angelo_agent.display_text import Text  # noqa: E402
from angelo_agent.refusal_codes import CODES, RefusalError  # noqa: E402

DOC = ADDON.parents[2] / "doc/angelo/agent_integration.rst"
CARD = {"id": "s1", "name": "angelo_create_cube", "digest": "e" * 64, "needs_person": True, "expires": 120,
        "summary": {"arguments": {"name": "A", "size": 1}, "approval": True}}


class Base(Fixture):
    def setUp(self):
        super().setUp()
        lead.reset()
        self.addCleanup(lead.reset)
        self.reply = lambda request: {"ok": True}
        lead.bind(send=lambda session, request, timeout: self.reply(request), spawn=lambda fn: fn(),
                  kill=lambda session: None, connection=lambda controller: True)
        lead._hosted[self.session] = self.mirror
        self.mirror.document = ("angelo-worker", "part", 1)

    def errors(self):
        return [m for m in self.mirror.state["messages"] if m.get("role") == "error"]

    def card(self):
        self.pending(dict(CARD))
        self.mirror.pump_events()
        lead.drew(self.mirror, "s1")
        return self.mirror.pending["s1"]


class StopBeforeSendTests(Base):
    """H1 (proof R3DefectH, inverted): the worker refused the Send for the person's Stop (``lead_seq``)."""

    def test_it_ends_interrupted_with_the_not_sent_row_as_a_withdrawn_send_does(self):
        self.ops.answer = {"error": worker_values.encode_error(RefusalError("stopped first", code="CANCELLED"))}
        self.mirror.send("Make a lamp")
        self.mirror.stop()   # After it left its lane: the worker gets the Stop first and refuses the Send.
        ops.reconcile()
        self.assertEqual(self.mirror.unsent_draft, "Make a lamp")
        self.assertEqual(self.mirror.status, "Interrupted")
        self.assertEqual(self.mirror.turn_outcome[:3], ("interrupted", True, True))
        self.assertEqual(self.mirror.state["messages"][-1]["text"], ops.NOT_SENT)
        self.assertEqual(self.errors(), [])   # Nothing failed: the person stopped it.

    def test_a_cancelled_send_with_no_stop_of_the_persons_still_says_why(self):
        self.ops.answer = {"error": worker_values.encode_error(RefusalError("ended", code="CANCELLED"))}
        self.mirror.send("Make a lamp")
        ops.reconcile()
        self.assertEqual(self.mirror.status, "Failed")
        self.assertIn(worker_reasons.ENDED_FIRST, self.errors()[-1]["text"])

    def test_a_stop_and_a_refusal_of_another_kind_still_says_why(self):
        self.ops.answer = {"error": worker_values.encode_error(RefusalError("busy", code="WRITER_BUSY"))}
        self.mirror.send("Make a lamp")
        self.mirror.stop()
        ops.reconcile()
        self.assertIn("WRK-BUSY", self.errors()[-1]["text"])


class LostApproveTests(Base):
    """H2 (proof R3DefectI, inverted): the lost Approve's row is its own translatable template, said in seconds."""

    def test_the_row_says_the_approval_did_not_reach_the_worker_and_translates_whole(self):
        clock = [0.0]
        settle.bind(spawn=lambda run: run(), now=lambda: clock[0])
        self.addCleanup(settle.reset)
        self.reply = lambda request: {"error": "no answer within 300 s", "unknown": True}
        self.ops.answer = lambda op: {"ok": True, "calls": [dict(CARD)], "outcome": None}
        call = self.card()
        with mock.patch.object(settle, "_worker_alive", lambda mirror: True), mock.patch("builtins.print"):
            self.assertIsNone(lead.approve(self.mirror, "s1"))
            for _ in range(3):   # Its unanswered answer arrives, the question is asked at once, its answer applied.
                self.mirror.pump_events()
        self.assertEqual(clock, [0.0])
        self.assertEqual((call.approved, call.approval), (False, True))
        row = self.errors()[-1]
        self.assertEqual(row["i18n"], [settle.LOST, {"code": worker_reasons.APPROVAL_LOST}])
        self.assertIn("didn't reach this chat's worker", row["text"])

    def test_an_approve_with_no_answer_yet_is_asked_about_within_seconds(self):
        clock = [0.0]
        settle.bind(spawn=lambda run: None, now=lambda: clock[0])
        self.addCleanup(settle.reset)
        self.card()
        self.mirror.approval_sent("s1")
        record = self.mirror._approving["s1"]
        self.assertEqual(record["due"], settle.ASK_SECONDS)
        self.assertLess(settle.ASK_SECONDS, ops.ANSWER_SECONDS / 10)

    def test_a_running_approve_is_asked_again_less_and_less_often_up_to_the_answer_bound(self):
        record = {"wait": settle.ASK_SECONDS}
        waits = []
        for _ in range(12):
            settle._later(record)
            waits.append(record["wait"])
        self.assertEqual(waits[0], settle.ASK_SECONDS * 2)
        self.assertEqual(waits[-1], ops.ANSWER_SECONDS)
        self.assertEqual(waits, sorted(waits))


class NotSentTests(Base):
    """H4: a chat op the lead never sent (no worker hosts the chat) reads as not open, never an internal fault."""

    def test_an_approve_to_a_chat_no_worker_hosts_says_it_is_not_open(self):
        def refuse(request):
            raise lifecycle.ChatOpFailed(request["op"], self.session, "its chat is not open in a worker", sent=False)
        self.reply = refuse
        self.card()
        with mock.patch("builtins.print"):
            lead.approve(self.mirror, "s1")
            self.mirror.pump_events()
        text = self.errors()[-1]["text"]
        self.assertIn(worker_reasons.NOT_OPEN, text)
        self.assertIn("WRK-NOT-OPEN", text)
        self.assertNotIn(worker_reasons.FAILED, text)

    def test_the_consent_lane_marks_its_chat_not_open_as_never_sent(self):
        from angelo_agent import worker_chat_wiring
        with mock.patch.object(lifecycle, "hosted", lambda session: None):
            with self.assertRaises(lifecycle.ChatOpFailed) as caught:
                worker_chat_wiring._consent_send(self.session, {"op": "chat.approve"}, 1.0)
        self.assertIs(caught.exception.sent, False)
        self.assertEqual(worker_reasons.of_error(caught.exception)[1], "WRK-NOT-OPEN")

    def test_a_chat_op_failure_that_may_have_been_sent_is_not_called_not_open(self):
        failed = lifecycle.ChatOpFailed("chat.approve", self.session, "Could not reach the worker")
        self.assertEqual(worker_reasons.of_error(failed)[1], "WRK-INTERNAL")


class SupportCodeTests(unittest.TestCase):
    """D: one documented ``WRK-*`` support code per reason; refusal codes stay in the log."""

    def test_every_reason_has_one_distinct_support_code_in_the_scheme(self):
        marked = {value for name, value in vars(worker_reasons).items()
                  if name.isupper() and isinstance(value, str) and not value.startswith("WRK-")}
        self.assertEqual(marked, set(worker_reasons.SUPPORT))
        codes = list(worker_reasons.SUPPORT.values()) + [worker_reasons.APPROVAL_LOST]
        self.assertEqual(len(codes), len(set(codes)))
        for code in codes:
            self.assertRegex(code, r"^WRK-[A-Z]+(-[A-Z]+)*$")

    def test_no_refusal_code_is_ever_shown_as_a_support_code(self):
        with mock.patch("builtins.print"):
            for code in CODES:
                for kind in ({}, {"gone": True}, {"timed_out": True}, {"consent": True}, {"not_sent": True}):
                    with self.subTest(code=code, kind=kind):
                        _text, support = worker_reasons.explain(dict(kind, error="x"), code)
                        self.assertIn(support, worker_reasons.SUPPORT.values())
                        self.assertNotIn(support, CODES)

    def test_the_log_keeps_the_refusal_code_and_the_raw_words_with_the_support_code(self):
        with mock.patch("builtins.print") as logged:
            worker_reasons.explain({"error": "Traceback: x"}, "WRITER_BUSY", log="chat.send")
        line = str(logged.call_args)
        for kept in ("Traceback: x", "WRITER_BUSY", "WRK-BUSY", "chat.send"):
            self.assertIn(kept, line)

    def test_every_support_code_is_documented(self):
        doc = DOC.read_text(encoding="utf-8")
        for code in list(worker_reasons.SUPPORT.values()) + [worker_reasons.APPROVAL_LOST]:
            self.assertIn("``%s``" % code, doc)


def _words(text):
    return re.findall(r"[a-z]+", text.lower().replace("'s", ""))


# Nouns every refusal names (who and what), and short function words: shared by design, never a repeated action.
SHARED = {"chat", "worker", "workers", "part", "this", "that", "your", "with", "angelo", "lead"}


def _stem(word):
    return word[:5] if len(word) >= 5 else word


class EveryTemplateWithEveryReasonTests(unittest.TestCase):
    """D: every refusal template × every reason it can carry, generated and read for sense."""

    WITH_CODE = (ops.SEND_REFUSED, ops.OPTIONS_REFUSED, opens.APPROVE_REFUSED, opens.DECLINE_REFUSED,
                 worker_mirror.NOT_ACCEPTED, worker_merge_gate.FAILED)
    PLAIN = (lifecycle.OPEN_FAILED,)
    RESTART = (worker_queue.REPLACED, worker_queue.MERGED, worker_queue.CLOSING)

    def combinations(self):
        for template in self.WITH_CODE + self.PLAIN:
            for reason, code in worker_reasons.SUPPORT.items():
                yield template, reason, code
        for reason in tuple(worker_reasons.SUPPORT) + self.RESTART:
            yield worker_queue.RESTART_FAILED, reason, None

    def test_no_reason_repeats_its_templates_own_words_or_reads_circularly(self):
        seen = 0
        for template, reason, code in self.combinations():
            fixed = re.sub(r"\{\w+\}", " ", template)
            own = {_stem(word) for word in _words(fixed) if len(word) >= 4 and word not in SHARED}
            said = {_stem(word) for word in _words(reason) if len(word) >= 4 and word not in SHARED}
            with self.subTest(template=template, reason=reason):
                self.assertEqual(own & said, set(), ui_text._english(template, why=reason, code=code or ""))
                shown = notice_text(template, reason, code)
                self.assertNotRegex(shown.lower(), r"\b(\w+) \1\b")   # No doubled word.
                if "{code}" in template:
                    self.assertEqual(shown.count(code), 1)
            seen += 1
        self.assertEqual(seen, (len(self.WITH_CODE) + len(self.PLAIN) + 1) * len(worker_reasons.SUPPORT)
                         + len(self.RESTART))

    def test_every_reason_is_a_lowercase_clause_and_every_template_takes_it_whole(self):
        for reason in tuple(worker_reasons.SUPPORT) + self.RESTART:
            with self.subTest(reason=reason):
                self.assertFalse(reason.endswith((".", ";")))
                self.assertTrue(reason[0].islower() or reason.startswith("Angelo"))
        for template in self.WITH_CODE:
            self.assertIn("({why}; support code {code})", template)

    def test_a_reason_nested_in_any_template_translates_whole(self):
        catalogue = {template: "<<%s>>" % template for template in self.WITH_CODE}
        catalogue.update({reason: reason.upper() for reason in worker_reasons.SUPPORT})
        ui_text._pgettext["iface"] = lambda message, context=None: catalogue.get(message, message)
        self.addCleanup(ui_text._pgettext.pop, "iface", None)
        for template in self.WITH_CODE:
            row = ui_text.notice(template, why=Text(worker_reasons.BUSY), code="WRK-BUSY")
            shown = ui_text.display_notice(dict(row, role="error"))
            self.assertIn(worker_reasons.BUSY.upper(), shown)
            self.assertTrue(shown.startswith("<<"))


def notice_text(template, reason, code):
    fields = {"why": Text(reason)}
    if "{code}" in template:
        fields["code"] = code
    return ui_text.notice(template, **fields)["text"]


class ProgramApproveTests(Base):
    """Proof R3FixedA, kept: a mirror's program card passes ``approve_current_review``."""

    def test_approve_current_review_passes(self):
        from test_native_review_preparation import call_for, drain
        call = call_for()
        self.pending({"id": "c1", "name": call.name, "digest": "d" * 64, "needs_person": True, "expires": 120,
                      "summary": {"arguments": call.arguments, "approval": True,
                                  "precondition": worker_values.encode(call.precondition),
                                  "recovery_evidence": worker_values.encode(None)}})
        self.mirror.pump_events()
        card = self.mirror.pending["c1"]
        drain(card)
        self.assertTrue(review.approve_current_review(self.mirror, "c1", {"approval_review": review.review_token(card)}))


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
