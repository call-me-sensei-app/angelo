# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Failure evidence for native suites: process status, logs and JSON receipts.

Pure functions, no Blender. A zero exit status is never sufficient on its own:
GUI suites quit normally after a failure, and the development preset builds
with ``WITH_ASSERT_RELEASE=ON`` and ``WITH_ASSERT_ABORT=OFF`` (via
``blender_lite.cmake`` and ``build_native.sh``), so a failed ``BLI_assert``
only prints and the process continues. The full-feature build
(``build_release_macos.sh``, ``blender_release.cmake``) compiles ``BLI_assert``
out like an official release, so the ``native_assertion`` markers can only fire
on the development build: run the suites there too for assertion coverage.
"""

import json
from pathlib import Path
import re
import signal

# Exact strings printed by Blender and the C runtime. Sources:
#   source/blender/blenlib/intern/BLI_assert.cc  (BLI_assert, unreachable code)
#   intern/guardedalloc/intern/leak_detector.cc  (leak report at exit)
#   intern/guardedalloc/intern/mallocn_*_impl.cc (allocator errors)
#   source/creator/creator_signals.cc            (crash log "Writing: <path>")
#   source/blender/python/intern/bpy_interface.cc (unbalanced Python call level)
#   macOS/glibc assert(3)                        (C/C++ asserts in libraries)
NATIVE_MARKERS = (
    ("native_assertion", re.compile(r"BLI_assert failed: .*")),
    ("native_assertion", re.compile(r"Code marked as unreachable has been executed.*")),
    ("native_assertion", re.compile(r"(?m)^Assertion failed: .*")),
    ("native_assertion", re.compile(r"(?m)^.*: Assertion `.*' failed\.$")),
    ("allocator_leak", re.compile(r"Error: Not freed memory blocks: \d+.*")),
    ("allocator_leak", re.compile(r"Freeing memory after the leak detector has run.*")),
    ("allocator_error", re.compile(r"(?m)^Memoryblock .*")),
    ("allocator_error", re.compile(r"(?:Malloc|Calloc|aligned_malloc) returns null: .*")),
    ("crash", re.compile(r"(?m)^Writing: .*\.crash\.txt$")),
    ("crash", re.compile(r"(?m)^Fatal Python error: .*")),
    # The main thread no longer sets bpy.context for the rest of the session (doc/angelo/long_jobs.rst).
    ("python_context_state", re.compile(r"ERROR: Python context internal state bug.*")),
)
TRACEBACK_HEADER = "Traceback (most recent call last):"
MAX_EVIDENCE = 5


def scan_log(text):
    """Return failure findings ``{"kind", "line"}`` in a suite's combined output.

    Python tracebacks are only counted when they start a line; a traceback
    embedded in a printed JSON receipt belongs to an already failed receipt.
    Blender operator reports ("Error: ...") are not failures: suites provoke
    them deliberately and assert on them.
    """
    findings = []
    for kind, pattern in NATIVE_MARKERS:
        for match in pattern.finditer(text):
            findings.append({"kind": kind, "line": match.group(0).strip()[:300]})
    for exception in traceback_exceptions(text):
        findings.append({"kind": "python_traceback", "line": exception[:300]})
    return findings


def traceback_exceptions(text):
    """Exception line ending each line-initial Python traceback in ``text``.

    Frames and source excerpts are indented; the first following unindented
    line is the exception. Chained tracebacks are reported individually.
    """
    lines = text.splitlines()
    results, index = [], 0
    while index < len(lines):
        if lines[index].rstrip() != TRACEBACK_HEADER:
            index += 1
            continue
        index += 1
        exception = "<truncated traceback>"
        while index < len(lines):
            line = lines[index]
            if line.strip() and not line[0].isspace():
                exception = line.strip()
                break
            index += 1
        results.append(exception)
    return results


def find_crash_logs(directory):
    """The app writes ``<tempdir>/<blend>.crash.txt`` (``angelo.crash.txt`` without a file) when it crashes."""
    return sorted(str(path) for path in Path(directory).rglob("*.crash.txt"))


CRASH_TAIL = 60   # Lines of each crash log copied into the suite's log and the run's summary.


def scrub_home(text, home=None):
    """``text`` with the runner's home folder shown as ``~`` (crash reports leave the machine)."""
    home = str(Path.home()) if home is None else home
    return text.replace(home, "~") if len(home) > 3 else text   # Never a bare drive or "/".


