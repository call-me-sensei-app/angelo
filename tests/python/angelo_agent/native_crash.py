# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End this process with a real native crash, on every platform: what a crashing native add-on does.

``ctypes.string_at(0)`` faults natively on macOS and Linux, but on Windows ctypes runs every foreign call inside an
SEH guard that turns the access violation into ``OSError``, a Python error any caller catches (an add-on that raises
it only "did not load"). So on Windows the fault comes from outside ctypes: ``faulthandler._read_null()``, Python's
own read of address 0, on the calling thread, as on the other platforms and as a native add-on crashes (the thread
matters: a crash while start-up scripts run is the scripts' only on the main thread, ``wm_angelo_gpu_startup.cc``).
Should a Python lack that function, the fault happens on a native thread that ctypes did not start and no Python
frame guards: ``CreateThread`` with start address 0. Either way the access violation is unhandled, so the process
ends through the unhandled-exception filter (Blender's crash handler writes its log).

``SOURCE`` is the function as text, for a fixture add-on to embed (it is written to disk and imported by the app,
where this module is not on the path); ``crash`` is the same function, defined from that text.
test_native_crash_fixture.py runs both in a fresh process and requires the fault.

``keep_crash_logs`` is for the suite that asked for the crash: Blender's crash handler leaves
``<temp>/<file>.crash.txt``, and the suite runner reads every ``*.crash.txt`` in a suite's folder as a crash of the
run (``native_suite_checks.find_crash_logs``). Once the crash is checked, the suite moves the logs aside.
"""

SOURCE = '''
def crash():
    """End this process with a real native fault that no Python or ctypes handler catches (native_crash.py)."""
    import ctypes
    import sys
    if sys.platform == "win32":
        try:
            import faulthandler
            read_null = faulthandler._read_null   # A read of address 0 on this thread, outside ctypes' guard.
        except (ImportError, AttributeError):
            read_null = None
        if read_null is not None:
            read_null()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateThread.restype = ctypes.c_void_p
        kernel32.CreateThread.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_uint32, ctypes.c_void_p]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        thread = kernel32.CreateThread(None, 0, None, None, 0, None)   # Starts at address 0: a fault outside ctypes.
        if thread:
            kernel32.WaitForSingleObject(thread, 0xFFFFFFFF)
    else:
        ctypes.string_at(0)   # No guard on POSIX: SIGSEGV.
    raise RuntimeError("native_crash: the process survived its native crash")
'''

exec(compile(SOURCE, __file__, "exec"))   # noqa: S102  Defines crash() from the very text fixtures embed.


def keep_crash_logs(folder, kept_in, prefix):
    """Move every crash log under ``folder`` into ``kept_in`` as ``<prefix>-<file>.crash-log.txt`` (numbered when two
    have one name) and return the new names: the crash's evidence, under a name the suite runner does not read."""
    from pathlib import Path
    kept = []
    for log in sorted(Path(folder).rglob("*.crash.txt")):
        if not log.is_file():
            continue
        stem = "%s-%s" % (prefix, log.name[:-len(".crash.txt")])
        target, number = Path(kept_in) / (stem + ".crash-log.txt"), 1
        while target.exists():
            number += 1
            target = Path(kept_in) / ("%s-%d.crash-log.txt" % (stem, number))
        log.replace(target)
        kept.append(target.name)
    return kept
