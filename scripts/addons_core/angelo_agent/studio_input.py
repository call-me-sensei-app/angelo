# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's input: the canvas's passive modal operator (clicks, typing, scrolling, drops), its start per window and the field caret.

Split from ``studio.py`` (its 500-line limit); ``studio`` owns the state and re-exports these names."""

import bpy
from bpy.types import Operator

from .input_policy import modal_options
from . import studio as core   # Rebound state (_enabled, _input_generation) is read through the module.
from . import canvas_controls, canvas_focus, chat_view as canvas, live_windows, scroll_input, studio_drag, studio_enter
from . import text_keys
from .studio_state import MULTILINE_FIELDS, error_text, refusable
from .ui_text import n_
from .studio import (  # noqa: E402 (studio imports this module after defining these)
    POPUP_ROW, _modals, _views, click, defer_drop, is_studio, library, redraw, view)


class ANGELO_OT_studio_input(Operator):
    bl_idname = "angelo.studio_input"
    bl_label = "Media Studio Input"
    bl_options = {'INTERNAL'} | modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    def invoke(self, context, event):
        self.window_pointer = context.window.as_pointer()
        self.input_generation = core._input_generation
        self.focus_area = None
        self.dragging = False
        _modals.add(self.window_pointer)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def moved_out(self, context, window, event):
        """A waiting press in a Studio area the pointer has left (a fast flick): start its drag all the same."""
        for pointer, state in list(_views.items()):
            pressed = state.get("asset_press")
            area = next((a for a in live_windows.areas(window) if a.as_pointer() == pointer and is_studio(a)), None) \
                if pressed else None
            region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
            if region is not None and not (region.x <= event.mouse_x < region.x + region.width
                                           and region.y <= event.mouse_y < region.y + region.height):
                if studio_drag.moved(context, window, area, region, state, *self.layout_point(state, region, event)):
                    return True
        return False

    def modal(self, context, event):
        if not core._enabled or getattr(self, "input_generation", core._input_generation) != core._input_generation:
            if getattr(self, "input_generation", core._input_generation) == core._input_generation:
                _modals.discard(self.window_pointer)
            return {'CANCELLED', 'PASS_THROUGH'}  # A retired handler: its last event belongs to what is below.
        window = context.window
        if not window or window.as_pointer() != self.window_pointer:
            return {'PASS_THROUGH'}
        from .input_policy import canvas_blocking, overlay_passes
        if canvas_blocking(window):  # Angelo's own overlays (the welcome, cards) leave this canvas usable.
            self.dragging = False
            studio_drag.forget(_views.values())
            return {'PASS_THROUGH'}
        if event.type == 'WINDOW_DEACTIVATE':
            studio_drag.forget(_views.values())
        if event.type == 'MOUSEMOVE' and self.moved_out(context, window, event):
            return {'RUNNING_MODAL'}   # A press that left its tile fast: the drag starts as it leaves.
        # Mouse release can occur in another editor or outside the window.
        # End selection drag before applying any region/focus filtering.
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.dragging = False
            released = [view.pop("pressed", None) for view in _views.values()]   # The primary's pressed look ends.
            for view_state in list(_views.values()):
                studio_click(window, view_state, studio_drag.released(view_state))
            if any(value is not None for value in released):
                redraw()
            return {'PASS_THROUGH'}
        mouse = 'MOUSE' in event.type or event.type in {'TRACKPADPAN', 'TRACKPADZOOM', 'EVT_DROP'}
        area = over = next((a for a in live_windows.areas(window) if is_studio(a)
                            and a.x <= event.mouse_x < a.x + a.width and a.y <= event.mouse_y < a.y + a.height), None)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and not area:
            self.focus_area = None
            for state in _views.values():
                state["field_focus"] = state["focus"] = None   # A click elsewhere ends editing and keyboard focus.
                state.pop("popover", None)
            redraw()
        if area is None and not mouse and self.focus_area:
            area = next((a for a in live_windows.areas(window) if is_studio(a) and a.as_pointer() == self.focus_area),
                        None)
        if not area or overlay_passes(event, context.window_manager):   # Timers, window events, the quit key.
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'WINDOW_DEACTIVATE'}:
                self.dragging = False
            return {'PASS_THROUGH'}
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if not region or (mouse and not (region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height)):
            return {'PASS_THROUGH'}
        state = view(area)
        x, y = self.layout_point(state, region, event)   # As the last draw laid things out (mirrored in Arabic).
        if event.type == 'EVT_DROP':
            defer_drop(window, area)
            return {'PASS_THROUGH'}
        popup_state = state.get("popover")
        if popup_state:
            if event.type in scroll_input.SCROLL_EVENTS:
                # Whole rows from the top; a trackpad swipe carries its remainder to its next event, never past an end.
                rest = popup_state.get("scroll_rest", 0.0) if event.type == 'TRACKPADPAN' else 0.0
                travel = rest - scroll_input.view_up(event, POPUP_ROW)
                rows = int(travel / POPUP_ROW)
                limit = max(0, len(popup_state["entries"]) - 4)
                scroll = max(0, min(limit, popup_state["scroll"] + rows))
                popup_state["scroll_rest"] = travel - rows * POPUP_ROW if scroll == popup_state["scroll"] + rows else 0.0
                popup_state["scroll"] = scroll
            elif event.type == 'ESC' and event.value == 'PRESS':
                state.pop("popover", None)
            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                choice = next((v for r, v in state.get("popup_hits", []) if canvas.inside(r, x, y)), None)
                if choice is not None:
                    action = popup_state["action"]
                    if action == "model":
                        state["models"][state["kind"]] = choice
                    elif action == "folder_picker" and choice in core.LIBRARY_ACTIONS:
                        state.pop("popover", None)
                        refusable(state, lambda: getattr(bpy.ops.angelo, core.LIBRARY_ACTIONS[choice])('INVOKE_DEFAULT'))
                    elif action == "folder_picker":
                        state.update(folder=choice, grid_scroll=0)
                    elif action.startswith(("frame:", "video_")):
                        from . import studio_video_ui
                        state.pop("popover", None)
                        with context.temp_override(window=window, area=area, region=region):
                            try:   # A frame that can't be read (too large, gone) is a notice, never the modal's end.
                                refusable(state, lambda: studio_video_ui.choose(bpy.context, state, action, choice))
                            except (ValueError, OSError) as error:
                                state["notice"] = error_text(error)
                    else:
                        state[action] = choice
                state.pop("popover", None)
            elif event.type == 'MOUSEMOVE':
                state["popup_hover"] = next((v for r, v in state.get("popup_hits", []) if canvas.inside(r, x, y)), None)
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and studio_drag.moved(context, window, area, region, state, x, y):
            area.tag_redraw()
            return {'RUNNING_MODAL'}   # Blender's asset drag has the pointer now.
        if event.type == 'MOUSEMOVE':
            if self.dragging and state.get("field_focus"):
                key = state["field_focus"]
                caret = point_to_caret(state, key, x, y)
                state["caret"] = caret
                state["selection"] = tuple(sorted((state.get("anchor", caret), caret)))
                area.tag_redraw()
                return {'RUNNING_MODAL'}
            if canvas_controls.pointer(window, area.tag_redraw, state, x, y, state.get("hits", [])[::-1]):
                return {'RUNNING_MODAL'}  # Keeps the hand: unconsumed, the console restores its text cursor.
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.dragging = False
            return {'PASS_THROUGH'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN'} and not state.get("prompt_dialog"):
            step = -scroll_input.view_up(event, 48)  # Measured down from each column's top.
            for column, key in (("create", "create_scroll"), ("folders", "folder_scroll"), ("assets", "grid_scroll")):
                if canvas.inside(state.get("geometry", {}).get(column, (0, 0, 0, 0)), x, y):
                    state[key] = max(0, min(state.get(key.replace("_scroll", "_max_scroll"), 0), state[key] + step))
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
        if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':   # A grid item's menu: what dragging it does.
            target = next(((r, a, v) for r, a, v in reversed(state.get("hits", [])) if canvas.inside(r, x, y)), None)
            if target and target[1] == "asset" and library().assets.get(target[2], {}).get("status") == "ready":
                from . import studio_item_menu
                studio_drag.forget([state])   # A waiting left press never becomes a drag behind the menu.
                with context.temp_override(window=window, area=area, region=region):
                    studio_item_menu.open_menu(state, target[2])
                return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.focus_area = area.as_pointer()
            target = next(((r, a, v) for r, a, v in reversed(state.get("hits", [])) if canvas.inside(r, x, y)), None)
            state["field_focus"] = state["focus"] = None   # A pointer click ends keyboard focus too.
            if target and target[1] == "asset" and studio_drag.available() and library().assets.get(target[2], {}).get("status") == "ready":
                studio_drag.press(state, target[2], target[0], area, event, x, y)   # A drag or, on release, a click.
                area.tag_redraw()
                return {'RUNNING_MODAL'}
            if target:
                rect, action, value = target
                state["pressed"] = (action, value)   # The primary button's pressed look until the release.
                self.activate(context, state, area, region, target)
                if action == "focus":
                    state["caret"] = point_to_caret(state, value, x, y)
                    state["anchor"] = state["caret"]
                    self.dragging = True
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        taken = self.keyboard(context, state, area, region, event, over is not None)   # Tab focus (canvas_focus).
        if taken is not None:
            return taken
        # Native popup/file-picker dialogs own their keyboard events. The
        # studio relinquishes focus before invoking any native modal dialog.
        key = state.get("field_focus")
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'RELEASE':
            target = studio_enter.on_release(state, key)   # Return runs a field's primary action on its release.
            if target is not None:
                self.activate(context, state, area, region, target)   # As a click on its button.
                area.tag_redraw()
                return {'RUNNING_MODAL'}
        if not key or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        modifier = event.ctrl or event.oskey
        value, caret = state[key], state["caret"]
        chosen, insert = state.get("selection"), None
        if modifier and event.type == 'A':
            state["selection"], state["caret"] = (0, len(value)), len(value)
        elif modifier and event.type in {'C', 'X'}:
            if chosen:
                context.window_manager.clipboard = value[chosen[0]:chosen[1]]
                if event.type == 'X':
                    value, caret = value[:chosen[0]] + value[chosen[1]:], chosen[0]
                    state["selection"] = None
        elif modifier and event.type == 'V':
            insert = context.window_manager.clipboard.replace("\r\n", "\n")
        elif event.type in {'BACK_SPACE', 'DEL'}:
            if chosen:
                value, caret = value[:chosen[0]] + value[chosen[1]:], chosen[0]
            elif event.type == 'BACK_SPACE' and caret:
                value, caret = value[:caret - 1] + value[caret:], caret - 1
            elif event.type == 'DEL':
                value = value[:caret] + value[caret + 1:]
            state["selection"] = None
        elif event.type in {'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
            previous = caret
            if event.type in {'UP_ARROW', 'DOWN_ARROW'}:
                rows = state.get("fields", {}).get(key, (None, ((value, 0),), 0))[1]
                index = max((i for i, (_, offset) in enumerate(rows) if offset <= caret), default=0)
                target = max(0, min(len(rows) - 1, index + (-1 if event.type == 'UP_ARROW' else 1)))
                caret = rows[target][1] + min(len(rows[target][0]), caret - rows[index][1])
            else:
                caret = max(0, caret - 1) if event.type == 'LEFT_ARROW' else min(len(value), caret + 1) if event.type == 'RIGHT_ARROW' else 0 if event.type == 'HOME' else len(value)
            state["selection"] = tuple(sorted((chosen[0] if chosen else previous, caret))) if event.shift else None
        elif event.type in {'RET', 'NUMPAD_ENTER'}:   # text_keys: Return acts, Shift+Return is a new line.
            if studio_enter.on_return(state, key, key in MULTILINE_FIELDS, event.shift,
                                      event.is_repeat) == text_keys.LINE:
                insert = "\n"
        elif event.type == 'ESC':
            state["field_focus"], state["selection"] = None, None
            state.pop("prompt_dialog", None)
        elif event.unicode and not modifier and not event.alt:
            insert = event.unicode
        else:
            return {'RUNNING_MODAL'}  # Never execute a studio prompt as Python.
        if insert is not None:
            if chosen:
                value, caret = value[:chosen[0]] + value[chosen[1]:], chosen[0]
            if key not in MULTILINE_FIELDS:
                insert = insert.replace("\n", " ")
            value, caret = value[:caret] + insert + value[caret:], caret + len(insert)
            state["selection"] = None
        state[key] = value[:16000 if key in MULTILINE_FIELDS else 200]
        state["caret"] = min(caret, len(state[key]))
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def keyboard(self, context, state, area, region, event, pointer_over):
        """Tab, the arrows, Return and Space on the Studio's controls (``canvas_focus``, in the order drawn), or None
        to pass the event on. Studio is a console area, so Tab is its own: from the canvas while the pointer is over
        it or a control is focused (otherwise it is the editor's under the pointer), and from a field being edited,
        which it leaves for the next control. Tab landing on a field enters it as a click does; Return or Space runs a
        control as its click (``activate``) on the key's release, so a held key never confirms Generate's dialog."""
        field = state.get("field_focus")
        if field is not None:
            if event.type != 'TAB' or event.value != 'PRESS' or event.ctrl or event.oskey or event.alt:
                return None   # The field's own keys (Return: ``studio_enter``).
            state.update(field_focus=None, selection=None, focus=("focus", field))
            state.pop("enter_armed", None)
        elif state.get("focus") is None and state.get("armed") is None and not pointer_over:
            return None
        target = canvas_focus.key(event, state, area.tag_redraw, drawn=True, on_release=True, wrap=True,
                                  enters=lambda key: key[0] == "focus", mirrored=state.get("mirror") is not None)
        if target is None:
            return None
        hit = canvas_focus.focused_hit(state) if target else None
        if hit is not None:
            self.activate(context, state, area, region, hit)
            if state.get("field_focus"):   # A field took the keyboard: its own outline shows, not the ring.
                canvas_focus.leave(state)
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    @staticmethod
    def layout_point(state, region, event):
        """The pointer in ``region`` as Studio's layout sees it: mirrored back about the axis its last draw mirrored
        about (``state["mirror"]``, a right-to-left language), so hits, carets and scrolls match what is on screen."""
        from . import canvas_rtl
        return canvas_rtl.layout_x(event.mouse_x - region.x, state.get("mirror")), event.mouse_y - region.y

    @staticmethod
    def activate(context, state, area, region, target):
        """Run a hit ``(rect, action, value)`` as its click, in the Studio's own area; a refusal becomes the notice
        (``refusable``). A pointer press and Return on a field's primary action both go through here."""
        rect, action, value = target
        with context.temp_override(window=context.window, area=area, region=region):
            refusable(state, lambda: click(bpy.context, state, action, value, rect))


def ensure_input(window, area):
    from .input_policy import canvas_blocking, handler_running
    if canvas_blocking(window):
        return
    if (window.as_pointer() not in _modals or not handler_running(window, "angelo.studio_input")) and not bpy.app.background:
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.angelo.studio_input('INVOKE_DEFAULT')


def point_to_caret(state, key, x, y):
    """The caret nearest layout point (``x``, ``y``) in field ``key``, where its draw put each caret."""
    from .studio_draw import _caret_x
    value = state[key]
    rect, rows, first = state.get("fields", {}).get(key, ((0, 0, 0, 0), ((value, 0),), 0))
    index = max(0, min(len(rows) - 1, first + int((rect[1] + rect[3] - 5 - y) / 19)))
    line, offset = rows[index]
    mirrored = state.get("mirror") is not None
    caret = min(range(len(line) + 1), key=lambda i: abs(rect[0] + 11 + _caret_x(line, i, mirrored) - x))
    return min(len(value), offset + caret)


def state_for(pointer):
    state = _views.get(int(pointer))
    if state is None:
        raise ValueError(n_("Media Studio has closed"))
    state["field_focus"] = None
    return state


def studio_click(window, state, released):
    """A press on an item that never became a drag: open it (the click it always was), or change the selection."""
    if not released:
        return
    action, identity, rect, area_pointer = released
    area = next((a for a in live_windows.areas(window) if a.as_pointer() == area_pointer and is_studio(a)), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if action == "open" and region is not None:
        with bpy.context.temp_override(window=window, area=area, region=region):
            refusable(state, lambda: click(bpy.context, state, "asset", identity, rect))
    if area is not None:
        area.tag_redraw()
