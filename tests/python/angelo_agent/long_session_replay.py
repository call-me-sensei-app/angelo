# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled long-session replay: the long benchmark's task cycle in one chat, answered by a scripted runtime.

``eval_live_agent_session.py`` runs unchanged (the real controller, UI pump,
default approval, commit operators, request checkpoints, receipts and host
grading); only the provider is replaced, as in
``test_blender_eval_live_agent_dryrun.py``. The scripted runtime answers each
prompt of ``eval_live_tasks.build_long_session_tasks`` with the typed program
Opus 5.5 sent for it in the recorded long runs, bound to the post-commit
revision of the previous edit. No process, network or account is used.

``ANGELO_REPLAY_MATERIAL`` chooses what the material task does:

* ``append`` (default, as Opus did): a new material in a new last slot, so the
  cube gains a material every five tasks until the 32-slot limit refuses the
  rest;
* ``replace``: the new material replaces slot 0, so the cube keeps one
  material while the file gains an orphan material and mesh per cycle.

``ANGELO_REPLAY_TASKS`` (default 627, 500 edits) is the task count and
``ANGELO_REPLAY_PROFILE`` a comma list of task indices to profile with
cProfile (``profile-NNN.prof``; Python 3.13's profiler sees every thread).
``ANGELO_REPLAY_HOST_TIMERS=1`` adds each edit's conversation-journal,
admission-save, ledger and snapshot-worker time to its row
(``long_session_timers.py``). Run from the repository root with new temporary
directories::

    ANGELO_DISABLE_DEFAULT_CHAT=1 ANGELO_DISABLE_KEYCHAIN=1 CFFIXED_USER_HOME=$D/home HOME=$D/home \\
    BLENDER_USER_CONFIG=$D/config ANGELO_QA_DIR=$D ANGELO_STATE_DIR=$D/state ANGELO_CONNECTIONS_DIR=$D/accounts \\
    Angelo --factory-startup --no-window-focus --enable-event-simulate --disable-autoexec --python-exit-code 1 \\
        --python tests/python/angelo_agent/long_session_replay.py

``$D/trial.json`` is the session's report (every task's receipt) and
``$D/long-session-replay.json`` the per-edit phases and Main ID counts
(doc/angelo/performance.rst, "Per-edit cost over a long session").
"""

import cProfile
import json
import os
from pathlib import Path
import re
import runpy
import sys
import threading
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI process"
assert bpy.app.use_event_simulate, "Pass --enable-event-simulate: outside input (Space starts playback) would stall the run"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
for name in ("ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "BLENDER_USER_CONFIG"):
    assert Path(os.environ[name]).resolve().is_relative_to(OUT.resolve()), name + " must be inside ANGELO_QA_DIR"
assert os.environ.get("ANGELO_DISABLE_DEFAULT_CHAT") == "1"
TASKS = int(os.environ.get("ANGELO_REPLAY_TASKS", "627"))
MATERIAL = os.environ.get("ANGELO_REPLAY_MATERIAL", "append")
PROFILE = {int(value) for value in os.environ.get("ANGELO_REPLAY_PROFILE", "").split(",") if value.strip()}
TIMERS = os.environ.get("ANGELO_REPLAY_HOST_TIMERS") == "1"
assert MATERIAL in {"append", "replace"}, "ANGELO_REPLAY_MATERIAL is append or replace"

import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402

live.MAX_LONG_SESSION_TASKS = max(TASKS, live.MAX_LONG_SESSION_TASKS)  # This unbilled process only.
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
PHASES = ("precondition_ms", "staging_ms", "verification_ms", "commit_ms", "main_thread_block_ms", "checkpoint_ms",
          "total_ms")
ID_COLLECTIONS = ("objects", "meshes", "materials", "node_groups", "images")


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


class LongRuntime:
    """Deterministic provider stand-in for the long cycle. It never starts a process or opens a socket."""

    def __init__(self, context, settings):
        self.controller, self.warm = context, False
        self.handle = self.revision = None
        self.dimensions, self.materials = [2., 2., 2.], 0

    def connect(self):
        self.warm = True
        self.controller.emit("native_session", {"id": "long-replay"})
        return "long-replay"

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
        context.request_tool("replay-" + uuid.uuid4().hex, name, arguments,
                             lambda result: (box.update(result=result), done.set()))
        if not done.wait(600):
            raise RuntimeError("Scripted tool call timed out: " + name)
        return box["result"]

    def say(self, context, text):
        context.emit("delta", {"item": "long-replay", "text": text})
        context.emit("message_complete", {"item": "long-replay", "text": text})

    def program(self, context, operation, checks=()):
        """One APPLY program bound to the previous edit's post-commit revision (a lookup only without one)."""
        write_set = []
        if self.handle:
            if not self.revision:
                observed = self.call(context, "angelo_scene_object", {"object": self.handle, "sections": ["TRANSFORM"]})
                self.revision = find(observed, "object_revision")
            write_set = [{"handle": self.handle, "revision": self.revision}]
        result = self.call(context, "angelo_scene_program", {
            "mode": "APPLY", "read_set": [], "write_set": write_set,
            "operations": [dict(operation, target=self.handle if operation["kind"] != "create_primitive" else None)],
            "expected_checks": [dict(check, target=self.handle) for check in checks]})
        objects = ((result or {}).get("live_revisions") or {}).get("objects") or []
        mine = next((item for item in objects if item.get("name") == live.EVAL_OBJECT), None)
        self.handle = mine["handle"] if mine else self.handle
        self.revision = mine.get("object_revision") if mine else None
        return bool((result or {}).get("success"))

    def run_request(self, context, native_id, text, observation=None):
        context.record_provider_turn()
        context.record_provider_accepted()
        prompt = text.split("\n", 1)[0]
        seen = next((item for item in (observation or {}).get("objects", []) if item.get("name") == live.EVAL_OBJECT), {})
        self.handle = self.handle or seen.get("handle")
        numbers = [float(value) for value in NUMBER.findall(prompt.split("'%s'" % live.EVAL_OBJECT, 1)[-1])]
        if prompt.startswith("How many"):
            meshes = sorted(item["name"] for item in (observation or {}).get("objects", []) if item.get("type") == "MESH")
            self.say(context, "%d mesh objects.\nMESHES: %s" % (len(meshes), ", ".join(meshes)))
        elif prompt.startswith("Create a cube"):
            self.program(context, {"kind": "create_primitive", "parameters": {
                "primitive": "CUBE", "name": live.EVAL_OBJECT, "size": 2, "location": [0, 0, 0]}})
            self.say(context, "Created.")
        elif prompt.startswith("Move"):
            self.program(context, {"kind": "transform", "parameters": {"location": numbers[:3]}})
            self.say(context, "Moved.")
        elif prompt.startswith("Scale"):
            self.dimensions = numbers[:3]
            self.program(context, {"kind": "transform", "parameters": {"scale": [value / 2 for value in numbers[:3]]}},
                         [{"kind": "dimensions", "expected": numbers[:3], "space": "WORLD", "tolerance": 0.001}])
            self.say(context, "Scaled.")
        elif prompt.startswith(("Add a non-destructive bevel", "Change the bevel")):
            kind = "modifier_add" if prompt.startswith("Add") else "modifier_configure"
            settings = {"width": numbers[0], "segments": int(numbers[1])}
            self.program(context, {"kind": kind, "parameters": dict(
                {"type": "BEVEL", "settings": settings}, **({"name": "Bevel"} if kind == "modifier_add" else {"modifier": "Bevel"}))})
            self.say(context, "Bevel set.")
        elif prompt.startswith("Give every face"):
            name = re.search(r"named '([^']+)'", prompt).group(1)
            values = [float(value) for value in NUMBER.findall(prompt.split("colour", 1)[1])]
            parameters = {"name": name, "inputs": {"Base Color": values[:4], "Roughness": values[4], "Metallic": values[5]}}
            if MATERIAL == "replace" and self.materials:
                parameters["slot"] = 0
            if self.program(context, {"kind": "material_principled", "parameters": parameters},
                            [{"kind": "material_assignment", "material": name, "all_faces": True}]):
                self.materials += 1
            self.say(context, "Material given.")
        elif prompt.startswith("What are the current"):
            # The harness offsets the cube before the question; the observation shows where it is now.
            location = seen.get("location") or [0., 0., 0.]
            self.say(context, "As observed.\nDIMENSIONS: %g %g %g; LOCATION: %g %g %g" % (*self.dimensions, *location))
        else:
            raise RuntimeError("The long replay has no script for: " + prompt)
        return "completed"


