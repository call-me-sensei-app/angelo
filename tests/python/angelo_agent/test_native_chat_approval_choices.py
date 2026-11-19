# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's approval controls in 2.0.1: the Approvals popover opens above its own button and states the chat's real
script rule.

Pure layouts and texts with stand-ins; no Blender. Contract: doc/angelo/agent_chat.rst, "Approval modes".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import managed_policy
from angelo_agent.chat_popovers import POPOVER_ANCHORS, popup_layout
from angelo_agent.managed_policy import Policy, with_organization
from angelo_agent.team_view import org_policy

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


class PopoverAnchorTests(unittest.TestCase):
    def test_each_popover_opens_above_the_control_that_opened_it_inside_the_region(self):
        approval = (59, 14, 120, 28)   # The approval chip: the composer toolbar's left side.
        model = (330, 14, 150, 28)     # The model chip: its right side.
        for width in (531, 360, 280, 200):
            with self.subTest(width=width):
                x, y, w, h = popup_layout("approval", width, 850, approval)["rect"]
                self.assertGreaterEqual(x, 8)
                self.assertLessEqual(x + w, width - 8)
                self.assertGreaterEqual(y, approval[1] + approval[3], "above its button")
                if width >= approval[0] + 256 + 8:
                    self.assertEqual(x, approval[0], "from its button's left edge, never off to the right")
                mx, _, mw, _ = popup_layout("controls", width, 850, (min(model[0], width - 158), 14, 150, 28))["rect"]
                self.assertLessEqual(mx + mw, width - 8)
        x, _, w, _ = popup_layout("controls", 531, 850, model)["rect"]
        self.assertEqual(x + w, model[0] + model[2], "a right-side chip's popover still ends at its right edge")

    def test_the_composer_records_the_approval_chips_anchor_and_the_view_reads_it(self):
        self.assertEqual(POPOVER_ANCHORS["approval"], "approval_anchor")
        composer = (ADDON / "chat_composer.py").read_text(encoding="utf-8")
        view = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        self.assertIn('state["approval_anchor"] = (59, cy - 14, approval_hit, 28)', composer)
        self.assertIn('POPOVER_ANCHORS.get(kind, "picker_anchor")', view)
        self.assertNotIn("General Python always requires review.", view, "the footer states this chat's real rule")


class ScriptRuleTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)

    def chat(self, consent=None, client=""):
        return SimpleNamespace(external_client=client, standing_script_consent=lambda: consent)

    def test_the_footer_says_the_rule_for_every_mode_and_consent_in_every_chat(self):
        from angelo_agent.chat_cards import script_rule
        for client in ("", "codex"):   # An External chat's scripts follow the same rule (2.0.1).
            with self.subTest(client=client):
                self.assertIn("review", script_rule(self.chat(client=client), "ASK"))
                self.assertIn("Read only", script_rule(self.chat(client=client), "READ_ONLY"))
                self.assertIn("run it once", script_rule(self.chat(client=client), "AUTO_BOUNDED"))
                session = self.chat(("session", client), client)
                self.assertIn("this session", script_rule(session, "AUTO_BOUNDED"))
                self.assertIn("this session", script_rule(session, "ASK"), "the chat's trust covers Ask too")
                self.assertIn("Auto chats", script_rule(self.chat(("always", client), client), "AUTO_BOUNDED"))
        managed_policy.reset(with_organization(Policy(), org_policy({"allowAutoApproval": False})))
        self.assertIn("organization", script_rule(self.chat(), "AUTO_BOUNDED"))


if __name__ == "__main__":
    unittest.main()
