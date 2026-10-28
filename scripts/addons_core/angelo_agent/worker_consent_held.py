# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a hosted chat's worker may hold of its consent lease (design A′, contract C6), for ``worker_consent_lead``.

Each lease the lead sends is, for that chat, in one of three states until a newer acknowledged lease supersedes it:

* **acknowledged**: the worker answered it in time (the lead's ``_acked``, the newest one only);
* **in flight**: sent, not answered yet, within ``ACK_SECONDS`` (``Send`` records);
* **uncertain**: not acknowledged in time, yet it may have reached the worker and been applied (its answer was
  lost, late or timed out). It stays accounted for until an acknowledged lease with a higher epoch exists, because
  the worker takes only higher epochs: a widening whose ack failed is still "maybe held" (audit2 #1).

A send is a **revocation** when it withdraws anything any of these may hold (``possible``); a revocation is then
acknowledged in time or its chat is stopped. Standard library only and no bpy.
"""

import threading

ACK_SECONDS = 2.0   # Contract C6: a revocation the worker has not acknowledged by then stops the chat's turn.


def maybe_taken(error):
    """Whether a request whose send raised ``error`` may have reached the worker: all but one never sent (refused
    before any byte, its chat not open in a worker, its worker already ended). Unknown is never "nothing changed"."""
    from .worker_chat_lifecycle import ChatOpFailed
    from .worker_process import RequestRefused
    return not (isinstance(error, (RequestRefused, ChatOpFailed)) or getattr(error, "sent", True) is False)


def narrows(before, after):
    """Whether ``after`` withdraws anything ``before`` granted (a replaced script nonce withdraws the old token)."""
    return (before.get("auto") is True and after.get("auto") is not True) or any(
        before.get(kind) and after.get(kind) != before[kind] for kind in ("scripts", "always_allow"))


class Send:
    def __init__(self, session, epoch, grants_now, revoking, started):
        self.session, self.epoch, self.grants = session, epoch, grants_now
        self.revoking, self.started = revoking, started
        self.done, self.answer, self.answered, self.expired = threading.Event(), None, None, False

    def in_time(self):
        """Answered, and within ``ACK_SECONDS`` of being sent (a later answer counts as lost: its chat was stopped)."""
        return (self.done.is_set() and not self.expired and self.answered is not None
                and self.answered - self.started < ACK_SECONDS)

    def maybe_held(self):
        """Not acknowledged in time, yet possibly applied: True unless its answer, in time, says it never was (never
        sent, or the worker's own refusal). A lost, late or timed-out answer is never "nothing changed"."""
        answer = self.answer if isinstance(self.answer, dict) else None
        if answer is None or not self.in_time():
            return True
        if "unknown" in answer:   # The lead's own transport answer (``worker_consent_lead._call``).
            return answer["unknown"] is not False
        return not (answer.get("error") or answer.get("error_code"))


def possible(acked, inflight, uncertain):
    """Every lease the worker may hold now: the acknowledged one, and each sent after it that is still in flight or
    uncertain (``acked``: ``(epoch, grants)``; ``uncertain``: ``[(epoch, grants)]``)."""
    held, granted = acked

    def later(epoch):
        return held is None or epoch > held
    return ([granted] + [sent.grants for sent in inflight if later(sent.epoch)]
            + [grants_now for epoch, grants_now in uncertain if later(epoch)])


def superseded(uncertain, held):
    """``uncertain`` without the leases an acknowledged lease of epoch ``held`` supersedes."""
    return [(epoch, grants_now) for epoch, grants_now in uncertain if held is None or epoch > held]
