# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an app session's live page shows, built from its conversation (``session_page``). Pure: no Blender.

``build`` reads the rows an app reads (``external_transcript.app_row``: never a thinking row, a row only the person
sees or an account identifier) and keeps less of them: what the app sent, the agent's words and Angelo's notices,
and each tool call's name and status, never its result text or arguments. Private paths are projected as the app's
own results are (``external_requests.project``). Then the status in the person's terms, the tool working now, the
goal's round and score, the time, the usage and cost as the chat's bar says them, the result once the turn ended, and
the latest pictures the agent got, by id. Only this conversation: nothing names another chat.
"""

from . import external_transcript as transcript
from .external_requests import project

ROWS = 40
PICTURES = 6
WORKING = ("running", "waiting_for_approval", "waiting_for_person", "stopping")
USAGE_KEYS = ("tokens", "money")   # This conversation's tokens and cost: never a plan's name or window (account-wide).


def _rows(messages):
    """The stream: (rows, whether the app sent anything)."""
    rows, sent = [], False
    for index, message in enumerate(messages):
        row = transcript.app_row(index, message)
        if row is None:
            continue
        if row["role"] == "tool":
            row = {"i": row["i"], "role": "tool", "tool": row["tool"], "status": row["status"]}
        else:
            row = {key: row[key] for key in ("i", "role", "text", "steer") if key in row}
            sent = sent or row["role"] == "app"
        rows.append(row)
    return rows[-ROWS:], sent


def _step(messages, turn):
    if turn not in WORKING:
        return None
    for message in reversed(messages):
        if message.get("role") == "activity" and message.get("status") in {"queued", "running", "approval"}:
            return message.get("tool") or None
    return None


def _result(rows):
    for row in reversed(rows):
        if row["role"] == "app":
            return None
        if row["role"] == "assistant":
            return row["text"]
    return None


def _goal(goal):
    if not goal:
        return None
    rounds = goal.get("rounds") or []
    last = rounds[-1] if rounds else {}
    return {"objective": goal.get("objective", ""), "status": goal.get("status", ""),
            "round": last.get("n", len(rounds)) if rounds else 0, "score": last.get("score"),
            "best": goal.get("best"), "pass_at": goal.get("pass_at")}


def _pictures(messages):
    found = []
    for message in reversed(messages):
        if message.get("role") != "activity":
            continue
        for identity in reversed(transcript.image_ids(message.get("result"))):
            if identity not in found:
                found.append(identity)
            if len(found) >= PICTURES:
                return found
    return found


def build(messages, *, conversation, version, turn, goal, usage, now, started, turn_started, app, roots,
          scene_version, strings=None):
    """The page's state for ``messages`` (the conversation ``conversation`` at its change counter ``version``)."""
    del version   # The page's own version counts publishes; the chat's is what the caller compares.
    rows, sent = _rows(messages)
    if turn in WORKING:
        status, result = turn, None
    else:
        status = "done" if sent else "ready"
        result = _result(rows) if sent else None
    state = {"conversation": conversation, "app": app, "status": status, "step": _step(messages, turn),
             "rows": rows, "result": result, "goal": _goal(goal),
             "usage": {key: usage.get(key) for key in USAGE_KEYS} if usage else None,
             "elapsed": max(0, int(now - started)),
             "turn_elapsed": max(0, int(now - turn_started)) if turn in WORKING and turn_started else None,
             "pictures": _pictures(messages), "scene_version": scene_version, "strings": dict(strings or {})}
    return project(state, tuple(roots))
