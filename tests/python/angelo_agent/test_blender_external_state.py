# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_state``'s tool section on real RNA in every context mode Blender has, and every paint mode's brush.

The 2.0.1 audit found two readings the fake-window host tests could not: a text object in Edit Mode looked its tool
up under a rebuilt ``EDIT_FONT`` (Blender's context mode is ``EDIT_TEXT``; a metaball's ``EDIT_METABALL``, not
``EDIT_META``), and an active sculpt brush read ``unified_paint_settings`` from the tool settings, which hold none
(each paint mode's settings hold their own). Both answered ``{"unavailable": ...}``. This enters each context mode
of ``WorkSpaceTools.from_space_view3d_mode``'s own enum on a real object of its kind and reads the whole window
state there:

* nothing is ``unavailable``, and the tool's ``context_mode`` is ``bpy.context.mode`` (Blender's, never rebuilt);
* in every paint mode whose settings hold a brush, the brush is reported with the size Blender applies, with the
  brush's ``use_unified_size`` off (its own) and on (its paint settings' unified size, a value no brush has; the
  unified settings' own ``use_unified_size`` is a deprecated flag Blender no longer reads);
* every mode of the enum is entered, or listed in ``NOT_ENTERED`` with why it cannot be in this Blender.

Runs in background Blender, no window needed::

    blender -b --factory-startup --python-exit-code 1 --python tests/python/angelo_agent/test_blender_external_state.py

Contract: doc/angelo/external_mcp.rst, "Window state".
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import traceback
from types import SimpleNamespace as NS

import bpy
from bl_ui.properties_paint_common import UnifiedPaintPanel

CHECKOUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(CHECKOUT, "scripts", "addons_core"))
from angelo_agent import external_state  # noqa: E402

# Context modes of the enum that no object of this Blender can be in: the legacy Grease Pencil object type is gone
# (Blender 4.3 converts it to GREASEPENCIL on load); its modes remain in the enum for old files' tool keys.
NOT_ENTERED = {mode: "legacy Grease Pencil (object type removed in Blender 4.3)"
               for mode in ("PAINT_GPENCIL", "EDIT_GPENCIL", "SCULPT_GPENCIL", "WEIGHT_GPENCIL", "VERTEX_GPENCIL")}
UNIFIED_SIZE = 123   # A unified diameter no brush has by default: a size read from elsewhere shows.
UNIFIED_STRENGTH = 0.375   # Likewise a unified strength (exact in binary, so it reads back as set).


def check(condition, what):
    if not condition:
        raise AssertionError(what)


def enum_modes():
    tools = bpy.types.WorkSpace.bl_rna.properties["tools"].srna
    return {item.identifier for item in tools.functions["from_space_view3d_mode"].parameters["mode"].enum_items}


def link(name, data):
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def objects():
    """One object of each kind that has an Edit Mode or a paint mode: (object, object modes to enter)."""
    bpy.ops.mesh.primitive_cube_add()
    mesh = bpy.context.active_object
    mesh.data.uv_layers.new(name="UVMap")   # Texture Paint needs a UV map.
    bpy.ops.object.particle_system_add()
    mesh.particle_systems[0].settings.type = "HAIR"
    bpy.ops.curve.primitive_bezier_curve_add()
    curve = bpy.context.active_object
    bpy.ops.surface.primitive_nurbs_surface_sphere_add()
    surface = bpy.context.active_object
    text = link("Text", bpy.data.curves.new("Text", "FONT"))
    meta = bpy.data.metaballs.new("Meta")
    meta.elements.new()
    meta = link("Meta", meta)
    lattice = link("Lattice", bpy.data.lattices.new("Lattice"))
    bpy.ops.object.armature_add()
    armature = bpy.context.active_object
    hair = link("Hair", bpy.data.hair_curves.new("Hair"))
    hair.data.add_curves([4])
    points = link("Points", bpy.data.pointclouds.new("Points"))
    bpy.ops.object.grease_pencil_add(type="STROKE")
    pencil = bpy.context.active_object
    return [(mesh, ("EDIT", "SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT", "PARTICLE_EDIT")),
            (curve, ("EDIT",)), (surface, ("EDIT",)), (text, ("EDIT",)), (meta, ("EDIT",)), (lattice, ("EDIT",)),
            (armature, ("EDIT", "POSE")), (hair, ("EDIT", "SCULPT_CURVES")), (points, ("EDIT",)),
            (pencil, ("EDIT", "PAINT_GREASE_PENCIL", "SCULPT_GREASE_PENCIL", "WEIGHT_GREASE_PENCIL",
                      "VERTEX_GREASE_PENCIL"))]


