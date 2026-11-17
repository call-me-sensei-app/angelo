# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A how-to card's layout keeps every part inside the card and cuts no text, at any width (learn mode). No Blender.

Covered (``learn_card_draw.layout`` with a stand-in ``measure`` and ``wrap``): at a wide, a usual and a narrow card
every text, key cap and pill lies inside the card's padding and above its bottom, and nothing overlaps on a line;
every text of the card is placed whole or as wrapped lines that join back to it; key caps follow the command's name
and wrap to a new line when they do not fit; "No shortcut in your keymap" shows for a command with no key and no
menu key, never for a step that is not a command and never beside a key cap; breadcrumbs carry one chevron between
two parts; a card grows with its steps and a rule separates them; ``plan`` returns the kept layout for the same
card and size and a new one for another; ``learn_rows`` shows no card for a step that is not a typed edit, without
looking anything up.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import learn_card_draw as draw, learn_rows  # noqa: E402
from angelo_agent.learn_cards import Step  # noqa: E402

LINE = 20
NOTE = "By hand you start the tool and move the mouse; Angelo gave the distance as numbers."
BEVEL = Step("mesh_bevel", 0, "Bevel", ("Ctrl B", "Shift Ctrl B"), ("3D Viewport", "Edge", "Bevel Edges"), (),
             (("Width", "0.02 m"), ("Segments", "3"), ("Profile Shape", "0.5")), ("Edit Mode", "3 edges selected"), NOTE,
             True)
APPLY = Step("transform_apply", 1, "Apply Object Transform", (), ("3D Viewport", "Object", "Apply", "All Transforms"),
             ("Apply", "Ctrl A"), (("Scale", "On"),), ("Object Mode",), "", True)
BRIDGE = Step("operator_run", 2, "Bridge Edge Loops", (), ("3D Viewport", "Edge", "Bridge Edge Loops"), (),
              (("Number of Cuts", "2"),), ("Edit Mode",), "", True)
OWN = Step("mesh_bend", 3, "Done by Angelo's own tool",
           note="Angelo did this with its own tool. No single command does the same by hand.")
CARD = (BEVEL, APPLY, BRIDGE, OWN)


def measure(text, size):
    return len(text) * size * 0.55


def wrap(text, width, size):
    lines, line = [], ""
    for word in text.split():
        if line and measure(line + " " + word, size) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    return lines + [line]


def boxes(items):
    """(left, right, top, bottom, value) of every text, key cap and pill."""
    found = []
    for item in items:
        if item[0] == "text":
            _, x, base, value, size, _role = item
            found.append((x, x + measure(value, size), base - size, base + size * 0.3, value))
        elif item[0] in ("cap", "pill"):
            _, x, y, wide, high, value, _size = item
            found.append((x, x + wide, y, y + high, value))
    return found


