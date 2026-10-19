# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object management through real staged programs: rename, duplicate, delete, transforms, origin, collections.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived checks. Covers commit, Undo, the refusals (taken
names, objects in use, live children, the last collection) and a forced
rollback of renames and collection edits. Run only in a disposable instance
with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-object-management-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def objects():
    return window.scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [binding(obj) for obj in reads],
                                                  "write_set": [binding(obj) for obj in writes], "operations": operations})


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": handle(target) if target is not None else None, "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def membership(obj):
    return sorted(collection.name for collection in obj.users_collection)


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def rename():
    baseline("objects QA rename")
    cube = objects()["Cube"]
    job, staged = run(program([op("object_rename", cube, name="Hero Cube")], writes=[cube]))
    check("rename_ready_without_live_edit", staged.get("ready_to_commit") and cube.name == "Cube")
    check("rename_commits", commit(job) and cube.name == "Hero Cube" and passed(job))
    undo()
    check("rename_one_undo", "Cube" in objects() and no_stages())
    cube = objects()["Cube"]
    job, result = run(program([op("object_rename", cube, name="Camera")], writes=[cube]))
    check("rename_to_taken_name_refused", result.get("status") == "failed" and "NAME_TAKEN" in result["error"] and cube.name == "Cube")


def duplicate():
    baseline("objects QA duplicate")
    cube = objects()["Cube"]
    material = cube.material_slots[0].material
    objects_before, materials_before = len(bpy.data.objects), len(bpy.data.materials)
    job, _ = run(program([op("object_duplicate", source=handle(cube), alias="twin", location=[3, 0, 0]),
                          op("object_duplicate", source=handle(cube), alias="ghost", name="QA Ghost", linked=True)],
                         reads=[cube]))
    check("duplicate_commits", commit(job) and passed(job))
    twin = next(obj for obj in objects() if obj.name.startswith("Cube.") and obj != cube)
    ghost = objects()["QA Ghost"]
    check("full_duplicate_has_own_data_and_location", twin.data != cube.data and tuple(twin.location) == (3, 0, 0)
          and len(twin.data.vertices) == len(cube.data.vertices))
    check("linked_duplicate_shares_live_data", ghost.data == cube.data)
    check("duplicates_keep_live_materials", twin.material_slots[0].material == material
          and len(bpy.data.materials) == materials_before)
    check("duplicates_join_source_collections", membership(twin) == membership(cube) == membership(ghost))
    check("duplicates_added_two_objects", len(bpy.data.objects) == objects_before + 2)
    undo()
    check("duplicate_one_undo", len(bpy.data.objects) == objects_before and "QA Ghost" not in objects() and no_stages())
    cube = objects()["Cube"]
    cube.keyframe_insert("location", frame=1)
    queries.invalidate()
    job, result = run(program([op("object_duplicate", source=handle(cube), alias="moving")], reads=[cube]))
    check("animated_duplicate_refused", result.get("status") == "failed" and "ANIMATED_SOURCE" in result["error"])
    cube.animation_data_clear()
    queries.invalidate()


def delete():
    baseline("objects QA delete")
    light = objects()["Light"]
    objects_before = len(bpy.data.objects)
    job, staged = run(program([op("object_delete", light)], writes=[light]))
    check("delete_preview_lists_removal", staged.get("ready_to_commit") and job.delta["removed"][0]["name"] == "Light"
          and "Light" in objects())
    check("delete_commits", commit(job) and passed(job) and "Light" not in bpy.data.objects
          and len(bpy.data.objects) == objects_before - 1 and job.result["changes"]["removed"][0]["name"] == "Light")
    undo()
    check("delete_one_undo", "Light" in objects() and no_stages())
    camera = objects()["Camera"]
    # Refused when the program is bound, before anything is staged or approved, with what to do instead.
    try:
        job, result = run(program([op("object_delete", camera)], writes=[camera]))
        error, staged = result.get("error", ""), bool(result.get("ready_to_commit"))
    except native.ProgramError as refusal:
        error, staged = str(refusal), False
    check("deleting_scene_camera_refused_before_staging", not staged and "ACTIVE_CAMERA" in error
          and "property_set" in error and "camera" in error)
    check("scene_camera_kept", "Camera" in objects() and window.scene.camera.name == "Camera" and no_stages())
    # The controller's completion (program_camera.host_program) clears the camera in the approved program instead.
    from angelo_agent import program_camera
    released = program_camera.host_program(window, program([op("object_delete", camera)], writes=[camera]))
    check("deleted_scene_camera_released_in_the_program",
          released["operations"][-1]["parameters"]["values"] == {"camera": None} and len(released["operations"]) == 2)
    job, staged = run(released)
    check("released_camera_stages", staged.get("ready_to_commit") and window.scene.camera.name == "Camera")
    check("released_camera_commits", commit(job) and passed(job) and "Camera" not in bpy.data.objects
          and window.scene.camera is None)
    undo()
    check("released_camera_one_undo", "Camera" in objects() and window.scene.camera.name == "Camera" and no_stages())
    active_camera_replaced()
    cube, light = objects()["Cube"], objects()["Light"]
    light.parent = cube
    queries.invalidate()
    job, result = run(program([op("object_delete", cube)], writes=[cube], reads=[]))
    refused = (result.get("status") == "failed" or not commit(job)) and "OBJECT_IN_USE" in (job.result or result).get("error", "")
    check("deleting_a_parent_refused", refused and "Cube" in objects() and light.parent == cube and no_stages())
    light.parent = None
    queries.invalidate()


