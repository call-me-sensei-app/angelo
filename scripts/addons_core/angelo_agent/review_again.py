# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A card whose binding changed while it was on screen asks the person again, instead of refusing their click.

A call binds what its card showed (``Controller._precondition``: a script the scene's revision, frame, mode and
objects, a transform its target, a paid generation its connection and model). The most natural review behaviour —
clicking the object to look at it, scrubbing, an Undo — moves that binding, and the person's Approve used to answer
``STALE_REVISION``: the card vanished, the step read Failed and on Claude the request ended. Now the person's own
click on a card whose binding changed puts the same card back, presented from the binding as it is now (its paid
account and price too: ``present``), with an *Asked again* chip and a line naming what changed. Nothing runs until a
click on the card as it is now (doc/angelo/agent_execution.rst, "A review that went stale").

* **Only the person's own click** renews: ``chat_view.dispatch_action`` records it on the call (``mark_click``) when
  the press was made on the card generation that is current. Every other approval (Auto, standing consent, a worker's
  own controller, a lane shadow, headless runs, apps, eval harnesses) carries no mark and keeps today's refusal.
* **One click never answers a re-ask unseen**: each renewal raises ``review_generation``; a press made on an earlier
  generation approves nothing, and for ``approval_wait.SHIELD_SECONDS`` after the renewed card is drawn its Approve
  takes no click (``shield``), so a double-click's second press lands on nothing.
* No count: the call's deadline (unchanged) bounds the re-asks, and each needs a new click.
* Typed programs keep their own path: a changed declared input fails their binding before a fresh one exists, and a
  stale program is correctable on Claude already.

No ``bpy``: host-testable.
"""

import time

from .ui_text import iface_, n_

ASKED_AGAIN = n_("Asked again")
NOTE = n_("What this step uses changed while it waited ({parts}), so it is asking again. Check it, then approve.")
# The script binding's parts as a person reads them (stale_binding.SCRIPT_PARTS keys); any other binding is named
# by what it is to the step, without guessing its kind.
PARTS = {"scene": n_("the active scene"), "revision": n_("the scene: an edit, a selection or an evaluation"),
         "frame": n_("the frame"), "mode": n_("the mode"), "objects": n_("the objects")}
OTHER = n_("what it uses")
FACT = ("What this call was bound to changed while its card was shown; the person was asked again and approved it "   # i18n: model-facing
        "as it was then.")   # i18n: model-facing
_drawn = {}     # (session id, call id) -> the card generation last drawn with its Approve (shown_approvals).
_shields = {}   # call id -> None (renewed, not drawn yet) or the monotonic time its renewed Approve takes clicks again.


def generation(call):
    return vars(call).get("review_generation", 0)


def drew(controller, call_id):
    """A draw showed ``call_id``'s Approve (``chat_guard.shown_approvals``): record which generation it showed."""
    call = controller.pending.get(call_id)
    if call is not None:
        _drawn[(controller.state.get("id"), call_id)] = generation(call)


def drawn(controller, call_id):
    return _drawn.get((controller.state.get("id"), call_id))


def shielded(call_id, now=None):
    """Whether ``call_id``'s renewed Approve takes no click yet: from its renewal until ``SHIELD_SECONDS`` after its
    first draw (``shield_hits``)."""
    if call_id not in _shields:
        return False
    until = _shields[call_id]
    if until is None or (time.monotonic() if now is None else now) < until:
        return True
    _shields.pop(call_id, None)
    return False


WHOLE = (-1e6, -1e6, 2e6, 2e6)   # Every spot of the transcript: a renewed card's layout moved (its new note).


def shield_hits(approves, now=None):
    """An inert hit over the whole transcript while a renewed card drawn now (``approves``: ``(rect, call id)`` from
    ``chat_guard.shown_approvals``) is within ``SHIELD_SECONDS`` of its first draw, to go before every other hit: its
    note moved its buttons, so a double-click's second press could land on any of them (Always allow included)."""
    from .approval_wait import SHIELD_SECONDS
    from .canvas_controls import INERT
    now = time.monotonic() if now is None else now
    until = 0.0
    for _rect, call_id in approves:
        if call_id in _shields:
            if _shields[call_id] is None:
                _shields[call_id] = now + SHIELD_SECONDS
            until = max(until, _shields[call_id])
    return [(WHOLE, INERT, until)] if now < until else []


