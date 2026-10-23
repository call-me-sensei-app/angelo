# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No Angelo modal handler keeps what is not its input, on a stand-in bpy (``input_policy``).

Blender quits through a handler in front of the window's modal handlers that waits for one motionless pointer move
(``wm_exit_schedule_delayed``). A modal handler added after the quit was requested sits in front of it: the welcome
screen opening on a timer kept that move (and every other event over the 3D view), so the quit never came while the
pointer rested there. Covered: the policy itself (what is not input, Blender's hover refresh, the person's quit key in
any key map); the welcome screen and the not-connected showcase passing those on while still using the move for
their hover and still taking their own clicks and keys; a closed or displaced handler passing its last event on;
and every modal handler in the add-on consulting the policy. The running app is ``test_blender_prefs_and_quit``;
the notice cards' rules are in ``test_native_card_host``.
"""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import input_policy  # noqa: E402
from notices_fakes import Notices  # noqa: E402

START, DEMO = (500, 300, 200, 40), (500, 400, 200, 40)


def event(kind, value='NOTHING', x=600, y=320, moved=True, **modifiers):
    """A window event; a move ``moved`` from one pixel away, else Blender's motionless hover refresh."""
    keys = dict(shift=False, ctrl=False, alt=False, oskey=False)
    keys.update(modifiers)
    return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y, mouse_prev_x=x - 1 if moved else x,
                           mouse_prev_y=y, **keys)


def keyconfigs(*items):
    """``WindowManager.keyconfigs`` whose user key map Window holds ``items`` (Ctrl+Q quits by default)."""
    items = items or (quit_item(),)
    return SimpleNamespace(user=SimpleNamespace(keymaps={"Window": SimpleNamespace(keymap_items=list(items))}))


def quit_item(key='Q', active=True, value='PRESS', **modifiers):
    keys = dict(shift=0, ctrl=1, alt=0, oskey=0)
    keys.update(modifiers)
    return SimpleNamespace(idname="wm.quit_blender", active=active, type=key, value=value, **keys)


class PolicyTests(unittest.TestCase):
    def test_timers_and_window_events_are_not_input(self):
        for kind in ("TIMER", "TIMER0", "TIMER2", "TIMER_JOBS", "TIMER_AUTOSAVE", "TIMER_REPORT", "TIMERREGION",
                     "WINDOW_DEACTIVATE", "NONE"):
            self.assertTrue(input_policy.not_input(event(kind)), kind)
        for kind in ("MOUSEMOVE", "LEFTMOUSE", "Q", "ESC", "TAB", "WHEELUPMOUSE", "TEXTINPUT"):
            self.assertFalse(input_policy.not_input(event(kind)), kind)

    def test_a_move_without_motion_is_blenders_refresh(self):
        self.assertTrue(input_policy.refresh_move(event('MOUSEMOVE', moved=False)))
        self.assertFalse(input_policy.refresh_move(event('MOUSEMOVE')), "the person moved the pointer")
        self.assertFalse(input_policy.refresh_move(event('LEFTMOUSE', 'PRESS', moved=False)))
        self.assertFalse(input_policy.refresh_move(SimpleNamespace(type='MOUSEMOVE', mouse_x=1, mouse_y=1)))

    def test_the_quit_key_is_the_persons_own(self):
        wm = SimpleNamespace(keyconfigs=keyconfigs())
        self.assertTrue(input_policy.quit_shortcut(event('Q', 'PRESS', ctrl=True), wm))
        self.assertFalse(input_policy.quit_shortcut(event('Q', 'PRESS'), wm), "Q alone is the Quick Favorites menu")
        self.assertFalse(input_policy.quit_shortcut(event('Q', 'PRESS', ctrl=True, shift=True), wm))
        self.assertFalse(input_policy.quit_shortcut(event('Q', 'RELEASE', ctrl=True), wm))
        rebound = SimpleNamespace(keyconfigs=keyconfigs(quit_item('W', ctrl=0, oskey=1)))
        self.assertTrue(input_policy.quit_shortcut(event('W', 'PRESS', oskey=True), rebound))
        self.assertFalse(input_policy.quit_shortcut(event('Q', 'PRESS', ctrl=True), rebound))
        anything = SimpleNamespace(keyconfigs=keyconfigs(quit_item(shift=-1, ctrl=-1, alt=-1, oskey=-1)))
        self.assertTrue(input_policy.quit_shortcut(event('Q', 'PRESS', alt=True), anything))
        off = SimpleNamespace(keyconfigs=keyconfigs(quit_item(active=False)))
        self.assertFalse(input_policy.quit_shortcut(event('Q', 'PRESS', ctrl=True), off))
        self.assertFalse(input_policy.quit_shortcut(event('Q', 'PRESS', ctrl=True), SimpleNamespace()))

    def test_an_overlay_passes_what_is_not_input_and_the_quit_key(self):
        wm = SimpleNamespace(keyconfigs=keyconfigs())
        self.assertTrue(input_policy.overlay_passes(event('TIMER'), wm))
        self.assertTrue(input_policy.overlay_passes(event('Q', 'PRESS', ctrl=True), wm))
        for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('G', 'PRESS'), ('ESC', 'PRESS')):
            self.assertFalse(input_policy.overlay_passes(event(kind, value), wm), kind)


