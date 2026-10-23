# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The top-bar suites click as a person does: pointer first, press a pass later; a shelf drag moves as a person's
does: past the drag threshold still on the tile, then in small moves (qa_input, 1.0.5)."""

import ast
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ("test_blender_studio_discoverability.py", "test_blender_top_bar_permanence.py", "test_blender_flow_menu.py",
          "test_blender_studio_tabs.py")


class QaInputTests(unittest.TestCase):
    def test_the_press_comes_a_pass_after_the_pointer(self):
        source = (HERE / "qa_input.py").read_text()
        press = source.split("def press(", 1)[1]
        self.assertLess(press.index("type='MOUSEMOVE'"), press.index("def click():"), "the pointer arrives first")
        self.assertIn("bpy.app.timers.register(click, first_interval=delay)", press, "the press on a later pass")

    def test_top_bar_suites_press_through_it(self):
        for name in SUITES:
            tree = ast.parse((HERE / name).read_text())
            press = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "press")
            self.assertIn("qa_input.press(", ast.unparse(press), name)
            self.assertNotIn("LEFTMOUSE", ast.unparse(press), name)


def _qa_input():
    """``qa_input`` loaded from its file under a private name: no Blender, and nothing left in ``sys.modules``."""
    spec = importlib.util.spec_from_file_location("_qa_input_under_test", HERE / "qa_input.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DragPathTests(unittest.TestCase):
    CASES = [((34, 174), (388, 879), 1.0), ((34, 174), (388, 879), 2.0), ((400, 300), (60, 290), 1.0),
             ((500, 500), (500, 120), 1.5), ((10, 10), (14, 12), 1.0)]

    def test_arrives_presses_nudges_on_the_button_travels_in_small_moves_and_releases_at_the_end(self):
        qa = _qa_input()
        for start, end, scale in self.CASES:
            with self.subTest(start=start, end=end, scale=scale):
                events = qa.drag_path(start, end, scale)
                kinds = [(kind, value) for kind, value, _xy in events]
                press = kinds.index(('LEFTMOUSE', 'PRESS'))
                self.assertEqual(events[press][2], start)
                self.assertEqual(events[press - 1], ('MOUSEMOVE', 'NOTHING', start), "the pointer is there first")
                self.assertEqual(events[-1], ('LEFTMOUSE', 'RELEASE', end))
                self.assertEqual(kinds[press + 1:-1], [('MOUSEMOVE', 'NOTHING')] * (len(events) - press - 2))
                nudges = [xy for _k, _v, xy in events[press + 1:press + 1 + qa.NUDGES]]
                reach = qa.NUDGE * qa.NUDGES * scale + 1e-9
                self.assertTrue(all(abs(x - start[0]) <= reach and abs(y - start[1]) <= reach for x, y in nudges),
                                "the threshold is crossed still on the pressed button")
                path = [xy for _k, _v, xy in events[press:]]
                for (ax, ay), (bx, by) in zip(path, path[1:]):
                    self.assertLessEqual(max(abs(bx - ax), abs(by - ay)), qa.STEP * scale + 1e-9)
                self.assertEqual(events[-2][2], end, "the last move reaches the drop point")

    def test_the_shelf_suite_drags_through_it(self):
        tree = ast.parse((HERE / "test_blender_media_shelf.py").read_text())
        drag = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "drag"))
        self.assertIn("qa_input.drag_path(", drag)
        self.assertNotIn("LEFTMOUSE", drag)


BUTTONS = {"LEFTMOUSE", "MIDDLEMOUSE", "RIGHTMOUSE"}
MOVES = {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _event(kind, value):
    """'move', 'press', 'release' or None for one event's literal type and value."""
    if kind in MOVES:
        return "move"
    if (kind in BUTTONS or kind is None) and value in ("PRESS", "RELEASE"):   # None: a button passed in.
        return value.lower()
    return None


