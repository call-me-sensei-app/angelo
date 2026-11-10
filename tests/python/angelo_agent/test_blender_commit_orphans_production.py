# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The production commit frees the data its own swaps replaced (``commit_orphans``, native right ``delete_replaced_data``).

Every call goes through the real ``Controller`` and ``angelo.commit_native_job``, so through ``program_undo.commit``
with the native bridge (``_angelo_undo.begin_synchronized``): the commit scope protects Main, which is why public
removal refuses there and the native right is the only path that frees anything.

* A transform apply frees the mesh it replaced; a transform and a material edit of one object in one program free the
  old mesh and the old material (a second pass: the old material's last user was the old mesh); a mesh with shape
  keys goes with its keys; a World edit frees the World it replaced.
* Kept, as the same edits in place keep them: a data-linked material the swap released (unlisted, as Blender lists
  nothing), and a deleted object's data (listed).
* A native refusal: a driver's target counts as no user, so only the native proof refuses that mesh; the batch is
  proved again root by root, the mesh beside it is freed and the refused one is counted (``not_freed``) and listed.
* Every family a commit swaps: a material edited for all its users (``ALL_USERS``), a World graph, particle settings,
  an Action.
* Forced: a commit that never sealed (the native "Seal" refusal), a pass limit, a deletion outside the proof (the
  commit's ``cleanup_error``) and a busy scene each keep what they must, count it and say why.
* Undo and Redo after every case give the exact states before and after it back.

Run in a disposable instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``; the binary must keep a background document's Undo history
(``_angelo_undo.keep_background_document``). No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import contextlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-commit-orphans-production-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
from angelo_agent import agent_workspace_ui, native_programs as native, program_undo  # noqa: E402
from angelo_agent import commit_orphans, program_commit_scope, scene_queries as queries, scene_settings  # noqa: E402
from unittest.mock import patch  # noqa: E402
from program_production_helpers import production_call, production_controller  # noqa: E402

REPORT = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}


def check(name, value, **details):
    REPORT["checks"][name] = bool(value)
    if details:
        REPORT["cases"][name] = details
    assert value, (name, details)


def window():
    return bpy.context.window_manager.windows[0]


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window(), obj)}


def op(kind, target, **parameters):
    return {"kind": kind, "target": target if isinstance(target, str) or target is None
            else queries.object_handle(target), "parameters": parameters}


def run(controller, label, operations, writes=(), scene=False):
    """One program through the production path (no invalidate here: it would expire the handles just built)."""
    write_set = [entry(obj) for obj in writes]
    if scene:
        settings = scene_settings.execute(window(), {})
        write_set.append({"handle": settings["handle"], "revision": settings["revision"]})
    result = production_call(controller, window(), label, "angelo_scene_program",
                             {"mode": "APPLY", "read_set": [], "write_set": write_set, "operations": operations,
                              "expected_checks": []})
    REPORT["cases"][label] = {key: result.get(key) for key in ("ok", "status", "error", "orphan_data", "undo_published",
                                                              "cleanup_error")}
    return result


def unused(collection):
    return sorted(block.name for block in collection if block.users == 0 and not block.use_fake_user)


def uids(collection):
    return {block.session_uid for block in collection}


def ball(name, x):
    data = bpy.data.meshes.new(name + " Mesh")
    data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (.5, .5, 1)], [],
                     [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)])
    data.materials.append(bpy.data.materials.new(name + " Paint"))
    obj = bpy.data.objects.new(name, data)
    obj.location.x = x
    bpy.context.scene.collection.objects.link(obj)
    return obj


