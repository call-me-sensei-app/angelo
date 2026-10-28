# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers canvas page: layout (centred column, every control hit-testable, nothing outside the
column, narrow widths) and the pure status-to-chip table. No Blender: a fake measuring view."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import card_kit as kit, providers_page_layout as page  # noqa: E402
from test_native_notice_cards import FakeView  # noqa: E402

SOURCES = ROOT / "scripts/addons_core/angelo_agent"


def model(first_run=False, locked=False):
    connection = {"kind": "connection", "tile": "O", "dot": "teal", "name": "ChatGPT", "tag": "Subscription",
                  "lines": ["OpenAI · Connected to ChatGPT"], "facts": [("model", "GPT-6.1 Sol by default", "muted"),
                                                                       ("list", "8 models in chat", "muted")],
                  "chip": ("Available in chat", "teal", False), "fix": None,
                  "icons": [("pencil", "edit", "c1"), ("more", "menu", "c1")]}
    expired = dict(connection, name="Claude", chip=("Sign-in expired", "amber", False), fix=("Sign in again", "reconnect", "c2"),
                   icons=[("pencil", "edit", "c2"), ("more", "menu", "c2")])
    switch = {"kind": "switch", "name": "Always allow scripts", "lines": ["Scripts run without asking."], "on": False,
              "action": "toggle", "value": "always_allow_scripts", "enabled": not locked}
    if locked:
        switch["lock"] = "Turned off by your organization."
    return {"hero": {"title": "AI Providers", "purpose": "The accounts Angelo's agent works with.", "add": "Add connection"},
            "attention": [{"title": "Some settings are managed by your organization.", "lines": ["Contact your admin."],
                           "locks": ["Always allow scripts", "Local CLI access"], "glyph": "lock"}] if locked else [],
            "team": {"slim": True, "title": "Angelo team", "lines": ["Sign in with your organization account."],
                     "button": ("Sign in", "team_sign_in", "")},
            "sections": [{"title": "Coding agents", "description": "Models that work in Angelo with you.",
                          "rows": [] if first_run else [connection, expired], "add": ("Add", "add", "CODING"),
                          "empty": "No coding agent yet."},
                         {"title": "Agent permissions", "rows": [switch]}],
            "trust": "Keys are never written to preferences."}


class Layout(unittest.TestCase):
    def plan(self, width=1100, scale=1.0, **kwargs):
        return page.layout(FakeView(), model(**kwargs), width, 2000, scale, kit.UiState())

    def test_the_column_is_centred_and_at_most_720_pixels(self):
        plan = self.plan()
        x, _, w, _ = plan.card
        self.assertEqual(w, page.MAX_WIDTH)
        self.assertAlmostEqual(x, (1100 - w) / 2)
        self.assertEqual(self.plan(scale=2.0, width=3000).card[2], page.MAX_WIDTH * 2)

    def test_every_control_is_reachable_and_inside_the_column(self):
        for width in (1100, 520, 380):
            plan = self.plan(width=width)
            x, y, w, h = plan.card
            actions = {action for _, action, _ in plan.hits}
            self.assertTrue({"add", "edit", "menu", "reconnect", "toggle", "team_sign_in"} <= actions, width)
            for (rx, ry, rw, rh), action, _ in plan.hits:
                self.assertGreaterEqual(rx, x - 1, (width, action))
                self.assertLessEqual(rx + rw, x + w + 7, (width, action))
            self.assertEqual(len(plan.focus_order), len(plan.hits))

    def test_text_stays_in_the_column_at_narrow_widths(self):
        for width in (1100, 520, 380):
            plan = self.plan(width=width)
            x, _, w, _ = plan.card
            for op in plan.ops:
                if op[0] == "text":
                    self.assertLessEqual(op[2] + FakeView.measure(op[1], op[4], 1), x + w + 1, (width, op[1]))

    def test_a_locked_switch_is_not_clickable_and_says_why(self):
        plan = self.plan(locked=True)
        self.assertNotIn("toggle", {action for _, action, _ in plan.hits})
        texts = [op[1] for op in plan.ops if op[0] == "text"]
        self.assertIn("Turned off by your organization.", texts)
        self.assertTrue(any(op[0] == "chip" and op[2] == "Local CLI access" for op in plan.ops))

    def test_the_first_run_page_says_what_to_do(self):
        texts = [op[1] for op in self.plan(first_run=True).ops if op[0] == "text"]
        self.assertIn("No coding agent yet.", texts)

    def test_hover_reaches_the_drawn_control(self):
        plan = self.plan()
        rect, action, value = next(h for h in plan.hits if h[1] == "edit")
        ui = kit.UiState(hover=(action, value))
        hovered = page.layout(FakeView(), model(), 1100, 2000, 1.0, ui)
        states = [op[3] for op in hovered.ops if op[0] == "iconbtn" and op[2] == "pencil"]
        self.assertIn((True, False, False, True), states)


class Readiness(unittest.TestCase):
    """The status-to-chip table is pure, and the module's text-free logic is checked from its source."""

    def test_the_table(self):
        source = (SOURCES / "providers_page_model.py").read_text()
        namespace = {}
        start = source.index("def readiness")
        end = source.index("\ndef ", start + 1)
        exec("def n_(text):\n    return text\nCODING, GENAI = 'CODING', 'GENAI'\n" + source[start:end], namespace)
        readiness = namespace["readiness"]
        self.assertEqual(readiness(True, "Connected to ChatGPT", "CODING"), ("Available in chat", "teal", False))
        self.assertEqual(readiness(True, "API key verified", "GENAI"), ("Key verified", "teal", False))
        self.assertEqual(readiness(False, "Checking the key…", "GENAI")[2], True)
        self.assertEqual(readiness(False, "Not connected", "CODING", was_ready=True)[0], "Sign-in expired")
        self.assertEqual(readiness(False, "Not connected", "CODING")[0], "Not connected")
        self.assertEqual(readiness(False, "API key rejected (401)", "GENAI")[1], "coral")
        self.assertEqual(readiness(False, "Key configured · not verified", "GENAI")[0], "Not checked yet")

    def test_the_new_modules_stay_small_and_ui_py_does_not_grow(self):
        for name in ("providers_page_layout.py", "providers_page_draw.py", "providers_page_model.py",
                     "providers_page_host.py", "providers_page_team.py"):
            self.assertLessEqual(len((SOURCES / name).read_text().splitlines()), 500, name)
        self.assertLessEqual(len((SOURCES / "ui.py").read_text().splitlines()), 2274)


if __name__ == "__main__":
    unittest.main()
