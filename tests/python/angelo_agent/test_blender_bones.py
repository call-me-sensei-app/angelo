# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""bone_set through real staged programs: rest edits, Bone and PoseBone settings, the pose commit and Undo.

An existing armature is edited in staged Edit mode (tail, roll, parent), its
Bone settings (including B-Bone segments, so the rig stays bindable) and pose
(a quaternion bone rotated by Euler angles, a rotation-mode change, a custom
shape object) are committed in one Undo step, read back and redone. A preview
is accepted; a manual edit after observation or during a preview makes the
plan stale; an animated channel, a parent cycle and another bone's path are
refused; an injected commit failure restores the live pose exactly. Every poll
suspends and resumes the stage (``spent_budget``). Run only in a disposable
instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-bones-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_validation  # noqa: E402
from angelo_agent import program_commit  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
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


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), reads=(), mode="APPLY"):
    """A model-style program, normalized by validate_call; the job derives its host checks."""
    return validate_call("angelo_scene_program", {"mode": mode, "read_set": [binding(obj) for obj in reads],
                                                  "write_set": [binding(obj) for obj in writes],
                                                  "operations": operations})


def bone_set(obj, **parameters):
    return {"kind": "bone_set", "target": handle(obj), "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    with spent_budget():
        return job, settle(job)


def derived(job, kind):
    """The job's host-derived obligations of ``kind`` (5073aa55350: never in the public arguments)."""
    return [item for item, flag in zip(job.obligations.checks(), job.obligations.derived_flags)
            if flag and item["kind"] == kind]


def passed(job):
    rows = job.result.get("checks") or []
    return bool(rows) and all(row.get("passed") for row in rows)


def failed_with(result, text):
    return result.get("status") == "failed" and text in (result.get("error") or "")


def rig():
    return window.scene.objects["QA Rig"]


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def near(a, b, tolerance=1e-5):
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def build_rig():
    data = bpy.data.armatures.new("QA Rig Data")
    obj = bpy.data.objects.new("QA Rig", data)
    window.scene.collection.objects.link(obj)
    shape = bpy.data.objects.new("QA Shape", bpy.data.meshes.new("QA Shape"))
    window.scene.collection.objects.link(shape)
    window.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bones = {}
    for name, head, tail, parent in (("Root", (0, 0, 0), (0, 0, 1), None), ("Upper", (0, 0, 1), (0, 0, 2), "Root"),
                                     ("Lower", (0, 0, 2), (0, 0, 3), "Upper"), ("Tip", (1, 0, 0), (1, 0, 1), None)):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent, bone.use_connect = bones[parent], True
        bones[name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    window.view_layer.update()
    queries.invalidate()
    return obj, shape


def snapshot(obj):
    """Rest geometry, pose channels and the settings the cases write, rounded."""
    def rounded(values):
        return [round(float(value), 6) for value in values]
    return {"bones": {bone.name: [rounded(bone.head_local), rounded(bone.tail_local), bone.parent.name if bone.parent else None,
                                  bone.use_connect, bone.use_deform, bone.bbone_segments, round(bone.envelope_distance, 6),
                                  rounded(bone.matrix_local.to_3x3().col[2])] for bone in obj.data.bones},
            "pose": {bone.name: [bone.rotation_mode, rounded(bone.location), rounded(bone.rotation_quaternion),
                                 rounded(bone.rotation_euler), rounded(bone.scale), bone.custom_shape.name if bone.custom_shape else None,
                                 list(bone.lock_location), round(bone.ik_stretch, 6)] for bone in obj.pose.bones}}


def commit_undo_redo():
    obj, shape = build_rig()
    bpy.ops.ed.undo_push(message="bones QA baseline")
    queries.invalidate()
    before, fingerprint = snapshot(rig()), native._fingerprint(rig())
    arguments = program([
        bone_set(obj, bone="Lower", tail=[0, .5, 3], roll=.3),
        bone_set(obj, bone="Upper", settings={"use_deform": False, "bbone_segments": 4, "envelope_distance": .5}),
        bone_set(obj, bone="Lower", rotation=[.5, 0, 0], location=[0, .1, 0], scale=[1, 1.2, 1]),
        bone_set(obj, bone="Root", rotation_mode="XYZ", rotation=[0, 0, .25],
                 pose_settings={"custom_shape": {"object": handle(shape)}, "lock_location": [True, False, False],
                                "ik_stretch": .3}),
        bone_set(obj, bone="Tip", parent="Root")], writes=[obj], reads=[shape])
    job, staged = run(arguments)
    check("derived_bone_checks", arguments["expected_checks"] == [] and len(derived(job, "bone")) == 4)
    check("bone_program_ready_without_live_edit", staged.get("ready_to_commit") and snapshot(rig()) == before
          and native._fingerprint(rig()) == fingerprint and no_stages())
    rows = [row["changes"].get("bones") for row in staged.get("delta", {}).get("details", []) if row["handle"] == handle(obj)]
    report["delta_bones"] = rows
    check("delta_lists_each_changed_bone_once", rows and rows[0]["changed"] == 4 and
          sorted(row["name"] for row in rows[0]["bones"]) == ["Lower", "Root", "Tip", "Upper"])
    check("bone_program_commits", commit(job) and passed(job)
          and len(job.result["checks"]) == len(job.obligations.checks()))
    live = rig()
    lower, pose = live.data.bones["Lower"], live.pose.bones
    check("rest_edit_live", near(lower.tail_local, [0, .5, 3]) and near(lower.head_local, [0, 0, 2])
          and abs(lower.AxisRollFromMatrix(lower.matrix_local.to_3x3())[1] - .3) < 1e-4
          and live.data.bones["Tip"].parent.name == "Root" and not live.data.bones["Tip"].use_connect)
    check("bone_settings_live", not live.data.bones["Upper"].use_deform and live.data.bones["Upper"].bbone_segments == 4
          and abs(live.data.bones["Upper"].envelope_distance - .5) < 1e-6)
    quaternion = pose["Lower"].rotation_quaternion
    check("quaternion_bone_keeps_its_mode", pose["Lower"].rotation_mode == "QUATERNION" and
          abs(quaternion.to_euler("XYZ")[0] - .5) < 1e-5 and near(pose["Lower"].location, [0, .1, 0])
          and near(pose["Lower"].scale, [1, 1.2, 1]))
    check("pose_settings_live", pose["Root"].rotation_mode == "XYZ" and near(pose["Root"].rotation_euler, [0, 0, .25])
          and pose["Root"].custom_shape == window.scene.objects["QA Shape"] and list(pose["Root"].lock_location) ==
          [True, False, False] and abs(pose["Root"].ik_stretch - .3) < 1e-6)
    revision = queries.object_revision(window, live)
    check("bbone_rig_keeps_an_exact_revision", isinstance(revision, str) and len(revision) == 64)
    live_rows = {row["handle"]: row for row in job.result.get("live_revisions", {}).get("objects", [])}
    check("post_commit_revision_binds_next_program", live_rows.get(handle(live), {}).get("object_revision") == revision)
    verify = scene_validation.execute(window, "angelo_scene_verify", {"read_set": [binding(live), binding(window.scene.objects["QA Shape"])],
                                      "checks": [{"kind": "bone", "target": handle(live), "bone": "Root", "rotation": [0, 0, .25],
                                                  "pose_settings": {"custom_shape": {"object": handle(window.scene.objects["QA Shape"])}}}]})
    check("live_bone_check_passes", verify["passed"])
    after = snapshot(live)
    report["after_commit"] = after
    undo()
    check("bone_program_one_undo", snapshot(rig()) == before and no_stages())
    redo()
    check("bone_program_one_redo", snapshot(rig()) == after and no_stages())
    undo()


def preview_and_stale():
    obj = rig()
    bpy.ops.ed.undo_push(message="bones QA preview baseline")
    queries.invalidate()
    arguments = program([bone_set(obj, bone="Upper", rotation=[0, .4, 0])], writes=[obj], mode="PREVIEW")
    job, result = run(arguments)
    check("preview_keeps_live_pose", result.get("status") == "preview" and
          tuple(rig().pose.bones["Upper"].rotation_quaternion) == (1, 0, 0, 0) and no_stages())
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("preview_accept_ready", settle(accepted).get("ready_to_commit"))
    check("preview_accept_commits", commit(accepted) and
          abs(rig().pose.bones["Upper"].rotation_quaternion.to_euler("XYZ")[1] - .4) < 1e-5)
    undo()
    check("preview_one_undo", tuple(rig().pose.bones["Upper"].rotation_quaternion) == (1, 0, 0, 0))
    # Settings the rig field did not cover: the bones field must make the observed revision stale.
    def fields():
        return queries._fingerprints.capture(window, rig(), queries._read_budget())["fields"]

    obj = rig()
    arguments = program([bone_set(obj, bone="Upper", settings={"use_deform": False})], writes=[obj])
    before = fields()
    obj.data.bones["Upper"].bbone_x = .4
    after = fields()
    check("bones_field_covers_bone_settings", before["rig"] == after["rig"] and before["bones"] != after["bones"])
    job, result = run(arguments)
    check("manual_bone_setting_makes_plan_stale", failed_with(result, "revision changed")
          and rig().data.bones["Upper"].use_deform and no_stages())
    obj.data.bones["Upper"].bbone_x = rig().data.bones["Root"].bbone_x
    obj.pose.bones["Upper"].custom_shape_wire_width = 2
    arguments = program([bone_set(obj, bone="Upper", location=[0, 0, .1])], writes=[obj])
    before = fields()
    obj.pose.bones["Upper"].custom_shape_wire_width = 3
    after = fields()
    check("bones_field_covers_pose_settings", before["rig"] == after["rig"] and before["bones"] != after["bones"])
    job, result = run(arguments)
    check("manual_pose_setting_makes_plan_stale", failed_with(result, "revision changed")
          and tuple(rig().pose.bones["Upper"].location) == (0, 0, 0) and no_stages())
    arguments = program([bone_set(rig(), bone="Upper", location=[0, 0, .2])], writes=[rig()], mode="PREVIEW")
    job, result = run(arguments)
    rig().pose.bones["Upper"].location = (.5, 0, 0)  # A manual pose while the preview waits.
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    outcome = settle(accepted)
    report["stale_preview_error"] = outcome.get("error")
    check("manual_pose_during_preview_is_stale", failed_with(outcome, "changed") and
          tuple(rig().pose.bones["Upper"].location) == (.5, 0, 0) and no_stages())
    rig().pose.bones["Upper"].location = (0, 0, 0)


def refusals():
    obj = rig()
    curve_owner = obj.pose.bones["Tip"]
    curve_owner.keyframe_insert("location", frame=1)
    queries.invalidate()
    job, result = run(program([bone_set(obj, bone="Tip", location=[0, 1, 0])], writes=[obj]))
    check("animated_channel_refused", failed_with(result, "BONE_ANIMATED") and tuple(rig().pose.bones["Tip"].location) == (0, 0, 0)
          and no_stages())
    obj.animation_data_clear()
    for name, parameters, text in (
            ("unknown_bone_refused", {"bone": "Missing", "roll": .1}, "exact name"),
            ("parent_cycle_refused", {"bone": "Root", "parent": "Lower"}, "own children"),
            ("degenerate_bone_refused", {"bone": "Tip", "head": [1, 0, 1]}, "degenerate")):
        before = native._fingerprint(rig())
        job, result = run(program([bone_set(rig(), **parameters)], writes=[rig()]))
        report.setdefault("refusals", {})[name] = result.get("error")
        check(name, failed_with(result, text) and native._fingerprint(rig()) == before and no_stages())
    try:
        validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": [binding(obj)], "operations": [
            bone_set(obj, bone="Root", settings={"bbone_custom_handle_start.use_deform": True})]})
        refused = False
    except ValueError as error:
        refused = "another bone" in str(error)
    check("other_bone_path_refused", refused)


