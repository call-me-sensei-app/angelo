# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dry run of the live-eval "survey" task set's grading, with no model and no billing.

Walks the survey in its own order on the factory scene and takes every task
through the trial driver's own grading steps: ``LiveGrading.before`` (fixtures,
ground truth, the before-probe), the driver's closed-check route (its helpers,
extracted verbatim by ``bench_mcp_grading``), ``LiveGrading.after`` and
``complete``. The scene results are built with plain ``bpy``
(``survey_reference_builds``) and the replies are scripted rows.

* Every graded edit (closed checks or result checks) is run twice: a turn that
  changes nothing must fail, and the reference result must pass. An edit that
  is asked again once its checks hold is ungraded, not passed. In between, the
  results that only look like the request (``near_misses``) must fail.
* Every edit the survey declares ungraded stays ungraded, with its reason.
* Every question is asked with an empty reply, a wrong answer, the right answer
  and, when it needs an image, the right answer without one; only the right
  answer with its observation passes, the harness's ground truth is what the
  scene shows, and its fixture is undone afterwards. A variant of the camera
  question passes only beside its other variant (alone it is held, ungraded),
  and the two fail together when both are answered alike.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import sys
import traceback
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/private/tmp/angelo-survey-tasks-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import bench_mcp_grading  # noqa: E402
import eval_answers as answers  # noqa: E402
import eval_grading  # noqa: E402
import eval_grading_live  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_reference_builds  # noqa: E402
import survey_truth  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402

namespace = {"live": live, "copy": __import__("copy"), "bpy": bpy, "VerificationContext": VerificationContext,
             "verify": verify}
exec(bench_mcp_grading.session_helpers_source(), namespace)  # The trial driver's helpers, verbatim.
report = {"checks": {}, "paid_requests": 0, "tasks": {}, "failures_after_edit": {}, "truths": {}, "measured": {},
          "near_misses": {}}
trial = {"tasks": []}
scene = bpy.context.window_manager.windows[0].scene
TASKS = survey.build_survey_tasks()
GRADING = eval_grading_live.LiveGrading("survey", TASKS, trial, namespace["bind_checks"], namespace["grading_targets"])
BUILDERS = survey_reference_builds.builders(bpy, scene)
NEAR_MISSES = survey_reference_builds.near_misses(bpy, scene)
IMAGE = {"role": "activity", "tool": "angelo_view", "status": "completed",
         "result": {"views": [{"bytes": 2048, "camera": {"source": answers.SCENE_CAMERA}}]}}
REFUSAL = {"role": "assistant", "text": "I cannot see the scene."}
# The task receipt's path when that view reached the model (eval_answers.image_delivery): every scripted turn has it.
DELIVERED = {"edits": 0, "images_delivered": 1, "images": [[2, 0, {"view:SOLID:SCENE_CAMERA": 1}]],
             "image_delivery": "confirmed"}
WRONG = {"yes_no": lambda value: "no" if value == "yes" else "yes", "counts": lambda value: [value[0] + 1] + value[1:],
         "names": lambda value: value[1:], "render": lambda value: [value[0], value[1] + 1, value[2]],
         "shortcut": lambda value: "ctrl+r"}
SPOKEN = {"yes_no": str, "counts": lambda value: " ".join(map(str, value)), "names": ", ".join,
          "render": lambda value: "%s %dx%d" % tuple(value), "shortcut": lambda value: value.title()}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def driver_grade(task, digest):
    """``Trial.grade`` up to its call of ``LiveGrading.after``: (passed, host_check)."""
    if task["kind"] != "read_only" and not task["checks"]:
        return None, {"ungraded": True}
    if task["kind"] == "read_only":
        unchanged = namespace["scene_digest"](scene) == digest
        return unchanged, {"scene_unchanged": unchanged}
    if task.get("targets"):
        objects = {alias: scene.objects.get(name) for alias, name in live.task_targets(task).items()}
        if not all(objects.values()):
            return False, {"success": False, "status": "fail", "reason": "object missing"}
        context = VerificationContext(objects, list(scene.objects))
        checks = namespace["bind_checks"](task["checks"], objects)
    else:
        obj = scene.objects.get(live.EVAL_OBJECT)
        if obj is None:
            return False, {"success": False, "status": "fail", "reason": "object missing"}
        context = VerificationContext(namespace["grading_targets"](scene, obj), list(scene.objects))
        checks = namespace["bind_checks"](task["checks"], obj)
    result = verify(context, checks)
    return bool(result["success"]), result


