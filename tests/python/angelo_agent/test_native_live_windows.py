# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Angelo walk over windows and areas goes through ``angelo_live_windows``, without Blender.

A window has no screen for a moment while an operator creates or closes it, and a draw callback that reads
``window.screen.areas`` then raises and loses its frame. Evidence: the 2026-10-22 draw-path audit at 9af9c2156f7
found corner Angelo's draw asking ``chat_toggle.chat_state`` about every window, one of about 170 such reads; no crash
was seen. One site's guard (fc38bb23712) would have fixed that read alone, so the class is fixed here instead: one
helper that yields only windows with a screen, and the source rule below that fails on any direct ``.windows`` or
``.areas`` read in Angelo's own modules.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "addons_core"))
sys.path.insert(0, str(SCRIPTS / "modules"))

import angelo_live_windows as live  # noqa: E402

HELPER = SCRIPTS / "modules/angelo_live_windows.py"
WALKED = {"windows", "areas"}          # WindowManager.windows, Screen.areas
HELPER_NAMES = {"live_windows", "angelo_live_windows"}
BY_NAME = {"getattr", "hasattr", "path_resolve", "attrgetter"}   # Reads that name the attribute as a string.


def angelo_sources():
    """Angelo's own Python: the agent add-on, its module-path packages, the startup layout and the launch scripts."""
    found = set(SCRIPTS.glob("addons_core/angelo_agent/**/*.py"))
    found |= set(SCRIPTS.glob("modules/_console_angelo*.py")) | set(SCRIPTS.glob("modules/angelo_*.py"))
    found |= set(SCRIPTS.glob("modules/angelo_*/**/*.py"))
    found |= set(SCRIPTS.glob("startup/angelo_*.py")) | set(SCRIPTS.glob("angelo/**/*.py"))
    return sorted(path for path in found if path != HELPER)


