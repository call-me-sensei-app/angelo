# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged Edit-mode kinds keep the private stage scene while a second window shows the live scene.

Outside ``--background``, entering or leaving Edit or Pose mode refreshes the
tool system of every other window (``WM_toolsystem_update_from_context_view3d``),
which sets each window as the context window in turn and so drops a Python
``scene`` override (``CTX_wm_window_set``). Without a fresh override after the
mode switch, the next operator reads and writes the artist's scene with the
stage's view layer: before the fix, an assertion and a segfault. This suite
opens a second main window on another workspace showing the live scene, runs
the ``uv_unwrap`` kind (``action_edit_context``, shared with the typed mesh and
UV kinds) and asserts: the scene was the private stage scene inside every staged
Edit-mode override, and the live scene's tool settings, 3D cursor, keying sets
and keyframes are byte-for-byte unchanged. (The operator_run cases live with
operator_run on feature/native-coverage.)
The live scene is primed so that a wrong scene is visible: cursor at (5, 5, 5),
edge select mode, auto-keying through an absolute keying set on the live Cube.

Use the exact Angelo binary with --factory-startup (GUI, not --background). No
provider process, account or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-edit-context-windows"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import action_mesh, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
CURSOR = (5.0, 5.0, 5.0)


def check(name, condition):
    report["checks"][name] = bool(condition)  # Every case runs; the suite fails at the end.


def window():
    return bpy.context.window_manager.windows[0]


def scene():
    return window().scene


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit") or result.get("status") == "preview":
            return result
    raise AssertionError("cooperative native program did not settle")


def plain(struct, depth=0):
    """Every stored RNA setting of a struct (nested non-ID structs too), as plain JSON.

    Read-only values are skipped: some are computed from the reading context
    (``particle_edit.object`` is the context's active object), not stored.
    """
    values = {}
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type" or prop.type == "COLLECTION" or (prop.is_readonly and prop.type != "POINTER"):
            continue
        value = getattr(struct, name, None)
        if prop.type == "POINTER":
            if value is None or isinstance(value, bpy.types.ID):
                if not prop.is_readonly:
                    values[name] = getattr(value, "name", None)
            elif depth < 2:
                values[name] = plain(value, depth + 1)
        elif getattr(prop, "is_array", False) or prop.type == "ENUM" and getattr(prop, "is_enum_flag", False):
            values[name] = sorted(value) if isinstance(value, (set, frozenset)) else [item for item in value]
        else:
            values[name] = value
    return values


def live_state():
    live = scene()
    curves = {}
    for obj in live.objects:
        action = obj.animation_data.action if obj.animation_data else None
        curves[obj.name] = None if obj.animation_data is None else [
            [curve.data_path, curve.array_index, len(curve.keyframe_points)]
            for layer in (action.layers if action else ()) for strip in layer.strips
            for bag in getattr(strip, "channelbags", ()) for curve in bag.fcurves]
    return json.dumps({"tool_settings": plain(live.tool_settings), "cursor": plain(live.cursor),
                       "keying_sets": [[ks.bl_label, [[path.data_path, path.id.name if path.id else None] for path in ks.paths]]
                                       for ks in live.keying_sets], "active_keying_set": live.keying_sets.active_index,
                       "frame": live.frame_current, "keys": curves}, sort_keys=True, default=str)


def record(log, label):
    current = bpy.context.scene
    log.append({"label": label, "stage_scene": bool(current.get("_angelo_program_stage")),
                "view_layer_of_scene": bpy.context.view_layer.id_data == current})


@contextmanager
def recorded(log):
    """Record the scene at the body of every staged Edit-mode override."""
    edit = action_mesh._edit_override

    @contextmanager
    def recording_edit(context, obj):
        with edit(context, obj):
            record(log, "edit_override_body")
            yield

    action_mesh._edit_override = recording_edit
    try:
        yield
    finally:
        action_mesh._edit_override = edit


