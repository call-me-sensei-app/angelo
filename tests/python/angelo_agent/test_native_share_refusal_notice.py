# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The network share a script was refused is named to the person and never to a model (share_refusal_notice).

Dependency-free: the chat's own controller with a stand-in for ``_angelo_guard.network_shares_refused()`` (the C
module's record of refused shares, ``(serial, share)`` pairs). The agent reads only the plain network refusal; the
person's chat gets a card naming the share after the call's end or the turn's end, once per refusal, and no handoff,
external app or saved-and-reopened chat gives that card to a model.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import external_transcript, grant_authority, script_confinement  # noqa: E402
from angelo_agent import share_refusal_notice as notice  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.document_offer import person_only  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402
from angelo_agent.ui_text import display_notice  # noqa: E402

SHARE = "\\\\angelo-test.invalid\\textures"
OTHER = "\\\\angelo-other.invalid\\renders"


class Guard:
    """``_angelo_guard`` as the app has it, for the calls this module makes."""

    def __init__(self):
        self.serial, self.pairs, self.running = 0, [], False

    def refuse(self, share):   # As the C module records it: moved to the end, the last 16 kept.
        self.serial += 1
        self.pairs = [pair for pair in self.pairs if pair[1] != share][-15:] + [(self.serial, share)]

    def network_shares_refused(self):
        if self.running:
            raise PermissionError("An agent script can't change how Angelo checks scripts.")
        return list(self.pairs)

    def confined(self):
        return self.running


class ChatFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.guard = Guard()
        patcher = mock.patch.object(notice, "_guard", lambda: self.guard)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("_named", [0]), ("_owed", {}), ("_unowned", [])):
            patched = mock.patch.object(notice, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.chat = self.open("one")

    def open(self, name):
        controller = Controller(str(Path(self.directory.name) / name))
        controller.set_document(("share-notice-" + name, 1))
        controller.busy = True
        self.addCleanup(controller.close)
        return controller

    def cards(self, chat=None):
        chat = chat or self.chat
        chat.pump_events()
        return [message for message in chat.state["messages"]
                if (message.get("i18n") or [None])[0] in notice.TEMPLATES]

    def script_ends(self, chat=None, call="script/1", shares=()):
        chat = chat or self.chat
        chat.emit("activity", {"id": call, "tool": "angelo_execute_python", "arguments": {"code": "open(r'x')"},
                               "status": "running"})
        self.script_runs(chat, call, shares)
        chat.emit("activity", {"id": call, "status": "failed",
                               "result": {"error": script_confinement.NETWORK}})

    def script_runs(self, chat, call, shares):
        """The script's run, as the executor makes it (``script_confinement.run_confined`` with its call key)."""
        runner = SimpleNamespace(run_confined=lambda *arguments: [self.guard.refuse(share) for share in shares])
        with mock.patch.object(script_confinement, "_guard", runner), \
                mock.patch.object(script_confinement, "_ensure_installed", lambda: None):
            script_confinement.run_confined("pass", {}, None, grant_authority.call_key(chat.state["id"], call))

    def turn_ends(self, chat=None):
        chat = chat or self.chat
        chat.emit("finished", {"status": "completed"})
        chat.pump_events()   # Each frame reads the chat's events; the next turn's come after.


class NamedToThePersonTests(ChatFixture, unittest.TestCase):
    def test_a_scripts_refused_share_is_named_in_its_chat_when_its_call_ends(self):
        self.script_ends(shares=[SHARE])
        (card,) = self.cards()
        self.assertIn(SHARE, display_notice(card))
        self.assertIn("map it to a drive letter", display_notice(card))
        self.assertTrue(person_only(card))

    def test_a_refusal_after_the_call_is_named_at_the_turns_end(self):
        self.script_ends()
        self.assertEqual(self.cards(), [])
        self.guard.refuse(SHARE)   # Work the script left (a timer) is refused later in the turn.
        self.turn_ends()
        self.assertEqual(len(self.cards()), 1)

    def test_each_refusal_is_named_once(self):
        self.script_ends(shares=[SHARE])
        self.turn_ends()
        self.script_ends(call="script/2")
        self.assertEqual(len(self.cards()), 1)

    def test_a_share_refused_again_in_a_later_turn_is_named_again(self):
        self.script_ends(shares=[SHARE])
        self.turn_ends()
        self.chat.busy = True
        self.script_ends(call="script/2", shares=[SHARE])   # Already the last one the C module lists.
        self.assertEqual(len(self.cards()), 2)

    def test_several_shares_are_a_card_each_for_the_latest_three(self):
        shares = ["\\\\angelo-%d.invalid\\s" % index for index in range(5)]
        self.script_ends(shares=shares + [shares[0]])
        named = [card["i18n"][1]["share"] for card in self.cards()]
        self.assertEqual(named, [shares[3], shares[4], shares[0]], "the latest last, each once")

    def test_the_name_is_quoted_in_its_sentence(self):
        self.script_ends(shares=[SHARE])
        (card,) = self.cards()
        self.assertIn("\u201c" + SHARE + "\u201d", display_notice(card))

    def test_another_chat_never_names_this_chats_refusal(self):
        other = self.open("two")
        self.script_ends(shares=[SHARE])
        self.script_ends(other, shares=[OTHER])
        self.turn_ends(other)
        (mine,), (theirs,) = self.cards(), self.cards(other)
        self.assertIn(SHARE, display_notice(mine))
        self.assertNotIn(OTHER, display_notice(mine))
        self.assertIn(OTHER, display_notice(theirs))
        self.assertNotIn(SHARE, display_notice(theirs))

    def test_two_chats_scripts_run_before_either_reads_its_events(self):
        other = self.open("two")
        self.script_runs(self.chat, "script/1", [SHARE])
        self.script_runs(other, "script/9", [OTHER])
        for chat, call in ((self.chat, "script/1"), (other, "script/9")):
            chat.emit("activity", {"id": call, "tool": "angelo_execute_python", "status": "failed"})
        (theirs,), (mine,) = self.cards(other), self.cards()
        self.assertEqual((theirs["i18n"][1]["share"], mine["i18n"][1]["share"]), (OTHER, SHARE))

    def test_a_scripts_chosen_name_is_shown_bounded_and_without_control_characters(self):
        self.script_ends(shares=["\\\\evil\\a\u202e\nb\u2028c\u2029d" + "x" * 200])
        (card,) = self.cards()
        share = card["i18n"][1]["share"]
        self.assertLessEqual(len(share), notice.NAME)
        for separator in ("\n", "\u202e", "\u2028", "\u2029"):   # Line, format, line and paragraph separators.
            self.assertNotIn(separator, share)
        self.assertTrue(share.startswith("\\\\evil\\a\ufffd\ufffdb\ufffdc\ufffdd"))


class CardTests(unittest.TestCase):
    def test_its_sentence_is_drawn_as_a_notice_card(self):
        """``chat_notices._OWN`` lists them (a system row drawn as a card, ``is_own_card``); chat_notices needs Blender,
        so its table is read from the source."""
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/chat_notices.py").read_text(encoding="utf-8"))
        table = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                     and any(getattr(target, "id", None) == "_OWN" for target in node.targets))
        keys = {ast.unparse(key): ast.unparse(value) for key, value in zip(table.keys, table.values)}
        self.assertEqual(notice.TEMPLATES, (notice.ONE,))
        self.assertTrue(keys["share_refusal_notice.ONE"].startswith("('warn'"))


