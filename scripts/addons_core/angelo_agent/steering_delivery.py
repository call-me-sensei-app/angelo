# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's waiting steers handed to a running request at a safe step of its runtime (``steering``).

Each route calls these only while its request still owns the chat (the provider's own ownership check first), and
a steer counts as delivered only once the model has read it:

* **Host-written rounds** (Claude typed plans before each later round; a Codex plan's correction turn):
  ``in_prompt`` puts the steers after the round's prompt, ``shown`` adds their rows once it is written, then
  ``settle``: delivered when the round answered while its request still owned the chat, else returned (their rows
  are withdrawn at the turn's end, never deleted).
* **Codex dynamic tool calls** (``codex_offer``): when no host call is outstanding (the last one was just answered,
  or none was when the person pressed Enter) the steers go into the open turn as native ``turn/steer`` user
  messages, from a thread of their own: Blender's main thread never waits for the runtime. Codex reads pending
  input only when it starts its next model request, after every tool output is in, and Angelo's runtime holds a
  code-mode cell while one of Angelo's calls is unanswered (``runtime_launch.HOLD_DYNAMIC_TOOLS``), so nothing the
  model asked for is running when it reads one. Accepted is not read: an interrupt drops Codex's pending input. The
  message is delivered only when its user item (``clientId``) has appeared and the model's output follows
  (``codex_event``); its row is added then, just before that output.

What a turn never read comes back at its end (``steering.Inbox.turn_ended``). The words are the person's, passed on
unchanged; they answer no approval.
"""

import threading

from .protocol import RpcError

STEER_HEADING = "Message from the person, sent while you were working:"   # Model-facing: never translated.
STEER_SECONDS = 10.0   # A turn/steer answer comes at once; without one the message is offered again, never lost.
# Codex events that are the model's own output: a steer seen before one of them was read by that model request.
MODEL_OUTPUT = ("item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/tool/call")
MODEL_ITEMS = frozenset({"agentMessage", "reasoning", "dynamicToolCall"})


def _host(context):
    return getattr(context, "host", context)


def _inbox(context):
    return getattr(_host(context), "inbox", None)


def _stopping(context):
    return bool(getattr(_host(context), "cancelled", False) or getattr(context, "cancelled", False))


def take(context):
    """The waiting steers for ``context``'s request, now being delivered; [] when there are none, it is stopping, or
    the request is not the person's (Angelo's own, a connected app's task): the person's words go only into a
    request of theirs, else into their next one."""
    inbox, host = _inbox(context), _host(context)
    if (inbox is None or _stopping(context) or getattr(host, "_host_request", None) is not None
            or getattr(host, "_external_task", False)):
        return []
    return inbox.take_steers()


def for_model(context, text):
    """The person's words as the model gets them: redacted as every message is (``Controller.redact``)."""
    redact = getattr(_host(context), "redact", None)
    return redact(text) if callable(redact) else text


def in_prompt(context, prompt):
    """``(prompt with the waiting steers after it, the steers)`` for a host-written round."""
    items = take(context)
    return with_steers(prompt, [for_model(context, item.text) for item in items]), items


def with_steers(prompt, texts):
    return prompt + "".join("\n\n" + STEER_HEADING + "\n" + text for text in texts)


def shown(context, items):
    """A host-written round carrying ``items`` was written: their rows go in now, before its answer."""
    inbox = _inbox(context)
    for item in inbox.mark(items, shown=True) if inbox is not None and items else ():
        context.emit("steer_shown", {"id": item.id, "text": item.text})


def settle(context, items, answered):
    """After a host-written round. Only evidence counts: a Claude round that was written (``shown``) or a Codex turn
    whose own message Codex echoed (``seen``) put them in the agent's conversation; those are read when the round
    ``answered`` (a row added if they had none), else they stay the request's until its end, never sent again
    (``steering.Inbox.turn_ended``). Every other one goes back to wait (or paused)."""
    inbox = _inbox(context)
    if not items or inbox is None:
        return
    reached = [item for item in items if item.shown or item.seen]
    inbox.returned([item for item in items if item not in reached], "stopped" if _stopping(context) else None)
    if answered:
        gone = inbox.delivered(reached)
        for item in gone:
            if not item.shown:
                context.emit("steer_shown", {"id": item.id, "text": item.text})
        _count(context, len(gone))


def _count(context, delivered):
    metrics = getattr(_host(context), "_request_metrics", None)
    if delivered and metrics is not None:
        metrics.count("steers_delivered", delivered)


# Codex dynamic tool calls ------------------------------------------------------------------------------------------

def codex_offer(provider, *, start=None):
    """Send the waiting steers into the provider's open turn when no host call is outstanding; never blocks."""
    scope, context = provider._scope, provider.controller
    if context is None or not getattr(provider, "steer_ready", False):
        return None
    with scope.lock:
        if (not scope.current(context) or scope.completed or scope.starting or not scope.turn_id
                or not scope.thread_id or scope.responses):
            return None
        thread_id, turn_id, rpc = scope.thread_id, scope.turn_id, provider.rpc
        items = take(context)
    if not items:
        return None
    if rpc is None:
        _inbox(context).returned(items)
        return None

    def send():
        for item in items:
            try:
                rpc.request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id,
                                           "clientUserMessageId": item.client_id,
                                           "input": [{"type": "text", "text": for_model(context, item.text)}]},
                            timeout=STEER_SECONDS)
            except TimeoutError:   # No answer in time: Codex may hold it, so it is not offered again this turn;
                _inbox(context).mark([item], accepted=True)   # its echo reads it, else the turn's end gives it back.
            except Exception:   # noqa: BLE001 - refused (no active turn, another turn), or never written: not taken.
                _inbox(context).returned([item], "stopped" if _stopping(context) else None)
            else:
                _inbox(context).mark([item], accepted=True)
    worker = threading.Thread(target=send, daemon=True, name="AngeloSteer")
    try:
        (start or threading.Thread.start)(worker)
    except RuntimeError:   # No thread could start (thread_start's case): nothing was sent, so it waits again.
        _inbox(context).returned(items)
        return None
    return worker


def echo(method, params):
    """Codex echoing a user message of the turn: evidence only (it admits no work), so it counts even after Stop."""
    item = params.get("item") if method == "item/started" and isinstance(params, dict) else None
    return isinstance(item, dict) and item.get("type") == "userMessage"


def codex_event(context, method, params, plan=None):
    """Codex's events while a steer is out: its user item marks it seen; the model's next output delivers it.
    ``plan`` (a plan turn's ``codex_plan.PlanTurns``): the final messages before a read steer were answered by it,
    so only the ones after it can supersede the final plan (``PlanTurns.final_plan``)."""
    inbox = _inbox(context)
    if inbox is None or not inbox.delivering():
        return
    item = params.get("item") if isinstance(params, dict) else None
    kind = item.get("type") if isinstance(item, dict) else None
    if method == "item/started" and kind == "userMessage" and isinstance(item.get("clientId"), str):
        inbox.mark(inbox.delivering(item["clientId"]), seen=True)
        return
    if method == "item/started" and kind == "userMessage":   # A host-written turn's own message, as Codex took it.
        inbox.mark([entry for entry in inbox.delivering() if entry.carried], seen=True)
        return
    if method in MODEL_OUTPUT or method == "item/started" and kind in MODEL_ITEMS:
        read = [entry for entry in inbox.delivering() if getattr(entry, "seen", False)]
        for entry in inbox.delivered(read):
            context.emit("steer_shown", {"id": entry.id, "text": entry.text})
        _count(context, len(read))
        if read and plan is not None:
            plan.steered = len(plan.messages)


def plan_turn(provider, native_id, text, turns):
    """A Codex plan's correction turn (``codex_plan.finish``) with the person's waiting steers after its message."""
    context = provider.controller
    text, items = in_prompt(context, text)
    if items:   # Seen once Codex echoes this turn's own message (``codex_event``): the evidence it has them.
        _inbox(context).mark(items, carried=True)
    status = None
    try:
        status = provider.plan_turn(native_id, text, turns)
    finally:
        settle(context, items, status == "completed")
    return status
