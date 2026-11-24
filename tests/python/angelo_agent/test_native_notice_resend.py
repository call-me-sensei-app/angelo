# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A failed turn whose card invites trying again can be sent again in one click (``chat_resend``, ``chat_notices``).

Evidence (1.0.10 maintainer ruling): "Put message back" made trying again three steps (put back, check, Enter) on
cards whose only advice is "try again". The class: a card's way out is a control, and a paid request is never sent
again without the person's click. Every notice kind is classified, so a new kind can't silently offer (or miss)
Resend; one click sends exactly the stored words and attachments once through ``console.send_message`` (the same
entry point as Enter), a refusal is the card's reason with "Put message back" still there, missing attachments are
refused before anything is sent, and the person's draft is left alone.
"""

import suite_isolation  # noqa: F401  Standalone provider isolation.

import ast
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import notice_kinds  # noqa: E402
from angelo_agent.display_text import Text  # noqa: E402
from angelo_agent.ui_text import n_  # noqa: E402

STUBS = ("bpy", "bpy.props", "bpy.types", "bpy.app", "bpy.app.handlers", "bpy.utils", "blf", "gpu", "gpu_extras",
         "gpu_extras.batch", "mathutils", "bpy_extras", "bpy_extras.io_utils", "bl_math")
PHOTO = {"id": "a1", "name": "chair.png", "kind": "image", "bytes": 2048, "path": "/state/a1.png"}
NOTES = {"id": "b2", "name": "notes.md", "kind": "text", "bytes": 300, "path": "/state/b2.md"}
TECHNICAL = "Traceback: RuntimeError(0x7f3a) in provider stream"


def chat(*, status="failed", references=(PHOTO, NOTES), gone=()):
    rows = [{"role": "user", "text": "Model a chair", "references": [dict(r) for r in references]},
            {"role": "assistant", "text": "Working on it"}, {"role": "error", "text": TECHNICAL}]

    def checked_path(reference):
        if reference["id"] in gone:
            raise ValueError("This image reference is unavailable; attach it again")
        return Path(reference["path"])

    return SimpleNamespace(state={"id": "c1", "messages": rows}, turn_outcome=(status, False, False, len(rows)),
                           attachments=[dict(PHOTO, id="draft")], chosen_tools=[{"tool": "bevel"}], busy=False,
                           version=0, selection_provider="", voice=None, require_model_chat=lambda: None,
                           references=SimpleNamespace(problem=lambda reference: None, checked_path=checked_path))


class Stubbed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._before = set(sys.modules)
        stubs = {name: mock.MagicMock(name=name) for name in STUBS}
        same = lambda message, context=None: message   # noqa: E731  English, as without Blender.
        stubs["bpy.app"].translations = SimpleNamespace(pgettext_iface=same, pgettext_tip=same, pgettext_rpt=same,
                                                        pgettext_data=same, locale="en_US")
        from angelo_agent import ui_text
        cls._translations = mock.patch.dict(ui_text._pgettext, clear=True)   # Its cache must not keep the stand-in.
        cls._translations.start()
        cls._modules = mock.patch.dict(sys.modules, stubs)
        cls._modules.start()
        from angelo_agent import chat_notices, chat_welcome, console
        cls.notices, cls.console = chat_notices, console
        cls._items = [mock.patch.object(chat_welcome, name, lambda ui, controller: [])
                      for name in ("notice_items", "sign_in_items")]
        for patch in cls._items:
            patch.start()

    @classmethod
    def tearDownClass(cls):
        for patch in cls._items:
            patch.stop()
        added = [name for name in sys.modules if name not in cls._before and name.startswith("angelo_agent.")]
        cls._modules.stop()
        cls._translations.stop()
        package = sys.modules.get("angelo_agent")
        for name in added:
            if package is not None and name.count(".") == 1:
                package.__dict__.pop(name.split(".", 1)[1], None)

    def card(self, controller, kind=None, latest=True):
        """The failed turn's own card: its last row before what its end added (``turn_tail``)."""
        from angelo_agent import turn_tail
        row = turn_tail.last_row(controller.state["messages"])
        if kind is None:
            return self.notices._card(row, self.key(controller), latest, SimpleNamespace(), controller, set())
        with mock.patch.object(notice_kinds, "kind", lambda message, by_text=True: kind):
            return self.notices._card(row, self.key(controller), latest, SimpleNamespace(), controller, set())

    @staticmethod
    def key(controller):
        from angelo_agent import turn_tail
        rows = controller.state["messages"]
        index = turn_tail.last(rows)
        return (index, id(rows[index]))

    @staticmethod
    def actions(card):
        return [action for action, *_ in card["actions"]]

    def click(self, controller, send=None):
        """The person's click on the card's Resend, with ``console.send_message`` recorded (or ``send`` run)."""
        calls = []

        def record(chat, text, references=None, **options):
            calls.append((chat, text, references))
            if send is not None:
                send(chat, text, references)
            # Unit seam: the real-path lifecycle regressions cover admission/refusal separately.
            receipt = options.get("submission")
            if receipt is not None:
                self.assertIs(options.get("run_chosen_tools"), False)
                receipt.queued(SimpleNamespace())

        with mock.patch.object(self.console, "send_message", record):
            self.notices.dispatch(None, controller, {}, self.notices.RESEND, self.key(controller))
        return calls


