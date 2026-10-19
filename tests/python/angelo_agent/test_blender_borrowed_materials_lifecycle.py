# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a stage that borrows live materials leaves control, in a disposable process.

Pins:

* Accepting a PREVIEW of a material-blind program keeps the live materials in
  their slots, with unchanged users, and leaves no material copy behind.
* An inspection edit through ``job_context`` reaches a private clone, never the
  live material, and the job still commits without a duplicate.
* A live material edit tagged but not yet evaluated still reaches the artist's
  depsgraph after a stage evaluation (the stage's active depsgraph clears the
  originals' recalc flags; the live graph keeps its own tags).
* A suspension that fails after the stage was evaluated leaves no stage and
  no extra material user.
* A nested node group of a borrowed material removed while the job waits
  refuses the program (native and file-backed stages) without a crash or write.
* A save while the job waits writes no stage scene and no material copy.
* An OBJECT-linked slot and the data slot beneath it are borrowed, released
  and restored on native and file-backed stages.
* A material that links an object is never borrowed in production: a Python
  pointer property makes it ineligible, and an ID property (which the family
  walk cannot see) is refused by the custom-link admission before staging.
"""

import json
import os
from pathlib import Path
import sys
import traceback
from unittest import mock

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, stage_borrowing  # noqa: E402
from program_qa_helpers import args_for, no_stages, spent_budget  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
MOVE = {"kind": "transform", "parameters": {"location": [1., 2., 3.]}}


def check(name, condition):
    report["checks"][name] = bool(condition)


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def cube(name, count=2, group=False):
    window = bpy.context.window
    materials = [bpy.data.materials.new("%s %d" % (name, index)) for index in range(count)]
    if group:
        tree = bpy.data.node_groups.new(name + " group", "ShaderNodeTree")
        tree.interface.new_socket("Value", in_out="OUTPUT", socket_type="NodeSocketFloat")
        tree.nodes.new("NodeGroupOutput")
        tree.nodes.new("ShaderNodeMath")
        node = materials[0].node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = tree
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    for material in materials:
        mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    window.scene.collection.objects.link(obj)
    window.view_layer.update()
    return window, obj, materials


def begin(window, obj, mode="APPLY"):
    handle = queries.object_handle(obj)
    return native.begin(window, "angelo_scene_program",
                        args_for(window, [dict(MOVE, target=handle)], writes=[obj], mode=mode))


def prepared(job):
    with spent_budget():
        job.poll()
    return job.stage


def names():
    return sorted(material.name for material in bpy.data.materials)


def preview_accept():
    window, obj, materials = cube("Preview")
    before, users = names(), [material.users for material in materials]
    result = settle(begin(window, obj, mode="PREVIEW"))
    check("preview:retained_without_live_users", result.get("status") == "preview" and no_stages()
          and [material.users for material in materials] == users)
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("preview:accept_commits", settle(accepted).get("ready_to_commit") and accepted.commit().get("success"))
    check("preview:live_materials_kept", list(obj.data.materials) == materials and names() == before
          and [material.users for material in materials] == users and list(obj.location) == [1., 2., 3.])
    check("preview:no_stage_left", no_stages())


def inspection_stays_private():
    window, obj, materials = cube("Inspect")
    materials[0].roughness, before = .5, names()
    job = begin(window, obj)
    check("inspect:ready", settle(job).get("ready_to_commit"))
    with native.job_context(job.id) as stage:
        held = next(iter(stage.objects.values())).data.materials[0]
        check("inspect:holds_a_private_clone", held != materials[0] and held.name != materials[0].name)
        held.roughness = .123
    check("inspect:live_untouched", round(materials[0].roughness, 4) == .5)
    check("inspect:commits_without_duplicates", job.commit().get("success") and list(obj.data.materials) == materials
          and names() == before and no_stages())


def live_tag_survives_stage_evaluation():
    window, obj, materials = cube("Recalc")
    materials[0].roughness = .5
    window.view_layer.update()
    materials[0].roughness = .7  # Tagged on the artist's graph, not evaluated yet.
    job = begin(window, obj)
    prepared(job)
    job.cancel()
    window.view_layer.update()
    evaluated = materials[0].evaluated_get(window.view_layer.depsgraph).roughness
    check("recalc:live_graph_still_updates", round(evaluated, 4) == .7)


def failed_suspension_releases():
    window, obj, materials = cube("Suspend")
    users, before = [material.users for material in materials], names()
    job = begin(window, obj)
    from angelo_agent import action_kernels
    with mock.patch.object(action_kernels, "node_metadata", side_effect=RuntimeError("injected suspension failure")):
        with spent_budget():
            result = job.poll()
    report["observed"]["failed_suspension"] = {key: result.get(key) for key in ("status", "final", "error_code")}
    if not result.get("final"):
        job.cancel()
    check("suspend_failure:no_stage_and_no_live_user", no_stages() and names() == before
          and [material.users for material in materials] == users and list(obj.location) == [0., 0., 0.])


def nested_group_removed(memory):
    label = "native" if memory else "bundle"
    window, obj, materials = cube("Group " + label, group=True)
    group = materials[0].node_tree.nodes["Group"].node_tree
    job = begin(window, obj)
    job.backend.memory_staging = memory
    stage = prepared(job)
    check("group_%s:borrowed" % label, any(row[1:3] == ("DATA", 0) for row in stage.extras.get(stage_borrowing.BORROWED, ())))
    check("group_%s:stage_kind" % label, (stage.native_owner is not None) == memory)
    bpy.data.node_groups.remove(group)
    result = settle(job)
    report["observed"]["group_" + label] = {key: result.get(key) for key in ("status", "final", "error_code")}
    check("group_%s:refused" % label, result.get("final") and not result.get("success")
          and result.get("error_code") in {"STALE_DEPENDENCY", "STALE_REVISION"})
    check("group_%s:nothing_written" % label, list(obj.location) == [0., 0., 0.] and no_stages()
          and list(obj.data.materials) == materials)


def save_while_waiting():
    window, obj, materials = cube("Saved")
    users, before = [material.users for material in materials], names()
    job = begin(window, obj)
    stage = prepared(job)
    check("save:job_waits_with_borrowed_rows", bool(stage.extras.get(stage_borrowing.BORROWED)))
    path = OUT / "saved_while_waiting.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    with bpy.data.libraries.load(str(path)) as (source, _destination):
        scenes, saved = list(source.scenes), sorted(source.materials)
    check("save:no_stage_scene", not any(name.startswith("__angelo_stage") for name in scenes))
    report["observed"]["save"] = {"saved": saved, "session": before}
    check("save:no_material_copy", set(saved) <= set(before) and {m.name for m in materials} <= set(saved)
          and [material.users for material in materials] == users)
    check("save:job_still_commits", settle(job).get("ready_to_commit") and job.commit().get("success")
          and list(obj.data.materials) == materials and no_stages())


def object_linked_slots(memory):
    """An OBJECT-linked slot and the data slot beneath it are both borrowed, released and restored."""
    label = "native" if memory else "bundle"
    window, obj, materials = cube("Object slot " + label, count=3)
    obj.data.materials.pop()  # Two data slots; the third material is linked to the object instead.
    obj.material_slots[1].link = "OBJECT"
    obj.material_slots[1].material = materials[2]
    window.view_layer.update()
    users, before = [material.users for material in materials], names()
    job = begin(window, obj)
    job.backend.memory_staging = memory
    stage = prepared(job)
    rows = sorted(row[1:3] for row in stage.extras.get(stage_borrowing.BORROWED, ()))
    check("object_slot_%s:all_three_slots_borrowed" % label, rows == [("DATA", 0), ("DATA", 1), ("OBJECT", 1)]
          and (stage.native_owner is not None) == memory and [material.users for material in materials] == users)
    check("object_slot_%s:commits_with_slots_kept" % label, settle(job).get("ready_to_commit")
          and job.commit().get("success") and list(obj.data.materials) == materials[:2]
          and [(slot.link, slot.material) for slot in obj.material_slots] == [("DATA", materials[0]), ("OBJECT", materials[2])]
          and [material.users for material in materials] == users and names() == before and no_stages())


def object_links_are_never_borrowed():
    """A material linking an object (Python pointer or ID property) is not borrowed, and its program refuses."""
    from angelo_agent import exact_fence, program_dependencies
    bpy.types.Material.review_target = bpy.props.PointerProperty(type=bpy.types.Object)
    try:
        for label, link in (("python_pointer", lambda material, other: setattr(material, "review_target", other)),
                            ("id_property", lambda material, other: material.__setitem__("review_target_id", other))):
            window, obj, materials = cube("Linked " + label, count=1)
            other = bpy.data.objects.new("Linked target " + label, None)
            window.scene.collection.objects.link(other)
            link(materials[0], other)
            window.view_layer.update()
            with exact_fence.fence():
                family = stage_borrowing._family(materials[0], program_dependencies._embedded_links)
            report["observed"]["linked_" + label] = None if family is None else [row[0] for row in family[0]]
            try:
                result = settle(begin(window, obj))
            except Exception as error:  # Refused while binding.
                result = {"final": True, "success": False, "error_code": getattr(error, "code", type(error).__name__)}
            report["observed"]["linked_" + label + "_result"] = {key: result.get(key) for key in ("status", "error_code")}
            check("linked_%s:refused_without_stage" % label, result.get("final") and not result.get("success")
                  and no_stages() and list(obj.location) == [0., 0., 0.])
            native.cleanup_all()
        # The Python pointer is a declared RNA link: the family walk itself refuses it.
        check("linked_python_pointer:not_eligible", report["observed"]["linked_python_pointer"] is None)
    finally:
        del bpy.types.Material.review_target


try:
    for test in (preview_accept, inspection_stays_private, live_tag_survives_stage_evaluation,
                 failed_suspension_releases, lambda: nested_group_removed(True),
                 lambda: nested_group_removed(False), save_while_waiting, lambda: object_linked_slots(True),
                 lambda: object_linked_slots(False), object_links_are_never_borrowed):
        try:
            test()
        finally:
            native.cleanup_all()
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_LIFECYCLE failed checks:", json.dumps(failed))
assert report["success"], failed
