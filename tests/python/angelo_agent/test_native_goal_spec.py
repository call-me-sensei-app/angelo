# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_goal``'s contract (``goal_spec``; doc/angelo/goal_mode.rst, "Setting a goal"): its actions, the measure's
camera validated as ``angelo_view``'s free camera is, target rules (never handles), scene checks by kind, refusals that
name the field, and a declaration small enough for every runtime."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import goal_spec, tool_specs  # noqa: E402

REF = "0b5e8d1c-0000-4000-8000-000000000001"
CAMERA = {"location": [0, -8, 2], "look_at": [0, 0, 1]}
HANDLE = "scn:" + "a" * 32 + ":7"


def check(args):
    return goal_spec.validate_goal(goal_spec.TOOL_NAME, args)


class ValidationTests(unittest.TestCase):
    def test_set_with_a_reference_measure(self):
        checked = check({"action": "SET", "objective": "Match the hut",
                         "measure": {"reference": REF, "camera": dict(CAMERA, lens=35), "targets": "VISIBLE"}})
        self.assertEqual(checked["measure"], {"reference": REF, "camera": {
            "location": [0.0, -8.0, 2.0], "look_at": [0.0, 0.0, 1.0], "lens": 35.0}, "targets": "VISIBLE"})

    def test_the_camera_defaults_and_bounds_are_the_views(self):
        measure = check({"action": "SET", "objective": "x", "measure": {"reference": REF, "camera": CAMERA}})["measure"]
        self.assertEqual((measure["camera"]["lens"], measure["targets"]), (50.0, "VISIBLE"))
        for camera, field in (({"location": [0, 0, 0], "look_at": [0, 0, 0]}, "measure.camera.look_at"),
                              (dict(CAMERA, lens=35, ortho_scale=2), "measure.camera"),
                              ({"location": [0, 0], "look_at": [0, 0, 1]}, "measure.camera.location"),
                              (dict(CAMERA, compare="OUTLINE"), "measure.camera")):
            with self.assertRaises(ValueError) as caught:
                check({"action": "SET", "objective": "x", "measure": {"reference": REF, "camera": camera}})
            self.assertIn(field, str(caught.exception))
            self.assertNotIn("views[", str(caught.exception))   # The goal's own field names, never the view's.

    def test_targets_are_a_rule_never_handles(self):
        ok = check({"action": "SET", "objective": "x",
                    "measure": {"reference": REF, "camera": CAMERA, "targets": "COLLECTION:Hut parts"}})
        self.assertEqual(ok["measure"]["targets"], "COLLECTION:Hut parts")
        for targets in (HANDLE, ["VISIBLE"], "COLLECTION:", "COLLECTION:" + "x" * 64, "COLLECTION:a\nb", "ALL"):
            with self.assertRaises(ValueError, msg=targets) as caught:
                check({"action": "SET", "objective": "x", "measure": {"reference": REF, "camera": CAMERA,
                                                                        "targets": targets}})
            self.assertIn("measure.targets", str(caught.exception))

    def test_a_reference_measure_needs_its_image_and_camera(self):
        for measure, field in (({"camera": CAMERA}, "measure.reference"), ({"reference": REF}, "measure.camera"),
                               ({"reference": "not-a-uuid", "camera": CAMERA}, "measure.reference")):
            with self.assertRaises(ValueError) as caught:
                check({"action": "SET", "objective": "x", "measure": measure})
            self.assertIn(field, str(caught.exception))

    def test_scene_checks_are_closed_verify_checks(self):
        checked = check({"action": "SET", "objective": "Under 20k triangles",
                         "measure": {"checks": [{"kind": "object_exists", "target": HANDLE, "expected": True}]}})
        self.assertEqual(checked["measure"]["checks"][0]["kind"], "object_exists")
        with self.assertRaises(ValueError):
            check({"action": "SET", "objective": "x", "measure": {"checks": [{"kind": "vibes"}]}})
        with self.assertRaises(ValueError) as caught:
            check({"action": "SET", "objective": "x", "measure": {"checks": [{"kind": "object_exists", "expected": True,
                                                                              "target": HANDLE}],
                                                                  "reference": REF, "camera": CAMERA}})
        self.assertIn("one of", str(caught.exception))

    def test_measure_and_stop(self):
        self.assertEqual(check({"action": "MEASURE"}), {"action": "MEASURE"})
        self.assertEqual(check({"action": "STOP", "reason": "BLOCKED", "message": "No reference of the back"}),
                         {"action": "STOP", "reason": "BLOCKED", "message": "No reference of the back"})
        for args in ({"action": "STOP", "message": "x"}, {"action": "STOP", "reason": "GIVE_UP", "message": "x"},
                     {"action": "STOP", "reason": "DONE"}, {"action": "MEASURE", "objective": "x"},
                     {"action": "SET", "objective": "x", "reason": "DONE"}, {"action": "LOOP"}, {},
                     {"action": "SET", "objective": ""}, {"action": "SET", "objective": "x" * 501},
                     {"action": "STOP", "reason": "DONE", "message": "x" * 501},
                     {"action": "SET", "objective": "a‮b"}):
            with self.assertRaises(ValueError, msg=args):
                check(args)

    def test_set_with_only_a_measure_is_valid(self):
        # Setting the measurement of a goal the person set in the chat bar: the host refuses it when no goal is held.
        self.assertEqual(check({"action": "SET", "measure": {"reference": REF, "camera": CAMERA}})["action"], "SET")