class Classified(Stubbed):
    def test_every_notice_kind_is_classified_as_resendable_or_not(self):
        """A kind added to ``notice_kinds.KINDS`` fails here until ``chat_resend.RESEND`` says whether it offers it."""
        from angelo_agent import chat_resend
        self.assertEqual(sorted(chat_resend.RESEND), sorted(notice_kinds.KINDS))
        self.assertLessEqual(set(self.notices._KNOWN), set(notice_kinds.KINDS), "a card for a kind nobody returns")

    def test_resend_is_offered_exactly_where_the_card_says_try_again(self):
        """Derived from the cards themselves: a kind whose words invite trying again offers Resend, no other."""
        from angelo_agent import chat_resend
        for kind in notice_kinds.KINDS:
            card = self.card(chat(), kind)
            says = "try again" in (card["title"] + " " + card["body"]).lower()
            self.assertEqual(chat_resend.RESEND[kind], says, kind)
            self.assertEqual(self.notices.RESEND in self.actions(card), says, kind)
            self.assertIn(self.notices.PUT_BACK, self.actions(card), kind)   # Put back stays on every failed turn.

    def test_never_where_trying_again_cant_help(self):
        for kind in ("signin", "credits", "usage", "context", "model"):
            self.assertNotIn(self.notices.RESEND, self.actions(self.card(chat(), kind)), kind)

    def test_only_the_failed_turns_own_latest_card_offers_it(self):
        self.assertNotIn(self.notices.RESEND, self.actions(self.card(chat(), latest=False)))
        self.assertNotIn(self.notices.RESEND, self.actions(self.card(chat(status="completed"))))
        nobody = chat()
        nobody.state["messages"][0]["request_kind"] = "angelo_event"   # Angelo's own request: nothing of theirs.
        self.assertNotIn(self.notices.RESEND, self.actions(self.card(nobody)))

    def test_its_label_is_translated_and_its_tooltip_says_what_it_costs(self):
        from angelo_agent import chat_resend
        card = self.card(chat())
        labels = {action: label for action, _value, label, _style in card["actions"]}
        self.assertEqual(labels[self.notices.RESEND], "Resend")
        self.assertIn("same message again", chat_resend.TIP)
        self.assertIn("cost", chat_resend.TIP)
        state = {"hits": []}
        hit = ((0, 0, 60, 24), self.notices.RESEND, self.key(chat()))
        with mock.patch.object(self.notices.ui_kit_status, "notice", lambda *a, **k: (40, [hit])), \
                mock.patch.object(self.notices, "_view", lambda: None):
            self.notices.draw(state, None, None, {"_notice_card": card}, hit[2], 100, 0, 400, 13, 18)
        from angelo_agent import canvas_controls
        self.assertEqual(canvas_controls.label_of(state, (hit[1], hit[2])), chat_resend.TIP)


