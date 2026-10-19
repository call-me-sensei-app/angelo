# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Visibility gaps in the compiled app: frame-free revisions, where typed programs end, script ids.

Run by ``run_native_suites.py --include test_blender_visibility_gaps`` (BACKGROUND). It proves in the real Blender:

* revisions: after ``frame_set``, an object nothing moves in time (a mesh, a child of a still parent, a mesh
  whose Mirror object is still) keeps its revision and a program bound at the earlier frame commits; one that can
  change with time (keyed, a child of a keyed parent, a Wave modifier, a Mirror object that is keyed) gets a new
  revision and a program bound at the earlier frame is refused ``STALE_REVISION`` with nothing written;
* where typed programs end: a typed MATH ``GREATER_THAN`` is refused naming ``ShaderNodeMath``, which commits it;
  an over-limit mesh's turn-context omission and bind refusal give its counts, limits and the script route;
  deleting the default cube names the material only its mesh used (``held_only_by_them``), and a save of the file
  does write that material, which reopens unused, while the mesh is not written;
* a finished script's result names its id (``script``).

No provider, account or network; the fixture saves and reopens one temporary file in ANGELO_QA_DIR.
"""

import json
import os
from pathlib import Path
import sys
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import native_programs as native, scene_queries as queries, turn_context  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.script_store import script_id  # noqa: E402

bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(W, obj)}


def run(arguments):
    """Bind, stage and commit an angelo_scene_program; its final result."""
    native.precondition(W, "angelo_scene_program", arguments)
    job = native.begin(W, "angelo_scene_program", arguments)
    result = job.poll()
    while not (result.get("final") or result.get("ready_to_commit")):
        result = job.poll()
    return job.commit() if result.get("ready_to_commit") else result


def mesh(name, location):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj.data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj.location = location
    W.scene.collection.objects.link(obj)
    return obj


def keyed(obj):
    obj.location = (0, 0, 0)
    obj.keyframe_insert("location", frame=1)
    obj.location = (10, 0, 0)
    obj.keyframe_insert("location", frame=120)


def revisions():
    scene = W.scene
    scene.frame_set(1)
    still, post, lantern = mesh("QA Rock", (3, 0, 0)), mesh("QA Post", (0, 4, 0)), mesh("QA Lantern", (0, 4, 2))
    lantern.parent = post
    car, body = bpy.data.objects.new("QA Car Motion", None), mesh("QA Car Body", (0, 0, 1))
    scene.collection.objects.link(car)
    keyed(car)
    body.parent = car
    wave = mesh("QA Wave", (0, -4, 0))
    wave.modifiers.new("Wave", "WAVE")
    target = bpy.data.objects.new("QA Mirror Target", None)
    scene.collection.objects.link(target)
    mirrored = mesh("QA Mirrored", (5, 5, 0))
    mirrored.modifiers.new("Mirror", "MIRROR").mirror_object = target
    W.view_layer.update()
    still_set, moving_set = [still, post, lantern, mirrored], [car, body, wave]
    before = {obj.name: entry(obj) for obj in still_set + moving_set}
    scene.frame_set(60)  # The recorded script's own frame change (friction scan, Astra bridge t1).
    queries.invalidate(W)
    after = {obj.name: queries.object_revision(W, obj) for obj in still_set + moving_set}
    check("still_objects_keep_their_revision", all(after[o.name] == before[o.name]["revision"] for o in still_set),
          changed=[o.name for o in still_set if after[o.name] != before[o.name]["revision"]])
    check("time_dependent_objects_get_a_new_revision",
          all(after[o.name] != before[o.name]["revision"] for o in moving_set),
          unchanged=[o.name for o in moving_set if after[o.name] == before[o.name]["revision"]])
    move = lambda obj, x: {"kind": "transform", "target": before[obj.name]["handle"],
                           "parameters": {"location": [x, 0, 0]}}
    result = run({"mode": "APPLY", "read_set": [before[post.name]], "write_set": [before[still.name], before[lantern.name]],
                  "expected_checks": [], "operations": [move(still, 7.5), move(lantern, 1.5)]})
    check("a_program_bound_before_the_frame_change_commits_on_still_objects",
          result.get("status") == "committed" and abs(still.location.x - 7.5) < 1e-6,
          status=result.get("status"), error=result.get("error"), code=result.get("error_code"))
    location = tuple(body.location)
    try:
        refused = run({"mode": "APPLY", "read_set": [], "write_set": [before[body.name]], "expected_checks": [],
                       "operations": [move(body, 3.0)]})
        code = refused.get("error_code")
    except ProgramError as error:
        code = error.code
    check("a_stale_binding_of_a_time_dependent_object_is_refused",
          code == "STALE_REVISION" and tuple(body.location) == location, code=code)
    mirrored_before = queries.object_revision(W, mirrored)
    keyed(target)  # The Mirror object now moves with time, so the mirrored mesh does too.
    queries.invalidate(W)
    check("gaining_a_keyed_mirror_object_changes_the_revision", queries.object_revision(W, mirrored) != mirrored_before)


def typed_ends():
    scene = W.scene
    holder = mesh("QA Shader Holder", (0, 0, -3))
    W.view_layer.update()
    def shader(node):
        return {"mode": "APPLY", "read_set": [], "write_set": [entry(holder)], "expected_checks": [],
                "operations": [{"kind": "shader_nodes", "target": queries.object_handle(holder), "parameters": {
                    "name": "QA Math", "nodes": [node, {"id": "o", "type": "OUTPUT_MATERIAL"}],
                    "links": [{"from_node": "t", "from_socket": "Value", "to_node": "o", "to_socket": "Surface"}]}}]}
    try:
        validate_program("angelo_scene_program", shader({"id": "t", "type": "MATH",
                                                         "settings": {"operation": "GREATER_THAN"}}))
        message = ""
    except ValueError as error:
        message = str(error)
    check("typed_math_refusal_names_blenders_node", "type ShaderNodeMath takes every setting" in message,
          message=message)
    result = run(shader({"id": "t", "type": "ShaderNodeMath", "settings": {"operation": "GREATER_THAN"}}))
    material = bpy.data.materials.get("QA Math")
    operations = [n.operation for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeMath"] if material else []
    check("shader_node_math_commits_greater_than", result.get("status") == "committed" and operations == ["GREATER_THAN"],
          status=result.get("status"), error=result.get("error"), operations=operations)
    big = bpy.data.objects.new("QA Grass", bpy.data.meshes.new("QA Grass"))
    side = 330  # 108,900 vertices: over the 100,000-vertex input limit.
    big.data.from_pydata([(x, y, 0) for y in range(side) for x in range(side)], [], [])
    scene.collection.objects.link(big)
    for obj in W.view_layer.objects:
        obj.select_set(False)
    big.select_set(True)
    W.view_layer.objects.active = big
    W.view_layer.update()
    observation = turn_context.build(W)[0]
    row = next((o for o in observation.get("objects", ()) if o.get("name") == "QA Grass"), {})
    omitted = row.get("revision_omitted") or {}
    check("over_limit_omission_gives_counts_and_route", omitted.get("reason") == "OVER_LIMIT"
          and "108,900 vertices; at most 100,000" in str(omitted.get("counts"))
          and omitted.get("route") == "angelo_execute_python"
          and "the mesh can stay as it is" in str(omitted.get("note")), omitted=omitted)
    try:
        native.precondition(W, "angelo_scene_program", {
            "mode": "APPLY", "read_set": [], "expected_checks": [],
            "write_set": [{"handle": queries.object_handle(big), "revision": "f" * 64}],
            "operations": [{"kind": "transform", "target": queries.object_handle(big),
                            "parameters": {"location": [0, 0, 1]}}]})
        refusal = None
    except ProgramError as error:
        refusal = (error.code, str(error))
    check("over_limit_bind_names_counts_and_route", refusal is not None and refusal[0] == "LIMIT_EXCEEDED"
          and "'QA Grass'" in refusal[1] and "angelo_execute_python" in refusal[1], refusal=refusal)


def script_ids():
    code = "result = {'objects': len(bpy.data.objects)}\n"
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "Count objects", "code": code})
    check("a_finished_script_names_its_id", result.get("script") == script_id(code) and result.get("success"),
          script=result.get("script"), error=result.get("error"))


def leftovers():
    """Last: it saves the file and opens it again."""
    cube = bpy.data.objects.get("Cube")
    material = cube.material_slots[0].material.name if cube and cube.material_slots else None
    result = run({"mode": "APPLY", "read_set": [], "write_set": [entry(cube)], "expected_checks": [],
                  "operations": [{"kind": "object_delete", "target": queries.object_handle(cube), "parameters": {}}]})
    orphan = result.get("orphan_data") or {}
    held = (orphan.get("held_only_by_them") or {}).get("names")
    check("deleted_cubes_material_is_named_apart", material is not None and held == [material]
          and "Cube" in orphan.get("names", ()) and "orphans_purge" in orphan.get("note", ""), orphan_data=orphan)
    path = OUT / "leftovers.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    reopened = bpy.data.materials.get(material)
    check("a_save_writes_that_material_unused_and_drops_the_mesh",
          reopened is not None and reopened.users == 0 and bpy.data.meshes.get("Cube") is None,
          material_users=None if reopened is None else reopened.users)


try:
    revisions()
    typed_ends()
    script_ids()
    leftovers()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "visibility-gaps.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("VISIBILITY_GAPS failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
