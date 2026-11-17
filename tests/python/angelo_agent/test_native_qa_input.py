# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The top-bar suites click as a person does: pointer first, press a pass later; a shelf drag moves as a person's
does: past the drag threshold still on the tile, then in small moves (qa_input, 1.0.5)."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

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
        self.assertIn("bpy.app.timers.register(click, first_interval=delay, persistent=True)", press,
                      "the press on a later pass, kept across a file load")

    def test_a_held_tick_stops_waiting_for_a_lost_press_and_says_why(self):
        """A press whose timer is gone (never sent) holds a suite's tick at most AIM_CAP, then the tick runs and
        ``lost`` says why (the review of 69d589a, NIT: it used to spin until the runner's timeout)."""
        qa = _qa_input()
        clock = [100.0]
        qa.time = type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])})
        ran = []
        tick = qa.after_clicks(lambda: ran.append(True) or 0.5)
        qa._on_the_way[0] = 1   # Registered, never sent.
        self.assertEqual(tick(), 0.02)
        clock[0] += qa.AIM_CAP + 1
        self.assertEqual(tick(), 0.5)
        self.assertEqual(ran, [True])
        self.assertEqual(qa.pending(), 0)
        self.assertTrue(qa.lost and "never sent" in qa.lost[0])

    def test_top_bar_suites_press_through_it(self):
        for name in SUITES:
            tree = ast.parse((HERE / name).read_text())
            press = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "press")
            self.assertIn("qa_input.press(", ast.unparse(press), name)
            self.assertNotIn("LEFTMOUSE", ast.unparse(press), name)

    def test_every_shielded_chat_control_is_aimed_through_the_window(self):
        """A shielded grant on the chat canvas (grant_guard.SHIELDED) mints its C ticket only from an aimed press
        through the window, so suites that click it pass it to window_click (GRANT_ACTIONS) and aim (AIMED). Review
        was missing (crc13b: agent_workspace_ui's Details never opened). The card host's own accept/confirm are not
        chat actions."""
        tree = ast.parse((HERE.parents[2] / "scripts/addons_core/angelo_agent/grant_guard.py").read_text())
        shielded = next(ast.literal_eval(node.value.args[0]) for node in tree.body if isinstance(node, ast.Assign)
                        and getattr(node.targets[0], "id", "") == "SHIELDED")
        qa = _qa_input()
        chat = set(shielded) - {"confirm", "accept"}
        self.assertTrue(chat)
        self.assertLessEqual(chat, set(qa.AIMED))
        self.assertLessEqual(set(qa.AIMED), set(qa.GRANT_ACTIONS))


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


def _simulated(statement, kind=None, value=None):
    """Whether ``statement`` is one ``event_simulate`` call (of ``kind`` and ``value`` when given)."""
    call = statement.value if isinstance(statement, ast.Expr) else None
    if not (isinstance(call, ast.Call) and ast.unparse(call.func).endswith("event_simulate")):
        return False
    words = {k.arg: ast.unparse(k.value) for k in call.keywords}
    return (kind is None or words.get("type") == repr(kind)) and (value is None or words.get("value") == repr(value))


def _same_pass(function):
    """Whether ``function`` sends the pointer's move and then a press in one pass: back to back in one block, or one
    loop over a tuple of events holding both."""
    for node in ast.walk(function):
        for body in (getattr(node, name, None) for name in ("body", "orelse")):
            if isinstance(body, list) and any(_simulated(a, "MOUSEMOVE") and _simulated(b, value="PRESS")
                                              for a, b in zip(body, body[1:])):
                return True
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            items = [ast.unparse(e) for e in node.iter.elts]
            if any("'MOUSEMOVE'" in e for e in items) and any("'PRESS'" in e for e in items):
                return True
    return False


ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"


