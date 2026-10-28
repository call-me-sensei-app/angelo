# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker's part file, leased to Angelo while Angelo reads it (design A', C10's part read lease; ``part.lease`` and
``part.unlease``).

Angelo reads a live worker's part file as the worker may save it: a merge lists the part's names and appends from it in
two opens of the file, and on Windows a save cannot replace a file another process has open. So Angelo's read takes a
lease first (``worker_chat_lifecycle``), and the worker's save of that part waits while a lease lasts:

* the worker takes both ops on its **reader thread** (``answers``), never queued behind its main loop, which may be
  running a call or saving, and the reader never waits there (audit2 #4): a release is answered at once, and so is a
  lease when no save of the part runs or waits. A lease asked while one does is recorded and granted, and answered
  from the save path, as that save ends (``saving``); a save that starts while leases are asked grants them first and
  then waits for them, so neither a read nor a save starves the other. A release, a Stop or a consent frame that
  follows a lease request is therefore never stuck behind it. Its answers go out on a sender thread of their own
  (``Replies``), never the reader's: the connection writes one whole frame at a time, and the main loop may be
  writing a large answer;
* a lease lasts at most ``MAX_SECONDS``, whatever Angelo asked: a save waits for the release or that bound, never
  longer, so an Angelo that died or forgot its release never stops the part from saving;
* a lease names the part file; one for any other file is refused (Angelo would read the wrong file).

Nothing else changes: with no lease (``ANGELO_WORKER_CHAT_HOST=0`` sends none) a save goes ahead at once. Standard
library only; the table is shared by the reader thread and the main thread under one condition.
"""

import math
import os
import queue
import secrets
import threading
import time
from contextlib import contextmanager

OPS = ("part.lease", "part.unlease")
MAX_SECONDS = 10.0   # The longest one lease holds a save back.
_tables = threading.Lock()


class PartLeases:
    """Leases of one worker's part files and the saves that wait for them."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._condition = threading.Condition()
        self._leases = {}        # token -> (part, expires)
        self._saving = 0
        self._waiting_saves = 0
        self._asked = []         # (part, seconds, answer): leases asked while a save ran or waited, granted as it ends

    def _live(self, part=None):
        now = self._clock()
        for token in [token for token, (_part, expires) in self._leases.items() if expires <= now]:
            del self._leases[token]
        return [expires for held, expires in self._leases.values() if part is None or held == part]

    def ask(self, part, seconds, answer):
        """A lease on ``part`` for ``seconds`` (capped), never waited for: ``answer((token, seconds))`` runs now when
        no save runs or waits, else from the save path as it ends (module docstring). ``ValueError`` for a length
        that is not a positive, finite number."""
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("A lease lasts a positive, finite number of seconds")
        seconds = min(seconds, MAX_SECONDS)
        with self._condition:
            if self._saving or self._waiting_saves:
                self._asked.append((part, seconds, answer))
                return
            grant = self._grant(part, seconds)
        answer(grant)

    def lease(self, part, seconds):
        """``ask``'s grant when it is granted at once, else None (asked, granted as the save ends, to nobody)."""
        granted = []
        self.ask(part, seconds, granted.append)
        return granted[0] if granted else None

    def _grant(self, part, seconds):
        token = secrets.token_hex(16)
        self._leases[token] = (part, self._clock() + seconds)
        return token, seconds

    def _granted(self):
        """Under the condition: the leases asked meanwhile, granted now (their answers are sent outside it)."""
        asked, self._asked = self._asked, []
        return [(answer, self._grant(part, seconds)) for part, seconds, answer in asked]

    @staticmethod
    def _answer(granted):
        for answer, grant in granted:
            try:
                answer(grant)
            except Exception as error:  # noqa: BLE001 - a lead that is gone never stops the save.
                print("Angelo worker: a part lease could not be answered:", type(error).__name__, error)

    def unlease(self, token):
        """End a lease; whether it was still held. Releasing twice, or an expired lease, is harmless."""
        with self._condition:
            found = self._leases.pop(token, None) is not None
            self._condition.notify_all()
            return found

    def held(self, part=None):
        with self._condition:
            return bool(self._live(part))

    @contextmanager
    def saving(self, part):
        """A save of ``part``: it grants the leases already asked, then waits while a lease on it lasts (each ends by
        its release or its bound); no lease is granted while it waits or runs, and those asked meanwhile are granted
        as it ends."""
        with self._condition:
            granted = self._granted() if not self._saving else []
            self._waiting_saves += 1
        self._answer(granted)
        with self._condition:
            try:
                while True:
                    live = self._live(part)
                    if not live:
                        break
                    self._condition.wait(max(min(live) - self._clock(), 0.001))
            finally:
                self._waiting_saves -= 1
            self._saving += 1
        try:
            yield
        finally:
            with self._condition:
                self._saving -= 1
                self._condition.notify_all()
                granted = self._granted() if not (self._saving or self._waiting_saves) else []
            self._answer(granted)


class Replies:
    """A worker's answers sent off the reader thread, in order, by one daemon thread."""

    def __init__(self, endpoint):
        self.endpoint, self._queue = endpoint, queue.Queue()
        threading.Thread(target=self._run, daemon=True, name="AngeloWorkerReplies").start()

    def put(self, frame):
        self._queue.put(frame)

    def _run(self):
        while True:
            frame = self._queue.get()
            try:
                self.endpoint.send(frame)
            except (OSError, ValueError):
                return   # The lead is gone; the reader ends the loop.


def replies_of(host):
    with _tables:
        replies = host.__dict__.get("reader_replies")
        if replies is None:
            replies = host.__dict__["reader_replies"] = Replies(host.endpoint)
        return replies


def leases_of(host):
    """The ``PartLeases`` of a worker host (made once, from whichever thread asks first)."""
    with _tables:
        table = host.__dict__.get("part_leases")
        if table is None:
            table = host.__dict__["part_leases"] = PartLeases()
        return table


def _same(one, other):
    return os.path.normcase(os.path.realpath(str(one))) == os.path.normcase(os.path.realpath(str(other)))


def handle(host, request, reply):
    """One lease op, answered once through ``reply`` (``{"ok": True, ...}``), never waited for; raises for a request
    it refuses."""
    table = leases_of(host)
    if request.get("op") == "part.lease":
        part = request.get("part")
        if not isinstance(part, str) or not _same(part, host.part):
            raise ValueError("This worker leases only its own part file")
        table.ask(host.part, request.get("seconds", MAX_SECONDS),
                  lambda grant: reply({"ok": True, "lease": grant[0], "seconds": grant[1]}))
        return
    token = request.get("lease")
    reply({"ok": True, "released": isinstance(token, str) and table.unlease(token)})


def answers(host, seq, request):
    """The worker's reader thread: take a lease op without waiting, answered on the connection (by ``Replies``) now
    or as a save ends; True when ``request`` was one, so the main loop never sees it."""
    if not isinstance(request, dict) or request.get("op") not in OPS:
        return False
    from . import worker_values
    replies = replies_of(host)

    def reply(response):
        replies.put({"seq": seq, "response": response})
    try:
        handle(host, request, reply)
    except Exception as error:   # noqa: BLE001 - every request is answered; the error is the answer.
        reply({"error": worker_values.encode_error(error)})
    return True
