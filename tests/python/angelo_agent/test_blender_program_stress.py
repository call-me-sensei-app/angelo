# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled stress replay of recorded Claude typed plans through the real app.

The live-eval driver (``eval_live_agent_session.py``) runs unchanged: fresh
chat and request checkpoints, ``turn-context`` observations, the real UI pump,
the product's default approval (Auto · bounded edits: programs are approved in
the pump, through ``Controller.approve_tool``), the commit operators, staged
native programs, preview Accept and host grading. Only Claude Code is replaced: ``ReplayClaude`` keeps
``ClaudeProvider.run_request`` (plan admission, native calls, certified
completion) and answers each round with the plan Claude recorded for that
prompt (cube_creation verbatim from two failing recorded trials),
bound to the live handle and revision of the previous result. No process,
network or account is used.

Each cycle is one default 6-task trial in a new chat, back to back in one
process, with one timing mode:

* ``immediate``: the next prompt is sent on the tick the previous turn ends;
* ``harness``: the launcher's 0.1 s pause, as in billed runs;
* ``maintenance``: snapshot maintenance is requested in the same tick as each
  send, while the previous capture may still be ingesting;
* ``reset_during_send``: the scene reset runs in the same tick as the next
  cycle's first send;
* ``frame_step``: the timeline steps one frame right after each program's
  approval (direct ``frame_set`` or ``screen.frame_offset``, the Right Arrow's
  operator);