def fixture():
    """A saved file opened again: the load starts the document's Undo history (keep_background_document)."""
    for index, name in enumerate(("Plain", "Painted", "Driven", "Deleted", "Beside", "Keyed", "Wooden", "Shared A",
                                  "Haired", "Unsealed", "Busy", "Twice", "Stray")):
        ball(name, 3 * index)
    shared = bpy.data.objects["Shared A"]
    other = bpy.data.objects.new("Shared B", bpy.data.meshes.new("Shared B Mesh"))
    other.data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    other.data.materials.append(shared.data.materials[0])
    bpy.context.scene.collection.objects.link(other)
    hair = bpy.data.objects["Haired"].modifiers.new("Hair", "PARTICLE_SYSTEM").particle_system.settings
    hair.type, hair.count = "HAIR", 100
    empty = bpy.data.objects.new("Keyed Empty", None)
    bpy.context.scene.collection.objects.link(empty)
    for frame, x in ((1, 0.0), (10, 2.0), (20, 4.0)):
        empty.location.x = x
        empty.keyframe_insert("location", frame=frame)
    empty.location.x = 0.0
    keyed = bpy.data.objects["Keyed"]
    keyed.shape_key_add(name="Basis")
    keyed.shape_key_add(name="Lift").data[4].co.z = 2.0
    bpy.data.objects["Wooden"].data.materials[0].name = "Wood"   # Data-linked: the mesh's slot holds it.
    reader = bpy.data.objects.new("Reader", None)
    bpy.context.scene.collection.objects.link(reader)
    variable = reader.driver_add("empty_display_size").driver.variables.new()
    variable.targets[0].id_type = "MESH"
    variable.targets[0].id, variable.targets[0].data_path = bpy.data.meshes["Driven Mesh"], "vertices[0].co.x"
    path = OUT / "fixture.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    queries.invalidate()


def objects():
    return window().scene.objects


def state():
    """What Undo and Redo must give back exactly: every object's data and materials, the World, the counts."""
    return {"objects": {obj.name: (obj.data.name if obj.data else None, len(obj.data.vertices) if obj.type == "MESH" else 0,
                                   round(obj.data.vertices[1].co.x, 5) if obj.type == "MESH" else 0,
                                   [slot.material.name if slot.material else None for slot in obj.material_slots])
                        for obj in objects()},
            "world": window().scene.world.name if window().scene.world else None,
            "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials), "worlds": len(bpy.data.worlds),
            "keys": len(bpy.data.shape_keys)}


def undo_redo(label, before, after):
    """One Undo gives the state before the commit back, one Redo the committed one."""
    bpy.ops.ed.undo()
    undone = state()
    bpy.ops.ed.redo()
    redone = state()
    check(label + "_undo_restores", undone == before, undone=undone, before=before)
    check(label + "_redo_reapplies", redone == after, redone=redone, after=after)


