# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Let the file system's clock pass a file's stamps before its stat vouches for what comes after it.

A later write in the step of a file's last stamp carries the same times, so a stat taken then cannot tell a same-size
write after it from no write: neither a witness ("unchanged since I read it") nor a cache keyed on the stat. Once the
clock has left that step, every later change carries another time. Each volume's file system decides the step:

* Windows (``tests/python/angelo_agent/probe_volume_stamps.py``, Windows Server 2022, CPython 3.13, 200 writes each,
  2026-11-04): NTFS stamps ChangeTime and LastWriteTime with the system clock's reading at the write (it ticks:
  15.625 ms by default, down to 0.5 ms), never ahead of it, and closing a handle moves neither; ``st_ctime_ns``
  (``os.fstat``) is the ChangeTime. FAT32 and exFAT keep no ChangeTime (0), keep LastWriteTime in 2 s steps rounded
  up (ahead of the clock by up to 2 s), and stamp it again at a handle's close (exFAT every time, FAT32 at some);
  their 10 ms creation time is compared by no witness. ``st_dev``'s low 32 bits are ``GetVolumeInformationW``'s
  serial; the volumes are listed on a thread (``volume_step``: 2 s on FAT and exFAT, 0 on NTFS, ReFS and the rest).
* Elsewhere the mount table (``angelo_change_clock_volumes``, measured on macOS): APFS stamps fine-grained times, so
  every call returns at once without reading a clock; HFS+ keeps 1 s steps, FAT 2 s, exFAT 10 ms and Linux's disk
  file systems a 16 ms tick, rounded down, and closing restamps none; a share or any other type is the coarsest.

Each time a stat carries is judged on its own, never a maximum of them, against its volume's step (``volume``). A
write at ``now`` on a known Windows volume carries a value in ``[now, now + step]`` (``below`` 0); off Windows in
``[now - step, now]`` (``below`` the step; an unknown type 2 s either way). Where nothing says (a device the Windows
listing has not answered, a Windows share, a stat without a device) a time with no sub-millisecond part may have been
kept in the coarsest step its trailing zeros allow (``_value_step``, as ``coarse_times`` asks too), rounded either
way, so ``below`` is that step. A share's server stamps with its own clock. A time in ``[now - below, now +
max(TICK, step)]`` waits (``now - below`` itself is past where below > 0):

* the modification time (always judged, since every write stamps it) can be set by any program, so one beyond that
  window either way is past (a time set ahead on purpose, on a coarse volume, is outside what this sees);
* the change time, with ``change=True`` (for a witness that compares it), is stamped and never set: one before the
  window is past, one beyond it means the clock was set back (``AHEAD``);
* a ``st_ctime`` that may be the creation time (a path's ``os.stat`` on Windows' CPython 3.13 reports it, equal to
  ``st_birthtime``; every Windows stat before 3.13 does), or that is the modification time (FAT and exFAT off
  Windows: ``Volume.changes``), is judged as a modification time. A change time of 0 (none kept) is no time.

