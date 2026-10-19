# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed-refusal edge cases: linked copies, frame moves and orbits, on real staged programs.

* A linked copy a material step gave a material commits only if it ends with
  its link root's materials and face material indices
  (``commit_fidelity.linked_layouts``): one resolved through ``source`` from an
  isolated copy, a library material sharing a stated name and faces a Geometry
  Nodes material index left on the root are refused before any write, while a
  restatement for all users, a ``copy_as`` copy reused by name and an isolated
  edit of the root's material after the duplicate commit as before.
* A program that sets the current frame is refused before approval only for
  values an F-curve or driver decides at the new frame
  (``program_refusals.check_frame_move``): a rename, a material and a key
  deletion of an animated object commit and keep their animation.
* An orbit's camera moves with time only when something animates what places
  it (``program_frames.placement_moves_with_time``): a Track To or a parent on
  objects nothing animates keeps a timeline move a frame-only rebind, and so
  does a Track To an animated object (it turns the camera; an orbit reads its
  position).

Run in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-typed-review-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_camera, scene_queries as queries  # noqa: E402
from angelo_agent.scene_settings import scene_handle, settings_revision  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
RED, BLUE = [.8, .1, .1, 1], [.1, .1, .9, 1]
NAME = "angelo_scene_program"


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def cube(name, x, scene):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=2)
    shape.to_mesh(mesh)
    shape.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location.x = x
    scene.collection.objects.link(obj)
    return obj


def material(name, colour):
    block = bpy.data.materials.new(name)
    block.use_nodes = True
    block.diffuse_color = colour
    block.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colour
    return block


def fresh(label):
    """A new scene with a camera and the cube 'Other'; the previous case's scene goes with its data."""
    scene = bpy.data.scenes.new("Review " + label)
    previous, window.scene = window.scene, scene
    if previous.name.startswith("Review "):
        doomed = set(previous.objects) | {obj.data for obj in previous.objects if obj.data is not None}
        bpy.data.scenes.remove(previous)
        bpy.data.batch_remove(ids=doomed)
    camera = bpy.data.objects.new("Camera " + label, bpy.data.cameras.new("Camera " + label))
    camera.location = (0, -9, 3)
    scene.collection.objects.link(camera)
    scene.camera = camera
    other = cube("Other " + label, 3, scene)
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="typed review baseline")
    queries.invalidate()
    return scene, other


def settled():
    """After a case's own setup: evaluated, and every cached revision read anew."""
    bpy.context.view_layer.update()
    queries.invalidate()


def entry(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def scene_entry():
    return {"handle": scene_handle(window.scene), "revision": settings_revision(window.scene)}


def run(operations, writes=(), reads=()):
    """Validate, bind, stage and commit; the first refusal's text, or the result."""
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": list(reads), "write_set": list(writes),
                                         "operations": operations, "expected_checks": []})
        approved = native.precondition(window, NAME, arguments)
    except ValueError as error:
        return {"status": "refused before approval", "error": str(error)}
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = job.result or staged
    return {"status": result.get("status"), "error": str(result.get("error") or "")}


def layout(name):
    obj = window.scene.objects.get(name)
    if obj is None:
        return None
    colours = [list(slot.material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)
               if slot.material and slot.material.node_tree else None for slot in obj.material_slots]
    return {"mesh": obj.data.name, "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
            "colours": [[round(value, 2) for value in colour] if colour else None for colour in colours],
            "faces": sorted({polygon.material_index for polygon in obj.data.polygons})}


def unchanged_by_refusal(outcome, *names):
    return (outcome["status"] != "committed" and "COMMIT_FIDELITY" in outcome["error"] and "linked copy" in outcome["error"]
            and all(window.scene.objects.get(name) is None for name in names) and no_stages())


