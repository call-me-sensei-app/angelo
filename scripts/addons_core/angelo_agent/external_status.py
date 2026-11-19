# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connected app's task status (doc/angelo/external_mcp.rst, "Status"): what Angelo's agent is doing now, for how
long, what it has cost so far and, once the turn ends, its result.

A task is the turn an ``angelo_send`` started: its id is ``<conversation>:<row>``, the row of the app's message. Its
usage is the chat's ledger total (``usage_meter``, the request in progress included as a lower bound) less the total
when it was sent. Model-facing: English, never translated; no account identifier. Main thread; no bpy.
"""

import time

from . import external_transcript as transcript

RESULT_TEXT = 4000


def task_id(conversation, row):
    return "%s:%d" % (conversation, row)


def parse_task(value, conversation):
    """The row a started task's id names in ``conversation``, or None (a held task, another conversation's, or not a
    task id)."""
    parts = value.rsplit(":", 1) if isinstance(value, str) else ()
    if len(parts) != 2 or parts[0] != conversation or not parts[1].isdigit():
        return None
    return int(parts[1])


def belongs(value, conversation):
    """Whether ``value`` is a task of ``conversation``: started (``<conversation>:<row>``) or held
    (``<conversation>:h<pending id>``: a message queued or steered while a turn ran)."""
    parts = value.rsplit(":", 1) if isinstance(value, str) else ()
    return (len(parts) == 2 and parts[0] == conversation
            and (parts[1].isdigit() or parts[1][:1] == "h" and parts[1][1:].isdigit()))


def track(tasks, held, controller, delivered, before):
    """The task an app's send just made, recorded: a started one at its row (its spend measured from now); a held one
    (queued or steered) by its own pending message, its id allocated now and bound when it is delivered (``settle``).
    2.0.1 audit P2-3: every message queued behind one turn got the same id from the transcript's length."""
    conversation = controller.state["id"]
    if delivered == "started":
        task = task_id(conversation, before)
        tasks[task] = usage(controller)
        return task
    item = controller.inbox.items()[-1]   # The message chat_pending.hold just appended for this send.
    joined = next((name for name in reversed(tasks) if belongs(name, conversation)), None)
    task = "%s:h%d" % (conversation, item.id)
    tasks[task], held[task] = None, (item, joined)
    return task


def settle(tasks, held, controller):
    """Bind each held task delivered since: a queued one to its request's row, its spend from then
    (``Pending.row``, ``usage_start``: chat_pending.dispatch_due). A steer the running turn took stays bound to the
    task it joined (``task_row``), its spend counted there; one still waiting, or removed unsent, has no row."""
    for task, (item, _joined) in list(held.items()):
        if item.row is not None and tasks.get(task) is None:
            tasks[task] = item.usage_start or {}


def task_row(task, controller, held):
    """The transcript row ``task``'s result is read from, or None while it has none (not sent yet, or removed)."""
    conversation = controller.state["id"]
    row = parse_task(task, conversation)
    if row is not None or task not in held:
        return row
    item, joined = held[task]
    if item.row is not None:
        return item.row
    from .steering import DELIVERING   # A steer the running turn took: gone from the tray while a runtime had it.
    waiting = {other.id for other in controller.inbox.items()}
    return parse_task(joined, conversation) if joined and item.phase == DELIVERING and item.id not in waiting else None


def usage(controller):
    """The conversation's usage so far: ``{"input", "cache_read", "cache_write", "output", "usd", "requests",
    "unpriced", "partial", "models", "live"}``; ``live`` when a request is still running (its numbers are lower
    bounds). Never raises: an unreadable ledger reads as nothing spent."""
    from . import usage_meter
    try:
        ledger = usage_meter.UsageLedger.shared(controller.telemetry.directory)
        record = ledger.chat(controller.state["id"]) or {}
        metrics = getattr(controller, "_request_metrics", None)
        live = usage_meter.in_progress(metrics, controller.state.get("provider")) \
            if getattr(controller, "busy", False) else None
        modes = usage_meter.combined([record.get("modes"), live])
    except Exception:  # noqa: BLE001 - status is display: no figure rather than a failed read.
        modes, live = {}, None
    total = dict(dict.fromkeys(usage_meter.TOKEN_KEYS + usage_meter.COUNT_KEYS, 0), usd=0.0, models=[])
    for bucket in modes.values():
        usage_meter.add_to({"all": total}, "all", bucket)
    total["usd"] = round(total["usd"], 6)
    total["live"] = bool(live)
    return total


