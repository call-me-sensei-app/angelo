# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tension map (1.0.9) in the compiled app, without a window: the Tension modifier's numbers and contracts.

Every case is a generic shape whose strain is known in closed form, never a production asset: a grid stretched,
squashed or sheared by a shape key; a shape key or a captured shape as the rest; a cylinder bent by an armature
(outside of the bend stretched, inside compressed); the modifier placed after the deform-only modifiers with Rest
Position turned on only when generated geometry comes before it; outputs that would overwrite built-in data
refused; Cycles reading the attribute through an Attribute node; save and reload; the 3D view's overlay settings
off by default with their defaults on files saved without them; the agent's generic ``modifier_add`` settings.
Report: ``results.json`` (with observed per-evaluation times, not checked: timing is the benchmark's).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh
from mathutils import Matrix, Vector

assert bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-tension-map"))
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        observed[name] = detail


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def grid(name="Grid", size=10):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=size, y_segments=size, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def shape_key(obj, name, transform, value=1.0):
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name=name, from_mix=False)
    for point, vertex in zip(key.data, obj.data.vertices):
        point.co = transform(vertex.co.copy())
    key.value = value
    return key


def evaluated(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).data


def values(mesh, name="tension", domain='POINT'):
    attribute = mesh.attributes.get(name)
    if attribute is None or attribute.domain != domain:
        return None
    found = [0.0] * len(attribute.data)
    attribute.data.foreach_get("value", found)
    return found


def edges_along(mesh, axis):
    """Edge indices of the mesh running along ``axis`` (0 x, 1 y) at rest."""
    result = []
    for edge in mesh.edges:
        a, b = (mesh.vertices[i].co for i in edge.vertices)
        delta = b - a
        if abs(delta[axis]) > 1e-6 and abs(delta[1 - axis]) < 1e-6:
            result.append(edge.index)
    return result


def close(a, b, tolerance=1e-4):
    return abs(a - b) <= tolerance


def stretch_cases():
    reset()
    obj = grid()
    shape_key(obj, "Stretch", lambda co: Vector((co.x * 1.5, co.y, co.z)))
    tension = obj.modifiers.new("Tension", 'TENSION')
    mesh = evaluated(obj)
    edge_values = values(mesh, "tension_edge", 'EDGE')
    along_x, along_y = edges_along(obj.data, 0), edges_along(obj.data, 1)
    check("stretch_x_edges_read_half", edge_values and all(close(edge_values[i], 0.5) for i in along_x))
    check("unstretched_y_edges_read_zero", edge_values and all(close(edge_values[i], 0.0) for i in along_y))
    point_values = values(mesh)
    interior = [v.index for v in obj.data.vertices if abs(v.co.x) < 0.95 and abs(v.co.y) < 0.95]
    # Two x edges at 1.5 and two y edges at 1.0 of the same rest length: (3 + 2) / 4 - 1.
    check("interior_vertex_is_the_rest_length_weighted_mean", point_values and all(
        close(point_values[i], 0.25) for i in interior))
    check("point_attribute_is_float_on_points", mesh.attributes["tension"].data_type == 'FLOAT')
    check("no_geometry_changed", len(mesh.vertices) == len(obj.data.vertices))

    obj.data.shape_keys.key_blocks["Stretch"].value = 0.0
    shape_key(obj, "Squash", lambda co: co * 0.8)
    mesh = evaluated(obj)
    point_values = values(mesh)
    check("uniform_compression_reads_negative", point_values and all(close(v, -0.2) for v in point_values))

    # A shape key as the rest: Basis -> Stretch (x 1.5) is the rest, Wide (x 2) is shown.
    for block in obj.data.shape_keys.key_blocks[1:]:
        block.value = 0.0
    shape_key(obj, "Wide", lambda co: Vector((co.x * 2.0, co.y, co.z)))
    tension.rest_source = 'SHAPE_KEY'
    tension.shape_key = "Stretch"
    edge_values = values(evaluated(obj), "tension_edge", 'EDGE')
    check("shape_key_rest_measures_from_that_key", edge_values and all(
        close(edge_values[i], 2.0 / 1.5 - 1.0) for i in along_x))
    tension.shape_key = "Missing"
    check("a_missing_shape_key_writes_nothing", values(evaluated(obj)) is None)

    # Sheared: the diagonal of each quad changes, its sides stay; no edge reads the shear as stretch.
    reset()
    obj = grid("Sheared")
    shape_key(obj, "Shear", lambda co: Vector((co.x + 0.3 * co.y, co.y, co.z)))
    obj.modifiers.new("Tension", 'TENSION')
    edge_values = values(evaluated(obj), "tension_edge", 'EDGE')
    along_x = edges_along(obj.data, 0)
    check("shear_keeps_parallel_edges_unstrained", edge_values and all(close(edge_values[i], 0.0) for i in along_x))


