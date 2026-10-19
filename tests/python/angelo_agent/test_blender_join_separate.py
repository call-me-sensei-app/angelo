# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""object_join and mesh_separate through real staged programs.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived checks. Covers world-space placement, material
slots mapped by live material, source deletion, parts joining the target's
collections, one-step Undo and the refusals. Run only in a disposable
instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-join-separate-qa"))
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


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [binding(obj) for obj in reads],
                                                  "write_set": [binding(obj) for obj in writes], "operations": operations})


def op(kind, target, **parameters):
    return {"kind": kind, "target": handle(target), "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def mesh_object(name, cubes, location=(0, 0, 0), collection=None, materials=()):
    """A mesh of unit cubes at the given local offsets; cube i uses material slot min(i, slots - 1)."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    for index, offset in enumerate(cubes):
        geometry = bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Translation(offset))
        for face in {face for vert in geometry["verts"] for face in vert.link_faces}:
            face.material_index = min(index, max(len(materials) - 1, 0))
    bm.to_mesh(mesh)
    bm.free()
    for material in materials:
        mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    (collection or window.scene.collection).objects.link(obj)
    return obj


def world_bounds(obj):
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    return [min(point[axis] for point in points) for axis in range(3)], [max(point[axis] for point in points) for axis in range(3)]


def close(a, b):
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def join():
    shared = bpy.data.materials["Material"]
    red = bpy.data.materials.new("QA Red")
    props = bpy.data.collections.new("QA Props")
    window.scene.collection.children.link(props)
    mesh_object("QA Left", [(0, 0, 0)], location=(4, 0, 0), collection=props, materials=[red])
    right = mesh_object("QA Right", [(0, 0, 0)], location=(0, 4, 0), materials=[shared])
    right.rotation_euler = (0, 0, 0.5)
    baseline("join QA")
    cube, left, right = (window.scene.objects[name] for name in ("Cube", "QA Left", "QA Right"))
    expected_right = world_bounds(right)
    materials_before = len(bpy.data.materials)
    job, staged = run(program([op("object_join", cube, sources=[handle(left), handle(right)])],
                              writes=[cube, left, right]))
    check("join_ready_without_live_edit", staged.get("ready_to_commit") and len(cube.data.vertices) == 8)
    check("join_commits", commit(job) and passed(job))
    cube = window.scene.objects["Cube"]
    check("join_sources_removed", "QA Left" not in bpy.data.objects and "QA Right" not in bpy.data.objects)
    check("join_counts", len(cube.data.vertices) == 24 and len(cube.data.polygons) == 18)
    points = [cube.matrix_world @ vertex.co for vertex in cube.data.vertices]
    check("join_keeps_world_placement", any(close(point, (4.5, 0.5, 0.5)) for point in points)
          and close([min(point[i] for point in points[16:]) for i in range(3)], expected_right[0]))
    check("join_maps_slots_by_live_material", [slot.material for slot in cube.material_slots] == [shared, red]
          and {polygon.material_index for polygon in cube.data.polygons} == {0, 1}
          and len(bpy.data.materials) == materials_before)
    # The replaced and source meshes are orphans (users 0), as after Blender's own Join.
    check("join_keeps_data_name", cube.data.name == "Cube"
          and all(mesh.users == 0 for mesh in bpy.data.meshes if mesh != cube.data))
    undo()
    check("join_one_undo", {"QA Left", "QA Right"} <= set(window.scene.objects.keys())
          and len(window.scene.objects["Cube"].data.vertices) == 8 and no_stages())
    cube, left = window.scene.objects["Cube"], window.scene.objects["QA Left"]
    left.vertex_groups.new(name="Weights")
    queries.invalidate()
    job, result = run(program([op("object_join", cube, sources=[handle(left)])], writes=[cube, left]))
    check("join_vertex_groups_refused", failed_with(result, "VERTEX_GROUPS"))
    left.vertex_groups.clear()
    modifier = cube.modifiers.new("QA Cut", "BOOLEAN")
    modifier.object = left
    queries.invalidate()
    job, result = run(program([op("object_join", cube, sources=[handle(left)])], writes=[cube, left]))
    check("join_source_in_use_refused", failed_with(result, "OBJECT_IN_USE") and "QA Left" in bpy.data.objects)
    cube.modifiers.remove(modifier)
    queries.invalidate()


def separate():
    red = bpy.data.materials["QA Red"]
    shared = bpy.data.materials["Material"]
    parts = mesh_object("QA Parts", [(0, 0, 0), (3, 0, 0)], location=(0, -4, 0),
                        collection=bpy.data.collections["QA Props"], materials=[shared, red])
    parts.rotation_euler = (0, 0, 0.25)
    baseline("separate QA")
    parts = window.scene.objects["QA Parts"]
    objects_before = set(bpy.data.objects.keys())
    job, _ = run(program([op("mesh_separate", parts, mode="LOOSE")], writes=[parts]))
    check("separate_loose_commits", commit(job))
    parts = window.scene.objects["QA Parts"]
    created = [bpy.data.objects[name] for name in set(bpy.data.objects.keys()) - objects_before]
    check("separate_creates_one_part", len(created) == 1 and created[0].name == "QA Parts.001")
    part = created[0]
    check("separate_split_geometry", len(parts.data.vertices) == len(part.data.vertices) == 8
          and part.data != parts.data and part.data.name != parts.data.name)
    check("separate_keeps_placement_and_materials", part.matrix_world == parts.matrix_world
          and [slot.material for slot in part.material_slots] == [shared, red])
    check("separate_part_joins_target_collections", sorted(c.name for c in part.users_collection) == ["QA Props"])
    undo()
    check("separate_one_undo", set(bpy.data.objects.keys()) == objects_before
          and len(window.scene.objects["QA Parts"].data.vertices) == 16 and no_stages())
    parts = window.scene.objects["QA Parts"]
    job, _ = run(program([op("mesh_separate", parts, mode="MATERIAL")], writes=[parts]))
    check("separate_material_commits", commit(job) and len(bpy.data.objects) == len(objects_before) + 1)
    undo()
    cube = window.scene.objects["Cube"]
    job, _ = run(program([op("mesh_separate", cube, mode="SELECTION", face_indices=[0])], writes=[cube]))
    check("separate_selection_commits", commit(job) and len(window.scene.objects["Cube"].data.polygons) == 5
          and len(window.scene.objects["Cube.001"].data.polygons) == 1)
    undo()
    cube = window.scene.objects["Cube"]
    job, result = run(program([op("mesh_separate", cube, mode="LOOSE")], writes=[cube]))
    check("separate_single_part_refused", failed_with(result, "NOTHING_TO_SEPARATE") and len(cube.data.vertices) == 8)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    join()
    separate()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "join-separate.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_JOIN_SEPARATE", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
