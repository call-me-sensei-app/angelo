# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's chrome (``markup_layout``): its bars, palette, instructions field and every control's hit and
label, at each mode, scale and size. No Blender here: text is measured at a fixed width per character.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md, markup_layout as ml, markup_tools as mt  # noqa: E402


def measure(text, size):
    return len(text) * size * 0.55


def plan(mode="view", w=1400, h=900, scale=1.0, palette=False, card=None):
    return ml.layout(w, h, scale, mode, palette, measure, card=card)


def inside(outer, inner):
    return (outer[0] - 0.01 <= inner[0] and inner[0] + inner[2] <= outer[0] + outer[2] + 0.01
            and outer[1] - 0.01 <= inner[1] and inner[1] + inner[3] <= outer[1] + outer[3] + 0.01)


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class ChromeTests(unittest.TestCase):
    def test_every_control_stays_clear_of_the_regions_drawn_over_the_view(self):
        """The asset shelf's header (a region of its own drawn over the view) covered the instructions field's lower
        edge in a narrow window (GUI suite, 2026-11-20). The chrome lays out inside the frame the toolbar, the
        sidebar and the bottom regions leave free (``chat_launcher.insets``), in every mode, palette and card."""
        left, right, bottom, w, h = 50, 120, 44, 1024, 640
        free = (left, bottom, w - left - right, h - bottom)
        for mode in ml.MODES:
            for palette in (False, True):
                for card in (None, 2):
                    with self.subTest(mode=mode, palette=palette, card=card):
                        p = ml.layout(w, h, 1.0, mode, palette, measure, card=card, insets=(left, right, bottom))
                        for rect, _action, _value in p["hits"]:
                            self.assertTrue(inside(free, rect), (rect, _action))
                        self.assertGreaterEqual(p["margins"][0], left)
                        self.assertGreaterEqual(p["margins"][1], right)
                        self.assertGreaterEqual(p["margins"][3], bottom)
        self.assertEqual(plan("markup")["hits"], ml.layout(1400, 900, 1.0, "markup", False, measure)["hits"],
                         "no insets: the same layout as before")

    def test_view_mode_offers_the_five_and_no_field(self):
        from unittest import mock   # The switch on (its off state: the next test): the five as they would ship.
        with mock.patch.object(mt, "AI_EDITS", True):
            p = plan("view")
        self.assertEqual([(i["action"], i["value"]) for i in p["bar"]],
                         [("mode", "markup"), ("mode", "comment"), ("ask", "remove_bg"), ("mode", "erase"),
                          ("mode", "resize")])
        self.assertIsNone(p["field"])
        self.assertIsNotNone(p["close"])

    def test_without_ai_edits_the_bar_offers_markup_comment_and_resize_only(self):
        """The shipping switch for Remove BG and Erase (``markup_tools.AI_EDITS``): off while their image connection
        is unproven live (coordinator, 2026-11-20). Off, neither is offered, and neither can be reached another way."""
        from unittest import mock
        with mock.patch.object(mt, "AI_EDITS", False):
            self.assertEqual([(i["action"], i["value"]) for i in plan("view")["bar"]],
                             [("mode", "markup"), ("mode", "comment"), ("mode", "resize")])
            session = mt.Session(md.MarkupDoc("5f0e1c1a-2b2c-4d4e-8f8f-123456789abc", (100, 100)),
                                 __import__("angelo_agent.markup_view", fromlist=["View"]).View(
                                     (100, 100), (0, 0, 400, 300), (0, 0, 0, 0)))
            for attempt in (lambda: session.set_mode("erase"), lambda: session.ask("remove_bg"),
                            lambda: session.ask("erase")):
                with self.assertRaises(ValueError):
                    attempt()

    def test_erase_mode_offers_brush_sizes_undo_clear_run_and_done(self):
        actions = [(i["action"], i["value"]) for i in plan("erase")["bar"]]
        self.assertEqual([v for a, v in actions if a == "width"], [1, 2, 3])
        for wanted in (("undo", None), ("clear", None), ("ask", "erase"), ("mode", "view")):
            self.assertIn(wanted, actions)
        self.assertIsNone(plan("erase")["field"])

    def test_resize_mode_offers_every_scale_apply_and_done(self):
        actions = [(i["action"], i["value"]) for i in plan("resize")["bar"]]
        self.assertEqual([v for a, v in actions if a == "scale"], [100, 75, 50, 25])
        self.assertIn(("apply_resize", None), actions)
        self.assertIn(("mode", "view"), actions)

    def test_a_question_card_takes_every_hit_with_its_two_answers(self):
        p = plan("erase", card=4)
        self.assertEqual([h[1] for h in p["hits"]], ["answer", "answer"])
        self.assertEqual([h[2] for h in p["hits"]], [False, True])
        self.assertTrue(inside((0, 0, 1400, 900), p["card"]))

    def test_markup_mode_offers_every_tool_colour_width_eraser_undo_redo_and_done(self):
        actions = [(i["action"], i["value"]) for i in plan("markup")["bar"]]
        tools = [value for action, value in actions if action == "tool"]
        self.assertEqual(set(tools), set(mt.TOOLS))
        for wanted in (("palette", None), ("undo", None), ("redo", None), ("mode", "view")):
            self.assertIn(wanted, actions)

    def test_markup_and_comment_modes_have_the_instructions_field_and_send(self):
        for mode in ("markup", "comment"):
            p = plan(mode)
            self.assertIsNotNone(p["field"])
            self.assertTrue(inside(p["field"], p["send"]))

    def test_the_palette_offers_every_colour_and_width(self):
        palette = [(i["action"], i["value"]) for i in plan("markup", palette=True)["palette"]]
        self.assertEqual([v for a, v in palette if a == "colour"], list(md.COLOURS))
        self.assertEqual([v for a, v in palette if a == "width"], list(md.WIDTHS))

    def test_hits_put_the_palette_first(self):
        p = plan("markup", palette=True)
        self.assertEqual(p["hits"][0][1], "colour")

    def test_nothing_overlaps_and_everything_is_on_screen_at_every_size_and_scale(self):
        for mode in ml.MODES:
            for w, h, scale in ((1400, 900, 1.0), (900, 620, 1.0), (2800, 1800, 2.0), (700, 500, 1.0)):
                p = plan(mode, w, h, scale, palette=(mode == "markup"))
                rects = [rect for rect, _a, _v in p["hits"]]
                for rect in rects:
                    self.assertTrue(inside((0, 0, w, h), rect), (mode, w, h, rect))
                items = p["bar"] + (p["palette"] or [])
                for i, a in enumerate(items):
                    for b in items[i + 1:]:
                        self.assertFalse(overlaps(a["rect"], b["rect"]), (mode, w, a["action"], b["action"]))

    def test_targets_are_at_least_32_px_at_1x_and_grow_with_scale(self):
        for scale in (1.0, 2.0):
            for item in plan("markup", scale=scale, palette=True)["bar"]:
                self.assertGreaterEqual(min(item["rect"][2], item["rect"][3]), 32 * scale - 0.01)

    def test_every_control_has_a_label_for_its_tooltip(self):
        for mode in ml.MODES:
            p = plan(mode, palette=True)
            for item in p["bar"] + (p["palette"] or []):
                self.assertTrue(item["label"], item)
            self.assertTrue(p["close_label"])

    def test_the_picture_keeps_clear_of_the_chrome(self):
        p = plan("markup")
        left, right, top, bottom = p["margins"]
        self.assertGreaterEqual(bottom, p["bar_rect"][1] + p["bar_rect"][3])
        self.assertGreaterEqual(top, 900 - p["close"][1])

    def test_tool_labels_name_their_keys_from_the_one_table(self):
        labels = {i["value"]: i["label"] for i in plan("markup")["bar"] if i["action"] == "tool"}
        for key, tool in mt.TOOL_KEYS.items():
            if tool in labels:
                self.assertEqual(labels[tool][1]["key"], key)


