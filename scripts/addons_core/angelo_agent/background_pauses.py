# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short pauses in a background thread's long Python passes, so Blender's main thread gets the GIL quickly.

The snapshot worker's whole-history passes (its lock-free retention and
orphan probes, store accounting) are Python loops that share the GIL with the
main thread. Python hands the GIL to a waiting thread only after the switch
interval (``sys.getswitchinterval()``, 5 ms by default), so each time the main
thread needs it during such a pass it can wait that long. A pass that calls
``pause()`` once per item sleeps ``PAUSE_SECONDS`` after ``WORK_SECONDS`` of
its own work: the sleep releases the GIL to a waiting main thread at once (a
zero-length sleep does not reliably, because the releasing thread usually takes
the GIL back first). The budget is time, measured: a sleep that overran (0.2 ms
asked takes about 0.3 ms on a Mac) earns the pass correspondingly more work
before the next one, so pauses take at most ``PAUSE_SHARE`` of its time, up
to ``MAX_WORK_SECONDS`` of work between them (a sleep the system stretched far
past that does not buy the pass a long stretch of GIL). The main thread waits
at most about that much work: half a millisecond, two after an overrun.

Only a thread that called ``enable()`` (the snapshot worker) ever pauses; the
same code on the main thread does not. A pause never happens while the thread
holds a profile transaction, which Blender's main thread needs for its next
capture: pausing then would make it wait longer, not shorter; garbage
collection, which holds the store's writer lock, has no pauses.
"""

import threading
import time

from .checkpoint_transactions import holds_any_transaction

WORK_SECONDS = 0.0005
PAUSE_SECONDS = 0.0002
PAUSE_SHARE = 0.3  # At most this share of a pass's time is spent paused, however long a sleep really takes,
MAX_WORK_SECONDS = 0.002  # up to this much work between two pauses.
_local = threading.local()


def enable():
    """Let this thread's passes pause (the snapshot worker's thread, from its loop)."""
    _local.deadline = time.perf_counter() + WORK_SECONDS


def pause():
    """Called once per item of a long pass: sleep briefly when this thread has worked ``WORK_SECONDS``."""
    deadline = getattr(_local, "deadline", None)
    if deadline is None:
        return
    now = time.perf_counter()
    if now < deadline:
        return
    work = WORK_SECONDS
    if not holds_any_transaction():
        time.sleep(PAUSE_SECONDS)
        woke = time.perf_counter()
        work = min(MAX_WORK_SECONDS, max(WORK_SECONDS, (woke - now) * (1 - PAUSE_SHARE) / PAUSE_SHARE))
        now = woke
    _local.deadline = now + work
