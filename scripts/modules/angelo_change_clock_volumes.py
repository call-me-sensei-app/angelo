# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a volume keeps file times off Windows, by its file system: ``angelo_change_clock``'s judge on macOS and Linux.

macOS names each mounted file system's type and flags in its mount table (``getfsstat`` with ``MNT_NOWAIT``: what
the kernel keeps, so no disk or server is asked and nothing blocks), and a file's ``st_dev`` is its mount's
``f_fsid.val[0]``. Measured on macOS 26.6 (CPython 3.13, disk images made by ``hdiutil``, 40 writes each, a same-size
rewrite in place at once 30 times each, 2026-11-05):

* APFS stamps nanosecond times from the precise clock, never below a reading taken before the write: every later
  write carries a later time (a rewrite kept every stat field 0 times in 30). Nothing is waited for or read.
* HFS+ keeps times in 1 s steps, FAT16 and FAT32 (``msdos``) in 2 s steps, exFAT in 10 ms steps, each rounded down
  (a stamp up to one step below the clock). None moves a time when a handle closes. A rewrite kept (inode, size,
  mtime, ctime): 30 times in 30 on HFS+ and FAT, 18 in 30 on exFAT. HFS+ keeps a change time no program sets; FAT and
  exFAT report the modification time as it (``os.utime`` moves both), so there it is judged as one a program sets,
  and a rewrite that puts the modification time back is outside what any stat sees.
* Any other type (SMB, NFS, AFP, WebDAV, a FUSE file system, one not seen yet), and APFS that is not local, is
  unknown: judged as the coarsest known, 2 s on either side of the clock. Whether closing a writer restamps there is
  not measured, so a writer's stat is judged by its own time, as Windows judges a share: one kept in 2 s steps may
  be FAT's and is refused; any other restamp at close only makes a later check see a change (fails closed). A
  server stamps with its own clock; one set further off than that is outside what a stat can see.

Linux names each mount's device and type in ``/proc/self/mountinfo``. Its disk and memory file systems stamp
nanosecond values from the kernel's tick clock (kernels before 6.13), so two writes in one tick share a time: judged
16 ms either side of the clock (``LINUX_DISK``), a wait short enough for a UI. FAT and exFAT (not measured here) and
anything unknown are the coarsest, as on macOS. A file whose ``st_dev`` no mount names (a btrfs subvolume's) is
unknown. Angelo ships no Linux build; its test runs use Linux.

An answer is kept per device (copied on write, read without a lock). A device number is given again to the next
volume mounted (every image above got the same one), so a watch counts mounts and unmounts on a thread of its own
(macOS: a kqueue ``EVFILT_FS`` filter; Linux: mountinfo polled for ``POLLPRI``; only that thread waits on it), and a
kept answer stands only while the count it was listed at holds: an int compare. A witness's change time whose
trailing zeros do not fit its answer (a fine time on a stepped volume, a stepped one on APFS, or an HFS+ stamp on an
even second) lists the mounts again; an answer the listing confirms keeps that stamp shape for its count, so an HFS+
volume lists once per shape, not per settle. Where no watch counts (its thread could not start or ended, or a forked
child before its first listing) the stamp check decides alone and every answer is listed again ``RECHECK_S`` after
it was listed; a watch that ended is started again then. On Linux a disk file system's whole-second stamps (ext2-4
with small inodes keep seconds) judge it by their own step.