def enter(obj, mode):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for other in bpy.context.view_layer.objects.selected:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode=mode)


def paint_of(mode):
    """The paint settings Blender's own brush panels read in ``mode`` (never the product's table)."""
    return UnifiedPaintPanel._paint_settings(bpy.context.scene.tool_settings, mode)


def check_brush(window, mode, paints):
    """The brush's size and strength both ways its own flags allow: its paint settings' unified values, and its own."""
    paint = paint_of(mode)
    brush = getattr(paint, "brush", None)
    state = external_state.read(bpy, window, None)
    if brush is None:
        check("brush" not in state["tool"], "%s: no brush, none reported: %s" % (mode, state["tool"]))
        return
    paints.append(mode)
    unified = getattr(paint, "unified_paint_settings", None)
    ways = [(False, brush.size, brush.strength)] + (
        [(True, unified.size, unified.strength)] if unified is not None else [])
    for use_unified, size, strength in ways:
        if hasattr(brush, "use_unified_size"):   # Particle Edit's brush: always its own.
            brush.use_unified_size = brush.use_unified_strength = use_unified
        got = external_state.read(bpy, window, None)["tool"].get("brush", {})
        check((got.get("diameter_px"), got.get("strength")) == (size, round(strength, 4)),
              "%s, unified %s: the brush's size and strength as Blender applies them (%s, %s): %s"
              % (mode, use_unified, size, strength, got))


def main():
    check(external_state.__file__.startswith(CHECKOUT), "the checkout's modules: %s" % external_state.__file__)
    window = NS(scene=bpy.context.scene, view_layer=bpy.context.view_layer,
                workspace=bpy.data.workspaces["Layout"], screen=bpy.data.screens["Layout"])
    for mode in enum_modes():
        unified = getattr(paint_of(mode), "unified_paint_settings", None)
        if unified is not None:
            unified.size, unified.strength = UNIFIED_SIZE, UNIFIED_STRENGTH
    entered, paints, failures = {"OBJECT"}, [], []
    state = external_state.read(bpy, window, None)
    check(state["tool"].get("context_mode") == "OBJECT", state["tool"])
    for obj, modes in objects():
        for object_mode in modes:
            try:
                enter(obj, object_mode)
            except Exception as error:   # noqa: BLE001 - every mode that can't be entered is reported below.
                failures.append("%s %s: %s" % (obj.type, object_mode, error))
                continue
            mode = bpy.context.mode
            entered.add(mode)
            state = external_state.read(bpy, window, None)
            missing = {name: value for name, value in state.items()
                       if isinstance(value, dict) and "unavailable" in value}
            check(not missing, "%s (%s %s): every section reads: %s" % (mode, obj.type, object_mode, missing))
            check(state["tool"]["context_mode"] == mode, "%s: Blender's own context mode: %s" % (mode, state["tool"]))
            check_brush(window, mode, paints)
    check(not failures, "every object mode entered: %s" % failures)
    never = enum_modes() - entered - set(NOT_ENTERED)
    check(not never, "every context mode entered or said why not: %s" % sorted(never))
    check("SCULPT" in paints, "a sculpt brush was read (Blender gives Sculpt Mode its default brush): %s" % paints)
    print("context modes entered: %s; brushes read in: %s" % (sorted(entered), sorted(paints)))
    print("EXTERNAL STATE ON BPY: OK")


try:
    main()
except Exception:  # noqa: BLE001 - the run's exit code is the test's verdict.
    traceback.print_exc()
    sys.exit(1)