class DeclarationTests(unittest.TestCase):
    def test_it_is_deferred_on_both_runtimes_and_named_in_their_instructions(self):
        from angelo_agent import claude_text, codex_text, plan_kinds
        self.assertIn(goal_spec.TOOL_NAME, tool_specs.TOOL_MAP)
        self.assertIn(goal_spec.TOOL_NAME, plan_kinds.DEFERRED_TOOLS)   # Looked up when a goal is asked for.
        more = next(t for t in tool_specs.codex_tools() if t.get("type") == "namespace")
        self.assertIn(goal_spec.TOOL_NAME, {tool["name"] for tool in more["tools"]})
        self.assertIn("angelo_goal", codex_text.codex_instructions(short_identifiers=True, deferred=True, plan=True,
                                                                    team=True).split("are deferred")[0])
        self.assertNotIn(goal_spec.TOOL_NAME, {t["name"] for t in tool_specs.dynamic_tools()})   # Never by voice.
        for text in (codex_text.codex_instructions(short_identifiers=True, deferred=True),
                     claude_text.claude_base(None) + claude_text.claude_tail(None)):
            self.assertIn("angelo_goal holds a goal across turns", text)
        self.assertNotIn("angelo_goal", codex_text.voice_instructions())
        self.assertNotIn("angelo_goal", claude_text.claude_base(set()) + claude_text.claude_tail(set()))

    def test_never_a_collaborators_tool(self):
        self.assertNotIn(goal_spec.TOOL_NAME, tool_specs.COLLABORATOR_TOOLS)

    def test_the_declaration_stays_small(self):
        more = next(t for t in tool_specs.codex_tools() if t.get("type") == "namespace")
        spec = next(t for t in more["tools"] if t["name"] == goal_spec.TOOL_NAME)
        size = len(json.dumps(spec, separators=(",", ":")))
        self.assertLess(size, 2000, size)   # Codex code mode turns unions to unknown past about 2,000 (gpt_launch).

    def test_the_description_states_facts_and_consent_never_a_workflow(self):
        text = goal_spec.GOAL_TOOLS[0]["description"]
        self.assertIn("only when the person", text)
        for prescriptive in ("you must", "always ", "first call", "before you", "then call", "should"):
            self.assertNotIn(prescriptive, text.lower())


if __name__ == "__main__":
    unittest.main()
