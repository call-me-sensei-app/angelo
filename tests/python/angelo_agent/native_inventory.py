# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The confinement's inventory of native code (test_blender_confinement_escapes; doc/angelo/script_confinement.rst)."""

# Every extension module the app bundles (and every module built into it), by what it can do with files: break-it
# MUST-3's inventory. A module the app gains that is in none of these fails test_blender_confinement_escapes until it
# is classified. ``REFUSED`` must match ``_angelo_guard.native_file_modules()`` (refused on import while confined).
REFUSED = {"_curses", "_curses_panel", "_dbm", "_gdbm", "readline", "_tkinter", "tkinter", "_interpreters",
           "_interpchannels", "_interpqueues", "_lsprof", "winsound"}
GATED = {   # Opens files natively; its file entry points are judged or refused in C.
    "_sqlite3": "no database can be attached; setlimit and extensions refused; its own file judged",
    "_ssl": "certificate loaders' paths judged", "_cycles": "osl_compile's paths judged",
    "blf": "load's path judged", "imbuf": "through Blender's gated file functions",
    "faulthandler": "its writers refused while confined", "mmap": "audited, descriptor judged",
    "posix": "open, descriptor I/O wrapped; the rest audited", "_io": "audited", "_ctypes": "every event refused",
    "_socket": "the network refused", "_imp": "wrapped", "_posixsubprocess": "wrapped", "_thread": "wrapped",
    "_signal": "wrapped", "_codecs": "wrapped", "atexit": "wrapped", "marshal": "gated (provenance)",
    "gc": "introspection refused", "sys": "hooks and monitoring refused or wrapped",
    # The pipeline libraries (bpy_angelo_guard_confine_wrap_pipeline.cc): gated where they are made, so a module the
    # person imported before any script is gated too. ``PIPELINE`` below classifies their entry points.
    "pxr": "every asset path through the confined Ar resolver; non-Ar writers, readers and Plug judged or refused",
    "OpenImageIO": "file names, search paths and plugin paths judged; color configs and LUTs judged",
    "openvdb": "read and write paths judged", "MaterialX": "file paths, search paths and every XInclude judged",
    "PyOpenColorIO": "config, LUT, ICC and archive paths judged; processors read LUTs through a judging IO proxy",
    "OpenEXR": "read and write paths judged", "opentimelineio": "native JSON file entry points judged",
    "oslquery": "shader name and search path judged", "aud": "sound files and HRTF folders judged",
    "manta": "every save and load path judged",
    # Windows.
    "nt": "open, descriptor I/O wrapped; the rest audited (as posix)",
    "_winapi": "CreateFile judged; CreateProcess, OpenProcess, TerminateProcess, CreateNamedPipe refused",
    "msvcrt": "handles only from judged entries; SetErrorMode refused",
    "_overlapped": "ConnectPipe refused; its sockets come only from _socket (the network refused)",
    "_wmi": "exec_query refused (every process's command line)",
    "winreg": "every change refused (Run keys start programs); reads stay",
}
PYTHON_IO = {   # Reads or writes files only through Python's own (audited) file objects.
    "numpy", "Cython", "zstandard", "_pickle", "_json", "_csv", "_elementtree", "pyexpat", "_lzma", "_bz2",
    "zlib", "_zoneinfo", "array",
}
NO_FILES = {   # No file access of its own (or only the system's own databases and log).
    "_asyncio", "_bisect", "_blake2", "_codecs_cn", "_codecs_hk", "_codecs_iso2022", "_codecs_jp", "_codecs_kr",
    "_codecs_tw", "_contextvars", "_datetime", "_decimal", "_hashlib", "_heapq", "_md5", "_multibytecodec",
    "_multiprocessing", "_opcode", "_posixshmem", "_queue", "_random", "_sha1", "_sha2", "_sha3", "_statistics",
    "_struct", "_uuid", "binascii", "cmath", "fcntl", "grp", "math", "resource", "select", "syslog", "termios",
    "unicodedata", "_abc", "_ast", "_bpy_hydra", "_bpy_path", "_collections", "_functools", "_locale",
    "_operator", "_sre", "_stat", "_string", "_suggestions", "_symtable", "_sysconfig", "_tokenize",
    "_tracemalloc", "_typing", "_warnings", "_weakref", "bl_math", "bmesh", "builtins", "errno", "gpu", "idprop",
    "itertools", "mathutils", "pwd", "time", "_scproxy",
    "xxsubtype",   # Windows builds CPython's example module in.
}

# The pipeline libraries' entry points (test_blender_confinement_pipeline lists every module function and class
# attribute whose name says files, in every bundled pipeline module). Each is classified: ``PIPELINE_GATED`` maps
# it to a statement calling it with a private path, which must be refused (bpy_angelo_guard_confine_pipeline_table);
# ``PIPELINE_OTHER`` says why it opens no file by a path of its own.
PIPELINE_ROOTS = ("pxr", "OpenImageIO", "openvdb", "MaterialX", "PyOpenColorIO", "OpenEXR", "opentimelineio",
                  "oslquery", "aud", "manta")
from pipeline_inventory import PIPELINE_GATED, PIPELINE_OTHER  # noqa: E402,F401  (the table: its own file)