def cases(controller):
    plain, uid, before = objects()["Plain"], objects()["Plain"].data.session_uid, state()
    result = run(controller, "transform_apply", [op("transform", plain, scale=[2.0, 1.0, 1.0]),
                                                 op("transform_apply", plain, scale=True)], [plain])
    check("a_transform_apply_commits_on_the_production_path", result["ok"] and result.get("undo_published") is not False,
          error=result.get("error"))
    check("its_replaced_mesh_is_freed", uid not in uids(bpy.data.meshes) and not unused(bpy.data.meshes)
          and "orphan_data" not in result, orphans=unused(bpy.data.meshes), orphan_data=result.get("orphan_data"))
    undo_redo("transform_apply", before, state())

    painted, before = objects()["Painted"], state()
    mesh_uid, paint_uid = painted.data.session_uid, painted.data.materials[0].session_uid
    result = run(controller, "transform_and_material", [
        op("transform", painted, scale=[1.0, 1.0, 2.0]), op("transform_apply", painted, scale=True),
        op("material_principled", painted, name="Painted Paint", inputs={"Roughness": .2})], [painted])
    check("a_transform_and_a_material_edit_free_both_replaced_blocks", result["ok"]
          and mesh_uid not in uids(bpy.data.meshes) and paint_uid not in uids(bpy.data.materials)
          and not unused(bpy.data.meshes) and not unused(bpy.data.materials) and "orphan_data" not in result,
          error=result.get("error"), meshes=unused(bpy.data.meshes), materials=unused(bpy.data.materials))
    undo_redo("transform_and_material", before, state())

    keyed, before = objects()["Keyed"], state()
    keyed_uid, keys = keyed.data.session_uid, len(bpy.data.shape_keys)
    result = run(controller, "shape_keys", [op("transform", keyed, scale=[1.0, 1.0, 3.0]),
                                            op("transform_apply", keyed, scale=True)], [keyed])
    check("a_mesh_with_shape_keys_goes_with_its_keys", result["ok"] and keyed_uid not in uids(bpy.data.meshes)
          and len(bpy.data.shape_keys) == keys and all(block.users for block in bpy.data.shape_keys),
          error=result.get("error"), keys=[(block.name, block.users) for block in bpy.data.shape_keys])
    undo_redo("shape_keys", before, state())

    wooden, before = objects()["Wooden"], state()
    result = run(controller, "released", [op("material_principled", wooden, name="Fresh Paint", slot=0,
                                              inputs={"Roughness": .5})], [wooden])   # Takes Wood's slot.
    note = result.get("orphan_data") or {}
    # As after the same edit in place: Wood stays without users and nothing lists it (Blender lists nothing).
    check("a_material_the_swap_released_stays_unlisted", result["ok"]
          and [slot.material.name for slot in objects()["Wooden"].material_slots] == ["Fresh Paint"]
          and "Wood" in bpy.data.materials and bpy.data.materials["Wood"].users == 0 and not note
          and not unused(bpy.data.meshes), orphan_data=note, meshes=unused(bpy.data.meshes))
    undo_redo("released", before, state())

    world_uid, before = window().scene.world.session_uid, state()
    result = run(controller, "world", [op("world_configure", scene_settings.execute(window(), {})["handle"],
                                          strength=0.4)], scene=True)
    check("a_world_edit_frees_the_world_it_replaced", result["ok"] and world_uid not in uids(bpy.data.worlds)
          and not unused(bpy.data.worlds), error=result.get("error"), worlds=unused(bpy.data.worlds))
    undo_redo("world", before, state())

    # A driver's target is no user, so only the native proof refuses the Driven mesh: the whole batch is refused,
    # each root is proved again alone, and the one beside it is freed.
    driven, beside, deleted, before = objects()["Driven"], objects()["Beside"], objects()["Deleted"], state()
    driven_uid, beside_uid = driven.data.session_uid, beside.data.session_uid
    result = run(controller, "refused", [op("mesh_recalculate_normals", driven), op("mesh_recalculate_normals", beside),
                                         op("object_delete", deleted)], [driven, beside, deleted])
    note = result.get("orphan_data") or {}
    target = objects()["Reader"].animation_data.drivers[0].driver.variables[0].targets[0].id
    check("a_native_refusal_keeps_only_the_refused_root", result["ok"] and target is not None
          and target.session_uid == driven_uid and beside_uid not in uids(bpy.data.meshes), error=result.get("error"))
    check("the_refusal_is_counted_and_everything_kept_is_listed",
          set(note.get("names", ())) == {target.name, "Deleted Mesh"} and note.get("not_freed") == 1
          and "points at" in note.get("note", "") and "deleted object" in note.get("note", ""), orphan_data=note)
    undo_redo("refused", before, state())
    kinds(controller)
    forced(controller)


def freed(label, operations, writes, old, collection, scene=False, controller=None):
    """One program whose commit must free ``old`` (a session uid in ``collection``), with Undo and Redo after it."""
    before, earlier = state(), set(unused(collection))   # Earlier cases may leave released data (Wood) by design.
    result = run(controller, label, operations, writes, scene=scene)
    check(label + "_frees_what_it_replaced", result["ok"] and old not in uids(collection)
          and not set(unused(collection)) - earlier and "orphan_data" not in result, error=result.get("error"),
          left=sorted(set(unused(collection)) - earlier), orphan_data=result.get("orphan_data"))
    undo_redo(label, before, state())


def kinds(controller):
    """Every family a commit swaps, on the production path."""
    shared, other = objects()["Shared A"], objects()["Shared B"]
    paint = shared.data.materials[0]
    freed("all_users", [op("material_principled", shared, name=paint.name, inputs={"Roughness": .3},
                           shared="ALL_USERS")], [shared], paint.session_uid, bpy.data.materials, controller=controller)
    check("all_users_both_objects_hold_the_edit", objects()["Shared B"].data.materials[0]
          == objects()["Shared A"].data.materials[0])
    freed("world_node_edit", [{"kind": "node_edit", "target": scene_settings.execute(window(), {})["handle"],
                               "parameters": {"world": True, "edits": [
                                   {"op": "add", "node": "Gain", "type": "ShaderNodeValue"}]}}],
          [], window().scene.world.session_uid, bpy.data.worlds, scene=True, controller=controller)
    haired = objects()["Haired"]
    freed("particle_settings", [op("particle_set", haired, modifier="Hair", settings={"count": 300})], [haired],
          haired.particle_systems[0].settings.session_uid, bpy.data.particles, controller=controller)
    empty = objects()["Keyed Empty"]
    freed("action", [op("keyframe_configure", empty, property="location", frames=[1, 10], interpolation="LINEAR")],
          [empty], empty.animation_data.action.session_uid, bpy.data.actions, controller=controller)


