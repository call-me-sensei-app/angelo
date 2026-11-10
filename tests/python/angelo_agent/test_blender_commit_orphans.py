# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The data a commit's own swaps leave without users is freed; nothing else is (``commit_orphans``).

* A mesh edit, a transform apply and a material edit free the mesh and material they replaced: no orphan stays,
  however many edits run, and the result names none.
* Undo after the commit gives the object its old mesh back (same name, vertices and normals); Redo the edited one.
* Never freed: data another object still uses, data with a fake user, data a driver variable points at, the
  person's own unused data, a deleted object's data (Blender's delete leaves it too; still reported), linked data.
* A mesh with shape keys goes with its keys.

Run in a disposable instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-commit-orphans-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"


def check(name, condition, **details):
    report["checks"][name] = bool(condition)
    if details:
        report["cases"][name] = details
    assert condition, (name, details)


def settle():
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="commit orphans")
    queries.invalidate()


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def op(kind, owner, /, **parameters):
    return {"kind": kind, "target": queries.object_handle(owner), "parameters": parameters}


def commit(build, writes):
    """Approve, stage and commit ``build()`` inside the host's Undo operator; ``(status, error, result)``."""
    settle()
    arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [entry(obj) for obj in writes],
                                     "operations": build()})
    approved = native.precondition(window, NAME, arguments)
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        staged = job.result or staged
    queries.invalidate()
    return staged.get("status"), str(staged.get("error") or ""), staged


def ball(name, location=(0, 0, 0)):
    data = bpy.data.meshes.new(name + " Mesh")
    data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (.5, .5, 1)], [],
                     [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)])
    data.materials.append(bpy.data.materials.new(name + " Paint"))
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    window.scene.collection.objects.link(obj)
    return obj


def unused(collection):
    return sorted(block.name for block in collection if block.users == 0)


def swaps():
    window.scene = bpy.data.scenes.new("Swaps")
    obj = ball("Plain")
    mesh, uid = obj.data, obj.data.session_uid
    normals = [tuple(round(v, 6) for v in polygon.normal) for polygon in mesh.polygons]
    status, error, result = commit(lambda: [op("transform", obj, scale=[2.0, 1.0, 1.0]),
                                            op("transform_apply", obj, scale=True)], [obj])
    check("a_transform_apply_commits", status == "committed", error=error)
    check("its_replaced_mesh_is_freed", uid not in {block.session_uid for block in bpy.data.meshes}
          and not unused(bpy.data.meshes) and "orphan_data" not in result, orphans=unused(bpy.data.meshes))
    check("the_edited_mesh_keeps_the_name", obj.data.name == "Plain Mesh" and obj.data.session_uid != uid)
    bpy.ops.ed.undo()
    obj = bpy.data.objects["Plain"]
    restored = [tuple(round(v, 6) for v in polygon.normal) for polygon in obj.data.polygons]
    check("undo_gives_the_old_mesh_back", obj.data.name == "Plain Mesh" and tuple(obj.scale) == (1.0, 1.0, 1.0)
          and abs(obj.data.vertices[1].co.x - 1.0) < 1e-6 and restored == normals and not unused(bpy.data.meshes))
    bpy.ops.ed.redo()
    obj = bpy.data.objects["Plain"]
    check("redo_gives_the_edited_mesh_back", abs(obj.data.vertices[1].co.x - 2.0) < 1e-6
          and not unused(bpy.data.meshes))

    painted = ball("Painted", (3, 0, 0))
    paint = painted.data.materials[0].session_uid
    status, error, result = commit(lambda: [op("material_principled", painted, name="Painted Paint",
                                                inputs={"Roughness": .2})], [painted])
    check("a_material_edit_commits_and_frees_what_it_replaced", status == "committed" and not unused(bpy.data.meshes)
          and not unused(bpy.data.materials) and paint not in {block.session_uid for block in bpy.data.materials}
          and "orphan_data" not in result, error=error, meshes=unused(bpy.data.meshes),
          materials=unused(bpy.data.materials))
    both = ball("Both", (9, 0, 0))
    both_mesh, both_paint = both.data.session_uid, both.data.materials[0].session_uid
    status, error, result = commit(lambda: [op("transform", both, scale=[1.0, 1.0, 2.0]),
                                            op("transform_apply", both, scale=True),
                                            op("material_principled", both, name="Both Paint", inputs={"Metallic": .5})],
                                   [both])
    check("a_transform_and_a_material_edit_in_one_program_free_both", status == "committed"
          and both_mesh not in {block.session_uid for block in bpy.data.meshes}
          and both_paint not in {block.session_uid for block in bpy.data.materials}
          and not unused(bpy.data.meshes) and not unused(bpy.data.materials) and "orphan_data" not in result,
          error=error, meshes=unused(bpy.data.meshes), materials=unused(bpy.data.materials))
    for index in range(30):   # Many edits leave nothing behind (500 per the measurement in agent_execution.rst).
        target = obj if index % 2 else painted
        status, error, _ = commit(lambda: [op("mesh_recalculate_normals", target)], [target])
        assert status == "committed", error
    check("thirty_edits_leave_no_unused_data", not unused(bpy.data.meshes) and not unused(bpy.data.materials))

    keyed = ball("Keyed", (6, 0, 0))
    keyed.shape_key_add(name="Basis")
    keyed.shape_key_add(name="Lift").data[4].co.z = 2.0
    keys = len(bpy.data.shape_keys)
    status, error, _ = commit(lambda: [op("transform", keyed, scale=[1.0, 1.0, 3.0]),
                                       op("transform_apply", keyed, scale=True)], [keyed])
    check("a_mesh_with_shape_keys_goes_with_its_keys", status == "committed" and not unused(bpy.data.meshes)
          and len(bpy.data.shape_keys) == keys and all(block.users for block in bpy.data.shape_keys), error=error,
          shape_keys=[(block.name, block.users) for block in bpy.data.shape_keys])


