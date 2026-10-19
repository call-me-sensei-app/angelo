# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A typed-plan thread's program reference leaves out only what its response format states exactly.

Every Codex plan thread sends its instructions (ending with the program
reference) and its strict response format with every request, and a new chat's
first request is not cached. The reference therefore names the action kinds and
checks the response format already states, instead of stating them twice; no
kind or check loses its exact schema and the host validates as before.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_plan
from angelo_agent.codex_reference import program_reference, response_format_kinds
from angelo_agent.plan_schema import (CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS, MODIFIER_KINDS,
                                      PLAN_CORE_MODIFIERS, _checks_items, core_operation)
from angelo_agent.providers import codex_instructions

KIND = re.compile(r'\{kind:"([a-z_]+)"')


def _program_arguments():
    """The angelo_scene_program arguments of the response format (references inlined)."""
    document = codex_plan.ordinary_schema()
    variants = document["properties"]["operations"]["items"]["oneOf"]
    (variant,) = [item for item in variants if item["properties"]["tool"].get("const") == "angelo_scene_program"]
    return variant["properties"]["arguments"]["properties"]


def _exact(items, field="kind"):
    return {variant["properties"][field]["const"] for variant in items["oneOf"]
            if "const" in variant["properties"].get(field, {})}


class PlanReferenceTests(unittest.TestCase):
    def setUp(self):
        self.full, self.plan = program_reference(), program_reference(plan=True)

    def test_every_omitted_kind_is_exact_in_the_response_format_as_in_the_reference(self):
        stated = response_format_kinds()
        self.assertTrue(stated)
        exact = _exact(_program_arguments()["operations"]["items"])
        for kind in stated:
            with self.subTest(kind=kind):
                self.assertIn(kind, exact)
                # The same exact schema the reference states for it.
                self.assertEqual(core_operation(kind, PLAN_CORE_MODIFIERS), core_operation(kind, CODEX_REFERENCE_MODIFIERS))
                self.assertIn('{kind:"%s"' % kind, self.full)
                self.assertNotIn('{kind:"%s"' % kind, self.plan)
                self.assertIn(kind, self.plan.split("\n")[0], "the reference names what it leaves to the format")

    def test_every_other_reference_kind_stays_exact_in_the_plan_reference(self):
        kept = set(CODEX_REFERENCE_ACTIONS) - set(response_format_kinds())
        self.assertEqual(set(KIND.findall(self.plan.split("type Op=")[1].split("\n")[0])), kept)
        if set(CODEX_REFERENCE_MODIFIERS) != set(PLAN_CORE_MODIFIERS):
            self.assertTrue(set(MODIFIER_KINDS) <= kept, "the reference's wider modifier types need no lookup")
            self.assertEqual(self.plan.count('type:"ARRAY"'), 2)

    def test_checks_are_left_to_the_response_formats_exact_expected_checks(self):
        checks = _exact(_checks_items())
        self.assertEqual(_exact(_program_arguments()["expected_checks"]["items"]), checks)
        self.assertIn("type Check=", self.full)
        self.assertNotIn("type Check=", self.plan)
        self.assertIn("expected_checks", self.plan.split("\n")[0])
        self.assertIn("Other check kinds", self.plan, "deferred checks stay named")

    def test_declared_aliases_are_all_used_and_lines_after_the_union_are_kept(self):
        lines = self.plan.split("\n")
        declared = [line[len("type "):line.index("=")] for line in lines if line.startswith("type ") and "Op=" not in line]
        self.assertTrue(declared)
        for name in declared:
            with self.subTest(alias=name):
                elsewhere = "\n".join(line for line in lines if not line.startswith("type %s=" % name))
                self.assertRegex(elsewhere, r"\b%s\b" % name)
        for line in self.full.split("\n")[self.full.split("\n").index(next(
                line for line in self.full.split("\n") if line.startswith("type Check="))) + 1:]:
            self.assertIn(line, self.plan)

    def test_only_plan_threads_change_and_they_get_smaller(self):
        for team in (False, True):
            with self.subTest(team=team):
                plan = codex_instructions(short_identifiers=True, deferred=True, plan=True, team=team)
                other = codex_instructions(short_identifiers=True, deferred=True, plan=False, team=team)
                self.assertTrue(plan.endswith(self.plan))
                self.assertTrue(other.endswith(self.full))
                self.assertLess(len(self.plan.encode()), len(self.full.encode()) - 3000)


if __name__ == "__main__":
    unittest.main()
