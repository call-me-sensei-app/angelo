# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object names in program order through real staged programs, by QA commit and by the production Undo bridge.

Every scenario runs twice. ``qa`` commits through ``angelo.program_qa_commit``
(native.commit_job inside an UNDO operator, as the other program suites do).
``production`` is the path a model's plan takes: the actual Controller admits
and approves a ToolCall, BlenderExecutor stages its ProgramJob, and
``angelo.commit_native_job`` commits through program_undo.commit, the native
``_angelo_undo`` bridge and its CommitScope, which approves deletions (the
path object_join and object_convert failed OWNERSHIP on before the fix).

* S1 rename the old camera, create "Camera", make it the scene camera, delete
  the old one: names, one Undo, one Redo, no ``__angelo_`` name left.
* S2 delete the camera, create "Camera", make it the scene camera.
* S3 a failure after the names are applied (an injected one, and the deleted
  camera becoming another scene's active camera while the stage waits) rolls
  every name back.
* S4 swap two names through a temporary one, with Undo.
* S5 S1 as a PREVIEW, accepted: memory staging and the file-bundle fallback
  (qa), and the Controller's angelo_accept_preview (production).
* S6 a key on the new camera names its Action and slot after "Camera"; a first
  key on an existing object names its slot after it.
* S7 object_join, a named object_convert(keep_original=False) taking its
  source's name and an unnamed one keeping it.
* S8 the plan in the person's sentence order: create "Camera", make it the
  scene camera, then delete the old one, committed in one plan; two creations
  asking for the name the old camera releases are refused before staging.

GUI factory startup with the exact Angelo binary; no provider, account or paid
request. Writes program-names.json in ANGELO_QA_DIR.
"""

import contextlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-program-names-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
from angelo_agent import agent_workspace_ui, native_programs as native, program_undo, ui  # noqa: E402
from angelo_agent import scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import Controller, ToolCall, ToolDispatchBudget  # noqa: E402
from angelo_agent.program_names import private_name  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

REPORT = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
OLD_CAMERA_X = None  # The factory camera's location.x, read once.


def check(name, value):
    REPORT["checks"][name] = bool(value)
    assert value, name


def window():
    return bpy.context.window_manager.windows[0]


def objects():
    return window().scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window(), obj)}


def scene_entry():
    settings = scene_settings.execute(window(), {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations, writes=(), mode="APPLY"):
    return {"mode": mode, "read_set": [], "write_set": list(writes), "operations": operations}


def private_objects():
    return sorted(obj.name for obj in bpy.data.objects if private_name(obj.name))


def private_slots():
    return sorted(slot.identifier for action in bpy.data.actions for slot in action.slots if "__angelo_" in slot.identifier)


def clean():
    return not private_objects() and not private_slots() and no_stages()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


# ----------------------------------------------------------------------------------------------------------------
# The two commit paths. Each returns the delivered result with "ok": committed with one Undo step.
# ----------------------------------------------------------------------------------------------------------------

def qa_commit(arguments, before_commit=None):
    job = native.begin(window(), "angelo_scene_program", validate_call("angelo_scene_program", arguments))
    staged = settle(job)
    if not staged.get("ready_to_commit"):
        return dict(staged, ok=False)
    if before_commit is not None:
        before_commit()
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return dict(job.result, ok=job.result.get("status") == "committed")


@contextlib.contextmanager
def production_controller():
    # No queries.invalidate() here: that starts a new document epoch, which expires the handles the caller's
    # arguments were just built with (baseline() and undo() invalidate before they are built, as a host does).
    controller = Controller(tempfile.mkdtemp(prefix="names-controller-", dir=OUT))
    session = controller.state["id"]
    controller.set_document(ui.document_key())
    controller.busy = True
    controller.state["options"]["approval"] = "ASK"
    ui._controllers[session] = controller
    try:
        yield controller
    finally:
        ui._controllers.pop(session, None)
        controller.busy = False
        controller.close()


def production_call(controller, name, tool, arguments, commit=True, before_commit=None):
    """One call admitted, approved (the fixture authorizes this exact program) and staged by the real Controller,
    then committed by the production operator. No controller or job method is stubbed."""
    session, document, executor, responses = controller.state["id"], controller.document, BlenderExecutor(window()), []
    controller.request_tool(name, tool, arguments, responses.append)
    call = controller.pending.get(session + "/" + name)
    assert isinstance(call, ToolCall), responses
    job = None
    for _ in range(400):
        controller.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
        if call.approval and not call.authorized and not responses:
            controller.approve_tool(call, executor, document)
            continue
        job = controller.native_jobs.get(call.id)
        if responses or job is not None and job.ready_to_commit:
            break
    if responses or not commit:
        return dict(responses[-1] if responses else {}, ok=False)
    assert job is not None and job.ready_to_commit, responses
    if before_commit is not None:
        before_commit()
    try:
        outcome = bpy.ops.angelo.commit_native_job("EXEC_DEFAULT", True, call_id=call.id, session_id=session)
    except RuntimeError:
        outcome = {"CANCELLED"}  # The operator reported the refusal; the delivered result says why.
    for _ in range(400):  # A refusal may restage first; it is final once the call delivers its one result.
        if responses:
            break
        controller.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
    check("production_" + name + "_delivers_one_result", len(responses) == 1 and call.id not in controller.pending)
    result = dict(responses[0])
    return dict(result, ok=outcome == {"FINISHED"} and result.get("undoable") is True and not result.get("error"))


def production_commit(arguments, before_commit=None):
    with production_controller() as controller:
        return production_call(controller, "program", "angelo_scene_program", arguments, before_commit=before_commit)


PATHS = (("qa", qa_commit), ("production", production_commit))


def record(label, result):
    REPORT["cases"][label] = {key: result.get(key) for key in ("ok", "status", "error", "error_code", "undoable",
                                                              "partial_changes_possible", "mutation_started",
                                                              "undo_published")}


# ----------------------------------------------------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------------------------------------------------

def replace_camera(mode="APPLY", extra=()):
    old, scene = objects()["Camera"], scene_entry()
    return program([op("object_rename", handle(old), name="Camera_Old"),
                    op("camera_create", name="Camera", alias="cam", location=[6, -8, 4], look_at=[0, 0, 0],
                       settings={"lens": 50}),
                    op("property_set", scene["handle"], values={"camera": "$cam"}),
                    op("object_delete", handle(old)), *extra], writes=[binding(old), scene], mode=mode)


def replaced(label):
    cameras = [obj for obj in bpy.data.objects if obj.type == "CAMERA"]
    camera = objects().get("Camera")
    check(label, len(cameras) == 1 and camera is not None and window().scene.camera == camera
          and abs(camera.location.x - 6) < 1e-4 and abs(camera.data.lens - 50) < 1e-4
          and "Camera_Old" not in bpy.data.objects and clean())


def original_camera(label):
    cameras = [obj for obj in bpy.data.objects if obj.type == "CAMERA"]
    camera = objects().get("Camera")
    check(label, len(cameras) == 1 and camera is not None and window().scene.camera == camera
          and abs(camera.location.x - OLD_CAMERA_X) < 1e-4 and "Camera_Old" not in bpy.data.objects and clean())


def s1_replace_camera(path, commit):
    label = "s1_" + path
    baseline(label)
    result = commit(replace_camera())
    record(label, result)
    check(label + "_commits", result["ok"])
    replaced(label + "_new_camera_takes_the_freed_name")
    undo()
    original_camera(label + "_one_undo_restores_the_old_camera")
    redo()
    replaced(label + "_one_redo_restores_the_new_camera")
    undo()


def s2_delete_then_create(path, commit):
    label = "s2_" + path
    baseline(label)
    old, scene = objects()["Camera"], scene_entry()
    result = commit(program([op("object_delete", handle(old)),
                             op("camera_create", name="Camera", alias="cam", location=[6, -8, 4], look_at=[0, 0, 0],
                                settings={"lens": 50}),
                             op("property_set", scene["handle"], values={"camera": "$cam"})],
                            writes=[binding(old), scene]))
    record(label, result)
    check(label + "_commits", result["ok"])
    replaced(label + "_new_camera_takes_the_deleted_name")
    undo()
    original_camera(label + "_one_undo_restores")


def s3_failure_after_names(path, commit):
    label = "s3_" + path
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):  # After apply_names gave every name.
        original(self, stage, journal)
        raise RuntimeError("injected failure after names were applied")

    baseline(label)
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        result = commit(replace_camera())
    record(label + "_injected", result)
    check(label + "_injected_failure_reported", not result["ok"] and not result.get("partial_changes_possible"))
    original_camera(label + "_injected_rollback_restores_every_name")
    other = bpy.data.scenes["QA Other Scene"]

    def other_scene_takes_the_camera():
        other.camera = objects()["Camera"]  # An artist's edit while the stage waits.

    result = commit(replace_camera(), before_commit=other_scene_takes_the_camera)
    record(label + "_other_scene_camera", result)
    other = bpy.data.scenes["QA Other Scene"]
    restored = other.camera == objects().get("Camera")
    other.camera = None
    # QA commits stage first and fail at the deletion, after the names (rolled back). The production path re-checks
    # deletions on fresh state during native preparation and refuses before any name is applied; it then reports
    # that preparation callbacks could have run (program_commit_controller._preparation_result) without a
    # mutation or an Undo step. Either way nothing changed, which the name checks below prove.
    check(label + "_other_scene_camera_fails_closed", not result["ok"] and (
        not result.get("partial_changes_possible")
        or result.get("mutation_started") is False and result.get("undo_published") is False))
    check(label + "_other_scene_camera_restores_every_name", restored)
    original_camera(label + "_other_scene_camera_rollback")


def s4_swap(path, commit):
    label = "s4_" + path
    baseline(label)
    cube, light = objects()["Cube"], objects()["Light"]
    result = commit(program([op("object_rename", handle(cube), name="QA Swap"),
                             op("object_rename", handle(light), name="Cube"),
                             op("object_rename", handle(cube), name="Light")], writes=[binding(cube), binding(light)]))
    record(label, result)
    check(label + "_commits", result["ok"])
    swapped = lambda: objects()["Cube"].type == "LIGHT" and objects()["Light"].type == "MESH" and clean()
    check(label + "_names_swapped", swapped())
    undo()
    check(label + "_one_undo_restores", objects()["Cube"].type == "MESH" and objects()["Light"].type == "LIGHT" and clean())
    redo()
    check(label + "_one_redo_swaps_again", swapped())
    undo()


def s5_preview_accept_qa(storage):
    label = "s5_qa_" + storage
    baseline(label)
    with patch.object(native.BlenderProgramBackend, "memory_staging", storage == "memory"), \
            patch.object(native.BlenderProgramBackend, "fuse_phases", False):
        preview = native.begin(window(), "angelo_scene_program",
                               validate_call("angelo_scene_program", replace_camera("PREVIEW")))
        result = settle(preview)
        added = [row["name"] for row in result.get("delta", {}).get("added", [])]
        check(label + "_retained_without_live_edit", result.get("status") == "preview" and added == ["Camera"]
              and abs(objects()["Camera"].location.x - OLD_CAMERA_X) < 1e-4 and clean())
        accepted = native.begin(window(), "angelo_accept_preview", {"preview_id": result["preview_id"]})
        check(label + "_accepted_ready", settle(accepted).get("ready_to_commit"))
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=accepted.id)
    record(label, dict(accepted.result, ok=accepted.result.get("status") == "committed"))
    check(label + "_commits", accepted.result.get("status") == "committed")
    replaced(label + "_names_after_accept")
    undo()
    original_camera(label + "_one_undo_restores")


def s5_preview_accept_production():
    label = "s5_production"
    baseline(label)
    with production_controller() as controller:
        preview = production_call(controller, "preview", "angelo_scene_program", replace_camera("PREVIEW"), commit=False)
        preview_id = preview.get("preview_id") or next(reversed(list(controller._native_preview_cards)), None)
        check(label + "_retained_without_live_edit", isinstance(preview_id, str)
              and abs(objects()["Camera"].location.x - OLD_CAMERA_X) < 1e-4 and clean())
        result = production_call(controller, "accept", "angelo_accept_preview", {"preview_id": preview_id})
    record(label, result)
    check(label + "_commits", result["ok"])
    replaced(label + "_names_after_accept")
    undo()
    original_camera(label + "_one_undo_restores")


def s6_keys(path, commit):
    label = "s6_" + path
    baseline(label)
    cube = objects()["Cube"]
    key = op("keyframe_insert", "$cam", property="location", value=[6, -8, 4], frame=1)
    first = op("keyframe_insert", handle(cube), property="location", value=[0, 0, 0], frame=1)
    arguments = replace_camera(extra=(key, first))
    arguments["write_set"].append(binding(cube))
    result = commit(arguments)
    record(label, result)
    check(label + "_commits", result["ok"])
    camera, cube = objects()["Camera"], objects()["Cube"]
    for name, obj in (("Camera", camera), ("Cube", cube)):
        animation = obj.animation_data
        slot = animation.action_slot if animation is not None else None
        REPORT["cases"][label + "_" + name] = {"action": animation.action.name if animation and animation.action else None,
                                                "slot": slot.identifier if slot else None,
                                                "last_slot": animation.last_slot_identifier if animation else None}
        check(label + "_" + name.lower() + "_action_and_slot_take_its_name",
              animation is not None and animation.action.name == name + "Action" and slot is not None
              and slot.identifier == "OB" + name and slot.name_display == name
              and animation.last_slot_identifier == "OB" + name)
    check(label + "_no_private_slot_or_name", clean())
    undo()
    original_camera(label + "_one_undo_restores")
    check(label + "_one_undo_removes_the_cube_key", objects()["Cube"].animation_data is None
          or objects()["Cube"].animation_data.action is None)


def s7_join_and_convert(path, commit):
    label = "s7_" + path
    baseline(label)
    cube, part, text, label_text = (objects()[name] for name in ("Cube", "QA Part", "QA Text", "QA Label"))
    vertices = len(cube.data.vertices) + len(part.data.vertices)
    result = commit(program([op("object_join", handle(cube), sources=[handle(part)]),
                             op("object_convert", source=handle(text), alias="solid", name="QA Text", keep_original=False),
                             op("object_convert", source=handle(label_text), alias="label", keep_original=False)],
                            writes=[binding(cube), binding(part), binding(text), binding(label_text)]))
    record(label, result)
    check(label + "_commits_through_the_scope", result["ok"])  # OWNERSHIP through the bridge before the fix.
    check(label + "_join_and_conversions_live", "QA Part" not in bpy.data.objects
          and len(objects()["Cube"].data.vertices) == vertices and objects()["QA Text"].type == "MESH"
          and objects()["QA Label"].type == "MESH" and "QA Text.001" not in bpy.data.objects
          and "QA Label.001" not in bpy.data.objects and clean())
    undo()
    check(label + "_one_undo_restores", "QA Part" in objects() and objects()["QA Text"].type == "FONT"
          and objects()["QA Label"].type == "FONT" and len(objects()["Cube"].data.vertices) == 8 and clean())


def s8_sentence_order(path, commit):
    label = "s8_" + path
    baseline(label)

    def plan(*cameras):
        old, scene = objects()["Camera"], scene_entry()
        created = [op("camera_create", name="Camera", alias=alias, location=[6, -8, 4], look_at=[0, 0, 0],
                      settings={"lens": 50}) for alias in cameras]
        return program(created + [op("property_set", scene["handle"], values={"camera": "$" + cameras[0]}),
                                  op("object_delete", handle(old))], writes=[binding(old), scene])

    result = commit(plan("cam"))
    record(label, result)
    check(label + "_commits_in_one_plan", result["ok"])  # Was Camera.001 and a failed name check.
    replaced(label + "_new_camera_takes_the_name_released_later")
    undo()
    original_camera(label + "_one_undo_restores")
    result = commit(plan("cam", "two"))
    record(label + "_ambiguous", result)
    check(label + "_ambiguous_refused_before_staging", not result["ok"] and "NAME_AMBIGUOUS" in (result.get("error") or "")
          and not result.get("mutation_started"))
    original_camera(label + "_ambiguous_changes_nothing")


# ----------------------------------------------------------------------------------------------------------------

def fixture():
    global OLD_CAMERA_X
    OLD_CAMERA_X = objects()["Camera"].location.x
    mesh = bpy.data.meshes.new("QA Part")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Translation((0, 3, 0)))
    bm.to_mesh(mesh)
    bm.free()
    collection = bpy.data.collections["Collection"]
    collection.objects.link(bpy.data.objects.new("QA Part", mesh))
    for name, body in (("QA Text", "A"), ("QA Label", "B")):
        data = bpy.data.curves.new(name, "FONT")
        data.body = body
        collection.objects.link(bpy.data.objects.new(name, data))
    bpy.data.scenes.new("QA Other Scene")
    baseline("program names fixture")


def run():
    classes = []
    try:
        win = window()
        area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.context.preferences.edit.use_global_undo = True
            native.register()
            classes = [commit_operator(REPORT)] + [c for c in agent_workspace_ui.workspace_operators()
                                                    if getattr(c, "bl_idname", "") == "angelo.commit_native_job"]
            for cls in classes:
                bpy.utils.register_class(cls)
            check("native_undo_bridge_available", program_undo.native_bridge() is not None)
            fixture()
            for path, commit in PATHS:
                s1_replace_camera(path, commit)
                s2_delete_then_create(path, commit)
                s3_failure_after_names(path, commit)
                s4_swap(path, commit)
                s6_keys(path, commit)
                s7_join_and_convert(path, commit)
                s8_sentence_order(path, commit)
            s5_preview_accept_qa("memory")
            s5_preview_accept_qa("file")
            s5_preview_accept_production()
            check("no_stage_residue", no_stages() and not native.runtime_is_staging())
        REPORT["success"] = True
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
        for cls in reversed(classes):
            with contextlib.suppress(Exception):
                bpy.utils.unregister_class(cls)
        (OUT / "program-names.json").write_text(json.dumps(REPORT, indent=2, default=str))
        print("PROGRAM_NAMES_DONE", REPORT.get("success", False), flush=True)
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1.)
