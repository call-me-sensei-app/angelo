# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's own merge waits while that chat has another scene edit bound and not yet run.

A scene edit binds the scene as its approval request saw it (``Controller._precondition``: a script binds every
object's transform, data and modifier count) and is refused ``STALE_REVISION`` when the scene differs at its run. A
merge (``angelo_workers_merge``) adds a worker's whole part to the scene, so a merge of the same chat that ran in that
window refused the chat's own edit, and the refusal opened a recovery hold. The chat itself chose both calls; neither
is stale to it. So a merge waits, still pending and approved as it was, while another call of the same chat holds a
binding (``precondition``), modifies the scene (``request_recovery.scene_mutation``) and has not begun its live
change (``mutation_started``): awaiting its approval, approved, or staging (a native program or a preview's
acceptance stages for several polls with ``executing`` set before its live commit, break-it MH2-M-3). It runs once
none does: the edit reached its terminal outcome (it ran or committed, was refused, declined, cancelled, discarded
or expired: every one leaves ``pending``), or began its live change, which runs to its end on the main thread
before the pump runs again. Merges do not wait for one another
(a merge binds its parts' saved files, never the scene), and another chat's or a worker's edits follow their own
rules. Recorded (C3 early town-centre diagnostic, Sol on Codex): the lead's background merge loop merged a part while
the lead's script waited for its approval; the script was refused STALE_REVISION and the hold that followed refused
every later merge.

The wait is checked at the merge's execution (``Controller._execute_tool``), which every approval route reaches (an
automatic approval, a person's click, a headless host's approval): nothing of the merge runs, execution returns
``WAITING``, and the Undo operator pushes no step for that outcome. A merge stopped there (``ToolCall.merge_waits``) is applied again by the pump (``resume``) through the
same approval route once nothing holds it; Auto does not reapply it while it waits.
With A' the order is the requests' (``deferred``): a merge's gate is asked without waiting, so an edit the chat asked
for after it could otherwise bind first and then hold it, and in Ask the worker-done event's merge waited on that
edit's card (R7: parallel_resume@hosted). An edit of the chat admitted after a pending merge therefore binds only once
the merge has run or been refused (left ``pending``): it stays queued, unbound and without a card, and then binds
against the merged scene. A merge waits only for edits asked before it. The lanes keep their order unchanged.
With A' a merge whose chat holds no such edit stops there once more while its parts' chats are held and files
leased (``worker_merge_gate``: asked without waiting, so the operator returns at once); the pump applies it once every
answer is in, and its reads run under them. A merge still waiting when its own lifetime ends (``tool_deadlines``) expires with that
reason (``expired``); its lifetime is never extended, so the provider never waits less than the host keeps it.
"""

MERGE = "angelo_workers_merge"
WAITING = {"merge_waiting": True}   # What execution returns for a merge that waits: nothing ran (no Undo step).


def _edit(call):
    """A scene edit a merge could change the scene under, not begun yet."""
    from .request_recovery import scene_mutation
    return call.name != MERGE and not call.mutation_started and scene_mutation(call.name, call.arguments)


def blocker(controller, call):
    """The pending call of ``controller``'s chat a merge ``call`` would change the scene under, or None: a bound edit,
    or (A') one asked before the merge that is itself waiting to bind behind an earlier merge (``deferred``)."""
    if call.name != MERGE:
        return None
    earlier = True
    for other in list(controller.pending.values()):
        if other is call:
            earlier = False
        elif _edit(other) and (other.precondition is not None
                               or earlier and getattr(other, "binds_after_merge", False)):
            return other
    return None


def deferred(controller, call):
    """A' only, at an edit's binding in the pump: True (put back on the chat's queue, the window never waits) while a
    merge of its chat asked before it is still pending; False once none is, when it binds as any edit."""
    from . import worker_chat_lifecycle
    call.binds_after_merge = False
    if call.name == MERGE or not worker_chat_lifecycle.enabled() or not _edit(call):
        return False
    for other in list(controller.pending.values()):
        if other is call:
            return False
        if other.name == MERGE:
            call.binds_after_merge = True
            controller.tools.put_nowait(call)
            return True
    return False


def waits(controller, call):
    """At a merge's execution: True (the call marked to resume) while another bound edit of its chat waits; False,
    with the mark cleared, when its execution is admitted, by whichever approval route reached it (break-it MH-M-3)."""
    from . import worker_merge_gate   # Then (A') its parts' holds and leases, asked without waiting.
    call.merge_waits = blocker(controller, call) is not None or worker_merge_gate.waits(controller, call)
    return call.merge_waits


def resumable(controller, call):
    """A merge stopped at execution whose chat no longer holds a bound edit: the pump applies it again."""
    from . import worker_merge_gate
    return (call.merge_waits and not call.executing and blocker(controller, call) is None
            and not worker_merge_gate.asking(controller, call))


def resume(controller, call, document, apply_mutation, budget):
    """The pump's step for a stopped merge: apply it through its approval route once nothing holds it (True)."""
    if apply_mutation is None or not resumable(controller, call):
        return False
    budget.admissions += 1
    call.merge_waits = False
    if controller._tool_available(call, document):
        budget.executions += 1
        apply_mutation(call)
    return True


def expired(call):
    """Why a merge that waited until its own deadline expired (``Controller.expire_tool``), or None for any other call."""
    if not call.merge_waits:
        return None
    return ("This merge waited for another scene edit of this chat to run (its approval or its run) until the merge's "
            "own deadline passed; nothing was merged, and the worker's part is unchanged, so it can be merged again")