def linked_layouts():
    cases = report["cases"]
    # A name reached through source: the root gets an isolated, recoloured copy, the linked copy the original.
    scene, other = fresh("source")
    wood = material("QA Wood", [.5, .3, .1, 1])
    other.data.materials.append(wood)
    holder = cube("Holder", -6, scene)
    holder.data.materials.append(wood)
    settled()
    outcome = run([op("create_primitive", primitive="CUBE", name="QA Post", alias="p", size=1),
                   op("object_duplicate", source="$p", alias="c", linked=True, name="QA Copy"),
                   op("node_edit", other, material="QA Wood", shared="ISOLATE",
                      edits=[{"op": "set", "node": "Principled BSDF", "inputs": {"Base Color": RED}}]),
                   op("material_assign", "$p", material="QA Wood", source=handle(other)),
                   op("material_assign", "$c", material="QA Wood", source=handle(holder))],
                  writes=[entry(other)], reads=[entry(holder)])
    cases["source_isolated_copy"] = outcome
    check("a_copy_given_another_datablock_by_source_is_refused_before_any_write",
          unchanged_by_refusal(outcome, "QA Post", "QA Copy") and layout(other.name)["materials"] == ["QA Wood"]
          and layout(other.name)["colours"] == [[.5, .3, .1, 1.0]])
    # A library material named like the one the program states.
    scene, other = fresh("library")
    path = str(OUT / "typed_review_library.blend")
    paint = bpy.data.materials.new("QA Paint")
    bpy.data.libraries.write(path, {paint})
    bpy.data.materials.remove(paint)
    with bpy.data.libraries.load(path, link=True) as (_source, target):
        target.materials = ["QA Paint"]
    other.data.materials.append(target.materials[0])
    settled()
    outcome = run([op("create_primitive", primitive="CUBE", name="QA Post", alias="p", size=1),
                   op("object_duplicate", source="$p", alias="c", linked=True, name="QA Copy"),
                   op("material_principled", "$p", name="QA Paint", inputs={"Base Color": BLUE}),
                   op("material_assign", "$c", material="QA Paint", source=handle(other))], reads=[entry(other)])
    cases["library_material"] = outcome
    check("a_copy_given_a_library_material_of_the_stated_name_is_refused", unchanged_by_refusal(outcome, "QA Post", "QA Copy"))
    # Faces a Geometry Nodes material index left on the root, which the copy's own write set to slot 0.
    scene, other = fresh("faces")
    index = {"name": "Index", "nodes": [
        {"id": "i", "type": "NodeGroupInput"}, {"id": "s", "type": "GeometryNodeSetMaterialIndex", "inputs": {"Material Index": 1}},
        {"id": "o", "type": "NodeGroupOutput"}], "links": [
        {"from_node": "i", "from_socket": "Geometry", "to_node": "s", "to_socket": "Geometry"},
        {"from_node": "s", "from_socket": "Geometry", "to_node": "o", "to_socket": "Geometry"}]}
    outcome = run([op("create_primitive", primitive="CUBE", name="QA Post", alias="a", size=1),
                   op("geometry_nodes", "$a", **index), op("modifier_apply", "$a", modifier="Index"),
                   op("object_duplicate", source="$a", alias="b", linked=True, name="QA Copy"),
                   op("material_principled", "$b", name="QA Paint", inputs={"Base Color": RED}, also=["$a"])])
    cases["root_face_indices"] = outcome
    check("a_copy_whose_root_keeps_other_face_indices_is_refused", unchanged_by_refusal(outcome, "QA Post", "QA Copy"))
    # What stays admitted and commits: a restatement for all users, a copy_as copy reused by name, a root edit.
    scene, other = fresh("restated")
    outcome = run([op("create_primitive", primitive="CUBE", name="QA Post", alias="p", size=1),
                   op("object_duplicate", source="$p", alias="c", linked=True, name="QA Copy"),
                   op("material_principled", "$p", name="QA Restated", inputs={"Base Color": RED}, also=["$c"]),
                   op("material_principled", "$p", name="QA Restated", inputs={"Base Color": BLUE}, shared="ALL_USERS")])
    cases["restated_for_all_users"] = dict(outcome, post=layout("QA Post"), copy=layout("QA Copy"))
    check("a_restatement_for_all_users_commits_on_both", outcome["status"] == "committed"
          and layout("QA Post") == layout("QA Copy") and layout("QA Copy")["colours"] == [[.1, .1, .9, 1.0]])
    scene, other = fresh("copy_as")
    other.data.materials.append(material("QA Oak", [.5, .3, .1, 1]))
    settled()
    outcome = run([op("create_primitive", primitive="CUBE", name="QA Post", alias="p", size=1),
                   op("object_duplicate", source="$p", alias="c", linked=True, name="QA Copy"),
                   op("material_assign", "$p", material="QA Oak", source=handle(other), copy_as="QA Oak Copy"),
                   op("material_assign", "$c", material="QA Oak Copy")], reads=[entry(other)])
    cases["copy_as_reused"] = dict(outcome, post=layout("QA Post"), copy=layout("QA Copy"))
    check("a_copy_as_copy_reused_by_name_commits", outcome["status"] == "committed"
          and layout("QA Copy")["materials"] == ["QA Oak Copy"] and layout("QA Post") == layout("QA Copy"))
    scene, other = fresh("root_edit")
    red = material("QA Live Red", RED)
    root = cube("QA Root", 8, scene)
    root.data.materials.append(red)
    other.data.materials.append(red)
    settled()
    outcome = run([op("object_duplicate", source=handle(root), alias="b", linked=True, name="QA Root Copy"),
                   op("node_edit", root, material="QA Live Red", shared="ISOLATE",
                      edits=[{"op": "set", "node": "Principled BSDF", "inputs": {"Base Color": BLUE}}])],
                  writes=[entry(root)])
    cases["root_material_isolated_after_the_duplicate"] = dict(outcome, root=layout("QA Root"), copy=layout("QA Root Copy"))
    check("an_isolated_edit_of_the_roots_material_still_commits_on_its_linked_copy", outcome["status"] == "committed"
          and layout("QA Root") == layout("QA Root Copy") and layout("QA Root Copy")["colours"] == [[.1, .1, .9, 1.0]]
          and layout(other.name)["colours"] == [[.8, .1, .1, 1.0]])


