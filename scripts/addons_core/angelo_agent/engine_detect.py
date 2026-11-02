# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which game engines are running or installed on this computer: Godot, Unity and Unreal Engine. No Blender here.

What it answers is only a set of engine ids (``godot``, ``unity``, ``unreal``), so Angelo can suggest the matching
live link (``engine_suggest``) and the agent can recommend it. Process names and paths are read here, compared and
dropped: nothing returned, stored, logged or sent ever holds one.

* **Running** reads the operating system's process table once per call, without starting a process: on macOS
  ``proc_listallpids`` and ``proc_name`` from libproc, on Windows a Toolhelp32 snapshot (``CreateToolhelp32Snapshot``,
  ``Process32FirstW`` / ``Process32NextW``). Both list the person's own processes without any permission prompt.
  Anywhere else it answers that nothing runs. ``running`` takes a ``names`` list instead, which is how the tests fake
  each operating system's process table.
* **Installed** looks for each engine's application where its installer or launcher puts it (``INSTALL_GLOBS``);
  the Unity Hub and the Epic Games Launcher put editors in fixed folders, Godot is a single application anyone may
  place anywhere, so only the usual places count.

A scan is cheap (well under a millisecond for a few hundred processes on Apple silicon; performance in
doc/angelo/engine_link.rst, "Engine detection"), but it is still called off Blender's main thread (``engine_suggest``).
"""

import ctypes
import fnmatch
import glob
import os
import sys

ENGINES = ("godot", "unity", "unreal")
DISPLAY = {"godot": "Godot", "unity": "Unity", "unreal": "Unreal Engine"}

# An editor's process name, lower-cased and without ".exe", per engine. Godot's executable carries its version
# (Godot_v4.3-stable_win64, Godot_v4.3-stable_mono_win64) and is often renamed, so any name that starts with "godot"
# counts. Unity's editor is "Unity" (the Hub, "Unity Hub", is not an editor). Unreal's editor is "UnrealEditor"
# (UE5; UE4's "UE4Editor" too); "UnrealEditor-Cmd" runs commandlets without a window, so it does not count.
_PROCESS_RULES = {
    "godot": (("godot*",), ()),
    "unity": (("unity",), ()),
    "unreal": (("unrealeditor", "unrealeditor-*", "ue4editor", "ue5editor"), ("unrealeditor-cmd", "*-cmd")),
}

# Where installers put each engine. ``~`` is the home folder; ``%NAME%`` an environment variable (Windows).
INSTALL_GLOBS = {
    "darwin": {
        "godot": ("/Applications/Godot*.app", "~/Applications/Godot*.app",
                  "~/Library/Application Support/Steam/steamapps/common/Godot Engine"),
        "unity": ("/Applications/Unity/Hub/Editor/*/Unity.app", "/Applications/Unity Hub.app",
                  "/Applications/Unity/Unity.app"),
        "unreal": ("/Users/Shared/Epic Games/UE_5.*", "/Users/Shared/Epic Games/UE_4.*"),
    },
    "win32": {
        "godot": ("%LOCALAPPDATA%/Microsoft/WinGet/Packages/GodotEngine.GodotEngine*",
                  "%ProgramFiles(x86)%/Steam/steamapps/common/Godot Engine",
                  "%LOCALAPPDATA%/Programs/Godot*"),
        "unity": ("%ProgramFiles%/Unity/Hub/Editor/*/Editor/Unity.exe", "%ProgramFiles%/Unity Hub/Unity Hub.exe"),
        "unreal": ("%ProgramFiles%/Epic Games/UE_5.*", "%ProgramFiles%/Epic Games/UE_4.*"),
    },
}


def engine_of(name):
    """The engine an executable's name belongs to, or None. ``name`` may be a path or end in .exe."""
    base = os.path.basename(name.replace("\\", "/")).strip().lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if not base:
        return None
    for engine, (match, exclude) in _PROCESS_RULES.items():
        if any(fnmatch.fnmatchcase(base, pattern) for pattern in match) and \
                not any(fnmatch.fnmatchcase(base, pattern) for pattern in exclude):
            return engine
    return None


def running(names=None, platform=None):
    """The engines whose editor runs now, as a frozenset of ids. ``names``: process names to judge instead of the
    operating system's (tests); otherwise read for ``platform`` (default ``sys.platform``)."""
    if names is None:
        names = process_names(platform or sys.platform)
    found = set()
    for name in names:
        engine = engine_of(name)
        if engine is not None:
            found.add(engine)
    return frozenset(found)


def process_names(platform):
    """Every process name this user can see; empty on an operating system this module does not read, or when the
    read fails (detection is a convenience, never a reason to fail)."""
    try:
        if platform == "darwin":
            return _mac_names()
        if platform == "win32":
            return _windows_names()
    except (OSError, AttributeError, ValueError):
        pass
    return []


def _mac_names():
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    count = libproc.proc_listallpids(None, 0)
    if count <= 0:
        return []
    pids = (ctypes.c_int * (count + 64))()   # Room for processes started between the two calls.
    count = libproc.proc_listallpids(pids, ctypes.sizeof(pids))
    names, buffer = [], ctypes.create_string_buffer(256)
    for pid in pids[:max(count, 0)]:
        if pid > 0 and libproc.proc_name(pid, buffer, ctypes.sizeof(buffer)) > 0:
            names.append(buffer.value.decode("utf-8", "replace"))
    return names


def _windows_names():
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    names = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            names.append(entry.szExeFile)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def _expand(pattern, environ):
    def variable(part):
        if part.startswith("%") and part.endswith("%") and len(part) > 2:
            return environ.get(part[1:-1])
        return part
    parts = pattern.split("/")
    expanded = [variable(part) for part in parts]
    if any(part is None for part in expanded):
        return None   # An unset variable: that place does not exist here.
    path = "/".join(expanded)
    if path.startswith("~"):
        home = environ.get("HOME") or environ.get("USERPROFILE")
        if not home:
            return None
        path = home + path[1:]
    return path


def installed(platform=None, environ=None, exists=None):
    """The engines installed where their installers put them, as a frozenset of ids. ``exists(pattern)`` decides
    whether a glob pattern matches (tests fake the file system); by default ``glob.glob``."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    exists = exists or (lambda pattern: bool(glob.glob(pattern)))
    found = set()
    for engine, patterns in INSTALL_GLOBS.get(platform, {}).items():
        for pattern in patterns:
            path = _expand(pattern, environ)
            if path is not None and exists(path):
                found.add(engine)
                break
    return frozenset(found)
