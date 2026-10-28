# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own editors keep no stock header, sidebar or toolbar, by construction (1.0.5, ux-coherence W2).

Manual's Media Studio and Generation Flows tabs and a flow's own window are one editor Angelo draws whole
(studio_entry_points.rst §3). Their header, sidebar and toolbar are hidden once, when the editor is made and when a
file is loaded, and kept hidden by events, never by polling: each editor's ``show_region_header`` /
``show_region_ui`` / ``show_region_toolbar`` is subscribed with ``bpy.msgbus``, so N, T or a script turning one on is
undone on the same notifier pass. A first cut polled every window every 0.5 s, which let stock chrome flash and
undid the person's own presses half a second late. The areas are also ``Area.angelo_owned`` (no region-toggle
arrows, part A).
"""

import bpy

from . import live_windows

CHROME = ("show_region_header", "show_region_ui", "show_region_toolbar")
# An editor just made is kept bare for this long (a new window may set its regions up a pass later), a bounded settle,
# never a poll. Hides are never animated (``turn_off``): an animated hide reads as shown until its slide ends.
SETTLE = 1.0
_owner = object()   # msgbus owner of every subscription here; cleared and renewed per file.
guarded = {}        # space pointer -> area pointer of each editor kept without chrome (GUI suites read it).
undone = []         # (space pointer, names) each time a subscription hid chrome again (GUI suites read it).


def shown(area):
    """The chrome flags still on in ``area``'s editor."""
    space = area.spaces.active
    return [name for name in CHROME if getattr(space, name, False)]


def _window_of(area):
    return next((w for w, a in live_windows.window_areas(bpy.context.window_manager)
                 if a.as_pointer() == area.as_pointer()), None)


def turn_off(window, area, names):
    """Set ``names`` off in ``area``'s editor at once. The window is in the context, the area never: Blender slides an
    overlapping region out only for the context's area, clearing its hidden flag for the slide (region_blend_start),
    so a guard that hid with the area in the context read the sidebar and toolbar as shown mid-slide, hid them again
    and restarted the slide, which a restarted hide ends shown (region_blend_end). The flow window and the Generation
    Flows tab kept both on screen after 73 such hides (interim q_9s6asi, 81ff7c4a9ed)."""
    space = area.spaces.active
    if window is None:
        with bpy.context.temp_override(area=None, region=None):
            for name in names:
                setattr(space, name, False)
        return
    with bpy.context.temp_override(window=window, screen=window.screen, area=None, region=None):
        for name in names:
            setattr(space, name, False)


def hide(area, window=None):
    """Hide the chrome in ``area``'s editor (with its window in the context when it is shown in one)."""
    names = shown(area)
    if names:
        turn_off(window or _window_of(area), area, names)
    return names


def _hide_space(space_pointer):
    for window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.spaces.active.as_pointer() == space_pointer:
            names = hide(area, window)
            if names:
                undone.append((space_pointer, names))
            return bool(names)
    return False


def _changed(space_pointer):
    """msgbus: a chrome flag of a guarded editor changed; turn it off again if it came on, now and once more a moment
    later. The interim dbe022dddb9 line pressed N and T over the Generation Flows tab: the hide made from the message
    came undone as Blender finished showing the region, and the sidebar and toolbar stayed. One follow-up per change
    the person (or a script) made, never a poll."""
    if _hide_space(space_pointer):
        bpy.app.timers.register(lambda: _hide_space(space_pointer) and None, first_interval=0.25)


def guard(area, window=None):
    """Hide ``area``'s chrome now, again over the next few passes (an editor just made may set its regions up a
    pass later), and keep it hidden by subscription."""
    from .editor_types import mark_angelo_owned
    window = window or _window_of(area)
    if window is not None:
        mark_angelo_owned(window, area)
    hide(area, window)
    space = area.spaces.active
    for name in CHROME:
        if hasattr(space, name):
            bpy.msgbus.subscribe_rna(key=space.path_resolve(name, False), owner=_owner,
                                     args=(space.as_pointer(),), notify=_changed)
    guarded[space.as_pointer()] = area.as_pointer()
    import time
    pointer, until = area.as_pointer(), time.monotonic() + SETTLE

    def settle():   # Bounded: SETTLE seconds after the editor is made, then the subscriptions alone keep it.
        target = next((a for _w, a in live_windows.window_areas(bpy.context.window_manager) if a.as_pointer() == pointer),
                      None)
        if target is not None:
            hide(target)
        return 0.05 if time.monotonic() < until else None
    bpy.app.timers.register(settle, first_interval=0.0)


