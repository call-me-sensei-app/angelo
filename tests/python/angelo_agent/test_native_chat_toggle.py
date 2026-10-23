# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The top bar's show/hide chat toggle and Ctrl+Option+N (``chat_toggle``), without Blender.

Why: in Manual mode a closed chat column could be found again only under Window > Angelo Chat, so the top bar gets a
button that hides and shows it. Covered here: which
state a click acts on (hidden, docked here, popped out), the drawn icon's two states, the tooltip that says what a
click does, the shortcut being free in Blender's default keymap and among Angelo's own, and where it is drawn and
registered. Since 1.0.4 (a user could not find the chat in Manual mode): the same button in Director and Manual, the
shortcut on its label, and the chat following the person across workspace tabs unless they hid it (``follow_step``).
``test_blender_chat_toggle`` and ``test_blender_mode_basics`` prove it in the running app.
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
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(handlers=handlers)
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types, "bpy.app": bpy.app,
                                       "bpy.app.handlers": handlers}):
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


class FollowTests(unittest.TestCase):
    """``follow_step(before, now, wants)``; observations are ``(workspace, state, director, has_view)``."""
    LAYOUT, SHADING, DIRECTOR = 1, 2, 3

    def step(self, before, now, wants=True):
        return toggle.follow_step(before, now, wants)

    def test_a_tab_switch_brings_the_chat_along(self):
        self.assertEqual(self.step((1, "docked", False, True), (2, "hidden", False, True)), (True, True))

    def test_a_popped_out_chat_counts_as_shown(self):
        self.assertEqual(self.step((1, "docked", False, True), (1, "popped", False, True), False), (False, True))

    def test_hiding_it_in_the_same_tab_is_the_persons_choice(self):
        dock, wants = self.step((1, "docked", False, True), (1, "hidden", False, True))
        self.assertEqual((dock, wants), (False, False))
        self.assertEqual(self.step((1, "hidden", False, True), (2, "hidden", False, True), wants), (False, False))

    def test_showing_it_again_restores_the_choice(self):
        self.assertEqual(self.step((2, "hidden", False, True), (2, "docked", False, True), False), (False, True))

    def test_a_tab_without_a_3d_view_keeps_the_choice_for_the_next(self):
        self.assertEqual(self.step((1, "docked", False, True), (2, "hidden", False, False)), (False, True))
        self.assertEqual(self.step((2, "hidden", False, False), (3, "hidden", False, True), True), (True, True))

    def test_mode_changes_are_the_modes_own(self):
        self.assertEqual(self.step((1, "docked", False, True), (3, "hidden", True, True)), (False, True))
        self.assertEqual(self.step((3, "hidden", True, True), (1, "hidden", False, True)), (False, True))

    def test_first_observation_never_docks(self):
        self.assertEqual(self.step(None, (1, "hidden", False, True)), (False, True))
        self.assertEqual(self.step(None, (1, "hidden", False, True), False), (False, False))

    def test_a_follow_docks_once_the_new_tab_has_drawn(self):
        """Docking inside the tab switch left the chat unpainted until the pointer crossed it (GUI runs 2026-10-21/22;
        the chat's own draw ran during the capture): the follow waits for the shown tab's first draw."""
        source = (ADDON / "chat_toggle.py").read_text()
        follow = source.split("\ndef follow(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("request_chat_dock", follow, "never inside the switch")
        self.assertIn("_pending[key] = (now[0], time.monotonic(), session)", follow)
        later = source.split("\ndef _dock_when_drawn(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("tab_drawn(window, since)", later)
        self.assertIn("SETTLE", later)
        self.assertIn("ui.request_chat_dock(", later)
        self.assertIn("!= workspace or not wanted[0]", later, "dropped when it no longer applies")
        watch = source.split("\ndef _watch(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("ui._layout_timer is not None or _pending", watch, "no hidden hint for the moment between")
        launcher = (ADDON / "chat_launcher.py").read_text()
        self.assertIn("window.as_pointer() in chat_toggle._pending", launcher, "no corner Angelo flash meanwhile")
        self.assertIn("drawn_at[area.as_pointer()] = time.monotonic()", launcher)

    def test_off_in_1_0_4(self):
        """Tab-follow waits for 1.0.4.x: its followed chat was sometimes not painted until the pointer moved."""
        self.assertFalse(toggle.FOLLOW_TABS)
        follow = (ADDON / "chat_toggle.py").read_text().split("\ndef follow(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if not FOLLOW_TABS", follow)

    def test_wired_to_the_watch_the_message_bus_and_file_loads(self):
        source = (ADDON / "chat_toggle.py").read_text()
        self.assertIn('msgbus.subscribe_rna(key=(bpy.types.Window, "workspace")', source)
        watch = source.split("\ndef _watch(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(watch.index("follow()"), watch.index("chat_hint.observe()"))
        self.assertIn("bpy.app.handlers.load_post.append(loaded)", source)
        follow = source.split("\ndef follow(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("director._timer is not None or ui._layout_timer is not None", follow, "never mid-move")
        execute = source.split("    def execute(self, context):", 1)[1]
        self.assertIn('wanted[0] = state == "hidden"', execute)


class SuiteWrapperTests(unittest.TestCase):
    def test_every_suite_wrapping_the_toggles_draw_passes_all_its_arguments(self):
        """chat_toggle.draw takes the top bar's compact flags; a GUI suite that wrapped it as draw(layout, context)
        raised on every top-bar draw (twice in 1.0.5: dc2261d4c13, then test_blender_flow_menu)."""
        import ast
        for path in sorted(ADDON.parents[2].glob("tests/python/angelo_agent/test_blender_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(ast.unparse(t) == "chat_toggle.draw" for t in node.targets) \
                        and isinstance(node.value, ast.Name):
                    wrapper = next((f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                                    and f.name == node.value.id), None)
                    if wrapper is not None:
                        self.assertTrue(wrapper.args.vararg and wrapper.args.kwarg,
                                        "%s: %s must take *args, **kwargs" % (path.name, wrapper.name))


class LabelTests(unittest.TestCase):
    def test_the_label_carries_the_shortcut_as_bound(self):
        import angelo_agent.shortcuts as shortcuts
        with mock.patch.object(shortcuts, "text", lambda idname: "⌃⌥N" if idname == "angelo.toggle_chat" else ""):
            self.assertEqual(toggle.button_text(), "Chat  ⌃⌥N")
        with mock.patch.object(shortcuts, "text", lambda idname: ""):
            self.assertEqual(toggle.button_text(), "Chat", "a removed shortcut is not quoted")

    def test_in_arabic_the_keys_stay_one_run_before_the_label(self):
        """Blender prints the chord in reading order ("⌃ ⌥ N", wm_keymap.cc); the Arabic label is pre-shaped visual
        text. The keys must reach the button unreversed, left of the label (1.0.5 review: "N ⌥ ^")."""
        import angelo_agent.shortcuts as shortcuts
        arabic = "\ufee9\ufe97\ufea9\ufead\ufea9"   # A pre-shaped (presentation-form) catalogue entry.
        with mock.patch.object(shortcuts, "text", lambda idname: "⌃ ⌥ N"), \
                mock.patch.object(toggle, "iface_", lambda text: arabic):
            self.assertEqual(toggle.button_text(), "⌃ ⌥ N  " + arabic)

    def test_shortcut_text_is_blenders_own(self):
        source = (ADDON / "shortcuts.py").read_text()
        self.assertIn("item.to_string(compact=True)", source.split("\ndef text(", 1)[1].split("\ndef ", 1)[0])
        draw = (ADDON / "chat_toggle.py").read_text().split("\ndef draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("label = button_text()", draw)


class WiringTests(unittest.TestCase):
    def test_drawn_in_both_modes_top_bar_beside_the_mode_menu(self):
        draw = (ADDON / "director.py").read_text().split("\ndef draw_topbar(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(draw.index("chat_toggle.draw(self.layout, context, level >= top_bar_fit.CHAT_SHORT"), draw.index('shown.menu("ANGELO_MT_workspace_mode"'))
        self.assertNotIn("if not active:", draw.split("chat_toggle.draw", 1)[0], "the same button in both modes")

    def test_the_mode_menu_is_as_wide_as_its_measured_name_never_blenders_estimate(self):
        # 1.0.5 RC (chat_start_states): the left-aligned mode menu took Blender's own width estimate and drew "Direc…".
        draw = (ADDON / "director.py").read_text().split("\ndef draw_topbar(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("shown.ui_units_x = top_bar_fit.menu_units(context, names[0 if active else 1])", draw)
        fit = (ADDON / "top_bar_fit.py").read_text().split("\ndef menu_units(", 1)[1]
        self.assertIn("+ 1.35", fit, "Blender's header-menu estimate (text + 1.1 units) plus a quarter unit")
        self.assertLess(draw.index("shown.ui_units_x"), draw.index('shown.menu("ANGELO_MT_workspace_mode"'))

    def test_the_shortcut_keeps_its_order_in_a_right_to_left_language(self):
        """Arabic catalogue text is in visual order; a bidi pass over "label  ⌃⌥N" drew "N ⌥ ^" (1.0.5 review)."""
        source = (ADDON / "chat_toggle.py").read_text()
        text = source.split("\ndef button_text(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('if _visual_rtl(label):', text)
        self.assertIn('return keys + "  " + label', text, "the keys as one left-to-right run, left of the label")

    def test_director_hides_and_shows_with_its_own_split(self):
        source = (ADDON / "chat_toggle.py").read_text()
        show = source.split("\ndef show(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('director.request_mode(window, "DIRECTOR", persist=False)', show)
        execute = source.split("    def execute(self, context):", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("is_director", execute, "a click in Director acts, never refuses")

    def test_pressed_while_shown(self):
        source = (ADDON / "chat_toggle.py").read_text()
        draw = source.split("\ndef draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("depress=shown", draw)
        self.assertIn("icon_value=icon_id(shown, ink)", draw)
        self.assertIn('ink = glyph_tint.colour("tool", shown)', draw, "the bubble in the button's text colour")

    def test_hide_keeps_the_draft_before_closing(self):
        source = (ADDON / "chat_toggle.py").read_text()
        hide = source.split("\ndef hide(", 1)[1].split("\nclass ", 1)[0]
        self.assertLess(hide.index("_keep_draft(area)"), hide.index("chat_hide.request_hide(window, area)"))
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
