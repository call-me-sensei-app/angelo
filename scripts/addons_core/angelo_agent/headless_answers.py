# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless host's held answers (``HeadlessHost.finish_answers``).

Without the window an app call's answer waits for the save or reload done after it (``HeadlessHost.after_call``).
Each goes out here, on the main thread: counted out first, which ends the call's turn so the request's next call may
run (``ExternalWork.count_out``), and then answered. Both happen whatever the save or reload did: one that raised
answers with the error, never leaving the call counted as in flight or its turn held (break-it review 2026-11-13).

A delivery asked for again (the launcher asks under the same call ID every round while it waits) is answered with
exactly what went out the first time, the save's note or error included: that answer is kept on the call's own record
(``ExternalApps._calls``), so it lives as long as the host knows the call, and a replay never rebuilds an answer from
the tool's raw result. A replay that reaches the main thread before its call's first answer went out waits for it;
past ``REPLAY_WAIT_S`` from the call's first such replay it says the answer is not kept, never that the call succeeded.
"""

import copy
import json
import queue
import time

from .claude_direct import _error
from .claude_direct_notes import NOT_KEPT

# How long a call's replays wait for its first answer. The request queues that answer in the same tick as it gives the
# replay the result, so this bounds only an answer that was lost.
REPLAY_WAIT_S = 60.0


def finish(host, clock=time.monotonic):
    """Main thread: answer every held delivery in order. Replays still waiting for their first answer stay for the next
    tick; if one delivery raises, the ones after it stay too and the error is raised (``HeadlessHost.finish_answers``
    logs it and keeps serving)."""
    entries = getattr(host, "_unsent", [])
    host._unsent = []
    while True:
        try:
            entries.append(host._answers.get_nowait())
        except queue.Empty:
            break
    done = 0
    try:
        for entry in entries:
            done += 1
            if entry[2]:   # replayed
                _replay(host, entry, clock)
            else:
                _answer(host, entry)
    finally:
        host._unsent = entries[done:] + host._unsent


def waiting(host):
    """Whether a held delivery is still to be answered (the host is not idle)."""
    return bool(getattr(host, "_unsent", None))


def _answer(host, entry):
    key, tool, _replayed, current, value, respond = entry
    note = None
    try:
        note = host.after_call(tool, current, value)
    except Exception as error:  # noqa: BLE001 - the answer still goes out, saying what failed.
        value = _error("Angelo could not save or reload after this call (%s: %s). Observe the scene again."
                       % (type(error).__name__, error))
    finally:
        if current is not None:
            current.count_out(key[1])   # In flight until now; its turn ends after its save or reload.
    answer = with_note(value, note)
    record = host._calls.get(key)
    if record is not None:
        record["answer"] = copy.deepcopy(answer)   # Exactly what went out, for a delivery asked for again.
    respond(answer)


def _replay(host, entry, clock):
    key, respond = entry[0], entry[5]
    record = host._calls.get(key)
    if record is not None and "answer" in record:
        respond(copy.deepcopy(record["answer"]))
        return
    if record is not None and clock() - record.setdefault("replay_since", clock()) < REPLAY_WAIT_S:
        host._unsent.append(entry)   # Its first answer has not gone out yet: one deadline for all its replays.
        return
    respond(_error(NOT_KEPT))


def with_note(value, note):
    if not note or not isinstance(value, dict) or not isinstance(value.get("content"), list):
        return value
    return dict(value, content=[{"type": "text", "text": note}, *value["content"]])


def nothing_ran(value):
    """Whether an MCP result is a refusal before anything ran: its host result block says ``NEEDS_GUI`` and
    ``scene_changed`` false. Decided from those fields, never from words an error could echo (review N2)."""
    for block in (value.get("content") or []) if isinstance(value, dict) else ():
        try:
            result = json.loads(block.get("text", "")) if isinstance(block, dict) else None
        except ValueError:
            continue
        if isinstance(result, dict) and result.get("error_code") == "NEEDS_GUI" and result.get("scene_changed") is False:
            return True
    return False
