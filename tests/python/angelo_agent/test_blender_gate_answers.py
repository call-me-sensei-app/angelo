# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dry run of the gate sets' grading at gate-set version 2, with no model and no billing.

Walks the forty-task long set (its opening is the default set's) on the factory
scene through the trial driver's own grading steps, as
``test_blender_survey_tasks.py`` walks the survey: ``LiveGrading.before``, the
driver's closed-check route (its helpers, extracted verbatim), ``after`` and
``complete``. Each edit's result is built with plain ``bpy`` from the values its
own checks name; each reply is a scripted row.

* Every edit fails before its result exists and passes after it: the edits and
  their checks are what version 1 recorded.
* Every question is asked with an empty reply, a refusal, a wrong answer, the
  right answer as the form asks and the right answer wrapped as a model might
  wrap it. Only the two right ones pass, the second counted as parsed
  leniently; the harness's truth is what the scene holds; the opening
  question's extra mesh is there for the turn and gone after it, and the cube
  is where the harness moved it for the turn and back afterwards.
* The Blender MCP side's route gives the same result: both runtimes' own
  ``run_task`` (Claude Code's and Codex's, in turn) run here on a stand-in
  stream, and the code they send over the add-on socket
  (``bench_mcp_grading``) is executed as the add-on would execute it.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/private/tmp/angelo-gate-answers-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import bench_mcp_codex  # noqa: E402
import bench_mcp_grading  # noqa: E402
import bench_mcp_trial  # noqa: E402
import eval_answers as answers  # noqa: E402
import eval_gate  # noqa: E402
import eval_grading  # noqa: E402
import eval_grading_live  # noqa: E402
import eval_live_tasks as live  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402

namespace = {"live": live, "copy": __import__("copy"), "bpy": bpy, "VerificationContext": VerificationContext,
             "verify": verify}
exec(bench_mcp_grading.session_helpers_source(), namespace)  # The trial driver's helpers, verbatim.
report = {"checks": {}, "paid_requests": 0, "truths": {}, "lenient": {}}
trial = {"tasks": []}
scene = bpy.context.window_manager.windows[0].scene
COUNT = live.LONG_SESSION_TASKS
TASKS = live.tasks_for("long", COUNT)
GRADING = eval_grading_live.LiveGrading("long", TASKS, trial, namespace["bind_checks"], namespace["grading_targets"])
FACTORY = ["Camera", "Cube", "Light"]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def said(text):
    return [{"role": "assistant", "text": text}]


def cube():
    return scene.objects.get(live.EVAL_OBJECT)


def build(task):
    """The scene as a correct answer to an edit leaves it, from the values the task's own checks name."""
    obj = cube()
    for item in task["checks"]:
        if item["kind"] == "object_exists" or (obj is None and task["id"] == "cube_creation"):
            mesh = bpy.data.meshes.new(live.EVAL_OBJECT)
            corners = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
            mesh.from_pydata(corners, [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4),
                                           (1, 5, 7, 3)])
            mesh.update()
            obj = bpy.data.objects.new(live.EVAL_OBJECT, mesh)
            scene.collection.objects.link(obj)
        elif item["kind"] == "transform":
            setattr(obj, item["property"], item["expected"])
        elif item["kind"] == "dimensions":
            obj.dimensions = item["expected"]
        elif item["kind"] == "modifier":
            modifier = next((m for m in obj.modifiers if m.type == item["type"]), None)
            modifier = modifier or obj.modifiers.new("Bevel", item["type"])
            for name, value in item["values"].items():
                setattr(modifier, name, value)
        elif item["kind"] == "material_values":
            material = bpy.data.materials.new(item["material"])
            if material.node_tree is None:
                material.use_nodes = True
            shader = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
            shader.inputs["Base Color"].default_value = item["base_color"]
            shader.inputs["Roughness"].default_value = item["roughness"]
            shader.inputs["Metallic"].default_value = item["metallic"]
            obj.data.materials.clear()
            obj.data.materials.append(material)
    bpy.context.view_layer.update()


def driver_grade(task, digest):
    """``Trial.grade`` up to its call of ``LiveGrading.after``: (passed, host_check)."""
    if task["kind"] == "read_only":
        unchanged = namespace["scene_digest"](scene) == digest
        return unchanged, {"scene_unchanged": unchanged}
    obj = cube()
    if obj is None:
        return False, {"success": False, "status": "fail", "reason": "object missing"}
    context = VerificationContext(namespace["grading_targets"](scene, obj), list(scene.objects))
    result = verify(context, namespace["bind_checks"](task["checks"], obj))
    return bool(result["success"]), result


