# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One write at a time into a state file, a bounded wait for a write in progress, and bounded close-time writes.

A store with a background writer (``telemetry.TelemetryStore``, ``usage_meter.UsageLedger``) takes its turn for
every write, the writer's and a close's alike. A close (a chat closing, quit, tests) waits for the write in
progress, then writes what is owed; a background write that runs after it finds nothing owed and never touches the
folder. So once a close returns, nothing of that store writes into the folder until the next change, and a quit
loses nothing.

Every wait is bounded, only for a stalled disk. A wait that gives up on a write is remembered: a later wait for that
same write gives up at once, so closing many chats at quit waits once, never once per chat. ``within`` runs a
close's own writes on a helper thread, so the bound covers them too: the caller (the UI thread, quit) never waits
longer than it.
"""

import math
import threading
import time
import weakref


def _seconds(timeout):
    """A wait's bound as ``Condition.wait`` takes it: 0 for None, negative or NaN; at most ``TIMEOUT_MAX``."""
    if type(timeout) not in (int, float) or not timeout > 0:
        return 0.0
    return min(float(timeout), threading.TIMEOUT_MAX) if math.isfinite(timeout) else threading.TIMEOUT_MAX


class WriteTurn:
    def __init__(self):
        self._condition = threading.Condition(threading.Lock())
        self._busy = False
        self._write = 0           # Names the write holding the turn; changes as the turn is taken.
        self._abandoned = None    # The write a bounded wait gave up on.

    def _take(self):
        self._busy = True
        self._write += 1

    def __enter__(self):
        with self._condition:
            while self._busy:
                self._condition.wait()
            self._take()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def acquire(self, timeout):
        """Take the turn within ``timeout`` seconds; False (nothing taken) once the write in progress outlasts it.

        A bound of 0 (or none) only checks: it never gives up on the write in progress for later waits. Release
        with ``release``. Never raises."""
        bound = _seconds(timeout)
        deadline = time.monotonic() + bound
        with self._condition:
            while self._busy:
                if self._abandoned == self._write or not bound:
                    return False   # Still the write an earlier wait gave up on (stalled), or no wait asked.
                left = deadline - time.monotonic()
                if left <= 0:
                    self._abandoned = self._write   # The write holding the turn now.
                    self._condition.notify_all()    # Every other wait for it gives up too.
                    return False
                self._condition.wait(left)
            self._take()
            return True

    def release(self):
        with self._condition:
            self._busy = False
            self._condition.notify_all()


def within(timeout, *calls, name="AngeloCloseWrites"):
    """Run each of ``calls`` on its own helper thread, side by side; return within ``timeout`` seconds whether or
    not they ended. True when they all ended in time.

    The calls are store writes that take their turn (a close's), each given the whole bound: one store's stall
    never shortens another's wait. A call that outlasts the bound (a stalled disk) ends on its own, never writing
    anything older than the file holds. When no thread can start (the interpreter is shutting down) the calls run
    here, unbounded."""
    def run(call):
        try:
            call()
        except Exception:  # noqa: BLE001 - a close never stops on its state files.
            pass
    workers = []
    for call in calls:
        worker = threading.Thread(target=run, args=(call,), daemon=True, name=name)
        try:
            worker.start()
        except RuntimeError:
            run(call)
            continue
        workers.append(worker)
    deadline = time.monotonic() + _seconds(timeout)
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    return not any(worker.is_alive() for worker in workers)


# A close's bound, in all, for a folder's owed writes and any write under way (only a stalled disk reaches it).
CLOSE_SECONDS = 2.0

_TRACKED, _TRACKED_LOCK = [], threading.Lock()   # Every store's bounded flush (``track``), held weakly.


def track(flush):
    """Register a store's bound method ``flush(timeout)``: quit (``close_all``) calls it. The store is held weakly."""
    with _TRACKED_LOCK:
        _TRACKED[:] = [method for method in _TRACKED if method() is not None]
        _TRACKED.append(weakref.WeakMethod(flush))


def close_folder(store):
    """``Controller.close``: a state folder's owed receipts (``store``, its ``TelemetryStore``) and usage, written
    after any write under way, side by side within ``CLOSE_SECONDS``. Once it returns nothing writes into the folder
    until the next change (a stalled disk aside). Never raises."""
    from . import usage_meter
    bound, directory = CLOSE_SECONDS, store.directory
    return within(bound, lambda: store.flush(timeout=bound), lambda: usage_meter.drain(directory, timeout=bound))


def close_all(timeout):
    """Quit, after the writers that outlive chats ended (``codex_runtime.close_all``): every tracked store's owed
    writes, one bound for all. Files only: never Blender data."""
    deadline = time.monotonic() + _seconds(timeout)
    with _TRACKED_LOCK:
        flushes = [method for method in (reference() for reference in _TRACKED) if method is not None]

    def flush_all():
        for flush in flushes:
            flush(deadline - time.monotonic())
    return within(timeout, flush_all)
