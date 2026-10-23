# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Show or hide the Angelo chat from the top right of the top bar, in Director and Manual alike, or with Ctrl+Option+N.

Before 1.0.2 a closed chat column could be found again only under Window > Angelo Chat. The toggle sits beside
the Director / Manual menu and is pressed while the chat is shown; its tooltip says what a click does
(doc/angelo/agent_chat.rst, "Show and hide"). Since 1.0.3 it is labelled *Chat*: the bare icon of 1.0.2 went
unnoticed, and a one-time hint (``chat_hint``) points at it the first time a person is in Manual mode and the first
time the chat disappears there. Since 1.0.4 the label also carries the shortcut as it is bound now (``Chat ⌃⌥N``),
and the button is the same in both modes: a person who could not find the chat in Manual mode (user report,
2026-10-21) had only ever seen Director, which drew no button, and the shortcut was named only in a tooltip.

* Hidden: Show docks the chat on its last side (``ui.request_chat_dock``) with the draft it had. In Director it
  rebuilds Director's own 3D view + chat split (``director.request_mode``), at Director's width.
* Docked in this window: Hide gives the chat's room back where Show took it (``chat_hide``) after keeping its
  draft; the conversation is the controller's and never changes. In Director the 3D view then
  fills the window; entering Director again shows the chat (Director is the 3D view and the chat).
* Popped out: Hide closes the pop-out window, keeping the draft; Show docks it again.

In Manual mode the chat also follows the person across workspace tabs (``follow``): a chat docked in Layout is docked
in Shading too when they switch to it, unless they hid it. Before 1.0.4 it lived in one tab's layout, so switching
tabs made it vanish with no action of theirs.

Its icon is drawn here, a speech bubble filled while the chat is shown and outlined while hidden, so the state
reads at a glance without a new image file. Ctrl+Option+N (``shortcuts.CHAT``) is free in every
non-modal keymap of Blender's keymaps; like every Angelo shortcut it is registered in the add-on keyconfig.
"""

import time

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import console
from .chat_icon import ICON_SIZE, bubble   # noqa: F401  The toggle's speech bubble (chat_toggle.bubble).
from . import top_bar_anchor
from .top_bar_anchor import anchor_rect, anchors   # noqa: F401  Where the button is (chat_toggle.anchors).
from .ui_text import iface_, join, n_

SHOW, HIDE = n_("Show the Angelo chat"), n_("Hide the Angelo chat")
LABEL = n_("Chat")   # Beside the icon (the catalogue entry the Chat menu's label already has).
NOT_SHOWN = n_("The chat couldn't open")
NOT_SHOWN_TRY = n_("Make room beside the 3D view, then try again, or open it from Window > Angelo Chat.")
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


def icon_id(shown, rgb=None):
    """Chat's bubble, filled while shown, in ``rgb`` (``glyph_tint.colour`` of the widget it sits on)."""
    global _icons
    from . import glyph_tint
    rgb = tuple(rgb or glyph_tint.DEFAULT)
    if _icons is None:
        from bpy.utils import previews
        _icons = previews.new()
    name = glyph_tint.key("shown" if shown else "hidden", rgb)
    if name not in _icons:
        preview = _icons.new(name)
        preview.icon_size = (ICON_SIZE, ICON_SIZE)
        preview.icon_pixels_float = bubble(shown, rgb=rgb)
    return _icons[name].icon_id




def button_units(text_width, unit):
    """The toggle's width in UI units: its icon, its label and Blender's padding, rounded up to a quarter unit, so
    the button is exactly as wide as ``anchor_rect`` says in any language."""
    import math
    return math.ceil((text_width / unit + 1.75) * 4) / 4



def button_text():
    """``Chat ⌃⌥N``: the label and the shortcut as bound now, as Blender's menus print it (``shortcuts.text``), in
    reading order. Just ``Chat`` when the person removed the shortcut."""
    from . import shortcuts
    from .ui_text import _visual_rtl
    keys = shortcuts.text(shortcuts.CHAT.idname)
    label = iface_(LABEL)
    if not keys:
        return label
    if _visual_rtl(label):   # The keys stay one left-to-right run ("⌃⌥N", not "N ⌥ ^"), left of the visual label.
        return keys + "  " + label
    return join([label, keys], "  ")


