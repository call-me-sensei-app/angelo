# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat mirror reads its chat's file off the lead's UI thread (design A′, C3 ``journal``).

Each ``journal`` frame makes the mirror read what its worker wrote (``worker_mirror_journal.MirrorStore``). The read's
cost follows the new record: a tool result of a 900-object edit is a record of about 200 KB, whose decode and checks
took about 3 ms of the UI thread on a Mac M4 and 17 ms on a Windows T4 VM under load, past the pump's 10 ms bound
(``test_blender_parallel_workers``, ``a_pump_never_waits_for_the_worker``). So the read runs on this mirror's reader
thread, and the UI thread only puts in the state it prepared (``worker_mirror_events.apply_read``):

* **Order.** The frames a pump drained after a journal frame wait behind its read (``behind``) and are applied after
  its state, in order, as if the read had run inline: the state never goes back over a newer display event.
* **One store, one reader.** Every read of the store holds ``MirrorStore.io``. A main-thread load (a hand-off, a
  notice saved once the file is the lead's) runs only when no read is waiting, and takes the same lock.
* **Short slices.** While a reader reads, Python's switch interval is 0.5 ms (``_quick_switches``), and the tail path
  also gives the GIL back about every millisecond (``MirrorStore.pause``), so the UI thread never waits 5 ms (which
  Windows stretches to its 15.6 ms timer tick) for the read's Python work, a whole read's included. One C call (a
  record's decode, a whole file's base) cannot be split: it is the UI thread's longest wait.
* **Newest wins.** The pump takes only the answer to its own newest question; a question asked while a read runs is
  read after it.

``bind(inline=True)`` reads in the asking thread, as ``worker_mirror_ops.bind`` runs lanes inline for host tests.
Standard library only and no ``bpy``.
"""

import sys
import threading
import time

IDLE_SECONDS = 30.0   # A reader with nothing to read ends; the next frame starts another.

_inline = False


def bind(inline=False):
    """Host tests: ``inline`` reads in the asking thread (the frames behind it then go on in the same pump)."""
    global _inline
    _inline = inline


def reset():
    bind()


def reader_of(mirror):
    found = mirror.__dict__.get("_journal_reader")
    if found is None:
        found = mirror.__dict__["_journal_reader"] = Reader(mirror.store._store)
    return found


def waiting(mirror):
    """Whether a read this mirror asked for has not been put in yet (its later frames wait behind it)."""
    return mirror.__dict__.get("_reading") is not None


def any_reading(controllers):
    """Whether any of ``controllers`` waits for a read (the UI timer then comes back within a few ms)."""
    return any(controller.__dict__.get("_reading") is not None for controller in controllers)


def ask(mirror, records, behind):
    """UI thread: read the file (``records``: the journal frame's count, or None for a whole read) while ``behind``
    waits for it."""
    mirror.__dict__["_reading"] = reader_of(mirror).ask(records)
    mirror.__dict__["_behind"] = list(behind)


def take(mirror):
    """UI thread: ``(state, error, behind)`` of the read asked last once it finished, else None (it still runs)."""
    number = mirror.__dict__.get("_reading")
    done = reader_of(mirror).done(number) if number is not None else None
    if done is None:
        return None
    mirror.__dict__["_reading"] = None
    return done[0], done[1], mirror.__dict__.pop("_behind", [])


class Reader:
    """One mirror's reads of its store, one at a time, on a thread of their own (module docstring)."""

    def __init__(self, store):
        self.store = store
        self._wake = threading.Condition()
        self._asked = None    # (number, records) of a read not started yet
        self._done = None     # (number, state, error) of the newest read finished
        self._count = 0
        self._running = False

    def ask(self, records):
        with self._wake:
            self._count += 1
            self._asked = number = self._count, records
            inline = _inline
            if not inline and not self._running:
                try:
                    threading.Thread(target=self._run, daemon=True, name="AngeloMirrorRead").start()
                except RuntimeError:   # No thread to be had: read here, as the pump did before (never stuck).
                    inline = True
                else:
                    self._running = True
            self._wake.notify()
        if inline:
            self._read_asked()
        return number[0]

    def done(self, number):
        with self._wake:
            done = self._done
        return (done[1], done[2]) if done is not None and done[0] == number else None

    def _run(self):
        while True:
            with self._wake:
                if self._asked is None:
                    self._wake.wait(IDLE_SECONDS)
                if self._asked is None:
                    self._running = False
                    return
            self._read_asked()

    def _read_asked(self):
        with self._wake:
            asked, self._asked = self._asked, None
        if asked is None:
            return
        number, records = asked
        store = self.store
        with store.io, _quick_switches():
            store.hint = records
            try:
                state, error = store._read()[0], None
            except Exception as failure:   # noqa: BLE001 - a read that raised is a failed read, tried again.
                state, error = None, failure
            error = error or getattr(store, "read_error", None)
        with self._wake:
            self._done = (number, state, error)


SWITCH_SECONDS = 0.0005   # The GIL's switch interval while any reader reads (Python's own is 5 ms).
_switches = threading.Lock()
_reading = [0, None]   # Reads under way, and the switch interval to put back after the last.


class _quick_switches:
    """While a reader reads, Python hands the GIL to a waiting thread every ``SWITCH_SECONDS`` instead of every 5 ms:
    the UI thread then waits at most that for any stretch of the read's Python work, wherever it is (a whole read's
    list check, a long row's text check, the task memory's clean). ``Slices`` give the GIL back on the tail path's
    loops as well. Nothing can split one C call (a record's decode): that call is the UI thread's longest wait."""

    def __enter__(self):
        if threading.current_thread() is threading.main_thread():
            return self   # An inline read: no other thread waits on this one.
        with _switches:
            if _reading[0] == 0:
                _reading[1] = sys.getswitchinterval()
                sys.setswitchinterval(min(_reading[1], SWITCH_SECONDS))
            _reading[0] += 1
        return self

    def __exit__(self, *exc):
        if threading.current_thread() is threading.main_thread():
            return
        with _switches:
            _reading[0] -= 1
            if _reading[0] == 0:
                sys.setswitchinterval(_reading[1])


class Slices:
    """``MirrorStore.pause``'s clock: off the UI thread, a short sleep gives the GIL back after each ``SLICE`` of work,
    so the UI thread takes it at once instead of after a switch interval."""

    SLICE = 0.001
    PAUSE = 0.0002

    def __init__(self):
        self._since = time.perf_counter()

    def pause(self):
        if threading.current_thread() is threading.main_thread():
            return
        now = time.perf_counter()
        if now - self._since >= self.SLICE:
            time.sleep(self.PAUSE)
            self._since = time.perf_counter()
