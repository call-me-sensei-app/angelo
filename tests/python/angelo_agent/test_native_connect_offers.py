# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Providers offers every subscription the chat's welcome card offers, with the same words.

The welcome card (``first_run.connect_options``) offers Connect Claude and
Connect ChatGPT for every subscription whose runtime is available, signed in or
not. AI Providers used to offer only sign-ins already found on this computer,
so a signed-out Claude Code next to a signed-in ChatGPT showed "Connect" for
ChatGPT alone. Discovery still creates nothing: a row is an offer, and only its
Connect button (``quick_connect``) adds a connection. Host-only, with a real
``ConnectionStore`` and a measuring stand-in view.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import card_kit as kit, first_run, native_suggestions as suggest  # noqa: E402
from angelo_agent import providers_page_layout as page, providers_page_model as model  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from test_native_notice_cards import FakeView  # noqa: E402

CLAUDE, CHATGPT = "claude_subscription", "codex_subscription"
# This Mac in the report: Claude Code installed but signed out; Codex's ChatGPT sign-in present.
MIXED = {CLAUDE: "Not connected", CHATGPT: "Connected to ChatGPT"}
MISSING = "Native runtime unavailable; check its executable path"


class Case(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ConnectionStore(self.directory.name)
        patcher = mock.patch.object(first_run, "built_in", lambda mode: False)  # No bundled runtime unless a test says so.
        patcher.start()
        self.addCleanup(patcher.stop)

    def offers(self, statuses=MIXED):
        return [(o["mode"], o["signed_in"]) for o in suggest.connect_offers(self.store, statuses)]

    def rows(self, statuses=MIXED):
        return model.connect_rows(SimpleNamespace(_authentication=SimpleNamespace(status=dict(statuses))), self.store)


class OfferTests(Case):
    def test_every_subscription_the_welcome_card_offers_is_offered_in_its_order(self):
        self.assertEqual(self.offers(), [(CLAUDE, False), (CHATGPT, True)])
        welcome = [r["id"] for r in first_run.connect_options(MIXED) if r["action"] == "welcome_connect"]
        self.assertEqual([mode for mode, _ in self.offers()], welcome)

    def test_offering_creates_nothing(self):
        self.offers()
        self.rows()
        self.assertEqual(self.store.records, {})
        self.assertFalse(self.store.path.exists(), "An offer never writes the account store")

    def test_a_missing_runtime_is_not_offered(self):
        self.assertEqual(self.offers({CLAUDE: MISSING, CHATGPT: MISSING}), [])
        with mock.patch.object(first_run, "built_in", lambda mode: mode == CHATGPT):
            self.assertEqual(self.offers({CLAUDE: MISSING, CHATGPT: MISSING}), [(CHATGPT, False)],
                             "ChatGPT runs on Angelo's own runtime: offered without a Codex CLI")

    def test_a_dismissed_subscription_is_not_offered(self):
        self.store.dismiss_native(CLAUDE)
        self.assertEqual(self.offers(), [(CHATGPT, True)])

    def test_an_existing_connection_takes_the_offer_away(self):
        self.store.add("ANTHROPIC", "SUBSCRIPTION", "Work Claude", reuse_native=False)
        self.assertEqual(self.offers(), [(CHATGPT, True)], "A signed-out mode already has its connection row")
        self.store.add("OPENAI", "SUBSCRIPTION", reuse_native=True)
        self.assertEqual(self.offers(), [])
        signed_in = {CLAUDE: "Connected to Claude", CHATGPT: "Connected to ChatGPT"}
        self.assertEqual(self.offers(signed_in), [(CLAUDE, True)],
                         "The computer's own sign-in is another account than an isolated profile (as before)")

    def test_signed_in_suggestions_are_unchanged(self):
        self.assertEqual([s["mode"] for s in suggest.suggestions(self.store, MIXED)], [CHATGPT])


class ReuseDefaultTests(Case):
    """The Add-connection form ticks "Use existing sign-in on this computer" only when that provider's sign-in
    was found here. It used to start ticked for every subscription, so Anthropic read as reusing a sign-in
    that does not exist while Connect would open the browser."""

    def test_ticked_only_for_a_found_sign_in(self):
        self.assertFalse(suggest.reuse_by_default("ANTHROPIC", MIXED))
        self.assertTrue(suggest.reuse_by_default("OPENAI", MIXED))
        self.assertFalse(suggest.reuse_by_default("OPENAI", {CHATGPT: "Checking native sign-in…"}))
        self.assertFalse(suggest.reuse_by_default("XAI", MIXED), "No subscription to reuse")
        self.assertTrue(suggest.form_properties(CHATGPT)["reuse_existing"], "A found sign-in's own form keeps it ticked")

    def test_the_sheet_takes_its_default_from_the_found_sign_in(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/providers_sheet.py").read_text()
        init = source.split("    def __init__(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("reuse_existing=None", init)
        self.assertIn("reuse_by_default(", source)


class RowTests(Case):
    def test_rows_use_the_welcome_cards_words(self):
        claude, chatgpt = self.rows()
        self.assertEqual(claude["buttons"][0], ("Connect Claude", "quick_connect", CLAUDE, "primary"))
        self.assertEqual(chatgpt["buttons"][0], ("Connect ChatGPT", "quick_connect", CHATGPT, "primary"))
        self.assertEqual(claude["lines"], [first_run.CONNECT[CLAUDE]["browser"]])
        self.assertEqual(chatgpt["lines"], [first_run.CONNECT[CHATGPT]["signed_in"]])
        self.assertEqual((claude["name"], chatgpt["name"]), ("Claude subscription", "ChatGPT subscription"))

    def test_rows_match_each_other_and_every_row_can_be_dismissed(self):
        claude, chatgpt = self.rows()
        self.assertEqual(set(claude), set(chatgpt), "Both subscriptions are offered the same way")
        self.assertNotIn("chip", claude, "The note already says whether the sign-in was found")
        for row, mode in ((claude, CLAUDE), (chatgpt, CHATGPT)):
            self.assertEqual(row["buttons"][1], ("Dismiss", "dismiss_suggestion", mode, "ghost"))

    def test_a_sign_in_open_in_the_browser_reads_as_in_progress_not_as_a_fix(self):
        # Connect Claude adds its connection, then waits for the provider's own browser sign-in.
        self.assertEqual(model.readiness(False, "Complete sign-in in your browser…", "CODING"), ("Checking…", "muted", True))
        self.assertEqual(model.readiness(False, "Not connected", "CODING")[0], "Not connected")

    def test_both_rows_fit_and_stay_clickable_at_every_width(self):
        section = {"title": "Get started", "description": "Connect an account to start chatting.", "rows": self.rows()}
        built = {"hero": {"title": "AI Providers", "purpose": "The accounts Angelo's agent works with."},
                 "attention": [], "team": None, "sections": [section], "trust": "Keys stay private."}
        for width in (1100, 520, 380):
            plan = page.layout(FakeView(), built, width, 2000, 1.0, kit.UiState())
            x, _, w, _ = plan.card
            connect = [(rect, value) for rect, action, value in plan.hits if action == "quick_connect"]
            self.assertEqual([value for _, value in connect], [CLAUDE, CHATGPT], width)
            for (rx, _, rw, _), _ in connect:
                self.assertGreaterEqual(rx, x - 1, width)
                self.assertLessEqual(rx + rw, x + w + 7, width)
            for op in plan.ops:
                if op[0] == "text":
                    self.assertLessEqual(op[2] + FakeView.measure(op[1], op[4], 1), x + w + 1, (width, op[1]))


if __name__ == "__main__":
    unittest.main()