def isolated_store():
    source = OUT / "source-accounts"
    record = ConnectionStore(source).add("OPENAI", "SUBSCRIPTION", name="Long replay scripted runtime")
    eval_live_agent.isolated_connection(source, record["id"], os.environ["ANGELO_CONNECTIONS_DIR"])
    return record["id"]


# Billing authorization is meaningless here: the scripted runtime replaces the
# provider class before any request, so nothing can reach an account.
os.environ.update(ANGELO_EVAL_AUTHORIZED="1", ANGELO_EVAL_CONNECTION_ID=isolated_store(), ANGELO_EVAL_TRIAL="1",
                  ANGELO_EVAL_TASK_TIMEOUT="600", ANGELO_EVAL_TASK_SET="long", ANGELO_EVAL_LONG_TASKS=str(TASKS))
from angelo_agent import controller as controller_module, ui  # noqa: E402
controller_module.CodexProvider = controller_module.ClaudeProvider = LongRuntime
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
if TIMERS:
    import long_session_timers as timers  # noqa: E402
    print("ANGELO_LONG_REPLAY_TIMERS", json.dumps(timers.install()), flush=True)
session = runpy.run_path(str(HERE / "eval_live_agent_session.py"), run_name="long_session_replay")
Trial = session["Trial"]
original_send, original_complete, original_finish = Trial.on_send, Trial.complete, Trial.finish
profilers, rows, timer_mark = {}, [], {}