def kept():
    window.scene = bpy.data.scenes.new("Kept")
    shared = ball("Shared")
    other = bpy.data.objects.new("Other", shared.data)
    window.scene.collection.objects.link(other)
    status, error, _ = commit(lambda: [op("mesh_recalculate_normals", shared)], [shared])
    check("data_another_object_uses_stays", status == "committed" and other.data.users == 1
          and other.data != shared.data, error=error)

    fake = ball("Fake")
    fake.data.use_fake_user = True
    old = fake.data.session_uid
    status, error, _ = commit(lambda: [op("mesh_recalculate_normals", fake)], [fake])
    check("data_with_a_fake_user_stays", status == "committed"
          and old in {block.session_uid for block in bpy.data.meshes}, error=error)

    target = ball("Driven Target")
    reader = bpy.data.objects.new("Reader", None)
    window.scene.collection.objects.link(reader)
    curve = reader.driver_add("empty_display_size")
    variable = curve.driver.variables.new()
    variable.targets[0].id_type = "MESH"
    variable.targets[0].id, variable.targets[0].data_path = target.data, "vertices[0].co.x"
    old = target.data.session_uid
    status, error, result = commit(lambda: [op("mesh_recalculate_normals", target)], [target])
    pointed = variable.targets[0].id
    check("data_a_driver_points_at_stays_and_is_reported", status == "committed" and pointed is not None
          and pointed.session_uid == old and pointed.name in (result.get("orphan_data") or {}).get("names", ()),
          error=error, orphan_data=result.get("orphan_data"))

    own = bpy.data.meshes.new("The Person's Spare")
    deleted = ball("Deleted", (9, 0, 0))
    edited = ball("Edited", (12, 0, 0))
    status, error, result = commit(lambda: [op("object_delete", deleted), op("mesh_recalculate_normals", edited)],
                                   [deleted, edited])
    left = unused(bpy.data.meshes)
    check("unused_data_the_commit_did_not_replace_stays", status == "committed" and own.name in left
          and "Deleted Mesh" in left and "Edited Mesh" not in left, error=error, left=left)
    check("a_deleted_objects_data_is_still_reported",
          "Deleted Mesh" in (result.get("orphan_data") or {}).get("names", ()), orphan_data=result.get("orphan_data"))


def linked():
    """A local object on a library's mesh: the edit gives it a local copy; the library's mesh is never freed."""
    library = OUT / "library.blend"
    window.scene = bpy.data.scenes.new("Linked")
    source = bpy.data.meshes.new("Library Mesh")
    source.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    bpy.data.libraries.write(str(library), {source}, fake_user=True)
    bpy.data.meshes.remove(source)
    with bpy.data.libraries.load(str(library), link=True) as (data_from, data_to):
        data_to.meshes = ["Library Mesh"]
    mesh = data_to.meshes[0]
    obj = bpy.data.objects.new("On Library Mesh", mesh)
    window.scene.collection.objects.link(obj)
    status, error, _ = commit(lambda: [op("mesh_recalculate_normals", obj)], [obj])
    present = [block for block in bpy.data.meshes if block.library is not None]
    check("linked_data_is_never_freed", len(present) == 1 and present[0].name == "Library Mesh",
          status=status, error=error[:200])


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    for part in (swaps, kept, linked):
        try:
            part()
        except AssertionError:
            report.setdefault("failed_parts", []).append(part.__name__ + ": " + traceback.format_exc()[-600:])
    check("every_part_passed", not report.get("failed_parts"))
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "commit-orphans.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_COMMIT_ORPHANS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error") or report.get("failed_parts")
