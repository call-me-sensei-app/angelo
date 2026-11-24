# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_plan``'s contract (``planning_spec``; doc/angelo/plan_mode.rst, "The tool"): START and PROPOSE only, a
plan's shape and limits, refusals that name the field, read only, deferred, and a declaration small enough for every
runtime."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import planning_spec, tool_specs  # noqa: E402


def approach(title="Boolean cut", steps=("Block out the body", "Cut the windows"), **extra):
    return dict({"title": title, "steps": list(steps), "estimate": "about 10 minutes"}, **extra)


def plan(**extra):
    return dict({"action": "PROPOSE", "summary": "Two ways to build the tower.",
                 "approaches": [approach(), approach("Modular kit", risks=["More objects to manage"])],
                 "recommended": 1}, **extra)


def check(args):
    return planning_spec.validate_plan(planning_spec.TOOL_NAME, args)


class ValidationTests(unittest.TestCase):
    def test_start_takes_nothing_else(self):
        self.assertEqual(check({"action": "START"}), {"action": "START"})
        with self.assertRaises(ValueError) as caught:
            check({"action": "START", "summary": "x"})
        self.assertIn("summary", str(caught.exception))

    def test_a_plan_comes_back_canonical(self):
        checked = check(plan(questions=["Is it for a game engine?"]))
        self.assertEqual(checked["action"], "PROPOSE")
        self.assertEqual(checked["recommended"], 1)
        self.assertEqual(checked["questions"], ["Is it for a game engine?"])
        self.assertEqual(checked["approaches"][0], {"title": "Boolean cut", "steps": ["Block out the body",
                         "Cut the windows"], "risks": [], "estimate": "about 10 minutes"})
        self.assertEqual(checked["approaches"][1]["risks"], ["More objects to manage"])
        self.assertEqual(check(plan())["questions"], [])

    def test_actions_are_closed(self):
        for args in ({}, {"action": "GO"}, {"action": "STOP"}, {"action": "start"}, ["START"], None):
            with self.assertRaises(ValueError, msg=repr(args)) as caught:
                check(args)
            self.assertIn("START or PROPOSE", str(caught.exception))
        with self.assertRaises(ValueError):
            planning_spec.validate_plan("angelo_goal", {"action": "START"})

    def test_counts_are_bounded(self):
        cases = ((plan(approaches=[]), "approaches"),
                 (plan(approaches=[approach()] * 4, recommended=0), "approaches"),
                 (plan(approaches=[approach(steps=[])], recommended=0), "steps"),
                 (plan(approaches=[approach(steps=["s"] * 13)], recommended=0), "steps"),
                 (plan(approaches=[approach(risks=["r"] * 7)], recommended=0), "risks"),
                 (plan(questions=["q"] * 6), "questions"))
        for args, field in cases:
            with self.assertRaises(ValueError, msg=field) as caught:
                check(args)
            self.assertIn(field, str(caught.exception))
        self.assertEqual(len(check(plan(approaches=[approach()] * 3, recommended=2))["approaches"]), 3)
        self.assertEqual(len(check(plan(approaches=[approach()], recommended=0))["approaches"]), 1)

    def test_lengths_are_bounded_and_text_is_required(self):
        limits = planning_spec.LIMITS
        cases = ((plan(summary="x" * (limits["summary"] + 1)), "summary"),
                 (plan(summary="  "), "summary"),
                 (plan(approaches=[approach(title="t" * (limits["title"] + 1))], recommended=0), "title"),
                 (plan(approaches=[approach(steps=["s" * (limits["step"] + 1)])], recommended=0), "steps[0]"),
                 (plan(approaches=[dict(approach(), estimate="e" * (limits["estimate"] + 1))], recommended=0),
                  "estimate"),
                 (plan(questions=["q" * (limits["question"] + 1)]), "questions[0]"))
        for args, field in cases:
            with self.assertRaises(ValueError, msg=field) as caught:
                check(args)
            self.assertIn(field, str(caught.exception))

    def test_control_and_direction_characters_are_refused(self):
        for bad in ("a\u202eb", "a\x00b", "line\x1bthing", "a\u200bb", "a\u2060b", "a\ufeffb", "a\U000e0041b",
                    "a\u200db"):   # Invisible format characters too (break-it review S2).
            with self.assertRaises(ValueError, msg=repr(bad)):
                check(plan(summary=bad))
            with self.assertRaises(ValueError, msg=repr(bad)):
                check(plan(approaches=[approach(steps=[bad])], recommended=0))
        self.assertEqual(check(plan(summary="Two lines\nare fine"))["summary"], "Two lines\nare fine")
        thai = "วางแผน\u200bก่อน\u200bเริ่ม"   # Thai breaks its words with U+200B: a plan in Thai is a plan (N3).
        self.assertEqual(check(plan(approaches=[approach(title=thai, steps=[thai])], recommended=0))["approaches"][0]
                         ["title"], thai)

    def test_recommended_is_an_index(self):
        for value in (2, -1, "0", 0.0, True, None):
            with self.assertRaises(ValueError, msg=repr(value)) as caught:
                check(plan(recommended=value))
            self.assertIn("recommended", str(caught.exception))

    def test_unknown_fields_are_refused_by_name(self):
        for args, field in ((plan(go=True), "go"), (plan(approaches=[approach(approval="AUTO")], recommended=0),
                                                     "approval")):
            with self.assertRaises(ValueError) as caught:
                check(args)
            self.assertIn(field, str(caught.exception))


class DeclarationTests(unittest.TestCase):
    def test_it_is_read_only_registered_and_validated_through_tool_specs(self):
        self.assertIn(planning_spec.TOOL_NAME, tool_specs.TOOL_MAP)
        self.assertIn(planning_spec.TOOL_NAME, tool_specs.READ_ONLY_TOOLS)   # Read only admits it: it changes nothing.
        self.assertEqual(tool_specs.validate_call(planning_spec.TOOL_NAME, {"action": "START"}), {"action": "START"})

    def test_it_is_deferred_and_never_for_voice_apps_or_collaborators(self):
        from angelo_agent import external_clients, plan_kinds
        self.assertIn(planning_spec.TOOL_NAME, plan_kinds.DEFERRED_TOOLS)
        self.assertIn(planning_spec.TOOL_NAME, external_clients.NEVER_FOR_APPS)
        self.assertNotIn(planning_spec.TOOL_NAME, tool_specs.COLLABORATOR_TOOLS)
        self.assertNotIn(planning_spec.TOOL_NAME, {tool["name"] for tool in tool_specs.declared_dynamic()})

    def test_the_declaration_fits_codex_code_modes_budget(self):
        declared = json.dumps(planning_spec.PLAN_TOOLS[0], separators=(",", ":"))
        self.assertLess(len(declared), 1900, len(declared))   # gpt_launch.rst: about 2,000 characters per tool.

    def test_the_description_states_what_it_does_never_a_go(self):
        description = planning_spec.PLAN_TOOLS[0]["description"]
        for fact in ("read only", "PROPOSE", "START", "Go"):
            self.assertIn(fact, description)
        self.assertNotIn("GO", planning_spec.ACTIONS)


if __name__ == "__main__":
    unittest.main()
