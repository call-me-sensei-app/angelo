# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a UI pump blocks: the main thread's time inside calls made to wait, not the pump's wall time.

"The pump never waits for the worker" is a claim about blocking, so it is measured as blocking. A pump's wall time
also holds frame application, GIL handoffs to the channel and reader threads and the OS taking the CPU away, which
on a Windows VM comes in 15.6 ms timer ticks: on the 1.0.9 cut 4 T4 a pump that waited for nothing took 11.6 ms
(its mirror's frames 11.3 ms, the consent tick 0.26 ms, no journal read), past a fixed 10 ms bound on wall time.

Inside ``watching()``, each ``measure()`` (one pump, main thread only) adds up the time spent in the primitives a
wait on a worker goes through, when the call is made to block:

* ``threading.Event.wait``, ``Condition.wait``, ``Semaphore.acquire``, ``Thread.join``, ``queue.Queue.get``/``put``
  (the channel's reply slots, the reader's wake-ups, the mirror's frame queue);
* socket receives, sends and accepts on a socket with a timeout other than 0, ``select.select``, ``os.read`` of a
  pipe, socket or device, ``os.waitpid`` without WNOHANG, ``subprocess.Popen.wait``/``communicate``, ``time.sleep``.

A call made not to block (``timeout=0``, ``block=False``, a non-blocking socket, a regular file's read) counts
nothing, whatever the machine takes from it. Nested waits (``Queue.get`` into ``Condition.wait``) count once.
Not seen: a raw ``_thread`` lock's ``acquire``/``with`` (a C type, never patched) and ``selectors``' own bound
``select``; a pump held there for a long worker answer meets the wall-time sanity bound instead (``wall_allowance``).

Proven on the 1.0.9 cut 4 app (macOS): with a stub that makes each pump ``Event.wait(0.02)`` for a worker signal,
both variants of ``test_blender_parallel_workers`` fail ``a_pump_never_waits_for_the_worker`` (30 ms blocked, wall
still inside its allowance); without it, no pump made a blocking call.
"""

import contextlib
import os
import queue
import select
import socket
import stat
import subprocess
import threading
import time

BLOCKED_LIMIT = 0.002   # Seconds a pump may spend in blocking calls: a wait for a worker's answer takes far longer.
WALL_LIMIT = 0.1   # A pump's wall time on the nominal machine (machine_clock), a sanity bound only.

_clock = time.perf_counter
_window = None   # The records of the open watching() window
_open = None   # The main thread's pump being measured
_depth = 0   # The main thread's waits in progress: only the outermost one counts


class Pump:
    """One measured pump: ``blocked`` seconds in ``calls`` blocking calls, the ``slowest`` one; the helper that
    measured it adds its ``wall`` seconds and whether its chat was ``busy``."""

    def __init__(self):
        self.blocked, self.calls, self.slowest, self.wall, self.busy = 0.0, 0, None, None, None

    def as_dict(self):
        return {"blocked_ms": round(self.blocked * 1000, 4), "calls": self.calls, "slowest": self.slowest,
                "wall_ms": None if self.wall is None else round(self.wall * 1000, 4), "busy": self.busy}


def _positive(timeout):
    return timeout is None or timeout > 0


def _socket_blocks(sock, *_args, **_keywords):
    return sock.gettimeout() != 0.0


def _fd_blocks(fd, *_args, **_keywords):
    try:   # A regular file's read never waits on another process; a pipe, socket or device may.
        mode = os.fstat(fd).st_mode
        if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
            return False
        return os.get_blocking(fd)
    except (OSError, AttributeError):
        return True


WAITS = {   # (owner, attribute): whether the call is made to block, from its own arguments
    (threading.Event, "wait"): lambda self, timeout=None: _positive(timeout),
    (threading.Condition, "wait"): lambda self, timeout=None: _positive(timeout),
    (threading.Semaphore, "acquire"): lambda self, blocking=True, timeout=None: blocking and _positive(timeout),
    (threading.Thread, "join"): lambda self, timeout=None: _positive(timeout),
    (queue.Queue, "get"): lambda self, block=True, timeout=None: block and _positive(timeout),
    (queue.Queue, "put"): lambda self, item, block=True, timeout=None: (block and self.maxsize > 0
                                                                        and _positive(timeout)),
    (subprocess.Popen, "wait"): lambda self, timeout=None: _positive(timeout),
    (subprocess.Popen, "communicate"): lambda self, input=None, timeout=None: True,
    (time, "sleep"): lambda seconds: seconds > 0,
    (select, "select"): lambda rlist, wlist, xlist, timeout=None: _positive(timeout),
    (os, "read"): _fd_blocks,
    (os, "waitpid"): lambda pid, options: not options & getattr(os, "WNOHANG", 0),
}
WAITS.update({(socket.socket, name): _socket_blocks for name in (
    "recv", "recv_into", "recvfrom", "recvfrom_into", "recvmsg", "recvmsg_into", "send", "sendall", "sendmsg",
    "accept") if hasattr(socket.socket, name)})


def _blocks(blocks, args, keywords):
    try:
        return bool(blocks(*args, **keywords))
    except Exception:   # Arguments the predicate does not know: counted as a wait (the call raises its own error).
        return True


def _wrap(label, original, blocks):
    def waiting(*args, **keywords):
        global _depth
        record = _open
        if record is None or _depth or threading.get_ident() != _main or not _blocks(blocks, args, keywords):
            return original(*args, **keywords)
        _depth += 1
        started = _clock()
        try:
            return original(*args, **keywords)
        finally:
            seconds = _clock() - started
            _depth -= 1
            record.blocked += seconds
            record.calls += 1
            if record.slowest is None or seconds * 1000 > record.slowest[1]:
                record.slowest = (label, round(seconds * 1000, 4))
    waiting.__wrapped__ = original
    return waiting


_main = threading.main_thread().ident   # The UI pump's thread


@contextlib.contextmanager
def watching():
    """Patches the primitives for the window and yields its list of ``Pump`` records, one per ``measure``."""
    global _window
    if _window is not None:
        raise RuntimeError("pump_blocking.watching() is already open")
    saved = []
    try:
        for (owner, name), blocks in WAITS.items():
            had = name in vars(owner)
            original = getattr(owner, name)
            saved.append((owner, name, had, original))
            label = "%s.%s" % (getattr(owner, "__name__", owner), name)
            setattr(owner, name, _wrap(label, original, blocks))
        _window = []
        yield _window
    finally:
        _window = None
        for owner, name, had, original in reversed(saved):
            if had:
                setattr(owner, name, original)
            else:
                delattr(owner, name)


@contextlib.contextmanager
def measure():
    """One pump: yields its ``Pump`` record, kept in the open window (if any). A pump inside a pump, or one off the
    main thread, is not measured on its own."""
    global _open
    record = Pump()
    if _window is None or _open is not None or threading.get_ident() != _main:
        yield record
        return
    _open = record
    try:
        yield record
    finally:
        _open = None
        _window.append(record)


def wall_allowance(limit=WALL_LIMIT):
    """``limit`` on this machine: grown by its reference pass (machine_clock), never shrunk."""
    import machine_clock
    return machine_clock.allowance(limit)


def summary(records):
    """The window's reading: the most a pump was blocked and the longest one's wall time, beside the slowest
    blocking call and the pumps that made any blocking call at all."""
    blocked = max(records, key=lambda record: record.blocked, default=None)
    longest = max(records, key=lambda record: record.wall or 0, default=None)
    return {"pumps": len(records), "max_blocked_ms": blocked.blocked * 1000 if blocked else 0.0,
            "most_blocked_pump": blocked.as_dict() if blocked else None,
            "max_wall_ms": (longest.wall or 0) * 1000 if longest else 0.0,
            "pumps_with_blocking_calls": sum(1 for record in records if record.calls),
            "blocked_limit_ms": BLOCKED_LIMIT * 1000}


def reading(records, busy=None):
    """A window's verdict over its pumps (those whose chat was ``busy``, when given): ``passed`` when it measured
    pumps, none spent ``BLOCKED_LIMIT`` in blocking calls, and none took past the machine's ``WALL_LIMIT``."""
    records = [record for record in records if busy is None or record.busy == busy]
    allowance = wall_allowance()
    result = summary(records)
    result.update(wall_allowance_ms=round(allowance * 1000, 3),
                  never_blocked=all(record.blocked < BLOCKED_LIMIT for record in records),
                  within_wall=all((record.wall or 0) < allowance for record in records))
    result["passed"] = bool(records) and result["never_blocked"] and result["within_wall"]
    return result
