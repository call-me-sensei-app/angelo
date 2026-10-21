# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Show or hide the Angelo chat from the top right of the top bar (Manual mode), or with Ctrl+Option+N.

Before 1.0.2 a closed chat column could be found again only under Window > Angelo Chat. The toggle sits beside
the Director / Manual menu and is pressed while the chat is shown; its tooltip says what a click does
(doc/angelo/agent_chat.rst, "Show and hide"). Since 1.0.3 it is labelled *Chat*: the bare icon of 1.0.2 went
unnoticed, and a one-time hint (``chat_hint``) points at it the first time a person is in Manual mode and the first
time the chat disappears there.

* Hidden: Show docks the chat on its last side (``ui.request_chat_dock``) with the draft it had.
* Docked in this window: Hide closes the chat's area (Blender's own Close Area, so the neighbours take its room)
  after keeping its draft; the conversation is the controller's and never changes.
* Popped out: Hide closes the pop-out window, keeping the draft; Show docks it again.

Director always shows the chat beside the 3D view, so the toggle is Manual mode's (the shortcut reports why).
Its icon is drawn here, a speech bubble filled while the chat is shown and outlined while hidden, so the state
reads at a glance without a new image file. Ctrl+Option+N (``shortcuts.CHAT``) is free in every
non-modal keymap of Blender's keymaps; like every Angelo shortcut it is registered in the add-on keyconfig.
"""

import bpy
from bpy.types import Operator

from . import console
from .ui_text import iface_, join, n_, rpt_

SHOW, HIDE = n_("Show the Angelo chat"), n_("Hide the Angelo chat")
LABEL = n_("Chat")   # Beside the icon (the catalogue entry the Chat menu's label already has).
DIRECTOR = n_("Director always shows the chat. Switch to Manual mode to hide it")
NOT_SHOWN = n_("The chat couldn't open")
NOT_SHOWN_TRY = n_("Make room beside the 3D view, then try again, or open it from Window > Angelo Chat.")
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


anchors = {}   # window pointer -> (left, right, bottom): the toggle's rectangle in window pixels, as last drawn.


def button_units(text_width, unit):
    """The toggle's width in UI units: its icon, its label and Blender's padding, rounded up to a quarter unit, so
    the button is exactly as wide as ``anchor_rect`` says in any language."""
    import math
    return math.ceil((text_width / unit + 1.75) * 4) / 4


def anchor_rect(region_x, region_y, scale_factor, units, unit):
    """``(left, right, bottom)`` of the toggle in window pixels. It is the first item of the top bar's right-hand
    region, which Blender sizes to its content and lays out from ``4 * scale_factor`` pixels in
    (``ED_region_header_layout``: global areas use that offset), so the rectangle follows from the region alone."""
    left = region_x + int(4.0 * scale_factor)
    return left, left + units * unit, region_y


def draw(layout, context):
    """The toggle, pressed while the chat is shown (``director.draw_topbar``, Manual mode). It is the right-hand
    region's first item, at a measured width, and records where it is (``anchors``) for the hint's arrow."""
    import blf
    state = chat_state(context.window, context.window_manager.windows)[0]
    shown = state != "hidden"
    label = iface_(LABEL)
    system = context.preferences.system
    unit = round(18.0 * system.ui_scale) + 2 * system.pixel_size   # UI_UNIT_X (wm_window.cc).
    blf.size(0, context.preferences.ui_styles[0].widget.points * system.ui_scale)
    units = button_units(blf.dimensions(0, label)[0], unit)
    row = layout.row()
    row.ui_units_x = units
    row.operator("angelo.toggle_chat", text=label, translate=False, icon_value=icon_id(shown), depress=shown,
                 emboss=True)
    region = context.region
    if context.window is not None and region is not None:
        anchors[context.window.as_pointer()] = anchor_rect(region.x, region.y, system.ui_scale, units, unit)


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
    ui.request_chat_dock(window, ui._dock_side, draft_override=(value, caret, _font), on_error=not_shown)


def not_shown(error):
    """The chat could not dock: said on a corner card, since the hidden chat can't show its own error (a Show that
    seemed to do nothing). Evidence: after Blender's Close Area, the dock refused and the reason went to the chat."""
    from . import app_notices, card_host, update_card
    from .display_text import Text, shown
    reason = shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else iface_(str(error))
    body = join([reason, iface_(NOT_SHOWN_TRY)], " ")   # Reading order kept in a right-to-left language.
    card_host.request(update_card.NoticeCard("alert", iface_(NOT_SHOWN), body, lifetime=12))
    card_host.pump(app_notices.ready)


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
    states change, the top bars redraw, and the first-time hint sees the change (``chat_hint.observe``). Cheap: one
    walk of the windows' areas twice a second."""
    global _last_states
    windows = bpy.context.window_manager.windows
    states = tuple(chat_state(window, windows)[0] for window in windows)
    if states != _last_states:
        _last_states = states
        redraw_top_bars()
    from . import chat_hint
    try:
        chat_hint.observe()
    except Exception as error:   # A hint must never stop the toggle's own watch.
        print("Angelo: chat hint:", error)
    return 0.5


def register():
    bpy.utils.register_class(ANGELO_OT_toggle_chat)
    if not bpy.app.background:
        bpy.app.timers.register(_watch, first_interval=1.0, persistent=True)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        from . import shortcuts
        _keymaps.append(shortcuts.add(keyconfig, shortcuts.CHAT))


def unregister():
    global _icons
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    from . import chat_hint
    chat_hint.reset()
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    if _icons is not None:
        from bpy.utils import previews
        previews.remove(_icons)
        _icons = None
    bpy.utils.unregister_class(ANGELO_OT_toggle_chat)