def turn(task, act=None, rows=lambda truth: []):
    """One task as the trial driver runs it: before, the turn itself (``act``), grading and completion."""
    record = {"task": task["id"], "kind": task["kind"], "status": "completed", "receipt": {"path": dict(DELIVERED)}}
    GRADING.before(bpy, scene, task, record)
    digest = namespace["scene_digest"](scene)
    truth = GRADING.turn.get("truth")
    if act is not None:
        act()
        bpy.context.view_layer.update()
    passed, record["host_check"] = driver_grade(task, digest)
    record["host_check_passed"] = GRADING.after(bpy, scene, task, record, passed, rows(truth))
    trial["tasks"].append(record)
    GRADING.complete(bpy, scene, task, record)
    check(task["id"] + "_grading_raised_nothing", "grading_error" not in record)
    report["tasks"].setdefault(task["id"], []).append(record["grade"])
    return record, truth


def edit(task):
    idle, _ = turn(task)
    check(task["id"] + "_fails_before_its_edit", idle["host_check_passed"] is False and idle["grade"] == "failed")
    for label, build in NEAR_MISSES.get(task["id"], {}).items():   # Close to the request, and not it.
        undo = []
        near, _ = turn(task, lambda: undo.append(build()))
        report["near_misses"]["%s: %s" % (task["id"], label)] = [entry["details"] for entry in
                                                                 near["host_check"]["results"]]
        check("%s_fails_on_%s" % (task["id"], label.replace(" ", "_")),
              near["host_check_passed"] is False and near["grade"] == "failed")
        undo[0]()
        bpy.context.view_layer.update()
    done, _ = turn(task, BUILDERS[task["id"]])
    if done["host_check_passed"] is not True:
        report["failures_after_edit"][task["id"]] = done["host_check"]
    check(task["id"] + "_passes_after_its_edit", done["host_check_passed"] is True and done["grade"] == "passed")
    if task.get("result_checks"):   # The measured values of every result check, for the receipt.
        report["measured"][task["id"]] = {"before": [entry["details"] for entry in idle["host_check"]["results"]],
                                          "after": [entry["details"] for entry in done["host_check"]["results"]]}
    if task["id"] == "timeline":   # Asked again when it already holds: nothing a check could attribute to the turn.
        again, _ = turn(task)
        check("an_edit_whose_checks_already_hold_is_ungraded",
              again["host_check_passed"] is None and again["grade"] == "ungraded" and "vacuous" in again["host_check"])


def scene_state():
    camera = scene.camera.data
    return (sorted(obj.name for obj in scene.objects), scene.render.resolution_x, scene.render.resolution_y,
            round(camera.lens, 6), round(camera.shift_x, 6), round(camera.shift_y, 6), round(camera.ortho_scale, 6))


def reply(task, value):
    form = task["answer"]["form"]
    line = "%s: %s" % (answers.FORMS[form][0], SPOKEN[form](value))
    return {"role": "assistant", "text": "Here is what I found.\n" + line}


def question(task, expected=None, partner=None):
    """Empty, wrong, right and (visual) unobserved answers; returns the passing record.

    ``partner`` is the other variant of a yes/no question: it is asked first, answered right, and must stay
    held until ``task`` is graded beside it.
    """
    spec, name, state = task["answer"], task["id"], scene_state()
    visual = [IMAGE] if spec.get("evidence") else []
    outcomes = {}
    for label, rows in (("empty", lambda truth: visual + [{"role": "assistant", "text": ""}]),
                        ("no_answer", lambda truth: visual + [REFUSAL]),
                        ("wrong", lambda truth: visual + [reply(task, WRONG[spec["form"]](truth["accept"][0]))]),
                        ("unsupported", lambda truth: [reply(task, truth["accept"][0])]),
                        ("right", lambda truth: visual + [reply(task, truth["accept"][0])])):
        if label == "unsupported" and not visual:
            continue
        GRADING.pairs.clear()   # Each probe is its own run of the question.
        if label == "right" and partner is not None:
            held, _ = turn(partner, rows=lambda truth: visual + [reply(partner, truth["accept"][0])])
            check(name + "_other_variant_alone_is_held_ungraded", (
                held["host_check_passed"], held["grade"], held["host_check"]["answer"].get("reason")) == (
                None, "ungraded", eval_grading_live.UNPAIRED))
        record, truth = turn(task, rows=rows)
        outcomes[label] = (record["host_check"]["answer"]["outcome"], record["host_check_passed"], record["grade"])
        if label == "right" and partner is not None:
            check(name + "_other_variant_passes_beside_it", (
                held["host_check"]["answer"]["outcome"], held["host_check_passed"], held["grade"]) == (
                "right", True, "passed"))
        check("%s_fixture_is_undone_after_%s" % (name, label), scene_state() == state)
    report["truths"][name] = truth
    wanted = {"empty": ("empty", False, "failed"), "no_answer": ("no_answer", False, "failed"),
              "wrong": ("wrong", False, "failed"), "right": ("right", True, "passed")}
    if visual:
        wanted["unsupported"] = ("unsupported", False, "failed")
    check(name + "_only_the_right_supported_answer_passes", outcomes == wanted)
    if expected is not None:
        check(name + "_ground_truth_is_what_the_scene_shows", truth["accept"][0] == expected)
    return record


