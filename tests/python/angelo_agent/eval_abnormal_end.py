# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a live-eval task's turn was doing when it did not end normally (``abnormal_end``, ``KEY``).

A task that ends normally keeps its progress marks, controller status and receipt (``eval_live_agent_session``).
Every other ending used to keep almost nothing, so a timed-out turn could not show whether the provider stream
went silent, ran away or waited on the host. Each non-normal ending now writes one block, the same for every
provider:

* the harness's own deadline (task timeout, preview-accept timeout, a warm-up's timeout): captured just before
  its Stop (``stop``);
* any other Stop, such as a one-shot cost cap: seen at the next tick (``refresh``, ``controller.cancelled``);
* a turn the runtime or host ended as ``interrupted`` or ``failed``, a timeout, an error, a provider limit or a
  background job that outlived its wait: captured when the task completes (``settle``);
* a harness exception or any other finish while a task is in flight (``unfinished``, at ``Trial.finish``);
* the app crashing, hanging or quitting mid-task: the launcher reads the last in-flight snapshot, written every
  ``REFRESH_S`` while a task runs (``IN_FLIGHT``, ``collect``).

The block holds: the progress marks so far (``ProgressWatch``); the controller's status and whether its turn had
ended; the provider's open turn (``turn_activity.signs``): the age and kind of its last event, ``model`` (a
model event, or the host handing the turn back after its own work) or ``keep_alive`` (a runtime keep-alive or
notice newer than any model event; Claude Code only, Codex sends none); the last host-visible tool call (the
transcript's tool rows, its name and age) and the host calls still pending; what the provider streamed since the
send (``stream``: text and thinking bytes and deltas, the current message's, the last 10 s rate and whether the
current burst of deltas made any structural progress; the harness's own observation, ``eval_stream_counts``);
whether an interrupt was sent, by whom, and whether the turn had ended by the time the task was recorded
(``acknowledged``, ``waited_ms``). Wall times (``sent_at_unix`` ...) bound the window the launcher exports runtime
trace rows for (``eval_trace_export``).
Names and counts only, never arguments or text. Standard library only at import (no bpy).
"""

import json
from pathlib import Path
import time

KEY = "abnormal_end"
UNFINISHED = "unfinished_task"
IN_FLIGHT = "in_flight.json"
VERSION = 1
REFRESH_S = 5.0
# The trial driver's phases while a task's message is worked on (``eval_live_agent_session``).
TURN_PHASES = frozenset({"running", "stopping", "accepting", "jobs"})
# Task statuses and receipt statuses that are not a normal ending.
ABNORMAL_STATUSES = frozenset({"timeout", "failed", "error", "invalid"})
ABNORMAL_RECEIPTS = frozenset({"interrupted", "failed"})


def _ms(seconds):
    return None if seconds is None else round(seconds * 1000, 1)


def turn_ended(controller):
    """The chat's turn is over: not busy and no worker thread alive (the trial driver's ``idle``)."""
    worker = getattr(controller, "_worker", None)
    return not controller.busy and not (worker and worker.is_alive())


def provider_event(controller, now):
    """The provider's open turn: whether it keeps activity at all, and its last event's age and kind."""
    provider = getattr(controller, "provider", None)
    facts = {"provider": type(provider).__name__ if provider is not None else None,
             "session": (getattr(controller, "state", None) or {}).get("native_id"),
             "turn_id": getattr(getattr(provider, "_scope", None), "turn_id", None)}
    try:
        from angelo_agent.turn_activity import signs
        watched, found = signs(provider) if provider is not None else (False, None)
    except Exception as error:   # A provider mid-teardown, or an app without turn activity.
        return dict(facts, watched=False, turn_open=False, kind=None, age_ms=None, error=type(error).__name__)
    if not found:
        return dict(facts, watched=bool(watched), turn_open=False, kind=None, age_ms=None)
    accepted, model, heard = found
    keep_alive = heard is not None and model is not None and heard > model
    newest = heard if keep_alive else model
    return dict(facts, watched=bool(watched), turn_open=True, input_accepted=accepted,
                kind="keep_alive" if keep_alive else "model", age_ms=_ms(now - newest) if newest is not None else None,
                model_event_age_ms=_ms(now - model) if model is not None else None,
                any_event_age_ms=_ms(now - heard) if heard is not None else None)


def tool_calls(trial, now):
    """The turn's last host-visible tool call (transcript tool rows) and the host calls still pending."""
    controller, progress = trial.controller, getattr(trial, "progress", None)
    rows = [row for row in controller.state["messages"][getattr(trial, "first_row", 0):]
            if isinstance(row, dict) and row.get("role") == "activity"]
    seen = getattr(progress, "last_tool_at", None)
    pending = [{"name": getattr(call, "name", None), "approval": bool(getattr(call, "approval", False)),
                "executing": bool(getattr(call, "executing", False))}
               for call in list((getattr(controller, "pending", None) or {}).values())]
    return {"rows": len(rows), "name": rows[-1].get("tool") if rows else None,
            "status": rows[-1].get("status") if rows else None,
            "age_ms": _ms(now - seen) if rows and seen is not None else None, "pending": pending}


def snapshot(trial, reason, captured, now=None, wall=None):
    """The block for ``trial``'s task now; ``captured`` says which path took it. Never raises: evidence must not
    end the task it describes."""
    now = time.monotonic() if now is None else now
    wall = time.time() if wall is None else wall
    try:
        return _snapshot(trial, reason, captured, now, wall)
    except Exception as error:
        return {"version": VERSION, "reason": reason, "captured": captured, "phase": getattr(trial, "phase", None),
                "after_send_ms": None, "sent_at_unix": None, "captured_at_unix": round(wall, 3),
                "error": "%s: %s" % (type(error).__name__, error), "interrupt": {"sent": False}}


def _snapshot(trial, reason, captured, now, wall):
    controller, sent = trial.controller, getattr(trial, "sent", None)
    progress = getattr(trial, "progress", None)
    since = now - sent if sent is not None else None
    return {"version": VERSION, "reason": reason, "captured": captured, "phase": trial.phase,
            "after_send_ms": _ms(since), "sent_at_unix": round(wall - since, 3) if since is not None else None,
            "captured_at_unix": round(wall, 3),
            "progress_marks": progress.peek() if hasattr(progress, "peek") else {},
            "controller_status": str(getattr(controller, "status", "")), "turn_ended": turn_ended(controller),
            "last_provider_event": provider_event(controller, now), "last_tool_call": tool_calls(trial, now),
            "stream": stream_counts(progress, now), "interrupt": {"sent": False}}


def stream_counts(progress, now):
    """What the provider streamed since the send, as the trial process observed it (``eval_stream_counts``)."""
    cursor = getattr(progress, "stream", None)
    if cursor is None:
        return {"observed_by": None, "note": "the stream was not observed (no in-app ProgressWatch)"}
    try:
        return cursor.summary(now, getattr(progress, "last_structure_at", None))
    except Exception as error:   # The rest of the block still stands.
        return {"observed_by": "harness", "error": "%s: %s" % (type(error).__name__, error)}


def _interrupt(block, by, now, wall):
    block["interrupt"] = {"sent": True, "sent_by": by, "sent_at_unix": round(wall, 3),
                          "sent_after_send_ms": block["after_send_ms"]}
    block["_stop_at"] = now   # Dropped when the task is recorded (``settle``).


def stop(trial, reason):
    """The harness's own deadline: capture the turn, then Stop it as the chat's Stop button does."""
    now, wall = time.monotonic(), time.time()
    block = trial.record.setdefault(KEY, snapshot(trial, reason, "deadline", now, wall))
    trial.controller.stop()
    if not block["interrupt"]["sent"]:
        _interrupt(block, "harness_deadline", now, wall)


def refresh(trial, out, now=None):
    """Every tick while a task runs: note a Stop the harness did not send, and keep the in-flight snapshot fresh."""
    try:
        _refresh(trial, out, now)
    except Exception:
        pass   # Evidence only: never ends the tick that drives the task.


def _refresh(trial, out, now):
    record = getattr(trial, "record", None)
    if trial.phase not in TURN_PHASES or record is None or trial.controller is None:
        return
    now = time.monotonic() if now is None else now
    if getattr(trial.controller, "cancelled", False) and KEY not in record:
        wall = time.time()
        record[KEY] = snapshot(trial, "stopped", "observed_stop", now, wall)
        _interrupt(record[KEY], "other", now, wall)
    marker = getattr(trial, "_abnormal_marker", None)
    if marker is not None and marker[0] is record and now - marker[1] < REFRESH_S:
        return
    trial._abnormal_marker = (record, now)
    block = record.get(KEY) or snapshot(trial, "in_flight", "periodic", now)
    _write(Path(out) / IN_FLIGHT, {"trial": record.get("trial"), "task": record.get("task"), "kind": record.get("kind"),
                                   "phase": trial.phase, KEY: _public(block)})


def settle(trial, status, out):
    """When a task (or a warm-up, ``out`` None) is recorded: the block of a non-normal ending, written now if no
    earlier path wrote it."""
    record, now, wall = trial.record, time.monotonic(), time.time()
    receipt = (record.get("receipt") or {}).get("status")
    abnormal = (KEY in record or status in ABNORMAL_STATUSES or receipt in ABNORMAL_RECEIPTS
                or record.get("job_wait_timed_out") or (record.get("accept") or {}).get("timed_out"))
    if abnormal and status != "skipped":
        block = record.get(KEY) or snapshot(trial, "job_timeout" if record.get("job_wait_timed_out")
                                            else receipt if receipt in ABNORMAL_RECEIPTS else status, "settle",
                                            now, wall)
        try:
            _finalize(trial, block, "complete", now, wall, receipt)
        except Exception as error:   # The task is still recorded.
            block["error"] = "%s: %s" % (type(error).__name__, error)
        record[KEY] = _public(block)
    if out is not None:
        _remove(Path(out) / IN_FLIGHT)
    trial._abnormal_marker = None


def _finalize(trial, block, ended_by, now, wall, receipt=None):
    block.update(ended_by=ended_by, ended_at_unix=round(wall, 3), receipt_status=receipt,
                 status_at_end=str(getattr(trial.controller, "status", "")),
                 turn_ended_at_end=turn_ended(trial.controller))
    if block["interrupt"].get("sent"):
        block["interrupt"].update(acknowledged=block["turn_ended_at_end"],
                                  waited_ms=_ms(now - block.get("_stop_at", now)))


def unfinished(trial, out):
    """``Trial.finish`` with a task still in flight (a harness exception, an abort): keep that task's block."""
    record = getattr(trial, "record", None)
    if (record is None or trial.phase not in TURN_PHASES or getattr(trial, "controller", None) is None
            or any(task is record for task in trial.report.get("tasks", ()))):
        return
    try:
        now, wall = time.monotonic(), time.time()
        reason = "harness_exception" if trial.report.get("error") else "finished_mid_task"
        block = record.get(KEY) or snapshot(trial, reason, "finish", now, wall)
        _finalize(trial, block, reason, now, wall, (record.get("receipt") or {}).get("status"))
        trial.report[UNFINISHED] = {"trial": record.get("trial"), "task": record.get("task"),
                                    "kind": record.get("kind"), "phase": trial.phase, KEY: _public(block)}
        _remove(Path(out) / IN_FLIGHT)
    except Exception as error:   # The evidence never hides the error that ended the trial.
        trial.report[UNFINISHED] = {"task": record.get("task"), "error": "%s: %s" % (type(error).__name__, error)}


def collect(folder, report, exit_code):
    """Launcher side, after the app exited: a task left in flight becomes ``unfinished_task`` (``app_exit``)."""
    path = Path(folder) / "qa" / IN_FLIGHT
    try:
        left = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        left = None
    if left and not report.get(UNFINISHED):
        block = dict(left.get(KEY) or {})
        ended = "harness_kill" if exit_code == "timeout" else "app_exit"
        if block.get("reason") in (None, "in_flight"):
            block["reason"] = ended
        block.update(ended_by=ended, exit_code=exit_code, ended_at_unix=round(time.time(), 3))
        if (block.get("interrupt") or {}).get("sent"):
            block["interrupt"] = dict(block["interrupt"], acknowledged=None)   # Unknown: the app ended first.
        report[UNFINISHED] = dict(left, **{KEY: block})
    return report


def blocks(report):
    """Every ``(task id, block)`` of a trial report: its tasks, its warm-up and its unfinished task."""
    found = [(task.get("task"), task[KEY]) for task in report.get("tasks") or () if isinstance(task.get(KEY), dict)]
    for name in ("warm_up", UNFINISHED):
        entry = report.get(name)
        if isinstance(entry, dict) and isinstance(entry.get(KEY), dict):
            found.append((entry.get("task") or name, entry[KEY]))
    return found


def _public(block):
    return {key: value for key, value in block.items() if not key.startswith("_")}


def _write(path, data):
    try:
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps(data, default=str), encoding="utf-8")
        partial.replace(path)
    except OSError:
        pass   # Evidence only: a full disk never stops the task.


def _remove(path):
    try:
        path.unlink()
    except OSError:
        pass
