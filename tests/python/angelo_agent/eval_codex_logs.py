# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex's own runtime log as provider-limit evidence: the throttles it retried without telling its client.

Codex retries a throttled Responses request itself (``codex_core::responses_retry``) and, in release builds on
websockets, reports only the retries after the first to its client: the first is in its log alone. That
throttle is the same limit evidence as one a client is told of (``eval_provider_limits``: a 429 Codex retried itself,
``willRetry``), so every Codex route reads the log after each trial, by one rule:

* every ``logs_*.sqlite`` of the trial's Codex homes is copied (with its ``-wal`` and ``-shm``) into the trial's
  evidence folder after its runtime has exited, and only the copy is read (reading folds the copied ``-wal`` into
  the copy, which then holds every row on its own);
* only the trial's own rows count: its window (from the trial's start, warm-up included, to its runtime's exit), and
  its own threads (a thread its client named, or else one first seen in the window: a thread of a run that began
  before the trial is another run's, as in an API-key connection's log, which every run on that connection shares);
* a ``WARN`` row of ``codex_core::responses_retry`` among those whose error field (``sampling_error=`` or
  ``compact_error=``) names a limit (``eval_provider_limits.limit_text``: HTTP 429, too many requests, a rate or
  usage limit, a quota) is limit evidence: the trial is invalid (``rate_limited``), exactly as a retried 429 its
  client saw. A retry for another reason (a 5xx, an overloaded server, a dropped stream) never counts, as everywhere
  in the rule;
* no log, an empty or unreadable log, or one of the trial's threads at Codex's per-thread retention (``KEEP_ROWS``
  rows or ``KEEP_BYTES``, oldest dropped first, so its early rows may be gone) is no evidence: the trial is never
  invalidated by what is missing, and its record marks it ``unverifiable`` with the reason, counted per side in the
  bench output (``unverifiable_counts``) so different detection rates stay visible;
* every copy is scrubbed of credential-shaped values (``SECRETS``: tokens, keys, cookies, authorization values,
  e-mail addresses) and rebuilt (``VACUUM``) before it is kept, then checked; a copy that still holds one is deleted.

Standard library only, no Blender.
"""

import re
import shutil
import sqlite3
import time
from pathlib import Path

import eval_provider_limits as limits

RETRY_TARGET = "codex_core::responses_retry"   # Codex's responses_retry.rs log_retry.
ERROR_FIELD = re.compile(r"\b(?:sampling|compact)_error=(?P<error>.*)\Z", re.DOTALL)
LOG_GLOBS = ("logs_*.sqlite", "log/logs_*.sqlite")   # Codex's log_dir, or its sqlite home (earlier versions).
SIDECARS = ("-wal", "-shm")
FOLDER = "codex-logs"   # In the trial's folder: the copies, read and kept as the trial's evidence.
KEEP_ROWS, KEEP_BYTES = 1000, 10 * 1024 * 1024   # Codex's log retention per thread partition (Codex 0.171).
REDACTED = "[redacted]"
# Credential-shaped values Codex's TRACE rows can carry (request and response headers, provider settings).
SECRETS = (
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?"), REDACTED),   # A JWT.
    (re.compile(r"(?i)\bbearer\s+(?!\[redacted\])[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),                       # An OpenAI-style key.
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),                       # A Google API key.
    (re.compile(r"(?i)(\b(?:set-)?cookie\\?\"?\s*[:=]\s*\\?\"?)(?!\[redacted\])[^\"\\\s][^\"\\\n]*"), r"\g<1>" + REDACTED),
    (re.compile(r"(?i)(\b(?:authorization|proxy-authorization|x-api-key|api[_-]?key|x-goog-api-key|"
                r"(?:access|refresh|id|session)[_-]?token|client[_-]?secret|password)\\?\"?\s*[:=]\s*\\?\"?)"
                r"(?!\[redacted\])[^\"\\\s,}\]]+"), r"\g<1>" + REDACTED),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED),   # An account's e-mail address.
)


def codex_runtime(mode):
    """Whether a connection of ``mode`` runs on the Codex runtime (``provider_registry.runtime_kind``)."""
    from angelo_agent import provider_registry
    if not mode:
        return False
    try:
        return provider_registry.runtime_kind(mode) == "codex"
    except (KeyError, ValueError, TypeError):
        return False


def home_logs(home):
    """The log databases of one Codex home (its sqlite home or log folder), oldest name first."""
    home = Path(home)
    return sorted({path for pattern in LOG_GLOBS for path in home.glob(pattern) if path.is_file()}) if home.is_dir() else []


def angelo_homes(roots, names):
    """Angelo's Codex state folders ``<root>/codex-state/<name>`` for each of ``names`` (the folder Angelo's runtime
    uses, ``runtime_launch``: its connection id, or its mode when it has none) under each of ``roots``."""
    seen, homes = set(), []
    for root in roots:
        for name in names:
            if not root or not name:
                continue
            home = (Path(root) / "codex-state" / name).resolve()
            if home not in seen:
                seen.add(home)
                homes.append(home)
    return homes


def copy_log(path, target):
    """``path`` with its ``-wal`` and ``-shm`` copied into the folder ``target``; the copied database's path."""
    target.mkdir(parents=True, exist_ok=True)
    copied = target / path.name
    shutil.copy2(path, copied)
    for suffix in SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, target / sidecar.name)
    return copied


