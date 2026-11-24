# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reopened chat's offer to continue a request Angelo was running when it closed (``crash_resume``).

``offer`` runs once when a saved chat is loaded (``ui.get_controller``): when its newest request was cut off, one
person-only card says what the call journal shows (done, may have applied, didn't run) with **Continue**
(``chat_notices``). The card stands while that request is still the chat's newest and cut off, no turn runs, and the
chat works on its own document (not waiting for its file, not on another document). Continue (``continue_request``)
sends Angelo's own request: the journal's facts for the model, the scene observed now, and, when something may have
applied, the hold that makes the model read the recovered scene before any edit (``RecoveryBarrier.arm_continue``).
Nothing is replayed. A worker's chat is never offered (its worker's journal has its own recovery). Main thread.
"""

from . import crash_resume
from .ui_text import n_, notice

CARD = n_("Angelo closed while it was working on this request. Done: {done} · May have applied: {maybe} · "
          "Didn't run: {not_run}. Continue picks it up from the scene as it is now; Restore on your message brings "
          "back the scene from before it.")
ROW = n_("Continue the request Angelo was working on when it closed (done: {done}, may have applied: {maybe}, "
         "didn't run: {not_run}).")
FAILED = n_("Angelo could not continue this request: {error}")
ACTION = "notice_continue_request"
LIVE = ("approval", "queued", "running")   # A step's saved status while it waited, was queued or ran.


def _read_only(tool):
    """Whether ``tool`` only reads: from the frozen set taken as tools register (tool_specs.READ_ONLY_TOOLS), never a
    spec's annotations, which a dict method could still change (test_native_dialog_consent)."""
    from .tool_specs import READ_ONLY_TOOLS
    return tool in READ_ONLY_TOOLS


def _eligible(controller):
    return (getattr(controller, "remote_executor", None) is None and not getattr(controller, "document_invalid", False)
            and getattr(controller, "awaited_file", None) is None)


def offer(controller):
    """Post the card once for this load when the chat's newest request was cut off."""
    rows = controller.state["messages"]
    index = crash_resume.cut_off(rows)
    if index is None or getattr(controller, "remote_executor", None) is not None:
        return None
    _close_live_steps(controller, rows, index)
    summary = crash_resume.summarize(controller.state, index, _read_only)
    done, maybe, not_run = crash_resume.parts(summary)
    controller.continue_offer = index
    controller.emit("message", {"role": "system", "notice": True, "continue_offer": index,
                                **notice(CARD, done=done, maybe=maybe, not_run=not_run)})
    return summary


def _close_live_steps(controller, rows, index):
    """The cut-off request's steps saved as waiting, queued or running wait for nothing now: each reads Stopped
    (``call_ended.CLOSED``, the host's own end, never in a result the model reads), saved once."""
    from . import call_ended
    for at in range(index + 1, len(rows)):
        row = rows[at]
        if (isinstance(row, dict) and row.get("role") == "activity" and row.get("status") in LIVE
                and not call_ended.ended(row)):
            row["ended"] = call_ended.CLOSED
            controller.store.touch(at)


def stands(controller):
    """Whether Continue is drawn: the offered request is still the newest, cut off, and nothing runs."""
    index = getattr(controller, "continue_offer", None)
    return (index is not None and not getattr(controller, "busy", False) and _eligible(controller)
            and crash_resume.cut_off(controller.state["messages"]) == index)


def continue_request(controller, ui):
    """The person's Continue. Returns True when the request was sent."""
    if not stands(controller):
        return False
    index = controller.continue_offer
    summary = crash_resume.summarize(controller.state, index, _read_only)
    message, parts = crash_resume.model_text(summary), crash_resume.parts(summary)
    failures = []
    if summary["maybe"] or summary["done"]:   # A done step may be missing from the reopened file (C-2).
        controller.recovery_barrier.arm_continue(crash_resume._listed(summary["maybe"] + summary["done"]))
    try:
        from . import worker_routing
        from .request_rows import CONTINUE
        ui.prepare_controller(controller)
        worker_routing.deliver(controller, ui.main_window(), message, lambda turn: controller.send(
            message, observation=turn.observation, recovery_evidence=turn.recovery_evidence, host_request=parts,
            host_kind=CONTINUE), failures.append)
    except Exception as error:   # noqa: BLE001 - said in the chat; nothing was sent.
        failures.append(error)
    if failures:
        controller.recovery_barrier.disarm()   # The hold belongs to the continuation only, never a later request.
        controller.emit("message", {"role": "system", **notice(FAILED, error=str(failures[0])[:300])})
        return False
    controller.continue_offer = None
    return True
