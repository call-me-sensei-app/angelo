# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An explicit new-send acknowledgment owned by the existing controller, never a transport.

Normal Enter has no observer. An exact retry uses one to keep pre-admission failures on
its notice rather than restoring somebody else's composer. Provider threads record facts;
only the main-thread pump settles them. A held message transfers to its existing inbox.
"""
import collections
import threading
import uuid


def fence(controller):
    return tuple(getattr(controller, name, None) for name in ("_epoch", "_request_generation", "_worker_request"))


class Submission:
    def __init__(self, controller, accepted, refused, unknown=None):
        self.id = uuid.uuid4().hex
        self.controller, self.accepted, self.refused = controller, accepted, refused
        self._fence, self._bound = fence(controller), False
        self._stops = getattr(controller, "stops", 0)
        self._lock, self._result, self._settled = threading.Lock(), None, False
        self._notified = False
        self.unknown = unknown
        self._unknown_shown = False
        self._worker_pair = None
        self._worker_facts = collections.OrderedDict()
        self.operation = None   # The mirror's exact local Request object, never only its wire id.

    def bind(self, controller):
        """The real send owner has begun this request (after its generation was allocated)."""
        if controller is not self.controller or self._bound or self._settled:
            raise ValueError("A submission belongs to its original chat")
        self._fence, self._bound = fence(controller), True
        controller._send_submission = self

    def before_start(self, controller):
        """A deferred preparation must not start after Stop or a replacement request."""
        if (controller is not self.controller or self._bound or self._settled or not self.current()
                or getattr(controller, "stops", 0) != self._stops):
            raise RuntimeError("The message was withdrawn before its request started")

    def owns_local(self):
        return (not getattr(self.controller, "_closing", False)
                and self._fence[:2] == fence(self.controller)[:2]
                and getattr(self.controller, "_send_submission", None) is self)

    def current(self):
        now = fence(self.controller)
        if not self._bound:
            # Connection preparation may replace its epoch; a replacement request may not.
            return not getattr(self.controller, "_closing", False) and self._fence[1:] == now[1:]
        if not self.owns_local():
            return False
        if self._worker_pair is not None:
            # An exact op response can arrive before its first frame. Never follow a different pair.
            return now[2] in (self._fence[2], self._worker_pair)
        return now == self._fence

    def pending(self):
        """A changed worker frame cannot unlock duplicate retry while its exact answer is still in flight."""
        return not self._settled and (self.owns_local() if self._bound else self.current())

    def acknowledge(self):
        """A provider/worker acknowledged the request. Recording alone never changes UI state."""
        with self._lock:
            if not self._settled and self._result is None and self.current():
                self._result = (True, None)

    def admit(self):
        """Main thread: the real send owner allocated/accepted this request, not a generic API return.

        Keep recovery ownership until provider delivery or a definite turn result: a worker can
        accept a Send and later report that its provider never got it.
        """
        with self._lock:
            if self._settled or self._notified or not self.current():
                return False
            self._notified = True
        self.accepted(None)
        return True

    def bind_worker(self, operation, pair):
        """Bind once, only from the actual Send answer correlated to this exact local Request."""
        if operation is not self.operation or not self.owns_local() or self._settled:
            return False
        if not (isinstance(pair, tuple) and len(pair) == 2 and all(type(n) is int for n in pair)):
            self.unsupported()
            return False
        if self._worker_pair is not None:
            return pair == self._worker_pair and self.current()
        self._worker_pair = pair
        return self.current()

    def unsupported(self):
        """Unknown is neither refusal nor delivery. Keep duplicate retry blocked and the composer untouched."""
        if not self._unknown_shown and not self._settled and self.owns_local():
            self._unknown_shown = True
            if self.unknown is not None:
                self.unknown()

    def observe_worker(self, pair, *, delivered=False, finished=False, not_sent=False, error=None):
        """Keep at most eight exact-pair facts while an answer is in flight; frames never bind a pair.

        True only means this local retry must not be restored into somebody else's composer.
        Before the correlated op answer, even a terminal frame is evidence waiting for an owner.
        """
        if (self.operation is None or self._settled or not self.owns_local()
                or not isinstance(pair, tuple) or len(pair) != 2 or not all(type(n) is int for n in pair)):
            return False
        facts = self._worker_facts.setdefault(pair, {})
        if delivered:
            facts['delivered'] = True
        if finished and 'not_sent' not in facts:
            facts['not_sent'] = not_sent
            facts['error'] = error
        while len(self._worker_facts) > 8:
            self._worker_facts.popitem(last=False)
        self.replay_worker()
        return True

    def replay_worker(self):
        if self._worker_pair is None or not self.current() or not self._notified or self._settled:
            return
        facts = self._worker_facts.get(self._worker_pair, {})
        if facts.get('delivered') or facts.get('not_sent') is False:
            self.acknowledge()
            self.settle()
        elif facts.get('not_sent') is True:
            self.reject(facts["error"])

    def reject(self, error):
        """A definite pre-admission refusal; never use a timeout as refusal when a worker may still take it."""
        with self._lock:
            if not self._settled and self._result is None and self.current():
                self._result = (False, error)
        self.settle()

    def queued(self, item):
        """Explicit admission by Inbox.hold: the existing bubble now owns retry, removal and reload."""
        with self._lock:
            if self._settled or not self.current():
                return False
            self._result = (True, item)
        return self.settle()

    def settle(self):
        """Main thread only: settle once and only while this invocation still owns its request."""
        with self._lock:
            if self._settled or self._result is None:
                return False
            self._settled = True
            result = self._result
            valid = self.current()
        if not valid:
            return False
        if getattr(self.controller, "_send_submission", None) is self:
            self.controller._send_submission = None
        if result[0]:
            if not self._notified:
                self._notified = True
                self.accepted(result[1])
        else:
            self.refused(result[1])
        return True


def settle(controller):
    submission = getattr(controller, "_send_submission", None)
    if submission is not None:
        return submission.settle()
    return False


def admit(controller, operation=None, pair=None):
    submission = getattr(controller, "_send_submission", None)
    if submission is not None:
        if operation is not None and not submission.bind_worker(operation, pair):
            return False
        admitted = submission.admit()
        if operation is not None:
            submission.replay_worker()
        return admitted
    return False


def worker_event(controller, pair, **facts):
    submission = getattr(controller, "_send_submission", None)
    return submission.observe_worker(pair, **facts) if submission is not None else False


def delivered(controller):
    """A provider acknowledgment, recorded on its worker; the UI pump runs the callback."""
    submission = getattr(controller, "_send_submission", None)
    if submission is not None:
        submission.acknowledge()


def finished(controller):
    """A definite non-not-sent result closes the current local admission lifetime."""
    delivered(controller)
    return settle(controller)


def not_sent(controller, error):
    """The real controller/worker's definite not-sent path: True means the notice owns recovery."""
    submission = getattr(controller, "_send_submission", None)
    if submission is None or not submission.current():
        return False
    submission.reject(error)
    return True
