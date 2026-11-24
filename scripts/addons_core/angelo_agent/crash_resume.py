# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Continue a request Angelo was working on when it closed (a crash, a forced quit, power lost): 2.1.0.

**Knowing a request was cut off.** Every request row is written with ``open_turn`` (this process's token:
``request_rows.request_fields``) and loses it when its turn ends (``message_end.ended``), or when the chat's next
request starts (``close_open``). A chat loaded with its newest request still open was cut off, unless the token is
this process's or names a process still running (another Angelo on the same state folder may be running it).

**An honest summary, from the call journal only.** Each call the request admitted has a durable record written
before it ran (``tool_finalization.journal_tool``), replaced by its result when it finished. ``summarize`` sorts the
request's calls into *done* (a final result), *may have applied* (the record says execution or commit had started,
with no final read-back), *didn't run* (admitted only, or shown with no record: never admitted) and *looked*
(read-only calls, whatever their state). Never from the model's words.

**Offer, never replay.** The reopened chat gets one person-only card (``CARD``) with the counts and **Continue**;
Restore on the request (its snapshot) stays where it always is. Continue is Angelo's own request (``REQUEST_KIND``),
its text the journal's facts for the model (``model_text``). Nothing is run again by Angelo: the old invocation ids
stay closed in the chat's ledger. When something may have applied, the request starts with a hold
(``request_recovery.RecoveryBarrier.arm_continue``): scene edits wait until the model has read the recovered scene
with a read tool, and run from its next step, so no edit is written blind over a change that may already be there.
A cut-off request with nothing that may have applied starts with no hold. Standard library only; no bpy.
"""

import os
import uuid

OPEN = "open_turn"
REQUEST_KIND = "angelo_continue"
TOKEN = {"pid": os.getpid(), "run": uuid.uuid4().hex}   # This process, for the life of the add-on module.
MAX_LISTED = 12   # Calls named per group in the model's text; the rest are counted.
LIVE_PHASES = ("execution_started", "commit_started")

# The model reads this; never translated.
MODEL_TEXT = ("Angelo closed while it was working on the request above, and the person asked to continue it. From "
              "Angelo's call journal (facts, not your memory): done: %s; may have applied, with no final read-back: %s; "
              "did not run: %s. Nothing was run again. The scene is the file as it was last saved, so a done step may "
              "be missing from it. Before any scene edit, read the current scene with a read tool: a step that may "
              "have applied may be in it already, and a done step may not. Redo a done step only where the scene "
              "shows it missing. Then finish the request.")


def mark():
    """The request row's field while its turn runs."""
    return {OPEN: dict(TOKEN)}


def opened(controller):
    """A request starts (``Controller._start_turn``): every earlier request of the chat is closed now, so only the
    newest can read as cut off; returns the new row's mark."""
    for index in close_open(controller.state["messages"]):
        controller.store.touch(index)   # The journal is told of each row (called here, never handed on).
    return mark()


def closed(controller):
    """A turn ended (``message_end.ended``): its request is no longer open, and the chat file says so at once (a
    best-effort save; a crash right after it would otherwise still find the request open)."""
    rows = controller.state["messages"]
    if any(isinstance(row, dict) and OPEN in row for row in rows):
        for index in close_open(rows):
            controller.store.touch(index)
        from .tool_finalization import save_observed_state
        save_observed_state(controller)


def _alive(pid):
    """Whether process ``pid`` still runs. Never a signal-0 kill: on Windows it sends Ctrl+C to that console process
    group and answers nothing (audit 2026-11-14 F1); the engine link's check opens the process there instead
    (``engine_link_protocol.process_alive``)."""
    if type(pid) is not int or pid <= 0:
        return False
    from . import engine_link_protocol
    try:
        return engine_link_protocol.process_alive(pid)
    except OSError:
        return True   # Can't tell: treat as alive (never offer over a turn another Angelo may be running).


