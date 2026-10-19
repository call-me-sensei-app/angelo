# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic native evaluation regressions; no external .blend assets or provider.

Run Angelo --background --factory-startup --disable-autoexec --python-exit-code 1
--python this_file with ANGELO_QA_DIR and Blender's user/recovery paths isolated.
The same checks run against the unmodified and optimized binaries. Assertions
verify evaluated results, not the implementation's source text.
"""

import json
import math
import os
from pathlib import Path
import traceback

import bpy

assert bpy.app.background, "This fixture requires a background scratch process"
assert "--factory-startup" in __import__("sys").argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
bpy.context.preferences.edit.use_global_undo = True
report = {"checks": {}, "binary": bpy.app.binary_path, "build_hash": bpy.app.build_hash.decode()}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def close(actual, expected, tolerance=1e-5):
    return all(abs(a - b) <= tolerance for a, b in zip(actual, expected))


def reset():
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bpy.data.batch_remove(tuple(bpy.data.collections))
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()


def empty(name, location=(0, 0, 0), collection=None):
    obj = bpy.data.objects.new(name, None)
    (collection or bpy.context.scene.collection).objects.link(obj)
    obj.location = location
    return obj


def position(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    return tuple(obj.evaluated_get(graph).matrix_world.translation)


def parent_chain():
    reset()
    root = empty("Root")
    parent = root
    children = []
    for index in range(32):
        child = empty("Child_%02d" % index, (1, 2, 3))
        child.parent = parent
        children.append(child)
        parent = child
    unrelated = empty("Unchanged", (7, 8, 9))
    for step, value in enumerate((2, -4, 0, 13)):
        root.location.x = value
        bpy.context.view_layer.update()
        check("parent_chain_%d" % step, close(position(children[-1]), (value + 32, 64, 96)))
        check("unrelated_%d" % step, close(position(unrelated), (7, 8, 9)))


def constraints_drivers_and_visibility():
    reset()
    source = empty("Source", (1, 0, 0))
    constrained = empty("Constrained")
    constraint = constrained.constraints.new("COPY_LOCATION")
    constraint.target = source
    driven = empty("Driven")
    curve = driven.driver_add("location", 0)
    variable = curve.driver.variables.new()
    variable.name = "source_x"
    variable.type = "TRANSFORMS"
    variable.targets[0].id = source
    variable.targets[0].transform_type = "LOC_X"
    variable.targets[0].transform_space = "WORLD_SPACE"
    curve.driver.expression = "source_x * 2"
    for step, hidden in enumerate((False, True, False, True)):
        source.hide_viewport = hidden
        source.location.x = step + 2
        bpy.context.view_layer.update()
        check("hidden_dependency_constraint_%d" % step,
              close(position(constrained), (step + 2, 0, 0)))
        check("hidden_dependency_driver_%d" % step,
              close(position(driven), (2 * (step + 2), 0, 0)))
    constraint.target = driven
    source.location.x = 11
    bpy.context.view_layer.update()
    check("changed_relation", close(position(constrained), (22, 0, 0)))


def animation_and_modifier():
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = "AnimatedMesh"
    modifier = obj.modifiers.new("Array", "ARRAY")
    modifier.count = 2
    modifier.use_merge_vertices = False
    for frame, location, hidden in ((1, 0, False), (5, 4, True), (9, -3, False)):
        obj.location.x = location
        obj.keyframe_insert("location", index=0, frame=frame)
        obj.hide_viewport = hidden
        obj.keyframe_insert("hide_viewport", frame=frame)
    for frame, expected in ((9, -3), (1, 0), (9, -3), (1, 0)):
        bpy.context.scene.frame_set(frame)
        check("animated_transform_%d_%s" % (frame, expected), close(position(obj), (expected, 0, 0)))
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        check("evaluated_array_%d" % frame, len(evaluated.data.vertices) == 16)
    bpy.context.scene.frame_set(5)
    check("animated_visibility_hidden", obj.hide_viewport)
    bpy.context.scene.frame_set(9)
    check("animated_visibility_restored", not obj.hide_viewport)
    modifier.count = 3
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    check("modifier_changed", len(evaluated.data.vertices) == 24)


def collections_and_graph_rebuild():
    reset()
    collection = bpy.data.collections.new("Switchable")
    bpy.context.scene.collection.children.link(collection)
    target = empty("CollectionMember", collection=collection)
    layer = bpy.context.view_layer.layer_collection.children[collection.name]
    for index, excluded in enumerate((True, False, True, False)):
        layer.exclude = excluded
        bpy.context.view_layer.update()
        check("collection_visibility_%d" % index,
              (target.name in bpy.context.view_layer.objects) == (not excluded))
    target.location = (4, 5, 6)
    bpy.context.view_layer.update()
    check("collection_restored_evaluation", close(position(target), (4, 5, 6)))
    name = target.name
    bpy.data.objects.remove(target, do_unlink=True)
    bpy.context.view_layer.update()
    replacement = empty(name, (9, 8, 7), collection)
    bpy.context.view_layer.update()
    check("delete_recreate_same_name", close(position(replacement), (9, 8, 7)))


def cyclic_dependencies():
    reset()
    a, b = empty("CycleA", (2, 3, 4)), empty("CycleB", (5, 6, 7))
    # Zero influence keeps the expected result defined while retaining relation cycles.
    for source, target in ((a, b), (b, a)):
        constraint = source.constraints.new("COPY_LOCATION")
        constraint.target = target
        constraint.influence = 0
    for index in range(5):
        a.location.x = index
        bpy.context.view_layer.update()
        check("cycle_stable_%d" % index, close(position(a), (index, 3, 4)))
        check("cycle_finite_%d" % index, all(math.isfinite(v) for v in position(b)))


def metaball_evaluation():
    reset()
    data = bpy.data.metaballs.new("Metaball")
    data.resolution = .3
    element = data.elements.new()
    element.radius = 1
    obj = bpy.data.objects.new("Metaball", data)
    bpy.context.scene.collection.objects.link(obj)
    extents = []
    for radius in (1, 2, 1):
        element.radius = radius
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            check("metaball_geometry_%s" % radius, len(mesh.vertices) > 0)
            extents.append(max(v.co.x for v in mesh.vertices) - min(v.co.x for v in mesh.vertices))
        finally:
            evaluated.to_mesh_clear()
    check("metaball_grows", extents[1] > extents[0] * 1.5)
    check("metaball_returns", abs(extents[0] - extents[2]) < 1e-5)


def armature_and_shape_key_rebuild():
    reset()
    bpy.ops.object.armature_add(enter_editmode=True)
    rig = bpy.context.object
    rig.data.edit_bones[0].name = "Root"
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh = bpy.data.meshes.new("DeformedMesh")
    positions = [(1, 0, 0), (2, 0, 0), (1, 1, 0)]
    mesh.from_pydata(positions, [], [(0, 1, 2)])
    obj = bpy.data.objects.new("DeformedObject", mesh)
    bpy.context.scene.collection.objects.link(obj)
    group = obj.vertex_groups.new(name="Root")
    group.add([0, 1, 2], 1, "REPLACE")
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Shift")
    for vertex in key.data:
        vertex.co.x += .5
    control = empty("ShapeControl")
    curve = key.driver_add("value")
    variable = curve.driver.variables.new()
    variable.name = "amount"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = control
    variable.targets[0].data_path = "location.x"
    curve.driver.expression = "amount"
    for index, (translation, amount) in enumerate(((0, 0), (3, 1), (-2, .5), (0, 0))):
        rig.pose.bones["Root"].location.x = translation
        control.location.x = amount
        # Creating and removing this ID forces graph reconstruction around each edit.
        probe = empty("RebuildProbe")
        bpy.context.view_layer.update()
        bpy.data.objects.remove(probe, do_unlink=True)
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        check("rig_shape_rebuild_%d" % index,
              len(evaluated.data.vertices) == 3 and len(evaluated.data.polygons) == 1 and all(
                  close(vertex.co, (original[0] + translation + .5 * amount, original[1], original[2]))
                  for vertex, original in zip(evaluated.data.vertices, positions)))
        check("rig_original_unchanged_%d" % index,
              len(mesh.vertices) == 3 and len(mesh.polygons) == 1 and
              all(close(vertex.co, original) for vertex, original in zip(mesh.vertices, positions)))


def post_update_second_pass():
    reset()
    parent, child = empty("CallbackParent"), empty("CallbackChild", (1, 0, 0))
    child.parent = parent
    bpy.context.view_layer.update()
    pending = [True]

    def update_after_evaluation(scene, graph):
        if pending[0]:
            pending[0] = False
            parent.location.x = 7

    bpy.app.handlers.depsgraph_update_post.append(update_after_evaluation)
    try:
        parent.location.x = 3
        bpy.context.view_layer.update()
        check("post_update_handler_called", not pending[0])
        check("second_evaluation_pass", close(position(child), (8, 0, 0)))
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(update_after_evaluation)


def undo_redo_and_reload():
    reset()
    parent, child = empty("SavedParent"), empty("SavedChild", (1, 2, 3))
    child.parent = parent
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="Native latency baseline")
    parent.location.x = 8
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="Native latency edit")
    check("before_undo", close(position(child), (9, 2, 3)))
    bpy.ops.ed.undo()
    check("undo_evaluated", close(position(bpy.data.objects["SavedChild"]), (1, 2, 3)))
    bpy.ops.ed.redo()
    check("redo_evaluated", close(position(bpy.data.objects["SavedChild"]), (9, 2, 3)))
    path = OUT / "native-latency.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False, compress=False)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    check("reload_evaluated", close(position(bpy.data.objects["SavedChild"]), (9, 2, 3)))
    bpy.data.objects["SavedParent"].location.x = -2
    bpy.context.view_layer.update()
    check("edit_after_reload", close(position(bpy.data.objects["SavedChild"]), (-1, 2, 3)))


try:
    for test in (parent_chain, constraints_drivers_and_visibility, animation_and_modifier,
                 collections_and_graph_rebuild, cyclic_dependencies, metaball_evaluation,
                 armature_and_shape_key_rebuild, post_update_second_pass, undo_redo_and_reload):
        test()
    report["success"] = all(report["checks"].values())
except Exception:
    report["success"] = False
    report["error"] = traceback.format_exc()
    raise
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("NATIVE_DEPSGRAPH_CHECKS", json.dumps(report), flush=True)
