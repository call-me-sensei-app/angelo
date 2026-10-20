# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Show or hide the Angelo chat from the top right of the top bar (Manual mode), or with Ctrl+Option+N.

Before 1.0.2 a closed chat column could be found again only under Window > Angelo Chat. The toggle sits beside
the Director / Manual menu and is pressed while the chat is shown; its tooltip says what a click does
(doc/angelo/agent_chat.rst, "Show and hide").

* Hidden: Show docks the chat on its last side (``ui.request_chat_dock``) with the draft it had.
* Docked in this window: Hide closes the chat's area (Blender's own Close Area, so the neighbours take its room)
  after keeping its draft; the conversation is the controller's and never changes.
* Popped out: Hide closes the pop-out window, keeping the draft; Show docks it again.

Director always shows the chat beside the 3D view, so the toggle is Manual mode's (the shortcut reports why).
Its icon is drawn here, a speech bubble filled while the chat is shown and outlined while hidden, so the state
reads at a glance without a new image file. Ctrl+Option+N follows Angelo's Ctrl+Option+D (Director) and is free
in Blender's default keymap; like every Angelo shortcut it is registered in the add-on keyconfig.
"""

import bpy
from bpy.types import Operator

from . import console
from .ui_text import iface_, n_, rpt_

SHOW, HIDE = n_("Show the Angelo chat"), n_("Hide the Angelo chat")
DIRECTOR = n_("Director always shows the chat. Switch to Manual mode to hide it")
ICON_SIZE = 32
_icons = None
_keymaps = []


def chat_state(window, windows):
    """``("docked", window, area)`` for a chat in ``window``, ``("popped", other, area)`` for one in another
    window, else ``("hidden", None, None)``. ``windows`` is the window manager's windows."""
    found = [(w, a) for w in windows for a in w.screen.areas if console.is_chat(a)]
    here = next(((w, a) for w, a in found if w == window), None)
    if here:
        return ("docked",) + here
    if found:
        return ("popped",) + found[0]
    return "hidden", None, None


