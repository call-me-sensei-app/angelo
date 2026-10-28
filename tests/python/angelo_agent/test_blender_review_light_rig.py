# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial cases of camera-relative ``orbit`` placement through the real controller and executor.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
No provider, account, user project, network request or paid call is involved;
the fixture edits only its factory-startup scene.

test_blender_camera_light_programs.py drives BlenderExecutor and the native
backend directly; this drives Controller.request_tool -> approval -> the
controller's Undo-operator commit with the real BlenderExecutor, as the chat
does, and records:

* held: one resolution, before approval, of the program the approval shows,
  committed exactly, certified by the host's summary with no model round;
* a camera moved after resolution makes the commit refuse (``CAMERA_MOVED``)
  and is never re-resolved (the documented contract); an observation: a rolled
  camera's "right" (azimuth pi/2);
* faults the review found, fixed on fix/v1-light-rig-review (the suite fails
  if any reproduces again): a program that moves the scene camera's parent,
  or an object a constraint makes it follow, is refused before approval; a
  far orbit is a VALIDATION refusal that claims no change, and the corrected
  orbit in the same request reaches approval.

Inspect review-light-rig.json: a zero exit status alone is not proof.
"""

import copy
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-review-light-rig"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import approvals, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.program_orbits import has_orbit  # noqa: E402
from program_qa_helpers import args_for, no_stages  # noqa: E402
from oneshot_mini_checks import behind  # noqa: E402  (the one-shot grader's rule, never a copy of its constant)

DOCUMENT = ("review-light-rig-fixture", 1)
CONTROLLER = {}
RESOLUTIONS = []
report = {"checks": {}, "observations": {}, "faults": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "checkout_modules": None}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def fault(name, reproduced, detail):
    """A review fault: recorded, and the suite fails at the end while any reproduces."""
    report["faults"][name] = {"reproduced": bool(reproduced), "detail": detail}


def window():
    return bpy.context.window_manager.windows[0]


class CountingExecutor(BlenderExecutor):
    def host_program(self, arguments, used=None):  # The controller's camera read (program_camera.staged_program).
        RESOLUTIONS.append(copy.deepcopy(arguments))
        return super().host_program(arguments, used)


class ControllerCommit(bpy.types.Operator):
    """The production continuation: the controller commits inside an Undo operator."""
    bl_idname = "angelo.review_light_rig_commit"
    bl_label = "Disposable controller commit"
    bl_options = {"REGISTER", "UNDO"}
    call_id: bpy.props.StringProperty()

    def execute(self, context):
        controller = CONTROLLER["value"]
        call = controller.pending.get(self.call_id)
        result = controller.commit_native_job(call, CountingExecutor(context.window), DOCUMENT) if call else None
        return {"FINISHED"} if isinstance(result, dict) and result.get("success") else {"CANCELLED"}


def operation(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def area(energy):
    return {"light_type": "AREA", "energy": energy, "size": 2}


def rig(prefix, look=(0, 0, 0)):
    return [operation("light_create", name=prefix + " Key", alias="key", look_at=list(look),
                      orbit={"azimuth": -math.pi / 4, "elevation": .6}, settings=area(1000)),
            operation("light_create", name=prefix + " Fill", alias="fill", look_at=list(look),
                      orbit={"azimuth": math.pi / 3, "elevation": .3}, settings=area(300)),
            operation("light_create", name=prefix + " Rim", alias="rim", look_at=list(look),
                      orbit={"azimuth": math.pi, "elevation": .7}, settings=area(800))]


def new_controller(case):
    controller = CONTROLLER["value"] = Controller(str(OUT / case))
    controller.set_document(DOCUMENT)
    controller.busy, controller.cancelled = True, False
    controller.state["options"]["approval"] = "ASK"
    controller._begin_metrics()
    return controller


def request(controller, identity, arguments):
    executor, responses, completions = CountingExecutor(window()), [], []
    controller.request_tool(identity, "angelo_scene_program", arguments, responses.append,
                            completion_sink=completions.append)
    controller.pump_tools(executor, DOCUMENT)  # Admission: the orbit resolves and the approval is requested.
    return controller.pending.get(controller.state["id"] + "/" + identity), responses, completions, executor


def drive(controller, executor, responses):
    commit = lambda call: bpy.ops.angelo.review_light_rig_commit("EXEC_DEFAULT", True, call_id=call.id)
    for _ in range(300):
        if responses:
            return responses[0]
        controller.pump_tools(executor, DOCUMENT, commit_mutation=commit, budget=ToolDispatchBudget(seconds=1))
    raise AssertionError("The program did not finish within bounded pumping")


def cosine_from_camera(position, camera_position, aim=Vector((0, 0, 0))):
    return (Vector(position) - aim).normalized().dot((Vector(camera_position) - aim).normalized())


def remove(prefix):
    for obj in [obj for obj in bpy.data.objects if obj.name.startswith(prefix)]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data) if isinstance(data, bpy.types.Light) else None
    window().view_layer.update()
    queries.invalidate()


def approval_path(camera):
    """Held: one resolution before approval; the approval shows, and the commit makes, the resolved program."""
    controller = new_controller("approval")
    RESOLUTIONS.clear()
    submitted = {"mode": "APPLY", "operations": rig("QA")}
    try:
        call, responses, completions, executor = request(controller, "rig", submitted)
        check("approval_requested_after_one_resolution", call is not None and call.approval and not responses
              and len(RESOLUTIONS) == 1 and not has_orbit(call.program_arguments) and has_orbit(call.arguments))
        shown = approvals.review_arguments(call)
        approved = [op["parameters"]["location"] for op in call.program_arguments["operations"]]
        check("approval_shows_the_resolved_locations",
              [op["parameters"]["location"] for op in shown["operations"]] == approved
              and all(any(c["kind"] == "transform" and c["expected"] == location for c in shown["expected_checks"])
                      for location in approved))
        details = approvals.approval_details(call.name, shown)
        report["observations"]["approval_details_excerpt"] = details[:1200]
        check("nothing_staged_before_approval", no_stages() and not any(o.name.startswith("QA ") for o in bpy.data.objects))
        controller.approve_tool(call, executor, DOCUMENT)
        result = drive(controller, executor, responses)
        report["observations"]["approval_path_result_status"] = result.get("status")
        check("committed_with_every_check_passing", result.get("status") == "committed"
              and all(row.get("passed") for row in result.get("checks", ())))
        made = [list(bpy.data.objects[op["parameters"]["name"]].matrix_world.translation)
                for op in call.program_arguments["operations"]]
        check("committed_exactly_where_approved", all(
            (Vector(a) - Vector(b)).length < 1e-5 for a, b in zip(made, approved)))
        check("resolved_once_through_staging_and_commit", len(RESOLUTIONS) == 1)
        cosines = {op["parameters"]["name"]: cosine_from_camera(position, camera.matrix_world.translation)
                   for op, position in zip(call.program_arguments["operations"], made)}
        report["observations"]["approval_path_cosines"] = cosines
        rim = next(position for op, position in zip(call.program_arguments["operations"], made)
                   if op["parameters"]["name"] == "QA Rim")
        check("rim_behind_from_the_camera", behind(tuple(rim), tuple(camera.matrix_world.translation)))
        summary = certified_completion([{"tool": "angelo_scene_program", "arguments": submitted}], [result],
                                       completions=completions[:1] or [None], read_only=lambda _name: False)
        report["observations"]["host_summary"] = summary
        check("host_certifies_it_without_a_model_round", isinstance(summary, str)
              and "no further model round" in summary and "12 of 12" in summary)
        controller.pump_events()
        rows = [m for m in controller.state["messages"] if m.get("tool") == "angelo_scene_program"]
        check("transcript_records_the_resolved_program", rows and rows[-1]["arguments"].get("program") == call.program_arguments)
    finally:
        controller.busy = False
        controller.close()
        remove("QA ")


def camera_moved_after_resolution(camera):
    """The documented contract: nothing re-places an approved orbit; commit refuses when its camera moved."""
    controller = new_controller("moved")
    RESOLUTIONS.clear()
    original = camera.matrix_world.copy()
    try:
        call, responses, _completions, executor = request(controller, "moved", {"mode": "APPLY", "operations": rig("QM")[2:]})
        approved = call.program_arguments["operations"][0]["parameters"]["location"]
        camera.location = (-camera.location.x, -camera.location.y, camera.location.z)  # Round to the other side.
        window().view_layer.update()
        controller.approve_tool(call, executor, DOCUMENT)
        result = drive(controller, executor, responses)
        made = list(bpy.data.objects["QM Rim"].matrix_world.translation) if "QM Rim" in bpy.data.objects else None
        report["observations"]["camera_moved_after_resolution"] = {
            "status": result.get("status"), "error": result.get("error"), "approved": approved, "made": made,
            "resolutions": len(RESOLUTIONS),
            "rim_cosine_from_moved_camera": None if made is None else cosine_from_camera(made, camera.matrix_world.translation)}
        # Commit reads the camera again (camera_guards) and refuses
        # rather than committing a rim no longer behind the subject, and never re-places it.
        check("moved_camera_commit_is_refused_and_never_re_placed", result.get("status") == "failed"
              and "CAMERA_MOVED" in str(result.get("error")) and made is None and len(RESOLUTIONS) == 1)
    finally:
        controller.busy = False
        controller.close()
        camera.matrix_world = original
        window().view_layer.update()
        remove("QM ")


def parent_moved_in_the_same_program(camera):
    """Fault: the orbit is measured from the camera before the program, yet moving its parent is not refused."""
    original = camera.matrix_world.copy()
    holder = bpy.data.objects.new("QP Camera Rig", None)
    window().scene.collection.objects.link(holder)
    camera.parent = holder
    camera.matrix_parent_inverse = holder.matrix_world.inverted()
    window().view_layer.update()
    queries.invalidate()
    before = camera.matrix_world.translation.copy()
    controller = new_controller("parent")
    RESOLUTIONS.clear()
    try:
        turn = operation("transform", queries.object_handle(holder), rotation=[0, 0, math.pi])
        arguments = args_for(window(), [turn] + rig("QP")[2:], writes=[holder])
        call, responses, _completions, executor = request(controller, "parent", arguments)
        refused = call is None and bool(responses) and "camera" in str(responses[0].get("error", ""))
        outcome = {"refused": refused, "answer": responses[0] if responses else None}
        if call is not None and call.approval:
            controller.approve_tool(call, executor, DOCUMENT)
            result = drive(controller, executor, responses)
            camera_after = camera.matrix_world.translation.copy()
            rim = bpy.data.objects.get("QP Rim")
            outcome.update(status=result.get("status"), camera_before=list(before), camera_after=list(camera_after),
                           rim=None if rim is None else list(rim.matrix_world.translation))
            if rim is not None:
                outcome["rim_cosine_from_camera_after_commit"] = cosine_from_camera(rim.matrix_world.translation, camera_after)
                outcome["rim_cosine_from_camera_before"] = cosine_from_camera(rim.matrix_world.translation, before)
        report["observations"]["parent_moved_in_the_same_program"] = outcome
        fault("program_moving_the_camera_parent_not_refused", not refused,
              "A program that turns the scene camera's parent 180 degrees and adds an orbit rim (azimuth pi) is "
              "approved and commits; the rim is then in front of the camera it was meant to stand behind.")
        check("camera_parent_write_refused_as_validation", refused and responses[0].get("error_code") == "VALIDATION"
              and "follows" in responses[0]["error"] and no_stages() and len(RESOLUTIONS) == 1)
    finally:
        controller.busy = False
        controller.close()
        camera.parent = None
        camera.matrix_world = original
        bpy.data.objects.remove(holder, do_unlink=True)
        window().view_layer.update()
        remove("QP ")


def constraint_target_moved_in_the_same_program(camera):
    """Fixed: an object a constraint makes the camera follow (Copy Location here) counts like its parent."""
    leader = bpy.data.objects.new("QC Leader", None)
    window().scene.collection.objects.link(leader)
    leader.location = camera.matrix_world.translation
    follow = camera.constraints.new("COPY_LOCATION")
    follow.target = leader
    window().view_layer.update()
    queries.invalidate()
    controller = new_controller("constraint")
    RESOLUTIONS.clear()
    try:
        move = operation("transform", queries.object_handle(leader), location=[-7, 7, 5])
        arguments = args_for(window(), [move] + rig("QC")[2:], writes=[leader])
        call, responses, _completions, _executor = request(controller, "constraint", arguments)
        report["observations"]["constraint_target_moved"] = responses[0] if responses else None
        check("camera_constraint_target_write_refused", call is None and responses
              and responses[0].get("error_code") == "VALIDATION" and "follows" in responses[0]["error"] and no_stages())
        unrelated = args_for(window(), rig("QC")[2:], reads=[leader])
        call, responses, _completions, _executor = request(controller, "unrelated", unrelated)
        check("reading_what_the_camera_follows_is_fine", call is not None and call.approval and not responses)
    finally:
        controller.busy = False
        controller.close()
        camera.constraints.remove(follow)
        bpy.data.objects.remove(leader, do_unlink=True)
        window().view_layer.update()
        remove("QC ")


def far_orbit_locks_the_request(camera):
    """Fault: a resolved location past the bound is refused UNKNOWN, claims partial changes, and arms the barrier."""
    controller = new_controller("far")
    try:
        far = rig("QF", look=(-99999, 99999, 0))[2:]
        call, responses, _completions, _executor = request(controller, "far", {"mode": "APPLY", "operations": far})
        first = responses[0] if responses else {}
        fixed = copy.deepcopy(far)
        fixed[0]["parameters"]["orbit"].update(azimuth=0., distance=10)  # On the camera's side: inside the bound.
        again, answers, _completions, _executor = request(controller, "fixed", {"mode": "APPLY", "operations": fixed})
        second = answers[0] if answers else {"approval": bool(again is not None and again.approval)}
        report["observations"]["far_orbit"] = {"first": first, "corrected": second, "staged": not no_stages()}
        check("far_orbit_stages_nothing", call is None and no_stages() and "QF Rim" not in bpy.data.objects)
        fault("far_orbit_unknown_with_partial_changes", first.get("error_code") != "VALIDATION"
              or "partial_changes_possible" in first,
              "A far orbit is refused %s with partial_changes_possible=%s although nothing was staged."
              % (first.get("error_code"), first.get("partial_changes_possible")))
        fault("far_orbit_locks_the_request", second.get("error_code") == "RECOVERY_REQUIRED",
              "After it, the corrected orbit (distance 10) in the same request is refused: %s" % second.get("error"))
        check("far_orbit_names_the_orbit", "orbit of 'QF Rim'" in str(first.get("error")))
        check("corrected_orbit_reaches_approval", second.get("approval") is True)
    finally:
        controller.busy = False
        controller.close()
        remove("QF ")


def rolled_camera(camera):
    """Observation: the frame is the camera's position; azimuth pi/2 ("its right") ignores its roll."""
    original = camera.matrix_world.copy()
    try:
        camera.matrix_world = camera.matrix_world @ Matrix.Rotation(math.pi / 2, 4, "Z")  # A 90 degree roll.
        window().view_layer.update()
        resolved = CountingExecutor(window()).host_program(
            {"mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [], "operations": [
                {"kind": "light_create", "target": None, "parameters": {
                    "name": "QR Right", "alias": None, "rotation": [0, 0, 0],  # An orbit takes no location.
                    "look_at": [0, 0, 0], "orbit": {"azimuth": math.pi / 2, "elevation": 0.}, "settings": area(10)}}]})
        location = Vector(resolved["operations"][0]["parameters"]["location"])
        local = camera.matrix_world.inverted() @ location
        report["observations"]["rolled_camera_right_in_camera_space"] = list(local)
        check("rolled_camera_right_is_not_screen_right", abs(local.x) < .1 * abs(local.y))
    finally:
        camera.matrix_world = original
        window().view_layer.update()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(ControllerCommit)
    report["checkout_modules"] = native.__file__
    check("checkout_modules_loaded", str(ROOT) in native.__file__)
    queries.invalidate()
    camera = window().scene.camera
    check("factory_camera", camera is not None and (camera.matrix_world.translation - Vector((7.3589, -6.9258, 4.9583))).length < 1e-3)
    for case in (approval_path, camera_moved_after_resolution, parent_moved_in_the_same_program,
                 constraint_target_moved_in_the_same_program, far_orbit_locks_the_request, rolled_camera):
        try:
            case(camera)
        except Exception:
            report.setdefault("case_errors", {})[case.__name__] = traceback.format_exc()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["held"] = not report.get("case_errors") and all(report["checks"].values())
    report["success"] = report["held"] and not any(row["reproduced"] for row in report["faults"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "review-light-rig.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_REVIEW_LIGHT_RIG", json.dumps({key: report.get(key) for key in ("success", "held", "paid_requests")}),
      flush=True)
assert report.get("success"), report.get("error") or {k: v for k, v in report["faults"].items() if v["reproduced"]}
