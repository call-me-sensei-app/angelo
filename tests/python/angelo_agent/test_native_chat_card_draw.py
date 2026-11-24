# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's decision cards (chat_card_draw): plain summaries, readable details, measured layout, and controls
that are either live with their original action or disabled with a reason, never dead."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import chat_card_draw as cards  # noqa: E402
from angelo_agent.canvas_controls import DISABLED  # noqa: E402


class FakeView(types.SimpleNamespace):
    """chat_view's drawing surface, recording what a card draws; text is 7 px per character."""

    def __init__(self):
        super().__init__(_font=0, _mono=1, shapes=[], texts=[],
                         canvas_rtl=types.SimpleNamespace(visual=lambda value: value))

    def shape(self, x, y, w, h, color, radius=0):
        self.shapes.append((x, y, w, h, color))

    def text(self, value, x, y, size=14, color=None, font=None):
        self.texts.append((value, x, y, size))

    def elide(self, value, width, size=14):
        return value

    def wrap(self, value, width, size, font):
        return tuple((row, 0) for row in value.split("\n"))


class CardDrawTests(unittest.TestCase):
    def setUp(self):
        self.view = FakeView()
        blf = types.SimpleNamespace(size=lambda font, size: None, dimensions=lambda font, value: (7 * len(value), 10))
        # The kit's status chip draws with the GPU module (covered by the kit's own suites): stand it in here.
        patches = [patch.object(cards, "_view", return_value=self.view), patch.dict(sys.modules, {"blf": blf}),
                   patch.object(cards, "_status", side_effect=lambda x, y, kind, label: x + 60)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_preview_summary_is_plain_words_not_a_test_log(self):
        descriptor = {"changes": {"added": [{"name": "Cube"}], "modified": [], "removed": []},
                      "checks": [{"name": "object_name", "status": True}, {"name": "transform", "status": "pass"}]}
        summary, counts = cards._plain_summary(descriptor)
        self.assertEqual(counts, {"added": 1, "modified": 0, "removed": 0})
        self.assertIn("1 object added", summary)
        self.assertIn("“Cube”", summary)
        self.assertIn("All 2 checks passed", summary)
        for jargon in ("Summary:", "object(s)", "Pass", "Staged changes", "commit"):
            self.assertNotIn(jargon, summary)

    def test_internal_phase_names_become_steps(self):
        self.assertEqual(cards._step({"progress": {"label": "OPERATIONS", "completed": 2, "total": 12}})[1], "Step 2 of 12")
        self.assertEqual(cards._step({})[0], None)

    def test_an_unavailable_preview_disables_accept_and_discard_with_a_reason(self):
        state = {"hits": []}
        message = {"counts": {"added": 1}, "image_height": 0, "detail_rows": (), "preview_id": "p", "available": False,
                   "mono": 1}
        controller = types.SimpleNamespace(state={"id": "chat"})
        ctx = {"state": state, "controller": controller, "width": 593, "size": 14, "line_height": 22,
               "band": (0, 2000), "expanded": set(), "draw_image": lambda *args: False}
        cards.draw("native_preview", message, (("1 object added", 0),), ("native_preview", "p"), 400, 100, ctx)
        actions = [(action, value) for _, action, value in state["hits"]]
        self.assertEqual(actions.count((DISABLED, cards.UNAVAILABLE)), 2)
        self.assertIn(("expand", ("native_preview", "p")), actions)
        self.assertFalse(any(action in {"accept_native_preview", "discard_native_preview"} for action, _ in actions))
        state["hits"].clear()
        cards.draw("native_preview", dict(message, available=True), (), ("native_preview", "p"), 400, 100, ctx)
        actions = [(action, value) for _, action, value in state["hits"]]
        self.assertIn(("accept_native_preview", ("chat", "p")), actions)
        self.assertIn(("discard_native_preview", ("chat", "p")), actions)

    def test_card_heights_leave_room_for_the_action_row(self):
        rows = tuple(("row", 0) for _ in range(3))
        height = cards._body_height(rows, (), (), 14, 22) + cards._footer(False)
        # The last text row's bottom sits above the top of the action row (bottom + BUTTON_Y + BUTTON_H).
        self.assertGreaterEqual(height - cards._body_height(rows, (), (), 14, 22), cards.BUTTON_Y + cards.BUTTON_H + 8)

    def test_changes_pill_measures_wide_numbers(self):
        state = {"hits": []}
        cards.draw_changes_pill(state, 593, 50, 3, 0, 123456, 7890123)
        (rect, action, _), = state["hits"]
        texts = {value: (x, size) for value, x, _, size in self.view.texts}
        plus_x, minus_x = texts["+123456"][0], texts["−7890123"][0]
        self.assertGreaterEqual(minus_x - plus_x, 7 * len("+123456"), "the numbers never overlap")
        self.assertLessEqual(minus_x + 7 * len("−7890123"), rect[0] + rect[2], "both stay inside the pill")
        self.assertEqual(action, "changes")


class GroupedStepsTests(unittest.TestCase):
    def test_runs_of_one_kind_are_counted_not_repeated(self):
        from angelo_agent.approval_steps import grouped_steps
        from angelo_agent.approvals import _label, _target_name

        def _grouped_steps(operations, labels=None):
            return grouped_steps(operations, lambda kind: _label(kind).capitalize(), lambda op: _target_name(op, labels))
        operations = ([{"kind": "create_primitive", "target": None, "parameters": {"name": "Cube"}}]
                      + [{"kind": "material_principled", "target": "$m%d" % i, "parameters": {}} for i in range(32)])
        lines = _grouped_steps(operations)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], "2–33. Material principled · new object and 31 more")
        labels = {"$m0": "Red", "$m1": "Blue"}
        self.assertEqual(_grouped_steps(operations, labels)[1], "2–33. Material principled · “Red”, “Blue” and 30 more")
        same = [{"kind": "material_principled", "target": "$c", "parameters": {}}] * 2
        self.assertEqual(_grouped_steps(same, {"$c": "Cube"}), ["1–2. Material principled · “Cube”"])


ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def _function(module, name):
    import ast
    tree = ast.parse((ADDON / (module + ".py")).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


def _handled(function):
    """Every action literal ``function`` compares ``action`` with (``==`` or ``in``)."""
    import ast
    return {k.value for node in ast.walk(function) if isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name) and node.left.id == "action"
            for c in node.comparators for k in ast.walk(c) if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _routed_to_cards(action):
    """Whether ``chat_view.dispatch_action``'s branch chain hands a click on ``action`` to ``chat_cards.dispatch``:
    the first branch whose test matches it (``==``, ``in`` or ``startswith``) decides, as the code runs."""
    import ast

    def matches(test):
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "action":
            values = {k.value for c in test.comparators for k in ast.walk(c) if isinstance(k, ast.Constant)}
            return action in values
        if (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) and test.func.attr == "startswith"
                and isinstance(test.func.value, ast.Name) and test.func.value.id == "action"):
            prefixes = {k.value for a in test.args for k in ast.walk(a) if isinstance(k, ast.Constant)}
            return any(action.startswith(prefix) for prefix in prefixes)
        return False   # A test on other state (an open sheet, a module's constant): not a card action's branch.

    def to_cards(body):
        return any(isinstance(n, ast.Attribute) and n.attr == "dispatch" and isinstance(n.value, ast.Name)
                   and n.value.id == "chat_cards" for statement in body for n in ast.walk(statement))

    for statement in _function("chat_view", "dispatch_action").body:
        node = statement
        while isinstance(node, ast.If):
            if matches(node.test):
                return to_cards(node.body)
            node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    return False


