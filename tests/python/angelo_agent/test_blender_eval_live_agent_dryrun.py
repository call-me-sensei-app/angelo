# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider-free dry run of the live-eval driver in a disposable GUI process.

Exercises ``eval_live_agent_session.py`` end to end against a scripted runtime
that stands in for Codex: no process, network, account or billing. The runtime
answers each task prompt with the typed tools a model would use, so the real
controller, UI pump, the product's default approval (Auto · bounded edits, so
typed programs never wait for the harness), commit operators, preview accept continuation,
receipts and host grading all run. It also asks once for reviewed Python, which
the harness must decline, and looks up one deferred schema, which each task's
activity counts must show. The opening question is the gate set's (``eval_gate``):
the scripted model names the meshes its observation lists, on the answer line
the prompt asks for, so the harness's extra mesh has to reach the observation
and be gone afterwards. Run from the repository root with new temporary
directories::

    ANGELO_DISABLE_DEFAULT_CHAT=1 BLENDER_USER_CONFIG=$D/config ANGELO_QA_DIR=$D \\
    ANGELO_STATE_DIR=$D/state ANGELO_CONNECTIONS_DIR=$D/accounts \\
    Angelo --factory-startup --disable-autoexec --python-exit-code 1 --no-window-focus --enable-event-simulate \\
        --python tests/python/angelo_agent/test_blender_eval_live_agent_dryrun.py

``$D/eval-dryrun.json`` reports named checks and ``success``. ``--enable-event-simulate``
(``run_native_suites.py`` passes it to every window suite) keeps the machine's
keyboard and mouse out: without it, outside input once started playback and a
scripted program timed out.
"""

import json
import os
from pathlib import Path
import runpy
import sys
import threading
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI process"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
for name in ("ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "BLENDER_USER_CONFIG"):
    assert Path(os.environ[name]).resolve().is_relative_to(OUT.resolve()), name + " must be inside ANGELO_QA_DIR"
assert os.environ.get("ANGELO_DISABLE_DEFAULT_CHAT") == "1"

import eval_gate  # noqa: E402
import eval_grading  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
from angelo_agent.action_recipes import expand_recipe  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402


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
    """Deterministic provider stand-in. It never starts a process or opens a socket."""

    def __init__(self, context, settings):
        self.controller, self.warm = context, False

    def connect(self):
        self.warm = True
        self.controller.emit("native_session", {"id": "dry-run-thread"})
        return "dry-run-thread"

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

    def say(self, context, text):
        context.emit("delta", {"item": "dry-run", "text": text})
        context.emit("message_complete", {"item": "dry-run", "text": text})

    def program(self, context, mode, operations, checks, handle=None):
        write_set = []
        if handle:
            observed = self.call(context, "angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"]})
            write_set = [{"handle": handle, "revision": find(observed, "object_revision")}]
        return self.call(context, "angelo_scene_program", {"mode": mode, "read_set": [], "write_set": write_set,
                                                           "operations": operations, "expected_checks": checks})

    def run_request(self, context, native_id, text, observation=None):
        context.record_provider_turn()
        context.record_provider_accepted()
        prompt = text.split("\n", 1)[0]
        handle = next((o["handle"] for o in (observation or {}).get("objects", []) if o["name"] == live.EVAL_OBJECT), None)
        op = lambda kind, target, **parameters: {"kind": kind, "target": target, "parameters": parameters}
        if prompt.startswith("How many"):
            meshes = sorted(o["name"] for o in (observation or {}).get("objects", []) if o.get("type") == "MESH")
            self.say(context, "%d mesh objects.\nMESHES: %s" % (len(meshes), ", ".join(meshes)))
        elif prompt.startswith("Create a cube"):
            self.say(context, "Creating the cube.")
            self.program(context, "APPLY", [op("create_primitive", None, primitive="CUBE", alias="eval",
                                               name=live.EVAL_OBJECT, size=2)], [])
        elif prompt.startswith("Scale"):
            self.say(context, "Scaling.")
            self.program(context, "APPLY", [op("transform", handle, scale=[2, .5, 1.5])], [], handle)
        elif prompt.startswith("Add a non-destructive bevel"):
            self.say(context, "Adding the bevel and smooth shading.")
            self.call(context, "angelo_execute_python", {"description": "Dry-run request the harness must decline",
                                                          "code": "result = len(bpy.data.objects)"})
            recipe = expand_recipe("bevel-and-shade", handle, {"width": .125, "segments": 3})
            self.program(context, "APPLY", recipe["operations"], recipe["expected_checks"], handle)
        elif prompt.startswith("Give every face"):
            self.say(context, "Assigning the material.")
            # A deferred-schema lookup round, as a model makes before a kind it has not used.
            self.call(context, "angelo_agent_capabilities", {"family": "ACTIONS", "names": ["material_principled"]})
            self.program(context, "APPLY", [op("material_principled", handle, name="QA Graded Material", inputs={
                "Base Color": [.25, .5, .75, 1], "Roughness": .3, "Metallic": .4})], [], handle)
        elif prompt.startswith("Stage a PREVIEW"):
            self.say(context, "Staging a preview for your review.")
            self.program(context, "PREVIEW", [op("transform", handle, location=live.PREVIEW_LOCATION)], [], handle)
        else:
            raise RuntimeError("The dry run has no script for: " + prompt)
        return "completed"


def isolated_store():
    """A fake profile-based Codex record, copied through the launcher's own isolation path."""
    source = OUT / "source-accounts"
    store = ConnectionStore(source)
    record = store.add("OPENAI", "SUBSCRIPTION", name="Dry-run scripted runtime")
    store.add("ANTHROPIC", "SUBSCRIPTION", name="Must not be copied")
    copied = eval_live_agent.isolated_connection(source, record["id"], os.environ["ANGELO_CONNECTIONS_DIR"])
    return record["id"], copied


