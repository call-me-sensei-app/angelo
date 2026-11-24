# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plan card and plan mode's chip as laid out (doc/angelo/plan_mode.rst, "The card and chips"): each approach's
title, steps, risks and estimate, the recommendation, open questions; Go with this and Edit only while the plan is
live; Go's hit carrying exactly its plan, approach and setting (its ticket's digest); the states after it; the chip's
two forms; the person's Go row; every string from translatable constants. Pure layout: no Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))

from angelo_agent import planning_card as C, planning_state as S, planning_text as T  # noqa: E402

PLAN = {"summary": "Two ways to build the tower, by how often it changes.", "recommended": 1,
        "questions": ["Is it for a game engine?"],
        "approaches": [{"title": "Booleans", "steps": ["Block out", "Cut the windows"], "risks": [],
                        "estimate": "10 min"},
                       {"title": "Modular kit", "steps": ["Make one bay", "Array it"],
                        "risks": ["More objects to manage"], "estimate": "20 min"}]}


def wrap(text, width, size):
    """Words to lines at about half the size per character, as the canvas measures roughly."""
    per_line, rows, line = max(1, int(width / (size * 0.55))), [], ""
    for word in text.split(" "):
        if line and len(line) + 1 + len(word) > per_line:
            rows.append((line, 0))
            line = word
        else:
            line = (line + " " + word).strip()
    return rows + [(line, 0)]


def measure(label, size):
    return len(label) * size * 0.55


def card(state=S.LIVE, setting="AUTO_BOUNDED", width=520, chosen=None):
    return C.layout(PLAN, "call-1", state, setting, 16, 900, width, wrap, 18, measure, chosen)


def texts(ops):
    return [op[1] for op in ops if op[0] == "text"]


def hits(ops):
    return [(op[2], op[3]) for op in ops if op[0] == "hit"]


