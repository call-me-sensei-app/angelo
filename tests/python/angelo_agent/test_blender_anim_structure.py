# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actions, NLA and timeline markers through real staged programs.

action_set (new with stash, assign an existing Action by ID, explicit
single-user isolation, unlink, rename, slots), nla_edit (tracks and strips:
add, set, move, remove; live strips play the live Actions) and marker_edit
(add, rename, move, bind a declared camera, remove), each committed in one
Undo step, Redo replaying it; every refusal (orphaned Action, stale assigned
Action, ambiguous track, unsupported strips, overlaps, undeclared marker
cameras, stale scene revision) and a forced NLA rollback. Run only in a
disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-anim-structure-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent import anim_commit  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def obj(name="Cube"):
    return window.scene.objects[name]


def handle(item):
    return queries.object_handle(item)


def action_id(name):
    """The ID an object read shows for the Action named ``name`` (looked up now: Undo replaces references)."""
    return "dat:%s:ACTION:%d" % (queries.document_epoch(), bpy.data.actions[name].session_uid)


def program(operations, writes, reads=(), scene=False, mode="APPLY"):
    entries = lambda items: [{"handle": handle(item), "revision": queries.object_revision(window, item)} for item in items]
    write_set = entries(writes)
    if scene:
        settings = scene_settings.execute(window, {})
        write_set.append({"handle": settings["handle"], "revision": settings["revision"]})
    return validate_call("angelo_scene_program", {"mode": mode, "read_set": entries(reads), "operations": operations,
                                                  "write_set": write_set})


def op(kind, target, **parameters):
    return {"kind": kind, "target": target if isinstance(target, str) else handle(target), "parameters": parameters}


def scene_handle():
    return scene_settings.execute(window, {})["handle"]


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


run_program = run  # The refusal table's previews, beside its local names.


def passed(job):
    return all(row.get("passed") for row in job.result.get("checks") or ())


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def state():
    """Every object's Action, slot and NLA stack, and the scene's markers, by name."""
    rows = {}
    for item in window.scene.objects:
        animation = item.animation_data
        if animation is None:
            continue
        rows[item.name] = [animation.action.name if animation.action else None,
                           animation.action_slot.identifier if animation.action_slot else None,
                           [[track.name, track.mute, track.is_solo, track.lock,
                             [[strip.name, strip.action.name if strip.action else None, round(strip.frame_start, 3),
                               round(strip.frame_end, 3), strip.blend_type, strip.repeat, strip.mute]
                              for strip in track.strips]] for track in animation.nla_tracks]]
    rows["markers"] = [[marker.name, marker.frame, marker.camera.name if marker.camera else None]
                       for marker in window.scene.timeline_markers]
    rows["actions"] = sorted(action.name for action in bpy.data.actions if action.users)
    return rows


def committed(name, operations, writes, reads=(), scene=False, expect=None):
    before = state()
    job, staged = run(program(operations, writes, reads, scene))
    check(name + "_ready_without_live_edit", staged.get("ready_to_commit") and state() == before and no_stages())
    check(name + "_commits", commit(job) and passed(job))
    after = state()
    if expect is not None:
        check(name + "_live_result", expect())
    undo()
    check(name + "_one_undo", state() == before and no_stages())
    redo()
    check(name + "_one_redo", state() == after)
    undo()
    return job


def refused(name, operations, writes, code, reads=(), scene=False):
    try:
        job, result = run(program(operations, writes, reads, scene))
        message = result.get("error") or ""
        ok = result.get("status") == "failed" and code in message
        if not ok:
            report.setdefault("unexpected", {})[name] = [json.dumps(result, default=str)[:300], state()]
            job.cancel()
    except ValueError as error:
        message, ok = str(error), code in str(error)
    report.setdefault("refusals", {})[name] = message[:300]
    check(name, ok and no_stages())


def setup():
    cube = obj()
    cube.animation_data_clear()
    for frame, x in ((1, 0), (20, 3)):
        cube.location = (x, 0, 0)
        cube.keyframe_insert("location", frame=frame)
    walk = bpy.data.actions.new("Walk")
    walk.use_fake_user = True  # A library Action: kept by its fake user, used by no object yet.
    layer = walk.layers.new("Layer")
    slot = walk.slots.new(id_type="OBJECT", name="Cube")
    curve = layer.strips.new(type="KEYFRAME").channelbag(slot, ensure=True).fcurves.new("location", index=2)
    curve.keyframe_points.insert(1, 0)
    curve.keyframe_points.insert(10, 1)
    walk.slots.new(id_type="MESH", name="Mesh")  # "MEMesh": a slot for mesh data, not an object.
    walk.slots.new(id_type="OBJECT", name="Mesh")  # "OBMesh": two slots now display as "Mesh".
    window.scene.frame_set(1)
    baseline("anim structure baseline")