def captured_rest_case():
    """Captured: an edit of the mesh itself (as sculpting is) measured against the shape before it."""
    from angelo_agent import tension_map
    reset()
    obj = grid("Sculpted")
    tension_map.capture_rest(obj)
    modifier = tension_map.tension_modifier(obj)
    check("capture_sets_the_captured_rest", modifier.rest_source == 'ATTRIBUTE'
          and obj.data.attributes["tension_rest"].data_type == 'FLOAT_VECTOR')
    for vertex in obj.data.vertices:
        vertex.co.x *= 1.2
    obj.data.update()
    edge_values = values(evaluated(obj), "tension_edge", 'EDGE')
    check("captured_rest_sees_the_edit", edge_values and all(
        close(edge_values[i], 0.2) for i in edges_along(obj.data, 0)))
    modifier.rest_source = 'ORIGINAL'
    point_values = values(evaluated(obj))
    check("original_rest_follows_the_edit", point_values and all(close(v, 0.0) for v in point_values))

    # Captured in Edit Mode: the edit mesh's layer, kept when Edit Mode ends.
    reset()
    obj = grid("Edited")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    tension_map.capture_rest(obj)
    bpy.ops.object.mode_set(mode='OBJECT')
    rest = obj.data.attributes.get("tension_rest")
    check("capture_in_edit_mode_keeps_the_rest", rest is not None and rest.data_type == 'FLOAT_VECTOR'
          and all((rest.data[i].vector - obj.data.vertices[i].co).length < 1e-6 for i in range(len(obj.data.vertices))))


def bent_cylinder():
    reset()
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.25, depth=4.0, location=(0, 0, 2.0))
    body = bpy.context.object
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-4],
                              cuts=31, use_grid_fill=False)
    bm.to_mesh(body.data)
    bm.free()
    armature = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    lower = armature.edit_bones.new("Lower")
    lower.head, lower.tail = (0, 0, 0), (0, 0, 2.0)
    upper = armature.edit_bones.new("Upper")
    upper.head, upper.tail, upper.parent, upper.use_connect = (0, 0, 2.0), (0, 0, 4.0), lower, True
    bpy.ops.object.mode_set(mode='OBJECT')
    for name in ("Lower", "Upper"):
        body.vertex_groups.new(name=name)
    for vertex in body.data.vertices:
        z = vertex.co.z + 2.0   # The cylinder's local z runs -2 .. 2.
        weight = min(1.0, max(0.0, (z - 1.6) / 0.8))   # A soft joint around the bend.
        body.vertex_groups["Lower"].add([vertex.index], 1.0 - weight, 'REPLACE')
        body.vertex_groups["Upper"].add([vertex.index], weight, 'REPLACE')
    body.parent = rig
    body.matrix_parent_inverse = rig.matrix_world.inverted()
    body.modifiers.new("Armature", 'ARMATURE').object = rig
    rig.pose.bones["Upper"].rotation_mode = 'XYZ'
    rig.pose.bones["Upper"].rotation_euler = (math.radians(80.0), 0.0, 0.0)   # Bends sideways (bone x axis).
    return body


