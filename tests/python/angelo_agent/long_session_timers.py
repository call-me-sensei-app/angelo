# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host timers for ``long_session_replay.py`` (``ANGELO_REPLAY_HOST_TIMERS=1``): the session-length costs.

Wraps, in this process only, the functions whose cost grew with a long session
(doc/angelo/performance.rst, "Session-length costs outside the checks") and
sums their time per bucket, so the replay can record each edit's share:

* ``journal_main_ms``: conversation journal saves (and compactions) on the main thread;
* ``admission_ms``: journal saves on any other thread (a provider thread's admission save, under the
  controller lock);
* ``ledger_ms``: whole mutation-ID ledger validations (``clean_invocations``) on any thread;
* ``inventory_ms``: snapshot inventories on the snapshot worker;
* ``maintain_ms``: the worker's whole maintenance task (inventories, store accounting, garbage
  collection, compaction).

Each bucket also has ``*_minstr``, the millions of instructions the calling
thread retired inside it (``perf_counters.read_thread_counts``, macOS): wall
times on the worker include waits for the GIL and vary with load, while its
instructions are the work it holds the GIL for. Wrappers are installed on
whichever of these names the build defines, so the same replay times the code
before and after a change.
"""

import collections
import threading
import time

import perf_counters

BUCKETS = ("journal_main", "admission", "ledger", "inventory", "maintain")

_totals = collections.defaultdict(float)
_lock = threading.Lock()
_installed = []


def _main():
    return threading.current_thread() is threading.main_thread()


def _wrap(owner, name, buckets):
    original = getattr(owner, name, None)
    if original is None or getattr(original, "_long_session_timer", False):
        return None

    def wrapper(*args, **kwargs):
        wall, counts = time.perf_counter(), perf_counters.read_thread_counts()
        try:
            return original(*args, **kwargs)
        finally:
            elapsed, after = (time.perf_counter() - wall) * 1000, perf_counters.read_thread_counts()
            retired = (after["instructions"] - counts["instructions"]) / 1e6 if counts and after else 0.0
            names = buckets()
            if names:
                with _lock:
                    for bucket in names:
                        _totals[bucket + "_ms"] += elapsed
                        _totals[bucket + "_minstr"] += retired

    wrapper._long_session_timer = True
    wrapper.__wrapped__ = original
    setattr(owner, name, wrapper)
    _installed.append((owner, name))
    return wrapper


def install():
    """Install every wrapper this build supports; returns the wrapped names."""
    from angelo_agent import checkpoint_inventory, checkpoints, sessions, snapshot_maintenance, snapshot_worker
    journal = lambda: ("journal_main",) if _main() else ("admission",)  # noqa: E731
    worker = lambda name: (lambda: () if _main() else (name,))  # noqa: E731
    for name in ("save", "compact"):
        _wrap(sessions.SessionStore, name, journal)
    _wrap(sessions, "clean_invocations", lambda: ("ledger",))
    shared = _wrap(checkpoint_inventory, "inventory", worker("inventory"))
    for module in (checkpoints, snapshot_maintenance):
        if shared is not None and hasattr(module, "inventory"):
            setattr(module, "inventory", shared)  # One wrapper: a call through either name counts once.
    _wrap(snapshot_worker.SnapshotWorker, "_maintain", worker("maintain"))
    try:
        from angelo_agent import session_journal
    except ImportError:  # Builds before the append-only journal.
        session_journal = None
    if session_journal is not None:
        encode, rewrite = session_journal.encode, session_journal.rewrite

        def counted_rewrite(path, data):  # Whole-file rewrites: the first save, compactions, recoveries.
            with _lock:
                _totals["journal_rewrites"] += 1
            return rewrite(path, data)
        session_journal.rewrite = counted_rewrite

        def counted(value):  # Rows a save encodes (the request's, touched and scrubbed ones; all on a rewrite).
            if isinstance(value, dict) and "role" in value:
                with _lock:
                    _totals["journal_rows"] += 1
            return encode(value)
        session_journal.encode = counted
    return ["%s.%s" % (getattr(owner, "__name__", owner), name) for owner, name in _installed]


def snapshot():
    with _lock:
        return dict(_totals)


def since(previous):
    """Each bucket's time and instructions since ``previous`` (a ``snapshot()``), rounded to 0.01."""
    current = snapshot()
    result = {bucket + unit: round(current.get(bucket + unit, 0.0) - previous.get(bucket + unit, 0.0), 2)
              for bucket in BUCKETS for unit in ("_ms", "_minstr")}
    for count in ("journal_rows", "journal_rewrites"):
        result[count] = int(current.get(count, 0) - previous.get(count, 0))
    return result, current
