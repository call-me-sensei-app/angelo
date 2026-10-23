# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The runtime's own trace rows for a live-eval turn that did not end normally, kept beside its trial.

Codex keeps a TRACE-level log database in its profile (``CODEX_HOME/logs_2.sqlite``, or under ``state/``;
codex-rs ``state``, table ``logs``: ``ts``, ``ts_nanos``, ``level``, ``target``, ``feedback_log_body`` (``message``
before that migration), ``thread_id``, ...). It holds the model stream's transport events with their times, which
neither the rollout (Codex drops transient stream errors and an aborted turn's partial output) nor the receipt has,
so it tells a silent stream from a runaway one. After each trial (``after_trial``, from ``eval_live_agent.run_trial``,
so the Mac harness and the Windows VM bench alike) every ``abnormal_end`` block (``eval_abnormal_end.blocks``) on
a dedicated Codex profile gets that turn's rows: its thread's and the app-server's threadless ones, from 1 s before
the send to 2 s after the turn's end, read only, into ``TRACE_FILE`` in the trial folder, one JSON object per row
naming its task. Bounded: at most ``MAX_ROWS`` rows and ``MAX_BYTES`` per turn (the first quarter and the last
three quarters of the window when it holds more, with the count omitted between them), bodies cut at
``MAX_BODY`` characters, ``MAX_FILE_BYTES`` per trial; credential-shaped text is redacted. The block's ``trace``
says what was exported.

Claude Code keeps no trace log of its own unless it is started with ``--debug``, which Angelo never does; its
block says so, and its transcript (which the bench copies beside the trial) is its record. A connection that
reuses the person's own native sign-in is never read: only a dedicated profile (``--codex-home`` or the
connection's own profile directory) is.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

import eval_abnormal_end as abnormal

TRACE_FILE = "codex-trace.jsonl"
MAX_ROWS, MAX_BYTES, MAX_BODY, MAX_FILE_BYTES = 4000, 256 * 1024, 2000, 1024 * 1024
HEAD_SHARE = 0.25
BEFORE_S, AFTER_S = 1.0, 2.0
CLAUDE_NOTE = ("Claude Code keeps no trace log unless started with --debug (Angelo never is); its transcript is "
               "the record")
NO_PROFILE = "no dedicated Codex profile for this connection: nothing was read"
SECRETS = (   # A named secret's value first (with its scheme), then bare tokens anywhere.
    (re.compile(r"(?i)((?:authorization|set-cookie|cookie|x-api-key|api[_-]?key|access_token|refresh_token|id_token"
                r"|client_secret|password|chatgpt-account-id)[\"']?\s*[:=]\s*)(?:(?:bearer|basic)\s+)?"
                r"(\"[^\"]*\"|'[^']*'|[^\s,;}]+)"), r"\1[redacted]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), "[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted]"),
)


def redact(text):
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def codex_home(accounts, args):
    """The run's dedicated Codex profile (``CODEX_HOME``), or None: ``--codex-home``, else the connection's own."""
    if getattr(args, "codex_home", ""):
        return Path(args.codex_home)
    try:
        from angelo_agent.connections import ConnectionStore
        record = ConnectionStore(Path(accounts)).records.get(getattr(args, "connection_id", "")) or {}
    except Exception:
        return None
    profile = record.get("native_profile")
    dedicated = profile and not record.get("reuse_native") and str(record.get("mode", "")).startswith("codex_")
    return Path(profile) if dedicated else None


def trace_database(home):
    """The newest ``logs_N.sqlite`` in the profile or its ``state`` folder, or None."""
    found = []
    for folder in (Path(home), Path(home) / "state"):
        for path in sorted(folder.glob("logs_*.sqlite")) if folder.is_dir() else ():
            version = path.stem.split("_", 1)[1]
            if version.isdigit():
                found.append((int(version), str(path)))
    return Path(max(found)[1]) if found else None


def _line(task, row):
    ts, nanos, level, target, body, place, thread = row[1:]
    stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".%09dZ" % (nanos or 0)
    body = redact(str(body or ""))
    entry = {"task": task, "t": stamp, "t_unix": round(ts + (nanos or 0) / 1e9, 6), "level": level, "target": target,
             "thread": thread, "at": place, "body": body[:MAX_BODY]}
    if len(body) > MAX_BODY:
        entry["body_cut"] = len(body) - MAX_BODY
    return json.dumps(entry, ensure_ascii=False) + "\n"


def turn_rows(database, task, thread, start, end):
    """``(lines, facts)``: the turn's rows inside ``[start, end]`` (unix seconds), bounded as the module says."""
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(logs)")}
        body = next((name for name in ("feedback_log_body", "message") if name in columns), "NULL")
        place = "file || ':' || line" if {"file", "line"} <= columns else "NULL"
        where = "ts BETWEEN ? AND ? AND (ts + ts_nanos * 1e-9) BETWEEN ? AND ?"
        params = [int(start), int(end) + 1, start, end]
        if thread and "thread_id" in columns:
            where, params = where + " AND (thread_id = ? OR thread_id IS NULL)", params + [thread]
        select = ("SELECT id, ts, ts_nanos, level, target, %s, %s, %s FROM logs WHERE %s"
                  % (body, place, "thread_id" if "thread_id" in columns else "NULL", where))
        total = connection.execute("SELECT COUNT(*) FROM logs WHERE " + where, params).fetchone()[0]
        head = connection.execute(select + " ORDER BY ts, ts_nanos, id LIMIT ?", params + [MAX_ROWS]).fetchall()
        tail = connection.execute(select + " ORDER BY ts DESC, ts_nanos DESC, id DESC LIMIT ?",
                                  params + [MAX_ROWS]).fetchall()
    finally:
        connection.close()
    lines = [_line(task, row) for row in head]
    if total <= MAX_ROWS and sum(len(line.encode("utf-8")) for line in lines) <= MAX_BYTES:
        return lines, {"rows": len(lines), "rows_in_window": total, "truncated": False}
    kept, used, ids = [], 0, set()
    for row, line in zip(head, lines):   # The window's start: what the turn began with.
        size = len(line.encode("utf-8"))
        if used + size > MAX_BYTES * HEAD_SHARE or len(kept) >= MAX_ROWS * HEAD_SHARE:
            break
        kept.append(line)
        ids.add(row[0])
        used += size
    ending = []
    for row in tail:   # Its end: what the turn was doing when it stopped.
        line = _line(task, row)
        size = len(line.encode("utf-8"))
        if row[0] in ids or used + size > MAX_BYTES or len(kept) + len(ending) >= MAX_ROWS:
            break
        ending.append(line)
        used += size
    omitted = total - len(kept) - len(ending)
    marker = [json.dumps({"task": task, "omitted_rows": omitted}) + "\n"] if omitted > 0 else []
    return kept + marker + ending[::-1], {"rows": len(kept) + len(ending), "rows_in_window": total,
                                          "truncated": omitted > 0, "omitted_rows": max(omitted, 0)}


def export(folder, report, home, mode=""):
    """Each abnormal block's trace rows into ``folder/TRACE_FILE``; the block's ``trace`` says what was kept."""
    found = abnormal.blocks(report)
    if not found:
        return report
    database = trace_database(home) if home is not None else None
    if database is None:
        note = (CLAUDE_NOTE if mode.startswith("claude_") else NO_PROFILE if home is None
                else "no Codex trace database in the profile")
        for _, block in found:
            block["trace"] = {"rows": 0, "note": note}
        return report
    target, written = Path(folder) / TRACE_FILE, 0
    for task, block in found:
        sent, ended = block.get("sent_at_unix"), block.get("ended_at_unix") or block.get("captured_at_unix")
        if sent is None or ended is None:
            block["trace"] = {"rows": 0, "note": "the turn's window is unknown"}
            continue
        if written >= MAX_FILE_BYTES:
            block["trace"] = {"rows": 0, "note": "the trial's trace budget was used by earlier turns"}
            continue
        thread = (block.get("last_provider_event") or {}).get("session")
        try:
            lines, facts = turn_rows(database, task, thread, sent - BEFORE_S, ended + AFTER_S)
        except sqlite3.Error as error:
            block["trace"] = {"rows": 0, "error": "%s: %s" % (type(error).__name__, error)}
            continue
        data = [line.encode("utf-8") for line in lines]
        while data and written + sum(map(len, data)) > MAX_FILE_BYTES:   # Whole rows only.
            data.pop()
            facts.update(truncated=True, file_budget_cut=facts.get("file_budget_cut", 0) + 1)
        if data:
            with target.open("ab") as out:
                out.write(b"".join(data))
        written += sum(map(len, data))
        block["trace"] = dict(facts, file=TRACE_FILE, database=database.name, thread=thread,
                              window_unix=[round(sent - BEFORE_S, 3), round(ended + AFTER_S, 3)])
    return report


def after_trial(folder, report, exit_code, accounts, args):
    """The launcher's step after a trial's app exited: a task left in flight, then every abnormal turn's trace."""
    report = abnormal.collect(folder, report, exit_code)
    try:
        return export(folder, report, codex_home(accounts, args), str(report.get("mode") or ""))
    except Exception as error:   # Evidence only: never loses the trial's report.
        report["trace_export_error"] = "%s: %s" % (type(error).__name__, error)
        return report
