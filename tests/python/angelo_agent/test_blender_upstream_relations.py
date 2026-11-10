# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``ViewLayer.angelo_upstream`` in the real binary: its Python shape, its statuses, and its cost on a large rig.

The GTests (``depsgraph_angelo_upstream*_test.cc``) prove the walk on graphs built in C++. This suite proves the RNA
function Python calls: the ``(status, records)`` tuple, records as flat integer triples whose UID is
``ID.session_uid`` and whose type code is ``ID.id_type``'s value, NO_GRAPH for a view layer nothing evaluated,
RELATIONS_STALE after a structural edit and OK after the update, UNDECLARED_READS for a full Python driver
expression, NOT_MAIN_THREAD from a Python thread, and a RuntimeError for an evaluated copy. Then a generated Rigify
rig with a mesh skinned to it: the rig's custom shapes are never listed, and each call's cost is measured.

Background, no provider, account or network. Writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import statistics
import tempfile
import threading
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-upstream-relations-qa"))
OUT.mkdir(parents=True, exist_ok=True)
NODE_TYPES = ("UNDEFINED", "OPERATION", "TIMESOURCE", "ID_REF", "PARAMETERS", "ANIMATION", "TRANSFORM", "GEOMETRY",
              "SEQUENCER", "COMPOSITOR", "LAYER_COLLECTIONS", "COPY_ON_EVAL", "OBJECT_FROM_LAYER", "HIERARCHY",
              "AUDIO", "ARMATURE", "GENERIC_DATABLOCK", "SCENE", "VISIBILITY", "EVAL_POSE", "BONE",
              "PARTICLE_SYSTEM", "PARTICLE_SETTINGS", "SHADING", "POINT_CACHE", "IMAGE_ANIMATION", "CACHE",
              "BATCH_CACHE", "INSTANCING", "SYNCHRONIZATION", "NTREE_OUTPUT", "NTREE_GEOMETRY_PREPROCESS")
ID_TYPE_VALUES = {item.identifier: item.value for item in bpy.types.ID.bl_rna.properties["id_type"].enum_items}
CALLS = 200
state = {"success": False, "checks": {}, "measurements": {}}


def check(name, passed, detail=None):
    state["checks"][name] = bool(passed)
    if detail is not None:
        state.setdefault("details", {})[name] = detail


def records(view_layer, id_data):
    status, flat = view_layer.angelo_upstream(id_data)
    triples = [tuple(flat[index:index + 3]) for index in range(0, len(flat), 3)]
    return status, len(flat) % 3 == 0, {(uid, code, NODE_TYPES[node]) for uid, code, node in triples}


def members(found):
    return {uid for uid, code, _ in found if code}


def basics():
    scene = bpy.context.scene
    fresh = scene.view_layers.new("Upstream fresh")
    cube = bpy.data.objects.new("Upstream cube", bpy.data.meshes.new("Upstream cube"))
    scene.collection.objects.link(cube)
    status, _, found = records(fresh, cube)
    check("a view layer nothing evaluated has no graph", status == "NO_GRAPH" and not found, status)
    view_layer = bpy.context.view_layer
    view_layer.update()
    status, triples, found = records(view_layer, cube)
    check("an evaluated layer lists the object", status == "OK" and triples, status)
    check("records carry ID.session_uid, ID.id_type's value and a node type",
          (cube.session_uid, ID_TYPE_VALUES["OBJECT"], "GEOMETRY") in found
          and (cube.data.session_uid, ID_TYPE_VALUES["MESH"], "GEOMETRY") in found, sorted(found)[:12])
    check("the scene's collection hierarchy is upstream",
          (scene.collection.session_uid, ID_TYPE_VALUES["COLLECTION"], "HIERARCHY") in found)
    added = bpy.data.objects.new("Upstream added", None)
    scene.collection.objects.link(added)
    status, _, found = records(view_layer, cube)
    check("a structural edit leaves the relations stale, with no records", status == "RELATIONS_STALE" and not found,
          status)
    view_layer.update()
    check("the next update makes them current", records(view_layer, cube)[0] == "OK")
    try:
        view_layer.angelo_upstream(cube.evaluated_get(view_layer.depsgraph))
        check("an evaluated copy is refused", False)
    except RuntimeError:
        check("an evaluated copy is refused", True)
    off_main = []
    worker = threading.Thread(target=lambda: off_main.append(view_layer.angelo_upstream(cube)))
    worker.start()
    worker.join()
    check("a Python thread gets NOT_MAIN_THREAD", off_main and off_main[0] == ("NOT_MAIN_THREAD", ()), off_main)
    curve = added.driver_add("location", 0)
    curve.driver.type = 'SCRIPTED'
    curve.driver.expression = "bpy.data.objects[0].location.x"
    view_layer.update()
    check("a full Python driver expression is an undeclared read",
          records(view_layer, added)[0] == "UNDECLARED_READS")
    check("other objects are unaffected", records(view_layer, cube)[0] == "OK")
    added.driver_remove("location", 0)
    view_layer.update()
    # Re-pointing a data-block custom property through Python's mapping access tags the relations, as an RNA
    # ID pointer does: a driver reading through it must not keep its old relation under OK.
    cube["upstream_target"] = added
    check("assigning a data-block to a custom property leaves the relations stale",
          records(view_layer, cube)[0] == "RELATIONS_STALE")
    view_layer.update()
    cube["upstream_target"] = added
    check("writing the same data-block again leaves them current", records(view_layer, cube)[0] == "OK")
    del cube["upstream_target"]
    check("removing it does too", records(view_layer, cube)[0] == "RELATIONS_STALE")
    view_layer.update()
    cube["upstream_number"] = 1.0
    check("a plain custom property leaves them current", records(view_layer, cube)[0] == "OK")
    moved_between_members(view_layer, cube)
    untagged_collection_writes(view_layer, cube)
    scene.view_layers.remove(fresh)