def crash_tail(path, lines=CRASH_TAIL):
    """A crash log's last ``lines`` lines, home folder scrubbed."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return "<unreadable: %s>" % error
    return scrub_home("\n".join(text.splitlines()[-lines:]))


def crash_logs(directory, log_path, exit_code, tails):
    """A reason for every crash log under ``directory``; each log's tail goes into ``tails`` and is appended to
    ``log_path`` (the suite's log, the file a remote run brings back). The suite's own process ending with status 0
    means a process it started crashed (a background writer shares its temporary folder), and the reason says so."""
    raw = find_crash_logs(directory)
    found = [scrub_home(path) for path in raw]
    tails.update((shown, crash_tail(path)) for shown, path in zip(found, raw))
    child = " (the suite's own process exited 0: a process it started crashed)" if exit_code == 0 else ""
    if found and log_path is not None:
        with open(log_path, "a", encoding="utf-8") as log:
            for path in found:
                tail = tails[path]
                log.write("\n==== crash log %s, last %d lines%s ====\n%s\n" % (path, CRASH_TAIL, child, tail))
    return ["crash log written: " + path + child for path in found]


# Windows has no signals: an unhandled exception ends the process with its NTSTATUS error code
# (0xC0000005 is an access violation), a range no ordinary exit status uses.
NTSTATUS_ERROR = 0xC0000000


def describe_exit(code):
    """Classify a subprocess return code: ``(status, reason)``; status None means success."""
    if code == 0:
        return None, None
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = "signal %d" % -code
        return "crashed", "terminated by " + name
    if code >= NTSTATUS_ERROR:
        return "crashed", "terminated by NTSTATUS 0x%08X" % code
    return "failed", "exit status %d" % code


def validate_report(spec, path, started_at):
    """Reasons a suite's JSON receipt does not prove success (empty when it does)."""
    path = Path(path)
    if not path.is_file():
        return ["report missing: " + str(path)]
    if path.stat().st_mtime < started_at - 1:
        return ["report is older than this run: " + str(path)]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return ["report unreadable: %s (%s)" % (path, error)]
    if not isinstance(data, dict):
        return ["report is not a JSON object"]
    reasons = []
    if spec.success_key is not None and spec.success_key not in data:
        reasons.append("report has no %r field (it has %s): the suite must write it" % (
            spec.success_key, ", ".join(sorted(map(str, data))) or "no fields"))
    elif spec.success_key is not None and data.get(spec.success_key) is not True:
        reasons.append("report %s=%r, expected true" % (spec.success_key, data.get(spec.success_key)))
    if spec.require_complete_phase and data.get("phase") != "complete":
        reasons.append("report phase=%r, expected 'complete'" % data.get("phase"))
    reasons += _check_reasons(data.get("checks"), named_checks_required=spec.success_key is None)
    for key in ("paid_requests", "billable_requests"):
        if key in data and (type(data[key]) is not int or data[key] != 0):
            reasons.append("report %s=%r, expected 0" % (key, data[key]))
    if data.get("provider_started") is True:
        reasons.append("report says a provider was started")
    if isinstance(data.get("failures"), dict) and data["failures"]:
        reasons.append("report failures: " + ", ".join(sorted(data["failures"])[:MAX_EVIDENCE]))
    if data.get("error"):
        reasons.append("report error: " + _last_line(str(data["error"])))
    return reasons


def _check_reasons(checks, named_checks_required):
    if isinstance(checks, dict):
        failed = [name for name, passed in checks.items() if passed is not True]
        if failed:
            return ["failed checks: " + ", ".join(failed[:MAX_EVIDENCE])]
        if not checks and named_checks_required:
            return ["report has no checks"]
        return []
    if isinstance(checks, list) and checks and all(isinstance(item, dict) for item in checks):
        failed = [str(item.get("name")) for item in checks if item.get("passed") is not True]
        return ["failed checks: " + ", ".join(failed[:MAX_EVIDENCE])] if failed else []
    if named_checks_required:
        return ["report has no named checks"]
    return []


def _last_line(text):
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return (lines[-1] if lines else text)[:300]
