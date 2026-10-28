# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One writer for a hosted chat's journal (design A', contract C7): the worker Blender, or Angelo, never both.

A worker chat's ``Controller`` runs in its worker Blender and writes the chat's file in Angelo's state folder
(``sessions/<id>.json``, ``SessionStore``). Angelo's mirror of the chat reads that file and never writes it.

* **Worker side.** The hosted chat's store is an ``OwnedSessionStore`` (a ``Controller`` subclass sets
  ``store_type``; ``HostedJournal``). Like every store it writes only over the file as it last read or wrote it
  (``session_guard``): another writer's change makes it refuse with ``JournalTaken`` and write nothing, while its
  own failed write or a metadata-only change does not. Its load is the claim: a file that changed while it was
  read is not hosted here (``JournalTaken``). An interleaved journal append cannot slip between the check and a
  rewrite either: the check holds the old file's ``flock``, and an append that waited for it finds that file
  unlinked and changes nothing (``session_journal.append``). ``release`` ends the ownership (``chat.close``, a
  park, Angelo gone): every later write raises ``JournalReleased``.
* **Angelo side.** ``hand_off`` (before ``chat.open``) marks the chat hosted (``sessions.HOSTED``). From then on no
  store in Angelo's process writes the file, and a store loaded before the hand-off never writes it again, even
  after ownership returns: it must ``load`` first. Ownership returns only through ``close_answered`` (the worker
  answered ``chat.close``) or ``worker_ended`` (its process has ended; ``ended()`` must say so, else nothing
  changes).
* **Worker crash** (``worker_ended``). The chat file is reopened exactly as Angelo reopens a chat after its own
  crash: loaded, then saved once with the save's time (``TranscriptRows.save``). Every admitted call has a durable
  record before it runs, and each record already holds its fail-closed result (``tool_finalization.journal_tool``:
  ``interrupted``, ``partial_changes_possible`` once execution or a commit started). So a crash leaves those
  calls ``interrupted``, with nothing to repeat. ``Recovered.interrupted`` lists them. ``Recovered.unrecorded``
  lists the calls the mirror showed that have no record: they were never durably admitted, so they never ran.
  The mirror then ends the turn from ``Recovered.state``.
* **Angelo crash** (``lead_gone``). The worker's connection reader ends when the channel closes
  (``worker_host._read``). The loop then stops each hosted chat's turn, closes it (the close's compacting save is
  the seal) and releases its store before the worker exits.

Standard library only; no bpy.
"""

import threading
import time
import uuid

from .session_guard import GuardedWriter, is_unknown
from .session_journal import JournalHosted, JournalTaken
from .sessions import HOSTED, SessionStore


class JournalReleased(OSError):
    """This owner gave the chat's file up (``release``): writing has stopped."""


class OwnedWriter(GuardedWriter):
    """The worker's ``GuardedWriter``, which also stops when released."""

    def __init__(self, path):
        self.released = False
        super().__init__(path)

    def _refuse(self):
        if self.released:
            raise JournalReleased("This chat's file was handed back; this worker writes nothing more to it")


class OwnedSessionStore(SessionStore):
    """The hosted chat's store in its worker: the single writer of the file it loaded (module docstring)."""

    def __init__(self, directory, session_id=None):
        super().__init__(directory, session_id)
        owned = OwnedWriter(self.path)
        owned.owned_fields = self._writer.owned_fields
        self._writer = owned

    def load(self):
        state = super().load()
        if is_unknown(self._writer.expected):
            raise JournalTaken("This chat's file changed while it was read; it is not hosted here")
        return state

    def _refuse_unkept(self):
        """``SessionStore``'s own refusal, without the hosted check: this store is the host (``sessions.HOSTED`` is
        the lead's record, and lead and worker share one process only in host tests)."""
        if self._unkept:
            raise OSError("This chat's saved file could not be read or kept; it is not saved over")

    @property
    def owned(self):
        return not self._writer.released

    def release(self):
        """Stop writing (``chat.close``, a park, Angelo gone): every later save raises ``JournalReleased``."""
        with self._writer.lock:
            self._writer.released = True