def scene_binding():
    from angelo_agent import scene_settings
    entry = scene_settings.execute(window, {})
    return {"handle": entry["handle"], "revision": entry["revision"]}


def active_camera_replaced():
    """Deleting the active camera commits when the same program makes another camera active."""
    baseline("objects QA delete active camera")
    camera, scene = objects()["Camera"], scene_binding()
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "write_set": [binding(camera), scene], "operations": [
            op("object_delete", camera),
            {"kind": "camera_create", "target": None, "parameters": {"name": "QA Deck Camera", "alias": "deck",
                                                                     "location": [0, -4, 2]}},
            {"kind": "property_set", "target": scene["handle"], "parameters": {"values": {"camera": "$deck"}}}]})
    job, staged = run(arguments)
    check("replaced_camera_delete_stages", staged.get("ready_to_commit") and window.scene.camera.name == "Camera")
    check("replaced_camera_delete_commits", commit(job) and passed(job) and "Camera" not in bpy.data.objects
          and window.scene.camera is not None and window.scene.camera.name == "QA Deck Camera")
    undo()
    check("replaced_camera_delete_one_undo", "Camera" in objects() and window.scene.camera.name == "Camera"
          and "QA Deck Camera" not in objects() and no_stages())
    # Another scene still looks through the camera: only that scene's owner can release it.
    camera, other = objects()["Camera"], bpy.data.scenes.new("QA Other Scene")
    other.camera = camera
    queries.invalidate()
    scene = scene_binding()
    try:
        job, result = run(validate_call("angelo_scene_program", {
            "mode": "APPLY", "read_set": [], "write_set": [binding(camera), scene], "operations": [
                op("object_delete", camera),
                {"kind": "property_set", "target": scene["handle"], "parameters": {"values": {"camera": None}}}]}))
        error, staged = result.get("error", ""), bool(result.get("ready_to_commit"))
    except native.ProgramError as refusal:
        error, staged = str(refusal), False
    check("camera_of_another_scene_refused_before_staging", not staged and "ACTIVE_CAMERA" in error
          and "QA Other Scene" in error and "Camera" in objects() and no_stages())
    bpy.data.scenes.remove(other)
    queries.invalidate()


def transforms_and_origin():
    baseline("objects QA transforms")
    cube = objects()["Cube"]
    cube.scale = (2, 1, .5)
    cube.rotation_euler = (0, 0, .7)
    cube.location = (1, 2, 3)
    window.view_layer.update()
    world_before = [tuple(cube.matrix_world @ vertex.co) for vertex in cube.data.vertices]
    queries.invalidate()
    job, _ = run(program([op("transform_apply", cube, scale=True, rotation=True)], writes=[cube]))
    check("apply_commits", commit(job) and passed(job))
    window.view_layer.update()
    world_after = [tuple(cube.matrix_world @ vertex.co) for vertex in cube.data.vertices]
    check("apply_keeps_world_geometry", tuple(cube.scale) == (1, 1, 1) and tuple(cube.location) == (1, 2, 3)
          and max(abs(a - b) for before, after in zip(world_before, world_after) for a, b in zip(before, after)) < 1e-5)
    queries.invalidate()
    job, _ = run(program([op("origin_set", cube, mode="POINT", point=[0, 0, 0])], writes=[cube]))
    check("origin_commits", commit(job))
    window.view_layer.update()
    moved = [tuple(cube.matrix_world @ vertex.co) for vertex in cube.data.vertices]
    check("origin_moves_only_the_origin", tuple(round(value, 5) for value in cube.location) == (0, 0, 0)
          and max(abs(a - b) for before, after in zip(world_after, moved) for a, b in zip(before, after)) < 1e-5)
    undo()
    undo()
    light = objects()["Light"]
    cube = objects()["Cube"]
    light.parent = cube
    cube.location = (1, 2, 3)
    window.view_layer.update()
    queries.invalidate()
    job, result = run(program([op("transform_apply", cube, location=True)], writes=[cube]))
    refused = result.get("status") == "failed" or not commit(job)
    check("apply_with_children_refused", refused and "children" in (job.result or result).get("error", "")
          and tuple(cube.location) == (1, 2, 3) and no_stages())
    light.parent = None
    cube.scale, cube.rotation_euler, cube.location = (1, 1, 1), (0, 0, 0), (0, 0, 0)
    queries.invalidate()


