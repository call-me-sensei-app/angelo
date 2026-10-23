# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-app driver for one trial of ``eval_live_agent.py``. BILLED: never run it directly.

The launcher starts a disposable ``--factory-startup`` GUI Angelo process with isolated config, state and a
connection store holding only the one selected connection, then runs this script. It refuses to start without the
launcher's authorization environment. It drives the real application: ``ui`` controller, UI pump timer, approval and
commit operators and checkpoint policy.

Per task it sends one prompt as the chat editor does: with the chat editor's own turn context when the launcher
passes ``--observation turn-context``, otherwise (the default, for comparison with earlier runs) with the
observation those runs sent, ``angelo_inspect_scene``'s first 32 objects. It answers what still asks
(``eval_approval``) as a test-only tester (approve only ``eval_live_tasks.HARNESS_APPROVABLE`` through the
``angelo.approve`` operator, decline everything else), waits for the turn to end, accepts a staged preview through
the human UI continuation when the task asks for one, then grades the scene with the closed host checks.
``eval_grading_live`` completes the grading contract (``eval_grading``): a question's answer check against ground
truth the harness holds, an edit's result checks, and ``grade``, which is ``passed`` only when a check read the
result (an unchecked answer or an edit no check can read is ``ungraded``, never passed). Timings come from the
request's own local performance receipt; no result is taken from the model's claims. Each record also keeps the
turn's activity counts (``survey_report.turn_activity``): tool names, capability lookups and rejected plans, never
arguments or text. Scenario task sets (``scenario_specs``) add reference attachments, their own checks and approval
rule, and an end-of-session save and Undo probe (``scenario_session``). Diagnostic runs (``eval_diagnostics``) turn
on debug thinking and may ask one question after the last grade (``eval_ask_why``).
"""

import copy
import json
import os
from pathlib import Path
import sys
import time
import traceback

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use the launcher's disposable GUI process"
if os.environ.get("ANGELO_EVAL_AUTHORIZED") != "1" or not os.environ.get("ANGELO_EVAL_CONNECTION_ID"):
    raise SystemExit("eval_live_agent_session.py runs only under eval_live_agent.py with --authorize-billed-runs")
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
import eval_returning_user as returning_user  # noqa: E402  (--new-chat-per-task, --request-log)
returning_user.in_app(OUT)
CONNECTION_ID = os.environ["ANGELO_EVAL_CONNECTION_ID"]
# --team-mode: the signed-in team's connection of this mode; its id is known once the sign-in is restored.
TEAM_MODE = os.environ.get("ANGELO_EVAL_TEAM_MODE", "")
TRIAL, TASK_TIMEOUT = int(os.environ.get("ANGELO_EVAL_TRIAL", "1")), float(os.environ.get("ANGELO_EVAL_TASK_TIMEOUT", "300"))
TASK_SET = os.environ.get("ANGELO_EVAL_TASK_SET", "default")
LONG_TASKS = int(os.environ.get("ANGELO_EVAL_LONG_TASKS", "40"))
# legacy: the observation earlier runs sent; turn-context: the chat editor's own path.
OBSERVATION = os.environ.get("ANGELO_EVAL_OBSERVATION", "legacy")
CONTEXT_REBASE_TOKENS = int(os.environ.get("ANGELO_EVAL_CONTEXT_REBASE_TOKENS", "0"))
AFTER_FAILURE = os.environ.get("ANGELO_EVAL_AFTER_FAILURE", "skip")
# --cache-ttl: the prompt-cache TTL of the chat's new Claude sessions ("" keeps the product's choice).
CACHE_TTL = os.environ.get("ANGELO_EVAL_CACHE_TTL", "")
# --approve-delegation: the one model a read-only consultation on this connection may use.
DELEGATION_MODEL = os.environ.get("ANGELO_EVAL_APPROVE_DELEGATION", "")
# --trusted-scripts: the chat's Trusted scripts switch, on as an artist turns it on (scene Python without a review).
TRUSTED_SCRIPTS = os.environ.get("ANGELO_EVAL_TRUSTED_SCRIPTS") == "1"
# Scenario sets only: --python-approvals (approve or decline reviewed Python), --demo-record,
# --stage-copies (a copy of the scene after every turn) and --render-device (the Cycles device preference).
PYTHON_APPROVALS = os.environ.get("ANGELO_EVAL_PYTHON_APPROVALS", "approve")
DEMO_RECORD = os.environ.get("ANGELO_EVAL_DEMO_RECORD") == "1"
STAGE_COPIES = os.environ.get("ANGELO_EVAL_STAGE_COPIES") == "1"
RENDER_DEVICE = os.environ.get("ANGELO_EVAL_RENDER_DEVICE", "")
STOP_GRACE = 30.
# An API-key connection loads its key from the OS key store after startup; a team connection also restores
# its sign-in, reads providers/available and fetches (or reuses) the coding agents' grant key.
KEY_WAIT, TEAM_WAIT = 30., 90.

bpy.context.preferences.view.show_splash = False
# Keep Blender's own temporary files (quit.blend recovery) in this trial's directory.
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402
import bench_gpu, eval_approval  # noqa: E402,E401  (the GPU VMs' Cycles preference; approval)
import eval_abnormal_end as end, eval_diagnostics, eval_warm_up  # noqa: E402,E401  (stops; diagnostics; warm-up)
import eval_grading_live  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
import scenario_session  # noqa: E402
import survey_report  # noqa: E402
from eval_receipts import (ProgressWatch, attempts_since, begin_opening, opening_wait,  # noqa: E402
                           receipt_marker, receipt_since, summarize_receipt, task_extras, warm_up_cursor)


def scene_digest(scene):
    """Names, types and transforms: a read-only turn must leave these unchanged."""
    return sorted((obj.name, obj.type, tuple(round(v, 6) for v in obj.location),
                   tuple(round(v, 6) for v in obj.dimensions)) for obj in scene.objects)


def bind_checks(checks, obj):
    """Concrete checks for the agent's actual objects; ``$bevel`` names its BEVEL modifier.

    ``obj`` is the eval object, or a dict of check alias -> object for a task
    graded on several objects; each ``$bevel`` binds to its own target's bevel.
    """
    targets = obj if isinstance(obj, dict) else {"$eval": obj}
    bound = copy.deepcopy(checks)
    for check in bound:
        if check.get("name") == "$bevel":
            target = targets.get(check.get("target", "$eval"))
            bevel = next((m.name for m in target.modifiers if m.type == "BEVEL"), None) if target else None
            check["name"] = bevel or "missing bevel modifier"
    return bound


def grading_targets(scene, obj):
    """The aliases task checks name: the eval object, the scene and the coverage set's other objects."""
    return {"$eval": obj, "$scene": scene, "$copy": scene.objects.get(live.COPY_OBJECT),
            "$rig": scene.objects.get(live.RIG_OBJECT), "$sketch": scene.objects.get(live.SKETCH_OBJECT),
            "$text": scene.objects.get(live.TEXT_OBJECT), "$strokes": scene.objects.get(live.STROKES_OBJECT),
            "$hair": scene.objects.get(live.HAIR_OBJECT)}