def camera_projection_matches_blender():
    corners = survey_truth.box_corners(bpy, scene.objects[live.EVAL_OBJECT])
    mine = [survey_truth.project(bpy, scene, scene.camera, corner) for corner in corners]
    theirs = [tuple(world_to_camera_view(scene, scene.camera, corner)) for corner in corners]
    return all(abs(a - b) < 1e-5 for first, second in zip(mine, theirs) for a, b in zip(first, second))


def orthographic_camera_states():
    """The camera fixture on an orthographic camera: both states, then everything as it was."""
    state, data = scene_state(), scene.camera.data
    data.type, truths = "ORTHO", []
    for wanted in survey_fixtures.CAMERA_STATES:
        fixture = {"kind": "camera_frame", "object": live.EVAL_OBJECT, "state": wanted}
        _, restore = survey_fixtures.apply(bpy, scene, fixture)
        truths.append(survey_truth.truth(bpy, scene, {"kind": "camera_frame", "object": live.EVAL_OBJECT})["accept"][0])
        restore()
    data.type = "PERSP"
    return truths == ["yes", "no"] and scene_state() == state


def run_checks():
    graded = [task for task in TASKS if eval_grading.basis(task) in ("checks", "result_checks")]
    check("every_graded_task_has_a_builder", sorted(BUILDERS) == sorted(task["id"] for task in graded))
    check("near_misses_are_of_graded_tasks", set(NEAR_MISSES) <= set(BUILDERS))
    check("no_task_is_left_without_a_check_or_a_reason", eval_grading.unguarded(TASKS) == [])
    expected = {"scene_overview": ["Brass Marker", "Camera", "Cube", "Light"], "mesh_stats": [12, 20, 10],
                "how_to_bevel": "ctrl+b", "render_info": ["EEVEE", 1600, 900], "view_check": "yes",
                "view_check_cropped": "no"}
    passing, variants = {}, {}
    for task in TASKS:   # The two variants of a yes/no question name the same pair.
        variants.setdefault((task.get("answer") or {}).get("pair") or task["id"], []).append(task)
    for task in TASKS:
        basis = eval_grading.basis(task)
        if basis in ("checks", "result_checks"):
            edit(task)
        elif basis == "ungraded":
            record, _ = turn(task)
            check(task["id"] + "_is_ungraded_with_its_reason", record["host_check_passed"] is None
                  and record["grade"] == "ungraded"
                  and record["host_check"] == {"ungraded": True, "reason": task["ungraded"]})
        else:
            check(task["id"] + "_is_graded_on_its_answer", basis == "answer")
            if task["id"] == "view_check":
                check("camera_projection_matches_blender", camera_projection_matches_blender())
            others = [other for other in variants[task["answer"].get("pair") or task["id"]] if other is not task]
            passing[task["id"]] = question(task, expected[task["id"]], others[0] if others else None)
            if task["id"] == "view_check_cropped":
                check("orthographic_camera_states", orthographic_camera_states())
                # The same answer to both variants: the first one's pass is withdrawn too.
                GRADING.pairs.clear()
                first, _ = turn(TASKS[[t["id"] for t in TASKS].index("view_check")],
                                rows=lambda truth: [IMAGE, reply(task, "yes")])
                second, _ = turn(task, rows=lambda truth: [IMAGE, reply(task, "yes")])
                check("a_constant_answer_fails_both_variants", [
                    (r["host_check"]["answer"]["outcome"], r["host_check_passed"], r["grade"]) for r in (first, second)
                ] == [("constant", False, "failed")] * 2)
    check("every_question_was_asked", sorted(passing) == sorted(expected))
    totals = trial["grading"]["totals"]
    check("totals_count_every_run",
          totals["total"] == len(trial["tasks"]) == sum(totals[state] for state in eval_grading.STATES))
    check("the_trial_records_its_contract_and_survey_version", (
        trial["grading"]["contract"], trial["grading"]["survey_version"], trial["grading"]["tasks_digest"]) == (
        eval_grading.CONTRACT, survey.VERSION, eval_grading.tasks_digest(TASKS)))
    report.update(graded_tasks=len(graded), survey_tasks=len(TASKS), totals=totals, success=True)


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "survey-tasks.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_SURVEY_TASKS", json.dumps({key: report.get(key) for key in ("success", "graded_tasks", "paid_requests")}),
      flush=True)
assert report.get("success"), report.get("error")
