# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merge's holds and leases, asked without waiting: no frame of the lead's window waits on a worker (A', C10).

A merge reads its parts' files on Blender's main thread, inside the approved-change Undo operator, while each part's
hosted chat is held (``chat.hold``: no admission, answered once no call executes) and each live part file is leased
(``part.lease``: the worker's save waits). Asking for those takes two phases, so a wedged worker never stops a frame:

1. **Ask** (``waits``, from ``merge_hold.waits`` at the merge's execution, before anything of it runs): its tiles say
   Merging, every hold goes out at once, each on a thread of its own, and once all are answered every lease does; the
   merge stops there as a waiting merge does (nothing ran, no Undo step), so the operator returns at once.
2. **Merge** (``guard``, in ``worker_sessions._merge``): once every answer is in (``asking`` is False) the pump applies
   the merge again through its own approval route (``merge_hold.resume``); its reads and the scene merge run as they
   always did, then every hold and lease is released without waiting.

A refusal, a worker that ended mid-hold, or no answer in time (``HOLD_SECONDS`` for the holds, then ``LEASE_WAIT`` for
the leases, each shared by all) fails the gate and releases it at once; the merge then runs only to its refusal
(WRITER_BUSY, nothing merged) and its chat says why, with a support code (``FAILED``). A worker whose process ended
before its lease leaves its file to read: nothing writes it any more. A lease is read under only while at least
``FRESH`` of its granted time is left: an older one is released and asked again before the merge runs, or refuses the
merge at its run. A release reaches each worker only after its own hold or lease was answered or given up on, so it
always follows the request it ends. One gate per merge call: the same call applied again joins it; another merge
whose parts overlap a gate under way is refused. A part whose chat is closing (a park, ``worker_chat_closing``) is
waited for first, bounded by the close's own ``CLOSE_SECONDS`` and off the main thread; once it was handed back there is
nothing to hold (only its part file is leased), and a hold or release its worker answers "hosts no such chat" for a
chat that closed meanwhile holds nothing and refuses nothing. The pump's ``sweep`` releases a gate whose merge left its
chat without reading it (expired, stopped, declined, refused) and puts its tiles back. A route that reaches the merge
without asking first asks and waits there, bounded (``waited``, ``worker_chat_lifecycle.merge_guard``). Off
(``ANGELO_WORKER_CHAT_HOST=0``), or with nothing to hold or lease, nothing is asked and the merge runs at once, as at
the base. Standard library only, no bpy; answers land on their own threads, everything else runs on the main thread.
"""

import threading
import time
from contextlib import contextmanager

from . import worker_chat_closing as closing, worker_chat_lifecycle as lifecycle
from .ui_text import n_

MERGE = "angelo_workers_merge"
FRESH = 0.5   # The share of a lease's granted time still left when the merge's reads begin.
# Shown in the lead's chat when a merge ran to its refusal because a worker did not let its part be read.
FAILED = n_("Nothing was merged: a worker's part was not ready to merge ({why}; support code {code}).")

_gates = {}   # (lead chat id, merge call id) -> Gate under way; a gate of no call (``waited``) under its own key
_releasing = {}   # chat session id -> Event, set once its newest merge release was sent (or given up on)
NOTHING = object()   # A hold's answer for a chat that closed first (``worker_chat_closing``): nothing to release.


class Gate:
    """One merge's holds and leases: asked on threads, ended (ready or failed) by their answers, released once."""

    def __init__(self, held, paths, lead=None, call=None, sessions=()):
        self.held, self.paths = list(held), [str(path) for path in paths]   # held: (session, worker) to hold
        self.lead, self.call, self.sessions = lead, call, list(sessions)     # sessions: every tile it marks
        self.leases, self.error, self.cause = [], None, None   # leases: (worker, token, granted at, seconds, path)
        self.done, self.released = threading.Event(), False
        self._lock, self._waiting, self._asked, self._answered = threading.Lock(), len(self.held), set(), set()
        self.deadline = time.monotonic() + lifecycle.HOLD_SECONDS   # The holds', then the leases' (``_lease_all``)

    def start(self):
        if not self.held:
            self._lease_all()
        for session, worker in self.held:
            with self._lock:
                if self.done.is_set():
                    break   # Failed already: a hold never asked needs no release.
                self._asked.add(session)
            _ask(session, "chat.hold", worker, lifecycle.HOLD_SECONDS, {},
                 lambda answer, s=session, w=worker: self._held(s, w, answer), after=_releasing.get(session))
        return self

    def _held(self, session, worker, answer):
        nothing = answer is NOTHING
        with self._lock:
            if not nothing:
                self._answered.add(session)
            late, failed = self.released, isinstance(answer, lifecycle.ChatOpFailed)
            if not late and not failed:
                self._waiting -= 1
            leases = not late and not failed and self._waiting == 0
        if late and not nothing:
            _release([(session, worker)], [])   # Its own release, now that its hold was answered or given up on.
        elif failed:
            self.fail(lifecycle._refusal("A worker's chat could not be held for the merge (%s)" % answer), answer)
        elif leases:
            self._lease_all()

    def _lease_all(self):
        serving = [(path, lifecycle._serving(path)) for path in self.paths]
        serving = [(path, worker) for path, worker in serving if worker is not None]   # No live worker: nothing saves.
        with self._lock:
            self._waiting, self.deadline = len(serving), time.monotonic() + lifecycle.LEASE_WAIT
        if not serving:
            self._ready()
        for path, worker in serving:
            if self.done.is_set():
                break
            _ask(None, "part.lease", worker, lifecycle.LEASE_WAIT, {"part": path, "seconds": lifecycle.LEASE_SECONDS},
                 lambda answer, w=worker, p=path: self._leased(w, p, answer))

    def _leased(self, worker, path, answer):
        failed = isinstance(answer, lifecycle.ChatOpFailed)
        lease = None if failed else (worker, answer.get("lease"), time.monotonic(),
                                     _seconds(answer.get("seconds")), path)
        with self._lock:
            late = self.released
            if not late and lease is not None:
                self.leases.append(lease)
            gone = failed and lifecycle._process_ended(worker)   # Ended meanwhile: nothing writes the file any more.
            if not late and (not failed or gone):
                self._waiting -= 1
            ready = not late and (not failed or gone) and self._waiting == 0
        if late and lease is not None:
            _release([], [lease])
        elif failed and not gone and not late:
            why = "A worker's part file could not be leased for the merge (%s)" % answer
            self.fail(lifecycle._refusal(why), answer)
        elif ready:
            self._ready()

    def _ready(self):
        self.done.set()

    def fail(self, refusal, cause=None):
        """End the gate failed with ``refusal`` (a RefusalError) and release it at once."""
        with self._lock:
            if self.error is None:
                self.error, self.cause = refusal, cause
        self.done.set()
        self.release()

    def renew(self):
        """Release this gate's leases and ask for them again; its holds stay."""
        with self._lock:
            old, self.leases = self.leases, []
            self.done.clear()
        _release([], old)
        self._lease_all()

    def fresh(self, now=None):
        now = time.monotonic() if now is None else now
        return all(now - granted <= seconds * (1 - FRESH) for _worker, _token, granted, seconds, _path in self.leases)

    def release(self):
        """Every hold and lease answered so far, released without waiting (later ones release themselves)."""
        with self._lock:
            if self.released:
                return
            self.released = True
            held = [(s, w) for s, w in self.held if s in self._answered]
            leases, self.leases = list(self.leases), []
        self.done.set()
        _release(held, leases)


def _seconds(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return lifecycle.LEASE_SECONDS
    return value if 0 < value <= lifecycle.LEASE_SECONDS else lifecycle.LEASE_SECONDS


def _ask(session, op, worker, timeout, fields, then, after=None):
    """``op`` to ``worker`` on a thread of its own, once ``after`` (an earlier merge's release of the same chat) was
    sent, so a release never overtakes the next hold; ``then`` gets the answer, or the ``ChatOpFailed``."""
    def run():
        if after is not None and not after.wait(timeout):   # Never sent ahead of a release still under way.
            then(lifecycle.ChatOpFailed(op, session, "an earlier merge's release of this chat was not sent in time",
                                        timed_out=True))
            return
        early = _closed_first(session, op, worker) if session is not None else None
        if early is not None:
            then(early)
            return
        try:
            answer = lifecycle._send(session, op, worker, timeout, **fields)
        except lifecycle.ChatOpFailed as error:
            answer = NOTHING if session is not None and closing.closed_meanwhile(session, worker, error) else error
        except Exception as error:   # noqa: BLE001 - any fault fails the gate closed, never leaves it waiting.
            answer = lifecycle.ChatOpFailed(op, session, "%s: %s" % (type(error).__name__, error))
        then(answer)
    lifecycle._background(run, "AngeloWorkerMerge")


def _closed_first(session, op, worker):
    """A chat's op of a merge, on its thread: a close of the chat under way ends first, bounded. None to send it;
    ``NOTHING`` once the chat was handed back (nothing holds it); a ``ChatOpFailed`` (fail closed) when the close did
    not end in time or the chat is now hosted by another worker."""
    if not closing.when_closed(session, lifecycle.CLOSE_SECONDS):
        return lifecycle.ChatOpFailed(op, session, "its close in its worker did not end in time", timed_out=True)
    current = lifecycle.hosted(session)
    if current is None:
        return NOTHING
    if current is not worker:
        return lifecycle.ChatOpFailed(op, session, "another worker hosts it now")
    return None


def _release(held, leases):
    """Each lease's ``part.unlease``, then each hold's ``chat.release``, sent off the main thread."""
    if not held and not leases:
        return
    sent = {session: threading.Event() for session, _worker in held}
    _releasing.update(sent)   # The next hold of each chat goes out once this release did.

    def run():
        for lease in leases:
            lifecycle._unlease(lease[:2])
        for session, worker in held:
            try:
                lifecycle._send(session, "chat.release", worker, lifecycle.HOLD_SECONDS)
            except lifecycle.ChatOpFailed as error:   # A worker that died holds nothing; a wedged one is stopped later.
                if not closing.closed_meanwhile(session, worker, error):   # A chat that closed holds nothing either.
                    print("Angelo: a worker chat's merge hold could not be released:", error)
            finally:
                sent[session].set()
    lifecycle._background(run, "AngeloWorkerRelease")


def targets(lead, arguments):
    """``(sessions, paths)`` of a merge of ``lead`` with ``arguments``: its parts' worker chats and part files."""
    from . import worker_sessions
    numbers = [part.get("worker") for part in (arguments or {}).get("parts") or () if isinstance(part, dict)]
    found = worker_sessions._parts   # session -> (number, name, part file), as ``worker_sessions.parts_of`` reads it
    sessions = [session for session in worker_sessions._started.get(lead.state["id"], ())
                if session in found and found[session][0] in numbers]
    return sessions, [found[session][2] for session in sessions]


def _what(sessions, paths):
    """The hosted chats to hold and the files a live worker serves: what a gate asks for."""
    held = [(session, lifecycle.hosted(session)) for session in sessions]
    return [(s, w) for s, w in held if w is not None], [p for p in paths if lifecycle._serving(p) is not None]


def _overlapping(held, paths, key):
    sessions, files = {session for session, _worker in held}, {str(path) for path in paths}
    return any(other is not key and not gate.released and (
        sessions & {session for session, _worker in gate.held} or files & set(gate.paths))
        for other, gate in list(_gates.items()))


def _key(lead, call):
    return (lead.state["id"], call.id)


def waits(lead, call):
    """Phase one, at the merge ``call``'s execution: True while its holds and leases are being asked (its gate is
    started here once, then joined). False when it may run now: nothing to ask, every answer in (the merge's
    ``guard`` takes the gate), or another merge's gate holds its parts (``guard`` refuses it)."""
    if call.name != MERGE or not lifecycle.enabled():
        return False
    key = _key(lead, call)
    gate = _gates.get(key)
    if gate is not None and (gate.call is not call or gate.released and gate.error is None):
        _drop(key, gate)   # A failed one stays: the merge runs to its refusal (``guard``).
        gate = None
    if gate is not None and gate.error is None and gate.done.is_set() and not gate.fresh():
        gate.renew()   # Its leases grew old while the merge waited (its holds stay): asked again.
    if gate is None:
        sessions, paths = targets(lead, call.arguments)
        held, live = _what(sessions, paths)
        if not held and not live or _overlapping(held, live, key):
            return False
        from . import parallel_sessions
        for session in sessions:
            parallel_sessions.merging(session)   # Shown as in progress while its answers come.
        gate = _gates[key] = Gate(held, live, lead, call, sessions)
        gate.start()
    return not gate.done.is_set()


def asking(lead, call):
    """Whether the merge ``call``'s holds and leases are still being asked (the pump does not apply it meanwhile)."""
    gate = _gates.get(_key(lead, call))
    return gate is not None and gate.call is call and not gate.done.is_set()


def leased(path):
    """Whether a merge under way holds a fresh lease on ``path`` already (its own reads take no second one)."""
    return any(gate.done.is_set() and gate.error is None and not gate.released and gate.fresh()
               and str(path) in {lease[4] for lease in gate.leases} for gate in list(_gates.values()))


def any_asking():
    return any(not gate.done.is_set() for gate in list(_gates.values()))


def _drop(key, gate):
    if _gates.get(key) is gate:
        del _gates[key]
    gate.release()


def _told(lead, gate):
    """The merge's chat says why its gate failed (display only), with a support code."""
    cause, emit = gate.cause, getattr(lead, "emit", None)
    if emit is None:
        return
    from .ui_text import notice
    from .worker_reasons import explain
    text, code = (cause.shown() if isinstance(cause, lifecycle.ChatOpFailed)
                  else explain({"error": str(gate.error)}, "WRITER_BUSY", log="merge"))
    emit("message", dict(role="error", **notice(FAILED, why=text, code=code)))


@contextmanager
def guard(lead, sessions, paths):
    """Phase two, around the merge's reads (``worker_sessions._merge``): the gate its ``waits`` asked, ready and fresh,
    else a ``RefusalError`` (WRITER_BUSY) before anything is read; released after, whatever happened. Off: nothing."""
    if not lifecycle.enabled():
        yield
        return
    call = getattr(lead, "executing_call", None)
    key = _key(lead, call) if call is not None else None
    gate = _gates.get(key) if key is not None else None
    if gate is None or gate.call is not call:
        with waited(sessions, paths, key):   # A route that did not ask first: asked and waited for here.
            yield
        return
    try:
        held, live = _what(sessions, paths)
        if gate.error is not None:
            _told(lead, gate)
            raise gate.error
        if not gate.done.is_set() or not gate.fresh():
            raise lifecycle._refusal("A worker's part could not be read while its lease held")
        if not set(held) <= set(gate.held) or not {str(p) for p in live} <= set(gate.paths):
            raise lifecycle._refusal("A worker of this merge started hosting its part while the merge waited")
        yield
    finally:
        _drop(key, gate)


@contextmanager
def waited(sessions, paths, key=None):
    """The merge's holds and leases asked and waited for here, bounded (``worker_chat_lifecycle.merge_guard``): only a
    route that did not ask first. A ``RefusalError`` before anything is read when one is not granted in time."""
    held, live = _what(sessions, paths) if lifecycle.enabled() else ([], [])
    if not held and not live:
        yield
        return
    key = key or object()
    if _overlapping(held, live, key):
        refusal = lifecycle._refusal("Another merge of these parts is under way")
        refusal.merging_elsewhere = True
        raise refusal
    gate = _gates[key] = Gate(held, live).start()
    try:
        while not gate.done.wait(min(0.05, max(0.0, gate.deadline - time.monotonic()))):
            if time.monotonic() >= gate.deadline:   # The holds' deadline, then the leases' (each shared by all).
                gate.fail(lifecycle._refusal("A worker did not answer the merge's hold or lease in time"))
        if gate.error is not None:
            raise gate.error
        yield
    finally:
        _drop(key, gate)


def sweep(now=None):
    """The pump: a gate whose merge left its chat is released and its tiles put back; one past its deadline fails."""
    now = time.monotonic() if now is None else now
    for key, gate in list(_gates.items()):
        if gate.call is None:
            continue   # ``waited``'s own: it ends where it waits.
        if gate.lead.pending.get(gate.call.id) is not gate.call:
            _drop(key, gate)
            from . import parallel_sessions
            for session in gate.sessions:
                parallel_sessions.merge_failed(session)
        elif not gate.done.is_set() and now >= gate.deadline:
            gate.fail(lifecycle._refusal("A worker did not answer the merge's hold or lease in time"))


def reset():
    for key, gate in list(_gates.items()):
        _drop(key, gate)
    _releasing.clear()