def keyed(obj, frames=((1, 1.0), (21, 21.0))):
    for frame, x in frames:
        obj.location.x = x
        obj.keyframe_insert("location", index=0, frame=frame)
    window.scene.frame_set(1)
    settled()


def frame_writes():
    cases = report["cases"]
    move = {"kind": "property_set", "target": None, "parameters": {"values": {"frame_current": 11}}}
    for label, operation in (("rename", lambda obj: op("object_rename", obj, name="QA Renamed")),
                             ("material", lambda obj: op("material_principled", obj, name="QA Frame Paint",
                                                         inputs={"Base Color": RED})),
                             ("key_delete", lambda obj: op("keyframe_delete", obj, property="location", frame=21))):
        scene, other = fresh("frame " + label)
        keyed(other)
        frame = dict(move, target=scene_handle(scene))
        outcome = run([operation(other), frame], writes=[entry(other), scene_entry()])
        window.scene.frame_set(window.scene.frame_current)  # The window's frame-change update.
        cases["frame_beside_" + label] = dict(outcome, frame=window.scene.frame_current, x=round(other.location.x, 3),
                                              name=other.name)
        check("a_%s_of_an_animated_object_beside_a_frame_move_commits" % label, outcome["status"] == "committed"
              and window.scene.frame_current == 11 and other.animation_data is not None
              and other.animation_data.action is not None  # Frame 11 between the keys; only the first key left.
              and round(other.location.x, 3) == (1.0 if label == "key_delete" else 11.0)
              and (label != "rename" or other.name == "QA Renamed"))
    scene, other = fresh("frame transform")
    keyed(other)
    outcome = run([op("transform", other, location=[0, 0, 5]), dict(move, target=scene_handle(scene))],
                  writes=[entry(other), scene_entry()])
    cases["frame_beside_unkeyed_location"] = outcome
    check("an_unkeyed_write_of_a_keyed_channel_beside_a_frame_move_is_refused_before_approval",
          outcome["status"] == "refused before approval" and "FRAME_ANIMATED" in outcome["error"]
          and "sets location" in outcome["error"] and no_stages())
    outcome = run([op("transform", other, scale=[2, 2, 2]), dict(move, target=scene_handle(scene))],
                  writes=[entry(other), scene_entry()])
    cases["frame_beside_unkeyed_scale"] = outcome
    check("a_write_of_a_channel_no_key_holds_commits", outcome["status"] == "committed"
          and tuple(round(value, 3) for value in other.scale) == (2, 2, 2))
    scene, other = fresh("frame scene")
    scene.gravity = (0, 0, -9.81)
    scene.keyframe_insert("gravity", frame=1)
    scene.gravity = (0, 0, -1.0)
    scene.keyframe_insert("gravity", frame=21)
    scene.frame_set(1)
    settled()
    outcome = run([{"kind": "property_set", "target": scene_handle(scene),
                    "parameters": {"values": {"frame_current": 11, "gravity": [0, 0, -5.0]}}}], writes=[scene_entry()])
    cases["frame_beside_keyed_scene_setting"] = outcome
    check("a_keyed_scene_setting_beside_a_frame_move_is_refused_before_approval",
          outcome["status"] == "refused before approval" and "FRAME_ANIMATED" in outcome["error"])


def orbit_cameras():
    """An orbit around a camera placed by objects nothing animates rebinds after a timeline move."""
    cases = report["cases"]
    rim = {"kind": "light_create", "target": None, "parameters": {
        "name": "QA Rim", "look_at": [0, 0, 0], "orbit": {"azimuth": 3.14159, "elevation": .5}}}
    # An orbit reads the camera's position only, which aiming it never moves.
    for label, rig, moves in (("track_to", "TRACK_TO", False), ("parent", "PARENT", False),
                              ("animated_target", "TRACK_TO", False), ("animated_parent", "PARENT", True)):
        scene, other = fresh("orbit " + label)
        empty = bpy.data.objects.new("QA Aim " + label, None)
        scene.collection.objects.link(empty)
        if rig == "TRACK_TO":
            constraint = scene.camera.constraints.new("TRACK_TO")
            constraint.target = empty
        else:
            scene.camera.parent = empty
        if moves:
            keyed(empty)
        settled()
        used = []
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [rim]})
        program_camera.host_program(window, arguments, used)
        cases["orbit_" + label] = {"moves_with_time": [seen.get("moves_with_time") for seen in used]}
        check("an_orbit_camera_%s_moves_with_time_%s" % (label, moves), [seen.get("moves_with_time") for seen in used]
              == [moves])


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    linked_layouts()
    frame_writes()
    orbit_cameras()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "typed-review.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_TYPED_REVIEW", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