class OneClick(Stubbed):
    def test_one_click_sends_the_stored_words_and_attachments_once(self):
        controller = chat()
        stored = [dict(r) for r in controller.state["messages"][0]["references"]]
        calls = self.click(controller)
        self.assertEqual([(text, references) for _chat, text, references in calls], [("Model a chair", stored)])
        self.assertIs(calls[0][0], controller)
        self.assertNotIn(self.notices.RESEND, self.actions(self.card(controller)), "sent: no second Resend")

    def test_a_double_click_sends_once(self):
        controller = chat()
        calls = self.click(controller) + self.click(controller)
        self.assertEqual(len(calls), 1)

    def test_a_click_while_the_first_is_in_flight_does_nothing(self):
        controller = chat()
        inner = []
        calls = self.click(controller, send=lambda *_: inner.extend(self.click(controller)))
        self.assertEqual((len(calls), inner), (1, []))

    def test_a_pill_from_an_earlier_card_sends_nothing(self):
        controller = chat()
        stale = self.key(controller)
        controller.state["messages"].append({"role": "user", "text": "something else"})
        with mock.patch.object(self.console, "send_message") as send:
            self.notices.dispatch(None, controller, {}, self.notices.RESEND, stale)
        send.assert_not_called()

    def test_the_draft_is_untouched(self):
        controller = chat()
        attachments, tools = [dict(r) for r in controller.attachments], list(controller.chosen_tools)
        seen = []
        calls = self.click(controller, send=lambda chat, *_: seen.append(list(chat.chosen_tools)))
        self.assertIsNot(calls[0][2], controller.attachments, "never the message box's attachments")
        self.assertEqual(controller.attachments, attachments)
        self.assertEqual(controller.chosen_tools, tools, "the composer's chosen tools are for the next message")
        self.assertEqual(seen, [tools], "retry policy must not temporarily mutate the unrelated composer")


class Refused(Stubbed):
    def refuse(self, error):
        controller = chat()

        def send(*_):
            raise error
        self.click(controller, send=send)
        return controller, self.card(controller)

    def test_a_refusal_is_the_cards_reason_and_put_back_stays(self):
        sentence = "Too many messages are waiting. Send or remove some of them first."
        controller, card = self.refuse(OverflowError(Text(n_(sentence))))
        self.assertEqual(card["body"], sentence)
        self.assertIn(self.notices.PUT_BACK, self.actions(card))
        self.assertIn(self.notices.RESEND, self.actions(card), "a refusal can be tried again")
        self.assertEqual(len(self.click(controller)), 1, "a refused resend sent nothing, so the next click may")

    def test_any_other_error_is_shown_too(self):
        _controller, card = self.refuse(RuntimeError("The provider closed the connection"))
        self.assertEqual(card["body"], "The provider closed the connection")
        _controller, card = self.refuse(RuntimeError("KeyError: 'route' at line 3"))
        self.assertNotIn("KeyError", card["body"])
        self.assertIn("KeyError: 'route'", card["details"] or "", "technical text goes under Details")
        self.assertIn(self.notices.PUT_BACK, self.actions(card))

    def test_missing_attachments_are_refused_before_anything_is_sent(self):
        for gone, word in ((("a1",), "1 attachment"), (("a1", "b2"), "2 attachments")):
            controller = chat(gone=gone)
            self.assertEqual(self.click(controller), [], gone)
            card = self.card(controller)
            self.assertIn(word, card["body"])
            self.assertIn("Put the message back", card["body"])
            self.assertIn(self.notices.PUT_BACK, self.actions(card))
            self.assertNotIn(self.notices.RESEND, self.actions(card), "the same click would be refused again")


