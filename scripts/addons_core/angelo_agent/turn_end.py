# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a turn's end does to the calls it admitted, and what the chat then says about them.

A turn that ends cancels each of its calls that never started (``Controller.cancel_tools``): an approval card nobody
answered, a call still queued or held for its scene. Its card then reads Stopped. Every provider holds a waiting
call open until the host answers it (``tool_deadlines``, ``runtime_launch.HOLD_DYNAMIC_TOOLS``), so a turn rarely
ends with one; but when it does, the model's last words were written while the call was still waiting, and may say
so ("awaiting script approval", 1.0.8 B-007), while the end itself takes the card away. So the host says what
happened, under those words: the step didn't run, and the person can ask again. The card and the chat then agree,
and nothing on screen promises an approval that no longer exists.

The design keeps the end's revocation (``tool_deadlines.revoke_turn_calls``): a call is never run for a turn
nobody is waiting on, so an approval click after the end can never start work whose result no model receives.

No ``bpy``: host-testable.
"""

from . import ui_text
from .ui_text import n_

ENDED = "The agent turn has ended"   # i18n: the cancelled calls' own error (plan_recovery reads it), never shown
NOT_RUN = n_("The turn ended while a step was still waiting, so it didn't run. Ask again if you still want it.")
# What the model reads of the account (ui_text.model_notice): what happened, without instructing it.
MODEL_ACCOUNT = ("Host: the turn ended while a tool call was still waiting "   # i18n: model-facing
                 "(for the person's approval or in the queue), so the host "   # i18n: model-facing
                 "cancelled it: it did not run and changed nothing.")   # i18n: model-facing


def never_started(host, call):
    """Whether ``call`` did no work yet: waiting for its approval, queued or held, not running, staging or a job."""
    return (not (call.executing or call.committing or call.staging or call.mutation_started)
            and call.id not in host.media_jobs and call.id not in host.delegate_jobs)


def lead_report(end, text, ended_unrun):
    """A worker chat's turn end as its lead reads it (``session_rail.turn_end``'s ``(end, text)``), with the account
    when its forwarded end said a step never ran (``ended_unrun``): a hosted worker's account row reaches the lead's
    mirror only with a later journal read, after the turn's outcome was recorded."""
    from .session_rail import NO_REPLY
    if end != "completed" or not ended_unrun or MODEL_ACCOUNT in text:
        return end, text
    return end, "\n\n".join(part for part in (text if text != NO_REPLY else "", MODEL_ACCOUNT) if part)


def stopped(host):
    """Whether the person stopped this turn: Stop pressed here (``cancelled``) or arrived for a worker's hosted chat
    before it was applied (``stop_arrived``). Stop's own notice then says what it stopped."""
    arrived = getattr(host, "stop_arrived", None)
    return bool(getattr(host, "cancelled", False)) or (callable(arrived) and bool(arrived()))


def end_turn_calls(host):
    """Cancel the ended turn's calls (``cancel_tools``); when one that never started was among them, or this request's
    end revoked one (``tool_deadlines.revoke_turn_calls``), add the host's account under the turn's last words.

    Called before the turn's outcome is recorded, so the account is part of the turn: a worker's report to its lead
    carries it (``session_rail.turn_end``), and it stays unsynced, so the chat's own model reads it with its next
    request (its ``model_text``). The end's record is popped whole: a request whose ``finished`` was dropped (a
    restore, a reconnect) never lends its record to a later turn. True when the account was added.
    """
    waiting = [call.id for call in list(host.pending.values()) if never_started(host, call)]
    host.cancel_tools(ENDED)
    with host.lock:
        record = host.__dict__.pop("unrun_by_fence", {})
        revoked = record.get((getattr(host, "_epoch", None), getattr(host, "_request_generation", None)))
        if stopped(host) or not (revoked or any(call_id not in host.pending for call_id in waiting)):
            return False
        index = len(host.state["messages"])
        host.state["messages"].append({"role": "system", "turn_end": True, **ui_text.model_notice(MODEL_ACCOUNT, NOT_RUN)})
        unsynced = getattr(host, "_native_unsynced_from", None)
        host._native_unsynced_from = index if type(unsynced) is not int else min(unsynced, index)
    return True
