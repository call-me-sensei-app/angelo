# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's Stop and consent take effect when their frame arrives (design A′, ``worker_chat_host``).

The worker's main loop answers requests one at a time between pumps, so a request waits while an edit, a script or a
save runs. Stop and a consent change must not (break-it S2, U8): the person's Stop or Auto→Ask holds from the moment
it reaches the worker, as it does in a lead chat, where the toggle runs on the main thread between calls. So the
worker's reader thread (``WorkerHost._read``) hands each request frame to ``arrived`` before queuing it:

* ``chat.consent`` of a chat hosted here is marked at once (``worker_consent_marks``): from then on no call starts
  without the person unless every consent that arrived still grants it, so a narrowing holds from its arrival and a
  widening only once applied. The lease itself (its grants, the revocation of the autonomous calls it no longer
  covers that have not started) is applied under the chat's lock on an applier thread of that chat's, in arrival
  order; its mark then ends. A narrowing is acknowledged as it is marked, anything else once applied. A consent for a chat not hosted yet (its
  ``chat.open`` still queued) is queued in order, and until the loop has handled it no call of that chat starts
  without a person (``consent_pending``).
* ``chat.stop`` marks the chat stopping (``stop_pending``, and ``cancelled``: its turn's events stop) and interrupts
  its provider at once, on a thread of its own, through the relay (the provider's ``stop_request`` with the fence of
  the turn running now, exactly as ``Controller.stop`` does it). The request is still queued: the loop's
  ``Controller.stop`` then cancels the turn's calls with their results. Until it has, no call of that chat starts,
  whatever a message queued ahead of the Stop began (``stop_pending``).

**The person's order.** Each op from the lead carries ``lead_seq`` (``worker_mirror_ops.order``), the order in which
the person acted, and a Stop applies to what they did before it (``covers``), whichever frame came first: the lead
sends a Stop on a thread of its own, so it can overtake the Send it stops. A Send ordered before a Stop already
received is refused, never run (``send``); a Stop ordered before the Send of the turn running now stops nothing
of it (``stops``). An op or a Stop with no order is covered (fail closed), and a Stop with no order is recorded
just after the newest order that reached the reader for its chat, so a Send that arrived before it, ordered or
not, is refused too (the lead stamps every Stop, ``WorkerProcess.request``; this holds whatever sent it).

The loop calls ``handled`` after each request; that ends its barrier. Every start check reads the marks in the
critical section it already has, under the chat's lock: ``_tool_available``, ``tool_deadlines.started`` (through
the chat's ``start_refusal``), ``worker_consent.admit_autonomous`` and Approve. So a call never starts once the frame
has arrived; one that passed its last check before had started, and its result is reported as it ends, as a Stop
leaves an edit already running.

**The reader never waits** on anything another thread holds (break-it R5d): it takes only this module's lock, which
no thread holds across I/O or another lock, and never the chat's lock, which a provider thread holds while it saves
the chat's journal. A Stop's ``cancelled`` is one store, its fence and provider are read without the lock (a fence a
Send changed meanwhile is stale, so the provider ignores it and the loop's Stop follows), and the provider is
interrupted on a new thread holding no lock of ours, so its own locks (``_lifecycle_lock``, the transport's) are
never taken under the chat's, and a wedged runtime never blocks the reader. Reader thread; no bpy.
"""

import threading
import time

from . import part_leases, worker_consent_marks as marks, worker_values as values

CONSENT, STOP = "chat.consent", "chat.stop"
STOPPED_FIRST = "A Stop you sent after this message reached the worker first, so it never ran"


def order_of(request):
    seq = request.get("lead_seq")
    return seq if type(seq) is int else None


def covers(stop, seq):
    """Whether a Stop ordered ``stop`` applies to an op (or the turn of a Send) ordered ``seq`` (module docstring)."""
    return stop is None or seq is None or seq < stop


class Arrivals:
    def __init__(self, chats):
        self.chats = chats
        self._lock = threading.Lock()
        self._barriers = {}   # seq -> (session, op, lead_seq): a Stop or a queued consent the loop has not handled yet
        self._stopped = {}    # session -> the latest order of a Stop received for it
        self._newest = {}     # session -> the newest order of any request that reached the reader for it
        self._consents = {}   # session -> {seq: (epoch, grants)}: hosted consents that arrived, not applied yet
        self._early = {}      # seq -> perf_counter at arrival: consents acknowledged as they were marked
        self._applier = marks.Applier(self._apply)

    def arrived(self, seq, request):
        """Reader thread: True when ``request`` was answered here (the loop never sees it)."""
        op, session, order = request.get("op"), request.get("session"), order_of(request)
        if not isinstance(session, str):
            return False
        with self._lock:
            if order is not None:
                self._newest[session] = max(order, self._newest.get(session, order))
        if op not in (CONSENT, STOP):
            return False
        chat = self.chats.hosted(session)
        if op == CONSENT and chat is not None:
            started, mark, early = time.perf_counter(), marks.mark_of(request), False
            if mark is not None:
                with self._lock:
                    arrived = self._consents.setdefault(session, {})
                    queued = any(entry[:2] == (session, CONSENT) for entry in self._barriers.values())
                    early = not queued and marks.acknowledged_on_arrival(chat.consent_lease, list(arrived.values()),
                                                                         mark)
                    arrived[seq] = mark
                    if early:
                        self._early[seq] = started
            if early:   # It holds from now (worker_consent_marks): the lead's ack wait ends while a save runs.
                part_leases.replies_of(self.chats.host).put({"seq": seq, "response": {
                    "epoch": mark[0], "ok": True, "worker_ms": round((time.perf_counter() - started) * 1000, 3)}})
            self._applier.put(session, (seq, chat, request))
            return True
        with self._lock:
            self._barriers[seq] = (session, op, order)
            if op == STOP:   # Unordered: after everything that arrived before it (module docstring).
                stopped = order if order is not None else self._newest.get(session, -1) + 1
                self._stopped[session] = max(stopped, self._stopped.get(session, stopped))
        if op == STOP and chat is not None and covers(order, getattr(chat, "lead_seq", None)):
            stop_now(chat)
        return False

    def handled(self, seq):
        """Main thread, after the loop answered ``seq``: its barrier, if any, ends."""
        with self._lock:
            self._barriers.pop(seq, None)

    def stop_pending(self, session, seq=None):
        """A Stop not handled yet that covers what is ordered ``seq`` (the turn running now, by default any)."""
        with self._lock:
            return any(entry[:2] == (session, STOP) and covers(entry[2], seq) for entry in self._barriers.values())

    def consent_pending(self, session):
        with self._lock:
            return any(entry[:2] == (session, CONSENT) for entry in self._barriers.values())

    def consent_covers(self, session, lease, grant):
        """Whether the consents of ``session`` that arrived and are not applied yet all still grant ``grant`` as
        ``lease`` holds it (``worker_consent_marks.covers``); the marks are read first."""
        with self._lock:
            arrived = list(self._consents.get(session, {}).values())
        return marks.covers(lease, arrived, grant)

    def send(self, chat, request, start):
        """Loop, a Send: refused when a Stop ordered after it was already received; else ``start`` runs its turn,
        ordered as this Send (a refused start leaves the turn running now as it was), and a Stop that arrived
        meanwhile and covers it stops it."""
        order, session = order_of(request), chat.state["id"]
        with self._lock:
            stopped = self._stopped.get(session)
        if stopped is not None and covers(stopped, order):
            from .worker_chat_holds import refused
            raise refused(STOPPED_FIRST, code="CANCELLED")
        previous, chat.lead_seq = getattr(chat, "lead_seq", None), order
        try:
            start()
        except BaseException:
            chat.lead_seq = previous
            raise
        if self.stop_pending(session, order):
            stop_now(chat)

    def stops(self, chat, request):
        """Loop, a ``chat.stop``: whether it applies to the chat's turn now (not one of a later Send)."""
        return covers(order_of(request), getattr(chat, "lead_seq", None))

    def _apply(self, seq, chat, request):
        """The chat's applier thread: the lease applied under the chat's lock, its mark ended, then its answer unless
        it was acknowledged as it was marked. A failed application keeps its mark (fail closed)."""
        session, started = chat.state["id"], time.perf_counter()
        with self._lock:
            early = self._early.pop(seq, None) is not None
        try:
            answer = self.chats.consent_answer(chat, request)
            applied = answer.get("ok") is True or not early
        except Exception as error:  # noqa: BLE001 - every request is answered; the error is the answer.
            answer, applied = {"error": values.encode_error(error)}, False
        if not applied:
            print("Angelo worker: a consent lease was not applied; its chat refuses what it withdrew until a newer "
                  "lease is:", answer.get("error_code"), answer.get("error"))
        else:
            with self._lock:
                arrived, held = self._consents.get(session, {}), chat.consent_lease.epoch
                for done in [key for key, (epoch, _grants) in arrived.items()
                             if key == seq or (held is not None and epoch < held)]:
                    del arrived[done]
                if not arrived:
                    self._consents.pop(session, None)
        if early:
            return   # Answered as it was marked; a request is answered once.
        answer["worker_ms"] = round((time.perf_counter() - started) * 1000, 3)
        try:
            self.chats.host.endpoint.send({"seq": seq, "response": answer})
        except (OSError, ValueError):
            pass   # The lead is gone; the reader ends the loop.


def stop_now(chat):
    """``chat`` stops now: marked without its lock, its provider interrupted on a thread of its own (module
    docstring). Returns that thread."""
    from .request_fences import RequestFence
    chat.cancelled = True   # One store: a provider thread's save may hold the chat's lock (module docstring).
    fence, provider = RequestFence.capture(chat), chat.provider
    stop_request = getattr(provider, "stop_request", None) if provider is not None else None
    target = (lambda: stop_request(fence)) if callable(stop_request) else getattr(provider, "stop", None)

    def interrupt():
        try:
            if target is not None:
                target()
        except Exception as error:  # noqa: BLE001 - the lead's kill is the backstop; the loop's Stop follows.
            print("Angelo worker: a chat's provider was not interrupted:", type(error).__name__, error)
        with chat.lock:   # Its redraw count; this thread may wait for a save, the reader never does.
            chat.version += 1
    thread = threading.Thread(target=interrupt, daemon=True, name="AngeloWorkerStop")
    thread.start()
    return thread