On Windows ``angelo_change_clock`` judges volumes itself; the two Win32 calls its listing makes are at the end here.
"""

import collections
import os
import sys
import threading
import time

PLATFORM = sys.platform    # Whose mount table and file-system table apply (a test stands in another).
SECOND = 10_000_000        # In FILETIME units (100 ns), as ``angelo_change_clock`` counts.
RECHECK_S = 60.0           # Without a mount watch, an answer is listed again when seen this long after.
MNT_LOCAL = 0x1000         # macOS ``f_flags``: the file system is stored on this computer.
MNT_NOWAIT = 2             # ``getfsstat``: the kernel's own copy, never a file system asked.
# ``step``: the step times are kept in; ``below``: how far under the clock's reading a write can stamp; ``restamps``:
# whether closing a writer's handle can move a time (None: judged by the time's own step); ``fine``: every later
# write stamps a later time (no clock read);
# ``changes``: ``st_ctime`` is a change time no program can set (else it is judged as a modification time).
Volume = collections.namedtuple("Volume", "step below restamps fine changes")
FINE = Volume(0, 0, False, True, True)
UNKNOWN = Volume(2 * SECOND, 2 * SECOND, None, False, True)   # A change time that runs ahead fails closed.
MAC_TYPES = {"apfs": FINE, "hfs": Volume(SECOND, SECOND, False, False, True),
             "msdos": Volume(2 * SECOND, 2 * SECOND, False, False, False),
             "exfat": Volume(100_000, 100_000, False, False, False)}
# Linux's disk and memory file systems stamp nanosecond times from the kernel's tick clock (before 6.13; finer once a
# stat asked since), which lags the clock read here by up to a tick (10 ms at 100 Hz): 16 ms below and above it.
LINUX_DISK = Volume(160_000, 160_000, False, False, True)
LINUX_TYPES = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "ramfs", "overlay", "zfs", "f2fs",
                         "bcachefs", "jfs", "nilfs2", "squashfs", "erofs"})
VALUE_STEPS = (20_000_000, 10_000_000, 100_000, 10_000)   # 2 s, 1 s, 10 ms, 1 ms: what trailing zeros can mean.
_monotonic = time.monotonic
_known = {}                # st_dev -> (Volume, monotonic time, generation, stamp steps the listing confirmed).
_lock = threading.Lock()
_getfsstat = None
EVFILT_FS = -9             # kqueue's file-system filter: an event at each mount and unmount (macOS).
VQ_MOUNTS = 0x8 | 0x10 | 0x20 | 0x100   # Its events that change the table: mount, unmount, dead, update.
_watching = None           # (pid, token, monotonic start) of the mount watch started (``_arm``), under ``_lock``.
_watched = None            # (pid, token) of the watch that counts now (set by its thread once it waits).
_generation = 0            # Counts mounts and unmounts the watch saw; a kept answer holds the count it was listed at.
listings = 0               # Mount listings made, and the time they took, for latency measurements.
listed_s = 0.0


def value_step(time_ns):
    """The coarsest step a time's trailing zeros allow, in FILETIME units (0: stamped finer than 1 ms)."""
    filetime = int(time_ns) // 100
    if int(time_ns) % 100:
        return 0
    return next((step for step in VALUE_STEPS if filetime % step == 0), 0)


def judge(file_system, local=True):
    """The ``Volume`` of a file system by its type name (``local``: macOS ``MNT_LOCAL``); unknown is the coarsest."""
    platform, name = PLATFORM, str(file_system).lower()
    if platform == "darwin":
        known = MAC_TYPES.get(name)
        return UNKNOWN if known is None or (known.fine and not local) else known
    if platform.startswith("linux"):
        if name in LINUX_TYPES:
            return LINUX_DISK
    return UNKNOWN   # FAT and exFAT on Linux (vfat, msdos, exfat), a share, FUSE, any other type: the coarsest.


def _coarser(first, second):
    """Two mounts of one device (a bind mount): the answer that waits longer and trusts less."""
    if first is None or first.fine:
        return second
    if second.fine:
        return first
    restamps = True if True in (first.restamps, second.restamps) else (
        None if None in (first.restamps, second.restamps) else False)
    return Volume(max(first.step, second.step), max(first.below, second.below), restamps,
                  False, first.changes or second.changes)   # A change time judged as stamped refuses more.