def turn(task, act=None, rows=lambda truth: []):
    """One task as the trial driver runs it: before, the turn itself (``act``), grading and completion."""
    record = {"task": task["id"], "kind": task["kind"], "status": "completed"}
    GRADING.before(bpy, scene, task, record)
    digest, during = namespace["scene_digest"](scene), sorted(obj.name for obj in scene.objects)
    truth = GRADING.turn.get("truth")
    if act is not None:
        act()
    passed, record["host_check"] = driver_grade(task, digest)
    record["host_check_passed"] = GRADING.after(bpy, scene, task, record, passed, rows(truth))
    trial["tasks"].append(record)
    GRADING.complete(bpy, scene, task, record)
    check(task["id"] + "_grading_raised_nothing", "grading_error" not in record)
    return record, truth, during


class LocalClient:
    """The add-on's part of the MCP side: execute the driver's code and hand back its ``result`` as JSON."""

    def __init__(self):
        self.truth = self.during = None

    def settle(self):
        pass

    def execute(self, code):
        scope = {}
        exec(code, scope)
        result = json.loads(json.dumps(scope["result"], default=repr))
        if "bench_mcp_answers.setup(" in code:
            self.truth = result.get("truth")
        elif "'digest'" in code:
            self.during = result["objects"]
        return result


def mcp_side(index, task, reply):
    """One MCP-side question as its driver runs it: the real ``run_task`` of either runtime on a stand-in stream."""
    client, config = LocalClient(), {"task_timeout": 60, "task_set": "long", "long_tasks": COUNT}
    stream = SimpleNamespace(process=object(), thread_id="thread", spawned=0., unexpected_requests=[],
                             send=lambda *args: None)
    if index % 2:   # Codex: the turn's agent message, then its completion.
        completed = {"turn": {"status": "completed"}}
        turn = [(0.1, {"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "a",
                                                                       "text": reply}}}),
                (0.2, {"method": "turn/completed", "params": completed})]
        stream.collect = lambda deadline, name: (turn, completed, None)
        usage = SimpleNamespace(begin_turn=lambda *args: None, observe=lambda *args: None,
                                finish_turn=lambda *args: {}, thread_started=lambda *args: None)
        record = bench_mcp_codex.run_task(task, client, stream, usage, config, 1)
    else:           # Claude Code: its result line carries the reply.
        result = {"type": "result", "subtype": "success", "is_error": False, "result": reply}
        stream.collect = lambda deadline, name: ([(0.1, result)], result, None)
        record = bench_mcp_trial.run_task(task, client, stream, SimpleNamespace(turn=lambda result: {}), config, 1)
    return record, client.truth, client.during


def expected_box(index):
    """Dimensions and location the cycle's own edits asked for (the checks of the tasks before a question),
    the location moved by the harness's offset for the turn."""
    earlier = [item for task in TASKS[:index] for item in task["checks"]]
    size = [item["expected"] for item in earlier if item["kind"] == "dimensions"][-1]
    place = [item["expected"] for item in earlier if item["kind"] == "transform"][-1]
    return [float(value) for value in size] + [value + offset for value, offset in zip(place, eval_gate.CUBE_OFFSET)]