connection_id, copied = isolated_store()
# Billing authorization is meaningless here: the scripted runtime replaces the
# provider class before any request, so nothing can reach an account.
os.environ.update(ANGELO_EVAL_AUTHORIZED="1", ANGELO_EVAL_CONNECTION_ID=connection_id,
                  ANGELO_EVAL_TASK_TIMEOUT="120", ANGELO_EVAL_TRIAL="1")
from angelo_agent import controller as controller_module, ui  # noqa: E402
controller_module.CodexProvider = controller_module.ClaudeProvider = ScriptedRuntime
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
session = runpy.run_path(str(HERE / "eval_live_agent_session.py"), run_name="eval_live_dry_run")
original_finish = session["Trial"].finish


def checked_finish(trial):
    report, tasks = trial.report, {task["task"]: task for task in trial.report["tasks"]}
    journeys = live.summarize_journeys(report["tasks"])
    question = (tasks.get("read_only_question", {}).get("host_check") or {}).get("answer") or {}
    checks = {
        "isolated_store_has_only_selected_connection": list(ConnectionStore(os.environ["ANGELO_CONNECTIONS_DIR"]).records) == [connection_id],
        "profile_points_at_source": copied["id"] == connection_id and copied["mode"] == "codex_subscription",
        "all_six_tasks_ran": [task["task"] for task in report["tasks"]] == [task["id"] for task in live.build_tasks()],
        "all_tasks_completed": all(task["status"] == "completed" for task in report["tasks"]),
        "all_host_checks_passed": all(task.get("host_check_passed") is True for task in report["tasks"]),
        # Gate set version 2: the question is read, on the truth the harness computed with its extra mesh in the scene.
        "the_question_passed_on_its_answer": (
            question.get("outcome"), question.get("parsed"), question.get("answered"), question.get("expected")) == (
            "right", "exactly", [eval_gate.PROBE_MESH, "Cube"], [[eval_gate.PROBE_MESH, "Cube"]]),
        "the_extra_mesh_was_the_harness_s_and_is_gone": (
            tasks["read_only_question"].get("harness_fixture") == ["fixture:probe_object"]
            and bpy.data.objects.get(eval_gate.PROBE_MESH) is None
            and bpy.data.meshes.get(eval_gate.PROBE_MESH) is None),
        "the_trial_states_its_gate_version_and_every_run_is_passed": (
            report["grading"].get("gate_version"), report["grading"].get("tasks_digest"),
            report["grading"].get("totals"), report["grading"].get("answers")) == (
            eval_gate.VERSION, eval_grading.GATE_DIGESTS[eval_gate.VERSION]["default"],
            {"passed": 6, "failed": 0, "inconclusive": 0, "ungraded": 0, "skipped": 0, "total": 6, "not_run": 0},
            {"read": 1, "parsed_leniently": 0}),
        "first_message_cold_rest_warm": [task.get("runtime_start") for task in report["tasks"]] == ["cold"] + ["warm"] * 5,
        "python_declined_not_run": tasks["bevel_and_shade"]["declined"] == ["angelo_execute_python"],
        "python_fallback_counted": tasks["bevel_and_shade"].get("python_fallbacks") == 1,
        # The product default, Auto · bounded edits: typed programs run without the harness's approval.
        "the_chat_is_in_the_default_auto_mode": report.get("approval") == "AUTO_BOUNDED",
        "typed_programs_ran_without_asking": all(task["approved"] == [] for task in report["tasks"][1:]),
        "edits_have_verified_change_time": all(tasks[name].get("first_verified_result_ms") is not None
                                               for name in ("cube_creation", "transform", "bevel_and_shade", "material")),
        "preview_accepted_and_committed": tasks["preview_accept"].get("accept", {}).get("accepted") is True
                                          and tasks["preview_accept"]["accept"].get("accept_to_commit_ms") is not None,
        "codex_rounds_not_inferred": all(task.get("model_rounds") is None for task in report["tasks"]),
        "activity_counts_tool_names_only": tasks["transform"].get("activity") == {
            "tools": {"angelo_scene_object": 1, "angelo_scene_program": 1}, "capability_lookups": 0,
            "lookup_rounds": 0, "plan_rejections": 0, "plans_refused_in_part": 0},
        "activity_counts_the_declined_script": tasks["bevel_and_shade"].get("activity", {}).get(
            "tools", {}).get("angelo_execute_python") == 1,
        "activity_counts_a_lookup_round": (tasks["material"].get("activity", {}).get("capability_lookups"),
                                           tasks["material"].get("activity", {}).get("lookup_rounds")) == (1, 1),
        "journeys_have_samples": all(journeys[name]["samples"] >= 1 for name in live.JOURNEYS),
    }
    (OUT / "eval-dryrun.json").write_text(json.dumps({"checks": checks, "success": all(checks.values()),
                                                      "journeys": journeys, "paid_requests": 0}, indent=2))
    print("ANGELO_EVAL_DRYRUN", json.dumps({"success": all(checks.values()), "checks": checks}), flush=True)
    return original_finish(trial)


session["Trial"].finish = checked_finish
