# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Constraint types added for native coverage, through real staged programs and Undo.

Limit Rotation, Limit Scale, Damped Track, Copy Transforms and Child Of (with
Set Inverse keeping the owner in place), committed and read back on the live
object, with exact object revisions afterwards. Run only in a disposable
instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-constraints-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

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


def binding(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def constraint(owner, kind, target=None, **settings):
    return {"kind": "constraint_add", "target": queries.object_handle(owner), "parameters": {
        "type": kind, "name": "QA " + kind, "target": queries.object_handle(target) if target else None, "settings": settings}}


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    cube, camera = window.scene.objects["Cube"], window.scene.objects["Camera"]
    cube.location = (1, 2, 3)
    window.view_layer.update()
    world_before = cube.matrix_world.copy()
    bpy.ops.ed.undo_push(message="constraints QA baseline")
    queries.invalidate()
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [binding(camera)], "write_set": [binding(cube)], "operations": [
            constraint(cube, "CHILD_OF", camera),
            constraint(cube, "LIMIT_ROTATION", use_limit=[True, False, False], minimum=[-.5, 0, 0], maximum=[.5, 0, 0]),
            constraint(cube, "LIMIT_SCALE", use_max=[True, True, True], maximum=[2, 2, 2]),
            constraint(cube, "DAMPED_TRACK", camera, track_axis="TRACK_Y"),
            constraint(cube, "COPY_TRANSFORMS", camera, mix_mode="AFTER")]})
    # Copy Transforms and Damped Track would move the cube; mute them to compare Child Of alone.
    job = native.begin(window, "angelo_scene_program", arguments)
    check("constraints_ready", settle(job).get("ready_to_commit") is True and not cube.constraints)
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("constraints_commit", "FINISHED" in result and job.result.get("status") == "committed")
    types = [(item.name, item.type) for item in cube.constraints]
    check("constraints_live", types == [("QA CHILD_OF", "CHILD_OF"), ("QA LIMIT_ROTATION", "LIMIT_ROTATION"),
                                        ("QA LIMIT_SCALE", "LIMIT_SCALE"), ("QA DAMPED_TRACK", "DAMPED_TRACK"),
                                        ("QA COPY_TRANSFORMS", "COPY_TRANSFORMS")])
    child = cube.constraints["QA CHILD_OF"]
    check("constraint_targets_are_live", child.target == camera and cube.constraints["QA DAMPED_TRACK"].target == camera
          and cube.constraints["QA DAMPED_TRACK"].track_axis == "TRACK_Y")
    check("limit_settings_live", cube.constraints["QA LIMIT_ROTATION"].use_limit_x and
          abs(cube.constraints["QA LIMIT_ROTATION"].max_x - .5) < 1e-6 and cube.constraints["QA LIMIT_SCALE"].use_max_z)
    for name in ("QA LIMIT_ROTATION", "QA LIMIT_SCALE", "QA DAMPED_TRACK", "QA COPY_TRANSFORMS"):
        cube.constraints[name].mute = True
    window.view_layer.update()
    check("child_of_set_inverse_keeps_owner", max(abs(cube.matrix_world[i][j] - world_before[i][j])
                                                  for i in range(4) for j in range(4)) < 1e-5)
    queries.invalidate()
    check("constrained_object_keeps_exact_revision", len(queries.object_revision(window, cube)) == 64)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("constraints_one_undo", not window.scene.objects["Cube"].constraints and no_stages())
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "constraints.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_CONSTRAINTS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