def moved_between_members(view_layer, cube):
    """A data-block moving from one member to another changes what a driver path reads, though the set of
    data-blocks the property holds is the same: the relations must go stale, and after the update the driver's
    reader must list the data-block its path names now."""
    scene = bpy.context.scene
    first, second = (bpy.data.objects.new("Upstream member %d" % index, None) for index in (1, 2))
    reader = bpy.data.objects.new("Upstream reader", None)
    for obj in (first, second, reader):
        scene.collection.objects.link(obj)
    cube["upstream_p"], cube["upstream_q"] = first, second
    curve = reader.driver_add("location", 0)
    curve.driver.type = 'SUM'
    variable = curve.driver.variables.new()
    variable.type = 'SINGLE_PROP'
    variable.targets[0].id = cube
    variable.targets[0].data_path = '["upstream_p"].location.x'
    view_layer.update()
    status, _, found = records(view_layer, reader)
    check("a driver reading through a custom property lists the data-block it names",
          status == "OK" and first.session_uid in members(found) and second.session_uid not in members(found),
          status)
    swapped = bpy.data.objects.new("Upstream swap source", None)
    swapped["upstream_p"], swapped["upstream_q"] = second, first
    view_layer.update()
    check("building the merge source leaves the relations current", records(view_layer, reader)[0] == "OK")
    cube.id_properties_ensure().update(swapped.id_properties_ensure())
    check("merging a group that swaps two members' data-blocks leaves the relations stale",
          records(view_layer, cube)[0] == "RELATIONS_STALE")
    view_layer.update()
    status, _, found = records(view_layer, reader)
    check("after the update the driver lists the data-block its path names now",
          status == "OK" and second.session_uid in members(found) and first.session_uid not in members(found),
          status)
    cube["upstream_pair"] = {"p": first, "q": second}
    view_layer.update()
    cube["upstream_pair"] = {"p": second, "q": first}
    check("swapping data-blocks inside a nested group leaves the relations stale",
          records(view_layer, cube)[0] == "RELATIONS_STALE")
    view_layer.update()
    cube["upstream_pair"] = {"q": first, "p": second}
    check("writing the same members in another order leaves them current", records(view_layer, cube)[0] == "OK")
    variable.targets[0].data_path = '["upstream_pair"]["p"].location.x'
    view_layer.update()
    check("a driver reads through a nested group", records(view_layer, reader)[0] == "OK")
    cube["upstream_pair"].name = "upstream_renamed"
    check("renaming a group that holds a data-block leaves the relations stale",
          records(view_layer, reader)[0] == "RELATIONS_STALE")
    view_layer.update()
    check("and tags them: the rebuilt relations find the path unresolved",
          records(view_layer, reader)[0] == "UNDECLARED_READS")
    cube["upstream_pair"] = {"p": first}
    view_layer.update()
    variable.targets[0].data_path = '["upstream_p"].location.x'
    view_layer.update()
    cube.pop("upstream_p")
    check("the struct's own pop leaves the relations stale", records(view_layer, reader)[0] == "RELATIONS_STALE")
    view_layer.update()
    check("and tags them", records(view_layer, reader)[0] == "UNDECLARED_READS")
    cube["upstream_p"] = first
    view_layer.update()
    cube.id_properties_clear()
    check("clearing the struct's properties leaves them stale", records(view_layer, reader)[0] == "RELATIONS_STALE")
    view_layer.update()
    check("and tags them too", records(view_layer, reader)[0] == "UNDECLARED_READS")
    reader.driver_remove("location", 0)
    for obj in (first, second, reader, swapped):
        bpy.data.objects.remove(obj)
    view_layer.update()


class UpstreamItem(bpy.types.PropertyGroup):
    target: bpy.props.PointerProperty(type=bpy.types.Object)