def since(now, before):
    """``now`` less ``before`` (two ``usage`` values): what one task spent."""
    if not before:
        return now
    spent = {key: max(0, now.get(key, 0) - before.get(key, 0)) for key in now
             if isinstance(now.get(key), int) and not isinstance(now.get(key), bool)}
    spent["usd"] = round(max(0.0, now.get("usd", 0.0) - before.get("usd", 0.0)), 6)
    spent["models"], spent["live"] = list(now.get("models", ())), now.get("live", False)
    return spent


def step(controller):
    """What the running turn is doing now: ``{"tool"}`` of the step in progress, or ``{"activity"}`` (the
    controller's own status line, English), or None between turns."""
    from . import live_progress
    if not controller.turn_running():
        return None
    try:
        facts = live_progress.facts(controller)
    except Exception:  # noqa: BLE001
        return None
    if facts.tool is not None:
        return {"activity": "waiting for the person's tool in the viewport"}
    if facts.step is not None:
        return {"tool": facts.step.get("tool", ""), "status": facts.step.get("status", "")}
    return {"activity": "thinking" if facts.thinking else str(controller.status)}


def elapsed(controller):
    """Seconds the running request has run, or None."""
    from . import live_progress
    started = live_progress.started(controller)
    return round(time.monotonic() - started, 1) if started is not None else None


def result(messages, row):
    """The finished task's result: the agent's last reply after ``row``, every picture id its tools got and which
    tools it ran (their names and statuses)."""
    reply, images, tools = "", [], []
    for message in messages[row + 1:]:
        role = message.get("role")
        if role == "user" and message.get("request_kind") in {None, "external_task"}:
            break   # The next task's message: what follows is the next task's.
        found = transcript.app_row(0, message) if role in {"assistant", "activity"} else None
        if found is None:
            continue
        if role == "assistant":
            reply = found["text"]
        else:
            tools.append({"tool": found["tool"], "status": found["status"]})
            images += [identity for identity in found.get("images", ()) if identity not in images]
    text = reply if len(reply) <= RESULT_TEXT else reply[:RESULT_TEXT]
    return {"summary": text, "truncated": len(reply) > RESULT_TEXT, "images": images, "tools": tools}


UNDECIDED = ("Chosen at this conversation's first message: the person's connection in Angelo, or the model the app "
             "picks with angelo_settings")


def route(controller):
    """Which kind of the person's connections runs the conversation, never an account identifier. Before its first
    turn, a conversation no connection is bound to yet (``selection_connection_id``: bound at its first message or the
    app's model choice) whose header is still the placeholder (``EXTERNAL_PROVIDER``) is undecided, never that runtime
    (2.0.1 acceptance: new_chat said codex, then ran on Claude). A provider the chat chose (a personal key binds no
    connection id) or a turn that ran is the connection's."""
    from .provider_registry import TEAM_MODES, billing_kind, runtime_kind
    from .external_host import EXTERNAL_PROVIDER   # The header a new External conversation is made with.
    ran = controller.turn_running() or any(message.get("role") in ("assistant", "activity")
                                           for message in controller.state.get("messages") or ())
    if (controller.state.get("provider") == EXTERNAL_PROVIDER and not ran
            and not getattr(controller, "selection_connection_id", None)):
        return {"runtime": "", "billing": "", "model": "", "decided": False, "note": UNDECIDED}
    provider = controller.state.get("provider") or ""
    try:
        kind = runtime_kind(provider)
    except (KeyError, ValueError):
        kind = ""
    try:   # A custom endpoint is billed by the organization's own server: an API key's.
        billing = ("team" if provider in TEAM_MODES else "API key" if billing_kind(provider) == "API_KEY"
                   else "subscription")
    except ValueError:
        billing = "subscription"
    return {"runtime": kind, "billing": billing, "model": (controller.state.get("options") or {}).get("model") or ""}