class CardActionsReachTheirHandlerTests(unittest.TestCase):
    """A card control the chat draws acts only if the chat's click dispatch hands it to the cards' handler: a renamed
    or new card action outside the routed names or prefixes would be a live-looking button that does nothing."""

    def test_every_action_the_cards_handle_is_routed_to_them(self):
        handled = _handled(_function("chat_cards", "dispatch"))
        self.assertIn("script_always_allow", handled)   # The reader sees the handler's actions.
        self.assertEqual(sorted(action for action in handled if not _routed_to_cards(action)), [])

    def test_the_standing_consent_buttons_are_handled_actions(self):
        from angelo_agent import chat_cards
        controller = types.SimpleNamespace(offers_remember_script=lambda call: True,
                                           offers_session_allow=lambda call: True,
                                           offers_always_allow=lambda call: True)
        with patch.object(chat_cards, "iface_", lambda text: text):
            buttons = chat_cards.always_allow_button(controller, None)
        self.assertEqual([button[0] for button in buttons],
                         ["script_remember", "script_session_allow", "script_always_allow", "url"])
        for action, *_rest in buttons:
            if action == "url":   # The limits page: chat_view opens it (its own "url" action), never a card's.
                self.assertIn('action == "url"', (ADDON / "chat_view.py").read_text(encoding="utf-8"))
                continue
            self.assertIn(action, _handled(_function("chat_cards", "dispatch")))
            self.assertTrue(_routed_to_cards(action))


if __name__ == "__main__":
    unittest.main()