def prime_live_scene():
    """A second window on another workspace, and live settings a wrong scene would read or write."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.window_new()
    windows = bpy.context.window_manager.windows
    other = next(space for space in bpy.data.workspaces if space != windows[0].workspace)
    windows[1].workspace = other
    live = scene()
    live.cursor.location = CURSOR
    settings = live.tool_settings
    settings.mesh_select_mode = (False, True, False)
    settings.use_uv_select_sync = False
    rig = bpy.data.objects.new("QA Window Rig", bpy.data.armatures.new("QA Window Rig"))
    live.collection.objects.link(rig)
    window().view_layer.objects.active = rig
    with bpy.context.temp_override(window=window()):
        bpy.ops.object.mode_set(mode="EDIT")
        bone = rig.data.edit_bones.new("Root")
        bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Root"].location = (1, 2, 3)
    cube = live.objects["Cube"]
    keying = live.keying_sets.new(idname="QA_WINDOWS_ABSOLUTE", name="QA Windows Absolute")
    keying.paths.add(cube, "location", index=-1)
    live.keying_sets.active_index = len(live.keying_sets) - 1
    settings.use_keyframe_insert_auto = True
    settings.use_keyframe_insert_keyingset = True
    window().view_layer.objects.active = cube
    with bpy.context.temp_override(window=window()):
        bpy.ops.ed.undo_push(message="edit context windows QA")
    queries.invalidate()


def program(target, operations):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "operations": operations, "write_set": [
        {"handle": queries.object_handle(target), "revision": queries.object_revision(window(), target)}]})


def run_case(label, target_name, operations, expect=None):
    print("ANGELO_EDIT_CONTEXT_WINDOWS_CASE", label, flush=True)  # A crash names its case in the log.
    queries.invalidate()
    target = scene().objects[target_name]
    arguments = program(target, [dict(operation, target=queries.object_handle(target)) for operation in operations])
    before, log = live_state(), []
    with recorded(log):
        job = native.begin(window(), "angelo_scene_program", arguments)
        result = settle(job)
    staged_ok = result.get("ready_to_commit")
    if staged_ok:
        with bpy.context.temp_override(window=window()):  # Commit belongs to the program's window.
            committed = "FINISHED" in bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        committed = committed and job.result.get("status") == "committed"
    else:
        committed = False
        job.cancel()
    after = live_state()
    case = {"staged": bool(staged_ok), "committed": committed, "error": (result.get("error") or job.result.get("error"))
            if not committed else None, "contexts": log, "live_unchanged": before == after}
    if before != after:
        case["live_before"], case["live_after"] = json.loads(before), json.loads(after)
    report["cases"][label] = case
    check(label + "_ran_operators", bool(log))
    check(label + "_stage_scene_at_every_operator", log and all(entry["stage_scene"] and entry["view_layer_of_scene"]
                                                                for entry in log))
    check(label + "_live_scene_settings_cursor_and_keys_unchanged", before == after)
    check(label + "_commits", committed)
    if expect is not None and committed:
        check(label + "_staged_result", expect(scene().objects[target_name]))
    if committed:
        with bpy.context.temp_override(window=window()):
            bpy.ops.ed.undo()
        queries.invalidate()
    check(label + "_no_stage_residue", no_stages() and not native.runtime_is_staging())


def run_checks():
    report["windows"] = [[item.scene.name, item.workspace.name] for item in bpy.context.window_manager.windows]
    check("second_window_shows_live_scene_on_another_workspace", len(bpy.context.window_manager.windows) == 2
          and report["windows"][0][0] == report["windows"][1][0] and report["windows"][0][1] != report["windows"][1][1])

    # The staged Edit-mode kind shared with the typed UV and mesh kinds.
    run_case("kind_uv_unwrap", "Cube", [{"kind": "uv_unwrap", "parameters": {"uv_map": "QA Window UV"}}],
             lambda cube: "QA Window UV" in cube.data.uv_layers)
    report["success"] = all(report["checks"].values())


def finish():
    native.cleanup_all()
    (OUT / "edit-context-windows.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_EDIT_CONTEXT_WINDOWS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}),
          flush=True)
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.quit_blender()


def run_cases():
    try:
        run_checks()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        finish()


def prime():
    """The workspace of a new window changes on the next event-loop pass, so the cases run in a later tick."""
    try:
        bpy.context.preferences.edit.use_global_undo = True
        native.register()
        bpy.utils.register_class(commit_operator(report))
        prime_live_scene()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
        finish()
        return None
    bpy.app.timers.register(run_cases, first_interval=1)
    return None


bpy.app.timers.register(prime, first_interval=1)