def direct_walks(source):
    """``(line, text)`` of every ``.windows``/``.areas`` read not made through the helper, and every read that names
    them as a string (``getattr(x, "windows")``, ``x.path_resolve("areas")``, ``attrgetter("windows")``)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in WALKED:
            if not (isinstance(node.value, ast.Name) and node.value.id in HELPER_NAMES):
                found.append((node.lineno, ast.unparse(node)))
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if name in BY_NAME and any(isinstance(arg, ast.Constant) and arg.value in WALKED for arg in node.args):
                found.append((node.lineno, ast.unparse(node)))
    return found


def timer_lookups(source):
    """``(line, name)`` of every function handed to ``bpy.app.timers.register`` (or a lambda handed to it) that looks
    its window up with a live ``live_windows.find`` itself: such a step gives up on a window between screens. It is
    built with ``live_windows.deferred`` instead, which waits (bounded) and passes the window in."""
    tree = ast.parse(source)
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)

    def called_name(node):   # ``name``, ``self.name`` or ``partial(name, ...)``: the module-local function meant.
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
            return node.attr
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "partial":
            return called_name(node.args[0]) if node.args else None
        return None

    def finds_itself(node):   # A live ``live_windows.find`` (only ``live=False`` asks the other question).
        return any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "find"
                   and isinstance(call.func.value, ast.Name) and call.func.value.id in HELPER_NAMES
                   and not any(keyword.arg == "live" and isinstance(keyword.value, ast.Constant)
                               and keyword.value.value is False for keyword in call.keywords)
                   for call in ast.walk(node))

    def looks_up(node):   # Itself, or through a module-local helper it calls (one level).
        if finds_itself(node):
            return True
        helpers = {called_name(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
        return any(finds_itself(helper) for name in helpers if name for helper in functions.get(name, ()))

    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "timers" and node.args):
            step = node.args[0]
            if isinstance(step, ast.Lambda):
                if looks_up(step):
                    found.append((node.lineno, "lambda"))
                continue
            name = called_name(step)
            if any(looks_up(function) for function in functions.get(name, ())):
                found.append((node.lineno, name))
    return found


# What creates a chat or a window: Angelo's own openers (the chat dock, Director's layout, Studio) and Blender's
# window operators. A step calling one of them must see every window first.
OPENS = {"toggle_chat", "open_chat", "request_chat_dock", "request_mode", "open_studio", "new_window", "area_dupli",
         "window_new", "userpref_show"}
CONSULTS = {"settled", "chat_state"}   # A plain timer that opens asks one of these (chat_state answers "changing").


def unsettled_openers(source):
    """``(line, step)`` of every timer step that may open a chat or a window (it calls one of ``OPENS``, itself or
    through module-local helpers two levels down) without first seeing every window: a ``live_windows.deferred``
    step needs ``settle=True``; a plain ``timers.register`` step must consult ``settled`` or ``chat_state``.
    Otherwise a chat or window between screens would not count, and a second one would open."""
    tree = ast.parse(source)
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)

    def names(node):
        return {getattr(call.func, "attr", getattr(call.func, "id", None)) for call in ast.walk(node)
                if isinstance(call, ast.Call)}

    def reaches(node, wanted, depth=2):
        called = names(node)
        return bool(called & wanted) or depth > 0 and any(
            reaches(helper, wanted, depth - 1) for name in called for helper in functions.get(name, ()))

    def steps(node):   # The functions a step argument stands for: a name, self.name, partial(name, ...), a lambda.
        if isinstance(node, ast.Lambda):
            return [node]
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "partial":
            return steps(node.args[0]) if node.args else []
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        return functions.get(name, [])
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "deferred" and len(node.args) >= 3:
            settled = any(k.arg == "settle" and getattr(k.value, "value", None) is True for k in node.keywords)
            if not settled and any(reaches(step, OPENS) for step in steps(node.args[2])):
                found.append((node.lineno, ast.unparse(node.args[2])[:40]))
        elif (node.func.attr == "register" and getattr(node.func.value, "attr", getattr(node.func.value, "id", None))
              == "timers" and node.args and not (isinstance(node.args[0], ast.Call)
                                                 and getattr(node.args[0].func, "attr", None) == "deferred")):
            if any(reaches(step, OPENS) and not reaches(step, CONSULTS, 0) for step in steps(node.args[0])):
                found.append((node.lineno, ast.unparse(node.args[0])[:40]))
    return found


def area(kind="VIEW_3D", pointer=0):
    return SimpleNamespace(type=kind, as_pointer=lambda: pointer)


def window(pointer, *areas, temporary=False, screen=True):
    shown = SimpleNamespace(areas=list(areas), is_temporary=temporary) if screen else None
    return SimpleNamespace(screen=shown, as_pointer=lambda: pointer, workspace=None)


def opening(pointer=99):
    """The window an operator is creating or closing: in the list, without a screen."""
    return window(pointer, screen=False)


class SourceRuleTests(unittest.TestCase):
    def test_no_angelo_module_walks_windows_or_areas_itself(self):
        sources = angelo_sources()
        self.assertGreater(len(sources), 600, "the scan finds the add-on")
        bypasses = ["%s:%d: %s" % (path.relative_to(ROOT), line, text)
                    for path in sources for line, text in direct_walks(path.read_text(encoding="utf-8"))]
        self.assertEqual(bypasses, [], "walk windows and areas through live_windows (scripts/modules/"
                                       "angelo_live_windows.py), which skips a window without a screen")

    def test_the_rule_catches_each_kind_of_bypass(self):
        for text in ("bpy.context.window_manager.windows", "for a in window.screen.areas: pass",
                     "screen.areas[0]", "context.window.screen.areas", "getattr(wm, 'windows', ())",
                     "manager.windows[0]", "x = [w for w in wm.windows if w.screen]",
                     "wm.path_resolve('windows')", "operator.attrgetter('areas')(screen)",
                     "live = wm; live.windows"):
            self.assertTrue(direct_walks(text), text)
        for text in ("live_windows.windows(wm)", "live_windows.areas(window)", "usage['windows']",
                     "def windows(): pass", "windows = []"):
            self.assertFalse(direct_walks(text), text)

    def test_no_timer_step_looks_its_window_up_itself(self):
        found = ["%s:%d: %s" % (path.relative_to(ROOT), line, name)
                 for path in angelo_sources() for line, name in timer_lookups(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "build a timer step for one window with live_windows.deferred")

    def test_the_timer_rule_catches_a_step_that_gives_up(self):
        bad = ("def f(p):\n    def run():\n        w = live_windows.find(wm, p)\n        return None\n"
               "    bpy.app.timers.register(run, first_interval=0)\n")
        self.assertEqual(timer_lookups(bad), [(5, "run")])
        self.assertTrue(timer_lookups("bpy.app.timers.register(lambda: go(live_windows.find(wm, p)))"))
        helper = "def _w(p):\n    return live_windows.find(wm, p)\n"
        for registered in ("def run():\n    _w(1)\nbpy.app.timers.register(run)\n",                  # Through a helper.
                           "def run(p):\n    _w(p)\nbpy.app.timers.register(functools.partial(run, 1))\n",
                           "class C:\n    def run(self):\n        _w(1)\n    def go(self):\n"
                           "        bpy.app.timers.register(self.run)\n",
                           "bpy.app.timers.register(lambda: _w(1))\n",
                           "def run():\n    live_windows.find(wm, 1, live=True)\nbpy.app.timers.register(run)\n"):
            self.assertTrue(timer_lookups(helper + registered), registered)
        self.assertFalse(timer_lookups("def run():\n    live_windows.find(wm, 1, live=False)\n"
                                       "bpy.app.timers.register(run)\n"), "asks gone-or-between, which is allowed")
        good = ("def f(p):\n    def run(w):\n        return None\n"
                "    bpy.app.timers.register(live_windows.deferred(lambda: wm, p, run), first_interval=0)\n")
        self.assertEqual(timer_lookups(good), [])

    def test_a_step_that_may_open_a_chat_or_window_waits_for_every_window(self):
        found = ["%s:%d: %s" % (path.relative_to(ROOT), line, name)
                 for path in angelo_sources() for line, name in unsettled_openers(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "give live_windows.deferred settle=True")
        bad = "def run(w):\n    bpy.ops.angelo.toggle_chat()\nx = live_windows.deferred(m, p, run)\n"
        self.assertEqual(unsettled_openers(bad), [(3, "run")])
        self.assertEqual(unsettled_openers(bad.replace("run)", "run, settle=True)")), [])
        through = "def go():\n    studio_toggle.open_studio(w)\ndef run(w):\n    go()\nx = live_windows.deferred(m, p, run)\n"
        self.assertEqual(unsettled_openers(through), [(5, "run")], "through a helper")
        self.assertTrue(unsettled_openers("x = live_windows.deferred(m, p, lambda w: ui.request_chat_dock(w))\n"))
        self.assertTrue(unsettled_openers("x = live_windows.deferred(m, p, functools.partial(director.request_mode, 1))\n"
                                          "def request_mode(w):\n    bpy.ops.screen.area_dupli()\n"))
        plain = "def tick():\n    bpy.ops.wm.window_new()\nbpy.app.timers.register(tick)\n"
        self.assertEqual(unsettled_openers(plain), [(3, "tick")], "a plain timer that opens a window")
        self.assertEqual(unsettled_openers(plain.replace("    bpy.ops", "    if live_windows.settled(wm): bpy.ops")), [])
        self.assertFalse(unsettled_openers("def tick():\n    card.show()\nbpy.app.timers.register(tick)\n"),
                         "showing a card opens no window")

    def test_tracked_windows_are_never_matched_against_live_windows_only(self):
        """A set or dict of window pointers built from ``windows``/``window_areas`` drops a window between screens
        as if it were gone (a tracked Studio window was forgotten for good this way); ``every_window``, ``find(...,
        live=False)`` or ``prune`` keep it."""
        def bad(source):
            return [node.lineno for node in ast.walk(ast.parse(source))
                    if isinstance(node, (ast.SetComp, ast.DictComp))
                    and "as_pointer" in ast.unparse(node.key if isinstance(node, ast.DictComp) else node.elt)
                    and any(isinstance(gen.iter, ast.Call) and getattr(gen.iter.func, "attr", None)
                            in {"windows", "window_areas"} for gen in node.generators)]
        self.assertEqual(bad("live = {w.as_pointer(): w for w in live_windows.windows(wm)}"), [1])
        self.assertEqual(bad("seen = {w.as_pointer() for w in live_windows.every_window(wm)}"), [])
        found = ["%s:%s" % (path.relative_to(ROOT), line) for path in angelo_sources()
                 for line in bad(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "track windows through live_windows.prune or every_window")

    def test_a_screen_is_read_only_where_it_is_known_to_exist(self):
        """``x.screen.attr`` (not ``bpy.ops.screen``) only in a function that checks ``.screen is (not) None`` or takes
        its windows from ``live_windows.windows``/``first``/a live ``find``: a window between screens has no screen.
        The handler's own ``context`` (``context.screen``, ``context.window.screen``) is exempt: Blender runs a draw or
        an operator in a window that has its screen; ``bpy.context`` in a timer is not."""
        def unguarded(source):
            found = []
            for function in ast.walk(ast.parse(source)):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                    continue
                text = ast.unparse(function)
                live_find = any(isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "find"
                                and ast.unparse(call.func).startswith("live_windows.")
                                and not any(k.arg == "live" for k in call.keywords) for call in ast.walk(function))
                guarded = (".screen is None" in text or ".screen is not None" in text or live_find
                           or any(f"live_windows.{name}(" in text for name in ("windows", "first")))
                body = function.body
                for node in (n for statement in body for n in ast.walk(statement)):
                    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                            and node.value.attr == "screen" and ast.unparse(node.value) != "bpy.ops.screen"
                            and ast.unparse(node.value) not in {"context.screen", "context.window.screen"}
                            and not guarded):
                        found.append((node.lineno, ast.unparse(node)))
            return sorted(set(found))
        self.assertTrue(unguarded("def f(window):\n    window.screen.show_statusbar = False\n"))
        self.assertFalse(unguarded("def f(window):\n    if window.screen is not None:\n"
                                   "        window.screen.show_statusbar = False\n"))
        self.assertFalse(unguarded("def f():\n    bpy.ops.screen.area_close()\n"))
        self.assertFalse(unguarded("def draw(self, context):\n    context.screen.show_statusbar\n"), "its own context")
        self.assertTrue(unguarded("def tick():\n    bpy.context.window.screen.areas_x\n"), "a timer's bpy.context")
        self.assertTrue(unguarded("def f(wm):\n    w = live_windows.find(wm, 1, live=False)\n    w.screen.areas_x\n"),
                        "find(live=False) can return a window between screens: no guard")
        found = ["%s:%d: %s" % (path.relative_to(ROOT), line, text) for path in angelo_sources()
                 for line, text in unguarded(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "check window.screen first, or take the window from live_windows")

    def test_chat_state_always_sees_every_window(self):
        bad = []
        for path in angelo_sources():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "chat_state"
                        and any(isinstance(sub, ast.Attribute) and sub.attr == "windows"
                                for arg in node.args[1:] for sub in ast.walk(arg))):
                    bad.append("%s:%d" % (path.relative_to(ROOT), node.lineno))
        self.assertEqual(bad, [], "pass live_windows.every_window: a live-only list hides a window between screens")

    def test_the_agent_reaches_the_same_module(self):
        from angelo_agent import live_windows
        self.assertIs(live_windows, live)


class HelperTests(unittest.TestCase):
    def manager(self, *windows):
        return SimpleNamespace(windows=list(windows))

    def test_a_window_without_a_screen_is_never_yielded(self):
        main, prefs = window(1, area()), window(2, area("PREFERENCES"), temporary=True)
        manager = self.manager(opening(), main, opening(98), prefs)
        self.assertEqual(live.windows(manager), [main, prefs])
        self.assertEqual(live.windows(manager, temporary=False), [main])
        self.assertIs(live.first(manager), main)
        self.assertIs(live.first(self.manager(opening(), prefs), temporary=False), None)
        self.assertEqual([(w.as_pointer(), a.type) for w, a in live.window_areas(manager)],
                         [(1, "VIEW_3D"), (2, "PREFERENCES")])

    def test_find_skips_a_window_without_a_screen(self):
        main = window(1, area())
        manager = self.manager(opening(5), main)
        self.assertIs(live.find(manager, 1), main)
        self.assertIsNone(live.find(manager, 5), "the opening window is not usable yet")
        self.assertIsNone(live.find(manager, 7))
        self.assertIsNone(live.find(manager, 0))
        self.assertIsNone(live.find(manager, None))

    def test_areas_are_empty_without_a_window_or_screen(self):
        view = area()
        self.assertEqual(live.areas(window(1, view)), [view])
        self.assertEqual(live.areas(opening()), [])
        self.assertEqual(live.areas(None), [])
        self.assertEqual(live.screen_areas(None), [])

    def test_no_window_manager(self):
        self.assertEqual(live.windows(None), [])
        self.assertEqual(live.every_window(None), [])
        self.assertTrue(live.settled(None))
        self.assertIsNone(live.first(None))
        self.assertIsNone(live.find(None, 1))
        self.assertFalse(live.between_screens(None, 1))
        self.assertEqual(live.window_areas(None), [])

    def test_gone_and_between_screens_are_told_apart(self):
        main, changing = window(1, area()), opening(5)
        manager = self.manager(main, changing)
        self.assertEqual(live.every_window(manager), [main, changing], "guards and cursors see every window")
        self.assertFalse(live.settled(manager))
        self.assertTrue(live.settled(self.manager(main)))
        self.assertIs(live.find(manager, 5, live=False), changing)
        self.assertTrue(live.between_screens(manager, 5), "deferred work for it waits")
        self.assertFalse(live.between_screens(manager, 1), "live: no wait")
        self.assertFalse(live.between_screens(manager, 7), "gone: give up")
        self.assertTrue(live.screenless(changing))
        self.assertFalse(live.screenless(main))
        self.assertFalse(live.screenless(None))

    @staticmethod
    def feed(wait, now, until, waiting=True, every=0.5):
        """Feed ``wait`` twice a second (as the watch does) up to ``until``; returns its last answer."""
        answer = None
        while now[0] < until:
            answer = wait(waiting)
            now[0] = round(now[0] + every, 6)
        return answer

    def test_waits_are_bounded(self):
        now = [0.0]
        wait = live.Wait(2.0, clock=lambda: now[0])
        self.assertFalse(wait(False))
        self.assertTrue(self.feed(wait, now, 2.0), "still within the limit at 1.5 s")
        self.assertFalse(wait(True), "past the limit the caller stops waiting")
        self.assertFalse(wait(False))
        self.assertTrue(wait(True), "a new wait starts afresh")

    def test_a_deferred_step_waits_out_a_window_between_screens(self):
        now, seen = [0.0], []
        changing = opening(5)
        manager = self.manager(changing)
        step = live.deferred(lambda: manager, 5, lambda w: seen.append(w) or "next", clock=lambda: now[0])
        self.assertEqual(step(), live.RETRY_S)
        self.assertEqual(seen, [])
        changing.screen = SimpleNamespace(areas=[], is_temporary=False)
        self.assertEqual(step(), "next", "the step's own interval")
        self.assertEqual(seen, [changing])
        manager.windows.clear()
        step()
        self.assertIsNone(seen[-1], "gone: the step hears None at once")

    def test_a_deferred_step_never_waits_for_good(self):
        now, seen = [0.0], []
        manager = self.manager(opening(5))
        step = live.deferred(lambda: manager, 5, seen.append, clock=lambda: now[0])
        while now[0] < live.WAIT_S:   # Looked at every RETRY_S, as the timer does.
            self.assertEqual(step(), live.RETRY_S)
            now[0] += live.RETRY_S
        step()
        self.assertEqual(seen, [None], "a window that never gets a screen counts as gone")

    def test_a_wait_left_from_an_earlier_moment_never_cuts_a_new_one_short(self):
        now = [0.0]
        wait = live.Wait(2.0, clock=lambda: now[0])
        self.assertTrue(wait(True))   # At startup, then not fed again (the caller's branch is no longer reached).
        now[0] = 600.0
        self.assertTrue(wait(True), "a new moment between screens waits its own full limit")
        self.assertTrue(self.feed(wait, now, 602.0))
        self.assertFalse(wait(True))

    def test_a_tracked_window_between_screens_is_kept(self):
        studio, other = window(1, area("CONSOLE")), window(2, area())
        changing = opening(3)
        manager = self.manager(studio, other, changing)
        tracked = {1, 2, 3, 4}
        first = live.prune(live.every_window(manager), tracked, lambda w: any(a.type == "CONSOLE" for a in live.areas(w)))
        self.assertEqual(tracked, {1, 3}, "gone (4) and repurposed (2) are forgotten; between screens (3) is kept")
        self.assertIn(first, (studio, changing))
        changing.screen = SimpleNamespace(areas=[area("CONSOLE")], is_temporary=False)
        self.assertEqual(live.prune(manager.windows, tracked, lambda w: True) and tracked, {1, 3},
                         "still tracked once settled (window_manager.windows works too)")

    def test_settle_waits_for_every_window(self):
        now, seen = [0.0], []
        main = window(1, area())
        manager = self.manager(main, opening(5))
        step = live.deferred(lambda: manager, 1, seen.append, settle=True, clock=lambda: now[0])
        self.assertEqual(step(), live.RETRY_S, "another window between screens could hold the chat")
        manager.windows.pop()
        step()
        self.assertEqual(seen, [main])

    def test_never_imports_bpy_or_the_agent(self):
        imports = {alias.name for node in ast.walk(ast.parse(HELPER.read_text()))
                   if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        imports |= {node.module for node in ast.walk(ast.parse(HELPER.read_text()))
                    if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(any(name.split(".")[0] in {"bpy", "angelo_agent"} for name in imports), imports)


def load(name):
    """``angelo_agent.<name>`` with a bare ``bpy`` (the toggle's own suite does the same)."""
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Panel=object)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(handlers=handlers, background=False)
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types, "bpy.app": bpy.app,
                                       "bpy.app.handlers": handlers}):
        sys.modules.pop("angelo_agent." + name, None)
        module = __import__("angelo_agent." + name, fromlist=["_"])
    return module, bpy


