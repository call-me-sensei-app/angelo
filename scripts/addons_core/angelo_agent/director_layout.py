# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A disposable Director workspace; the user's manual editors are never joined."""

import bpy

from . import console, docking
from .legacy_names import MANUAL_WORKSPACE, WORKSPACE_DIRECTOR, read_property, write_property


def is_director_workspace(workspace):
    """A workspace this host created for Director mode; files saved before the rename use the old flag key."""
    return bool(workspace is not None and read_property(workspace, WORKSPACE_DIRECTOR))


def manual_workspace_name(workspace):
    return read_property(workspace, MANUAL_WORKSPACE, "") if workspace is not None else ""


def is_director(window):
    return bool(window and is_director_workspace(window.workspace))


def configure(window):
    window.screen.show_statusbar = False
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            for name in ("show_region_header", "show_region_tool_header", "show_region_toolbar", "show_region_ui", "show_region_asset_shelf"):
                if hasattr(space, name) and not space.is_property_readonly(name):
                    setattr(space, name, False)
            space.show_gizmo = False
            # Overlays stay on so selection outlines, cameras and lights are visible;
            # only reference clutter (floor grid, axes, text, cursor) is hidden.
            space.overlay.show_overlays = True
            for name in ("show_floor", "show_axis_x", "show_axis_y", "show_axis_z", "show_text", "show_stats", "show_cursor"):
                if hasattr(space.overlay, name):
                    setattr(space.overlay, name, False)
            area.tag_redraw()


def build(window):
    from . import ui
    source = window.workspace
    existing_chat = next((a for a in window.screen.areas if console.is_chat(a)), None)
    draft = (existing_chat.spaces.active.history[-1].body, existing_chat.spaces.active.history[-1].current_character) if existing_chat and existing_chat.spaces.active.history else ui._drafts.get(ui.get_controller().state['id'], ('', 0))
    session = ui.get_controller().state['id']
    target = next((w for w in bpy.data.workspaces if is_director_workspace(w)), None)
    if target is None:
        if not any(a.type == 'VIEW_3D' for a in window.screen.areas):
            raise RuntimeError("Open a 3D Viewport before entering Director Mode")
        before = {w.as_pointer() for w in bpy.data.workspaces}
        with bpy.context.temp_override(window=window):
            result = bpy.ops.workspace.duplicate()
        target = next((w for w in bpy.data.workspaces if w.as_pointer() not in before), None)
        if result != {'FINISHED'} or target is None:
            raise RuntimeError("Could not create the Director workspace")
        target.name = "Director"
        write_property(target, WORKSPACE_DIRECTOR, True)
        from .studio_workspaces import place
        place()   # Director · Media Studio · Generation Flows, the tabs built first or not (never a switch).
    if not is_director_workspace(source):
        write_property(target, MANUAL_WORKSPACE, source.name)
    window.workspace = target
    yield .15
    # A previously saved Director layout needs only chrome enforcement.
    chats = [a for a in window.screen.areas if console.is_chat(a)]
    if len(window.screen.areas) == 2 and len(chats) == 1 and any(a.type == 'VIEW_3D' for a in window.screen.areas):
        configure(window)
        ui.setup_chat_area(window, chats[0])
        if ui.get_controller().state['id'] == session:
            chats[0].spaces.active.history[-1].body, chats[0].spaces.active.history[-1].current_character = draft
        return
    keep = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height, default=None)
    if keep is None:
        keep = max(window.screen.areas, key=lambda a: a.width * a.height)
        from .editor_types import set_editor
        set_editor(window, keep, area_type='VIEW_3D')
    pointer = keep.as_pointer()
    # Only operate on the duplicated workspace. Close native neighbors one at
    # a time and yield for geometry/notifiers; never hold a removed RNA area.
    while len(window.screen.areas) > 1:
        closed = False
        for area in list(window.screen.areas):
            if area.as_pointer() == pointer:
                continue
            with bpy.context.temp_override(window=window, area=area):
                if bpy.ops.screen.area_close.poll():
                    result = bpy.ops.screen.area_close()
                    closed = result == {'FINISHED'}
            if closed:
                break
        if not closed:
            raise RuntimeError("The Director copy has an unaligned native layout; manual editors were preserved")
        yield .08
    created = docking.split(window, pointer, "VERTICAL", .65)
    yield .1
    chat = max((docking.area_for(window, p) for p in (pointer, created)), key=lambda a: a.x)
    ui.setup_chat_area(window, chat)
    if ui.get_controller().state['id'] == session:
        chat.spaces.active.history[-1].body, chat.spaces.active.history[-1].current_character = draft
    configure(window)


def restore(window):
    from . import ui
    if not is_director(window):
        return
    ui.remember_draft(ui.get_controller())
    from .studio_workspaces import tab_kind
    target = bpy.data.workspaces.get(manual_workspace_name(window.workspace))
    if target is None or is_director_workspace(target):   # Never one of Angelo's own tabs (studio_workspaces).
        target = next((w for w in bpy.data.workspaces if not is_director_workspace(w) and tab_kind(w) is None), None)
    if target is None:
        raise RuntimeError("No manual workspace is available; use the native workspace picker")
    window.workspace = target
    yield .15
    # Restore the current draft to the manual chat, not an older hidden copy.
    draft = ui._drafts.get(ui.get_controller().state['id'], ('', 0))
    for area in window.screen.areas:
        if console.is_chat(area):
            ui.setup_chat_area(window, area)
            area.spaces.active.history[-1].body, area.spaces.active.history[-1].current_character = draft
    if not any(console.is_chat(a) for a in window.screen.areas) and any(a.type == 'VIEW_3D' for a in window.screen.areas):
        ui.request_chat_dock(window, "RIGHT", draft_override=(draft[0], draft[1], 18))