def on_send(trial):
    if trial.index in PROFILE and trial.index not in profilers and trial.index < len(trial.tasks):
        profilers[trial.index] = cProfile.Profile()
        profilers[trial.index].enable()
    return original_send(trial)


def complete(trial, status, reason=""):
    global timer_mark
    index = trial.index
    if index in profilers:
        profilers[index].disable()
        profilers[index].dump_stats(str(OUT / ("profile-%03d.prof" % index)))
    result = original_complete(trial, status, reason)
    record = trial.report["tasks"][-1] if trial.report["tasks"] else {}
    receipt = record.get("receipt") or {}
    # Host time since the previous task ended: this task's saves, and the snapshot worker's run for its capture
    # (a run still going when the task ends counts towards the next task).
    host, timer_mark = timers.since(timer_mark) if TIMERS else ({}, timer_mark)
    if (receipt.get("counts") or {}).get("native_commits"):
        phases = receipt.get("phases") or {}
        rows.append(dict({"edit": len(rows) + 1, "task": record.get("task"), "grade": record.get("grade")},
                         **{phase: phases.get(phase) for phase in PHASES}, **host,
                         **{name: len(getattr(bpy.data, name)) for name in ID_COLLECTIONS}))
    return result


def finish(trial):
    journals = {path.name: path.stat().st_size
                for path in (Path(os.environ["ANGELO_STATE_DIR"]) / "sessions").glob("*.json")}
    (OUT / "long-session-replay.json").write_text(json.dumps({
        "tasks": TASKS, "material": MATERIAL, "host_timers": TIMERS, "journal_bytes": journals, "edits": rows,
        "paid_requests": 0}, indent=1))
    print("ANGELO_LONG_REPLAY_DONE", json.dumps({"edits": len(rows), "material": MATERIAL}), flush=True)
    return original_finish(trial)


Trial.on_send, Trial.complete, Trial.finish = on_send, complete, finish
