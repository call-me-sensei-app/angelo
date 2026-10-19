# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider-free dry run of the request survey's grading contract in a disposable GUI process.

Runs the real trial driver (``eval_live_agent_session.py``) on a short list of
survey tasks against a scripted runtime that stands in for the model: no
process, network, account or billing. The real controller, ``angelo_view``,
typed programs, receipts, fixtures, ground truth and grading all run, so this
proves on real chat rows what the host tests prove on scripted ones:

* an answer line that wraps the answer ("12 vertices, 20 edges...", "no, it is
  cut off") is read and recorded as read leniently;
* a question passes only on the right answer, and a visual one only when an
  image through the scene camera was captured before the answer line was last
  stated (the scripted model looks with ``angelo_view`` and then answers, in one
  rewritten reply row as the Claude runtime writes it and in separate rows as
  Codex does; or it answers without looking, answers first and looks after,
  looks from the front instead, or says nothing);
* the harness's fixtures reach the model's observation and are undone
  afterwards (the probe object, the output resolution, the camera's lens and
  shift);
* an edit graded by a result check passes through a typed program, and an edit
  the survey declares ungraded is recorded as ungraded with its reason;
* the gate sets' cube question (``eval_gate``) is answered from the turn
  context the driver sends (the observation of gate runs), on the line its
  prompt asks for: the context states where the harness moved the cube for
  the turn, that is the harness's truth, and the cube is back afterwards.

Run from the repository root with new temporary directories::

    ANGELO_DISABLE_DEFAULT_CHAT=1 BLENDER_USER_CONFIG=$D/config ANGELO_QA_DIR=$D \\
    ANGELO_STATE_DIR=$D/state ANGELO_CONNECTIONS_DIR=$D/accounts \\
    Angelo --factory-startup --disable-autoexec --python-exit-code 1 --no-window-focus \\
        --python tests/python/angelo_agent/test_blender_survey_grading_dryrun.py

``$D/survey-grading-dryrun.json`` reports named checks and ``success``.
"""

import copy
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI process"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
for name in ("ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "BLENDER_USER_CONFIG"):
    assert Path(os.environ[name]).resolve().is_relative_to(OUT.resolve()), name + " must be inside ANGELO_QA_DIR"
assert os.environ.get("ANGELO_DISABLE_DEFAULT_CHAT") == "1"

import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent import request_path  # noqa: E402

SURVEY = {task["id"]: task for task in survey.build_survey_tasks()}
GATE_CUBE_QUESTION = live.tasks_for("long", 7)[-1]   # The long gate set's "dimensions and location" question.
# A model round is never instant. The driver samples the chat every 50 ms and credits an image only when it
# was captured in an earlier sample than the reply's final text, so the scripted model takes this long to
# reply after a tool result (a real round takes seconds).
MODEL_ROUND_S = 1.0


def variant(task_id, new_id):
    """Another run of a question under its own id (and its own pair, so it is graded by itself)."""
    task = copy.deepcopy(SURVEY[task_id])
    task["id"] = new_id
    if task["answer"].get("pair"):
        task["answer"]["pair"] = new_id
    return task


def find(value, key):
    """First value for ``key`` anywhere in a JSON-like result."""
    if isinstance(value, dict):
        if key in value:
            return value[key]
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            found = find(item, key)
            if found is not None:
                return found
    return None


class ScriptedRuntime:
    """Deterministic provider stand-in: one scripted turn per task, in order. No process, no socket."""

    def __init__(self, context, settings):
        self.controller, self.warm = context, False

    def connect(self):
        self.warm = True
        self.controller.emit("native_session", {"id": "survey-grading-dry-run"})
        return "survey-grading-dry-run"

    def has_warm_runtime(self):
        return self.warm

    def bind_request(self, context):
        self.controller = context

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        pass

    def call(self, context, name, arguments):
        done, box = threading.Event(), {}
        context.request_tool("dry-" + uuid.uuid4().hex, name, arguments,
                             lambda result: (box.update(result=result), done.set()))
        if not done.wait(120):
            raise RuntimeError("Scripted tool call timed out: " + name)
        return box["result"]

    def say(self, context, text, item="dry-run"):
        """One reply; a later one under the same ``item`` rewrites the same chat row, as Claude's rounds do."""
        context.emit("delta", {"item": item, "text": text})
        context.emit("message_complete", {"item": item, "text": text})

    def program(self, context, operations, handle=None):
        write_set = []
        if handle:
            observed = self.call(context, "angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"]})
            write_set = [{"handle": handle, "revision": find(observed, "object_revision")}]
        return self.call(context, "angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": write_set,
                                                           "operations": operations, "expected_checks": []})

    def look(self, context, view):
        result = self.call(context, "angelo_view", {"views": [view]})
        VIEWS.append(result)
        # As a Claude round does: the next message carries the view's pictures, and the runtime accepting it is the
        # receipt's proof that they reached the model (eval_answers.image_delivery).
        request_path.claude_inputs(context, context.references.tool_images(result))
        context.record_provider_accepted()
        time.sleep(MODEL_ROUND_S)

    def run_request(self, context, native_id, text, observation=None):
        context.record_provider_turn()
        context.record_provider_accepted()
        task_id, script = SCRIPT[len(TURNS)]
        TURNS.append(task_id)
        assert text.startswith(TASKS[len(TURNS) - 1]["prompt"]), "the driver sent the tasks out of order"
        script(self, context, observation or {})
        return "completed"