def actions():
    original = obj().animation_data.action.name
    refused("new_without_stash_refused", [op("action_set", obj(), mode="NEW", name="Idle")], [obj()], "ACTION_ORPHANED")
    committed("new_with_stash", [op("action_set", obj(), mode="NEW", name="Idle", stash_previous=True)], [obj()],
              expect=lambda: obj().animation_data.action.name == "Idle"
              and [track.name for track in obj().animation_data.nla_tracks] == ["[Action Stash]"]
              and obj().animation_data.nla_tracks[0].mute
              and obj().animation_data.nla_tracks[0].strips[0].action.name == original)
    committed("assign_existing_by_id", [op("action_set", obj(), mode="ASSIGN", action=action_id("Walk"),
                                           stash_previous=True)], [obj()],
              expect=lambda: obj().animation_data.action == bpy.data.actions["Walk"]
              and obj().animation_data.action_slot.identifier == "OBCube"
              and bpy.data.actions["Walk"].name == "Walk")
    refused("assigned_then_edited_refused", [op("action_set", obj(), mode="ASSIGN", action=action_id("Walk"),
                                                stash_previous=True),
                                             op("keyframe_edit", obj(), operation="OFFSET", offset=2)], [obj()],
            "ACTION_ASSIGNED")
    # An assigned Action edited after staging (a preview held open): the accept is refused.
    preview = program([op("action_set", obj(), mode="ASSIGN", action=action_id("Walk"), stash_previous=True)], [obj()],
                      mode="PREVIEW")
    job, retained = run(preview)
    bpy.data.actions["Walk"].layers[0].strips[0].channelbags[0].fcurves[0].keyframe_points[0].co.y = 5
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": retained["preview_id"]})
    result = settle(accepted)
    if result.get("ready_to_commit"):
        commit(accepted)
        result = accepted.result
    check("assigned_action_changed_refused", result.get("status") == "failed" and obj().animation_data.action.name == original)
    bpy.data.actions["Walk"].layers[0].strips[0].channelbags[0].fcurves[0].keyframe_points[0].co.y = 0
    queries.invalidate()
    committed("unlink_with_stash", [op("action_set", obj(), mode="UNLINK", stash_previous=True)], [obj()],
              expect=lambda: obj().animation_data.action is None and len(obj().animation_data.nla_tracks) == 1)
    committed("rename_action", [op("action_set", obj(), mode="RENAME", name="CubeMotion")], [obj()],
              expect=lambda: obj().animation_data.action.name == "CubeMotion")
    refused("rename_taken_refused", [op("action_set", obj(), mode="RENAME", name="Walk")], [obj()], "NAME_TAKEN")
    committed("new_slot", [op("action_set", obj(), mode="SLOT", new_slot="Alternate")], [obj()],
              expect=lambda: obj().animation_data.action_slot.identifier == "OBAlternate")
    committed("unassign_slot", [op("action_set", obj(), mode="SLOT")], [obj()],
              expect=lambda: obj().animation_data.action_slot is None)


def shared():
    other = bpy.data.objects.new("Other", bpy.data.meshes.new("Other"))
    window.scene.collection.objects.link(other)
    other.animation_data_create().action = obj().animation_data.action
    window.view_layer.update()
    baseline("anim structure shared")
    refused("slot_on_shared_refused", [op("action_set", obj(), mode="SLOT", new_slot="Mine")], [obj()], "ACTION_SHARED")
    job = committed("single_user_isolates", [op("action_set", obj(), mode="SINGLE_USER")], [obj()],
                    expect=lambda: obj().animation_data.action != obj("Other").animation_data.action)
    report["isolated"] = job.result.get("isolated_shared_data")
    check("single_user_reported", "SINGLE_USER" in json.dumps(job.result.get("isolated_shared_data")))
    committed("stash_shared_keeps_live", [op("action_set", obj(), mode="NEW", name="Fresh", stash_previous=True)],
              [obj()], expect=lambda: obj().animation_data.nla_tracks[0].strips[0].action
              == obj("Other").animation_data.action and obj().animation_data.action.name == "Fresh")
    refused("single_user_unshared_refused", [op("action_set", obj("Other"), mode="SINGLE_USER"),
                                             op("action_set", obj("Other"), mode="SINGLE_USER")], [obj("Other")],
            "ACTION_NOT_SHARED")
    bpy.data.objects.remove(obj("Other"))
    window.view_layer.update()
    baseline("anim structure unshared")


