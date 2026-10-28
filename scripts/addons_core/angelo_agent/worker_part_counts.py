# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How many objects a worker's saved part file holds, for a merge's precondition and status, without the window ever
waiting on a worker (design A′).

The count is read by name (``bpy.data.libraries.load``: nothing is loaded) once per version of the file, keyed by
``(path, mtime_ns, size)``. A live worker's file is read only under its lease (``part.lease``, C10), and asking for
one could wait ``READ_LEASE_WAIT`` on a wedged worker, so here the lease is asked on a thread of its own and the read
runs on the next UI pump once it is granted (``pump``), then the lease is released off the main thread. Until then
the count is ``UNKNOWN``: the precondition refuses the part as not ready yet (fail closed, WRITER_BUSY) and its tile
says its part is being checked. What "mergeable" means is unchanged: a part whose file holds no objects is refused,
and one that cannot be read here (no Blender, an unreadable file) is left to the merge's own read.

A live worker also reports its count with each save (``saved_facts``, in ``part_saved``), so a merge's status
knows it the moment the save is announced, with no lease and no refusal: the lead trusts it only for the exact
file version it names (``note``: device, inode, mtime and size, matched against the lead's own ``os.stat`` of the
path the reporting worker serves), and only a count of at least one, which can only make a part mergeable sooner;
the merge itself reads the file under its gate's lease and refuses one that holds no objects. A missing or
mismatched report, or a zero, takes the lease path above. Reason: every first merge after a hosted save was
refused once as "still being checked", costing the lead's model a round (native r2).