def draw(layout, context, compact=False, icon_only=False):
    """The toggle, pressed while the chat is shown (``director.draw_topbar``, both modes). It is the right-hand
    region's first item, at a measured width (``top_bar_fit``), and records where it is (``anchors``) for the hint's
    arrow."""
    from . import glyph_tint, top_bar_fit
    state = chat_state(context.window, context.window_manager.windows)[0]
    shown = state != "hidden"
    label = button_text()
    if compact:   # No room for the shortcut beside it (top_bar_fit.compact); the tooltip still names it.
        label = "" if icon_only else iface_(LABEL)
    row = top_bar_fit.fitted_row(layout, context, "chat", label)
    ink = glyph_tint.colour("tool", shown)   # The glyph in the button's own text colour, in every theme.
    row.operator("angelo.toggle_chat", text=label, translate=False, icon_value=icon_id(shown, ink), depress=shown,
                 emboss=True)
    region = context.region
    if context.window is not None and region is not None:
        _, units, unit = top_bar_fit.widths[context.window.as_pointer()]["chat"]
        top_bar_anchor.record(context, region, units, unit)


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
    from . import director, ui
    if director.is_director(window):
        # Director's own split, at Director's width, with the kept draft (``director_layout.build``).
        director.request_mode(window, "DIRECTOR", persist=False)
        return
    controller = ui.get_controller()
    value, caret = ui._drafts.get(controller.state["id"], ("", 0))
    ui.request_chat_dock(window, ui._dock_side, draft_override=(value, caret, _font), on_error=not_shown)


