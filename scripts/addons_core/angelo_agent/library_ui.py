# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's operators and entry points (doc/angelo/library.rst, "Where people find it").

* ``angelo.library_open``: the search pill opened (or the docked browser) in a 3D view of the window, the largest
  one when the call comes from elsewhere (the chat's + menu, a menu, a shortcut).
* ``angelo.library_add``: one item by id at the 3D cursor (a texture set on the selected objects, an HDRI as the
  world). Waits for the download, so scripts and tests can call it; the overlay's own add never waits.
* ``angelo.library_credits``: the credits of every library item in this file, copied to the clipboard.
* The chat bar's + menu row "Angelo Library" (``chat_destinations``), registered here.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator, Panel

from . import live_windows
from .ui_text import iface_, n_, rpt_

LABEL = n_("Angelo Library")
WHY = n_("Free models, materials and HDRIs, powered by ToonLab")
WAIT_SECONDS = 600.0


def target_view(context):
    """``(area, region)`` of the 3D view to open in: the context's own, else the largest in the window."""
    from . import library_overlay
    area = context.area if context.area is not None and context.area.type == 'VIEW_3D' else None
    if area is None and context.window is not None:
        views = [a for a in live_windows.areas(context.window) if a.type == 'VIEW_3D']
        area = max(views, key=lambda a: a.width * a.height) if views else None
    region = library_overlay.main_region(area) if area is not None else None
    return area, region


class ANGELO_OT_library_open(Operator):
    bl_idname = "angelo.library_open"
    bl_label = "Angelo Library"
    bl_description = "Search free models, materials and HDRIs and drop them into the scene"

    mode: EnumProperty(items=(('SEARCH', "Search", ""), ('BROWSE', "Browse", "")), default='SEARCH',
                       options={'SKIP_SAVE'})
    query: StringProperty(options={'SKIP_SAVE'})
    kind: EnumProperty(items=(('ANY', "All", ""), ('MODEL', "Models", ""), ('TEXTURE', "Materials", ""),
                              ('HDRI', "HDRIs", ""), ('VFX', "VFX", "")), default='ANY', options={'SKIP_SAVE'})
    lock: BoolProperty(description="Show only this kind (a surface for one kind: materials, the world)",
                       options={'SKIP_SAVE'})

    def execute(self, context):
        from . import library_overlay
        area, _region = target_view(context)
        if area is None:
            self.report({'ERROR'}, rpt_("Open a 3D view to use the Angelo Library"))
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, rpt_("The Angelo Library adds to the scene in Object Mode"))
            return {'CANCELLED'}
        state = library_overlay.state_for(area)
        if self.query:
            state.update(query=self.query[:200], caret=len(self.query[:200]), selected=None, scroll=0.0)
        kind = None if self.kind == 'ANY' else self.kind.lower()
        if kind is not None and not self.lock:
            state.update(kind=kind, category=None)
        library_overlay.open_overlay(area, self.mode.lower(), lock=kind if self.lock else None)
        return {'FINISHED'}

    def invoke(self, context, event):
        return self.execute(context)


class ANGELO_OT_library_add(Operator):
    bl_idname = "angelo.library_add"
    bl_label = "Add from Angelo Library"
    bl_description = "Add a library item at the 3D cursor; a material goes on the selected objects, an HDRI becomes the world"
    bl_options = {'REGISTER'}   # The import pushes its own Undo step.

    item_id: StringProperty(name="Item", options={'SKIP_SAVE'})
    resolution: StringProperty(name="Resolution", options={'SKIP_SAVE'})

    def execute(self, context):
        from . import library_host, library_import
        library = library_host.library()
        if library.catalog is None:
            library.ensure_catalog()
            _wait(lambda: library.catalog is not None or library.status == "offline", library)
        item = library.get(self.item_id)
        if item is None:
            self.report({'ERROR'}, rpt_("That item isn't in the Angelo Library"))
            return {'CANCELLED'}
        future = library.fetch(item, self.resolution or None)
        try:
            try:
                facts = future.result(timeout=WAIT_SECONDS)
            except Exception as error:   # The download's or the check's own sentence.
                self.report({'ERROR'}, library_host.describe(error)[0])
                return {'CANCELLED'}
            targets = [o for o in context.selected_objects if o.type == 'MESH']
            try:
                library_import.import_item(context, item, facts, targets=targets)
            except Exception as error:
                self.report({'ERROR'}, str(error)[:300])
                return {'CANCELLED'}
            return {'FINISHED'}
        finally:
            future.lease.release()   # Imported (or refused): the cache may trim the item again.


def _wait(done, library, seconds=60.0):
    import time
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        library.pump()
        time.sleep(0.02)
    library.pump()


class ANGELO_OT_library_credits(Operator):
    bl_idname = "angelo.library_credits"
    bl_label = "Angelo Library Credits"
    bl_description = "Copy the credits of every Angelo Library item in this file"

    def execute(self, context):
        from . import library_import
        records = library_import.credits()
        if not records:
            self.report({'INFO'}, rpt_("This file uses no Angelo Library items yet"))
            return {'FINISHED'}
        context.window_manager.clipboard = library_import.credits_text(records)
        needed = sum(1 for r in records if r.get("attribution_required"))
        if needed:
            self.report({'INFO'}, rpt_("Credits copied: {count} items, {needed} need attribution",
                                           count=len(records), needed=needed))
        else:
            self.report({'INFO'}, rpt_("Credits copied: {count} items, all CC0", count=len(records)))
        return {'FINISHED'}


class _Surface:
    """A properties tab where one kind of library item is used: the button opens the browser showing only it."""
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_options = {'DEFAULT_CLOSED'}
    KIND = ""
    TEXT = ""
    ICON = 'NONE'

    def draw(self, context):
        column = self.layout.column()
        column.scale_y = 1.4
        props = column.operator("angelo.library_open", text=iface_(self.TEXT), translate=False, icon=self.ICON)
        props.mode, props.kind, props.lock = 'BROWSE', self.KIND, True


class ANGELO_PT_library_materials(_Surface, Panel):
    bl_label = "Angelo Library Materials"
    bl_context = "material"
    bl_order = 100
    KIND, TEXT, ICON = 'TEXTURE', n_("Browse Library Materials"), 'MATERIAL'


class ANGELO_PT_library_world(_Surface, Panel):
    bl_label = "Angelo Library HDRIs"
    bl_context = "world"
    bl_order = 100
    KIND, TEXT, ICON = 'HDRI', n_("Browse Library HDRIs"), 'WORLD'


classes = (ANGELO_OT_library_open, ANGELO_OT_library_add, ANGELO_OT_library_credits, ANGELO_PT_library_materials,
           ANGELO_PT_library_world)


def register():
    from . import chat_destinations, library_overlay
    for cls in classes:
        bpy.utils.register_class(cls)
    library_overlay.register()
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "library", LABEL, WHY, glyph="library", order=14, section=chat_destinations.CREATE,
        operator="angelo.library_open", properties=(("mode", "BROWSE"),), owner="library_ui"))


def unregister():
    from . import chat_destinations, library_host, library_overlay
    chat_destinations.unregister("library")
    library_overlay.unregister()
    library_host.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
