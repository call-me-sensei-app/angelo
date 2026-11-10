# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``steering`` gate set: the person sends a second message while the agent works, on a real model.

Each task's prompt goes out as usual; once the turn has finished its first tool step (or, if it never takes one,
once it ends) the harness sends the task's ``steer`` text exactly as Enter does during a turn
(``chat_pending.hold``). Angelo then steers it into the running turn at its next safe step or sends it when the
turn ends (``steering``); a task with ``"mode": "queue"`` switches it to After this turn, as the bubble's control
does. The task is graded only once nothing of the person's waits any more and the chat is idle
(``jobs_pending``), with the harness's closed checks over the named objects, so the scene must show both the
prompt and the steer. Each record says how the steer went: ``read_mid_turn`` (a steer row the turn read),
``sent_after_turn`` (its own request when the turn ended), or ``left`` (still pending or paused: a failure).

The driver (``eval_live_agent_session.py``) treats this like a scenario set (``scenario_session.for_trial``):
approvals follow the harness's typed allowlist (``eval_live_tasks.approval_decision``). Dependency-free apart from
``bpy`` and the add-on, both inside the app.
"""

import copy
import os
import time

TASK_SET = "steering"
# An after-turn request is a whole turn of its own: it gets the task's own timeout, which the harness's trial
# deadline already allows twice per task (eval_live_agent.run_trial).
WAIT_S = float(os.environ.get("ANGELO_EVAL_TASK_TIMEOUT", "300"))
NAMES = {"$a": "Steer A", "$b": "Steer B", "$c": "Steer C", "$d": "Steer D"}


def _unit(alias, x, size=1.0):
    return [{"kind": "dimensions", "target": alias, "expected": [size] * 3, "tolerance": 0.0001},
            {"kind": "transform", "target": alias, "property": "location", "expected": [x, 0.0, 0.0],
             "tolerance": 0.0001}]


def _paint(alias):
    return [{"kind": "material_assignment", "target": alias, "material": "Steer Paint", "all_faces": True},
            {"kind": "material_values", "target": alias, "material": "Steer Paint", "base_color": [1.0, 0.0, 0.0, 1.0],
             "roughness": 0.5, "metallic": 0.0, "tolerance": 0.00001}]


def tasks():
    """Three edits in one chat; each steer changes or extends what the prompt asked, so either way it arrives the
    finished scene shows whether the agent took the person's words."""
    every = {"kind": "edit", "requires_object": False, "targets": dict(NAMES)}
    return [
        dict(every, id="steer_resize", source="steering: a steer that changes the plan",
             prompt="Create four cubes named 'Steer A', 'Steer B', 'Steer C' and 'Steer D', each exactly 1 m on every "
                    "side, at locations (0, 0, 0), (2, 0, 0), (4, 0, 0) and (6, 0, 0).",
             steer={"text": "Change of plan: make 'Steer D' 2 m on every side instead of 1 m; keep it at (6, 0, 0)."},
             checks=_unit("$a", 0.0) + _unit("$b", 2.0) + _unit("$c", 4.0) + _unit("$d", 6.0, 2.0)),
        dict(every, id="steer_extend", source="steering: a steer that adds to the request",
             prompt="Give 'Steer A' and 'Steer B' a new Principled BSDF material named 'Steer Paint' with base colour "
                    "(1, 0, 0, 1), roughness 0.5 and metallic 0, on every face.",
             steer={"text": "Also give 'Steer C' that same material on every face."},
             checks=_paint("$a") + _paint("$b") + _paint("$c")),
        dict(every, id="steer_after_turn", source="steering: a message queued for after the turn",
             prompt="Add a non-destructive bevel modifier with a width of 0.05 m and 2 segments to 'Steer A'.",
             steer={"text": "Now move 'Steer B' to location (2, 3, 0).", "mode": "queue"},
             checks=[{"kind": "modifier", "target": "$a", "name": "$bevel", "type": "BEVEL",
                      "values": {"width": 0.05, "segments": 2}, "tolerance": 0.00001},
                     {"kind": "transform", "target": "$b", "property": "location", "expected": [2.0, 3.0, 0.0],
                      "tolerance": 0.0001}]),
    ]


