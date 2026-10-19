# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Animation edits through real staged programs: key delete/configure, shape keys and NLA push-down.

Keys are read through the object's Action slot channelbag (Blender 5), as the
kernels write them. Programs are normalized by ``tool_specs.validate_call`` as
a model's plan is. Covers commit, derived checks, Undo, refusals and forced
rollbacks of a push-down (the active track and modifier come back) and of an
NLA edit over an active strip (never claimed exact while it differs). Run
only in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-animation-edits-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_commit, scene_queries as queries  # noqa: E402
from angelo_agent.action_animation import channelbag  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def cube():
    return window.scene.objects["Cube"]


def handle(obj):
    return queries.object_handle(obj)


def program(operations, obj):
    return validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "operations": operations,
        "write_set": [{"handle": handle(obj), "revision": queries.object_revision(window, obj)}]})


def op(kind, obj, **parameters):
    return {"kind": kind, "target": handle(obj), "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def keys(obj, index=0):
    curve = channelbag(obj).fcurves.find("location", index=index)
    return [(round(point.co.x), point.interpolation, point.easing) for point in curve.keyframe_points] if curve else []


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def animate():
    obj = cube()
    for frame, x in ((1, 0), (10, 2), (20, 4)):
        obj.location.x = x
        obj.keyframe_insert("location", frame=frame)
    obj.location.x = 0
    window.view_layer.update()
    bpy.ops.ed.undo_push(message="animation QA keys")
    queries.invalidate()


def key_edits():
    obj = cube()
    artist_action = obj.animation_data.action
    job, staged = run(program([op("keyframe_configure", obj, property="location", frames=[1, 10], interpolation="LINEAR",
                                  easing="EASE_IN"),
                               op("keyframe_delete", obj, property="location", frame=20)], obj))
    check("key_edit_ready_without_live_edit", staged.get("ready_to_commit") and len(keys(obj)) == 3)
    check("key_edit_commits", commit(job) and passed(job))
    check("key_edit_live", keys(cube()) == [(1, "LINEAR", "EASE_IN"), (10, "LINEAR", "EASE_IN")]
          and len(keys(cube(), 2)) == 2)
    original = artist_action.layers[0].strips[0].channelbags[0]
    check("artist_action_untouched", len(original.fcurves.find("location", index=0).keyframe_points) == 3
          and cube().animation_data.action != artist_action and cube().animation_data.action.name == artist_action.name.rsplit(".", 1)[0])
    undo()
    check("key_edit_one_undo", keys(cube()) == [(1, "BEZIER", "AUTO"), (10, "BEZIER", "AUTO"), (20, "BEZIER", "AUTO")]
          and no_stages())
    job, result = run(program([op("keyframe_delete", cube(), property="location", frame=15)], cube()))
    check("missing_key_refused", result.get("status") == "failed" and "KEY_NOT_FOUND" in result["error"] and no_stages())


def shape_keys():
    bpy.ops.ed.undo_push(message="animation QA shape keys")
    queries.invalidate()
    obj = cube()
    job, _ = run(program([op("shape_key_add", obj, name="Smile", value=.3)], obj))
    check("shape_key_add_commits", commit(job) and passed(job))
    blocks = cube().data.shape_keys.key_blocks
    check("shape_key_live", [block.name for block in blocks] == ["Basis", "Smile"] and abs(blocks["Smile"].value - .3) < 1e-6)
    queries.invalidate()
    with spent_budget():  # Every step yields, so the stage is suspended and resumed with its shape keys.
        job, _ = run(program([op("shape_key_value", cube(), name="Smile", value=.8, frame=5)], cube()))
    check("shape_key_keyframe_commits", commit(job) and passed(job))
    key = cube().data.shape_keys
    check("shape_key_keyed_live", key.animation_data is not None and key.animation_data.action is not None
          and channelbag(key).fcurves.find('key_blocks["Smile"].value') is not None)
    job, result = run(program([op("shape_key_add", cube(), name="Smile")], cube()))
    check("duplicate_shape_key_refused", result.get("status") == "failed" and "NAME_TAKEN" in result["error"])
    undo()
    undo()
    check("shape_keys_two_undos", cube().data.shape_keys is None and no_stages())


def push_down():
    bpy.ops.ed.undo_push(message="animation QA nla")
    queries.invalidate()
    obj = cube()
    job, _ = run(program([op("nla_push_down", obj, track="QA Walk")], obj))
    check("push_down_commits", commit(job) and passed(job))
    animation = cube().animation_data
    check("push_down_live", animation.action is None and [track.name for track in animation.nla_tracks] == ["QA Walk"]
          and len(animation.nla_tracks[0].strips) == 1 and animation.nla_tracks[0].strips[0].action is not None)
    undo()
    check("push_down_one_undo", cube().animation_data.action is not None and not len(cube().animation_data.nla_tracks))
    real = program_commit._append_tracks

    def fail_after(original, staged, journal):
        real(original, staged, journal)
        raise RuntimeError("injected failure after the push-down")

    job, _ = run(program([op("nla_push_down", cube())], cube()))
    with patch.object(program_commit, "_append_tracks", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("push_down_rollback", job.result.get("status") == "failed" and not job.result.get("partial_changes_possible")
          and cube().animation_data.action is not None and not len(cube().animation_data.nla_tracks) and no_stages())


def flags():
    """The NLA stack with its active track and strip flags, the modifier stack with its active one, the location."""
    obj = cube()
    animation = obj.animation_data
    return {"action": animation.action.name if animation and animation.action else None,
            "tracks": [[track.name, track.active, track.mute, [[strip.name, strip.active, strip.action.name if strip.action
                        else None, round(strip.frame_start, 3), round(strip.frame_end, 3)] for strip in track.strips]]
                       for track in (animation.nla_tracks if animation else ())],
            "modifiers": [[item.name, item.type, item.is_active] for item in obj.modifiers],
            "location": [round(value, 4) for value in obj.location]}


def active_flags_setup():
    """Blender's own push-down (an active track and strip), a second Action, and an active modifier not the last."""
    obj = cube()
    obj.animation_data_clear()
    for frame, x in ((1, 0), (20, 3)):
        obj.location = (x, 0, 0)
        obj.keyframe_insert("location", frame=frame)
    area = window.screen.areas[0]
    kind, area.type = area.type, "DOPESHEET_EDITOR"
    area.spaces.active.ui_mode = "ACTION"
    region = next(item for item in area.regions if item.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region, active_object=obj, object=obj):
        pushed = bpy.ops.action.push_down()
    area.type = kind
    for frame, z in ((1, 0), (10, 2)):
        obj.location = (0, 0, z)
        obj.keyframe_insert("location", frame=frame)
    for name, modifier in (("A", "SUBSURF"), ("B", "BEVEL"), ("C", "WIREFRAME")):
        obj.modifiers.new(name, modifier)
    obj.modifiers.active = obj.modifiers["A"]
    window.scene.frame_set(window.scene.frame_current)  # Evaluated: animated values are what the scene shows.
    bpy.ops.ed.undo_push(message="animation QA active flags")
    queries.invalidate()
    return "FINISHED" in pushed


def failing_commit(operations):
    """Commit ``operations`` with a failure injected after every live transfer; the job's result."""
    real = program_commit._restore_source_identities

    def fail_after(*arguments, **keywords):
        real(*arguments, **keywords)
        raise RuntimeError("injected failure after every live transfer")

    job, _ = run(program(operations, cube()))
    with patch.object(program_commit, "_restore_source_identities", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    queries.invalidate()
    return job.result


def active_flags_rollback():
    """A failed commit puts back the active track and modifier; an exactness claim always matches the scene."""
    check("active_flags_setup", active_flags_setup())
    before = flags()
    check("active_flags_baseline", before["tracks"][0][1] and before["modifiers"][0][2]
          and any(strip[1] for strip in before["tracks"][0][3]))
    result = failing_commit([op("nla_push_down", cube(), track="Pushed")])
    report["active_push_down_rollback"] = {"result": {key: result.get(key) for key in (
        "status", "rollback_verified", "partial_changes_possible", "rollback_error")}, "before": before, "after": flags()}
    check("push_down_rollback_restores_active_track_and_modifier", result.get("status") == "failed"
          and result.get("rollback_verified") is True and not result.get("partial_changes_possible")
          and flags() == before and no_stages())
    # A rebuilt NLA stack cannot mark a strip active again (read-only from Python): never claimed exact.
    track = cube().animation_data.nla_tracks[0].name
    result = failing_commit([op("nla_edit", cube(), operation="TRACK_SET", track=track, mute=True)])
    report["active_strip_rollback"] = {"result": {key: result.get(key) for key in (
        "status", "rollback_verified", "partial_changes_possible", "rollback_error")}, "before": before, "after": flags()}
    exact = flags() == before
    check("strip_rollback_never_claimed_exact_while_different", result.get("status") == "failed" and no_stages()
          and (exact or (result.get("rollback_verified") is not True and result.get("partial_changes_possible") is True)))


def property_keys():
    bpy.ops.ed.undo_push(message="animation QA property keys")
    queries.invalidate()
    obj = cube()
    obj.animation_data_clear()
    queries.invalidate()
    job, staged = run(program([op("property_keyframe", obj, values={"hide_render": True, "pass_index": 3}, frame=5,
                                  interpolation="CONSTANT")], obj))
    check("property_keys_ready_without_live_edit", staged.get("ready_to_commit") and cube().animation_data is None)
    check("property_keys_commit", commit(job) and passed(job))
    bag = channelbag(cube())
    curve = bag.fcurves.find("pass_index") if bag else None
    check("property_keys_live", curve is not None and bag.fcurves.find("hide_render") is not None
          and [(round(p.co.x), p.interpolation) for p in curve.keyframe_points] == [(5, "CONSTANT")]
          and cube().animation_data.action.name.startswith("CubeAction"))  # An orphan CubeAction is still in Main.
    undo()
    camera = window.scene.objects["Camera"]
    camera.keyframe_insert("location", frame=1)  # Blender keys the data into this Action's second slot.
    queries.invalidate()
    actions_before = len(bpy.data.actions)
    job, _ = run(program([op("property_keyframe", camera, values={"data.dof.focus_distance": 4.0}, frame=10)], camera))
    check("data_keys_commit", commit(job) and passed(job))
    camera = window.scene.objects["Camera"]
    data_action = camera.data.animation_data.action if camera.data.animation_data else None
    check("data_keys_share_the_object_action", data_action is not None and data_action == camera.animation_data.action
          and channelbag(camera.data).fcurves.find("dof.focus_distance") is not None
          and camera.data.name == "Camera" and data_action.name == "CameraAction"
          and len(bpy.data.actions) <= actions_before + 1)
    undo()
    try:
        program([op("property_keyframe", cube(), values={"display_type": "WIRE"}, frame=1)], cube())
        refused = False
    except ValueError as error:
        refused = "NOT_ANIMATABLE" in str(error)
    check("enum_setting_not_keyable", refused)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    animate()
    key_edits()
    shape_keys()
    push_down()
    property_keys()
    active_flags_rollback()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "animation-edits.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_ANIMATION_EDITS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
