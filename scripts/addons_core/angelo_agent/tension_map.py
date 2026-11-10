# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tension map: where a posed, simulated, shape-keyed or sculpted mesh stretches and compresses (1.0.9).

Contract: doc/angelo/tension_map.rst. The measuring and the drawing are native: the **Tension** modifier writes each
vertex's strain to a float attribute (``tension`` by default; edges to ``tension_edge``), which an Attribute node
reads in EEVEE and Cycles, and the 3D view's tension overlay (``View3DOverlay.show_tension``) colours it blue
(compressed) to red (stretched), live while posing or playing. Blender inserts the modifier after the last
deform-only modifier (Armature, Cloth, ...), so a Subdivision Surface after it interpolates the result.

This module is the one click: the chat bar's + menu entry (**Tension map**) runs ``angelo.tension_map``, which adds
the modifier to the selected meshes that lack it (one Undo step), turns the overlay on in this window's 3D views and
opens the card (``tension_map_card``) with the rest shape and the range controls. The agent reaches the same modifier
through its typed ``modifier_add`` (type ``TENSION``).
"""

import bpy
from bpy.types import Operator

from . import tension_map_logic as logic
from .ui_text import iface_, n_

MENU_LABEL = n_("Tension Map")   # The feature's name, as its operator and overlay spell it.
MENU_WHY = n_("See where a posed or simulated mesh stretches (red) and compresses (blue)")
SECTION = n_("Inspect")
UNDO_ADD = n_("Tension Map")
UNDO_REST, UNDO_CAPTURE, UNDO_REMOVE = n_("Tension rest shape"), n_("Capture rest shape"), n_("Remove tension map")


def tension_modifier(obj):
    """The object's last Tension modifier (the one the overlay shows), or None."""
    found = [modifier for modifier in obj.modifiers if modifier.type == 'TENSION']
    return found[-1] if found else None


def targets(context):
    """The meshes a click measures: the active one first, then the other selected local meshes."""
    active = context.view_layer.objects.active
    candidates = list(context.selected_objects)
    if active is not None and active not in candidates:
        candidates.insert(0, active)
    names = logic.target_names([(obj.name, obj.type, obj.library is None, obj == active) for obj in candidates])
    return [bpy.data.objects[name] for name in names]


def ensure(obj):
    """The object's Tension modifier, added when it has none (Blender places it after the deform-only modifiers)."""
    return tension_modifier(obj) or obj.modifiers.new(logic.MODIFIER_NAME, 'TENSION')


def view3d_spaces(context):
    from . import live_windows
    window = context.window
    if window is None:
        return []
    return [area.spaces.active for area in live_windows.areas(window) if area.type == 'VIEW_3D']


def overlay_shown(context):
    return any(space.overlay.show_overlays and space.overlay.show_tension for space in view3d_spaces(context))


def show(context, on):
    """The map on or off in this window's 3D views. Turning it on also shows a view's overlays, which hold it."""
    for space in view3d_spaces(context):
        space.overlay.show_tension = on
        if on:
            space.overlay.show_overlays = True


def set_overlay(context, **values):
    """Range and opacity, in every 3D view of this window (``tension_stretch_max`` ...)."""
    for space in view3d_spaces(context):
        for name, value in values.items():
            setattr(space.overlay, name, value)


def overlay_values(context):
    spaces = view3d_spaces(context)
    overlay = spaces[0].overlay if spaces else None
    if overlay is None:
        return None
    return overlay.tension_stretch_max, overlay.tension_compress_max, overlay.tension_opacity


def capture_rest(obj):
    """Store the mesh's current shape as its rest (``tension_rest``) and measure against it. In Edit Mode the edit
    mesh holds the shape (and the mesh's attributes are not reachable): it gets the layer, kept when Edit Mode ends."""
    if obj.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        layers = bm.verts.layers.float_vector
        layer = layers.get(logic.REST_ATTRIBUTE) or layers.new(logic.REST_ATTRIBUTE)
        for vertex in bm.verts:
            vertex[layer] = vertex.co
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    else:
        mesh = obj.data
        coords = [0.0] * (3 * len(mesh.vertices))
        mesh.vertices.foreach_get("co", coords)
        attribute = mesh.attributes.get(logic.REST_ATTRIBUTE)
        if attribute is not None and (attribute.domain != 'POINT' or attribute.data_type != 'FLOAT_VECTOR'):
            mesh.attributes.remove(attribute)
            attribute = None
        if attribute is None:
            attribute = mesh.attributes.new(logic.REST_ATTRIBUTE, 'FLOAT_VECTOR', 'POINT')
        attribute.data.foreach_set("vector", coords)
        mesh.update()
    modifier = ensure(obj)
    modifier.rest_attribute = logic.REST_ATTRIBUTE
    modifier.rest_source = 'ATTRIBUTE'


def has_rest(obj, name):
    if obj.mode == 'EDIT':
        import bmesh
        return bmesh.from_edit_mesh(obj.data).verts.layers.float_vector.get(name) is not None
    return obj.data.attributes.get(name) is not None