def mark_click(controller, call_id, pressed=None):
    """The person's Approve on ``call_id``'s card: True when it may go on. ``pressed`` is ``(call id, generation)`` the
    press was made on (None, or another card's: a keyboard activation of the focused, drawn card). A press on an
    earlier generation of a renewed card, or within its shield, approves nothing."""
    call = controller.pending.get(call_id)
    if call is None:
        return True   # The operator answers a gone card itself.
    shown = pressed[1] if isinstance(pressed, tuple) and pressed[0] == call_id else drawn(controller, call_id)
    if shown is not None and shown != generation(call) or shielded(call_id):
        return False
    call.person_click = generation(call) if shown is None else shown   # The generation this click answers.
    return True


def take_click(call):
    """Consume the person's click mark: the card generation it answers, or None (``_execute_tool`` reads it once,
    whatever happens next)."""
    return vars(call).pop("person_click", None)


def unanswered(call, clicked):
    """Whether ``call`` was asked again and this run carries no click on the card as it is now: an approval queued
    before the renewal (a double-click, a lane chat's queued apply) never runs the renewed card. It stays."""
    return bool(generation(call)) and not getattr(call, "automatic", False) and clicked != generation(call)


def label_usage(call, executor):
    """The paid account and price line a card shows, from its binding (``executor.usage_label``)."""
    from .flow_tool_spec import FLOW_USAGE
    from .studio_spec import STUDIO_USAGE
    if call.name in STUDIO_USAGE:
        call.usage_label = executor.usage_label(call.name, call.arguments)
    elif call.name in FLOW_USAGE:
        call.usage_label = executor.usage_label(call.name, call.arguments, call.precondition)


def show_card(controller, call, prepare_review, renewal=False):
    """Prepare the card's review from its binding and announce it (dispatch and renewal alike). A renewal clears what
    the row said before; a recovery review's card states the evidence it reviews either way."""
    call.precondition = prepare_review(call, call.precondition)   # The controller's (one per presentation).
    event = {"id": call.id, "status": "approval"}
    if call.recovery_review:
        event["result"] = {"recovery": controller.recovery_barrier.description()}
    elif renewal:
        event["result"] = {}
    controller.emit("activity", event)


def renew(controller, call, executor, current, change, clicked):
    """Put ``call``'s card back against ``current`` when the person's own click (``clicked``) found its binding changed,
    and True; else False (refused as before). Nothing runs here."""
    if clicked is None or clicked != generation(call) or current is None or call.automatic or call.executing \
            or call.mutation_started:
        return False
    from . import stale_binding
    parts = stale_binding.script_parts(call.precondition, current) if call.name == stale_binding.SCRIPT else None
    before, call.precondition = call.precondition, current
    if getattr(before, "digest", None) != getattr(current, "digest", None):
        vars(call).pop("flow_selection", None)   # A flow's choices belong to the plan they were made on.
    label_usage(call, executor)
    call.approval, call.approved, call.approval_started = True, False, time.monotonic()
    call.review_generation = generation(call) + 1
    call.renewed_parts = [key for key, _label in parts] if parts else ([change["kind"]] if change else [])
    vars(call).pop("approval_title", None)
    _shields[call.id] = None
    from .approval_review import prepare_review
    show_card(controller, call, prepare_review, renewal=True)
    controller.version += 1
    return True


def note(call):
    """The renewed card's line naming what changed, translated, or "" for a card never renewed."""
    if not generation(call):
        return ""
    keys = vars(call).get("renewed_parts") or []
    labels = [iface_(PARTS[key]) for key in keys if key in PARTS] or [iface_(OTHER)]
    return iface_(NOTE, parts=", ".join(labels))


def note_rows(call, wrap, width, size, font):
    text = note(call)
    return tuple(wrap(text, width, size, font)) if text else ()


def finished(call, result):
    """``result`` as delivered (``Controller.finish_tool``): a call that started after a renewal states it
    (model-facing, ``approval_renewed``). The call's draw records end with it."""
    forget(call.id)
    if not generation(call) or not getattr(call, "executing", False) or not isinstance(result, dict):
        return result
    return dict(result, approval_renewed={"binding_changed": list(vars(call).get("renewed_parts") or []),
                                          "note": FACT})


def forget(call_id):
    _shields.pop(call_id, None)
    for key in [key for key in _drawn if key[1] == call_id]:
        _drawn.pop(key, None)
