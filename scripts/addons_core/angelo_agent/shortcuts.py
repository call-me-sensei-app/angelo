# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's window-wide shortcuts: one definition each, registered in the add-on keyconfig and named from the keymap.

A window-wide shortcut lives in Blender's "Window" keymap, which Blender consults after every region and area keymap.
A chord any editor binds is therefore taken by that editor first: Ctrl+Option+D (1.0 to 1.0.2) was
``anim.driver_button_remove`` in the "User Interface" keymap (every header, sidebar, Properties, Outliner, timeline and
text region, even with no driver under the pointer) and ``outliner.drivers_delete_selected`` in the Outliner, so the
Director toggle worked only over the 3D viewport. Each window chord here is free in every non-modal keymap of
Blender's keymaps (default, left-click select, legacy, industry compatible); ``test_native_shortcuts`` keeps it so and
``test_blender_director_shortcut`` proves it in each editor. Registered on the add-on keyconfig, never in C, so a
keyconfig preset reload keeps them.
"""

from collections import namedtuple

Shortcut = namedtuple("Shortcut", "idname chord keymap space_type", defaults=("Window", 'EMPTY'))

# Director / Manual: Ctrl+Option+M (Ctrl+Alt+M), the maintainer's choice (2026-10-20). Shown wherever a shortcut is.
DIRECTOR = Shortcut("angelo.director_toggle", {"type": 'M', "ctrl": True, "alt": True})
# Show or hide the chat in Manual mode: Ctrl+Option+N (Ctrl+Alt+N).
CHAT = Shortcut("angelo.toggle_chat", {"type": 'N', "ctrl": True, "alt": True})
WINDOW_SHORTCUTS = (DIRECTOR, CHAT)
# 1.0's Ctrl+Option+D keeps working where it always did, over the 3D view's main region (the "3D View" keymap, which
# Blender's "User Interface" keymap is not on), for people who learned it. Its own operator, so no menu, tooltip or
# search ever names it: only DIRECTOR is shown.
DIRECTOR_VIEWPORT = Shortcut("angelo.director_toggle_viewport", {"type": 'D', "ctrl": True, "alt": True},
                             "3D View", 'VIEW_3D')


def add(keyconfig, shortcut):
    """Registers ``shortcut`` in the add-on keyconfig (its keymap); returns ``(keymap, item)`` for removal."""
    keymap = keyconfig.keymaps.new(name=shortcut.keymap, space_type=shortcut.space_type)
    chord = shortcut.chord
    modifiers = {key: chord[key] for key in ("ctrl", "alt", "shift", "oskey") if chord.get(key)}
    return keymap, keymap.keymap_items.new(shortcut.idname, chord["type"], 'PRESS', **modifiers)


def bound(idname):
    """The chord ``idname`` is bound to now (``ctrl``/``alt``/``shift``/``oskey``/``type``/``name``), or None.

    The user keyconfig includes the add-on's items once Blender has merged them; before that the add-on's own binding.
    An item the person switched off, or moved to a mouse button, has no keys to name.
    """
    import bpy
    keyconfigs = bpy.context.window_manager.keyconfigs
    items = []
    for keyconfig in (keyconfigs.user, keyconfigs.addon):
        keymap = keyconfig.keymaps.get("Window") if keyconfig else None
        items = [i for i in keymap.keymap_items if i.idname == idname] if keymap else []
        if items:
            break
    item = next((i for i in items if i.active), None)
    if item is None or item.map_type != 'KEYBOARD':
        return None
    name = bpy.types.Event.bl_rna.properties["type"].enum_items[item.type].name
    return {"ctrl": item.ctrl == 1, "alt": item.alt == 1, "shift": item.shift == 1, "oskey": item.oskey == 1,
            "type": item.type, "name": name}