def _stamp(ts, nanos):
    return ts + (nanos or 0) / 1e9


def _own(connection, key, column, start, threads):
    """Whether the thread (or, for a row without one, the process) ``key`` is the trial's own."""
    if column == "thread_id" and threads is not None:
        return key in threads
    first = connection.execute("SELECT ts, ts_nanos FROM logs WHERE %s = ? ORDER BY ts, ts_nanos LIMIT 1" % column,
                               (key,)).fetchone()
    return first is not None and _stamp(*first) >= start


def read_log(path, start, end, threads=None):
    """``(limit rows, notes, possibly pruned threads)`` of one copied log for the window ``start``..``end`` (epoch
    seconds); ``threads``: the trial's own thread ids when its client named them, else None (first seen in the
    window)."""
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as error:
        return [], ["unreadable: %s" % error], []
    found, notes, pruned = [], [], []
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'logs'").fetchone():
            return [], ["no logs table"], []
        if connection.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0:
            return [], ["empty"], []
        rows = connection.execute(
            "SELECT ts, ts_nanos, level, target, feedback_log_body, thread_id, process_uuid FROM logs "
            "WHERE ts BETWEEN ? AND ?", (int(start) - 1, int(end) + 1)).fetchall()
        mine, decided = set(), {}
        for ts, nanos, level, target, body, thread, process in rows:
            stamp = _stamp(ts, nanos)
            key, column = (thread, "thread_id") if thread else (process, "process_uuid")
            if not start <= stamp <= end or key is None:
                continue
            if (column, key) not in decided:
                decided[column, key] = _own(connection, key, column, start, threads)
            if not decided[column, key]:
                continue
            if thread:
                mine.add(thread)
            match = ERROR_FIELD.search(body or "") if level == "WARN" and target == RETRY_TARGET else None
            error = match and " ".join(match.group("error").split())
            if error and limits.limit_text(error):
                what = " ".join(body[body.rfind("}") + 1:match.start()].split()).lstrip(": ")
                found.append({"at": round(stamp, 3), "thread": thread, "error": error[:300], "retry": what[:200]})
        for thread in sorted(mine):
            count, size = connection.execute("SELECT COUNT(*), COALESCE(SUM(estimated_bytes), 0) FROM logs "
                                             "WHERE thread_id = ?", (thread,)).fetchone()
            if count >= KEEP_ROWS or size >= KEEP_BYTES:
                pruned.append({"thread": thread, "rows": count, "bytes": size})
        if not mine:
            notes.append("no row of the trial's own threads in its window")
    except sqlite3.Error as error:
        notes.append("unreadable: %s" % error)
    finally:
        connection.close()
    return found, notes, pruned


