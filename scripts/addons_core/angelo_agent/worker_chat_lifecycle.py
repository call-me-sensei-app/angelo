# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's lifecycle on Angelo's side when its real Controller runs in its worker (design A', contract C10).

With A' a worker chat's Controller lives in its worker Blender (``worker_chat_host``), so every lifecycle step Angelo
takes for it is a chat op sent to that worker through one seam, ``send_op``, and each one fails closed:

* **open** (``opened``, from ``worker_routing.adopt``: a new start, a warm worker taken or reused, a parked chat's
  relaunch): ``chat.open`` once the chat binds; ``resume`` for a chat that continues its own conversation (a relaunch).
  A chat whose open fails leaves its worker, which stops: it never runs half-hosted. On Blender's main thread the
  open is two phases (``defer``): the chat binds and its side here is prepared at once, the ``chat.open`` itself is
  sent on a thread of its own (bounded by ``OPEN_SECONDS``), and ``finish_opens`` (the start timer) ends it there:
  its waiters (``when_open``: the brief, a relaunch's messages) run on success; on a refusal, a timeout or a lost
  worker the chat leaves its worker first. No frame waits on a worker, however wedged it is.
* **close** (``close_chat``): ``chat.close`` seals the chat's journal in its worker. A park closes before its save, a
  warm reuse before the process takes the next part, and every stop of a worker (``WorkerProcess.stop``) closes what it
  hosts first, each bounded; the stop itself then ends whatever a close could not reach. A chat is **closing** from
  before its ``chat.close`` is sent until that close ended, neither hosted nor handed back (``worker_chat_closing``);
  a second close joins the one under way.
* **switch** (``before_switch`` / ``after_switch``, ``WorkerProcess._switch``): every chat the worker hosts closes
  first; a close that fails refuses the switch (the worker is then stopped and launched afresh). A chat still bound to
  that worker afterwards moves with it: ``chat.open`` with ``resume`` on the new part (one that cannot fails the start).
* **merge** (``worker_merge_gate``): each part's hosted chat is held (``chat.hold``: no admission, answered once no
  call executes) and each live part file leased (``part.lease``) while the lead reads them, asked without waiting
  before the merge runs. One that cannot be held or leased in time refuses the merge before anything is appended;
  every hold and lease is released after, whatever happened (a hold that timed out too: the worker may still apply it).
* **read** (``leased_read``): any other read of a live part file takes a lease too (``part_leases``: the worker's save
  waits for it, bounded). A lease granted after its request was given up on is released as it arrives (``LATE``,
  ``worker_channel.Client.request``), whichever of these asked: no grant is dropped to stall the worker's next save.

