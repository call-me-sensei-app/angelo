# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Game Physics panel (Properties > Physics) and its popup (chat bar + menu > Game engine live link).

Both draw ``draw_settings``: the body and its mass, the shape fitted to the mesh (with Edit Fitted Size to take the
fit over by hand), collision only, the scene's physics format, and the findings about the object and what is below
it (``game_physics``). The settings are ``Object.angelo_physics`` from the glTF add-on, which writes them on every
glTF export. doc/angelo/game_physics.rst has the contract.
"""

import bpy

from . import game_physics
from .ui_text import iface_, n_, tip_

GEOMETRY = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META"})
PHYSICS_TYPES = GEOMETRY | {"EMPTY"}
ICONS = {game_physics.ERROR: 'CANCEL', game_physics.WARN: 'ERROR', game_physics.INFO: 'INFO'}
FITTED = {"BOX": n_("Fitted box: {x} × {y} × {z}"), "SPHERE": n_("Fitted sphere: radius {radius}"),
          "CAPSULE": n_("Fitted capsule: radius {radius}, height {height}"),
          "CYLINDER": n_("Fitted cylinder: radius {radius}, height {height}")}
POPUP_WIDTH = 380   # The popup's width in UI units (invoke_popup); its text wraps to it, not the region's.
HINT = n_("glTF exports write this as OMI physics, which Godot 4.4 and newer import as physics nodes")


def _shapes():
    from io_scene_gltf2.angelo_physics import shapes
    return shapes


def fitted(obj):
    """The shape ``obj``'s settings would fit to its geometry now (Blender axes), or None."""
    settings = obj.angelo_physics
    shapes = _shapes()
    if obj.type not in GEOMETRY or settings.shape not in shapes.PRIMITIVES:
        return None
    minimum, maximum = shapes.bounds(obj.bound_box)
    return shapes.fit_bounds(settings.shape, minimum, maximum, settings.axis)


def _length(context, value):
    units = context.scene.unit_settings
    return bpy.utils.units.to_string(units.system, 'LENGTH', value * units.scale_length, precision=3)


def draw_settings(layout, context, obj, width=None):
    """``width``: the UI units the text wraps to, when it isn't the region's (a popup's)."""
    settings = obj.angelo_physics
    shapes = _shapes()
    layout.use_property_split = True
    layout.use_property_decorate = False
    column = layout.column()
    column.prop(settings, "body")
    if settings.body == "RIGID":
        column.prop(settings, "mass")
    column.prop(settings, "shape")
    geometry = obj.type in GEOMETRY
    if settings.shape in shapes.PRIMITIVES:
        if geometry:
            column.prop(settings, "fit")
        if settings.shape in ("CAPSULE", "CYLINDER"):
            column.prop(settings, "axis")
        if settings.fit == "MANUAL" or not geometry:
            if settings.shape == "BOX":
                column.prop(settings, "size")
            else:
                column.prop(settings, "radius")
                if settings.shape != "SPHERE":
                    column.prop(settings, "height")
            column.prop(settings, "offset")
        else:
            fit = fitted(obj)
            row = column.row()
            row.label(text=iface_(FITTED[fit.kind], x=_length(context, fit.size[0]), y=_length(context, fit.size[1]),
                                  z=_length(context, fit.size[2]), radius=_length(context, fit.radius),
                                  height=_length(context, fit.height)), translate=False)
            row.operator(ANGELO_OT_game_physics_edit_fit.bl_idname, text="", icon='GREASEPENCIL')
    if geometry and settings.shape != "NONE":
        column.prop(settings, "collision_only")
    column.prop(context.scene, "angelo_physics_format")
    found = game_physics.scene_findings(context.scene, [obj] + list(obj.children_recursive))
    if found:
        box = layout.box()
        for finding in found:
            params = finding["params"]
            _wrapped(box, context, iface_(finding["message"], **params), ICONS[finding["severity"]], width)
            _wrapped(box, context, iface_(finding["fix"], **params), 'BLANK1', width)
    elif settings.body != "NONE" or settings.shape != "NONE":
        _wrapped(layout, context, iface_(HINT), 'INFO', width)


def _wrapped(layout, context, text, icon, width=None):
    """``text`` on as many labels as the editor's width needs (Blender cuts a long label), the icon on the first.
    Width in UI units: ``width`` (a popup's), else the region's pixels over the interface scale, less the panel's and box's padding, the
    label's text margins and its icon (``flow_text_field.MARGINS`` and 20). Right-to-left text stays one label:
    its catalogue holds it in display order, which a left-to-right wrap would scramble."""
    from . import canvas_rtl, flow_text_field
    if width is None:
        region = context.region
        width = (region.width if region is not None else 300) / max(context.preferences.system.ui_scale, 0.1)
    points = flow_text_field._points()
    lines = [text] if canvas_rtl.has_rtl(text) else flow_text_field.wrap(
        text, max(80, width - flow_text_field.MARGINS - 30), lambda part: flow_text_field._measure(part, points))
    column = layout.column(align=True)
    for index, line in enumerate(lines):
        column.label(text=line, icon=icon if index == 0 else 'BLANK1', translate=False)


class ANGELO_OT_game_physics_edit_fit(bpy.types.Operator):
    bl_idname = "angelo.game_physics_edit_fit"
    bl_label = "Edit Fitted Size"
    bl_description = "Switch the shape to Manual, starting from the size fitted to the mesh now"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type in GEOMETRY and fitted(obj) is not None

    def execute(self, context):
        obj = context.object
        settings, fit = obj.angelo_physics, fitted(obj)
        settings.offset = fit.center
        if fit.kind == "BOX":
            settings.size = fit.size
        else:
            settings.radius = fit.radius
            settings.height = fit.height
        if fit.axis is not None:
            settings.axis = "XYZ"[fit.axis]
        settings.fit = "MANUAL"
        return {'FINISHED'}


class ANGELO_PT_game_physics(bpy.types.Panel):
    bl_label = "Game Physics"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "physics"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type in PHYSICS_TYPES

    def draw(self, context):
        draw_settings(self.layout, context, context.object)


class ANGELO_OT_game_physics(bpy.types.Operator):
    bl_idname = "angelo.game_physics"
    bl_label = "Game Physics"
    bl_description = "Set the active object's collision shape and physics body for game engines"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or obj.type not in PHYSICS_TYPES:
            cls.poll_message_set(tip_("Select a mesh or an empty first"))
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=POPUP_WIDTH)

    def draw(self, context):
        self.layout.label(text=context.object.name, icon='PHYSICS', translate=False)
        draw_settings(self.layout, context, context.object, POPUP_WIDTH)

    def execute(self, context):
        return {'FINISHED'}


CLASSES = (ANGELO_OT_game_physics_edit_fit, ANGELO_PT_game_physics, ANGELO_OT_game_physics)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
