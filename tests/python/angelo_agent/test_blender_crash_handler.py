# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The native crash handler on a built Angelo: every crash writes a log and ends the process, never hangs.

Run with Angelo's bundled Python (no window opens; background children only)::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/test_blender_crash_handler.py \\
        --binary <Angelo.app>/Contents/MacOS/Angelo --out <folder ending in .noindex>

Each case starts a REAL ``Angelo --background`` child with its own HOME and temporary folder, makes it crash one
way, and checks that the crash log (``<temp>/angelo.crash.txt``) is non-empty and readable by the add-on's own
reader (``crash_log.parse``: header, native frames of Angelo's executable), and that the child ended by the crash
signal within the handler's watchdog bound. The ways cover the classes that made the 1.0.6 rehearsal's handler
leave a 0-byte log and hang (contract: doc/angelo/updates_and_reports.rst, "The crash handler"):

* ``main_gil``: a fault on the main thread holding the GIL (Python frames expected);
* ``worker_no_gil``: a fault on a worker thread that released the GIL;
* ``fault_in_handler``: the handler itself faults (the crashed thread's Python frame object is corrupted, as after
  memory corruption), so the log must already hold the minimal record;
* ``stdout_locked``: another thread holds ``stdout``'s lock (it was printing when the crash came);
* ``watchdog``: with ``--console-crash-handler``, another thread holds ``stderr``'s lock, so the handler's extras
  block and only the watchdog ends the process; the minimal record is on stderr;
* ``sigbus``: a read past the end of a mapped file (SIGBUS, not SIGSEGV);
* ``stack_overflow``: unbounded recursion on the main thread (the handler needs its alternate stack).

Writes ``crash-handler.json`` (``success`` true only when every case passed) and prints ANGELO_CRASH_HANDLER_OK.
"""

import argparse
import importlib.util
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRASH_LOG_PY = HERE.parents[2] / "scripts" / "addons_core" / "angelo_agent" / "crash_log.py"
# creator_crash_log.hh: CRASH_HANDLER_TIMEOUT_S. A case may take that long (the watchdog) plus scheduling slack on a
# loaded machine; anything longer is a hang.
HANDLER_TIMEOUT_S = 10
END_BOUND_S = HANDLER_TIMEOUT_S + 8
STARTUP_CAP_S = 180   # Until the child says it is about to crash.

PRELUDE = r'''
import ctypes, sys, threading, time
libc = ctypes.CDLL(None)
def stream(name):
    symbol = {"stdout": "__stdoutp", "stderr": "__stderrp"}[name] if sys.platform == "darwin" else name
    return ctypes.c_void_p.in_dll(libc, symbol)
def hold_lock(name):
    locked = threading.Event()
    def run():
        libc.flockfile(stream(name))
        locked.set()
        time.sleep(3600)
    threading.Thread(target=run, daemon=True).start()
    locked.wait(30)
def about_to_crash():
    sys.stdout.write("ABOUT_TO_CRASH\n")
    sys.stdout.flush()
'''

CASES = {
    "main_gil": PRELUDE + r'''
def crash_main_with_gil():
    about_to_crash()
    ctypes.string_at(0)   # PYFUNCTYPE: the GIL stays held.
crash_main_with_gil()
''',
    "worker_no_gil": PRELUDE + r'''
def crash_worker():
    ctypes.memmove(0, b"x", 1)   # CFUNCTYPE: the GIL is released around the call.
worker = threading.Thread(target=crash_worker)
about_to_crash()
worker.start()
worker.join()
''',
    "fault_in_handler": PRELUDE + r'''
import gc
gc.disable()   # A collection would traverse the corrupted frame before the crash.
def crash_with_corrupt_frame():
    frame = sys._getframe()
    # CPython 3.13: PyFrameObject is ob_refcnt, ob_type, f_back, f_frame; the _PyInterpreterFrame f_frame points
    # to starts with f_executable (the code object). Checked first, so a layout change fails the case instead of
    # corrupting something else.
    word = ctypes.sizeof(ctypes.c_void_p)
    f_frame = ctypes.c_void_p.from_address(id(frame) + 3 * word)
    assert ctypes.c_void_p.from_address(f_frame.value).value == id(frame.f_code), "unexpected PyFrameObject layout"
    f_frame.value = 0x10   # The handler's Python walk (PyFrame_GetCode) faults here.
    about_to_crash()
    ctypes.string_at(0)
crash_with_corrupt_frame()
''',
    "stdout_locked": PRELUDE + r'''
hold_lock("stdout")
about_to_crash()
ctypes.string_at(0)
''',
    "watchdog": PRELUDE + r'''
hold_lock("stderr")
about_to_crash()
ctypes.string_at(0)
''',
    "sigbus": PRELUDE + r'''
import mmap, os, tempfile
path = os.path.join(tempfile.gettempdir(), "mapped.bin")
with open(path, "wb") as handle:
    handle.write(b"\0" * 2 * mmap.PAGESIZE)
handle = open(path, "r+b")
mapped = mmap.mmap(handle.fileno(), 2 * mmap.PAGESIZE)
os.truncate(path, 0)
about_to_crash()
mapped[mmap.PAGESIZE]   # Past the end of the file: SIGBUS.
''',
    "stack_overflow": PRELUDE + r'''
sys.setrecursionlimit(10 ** 7)
class Deeper:
    def __repr__(self):
        return repr(Deeper())   # Each level re-enters C (PyObject_Repr), so the native stack grows.
about_to_crash()
repr(Deeper())
''',
}
EXPECTED_SIGNALS = {"sigbus": {signal.SIGBUS},
                    "stack_overflow": {signal.SIGSEGV, signal.SIGBUS}}
CONSOLE_CASES = {"watchdog"}
# The main thread's stack for the overflow case: small enough that the native stack runs out long before
# Python's own C-recursion limit stops the recursion.
OVERFLOW_STACK_BYTES = 2 * 1024 * 1024


def load_crash_log():
    spec = importlib.util.spec_from_file_location("crash_log", CRASH_LOG_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated_env(root):
    home, tmp, state, connections = (root / name for name in ("home", "tmp", "state", "connections"))
    for folder in (home, tmp, state, connections):
        folder.mkdir(parents=True, exist_ok=True)
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(tmp) + "/",
            "TEMP": str(tmp), "TMP": str(tmp), "ANGELO_STATE_DIR": str(state),
            "ANGELO_CONNECTIONS_DIR": str(connections), "ANGELO_DISABLE_KEYCHAIN": "1",
            "ANGELO_DISABLE_DEFAULT_CHAT": "1", "ANGELO_DISABLE_INTRO": "1", "LANG": "en_US.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1"}, tmp


def run_case(binary, name, root):
    """Start the child, wait for its ABOUT_TO_CRASH line, then for its end; kill it if it outlives the bound."""
    environ, tmp = isolated_env(root)
    console = ["--console-crash-handler"] if name in CONSOLE_CASES else []
    # One list: the window-launch scan (test_native_gui_launch_focus) sees it opens no window.
    command = [str(binary), "--background", "--factory-startup", "--disable-autoexec", *console,
               "--python-exit-code", "1", "--python-expr", CASES[name]]

    def limit_stack():
        if name == "stack_overflow":
            hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
            resource.setrlimit(resource.RLIMIT_STACK, (OVERFLOW_STACK_BYTES, hard))

    child = subprocess.Popen(command, env=environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             preexec_fn=limit_stack, start_new_session=True)
    marked, out, err = threading.Event(), [], []
    marked_at = [None]

    def read_stdout():
        for raw in child.stdout:
            out.append(raw)
            if raw.strip() == b"ABOUT_TO_CRASH" and not marked.is_set():
                marked_at[0] = time.monotonic()
                marked.set()

    readers = [threading.Thread(target=read_stdout, daemon=True),
               threading.Thread(target=lambda: err.extend(child.stderr), daemon=True)]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    while not marked.is_set() and child.poll() is None and time.monotonic() - started < STARTUP_CAP_S:
        marked.wait(0.2)
    hung = False
    if marked.is_set():
        try:
            child.wait(timeout=max(0.0, END_BOUND_S - (time.monotonic() - marked_at[0])))
        except subprocess.TimeoutExpired:
            hung = True
    elif child.poll() is None:
        hung = True
    if hung:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    ended = time.monotonic()
    for reader in readers:
        reader.join(5)
    logs = sorted(path for path in tmp.rglob("*.crash.txt") if path.is_file())
    return {"returncode": child.returncode, "hung": hung, "marked": marked.is_set(),
            "end_seconds": round(ended - marked_at[0], 2) if marked_at[0] else None,
            "stdout": b"".join(out).decode("utf-8", "replace"), "stderr": b"".join(err).decode("utf-8", "replace"),
            "logs": logs, "partials": sorted(str(path.name) for path in tmp.rglob("*.crash.txt.partial"))}


def judge(name, result, crash_log):
    """The case's checks, each (name, passed, detail)."""
    expected = EXPECTED_SIGNALS.get(name, {signal.SIGSEGV})
    checks = [("reached the crash", result["marked"], result["stdout"][-2000:] + result["stderr"][-2000:]),
              ("ended without hanging", not result["hung"], result["end_seconds"]),
              ("ended within %d s" % END_BOUND_S,
               result["end_seconds"] is not None and result["end_seconds"] <= END_BOUND_S, result["end_seconds"]),
              ("ended by the crash signal", -result["returncode"] in expected, result["returncode"])]
    if name in CONSOLE_CASES:
        # The record starts at its header line; the child's own start-up output comes before it.
        text = result["stderr"]
        text = text[text.find("# Blender "):] if "# Blender " in text else text
        checks.append(("no log file in console mode", not result["logs"], [str(p) for p in result["logs"]]))
    else:
        text = result["logs"][0].read_text(encoding="utf-8", errors="replace") if result["logs"] else ""
        checks.append(("one non-empty crash log", len(result["logs"]) == 1 and bool(text.strip()),
                       [str(p) for p in result["logs"]]))
    parsed = crash_log.parse(text)
    checks += [("header parsed", bool(parsed.build), text[:300]),
               ("signal line written", any("# Signal %d (" % number in text for number in expected), text[:600]),
               ("native frames read", len(parsed.native) >= 3, len(parsed.native)),
               ("frames name Angelo's executable", parsed.owned_by_angelo(), [f["image"] for f in parsed.native][:8])]
    crashed_function = {"main_gil": "crash_main_with_gil"}.get(name)
    if crashed_function:
        checks.append(("Python frames of the crashed function",
                       any(frame["function"] == crashed_function for frame in parsed.python), parsed.python))
    if name == "fault_in_handler":
        # The full record started (its side file is there) and faulted in the Python walk; the log is the minimal one.
        checks.append(("the minimal record survived the handler's own fault",
                       bool(result["partials"]) and "# Python backtrace" not in text,
                       (result["partials"], text[-400:])))
    elif name not in CONSOLE_CASES:
        checks.append(("the full record replaced the minimal one", not result["partials"], result["partials"]))
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--case", action="append", choices=sorted(CASES), help="run only these cases")
    options = parser.parse_args()
    binary = Path(options.binary).resolve()
    out = Path(options.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    crash_log = load_crash_log()
    report = {"success": False, "binary": str(binary), "cases": {}, "checks": {}}
    root = Path(tempfile.mkdtemp(prefix="crash-handler-", dir="/private/tmp" if sys.platform == "darwin" else None))
    try:
        for name in options.case or list(CASES):
            result = run_case(binary, name, root / name)
            checks = judge(name, result, crash_log)
            for check, passed, detail in checks:
                report["checks"]["%s: %s" % (name, check)] = bool(passed)
                print(("PASS " if passed else "FAIL ") + name + ": " + check +
                      ("" if passed else " (%s)" % repr(detail)[:600]), flush=True)
            kept = out / name
            kept.mkdir(exist_ok=True)
            # Kept under another name: the suite runner fails a run that leaves a ``*.crash.txt`` in its folder.
            for index, log in enumerate(result["logs"]):
                shutil.copy2(log, kept / ("crash-log-%d.txt" % index))
            (kept / "stderr.txt").write_text(result["stderr"], encoding="utf-8")
            report["cases"][name] = {"returncode": result["returncode"], "hung": result["hung"],
                                     "end_seconds": result["end_seconds"], "partials": result["partials"],
                                     "passed": all(passed for _check, passed, _detail in checks)}
        report["success"] = bool(report["cases"]) and all(case["passed"] for case in report["cases"].values())
    finally:
        shutil.rmtree(root, ignore_errors=True)
        (out / "crash-handler.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["success"]:
        print("ANGELO_CRASH_HANDLER_OK")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