def _mac_mounts():
    """``[(st_dev, type, local)]`` of every mount (``getfsstat``, ``MNT_NOWAIT``)."""
    global _getfsstat
    import ctypes

    class Statfs(ctypes.Structure):   # struct statfs with 64-bit inodes (every arm64 and current x86_64 build).
        _fields_ = [("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32), ("f_counts", ctypes.c_uint64 * 5),
                    ("f_fsid", ctypes.c_int32 * 2), ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
                    ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
                    ("f_fstypename", ctypes.c_char * 16), ("f_mntonname", ctypes.c_char * 1024),
                    ("f_mntfromname", ctypes.c_char * 1024), ("f_flags_ext", ctypes.c_uint32),
                    ("f_reserved", ctypes.c_uint32 * 7)]
    if _getfsstat is None:
        libc = ctypes.CDLL(None, use_errno=True)
        try:   # x86_64 names the 64-bit-inode struct's entry point apart; arm64 has only that one.
            function = getattr(libc, "getfsstat$INODE64")
        except AttributeError:
            function = libc.getfsstat
        function.restype, function.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        _getfsstat = function
    for _attempt in range(3):   # A mount added between the count and the copy: count again.
        count = _getfsstat(None, 0, MNT_NOWAIT)
        if count < 0:
            raise OSError(ctypes.get_errno(), "getfsstat failed")
        table = (Statfs * (count + 4))()
        found = _getfsstat(table, ctypes.sizeof(table), MNT_NOWAIT)
        if found < 0:
            raise OSError(ctypes.get_errno(), "getfsstat failed")
        if found < len(table):
            return [(entry.f_fsid[0] & 0xFFFFFFFF, entry.f_fstypename.decode("utf-8", "replace"),
                     bool(entry.f_flags & MNT_LOCAL)) for entry in table[:found]]
    raise OSError("the mount table kept growing while it was read")


def linux_device(major, minor):
    """Linux's ``st_dev`` of a ``major:minor`` (glibc's ``makedev``, which ``os.makedev`` is on Linux)."""
    return ((major & 0xFFF) << 8) | ((major & ~0xFFF) << 32) | (minor & 0xFF) | ((minor & ~0xFF) << 12)


def _linux_mounts(path="/proc/self/mountinfo"):
    """``[(st_dev, type, True)]`` of every mount: mountinfo's ``major:minor`` and the type after its `` - ``."""
    mounts = []
    with open(path, "rb") as source:
        for line in source.read().decode("utf-8", "replace").splitlines():
            fields, _separator, tail = line.partition(" - ")
            fields, tail = fields.split(), tail.split()
            if len(fields) < 3 or not tail or ":" not in fields[2]:
                continue
            major, _colon, minor = fields[2].partition(":")
            mounts.append((linux_device(int(major), int(minor)), tail[0], True))
    return mounts


def mounts():
    """``{st_dev: Volume}`` of every mount on this host (macOS and Linux; elsewhere none, so all are unknown)."""
    if PLATFORM == "darwin":
        listed = _mac_mounts()
    elif PLATFORM.startswith("linux"):
        listed = _linux_mounts()
    else:
        listed = []
    found = {}
    for device, file_system, local in listed:
        found[device] = _coarser(found.get(device), judge(file_system, local))
    return found


def _bump():
    global _generation
    with _lock:
        _generation += 1


def _forked():
    """In a forked child: a lock of its own (the parent's watch thread may have held it), and no watch yet."""
    global _lock, _watching, _watched
    _lock, _watching, _watched = threading.Lock(), None, None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_forked)


def _watch(pid, token):
    """The mount watch's thread: counts each mount and unmount (``_generation``) while this process lives. macOS: a
    kqueue ``EVFILT_FS`` filter; Linux: ``/proc/self/mountinfo`` polled for ``POLLPRI`` (CPython's poll object takes
    one waiter at a time: only this thread waits on it). Once it waits, ``_watched`` names it; one that ends is none."""
    import select
    handle = None
    try:
        if PLATFORM == "darwin":
            queue = select.kqueue()
            queue.control([select.kevent(0, filter=EVFILT_FS, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR)], 0, 0)
            wait = lambda: any(event.fflags & VQ_MOUNTS for event in queue.control(None, 64, None))   # noqa: E731
        else:
            handle = open("/proc/self/mountinfo", "rb")
            poller = select.poll()
            poller.register(handle.fileno(), select.POLLPRI | select.POLLERR)
            wait = lambda: poller.poll(None)   # noqa: E731
        _begin(pid, token)
        while True:
            if wait():
                _bump()
    except Exception:   # noqa: BLE001 - a watch that fails is no watch: answers fall back to their stamps.
        pass
    finally:
        _end(pid, token)
        if handle is not None:
            handle.close()


def _begin(pid, token):
    """The watch waits: it counts for this process from now; no answer listed before counts as watched."""
    global _watched, _generation
    with _lock:
        _generation += 1
        _watched = (pid, token)


def _end(pid, token):
    """The watch ended: it counts no more, and no answer it vouched for stands."""
    global _watched, _generation
    with _lock:
        if _watched == (pid, token):
            _watched = None
        _generation += 1


def _arm():
    """Start this process's mount watch (macOS, Linux) unless one runs, or one ended under ``RECHECK_S`` ago; the
    generation a listing starting now holds, or None while no watch counts for this process."""
    global _watching
    pid, start = os.getpid(), None
    if PLATFORM == "darwin" or PLATFORM.startswith("linux"):
        with _lock:
            running = _watching is not None and _watching[0] == pid and (
                _watched == _watching[:2] or _monotonic() - _watching[2] <= RECHECK_S)
            if not running:
                start = _watching = (pid, object(), _monotonic())
        if start is not None:
            try:   # As the add-on's thread_start: a thread that cannot start leaves no watch, and the stamps decide.
                threading.Thread(target=_watch, args=start[:2], name="AngeloMountWatch", daemon=True).start()
            except RuntimeError:
                pass
    with _lock:
        return _generation if _watched is not None and _watched[0] == pid else None