class TooltipTests(unittest.TestCase):
    """Re-review of b5808fd5a2e, N5: the paste hint was one unwrapped line of about 85 characters, shown to everyone.
    ``markup_chrome`` draws with Blender, so its row breaker and language set are read from the source."""

    def chrome(self):
        import ast
        tree = ast.parse((HERE.parents[2] / "scripts/addons_core/angelo_agent/markup_chrome.py").read_text(encoding="utf-8"))
        found = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "tip_rows" or isinstance(node, ast.Assign) and \
                    any(getattr(t, "id", "") == "PASTE_LANGUAGES" for t in node.targets):
                exec(compile(ast.Module([node], []), "markup_chrome", "exec"), found)
        return found

    def test_a_long_tooltip_wraps_at_spaces_or_between_characters(self):
        rows = self.chrome()["tip_rows"]
        measure = len   # One unit a character.
        self.assertEqual(rows("Send to Angelo", measure, 40), ["Send to Angelo"])
        wrapped = rows(ml.LABELS["paste_hint"], measure, 40)
        self.assertGreater(len(wrapped), 1)
        self.assertTrue(all(len(row) <= 40 for row in wrapped))
        self.assertEqual(" ".join(wrapped), ml.LABELS["paste_hint"])
        cjk = rows("要在此处输入中文、日文或韩文，请粘贴文本：此处暂不支持输入法", measure, 10)
        self.assertTrue(all(len(row) <= 10 for row in cjk) and len(cjk) > 1)

    def test_the_paste_hint_is_for_the_input_method_languages(self):
        self.assertEqual(self.chrome()["PASTE_LANGUAGES"], frozenset({"zh_HANS", "zh_HANT", "ja", "ko"}))


if __name__ == "__main__":
    unittest.main()