def set_rest(objects, source, shape_key=None):
    for obj in objects:
        modifier = ensure(obj)
        if source == 'ATTRIBUTE' and not has_rest(obj, modifier.rest_attribute):
            capture_rest(obj)   # Nothing captured yet: the shape as it is now.
            continue
        if source == 'SHAPE_KEY':
            keys = obj.data.shape_keys
            names = [block.name for block in keys.key_blocks] if keys else []
            if shape_key in names:
                modifier.shape_key = shape_key
            elif modifier.shape_key not in names and names:
                modifier.shape_key = keys.reference_key.name
        modifier.rest_source = source


def shape_key_names(obj):
    keys = obj.data.shape_keys if obj is not None and obj.type == 'MESH' else None
    return [block.name for block in keys.key_blocks] if keys else []


def remove(objects):
    for obj in objects:
        for modifier in [m for m in obj.modifiers if m.type == 'TENSION']:
            obj.modifiers.remove(modifier)


def undo_push(message, window_pointer=None):
    """One Undo step for a change the card made. In the card's own window, as the photo drop card does: the card's
    actions run from its modal handler, whose context need not hold one."""
    from . import live_windows
    window = live_windows.find(bpy.context.window_manager, window_pointer) if window_pointer else None
    if window is None:
        bpy.ops.ed.undo_push(message=iface_(message))
        return
    with bpy.context.temp_override(window=window):
        bpy.ops.ed.undo_push(message=iface_(message))


class Live:
    """The card's source: the measured meshes (by name, so a deleted one drops out) and this window's 3D views."""

    OVERLAY = {"stretch": "tension_stretch_max", "compress": "tension_compress_max", "opacity": "tension_opacity"}

    def __init__(self, names, window_pointer=None):
        self.names = tuple(names)
        self.window_pointer = window_pointer   # The window the click came from: its 3D views, its Undo step.

    def objects(self):
        found = (bpy.data.objects.get(name) for name in self.names)
        return [obj for obj in found if obj is not None and obj.type == 'MESH']

    def snapshot(self):
        context = bpy.context
        measured = [(obj, tension_modifier(obj)) for obj in self.objects()]
        measured = [(obj, modifier) for obj, modifier in measured if modifier is not None]
        first = measured[0] if measured else (None, None)
        return logic.Snapshot(
            names=tuple(obj.name for obj, _ in measured), rest_sources=tuple(m.rest_source for _, m in measured),
            shape_key=first[1].shape_key if first[1] else "", shape_keys=tuple(shape_key_names(first[0])),
            attribute=first[1].attribute_name if first[1] else logic.ATTRIBUTE, shown=overlay_shown(context),
            values=overlay_values(context))

    def apply(self, action, value):
        context = bpy.context
        if action == "show":
            show(context, not overlay_shown(context))
        elif action in self.OVERLAY:
            set_overlay(context, **{self.OVERLAY[action]: float(value)})
        elif action == "rest" and value:
            set_rest(self.objects(), value)
            undo_push(UNDO_REST, self.window_pointer)
        elif action == "shape_key":
            set_rest(self.objects(), 'SHAPE_KEY', value)
            undo_push(UNDO_REST, self.window_pointer)
        elif action == "capture":
            for obj in self.objects():
                capture_rest(obj)
            undo_push(UNDO_CAPTURE, self.window_pointer)
        elif action == "remove":
            remove(self.objects())
            show(context, False)
            undo_push(UNDO_REMOVE, self.window_pointer)


class ANGELO_OT_tension_map(Operator):
    bl_idname = "angelo.tension_map"
    bl_label = "Tension Map"
    bl_description = "Show where the selected meshes stretch and compress"
    # No UNDO flag: Blender pushes no step for an operator a script runs, and the chat bar's + menu runs it from a
    # script, so execute pushes its own step (one, however it was started).
    bl_options = {'REGISTER'}

    def execute(self, context):
        from . import card_host, tension_map_card
        objects = targets(context)
        added = [obj for obj in objects if tension_modifier(obj) is None]
        for obj in objects:
            ensure(obj)
        show(context, True)
        if added:
            undo_push(UNDO_ADD, context.window.as_pointer() if context.window else None)
        if card_host.is_active(tension_map_card.KIND):
            card_host.close("replaced")
        window = context.window.as_pointer() if context.window else None
        card_host.request(tension_map_card.TensionCard(Live((obj.name for obj in objects), window)))
        card_host.pump(lambda: True)
        if not objects:
            self.report({'INFO'}, iface_(tension_map_card.NO_MESH))
        return {'FINISHED'}


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_tension_map)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "tension_map", MENU_LABEL, MENU_WHY, glyph="tension", order=47, section=SECTION,
        operator="angelo.tension_map", owner="tension_map"))


def unregister():
    from . import card_host, chat_destinations, tension_map_card
    chat_destinations.unregister("tension_map")
    if card_host.is_active(tension_map_card.KIND):
        card_host.close("done")
    card_host.withdraw(tension_map_card.KIND)
    bpy.utils.unregister_class(ANGELO_OT_tension_map)
