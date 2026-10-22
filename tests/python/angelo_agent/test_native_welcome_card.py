# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The empty chat's welcome card rows (``chat_welcome.draw_empty``), without Blender.

A connected empty chat shows the example prompts, then one tip that links to the
website's docs section explaining it, then (on a team connection) "More
options". Every row stays above the composer, a tip that wraps keeps one hit,
and both links share the card's one link style. ``test_blender_first_run``
proves the same card in the running app and screenshots it.
"""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import canvas_controls, first_run  # noqa: E402
from angelo_agent.markdown_text import safe_url  # noqa: E402


def measure(value, size):
    """Text is half its font size wide per character in these tests (Inter's average is close)."""
    return 0.5 * size * len(value)


class FakeBlf:
    def __init__(self):
        self.current = 14

    def size(self, font, size):
        self.current = size

    def dimensions(self, font, value):
        return measure(value, self.current), self.current


def fake_modules():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object)
    bpy.props = SimpleNamespace(StringProperty=lambda **_: None, IntProperty=lambda **_: None)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.context = SimpleNamespace(preferences=SimpleNamespace(view=SimpleNamespace(use_reduce_motion=True)))
    gpu = SimpleNamespace(state=SimpleNamespace(blend_set=lambda mode: None))
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "blf": FakeBlf(), "gpu": gpu}


class FakeView(SimpleNamespace):
    """chat_view's drawing surface, recording text and underlines; wraps greedily as ``measure`` measures."""

    MUTED = (0.5, 0.5, 0.5, 1)

    def __init__(self):
        super().__init__(_font=0, texts=[], strokes=[])

    def text(self, value, x, y, size=14, color=None, font=None):
        self.texts.append((value, x, y, size, color))

    def measure(self, value, size=14, font=None, scale=None):
        return measure(value, size)

    def stroke(self, x1, y1, x2, y2, color, width=1):
        self.strokes.append((x1, y1, x2, y2, color))

    def shape(self, *args):
        pass

    def icon(self, *args):
        pass

    def wrap(self, value, width, size, font):
        rows, line = [], ""
        for word in value.split():
            candidate = (line + " " + word).strip()
            if line and measure(candidate, size) > width:
                rows.append((line, 0))
                line = word
            else:
                line = candidate
        return rows + [(line, 0)] if line else rows


class WelcomeCardRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(sys.modules, fake_modules()):
            for name in ("angelo_agent.chat_welcome", "angelo_agent.quick_connect"):
                sys.modules.pop(name, None)
            cls.welcome = importlib.import_module("angelo_agent.chat_welcome")

    @classmethod
    def tearDownClass(cls):
        for name in ("angelo_agent.chat_welcome", "angelo_agent.quick_connect"):
            sys.modules.pop(name, None)

    def draw(self, connected=True, width=593, team_line=None, options=(), model_card=None):
        """Draw the card into a fresh state; returns (state, view). The composer's top (the floor) is at 120.

        ``model_card`` is the card a chat without a model gets (``test_native_chat_start_states``); None: the
        chat has a model."""
        view, state = FakeView(), {"hits": []}
        policy = SimpleNamespace(current=lambda: SimpleNamespace(team_only=False, allow_personal_connections=True))
        team_chat = SimpleNamespace(ready_line=lambda record: team_line)
        patches = [mock.patch.object(canvas_controls, "ring", lambda *args, **kwargs: None)]
        for name, module in (("chat_view", view), ("managed_policy", policy), ("team_chat", team_chat)):
            patches += [mock.patch.dict(sys.modules, {"angelo_agent." + name: module}),
                        mock.patch.object(angelo_agent, name, module, create=True)]
        patches.append(mock.patch.object(self.welcome, "_options", return_value=list(options)))
        patches.append(mock.patch.object(self.welcome, "model_card", return_value=model_card))
        for item in patches:
            item.start()
        try:
            ui = SimpleNamespace(connection_record=lambda: None, get_controller=lambda: None)
            self.welcome.draw_empty(state, ui, width, 900, 120, 14, connected)
        finally:
            for item in reversed(patches):
                item.stop()
        return state, view

    @staticmethod
    def actions(state):
        return [(action, value) for _, action, value in state["hits"]]

    @staticmethod
    def rect(state, action):
        return next(rect for rect, kind, _ in state["hits"] if kind == action)

    def tip_lines(self, view):
        return [entry for entry in view.texts
                if entry[4] == canvas_controls.LINK and not entry[0].startswith("More options")]

    def test_examples_then_one_tip_that_links_to_the_docs(self):
        state, view = self.draw()
        examples = [("welcome_example", index) for index in range(len(first_run.EXAMPLES))]
        self.assertEqual(self.actions(state), examples + [("url", first_run.TIP_URL)])
        self.assertEqual(state["drawn_tip"], (first_run.TIP, first_run.TIP_URL))
        self.assertEqual(state["hit_style"][("url", first_run.TIP_URL)], "link")
        (value, x, y, size, _), = self.tip_lines(view)
        self.assertEqual((value, x, size), (first_run.TIP, 24, 11), "One quiet line, the size of the card's other link")
        underline = (24, y - 3, 24 + measure(value, 11), y - 3, canvas_controls.LINK)
        self.assertIn(underline, view.strokes, "Underlined, as a link")
        last_example, tip = state["hits"][-2][0], self.rect(state, "url")
        self.assertLessEqual(tip[1] + tip[3], last_example[1], "The tip sits under the examples")
        self.assertGreaterEqual(tip[1], 120, "and above the composer")

    def test_a_wrapped_tip_keeps_one_hit_over_every_line(self):
        state, view = self.draw(width=200)
        lines = self.tip_lines(view)
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(entry[0] for entry in lines), first_run.TIP, "Wrapped, never truncated")
        self.assertEqual(self.actions(state).count(("url", first_run.TIP_URL)), 1)
        x, y, w, h = self.rect(state, "url")
        for value, tx, ty, _, _ in lines:
            self.assertTrue(x <= tx and tx + measure(value, 11) <= x + w and y <= ty - 3 and ty + 11 <= y + h, value)
            self.assertIn((tx, ty - 3, tx + measure(value, 11), ty - 3, canvas_controls.LINK), view.strokes)
        self.assertGreaterEqual(min(rect[1] for rect, _, _ in state["hits"]), 120, "Every row stays above the composer")

    def test_more_options_follows_the_tip_on_a_team_connection(self):
        state, view = self.draw(team_line="Your team's Claude and OpenAI keys are ready.")
        self.assertEqual(self.actions(state)[-2:], [("url", first_run.TIP_URL), ("settings", None)])
        tip, more = self.rect(state, "url"), self.rect(state, "settings")
        self.assertLessEqual(more[1] + more[3], tip[1], "More options sits under the tip, without overlap")
        self.assertEqual(more[3], 22, "A one-line link keeps its hit height")
        self.assertEqual(state["hit_style"][("settings", None)], "link")

    def test_no_tip_before_a_connection(self):
        api_key = {"id": "api_key", "label": first_run.API_KEY["label"], "note": first_run.API_KEY["note"],
                   "action": "welcome_api_key", "value": None}
        state, view = self.draw(connected=False, options=[api_key])
        self.assertIsNone(state["drawn_tip"])
        self.assertNotIn(("url", first_run.TIP_URL), self.actions(state))
        self.assertEqual(self.actions(state), [("welcome_api_key", None), ("settings", None)])
        self.assertEqual(self.tip_lines(view), [])


class TipTextTests(unittest.TestCase):
    def test_the_tip_is_one_short_sentence(self):
        self.assertTrue(first_run.TIP.startswith("Tip: ") and first_run.TIP.endswith("."))
        self.assertLessEqual(len(first_run.TIP), 70)
        self.assertNotRegex(first_run.TIP, r"\d", "Figures belong to the docs, which say what each is measured against")

    def test_the_link_is_the_spend_less_section_of_the_identity_manual_page(self):
        manual = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text())["urls"]["manual"]
        self.assertEqual(first_run.TIP_URL, manual + "#spend-less")
        self.assertTrue(safe_url(first_run.TIP_URL))

    def test_a_click_opens_it_through_the_chat_url_action(self):
        source = (ADDON / "chat_view.py").read_text()
        dispatch = source.split("\ndef dispatch_action(", 1)[1].split("\ndef dispatch_popup(", 1)[0]
        branch = dispatch.split('elif action == "url":', 1)[1].split("\n    elif ", 1)[0]
        self.assertIn("safe_url(value)", branch)
        self.assertIn("bpy.ops.wm.url_open(url=value)", branch)


if __name__ == "__main__":
    unittest.main()