def stays(label, result, mesh_uid, reason):
    """The replaced mesh stays, counted (not_freed) and said why (not_freed_reasons)."""
    note = result.get("orphan_data") or {}
    check(label + "_keeps_counts_and_says_why", result["ok"] and mesh_uid in uids(bpy.data.meshes)
          and note.get("not_freed") == 1 and any(reason in text for text in note.get("not_freed_reasons", ())),
          error=result.get("error"), orphan_data=note)


def forced(controller):
    """The paths a well-formed commit never takes, forced: no seal, a pass limit, a stray deletion, a busy scene."""
    unsealed = objects()["Unsealed"]
    uid, before = unsealed.data.session_uid, state()
    with patch.object(commit_orphans, "seal", lambda *args: False):
        result = run(controller, "unsealed", [op("mesh_recalculate_normals", unsealed)], [unsealed])
    stays("unsealed", result, uid, "Seal the replaceable data")
    undo_redo("unsealed", before, state())

    busy = objects()["Busy"]
    uid, before = busy.data.session_uid, state()
    with patch.object(commit_orphans, "_scene_busy", lambda: True):
        result = run(controller, "busy", [op("mesh_recalculate_normals", busy)], [busy])
    stays("busy", result, uid, "evaluating the scene")
    undo_redo("busy", before, state())

    # A pass limit: the native receipt says it stopped unfinished before the root went (no real program needs a second
    # pass to reach it, so the receipt is forced; the native loop's limit is the same field).
    twice = objects()["Twice"]
    uid, before = twice.data.session_uid, state()

    def unfinished(self, roots, max_passes=16):
        self.check()
        return {"success": True, "removed": [], "refused": [], "passes": 1, "unfinished": True, "unexpected": 0,
                "error": ""}
    with patch.object(program_commit_scope.CommitScope, "delete_replaced", unfinished):
        result = run(controller, "unfinished", [op("mesh_recalculate_normals", twice)], [twice])
    stays("unfinished", result, uid, "freeing stopped after 1 passes")
    undo_redo("unfinished", before, state())

    stray, before = objects()["Stray"], state()
    original = program_commit_scope.CommitScope.delete_replaced

    def stray_deletion(self, roots, max_passes=16):
        return dict(original(self, roots, max_passes), unexpected=1)
    with patch.object(program_commit_scope.CommitScope, "delete_replaced", stray_deletion):
        result = run(controller, "unexpected", [op("mesh_recalculate_normals", stray)], [stray])
    check("a_deletion_outside_the_proof_is_reported_loudly", result["ok"] and "outside its proof" in str(
          result.get("cleanup_error")) and result.get("cleanup_warning") is True, cleanup=result.get("cleanup_error"))
    undo_redo("unexpected", before, state())


def run_all():
    classes = []
    try:
        bridge = program_undo.native_bridge()
        check("native_undo_bridge_available", bridge is not None)
        program_undo.keep_background_document()   # Every later load starts the loaded document's Undo history.
        fixture()
        check("the_loaded_document_has_undo_history", program_undo.keep_background_document())
        bpy.context.preferences.edit.use_global_undo = True
        native.register()
        classes = [c for c in agent_workspace_ui.workspace_operators()
                   if getattr(c, "bl_idname", "") == "angelo.commit_native_job"]
        for cls in classes:
            bpy.utils.register_class(cls)
        REPORT["frees_replaced_data"] = all(callable(getattr(bridge, name, None))
                                            for name in ("seal_replaceable", "delete_replaced_data"))
        with production_controller(OUT) as controller:
            cases(controller)
        check("no_stage_residue", not any(scene.get("_angelo_program_stage") for scene in bpy.data.scenes)
              and not native.runtime_is_staging())
        REPORT["success"] = True
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
        for cls in reversed(classes):
            with contextlib.suppress(Exception):
                bpy.utils.unregister_class(cls)
    (OUT / "commit-orphans-production.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_COMMIT_ORPHANS_PRODUCTION", json.dumps({key: REPORT.get(key) for key in ("success", "paid_requests")}),
          flush=True)
    assert REPORT.get("success"), REPORT.get("error")


run_all()