def redact(text):
    """``text`` with every credential-shaped value (``SECRETS``) replaced."""
    if not isinstance(text, str):
        return text
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def scrub(path):
    """Redact every row of a copied log, rebuild the file so no freed page keeps the old text, and check it: the
    number of rows changed, or None when a value survived (the caller deletes the copy)."""
    connection = sqlite3.connect(path)
    try:
        connection.create_function("angelo_redact", 1, redact, deterministic=True)
        changed = connection.execute("UPDATE logs SET feedback_log_body = angelo_redact(feedback_log_body) "
                                     "WHERE feedback_log_body IS NOT angelo_redact(feedback_log_body)").rowcount
        connection.commit()
        connection.execute("PRAGMA journal_mode=DELETE")   # Folds and removes the -wal.
        connection.execute("VACUUM")
        left = connection.execute("SELECT COUNT(*) FROM logs WHERE feedback_log_body IS NOT "
                                  "angelo_redact(feedback_log_body)").fetchone()[0]
    finally:
        connection.close()
    for suffix in SIDECARS:
        Path(str(path) + suffix).unlink(missing_ok=True)
    raw = Path(path).read_bytes().decode("utf-8", "replace")
    return None if left or redact(raw) != raw else changed


def _remove(copied):
    for path in (copied, *(Path(str(copied) + suffix) for suffix in SIDECARS)):
        Path(path).unlink(missing_ok=True)


def trial_logs(homes, start, end, folder, threads=None):
    """``(evidence or None, record)``: every log of ``homes`` copied into ``folder``/``FOLDER``, read for the trial's
    window and own threads, then scrubbed. ``record`` (kept in the trial's report) lists each copy, its limit rows,
    its notes, its possibly pruned threads, and ``unverifiable``: why the log could not show every throttle."""
    record = {"window": [round(start, 3), round(end, 3)], "logs": [], "unverifiable": []}
    logs = [(index, Path(home), path) for index, home in enumerate(homes) for path in home_logs(home)]
    if not logs:
        record["unverifiable"].append("no Codex runtime log found")
    found = []
    for index, home, path in logs:
        relative = path.relative_to(home)   # Each log keeps its own place: two logs of one home never collide.
        target = Path(folder) / FOLDER / ("home-%02d" % index) / relative.parent
        entry = {"name": relative.as_posix(), "home": index}
        record["logs"].append(entry)
        try:
            copied = copy_log(path, target)
        except OSError as error:
            entry["notes"] = ["not copied: %s" % error]
            record["unverifiable"].append("%s not copied" % entry["name"])
            continue
        rows, notes, pruned = read_log(copied, start, end, threads)
        entry.update(limit_rows=rows, notes=notes, possibly_pruned=pruned)
        found += rows
        record["unverifiable"] += ["%s: %s" % (entry["name"], note) for note in notes]   # Each says what is missing.
        record["unverifiable"] += ["%s: thread %s at Codex's retention (%d rows, %d bytes); its early rows may be gone"
                                   % (entry["name"], item["thread"], item["rows"], item["bytes"]) for item in pruned]
        try:
            kept = scrub(copied)
        except (OSError, sqlite3.Error):
            kept = None
        if kept is None:
            _remove(copied)
            entry["notes"] = notes + ["copy deleted: it could not be scrubbed of credential-shaped values and checked"]
        else:
            entry.update(copy=copied.relative_to(folder).as_posix(), redacted_rows=kept)
    if not found:
        return None, record
    first = min(found, key=lambda row: row["at"])   # The error first: it names the limit (STOPPED_MESSAGE).
    return ("Codex retried a throttled request it did not report (runtime log, %d row%s): %s"
            % (len(found), "" if len(found) == 1 else "s", first["error"][:160])), record


