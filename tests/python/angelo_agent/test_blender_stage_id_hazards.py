# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression: what a staged copy's name or count could do to a program, and the data a commit leaves behind.

* H1: a live material in two slots of one object became two staged copies, so a material_assignment check over
  all faces counted only the first slot's faces and refused a correct program.
* H2: a material_assign gave a new object a live material, and an edit of it for ALL_USERS on that object raised
  SHARING_UNKNOWN, an internal error that blocked the request; it is a correctable refusal naming the remedy.
* H3: staged copies held the suffixed names ("Material.001") the live file leaves free: a material asked for by
  that name was refused NAME_TAKEN, a duplicate's mesh became "Cube.002" where Blender gives "Cube.001", and a
  data-path key named its Action slot after the copy. No private name may reach the file.
* H4: the commit result says that replaced data left without users is normal until the file is saved, and every
  datablock left without users after a commit existed before it (staging leaves none of its own).

Disposable headless instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call. Writes stage-id-hazards.json in ANGELO_QA_DIR.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-stage-id-hazards-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "results": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]
COLLECTIONS = ("objects", "meshes", "materials", "node_groups", "worlds", "actions", "cameras", "lights", "curves",
               "shape_keys", "particles", "images", "collections", "scenes")


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def objects():
    return window.scene.objects


def handle(name):
    return queries.object_handle(objects()[name])


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def run(label, operations, writes=(), reads=(), checks=(), scene=False):
    """One program through staging and the QA commit operator; the job's final result (handles stay valid)."""
    entry = lambda name: {"handle": handle(name), "revision": queries.object_revision(window, objects()[name])}
    write_set = [entry(name) for name in writes]
    if scene:
        settings = scene_settings.execute(window, {})
        write_set.append({"handle": settings["handle"], "revision": settings["revision"]})
    arguments = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [entry(name) for name in reads],
                                                       "write_set": write_set, "operations": operations,
                                                       "expected_checks": list(checks)})
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(400):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = dict(job.result or staged)
    report["results"][label] = {key: value for key, value in result.items() if key not in ("delta", "live_revisions")}
    queries.invalidate()
    return result


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def committed(result):
    return result.get("status") == "committed" and not result.get("error")


def private_names():
    return sorted("%s:%s" % (collection, block.name) for collection in COLLECTIONS
                  for block in getattr(bpy.data, collection) if "__angelo_" in block.name)


def uids():
    return {block.session_uid for collection in COLLECTIONS for block in getattr(bpy.data, collection)}


def orphans():
    return [block for collection in COLLECTIONS[1:] for block in getattr(bpy.data, collection)
            if block.users == 0 and not block.use_fake_user]


def h1_one_copy_per_live_material():
    cube = objects()["Cube"]
    twice = bpy.data.materials.new("QA Twice")
    cube.data.materials.clear()
    cube.data.materials.append(twice)
    cube.data.materials.append(twice)
    for polygon in cube.data.polygons:
        polygon.material_index = polygon.index % 2
    baseline("two slots")
    result = run("h1", [op("transform", handle("Cube"), location=[0, 0, 0.5])], writes=["Cube"],
                 checks=[{"kind": "material_assignment", "target": handle("Cube"), "material": "QA Twice",
                          "all_faces": True}])
    check("h1_all_faces_of_a_material_in_two_slots", committed(result))
    check("h1_both_slots_keep_the_live_material", [slot.material for slot in objects()["Cube"].material_slots]
          == [twice, twice])


def h2_all_users_edit_of_a_reused_material():
    stone = bpy.data.materials.new("QA Stone")
    objects()["Cube"].data.materials[0] = stone
    baseline("stone")
    result = run("h2", [op("create_primitive", name="QA Plinth", alias="plinth", primitive="CUBE", location=[3, 0, 0]),
                        op("material_assign", "$plinth", material="QA Stone", source=handle("Cube")),
                        op("material_principled", "$plinth", name="QA Stone", shared="ALL_USERS",
                           inputs={"Base Color": [0.8, 0.1, 0.1, 1.0]})], reads=["Cube"])
    report["h2_error"] = result.get("error")
    check("h2_is_a_correctable_refusal", result.get("status") == "failed" and result.get("error_code") == "VALIDATION"
          and not result.get("partial_changes_possible") and "QA Plinth" not in objects() and no_stages())
    remedy = run("h2_remedy", [op("material_principled", handle("Cube"), name="QA Stone", shared="ALL_USERS",
                                  inputs={"Base Color": [0.8, 0.1, 0.1, 1.0]})], writes=["Cube"])
    check("h2_the_remedy_commits", committed(remedy))


