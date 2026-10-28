# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's merge hold (design A′, ``worker_chat_host``): ``chat.hold`` and ``chat.release``.

``chat.hold`` sets ``holding`` at once (from then on nothing starts without a person and no approval is taken) and
is answered once no call of the chat is under way, at most ``HOLD_SECONDS`` later (``WorkerHost.run`` sends nothing
meanwhile: the answer is a ``Deferred``). Two rules keep a hold from outliving the merge that asked for it:

* a hold is granted only while it is still wanted: ``chat.release`` (the lead's merge ended, or gave up waiting)
  refuses that chat's deferred holds at once, and a grant checks ``holding`` again (break-it F1: a release handled
  before the late grant left the chat held for good, running nothing and taking no approval);
* the part is saved first: the merge reads the part file, so a hold whose save fails is refused, never granted with
  the last edit missing from the file (break-it F8).

Main thread (the worker's loop); no bpy beyond the host's own save.
"""

import time

from . import worker_values as values

HOLD_SECONDS = 60.0    # A merge hold waits this long for a call under way, then is refused (the hold is not kept).


class Deferred:
    """``chat.hold``'s answer before it holds: ``WorkerHost.run`` leaves it to ``WorkerChatHost`` to send."""

    def __init__(self, controller, started):
        self.controller, self.started = controller, started
        self.deadline = time.monotonic() + HOLD_SECONDS


def hold(chat):
    chat.holding = True   # From now on nothing starts without a person, and no approval is taken.
    return Deferred(chat, time.perf_counter())


def release(chats, chat):
    """``chat.release``: the hold ends, and a deferred one of this chat is refused now (never granted later)."""
    chat.holding = chat.held = False
    for seq, waiting in list(chats._waiting.items()):
        if waiting.controller is chat:
            del chats._waiting[seq]
            _send(chats, seq, waiting, {"error": values.encode_error(refused(
                "The merge hold was released before it held", code="CANCELLED"))})
    return {"ok": True}


def answer_holds(chats):
    """Each deferred hold that can be answered now: granted (the part saved), or refused (its chat closed, its
    hold released, its save failed, or a call still under way at its deadline)."""
    for seq, waiting in list(chats._waiting.items()):
        chat = waiting.controller
        if chats.chats.get(chat.state["id"]) is not chat:
            answer = {"error": values.encode_error(refused("The chat closed before the merge hold",
                                                            details=NOT_HOSTED))}
        elif not chat.holding:
            answer = {"error": values.encode_error(refused("The merge hold was released before it held",
                                                            code="CANCELLED"))}
        elif not chat.busy_with_calls():
            failure = chats.save_part_now()
            if failure is None:
                chat.held = True
                answer = {"ok": True, "held": True}
            else:
                chat.holding = False
                answer = {"error": values.encode_error(refused(
                    "The worker could not save its part before the merge (%s), so the merge hold was not taken"
                    % failure, code="WRITER_BUSY"))}
        elif time.monotonic() >= waiting.deadline:
            chat.holding = False
            answer = {"error": values.encode_error(refused(
                "A call of this chat was still under way after %.0f s, so the merge hold was not taken" % HOLD_SECONDS,
                code="WRITER_BUSY"))}
        else:
            continue
        del chats._waiting[seq]
        _send(chats, seq, waiting, answer)


def _send(chats, seq, waiting, answer):
    answer["document"] = list(chats.ui.document_key())
    answer["worker_ms"] = round((time.perf_counter() - waiting.started) * 1000, 3)
    try:
        chats.host.endpoint.send({"seq": seq, "response": answer})
    except (OSError, ValueError):
        pass   # The lead is gone; its reader ends the loop.


NOT_HOSTED = {"not_hosted": True}   # A refusal's details: this worker hosts no such chat (it closed here, or never
#                                     opened); the lead reads these, never the words (``worker_chat_closing``).


def refused(message, code="UNSUPPORTED", details=None):
    """A refusal that changed nothing (the worker host's own, shared)."""
    from .refusal_codes import RefusalError
    error = RefusalError(message, code=code, details=details)
    error.partial_changes_possible = False
    return error