def bind(checks, objects):
    """``$bevel`` names the target's own BEVEL modifier (the agent chooses its name)."""
    bound = copy.deepcopy(checks)
    for check in bound:
        if check.get("name") == "$bevel":
            target = objects.get(check.get("target"))
            bevel = next((m.name for m in target.modifiers if m.type == "BEVEL"), None) if target else None
            check["name"] = bevel or "missing bevel modifier"
    return bound


class SteerSession:
    """The ``scenario_session`` duties for the steering set (see the module docstring)."""

    job_wait_s = WAIT_S

    def __init__(self, out):
        self.out, self.report = out, {"set": TASK_SET}
        self.controller, self.task, self.record, self.first_row, self.item = None, None, None, 0, None

    def begin(self, bpy, window):
        return self.report

    def ready(self, finishing=False):
        return True

    def attach(self, controller, task, record):
        """Watch the turn: its first finished tool step is when the person types (``_poll``)."""
        import bpy
        self.controller, self.task, self.record = controller, task, record
        self.first_row, self.item, self.sent_at = len(controller.state["messages"]), None, time.monotonic()
        record["steer"] = {"text": task["steer"]["text"], "mode": task["steer"].get("mode", "steer")}
        bpy.app.timers.register(self._poll, first_interval=0.2)

    def _poll(self):
        controller, steer = self.controller, self.record["steer"]
        if controller is None or self.item is not None:
            return None
        rows = controller.state["messages"][self.first_row:]
        stepped = any(row.get("role") == "activity" and row.get("status") in {"completed", "failed"} for row in rows)
        running = controller.turn_running()
        if running and not stepped and time.monotonic() - self.sent_at < WAIT_S:
            return 0.2
        from angelo_agent import chat_pending
        from angelo_agent.steering import QUEUE
        self.item = chat_pending.hold(controller, steer["text"])   # As Enter does during a turn (console.execute).
        steer.update(held_ms=round((time.monotonic() - self.sent_at) * 1000, 1), held_during_turn=running,
                     steerable=self.item.mode != QUEUE)
        if steer["mode"] == "queue":
            controller.inbox.set_mode(self.item.id, QUEUE)   # The bubble's After this turn.
        return None

    def decide(self, tool_name):
        import eval_live_tasks as live
        return live.approval_decision(tool_name)

    def jobs_pending(self):
        """Graded once the steer was sent, nothing of the person's waits and the chat is idle."""
        controller = self.controller
        return controller is not None and (self.item is None or controller.turn_running() or len(controller.inbox) > 0)

    def outcome(self):
        rows = self.controller.state["messages"][self.first_row:]
        text = self.record["steer"]["text"]
        if any(r.get("steer") is True and r.get("text") == text and not r.get("withdrawn") for r in rows):
            return "read_mid_turn"
        if any(r.get("role") == "user" and not r.get("steer") and text in str(r.get("text", "")) for r in rows[1:]):
            return "sent_after_turn"
        return "left"

    def grade(self, bpy, scene, task, record, workspace=None, window=None):
        from angelo_agent.scene_validation import VerificationContext, verify
        objects = {alias: scene.objects.get(name) for alias, name in task["targets"].items()}
        result = (verify(VerificationContext(objects, list(scene.objects)), bind(task["checks"], objects))
                  if all(objects.values()) else {"success": False, "status": "fail", "reason": "object missing"})
        outcome = self.outcome()
        errors = [r for r in self.controller.state["messages"][self.first_row:] if r.get("role") == "error"]
        record["steer"].update(outcome=outcome, left_pending=len(self.controller.inbox), error_rows=len(errors))
        if task["steer"].get("mode") == "queue" and outcome == "read_mid_turn":
            outcome = "left"   # After this turn must never be read midway.
        record["host_check"] = result
        return bool(result.get("success")) and outcome != "left" and not errors and not len(self.controller.inbox)

    def stopped(self, bpy, scene, task, record, workspace=None, window=None):
        if self.controller is not None:
            record.setdefault("steer", {}).update(outcome=self.outcome(), left_pending=len(self.controller.inbox))

    def finish(self, bpy, window, probe=True):
        return None

    def undo_probe(self, bpy, window):
        return None
