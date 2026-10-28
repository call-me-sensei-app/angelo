# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker chat's card needs beyond the call's arguments, rebuilt on the lead from its pending summary (A′, C3).

A window's chat prepares a typed edit's review when it admits the call (``approval_review.prepare_review``): the card
of ``angelo_scene_program`` and ``angelo_apply_recipe`` draws only from that prepared review. A mirror admits nothing,
so it prepares the same review from what the worker's ``pending`` summary carries (``worker_chat_events.review_fields``),
once per call it has not drawn before (``worker_mirror_events.pending_calls``):

* ``program_arguments``: the host-expanded program a recipe call shows and stages;
* ``recovery_review``: a renewed approval after a recovery;
* ``precondition`` and ``recovery_evidence``: exact host values (``worker_values.encode``; a program's binding stays a
  ``ProgramBinding`` with its ``FrozenObligations``), decoded here with ``worker_values.decode``. ``prepare_review``
  checks the obligations against the program as on admission. A summary that names a value it could not encode
  (``unencoded``), or one that does not decode, prepares no review.

The review is presentation only, as in any chat: the worker approves only the digest the card drew (C6), so nothing
rebuilt here can widen what runs. A program whose review cannot be prepared draws no card; the chat says so once,
so the person knows to Stop the turn rather than wait (the error itself is logged); the row is the lead's own, kept
across reloads. Main thread only. Standard library only and no ``bpy``.
"""

from .ui_text import n_, notice

NO_REVIEW = n_("A change in this chat is waiting for approval, but its review could not be prepared here. "
               "Stop the turn and ask again.")


def prepare(call, summary):
    """Fill ``call`` (a mirror's new ``ToolCall``) from its summary and prepare its review (module docstring).
    Returns an error row's fields when a program's review could not be prepared, else None."""
    from . import approval_review, worker_values
    call.recovery_review = summary.get("recovery_review") is True
    unencoded = summary.get("unencoded")
    try:
        call.precondition = worker_values.decode(summary.get("precondition"))
        call.recovery_evidence = worker_values.decode(summary.get("recovery_evidence"))
    except (TypeError, ValueError, RecursionError) as error:
        call.precondition = call.recovery_evidence = None
        unencoded = unencoded or [type(error).__name__]
    if call.name not in approval_review._PROGRAM_TOOLS or not call.approval:
        return None
    try:
        if unencoded:
            raise ValueError("its worker could not send %s" % ", ".join(str(name) for name in unencoded)[:120])
        call.precondition = approval_review.prepare_review(call, call.precondition)
    except Exception as error:  # noqa: BLE001 - a review that cannot be prepared is never drawn.
        approval_review.clear_review(call)
        print("Angelo: a worker chat's review could not be prepared:", type(error).__name__, str(error)[:200])
        return {"role": "error", **notice(NO_REVIEW)}
    return None