class Fresh:
    """Import add-on modules afresh under ``Notices``' stand-in bpy; the package's own attributes come back after."""

    NAMES = ("welcome_screen", "connect_hero", "intro")

    def __init__(self, case):
        self.views = {}
        stubs = {"splash_setup": types.ModuleType("splash_setup"), "chat_view": types.ModuleType("chat_view"),
                 "console": types.ModuleType("console")}
        stubs["splash_setup"].sequence = []
        stubs["chat_view"]._views = self.views
        stubs["console"].is_chat = lambda area: False
        notices = Notices(**stubs)
        self.n = notices.__enter__()
        case.addCleanup(notices.__exit__, None, None, None)
        saved = {name: getattr(angelo_agent, name) for name in self.NAMES if hasattr(angelo_agent, name)}
        case.addCleanup(self.restore, saved)
        for name in self.NAMES:
            sys.modules.pop("angelo_agent." + name, None)
            setattr(self, name, importlib.import_module("angelo_agent." + name))
        self.wm = self.n.bpy.context.window_manager
        self.wm.keyconfigs = keyconfigs()
        self.window = self.wm.windows[0]
        self.context = SimpleNamespace(window=self.window, window_manager=self.wm)

    def restore(self, saved):
        for name in self.NAMES:
            if name in saved:
                setattr(angelo_agent, name, saved[name])
            elif hasattr(angelo_agent, name):
                delattr(angelo_agent, name)


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        self.fresh = Fresh(self)
        intro = self.intro = self.fresh.intro
        self.choices, self.links = [], []
        intro.remember, intro.open_demo = self.choices.append, self.links.append
        self.operator = intro.ANGELO_OT_intro()
        self.operator.token = object()
        intro._open.update(window=self.fresh.window.as_pointer(), area=11, hover=None, pressed=None, focus=None,
                           token=self.operator.token, plan={"hits": [(START, "start", None), (DEMO, "demo", 0)],
                                                            "card": (300, 200, 600, 400)})

    def send(self, *events):
        return [self.operator.modal(self.fresh.context, e) for e in events]

    def test_blenders_refresh_passes_on_after_the_hover_it_shows(self):
        self.assertEqual(self.send(event('MOUSEMOVE', moved=False)), [{'PASS_THROUGH'}], "a quit waits for it")
        self.assertEqual((self.intro._open["hover"], self.intro._open["cursor"]), (("start", None), 'HAND_POINT'))
        self.assertEqual(self.send(event('MOUSEMOVE', x=600, y=420)), [{'RUNNING_MODAL'}], "the person's move stays")
        self.assertEqual(self.intro._open["hover"], ("demo", 0))

    def test_timers_window_events_and_the_quit_key_pass_on(self):
        for kind, value, keys in (('TIMER', 'NOTHING', {}), ('TIMER_REPORT', 'NOTHING', {}),
                                  ('WINDOW_DEACTIVATE', 'NOTHING', {}), ('NONE', 'NOTHING', {}),
                                  ('Q', 'PRESS', {"ctrl": True})):
            self.assertEqual(self.send(event(kind, value, **keys)), [{'PASS_THROUGH'}], kind)
        self.assertTrue(self.intro.is_open())

    def test_it_still_takes_its_own_clicks_keys_and_shields_the_view(self):
        self.assertEqual(self.send(event('G', 'PRESS'), event('MIDDLEMOUSE', 'PRESS')), [{'RUNNING_MODAL'}] * 2,
                         "nothing behind it reacts")
        self.assertEqual(self.send(event('TAB', 'PRESS')), [{'RUNNING_MODAL'}])
        self.assertEqual(self.intro._open["focus"], ("demo", 0), "Tab reaches its controls in reading order")
        self.send(event('LEFTMOUSE', 'PRESS', x=600, y=420), event('LEFTMOUSE', 'RELEASE', x=600, y=420))
        self.assertEqual(len(self.links), 1, "a demo tile opens its link and keeps the welcome")
        self.assertTrue(self.intro.is_open())
        self.assertEqual(self.send(event('LEFTMOUSE', 'PRESS'), event('LEFTMOUSE', 'RELEASE')),
                         [{'RUNNING_MODAL'}, {'FINISHED'}])
        self.assertEqual((self.choices, self.intro.is_open()), (["DIRECTOR"], False), "Start creating closes it")

    def test_escape_closes_it(self):
        self.assertEqual(self.send(event('ESC', 'PRESS')), [{'FINISHED'}])
        self.assertEqual((self.choices, self.intro.is_open()), ([None], False))

    def test_a_closed_or_displaced_welcome_passes_its_last_event_on(self):
        self.intro._open.clear()
        self.assertEqual(self.send(event('MOUSEMOVE', moved=False)), [{'FINISHED', 'PASS_THROUGH'}])
        self.setUp()
        self.fresh.window.screen.areas.clear()   # A layout change took its 3D view.
        self.assertEqual(self.send(event('MOUSEMOVE', moved=False)), [{'FINISHED', 'PASS_THROUGH'}])
        self.assertFalse(self.intro.is_open())