def bubble(filled, size=ICON_SIZE):
    """RGBA floats (bottom row first) of the toggle's speech bubble: filled with three cut-out dots while shown,
    an outline while hidden. Light on Angelo's teal top bar; 4x4 supersampled edges."""
    def inside_body(x, y):
        left, right, bottom, top, radius = 3.5, size - 3.5, 9.0, size - 4.0, 6.0
        cx = min(max(x, left + radius), right - radius)
        cy = min(max(y, bottom + radius), top - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 and left <= x <= right and bottom <= y <= top

    def inside_tail(x, y):   # A small triangle under the body's lower left: (8, 10), (15, 10), (6, 3).
        corners = ((8.0, 10.0), (15.0, 10.0), (6.0, 3.0))
        signs = [(bx - ax) * (y - ay) - (by - ay) * (x - ax) for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1])]
        return all(value >= 0 for value in signs) or all(value <= 0 for value in signs)

    def shape(x, y):
        return inside_body(x, y) or inside_tail(x, y)

    def dot(x, y):
        return any((x - cx) ** 2 + (y - 17.5) ** 2 <= 2.4 ** 2 for cx in (10.5, 16.0, 21.5))

    def inner(x, y):   # The outline variant's hollow: the body shrunk by 2.2 px.
        left, right, bottom, top, radius = 5.7, size - 5.7, 11.2, size - 6.2, 3.8
        cx = min(max(x, left + radius), right - radius)
        cy = min(max(y, bottom + radius), top - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 and left <= x <= right and bottom <= y <= top

    pixels = []
    for row in range(size):
        for column in range(size):
            hits = 0
            for sy in range(4):
                for sx in range(4):
                    x, y = column + (sx + 0.5) / 4, row + (sy + 0.5) / 4
                    if filled:
                        hits += shape(x, y) and not dot(x, y)
                    else:
                        hits += shape(x, y) and not inner(x, y)
            alpha = hits / 16
            pixels += (0.93 * alpha, 0.95 * alpha, 0.94 * alpha, alpha)   # Premultiplied, as Blender draws icons.
    return pixels


def icon_id(shown):
    global _icons
    if _icons is None:
        from bpy.utils import previews
        _icons = previews.new()
        for name, filled in (("shown", True), ("hidden", False)):
            preview = _icons.new(name)
            preview.icon_size = (ICON_SIZE, ICON_SIZE)
            preview.icon_pixels_float = bubble(filled)
    return _icons["shown" if shown else "hidden"].icon_id


def draw(layout, context):
    """The toggle, pressed while the chat is shown (``director.draw_topbar``, Manual mode)."""
    state = chat_state(context.window, context.window_manager.windows)[0]
    shown = state != "hidden"
    layout.operator("angelo.toggle_chat", text="", icon_value=icon_id(shown), depress=shown, emboss=True)


def _keep_draft(area):
    """The conversation keeps its draft and font while its chat is closed (``ui._drafts``)."""
    from . import ui
    space = area.spaces.active
    if space.history:
        ui._drafts[ui.get_controller().state["id"]] = (space.history[-1].body, space.history[-1].current_character)
    global _font
    _font = space.font_size


_font = 18


def show(window):
    from . import ui
    controller = ui.get_controller()
    value, caret = ui._drafts.get(controller.state["id"], ("", 0))
    ui.request_chat_dock(window, ui._dock_side, draft_override=(value, caret, _font))


def hide(state, window, area):
    from . import ui
    _keep_draft(area)
    with bpy.context.temp_override(window=window, area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
        if state == "popped":
            bpy.ops.wm.window_close()
        else:
            ui._dock_metadata.pop(area.as_pointer(), None)
            bpy.ops.screen.area_close()


class ANGELO_OT_toggle_chat(Operator):
    bl_idname = "angelo.toggle_chat"
    bl_label = "Show or Hide Chat"
    bl_description = "Show or hide the Angelo chat"
    bl_options = {'INTERNAL'}

    @classmethod
    def description(cls, context, properties):
        if context is None or context.window is None:
            return iface_(SHOW)
        return iface_(SHOW if chat_state(context.window, context.window_manager.windows)[0] == "hidden" else HIDE)

    def execute(self, context):
        from . import director
        from .display_text import Text, shown
        here = context.window   # Captured: closing an area leaves the context without a window.
        if here is None:
            return {'CANCELLED'}
        if director.is_director(here):
            self.report({'INFO'}, rpt_(DIRECTOR))
            return {'CANCELLED'}
        windows = context.window_manager.windows
        state, window, area = chat_state(here, windows)
        try:
            if state == "hidden":
                show(here)
            else:
                hide(state, window, area)
        except RuntimeError as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        redraw_top_bars()
        return {'FINISHED'}


def redraw_top_bars():
    """Rebuild every window's top bar, so the toggle's pressed state follows the chat. The top bar is a global
    area (not in ``Screen.areas``): only notifiers or the message bus reach it, and it subscribes to its
    workspace's tools."""
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is None:
        return
    # Each top bar subscribes to its own workspace's tools; a type-wide key would never reach an instance's
    # subscribers (Blender's message bus notifies upward from an instance, not downward from a type).
    for workspace in {window.workspace for window in bpy.context.window_manager.windows if window.workspace}:
        msgbus.publish_rna(key=workspace.path_resolve("tools", False))


_last_states = None


def _watch():
    """The chat also closes or pops out by other means (Close Area, Pop Out, a window closing): when the chats'
    states change, the top bars redraw. Cheap: one walk of the windows' areas twice a second."""
    global _last_states
    windows = bpy.context.window_manager.windows
    states = tuple(chat_state(window, windows)[0] for window in windows)
    if states != _last_states:
        _last_states = states
        redraw_top_bars()
    return 0.5


def register():
    bpy.utils.register_class(ANGELO_OT_toggle_chat)
    if not bpy.app.background:
        bpy.app.timers.register(_watch, first_interval=1.0, persistent=True)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        keymap = keyconfig.keymaps.new(name="Window", space_type='EMPTY')
        _keymaps.append((keymap, keymap.keymap_items.new("angelo.toggle_chat", 'N', 'PRESS', ctrl=True, alt=True)))


def unregister():
    global _icons
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    if _icons is not None:
        from bpy.utils import previews
        previews.remove(_icons)
        _icons = None
    bpy.utils.unregister_class(ANGELO_OT_toggle_chat)
