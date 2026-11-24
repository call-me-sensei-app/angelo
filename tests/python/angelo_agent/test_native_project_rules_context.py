# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's standing rules reach the model as one per-turn block: whole to a new context, again only on change.

Never in the runtimes' base instructions (the cached prefix stays the same), and a removal is said once, since the
earlier rules are in the native history. The real Controller over the unbilled Claude fixture (``ControllerFixture``).
"""
import suite_isolation  # noqa: F401

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger, project_rules  # noqa: E402
from test_native_cache_diet import ControllerFixture  # noqa: E402

RULES = project_rules.HEADER + project_rules.GLOBAL_LABEL + "Use metres. Name parts by role."
CHANGED = project_rules.HEADER + project_rules.GLOBAL_LABEL + "Use centimetres."


def delta(first):
    return context_ledger.TurnBlocks(dict(first.after, native_id="native-1"))


class RulesBlockTests(unittest.TestCase):
    def test_sent_once_then_only_when_changed_and_retracted_once(self):
        first = context_ledger.TurnBlocks()
        self.assertEqual(first.standing("rules", RULES, project_rules.NO_RULES), RULES)
        self.assertEqual(delta(first).standing("rules", RULES, project_rules.NO_RULES), "")
        self.assertEqual(delta(first).standing("rules", CHANGED, project_rules.NO_RULES), CHANGED)
        self.assertEqual(delta(first).standing("rules", "", project_rules.NO_RULES), project_rules.NO_RULES)
        never = context_ledger.TurnBlocks()
        never.standing("rules", "", project_rules.NO_RULES)
        self.assertEqual(delta(never).standing("rules", "", project_rules.NO_RULES), "", "Nothing to retract")

    def test_the_rules_digest_survives_persistence(self):
        first = context_ledger.TurnBlocks()
        first.standing("rules", RULES, project_rules.NO_RULES)
        saved = context_ledger.clean(dict(first.after, native_id="native-1"))
        self.assertIn("rules", saved, "Else a restart forgets the rules were sent and a removal is never said")
        self.assertEqual(context_ledger.TurnBlocks(saved).standing("rules", "", project_rules.NO_RULES),
                         project_rules.NO_RULES)


class ControllerRulesTests(ControllerFixture):
    def test_rules_reach_the_model_once_and_follow_changes(self):
        self.c.rules = RULES
        first = self.turn("How many objects are there?")
        self.assertIn("Use metres. Name parts by role.", first)
        second = self.turn("And now?")
        self.assertNotIn("Name parts by role", second, "Unchanged rules stay in the native history")
        self.c.rules = CHANGED
        self.assertIn("Use centimetres.", self.turn("Again?"))
        self.c.rules = ""
        self.assertIn(project_rules.NO_RULES, self.turn("Last one?"))

    def test_rules_never_enter_the_base_instructions(self):
        self.c.rules = RULES
        self.turn("Hello")
        instructions = getattr(self.c, "base_instructions", None) or ""
        self.assertNotIn("Name parts by role", instructions + getattr(self.c, "instructions", ""))
        for command in self.launches:
            self.assertFalse(any("Name parts by role" in part for part in command), "Never on the command line")

    def test_a_rule_change_mid_turn_waits_for_the_next_message(self):
        self.c.rules = RULES
        self.turn("First")
        self.assertEqual(self.c._turn_rules, RULES)


if __name__ == "__main__":
    unittest.main()
