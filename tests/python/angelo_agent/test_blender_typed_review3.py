# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed-refusal edge cases: what places a camera, keyed frame moves and held materials, on real rigs.

* What places a camera, read from Blender's RNA (``object_dependencies``):
  constraints' custom space objects, the Objects a vertex parent's modifiers
  point at (Mirror, Array, Hook, a Geometry Nodes input) and a vertex group
  of such a mesh. Each rig is judged against the camera's evaluated position at
  two frames, moving and still; an orbit beside a frame move, or beside a move
  of one of those objects, is refused before approval.
* Frame moves refuse the configure kinds' keyed paths before approval (a
  light's energy, a pose bone's location); parenting with keep_transform onto
  a parent writes no channel and commits.
* A name-only ``material_principled`` of a material the object holds keeps it
  (a live red material stays red), assigning its slot to the faces it names.
* A Geometry Nodes apply beside a World edit commits, and a failure while
  resuming the stage before any write reports the verified unchanged scene.

Run in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-typed-review3-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_camera, program_frames, program_orbits  # noqa: E402
from angelo_agent import scene_queries as queries  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.scene_settings import scene_handle, settings_revision  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"
ORBIT = {"azimuth": math.pi, "elevation": .3}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def entry(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def scene_entry():
    return {"handle": scene_handle(window.scene), "revision": settings_revision(window.scene)}


def settled():
    bpy.context.view_layer.update()
    queries.invalidate()


def new_scene(label):
    scene = bpy.data.scenes.new("Review3 " + label)
    window.scene = scene
    cam = bpy.data.objects.new("Cam " + label, bpy.data.cameras.new("Cam " + label))
    cam.location = (0, -9, 3)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def empty(scene, name, moving):
    obj = bpy.data.objects.new(name, None)
    scene.collection.objects.link(obj)
    if moving:
        for frame, x in ((1, 0.0), (21, 4.0)):
            obj.location.x = x
            obj.keyframe_insert("location", index=0, frame=frame)
    return obj


def host(scene, name):
    import bmesh
    data = bpy.data.meshes.new(name)
    shape = bmesh.new()
    bmesh.ops.create_grid(shape, x_segments=3, y_segments=3, size=1.0)
    shape.to_mesh(data)
    shape.free()
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def hang(cam, obj):
    cam.parent, cam.parent_type, cam.parent_vertices = obj, "VERTEX", (4, 4, 4)


def custom_space(scene, cam, mover):
    copy = cam.constraints.new("COPY_LOCATION")
    copy.target = empty(scene, "Aim", False)
    copy.owner_space, copy.space_object = "CUSTOM", mover


def mirror_parent(scene, cam, mover):
    mesh = host(scene, "Mirror Host")
    modifier = mesh.modifiers.new("Mirror", "MIRROR")
    modifier.mirror_object, modifier.use_mirror_merge = mover, False
    hang(cam, mesh)


def array_parent(scene, cam, mover):
    mesh = host(scene, "Array Host")
    modifier = mesh.modifiers.new("Array", "ARRAY")
    modifier.use_relative_offset, modifier.use_object_offset, modifier.offset_object = False, True, mover
    hang(cam, mesh)


def hooked_group(scene, cam, mover):
    mesh = host(scene, "Hooked")
    mesh.vertex_groups.new(name="Tip").add([4], 1.0, "REPLACE")
    hook = mesh.modifiers.new("Hook", "HOOK")
    hook.object = mover
    hook.vertex_indices_set([4])
    copy = cam.constraints.new("COPY_LOCATION")
    copy.target, copy.subtarget = mesh, "Tip"


RIGS = {"custom_space": custom_space, "mirror_parent": mirror_parent, "array_parent": array_parent,
        "hooked_group": hooked_group}


def placement():
    """Each rig moving and still, judged against Blender's evaluated camera position at frames 1 and 21."""
    for label, build in RIGS.items():
        for moving in (True, False):
            scene, cam = new_scene("%s %s" % (label, moving))
            mover = empty(scene, "Mover", moving)
            build(scene, cam, mover)
            settled()
            positions = []
            for frame in (1, 21):
                scene.frame_set(frame)
                positions.append([round(value, 4) for value in cam.matrix_world.translation])
            scene.frame_set(1)
            moves, judged = positions[0] != positions[1], program_frames.placement_moves_with_time(cam)
            follows = program_orbits.followed_objects(cam, lambda obj: obj.name) or []
            report["cases"]["placement_%s_%s" % (label, moving)] = {"moves": moves, "judged": judged, "follows": follows}
            check("placement_%s_%s_matches_blender" % (label, "moving" if moving else "still"),
                  moves == moving and judged == moves and mover.name in follows)


def orbits():
    """An orbit beside a frame move, or beside a move of what places the camera, is refused before approval."""
    light = {"kind": "light_create", "target": None, "parameters": {"name": "QA Rim", "look_at": [0, 0, 0],
                                                                      "orbit": dict(ORBIT)}}
    for label, build in (("custom_space", custom_space), ("mirror_parent", mirror_parent)):
        scene, cam = new_scene("orbit frame " + label)
        build(scene, cam, empty(scene, "Mover", True))
        scene.frame_set(1)
        settled()
        frame = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {"frame_current": 21}}}
        try:
            arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [scene_entry()],
                                             "operations": [light, frame]})
            program_camera.host_program(window, arguments)
            refused = ""
        except ValueError as error:
            refused = str(error)
        report["cases"]["orbit_frame_" + label] = refused
        check("orbit_beside_a_frame_move_%s_refused" % label, "moves with time" in refused)
        scene, cam = new_scene("orbit move " + label)
        mover = empty(scene, "Mover", False)
        build(scene, cam, mover)
        settled()
        try:
            arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [entry(mover)],
                                             "operations": [op("transform", mover, location=[10, 0, 0]), light]})
            program_camera.host_program(window, arguments)
            refused = ""
        except ValueError as error:
            refused = str(error)
        report["cases"]["orbit_move_" + label] = refused
        check("orbit_beside_a_move_of_what_places_the_camera_%s_refused" % label,
              "moves an object the scene camera follows" in refused)