The worker writes a chat's journal until it answered ``chat.close`` or its process ended; only then is the chat handed
back (``HANDOFF``). Off (``ANGELO_WORKER_CHAT_HOST=0``), or in a build without the worker side, nothing here sends
anything: no chat is ever hosted, every step is today's. Standard library only, no bpy; any thread (the table is
locked).
"""

import threading
from contextlib import contextmanager
from pathlib import Path

from .ui_text import n_

OPEN_SECONDS = 15.0    # The worker loads the chat's file and makes its Controller between two pumps.
CLOSE_SECONDS = 5.0    # Seals the journal and closes the provider (the lead ends the model child itself).
HOLD_SECONDS = 10.0    # A merge's part is finished, so no call should be executing; a call that is, finishes first.
LEASE_SECONDS = 10.0   # The longest one lease keeps the worker from saving its part (the worker caps it too).
LEASE_WAIT = 5.0       # Its grant: the worker answers on its reader thread, waiting only for a save under way.
READ_LEASE_WAIT = 1.0  # A read that is not a merge's (``leased_read``) waits less: its caller then reads nothing.
CLOSE_REASON = "the chat's worker stops"
# Words a connection setting's name carries when its value is a credential: never sent to a worker (rule 4).
SECRET_WORDS = ("key", "secret", "token", "password", "credential")
WITHHELD = frozenset({"auth_directory"})   # The connections folder: the lead launches the model child (C5).

# Shown in the chat whose worker could not open it (its worker stops; the start's status says why too).
OPEN_FAILED = n_("This chat could not start in its worker ({why}). Its worker was stopped; nothing ran.")

_lock = threading.Lock()
_hosted = {}   # chat session id -> the WorkerProcess that may host its Controller (from chat.open's send on)
_opening = {}  # chat session id -> Opening: a deferred chat.open not yet ended on the main thread (``finish_opens``)
_unanswered = {}   # chat session id -> its chat.open sends not answered yet, on every path (``answering``)
_closing = {}   # chat session id -> Event set once its chat.close under way ended (answered, refused or lost)
HANDOFF = []   # Callables (session, closed): its worker no longer writes its journal (closed: chat.close answered;
#                else its process ended). The journal's owner (L5) takes the chat back there.


class ChatOpFailed(RuntimeError):
    """A chat op that was not answered ``ok``: refused, not answered in time (``timed_out``), or its worker ended."""

    def __init__(self, op, session, why, timed_out=False, record=None, gone=False, sent=None):
        super().__init__("%s%s failed: %s" % (op, " for chat %s" % session if session else "", why))
        self.op, self.session, self.timed_out, self.gone = op, session, timed_out, gone
        self.sent = sent               # False: the lead never sent it (no worker hosts the chat); None: unknown.
        self.reason = str(why)[:300]   # Why, without the op or the chat id: what a person may be shown.
        self.record = record           # The worker's own ``encode_error`` record, for ``decode_error``.

    def shown(self):
        """``(text, support code)`` for a person (``worker_mirror_ops.why``)."""
        from .worker_mirror_ops import why
        return why({"error": self.record if self.record is not None else self.reason, "gone": self.gone,
                    "timed_out": self.timed_out, "not_sent": self.sent is False}, self.op)


def host_available():
    """Whether this build carries the worker side that hosts chats: workers run the same build as the lead."""
    from importlib.util import find_spec
    return find_spec(__package__ + ".worker_chat_host") is not None


def enabled(environ=None):
    """A' is on (``worker_chat_seams.enabled``: ``ANGELO_WORKER_CHAT_HOST`` unset or not ``0``), in a build that can
    host chats."""
    from .worker_chat_seams import enabled as switch
    return switch(environ) and host_available()


def send_op(session, op, *, worker, timeout, **fields):
    """The one seam every op goes through: ``op`` for chat ``session`` (None for an op of the worker's own) to
    ``worker`` (a ``WorkerProcess``), answered within ``timeout``; the answer, ``{"ok": True, ...}``. Anything else
    raises ``ChatOpFailed``: whether the worker applied it is then unknown, and every caller acts as if it might
have."""
    from .worker_process import RequestRefused, WorkerUnavailable
    if session is not None:
        fields["session"] = session
    late = LATE.get(op)
    if late is not None and getattr(worker, "takes_late", False) is True:   # Its answer after ``timeout`` is undone.
        fields["late"] = lambda answer: late(worker, answer)
    try:
        answer = worker.request(op, timeout, **fields)
    except WorkerUnavailable as error:
        raise ChatOpFailed(op, session, str(error), timed_out=error.timed_out,
                           gone=not error.timed_out and _process_ended(worker)) from error
    except RequestRefused as error:
        raise ChatOpFailed(op, session, str(error)) from error
    if not isinstance(answer, dict) or answer.get("error") is not None or answer.get("ok") is not True:
        why = answer.get("error") if isinstance(answer, dict) else answer
        record = why if isinstance(why, dict) else None
        text = (str(record.get("message"))[:200] if record is not None else str(why)[:200] if why is not None
                else "it answered without ok")
        raise ChatOpFailed(op, session, text, record=record)
    return answer


def _send(session, op, worker, timeout, **fields):
    return send_op(session, op, worker=worker, timeout=timeout, **fields)   # Looked up here: tests replace it.


def hosted(session):
    """The worker that may host ``session``'s Controller, or None."""
    with _lock:
        return _hosted.get(session)


def hosted_on(worker):
    """The chats ``worker`` may host, in the order they were opened."""
    with _lock:
        return [session for session, owner in _hosted.items() if owner is worker]


def _drop(session, closed):
    with _lock:
        if _hosted.pop(session, None) is None:
            return
    for owner in list(HANDOFF):
        try:
            owner(session, closed)
        except Exception as error:   # noqa: BLE001 - one owner's failure never keeps a chat in its worker.
            print("Angelo: a worker chat's journal handoff:", type(error).__name__, error)


def _process_ended(worker):
    process = getattr(worker, "process", None)
    return process is None or process.poll() is not None


def _prepare(controller, worker):
    """The chat's side in Angelo before its open (``worker_chat_wiring.prepare``): its mirror routed to ``worker``,
    its file handed off, its consent registered; the standing-consent lease the open carries (C6)."""
    from . import worker_chat_wiring
    return worker_chat_wiring.prepare(controller, worker)


def worker_settings(controller):
    """The chat's connection settings as a worker may hold them: no credential, no connections folder (rule 4)."""
    key = getattr(controller, "api_key", "") or ""
    return {name: value for name, value in dict(getattr(controller, "settings", None) or {}).items()
            if isinstance(name, str) and name not in WITHHELD and not any(w in name.lower() for w in SECRET_WORDS)
            and not (key and value == key)}


def _open_fields(controller, worker, resume):
    """The chat counts as hosted on ``worker`` from here (answered or not, a failed open is still closed or ended
    with its worker) and its side in Angelo is prepared (local only): the ``chat.open`` fields.

    One host per chat: while another worker may still host it (its stop has not handed it back), the open is
    refused and that worker's entry is left alone, so its stop still closes and hands back the chat, and the refused
    worker's never does (C7, single writer). A refusal while preparing (another writer, not a mirror) is before
    anything was sent, so the chat is not hosted on ``worker`` either."""
    session = controller.state["id"]
    with _lock:
        current = _hosted.get(session)
        if current is not None and current is not worker:
            raise ChatOpFailed("chat.open", session, "its previous worker still hosts it; nothing was opened")
        _hosted[session] = worker
        _unanswered[session] = _unanswered.get(session, 0) + 1   # Before its lease is registered (``answering``).
    try:
        consent = _prepare(controller, worker)
    except BaseException as error:
        _answered(session)
        if isinstance(error, ChatOpFailed):
            with _lock:
                if _hosted.get(session) is worker and current is None:
                    del _hosted[session]
        raise
    from .worker_mirror_pins import options as pinned_options
    return dict(store_directory=str(controller.store.directory), settings=worker_settings(controller),
                models=list(getattr(controller, "models", None) or ()),
                options=pinned_options(controller), consent=consent, resume=bool(resume),   # The lead's own.
                open_id=getattr(controller, "announced_open", None),   # C9: the id its frames carry.
                gpu_verdict=_gpu_verdict(worker))


def _gpu_verdict(worker):
    """Angelo's offscreen verdict for ``worker``'s binary (``remote_executor.OFFSCREEN_VERDICTS``, as the lane route
    passes it), so a hosted chat's first view never starts a probe child; None when Angelo has none: it probes."""
    from .remote_executor import OFFSCREEN_VERDICTS
    verdict = OFFSCREEN_VERDICTS.get(str(getattr(worker, "binary", None) or ""))
    return list(verdict) if verdict is not None else None


def open_chat(controller, worker, resume):
    """``chat.open`` for ``controller``'s chat on ``worker``, waited for here (``ChatOpFailed`` to the caller): only
    off Blender's main thread (a switch's move, ``after_switch``) or where nothing draws."""
    session, fields = controller.state["id"], _open_fields(controller, worker, resume)
    try:
        _send(session, "chat.open", worker, OPEN_SECONDS, **fields)
    finally:
        _answered(session)


def answering(session):
    """Whether a ``chat.open`` of ``session``, on any path, is not answered yet: it carries the chat's lease
    (``worker_consent_lead.register``), so a consent change sent meanwhile could reach the worker first and read as
    a lost revocation (re-review R2, and a switch's move, which opens inline)."""
    with _lock:
        return _unanswered.get(session, 0) > 0


def _answered(session):
    with _lock:
        left = _unanswered.get(session, 0) - 1
        if left > 0:
            _unanswered[session] = left
        else:
            _unanswered.pop(session, None)


class Opening:
    """A deferred ``chat.open``: sent on its own thread, ended on the main thread (``finish_opens``)."""

    def __init__(self, controller):
        self.controller, self.done, self.error, self.waiters = controller, threading.Event(), None, []


def opened(controller, worker, resume, defer=False):
    """``worker_routing.adopt``: the chat bound to ``worker`` opens there (off: nothing). One that cannot leaves its
    worker as every ending does (``worker_routing.unbind``, which stops it) and the error goes to the caller.
    ``defer`` (Blender's main thread): only the local phase runs here, the send goes off the main thread and
    ``finish_opens`` ends it (module docstring); this returns at once."""
    if not enabled():
        return
    try:
        if not defer:
            return open_chat(controller, worker, resume)
        fields = _open_fields(controller, worker, resume)
    except ChatOpFailed:
        _leave(controller)
        raise
    session, opening = controller.state["id"], Opening(controller)
    _opening[session] = opening

    def run():
        try:
            _send(session, "chat.open", worker, OPEN_SECONDS, **fields)
        except BaseException as error:   # noqa: BLE001 - ended on the main thread (``finish_opens``).
            opening.error = error
        finally:
            _answered(session)
            opening.done.set()
    threading.Thread(target=run, daemon=True, name="AngeloWorkerChatOpen").start()


def _leave(controller):
    from . import worker_routing
    worker_routing.unbind(controller, worker_routing.END_NOT_BRIEFED)   # No turn runs yet: nothing is said.


def when_open(controller, then):
    """Main thread: ``then(error)`` once ``controller``'s deferred open has ended (``finish_opens``: None when it
    opened). False when no open of it is under way: nothing to wait for."""
    opening = _opening.get(controller.state["id"])
    if opening is None or opening.controller is not controller:
        return False
    opening.waiters.append(then)
    return True


def opening():
    """Whether a deferred open is still to be ended (the start timer keeps running for it)."""
    return bool(_opening)


def finish_opens():
    """Main thread (the start timer): every deferred open that was answered, or failed, ends here. One that was not
    ``ok`` (refused, timed out, its worker lost) leaves its worker first, which stops; then its waiters run."""
    for session, entry in [item for item in list(_opening.items()) if item[1].done.is_set()]:
        if _opening.get(session) is entry:
            del _opening[session]
        error = entry.error
        if error is not None:
            try:
                _leave(entry.controller)
            except Exception as failure:   # noqa: BLE001 - its waiters still hear why.
                print("Angelo: a worker chat that could not open did not leave its worker:", failure)
        for then in entry.waiters:
            try:
                then(error)
            except Exception as failure:   # noqa: BLE001 - one waiter never stops the others or the timer.
                print("Angelo: after a worker chat's open:", type(failure).__name__, failure)


def tell(controller, error):
    """Say in the chat why it could not start in its worker (display only: its file is not written here)."""
    from .ui_text import notice
    from .worker_reasons import of_error
    text = error.shown()[0] if isinstance(error, ChatOpFailed) else of_error(error, "chat.open")[0]
    controller.emit("message", dict(role="error", **notice(OPEN_FAILED, why=text)))


def close_chat(session, reason, timeout=CLOSE_SECONDS):
    """``chat.close`` for a hosted chat; True when its worker answered (its journal is sealed and handed back), False
    when nothing was hosted. ``ChatOpFailed`` otherwise; a chat whose worker's process ended is handed back then.

    A worker that hosts no such chat answers ``{ok, closed: false}`` (C3): an earlier close it applied answered too
    late, or it never opened the chat. It writes nothing for it either way, so the chat is handed back on that
    answer as on any other, and a retried close always ends."""
    with _lock:
        worker, under_way = _hosted.get(session), _closing.get(session)
        if worker is not None and under_way is None:
            done = _closing[session] = threading.Event()   # Closing from before its chat.close is sent.
    if worker is None:
        return False
    if under_way is not None:   # Another close of it is under way: this one joins it, bounded.
        if not under_way.wait(timeout):
            raise ChatOpFailed("chat.close", session, "an earlier close of it did not end in time", timed_out=True)
        if hosted(session) is not None:
            raise ChatOpFailed("chat.close", session, "an earlier close of it did not go through")
        return True
    try:
        answer = _send(session, "chat.close", worker, timeout, reason=str(reason)[:200])
        if answer.get("closed") is False:
            print("Angelo: a worker no longer hosted a chat it was asked to close; it is handed back")
        _drop(session, closed=True)
        return True
    except ChatOpFailed:
        if _process_ended(worker):
            _drop(session, closed=False)
        raise
    finally:
        with _lock:
            if _closing.get(session) is done:
                del _closing[session]
        done.set()


def closing(worker):
    """``WorkerProcess.stop``, before it closes the connection: each chat ``worker`` hosts closes, bounded; a close
    that fails is logged (the stop that follows ends the process, and ``ended`` hands the chat back)."""
    for session in hosted_on(worker):
        try:
            close_chat(session, CLOSE_REASON)
        except ChatOpFailed as error:
            print("Angelo: a worker chat could not close before its worker stopped:", error)


def ended(worker):
    """``WorkerProcess.stop``, once its process has ended: every chat it may still host is handed back."""
    router = getattr(worker, "chat_router", None)
    if router is not None:
        router.end()   # Before the hand-backs: an open finishing meanwhile holds nothing (``EventRouter.end``).
    for session in hosted_on(worker):
        _drop(session, closed=False)


def before_switch(worker):
    """``WorkerProcess._switch``, before ``switch_part``: every chat ``worker`` hosts closes (the earlier part's
    journal sealed); ``ChatOpFailed`` refuses the switch. The chats still bound to ``worker`` (they move with it)."""
    from . import worker_routing
    sessions = hosted_on(worker)
    moving = [session for session in sessions if worker_routing._workers.get(session) is worker]
    for session in sessions:
        close_chat(session, "the worker takes another part")
    return moving


def after_switch(worker, moving):
    """After a start that switched (or relaunched) ``worker``: each chat in ``moving`` opens there again, with
    ``resume``. One whose chat is gone or cannot open fails the start (``ChatOpFailed``): its binding then ends as a
    dead worker's does."""
    for session in moving:
        controller = _controller(session)
        if controller is None:
            raise ChatOpFailed("chat.open", session, "its chat is no longer in Angelo")
        open_chat(controller, worker, resume=True)


def _controller(session):
    try:
        from . import ui
    except ImportError:
        return None
    return ui._controllers.get(session)


def _refusal(message):
    from .refusal_codes import RefusalError
    error = RefusalError(message + "; nothing was merged", code="WRITER_BUSY")
    error.partial_changes_possible = False
    return error


def _serving(path):
    """A live worker that has ``path`` open as its part (it may save it), or None."""
    from . import worker_process
    target = Path(path)
    for worker in worker_process.live():
        served = getattr(worker, "serving", None) or getattr(worker, "part", None)
        if served is not None and Path(served) == target and worker.alive():
            return worker
    return None


def _lease(path, wait):
    """A lease on ``path`` from the live worker that serves it: (worker, token, seconds granted), or None when no worker
    can save it. ``ChatOpFailed`` when a live worker does not grant it in time."""
    worker = _serving(path)
    if worker is None:
        return None
    try:
        answer = _send(None, "part.lease", worker, wait, part=str(path), seconds=LEASE_SECONDS)
    except ChatOpFailed:
        if _process_ended(worker):
            return None   # Its process ended meanwhile: nothing writes the file any more.
        raise
    seconds = answer.get("seconds")   # The worker caps what was asked; anything else reads as the most asked.
    granted = type(seconds) in (int, float) and 0 < seconds <= LEASE_SECONDS
    return worker, answer.get("lease"), float(seconds) if granted else LEASE_SECONDS


def _released_late(worker, answer):
    """A lease granted after its request was given up on (any thread): released at once, never left to stall the
    worker's next save until its bound (audit #4)."""
    if isinstance(answer, dict) and answer.get("ok") is True and answer.get("lease") is not None:
        _unlease((worker, answer["lease"]))


LATE = {"part.lease": _released_late}   # An op whose late answer still holds something: what undoes it.


def _unlease(lease):
    worker, token = lease[:2]
    try:
        _send(None, "part.unlease", worker, LEASE_WAIT, lease=token)
    except ChatOpFailed as error:   # The worker's own bound ends the lease anyway.
        print("Angelo: a part lease could not be released:", error)


@contextmanager
def leased_read(path):
    """Read ``path`` with its live worker's lease (off: as today). ``ChatOpFailed`` when it is not granted: the
    caller then reads nothing."""
    from . import worker_merge_gate   # A merge's own read, under the lease its gate holds: no second one.
    lease = _lease(path, READ_LEASE_WAIT) if enabled() and not worker_merge_gate.leased(path) else None
    try:
        yield
    finally:
        if lease is not None:
            _unlease(lease)


def _background(function, name):
    """A merge's sends run here, off the main thread (a test may run them inline)."""
    threading.Thread(target=function, daemon=True, name=name).start()


@contextmanager
def merge_guard(sessions, paths):
    """A merge's reads of its parts' files: every hosted chat of ``sessions`` held, every live part file of ``paths``
    leased, asked and waited for here, bounded, and all released after (``worker_merge_gate.waited``: only a route
    that did not ask first; the merge itself asks without waiting, ``worker_merge_gate``). A ``RefusalError``
    (WRITER_BUSY) before anything is read when one cannot be held or leased in time. Off: nothing is sent."""
    from . import worker_merge_gate
    with worker_merge_gate.waited(sessions, paths):
        yield
