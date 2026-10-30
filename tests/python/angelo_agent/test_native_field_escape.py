# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Escape ends editing in every text field Angelo draws: the typed text stays and the next key is not the field's.

The focus contract (``input_policy``): a field that is being edited keeps the keyboard even while the pointer drifts
over another editor, so a long message never loses keystrokes, until Escape, a click elsewhere or pointer use
elsewhere. Escape ends editing like a Blender text field or a chat app's composer, but keeps the draft (Blender's own
fields revert theirs; Angelo's never throw typing away).

Evidence for the class (C11, 1.0.6 dogfood, Director mode on macOS): after typing in the chat's composer the person
moved the pointer over the 3D view and pressed Backspace, Escape and Z; Escape only cleared the selection, so Z typed
"z" into the chat instead of opening the shading pie. Each kind of Angelo-drawn field is driven here through its own
key handler on stand-ins: the chat's composer (``chat_view.ANGELO_OT_chat_input``), Media Studio's fields
(``studio_input.ANGELO_OT_studio_input``) and the kit's field (``ui_kit_field`` in the connection sheet). Flow nodes'
text fields are Blender's own widgets and keep Blender's Escape. The running app is ``test_blender_input_capture``.
"""

import ast
import functools
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
import qa_operator  # noqa: E402
from angelo_agent import canvas_controls, canvas_rtl, chat_composer, ui_kit_field  # noqa: E402

WINDOW = 1
FIELD_AREA, VIEW_AREA = 21, 22   # The field's editor on the left half of the window, the 3D view on the right.


def _reraise():
    raise   # ``_kept_alive`` logs and passes on a failing event; a test wants the failure itself.


def load(name, wanted, namespace):
    """Run only ``wanted`` (top-level functions and classes, in file order) of add-on module ``name`` in
    ``namespace``: the key handlers as written, on stand-ins for what Blender gives them."""
    path = ADDON / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = [node for node in tree.body if getattr(node, "name", None) in wanted]
    namespace.setdefault("__name__", "angelo_agent." + path.stem)
    namespace.setdefault("__package__", "angelo_agent")
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class Area:
    def __init__(self, pointer, x, width=600, height=800, history=None):
        self._pointer, self.x, self.y, self.width, self.height = pointer, x, 0, width, height
        self.regions = [SimpleNamespace(type='WINDOW', x=x, y=0, width=width, height=height)]
        self.spaces = SimpleNamespace(active=SimpleNamespace(history=history or []))

    def as_pointer(self):
        return self._pointer

    def tag_redraw(self):
        pass


def key(kind, unicode="", over=VIEW_AREA, value='PRESS', **modifiers):
    """A key press with the pointer over the 3D view (or ``over`` the field's editor)."""
    keys = dict(shift=False, ctrl=False, alt=False, oskey=False)
    keys.update(modifiers)
    x = 900 if over == VIEW_AREA else 300
    return SimpleNamespace(type=kind, value=value, unicode=unicode, mouse_x=x, mouse_y=400, mouse_prev_x=x - 1,
                           mouse_prev_y=400, **keys)


class ChatComposerTests(unittest.TestCase):
    """The chat's composer, focused, with the pointer resting over the 3D view."""

    def setUp(self):
        self.line = SimpleNamespace(body="", current_character=0)
        self.chat, self.view3d = Area(FIELD_AREA, 0, history=[self.line]), Area(VIEW_AREA, 600)
        self.views = {FIELD_AREA: {"focused": True}}
        self.state = self.views[FIELD_AREA]
        console = SimpleNamespace(is_chat=lambda area: area.as_pointer() == FIELD_AREA, execute=lambda context: None)
        controller = SimpleNamespace(state={"id": "session"}, selection_connection_id="")
        ui = SimpleNamespace(get_controller=lambda: controller,
                             team_account_ui=SimpleNamespace(note_window_event=lambda kind: None))
        marble = SimpleNamespace(note_event=lambda kind, value: None)
        package = sys.modules["angelo_agent"]
        for name, stand_in in (("console", console), ("ui", ui), ("marble_host", marble)):
            # ``from . import x`` reads the package's attribute first, then sys.modules: both are replaced.
            patcher = patch.dict(sys.modules, {"angelo_agent." + name: stand_in})
            patcher.start()
            self.addCleanup(patcher.stop)
            attribute = patch.object(package, name, stand_in, create=True)
            attribute.start()
            self.addCleanup(attribute.stop)
        areas = [self.chat, self.view3d]
        controls = SimpleNamespace(key=canvas_controls.key, leave_areas=lambda *a: None, pointer=lambda *a: False,
                                   press=lambda *a: None, release=lambda *a: None, DISABLED="disabled")
        namespace = load("chat_view.py", {"inside", "_kept_alive", "ANGELO_OT_chat_input"}, {
            "functools": functools, "traceback": SimpleNamespace(print_exc=_reraise),
            "bpy": SimpleNamespace(types=SimpleNamespace(Operator=object), context=None),
            "modal_options": lambda identity: set(), "_enabled": True, "_input_generation": 0, "_views": self.views,
            "_modals": set(), "_scroll": {}, "live_windows": SimpleNamespace(areas=lambda window: areas),
            "canvas_controls": controls, "canvas_rtl": canvas_rtl, "chat_composer": chat_composer,
            "chat_scrollbar": SimpleNamespace(handle=lambda *a: False), "ui_scale": lambda: 1.0,
            "cursor_position": chat_composer.cursor_position, "set_draft": chat_composer.set_draft,
            "over_overlay": lambda window, event: False, "clear_blocked_notice": lambda window: None,
            "note_blocked_click": lambda *a: None, "release_active": lambda *a: False})
        self.operator = qa_operator.stand_in(
            namespace["ANGELO_OT_chat_input"], input_generation=0, window_pointer=WINDOW, selected_all=False,
            anchor=None, dragging=False, session="session", pending_click=None, focused_area=FIELD_AREA)
        window = SimpleNamespace(as_pointer=lambda: WINDOW, modal_operators=[])
        self.context = SimpleNamespace(window=window, window_manager=SimpleNamespace(clipboard=""))

    def send(self, *events):
        return [self.operator.modal(self.context, event) for event in events]

    def type_text(self, text):
        return self.send(*(key(char.upper(), char) for char in text))

    def test_a_focused_composer_keeps_typing_while_the_pointer_rests_over_the_3d_view(self):
        self.assertEqual(self.type_text("hello"), [{'RUNNING_MODAL'}] * 5, "no keystroke lost to the drifting pointer")
        self.assertEqual(self.send(key('BACK_SPACE')), [{'RUNNING_MODAL'}])
        self.assertEqual(self.line.body, "hell")

    def test_escape_ends_editing_keeps_the_draft_and_the_next_keys_are_blenders(self):
        self.type_text("make a cube")
        self.assertEqual(self.send(key('ESC')), [{'RUNNING_MODAL'}], "the field's own Escape")
        self.assertEqual(self.line.body, "make a cube", "the draft is kept")
        self.assertEqual((self.state["focused"], self.operator.focused_area), (False, None))
        self.assertEqual(self.send(key('Z', "z"), key('BACK_SPACE'), key('TAB'), key('ESC')), [{'PASS_THROUGH'}] * 4,
                         "the shading pie, and every later key, reach the 3D view")
        self.assertEqual(self.line.body, "make a cube")

    def test_escape_with_the_pointer_over_the_chat_ends_editing_too(self):
        self.type_text("abc")
        self.assertEqual(self.send(key('ESC', over=FIELD_AREA)), [{'RUNNING_MODAL'}])
        self.assertEqual((self.line.body, self.state["focused"], self.operator.focused_area), ("abc", False, None))
        self.assertEqual(self.send(key('Z', "z")), [{'PASS_THROUGH'}])

    def test_escape_clears_a_selection_as_it_ends_editing(self):
        self.type_text("abc")
        self.send(key('A', "a", oskey=True))
        self.assertEqual(self.send(key('ESC')), [{'RUNNING_MODAL'}])
        self.assertEqual((self.state["selection"], self.operator.selected_all, self.line.body), (None, False, "abc"))
        self.assertEqual(self.send(key('BACK_SPACE')), [{'PASS_THROUGH'}], "no selection is deleted afterwards")
        self.assertEqual(self.line.body, "abc")

    def test_escape_from_a_tab_focused_control_returns_to_the_field_first(self):
        self.state.update(hits=[((0, 0, 28, 28), "send", None)], focus=("send", None))
        self.assertEqual(self.send(key('ESC')), [{'RUNNING_MODAL'}])
        self.assertEqual((self.state["focus"], self.state["focused"]), (None, True), "back in the field")
        self.assertEqual(self.send(key('ESC')), [{'RUNNING_MODAL'}])
        self.assertFalse(self.state["focused"])
        self.assertEqual(self.send(key('Z', "z")), [{'PASS_THROUGH'}])

    def test_a_waiting_chat_releases_the_keyboard_on_escape_too(self):
        self.state["composer_hidden"] = True   # No account yet: Tab and Return choose a connect option.
        self.assertEqual(self.send(key('ESC')), [{'RUNNING_MODAL'}])
        self.assertFalse(self.state["focused"])
        self.assertEqual(self.send(key('TAB')), [{'PASS_THROUGH'}], "Tab is the 3D view's again")

    def test_a_click_in_the_composer_takes_the_keyboard_back(self):
        self.type_text("abc")
        self.send(key('ESC'))
        self.state.update(composer=(0, 300, 600, 200), draft_rows=[])   # Under the pointer.
        self.assertEqual(self.send(key('LEFTMOUSE', over=FIELD_AREA)), [{'RUNNING_MODAL'}])
        self.send(key('LEFTMOUSE', over=FIELD_AREA, value='RELEASE'))
        self.assertEqual((self.state["focused"], self.operator.focused_area), (True, FIELD_AREA))
        self.assertEqual(self.send(key('D', "d")), [{'RUNNING_MODAL'}])
        self.assertIn("d", self.line.body)


class StudioFieldTests(unittest.TestCase):
    """A Media Studio field, being edited, with the pointer resting over the 3D view."""

    def setUp(self):
        self.studio, self.view3d = Area(FIELD_AREA, 0), Area(VIEW_AREA, 600)
        self.state = {"focus": "prompt", "prompt": "a cat", "caret": 5, "selection": None}
        areas = [self.studio, self.view3d]
        core = SimpleNamespace(_enabled=True, _input_generation=0)
        drag = SimpleNamespace(forget=lambda states: None, moved=lambda *a: False)
        namespace = load("studio_input.py", {"ANGELO_OT_studio_input"}, {
            "Operator": object, "bpy": SimpleNamespace(context=None), "modal_options": lambda identity: set(),
            "core": core, "_modals": set(), "_views": {FIELD_AREA: self.state},
            "live_windows": SimpleNamespace(areas=lambda window: areas),
            "is_studio": lambda area: area.as_pointer() == FIELD_AREA, "view": lambda area: self.state,
            "studio_drag": drag, "MULTILINE_FIELDS": frozenset({"prompt"}), "POPUP_ROW": 24})
        self.operator = qa_operator.stand_in(namespace["ANGELO_OT_studio_input"], window_pointer=WINDOW,
                                             input_generation=0, focus_area=FIELD_AREA, dragging=False)
        window = SimpleNamespace(as_pointer=lambda: WINDOW, modal_operators=[])
        self.context = SimpleNamespace(window=window, window_manager=SimpleNamespace(clipboard=""))

    def send(self, *events):
        return [self.operator.modal(self.context, event) for event in events]

    def test_escape_ends_editing_keeps_the_text_and_the_next_keys_are_blenders(self):
        self.assertEqual(self.send(key('S', "s")), [{'RUNNING_MODAL'}], "typing stays while the pointer drifts")
        self.assertEqual(self.state["prompt"], "a cats")
        self.assertEqual(self.send(key('ESC')), [{'RUNNING_MODAL'}])
        self.assertEqual((self.state["focus"], self.state["prompt"]), (None, "a cats"))
        self.assertEqual(self.send(key('Z', "z"), key('BACK_SPACE')), [{'PASS_THROUGH'}] * 2)
        self.assertEqual(self.state["prompt"], "a cats")


class KitFieldTests(unittest.TestCase):
    """The kit's field (``ui_kit_field``) in the connection sheet, the card that edits text."""

    def setUp(self):
        namespace = {}
        tree = ast.parse((ADDON / "providers_sheet.py").read_text(encoding="utf-8"))
        sheet = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ConnectionSheet")
        method = next(node for node in sheet.body if getattr(node, "name", None) == "key_event")
        exec(compile(ast.Module(body=[method], type_ignores=[]), "providers_sheet.py", "exec"), {
            "field": ui_kit_field, "bpy": SimpleNamespace(context=SimpleNamespace(
                window_manager=SimpleNamespace(clipboard=""))), "__builtins__": __builtins__}, namespace)
        self.name = ui_kit_field.FieldState(text="Studio", editing=True)
        self.sheet = SimpleNamespace(select_open=False, _fields=lambda: {"name": self.name}, _changed=lambda host: None)
        self.key_event = functools.partial(namespace["key_event"], self.sheet)

    def test_escape_ends_editing_keeps_the_text_and_the_next_key_is_not_the_fields(self):
        self.assertTrue(self.key_event(key('S', "s"), None))
        self.assertEqual(self.name.text, "Studios")
        self.assertTrue(self.key_event(key('ESC'), None), "the field's own Escape")
        self.assertEqual((self.name.editing, self.name.text), (False, "Studios"))
        self.assertFalse(self.key_event(key('Z', "z"), None), "the next key goes to the card's own keys")
        self.assertEqual(self.name.text, "Studios")

    def test_the_field_itself_keeps_its_text_on_escape(self):
        state = ui_kit_field.FieldState(text="sk-test", cursor=3, editing=True)
        self.assertEqual(ui_kit_field.field_key(state, 'ESC'), "cancel")
        self.assertEqual((state.editing, state.text), (False, "sk-test"))


class EveryFieldTests(unittest.TestCase):
    def test_every_add_on_key_handler_that_types_text_is_covered_here(self):
        """A new hand-drawn text field (a key handler inserting ``event.unicode``) is added to this suite."""
        typing = sorted(path.name for path in ADDON.glob("*.py")
                        if "event.unicode" in path.read_text(encoding="utf-8") and path.name != "input_policy.py")
        # chat_view: the composer and the model search (whose Escape clears the query, then closes the picker).
        self.assertEqual(typing, ["chat_view.py", "studio_input.py"])


if __name__ == "__main__":
    unittest.main()