def armature_case():
    body = bent_cylinder()
    tension = body.modifiers.new("Tension", 'TENSION')
    check("added_after_the_armature", list(body.modifiers).index(tension) == 1)
    mesh = evaluated(body)
    point_values = values(mesh)
    # Which way it bent, from the deformed top: the inside of the bend faces that way.
    top = [v.index for v in body.data.vertices if v.co.z > 1.9]
    moved = sum((mesh.vertices[i].co for i in top), Vector()) / len(top)
    toward = Vector((moved.x, moved.y)).normalized()
    joint = [v for v in body.data.vertices if abs(v.co.z) < 0.25]   # Rest z near the joint (local 0).
    side = {v.index: Vector((v.co.x, v.co.y)).dot(toward) for v in joint}
    outside = [point_values[i] for i, d in side.items() if d < -0.2]
    inside = [point_values[i] for i, d in side.items() if d > 0.2]
    far = [point_values[v.index] for v in body.data.vertices if v.co.z < -1.2]
    check("bend_stretches_the_outside", outside and sum(outside) / len(outside) > 0.05,
          round(sum(outside) / max(1, len(outside)), 4))
    check("bend_compresses_the_inside", inside and sum(inside) / len(inside) < -0.05,
          round(sum(inside) / max(1, len(inside)), 4))
    check("away_from_the_joint_is_unstrained", far and max(abs(v) for v in far) < 1e-3)
    tension.smooth_iterations = 4
    smoothed = values(evaluated(body))
    spread = lambda vals: max(vals) - min(vals)  # noqa: E731
    check("smoothing_narrows_the_spread", spread(smoothed) < spread(point_values))


def placement_cases():
    reset()
    obj = grid("Stacked")
    rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    bpy.context.scene.collection.objects.link(rig)
    obj.modifiers.new("Mirror", 'MIRROR')
    obj.modifiers.new("Armature", 'ARMATURE').object = rig
    obj.modifiers.new("Subdivision", 'SUBSURF')
    tension = obj.modifiers.new("Tension", 'TENSION')
    check("placed_after_the_last_deform_before_subdivision",
          [m.type for m in obj.modifiers] == ['MIRROR', 'ARMATURE', 'TENSION', 'SUBSURF'])
    check("rest_position_on_after_generated_geometry", obj.add_rest_position_attribute)
    mesh = evaluated(obj)
    point_values = values(mesh)
    check("measured_through_generated_geometry_and_subdivided",
          point_values is not None and len(point_values) == len(mesh.vertices) > 2 * len(obj.data.vertices)
          and all(abs(v) < 1e-4 for v in point_values))

    reset()
    obj = grid("Subdivided")
    obj.modifiers.new("Subdivision", 'SUBSURF')
    obj.modifiers.new("Tension", 'TENSION')
    check("first_when_nothing_deforms", [m.type for m in obj.modifiers] == ['TENSION', 'SUBSURF'])
    check("rest_position_stays_off_without_generated_geometry", not obj.add_rest_position_attribute)

    reset()
    obj = grid("Cloth")
    obj.modifiers.new("Cloth", 'CLOTH')
    obj.modifiers.new("Tension", 'TENSION')
    check("cloth_is_measured", [m.type for m in obj.modifiers] == ['CLOTH', 'TENSION'])

    # Geometry a modifier makes with copied rest positions (Solidify's rim: zero rest length) reads 0 at rest, never
    # as stretch.
    reset()
    obj = grid("Shell")
    obj.modifiers.new("Solidify", 'SOLIDIFY').thickness = 0.2
    rig = bpy.data.objects.new("ShellRig", bpy.data.armatures.new("ShellRig"))
    bpy.context.scene.collection.objects.link(rig)
    obj.modifiers.new("Armature", 'ARMATURE').object = rig
    obj.modifiers.new("Tension", 'TENSION')
    point_values = values(evaluated(obj))
    check("copied_rest_positions_read_zero_at_rest", obj.add_rest_position_attribute and point_values
          and max(abs(v) for v in point_values) < 1e-4, max(abs(v) for v in point_values) if point_values else None)

    # Built-in names are refused rather than overwritten.
    reset()
    obj = grid("Guarded")
    tension = obj.modifiers.new("Tension", 'TENSION')
    tension.attribute_name = "position"
    mesh = evaluated(obj)
    check("a_built_in_output_name_is_refused", mesh.attributes["position"].data_type == 'FLOAT_VECTOR'
          and len(mesh.vertices) == len(obj.data.vertices))
    tension.attribute_name = "rest_position"
    check("blenders_rest_position_is_never_an_output", values(evaluated(obj), "rest_position") is None)
    tension.attribute_name, tension.edge_attribute_name = "tension", ""
    mesh = evaluated(obj)
    check("an_empty_edge_name_writes_no_edge_attribute",
          values(mesh) is not None and mesh.attributes.get("tension_edge") is None)