def eval_handle(observation):
    return next((o["handle"] for o in observation.get("objects", []) if o["name"] == live.EVAL_OBJECT), None)


def op(kind, target, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def overview(runtime, context, observation):
    # A model that reads the observation the driver sent: the harness's probe object is in it.
    names = ", ".join(entry["name"] for entry in observation["objects"])
    runtime.say(context, "Your scene holds these objects.\nOBJECTS: " + names)


def create_cube(runtime, context, observation):
    runtime.say(context, "Creating the cube.")
    runtime.program(context, [op("create_primitive", None, primitive="CUBE", alias="eval", name=live.EVAL_OBJECT,
                                 size=2)])


def subdivide(runtime, context, observation):
    runtime.say(context, "Adding the subdivision surface modifier.")
    handle = eval_handle(observation)
    runtime.program(context, [op("modifier_add", handle, type="SUBSURF", name="Subdivision",
                                 settings={"levels": 2, "render_levels": 3})], handle)


def cube_box(runtime, context, observation):
    # A model that reads the cube's entry in its turn context and answers on the line the gate question asks for.
    entry = next(entry for entry in observation["objects"] if entry["name"] == live.EVAL_OBJECT)
    BOX_SEEN.append({key: entry.get(key) for key in ("dimensions", "location")})
    runtime.say(context, "It is where it was made.\nDIMENSIONS: %g %g %g; LOCATION: %g %g %g" % (
        *entry["dimensions"], *entry["location"]))


def answer(text, view=None, rows="one", first=False):
    """A scripted reply; with ``view`` the model looks before it (or, with ``first``, only after it)."""
    def script(runtime, context, observation):
        if view and not first:
            runtime.say(context, "Let me look.", "reply-1")
            runtime.look(context, view)
        runtime.say(context, text, "reply-1" if rows == "one" else "reply-2")
        if view and first:
            runtime.look(context, view)
    return script


# (task, the scripted turn): what a model might do, and what the harness must make of it.
SCRIPT = [
    (SURVEY["scene_overview"], overview),
    (SURVEY["create_cube"], create_cube),
    (GATE_CUBE_QUESTION, cube_box),
    (SURVEY["subdivision"], subdivide),
    (SURVEY["save_blend"], answer("I cannot save files from here.")),
    (SURVEY["mesh_stats"], answer("The base mesh is a cube.\nCOUNTS: 8 12 6")),
    (variant("mesh_stats", "mesh_stats_wrong"), answer("COUNTS: 9 12 6")),
    (variant("mesh_stats", "mesh_stats_in_words"), answer("COUNTS: 8 vertices, 12 edges and 6 faces")),
    (SURVEY["render_info"], answer("It renders with EEVEE.\nRENDER: EEVEE 1600x900")),
    (SURVEY["how_to_bevel"], answer("Select the top edges in Edit Mode, press the shortcut and drag.\n"
                                    "SHORTCUT: Ctrl+B")),
    (SURVEY["view_check"], answer("All of it is in view.\nANSWER: yes", view="SCENE_CAMERA")),
    (SURVEY["view_check_cropped"], answer("ANSWER: no, it is cut off on the right", view="SCENE_CAMERA", rows="two")),
    (variant("view_check", "view_check_unobserved"), answer("ANSWER: yes")),
    (variant("view_check", "view_check_answered_first"), answer("ANSWER: yes", view="SCENE_CAMERA", first=True)),
    (variant("view_check_cropped", "view_check_front"), answer("ANSWER: no", view="FRONT")),
    (variant("view_check", "view_check_silent"), lambda runtime, context, observation: None),
]
TASKS = [task for task, _ in SCRIPT]
SCRIPT = [(task["id"], script) for task, script in SCRIPT]
TURNS, VIEWS, HELD, BOX_SEEN = [], [], [], []


def isolated_store():
    """A fake profile-based Codex record, copied through the launcher's own isolation path."""
    source = OUT / "source-accounts"
    record = ConnectionStore(source).add("OPENAI", "SUBSCRIPTION", name="Dry-run scripted runtime")
    eval_live_agent.isolated_connection(source, record["id"], os.environ["ANGELO_CONNECTIONS_DIR"])
    return record["id"]


def camera_state():
    data = bpy.data.objects["Camera"].data
    return [round(data.lens, 5), round(data.shift_x, 5), round(data.shift_y, 5)]


CAMERA_AT_START = camera_state()
# Billing authorization is meaningless here: the scripted runtime replaces the provider class before any
# request, so nothing can reach an account.
os.environ.update(ANGELO_EVAL_AUTHORIZED="1", ANGELO_EVAL_CONNECTION_ID=isolated_store(), ANGELO_EVAL_TASK_SET="survey",
                  ANGELO_EVAL_TASK_TIMEOUT="120", ANGELO_EVAL_TRIAL="1", ANGELO_EVAL_OBSERVATION="turn-context")
live.tasks_for = lambda *args: TASKS   # The driver, its grading and this script share the short list.
from angelo_agent import controller as controller_module, ui  # noqa: E402
controller_module.CodexProvider = controller_module.ClaudeProvider = ScriptedRuntime
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
session = runpy.run_path(str(HERE / "eval_live_agent_session.py"), run_name="survey_grading_dry_run")
original_finish = session["Trial"].finish


def graded(record):
    return [answer_of(record).get("outcome"), record.get("host_check_passed"), record.get("grade")]


def answer_of(record):
    return ((record or {}).get("host_check") or {}).get("answer") or {}


def checked_finish(trial):
    report, tasks = trial.report, {task["task"]: task for task in trial.report["tasks"]}
    scene = trial.window.scene
    answers = {name: graded(record) for name, record in tasks.items() if record["kind"] == "read_only"}
    view, front, first, unobserved = (tasks.get(name, {}) for name in (
        "view_check", "view_check_front", "view_check_answered_first", "view_check_unobserved"))
    overview, bevel = answer_of(tasks.get("scene_overview")), answer_of(tasks.get("how_to_bevel"))
    checks = {
        "every_task_ran_in_order": (TURNS == [task["id"] for task in TASKS]
                                    and TURNS == [task["task"] for task in report["tasks"]]),
        "no_grading_error": not any("grading_error" in record for record in report["tasks"]) and "error" not in report,
        "answers_are_graded_as_scripted": answers == {
            "scene_overview": ["right", True, "passed"], "mesh_stats": ["right", True, "passed"],
            "question_05": ["right", True, "passed"],
            "mesh_stats_wrong": ["wrong", False, "failed"], "mesh_stats_in_words": ["right", True, "passed"],
            "render_info": ["right", True, "passed"],
            "how_to_bevel": ["right", True, "passed"], "view_check": ["right", True, "passed"],
            "view_check_cropped": ["right", True, "passed"], "view_check_unobserved": ["unsupported", False, "failed"],
            "view_check_answered_first": ["unsupported", False, "failed"],
            "view_check_front": ["unsupported", False, "failed"], "view_check_silent": ["empty", False, "failed"]},
        "the_probe_reached_the_observation_and_is_gone": (
            overview.get("expected") == [sorted(["Camera", "Cube", "Light", survey.PROBE_OBJECT])]
            and tasks["scene_overview"].get("harness_fixture") == ["fixture:probe_object"]
            and scene.objects.get(survey.PROBE_OBJECT) is None),
        "the_shortcut_truth_is_read_from_this_process_keymap": (
            "ctrl+b" in bevel.get("expected", ()) and bool(bevel["truth"]["this_process_keymap"])),
        "the_resolution_fixture_is_undone": (scene.render.resolution_x, scene.render.resolution_y) == (1920, 1080),
        "the_camera_fixture_is_undone": camera_state() == CAMERA_AT_START,
        "both_camera_truths_were_made": [answer_of(tasks.get(name)).get("expected") for name in (
            "view_check", "view_check_cropped")] == [["yes"], ["no"]],
        "a_real_view_through_the_camera_is_the_evidence": (
            view.get("evidence") == {"images": 1, "sources": {"SCENE_CAMERA": 1}, "reused": 0,
                                     "current_scene_camera": 1}
            and [answer_of(view).get(key) for key in ("supported", "image_captured", "images_before_answer")]
            == [True, True, 1]),
        # One reply row rewritten after the look (Claude) and a second row after it (Codex) are both in time.
        "evidence_is_timed_by_the_drivers_samples_not_row_order": all(
            answer_of(tasks.get(name)).get("evidence_timing") == "sampled while the turn ran"
            for name in ("view_check", "view_check_cropped", "view_check_answered_first")),
        "an_answer_given_before_looking_is_unsupported": [
            answer_of(first).get(key) for key in ("image_captured", "images_before_answer")] == [True, 0],
        "a_front_view_is_an_image_but_not_the_cameras": (
            front.get("evidence", {}).get("sources") == {"AXIS:FRONT": 1}
            and [answer_of(front).get(key) for key in ("image_captured", "supported")] == [True, False]),
        "an_unobserved_answer_had_no_image": answer_of(unobserved).get("image_captured") is False,
        "the_views_were_real_images": len(VIEWS) == 4 and all(
            entry.get("success") and entry["views"][0]["bytes"] > 1000 for entry in VIEWS),
        "looking_changed_nothing": all(tasks[name]["host_check"]["scene_unchanged"] for name in answers),
        "closed_checks_grade_the_cube": (tasks["create_cube"]["host_check_passed"], tasks["create_cube"]["grade"]) == (
            True, "passed"),
        "a_result_check_grades_the_typed_modifier": (
            tasks["subdivision"]["host_check_passed"] is True and tasks["subdivision"]["grade"] == "passed"
            and tasks["subdivision"]["host_check"]["results"][0]["details"]["observed"]["values"] == {
                "levels": 2, "render_levels": 3}),
        "a_declared_ungraded_edit_is_ungraded": (
            tasks["save_blend"]["host_check"] == {"ungraded": True, "reason": survey.FILE_RESULT}
            and tasks["save_blend"]["host_check_passed"] is None and tasks["save_blend"]["grade"] == "ungraded"),
        # Inconclusive is a third verdict in every report, never a pass (729fdc6c6c5).
        "totals_state_every_run": report["grading"]["totals"] == {"passed": 10, "failed": 5, "inconclusive": 0,
                                                                  "ungraded": 1, "skipped": 0, "total": 16,
                                                                  "not_run": 0},
        # An answer wrapped in words or followed by a clause is read, and recorded as read leniently.
        "answers_read_leniently_say_so": [
            [answer_of(tasks.get(name)).get(key) for key in ("parsed", "lenient")]
            for name in ("mesh_stats_in_words", "view_check_cropped", "view_check")] == [
            ["leniently", ["numbers named, read by their names"]], ["leniently", ["a clause after the answer"]],
            ["exactly", None]],
        # The gate's cube question: the turn context's own numbers, as the form asks, are the harness's truth.
        "the_gate_cube_question_is_read_from_the_turn_context": (
            BOX_SEEN == [{"dimensions": [2.0, 2.0, 2.0], "location": [0.25, -0.5, 0.75]}]
            and [answer_of(tasks.get("question_05")).get(key) for key in ("expected", "parsed", "stated")]
            == [[[2.0, 2.0, 2.0, 0.25, -0.5, 0.75]], "exactly", "2 2 2; LOCATION: 0.25 -0.5 0.75"]
            and tasks["question_05"].get("harness_fixture") == ["fixture:object_offset"]
            and [round(value, 6) for value in bpy.data.objects[live.EVAL_OBJECT].location] == [0.0, 0.0, 0.0]),
        "answers_read_are_counted": report["grading"].get("answers") == {"read": 12, "parsed_leniently": 2},
        # Right, with its observation, and still no pass until the other variant was graded beside it.
        "the_first_camera_variant_was_held_until_the_second": HELD == [[None, "ungraded"]],
        "the_trial_records_the_survey_version": (
            report["grading"]["survey_version"], report["grading"]["task_set"]) == (survey.VERSION, "survey"),
    }
    (OUT / "survey-grading-dryrun.json").write_text(json.dumps({
        "checks": checks, "success": all(checks.values()), "answers": answers, "grading": report["grading"],
        "paid_requests": 0}, indent=2))
    print("ANGELO_SURVEY_GRADING_DRYRUN", json.dumps({"success": all(checks.values()), "checks": checks}), flush=True)
    return original_finish(trial)


def watched_complete(trial, status, reason=""):
    """The first camera variant as it stood when its own turn ended, before the second was asked."""
    record = trial.record
    result = original_complete(trial, status, reason)
    if record["task"] == "view_check":
        HELD.append([record.get("host_check_passed"), record.get("grade")])
    return result


original_complete = session["Trial"].complete
session["Trial"].complete = watched_complete
session["Trial"].finish = checked_finish