def hand_rolled_drags(source):
    """(function, line) of every pointer move a module sends while a button it pressed is held, following its own
    helpers: a sender (``send(kind, value)``, whose event type is a parameter) by its call's literal arguments, any
    other function by the events it sends. Events built by ``qa_input`` carry no literals and are not seen."""
    tree = ast.parse(source)
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}

    def simulated(call):
        return isinstance(call.func, ast.Attribute) and call.func.attr == "event_simulate"

    senders = {name for name, f in functions.items()
               if any(isinstance(n, ast.Call) and simulated(n)
                      and _text({k.arg: k.value for k in n.keywords}.get("type")) is None
                      for n in ast.walk(f))}
    memo = {}

    def events(function, seen=()):
        if function.name in memo:
            return memo[function.name]
        found = []
        for node in sorted((n for n in ast.walk(function) if isinstance(n, (ast.Call, ast.Tuple))),
                           key=lambda n: (n.lineno, n.col_offset)):
            if isinstance(node, ast.Tuple):
                texts = [_text(e) for e in node.elts[:2]]
                kind = _event(*texts) if len(texts) == 2 else None
            elif simulated(node):
                keywords = {k.arg: _text(k.value) for k in node.keywords}
                kind = _event(keywords.get("type"), keywords.get("value"))
            elif isinstance(node.func, ast.Name) and node.func.id in functions and node.func.id not in seen:
                if node.func.id in senders:
                    texts = [_text(a) for a in node.args] + [_text(k.value) for k in node.keywords
                                                             if k.arg in ("kind", "type", "value")]
                    texts = [t for t in texts if t is not None]
                    first = _text(node.args[0]) if node.args else None
                    kind = (_event(texts[0], texts[1] if len(texts) > 1 else "NOTHING") if first is not None
                            else _event(None, texts[0]) if texts else None)
                else:
                    found += [(e, node.lineno) for e, _line in events(functions[node.func.id],
                                                                      seen + (function.name,))]
                    continue
            else:
                kind = None
            if kind:
                found.append((kind, node.lineno))
        memo[function.name] = found
        return found

    drags = []
    for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        held = False
        for kind, line in events(function):
            if kind == "press":
                held = True
            elif kind == "release":
                held = False
            elif held:
                drags.append((function.name, line))
                break
    return drags


class NoHandRolledDragTests(unittest.TestCase):
    def test_every_suite_drags_through_qa_input(self):
        """A drag whose first move leaves the pressed button in one event never starts (the media shelf suite's
        a1c3a0dc6cf misses); qa_input.drag_path is the one place a drag's path is made."""
        files = sorted(HERE.glob("test_blender_*.py")) + sorted(p for p in HERE.glob("qa_*.py")
                                                                if p.name != "qa_input.py")
        found = {p.name: d for p in files if (d := hand_rolled_drags(p.read_text()))}
        self.assertEqual(found, {}, "drag through qa_input.drag_path (and qa_input.send)")

    def test_the_check_sees_drags_sent_directly_and_through_helpers(self):
        direct = ("def tick(w):\n    w.event_simulate(type='LEFTMOUSE', value='PRESS', x=1, y=1)\n"
                  "    for x in (5, 50):\n        w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=1)\n"
                  "    w.event_simulate(type='LEFTMOUSE', value='RELEASE', x=50, y=1)\n")
        helper = ("def move(x, y):\n    w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)\n"
                  "def drag():\n    w.event_simulate(type='LEFTMOUSE', value='PRESS', x=1, y=1)\n    move(9, 9)\n")
        sender = ("def send(kind, value='NOTHING', **m):\n    w.event_simulate(type=kind, value=value, x=1, y=1)\n"
                  "def drag(button):\n    send(button, 'PRESS')\n    send('MOUSEMOVE')\n"
                  "    send(button, 'RELEASE')\n")
        listed = "def drag():\n    return [('LEFTMOUSE', 'PRESS', p), ('MOUSEMOVE', 'NOTHING', q)]\n"
        click = ("def click(w):\n    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), "
                 "('LEFTMOUSE', 'RELEASE')):\n        w.event_simulate(type=kind, value=value, x=1, y=1)\n"
                 "    w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=2, y=2)\n")
        for source in (direct, helper, sender, listed):
            self.assertTrue(hand_rolled_drags(source), source)
        self.assertEqual(hand_rolled_drags(click), [])


if __name__ == "__main__":
    unittest.main()
