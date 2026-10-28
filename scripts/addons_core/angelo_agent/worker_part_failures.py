# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosting worker's failed part saves, as its chats show them (design A′, audit #6).

A worker that could not save its part (``worker_chat_part.save_now``: a full disk, a folder gone) says
``part_save_failed`` with no chat named, and tries again a second later. Each chat the worker hosts then shows one
kept row (``worker_mirror_ops.add_row``: it stays across the mirror's reloads) saying its edits are not saved yet,
with the support code ``NOT_SAVED``, once per failure episode: retries that fail again add nothing, and the next save
that works (``part_saved``) ends the episode, so a later failure is said again. A chat opened on the worker during an
episode is told at its next failure. The worker's raw words are only logged. Lead-only mirror event ``SAVE_FAILED``
(a ``worker_`` kind no worker frame can pose as), applied on the main thread with the chat's other frames. Standard
library only, no bpy.
"""

import threading

from .ui_text import n_, notice

SAVE_FAILED = "worker_part_save_failed"
NOT_SAVED = "WRK-NOT-SAVED"   # Its support code (doc/angelo/agent_integration.rst).
UNSAVED = n_("Your latest edits in this chat are not saved yet: its worker could not save its part, and it keeps "
             "trying (support code {code}).")

_lock = threading.Lock()
_told = {}   # id(worker) -> (worker, sessions told in its current failure episode)


def failed(worker, data, sessions, mirror_of):
    """The channel's reader thread: ``worker``'s save failed; each of ``sessions`` (the chats it hosts) not told yet
    in this episode gets its row, through ``mirror_of(session)``."""
    from . import worker_mirror_events as events
    from .worker_mirror import LEAD_KINDS
    LEAD_KINDS.setdefault(SAVE_FAILED, row)   # Applied by the mirror's pump, on the main thread.
    error = data.get("error") if isinstance(data, dict) else None
    print("Angelo: a worker could not save its part (%s): %s" % (NOT_SAVED, str(error)[:300]))
    with _lock:
        entry = _told.get(id(worker))
        if entry is None or entry[0] is not worker:
            entry = _told[id(worker)] = (worker, set())
        fresh = [session for session in sessions if session not in entry[1]]
        entry[1].update(fresh)
    for session in fresh:
        mirror = mirror_of(session)
        if mirror is not None:
            events.put(mirror, SAVE_FAILED, {})


def saved(worker):
    """``worker`` saved its part: its failure episode, if any, is over."""
    with _lock:
        entry = _told.get(id(worker))
        if entry is not None and entry[0] is worker:
            del _told[id(worker)]


def row(mirror, data):
    """Main thread (``LEAD_KINDS``): the chat's kept row."""
    from .worker_mirror_ops import add_row
    add_row(mirror, "error", notice(UNSAVED, code=NOT_SAVED))
    return True