def open_from_menu(window, side):
    """Window > Angelo Chat. In Director: Director's own split, as its Chat button does, when this window shows no
    chat. In Manual: a dock on ``side`` (``ui.request_chat_dock``), moving the chat there if it is elsewhere."""
    from . import director, ui
    if director.is_director(window):
        if chat_state(window, bpy.context.window_manager.windows)[0] != "docked":
            show(window)
        return
    ui.request_chat_dock(window, side)


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
    from . import chat_hide
    _keep_draft(area)
    if state == "docked":
        chat_hide.request_hide(window, area)   # Undoes Show: the 3D view gets back the strip Show took.
        return
    with bpy.context.temp_override(window=window, area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
        bpy.ops.wm.window_close()


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
        from .display_text import Text, shown
        here = context.window   # Captured: closing an area leaves the context without a window.
        if here is None:
            return {'CANCELLED'}
        from . import chat_hide, director, ui
        if ui._layout_timer is not None or director._timer is not None:
            chat_hide.after_move(here)   # Applied once the chat stops moving, never refused.
            return {'FINISHED'}
        state, window, area = chat_state(here, context.window_manager.windows)
        try:
            if state == "hidden":
                show(here)
            else:
                hide(state, window, area)
            wanted[0] = state == "hidden"   # The person's own choice, which a workspace tab switch follows.
            from . import chat_launcher
            chat_launcher.update()   # Angelo in the corner comes (hidden) or goes (shown) at once.
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


# -- the chat follows workspace tabs (Manual mode) --------------------------------------------------------------
# Off in 1.0.4: a followed chat was sometimes not painted until the pointer moved over it (GUI runs, 2026-10-21/22),
# so it waits for 1.0.4.x. A tab without the chat shows Angelo in its corner (chat_launcher), one click away.
FOLLOW_TABS = False
wanted = [True]   # The person wants the chat shown: False once they hide it, True once it is shown again.
_seen = {}        # Window pointer -> Seen at the last observation.
_owner = object()  # The message bus subscription's owner.
_pending = {}     # Window pointer -> (workspace pointer, switched at, session): a follow waiting for the tab to draw.
SETTLE = 2.0      # The longest a follow waits for the new tab's first draw (seconds).


def observation(window, windows):
    """``(workspace, state, director, has_view)`` of ``window`` now: its workspace's pointer, ``chat_state``, whether
    it is Director's, and whether it has a 3D view to dock beside."""
    from . import director
    areas = window.screen.areas
    return (window.workspace.as_pointer() if window.workspace else 0, chat_state(window, windows)[0],
            director.is_director(window), any(a.type == 'VIEW_3D' for a in areas))


def follow_step(before, now, wants):
    """``(dock, wants)``: whether to dock the chat into the tab just shown, and the person's choice after ``now``.

    Pure, so the rules are host-tested. ``before``/``now`` are ``observation`` tuples (``before`` None at first).

    * A chat seen (docked or popped out) means they want it. A chat that disappears while the tab and the mode stay
      the same was hidden by them (the toggle, the shortcut, Blender's Close Area, a pop-out window closing).
    * A switch between two Manual tabs that leaves the chat hidden docks it in the new tab when they want it and the
      tab has a 3D view. A tab without one (Compositing, Video Editing) leaves the choice as it was for the next tab.
    * A mode change is the mode's own business (``director_layout.build``/``restore`` place the chat).
    """
    workspace, state, director, has_view = now
    if state != "hidden":
        return False, True
    if before is None:
        return False, wants
    same_tab = before[0] == workspace and before[2] == director
    if same_tab:
        return False, False if before[1] != "hidden" else wants
    switched_tabs = not before[2] and not director
    return switched_tabs and has_view and wants, wants


def tab_draft(workspace_pointer):
    """The unsent draft ``(text, caret)`` of the chat in the tab ``workspace_pointer`` (each tab's layout has its own
    chat area), or None."""
    workspace = next((w for w in bpy.data.workspaces if w.as_pointer() == workspace_pointer), None)
    for screen in workspace.screens if workspace is not None else ():
        for area in screen.areas:
            if console.is_chat(area) and area.spaces.active.history:
                line = area.spaces.active.history[-1]
                return line.body, line.current_character
    return None


def follow():
    """Watch the main window's tab and chat (``follow_step``) and dock the chat into a newly shown tab. Runs from the
    message bus the moment the workspace changes, and twice a second from ``_watch``. The draft goes with the person:
    the tab they left holds the one they were writing, so a followed chat, or a tab's own chat, takes it."""
    from . import chat_hint, director, ui
    window = chat_hint.main_window()
    if not FOLLOW_TABS or window is None or window.screen is None or bpy.app.background:
        return
    if director._timer is not None or ui._layout_timer is not None:
        return   # Mid-move: the chat may be briefly gone; the move's own result is observed next.
    key = window.as_pointer()
    before, now = _seen.get(key), observation(window, bpy.context.window_manager.windows)
    dock, wanted[0] = follow_step(before, now, wanted[0])
    _seen[key] = now
    switched = before is not None and before[0] != now[0] and not before[2] and not now[2]
    draft = tab_draft(before[0]) if switched and before[1] == "docked" else None
    session = ui.get_controller().state["id"]
    if draft is not None:
        ui._drafts[session] = draft
    if dock:
        # Dock once the new tab has drawn (``_dock_when_drawn``): a split made in the switch itself, before Blender
        # has laid the shown screen out, left the chat unpainted until the pointer crossed it (GUI runs 2026-10-21/22:
        # its draw ran, its region showed the split's freed editors). director_layout.restore waits likewise.
        _pending[key] = (now[0], time.monotonic(), session)
        if not bpy.app.timers.is_registered(_dock_when_drawn):
            bpy.app.timers.register(_dock_when_drawn, first_interval=0.05)
    elif draft is not None and now[1] == "docked":
        area = chat_state(window, bpy.context.window_manager.windows)[2]
        if area.spaces.active.history:
            area.spaces.active.history[-1].body, area.spaces.active.history[-1].current_character = draft
            area.tag_redraw()


def tab_drawn(window, since):
    """Whether a 3D view of ``window``'s current screen has drawn since ``since`` (``chat_launcher.drawn_at``: every
    3D view draw is recorded)."""
    from . import chat_launcher
    return any(chat_launcher.drawn_at.get(area.as_pointer(), 0.0) > since
               for area in window.screen.areas if area.type == 'VIEW_3D')


def _dock_when_drawn():
    """A follow's dock, once the tab it follows to has drawn (or after ``SETTLE`` seconds), if it still applies: the
    same tab, the chat still hidden, the person still wanting it. A follow that ends without a dock lets corner Angelo,
    held back while it was pending (``chat_launcher.target``), show now (``chat_launcher.settled``)."""
    from . import chat_hint, chat_launcher, ui
    window = chat_hint.main_window()
    pending = _pending.get(window.as_pointer()) if window is not None else None
    if pending is None:
        _pending.clear()
        chat_launcher.settled(None)
        return None
    workspace, since, session = pending
    if ((window.workspace.as_pointer() if window.workspace else 0) != workspace or not wanted[0]
            or chat_state(window, bpy.context.window_manager.windows)[0] != "hidden"
            or ui.get_controller().state["id"] != session):
        _pending.pop(window.as_pointer(), None)
        chat_launcher.settled(window.as_pointer())
        return None
    if ui._layout_timer is not None or (not tab_drawn(window, since) and time.monotonic() - since < SETTLE):
        return 0.05
    _pending.pop(window.as_pointer(), None)
    try:
        ui.request_chat_dock(window, ui._dock_side, draft_override=lambda: (*ui._drafts.get(session, ("", 0)), _font),
                             on_complete=followed, on_error=lambda error: print("Angelo: chat follow:", error))
    except RuntimeError as error:   # A tool holds the window, or the layout can't take a column: Chat still works.
        print("Angelo: chat follow:", error)
        chat_launcher.settled(window.as_pointer())
    return None


def followed(_window, area):
    """The chat is docked in the tab just shown: draw it, and the pressed Chat button, now rather than at the next
    event (a capture right after the dock still showed the split's copied editors, run 1, 2026-10-21)."""
    area.tag_redraw()
    redraw_top_bars()
    from . import chat_launcher
    chat_launcher.update()


def _follow_safely():
    """The message bus's callback on a workspace change: the follow, and corner Angelo moving to the tab just shown at
    once (it waited for the next watch, half a second or more under load: release GUI runs, 2026-10-22). A failure is
    printed, never raised into Blender's notifier loop."""
    from . import chat_launcher
    for step in (follow, chat_launcher.update):
        try:
            step()
        except Exception as error:   # noqa: BLE001
            print("Angelo: chat follow:", error)


def subscribe():
    """The window's workspace changing (a tab click, Ctrl+PageUp, a script) runs ``follow`` at once."""
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is None or bpy.app.background:
        return
    msgbus.clear_by_owner(_owner)
    msgbus.subscribe_rna(key=(bpy.types.Window, "workspace"), owner=_owner, args=(), notify=_follow_safely)


@persistent
def loaded(_):
    """After a file load: new windows and workspaces, and the message bus starts empty. Corner Angelo is placed once
    the loaded windows are ready (a one-shot timer), not at the next watch."""
    _seen.clear()
    _pending.clear()
    subscribe()
    from . import chat_launcher
    if not bpy.app.background:
        bpy.app.timers.register(chat_launcher.update, first_interval=0.1)


_last_states = None


def _watch():
    """Twice a second, for what notifies no one (Close Area, a window closing): when the chats' states change, the top
    bars redraw, corner Angelo moves (signalled changes move it at once) and the hint sees it (``chat_hint.observe``)."""
    global _last_states
    windows = bpy.context.window_manager.windows
    states = tuple(chat_state(window, windows)[0] for window in windows)
    if states != _last_states:
        _last_states = states
        redraw_top_bars()
    from . import chat_hint, ui
    try:
        follow()
    except Exception as error:   # Following must never stop the toggle's own watch.
        print("Angelo: chat follow:", error)
    from . import chat_launcher
    try:
        chat_launcher.update()   # Angelo in the corner while the chat is hidden, however it was hidden.
    except Exception as error:   # Never stop the toggle's own watch.
        print("Angelo: chat launcher:", error)
    if ui._layout_timer is not None or _pending:
        return 0.5   # Docking (perhaps into a tab just shown): the hint judges the result, not the moment between.
    try:
        chat_hint.observe()
    except Exception as error:   # A hint must never stop the toggle's own watch.
        print("Angelo: chat hint:", error)
    return 0.5


def register():
    bpy.utils.register_class(ANGELO_OT_toggle_chat)
    if not bpy.app.background:
        bpy.app.timers.register(_watch, first_interval=1.0, persistent=True)
        subscribe()
    bpy.app.handlers.load_post.append(loaded)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        from . import shortcuts
        _keymaps.append(shortcuts.add(keyconfig, shortcuts.CHAT))


def unregister():
    global _icons
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    if bpy.app.timers.is_registered(_dock_when_drawn):
        bpy.app.timers.unregister(_dock_when_drawn)
    _pending.clear()
    if loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(loaded)
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is not None:
        msgbus.clear_by_owner(_owner)
    _seen.clear()
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
