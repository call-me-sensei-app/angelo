# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A goal held across a chat's turns: its fields, its budget, its rounds and what a finished round decides.

Contract: doc/angelo/goal_mode.rst ("The goal", "Checks", "Stopping"). A goal is a plain dict, so ``goal_store``
saves it as JSON as it is. This module never measures, sends or draws: ``goal_measure`` measures, ``goal_loop``
decides when a round is measured and sent, ``chat_goal`` draws. No bpy, no I/O, no clock: working time is what the
rounds report.

The decision after a round, in this order: the check passed (never in a round whose measurement changed); a limit of
the budget reached (working time, spend, rounds); too many rounds in a row that could not be measured; too many in a
row without progress; otherwise the goal goes on.
"""

import math
import uuid

# Checks.
REFERENCE, SCENE, JUDGED = "REFERENCE", "SCENE", "JUDGED"
CHECKS = (REFERENCE, SCENE, JUDGED)
# Where it was set.
CHAT_BAR, WORDS = "chat_bar", "words"
# Statuses: not ended, then the ends (doc "Stopping").
PENDING, PROPOSED, ACTIVE, PAUSED = "pending", "proposed", "active", "paused"
PASSED, STALLED, BUDGET, UNMEASURED = "passed", "stalled", "budget", "unmeasured"
DONE, NEEDS_INPUT, BLOCKED, STOPPED, FAILED = "done", "needs_input", "blocked", "stopped", "failed"
ENDS = frozenset({PASSED, STALLED, BUDGET, UNMEASURED, DONE, NEEDS_INPUT, BLOCKED, STOPPED, FAILED})
AGENT_REASONS = {"DONE": DONE, "NEEDS_INPUT": NEEDS_INPUT, "BLOCKED": BLOCKED}
# Who paused a goal (``paused_by``); ``reason`` keeps the agent's question when it was the agent. Stored English;
# ``goal_text`` words them for the person.
PAUSED_BY_MESSAGE, PAUSED_BY_RESTART, PAUSED_BY_AGENT = "your message", "Angelo restarted", "agent"
# An active goal no round ran or was due for: its chat was opened again, or a request did not start.
PAUSED_BY_IDLE = "nothing was continuing it"
NOT_STARTED = "not started"   # A proposed goal's end when the person sent another message instead of Start.
# Why Angelo ended a goal (stored English; ``goal_text`` words each for the person as a whole template).
STOPPED_BY_PERSON, INTERRUPTED = "Stopped", "the round was interrupted by Angelo"
CONNECTION_CHANGED, READ_ONLY = "the chat's connection changed", "the chat is read only"
DOCUMENT_CHANGED = "another document is open"   # A file opened, reloaded or reverted: the goal's scene is gone.
GOAL_DEFECT = "a defect in Angelo's goal handling"   # Shown with its error, quoted as data.
TURN_ERROR, NOT_SENT, SEND_FAILED = "the turn ended with an error", "the request was not sent", "Angelo could not send"
# A round's kinds and verdicts.
PERSON, AUTOMATIC, WORKER_EVENT = "person", "automatic", "worker_event"
PROGRESS, NO_PROGRESS, UNMEASURED_ROUND, MEASURE_CHANGED = "progress", "no_progress", "unmeasured", "measure_changed"

PASS_AT = 0.90
PASS_AT_RANGE = (0.5, 0.99)
# Smallest rise of the best overlap that counts as progress: half a point, above one pixel's rasterisation step on
# the outline of a subject about 300 pixels across (one pixel of its edge is about 0.3% of its area).
PROGRESS_IOU = 0.005
# Defaults: the scale of one whole scene build in the project's own one-shot limits (40-60 minutes, $20-50 at list
# price; oneshot_tests.rst, "Limits"): one full job's working time, a fraction of its cost.
MINUTES, STALL_ROUNDS, MAX_ROUNDS, UNMEASURED_ROUNDS, SPEND_USD = 60, 3, 20, 2, 10.0
OBJECTIVE_MAX = 500
MAX_ROUNDS_KEPT = 64   # A goal's round records kept (``max_rounds`` is at most this).


class Locked(ValueError):
    """The measurement is locked: only the person unlocks it (``unlock``)."""


def new(objective, origin, *, check=JUDGED, pass_at=PASS_AT, paid=False, proposed=False, now=0.0,
        connection=None):
    """A goal as held from now: active, or ``proposed`` (Ask: it waits for the person's Start)."""
    if check not in CHECKS or origin not in (CHAT_BAR, WORDS):
        raise ValueError("A goal's check is REFERENCE, SCENE or JUDGED, set from the chat bar or the person's words")
    goal = {"id": str(uuid.uuid4()), "objective": str(objective)[:OBJECTIVE_MAX], "origin": origin, "check": check,
            "measure": None, "pass_at": PASS_AT, "status": PROPOSED if proposed else ACTIVE, "reason": "",
            "budget": {"minutes": MINUTES, "stall_rounds": STALL_ROUNDS, "max_rounds": MAX_ROUNDS,
                       "unmeasured_rounds": UNMEASURED_ROUNDS, "usd": SPEND_USD if paid else None, "tokens": None},
            "spent": {"working_ms": 0, "tokens": 0, "usd": 0.0}, "rounds": [], "best": None, "stall": 0,
            "unmeasured": 0, "started_at": now, "connection": connection, "ended_at_score": None,
            "paused_by": "", "measure_request": None}
    set_pass_at(goal, pass_at)
    return goal


def set_pass_at(goal, value):
    if type(value) not in (int, float) or not math.isfinite(value) or not PASS_AT_RANGE[0] <= value <= PASS_AT_RANGE[1]:
        raise ValueError("pass_at is an overlap from %.2f to %.2f" % PASS_AT_RANGE)
    goal["pass_at"] = float(value)


def running(goal):
    return bool(goal) and goal.get("status") == ACTIVE


def ended(goal):
    return bool(goal) and goal.get("status") in ENDS


def judged(goal):
    """Whether the goal is judged by the agent: JUDGED, or a REFERENCE goal whose camera is not stated yet."""
    return goal["check"] == JUDGED or goal.get("measure") is None


def score(goal, measurement):
    """The round's comparable number (REFERENCE: overlap; SCENE: checks passing), or None when unmeasured."""
    if judged(goal) or not isinstance(measurement, dict) or not measurement.get("measured"):
        return None
    value = measurement.get("iou") if goal["check"] == REFERENCE else measurement.get("passed_count")
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _passed(goal, measurement):
    value = score(goal, measurement)
    if value is None:
        return False
    if goal["check"] == REFERENCE:
        return value >= goal["pass_at"]
    return bool(measurement.get("all_passed"))


def _verdict(goal, measurement, scene_changed, measure_changed):
    if judged(goal):
        return PROGRESS if scene_changed else NO_PROGRESS
    value = score(goal, measurement)
    if value is None:
        return UNMEASURED_ROUND   # Unmeasured whatever else changed: it can neither pass nor make progress.
    if measure_changed:
        return MEASURE_CHANGED
    step = PROGRESS_IOU if goal["check"] == REFERENCE else 1
    return PROGRESS if goal["best"] is None or value >= goal["best"] + step - 1e-9 else NO_PROGRESS


def over_budget(goal):
    """``(which, used, limit)`` of the first limit reached, or None: before a round and after one."""
    budget, spent = goal["budget"], goal["spent"]
    if spent["working_ms"] >= budget["minutes"] * 60_000:
        return ("minutes", spent["working_ms"] / 60_000, budget["minutes"])
    if budget.get("usd") is not None and spent["usd"] >= budget["usd"]:
        return ("usd", spent["usd"], budget["usd"])
    if budget.get("tokens") is not None and spent["tokens"] >= budget["tokens"]:
        return ("tokens", spent["tokens"], budget["tokens"])
    if len(goal["rounds"]) >= budget["max_rounds"]:
        return ("rounds", len(goal["rounds"]), budget["max_rounds"])
    return None


def record_round(goal, *, kind, working_ms, measure_ms, tokens, usd, measurement, scene_changed,
                 measure_changed=False):
    """Add a finished round; return ``(status, reason)``. ``ACTIVE`` goes on; anything else is the goal's end."""
    verdict = _verdict(goal, measurement, scene_changed, measure_changed)
    value = score(goal, measurement)
    if verdict == UNMEASURED_ROUND:
        goal["unmeasured"] += 1
    else:
        goal["unmeasured"] = 0
        # A changed measurement is neither progress nor a stall: changing it never resets the count (break-it M4).
        goal["stall"] = 0 if verdict == PROGRESS else goal["stall"] + (verdict != MEASURE_CHANGED)
    if value is not None and verdict != MEASURE_CHANGED and (goal["best"] is None or value > goal["best"]):
        goal["best"] = value
    if verdict == MEASURE_CHANGED:
        goal["best"] = value   # Another measurement: its numbers are the new baseline.
    if (goal.get("measure") and isinstance(measurement, dict) and measurement.get("measured")
            and (goal["check"] != REFERENCE or (measurement.get("drawn") or 0) > 0)):
        goal["measure"]["locked"] = True   # Measured once (a reference against a drawn scene): every check locks.
    charge(goal, working_ms, tokens, usd)
    goal["rounds"].append({"n": len(goal["rounds"]) + 1, "kind": kind, "working_ms": int(working_ms or 0),
                           "measure_ms": measure_ms, "tokens": tokens, "usd": usd, "score": value, "verdict": verdict,
                           "reason": (measurement or {}).get("reason", "") if verdict == UNMEASURED_ROUND else ""})
    del goal["rounds"][:-MAX_ROUNDS_KEPT]
    if verdict != MEASURE_CHANGED and _passed(goal, measurement):
        return _finish(goal, PASSED, "")
    limit = over_budget(goal)
    if limit is not None:
        return _finish(goal, BUDGET, limit[0])
    if goal["unmeasured"] >= goal["budget"]["unmeasured_rounds"]:
        return _finish(goal, UNMEASURED, goal["rounds"][-1]["reason"])
    if goal["stall"] >= goal["budget"]["stall_rounds"]:
        return _finish(goal, STALLED, "")
    return ACTIVE, ""


def charge(goal, working_ms, tokens, usd):
    """Count what a turn used toward the goal's budget: every round's, also one the goal stopped or paused in."""
    spent = goal["spent"]
    spent["working_ms"] += max(0, int(working_ms or 0))
    spent["tokens"] += max(0, int(tokens or 0))
    spent["usd"] = round(spent["usd"] + max(0.0, float(usd or 0.0)), 6)


def _finish(goal, status, reason):
    goal["status"], goal["reason"] = status, reason
    goal["ended_at_score"] = goal["rounds"][-1]["score"] if goal["rounds"] else None
    return status, reason


def end(goal, status, reason):
    """End a goal that is not ended yet (Stop, a failure, a connection change)."""
    if status not in ENDS or ended(goal):
        raise ValueError("Only a goal that has not ended can end, with one of its end statuses")
    _finish(goal, status, reason)


def agent_stop(goal, reason, message):
    """``angelo_goal`` STOP with the agent's reason and message (kept as data): DONE and BLOCKED end the goal;
    NEEDS_INPUT pauses it with the agent's question, and the person's answer and Resume continue it."""
    if reason not in AGENT_REASONS:
        raise ValueError("reason is DONE, NEEDS_INPUT or BLOCKED")
    message = str(message)[:OBJECTIVE_MAX]
    if reason == "NEEDS_INPUT":
        pause(goal, message, by=PAUSED_BY_AGENT)
        return
    end(goal, AGENT_REASONS[reason], message)
    goal["ended_at_score"] = goal["best"] if goal["best"] is not None else goal["ended_at_score"]


def pause(goal, reason, *, by=None):
    """Pause an active goal; ``by`` who paused it (the person's message, a restart, the agent), default ``reason``."""
    if goal.get("status") != ACTIVE:
        raise ValueError("Only an active goal can pause")
    goal["status"], goal["reason"], goal["paused_by"] = PAUSED, reason, by or reason


def resume(goal):
    if goal.get("status") != PAUSED:
        raise ValueError("Only a paused goal can resume")
    goal["status"], goal["reason"], goal["paused_by"] = ACTIVE, "", ""


def lapse(goal):
    """The person sent another message without pressing Start: a proposed goal ends unstarted."""
    if goal.get("status") == PROPOSED:
        goal["status"], goal["reason"] = STOPPED, NOT_STARTED


def start(goal):
    """The person's Start on a proposed goal."""
    if goal.get("status") != PROPOSED:
        raise ValueError("Only a proposed goal can start")
    goal["status"] = ACTIVE


def after_restart(goal):
    """A goal read back after Angelo started again: an active one waits for the person's Resume."""
    if goal.get("status") == ACTIVE:
        pause(goal, PAUSED_BY_RESTART)


def replace_measure(goal, measure):
    """The agent's measurement, while it is not locked; the round that changes it cannot pass. While it is locked the
    agent's measurement is kept as its request (the chip's Allow) and ``Locked`` is raised; the goal goes on."""
    if goal.get("measure") and goal["measure"].get("locked"):
        goal["measure_request"] = dict(measure)
        raise Locked("The measurement is locked since its first measured round; your change waits for the person's "
                     "Allow")
    goal["measure"], goal["measure_request"] = dict(measure, locked=False), None


def allow_measure_request(goal):
    """The person's Allow on the agent's request to change the measurement."""
    request = goal.get("measure_request")
    if request is None:
        raise ValueError("The agent has not asked to change the measurement")
    goal["measure"], goal["measure_request"] = dict(request, locked=False), None


def unlock(goal):
    """The person's choice in the goal card: the agent may state another measurement."""
    if goal.get("measure"):
        goal["measure"]["locked"] = False
