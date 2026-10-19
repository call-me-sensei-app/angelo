# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live materials a material-blind program reads in place instead of copying, in a disposable process.

A move or a typed modifier edit borrows each eligible material (stage_borrowing):
the stage stays a closed native graph or a bundle without live materials, the
live materials' users are unchanged after every yield and after commit, and a
borrowed material that was removed, edited or animated while the job waited
refuses the program without writing anything. Programs that can write a
material still clone it.
"""

import json
import os
from pathlib import Path
import sys
import traceback

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
report = {"checks": {}, "binary": bpy.app.binary_path, "success": False}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def principled(name, colour):
    material = bpy.data.materials.new(name)
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colour
    return material


def fixture(name, count=3):
    """A cube with ``count`` Principled materials; the second one is animated, so it is cloned."""
    window = bpy.context.window
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    materials = [principled("%s %d" % (name, index), (index / 4, .5, .3, 1.)) for index in range(count)]
    for material in materials:
        mesh.materials.append(material)
    materials[1].node_tree.animation_data_create()
    obj = bpy.data.objects.new(name, mesh)
    obj.modifiers.new("Bevel", "BEVEL")
    window.scene.collection.objects.link(obj)
    window.view_layer.update()
    return window, obj, materials


def program(window, obj, operation=None):
    handle = queries.object_handle(obj)
    operation = operation or {"kind": "transform", "parameters": {"location": [1., 2., 3.]}}
    return native.begin(window, "angelo_scene_program", args_for(window, [dict(operation, target=handle)], writes=[obj]))


def prepared(job):
    with spent_budget():
        job.poll()  # Preparation alone: the stage is suspended.
    return job.stage


def users(materials):
    return [material.users for material in materials]


def remove(obj, materials):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    for material in materials:
        if material.name in bpy.data.materials:
            bpy.data.materials.remove(material)


def borrowed_and_committed():
    window, obj, materials = fixture("Borrowing cube")
    before, counts = set(native._main_ids()), users(materials)
    job = program(window, obj)
    stage = prepared(job)
    rows = stage.extras.get(stage_borrowing.BORROWED, ())
    check("move_borrows_unanimated_materials", [row[1:3] for row in rows] == [("DATA", 0), ("DATA", 2)])
    check("borrowing_stage_stays_native", stage.native_owner is not None and not stage.bundle)
    check("suspended_stage_holds_no_live_user", users(materials) == counts and no_stages())
    owned = {block.as_pointer() for block in stage.resident.owned_ids}
    check("only_the_animated_material_is_cloned", not owned & {materials[0].as_pointer(), materials[2].as_pointer()}
          and sum(isinstance(block, bpy.types.Material) for block in stage.resident.owned_ids) == 1)
    result = settle(job)
    check("borrowing_move_is_ready", result.get("ready_to_commit"))
    result = job.commit()
    check("borrowing_move_commits", result.get("success") and list(obj.location) == [1., 2., 3.])
    check("commit_keeps_the_live_materials", list(obj.data.materials) == materials and users(materials) == counts)
    check("commit_leaves_no_stage_ids", no_stages() and set(native._main_ids()) == before)
    job = program(window, obj, {"kind": "modifier_configure",
                                "parameters": {"modifier": "Bevel", "type": "BEVEL", "settings": {"width": .2}}})
    check("modifier_edit_borrows", bool(prepared(job).extras.get(stage_borrowing.BORROWED)))
    check("modifier_edit_commits", settle(job).get("ready_to_commit") and job.commit().get("success")
          and abs(obj.modifiers["Bevel"].width - .2) < 1e-6 and users(materials) == counts and no_stages())
    remove(obj, materials)


def file_backed_bundle_holds_no_live_material():
    window, obj, materials = fixture("Bundled cube")
    job = program(window, obj)
    job.backend.memory_staging = False
    stage = prepared(job)
    with bpy.data.libraries.load(stage.bundle, link=False) as (source, _destination):
        names = list(source.materials)
    check("bundle_is_file_backed", stage.native_owner is None and Path(stage.bundle).is_file())
    check("bundle_carries_only_the_clone", materials[0].name not in names and materials[2].name not in names
          and len(names) == 1)
    check("bundle_hydration_restores_and_commits", settle(job).get("ready_to_commit") and job.commit().get("success")
          and list(obj.data.materials) == materials and no_stages())
    remove(obj, materials)


def live_changes_while_suspended_refuse():
    window, obj, materials = fixture("Stale cube")
    job = program(window, obj)
    prepared(job)
    bpy.data.materials.remove(materials.pop())
    result = settle(job)
    check("removed_borrowed_material_refuses", result.get("final") and not result.get("success")
          and result.get("error_code") == "STALE_DEPENDENCY" and no_stages() and list(obj.location) == [0., 0., 0.])

    job = program(window, obj)
    prepared(job)
    materials[0].node_tree.animation_data_create()  # Evaluating it on the stage would write it back.
    result = settle(job)
    check("animated_borrowed_material_refuses", result.get("final") and result.get("error_code") == "STALE_DEPENDENCY"
          and no_stages() and list(obj.location) == [0., 0., 0.])
    materials[0].node_tree.animation_data_clear()

    job = program(window, obj)
    check("edit_target_is_ready", settle(job).get("ready_to_commit"))
    materials[0].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = .9
    result = job.commit()
    check("edited_borrowed_material_refuses_commit", not result.get("success") and no_stages()
          and result.get("error_code") in {"STALE_DEPENDENCY", "STALE_REVISION"} and list(obj.location) == [0., 0., 0.])
    remove(obj, materials)


def writers_still_clone():
    window, obj, materials = fixture("Writing cube")
    job = program(window, obj, {"kind": "material_principled",
                                "parameters": {"name": "New material", "slot": 0, "inputs": {"Roughness": .4}}})
    stage = prepared(job)
    clones = [block for block in stage.resident.owned_ids if isinstance(block, bpy.types.Material)]
    check("material_program_clones_every_material", not stage.extras.get(stage_borrowing.BORROWED)
          and len(clones) == len(materials))
    job.cancel()
    check("cancelled_writer_leaves_no_stage", no_stages())
    remove(obj, materials)


try:
    borrowed_and_committed()
    file_backed_bundle_holds_no_live_material()
    live_changes_while_suspended_refuse()
    writers_still_clone()
    report["success"] = True
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