class Layout(unittest.TestCase):
    def test_everything_stays_inside_the_card_at_any_width(self):
        for width in (640, 380, 300, 220):
            height, items = draw.layout(CARD, width, 14, LINE, measure, wrap)
            with self.subTest(width=width):
                for left, right, top, bottom, value in boxes(items):
                    self.assertGreaterEqual(left, draw.PAD - 0.01, value)
                    self.assertLessEqual(right, width - draw.PAD + 0.01, value)
                    self.assertGreaterEqual(top, 0, value)
                    self.assertLessEqual(bottom, height, value)

    def test_nothing_overlaps_on_a_line(self):
        for width in (640, 300):
            _height, items = draw.layout(CARD, width, 14, LINE, measure, wrap)
            placed = boxes(items)
            for index, first in enumerate(placed):
                for second in placed[index + 1:]:
                    rows_meet = first[2] < second[3] - 4 and second[2] < first[3] - 4
                    columns_meet = first[0] < second[1] - 0.01 and second[0] < first[1] - 0.01
                    with self.subTest(width=width, first=first[4], second=second[4]):
                        self.assertFalse(rows_meet and columns_meet)

    def test_no_text_is_cut(self):
        for width in (640, 220):
            _height, items = draw.layout(CARD, width, 14, LINE, measure, wrap)
            shown = " ".join(item[3] if item[0] == "text" else item[5] for item in items if item[0] in ("text", "cap", "pill"))
            for step in CARD:
                wanted = [step.title, step.note, *step.keys, *step.path, *step.needs,
                          *(text for pair in step.settings for text in pair)]
                for text in filter(None, wanted):
                    for word in text.split():
                        with self.subTest(width=width, word=word):
                            self.assertIn(word, shown)
            self.assertIn("Ctrl A", shown)   # The key that opens a menu, in its sentence.

    def test_key_caps_follow_the_name_and_wrap_when_they_do_not_fit(self):
        _height, wide = draw.layout((BEVEL,), 640, 14, LINE, measure, wrap)
        title = next(item for item in wide if item[0] == "text" and item[3] == "Bevel")
        caps = [item for item in wide if item[0] == "cap"]
        self.assertEqual([cap[5] for cap in caps], ["Ctrl B", "Shift Ctrl B"])
        self.assertTrue(all(cap[1] > title[1] and abs(cap[2] + cap[4] / 2 - (title[2] - 14 * 0.36)) < 0.01 for cap in caps),
                        "on the name's line, after it")
        _height, narrow = draw.layout((BEVEL,), 170, 14, LINE, measure, wrap)
        ys = sorted({item[2] for item in narrow if item[0] == "cap"})
        self.assertGreater(len(ys), 1, "the second key cap takes a new line")

    def test_no_shortcut_is_said_only_for_a_command_without_any_key(self):
        no_key = draw.NO_KEY

        def says(step):
            return any(item[0] == "text" and item[3] == no_key for item in draw.layout((step,), 400, 14, LINE, measure, wrap)[1])
        self.assertTrue(says(BRIDGE))
        self.assertFalse(says(BEVEL), "it has keys")
        self.assertFalse(says(APPLY), "a key opens its menu")
        self.assertFalse(says(OWN), "not a command")

    def test_breadcrumbs_have_one_chevron_between_two_parts(self):
        _height, items = draw.layout((APPLY,), 640, 14, LINE, measure, wrap)
        self.assertEqual(sum(1 for item in items if item[0] == "next"), len(APPLY.path) - 1)

    def test_a_card_grows_with_its_steps_and_rules_separate_them(self):
        one = draw.layout((BEVEL,), 400, 14, LINE, measure, wrap)
        four = draw.layout(CARD, 400, 14, LINE, measure, wrap)
        self.assertGreater(four[0], one[0])
        self.assertEqual(sum(1 for item in four[1] if item[0] == "rule"), len(CARD) - 1)
        self.assertEqual(sum(1 for item in one[1] if item[0] == "rule"), 0)

    def test_a_layout_is_kept_for_the_same_card_and_size(self):
        self.addCleanup(draw.forget)
        calls = []

        def counted(text, size):
            calls.append(text)
            return measure(text, size)
        first = draw.plan(CARD, 400, 14, LINE, counted, wrap)
        measured = len(calls)
        self.assertIs(draw.plan(CARD, 400, 14, LINE, counted, wrap), first)
        self.assertEqual(len(calls), measured, "nothing is measured again")
        self.assertIsNot(draw.plan(CARD, 300, 14, LINE, counted, wrap), first)
        self.assertIsNot(draw.plan(tuple(CARD), 400, 14, LINE, counted, wrap), None)
        for width in range(draw.MAX_PLANS + 8):
            draw.plan(CARD, 200 + width, 14, LINE, measure, wrap)
        self.assertLessEqual(len(draw._plans), draw.MAX_PLANS)


class Rows(unittest.TestCase):
    def test_a_step_that_is_not_a_typed_edit_shows_no_card_and_looks_nothing_up(self):
        script = {"role": "activity", "call_id": "c", "tool": "angelo_execute_python", "arguments": {"code": "pass"},
                  "status": "completed", "result": {}}
        self.assertIsNone(learn_rows.block(script, "activity", "c", {"c"}, 400, 14, LINE))
        self.assertIsNone(learn_rows.block(script, "thinking", "c", {"c"}, 400, 14, LINE))
        self.assertEqual(learn_rows.under(script, "activity", "c", {"c"}, 400, 14, LINE, 7), (script, 7))
        self.assertEqual(learn_rows.draw_under(None, script, 0, 0, 400, {}), 0)


    def test_a_drawn_card_is_counted_once_by_how_it_came_to_be_open(self):
        self.addCleanup(learn_rows.forget)
        learn_rows.forget()

        class View:   # The canvas's primitives, drawing nothing.
            def shape(self, *args): pass
            def text(self, *args): pass
            def icon(self, *args): pass
        height, items = draw.layout((BEVEL,), 400, 14, LINE, measure, wrap)
        clicked = {"call_id": "a", "_learn": items, "_learn_height": height, "_learn_opened": True}
        kept = {"call_id": "b", "_learn": items, "_learn_height": height, "_attached": True}
        state = {}
        for _ in range(3):   # Every frame draws it again: still one step each.
            self.assertEqual(learn_rows.draw_under(View(), clicked, 24, 300, 400, state), height + learn_rows.GAP)
            learn_rows.draw_under(View(), kept, 24, 300, 400, state)
        self.assertEqual((learn_rows.opened(), learn_rows.kept_open()), (1, 1))
        self.assertEqual(sorted(state["learn_drawn"]), ["a", "b"])
        self.assertIn("Bevel", state["learn_drawn"]["a"]["texts"])
        self.assertEqual(state["learn_drawn"]["a"]["caps"], ["Ctrl B", "Shift Ctrl B"])
        learn_rows.forget()
        self.assertEqual((learn_rows.opened(), learn_rows.kept_open()), (0, 0))


if __name__ == "__main__":
    unittest.main()