class HostedJournal:
    """Mixin for the worker's hosted ``Controller``: its store is an ``OwnedSessionStore`` (``Controller.__init__``)."""

    store_type = OwnedSessionStore


def end_hosting(controller):
    """Worker: end a hosted chat (``chat.close``, a park): close it, which compacts its file, then release it.

    Returns whether the close's save reached the file. Writing has stopped either way.
    """
    try:
        controller.close()
    finally:
        release = getattr(controller.store, "release", None)
        if callable(release):
            release()
    return not getattr(controller, "persistence_warning", "")


def lead_gone(controllers):
    """Worker: Angelo's channel closed. Stop every hosted chat's turn, then end it; never raises."""
    ended = {}
    for controller in list(controllers):
        session = controller.state.get("id") if isinstance(getattr(controller, "state", None), dict) else None
        try:
            if getattr(controller, "busy", False):
                controller.stop("Angelo ended; this worker stopped the turn")
            ended[session] = end_hosting(controller)
        except Exception:  # noqa: BLE001 - every other chat is still ended.
            ended[session] = False
            release = getattr(getattr(controller, "store", None), "release", None)
            if callable(release):
                release()
    return ended


# Angelo side --------------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_hosts = {}   # session id -> a callable, True once the hosting worker's process has ended.


def _key(session_id):
    return str(uuid.UUID(session_id))


def hand_off(session_id, ended):
    """Before ``chat.open``: a worker writes this chat's file from now on (``JournalTaken`` if one already does)."""
    key = _key(session_id)
    if not callable(ended):
        raise TypeError("hand_off needs a callable that says whether the worker's process has ended")
    with _lock:
        if key in _hosts:
            raise JournalTaken("Another worker already writes this chat's file")
        _hosts[key] = ended
        HOSTED[key] = (True, HOSTED.get(key, (False, 0))[1] + 1)


def hosted(session_id):
    with _lock:
        return _key(session_id) in _hosts


def _regain(key):
    del _hosts[key]
    HOSTED[key] = (False, HOSTED[key][1])


def close_answered(session_id):
    """The worker answered ``chat.close``: its writing has stopped, so the file is this process's again."""
    key = _key(session_id)
    with _lock:
        if key not in _hosts:
            raise JournalHosted("This chat was not handed to a worker")
        _regain(key)


class Recovered:
    """A chat reopened after its worker ended (``worker_ended``)."""

    __slots__ = ("state", "interrupted", "unrecorded", "saved", "finished")

    def __init__(self, state, interrupted, unrecorded, saved):
        self.state, self.interrupted, self.unrecorded, self.saved = state, interrupted, unrecorded, saved
        self.finished = {"status": "interrupted"}   # The turn's end the mirror emits.


def worker_ended(session_id, directory, pending=()):
    """The hosting worker's process has ended: regain the file, reopen the chat as after a crash (``recover``).

    ``JournalHosted`` and nothing changed while ``ended()`` still says the process runs, or when the chat was never
    handed off. ``pending``: the call ids the mirror showed.
    """
    key = _key(session_id)
    with _lock:
        ended = _hosts.get(key)
        if ended is None:
            raise JournalHosted("This chat was not handed to a worker")
        if ended() is not True:
            raise JournalHosted("The worker's process has not ended; its chat's file is still its own")
        _regain(key)
    return recover(directory, key, pending)


def recover(directory, session_id, pending=()):
    """Reopen a chat as Angelo does after its own crash: load it, save it once with the save's time."""
    from .tool_finalization import PERSISTENCE_ERRORS
    store = SessionStore(directory, session_id)
    state = store.load()
    saved = True
    try:
        state["updated"] = time.time()   # As every Controller save (TranscriptRows.save).
        store.save(state)
    except PERSISTENCE_ERRORS:
        saved = False   # The file keeps the worker's records, which load the same way next time.
    results = state["results"]
    interrupted = [{"id": key, "phase": record.get("phase"), "result": record.get("result")}
                   for key, record in results.items() if record.get("phase") != "completed"]
    unrecorded = [call for call in pending if call not in results]
    return Recovered(state, interrupted, unrecorded, saved)