def run(operations, writes=()):
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": list(writes),
                                         "operations": operations, "expected_checks": []})
        approved = native.precondition(window, NAME, arguments)
    except ValueError as error:
        return {"status": "refused before approval", "error": str(error)}, None
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    return staged, job


def committed(operations, writes=()):
    staged, job = run(operations, writes)
    if job is not None and staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        staged = job.result or staged
    return {"status": staged.get("status"), "error": str(staged.get("error") or ""),
            "partial_changes_possible": staged.get("partial_changes_possible"), "scene_changed": staged.get("scene_changed"),
            "rollback_verified": staged.get("rollback_verified")}


def frame_writes():
    scene, cam = new_scene("frame writes")
    lamp = bpy.data.objects.new("QA Lamp", bpy.data.lights.new("QA Lamp", "POINT"))
    scene.collection.objects.link(lamp)
    for frame, energy in ((1, 100.0), (21, 500.0)):
        lamp.data.energy = energy
        lamp.data.keyframe_insert("energy", frame=frame)
    holder, child = empty(scene, "Holder", False), empty(scene, "Keyed", True)
    scene.frame_set(1)
    settled()
    move = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {"frame_current": 11}}}
    outcome = committed([op("light_configure", lamp, settings={"energy": 1000.0}), move], [entry(lamp), scene_entry()])
    report["cases"]["keyed_light_energy"] = outcome
    check("a_keyed_light_energy_beside_a_frame_move_is_refused_before_approval",
          outcome["status"] == "refused before approval" and "sets data.energy" in outcome["error"])
    outcome = committed([op("parent_set", child, parent=handle(holder), keep_transform=True), move],
                        [entry(child), entry(holder), scene_entry()])
    report["cases"]["keyed_object_parented_in_place"] = outcome
    check("parenting_a_keyed_object_in_place_beside_a_frame_move_commits",
          outcome["status"] == "committed" and child.parent == holder and window.scene.frame_current == 11)


def names_only():
    scene, cam = new_scene("names only")
    red = bpy.data.materials.new("QA Live Red")
    red.use_nodes = True
    red.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (.8, .1, .1, 1)
    blue = bpy.data.materials.new("QA Live Blue")
    cube = host(scene, "QA Box")
    cube.data.materials.append(blue)
    cube.data.materials.append(red)
    settled()
    outcome = committed([op("material_principled", cube, name="QA Live Red", face_indices=[0])], [entry(cube)])
    colour = list(red.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)
    report["cases"]["name_only_held"] = dict(outcome, colour=colour, faces=[p.material_index for p in cube.data.polygons])
    check("a_name_only_statement_keeps_the_held_material_and_assigns_its_faces", outcome["status"] == "committed"
          and [round(value, 3) for value in colour] == [.8, .1, .1, 1.0]
          and cube.material_slots[1].material == red and cube.data.polygons[0].material_index == 1
          and len(bpy.data.materials) == 2)


def node_metadata_and_resume():
    scene, cam = new_scene("node metadata")
    scene.world = bpy.data.worlds.new("QA World")
    settled()
    index = {"name": "Index", "nodes": [
        {"id": "i", "type": "NodeGroupInput"}, {"id": "s", "type": "GeometryNodeSetMaterialIndex",
                                                "inputs": {"Material Index": 1}},
        {"id": "o", "type": "NodeGroupOutput"}], "links": [
        {"from_node": "i", "from_socket": "Geometry", "to_node": "s", "to_socket": "Geometry"},
        {"from_node": "s", "from_socket": "Geometry", "to_node": "o", "to_socket": "Geometry"}]}
    world = {"kind": "world_configure", "target": scene_handle(scene), "parameters": {"color": [.2, .3, .4]}}
    operations = [op("create_primitive", primitive="CUBE", name="QA Post", alias="a", size=1),
                  op("geometry_nodes", "$a", **index), op("modifier_apply", "$a", modifier="Index"), world]
    outcome = committed(operations, [scene_entry()])
    report["cases"]["applied_graph_beside_world"] = outcome
    check("an_applied_graph_beside_a_world_edit_commits", outcome["status"] == "committed"
          and [round(value, 3) for value in window.scene.world.color] == [.2, .3, .4])
    scene, cam = new_scene("resume failure")
    scene.world = bpy.data.worlds.new("QA World 2")
    settled()
    staged, job = run([dict(world, target=scene_handle(scene))], [scene_entry()])
    if job is not None and staged.get("ready_to_commit"):
        def fail(stage):
            raise RefusalError("Node metadata refers outside the staged IDs", code="INTERNAL_ERROR")
        job.backend.resume_stage = fail
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = job.result if job is not None else staged
    report["cases"]["resume_failure"] = {key: result.get(key) for key in (
        "status", "error", "scene_changed", "partial_changes_possible", "rollback_verified", "mutation_started")}
    check("a_failure_resuming_the_stage_reports_the_verified_unchanged_scene", result.get("status") == "failed"
          and result.get("partial_changes_possible") is False and result.get("scene_changed") is False
          and result.get("rollback_verified") is True and no_stages())


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    placement()
    orbits()
    frame_writes()
    names_only()
    node_metadata_and_resume()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "typed-review3.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_TYPED_REVIEW3", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