A file no live worker serves, and a merge's own read under the lease its gate holds (``worker_merge_gate.leased``),
are read at once, as before. A refused or timed-out lease is asked again ``RETRY_SECONDS`` later; a granted one is read
under only while ``worker_merge_gate.FRESH`` of its granted time is left (a pump held up past that releases it unread,
and the next ``count`` asks again). Every lease
granted is released exactly once: by the pump after its read, or at once when its ask was dropped first (replaced
by a newer version's, or ``reset``), so no lease ever waits out its bound and stalls the worker's next save.

``bpy`` only on the main thread (``count``, ``pump``); the lease threads never touch it.
"""

import os
import threading
import time

UNKNOWN = "unknown"     # The count of a live worker's current file before its lease was granted.
RETRY_SECONDS = 1.0     # A lease refused (or never answered) is asked again this long after.
PUMP_SECONDS = 0.25     # How often the pump looks at the finished workers' parts.
LIMIT = 256             # File versions whose counts are kept.

_counts = {}            # (path, mtime_ns, size) -> its object count
_asks = {}              # path -> _Ask
_reported = {}          # id(worker) -> (worker, identity, objects): its newest save's report (``note``)
_lock = threading.Lock()
_last_pump = [0.0]


class _Ask:
    """One lease asked for ``path``'s current version: ``asking`` → ``granted`` (with its lease, None when no worker
    serves it any more) or ``refused`` (asked again at ``retry``)."""

    def __init__(self, key):
        self.key, self.state, self.lease, self.retry = key, "asking", None, 0.0
        self.until = None   # A granted lease's last moment to read under it (``_lease``).


def _spawn(function):
    threading.Thread(target=function, daemon=True, name="AngeloPartCount").start()


def _key(path, stat=None):
    stat = os.stat(path) if stat is None else stat
    return str(path), stat.st_mtime_ns, stat.st_size


def _identity(stat):
    """One version of one file: a save writes a new file (a new inode), so a report never matches a later save."""
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def saved_facts(bpy, path):
    """Worker side, on its main thread right after it saved ``path`` (``worker_chat_part.save_now``): what a merge's
    status reads of that version, ``{"objects", "identity"}``, or None when it cannot say. ``objects`` counts what
    the save wrote, as the lead's by-name listing counts them: this file's own objects that have a user (a save
    skips one with none; a linked one is the library's). The worker cannot list its own open file."""
    try:
        before = _identity(os.stat(path))
        objects = sum(1 for obj in bpy.data.objects if obj.library is None and obj.users > 0)
        if _identity(os.stat(path)) != before:
            return None
    except Exception:   # noqa: BLE001 - nothing reported: the lead takes the lease path.
        return None
    return {"objects": objects, "identity": list(before)}


def note(worker, facts):
    """Lead side, the channel's reader thread: ``worker``'s report of its newest save (``part_saved``'s ``part``).
    Kept only while well formed and at least one object; trusted only by ``count`` for the version it names."""
    identity = facts.get("identity") if isinstance(facts, dict) else None
    objects = facts.get("objects") if isinstance(facts, dict) else None
    trusted = (type(objects) is int and objects >= 1 and isinstance(identity, list) and len(identity) == 4
               and all(type(value) is int for value in identity))
    with _lock:
        if trusted:
            _reported[id(worker)] = (worker, tuple(identity), objects)
        else:
            _reported.pop(id(worker), None)   # An older report never stands for this save.


def _reported_count(worker, stat):
    """The count ``worker`` reported for exactly this version of the file it serves, else None."""
    with _lock:
        entry = _reported.get(id(worker))
    if entry is None or entry[0] is not worker or entry[1] != _identity(stat):
        return None
    return entry[2]


def count(path):
    """The object count of ``path``'s current version, ``UNKNOWN`` while a live worker's lease is being asked (never
    waited for here), or None when it cannot be read here."""
    try:
        stat = os.stat(path)
        key = _key(path, stat)
        import bpy  # noqa: F401 - only where Blender reads the file.
    except (OSError, ImportError):
        return None
    if key in _counts:
        return _counts[key]
    from . import worker_chat_lifecycle as lifecycle, worker_merge_gate
    owner = None if not lifecycle.enabled() or worker_merge_gate.leased(path) else lifecycle._serving(path)
    if owner is None:
        return _read(key)   # Nothing to wait on: no worker saves it now, or the merge's gate holds its lease.
    reported = _reported_count(owner, stat)
    if reported is not None:   # Its worker's own report of this very version (``note``): no lease to wait for.
        _keep(key, reported)
        return reported
    dropped = None
    with _lock:
        ask = _asks.get(key[0])
        if ask is not None and ask.state == "refused" and time.monotonic() >= ask.retry:
            ask = None
        if ask is None or (ask.key != key and ask.state != "asking"):
            dropped = _asks.get(key[0])
            ask = _asks[key[0]] = _Ask(key)
            _spawn(lambda: _lease(ask))
    if dropped is not None:
        _release(dropped)   # Granted for a version the worker has since saved over: never read, released now.
    return UNKNOWN


def checking(path):
    """Whether ``path``'s count is being asked for (its tile says so)."""
    try:
        key = _key(path)
    except OSError:
        return False
    with _lock:
        ask = _asks.get(key[0])
    return key not in _counts and ask is not None


def _lease(ask):
    """A lease thread: the worker's grant, never waited for by the window."""
    from . import worker_chat_lifecycle as lifecycle
    try:
        lease = lifecycle._lease(ask.key[0], lifecycle.READ_LEASE_WAIT)
    except Exception as error:   # noqa: BLE001 - not granted: not mergeable yet, asked again later.
        print("Angelo: a worker's part could not be leased to count it:", type(error).__name__)
        with _lock:
            ask.state, ask.retry = "refused", time.monotonic() + RETRY_SECONDS
        return
    from .worker_merge_gate import FRESH   # Read only while this share of its time is left, as a merge's reads.
    until = time.monotonic() + lease[2] * (1 - FRESH) if lease is not None else None
    with _lock:
        ask.state, ask.lease, ask.until = "granted", lease, until
        current = _asks.get(ask.key[0]) is ask
    if not current:
        _release(ask)   # Dropped while it was being asked (``reset``): nobody reads under it.


def _release(ask):
    """Release ``ask``'s lease once, off the main thread (a granted ask only)."""
    with _lock:
        lease, ask.lease = (ask.lease, None) if ask.state == "granted" else (None, ask.lease)
        ask.state = "released" if lease is not None else ask.state
    if lease is not None:
        from . import worker_chat_lifecycle as lifecycle
        _spawn(lambda: lifecycle._unlease(lease))


def pump(now=None):
    """UI timer: read each granted file under its lease and release it; keep the finished workers' counts current,
    so a merge finds them known."""
    now = time.monotonic() if now is None else now
    with _lock:
        granted = [(path, ask) for path, ask in _asks.items() if ask.state == "granted"]
        for path, _ask in granted:
            del _asks[path]
    for path, ask in granted:
        try:   # Under its lease the file cannot change; past it, it is released unread and asked again (audit #5).
            if ask.lease is None or (now <= ask.until and _key(path) == ask.key):
                _read(_key(path))
        except OSError:
            pass
        finally:
            _release(ask)
    if now - _last_pump[0] < PUMP_SECONDS:
        return bool(granted)
    _last_pump[0] = now
    from . import worker_sessions
    for session, part in list(worker_sessions._parts.items()):
        if os.path.isfile(part[2]) and worker_sessions.finished(session):
            count(part[2])
    return bool(granted)


def _read(key):
    import bpy
    try:
        with bpy.data.libraries.load(key[0]) as (source, _target):
            found = len(source.objects)
    except Exception:   # noqa: BLE001 - an unreadable file is the merge's own refusal to make.
        return None
    _keep(key, found)
    return found


def _keep(key, found):
    if len(_counts) >= LIMIT:
        _counts.clear()
    _counts[key] = found


def reset():
    with _lock:
        _counts.clear()
        _reported.clear()
        dropped = list(_asks.values())
        _asks.clear()
    for ask in dropped:
        _release(ask)
    _last_pump[0] = 0.0