def untagged_collection_writes(view_layer, cube):
    """A registered collection's move or removal re-points what a driver path names without tagging
    the relations (Blender's RNA collection functions do not); the query re-resolves the path and
    refuses rather than report the old relation. The refusal lasts until the relations are rebuilt."""
    scene = bpy.context.scene
    bpy.utils.register_class(UpstreamItem)
    bpy.types.Object.upstream_items = bpy.props.CollectionProperty(type=UpstreamItem)
    try:
        first, second, reader = (bpy.data.objects.new("Upstream %s" % name, None)
                                 for name in ("item 1", "item 2", "item reader"))
        for obj in (first, second, reader):
            scene.collection.objects.link(obj)
        for obj in (first, second):
            cube.upstream_items.add().target = obj
        curve = reader.driver_add("location", 0)
        curve.driver.type = 'SUM'
        variable = curve.driver.variables.new()
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id = cube
        variable.targets[0].data_path = "upstream_items[0].target.location.x"
        view_layer.update()
        status, _, found = records(view_layer, reader)
        check("a driver reading through a registered collection lists its item's target",
              status == "OK" and first.session_uid in members(found), status)
        cube.upstream_items.move(0, 1)
        check("moving the collection's items makes the query refuse",
              records(view_layer, reader)[0] == "RELATIONS_STALE")
        scene.collection.objects.unlink(second)
        scene.collection.objects.link(second)
        view_layer.update()
        status, _, found = records(view_layer, reader)
        check("after a rebuild the driver lists the item now first",
              status == "OK" and second.session_uid in members(found) and first.session_uid not in members(found),
              status)
        cube.upstream_items.remove(0)
        check("removing an item makes the query refuse", records(view_layer, reader)[0] == "RELATIONS_STALE")
        reader.driver_remove("location", 0)
        for obj in (first, second, reader):
            bpy.data.objects.remove(obj)
        view_layer.update()
    finally:
        del bpy.types.Object.upstream_items
        bpy.utils.unregister_class(UpstreamItem)


def rigify_rig():
    addon_utils.enable("rigify", default_set=True)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.armature_human_metarig_add()
    metarig = bpy.context.active_object
    bpy.ops.pose.rigify_generate()
    rig = max((obj for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj is not metarig),
              key=lambda obj: len(obj.data.bones))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    body = bpy.context.active_object
    # Skinned to a deform bone: an Armature modifier that deforms by vertex groups depends on the pose channels its
    # groups name (MOD_armature.cc, DEG_OB_COMP_BONE), so a mesh with no group has no pose relation at all.
    deform = next(bone.name for bone in rig.data.bones if bone.use_deform)
    body.vertex_groups.new(name=deform).add(range(len(body.data.vertices)), 1.0, 'REPLACE')
    body.modifiers.new("Armature", 'ARMATURE').object = rig
    bpy.context.view_layer.update()
    return metarig, rig, body


def measure(view_layer, id_data, name):
    status, _, found = records(view_layer, id_data)
    timings = []
    for _ in range(CALLS):
        start = time.perf_counter()
        view_layer.angelo_upstream(id_data)
        timings.append((time.perf_counter() - start) * 1000.0)
    timings.sort()
    state["measurements"][name] = {
        "status": status, "records": len(found), "ids": len(members(found)), "calls": CALLS,
        "median_ms": round(statistics.median(timings), 4), "p95_ms": round(timings[int(CALLS * 0.95) - 1], 4),
        "max_ms": round(timings[-1], 4)}
    return status, found


def rig_case():
    metarig, rig, body = rigify_rig()
    view_layer = bpy.context.view_layer
    stats = view_layer.depsgraph.debug_stats()
    state["measurements"]["graph"] = {"stats": stats, "bones": len(rig.data.bones)}
    status, rig_found = measure(view_layer, rig, "rigify_rig")
    check("the generated rig lists OK", status == "OK", status)
    shapes = {bone.custom_shape.session_uid for bone in rig.pose.bones if bone.custom_shape is not None}
    check("the rig's custom shapes are never upstream", shapes and not (shapes & members(rig_found)),
          {"shapes": len(shapes)})
    status, body_found = measure(view_layer, body, "skinned_mesh")
    check("the skinned mesh lists OK", status == "OK", status)
    check("the skinned mesh reaches the rig's pose and armature",
          any(record[:2] == (rig.session_uid, ID_TYPE_VALUES["OBJECT"]) and record[2] in ("EVAL_POSE", "BONE")
              for record in body_found)
          and rig.data.session_uid in members(body_found))
    check("the metarig feeds neither", metarig.session_uid not in members(body_found) | members(rig_found))
    check("the mesh does not feed its rig", body.session_uid not in members(rig_found))


try:
    basics()
    rig_case()
    state["success"] = all(state["checks"].values())
except Exception:
    state["error"] = traceback.format_exc()
(OUT / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
print("ANGELO_UPSTREAM_RELATIONS", json.dumps(state), flush=True)