* ``playback``: ``screen.animation_play`` (Space's operator) starts animation
  playback right after each program's approval and ``animation_cancel`` stops
  it 0.25 s later.

The last two reproduce recorded ``interrupted`` failures: keyboard input
reached the trial window, the frame moved between approval and staging, and
the request ended with "The target changed after approval". The change happens
inside the approval itself (``Controller.approve_tool``), synchronously, so it
always lands between approval and staging: in Auto, the product default, the
program is approved and its staging starts in the same UI tick, and a
simulated key press would only be read after staging had begun (a different
moment, which the rebind budget of ``program_context`` is not meant to cover).
The checks then hold the fencing to its contract: every playback made the
program wait (``playback_waits``) and every frame step was rebound
(``frame_rebinds``), with no failure. Run from the
repository root through the runner (GUI, event simulation)::

    python3 tests/python/angelo_agent/run_native_suites.py --binary "$ANGELO" --gui \\
        --include test_blender_program_stress.py

``ANGELO_STRESS_TASK_SET=long`` runs the first ``ANGELO_STRESS_LONG_TASKS``
(default 17) tasks of the long set instead, with the recorded long-session
plans of ``program_stress_plans.py``: moves through ``angelo_set_transform`` by
name, ``o<n>`` handle (a recorded failure) and long handle, and
"Change the bevel modifier" decided from the message alone, as a recorded
rebased context did, including its recorded
``bevel-and-shade`` plan and the correction a refusal asks for.

Plans come in two shapes, alternating every six cycles so each timing mode
sees both: ``recorded`` (as Claude wrote them: replies, explicit
long-handle bindings) and ``toolset21`` (the integrated contract's defaults,
doc/angelo/agent_stack_integration.rst: no edit reply, no read/write set or
recipe revision, so the host infers every binding from the turn context, and
the resize first looks the cube up with ``verify`` left out, so the host must
give it the round that does the edit). ``ANGELO_STRESS_PLAN_SHAPE`` pins one
shape for a direct run. ``ANGELO_STRESS_CYCLES`` (default 100, 6 tasks each)
sets the size.
``ANGELO_STRESS_ADDON_ROOT`` loads the add-on from another checkout, e.g. the
pre-fix commit, to show the reproduction. ``program-stress.json`` reports every
task's outcome, the failure classes and the recovery counters.
"""

import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI process"
assert bpy.app.use_event_simulate, "Run with --enable-event-simulate: real input must not reach this window"
HERE = Path(__file__).resolve().parent
ADDON_ROOT = Path(os.environ.get("ANGELO_STRESS_ADDON_ROOT") or HERE.parents[2])
sys.path[:0] = [str(ADDON_ROOT / "scripts/addons_core"), str(HERE)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
for name in ("ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "BLENDER_USER_CONFIG"):
    assert Path(os.environ[name]).resolve().is_relative_to(OUT.resolve()), name + " must be inside ANGELO_QA_DIR"
assert os.environ.get("ANGELO_DISABLE_DEFAULT_CHAT") == "1"
CYCLES = int(os.environ.get("ANGELO_STRESS_CYCLES", "100"))
MODES = ("immediate", "harness", "maintenance", "reset_during_send", "frame_step", "playback")
SHAPES = {"both": ("recorded", "toolset21"), "recorded": ("recorded",), "toolset21": ("toolset21",)}[
    os.environ.get("ANGELO_STRESS_PLAN_SHAPE", "both")]
PLAYBACK_SECONDS = .25
TASK_SET = os.environ.get("ANGELO_STRESS_TASK_SET", "default")
LONG_TASKS = int(os.environ.get("ANGELO_STRESS_LONG_TASKS", "17"))

# The add-on first: the driver modules below put this checkout's add-on on sys.path too.
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent import controller as controller_module, providers, ui  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
from program_stress_plans import STOPPED, LongSession  # noqa: E402

# The cube_creation plans of the two failing trials, verbatim.
CUBE_PLANS = (
    {"reply": "Creating a 2 m cube named 'Eval Cube' at the world origin, with a dimensions check.",
     "operations": [{"tool": "angelo_scene_program", "arguments": {
         "mode": "APPLY", "read_set": [], "write_set": [], "operations": [{"kind": "create_primitive", "target": None,
             "parameters": {"primitive": "CUBE", "name": "Eval Cube", "size": 2, "location": [0, 0, 0],
                            "alias": "evalcube"}}],
         "expected_checks": [{"kind": "dimensions", "target": "$evalcube", "expected": [2, 2, 2], "tolerance": 0.0001}]}}]},
    {"reply": "Creating a 2 m cube named \"Eval Cube\" at the world origin.",
     "operations": [{"tool": "angelo_scene_program", "arguments": {
         "mode": "APPLY", "operations": [{"kind": "create_primitive", "target": None, "parameters": {
             "primitive": "CUBE", "name": "Eval Cube", "alias": "evalcube", "size": 2, "location": [0, 0, 0]}}],
         "expected_checks": [{"kind": "dimensions", "target": "$evalcube", "expected": [2, 2, 2], "tolerance": 0.0001,
                              "space": "WORLD"}]}}]},
)
PROGRAM_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})


class Replay:
    """The recorded plan for each prompt, bound to the live handle/revision of the last result."""

    def __init__(self):
        self.handle = self.revision = None
        self.cube_plans = 0
        self.pending = None  # toolset21: the edit a lookup round was for.
        self.long_session, self.last_prompt = LongSession(self, 0), ""

    def observe(self, result):
        objects = ((result or {}).get("live_revisions") or {}).get("objects") or ()
        for row in objects:
            if row.get("name") == live.EVAL_OBJECT and row.get("handle") and row.get("object_revision"):
                self.handle, self.revision = row["handle"], row["object_revision"]

    def program(self, mode, operations, checks=()):
        arguments = {"mode": mode, "write_set": [{"handle": self.handle, "revision": self.revision}],
                     "operations": operations}
        if checks:
            arguments["expected_checks"] = list(checks)
        return {"tool": "angelo_scene_program", "arguments": arguments}

    def recipe(self, recipe, parameters):
        return {"tool": "angelo_apply_recipe", "arguments": {"recipe": recipe, "target": self.handle,
                "revision": self.revision, "mode": "APPLY", "parameters": parameters}}

    def plan(self, text):
        prompt = text.split("\n", 1)[0]
        if prompt.startswith("Host confirms") and self.pending is not None:
            plan, self.pending = self.pending, None
            return plan
        if not prompt.startswith(("Host confirms", "The host rejected", STOPPED)):
            self.last_prompt = prompt
        long_plan = self.long_session.plan(text) if TASK_SET == "long" and self.handle is not None else None
        if long_plan is None and prompt.startswith(STOPPED):
            long_plan = self.long_session.plan(text)
        plan = self.recorded(prompt) if long_plan is None else long_plan
        return plan if STRESS.shape == "recorded" else self.toolset21(prompt, plan)

    def toolset21(self, prompt, plan):
        """The plan as the integrated contract lets a model write it (module docstring)."""
        if not plan["operations"]:
            return plan
        operations = []
        for operation in plan["operations"]:
            arguments = {key: value for key, value in operation["arguments"].items()
                         if key not in ("read_set", "write_set", "revision")}
            operations.append(dict(operation, arguments=arguments))
        plan = {"operations": operations}
        if prompt.startswith("Scale"):
            self.pending = plan
            return {"operations": [{"tool": "angelo_scene_object", "arguments": {
                "object": self.handle, "sections": ["TRANSFORM"], "limit": 10}}]}
        return plan

    def recorded(self, prompt):
        if prompt.startswith(("Host confirms", "The host rejected", STOPPED)):
            return {"reply": "Unexpected continuation in the stress replay.", "operations": []}
        if prompt.startswith("How many mesh objects"):
            return {"reply": "The scene has one mesh object, named \"Cube\".", "operations": []}
        if prompt.startswith("Create a cube"):
            self.handle = self.revision = None
            plan = CUBE_PLANS[self.cube_plans % len(CUBE_PLANS)]
            self.cube_plans += 1
            return json.loads(json.dumps(plan))
        if self.handle is None:
            raise RuntimeError("The replay has no live handle for: " + prompt)
        if prompt.startswith("Scale"):
            return {"reply": "Scaling \"Eval Cube\" to 4 x 1 x 3 m.", "operations": [self.program(
                "APPLY", [{"kind": "transform", "target": self.handle, "parameters": {"scale": [2, 0.5, 1.5]}}],
                [{"kind": "dimensions", "target": self.handle, "expected": [4, 1, 3], "tolerance": 0.0001,
                  "space": "WORLD"}])]}
        if prompt.startswith("Add a non-destructive bevel"):
            return {"reply": "Adding the bevel and smooth shading.",
                    "operations": [self.recipe("bevel-and-shade", {"width": 0.125, "segments": 3})]}
        if prompt.startswith("Give every face"):
            return {"reply": "Assigning the material.", "operations": [self.recipe("material-setup", {
                "name": "QA Graded Material", "base_color": [0.25, 0.5, 0.75, 1], "roughness": 0.3, "metallic": 0.4})]}
        if prompt.startswith("Stage a PREVIEW"):
            return {"reply": "Staging a preview that moves \"Eval Cube\" to (3, 0, 0).", "operations": [self.program(
                "PREVIEW", [{"kind": "transform", "target": self.handle, "parameters": {"location": [3, 0, 0]}}])]}
        raise RuntimeError("The replay has no recorded plan for: " + prompt)


REPLAY = Replay()


class ReplayClaude(providers.ClaudeProvider):
    """ClaudeProvider with recorded plans in place of the Claude Code process."""

    def connect(self):
        return self.controller.state.get("native_id")

    def has_warm_runtime(self):
        return getattr(self, "_warm", False)

    def list_models(self):
        return []

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        pass

    def _native_call(self, name, arguments, *, owner):
        result = super()._native_call(name, arguments, owner=owner)
        REPLAY.observe(result)
        return result

    def _turn(self, native_id, text, references=(), *, owner):
        context = owner.controller
        if not self._current(owner):
            return None
        if not getattr(self, "_warm", False):
            self._warm = True
            context.emit("native_session", {"id": "replay-" + uuid.uuid4().hex})
        providers._record_runtime_round(context, claude=True)
        plan = REPLAY.plan(text)
        self._emit(owner, "message_complete", {"item": "claude-assistant", "text": plan.get("reply", ""),
                                               "plan_operations": bool(plan["operations"])})
        return dict(plan, verify=plan.get("verify", False), reply=plan.get("reply", ""))


def isolated_store():
    source = OUT / "source-accounts"
    store = ConnectionStore(source)
    record = store.add("ANTHROPIC", "SUBSCRIPTION", name="Stress replay (no process)")
    eval_live_agent.isolated_connection(source, record["id"], os.environ["ANGELO_CONNECTIONS_DIR"])
    return record["id"]


connection_id = isolated_store()
# The replay asks the gate sets' questions as gate-set version 1 did (eval_gate): its plans and replies were
# recorded for those prompts, and it is about staging, approval and commit under load, not about answers.
_tasks_for = live.tasks_for
live.tasks_for = lambda task_set="default", count=live.LONG_SESSION_TASKS, version=None: _tasks_for(task_set, count, 1)
os.environ.update(ANGELO_EVAL_AUTHORIZED="1", ANGELO_EVAL_CONNECTION_ID=connection_id, ANGELO_EVAL_TASK_TIMEOUT="60",
                  ANGELO_EVAL_TRIAL="1", ANGELO_EVAL_OBSERVATION="turn-context", ANGELO_EVAL_TASK_SET=TASK_SET,
                  ANGELO_EVAL_LONG_TASKS=str(LONG_TASKS))
controller_module.CodexProvider = controller_module.ClaudeProvider = ReplayClaude
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
session = runpy.run_path(str(HERE / "eval_live_agent_session.py"), run_name="program_stress")
Trial = session["Trial"]


class Stress:
    def __init__(self):
        self.cycle, self.started = 0, time.monotonic()
        self.tasks, self.failures, self.injections = [], [], {}
        self.counts = {}
        self.injected_calls = set()
        self.error = None

    @property
    def mode(self):
        return MODES[self.cycle % len(MODES)]

    @property
    def shape(self):
        return SHAPES[(self.cycle // len(MODES)) % len(SHAPES)]

    def window(self):
        return ui.main_window()


STRESS = Stress()


def reset_scene():
    """Back to the factory default-cube scene: the eval cube, its data and the frame."""
    window = STRESS.window()
    stop_playback(window)
    for obj in [obj for obj in bpy.data.objects if obj.name.startswith(live.EVAL_OBJECT)]:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
    # Earlier commits leave orphan meshes that still hold these materials, so a
    # users==0 test keeps them and the next cycle's material gets a ".001" name.
    for material in [m for m in bpy.data.materials if m.name.startswith(("QA Graded Material", "Long Material"))]:
        bpy.data.materials.remove(material)
    window.scene.frame_set(1)


def stop_playback(window):
    if window.screen.is_animation_playing:
        with bpy.context.temp_override(window=window):
            bpy.ops.screen.animation_cancel(restore_frame=False)


def inject(call):
    """Disturb the timeline right after a program's approval, as stray keyboard input can."""
    mode = STRESS.mode
    if mode not in {"frame_step", "playback"} or call.name not in PROGRAM_TOOLS or call.id in STRESS.injected_calls:
        return
    STRESS.injected_calls.add(call.id)
    window = STRESS.window()
    STRESS.injections[mode] = STRESS.injections.get(mode, 0) + 1
    # Synchronous, inside the approval: the frame has moved (or playback runs) before staging can start.
    with bpy.context.temp_override(window=window):
        if mode == "frame_step":
            if len(STRESS.injected_calls) % 2:
                window.scene.frame_set(window.scene.frame_current + 1)
            else:
                bpy.ops.screen.frame_offset(delta=1)   # What the Right Arrow runs.
            return
        bpy.ops.screen.animation_play()   # What Space runs; playing from now on.
    if not window.screen.is_animation_playing:
        STRESS.error = "Playback did not start inside the approval"

    def stop():
        stop_playback(window)
        return None
    bpy.app.timers.register(stop, first_interval=PLAYBACK_SECONDS)


original_approve = controller_module.Controller.approve_tool


def approve_tool(self, call, executor, document):
    result = original_approve(self, call, executor, document)
    if call.authorized and not call.local:
        try:
            inject(call)
        except Exception:
            STRESS.error = traceback.format_exc()
    return result


controller_module.Controller.approve_tool = approve_tool
original_complete, original_on_send = Trial.complete, Trial.on_send


def complete(trial, status, reason=""):
    interval = original_complete(trial, status, reason)
    record = trial.report["tasks"][-1]
    window = STRESS.window()
    stop_playback(window)  # A task never leaves playback running for the next one.
    counts = (record.get("receipt") or {}).get("counts") or {}
    for key in ("frame_rebinds", "staging_restarts", "playback_waits", "tool_failures") + tuple(
            key for key in counts if key.startswith("failure_")):
        if counts.get(key):
            STRESS.counts[key] = STRESS.counts.get(key, 0) + counts[key]
    entry = {"cycle": STRESS.cycle, "mode": STRESS.mode, "shape": STRESS.shape, "task": record["task"],
             "status": record["status"], "host_check_passed": record.get("host_check_passed"),
             "total_ms": record.get("total_ms"), "model_rounds": record.get("model_rounds")}
    if STRESS.shape == "toolset21":
        # No reply row for a reply-less edit, and the lookup round led to the edit.
        rows = trial.controller.state.get("messages", [])
        start = max(index for index, row in enumerate(rows) if row.get("role") == "user")
        if any(row.get("role") == "assistant" and not row.get("text") for row in rows[start:]):
            entry["empty_chat_row"] = True
        if record["task"] in ("transform", "resize_02") and record.get("model_rounds") != 2:
            entry["lookup_round_missing"] = True
    STRESS.tasks.append(entry)
    if (record["status"] != "completed" or record.get("host_check_passed") is not True
            or entry.get("empty_chat_row") or entry.get("lookup_round_missing")):
        # This task's rows only: the previous task's host summary is not this task's error.
        messages = trial.controller.state.get("messages", [])[getattr(trial, "first_row", 0):]
        errors = [m.get("text") or (m.get("result") or {}).get("error") for m in messages[-6:]
                  if m.get("role") == "system" or m.get("status") == "failed"]
        STRESS.failures.append(dict(entry, errors=[e for e in errors if e][-3:], reason=record.get("reason")))
    return 0.0001 if STRESS.mode in {"immediate", "maintenance", "reset_during_send"} else interval


def on_send(trial):
    if trial.index == 0 and STRESS.mode == "reset_during_send":
        reset_scene()
    if STRESS.mode == "maintenance":
        from angelo_agent import snapshot_service
        snapshot_service.request_maintenance(ui)
    return original_on_send(trial)


def finish(trial):
    if STRESS.error:
        return write_and_quit()
    STRESS.cycle += 1
    if STRESS.cycle < CYCLES:
        if STRESS.mode != "reset_during_send":
            reset_scene()
        trial.__init__()  # A new chat, as each billed trial starts one.
        REPLAY.long_session = LongSession(REPLAY, STRESS.cycle)
        return 0.0001
    return write_and_quit()


def write_and_quit():
    by_mode, by_shape = {}, {}
    for task in STRESS.tasks:
        failed = (task["status"] != "completed" or task["host_check_passed"] is not True
                  or bool(task.get("empty_chat_row") or task.get("lookup_round_missing")))
        for key, table in ((task["mode"], by_mode), (task["shape"], by_shape)):
            entry = table.setdefault(key, {"tasks": 0, "failures": 0})
            entry["tasks"] += 1
            entry["failures"] += failed
    expected = len(live.tasks_for(TASK_SET, LONG_TASKS)) * CYCLES
    checks = {
        "every_task_ran": len(STRESS.tasks) == expected,
        "zero_failures": not STRESS.failures,
        "no_driver_error": STRESS.error is None,
        "frame_changes_were_injected": STRESS.injections.get("frame_step", 0) > 0 or CYCLES < 5,
        "playback_was_injected": STRESS.injections.get("playback", 0) > 0 or CYCLES < 6,
        # The fencing's contract (program_context): a program approved while playback runs waits for it to stop,
        # and a program whose frame moved between approval and staging is rebound once at the current frame.
        "every_playback_was_waited_for": STRESS.counts.get("playback_waits", 0) >= STRESS.injections.get("playback", 0),
        "every_frame_change_was_rebound": STRESS.counts.get("frame_rebinds", 0) >= sum(STRESS.injections.values()),
    }
    report = {"checks": checks, "success": all(checks.values()), "cycles": CYCLES, "tasks": len(STRESS.tasks),
              "expected_tasks": expected, "failures": STRESS.failures[:50], "failure_count": len(STRESS.failures),
              "by_mode": by_mode, "by_shape": by_shape, "injections": STRESS.injections,
              "receipt_counts": STRESS.counts, "task_set": TASK_SET,
              "seconds": round(time.monotonic() - STRESS.started, 1), "addon_root": str(ADDON_ROOT),
              "driver_error": STRESS.error, "paid_requests": 0}
    (OUT / "program-stress.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_PROGRAM_STRESS", json.dumps({key: report[key] for key in
          ("success", "tasks", "failure_count", "by_mode", "by_shape", "injections", "receipt_counts",
           "seconds")}), flush=True)
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=.5)
    return None


Trial.complete, Trial.on_send, Trial.finish = complete, on_send, finish
