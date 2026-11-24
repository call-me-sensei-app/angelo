# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex runtime log database with the real schema and row shape (``logs_2.sqlite``, Codex 0.171 as Angelo bundles
it), for the tests of ``eval_codex_logs``. Its rows stay in the -wal, as a running Codex's do."""

import sqlite3
from pathlib import Path

THREAD = "01a20b74-83b6-7de2-a157-d625b60cf09d"
SPAN = ('session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.turn_input" submission.id="t1" '
        'codex.op="turn_input"}:turn{otel.name="session_task.turn" thread.id=%s turn.id="t1"}' % (THREAD, THREAD))
RETRY_TARGET = "codex_core::responses_retry"


def retry_body(error, field="sampling_error", what="stream disconnected - retrying sampling request (1/5 in 577.9s)..."):
    """A ``responses_retry`` row's body as Codex writes it (its span, then its message and fields)."""
    return "%s: %s turn_id=t1 retries=1 max_retries=5 %s=%s" % (SPAN, what, field, error)


THROTTLED = retry_body("exceeded retry limit, last status: 429 Too Many Requests")


class Log:
    """A Codex log database in WAL mode whose rows stay in its -wal (the runtime's connection open)."""

    SCHEMA = """CREATE TABLE logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, ts_nanos INTEGER NOT NULL, level TEXT NOT NULL,
        target TEXT NOT NULL, feedback_log_body TEXT, module_path TEXT, file TEXT, line INTEGER, thread_id TEXT,
        process_uuid TEXT, estimated_bytes INTEGER NOT NULL DEFAULT 0)"""

    def __init__(self, home, name="logs_2.sqlite", folder="log"):
        self.path = Path(home) / folder / name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.connection.execute(self.SCHEMA)
        self.connection.commit()

    def add(self, at, body, level="WARN", target=RETRY_TARGET, thread=THREAD, size=0):
        self.many(at, 1, body, level, target, thread, size)

    def many(self, at, count, body="row", level="TRACE", target="codex_core::session", thread=THREAD, size=0):
        rows = [(int(at), int((at % 1) * 1e9), level, target, body, target, thread, "process-1", size)] * count
        self.connection.executemany(
            "INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body, module_path, file, line, thread_id, "
            "process_uuid, estimated_bytes) VALUES (?, ?, ?, ?, ?, ?, 'core\\src\\responses_retry.rs', 206, ?, ?, ?)",
            rows)
        self.connection.commit()

    def close(self):
        self.connection.close()
