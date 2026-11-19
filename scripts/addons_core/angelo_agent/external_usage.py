# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an app's conversation has spent, as the app reads it (``angelo_usage``). Main thread, read-only, no I/O.

* **Turns**: each finished model turn of the conversation (its receipt, ``telemetry``), newest first: the model
  requests it made, input (new), cache-read, cache-write and output tokens and the estimate at list price
  (``usage_meter.receipt_entries``: each turn priced at its requests' mean prompt size, so a turn whose requests
  averaged past a model's price step is priced above it; ``unpriced`` for a model the table doesn't know, never $0;
  ``partial`` for a turn whose usage a runtime did not fully report).
* **Tasks**: each of the app's tasks (an ``angelo_send``) with what it spent, from the conversation's ledger total
  when it was sent to the next task's (``external_status.since``).
* **Conversation**: the ledger's total plus the request in progress (lower bounds while ``live``).
* **Goal**: the held goal's budget, spend, rounds and time (``goal_controls.summary``).
* **Plan**: a subscription's usage windows as its runtime last reported them (``usage_windows``: used fraction and
  reset time), never asked for; ``null`` for an API key or a team connection, whose spend is the estimate.
* **Jobs**: Angelo's jobs (renders, bakes, simulations) run on this computer and spend no credits.

``billing`` says how to read the figures: on a subscription the estimate is what the same tokens would cost at list
price, not a charge. doc/angelo/external_mcp.rst, "Usage".
"""

from . import external_status as status
from . import usage_meter

MAX_TURNS = 20
JOBS = {"credits": None, "note": "Angelo's jobs run on this computer and spend no credits"}


def turn_usage(receipt, default_mode=None):
    """One receipt's own spend and its consultations', summed: tokens, ``requests``, ``usd``, ``unpriced``,
    ``partial`` and ``models``; None when it reported no usage."""
    entries = usage_meter.receipt_entries(receipt, default_mode)
    if not entries:
        return None
    total = dict(dict.fromkeys(usage_meter.TOKEN_KEYS + usage_meter.COUNT_KEYS, 0), usd=0.0, models=[])
    for _mode, value in entries:
        usage_meter.add_to({"all": total}, "all", value)
    total["usd"] = round(total["usd"], 6)
    usage = receipt.get("usage") or {}
    total["requests"] = usage.get("requests") or total["requests"]   # Model requests inside the turn.
    total["status"] = receipt.get("status")
    total["seconds"] = round((receipt.get("phases") or {}).get("total_ms", 0) / 1000.0, 2)
    return total


def turns(receipts, chat_id, default_mode=None, limit=MAX_TURNS):
    """The conversation's finished turns with usage, newest first (``receipts``: the telemetry's, any chat's)."""
    rows = []
    for receipt in reversed(list(receipts)):
        if receipt.get("session_id") != chat_id or receipt.get("kind") == "warm_up":
            continue
        value = turn_usage(receipt, default_mode)
        if value is not None:
            rows.append(value)
            if len(rows) >= limit:
                break
    return rows


def tasks(starts, now):
    """``[{"task", "usage"}]`` for the app's tasks, oldest first: ``starts`` maps each task to the conversation's
    total when it was sent (``ExternalHost._tasks``); each spent up to the next one's start, the last up to ``now``."""
    started = [(task, before) for task, before in starts.items() if before is not None]
    spent = {}
    for index, (task, before) in enumerate(started):   # A held task not sent yet has no start, and spent nothing.
        after = started[index + 1][1] if index + 1 < len(started) else now
        spent[task] = status.since(after, before)
    return [{"task": task, "usage": spent.get(task)} for task in starts]


def plan(controller, route):
    """The subscription's usage windows, or None (an API key's or team's spend is the estimate)."""
    if route.get("billing") != "subscription":
        return None
    from . import usage_windows
    try:
        found = usage_windows.current(usage_windows.connection_key(controller))
    except Exception:  # noqa: BLE001 - display only: no windows rather than a failed read.
        return None
    if not found:
        return None
    return {"windows": [{"kind": w["kind"], "used": w.get("used"), "resets_at": w.get("resets_at"),
                         "minutes": w.get("minutes")} for w in found["windows"]],
            "limited": bool(found.get("limited")), "overage": bool(found.get("overage")), "reported_at": found["at"]}


def goal(controller):
    from . import goal_controls, goal_loop
    held = goal_loop.goal_of(controller)
    return goal_controls.summary(held) if held is not None else None


def summary(controller, starts, receipts):
    """Everything ``angelo_usage`` answers for one conversation (``starts``: this app's task starts in it)."""
    route = status.route(controller)
    now = status.usage(controller)
    mine = {task: before for task, before in starts.items()
            if status.belongs(task, controller.state["id"])}
    return {"billing": route["billing"], "conversation": now,
            "turns": turns(receipts, controller.state["id"], controller.state.get("provider")),
            "tasks": tasks(mine, now)[-MAX_TURNS:], "goal": goal(controller), "plan": plan(controller, route),
            "jobs": dict(JOBS),
            "pricing": "Each turn is priced at its requests' mean prompt size against the model's price step"}
