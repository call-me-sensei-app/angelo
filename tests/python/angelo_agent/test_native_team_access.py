# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which reason keeps a signed-in person's team from the chat, and what the app suggests (``team_access``).

Two cases it must get right: keys held in another organization (not "Ask your
admin for access"), and a ready team (no personal Connect options in the chat). The window suite ``test_blender_team_chat`` shows each case.
"""

import base64
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import team_access  # noqa: E402
from angelo_agent.display_text import shown  # noqa: E402
from angelo_agent.team_view import AccountView, parse_tokens, token_role  # noqa: E402

VIEW = AccountView(status="signed_in", org_id="org-1", org_name="Hyperbond Studio", user_id="user-1", seat=True,
                   role="member", providers=("anthropic", "openai"), version=1)


def jwt(claims):
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")  # noqa: E731
    return part({"alg": "EdDSA"}) + "." + part(claims) + "." + "s" * 43


class ReasonTests(unittest.TestCase):
    def test_no_seat_names_the_team_and_tells_a_member_whom_to_contact(self):
        state = team_access.access(replace(VIEW, seat=False), coding_ready=False, admin_contact="it@hyperbond.example")
        self.assertEqual((state["state"], state["title"], state["note"], state["page"]),
                         (team_access.NO_SEAT, "No seat in Hyperbond Studio", "Contact your admin: it@hyperbond.example", ""))
        self.assertEqual(team_access.access(replace(VIEW, seat=False), False)["note"], "Contact your admin")

    def test_an_admin_is_offered_the_console_page_that_fixes_it(self):
        admin = replace(VIEW, role="admin")
        no_seat = team_access.access(replace(admin, seat=False), False)
        self.assertEqual((no_seat["note"], no_seat["page"]), ("Assign yourself a seat in People", "people"))
        no_keys = team_access.access(replace(admin, providers=()), False)
        self.assertEqual((no_keys["state"], no_keys["title"], no_keys["note"], no_keys["page"]),
                         (team_access.NO_PROVIDERS, "Hyperbond Studio has no AI provider keys yet",
                          "Add keys in the console → Keys", "keys"))
        owner = team_access.access(replace(VIEW, role="owner", providers=("google", "tripo")), False)
        self.assertEqual((owner["state"], owner["page"]), (team_access.NO_CODING, "keys"))
        self.assertEqual(owner["title"], "No coding provider (Claude or OpenAI) is set up for this team")

    def test_a_member_never_gets_an_admin_page(self):
        for view in (replace(VIEW, seat=False), replace(VIEW, providers=()), replace(VIEW, providers=("google",))):
            self.assertEqual(team_access.access(view, False)["page"], "", view)

    def test_waiting_states_are_busy_and_errors_are_shown(self):
        checking = team_access.access(replace(VIEW, providers=None), False)
        self.assertEqual((checking["state"], checking["busy"]), (team_access.CHECKING, True))
        preparing = team_access.access(VIEW, coding_ready=False)
        self.assertEqual((preparing["state"], preparing["note"]), (team_access.CHECKING, "Preparing your team's connection…"))
        failed = team_access.access(replace(VIEW, providers=None, providers_error="Couldn't reach Angelo"), False)
        self.assertEqual((failed["state"], failed["note"]), (team_access.ERROR, "Couldn't reach Angelo"))
        self.assertIsNone(team_access.access(AccountView(), False), "Not signed in: the row's own states apply")

    def test_ready_says_which_keys_in_one_whole_sentence(self):
        both = team_access.access(VIEW, coding_ready=True)
        self.assertEqual((both["state"], both["note"]),
                         (team_access.READY, "Signed in to Hyperbond Studio. Your team's Claude and OpenAI keys are ready."))
        self.assertEqual(team_access.ready_line(replace(VIEW, providers=("anthropic",))),
                         "Signed in to Hyperbond Studio. Your team's Claude key is ready.")
        self.assertEqual(shown(both["note"]), both["note"], "Translated whole, with its nested template")
        self.assertEqual(team_access.coding_providers(("openai", "google", "anthropic")), ["Claude", "OpenAI"])


class RoleTests(unittest.TestCase):
    def test_the_access_token_carries_the_role(self):
        self.assertEqual(token_role(jwt({"role": "admin", "seat": True})), "admin")
        self.assertEqual(token_role(jwt({"role": "superuser"})), "", "Unknown roles are not trusted")
        for bad in ("not-a-jwt", "a.!!!.c", jwt(["list"]), ""):
            self.assertEqual(token_role(bad), "", bad)

    def test_parse_tokens_reads_the_role_for_display_only(self):
        tokens = parse_tokens({"access_token": jwt({"role": "owner"}), "refresh_token": "r" * 32, "expires_in": 600,
                               "org": {"id": "org-1", "name": "Hyperbond Studio"}, "user": {"id": "u"}, "seat": True,
                               "scope": "api"})
        self.assertEqual(tokens["role"], "owner")
        self.assertTrue(replace(VIEW, role="owner").is_admin)
        self.assertFalse(replace(VIEW, role="").is_admin, "Unknown role: treated as a member")


class ChatSourceTests(unittest.TestCase):
    def test_a_chat_with_no_connection_starts_on_the_team_claude_first(self):
        source = (ADDON / "team_chat.py").read_text()
        self.assertIn('CODING_ORDER = ("claude_team", "codex_team")', source)
        bind = source.split("\ndef bind_team_chats(", 1)[1]
        self.assertIn("ui.choose_model_connection(controller, ready[0][\"id\"])", bind)
        self.assertIn('record.get("method") != "TEAM" or ui.connection_ready(record)', bind,
                      "A chat on a personal connection is left alone; one on a gone team connection moves")

    def test_focus_chat_open_and_token_refresh_recheck_the_providers(self):
        chat = (ADDON / "team_chat.py").read_text()
        self.assertIn("refresh_providers(older_than=RECHECK_AFTER)", chat)
        self.assertIn("ui.team_account_ui.note_window_event(event.type)", (ADDON / "chat_view.py").read_text())
        self.assertIn('self._enqueue("providers")  # Each token refresh', (ADDON / "team_account.py").read_text())
        self.assertIn("team_chat.recheck()", (ADDON / "team_account_ui.py").read_text())

    def test_personal_options_on_a_team_chat_follow_the_policy(self):
        source = (ADDON / "chat_welcome.py").read_text()
        self.assertIn("if not connected or (team_line and personal):", source)


if __name__ == "__main__":
    unittest.main()