def question(index, task):
    """Empty, refusing, wrong, right and leniently read answers on both benchmark sides."""
    name, form = task["id"], task["answer"]["form"]
    label, before = answers.FORMS[form][0], sorted(obj.name for obj in scene.objects)
    placed = [tuple(obj.location) for obj in scene.objects]
    if form == "mesh_names":
        wanted = sorted(name for name in before if scene.objects[name].type == "MESH") + [eval_gate.PROBE_MESH]
        right = lambda truth: "%s: %s" % (label, ", ".join(truth["accept"][0]))
        wrong = lambda truth: "%s: %s" % (label, ", ".join(truth["accept"][0][1:]))   # The factory scene's answer.
        loose = lambda truth: "%s: %d (%s)" % (label, len(truth["accept"][0]), " and ".join(truth["accept"][0]))
    else:
        wanted = expected_box(index)
        line = "%s: %g %g %g; LOCATION: %g %g %g"
        right = lambda truth: line % (label, *truth["accept"][0])
        wrong = lambda truth: line % (label, *truth["accept"][0][:5], truth["accept"][0][5] + 1)
        loose = lambda truth: "%s: %g m x %g m x %g m\nLOCATION: (%g, %g, %g)" % (label, *truth["accept"][0])
    replies = {"empty": lambda truth: "", "no_answer": lambda truth: "I cannot see the scene.", "wrong": wrong,
               "right": lambda truth: "Here it is.\n" + right(truth), "lenient": loose}
    outcomes, mcp = {}, {}
    for kind, reply in replies.items():
        record, truth, during = turn(task, rows=lambda truth, reply=reply: said(reply(truth)))
        answer = record["host_check"]["answer"]
        outcomes[kind] = (answer["outcome"], answer.get("parsed"), record["host_check_passed"], record["grade"])
        check("%s_scene_is_as_before_after_%s" % (name, kind), sorted(obj.name for obj in scene.objects) == before
              and [tuple(obj.location) for obj in scene.objects] == placed)
        other, other_truth, objects = mcp_side(index + len(mcp), task, reply(truth))
        mcp[kind] = (other["host_check"]["answer"]["outcome"], other["host_check"]["answer"].get("parsed"),
                     other["host_check_passed"])
        check("%s_mcp_side_computes_the_same_truth_%s" % (name, kind), other_truth == truth and objects == during
              and "harness_fixture_errors" not in other and other["status"] == "completed")
    report["truths"][name], report["lenient"][name] = truth, record["host_check"]["answer"].get("lenient")
    check(name + "_only_right_answers_pass", outcomes == {
        "empty": ("empty", None, False, "failed"), "no_answer": ("no_answer", None, False, "failed"),
        "wrong": ("wrong", "exactly", False, "failed"), "right": ("right", "exactly", True, "passed"),
        "lenient": ("right", "leniently", True, "passed")})
    check(name + "_mcp_side_grades_alike", mcp == {kind: value[:3] for kind, value in outcomes.items()})
    check(name + "_truth_is_what_the_scene_holds", answers.matches(form, wanted, truth["accept"])
          if form == "box" else truth["accept"] == [sorted(wanted)])
    check(name + "_scene_is_as_before_after_the_mcp_side", sorted(obj.name for obj in scene.objects) == before
          and [tuple(obj.location) for obj in scene.objects] == placed)
    if form == "mesh_names":
        check("the_extra_mesh_is_in_the_scene_for_the_turn_only", (
            eval_gate.PROBE_MESH in during and eval_gate.PROBE_MESH not in before
            and bpy.data.meshes.get(eval_gate.PROBE_MESH) is None))
    else:
        back = [float(value) for value in tuple(cube().location)]
        check(name + "_asks_for_where_the_harness_moved_the_cube", (
            truth["accept"][0][3:] != back and record["harness_fixture"] == ["fixture:object_offset"]))


def run_checks():
    check("the_walk_is_the_current_gate_set", (
        eval_gate.tasks_version(TASKS), eval_grading.tasks_digest(TASKS)) == (
        eval_gate.VERSION, eval_grading.GATE_DIGESTS[eval_gate.VERSION]["long"]))
    check("the_scene_is_the_factory_one", sorted(obj.name for obj in scene.objects) == FACTORY)
    questions = 0
    for index, task in enumerate(TASKS):
        if task["kind"] == "read_only":
            question(index, task)
            questions += 1
            continue
        idle, _, _ = turn(task)
        check(task["id"] + "_fails_before_its_edit", idle["host_check_passed"] is False and idle["grade"] == "failed")
        done, _, _ = turn(task, lambda: build(task))
        check(task["id"] + "_passes_after_its_edit", done["host_check_passed"] is True and done["grade"] == "passed")
    check("every_question_was_asked", questions == 8 == sum(task["kind"] == "read_only" for task in TASKS))
    truths = [json.dumps(report["truths"][task["id"]]["accept"]) for task in TASKS[2:] if task["kind"] == "read_only"]
    check("no_two_cube_questions_have_the_same_answer", len(set(truths)) == len(truths) == 7)
    grading = trial["grading"]
    check("the_trial_states_its_gate_version", (grading["gate_version"], grading["tasks_digest"]) == (
        eval_gate.VERSION, eval_grading.GATE_DIGESTS[eval_gate.VERSION]["long"]))
    check("answers_read_and_parsed_leniently_are_counted", grading["answers"] == {
        "read": 3 * questions, "parsed_leniently": questions})
    check("totals_count_every_run", grading["totals"]["total"] == len(trial["tasks"]) == sum(
        grading["totals"][state] for state in eval_grading.STATES))
    report.update(tasks=len(TASKS), questions=questions, totals=grading["totals"], answers=grading["answers"],
                  success=True)


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "gate-answers.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_GATE_ANSWERS", json.dumps({key: report.get(key) for key in ("success", "tasks", "questions",
                                                                         "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