class OwnCardMarks(Stubbed):
    """Angelo's own cards with a way out (``chat_notices._OWN``) are drawn with the mark each declares: a folder this
    chat reads is information (``info``), never an error (Director GUI proof 2026-11-15: the folder card showed the
    red error mark and border)."""

    def test_every_own_card_shows_its_declared_mark(self):
        import string
        from angelo_agent.ui_text import notice
        for template, (mark, _actions, _stands) in self.notices._OWN.items():
            fields = {name: "x" for _, name, _, _ in string.Formatter().parse(template) if name}
            message = {"role": "system", "notice": True, **notice(template, **fields)}
            with self.subTest(template=template[:40]):
                self.assertEqual(self.notices._content(message)[1], mark)


class EndOfTurnRows(Stubbed):
    """What a turn's end adds after its error (a card for a share a timer was refused, the account of a step that never
    ran) never takes the failed turn's Resend or Put back (review of 04c159c S5); a row after its outcome still does."""

    def failed(self, tail):
        controller = chat()
        rows = controller.state["messages"]
        rows[-1] = {"role": "error", "text": "Rate limit exceeded"}   # The provider's limit, after a refused timer.
        rows.append(tail)
        controller.turn_outcome = ("failed", False, False, len(rows))   # Recorded after the end's rows.
        return controller

    def tails(self):
        from angelo_agent import share_refusal_notice, turn_end
        from angelo_agent.ui_text import model_notice
        return (share_refusal_notice.row("\\\\srv\\tex"),
                {"role": "system", "turn_end": True, **model_notice(turn_end.MODEL_ACCOUNT, turn_end.NOT_RUN)})

    def test_the_error_card_keeps_resend_and_put_back(self):
        from angelo_agent import drafts
        for tail in self.tails():
            with self.subTest(tail=tail["text"][:30]):
                controller = self.failed(tail)
                actions = self.actions(self.card(controller))
                self.assertIn(self.notices.RESEND, actions)
                self.assertIn(self.notices.PUT_BACK, actions)
                self.assertIsNotNone(drafts.failed_request(controller, controller.state["messages"][-2]))
                self.assertEqual(len(self.click(controller)), 1, "one click sends it again")

    def test_the_rails_failed_mark_stays(self):
        from angelo_agent import session_rail_draw
        for tail in self.tails():
            self.assertTrue(session_rail_draw._last_failed(self.failed(tail)))

    def test_a_row_after_the_outcome_still_hides_them(self):
        from angelo_agent import drafts
        controller = self.failed(self.tails()[0])
        controller.state["messages"].append({"role": "system", "notice": True, "text": "Later"})
        self.assertIsNone(drafts.failed_request(controller))

    def test_no_reader_of_the_latest_failure_reads_the_last_row(self):
        """Each asks ``turn_tail``: a ``[-1]`` row there would lose the buttons again."""
        for name in ("drafts", "chat_resend", "chat_blocks", "chat_notices", "session_rail_draw"):
            source = (ROOT / "scripts/addons_core/angelo_agent" / (name + ".py")).read_text(encoding="utf-8")
            found = re.search(r"(rows|messages\"\])\[-1\]", source)
            self.assertIsNone(found, "%s: %s" % (name, found and found.group(0)))


