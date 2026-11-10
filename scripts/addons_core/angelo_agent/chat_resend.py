# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-click Resend on a failed turn's latest card (``chat_notices``). Pure Python.

API:
    RESEND: {notice kind: bool} for every ``notice_kinds.KINDS``
    offered(controller, message, kind) -> bool    whether the card for ``message`` has Resend
    resend(controller, row) -> bool               the person's click (``chat_notices.dispatch`` only)
    refusal(controller, message) -> dict | None   why the last Resend of ``message`` was refused

A request is paid, so it is sent again only on the person's click, once: it sends exactly the stored request
(``drafts.failed_request``: its words and attachments) through ``console.send_message``, the entry point Enter uses
(held as a pending message while a turn runs, otherwise a new request with its price and approval as usual). The
message box is left alone: neither its draft, its attachments nor the tools chosen for the next message go with it.
A refusal (a ``Text`` sentence or any other error) becomes the card's reason and nothing is lost: "Put message back"
stays. Attachments no longer on this computer are refused before anything is sent. A Resend that went through
takes both pills away: the request is on its way again.
"""

import json

from . import drafts, notice_kinds, request_submissions
from .ui_text import n_

LABEL = n_("Resend")   # The card's pill; its tooltip (``canvas_controls.label``) says what it does and costs.
TIP = n_("Send the same message again, with its attachments. It may cost what it did before.")

# Whether a failed turn's card offers Resend, for every notice kind. Only where the card invites trying again and
# the same request, sent again unchanged, can go through; the others have their own way out on the card.
RESEND = {
    "rate": True,          # "Wait a moment, then try again."
    "overloaded": True,    # "Try again in a moment."
    "offline": True,       # "Check your internet connection, then try again."
    "unknown": True,       # "This turn stopped before it finished. Try again."
    "signin": False,       # Sign in again first (its sign-in pill).
    "credits": False,      # Add credits or switch connection (its pills).
    "usage": False,        # The limit holds until it resets (Switch connection).
    "model": False,        # This connection can't use the model: choose another first.
    "context": False,      # The same request is as long again: a new chat, or fewer attachments.
}


def _mine(controller):
    return vars(controller) if hasattr(controller, "__dict__") else {}


def offered(controller, message, kind):
    """The latest failed turn's card offers Resend: its kind invites trying again, its request is the person's,
    no Resend of it is under way, and it was not refused for attachments that are gone."""
    pending = _mine(controller).get("_resending")
    if isinstance(pending, request_submissions.Submission) and not pending.pending():
        _mine(controller).pop("_resending", None)   # A replacement request cannot inherit an old click.
    recovery = _mine(controller).get("_resend_recovery")
    if recovery is not None and recovery.request(controller, message) is not None:
        kind = recovery.kind
    refused = refusal(controller, message)
    return (RESEND.get(kind, False) and _mine(controller).get("_resending") is None
            and not (refused and refused.get("missing"))
            and drafts.failed_request(controller, message) is not None)


def refusal(controller, message):
    """``{"missing": count}`` or ``{"notice": error row body}`` for the last refused Resend of ``message``."""
    found = _mine(controller).get("_resend_refused")
    return found[1] if found and found[0] is message else None


def _snapshot(found):
    """Private immutable words/reference metadata; every consumer receives a fresh decoded copy."""
    text, references = found
    return json.dumps([text, references], sort_keys=True, ensure_ascii=False, allow_nan=False)


class _Recovery:
    """A definite refused retry's own error and original person payload, never permission or request kind."""
    def __init__(self, controller, row, payload, kind):
        self.row, self.payload, self.kind = row, payload, kind
        self.fence, self.controller = request_submissions.fence(controller), controller
        self.error = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)

    def request(self, controller, message=None):
        rows = controller.state.get("messages") or []
        if (controller is not self.controller or self.fence != request_submissions.fence(controller)
                or not rows or rows[-1].get("role") != "error"
                or (message is not None and message is not rows[-1])
                or any(rows[-1] is _mine(controller).get(name) for name in ("_put_back_row", "_resent_row"))):
            return None
        row = rows[-1]
        # A mirror may rebuild the same history from its journal. Rebind only the exact error,
        # same request fence and same strict person request before it; equal text alone is no owner.
        if row is not self.row and json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) != self.error:
            return None
        found = drafts.request_before_error(rows, len(rows) - 1)
        if found is None or _snapshot(found) != self.payload:
            return None
        self.row = row
        text, references = json.loads(self.payload)
        return text, references


