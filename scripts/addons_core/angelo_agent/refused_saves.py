# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the person is told when Windows keeps refusing a save (``shared_replace.Refused``): another program has the
file open. Every replace in the add-on goes through ``shared_replace``, so this one place says it for all of them;
each caller keeps what it was saving or reports its own failure (doc/angelo/agent_execution.rst, "Every replace
lands while the file is read").

``heard`` runs on any thread and only queues the file's name; a one-second timer on the main thread shows one card
for each file due, each at most every ``AGAIN`` seconds. Each file's card is its own kind (``refused_save:<name>``),
so it never takes the place of another notice waiting to be shown (the settings import's Undo, a crash report) nor of
another file's card. The names are the person's or Angelo's file names, never translated.
"""

import collections
import os
import time

from . import pump_guard, shared_replace
from .ui_text import iface_, n_

HELD = n_("Another program has {file} open, so Angelo couldn't save it just now. If this keeps happening, close "
          "that program.")
AGAIN = 600.0      # Seconds before the same file's refusal is told again.
KIND = "refused_save:"   # i18n: an internal card kind (with the file's name), never shown.
WAITING = 3        # At most this many of these cards wait to be shown; more files are told as they go.
INTERVAL = 1.0     # The main-thread check of the queue (an empty deque: nothing else runs).
_heard = collections.deque(maxlen=32)   # File names refused, oldest first (deque appends are thread-safe).
_told = {}         # {name: when (monotonic) it was told}


def heard(target):
    """``shared_replace.on_refused``: queue ``target``'s name to be told on the main thread."""
    _heard.append(os.path.basename(os.fspath(target)))


def due(now=None, limit=None):
    """The names to tell now, once each, never sooner than ``AGAIN`` after the last time; at most ``limit`` (the
    rest stay queued for a later tick)."""
    now = time.monotonic() if now is None else now
    names = []
    while _heard and (limit is None or len(names) < limit):
        name = _heard.popleft()
        if name not in names and now - _told.get(name, now - AGAIN) >= AGAIN:
            _told[name] = now
            names.append(name)
    return names


@pump_guard.keep_alive(INTERVAL)   # Blender drops a timer that raises: the next refusal is still told.
def _tell():
    if not _heard:
        return INTERVAL
    from . import app_notices, card_host, update_card
    waiting = sum(card.kind.startswith(KIND) for card in card_host.queued())
    names = due(limit=max(0, WAITING - waiting))   # Never a queue of cards ahead of other notices.
    if names:
        for name in names:
            card = update_card.NoticeCard("alert", iface_(HELD, file=name), "", lifetime=10)
            card.kind = KIND + name   # Waits beside other notices and other files' cards, never in their place.
            card_host.request(card)
        card_host.pump(app_notices.ready)
    return INTERVAL


def register():
    import bpy
    shared_replace.on_refused = heard
    if not bpy.app.timers.is_registered(_tell):
        bpy.app.timers.register(_tell, first_interval=INTERVAL, persistent=True)


def unregister():
    import bpy
    shared_replace.on_refused = None
    if bpy.app.timers.is_registered(_tell):
        bpy.app.timers.unregister(_tell)
    _heard.clear()