def h3_staged_copies_never_hold_artist_names():
    objects()["Cube"].data.materials[0] = bpy.data.materials.new("QA Wood")  # "QA Wood.001" is free in the file.
    baseline("wood")
    before = uids()
    taken = run("h3_free_suffix", [op("transform", handle("Cube"), location=[0, 0, 1]),
                                   op("create_primitive", name="QA Plaque", alias="plaque", primitive="CUBE",
                                      location=[0, 4, 0]),
                                   op("material_principled", "$plaque", name="QA Wood.001",
                                      inputs={"Base Color": [0.2, 0.2, 0.2, 1.0]})], writes=["Cube"])
    check("h3_a_name_only_a_staged_copy_held_is_free", committed(taken)
          and objects()["QA Plaque"].material_slots[0].material.name == "QA Wood.001")
    duplicated = run("h3_duplicate", [op("transform", handle("Cube"), location=[0, 0, 1.5]),
                                      op("object_duplicate", source=handle("Cube"), alias="dup", name="QA Cube Copy",
                                         location=[0, -4, 0])], writes=["Cube"])
    report["duplicate_mesh"] = objects()["QA Cube Copy"].data.name if "QA Cube Copy" in objects() else None
    others = {mesh.name for mesh in bpy.data.meshes if "QA Cube Copy" not in objects()
              or mesh != objects()["QA Cube Copy"].data}
    expected = next("Cube.%03d" % number for number in range(1, 999) if "Cube.%03d" % number not in others)
    check("h3_a_duplicate_takes_the_name_blender_gives", committed(duplicated)
          and objects()["QA Cube Copy"].data.name == expected)
    keyed = run("h3_data_key", [op("property_keyframe", handle("Camera"), values={"data.lens": 70.0}, frame=1)],
                writes=["Camera"])
    animation = objects()["Camera"].data.animation_data
    slot = animation.action_slot if animation is not None else None
    report["camera_slot"] = slot.identifier if slot is not None else None
    check("h3_a_data_key_slot_is_named_after_the_live_data", committed(keyed) and slot is not None
          and slot.identifier == "CA" + objects()["Camera"].data.name)
    twin = objects()["Cube"].copy()
    twin.name = "QA Twin"
    window.scene.collection.objects.link(twin)
    baseline("twin")
    shared = run("h3_shared_data", [op("mesh_inset", handle("QA Twin"), face_indices=[1])], writes=["QA Twin"])
    check("h3_a_kept_copy_of_shared_data_gets_a_plain_name", committed(shared)
          and objects()["QA Twin"].data.name.startswith("Cube.") and objects()["Cube"].data.name == "Cube")
    report["private_names"] = private_names()
    check("h3_no_private_name_reaches_the_file", not private_names())
    report["new_orphans"] = sorted(block.name for block in orphans() if block.session_uid not in before)


def h4_leftovers():
    before = uids()
    result = run("h4_world", [op("world_configure", scene_settings.execute(window, {})["handle"], strength=0.4)],
                 scene=True)
    note = result.get("orphan_data") or {}
    report["orphan_note"] = note
    check("h4_world_commits", committed(result) and window.scene.world.name == "World")
    check("h4_the_result_says_leftover_data_is_normal", note.get("names") == ["World.001"]
          and "saved" in note.get("note", ""))
    created = [block.name for block in orphans() if block.session_uid not in before]
    report["h4_created_orphans"] = created
    check("h4_staging_leaves_no_datablock_of_its_own", not created)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    bpy.ops.ed.undo_push(message="hazards baseline")
    h1_one_copy_per_live_material()
    h2_all_users_edit_of_a_reused_material()
    h3_staged_copies_never_hold_artist_names()
    h4_leftovers()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "stage-id-hazards.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_STAGE_ID_HAZARDS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