def _list(device, before=None):
    """List the mounts again (in the caller's thread: the table is the kernel's, so it never blocks) and answer.
    ``before``: the kept entry a contradicting stamp sent here, and that stamp's step: when the listing gives the
    same answer at the same count, the entry keeps that shape as confirmed."""
    global _known, listings, listed_s
    started, generation = _monotonic(), _arm()
    try:
        found = mounts()
    except Exception:   # noqa: BLE001 - a table that cannot be read decides nothing: the coarsest stands, and
        found, generation = {}, None   # it is listed again RECHECK_S later, whatever the watch counts.
    now = _monotonic()
    with _lock:
        known = dict(_known)
        for listed, volume in found.items():
            known[listed] = (volume, now, generation, ())
        if device not in found:
            known[device] = (UNKNOWN, now, generation, ())   # No mount has it: unknown till a volume comes or goes.
        elif before is not None:
            (volume, _when, kept, shapes), shape = before
            if volume == found[device] and kept is not None and kept == generation == _generation:
                known[device] = (volume, now, generation, shapes + (shape,))
        _known = known
        listings += 1
        listed_s += now - started
    return known[device][0]


def _stamp_step(found):
    """The step a time on a volume so judged has in its trailing zeros: 0 for a nanosecond one."""
    return 0 if found.fine or found == LINUX_DISK else found.step


def _by_stamp(found, shape):
    """A Linux disk file system's answer for a stamp in whole seconds (or 10 ms): its own step, rounded down."""
    if found == LINUX_DISK and shape > found.step:
        return Volume(shape, shape, False, False, True)
    return found


def volume(device, change_ns):
    """The ``Volume`` of the device a stat's ``st_dev`` names; ``change_ns`` its change time (its modification time
    where it has none), which checks a kept answer. None without a device. A kept answer costs a dict lookup, an
    int compare and the time's trailing zeros (about 0.4 us); a listing about 80 us (Mac 2 under load). Never
    raises: whatever fails answers the coarsest."""
    if device is None:
        return None
    try:
        device = int(device) & 0xFFFFFFFF if PLATFORM == "darwin" else int(device)   # A dev_t is 32 bits there.
        shape = 0 if change_ns is None else value_step(change_ns)
        known = _known.get(device)
        if known is not None:
            found, when, generation, shapes = known
            watched = _watched
            if generation is not None and watched is not None and watched[0] == os.getpid() \
                    and generation == _generation:
                # The watch counted no volume coming or going since this was listed: the device is that volume.
                if found == UNKNOWN or change_ns is None or shape == _stamp_step(found) or shape in shapes:
                    return _by_stamp(found, shape)
                return _by_stamp(_list(device, (known, shape)), shape)
            if generation is None and (watched is None or watched[0] != os.getpid() or found == UNKNOWN) \
                    and _monotonic() - when <= RECHECK_S and (
                    found == UNKNOWN or (change_ns is not None and shape == _stamp_step(found))):
                return _by_stamp(found, shape)   # No watch: the witness's stamp checks it, for RECHECK_S at most.
        return _by_stamp(_list(device), shape)
    except Exception:   # noqa: BLE001 - never into a settle, a draw or a timer.
        return UNKNOWN


# Windows (``angelo_change_clock`` lists its volumes with these; nothing here runs elsewhere).
def windows_volume_names():
    """Every local volume's ``\\\\?\\Volume{...}\\`` name (``FindFirstVolumeW``: no volume is asked anything)."""
    import ctypes
    from ctypes import wintypes
    kernel32, names = ctypes.WinDLL("kernel32", use_last_error=True), []
    first, following, close = kernel32.FindFirstVolumeW, kernel32.FindNextVolumeW, kernel32.FindVolumeClose
    first.restype, first.argtypes = wintypes.HANDLE, [wintypes.LPWSTR, wintypes.DWORD]
    following.restype, following.argtypes = wintypes.BOOL, [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
    close.argtypes = [wintypes.HANDLE]
    name = ctypes.create_unicode_buffer(1024)
    handle = first(name, len(name))
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return names
    try:
        while True:
            names.append(name.value)
            if not following(handle, name, len(name)):
                return names   # ERROR_NO_MORE_FILES, or a volume that vanished meanwhile: what was found stands.
    finally:
        close(handle)


def windows_volume_information(name):
    """``(32-bit serial, file-system name)`` of one volume (``GetVolumeInformationW``), or None."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    information = kernel32.GetVolumeInformationW
    information.restype = wintypes.BOOL
    information.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                            wintypes.DWORD]
    serial, system, quiet = wintypes.DWORD(), ctypes.create_unicode_buffer(64), wintypes.DWORD()
    # SEM_FAILCRITICALERRORS: a card reader or an optical drive without media fails at once, never with a dialog.
    kernel32.SetThreadErrorMode(0x0001, ctypes.byref(quiet))
    try:
        if not information(name, None, 0, ctypes.byref(serial), None, None, system, len(system)):
            return None
        return serial.value, system.value
    finally:
        kernel32.SetThreadErrorMode(quiet.value, None)