class OwnCardKinds(Stubbed):
    """An own card's kind is its template's, never its words' (review of 04c159c M2): a share or a folder a person or
    a script named "Billing", "offline" or "sign in again" is still that card, without a provider title or pills."""

    NAMES = ("\\\\fs01\\Billing", "\\\\nas\\offline", "sign in again", "rate limit exceeded", "out of credits")

    def test_every_own_card_keeps_its_kind_and_mark_whatever_its_fields_say(self):
        import string
        from angelo_agent.ui_text import notice
        for template, (mark, _actions, _stands) in self.notices._OWN.items():
            fields = [name for _, name, _, _ in string.Formatter().parse(template) if name]
            for role in ("system", "error"):
                for name in self.NAMES:
                    message = {"role": role, "notice": True, **notice(template, **{field: name for field in fields})}
                    with self.subTest(template=template[:40], role=role, name=name):
                        kind, shown, *_ = self.notices._content(message)
                        self.assertEqual((kind, shown), ("unknown", mark))

    def test_the_share_and_folder_cards_by_name(self):
        from angelo_agent import context_folder_host, share_refusal_notice
        from angelo_agent.ui_text import notice
        for message in (share_refusal_notice.row("\\\\fs01\\Billing"),
                        {"role": "system", "notice": True, **notice(context_folder_host.ATTACHED, name="billing")}):
            with self.subTest(template=message["i18n"][0][:40]):
                self.assertEqual(self.notices._content(message)[0], "unknown")

    def test_a_providers_error_row_is_still_read_by_its_text(self):
        self.assertEqual(notice_kinds.kind({"role": "error", "text": "Your credit balance is too low"}), "credits")
        self.assertEqual(notice_kinds.kind({"text": "Rate limit exceeded"}), "rate", "a bare text (studio_enhance)")
        self.assertEqual(notice_kinds.kind({"role": "system", "text": "Rate limit exceeded"}), "unknown")
        self.assertEqual(notice_kinds.kind({"role": "error", "text": "Rate limit exceeded"}, by_text=False), "unknown")


class OnlyAClick(unittest.TestCase):
    """Nothing resends by itself: ``send_message`` runs from the person's Enter (``console.execute``) and from
    ``chat_resend.resend``; that runs only from a notice card's pill (``chat_notices.dispatch``, which
    ``chat_view.dispatch_action`` calls for a click). Never from a draw, layout, timer or event pump."""

    # The picture viewer's Send (markup_send._deliver): the person's own click there, delivered once its marked
    # picture is encoded (a timer joins the encode thread); a closed viewer's token stops it, so one click sends once.
    CALLERS = {"send_message": {("console.py", "execute"), ("chat_resend.py", "resend"),
                                ("markup_send.py", "_deliver"),
                                # A plan card's Go (plan_mode.rst): the operator its ticketed press runs.
                                ("planning_controls.py", "_send")},
               "resend": {("chat_notices.py", "dispatch")}}

    def test_only_a_click_or_enter_sends_again(self):
        found = {name: set() for name in self.CALLERS}
        for path in sorted(ADDON.glob("*.py")):
            _Sites(path.name, found).visit(ast.parse(path.read_text(encoding="utf-8")))
        for name, allowed in self.CALLERS.items():
            self.assertEqual(found[name] - allowed, set(), "%s from somewhere other than a click or Enter" % name)
            self.assertEqual(allowed - found[name], set(), "%s: an expected caller moved; check this list" % name)


class _Sites(ast.NodeVisitor):
    """(file, innermost function) of every call of ``send_message`` or ``chat_resend.resend``, and of every place
    either is handed on uncalled (a timer, a handler list, a lambda's default): a hand-on counts as its own site."""

    def __init__(self, name, found):
        self.name, self.found, self.functions = name, found, ["<module>"]

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _target(self, func):
        name = getattr(func, "attr", getattr(func, "id", None))
        if name == "send_message":
            return name
        if name == "resend" and (self.name == "chat_resend.py" or getattr(getattr(func, "value", None), "id", None)
                                 == "chat_resend"):
            return name
        return None

    def visit_Call(self, node):
        target = self._target(node.func)
        if target:
            self.found[target].add((self.name, self.functions[-1]))
        for value in list(node.args) + [keyword.value for keyword in node.keywords]:
            handed = self._target(value) if isinstance(value, (ast.Name, ast.Attribute)) else None
            if handed:
                self.found[handed].add((self.name, self.functions[-1] + " (handed on)"))
        self.generic_visit(node)


if __name__ == "__main__":
    unittest.main()