def connection_folders(connections, connection_id):
    """``(mode, folder names)`` of the trial's connection: its record in the trial's connection store, or a team
    connection's mode (``eval_live_tasks.TEAM_PREFIX``). Angelo's runtime keeps Codex state under the connection id,
    or under the mode for a connection without one (a team's, a reused native sign-in)."""
    import eval_live_tasks as live
    if str(connection_id or "").startswith(live.TEAM_PREFIX):
        mode = connection_id[len(live.TEAM_PREFIX):]
        return mode, [mode]
    record = None
    if connections and (Path(connections) / "connections.json").is_file():
        from angelo_agent.connections import ConnectionStore   # Read as eval_live_agent.isolated_connection reads it.
        try:
            record = ConnectionStore(connections).records.get(connection_id)
        except (OSError, ValueError, TypeError):
            record = None
    if not isinstance(record, dict):
        return None, []
    return record.get("mode"), [name for name in (connection_id, record.get("mode")) if name]


def trial_runs_codex(connections, connection_id):
    """Whether the trial's connection runs on Codex (``connection_folders``)."""
    return codex_runtime(connection_folders(connections, connection_id)[0])


def after_angelo_trial(report, folder, environment, connection_id, since):
    """An Angelo trial (``eval_live_agent.run_trial``) after its app, and so its runtime, has exited: on a Codex
    connection, its Codex state's log is copied and read for the trial (``trial_logs``). Codex keeps that state at
    ``<connections or state folder>/codex-state/<connection id or mode>`` (``runtime_launch``): the trial's own
    connection store, or, for an API-key connection run in place, the source store outside the trial. Angelo names no
    thread to the harness, so its own threads are those first seen in the window. A Claude trial is returned
    unchanged: its own evidence is in its stream and receipts."""
    connections = environment.get("ANGELO_CONNECTIONS_DIR")
    mode, names = connection_folders(connections, connection_id)
    if not codex_runtime(mode):
        return report
    roots = (Path(folder) / "state", environment.get("ANGELO_STATE_DIR"), connections)
    return mark_invalid(report, *trial_logs(angelo_homes(roots, names), since, time.time(), folder))


def after_mcp_trial(output, folder, thread_ids, since):
    """An MCP-side Codex trial (``bench_mcp_codex.run_trial``) after its app-server has exited: its own sqlite home
    (``CODEX_SQLITE_HOME``, the trial's ``codex-sqlite``) read for the trial, its threads those its stream named.
    ``CODEX_HOME``'s log is never read: the trial's Codex writes to its own sqlite home."""
    threads = {thread for thread in thread_ids or () if thread} or None
    return mark_invalid(output, *trial_logs([Path(folder) / "codex-sqlite"], since, time.time(), folder, threads))


def mark_invalid(report, evidence, record, key="codex_log"):
    """Record ``record`` in ``report``; with ``evidence``, the trial is rate-limited (unless it already was)."""
    report[key] = record
    if evidence and report.get("invalid") != limits.INVALID:
        report.update(invalid=limits.INVALID, aborted=limits.STOPPED_MESSAGE % evidence)
    return report


def unverifiable(report):
    """Whether a trial's Codex log could not show every throttle (its ``codex_log`` record says why)."""
    record = report.get("codex_log") if isinstance(report, dict) else None
    return bool(isinstance(record, dict) and record.get("unverifiable"))


def unverifiable_counts(trial_meta):
    """``{side: count}`` of trials whose Codex log was unverifiable, for the bench's summary."""
    counts = {}
    for meta in trial_meta:
        if isinstance(meta, dict) and meta.get("codex_log_unverifiable"):
            side = meta.get("side", "angelo")
            counts[side] = counts.get(side, 0) + 1
    return counts