def ring_mesh(name, count, fan):
    """One n-gon of ``count`` vertices, or a triangle fan whose pole has valence ``count``."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    rim = [bm.verts.new((math.cos(2 * math.pi * i / count), math.sin(2 * math.pi * i / count), 0.0))
           for i in range(count)]
    if fan:
        pole = bm.verts.new((0.0, 0.0, 0.0))
        for i in range(count):
            bm.faces.new((pole, rim[i], rim[(i + 1) % count]))
    else:
        bm.faces.new(rim)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def evaluation_seconds(obj, key):
    """Median of 5 evaluations of the object's stack with the shape key moving (first one warms the caches)."""
    samples = []
    for step in range(6):
        key.value = 0.5 + 0.05 * step
        started = time.perf_counter()
        obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        bpy.context.evaluated_depsgraph_get().update()
        samples.append(time.perf_counter() - started)
    return sorted(samples[1:])[2]


def scaling_cases():
    """Large n-gons and high-valence poles stay linear (a per-face corner scan or a linear duplicate check made one
    N-sided face cost N^2: 40,000 vertices would be 1.6e9 steps). Each shape is measured at two sizes, 4x apart, with
    and without the modifier; the modifier's own time may grow at most 8x (linear is 4x, quadratic 16x)."""
    for fan in (False, True):
        times = {}
        for count in (10000, 40000):
            reset()
            obj = ring_mesh("Ring", count, fan)
            key = shape_key(obj, "Wide", lambda co: Vector((co.x * 1.1, co.y, co.z)))
            base = evaluation_seconds(obj, key)
            obj.modifiers.new("Tension", 'TENSION')
            times[count] = max(evaluation_seconds(obj, key) - base, 1e-5)
            point_values = values(evaluated(obj))
            check("%s_%d_measured" % ("fan" if fan else "ngon", count),
                  point_values is not None and all(math.isfinite(v) for v in point_values))
        label = "pole_valence" if fan else "ngon_size"
        observed[label + "_modifier_seconds"] = {str(k): round(v, 5) for k, v in times.items()}
        # A quadratic path costs seconds here; below 20 ms the ratio of two tiny times is only noise.
        check(label + "_stays_linear",
              times[40000] < 0.5 and (times[40000] < 0.02 or times[40000] / times[10000] < 8.0))

    # Loose edges (a wire mesh: no faces) are measured like any edges.
    reset()
    obj = grid("Wire")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.delete(bm, geom=list(bm.faces), context='FACES_ONLY')
    bm.to_mesh(obj.data)
    bm.free()
    shape_key(obj, "Stretch", lambda co: Vector((co.x * 1.5, co.y, co.z)))
    obj.modifiers.new("Tension", 'TENSION')
    point_values = values(evaluated(obj))
    interior = [v.index for v in obj.data.vertices if abs(v.co.x) < 0.95 and abs(v.co.y) < 0.95]
    check("loose_edges_are_measured", point_values and all(close(point_values[i], 0.25) for i in interior))