def smooth_shaded(obj):
    """Smooth faces on the base mesh, or a verified face smooth-shading node graph."""
    if obj.type == "MESH" and obj.data.polygons and all(face.use_smooth for face in obj.data.polygons):
        return True
    for modifier in obj.modifiers:
        if modifier.type == "NODES":
            check = {"kind": "smooth_shading_graph", "target": "$eval", "modifier": modifier.name, "expected": True}
            if verify(VerificationContext({"$eval": obj}, list(obj.users_scene[0].objects)), [check])["success"]:
                return True
    return False


def stop_playback(window):
    """Reset mode only: animation playback started by outside input would move the frame mid-task."""
    if window.screen.is_animation_playing:
        with bpy.context.temp_override(window=window):
            bpy.ops.screen.animation_cancel(restore_frame=False)


class Trial:
    def __init__(self):
        self.tasks = live.tasks_for(TASK_SET, LONG_TASKS)
        self.index, self.phase, self.deadline = 0, "boot", 0.
        self.controller = self.window = self.record = None
        self.decided = set()
        self.warm_ups = warm_up_cursor()  # This launch's billed warm-ups begin after it (task_extras).
        self.scenario = scenario_session.for_trial(TASK_SET, OUT, PYTHON_APPROVALS, DEMO_RECORD, STAGE_COPIES)
        self.report = {"trial": TRIAL, "connection_id": CONNECTION_ID, "binary": bpy.app.binary_path,
                       "version": bpy.app.version_string, "tasks": [], "success": False}
        self.grading = eval_grading_live.LiveGrading(TASK_SET, self.tasks, self.report, bind_checks, grading_targets)

    def write(self):
        (OUT / "trial.json").write_text(json.dumps(self.report, indent=2, default=str))

    def tick(self):
        try:
            end.refresh(self, OUT)   # A Stop the harness did not send; the in-flight snapshot a crash leaves.
            return (self.why.tick if self.phase == "why" else getattr(self, "on_" + self.phase))()  # why: eval_ask_why
        except Exception:
            self.report["error"] = traceback.format_exc()
            return self.finish()

    def on_boot(self):
        self.window = ui.main_window()
        if self.window is None:
            return .2
        records = ui.connections().records
        if TEAM_MODE:
            return self.on_team_boot(records)
        if list(records) != [CONNECTION_ID]:
            raise RuntimeError("The isolated connection store must hold exactly the selected connection")
        if records[CONNECTION_ID]["method"] == "API_KEY" and not ui.connection_ready(records[CONNECTION_ID]):
            self.key_deadline = getattr(self, "key_deadline", None) or time.monotonic() + KEY_WAIT
            if time.monotonic() < self.key_deadline:
                return .2
            self.report["aborted"] = "The API-key connection's saved key was not ready; nothing was sent"
            return self.finish()
        self.controller = ui.create_session(connection_id=CONNECTION_ID)
        if self.controller.state.get("connection_id") != CONNECTION_ID:
            raise RuntimeError("The chat did not bind the selected connection")
        self.report.update(mode=records[CONNECTION_ID]["mode"], **self.configure())
        if self.scenario is not None:
            self.report["scenario"] = self.scenario.begin(bpy, self.window)
            if RENDER_DEVICE:
                self.report["render_device"] = scenario_session.use_render_device(bpy, RENDER_DEVICE)
        self.phase = "send"
        return .1

    def configure(self):
        """Both boot paths: effort, model, approval, Trusted scripts, context, cache, GPU (bench_gpu) then opening."""
        options = {"effort": os.environ.get("ANGELO_EVAL_EFFORT", "low"), "fast": False, **eval_approval.options()}
        if os.environ.get("ANGELO_EVAL_MODEL"):
            options["model"] = os.environ["ANGELO_EVAL_MODEL"]
        self.controller.set_options(**options)
        approval = eval_approval.apply(ui, self.controller)  # The chat's actual mode, for the report.
        if TRUSTED_SCRIPTS:
            self.controller.set_trusted_scripts(True)
        self.controller.context_rebase_tokens = CONTEXT_REBASE_TOKENS
        if CACHE_TTL:
            self.controller.prompt_cache_ttl_override = CACHE_TTL
        self.controller.single_model_runs = True   # Measurements stay on the requested model: no refusal fallback.
        diagnostics = eval_diagnostics.configure(self.controller)   # Debug thinking before the chat's first launch.
        self.report["gpu_preference"], self.opening = bench_gpu.run_if_required(bpy), begin_opening(self.controller)
        return {"cache_ttl": CACHE_TTL or "product default", "trusted_scripts": bool(self.controller.trusted_scripts),
                "refusal_fallback": "disabled", "approve_delegation": DELEGATION_MODEL or None, **approval, **diagnostics}

    def on_team_boot(self, records):
        """Wait for the team sign-in to restore and its connection of TEAM_MODE to be usable, then bind it."""
        global CONNECTION_ID
        from angelo_agent import team_account_ui
        if records:
            raise RuntimeError("A team trial's profile must hold no stored connection")
        record = next((r for r in team_account_ui.records().values() if r["mode"] == TEAM_MODE), None)
        if record is None or not ui.connection_ready(record):
            self.key_deadline = getattr(self, "key_deadline", None) or time.monotonic() + TEAM_WAIT
            if time.monotonic() < self.key_deadline:
                return .2
            account = team_account_ui.account()
            view = account.view() if account is not None else None
            self.report["aborted"] = "The team connection was not ready; nothing was sent (%s)" % (
                team_account_ui.reason(record) if record else (view.status + " " + str(view.message)) if view
                else "no team account")
            return self.finish()
        CONNECTION_ID = record["id"]
        self.report["connection_id"] = CONNECTION_ID
        records = {CONNECTION_ID: record}
        self.controller = ui.create_session(connection_id=CONNECTION_ID)
        if self.controller.state.get("connection_id") != CONNECTION_ID:
            raise RuntimeError("The chat did not bind the team connection")
        self.report.update(mode=TEAM_MODE, route="angelo-gateway", **self.configure())
        if self.scenario is not None:
            self.report["scenario"] = self.scenario.begin(bpy, self.window)
        self.phase = "send"
        return .1

    def on_send(self):
        if (self.scenario is not None and not self.scenario.ready(finishing=self.index >= len(self.tasks))
                or opening_wait(self)):
            return .1
        if eval_warm_up.active():   # The trial's own boot and configuration, then one warm-up message instead.
            return eval_warm_up.send(self, ui, OBSERVATION, CONTEXT_REBASE_TOKENS, TASK_TIMEOUT)
        if self.index >= len(self.tasks):
            self.report["success"] = (not self.report.get("invalid")
                                      and all(t["status"] != "error" for t in self.report["tasks"]))
            if self.scenario is not None and self.report["tasks"]:
                self.scenario.finish(bpy, self.window, probe=not eval_diagnostics.asks_why(self))   # Probe later.
            return eval_diagnostics.after_trial(self, bpy)   # --ask-why-on-miss, else finish.
        task = self.tasks[self.index]
        scene = self.window.scene
        self.record = {"trial": TRIAL, "task": task["id"], "kind": task["kind"], "source": task["source"],
                       "approved": [], "declined": []}
        if AFTER_FAILURE == "reset":
            stop_playback(self.window)
            steps = live.reset_for_task(bpy, scene, task)
            if steps:
                self.record["harness_reset"] = steps
        fixture = live.prepare_fixture(bpy, scene, task)  # The harness makes it, never the agent.
        if fixture:
            self.record["harness_fixture"] = fixture
        if any(scene.objects.get(name) is None for name in live.required_objects(task)):
            return self.complete("skipped", reason="the object from an earlier task is missing")
        self.grading.before(bpy, scene, task, self.record)  # Fixtures, ground truth and the before-probe.
        # No eval task changes the frame: a moved frame or running playback is outside input.
        self.record["environment"] = {"frame_before": scene.frame_current,
                                      "input_isolated": bool(bpy.app.use_event_simulate)}
        self.digest = scene_digest(scene)
        returning_user.next_chat(self, ui, CONNECTION_ID)   # --new-chat-per-task (eval_returning_user)
        self.receipts = receipt_marker(self.controller)
        # The chat editor's send path (console.execute), without its text buffer.
        ui.prepare_controller()
        # prepare_controller applies the preference (off in a factory profile).
        self.controller.context_rebase_tokens = CONTEXT_REBASE_TOKENS
        self.offer_collaborators()
        self.message_index = len(self.controller.state["messages"])
        if self.scenario is not None:
            self.scenario.attach(self.controller, task, self.record)
        turn = None
        if OBSERVATION == "turn-context":
            from angelo_agent import turn_context
            turn = turn_context.prepare(ui.main_window(), self.controller.state["id"], message=task["prompt"])
            observation = turn.observation
        else:
            # The observation earlier benchmark runs sent (inspect_scene, first 32 objects).
            observation = BlenderExecutor(ui.main_window()).execute("angelo_inspect_scene", {})
            observation["objects"] = observation["objects"][:32]
            observation["truncated"] = observation["object_count"] > len(observation["objects"])
        self.sent, self.progress = (now := time.monotonic()), ProgressWatch(now, self.controller)
        self.first_row = len(self.controller.state["messages"])
        self.controller.send(task["prompt"], observation=observation,
                             recovery_evidence=turn.recovery_evidence if turn is not None else None)
        if turn is not None:
            turn.commit()  # Only a delivered observation is a planning boundary.
        self.deadline, self.phase = self.sent + TASK_TIMEOUT, "running"
        return .05

    def offer_collaborators(self):
        """Only the approved model on this connection, or none in the delegation set's other arm."""
        if DELEGATION_MODEL:
            offered = [dict(entry, models=[m for m in entry["models"] if m["id"] == DELEGATION_MODEL])
                       for entry in self.controller.collaborators if entry["connection_id"] == CONNECTION_ID]
            offered = [entry for entry in offered if entry["models"]]
            if not offered:
                raise RuntimeError("--approve-delegation %s is not in this connection's model catalog" % DELEGATION_MODEL)
            self.controller.collaborators = offered
        elif TASK_SET == "delegation":
            self.controller.collaborators = []

    def answer_approvals(self):
        for call in list(self.controller.pending.values()):
            if not call.approval or call.executing or call.authorized or call.id in self.decided:
                continue
            if self.controller._automatic_script(call):
                continue  # Trusted or always-allowed: the chat runs it itself (Controller.pump_tools), as for an artist.
            self.decided.add(call.id)
            delegation = (CONNECTION_ID, DELEGATION_MODEL) if DELEGATION_MODEL else None
            decision = (self.scenario.decide(call.name) if self.scenario is not None else
                        live.approval_decision(call.name, call.arguments, delegation=delegation))
            if decision == "approve":
                with bpy.context.temp_override(window=self.window):
                    result = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id,
                                                    session_id=self.controller.state["id"])
                    if 'CANCELLED' in result and call.id in self.controller.reviewed_calls:
                        # Reviewed Python takes two clicks: the first opens the script, the second runs it.
                        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id,
                                               session_id=self.controller.state["id"])
                self.record["approved"].append(call.name)
            else:
                self.controller.decline_tool(call.id)
                self.record["declined"].append(call.name)

    def on_running(self):
        self.answer_approvals()
        self.grading.observe(self.controller.state["messages"], self.message_index)
        if time.monotonic() > self.deadline:
            end.stop(self, "timeout")
            self.deadline, self.phase = time.monotonic() + STOP_GRACE, "stopping"
            return .05
        if not end.turn_ended(self.controller):
            return .05
        self.record.update(send_to_idle_ms=round((time.monotonic() - self.sent) * 1000, 3), **self.progress.record())
        self.record["receipt"] = receipt_since(self.controller, self.receipts)
        self.record.update(attempts_since(self.controller, self.receipts))
        self.record["activity"] = self.turn_activity()
        self.record["controller_status"] = self.controller.status
        if self.controller.connection_error or self.controller.setup_required:
            self.report["aborted"] = "Provider sign-in/setup failed; no further billed messages were sent"
            self.complete("error", reason=self.report["aborted"])
            self.index = len(self.tasks)
            return .05
        limit = limits.angelo_limit(self.controller.state["messages"][self.first_row:],
                                    (self.record["receipt"] or {}).get("usage"), (self.record["receipt"] or {}).get("counts"))
        if limit:  # The account's limit: the trial is invalid and the run stops after this task, graded as usual.
            self.report["invalid"] = limits.INVALID
            self.report["aborted"] = limits.STOPPED_MESSAGE % limit
            self.record["invalid"] = limits.INVALID
        if self.tasks[self.index]["kind"] == "preview":
            return self.start_accept()
        if self.scenario is not None and self.scenario.jobs_pending():
            # A render the turn started is part of its result: grade when Blender's job has ended.
            self.job_started = time.monotonic()
            self.deadline, self.phase = self.job_started + self.scenario.job_wait_s, "jobs"
            return .5
        return self.grade()

    def on_jobs(self):
        """A scenario turn's background job: wait for it (at most the scenario's ``JOB_WAIT_S``), then grade."""
        self.answer_approvals()
        pending = self.scenario.jobs_pending()
        if pending and time.monotonic() < self.deadline:
            return .5
        self.record["job_wait_ms"] = round((time.monotonic() - self.job_started) * 1000, 3)
        if pending:
            self.record["job_wait_timed_out"] = True
        return self.grade(note="a background job was still running after %g s" % self.scenario.job_wait_s
                          if pending else "")

    def turn_activity(self):
        """This turn's tool names, capability lookups and rejected plans (counts only, no arguments)."""
        return survey_report.turn_activity(self.controller.state["messages"][self.first_row:])

    def on_stopping(self):
        if end.turn_ended(self.controller) or time.monotonic() > self.deadline:
            self.record["receipt"] = receipt_since(self.controller, self.receipts)
            self.record["activity"] = self.turn_activity()
            return self.complete("timeout", reason="task exceeded %g s and was stopped" % TASK_TIMEOUT)
        return .05

    def start_accept(self):
        previews = self.controller.native_previews()
        if not previews:
            return self.grade(note="no retained preview to accept")
        descriptor = previews[-1]
        executor = BlenderExecutor(ui.main_window())
        executor.studio_context = self.controller  # As the accept operator does: the stage belongs to this chat.
        self.receipts = receipt_marker(self.controller)
        self.accept_started = time.monotonic()
        accepted = self.controller.accept_native_preview(descriptor["preview_id"], executor, ui.document_key(),
                                                         descriptor["preview_token"])
        self.record["accept"] = {"accepted": bool(accepted)}
        if not accepted:
            return self.grade(note="the preview acceptance was rejected")
        self.deadline, self.phase = time.monotonic() + TASK_TIMEOUT, "accepting"
        return .05

    def on_accepting(self):
        if time.monotonic() > self.deadline:
            end.stop(self, "accept_timeout")
            self.record["accept"]["timed_out"] = True
            return self.grade(note="the accepted preview did not commit in time")
        if not end.turn_ended(self.controller):
            return .05
        self.record["accept"].update(accept_to_commit_ms=round((time.monotonic() - self.accept_started) * 1000, 3),
                                     receipt=receipt_since(self.controller, self.receipts))
        return self.grade()

    def grade(self, note=""):
        task = self.tasks[self.index]
        scene = self.window.scene
        route = scenario_session.grading_route(task, self.scenario is not None)
        if route == "ungraded":
            # A survey request no closed check can grade: its receipt and approvals are the outcome.
            self.record["host_check"] = {"ungraded": True}
            passed = None
        elif route == "read_only":
            passed = scene_digest(scene) == self.digest
            self.record["host_check"] = {"scene_unchanged": passed}
            if task.get("reply_includes"):
                reply = " ".join(str(m.get("text", "")) for m in self.controller.state["messages"][self.message_index:]
                                 if m.get("role") == "assistant")
                missing = [name for name in task["reply_includes"] if name not in reply]
                self.record["host_check"]["reply_missing"] = missing
                passed = passed and not missing
        elif route == "scenario":
            passed = self.scenario.grade(bpy, scene, task, self.record, self.workspace(), self.window)
        else:
            if task.get("targets"):  # The delegation set grades the objects it names.
                objects = {alias: scene.objects.get(name) for alias, name in live.task_targets(task).items()}
                obj = objects.get("$eval")
                result = (verify(VerificationContext(objects, list(scene.objects)), bind_checks(task["checks"], objects))
                          if all(objects.values()) else {"success": False, "status": "fail", "reason": "object missing"})
            else:
                obj = scene.objects.get(live.EVAL_OBJECT)
                result = (verify(VerificationContext(grading_targets(scene, obj), list(scene.objects)),
                                 bind_checks(task["checks"], obj))
                          if obj is not None else {"success": False, "status": "fail", "reason": "object missing"})
            passed = bool(result["success"])
            if obj is not None and task.get("smooth_shading"):
                result["smooth_shaded"] = smooth_shaded(obj)
                passed = passed and result["smooth_shaded"]
            self.record["host_check"] = result
        passed = self.grading.after(bpy, scene, task, self.record, passed,
                                    self.controller.state["messages"][self.message_index:])
        self.record["host_check_passed"] = passed
        status = "completed" if (self.record.get("receipt") or {}).get("status") == "completed" else "failed"
        if self.record.get("invalid") == limits.INVALID:  # Its grade kept; invalid, never a failure; exit code 3.
            self.record.update(grade=eval_grading_live.grading.state(dict(self.record, status=status), task),
                               graded_status=status)
            return self.complete("invalid", reason=self.report["aborted"])
        return self.complete(status, reason=note)

    def workspace(self):
        """The chat's file workspace, where a background job publishes its output."""
        files = getattr(self.controller, "files", None)
        return str(files.root) if files is not None else None

    def complete(self, status, reason=""):
        if self.scenario is not None:
            self.scenario.stopped(bpy, self.window.scene, self.tasks[self.index], self.record, self.workspace(),
                                  self.window)
        environment = self.record.get("environment")
        if environment is not None:
            playing = bool(self.window.screen.is_animation_playing)
            environment.update(frame_after=self.window.scene.frame_current, playback=playing)
            environment["external_input_suspected"] = playing or environment["frame_after"] != environment["frame_before"]
            if environment["external_input_suspected"]:
                print("ANGELO_LIVE_EVAL_ENVIRONMENT", json.dumps({"trial": TRIAL, "task": self.record["task"],
                                                                  **environment}), flush=True)
        self.record.update(status=status, **({"reason": reason} if reason else {}))
        end.settle(self, status, OUT)
        self.record.update(summarize_receipt(self.record.get("receipt")), **task_extras(self))
        self.report["tasks"].append(self.record)
        self.grading.complete(bpy, self.window.scene, self.tasks[self.index], self.record)
        self.write()
        print("ANGELO_LIVE_EVAL_TASK", json.dumps({k: self.record.get(k) for k in
              ("trial", "task", "status", "grade", "host_check_passed", "total_ms", "runtime_start")}), flush=True)
        self.index = len(self.tasks) if status == "invalid" else self.index + 1  # A provider limit stops the run.
        self.phase = "send"
        return .1

    def finish(self):
        end.unfinished(self, OUT)   # Finished with a task in flight (a harness exception, an abort).
        self.write()
        print("ANGELO_LIVE_EVAL_TRIAL_DONE", json.dumps({"trial": TRIAL, "success": self.report["success"],
                                                         "grading": self.report["grading"]}), flush=True)
        bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=.5)
        return None


trial = Trial()
bpy.app.timers.register(trial.tick, first_interval=1.)
