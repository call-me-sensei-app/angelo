# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Is another Angelo open? (``version_carry_over``): while one is, a first launch uses the earlier folder in place
instead of moving it. The process list comes from ``ps`` (this user's processes) or, on Windows, a Toolhelp
snapshot; a list that cannot be read counts as another Angelo, so the move never runs under a live session.

Standard library only (``bpy`` only for this executable's path).
"""

import os
import re
import subprocess
import sys


def process_table(platform=None):
    """``[(pid, parent pid, executable name)]`` of this user's processes (Windows: every one visible). Raises
    ``OSError`` when the list cannot be read, or reads as empty (a list without this process is never a real one)."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        rows = windows_table()
    else:
        # This user's processes: another account's Angelo can't touch this account's folders.
        listing = subprocess.run(["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,ppid=,comm="], capture_output=True,
                                 text=True, timeout=20)
        if listing.returncode != 0:
            raise OSError("ps failed: %s" % listing.stderr.strip()[:200])
        rows = []
        for line in listing.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1]), re.split(r"[\\/]", parts[2].strip())[-1]))
    if not rows:
        raise OSError("The process list read as empty")
    return rows


def windows_table():
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        raise OSError("No process snapshot")
    rows = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            rows.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def others_of(rows, executable, own_pid):
    """Whether ``rows`` hold a process of ``executable`` that is neither this process nor one it started (Cycles
    lists GPUs with a child of this same binary; workers are children too)."""
    own = re.split(r"[\\/]", executable)[-1].lower()
    stem = own[:-4] if own.endswith(".exe") else own
    parents = {pid: parent for pid, parent, _name in rows}

    def ours(pid):
        seen = set()
        while pid not in seen and pid in parents:
            if pid == own_pid:
                return True
            seen.add(pid)
            pid = parents[pid]
        return pid == own_pid
    # macOS ``ps`` may shorten a name; Windows names the file with ".exe".
    return any(name.lower() in {own, stem, stem + ".exe"} and not ours(pid) for pid, _parent, name in rows)


def others_running():
    """Whether another Angelo runs now; a list that cannot be read counts as one."""
    try:
        import bpy
        return others_of(process_table(), bpy.app.binary_path, os.getpid())
    except (ImportError, AttributeError, OSError, subprocess.SubprocessError):
        return True
