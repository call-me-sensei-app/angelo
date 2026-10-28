# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The trial's own task record, written at each task's send (BENCH-PREREG, Note on F), and every write of it whole.

At a task's send, before the provider request goes out, the in-app driver writes the trial's own
``eval-live-agent.json`` (the path the launcher passes in ``ENV``): the launcher's earlier trials as it last wrote them,
then this trial's report so far, its completed tasks in ``tasks`` and the task in flight in ``unfinished_task`` with its
stamp (``bench_integrity.trial_stamp``: ``prompt_sha256``, ``test_code`` and, in the parallel arm only,
``parallel_request``). The task in flight is never added to ``tasks``: the bench builds its rows from ``tasks``. Both
arms write it the same way, from the same task list the message is sent from. The same stamp stays on the in-flight
snapshot (``eval_abnormal_end``), so the launcher's ``unfinished_task`` of a crashed app carries it too.

A record written before the launcher's end write carries ``IN_PROGRESS``; the end write never does. Every reader of the
record goes through ``finished`` and ``read`` (guarded by ``test_native_bench_record_guard``), which take it as
no record, exactly as before records were written at send: a launch cut short is still set aside and run again, and no
report counts it. Only Amendment F's check of a set-aside attempt reads it on purpose (``bench_messages``).

Every write goes through ``write``: a temporary file in the same folder, flushed and fsynced, then ``os.replace``, so a
write cut short leaves the earlier copy whole. Windows refuses the replace while another process holds the record open
(a sharing violation, ``PermissionError``): the replace is retried, and the launcher's end write then falls back to
writing in place, as it always did, so it never fails where the old write succeeded. A temporary file a killed writer
left is removed by the next write.

Standard library only at import (no bpy).
"""
import errno
import json
import os
from pathlib import Path
import time

NAME = "eval-live-agent.json"
ENV = "ANGELO_EVAL_RECORD"
IN_PROGRESS = "in_progress"
REPLACE_TRIES, REPLACE_WAIT_S = 6, .05   # A Windows sharing violation: up to about a second in all.


def environment(output):
    """The trial process's variable: where its own record is (``output``: the launcher's output folder)."""
    return {ENV: str(Path(output) / NAME)}


def write(path, data, in_place_fallback=False):
    """``data`` as ``path``'s whole content (``json.dumps``, indent 2, as the launcher has always written it): a
    temporary file beside it, flushed and fsynced, then ``os.replace``. Raises when it cannot; the earlier copy stays.
    ``in_place_fallback`` (the launcher's end write): a replace Windows still refuses after its retries is written in
    place instead, as the record always was."""
    path, text = Path(path), json.dumps(data, indent=2, default=str)
    for left in path.parent.glob(path.name + ".*.partial"):   # A killed writer's temporary file.
        try:
            left.unlink()
        except OSError:
            pass
    partial = path.with_name("%s.%d.partial" % (path.name, os.getpid()))
    try:
        with open(partial, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(partial, path)
    except BaseException as error:
        try:
            partial.unlink()
        except OSError:
            pass
        if not (in_place_fallback and isinstance(error, PermissionError)):
            raise
        path.write_text(text)   # The write the launcher always made: never fails where it succeeded.
        return
    try:   # The rename itself survives a power cut (POSIX; Windows has no folder handle to sync).
        folder = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    except (OSError, AttributeError):
        pass


def _replace(partial, path):
    for attempt in range(REPLACE_TRIES):
        try:
            return os.replace(partial, path)
        except PermissionError:   # Windows: another process has the record open.
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_S * (attempt + 1))


def in_progress(data):
    """Whether ``data`` was written before its launcher's end write (at a send, or between trials)."""
    return isinstance(data, dict) and IN_PROGRESS in data


def finished(target):
    """Whether ``target`` (a record's path, or a parsed record) is its launcher's end write: there, and not
    ``IN_PROGRESS``. A path that cannot be read counts as there, so the caller's own read meets it exactly as before."""
    if not isinstance(target, (str, os.PathLike)):
        return not in_progress(target)
    path = Path(target)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return True
    return not in_progress(data)


def finished_only(paths):
    """The paths of ``paths`` that are end writes (``finished``), in order: a glob for records, as it found them."""
    return [path for path in paths if finished(path)]


def read(path, encoding=None):
    """The record at ``path`` as ``json.loads(path.read_text())`` reads it, except that a record written before its
    launcher's end write raises ``FileNotFoundError``, as no record did."""
    data = json.loads(Path(path).read_text(encoding=encoding))
    if in_progress(data):
        raise FileNotFoundError(errno.ENOENT, "a record written before its launcher's end write", str(path))
    return data


def between_trials(path, report, trial):
    """The launcher, after trial ``trial`` when another follows: its trials so far, marked ``IN_PROGRESS``. Evidence
    only: a write that fails is left there (the end write still comes)."""
    try:
        write(path, dict(report, **{IN_PROGRESS: {"written": "between_trials", "after_trial": trial}}))
    except OSError:
        pass


def stamp(trial):
    """The stamp of the task ``trial`` (the in-app driver) has in flight, written at its send; {} before that send."""
    sent = getattr(trial, "sent_stamp", None)
    return dict(sent[1]) if sent and sent[0] is getattr(trial, "record", None) else {}


def at_send(trial, task, number):
    """In the app, right before ``task``'s message is sent: the trial's own record with ``task`` in flight (``number``:
    the trial's number in its launch). A write that fails is noted in the report and the message is still sent."""
    import bench_integrity
    try:
        trial.sent_stamp = (trial.record, bench_integrity.trial_stamp(task))
    except Exception as error:   # noqa: BLE001  Evidence only: never ends the task before its message.
        trial.report["send_record_error"] = "%s: %s" % (type(error).__name__, error)
        return
    target = os.environ.get(ENV)
    if not target:
        return
    record = trial.record
    entry = dict(trial.report, unfinished_task=dict({key: record.get(key) for key in ("trial", "task", "kind")},
                                                    phase=trial.phase, **trial.sent_stamp[1]))
    try:
        earlier = json.loads(Path(target).read_text()).get("trials") if Path(target).is_file() else []
    except (OSError, ValueError, AttributeError):
        earlier = []
    earlier = list(earlier[:number - 1]) if isinstance(earlier, list) else []
    try:
        write(target, {IN_PROGRESS: {"written": "at_send", "trial": number, "task": record.get("task"),
                                     "at_unix": round(time.time(), 3)}, "trials": earlier + [entry]})
    except Exception as error:   # noqa: BLE001  Evidence only: the end write still records the task.
        trial.report["send_record_error"] = "%s: %s" % (type(error).__name__, error)