def _missing(controller, references):
    """How many of ``references`` can no longer be sent (``ReferenceStore.checked_path``: gone, replaced, changed)."""
    store = getattr(controller, "references", None)
    count = 0
    for reference in references:
        try:
            store.checked_path(reference)
        except Exception:   # Any reason it can't be read now: the message can't go as it was.
            count += 1
    return count


def resend(controller, row):
    """The person's click on Resend for ``row`` (the chat's latest error row); True when this click began preparing or explicitly queued the retry."""
    from . import console
    from .display_text import Text, error_notice
    mine = _mine(controller)
    kind = notice_kinds.kind(row)
    recovery = mine.get("_resend_recovery")
    if recovery is not None and recovery.request(controller, row) is not None:
        kind = recovery.kind
    if not offered(controller, row, kind):   # In flight, sent already, refused for good, or no longer the latest.
        return False
    payload = _snapshot(drafts.failed_request(controller, row))
    text, references = json.loads(payload)
    submission = None

    def accepted(item):
        if mine.get("_resending") is not submission:
            return
        mine.pop("_resending", None)
        if item is None:
            controller._resend_owner = submission
        else:
            mine.pop("_resend_owner", None)   # Explicitly transferred to the whole Inbox item.
        controller._resend_refused = None
        controller._resend_recovery = None
        controller._resent_row = row   # An acknowledged request, or an explicitly admitted inbox item.
        controller.version += 1

    def refused(error):
        if (mine.get("_resending") is not submission
                and mine.get("_resend_owner") is not submission):
            return
        mine.pop("_resending", None)
        mine.pop("_resend_owner", None)
        if mine.get("_resent_row") is row:
            mine.pop("_resent_row", None)
        rows = controller.state.get("messages") or []
        target = rows[-1] if rows and rows[-1].get("role") == "error" else None
        found = drafts.request_before_error(rows, len(rows) - 1) if target is not None else None
        if found is None or _snapshot(found) != payload:
            # A turn was allocated but definitely never sent. Keep its refusal in the transcript,
            # not in the unrelated composer. Mirrors keep lead-owned rows across journal reloads.
            body = error_notice(error)
            if getattr(controller, "hosted_in_worker", False):
                from .worker_mirror_ops import add_row
                add_row(controller, "error", body)
            else:
                controller._append_row(dict(body, role="error"))
            target = controller.state["messages"][-1]
        controller._resend_refused = (target, {"notice": error_notice(error)})
        controller._resend_recovery = _Recovery(controller, target, payload, kind)
        controller.version += 1

    def unknown():
        if mine.get("_resending") is submission or mine.get("_resend_owner") is submission:
            controller._resend_refused = (row, {"notice": error_notice(RuntimeError(Text(n_(
                "This request's delivery is unknown. Wait for its result before trying again."))))})
            controller.version += 1

    submission = request_submissions.Submission(controller, accepted, refused, unknown)
    controller._resending = submission
    try:
        missing = _missing(controller, references)
        if missing:
            mine.pop("_resending", None)
            controller._resend_refused = (row, {"missing": missing})
            return False
        controller.require_model_chat()
        if controller.voice is not None:
            raise RuntimeError(Text(n_("Stop voice to send this message again.")))
        console.send_message(controller, text, references, run_chosen_tools=False, submission=submission)
    except Exception as error:
        submission.reject(error)
        return False
    finally:
        if hasattr(controller, "version"):
            controller.version += 1
    # A normal API return may mean parked/deferred preparation. Only its owner settles submission.
    return True