def collections():
    baseline("objects QA collections")
    cube = objects()["Cube"]
    job, _ = run(program([op("collection_link", cube, collection="QA Props", create=True, mode="MOVE")], writes=[cube]))
    check("move_into_new_collection_commits", commit(job) and passed(job) and membership(cube) == ["QA Props"]
          and window.scene.collection.children.get("QA Props") is not None)
    queries.invalidate()
    job, _ = run(program([op("collection_link", cube, collection="Collection")], writes=[cube]))
    check("link_existing_collection", commit(job) and membership(cube) == ["Collection", "QA Props"])
    queries.invalidate()
    job, result = run(program([op("collection_link", cube, collection="QA Props", mode="UNLINK"),
                               op("collection_link", cube, collection="Collection", mode="UNLINK")], writes=[cube]))
    check("unlinking_the_last_collection_refused", result.get("status") == "failed" and "LAST_COLLECTION" in result["error"]
          and membership(cube) == ["Collection", "QA Props"])
    job, result = run(program([op("collection_link", cube, collection="Nowhere")], writes=[cube]))
    check("unknown_collection_refused", result.get("status") == "failed" and "COLLECTION_NOT_FOUND" in result["error"])
    undo()
    undo()
    check("collections_two_undos", membership(objects()["Cube"]) == ["Collection"] and bpy.data.collections.get("QA Props") is None)


def create_objects():
    baseline("objects QA create")
    job, _ = run(program([op("create_object", type="TEXT", name="QA Title", alias="title", body="Angelo", extrude=.1,
                             location=[0, 0, 2]),
                          op("create_object", type="CURVE", name="QA Ring", alias="ring", shape="CIRCLE", radius=2),
                          op("create_object", type="EMPTY", name="QA Pivot", alias="pivot", display="ARROWS"),
                          {"kind": "parent_set", "target": "$title", "parameters": {"parent": "$pivot"}}]))
    check("create_objects_commit", commit(job) and passed(job))
    title, ring, pivot = objects()["QA Title"], objects()["QA Ring"], objects()["QA Pivot"]
    check("created_objects_live", title.type == "FONT" and title.data.body == "Angelo" and ring.type == "CURVE"
          and len(ring.data.splines[0].bezier_points) == 4 and ring.data.splines[0].use_cyclic_u
          and pivot.empty_display_type == "ARROWS" and title.parent == pivot)
    queries.invalidate()
    job, _ = run(program([{"kind": "property_set", "target": handle(ring), "parameters": {"values": {"data.bevel_depth": .1}}}],
                         writes=[ring]))
    check("created_curve_takes_property_set", commit(job) and abs(objects()["QA Ring"].data.bevel_depth - .1) < 1e-6)
    undo()
    undo()
    check("create_objects_two_undos", "QA Title" not in objects() and "QA Ring" not in objects() and no_stages())


def forced_rollback():
    queries.invalidate()
    cube = objects()["Cube"]
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after collection edits")

    job, _ = run(program([op("object_rename", cube, name="QA Renamed"),
                          op("collection_link", cube, collection="QA Rollback", create=True, mode="MOVE")], writes=[cube]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("rollback_reports_failure", job.result.get("status") == "failed" and not job.result.get("partial_changes_possible"))
    check("rollback_restores_name_and_membership", cube.name == "Cube" and membership(cube) == ["Collection"]
          and bpy.data.collections.get("QA Rollback") is None and no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    rename()
    duplicate()
    delete()
    transforms_and_origin()
    collections()
    create_objects()
    forced_rollback()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "object-management.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_OBJECT_MANAGEMENT", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