def rollback():
    obj = rig()
    obj.pose.bones["Lower"].rotation_mode = "AXIS_ANGLE"
    obj.pose.bones["Lower"].rotation_axis_angle = (.3, 0, 1, 0)
    window.view_layer.update()
    queries.invalidate()
    before, fingerprint = snapshot(rig()), native._fingerprint(rig())
    job, result = run(program([bone_set(obj, bone="Lower", rotation_mode="QUATERNION", rotation=[.2, .1, 0]),
                               bone_set(obj, bone="Upper", tail=[0, .2, 2])], writes=[obj]))

    def fail(*args, **kwargs):
        raise RuntimeError("injected after the pose transfer")

    with patch.object(program_commit, "_restore_source_identities", side_effect=fail):
        outcome = job.commit()
    check("injected_failure_rolls_back_pose_exactly", not outcome["success"] and not outcome["partial_changes_possible"]
          and snapshot(rig()) == before and native._fingerprint(rig()) == fingerprint and no_stages())
    report["rollback_error"] = outcome.get("error")


def revision_cost():
    """A 500-bone rig: the bones field, one observed revision and a one-bone pose commit, timed (reported only)."""
    data = bpy.data.armatures.new("QA Large Rig")
    obj = bpy.data.objects.new("QA Large Rig", data)
    window.scene.collection.objects.link(obj)
    window.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    for index in range(500):
        bone = data.edit_bones.new("Bone.%03d" % index)
        bone.head, bone.tail = (index * .01, 0, 0), (index * .01, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    window.view_layer.update()
    from angelo_agent import bone_state
    from angelo_agent.scene_read_values import ReadBudget
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        bone_state.armature_state(obj, ReadBudget(2000000, 12000, 10), lambda value: value.name)
        timings.append((time.perf_counter() - started) * 1000)
    queries.invalidate()
    started = time.perf_counter()
    revision = queries.object_revision(window, obj)
    report["revision_cost_500_bones_ms"] = {"bones_field": sorted(timings), "object_revision": (time.perf_counter() - started) * 1000}
    check("large_rig_revision_exact", isinstance(revision, str) and len(revision) == 64)
    bpy.ops.ed.undo_push(message="bones QA large rig")
    queries.invalidate()
    obj = window.scene.objects["QA Large Rig"]
    job, staged = run(program([bone_set(obj, bone="Bone.250", rotation=[0, 0, .3])], writes=[obj]))
    started = time.perf_counter()
    check("large_rig_pose_commits", commit(job) and passed(job))
    report["revision_cost_500_bones_ms"]["pose_commit"] = (time.perf_counter() - started) * 1000
    undo()
    check("large_rig_pose_one_undo", tuple(window.scene.objects["QA Large Rig"].pose.bones["Bone.250"].rotation_quaternion)
          == (1, 0, 0, 0))
    bpy.data.objects.remove(window.scene.objects["QA Large Rig"])
    queries.invalidate()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    commit_undo_redo()
    preview_and_stale()
    refusals()
    rollback()
    revision_cost()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "bones.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_BONES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