class ShowcaseTests(unittest.TestCase):
    def setUp(self):
        self.fresh = Fresh(self)
        hero = self.hero = self.fresh.connect_hero
        self.operator = hero.ANGELO_OT_connect_hero()
        self.operator.token = object()
        hero._state.update(window=self.fresh.window.as_pointer(), area=11, fading_since=None, hover=None, pressed=None,
                           focus=None, token=self.operator.token, plan={"hits": [(START, "explore", None)]})

    def send(self, *events):
        return [self.operator.modal(self.fresh.context, e) for e in events]

    def test_blenders_refresh_passes_on_and_the_persons_move_stays(self):
        self.assertEqual(self.send(event('MOUSEMOVE', moved=False)), [{'PASS_THROUGH'}])
        self.assertEqual(self.hero._state["hover"], ("explore", None))
        self.assertEqual(self.send(event('MOUSEMOVE')), [{'RUNNING_MODAL'}])
        self.assertEqual(self.send(event('TIMER'), event('Q', 'PRESS', ctrl=True)), [{'PASS_THROUGH'}] * 2)

    def test_a_released_showcase_passes_its_last_event_on(self):
        self.hero._state.clear()
        self.assertEqual(self.send(event('MOUSEMOVE', moved=False)), [{'FINISHED', 'PASS_THROUGH'}])


class AuditTests(unittest.TestCase):
    """Every modal handler in the add-on consults the policy before it keeps anything."""

    POLICY = {"intro.py": "input_policy.overlay_passes(event, context.window_manager)",
              "card_host.py": "input_policy.overlay_passes(event, context.window_manager)",
              "chat_launcher.py": "input_policy.overlay_passes(event, context.window_manager)",
              "chat_view.py": "overlay_passes(event, context.window_manager)",
              "studio_input.py": "overlay_passes(event, context.window_manager)",
              "providers_page_host.py": "overlay_passes(event, context.window_manager)",
              "flow_window.py": "input_policy.overlay_passes(event, context.window_manager)",
              "flow_menu.py": "input_policy.overlay_passes(event, context.window_manager)",
              "asset_browser_entry.py": "input_policy.overlay_passes(event, context.window_manager)",
              "asset_shelf_add.py": "input_policy.overlay_passes(event, context.window_manager)",
              # Its own wider rule: timers, window events and every Ctrl/Cmd or F-key shortcut.
              "connect_hero.py": "if event.type in WINDOW_EVENTS or event.ctrl or event.oskey or function_key:",
              # Passes everything except Return and Escape, which decide its review card wherever the pointer is.
              "agent_workspace_ui.py": "return review_card.wait(self, context, event)"}
    REFRESH = ("intro.py", "card_host.py", "connect_hero.py", "providers_page_host.py", "flow_menu.py")

    def test_every_modal_handler_is_known_and_consults_the_policy(self):
        found = sorted(path.name for path in ADDON.glob("*.py") if "    def modal(self, context, event):" in
                       path.read_text(encoding="utf-8"))
        self.assertEqual(found, sorted(self.POLICY), "a new modal handler: add it here with its rule")
        for name, rule in self.POLICY.items():
            modal = (ADDON / name).read_text(encoding="utf-8").split("    def modal(self, context, event):", 1)[1]
            self.assertTrue(rule in modal.split("\n    def ", 1)[0], name)

    def test_no_modal_reads_a_local_before_any_statement_assigns_it(self):
        """A modal handler that reads a local in a statement before the one that first assigns it raises
        UnboundLocalError on that event and ends, leaving its canvas dead (Studio's input, 1.0.5 preview line
        12:14: its LEFTMOUSE release branch read ``state`` before the function assigned it)."""
        import ast
        for path in sorted(ADDON.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not (isinstance(function, ast.FunctionDef) and function.name == "modal"):
                    continue
                declared = {n for node in ast.walk(function) if isinstance(node, (ast.Global, ast.Nonlocal))
                            for n in node.names}
                arguments = {a.arg for a in function.args.args}

                def stored(node):
                    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                local = set().union(*(stored(statement) for statement in function.body)) - declared - arguments
                assigned = set()
                for statement in function.body:
                    loaded = {n.id for n in ast.walk(statement)
                              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                    early = (loaded & local) - assigned - stored(statement)
                    self.assertEqual(early, set(), "{}:{} reads {} before assigning it".format(
                        path.name, statement.lineno, sorted(early)))
                    assigned |= stored(statement)

    def test_overlays_pass_blenders_refresh_on(self):
        for name in self.REFRESH:
            self.assertTrue("refresh_move(event)" in (ADDON / name).read_text(encoding="utf-8"), name)

    def test_review_card_passes_everything_but_its_decision_keys(self):
        wait = (ADDON / "review_card.py").read_text(encoding="utf-8").split("def wait(", 1)[1]
        self.assertIn("if card.decision is None:\n        return {'PASS_THROUGH'}", wait)


if __name__ == "__main__":
    unittest.main()
