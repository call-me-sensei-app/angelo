# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A process's identity read from the kernel, never by running a program. No bpy.

``started`` is the process's start time as ``ps -o lstart=`` prints it in the C locale and UTC, whitespace collapsed
(``"Wed Nov 18 11:06:40 2026"``): with its pid it names one process, and records written by builds that ran ``ps``
still verify. ``executable_name`` is its executable's file name, and ``user_processes`` lists this user's processes
(``ps -U``). macOS reads them from ``sysctl`` (``KERN_PROC_PID``, the ``p_starttime`` ``ps`` reads) and libproc;
Linux from ``/proc``, as ``ps`` does there. Elsewhere there is none (Windows has its own Win32 readers). Why not ``ps``: it started a process at every launch (``headless_window_lock``), and the main thread never starts
one to read an identity (doc/angelo/mcp_headless_window.rst, "Hand-over")."""

import os
import sys
import time

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")   # time.struct_time.tm_wday: Monday is 0.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CTL_KERN, _KERN_PROC, _KERN_PROC_PID = 1, 14, 1
_PATH_MAX = 4096   # PROC_PIDPATHINFO_MAXSIZE
_PROC_RUID_ONLY, _PROC_PIDTBSDINFO, _BSDINFO_SIZE = 5, 3, 136   # libproc: struct proc_bsdinfo is 136 bytes.
PROC_STATUS = "%s/%s/status"   # Linux: a process's owner, parent and short name (``/proc`` or a test's tree).
PROC_EXE = "%s/%s/exe"         # Linux: the link to its executable.
_PID_MAX = 2 ** 31 - 1   # pid_t is a signed 32-bit int on macOS and Linux.
_libc = []


def start_text(seconds):
    """``seconds`` since the epoch as ``ps`` prints a start time in the C locale and UTC (whole seconds, as ``ps``),
    with names that never follow the caller's locale."""
    moment = time.gmtime(int(seconds))
    return "%s %s %d %02d:%02d:%02d %d" % (DAYS[moment.tm_wday], MONTHS[moment.tm_mon - 1], moment.tm_mday,
                                           moment.tm_hour, moment.tm_min, moment.tm_sec, moment.tm_year)


def _pid(pid):
    """``pid`` as a process ID the kernel could hold (1 to 2**31 - 1), else None: a record's pid is read from a file
    anyone could have written, and a bool or a float is never one."""
    if isinstance(pid, bool) or not isinstance(pid, (int, str)):
        return None
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= _PID_MAX else None


def _system():
    if not _libc:
        import ctypes
        _libc.append(ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True))
    return _libc[0]


