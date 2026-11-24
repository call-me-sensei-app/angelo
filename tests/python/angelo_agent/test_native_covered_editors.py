# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing the chat hands on reaches an editor an Angelo overlay covers, and one surface holds the keyboard.

Re-review of markup b5808fd5a2e, M1: with the chat holding the keyboard and the pointer on the picture viewer, the
chat handed what it didn't use (Ctrl+J, Ctrl+Numpad 0, the numpad, every key while its composer was hidden, a dropped
file) to the 3D view the viewer covers. ``overlay_holds.holds_covered`` is the one check, at the chat's one modal.
S1: the + menu's row opened the viewer with the keyboard and the chat took it back as its menu closed. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import input_policy, keyboard_owner, overlay_holds  # noqa: E402


def event(kind, value="PRESS", x=50, y=50, **modifiers):
    return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y, ctrl=modifiers.get("ctrl", False),
                           oskey=modifiers.get("oskey", False), alt=False, shift=False, unicode="")


class HoldTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(overlay_holds.COVERS, {"test": lambda window, x, y: x < 100}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = object()

    def modal(self, result):
        return overlay_holds.holds_covered(lambda op, context, ev: set(result))

    def run_modal(self, item, result=('PASS_THROUGH',)):
        return self.modal(result)(None, SimpleNamespace(window=self.window), item)

    def test_keys_and_drops_handed_on_into_a_covered_editor_are_held(self):
        for item in (event("J", ctrl=True), event("NUMPAD_0", ctrl=True), event("NUMPAD_7"), event("DEL"),
                     event("DEL", "RELEASE"), event("Z", ctrl=True), event("EVT_DROP")):
            with self.subTest(key=item.type, value=item.value):
                self.assertEqual(self.run_modal(item), {'RUNNING_MODAL'})

    def test_what_the_overlay_doesnt_cover_or_isnt_a_key_goes_on(self):
        self.assertEqual(self.run_modal(event("J", ctrl=True, x=500)), {'PASS_THROUGH'}, "a visible editor")
        for item in (event("MOUSEMOVE"), event("LEFTMOUSE"), event("WHEELUPMOUSE"), event("TRACKPADPAN"),
                     event("TIMER"), event("NDOF_MOTION")):
            with self.subTest(kind=item.type):
                self.assertEqual(self.run_modal(item), {'PASS_THROUGH'}, "the pointer's own path, or no input")
        save = event("S", **({"oskey": True} if sys.platform == "darwin" else {"ctrl": True}))
        self.assertEqual(self.run_modal(save), {'PASS_THROUGH'}, "Save is handed on (N8)")
        self.assertEqual(self.run_modal(event("J", ctrl=True), ('RUNNING_MODAL',)), {'RUNNING_MODAL'})

    def test_a_retired_handler_still_ends_when_its_last_event_is_held(self):
        """Re-read of 0690d5c, S2: the chat's retired handler returns CANCELLED with PASS_THROUGH; held, it became
        RUNNING_MODAL and stayed. Only the pass-on is taken off."""
        self.assertEqual(self.run_modal(event("J", ctrl=True), ('CANCELLED', 'PASS_THROUGH')), {'CANCELLED'})
        self.assertEqual(self.run_modal(event("J", ctrl=True), ('FINISHED', 'PASS_THROUGH')), {'FINISHED'})

    def test_undo_never_reaches_a_hidden_scene_from_a_chat_holding_the_keyboard(self):
        """Re-read of 0690d5c, N2, narrowed on 41fd23a: with no draft history the chat's Undo fell back to the scene's
        under the picture viewer. While an overlay hides part of the scene and a chat holds the keyboard, the
        person's Undo and Redo keys (their Screen key map, never a hard-coded Z) are held; with no chat holding the
        keyboard an Undo over a visible editor is the person's and goes on; with no overlay, as before."""
        command = {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}
        undo = event("Z", x=500, **command)
        item = SimpleNamespace(idname="ed.undo", active=True, type="Z", value="PRESS", shift=0, alt=0,
                               ctrl=int(command.get("ctrl", False)), oskey=int(command.get("oskey", False)))
        keymaps = {"Screen": SimpleNamespace(keymap_items=[item])}
        manager = SimpleNamespace(keyconfigs=SimpleNamespace(user=SimpleNamespace(keymaps=keymaps)))

        def run(chat_holds):
            modal = overlay_holds.holds_covered(lambda op, context, ev: {'PASS_THROUGH'})
            with mock.patch.object(keyboard_owner, "chat_has_it", lambda window, menus=False: chat_holds):
                return modal(None, SimpleNamespace(window=self.window, window_manager=manager), undo)
        self.assertEqual(run(True), {'PASS_THROUGH'}, "no overlay hides the scene")
        with mock.patch.dict(overlay_holds.COVERS_WINDOW, {"test": lambda window: True}, clear=True):
            self.assertEqual(run(True), {'RUNNING_MODAL'}, "a chat holding the keyboard: held")
            self.assertEqual(run(False), {'PASS_THROUGH'}, "no chat holds it, the pointer over a visible editor")
            plain = SimpleNamespace(**{**vars(undo), "oskey": False, "ctrl": False})
            with mock.patch.object(keyboard_owner, "chat_has_it", lambda window, menus=False: True):
                self.assertEqual(overlay_holds.holds_covered(lambda op, c, ev: {'PASS_THROUGH'})(
                    None, SimpleNamespace(window=self.window, window_manager=manager), plain), {'PASS_THROUGH'},
                    "a plain Z is no Undo in the person's key map")
        moved = SimpleNamespace(**{**vars(item), "type": "U"})   # The person moved Undo to Cmd/Ctrl+U.
        keymaps["Screen"] = SimpleNamespace(keymap_items=[moved])
        with mock.patch.dict(overlay_holds.COVERS_WINDOW, {"test": lambda window: True}, clear=True):
            self.assertEqual(run(True), {'PASS_THROUGH'}, "Z is no longer their Undo")

    def test_an_areas_action_zones_are_the_pointers_own_path(self):
        """N3: the action zones of an area (its corners, for splitting) are never held as keys."""
        for kind in ("ACTIONZONE_AREA", "ACTIONZONE_REGION", "ACTIONZONE_FULLSCREEN"):
            with self.subTest(kind=kind):
                self.assertEqual(self.run_modal(event(kind)), {'PASS_THROUGH'})

    def test_a_check_that_fails_holds(self):
        def broken(window, x, y):
            raise RuntimeError("gone")
        with mock.patch.dict(overlay_holds.COVERS, {"broken": broken}, clear=True):
            self.assertEqual(self.run_modal(event("J", ctrl=True, x=500)), {'RUNNING_MODAL'})


class WiringTests(unittest.TestCase):
    def test_the_chats_modal_is_the_one_held_and_the_viewer_registers_what_it_covers(self):
        tree = ast.parse((ADDON / "chat_view.py").read_text(encoding="utf-8"))
        modal = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "modal"
                     and any(ast.unparse(d) == "_kept_alive" for d in n.decorator_list))
        self.assertEqual(ast.unparse(modal.decorator_list[0]), "holds_covered", "outermost: every pass-on is checked")
        overlay = (ADDON / "markup_overlay.py").read_text(encoding="utf-8")
        self.assertIn('overlay_holds.COVERS["markup"] = covers_point', overlay)
        self.assertIn('overlay_holds.COVERS.pop("markup", None)', overlay)

    def test_an_overlay_stays_ahead_of_a_chat_handler_made_again(self):
        """Re-read of 0690d5c, S1: ensure_input made the chat's handler again ahead of the viewer, and the hold then
        kept the viewer's own keys from it (Escape, F, the tool letters). The viewer's handler comes forward again;
        the old one ends at its next event (its own mark), the viewer's state and token kept."""
        tree = ast.parse((ADDON / "chat_view.py").read_text(encoding="utf-8"))
        ensure = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ensure_input")
        body = ast.unparse(ensure)
        self.assertLess(body.index("bpy.ops.angelo.chat_input('INVOKE_DEFAULT')"), body.index("keep_ahead(window)"))
        overlay = (ADDON / "markup_overlay.py").read_text(encoding="utf-8")
        self.assertIn('overlay_holds.AHEAD["markup"] = come_forward', overlay)
        self.assertIn('overlay_holds.AHEAD.pop("markup", None)', overlay)
        self.assertIn("state.handler is not self.handler", overlay)
        forward = overlay.split("def come_forward", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("bpy.ops.angelo.markup_viewer('INVOKE_DEFAULT')", forward)
        self.assertNotIn("token =", forward, "the viewer's token stays: a send in flight still lands")

    def test_one_overlay_that_fails_to_come_forward_never_stops_the_others(self):
        called = []

        def broken(window):
            raise RuntimeError("gone")
        with mock.patch.dict(overlay_holds.AHEAD, {"a": broken, "b": called.append}, clear=True):
            overlay_holds.keep_ahead("win")
        self.assertEqual(called, ["win"])

    def test_the_chat_never_takes_the_keyboard_back_from_a_surface_its_menu_opened(self):
        """S1: + > Mark up a picture opened the viewer with the keyboard; the chat then refocused as its menu closed."""
        source = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        self.assertIn('if not state.get("popover") and not state.get("blur_request"):', source)
        opened = (ADDON / "markup_overlay.py").read_text(encoding="utf-8").split("def open_reference", 1)[1]
        self.assertIn("keyboard_owner.take(window)", opened.split("\ndef ", 1)[0])


class OwnerTests(unittest.TestCase):
    def chats(self, *states):
        areas = [SimpleNamespace(pointer=n, tag_redraw=lambda: None) for n in range(len(states))]
        views = {n: state for n, state in enumerate(states)}
        return areas, views

    def patched(self, areas, views):
        """The chat's views and console as stand-ins (``chat_view`` needs Blender's drawing modules)."""
        import angelo_agent
        for area in areas:
            area.as_pointer = (lambda pointer: lambda: pointer)(area.pointer)
        chat_view = SimpleNamespace(_views={a.pointer: views[a.pointer] for a in areas})
        console = SimpleNamespace(is_chat=lambda area: True)
        return (mock.patch.object(keyboard_owner.live_windows, "areas", lambda window: areas),
                mock.patch.dict(sys.modules, {"angelo_agent.chat_view": chat_view, "angelo_agent.console": console}),
                mock.patch.object(angelo_agent, "chat_view", chat_view, create=True),
                mock.patch.object(angelo_agent, "console", console, create=True))

    def test_taking_the_keyboard_closes_the_chats_menu_and_asks_every_chat_to_let_go(self):
        typing, menu = {"focused": True}, {"focused": False, "popover": {"kind": "plus"}}
        areas, views = self.chats(typing, menu)
        a, b, c, d = self.patched(areas, views)
        with a, b, c, d:
            self.assertTrue(keyboard_owner.chat_has_it(None))
            self.assertTrue(keyboard_owner.chat_has_it(None, menus=True))
            keyboard_owner.take(None)
            self.assertFalse(keyboard_owner.chat_has_it(None, menus=True))
        self.assertEqual((typing["blur_request"], menu["blur_request"]), (True, True))
        self.assertNotIn("popover", menu, "a press on the viewer closes the chat's menu (N2)")

    def test_a_chat_holding_the_keyboard_takes_its_input_method_events_over_any_surface(self):
        """Re-read of 0690d5c, M1, for the live view too (session_rail_input._live asks chat_takes): the input method's
        events and every key while the window composes are the chat's."""
        areas, views = self.chats({"focused": True})
        a, b, c, d = self.patched(areas, views)
        window, composing = SimpleNamespace(is_ime_composing=False), SimpleNamespace(is_ime_composing=True)
        with a, b, c, d:
            for kind in ("IME_COMPOSITE_START", "IME_COMPOSITE_EVENT", "IME_COMPOSITE_END"):
                with self.subTest(kind=kind):
                    self.assertTrue(keyboard_owner.chat_takes(window, event(kind)))
            self.assertTrue(keyboard_owner.chat_takes(composing, event("NUMPAD_7")))
            self.assertFalse(keyboard_owner.chat_takes(window, event("NUMPAD_7")))
        areas, views = self.chats({"focused": False})
        a, b, c, d = self.patched(areas, views)
        with a, b, c, d:
            self.assertFalse(keyboard_owner.chat_takes(composing, event("IME_COMPOSITE_START")), "no chat: not its")

    def test_an_open_menu_is_the_chats_keyboard_for_the_viewer_only(self):
        """R8-1 keeps a live view's keys its own while a picker is open; the viewer hands typing to the menu (N2)."""
        areas, views = self.chats({"focused": False, "popover": {"kind": "plus"}})
        a, b, c, d = self.patched(areas, views)
        with a, b, c, d:
            self.assertFalse(keyboard_owner.chat_has_it(None))
            self.assertTrue(keyboard_owner.chat_has_it(None, menus=True))


if __name__ == "__main__":
    unittest.main()