def cycles_reads_the_attribute():
    """A plane stretched on one half, emission = tension: Cycles shows the stretched half brighter."""
    reset()
    obj = grid("Shaded", size=16)
    shape_key(obj, "Half", lambda co: Vector((co.x * 2.0 if co.x > 0 else co.x, co.y, co.z)))
    obj.modifiers.new("Tension", 'TENSION')
    material = bpy.data.materials.new("Tension")
    tree = material.node_tree
    tree.nodes.clear()
    attribute = tree.nodes.new("ShaderNodeAttribute")
    attribute.attribute_name = "tension"
    emission = tree.nodes.new("ShaderNodeEmission")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(attribute.outputs["Fac"], emission.inputs["Strength"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    obj.data.materials.append(material)
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 4.5
    camera.location = (0.5, 0, 5)
    bpy.context.scene.collection.objects.link(camera)
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 4
    scene.render.resolution_x, scene.render.resolution_y = 64, 32
    path = OUT / "cycles-tension.png"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    pixels = list(image.pixels)
    width, height = image.size

    def brightness(x):
        row = height // 2
        index = (row * width + x) * 4
        return sum(pixels[index:index + 3])
    left, right = brightness(width // 5), brightness(width * 4 // 5)
    check("cycles_reads_the_tension_attribute", right > left + 0.2, {"left": round(left, 3), "right": round(right, 3)})


def save_and_reload():
    reset()
    obj = grid("Saved")
    tension = obj.modifiers.new("Tension", 'TENSION')
    tension.rest_source, tension.shape_key, tension.smooth_iterations = 'SHAPE_KEY', "Pose", 3
    path = OUT / "tension.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    loaded = bpy.data.objects["Saved"].modifiers["Tension"]
    check("settings_survive_save_and_reload", (loaded.type, loaded.rest_source, loaded.shape_key,
                                              loaded.smooth_iterations) == ('TENSION', 'SHAPE_KEY', "Pose", 3))


def overlay_defaults():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    spaces = [s for screen in bpy.data.screens for area in screen.areas for s in area.spaces if s.type == 'VIEW_3D']
    # The startup file was saved without these fields: versioning gives them their defaults, never zero ranges.
    check("overlay_off_by_default_with_its_defaults", spaces and all(
        not s.overlay.show_tension and close(s.overlay.tension_stretch_max, 0.25)
        and close(s.overlay.tension_compress_max, 0.25) and close(s.overlay.tension_opacity, 0.85) for s in spaces))
    prop = bpy.types.View3DOverlay.bl_rna.properties["show_tension"]
    check("overlay_switch_is_not_animatable", not prop.is_animatable)


def agent_generic_settings():
    from angelo_agent import action_schemas, generic_settings
    reset()
    obj = grid("Agent")
    generic_settings.check_type("TENSION", action_schemas.MODIFIER_SETTINGS)
    modifier = obj.modifiers.new("Angelo Tension", 'TENSION')
    context = SimpleNamespace(resolve=lambda reference: None, objects={}, aliases={})
    generic_settings.apply(context, obj, modifier, {"rest_source": "SHAPE_KEY", "shape_key": "Basis",
                                                    "smooth_iterations": 2}, "modifier")
    check("agent_bounds_it_as_data_only", generic_settings.bound_factor("TENSION", modifier) == 1)
    check("agent_writes_tension_settings_through_rna",
          (modifier.rest_source, modifier.shape_key, modifier.smooth_iterations) == ('SHAPE_KEY', "Basis", 2))


def timings():
    """Observed only: the modifier on a 250,000-vertex grid, per evaluation (benchmarks live elsewhere)."""
    reset()
    obj = grid("Dense", size=499)
    key = shape_key(obj, "Wave", lambda co: Vector((co.x * 1.1, co.y, co.z + 0.05 * math.sin(co.x * 9))))
    rows = {}
    for label, with_tension in (("without", False), ("with", True)):
        if with_tension:
            obj.modifiers.new("Tension", 'TENSION')
        samples = []
        for step in range(12):
            key.value = 0.5 + 0.04 * step
            started = time.perf_counter()
            bpy.context.evaluated_depsgraph_get().update()
            samples.append(time.perf_counter() - started)
        samples.sort()
        rows[label] = round(1000 * samples[len(samples) // 2], 2)
    observed["dense_grid_eval_ms_median"] = dict(rows, vertices=len(obj.data.vertices))


try:
    sys.path.insert(0, str(Path(bpy.utils.system_resource('SCRIPTS')) / "addons_core"))
    for case in (stretch_cases, captured_rest_case, armature_case, placement_cases, scaling_cases,
                 cycles_reads_the_attribute,
                 save_and_reload, overlay_defaults, agent_generic_settings, timings):
        try:
            case()
        except Exception:
            checks[case.__name__ + "_ran"] = False
            observed[case.__name__ + "_error"] = traceback.format_exc()
    report["success"] = bool(checks) and all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_TENSION_MAP", json.dumps({"success": report["success"],
                                         "failed": [k for k, v in checks.items() if not v]}), flush=True)
