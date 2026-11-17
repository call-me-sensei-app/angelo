# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The crash reports macOS wrote for a suite's own Angelo or Blender processes, kept with the suite (stdlib only).

macOS saves at most 25 reports of one process in ``~/Library/Logs/DiagnosticReports``; past that, osanalyticshelper
drops each new one ("Log limit exceeded"). Suites that crash the app on purpose (the crash handler, crash cards,
settings import) filled it, so the one real crash of 1.1.0's cut 08e2b0f7842 (test_blender_prefs_and_quit's
quit-open, 2026-11-13: a launch that died 0.9 s after restoring its windows, before Angelo's own crash handler
wrote anything) left no report at all. After each suite, ``take`` moves every report written since the suite began
for a process a test launched (never the person's own app, which launchd starts) into the suite's folder: none
counts against the limit, every one stays as evidence beside the suite that made it, and the suite's log says what
crashed (``summary``: the exception, the termination reason, the crashed thread's frames).
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

NAMES = ("Angelo-", "Blender-")   # The app and stock Blender (benchmarks, the stock side of a comparison).
FRAMES = 24


def folder():
    """The person's report folder. A suite's HOME is a sandbox, so it comes from the account, never from HOME."""
    if sys.platform != "darwin":
        return None
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "Logs" / "DiagnosticReports"


def read(path):
    """``(header, body)`` of an ``.ips`` file (a JSON line, then a JSON document); ``(None, None)`` if unreadable."""
    try:
        first, rest = Path(path).read_text(encoding="utf-8", errors="replace").split("\n", 1)
        return json.loads(first), json.loads(rest)
    except (OSError, ValueError):
        return None, None


def summary(header, body, frames=FRAMES):
    """What crashed, in a few lines: the process, its exception and termination, the crashed thread's frames."""
    exception, termination = body.get("exception") or {}, body.get("termination") or {}
    lines = ["%s pid %s (parent %s) at %s, app %s: %s %s%s; termination %s %s" % (
        body.get("procName") or header.get("app_name"), body.get("pid"), body.get("parentProc"),
        header.get("timestamp"), header.get("app_version"), exception.get("type"), exception.get("signal") or "",
        (" " + exception["subtype"]) if exception.get("subtype") else "", termination.get("namespace"),
        termination.get("indicator") or termination.get("code"))]
    if body.get("asi"):
        lines.append("  application specific: %s" % json.dumps(body["asi"])[:600])
    threads, images = body.get("threads") or [], body.get("usedImages") or []
    index = body.get("faultingThread", 0)
    if 0 <= index < len(threads):
        thread = threads[index]
        lines.append("  crashed thread %d %s %s" % (index, thread.get("name") or "", thread.get("queue") or ""))
        for frame in thread.get("frames", [])[:frames]:
            image = images[frame["imageIndex"]] if 0 <= frame.get("imageIndex", -1) < len(images) else {}
            symbol = frame.get("symbol", "0x%x" % frame.get("imageOffset", 0))
            source = " (%s:%s)" % (frame["sourceFile"], frame.get("sourceLine")) if frame.get("sourceFile") else ""
            lines.append("    %s %s%s" % (image.get("name", "?"), symbol, source))
    return "\n".join(lines)


def take(since, dest, *, wait=0.0, reports=None, clock=time.time, sleep=time.sleep):
    """Move the reports written at or after ``since`` for a test's Angelo or Blender into ``dest``; their summaries.
    ``wait``: seconds to keep looking for one still being written (ReportCrash saves a moment after the death)."""
    reports = folder() if reports is None else Path(reports)
    if reports is None or not reports.is_dir():
        return []
    taken, deadline = [], clock() + wait
    while True:
        for path in sorted(reports.glob("*.ips")):
            if not path.name.startswith(NAMES):
                continue
            try:
                if path.stat().st_mtime < since - 1.0:
                    continue
            except OSError:
                continue
            header, body = read(path)
            if body is None or body.get("parentProc") == "launchd":   # The person's own app: theirs, left alone.
                continue
            Path(dest).mkdir(parents=True, exist_ok=True)
            target = Path(dest) / path.name
            try:
                shutil.move(str(path), str(target))
            except OSError:
                continue
            taken.append({"report": str(target), "pid": body.get("pid"), "summary": summary(header, body)})
        if taken or clock() >= deadline:
            return taken
        sleep(0.5)