class CardTests(unittest.TestCase):
    def test_a_live_card_shows_the_whole_plan(self):
        ops, height = card()
        shown = texts(ops)
        for text in ("Booleans", "Modular kit", "Block out", "Array it", "More objects to manage", "Recommended",
                     "Estimate: 20 min", "Is it for a game engine?", PLAN["summary"], "Go with this · Auto", "Edit"):
            self.assertTrue(any(text in line for line in shown), text)
        self.assertGreater(height, 200)
        self.assertEqual(ops[0][0], "rect")   # The panel under everything, as tall as the card.
        self.assertEqual(ops[0][4], height)

    def test_go_carries_its_plan_approach_and_setting_and_then_chooses_the_setting(self):
        found = hits(card()[0])
        self.assertIn(("plan_go", ("call-1", 0, "AUTO_BOUNDED")), found)
        self.assertIn(("plan_go", ("call-1", 1, "AUTO_BOUNDED")), found)
        self.assertIn(("plan_edit", ("call-1", 1)), found)
        self.assertIn(("plan_then", ("call-1", "ASK")), found)
        self.assertIn(("plan_go", ("call-1", 1, "ASK")), hits(card(setting="ASK")[0]))
        self.assertIn("Go with this · Ask", texts(card(setting="ASK")[0]))   # The button names its setting (M1).
        self.assertNotIn("Go with this · Auto", texts(card(setting="ASK")[0]))
        from angelo_agent import grant_guard
        self.assertLessEqual({"plan_go", "plan_chip_stop"}, grant_guard.SHIELDED)   # Never a press as it appears.

    def test_only_a_live_plan_has_controls(self):
        for state, words in ((S.SUPERSEDED, "A newer plan replaced this one."),
                             (S.ENDED, "Planning ended without a choice."),
                             (S.CHOSEN, "You went with approach 2: Modular kit")):
            with self.subTest(state):
                ops, _ = card(state, chosen=1 if state == S.CHOSEN else None)
                self.assertEqual(hits(ops), [])
                self.assertIn(words, texts(ops))

    def test_go_never_cuts_its_setting(self):
        """Go's label says what the press does: on a narrow card it wraps, the setting kept whole (round-2 nit)."""
        for width in (520, 200, 150):
            with self.subTest(width=width):
                ops, _ = card(width=width)
                go = [op for op in ops if op[0] == "hit" and op[2] == "plan_go"][0][1]
                inside = [op for op in ops if op[0] == "text" and go[0] <= op[2] <= go[0] + go[2]
                          and go[1] <= op[3] <= go[1] + go[3]]
                self.assertEqual("".join(op[1] for op in inside).replace(" ", ""), "Gowiththis·Auto")
                self.assertTrue(all(measure(op[1], op[4]) <= op[6] for op in inside), "a line wider than its button")

    def test_a_narrow_card_wraps_and_keeps_every_control_inside(self):
        ops, _ = card(width=240)
        for op in ops:
            if op[0] == "hit":
                x, _y, w, _h = op[1]
                self.assertLessEqual(x + w, 16 + 240 - C.PAD + 0.5, op)

    def test_every_string_the_card_shows_is_a_translatable_constant(self):
        tree = ast.parse((ADDON / "planning_card.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "iface_":
                self.assertTrue(ast.unparse(node.args[0]).startswith("T."), ast.unparse(node))   # Never a literal.


class RowAndChipTests(unittest.TestCase):
    def test_the_persons_go_row_reads_as_their_choice(self):
        choice = S.choice_fields("call-1", 1, PLAN, "ASK", "Six bays")
        row = {"role": "user", "text": S.go_text("call-1", 1, PLAN, "ASK", "Six bays"), "plan_choice": choice}
        shown = T.row_display(row["plan_choice"], row["plan_choice"]["notes"])
        self.assertEqual(shown, "Going with approach 2: Modular kit\nYour changes: Six bays")
        self.assertIn("angelo_plan", row["text"])   # The model reads the facts; the person, their choice.
        source = (ADDON / "console.py").read_text(encoding="utf-8")   # console needs bpy: its route read as source.
        self.assertIn('return row_display(message["plan_choice"], message["plan_choice"].get("notes", ""))', source)

    def chat(self, messages, planning=False):
        options = {"approval": "READ_ONLY", "planning": True} if planning else {"approval": "ASK"}
        return SimpleNamespace(state={"id": "c", "messages": messages, "options": options})

    def test_the_chip_says_planning_then_holds_the_chosen_plan(self):
        proposed = [{"role": "activity", "tool": "angelo_plan", "call_id": "call-1",
                     "result": {"status": "SHOWN", "plan": PLAN}}]
        self.assertEqual(C.chip_parts(self.chat(proposed, planning=True)),
                         (T.CHIP_PLANNING, [], "plan_chip_stop", None))
        self.assertIsNone(C.chip_parts(self.chat(proposed)))
        chosen = proposed + [{"role": "user", "text": "x", "plan_choice": S.choice_fields("call-1", 1, PLAN, "ASK")}]
        title, buttons, close, value = C.chip_parts(self.chat(chosen))
        self.assertEqual(title, "Plan · Modular kit")
        self.assertEqual(buttons, [(T.REVISE, "plan_chip_revise", None)])
        self.assertEqual((close, value), ("plan_chip_dismiss", "call-1"))
        dismissed = self.chat(chosen)
        dismissed._plan_chip_dismissed = "call-1"
        self.assertIsNone(C.chip_parts(dismissed))
        newer = chosen + [dict(proposed[0], call_id="call-2")]   # A newer plan: no chosen plan to hold.
        self.assertIsNone(C.chip_parts(self.chat(newer)))

    def test_the_chip_reads_a_chat_once_until_it_changes(self):
        """Break-it review nit 7: the chip is asked three times a frame in every chat; the chosen plan is read from the
        rows once per change of them, not once per ask."""
        from unittest.mock import patch
        proposed = [{"role": "activity", "tool": "angelo_plan", "call_id": "call-1",
                     "result": {"status": "SHOWN", "plan": PLAN}}]
        chat = self.chat(proposed + [{"role": "user", "text": "x",
                                      "plan_choice": S.choice_fields("call-1", 1, PLAN, "ASK")}])
        real, scans = S.chosen, []
        with patch.object(S, "chosen", lambda messages: scans.append(1) or real(messages)):
            for _ in range(3):
                self.assertEqual(C.chip_parts(chat)[0], "Plan · Modular kit")
            self.assertEqual(len(scans), 1)
            chat.state["messages"].append(dict(proposed[0], call_id="call-2"))   # A newer plan.
            self.assertIsNone(C.chip_parts(chat))
            self.assertEqual(len(scans), 2)
            chat.state["messages"][-1] = {"role": "assistant", "text": "replaced in place"}
            self.assertEqual(C.chip_parts(chat)[0], "Plan · Modular kit")
            chat.state["messages"] = list(chat.state["messages"][:2])   # A new list (a reload, a rebase).
            self.assertEqual(C.chip_parts(chat)[0], "Plan · Modular kit")
        self.assertEqual(len(scans), 4)

    def test_revise_plan_is_shielded_and_only_the_persons_press(self):
        """Break-it review nit 5: Revise plan appears with the chip in the frame Go may start a goal, and it ends a
        goal: a press that lands on it as it appears is not taken (``SHIELDED``), and a click with no ticket of the
        person's does nothing."""
        from unittest.mock import patch
        from angelo_agent import grant_guard
        self.assertIn("plan_chip_revise", grant_guard.SHIELDED)
        revised, chat = [], self.chat([])
        context = SimpleNamespace(area=None)
        with patch("angelo_agent.planning_controls.revise", lambda c: revised.append(c)):
            with patch("angelo_agent.grant_authority.granting", lambda: False):
                self.assertFalse(C.dispatch(context, chat, {}, "plan_chip_revise", None))
            self.assertEqual(revised, [])
            with patch("angelo_agent.grant_authority.granting", lambda: True):
                self.assertTrue(C.dispatch(context, chat, {}, "plan_chip_revise", None))
        self.assertEqual(revised, [chat])

    def test_an_angelo_request_never_counts_as_a_choice(self):
        row = {"role": "user", "request_kind": "angelo_goal", "plan_choice": {"call_id": "call-1"}}
        self.assertIsNone(S.choice_of(row))


if __name__ == "__main__":
    unittest.main()