def _shown(*_args):
    """msgbus (a window's workspace changed): hide the chrome of every Angelo-owned editor a window now shows.
    A tab's editor guarded while its workspace was not shown came back with the node editor's sidebar and toolbar on
    (interim dbe022dddb9 line, Generation Flows tab: show_region_ui and show_region_toolbar set without a message), so
    a tab is guarded again each time it is shown."""
    from . import flow_window
    windows = live_windows.windows(bpy.context.window_manager)
    owned = {a.as_pointer() for a in flow_window.owned_areas(windows, bpy.data.workspaces)}
    for window in windows:
        for area in live_windows.areas(window):
            if area.as_pointer() in owned:
                names = hide(area, window)
                if names:
                    undone.append((area.spaces.active.as_pointer(), names))


def guard_all():
    """Every Angelo-owned editor in the file: each flow window's and each Angelo tab's (``flow_window.owned_areas``).
    msgbus subscriptions end with the file they were made in, so this runs on every load."""
    from . import flow_window
    bpy.msgbus.clear_by_owner(_owner)
    guarded.clear()
    bpy.msgbus.subscribe_rna(key=(bpy.types.Window, "workspace"), owner=_owner, args=(), notify=_shown)
    for area in flow_window.owned_areas(live_windows.windows(bpy.context.window_manager), bpy.data.workspaces):
        guard(area)


@bpy.app.handlers.persistent
def _loaded(*_args):
    if not bpy.app.background:
        bpy.app.timers.register(lambda: guard_all() and None, first_interval=0.1)


class ANGELO_OT_owned_area_key(bpy.types.Operator):
    """N or T over an editor Angelo draws whole: nothing to show there (its sidebar and toolbar stay hidden)."""
    bl_idname = "angelo.owned_area_key"
    bl_label = "Angelo editor key"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):   # Elsewhere the key goes on to Blender's own N and T.
        return bool(context.area is not None and getattr(context.area, "angelo_owned", False))

    def execute(self, context):
        return {'FINISHED'}


# N and T in the editors Angelo draws whole: add-on keymap items come first (wm_keymap_addon_add), and a failed poll
# passes the key on, so only Angelo's tabs and windows lose them. Undoing the toggle after the fact did not hold on
# the Generation Flows tab (interim dbe022dddb9: the sidebar and toolbar came back after every hide).
# Only the node editor, in the keymap that binds its N and T ("Node Generic", blender_default.km_node_generic; an item
# in "Node Editor" never matched them, so a probe saw N shown and undone 11 times): Media Studio's console has no
# stock sidebar or toolbar, and its N and T are letters typed.
KEYMAPS = (("Node Generic", 'NODE_EDITOR'),)
_keymaps = []


def register():
    bpy.utils.register_class(ANGELO_OT_owned_area_key)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        for name, space_type in KEYMAPS:
            keymap = keyconfig.keymaps.new(name=name, space_type=space_type)
            for key in ('N', 'T'):
                _keymaps.append((keymap, keymap.keymap_items.new(ANGELO_OT_owned_area_key.bl_idname, key, 'PRESS')))
    bpy.app.handlers.load_post.append(_loaded)
    if not bpy.app.background:   # The first file's tabs (load_post covers every later one).
        bpy.msgbus.subscribe_rna(key=(bpy.types.Window, "workspace"), owner=_owner, args=(), notify=_shown)


def unregister():
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    bpy.utils.unregister_class(ANGELO_OT_owned_area_key)
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
    bpy.msgbus.clear_by_owner(_owner)
    guarded.clear()
