# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Return in a text field (``text_keys.enter``) and in Media Studio's fields (``studio_enter``, ``studio_input``).

The class: the chat sends on Return and starts a line on Shift+Return, while Media Studio's prompt fields inserted a
line on Return, ignored Shift and never ran Generate, so the same key meant two things. Now one rule: Return runs the
field's primary action (or does nothing and says why), Shift+Return inserts a line, a field with no primary action
lets go of the keyboard. Return runs the action through the button's own hit (the same ``click``, so Generate's
confirmation with its billing and price comes first) and on the key's release, so a held Return cannot confirm the
dialog it opened (Blender's dialog confirms on any Return press, repeats included).
"""

import suite_isolation  # noqa: F401  Standalone provider isolation.

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import studio_enter, text_keys  # noqa: E402
from angelo_agent.text_keys import BLOCKED, BLUR, LINE, PRIMARY  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_the_rule(self):
        table = [  # multiline, shift, primary (None: has none; True: available; False: unavailable) -> what Return does
            (True, False, True, PRIMARY), (True, False, False, BLOCKED), (True, False, None, BLUR),
            (True, True, True, LINE), (True, True, False, LINE), (True, True, None, LINE),
            (False, False, True, PRIMARY), (False, False, False, BLOCKED), (False, False, None, BLUR),
            (False, True, True, PRIMARY), (False, True, False, BLOCKED), (False, True, None, BLUR),
        ]
        for multiline, shift, primary, expected in table:
            self.assertEqual(text_keys.enter(multiline, shift, primary), expected, (multiline, shift, primary))

    def test_return_never_inserts_a_line(self):
        for multiline in (True, False):
            for primary in (None, True, False):
                self.assertNotEqual(text_keys.enter(multiline, False, primary), LINE)


GENERATE_HIT = ((10, 10, 200, 40), "generate", None)


def studio_state(available=True):
    state = {"field_focus": "prompt", "hits": [((0, 0, 5, 5), "focus", "prompt")], "notice": ""}
    if available:
        state["hits"].append(GENERATE_HIT)
    studio_enter.primary(state, "prompt", "generate", None, None if available else "Describe what you want to generate")
    return state


class StudioTests(unittest.TestCase):
    def test_return_runs_generate_on_its_release(self):
        state = studio_state()
        self.assertEqual(studio_enter.on_return(state, "prompt", True, False, False), PRIMARY)
        self.assertEqual(state["field_focus"], "prompt")
        self.assertEqual(studio_enter.on_release(state, "prompt"), GENERATE_HIT, "The button's own hit: its click")
        self.assertIsNone(studio_enter.on_release(state, "prompt"), "Once per press")

    def test_a_held_return_runs_it_once(self):
        state = studio_state()
        studio_enter.on_return(state, "prompt", True, False, False)
        studio_enter.on_return(state, "prompt", True, False, True)   # Key repeats while held.
        self.assertEqual(studio_enter.on_release(state, "prompt"), GENERATE_HIT)
        state = studio_state()
        studio_enter.on_return(state, "prompt", True, False, True)   # Only repeats (the press went elsewhere).
        self.assertIsNone(studio_enter.on_release(state, "prompt"))

    def test_unavailable_says_why_and_does_nothing(self):
        state = studio_state(available=False)
        self.assertEqual(studio_enter.on_return(state, "prompt", True, False, False), BLOCKED)
        self.assertEqual(state["notice"], "Describe what you want to generate")
        self.assertIsNone(studio_enter.on_release(state, "prompt"))

    def test_shift_return_is_a_line(self):
        state = studio_state()
        self.assertEqual(studio_enter.on_return(state, "prompt", True, True, False), LINE)
        self.assertIsNone(studio_enter.on_release(state, "prompt"))

    def test_a_field_without_a_primary_action_lets_go(self):
        state = {"field_focus": "size", "hits": [], "selection": (0, 2)}
        self.assertEqual(studio_enter.on_return(state, "size", False, False, False), BLUR)
        self.assertIsNone(state["field_focus"])

    def test_focus_moved_before_the_release(self):
        state = studio_state()
        studio_enter.on_return(state, "prompt", True, False, False)
        self.assertIsNone(studio_enter.on_release(state, None))
        self.assertIsNone(studio_enter.on_release(state, "prompt"), "The armed press was spent")


def _calls(path, name):
    tree = ast.parse((ADDON / path).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == name]


class WiringTests(unittest.TestCase):
    def test_studio_input_routes_return_through_the_rule(self):
        source = (ADDON / "studio_input.py").read_text(encoding="utf-8")
        self.assertTrue(_calls("studio_input.py", "on_return"), "Return's press goes through studio_enter")
        self.assertTrue(_calls("studio_input.py", "on_release"), "Return's release runs the primary action")
        self.assertNotIn("if key in MULTILINE_FIELDS:\n                insert", source,
                         "Return no longer inserts a line by itself")
        release = source[source.index("studio_enter.on_release"):source.index("if not key or event.value")]
        press = source[source.index("event.type == 'LEFTMOUSE' and event.value == 'PRESS'"):
                       source.index("studio_enter.on_release")]
        for path in (release, press):
            self.assertIn("self.activate(context, state, area, region, target)", path, "The same click as the button's")
        activate = source[source.index("def activate"):]
        self.assertIn("refusable(state, lambda: click(bpy.context, state, action, value, rect))", activate)

    def test_every_paragraph_field_names_its_primary_action(self):
        """Media Studio's paragraph fields (studio_state.MULTILINE_FIELDS) each name what Return runs there."""
        state_tree = ast.parse((ADDON / "studio_state.py").read_text(encoding="utf-8"))
        fields = next(ast.literal_eval(n.value.args[0]) for n in ast.walk(state_tree) if isinstance(n, ast.Assign)
                      and getattr(n.targets[0], "id", "") == "MULTILINE_FIELDS")
        named = set()
        for path in ("studio_draw.py", "studio_workflows.py"):
            for call in _calls(path, "primary"):
                key = call.args[1]
                named.add(key.value if isinstance(key, ast.Constant) else
                          "workflow_text" if getattr(key, "id", "") == "INPUT_KEY" else None)
        self.assertEqual(named, set(fields))


if __name__ == "__main__":
    unittest.main()