The boundary and the limit, together: a settle guarantees that every write made after it returns, by any writer,
stamps another change time; a write inside the tick of the witness's own stamp, made before that, carries the same
stamp and no stat can see it. No wait can order a writer whose timing is independent of the caller's, so a site
puts its settle where the next party learns of the file (Sculpt publication: both components at once, as
``publish``'s last step, so the time its record write takes counts toward the wait, which ends before it returns).
A witness taken on a handle still open for writing (``writing=True``: the capture's own outputs, a Restore's working
copies) is never trusted where closing can restamp (Windows' FAT and exFAT; where nothing says, a share or an unknown
type, a modification time in 2 s steps): ``ClosingRestamps`` at once.

The calls, by what the stat is for:

* ``settle(witness)`` / ``settle_all(witnesses)`` wait until every time is past, before a witness binds bytes read
  after it (a hash, a copy, an excerpt), or binds an approval, a persisted key or a later check to the file as it is
  (``doc/angelo/checkpoint_policy.rst``, "Differences and limits"). It sleeps (``STEP_S``, or until a coarse step
  ends), capped at ``LIMIT_S`` beyond the longest step. A change time beyond the window raises ``Unsettled`` (a
  ``ValueError``: every caller treats it as "the file changed") at once, as does a wait for a step longer than a
  tick on Blender's main thread with windows (``_freezes_ui``), saying to read the file again in a moment; others
  wait it out, as does the main thread inside ``awaited()`` (a step the person already waits for: a snapshot
  capture, a Restore). Past times cost one clock read; nothing waits for the Windows volume listing.
* ``settled(witness)`` asks without waiting, for a cache: a value read under a time still in its step is used once
  and never kept (``cache_key``). A change time ahead of the clock counts as past here: every write until the clock
  gets there stamps an earlier time, so a cache never reads such a file again and again.
* ``unique()`` is a key no other ever equals, in any process (a random UUID): what stands for a stat nothing vouches
  for, persisted or sent to another process too.
* ``now_s()`` is the present on the clock files are stamped with, in seconds since 1970 (Windows:
  ``GetSystemTimeAsFileTime``, as NTFS stamps; CPython 3.13's ``time.time`` is past a stamp in its own tick).
* ``prime()`` starts the Windows volume listing on a thread, once per process; a device first seen starts one too
  (off Windows it does nothing: a device first seen lists the mounts in the caller's thread, which never blocks).

``waits``/``waited_s`` count the settles that waited, ``listings``/``listed_s`` the volume listings. ``settle`` keeps
its own ``time.sleep`` and ``time.monotonic``, so a test that patches them elsewhere never spins it.

This module imports nothing of the agent: ``angelo_render_device`` uses it too, and the agent reaches it as
``angelo_agent.change_clock``.
"""

import contextlib
import os
import sys
import threading
import time
import types
import uuid

try:
    import angelo_change_clock_volumes as _posix
except ImportError:   # Loaded by its path (a checkout's tests, export_snapshot's child): the file beside this one.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("angelo_change_clock_volumes", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "angelo_change_clock_volumes.py"))
    _posix = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
    _spec.loader.exec_module(_posix)

COARSE = os.name == "nt"   # Windows: every volume ticks or steps; listed on a thread. Elsewhere ``_posix`` judges.
LIMIT_S = 1.0              # The cap of a wait beyond the longest step it waits for. Fail closed past it.
STEP_S = 0.0005            # One sleep between clock reads: the finest tick, never a busy spin.
TICK = 160_000             # 16 ms in FILETIME steps: more than the default 15.625 ms tick.
RECHECK_S = 60.0           # A device no listing found (a share) is looked for again when next seen after this long.
VOLUME_TIMEOUT_S = 2.0     # A volume slower to answer (a sleeping disk, a network VHD) is left out of a listing.
# CPython before 3.13 reports the creation time as a Windows ``st_ctime``; a descriptor witness carries the real
# ChangeTime as ``st_change_time`` there (``checkpoint_filesystem.descriptor_witness``).
_LEGACY = COARSE and sys.version_info[:2] < (3, 13)
_EPOCH_AS_FILETIME = 116444736000000000   # 1970-01-01 in 100-nanosecond steps since 1601-01-01.
SETTLED, WAIT, AHEAD = "settled", "wait", "ahead"
# Windows' LastWriteTime step in FILETIME units, as measured (docstring); each stepped one stamps at handle close.
FILE_SYSTEM_STEPS = {"FAT": 20_000_000, "FAT32": 20_000_000, "EXFAT": 20_000_000}
Volume, VALUE_STEPS, _value_step = _posix.Volume, _posix.VALUE_STEPS, _posix.value_step
_WINDOWS = {0: Volume(0, 0, False, False, True), 20_000_000: Volume(20_000_000, 0, True, False, True)}
_clock = None
_sleep, _monotonic = time.sleep, time.monotonic   # Its own: a test patching time.sleep elsewhere never spins here.
_counted = threading.Lock()
# 32-bit volume serial -> (its step, or None for no local volume; when it was decided). Replaced whole under
# ``_listing_lock`` (copy on write), so ``volume_step`` reads it lock-free; no lock is held across a listing.
_volumes = {}
_listing_lock = threading.Lock()
_listing = None            # The running listing thread (set only once it started), or None.
_wanted = set()            # Serials asked for since the running listing took its own (``_listing_lock``).
_slow = set()              # Volumes whose last query still runs on a dropped thread (``_listing_lock``).
_answers = {}              # Volume name -> (32-bit serial, step), from earlier listings (the listing thread's own).
_listed_once = False       # A listing finished in this process (``prime`` then lists no more).
waits, waited_s = 0, 0.0   # Settles that waited for the clock (any thread), and for how long: latency measurements.
listings, listed_s = 0, 0.0   # Windows volume listings finished, and the time they took (on their own thread).
_awaited = threading.local()   # ``depth``: ``awaited()`` blocks this thread is inside.


class Unsettled(ValueError):
    """A file's change time is ahead of the system clock (the clock was set back), its time stayed in its step past
    the cap, or a coarse step would be waited out on a UI's main thread: nothing vouches for the file."""


class ClockAhead(Unsettled):
    """A change time beyond what a write now could carry: this clock, or a share's server's, is set wrong."""


class ClosingRestamps(Unsettled):
    """The stat was taken on an open writer where closing it restamps: a lasting property of the file's volume."""


def system_time():
    """GetSystemTimeAsFileTime: the clock NTFS stamps files with, in 100 ns steps since 1601 (elsewhere
    ``time.time_ns`` in those steps)."""
    global _clock
    if os.name != "nt":
        return time.time_ns() // 100 + _EPOCH_AS_FILETIME
    import ctypes
    if _clock is None:
        function = ctypes.WinDLL("kernel32").GetSystemTimeAsFileTime
        function.restype, function.argtypes = None, [ctypes.POINTER(ctypes.c_ulonglong)]
        _clock = function
    value = ctypes.c_ulonglong()
    _clock(ctypes.byref(value))
    return value.value


def now_s():
    """The present on the clock files are stamped with, in seconds since 1970: ``GetSystemTimeAsFileTime`` on Windows
    (a file written in this tick carries exactly it; ``time.time`` there is already past it), else ``time.time``."""
    return (system_time() - _EPOCH_AS_FILETIME) / 1e7 if COARSE else time.time()


_volume_names = _posix.windows_volume_names   # Windows' local volume names (a name a test can stand in for).


def _volume_information(name):
    """``(32-bit serial, step)`` of one volume (``GetVolumeInformationW``), or None when it cannot answer."""
    found = _posix.windows_volume_information(name)
    return None if found is None else (found[0], file_system_step(found[1]))


def file_system_step(name):
    """The step a file system keeps LastWriteTime in (FILETIME units; 0: the clock's own reading), by its name."""
    return FILE_SYSTEM_STEPS.get(str(name).upper(), 0)


def _query(name, answer):
    try:
        found = _volume_information(name)
        if found is not None:
            answer.append(found)
    except OSError:
        pass
    finally:
        with _listing_lock:
            _slow.discard(name)


def _volume_steps():
    """``{32-bit volume serial: step}`` of every local volume. Only a volume not answered before is asked (one new
    since the last listing, or one that did not answer): a listing whose volume names are unchanged asks nothing.
    Those asked are asked at once, each on a thread of its own, all within one ``VOLUME_TIMEOUT_S``, so slow ones (a
    sleeping disk, a network-backed VHD) never add up or hold up the others; a slow one's answer is dropped, and it
    is not asked again while that query still runs. Two volumes sharing a serial keep the larger step (a wrong 0
    would let a coarse volume's write pass; a wrong step only waits)."""
    names = _volume_names()
    workers = []
    for name in names:
        with _listing_lock:
            if name in _answers or name in _slow:
                continue
            _slow.add(name)
        answer = []
        worker = threading.Thread(target=_query, args=(name, answer), name="AngeloVolumeQuery", daemon=True)
        try:   # As the add-on's thread_start.started: nothing is left recorded for a query that never started.
            worker.start()
        except RuntimeError:
            with _listing_lock:
                _slow.discard(name)
            continue
        workers.append((name, worker, answer))
    deadline = _monotonic() + VOLUME_TIMEOUT_S
    for _name, worker, _answer in workers:
        worker.join(max(0.0, deadline - _monotonic()))
    found = {}
    with _listing_lock:
        for name, _worker, answer in workers:
            if answer:
                _answers[name] = answer[0]
        for name in [name for name in _answers if name not in names]:
            del _answers[name]   # A volume gone (unplugged): its next appearance is asked again.
        for name in names:
            if name in _answers:
                serial, step = _answers[name]
                found[serial] = max(found.get(serial, 0), step)
    return found


def _list():
    """The listing thread: lists the volumes, then again while another serial was asked for meanwhile."""
    global _volumes, _listing, _listed_once, listings, listed_s
    while True:
        with _listing_lock:
            wanted = set(_wanted)
            _wanted.clear()
            if any(serial not in _volumes for serial in wanted):
                # A serial never seen (a new share, a volume formatted again under its old name) asks every volume
                # once more; a re-check of one already decided asks only volumes whose names are new.
                _answers.clear()
        started = _monotonic()
        try:
            found = _volume_steps()
        except Exception:   # noqa: BLE001 - a listing that fails decides nothing; the fallback stands.
            found = {}
        now = _monotonic()
        with _listing_lock:
            volumes = dict(_volumes)
            for serial, step in found.items():
                volumes[serial] = (max(step, (volumes.get(serial) or (0, 0))[0] or 0), now)
            for serial in wanted - set(found):
                volumes[serial] = (None, now)   # No local volume has it (a share), or its volume answered too slowly.
            _volumes = volumes
            _listed_once = True
            listings += 1
            listed_s += now - started
            if not _wanted:
                _listing = None
                return


def _request(serial):
    """Ask the listing thread about ``serial`` (None: list only), starting it unless one runs. Never waits; a thread
    that cannot start (no thread to give, the interpreter shutting down) leaves nothing recorded, so the fallback
    answers and the next sight asks again."""
    global _listing
    with _listing_lock:
        if serial is not None:
            _wanted.add(serial)
        if _listing is not None:
            return
        thread = threading.Thread(target=_list, name="AngeloVolumeSteps", daemon=True)
        try:   # Started before it is recorded (the add-on's thread_start convention); it waits for this lock.
            thread.start()
        except RuntimeError:
            return
        _listing = thread


def prime():
    """Start the volume listing in the background (at import and at registration), so a first settle meets a known
    volume; nothing waits for it. Once a listing has finished in this process it does nothing: a device first seen
    later asks for itself."""
    if COARSE and not _listed_once:
        _request(None)


def listed(timeout=None):
    """Wait for a running listing to end (tests, the bench, never a settle); whether none runs any more."""
    thread = _listing
    try:
        thread is None or thread.join(timeout)
    except RuntimeError:   # Not started: nothing runs.
        pass
    return _listing is None


def volume_step(device):
    """The step, in FILETIME units, in which the volume a stat's ``st_dev`` names keeps LastWriteTime: 2 s (FAT32
    and exFAT, measured), 0 (NTFS, ReFS, any other local file system); None off Windows, without a device, for a
    device the background listing has not answered yet, or one no local volume has (a share, asked again
    ``RECHECK_S`` after). CPython's ``st_dev`` is the 64-bit volume serial where the volume has one; its low 32 bits
    are the serial ``GetVolumeInformationW`` reports (measured on NTFS, FAT32 and exFAT). A known device costs a dict
    lookup, no lock. Never raises: whatever fails answers None, and the fallback applies."""
    if not COARSE or device is None:
        return None
    try:
        serial = int(device) & 0xFFFFFFFF
        known = _volumes.get(serial)
        if known is None:
            _request(serial)
            return None
        step, when = known
        if step is None and _monotonic() - when > RECHECK_S:
            _request(serial)
        return step
    except Exception:   # noqa: BLE001 - never into a settle, a draw or a timer: the fallback answers.
        return None


def volume(witness):
    """How a stat's volume keeps times (``Volume``), or None where nothing says (no device; on Windows one not listed
    yet or a share). Off Windows the mount table answers, checked against the witness's change time."""
    device = getattr(witness, "st_dev", None)
    if COARSE:
        step = volume_step(device)
        return None if step is None else _WINDOWS.get(step) or Volume(step, 0, step > 0, False, True)
    stamp = getattr(witness, "st_ctime_ns", None)
    return _posix.volume(device, witness.st_mtime_ns if stamp is None else stamp)


def coarse_times(device, time_ns):
    """Whether a file's times can be shared by two writes far apart (``checkpoint_seal``): its volume's step where
    the volume is known, else a time with no sub-millisecond part (a Windows share, a volume not listed yet)."""
    known = volume(types.SimpleNamespace(st_dev=device, st_mtime_ns=time_ns))
    return known.step > 0 if known is not None else _value_step(time_ns) > 0


def _filetime(time_ns):
    return int(time_ns) // 100 + _EPOCH_AS_FILETIME


def times(witness, change=True):
    """``[(FILETIME, kind, step, below)]`` of the times to judge: ``kind`` ``"exact"`` (a change time), ``"loose"``
    (one a program can set, or that may be the creation time); ``step`` and ``below`` (how far under the clock's
    reading a write can stamp) its volume's (``volume``), or where nothing says, each time's own (``_value_step``)."""
    known = volume(witness)

    def steps(time_ns):
        if known is not None:
            return known.step, known.below
        guess = _value_step(time_ns)
        return guess, guess
    mtime = witness.st_mtime_ns
    found = [(_filetime(mtime), "loose") + steps(mtime)]
    if not change:
        return found
    exact = getattr(witness, "st_change_time", None)
    if exact is None:
        ctime = witness.st_ctime_ns
        if _LEGACY or ctime == getattr(witness, "st_birthtime_ns", None) or not getattr(known, "changes", True):
            found.append((_filetime(ctime), "loose") + steps(ctime))
            return found
        exact, exact_ns = _filetime(ctime), ctime
    else:
        exact_ns = (exact - _EPOCH_AS_FILETIME) * 100
    if exact > 0:   # 0: a file system that keeps no change time (measured on FAT32 and exFAT).
        found.append((exact, "exact") + steps(exact_ns))
    return found


def verdict(witness, now, change=True):
    """``(SETTLED, 0)``, ``(WAIT, seconds the longest pending time may still take)`` or ``(AHEAD, 0)`` at ``now``."""
    state, longest = SETTLED, 0.0
    for value, kind, step, below in times(witness, change):
        if value > now + max(TICK, step):   # Beyond what a write now could carry: set ahead, or the clock went back.
            if kind == "exact":
                return AHEAD, 0.0
        elif value > now - below or value == now:   # A write now could still carry it.
            state, longest = WAIT, max(longest, STEP_S, (value + below - now + 1) / 1e7)
    return state, longest


def _closing_restamps(witness):
    """Whether closing a writer's handle can move this file's times: Windows' FAT32 and exFAT stamp at close (every
    stepped file system measured there), NTFS and every macOS one measured never. Where nothing says (an unknown type,
    a share), as ``times``: a time kept in 2 s steps may be FAT's; a restamp elsewhere fails a later check closed."""
    known = volume(witness)
    if known is None or known.restamps is None:
        return _value_step(witness.st_mtime_ns) >= FILE_SYSTEM_STEPS["FAT"]
    return known.restamps


def _fine(witnesses):
    """Whether every witness is on a volume whose every later write stamps a later time (APFS): no clock to read."""
    return all(getattr(volume(witness), "fine", False) for witness in witnesses)


def settled(witness, clock=None, *, change=True):
    """Whether every time ``witness`` carries is past (always on a fine volume); one ahead of the clock is past."""
    if _fine((witness,)):
        return True
    return verdict(witness, (system_time if clock is None else clock)(), change)[0] != WAIT


def unique():
    """A key no other key ever equals, in any process: what stands for a stat nothing vouches for."""
    return ("unsettled", uuid.uuid4().hex)


def cache_key(witness, key, *, change=False):
    """``key`` when ``witness`` is settled, else ``unique()``: the value read under it is used once, never reused.
    A cache keys on the modification time (and size); ``change=True`` for a key that holds the change time too."""
    return key if settled(witness, change=change) else unique()


def settled_key(witness, key, *, change=False):
    """``key`` once ``witness`` settled (a wait, at most a step), else ``unique()``: for a key kept beyond this process
    (a digest saved in a .blend) or one that starts costly work (a bake), which a cache's miss must never repeat."""
    try:
        settle(witness, change=change)
    except Unsettled:
        return unique()
    return key


def settle(witness, clock=None, *, change=True, writing=False):
    """Return ``witness`` once every time it carries is past (``settle_all``)."""
    _wait((witness,), clock, change, writing)
    return witness


def settle_all(witnesses, clock=None, *, change=True, writing=False):
    """Wait until every time of every witness is past: one wait, however many files. ``Unsettled`` at once for a
    change time ahead of the clock or a coarse step where a wait would freeze a UI, or when a time stays in its step
    past the cap; ``ClosingRestamps`` for a witness on a handle still open for writing where closing restamps."""
    _wait(witnesses, clock, change, writing)


@contextlib.contextmanager
def awaited():
    """Inside it, the main thread waits a coarse step out instead of refusing (each settle still fails closed past its
    cap): for a step the person already waits for (a snapshot capture, a Restore), where refusing would refuse every
    one on a coarse disk or a share (a home folder there). Nested blocks count; each thread has its own."""
    _awaited.depth = getattr(_awaited, "depth", 0) + 1
    try:
        yield
    finally:
        _awaited.depth -= 1


def _freezes_ui():
    """Whether a wait here would freeze a UI unasked: Blender's main thread with windows (``bpy.app.background``
    False) outside ``awaited()``. A thread, a background Blender and a process without ``bpy`` wait; never imported."""
    if threading.current_thread() is not threading.main_thread() or getattr(_awaited, "depth", 0):
        return False
    app = getattr(sys.modules.get("bpy"), "app", None)
    return getattr(app, "background", True) is False


def _wait(witnesses, clock, change, writing=False):
    global waits, waited_s
    witnesses, started, limit = tuple(witnesses), None, 0.0
    clock = system_time if clock is None else clock
    if _fine(witnesses):
        return
    if writing and any(_closing_restamps(witness) for witness in witnesses):
        # Measured: FAT32 and exFAT stamp LastWriteTime when the handle closes, so what is compared later may move.
        raise ClosingRestamps("The file's volume stamps its times when a writer closes it; an open writer's stat "
                              "vouches for nothing")
    while True:
        now = clock()
        verdicts = [verdict(witness, now, change) for witness in witnesses]
        if any(state == AHEAD for state, _seconds in verdicts):
            raise ClockAhead("The file's change time is ahead of the system clock")
        pending = max((wait for state, wait in verdicts if state == WAIT), default=None)
        if pending is None:
            break
        if pending * 1e7 > 2 * TICK and _freezes_ui():
            # A step longer than a tick (2 s on FAT and exFAT) would freeze Blender's UI: refused as changed there,
            # in words that let an agent simply read the file again (an agent-facing tool result carries them).
            raise Unsettled("This file is on a volume that keeps file times in steps of up to 2 seconds (FAT, exFAT, "
                            "HFS+ or a network share) and was written moments ago, so a later change could not be told "
                            "from none yet; read it again in about %d s" % max(1, int(pending + 0.999)))
        if started is None:
            started, limit = _monotonic(), LIMIT_S + pending
        elif _monotonic() - started > limit:
            raise Unsettled("The file's time stayed in its clock step past the wait's cap")
        _sleep(min(max(pending, STEP_S), LIMIT_S))   # A tick's wait in short steps; a coarse step's at once.
    if started is not None:
        with _counted:
            waits += 1
            waited_s += _monotonic() - started