def granting_actions():
    """Every Angelo surface action whose dispatch branch reaches a guarded operator: through the person's click path
    (``grant_guard.clicked``, ``stand_in``) or by invoking one of the audited operators directly (Voice), read from
    the add-on itself."""
    import re
    audit = ast.parse((HERE / "test_native_grant_operators.py").read_text(encoding="utf-8"))
    AUDIT = next(ast.literal_eval(node.value) for node in audit.body   # Read, not imported: no stand-in modules.
                 if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "AUDIT")
    invoked = re.compile(r"ops\.angelo\.(%s)\(" % "|".join(name.split(".", 1)[1] for name in AUDIT))
    found = set()
    for path in sorted(ADDON.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.If):
                continue
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            if "clicked(" not in body and "stand_in(" not in body and not invoked.search(body):
                continue
            for test in (n for n in ast.walk(node.test) if isinstance(n, ast.Compare)):
                if isinstance(test.left, ast.Name) and test.left.id == "action":
                    for item in test.comparators:
                        values = item.elts if isinstance(item, (ast.Tuple, ast.Set, ast.List)) else [item]
                        found |= {v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)}
    return found


def unrouted_chat_clicks(source):
    """Chat click helpers that send a same-pass click (themselves or through a small helper) without sending the
    actions that reach a grant through ``qa_input.press`` (``GRANT_ACTIONS``): ``[(name, line)]``."""
    functions = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)]
    small = {f.name for f in functions if _same_pass(f) and len(f.body) <= 4}
    found = []
    for function in functions:
        text = ast.unparse(function)
        if "chat_point" not in text and "chat_rect" not in text:
            continue
        calls = {c.func.id for c in ast.walk(function) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if (_same_pass(function) or calls & small) and "GRANT_ACTIONS" not in text:
            found.append((function.name, function.lineno))
    return found


class GrantClicksAreAPersonsTests(unittest.TestCase):
    """A chat control that reaches a grant takes a press only after the person's real move onto it once it has been
    drawn ``ARM_SECONDS`` (002711ed58f; doc/angelo/grant_operators.rst). Suites whose helpers clicked the welcome's
    Connect with the move and the press in one pass, as it was drawn, never connected (gcp-kit's Mac round at
    02bfa33: 13 chat suites and prefs_and_quit). Such actions go through ``qa_input.press``; every other click keeps
    its own pace."""

    def test_every_action_that_reaches_a_grant_is_a_grant_action(self):
        import qa_input
        derived = granting_actions()
        self.assertTrue({"welcome_connect", "approve"} <= derived, derived)   # The reader finds what it should.
        self.assertEqual(derived - qa_input.GRANT_ACTIONS, set())

    def test_every_chat_click_helper_sends_grant_actions_as_a_person(self):
        found = {}
        for path in sorted(HERE.glob("*.py")):
            if path.name in ("qa_input.py", "test_native_qa_input.py"):
                continue
            if hits := unrouted_chat_clicks(path.read_text(encoding="utf-8")):
                found[path.name] = hits
        self.assertEqual(found, {}, "route qa_input.GRANT_ACTIONS through qa_input.press")

    def test_the_check_sees_an_unrouted_helper_and_not_a_routed_one(self):
        raw = ("def click(action):\n    x, y = chat_point(region, rect)\n"
               "    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):\n"
               "        w.event_simulate(type=kind, value=value, x=x, y=y)\n")
        routed = raw.replace("    for kind", "    if action in qa_input.GRANT_ACTIONS:\n"
                             "        return qa_input.press(w, x, y)\n    for kind")
        through = ("def press(x, y):\n    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS')):\n"
                   "        w.event_simulate(type=kind, value=value, x=x, y=y)\n"
                   "def click(action):\n    press(*chat_point(region, rect))\n")
        self.assertEqual(unrouted_chat_clicks(raw), [("click", 1)])
        self.assertEqual(unrouted_chat_clicks(routed), [])
        self.assertEqual(unrouted_chat_clicks(through), [("click", 4)])


if __name__ == "__main__":
    unittest.main()