def nla():
    committed("tracks_and_strips", [
        op("nla_edit", obj(), operation="TRACK_ADD", track="Base"),
        op("nla_edit", obj(), operation="STRIP_ADD", track="Base", action=action_id("Walk"), frame=30, name="Walk A",
           settings={"repeat": 2.0, "blend_type": "ADD"}),
        op("nla_edit", obj(), operation="TRACK_ADD", track="Layer", position="BOTTOM"),
        op("nla_edit", obj(), operation="TRACK_SET", track="Layer", mute=True, lock=True)], [obj()],
        expect=lambda: [track.name for track in obj().animation_data.nla_tracks] == ["Layer", "Base"]
        and obj().animation_data.nla_tracks["Base"].strips["Walk A"].action == bpy.data.actions["Walk"]
        and obj().animation_data.nla_tracks["Base"].strips["Walk A"].repeat == 2.0
        and obj().animation_data.nla_tracks["Base"].strips["Walk A"].frame_end == 48.0  # Walk's keys 1-10, twice.
        and obj().animation_data.nla_tracks["Layer"].mute and obj().animation_data.action is not None)
    redo()  # Keep the tracks for the edits below.
    committed("strip_edits", [
        op("nla_edit", obj(), operation="STRIP_SET", track="Base", strip="Walk A", name="Walk B",
           move_to_track="Layer", settings={"frame_start": 40.0, "influence": .5, "extrapolation": "HOLD_FORWARD"}),
        op("nla_edit", obj(), operation="TRACK_SET", track="Base", name="Top", index=0, solo=True)], [obj()],
        expect=lambda: obj().animation_data.nla_tracks["Layer"].strips["Walk B"].frame_start == 40.0
        and [track.name for track in obj().animation_data.nla_tracks] == ["Top", "Layer"]
        and obj().animation_data.nla_tracks["Top"].is_solo
        and obj().animation_data.nla_tracks["Layer"].strips["Walk B"].action == bpy.data.actions["Walk"])
    committed("remove_strip_and_track", [op("nla_edit", obj(), operation="STRIP_REMOVE", track="Base", strip="Walk A"),
                                         op("nla_edit", obj(), operation="TRACK_REMOVE", track="Layer")], [obj()],
              expect=lambda: [track.name for track in obj().animation_data.nla_tracks] == ["Base"]
              and not len(obj().animation_data.nla_tracks["Base"].strips))
    refused("strip_overlap_refused", [op("nla_edit", obj(), operation="STRIP_ADD", track="Base", action=action_id("Walk"),
                                         frame=35)], [obj()], "STRIP_OVERLAP")
    duplicate = obj().animation_data.nla_tracks.new()
    duplicate.name = "Base"
    queries.invalidate()
    refused("ambiguous_track_refused", [op("nla_edit", obj(), operation="TRACK_SET", track="Base", mute=True)], [obj()],
            "TRACK_AMBIGUOUS")
    obj().animation_data.nla_tracks.remove(obj().animation_data.nla_tracks[-1])  # The duplicate, never pushed.
    queries.invalidate()
    real = anim_commit.rebuild_nla

    def fail_after(*arguments):
        real(*arguments)
        raise RuntimeError("injected failure after the NLA rebuild")

    before = state()
    job, _ = run(program([op("nla_edit", obj(), operation="TRACK_REMOVE", track="Layer")], [obj()]))
    with patch.object(anim_commit, "rebuild_nla", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["nla_rollback"] = {"result": {key: job.result.get(key) for key in (
        "status", "error", "error_code", "scene_changed", "partial_changes_possible", "rollback_verified",
        "rollback_error", "cleanup_error")}, "before": before, "after": state(), "no_stages": no_stages()}
    check("nla_rollback_restores_stack", job.result.get("status") == "failed"
          and not job.result.get("partial_changes_possible") and job.result.get("rollback_verified") is True
          and state() == before and no_stages())


def markers():
    scene = window.scene
    camera = obj("Camera")
    target = scene_handle()
    committed("markers_add_bind_rename", [
        op("marker_edit", target, operation="ADD", name="Start", frame=1),
        op("marker_edit", target, operation="ADD", name="Hit", frame=24, camera=handle(camera)),
        op("marker_edit", target, operation="SET", marker="Start", name="Begin", frame=2)], [], reads=[camera],
        scene=True, expect=lambda: [(m.name, m.frame, m.camera) for m in window.scene.timeline_markers]
        == [("Begin", 2, None), ("Hit", 24, obj("Camera"))] and not any(m.select for m in window.scene.timeline_markers))
    redo()
    target = scene_handle()
    refused("marker_camera_undeclared_refused", [op("marker_edit", target, operation="REMOVE", marker="Begin")], [],
            "MARKER_CAMERA_UNDECLARED", scene=True)
    committed("markers_unbind_remove", [op("marker_edit", target, operation="BIND_CAMERA", marker="Hit", camera=None),
                                        op("marker_edit", target, operation="REMOVE", marker="Begin")], [],
              reads=[obj("Camera")], scene=True,
              expect=lambda: [(m.name, m.camera) for m in window.scene.timeline_markers] == [("Hit", None)])
    stale = program([op("marker_edit", scene_handle(), operation="ADD", name="Late", frame=50)], [], scene=True)
    window.scene.timeline_markers.new("Manual", frame=60)  # A manual marker after observation.
    job, result = run(stale)
    check("marker_stale_scene_refused", result.get("status") == "failed" and no_stages())
    undo()


def refusal_table():
    """Every structure refusal not reached above: Action IDs and slots, NLA names, markers, commit-time staleness."""
    empty = bpy.data.objects.new("Bare", None)
    window.scene.collection.objects.link(empty)
    baseline("anim structure refusals")
    epoch, walk_uid = queries.document_epoch(), bpy.data.actions["Walk"].session_uid
    cases = [
        ("expired_action_id_refused", "ACTION_EXPIRED", [op("action_set", obj(), mode="ASSIGN", stash_previous=True,
                                                            action="dat:%s:ACTION:%d" % ("f" * 32, walk_uid))]),
        ("missing_action_id_refused", "ACTION_NOT_FOUND", [op("action_set", obj(), mode="ASSIGN", stash_previous=True,
                                                              action="dat:%s:ACTION:999999999" % epoch)]),
        ("already_assigned_refused", "ALREADY_ASSIGNED", [op("action_set", obj(), mode="ASSIGN", action=action_id(
            obj().animation_data.action.name))]),
        ("missing_slot_refused", "SLOT_NOT_FOUND", [op("action_set", obj(), mode="ASSIGN", action=action_id("Walk"),
                                                      slot="Nope", stash_previous=True)]),
        ("mesh_slot_refused", "SLOT_TYPE", [op("action_set", obj(), mode="ASSIGN", action=action_id("Walk"),
                                               slot="MEMesh", stash_previous=True)]),
        ("ambiguous_slot_refused", "SLOT_AMBIGUOUS", [op("action_set", obj(), mode="ASSIGN", action=action_id("Walk"),
                                                         slot="Mesh", stash_previous=True)]),
        ("new_name_taken_refused", "NAME_TAKEN", [op("action_set", obj(), mode="NEW", name="Walk",
                                                     stash_previous=True)]),
        ("track_missing_refused", "TRACK_NOT_FOUND", [op("nla_edit", obj(), operation="TRACK_REMOVE", track="Nope")]),
        ("strip_missing_refused", "STRIP_NOT_FOUND", [op("nla_edit", obj(), operation="STRIP_REMOVE", track="Base",
                                                         strip="Nope")]),
        ("track_name_taken_refused", "NAME_TAKEN", [op("nla_edit", obj(), operation="TRACK_ADD", track="Base")]),
        ("strip_name_taken_refused", "NAME_TAKEN", [
            op("nla_edit", obj(), operation="STRIP_ADD", track="Base", action=action_id("Walk"), frame=200, name="Walk C"),
            op("nla_edit", obj(), operation="STRIP_SET", track="Base", strip="Walk A", name="Walk C")]),
        ("strip_value_clamped_refused", "STRIP_VALUE_NOT_RETAINED", [
            op("nla_edit", obj(), operation="STRIP_SET", track="Base", strip="Walk A", settings={"blend_in": 5000.0})]),
        ("marker_on_object_refused", "OWNER_TYPE_MISMATCH", [op("marker_edit", obj(), operation="REMOVE", marker="X")]),
    ]
    for name, code, operations in cases:
        refused(name, operations, [obj()], code)
    for name, code, operations in (
            ("no_action_refused", "NO_ACTION", [op("action_set", obj("Bare"), mode="RENAME", name="Anything")]),
            ("nothing_to_stash_refused", "NOTHING_TO_STASH", [op("action_set", obj("Bare"), mode="NEW",
                                                                 stash_previous=True)]),
            ("no_nla_refused", "NO_NLA", [op("nla_edit", obj("Bare"), operation="TRACK_REMOVE", track="Base")])):
        refused(name, operations, [obj("Bare")], code)
    target = scene_handle()
    for name, code, operations, reads in (
            ("marker_missing_refused", "MARKER_NOT_FOUND", [op("marker_edit", target, operation="REMOVE", marker="Nope")], []),
            ("marker_name_taken_refused", "NAME_TAKEN", [op("marker_edit", target, operation="ADD", name="Twin", frame=3)], []),
            ("marker_ambiguous_refused", "MARKER_AMBIGUOUS", [op("marker_edit", target, operation="SET", marker="Twin",
                                                                 frame=4)], []),
            ("marker_not_camera_refused", "NOT_A_CAMERA", [op("marker_edit", target, operation="ADD", name="Cam",
                                                              frame=5, camera=handle(obj("Light")))], [obj("Light")])):
        if name == "marker_name_taken_refused":
            window.scene.timeline_markers.new("Twin", frame=1)
            queries.invalidate()
            target = scene_handle()
            operations[0]["target"] = target
        if name == "marker_ambiguous_refused":
            window.scene.timeline_markers.new("Twin", frame=2)
            queries.invalidate()
            target = scene_handle()
            operations[0]["target"] = target
        if name == "marker_not_camera_refused":
            operations[0]["target"] = scene_handle()
            operations[0]["parameters"]["camera"] = handle(obj("Light"))  # Handles change with each new epoch.
        refused(name, operations, [], code, reads=reads, scene=True)
    library_path = OUT / "linked-actions.blend"
    bpy.data.libraries.write(str(library_path), {bpy.data.actions["Walk"]})
    with bpy.data.libraries.load(str(library_path), link=True) as (source, destination):
        destination.actions = ["Walk"]
    linked = next(action for action in bpy.data.actions if action.library is not None)
    queries.invalidate()
    refused("linked_action_refused", [op("action_set", obj(), mode="ASSIGN", stash_previous=True,
                                         action="dat:%s:ACTION:%d" % (queries.document_epoch(), linked.session_uid))],
            [obj()], "ACTION_LINKED")
    from angelo_agent import anim_markers
    with patch.object(anim_markers, "MAX_MARKERS", 2):
        refused("marker_bound_refused", [op("marker_edit", scene_handle(), operation="ADD", name="Third", frame=9)], [],
                "TOO_MANY_MARKERS", scene=True)
    # Commit-time staleness: an Action the program plays is deleted, a rename's name taken, while a preview is open.
    library = bpy.data.actions["Walk"].copy()
    library.name, library.use_fake_user = "Run", True  # Used by no object: only the program references it.
    baseline("anim structure commit staleness")
    for name, operations, interfere in (
            ("referenced_action_deleted_refused", lambda: [op("nla_edit", obj(), operation="STRIP_ADD", track="Base",
                                                              action=action_id("Run"), frame=300)],
             lambda: bpy.data.actions.remove(bpy.data.actions["Run"])),
            ("rename_taken_at_commit_refused", lambda: [op("action_set", obj(), mode="RENAME", name="Later")],
             lambda: bpy.data.actions.new("Later"))):
        job, retained = run_program(program(operations(), [obj()], mode="PREVIEW"))
        interfere()
        accepted = native.begin(window, "angelo_accept_preview", {"preview_id": retained["preview_id"]})
        result = settle(accepted)
        if result.get("ready_to_commit"):
            commit(accepted)
            result = accepted.result
        report.setdefault("refusals", {})[name] = (result.get("error") or "")[:300]
        check(name, result.get("status") == "failed" and not result.get("partial_changes_possible") and no_stages())
        undo()
        redo()  # Back to the baseline, without the interfering edit.


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    setup()
    actions()
    shared()
    nla()
    markers()
    refusal_table()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "anim-structure.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_ANIM_STRUCTURE", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
