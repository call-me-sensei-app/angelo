# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's consent frame holds from its arrival, before its lease is applied (``worker_chat_arrival``).

The worker's reader thread never waits for a chat's lock: a provider thread holds it while it saves the chat's
journal, and a slow disk would hold a revocation, and the Stop behind it, for that save's length. So the reader only
records a **mark** of each ``chat.consent`` frame of a hosted chat (its epoch and the grants it carries,
``mark_of``), and an **applier** thread per chat applies the leases in their arrival order under the chat's lock
(``worker_consent.apply_consent``). The mark ends as its lease is applied.

**When it is answered.** A frame that narrows anything the chat may hold (its lease, or a consent that arrived
before it and is not applied yet) is acknowledged as soon as it is marked (``acknowledged_on_arrival``): from then
on the mark wins every admission and start check, so the worker enforces it, and the lead's ack-or-kill deadline
(``worker_consent_held.ACK_SECONDS``) is about exactly that; a slow save never turns it into a spurious stop. Any
other frame (a widening, a repeat, an older or conflicting epoch, a frame that is not a lease, or one while a
consent queued before the chat was hosted is not handled yet) is answered only once applied, so a widening is never
held early. A narrowing acknowledged on arrival whose application then fails keeps its mark (fail closed): the chat
refuses what it withdraws until a newer lease is applied.

Until then the mark wins (fail closed): a call starts without the person only when the lease grants it **and** every
consent that arrived since still grants it as the lease holds it (``covers``), so a narrowing holds from its arrival
and a widening holds only once applied. A mark is judged as ``apply_consent`` will judge its frame: an older epoch
or a repeat changes nothing, conflicting grants under the epoch held revoke everything, and a frame that is not a
lease (refused on application) changes nothing. Standard library only; no bpy.
"""

import threading
from collections import deque

from .worker_consent import GRANTS, clean_grants


def mark_of(request):
    """``(epoch, grants)`` of a ``chat.consent`` frame, or None when it is not a lease (it changes nothing)."""
    epoch, clean = request.get("epoch"), clean_grants(request.get("grants"))
    if type(epoch) is not int or epoch < 0 or clean is None:
        return None
    return epoch, clean


def covers(lease, marks, grant):
    """Whether every consent in ``marks`` (arrived, not applied yet) still grants ``grant`` ("auto", "scripts",
    "always_allow") as ``lease`` holds it now. Read the marks before the lease: a lease applied in between then
    finds its own mark a repeat."""
    held = lease.grants()
    for epoch, clean in marks:
        if lease.epoch is not None and epoch < lease.epoch:
            continue   # Older than the lease held: refused on application.
        if epoch == lease.epoch:
            if clean == held:
                continue   # A repeat: acknowledged again, nothing changes.
            return False   # Conflicting grants under one epoch revoke everything.
        if clean["auto"] is not True:
            return False
        if grant != "auto" and (grant not in GRANTS[1:] or held.get(grant) is None or clean.get(grant) != held[grant]):
            return False   # Withdrawn or replaced: a new nonce is a new token, granted only once applied.
    return True


def acknowledged_on_arrival(lease, marks, mark):
    """Whether ``mark`` is acknowledged as it is marked: it narrows what ``lease`` or an earlier mark in ``marks``
    grants, and it is newer than all of them, so applying it in arrival order takes its epoch (module docstring)."""
    from .worker_consent_held import narrows
    epoch, clean = mark
    if lease.epoch is None or epoch <= lease.epoch or any(epoch <= other for other, _grants in marks):
        return False
    return any(narrows(before, clean) for before in [lease.grants()] + [grants for _epoch, grants in marks])


class Applier:
    """Applies each chat's consent frames in arrival order on a thread of that chat's, never the reader's: ``apply``
    runs ``(seq, chat, request)``, which may wait for the chat's lock. One chat's slow save never delays another's."""

    def __init__(self, apply):
        self._apply = apply
        self._lock = threading.Lock()   # Never held across ``apply``.
        self._queues = {}               # session -> deque of frames not applied yet; present while its thread runs

    def put(self, session, item):
        with self._lock:
            waiting = self._queues.get(session)
            if waiting is not None:
                waiting.append(item)
                return
            self._queues[session] = deque([item])
        threading.Thread(target=self._drain, args=(session,), daemon=True, name="AngeloWorkerConsent").start()

    def _drain(self, session):
        while True:
            with self._lock:
                waiting = self._queues[session]
                if not waiting:
                    del self._queues[session]
                    return
                item = waiting.popleft()
            try:
                self._apply(*item)
            except Exception as error:  # noqa: BLE001 - its mark stays, so the chat stays refused (fail closed).
                print("Angelo worker: a consent lease was not applied:", type(error).__name__, error)