class CallSiteTests(unittest.TestCase):
    """The draw and watch paths the audit named, each with a window an operator is opening in the list."""

    def test_tools_never_run_in_a_window_between_screens_and_never_spin_while_waiting(self):
        source = (SCRIPTS / "addons_core/angelo_agent/ui.py").read_text()
        pump = source.split("\ndef pump(", 1)[1].split("\ndef ", 1)[0]
        dispatch = pump.index("pump_controller_tools(")
        self.assertIn("held = _tools_wait(not live_windows.settled(bpy.context.window_manager)) or "
                      "live_windows.screenless(main_window())", pump[:dispatch], "fed every tick; never a screen-less main window")
        self.assertIn("if window and controllers and not held and not tools_wait():", pump[:dispatch])
        self.assertIn("return live_windows.RETRY_S if held else 0.001", pump)
        self.assertIn("live=False", source.split("\ndef main_window(", 1)[1].split("\ndef ", 1)[0],
                      "the docked window, never a substitute")

    def test_the_web_preview_keeps_its_window_and_waits_out_its_screen(self):
        source = (SCRIPTS / "addons_core/angelo_agent/web_preview.py").read_text()
        self.assertIn('live_windows.find(wm, _state["window"], live=False)', source)
        tick = source.split("\ndef _tick(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(tick.index("window.screen is None"), tick.index("refresh(window)"))

    def test_the_chat_wait_sees_a_tool_in_every_window(self):
        source = (SCRIPTS / "addons_core/angelo_agent/marble_host.py").read_text()
        self.assertIn("live_windows.every_window(", source.split("\ndef paused(", 1)[1].split("\ndef ", 1)[0])

    def test_the_toggle_never_opens_a_second_chat_while_a_window_is_changing(self):
        source = (SCRIPTS / "addons_core/angelo_agent/chat_toggle.py").read_text()
        execute = source.split("    def execute(self, context):", 1)[1].split("\n    def ", 1)[0]
        self.assertLess(execute.index('if state == "changing":'), execute.index("show(here)"))
        drawn = source.split("\ndef _dock_when_drawn(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(drawn.index('state == "changing"'), drawn.index("request_chat_dock("))

    def test_a_tool_running_in_a_window_between_screens_is_still_work(self):
        from angelo_agent import input_policy, work_guard
        busy = SimpleNamespace(screen=None, as_pointer=lambda: 5, modal_operators=["TRANSFORM_OT_translate"])
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[busy])))
        with mock.patch.object(input_policy, "active_interactions", lambda w: list(w.modal_operators)):
            self.assertTrue(work_guard._interaction(bpy), "a guard never reads a window between screens as idle")

    def test_corner_angelo_and_the_toggle_read_a_list_with_an_opening_window(self):
        toggle, bpy = load("chat_toggle")
        chat = area("CONSOLE", 3)
        main = window(1, area(), chat)
        bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[opening(), main]))
        with mock.patch.object(toggle.console, "is_chat", lambda a: a.type == "CONSOLE"):
            self.assertEqual(toggle.chat_state(main, live.windows(bpy.context.window_manager)), ("docked", main, chat))
            # Even a raw list (a caller outside this rule, a test) never raises on the opening window.
            self.assertEqual(toggle.chat_state(main, [opening(), main])[0], "docked", "here: certain")
            other = window(2, area())
            self.assertEqual(toggle.chat_state(other, [other, main])[0], "popped")
            self.assertEqual(toggle.chat_state(other, [other, opening()])[0], "changing",
                             "a window between screens could hold the chat: never 'hidden'")
            self.assertEqual(toggle.chat_state(other, [other])[0], "hidden")

    def test_the_first_time_hint_finds_the_main_window_past_an_opening_one(self):
        hint, bpy = load("chat_hint")
        main = window(1, area())
        bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[opening(), main]))
        sys.modules["bpy"], saved = bpy, sys.modules.get("bpy")
        try:
            self.assertIs(hint.main_window(), main)
        finally:
            if saved is None:
                sys.modules.pop("bpy", None)
            else:
                sys.modules["bpy"] = saved


if __name__ == "__main__":
    unittest.main()