def _requests(rows):
    """Indices of request rows (the person's, a connected app's, Angelo's own): user rows that are no steer."""
    return [index for index, row in enumerate(rows) if isinstance(row, dict) and row.get("role") == "user"
            and row.get("steer") is not True and row.get("withdrawn") is not True]


def close_open(rows, keep=None):
    """Drop ``open_turn`` from every request row but ``keep`` (an index); returns the indices it changed, for the
    caller to tell the journal."""
    changed = []
    for index, row in enumerate(rows):
        if index != keep and isinstance(row, dict) and OPEN in row:
            row.pop(OPEN, None)
            changed.append(index)
    return changed


def cut_off(rows, alive=None, token=None):
    """The index of the newest request when it was cut off by a process that has ended, else None."""
    token, alive = TOKEN if token is None else token, _alive if alive is None else alive
    requests = _requests(rows)
    if not requests:
        return None
    index = requests[-1]
    marker = rows[index].get(OPEN)
    if not isinstance(marker, dict):
        return None
    if marker.get("run") == token["run"] or alive(marker.get("pid")):
        return None
    return index


def _short(arguments):
    """A call's arguments as a short, bounded text for the model (data, never instructions)."""
    import json
    try:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        text = "{}"
    return text if len(text) <= 240 else text[:239] + "…"


def summarize(state, index, read_only):
    """``{"done", "maybe", "not_run", "looked", "checkpoint"}`` for the request at row ``index``. Each list holds
    ``(tool, arguments, description)``; ``read_only(tool)`` says whether a tool only reads."""
    rows, results = state.get("messages") or [], state.get("results") or {}
    groups = {"done": [], "maybe": [], "not_run": [], "looked": []}
    end = next((later for later in _requests(rows) if later > index), len(rows))
    seen = set()
    for row in rows[index + 1:end]:
        if not isinstance(row, dict) or row.get("role") != "activity" or row.get("call_id") in seen:
            continue
        seen.add(row.get("call_id"))
        tool = row.get("tool") or ""
        arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        entry = (tool, arguments, str(arguments.get("description") or "")[:160])
        groups[_group(row, results.get(row.get("call_id")), read_only(tool))].append(entry)
    groups["checkpoint"] = rows[index].get("checkpoint") if isinstance(rows[index], dict) else None
    return groups


def _group(row, record, read_only):
    """Which group a call of the cut-off request is in. Its journal record decides; a record the bounded journal no
    longer holds (``tool_finalization``: 256 per chat) falls back to the row, and a call the row shows as running
    counts as may have applied, as does one it shows as failed (a script can fail after it changed the scene): a
    summary never says "didn't run" of something that may have."""
    if read_only:
        return "looked"
    result = record.get("result") if isinstance(record, dict) else None
    if not isinstance(result, dict):
        status, shown = row.get("status"), row.get("result")
        if status == "completed" and isinstance(shown, dict) and shown and not shown.get("error"):
            return "done"
        return "not_run" if status in (None, "queued", "approval", "waiting") else "maybe"   # Failed: it may have run.
    if result.get("interrupted") is True:
        live = record.get("phase") in LIVE_PHASES or result.get("partial_changes_possible") is True
        return "maybe" if live else "not_run"
    if result.get("error"):
        return "maybe" if result.get("partial_changes_possible") is True else "not_run"
    return "done"


def _listed(entries):
    if not entries:
        return "none"
    named = ["%s %s" % (tool, _short(arguments)) for tool, arguments, _ in entries[:MAX_LISTED]]
    more = len(entries) - MAX_LISTED
    return "; ".join(named) + (" and %d more" % more if more > 0 else "")


def model_text(summary):
    return MODEL_TEXT % (_listed(summary["done"]), _listed(summary["maybe"]), _listed(summary["not_run"]))


def parts(summary):
    """The Continue row's counts (``console.display_text``): done, may have applied, didn't run."""
    return [len(summary["done"]), len(summary["maybe"]), len(summary["not_run"])]