class NeverForAModelTests(ChatFixture, unittest.TestCase):
    def test_no_handoff_names_the_share(self):
        self.script_ends(shares=[SHARE])
        self.turn_ends()
        self.cards()
        self.assertNotIn("angelo-test", handoff(self.chat.state["messages"])[0])

    def test_an_app_reading_the_conversation_never_gets_the_card(self):
        self.script_ends(shares=[SHARE])
        (card,) = self.cards()
        self.assertIsNone(external_transcript.app_row(0, card))

    def test_a_reopened_chat_keeps_it_the_persons_only(self):
        self.script_ends(shares=[SHARE])
        self.turn_ends()
        self.cards()
        self.chat.save()
        self.assertIn(json.dumps(SHARE)[1:-1], self.chat.store.path.read_text(encoding="utf-8"),
                      "the person's chat keeps the card")
        reopened = Controller(str(Path(self.directory.name) / "one"))
        self.addCleanup(reopened.close)
        reopened.set_document(("share-notice-one", 1))
        cards = self.cards(reopened)
        self.assertTrue(cards and all(person_only(card) for card in cards))
        self.assertNotIn("angelo-test", handoff(reopened.state["messages"])[0])


class NothingToReadTests(ChatFixture, unittest.TestCase):
    def test_a_build_without_the_call_names_nothing(self):
        with mock.patch.object(notice, "_guard", lambda: SimpleNamespace(confined=lambda: False)):
            self.script_ends()
            self.turn_ends()
        self.assertEqual(self.cards(), [])

    def test_no_c_module_names_nothing(self):
        with mock.patch.object(notice, "_guard", lambda: None):
            self.script_ends()
            self.turn_ends()
        self.assertEqual(self.cards(), [])

    def test_never_asked_while_a_script_runs(self):
        self.guard.refuse(SHARE)
        self.guard.running = True
        self.assertEqual(notice.refusals(), [])
        self.guard.running = False
        self.assertEqual(notice.unnamed(self.chat.state["id"]), [SHARE], "named once the script has ended")

    def test_a_confined_thread_never_asks(self):
        """While ``confined()``, the call is never made (it would be refused, and a refusal is a finding)."""
        self.guard.refuse(SHARE)
        asked = []
        with mock.patch.object(self.guard, "confined", lambda: True), \
                mock.patch.object(self.guard, "network_shares_refused", lambda: asked.append(1) or []):
            self.assertEqual(notice.refusals(), [])
        self.assertEqual(asked, [])

    def test_a_later_modules_third_element_is_read_as_a_pair(self):
        with mock.patch.object(self.guard, "network_shares_refused", lambda: [(4, SHARE, "chat/call"), (5, OTHER)]):
            self.assertEqual(notice.refusals(), [(4, SHARE), (5, OTHER)])

    def test_a_reply_that_isnt_a_list_names_nothing(self):
        for reply in (None, 3, object()):
            with mock.patch.object(self.guard, "network_shares_refused", lambda reply=reply: reply):
                self.assertEqual(notice.refusals(), [])

    def test_a_workers_mirror_names_nothing_and_leaves_it_for_a_chat_here(self):
        mirror = self.open("mirror")
        mirror.hosted_in_worker = True
        self.guard.refuse(SHARE)   # Outside any run: owed to no chat.
        self.assertFalse(notice.note(mirror))
        self.assertEqual(self.cards(mirror), [])
        self.turn_ends()
        self.assertEqual([card["i18n"][1]["share"] for card in self.cards()], [SHARE])

    def test_a_reply_that_isnt_the_promised_pairs_names_nothing(self):
        with mock.patch.object(self.guard, "network_shares_refused", lambda: [SHARE, (1, 2), ("1", SHARE)]):
            self.assertEqual(notice.refusals(), [])


if __name__ == "__main__":
    unittest.main()