def _darwin_start_seconds(pid):
    """``kinfo_proc.kp_proc.p_starttime.tv_sec``: the first field of ``extern_proc``, itself the first of
    ``kinfo_proc``. A pid with no process answers an empty buffer."""
    import ctypes
    name = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
    buffer = ctypes.create_string_buffer(1024)   # sizeof(struct kinfo_proc) is 648 on 64-bit macOS.
    size = ctypes.c_size_t(ctypes.sizeof(buffer))
    if _system().sysctl(name, 4, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or size.value < 16:
        return None
    return ctypes.c_int64.from_buffer(buffer, 0).value


def _linux_start_seconds(pid):
    """Boot time plus the process's start in clock ticks, in whole seconds: what procps's ``lstart`` prints."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as stat:
            fields = stat.read().rsplit(b")", 1)[1].split()   # After the name, which may hold spaces or parens.
        with open("/proc/stat", "rb") as whole:
            boot = next(int(line.split()[1]) for line in whole if line.startswith(b"btime "))
        return boot + int(fields[19]) // os.sysconf("SC_CLK_TCK")   # Field 22 of proc(5); the list starts at 3.
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def started(pid, platform=None):
    """The process's start identity (see the module), or None when it can't be read or there is no such process:
    any failure reading it is "unknown", never an exception (the GUI lock reads it at register)."""
    pid, platform = _pid(pid), platform or sys.platform
    if pid is None:
        return None
    try:
        if platform == "darwin":
            seconds = _darwin_start_seconds(pid)
        elif platform.startswith("linux"):
            seconds = _linux_start_seconds(pid)
        else:
            return None
        return start_text(seconds) if seconds and seconds > 0 else None
    except Exception:   # noqa: BLE001  A kernel answer this can't read is no identity.
        return None


def executable_name(pid, platform=None):
    """The file name of the process's executable (``Angelo``), or None (any failure reading it included)."""
    pid, platform = _pid(pid), platform or sys.platform
    if pid is None:
        return None
    try:
        if platform == "darwin":
            import ctypes
            buffer = ctypes.create_string_buffer(_PATH_MAX)
            length = _system().proc_pidpath(pid, buffer, ctypes.c_uint32(_PATH_MAX))
            path = buffer.raw[:length].decode("utf-8", "surrogateescape") if length > 0 else ""
        elif platform.startswith("linux"):
            path = os.readlink("/proc/%d/exe" % pid)
        else:
            return None
        return os.path.basename(path.removesuffix(" (deleted)")) or None
    except Exception:   # noqa: BLE001  An answer this can't read names no executable.
        return None


def _darwin_rows():
    if os.name == "nt":
        raise OSError("libproc is macOS's")
    import ctypes
    system, uid = _system(), os.getuid()
    count = system.proc_listpids(_PROC_RUID_ONLY, ctypes.c_uint32(uid), None, 0)
    if count <= 0:
        raise OSError("proc_listpids failed")
    pids = (ctypes.c_int * (count // ctypes.sizeof(ctypes.c_int) + 64))()   # Room for processes started meanwhile.
    count = system.proc_listpids(_PROC_RUID_ONLY, ctypes.c_uint32(uid), pids, ctypes.sizeof(pids))
    if count <= 0:
        raise OSError("proc_listpids failed")
    rows, info = [], ctypes.create_string_buffer(_BSDINFO_SIZE)
    for pid in pids[:count // ctypes.sizeof(ctypes.c_int)]:
        if pid <= 0 or system.proc_pidinfo(pid, _PROC_PIDTBSDINFO, ctypes.c_uint64(0), info,
                                            _BSDINFO_SIZE) != _BSDINFO_SIZE:
            continue   # Ended since it was listed.
        parent = ctypes.c_uint32.from_buffer(info, 16).value   # pbi_ppid, after pbi_flags/status/xstatus/pid.
        name = executable_name(pid, "darwin") or info.raw[48:64].split(b"\0", 1)[0].decode("utf-8", "replace")
        rows.append((pid, parent, name))
    return rows


def _linux_rows(proc="/proc", uid=None):
    """This user's processes from ``proc`` (``/proc``; the tests pass a tree of their own)."""
    if os.name == "nt":
        raise OSError("/proc is Linux's")
    rows, uid = [], os.getuid() if uid is None else uid
    for entry in os.listdir(proc):
        if not entry.isdigit():
            continue
        try:
            with open(PROC_STATUS % (proc, entry), encoding="utf-8", errors="replace") as status:
                fields = dict(line.split(":", 1) for line in status if ":" in line)
            if int(fields["Uid"].split()[0]) != uid:   # The real uid, as ``ps -U``.
                continue
            try:
                name = os.path.basename(os.readlink(PROC_EXE % (proc, entry)).removesuffix(" (deleted)"))
            except OSError:
                name = ""   # A kernel thread, or not ours to read: its short name instead.
            rows.append((int(entry), int(fields["PPid"]), name or fields["Name"].strip()))
        except (OSError, KeyError, ValueError, IndexError):
            continue   # Ended since it was listed, or not this user's to read.
    return rows


def user_processes(platform=None):
    """``[(pid, parent pid, executable name)]`` of this user's processes (``ps -U <uid>``). Raises ``OSError`` when
    the list can't be read."""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            return _darwin_rows()
        if platform.startswith("linux"):
            return _linux_rows()
    except AttributeError as error:   # A libSystem without the call.
        raise OSError(str(error)) from error
    raise OSError("no native process list on %s" % platform)
