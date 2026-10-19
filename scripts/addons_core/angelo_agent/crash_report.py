# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The crash report: what it contains, the exact text that is sent, and the crash waiting for a decision.

Contract: doc/angelo/updates_and_reports.rst. A report is **the stack and the
environment, nothing else**: the build, the operating system and CPU, the
graphics card, the native and Python stack frames (``crash_log``), and a random
report id. It never holds a scene, a prompt, a chat, a file name or path
outside the Angelo installation, an account or a key, and no identifier of
the person or computer. The dialog shows ``render(document)``; the upload sends
``body(document)``, the very same text encoded, so what the person read is what
leaves (a test pins that).

Standard library only; no bpy (the GPU and Blender version are passed in).
"""

import json
import platform
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from . import app_version, crash_log, crash_state

SCHEMA = "angelo.crash-report.v1"
MAX_BODY = 64 * 1024   # The server reads at most 65,536 bytes and answers 413 (never retried) beyond it.
_PRINTABLE = re.compile(r"[^A-Za-z0-9 ._,;:+()\[\]/\-@#=<>*]")


def clean(value, limit=120):
    """Short printable ASCII text for environment fields (an odd character becomes ``?``)."""
    return _PRINTABLE.sub("?", str(value or ""))[:limit].strip()


def system_info(gpu=None, system=None, machine=None, release=None):
    """The operating system, CPU architecture and graphics card. ``gpu`` is ``(vendor, renderer, version, backend)``."""
    name = system or platform.system()
    if name == "Darwin":
        name, version = "macOS", release or platform.mac_ver()[0]
    elif name == "Windows":
        name, version = "Windows", release or platform.version()
    else:
        name, version = name or "Unknown", release or platform.release()
    vendor, renderer, driver, backend = (list(gpu) + [""] * 4)[:4] if gpu else ("", "", "", "")
    return {"os": clean(name, 24), "os_version": clean(version, 40), "arch": clean(machine or platform.machine(), 24),
            "gpu": {"vendor": clean(vendor, 60), "renderer": clean(renderer, 120), "version": clean(driver, 120),
                    "backend": clean(backend, 24)}}


def app_info(blender_base="", build_hash="", build_date=""):
    return {"name": "Angelo", "version": clean(app_version.current(), 40), "blender_base": clean(blender_base, 24),
            "build_hash": clean(build_hash, 40), "build_date": clean(build_date, 24)}


def build_document(parsed, system, app, crashed_ns, now, report_id=None):
    """The report for a parsed crash log. ``crashed_ns`` is the log's modification time; it is rounded to the minute."""
    crashed = datetime.fromtimestamp(crashed_ns / 1e9, timezone.utc).replace(second=0, microsecond=0)
    build = parsed.build
    document = {
        "schema": SCHEMA, "report_id": report_id or str(uuid.uuid4()), "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crashed_at": crashed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": app,
        "crashed_build": {"blender_version": clean(build.get("version")), "commit_date": clean(
            (build.get("date", "") + " " + build.get("time", "")).strip(), 24), "hash": clean(build.get("hash"), 40),
            "same_build": bool(build.get("hash")) and build.get("hash") == app.get("build_hash")},
        "system": system,
        "crash": {"exception": parsed.exception or None, "native_stack": parsed.native, "native_threads": parsed.threads,
                  "python_stack": parsed.python, "loaded_modules": parsed.modules},
        "omitted_log_lines": parsed.dropped,
    }
    return _fit(document)


def _fit(document):
    """Shrink the stacks until the text is within ``MAX_BODY`` (a report is a stack, never a dump)."""
    crash = document["crash"]
    for key in ("loaded_modules", "native_threads", "python_stack", "native_stack"):
        while len(render(document)) > MAX_BODY and crash[key]:
            crash[key] = crash[key][:len(crash[key]) // 2]
    return document


def render(document):
    """The report's text: deterministic, ASCII, indented so a person can read it."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True)


def body(document):
    """The bytes the upload sends: ``render(document)``, encoded, and nothing else."""
    return render(document).encode("ascii")


def default_file_name(document):
    return "Angelo-crash-report-%s.json" % document["crashed_at"][:10]


@dataclass(frozen=True)
class Pending:
    """One crash waiting for a decision."""

    fingerprint: str
    name: str          # The crash log's file name (shown as "angelo.crash.txt"; never a path).
    mtime_ns: int
    parsed: crash_log.ParsedLog


def find_pending(temp_base, state, now, roots, limit=6):
    """``(newest unhandled Angelo crash or None, [(fingerprint, status) to record])``.

    A log that is not provably Angelo's (a stock Blender shares the folder, or no frame names Angelo's executable) is
    left alone and never recorded; older unhandled Angelo logs are recorded ``superseded`` (one question per launch,
    about the latest crash).
    """
    pending, records = None, []
    for path, mtime_ns, size in crash_log.crash_files(temp_base, now)[:limit]:
        key = crash_state.fingerprint(path.name, mtime_ns, size)
        if state.is_handled(key):
            continue
        parsed = crash_log.parse(crash_log.read(path), roots)
        if not parsed.owned_by_angelo():
            continue
        if pending is None:
            pending = Pending(key, path.name, mtime_ns, parsed)
        else:
            records.append((key, crash_state.SUPERSEDED))
    return pending, records
