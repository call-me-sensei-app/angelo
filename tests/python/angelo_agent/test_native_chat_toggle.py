# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The top bar's show/hide chat toggle and Ctrl+Option+N (``chat_toggle``), without Blender.

Why: in Manual mode a closed chat column could be found again only under Window > Angelo Chat, so the top bar gets a
button that hides and shows it. Covered here: which
state a click acts on (hidden, docked here, popped out), the drawn icon's two states, the tooltip that says what a
click does, the shortcut being free in Blender's default keymap and among Angelo's own, and where it is drawn and
registered. ``test_blender_chat_toggle`` proves it in the running app.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def load():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object)
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types}):
        sys.modules.pop("angelo_agent.chat_toggle", None)
        import angelo_agent.chat_toggle as module
    return module


toggle = load()


def area(chat):
    return SimpleNamespace(chat=chat)


def window(*areas):
    return SimpleNamespace(screen=SimpleNamespace(areas=list(areas)))


class StateTests(unittest.TestCase):
    def state(self, here, windows):
        with mock.patch.object(toggle.console, "is_chat", lambda a: a.chat):
            return toggle.chat_state(here, windows)

    def test_docked_here(self):
        chat = area(True)
        main = window(area(False), chat)
        self.assertEqual(self.state(main, [main]), ("docked", main, chat))

    def test_popped_out_in_another_window(self):
        chat = area(True)
        main, pop = window(area(False)), window(chat)
        self.assertEqual(self.state(main, [main, pop]), ("popped", pop, chat))

    def test_hidden(self):
        main = window(area(False))
        self.assertEqual(self.state(main, [main]), ("hidden", None, None))

    def test_docked_here_wins_over_a_pop_out(self):
        here, other = area(True), area(True)
        main, pop = window(here), window(other)
        self.assertEqual(self.state(main, [pop, main])[0], "docked")


class IconTests(unittest.TestCase):
    def test_two_states_read_apart(self):
        filled, outline = toggle.bubble(True), toggle.bubble(False)
        self.assertEqual(len(filled), toggle.ICON_SIZE ** 2 * 4)
        cover = [sum(p[3::4]) / toggle.ICON_SIZE ** 2 for p in (filled, outline)]
        self.assertGreater(cover[0], 0.3)
        self.assertLess(cover[1], cover[0] * 0.6, "the hidden state is an outline, clearly lighter")
        centre = (toggle.ICON_SIZE // 2 + 4) * toggle.ICON_SIZE + toggle.ICON_SIZE // 2
        self.assertEqual(outline[centre * 4 + 3], 0.0, "hollow inside")
        self.assertTrue(all(0.0 <= value <= 1.0 for value in filled))
        for pixels in (filled, outline):   # Premultiplied: a transparent pixel adds no colour (no white square).
            for index in range(0, len(pixels), 4):
                self.assertTrue(all(pixels[index + c] <= pixels[index + 3] + 1e-9 for c in range(3)))

    def test_the_top_bar_follows_the_chat(self):
        """The top bar is a global area: Screen.areas never holds it, so only the message bus redraws it."""
        source = (ADDON / "chat_toggle.py").read_text()
        self.assertIn('msgbus.publish_rna(key=workspace.path_resolve("tools", False))', source)
        self.assertNotIn("(bpy.types.WorkSpace", source, "a type-wide key never reaches an instance's subscribers")
        self.assertIn("redraw_top_bars()", source.split("    def execute(self, context):", 1)[1])
        self.assertNotIn("'TOPBAR'", source)
        watch = source.split("\ndef _watch(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if states != _last_states:", watch)
        self.assertIn("bpy.app.timers.register(_watch", source)

    def test_a_closed_area_never_leaves_execute_without_its_window(self):
        execute = (ADDON / "chat_toggle.py").read_text().split("    def execute(self, context):", 1)[1]
        self.assertIn("here = context.window", execute)
        self.assertNotIn("context.window.screen", execute)


class TooltipTests(unittest.TestCase):
    def test_says_what_a_click_does(self):
        operator = toggle.ANGELO_OT_toggle_chat
        here = window(area(False))
        context = SimpleNamespace(window=here, window_manager=SimpleNamespace(windows=[here]))
        with mock.patch.object(toggle.console, "is_chat", lambda a: a.chat):
            self.assertEqual(operator.description(context, None), "Show the Angelo chat")
            here.screen.areas.append(area(True))
            self.assertEqual(operator.description(context, None), "Hide the Angelo chat")
        self.assertEqual(operator.description(None, None), "Show the Angelo chat")


class ShortcutTests(unittest.TestCase):
    def test_ctrl_option_n_is_the_chat_toggles(self):
        """Free in every Blender editor and distinct from Angelo's other shortcuts: ``test_native_shortcuts``."""
        import angelo_agent.shortcuts as shortcuts
        self.assertEqual((shortcuts.CHAT.idname, shortcuts.CHAT.chord, shortcuts.CHAT.keymap),
                         ("angelo.toggle_chat", {"type": 'N', "ctrl": True, "alt": True}, "Window"))
        self.assertIn("shortcuts.add(keyconfig, shortcuts.CHAT)", (ADDON / "chat_toggle.py").read_text())


class WiringTests(unittest.TestCase):
    def test_drawn_in_manual_modes_top_bar_beside_the_mode_menu(self):
        draw = (ADDON / "director.py").read_text().split("\ndef draw_topbar(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(draw.index("chat_toggle.draw(self.layout, context)"), draw.index('mode.menu("ANGELO_MT_workspace_mode"'))
        self.assertIn("if not active:", draw.split("chat_toggle.draw", 1)[0].rsplit("\n", 3)[-3], "Manual mode only")

    def test_pressed_while_shown(self):
        source = (ADDON / "chat_toggle.py").read_text()
        draw = source.split("\ndef draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("depress=shown", draw)
        self.assertIn("icon_value=icon_id(shown)", draw)

    def test_hide_keeps_the_draft_before_closing(self):
        source = (ADDON / "chat_toggle.py").read_text()
        hide = source.split("\ndef hide(", 1)[1].split("\nclass ", 1)[0]
        self.assertLess(hide.index("_keep_draft(area)"), hide.index("bpy.ops.screen.area_close()"))
        self.assertIn("bpy.ops.wm.window_close()", hide)
        show = source.split("\ndef show(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("ui.request_chat_dock(window, ui._dock_side, draft_override=(value, caret, _font), on_error=not_shown)", show)

    def test_no_function_rebinds_bpy(self):
        """A local ``import bpy.x`` makes ``bpy`` local to the whole function (it failed at quit in unregister)."""
        tree = ast.parse((ADDON / "chat_toggle.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Import):
                        self.assertFalse(any(alias.name.split(".")[0] == "bpy" for alias in inner.names), node.name)

    def test_registered_with_the_add_on(self):
        init = (ADDON / "__init__.py").read_text()
        self.assertIn("chat_toggle.register()", init)
        self.assertIn("chat_toggle.unregister()", init)
        ast.parse((ADDON / "chat_toggle.py").read_text())


if __name__ == "__main__":
    unittest.main()
