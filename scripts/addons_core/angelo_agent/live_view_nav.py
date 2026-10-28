# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which input a live view lets through to Blender: almost none.

A live view covers a 3D view of the artist's document that no one sees, so no event over it may reach that view or
its regions: not even an event that matches a window-wide keymap item, because Blender's Window, Screen Editing and
User Interface keymaps match left and right presses, X, Delete, I, E and K, find no button or action zone there and
hand them on to the 3D view (break-it P1-2 H1). The live view navigates itself (``live_nav``) and takes everything
else; an event passes on only when it is

* a window-wide command that never edits the document, bound in the artist's own keymap (``ALLOWED``: save, open,
  quit, preferences, full screen, workspaces, Angelo's own chat and studio toggles);
* a key the chat's field edits while the chat holds keyboard focus (``input_policy.edits_field``, P1-2 M2): other
  keys and shortcuts are held then too;
* on an area edge, where Blender resizes and joins areas (``session_rail_input``);
* on one of Angelo's own overlays in that view (``session_rail_input``).

Pure: events and keymap items are plain values (host-tested); ``session_rail_input`` reads them from Blender.
"""

from dataclasses import dataclass

GLOBAL_KEYMAPS = ("Window", "Screen")
ALLOWED = frozenset({
    "wm.save_mainfile", "wm.save_as_mainfile", "wm.open_mainfile", "wm.read_homefile", "wm.quit_blender",
    "screen.userpref_show", "wm.window_fullscreen_toggle", "screen.screen_full_area", "screen.workspace_cycle",
    "wm.window_new", "angelo.toggle_chat", "angelo.toggle_studio", "angelo.open_chat"})
ANY = -1


@dataclass(frozen=True)
class Event:
    type: str
    value: str
    shift: bool = False
    ctrl: bool = False
    alt: bool = False
    oskey: bool = False


@dataclass(frozen=True)
class Item:
    """A keymap item as matched: modifier fields are 1 (held), 0 (not held) or ANY."""
    idname: str
    type: str
    value: str
    shift: int = 0
    ctrl: int = 0
    alt: int = 0
    oskey: int = 0
    any: bool = False
    active: bool = True


def matches(item, event):
    if not item.active or item.type != event.type:
        return False
    if item.value not in (event.value, "ANY"):
        return False
    if item.any:
        return True
    for name in ("shift", "ctrl", "alt", "oskey"):
        wanted = getattr(item, name)
        if wanted != ANY and bool(wanted) != getattr(event, name):
            return False
    return True


def allowed(event, items):
    """Whether ``event`` is one of the window-wide commands a live view lets through (``ALLOWED``)."""
    return any(item.idname in ALLOWED and matches(item, event) for item in items)


def items_from(keyconfig):
    """``Item``s of ``keyconfig``'s window-wide keymaps bound to an ``ALLOWED`` command."""
    found = []
    for name in GLOBAL_KEYMAPS:
        keymap = keyconfig.keymaps.get(name) if keyconfig is not None else None
        for item in keymap.keymap_items if keymap is not None else ():
            if item.idname in ALLOWED:
                found.append(Item(item.idname, item.type, item.value, int(item.shift), int(item.ctrl),
                                  int(item.alt), int(item.oskey), bool(item.any), bool(item.active)))
    return tuple(found)


def event_from(event):
    return Event(event.type, event.value, bool(event.shift), bool(event.ctrl), bool(event.alt), bool(event.oskey))
