# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether each trial was sent its arm's registered message (BENCH-PREREG Amendment F; host side, sends nothing).

The record is the trial's own task record (``eval-live-agent.json``, stamped by ``bench_integrity.trial_stamp``):
``prompt_sha256`` (the SHA-256, UTF-8, of the text sent) and ``parallel_request``. The registered message is derived,
never copied per test: the scenario's brief comes from the registry the harness sends from
(``eval_live_tasks.tasks_for``, which the in-app driver calls), and the parallel arm's is that brief, a blank line,
then Amendment D's sentence (``eval_parallel.PARALLEL_REQUEST``, accepted only while it hashes to the frozen digest
below). One rule for every registered test and every worker cap: nothing here reads a test's name or a cap.

A trial whose record does not show its arm's message is named with what it recorded; the verdict is then INCOMPLETE
(F2). Naming never changes a trial's place in its arm: a lost trial stays C6's failed trial (the review's L-1). A lost trial (no task record, Amendment C6) and one the harness ended before its
message (rule 2) sent nothing to check (F3): the caller passes only trials with a task record, and one that sent
nothing is skipped here too.

Every attempt a resume set aside under an arm's ``interrupted/`` folder that left a task record is checked the same
way, with the same registry, in both arms (the Clarification of F); one that does not show its arm's message is named
with the attempt it was, and the verdict is INCOMPLETE too. An attempt left a task record when its
``eval-live-agent.json`` records a task, or cannot be read to say it records none; an attempt with no record, or one
whose record plainly holds no task (C6's lost trial), has no message to check (F3). Warm-ups are never attempts (they
live under ``warm-up/``) and stay unchecked.

A record lists the task it holds in flight when its ``unfinished_task`` carries that task's stamp, or when the record
was written at that task's send (BENCH-PREREG, Note on F; ``eval_send_record`` stamps it there, so a stamp missing from
it is named): a set-aside attempt cut short after its message then has a task record, and the task in flight is checked
with the same rule and registry at its own place in the test's task list (its ``task`` among the registered tasks), as
completed tasks are at theirs. Any other ``unfinished_task`` without a stamp lists no task, as before. Only set-aside
attempts read the stamp of a task in flight (the maintainer: "Current C6-lost trials stay unchecked, per F3 as frozen;
only set-aside attempts read the stamp"): which current trials are checked, and what is read of them, is unchanged.
"""
import hashlib
import json
from pathlib import Path

import eval_send_record   # The record written at a send (standard library only).

# Amendment D's sentence, as frozen (UTF-8, no trailing newline). The text pins it, so it is asserted, never derived.
D_SENTENCE_SHA256 = "5ecc54171d8a9b199eaa8cd9ce0dd59005fdce27101740f512252994491e0270"
RECORD = eval_send_record.NAME   # Read raw on purpose: F checks a set-aside attempt's record written at a send.


def sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def sentence():
    """Amendment D's sentence: the constant the parallel arm sends, or None when it is not the frozen text."""
    import eval_parallel
    text = getattr(eval_parallel, "PARALLEL_REQUEST", None)
    return text if isinstance(text, str) and sha256(text) == D_SENTENCE_SHA256 else None


def task_ids(task_set):
    """The ids of ``task_set``'s tasks in send order, from the same task list as ``registered``."""
    import eval_live_tasks
    return [task["id"] for task in eval_live_tasks.tasks_for(task_set)]


def registered(task_set):
    """{arm: [(prompt_sha256, parallel_request), ...]}: each arm's registered message per task, in send order. The
    brief is the harness's own task list for ``task_set``; the parallel message is the brief, a blank line, then D's
    sentence (F1), composed here from that definition rather than by the code that sends it."""
    import eval_live_tasks
    briefs, request = [task["prompt"] for task in eval_live_tasks.tasks_for(task_set)], sentence()
    return {"single": [(sha256(brief), None) for brief in briefs],
            "parallel": [(sha256(brief + "\n\n" + request), request) if request else (None, None) for brief in briefs]}


def task_set(run):
    try:
        value = json.loads((Path(run) / "interleave.json").read_text())["task_set"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) else None


def recorded(trial_folder):
    """The task records of the trial's ``eval-live-agent.json`` (a list of objects), or None when it cannot say."""
    try:
        tasks = (json.loads((Path(trial_folder) / RECORD).read_text()).get("trials") or [{}])[0].get("tasks")
    except Exception:   # noqa: BLE001  Any shape: the record does not show what was sent.
        return None
    return tasks if isinstance(tasks, list) and tasks and all(isinstance(task, dict) for task in tasks) else None


def why_not(arm, tasks, expected):
    """Why ``tasks`` (the trial's task records) do not show ``arm``'s registered message ``expected`` ([]: they do)."""
    if tasks is None:
        return ["no readable task record with a prompt_sha256"]
    return [line for index, task in enumerate(tasks) for line in why_task(arm, task, expected, index, "task %d" % (
        index + 1))]


def why_task(arm, task, expected, index, label):
    """Why one task record (``label``, at ``index`` in the test's task list) does not show its registered message."""
    digest, request = expected[index] if index < len(expected) else (None, None)
    if digest is None:
        return ["%s: no registered message to compare (%s)" % (label, (
            "the harness's sentence is not Amendment D's" if index < len(expected)
            else "the scenario registers %d task(s)" % len(expected)))]
    found = []
    if task.get("prompt_sha256") != digest:
        found.append("%s: %s" % (label, "prompt_sha256 %s, not the registered message's" % (
            task["prompt_sha256"]) if task.get("prompt_sha256") else "no prompt_sha256"))
    if arm == "single" and "parallel_request" in task:
        found.append("%s: a parallel_request in the single arm" % label)
    elif arm == "parallel" and task.get("parallel_request") != request:
        found.append("%s: %s" % (label, "no parallel_request" if not task.get("parallel_request") else
                                 "a parallel_request other than Amendment D's sentence (SHA-256 %s)"
                                 % sha256(task["parallel_request"])))
    return found


STAMP_KEYS = ("prompt_sha256", "parallel_request")   # ``bench_integrity.trial_stamp``'s message fields.


def in_flight(trial_folder):
    """``(tasks, entry)`` when the record lists a task in flight (``unfinished_task`` with a ``STAMP_KEYS`` field, or
    in a record written at that task's send): its completed task records ([] when none) and that entry; else
    ``(None, None)``."""
    try:
        data = json.loads((Path(trial_folder) / RECORD).read_text())
        first = (data.get("trials") or [{}])[0]
        entry, tasks = first.get("unfinished_task"), first.get("tasks")
        at_send = data[eval_send_record.IN_PROGRESS].get("written") == "at_send" if eval_send_record.in_progress(
            data) else False
    except Exception:   # noqa: BLE001  Any shape: no task in flight shown.
        return None, None
    if not isinstance(entry, dict) or not (at_send or any(key in entry for key in STAMP_KEYS)):
        return None, None
    return (tasks if tasks == [] else recorded(trial_folder)), entry


def why_in_flight(arm, entry, expected, ids):
    """Why the task in flight (``entry``) does not show ``arm``'s registered message: ``why_task`` at that task's own
    place in the test's task list (``ids``)."""
    index = ids.index(entry.get("task")) if entry.get("task") in ids else None
    if index is None:
        return ["the task in flight (%s): not a task of the run's test" % json.dumps(entry.get("task"))]
    return why_task(arm, entry, expected, index, "task %d (in flight)" % (index + 1))


def left_a_task_record(trial_folder):
    """Whether a set-aside attempt left a task record to check: its record is there and does not plainly hold no task
    (C6's lost trial, F3). One that cannot be read cannot show its message, so it is checked and named, not skipped."""
    path = Path(trial_folder) / RECORD
    if not path.is_file():
        return False
    try:
        first = (json.loads(path.read_text()).get("trials") or [{}])[0]
    except Exception:   # noqa: BLE001  Any shape: it cannot say it holds no task.
        return True
    return not isinstance(first, dict) or bool(first.get("tasks")) or in_flight(trial_folder)[1] is not None


def unshown(run, trials):
    """[{arm, trial, attempt, recorded, why}] for each trial in ``trials`` ({arm: trial numbers with a task record}),
    and each attempt a resume set aside in those arms that left a task record (``attempt``: its folder under the arm,
    else None), whose record does not show its arm's registered message (F1, F2, the Clarification of F). The same
    check for both arms, current trials and set-aside attempts, every test and every cap."""
    from bench_parallel_vs_single import attempts, sent_nothing   # The runner's set-asides; rule 2's own test (F3).
    run, name = Path(run), task_set(run)
    try:
        expected, ids = (registered(name), task_ids(name)) if name else (None, None)
    except Exception:   # noqa: BLE001  An unregistered test: no registered message to show.
        expected = ids = None
    found = []

    def check(arm, folder, trial, attempt):
        if sent_nothing(folder, trial):
            return
        # Only a set-aside attempt reads a task in flight, stamped at its send (the maintainer, Note on F).
        tasks, entry = in_flight(folder / ("angelo-trial-%02d" % trial)) if attempt else (None, None)
        tasks = recorded(folder / ("angelo-trial-%02d" % trial)) if entry is None else tasks
        why = (why_not(arm, tasks, expected[arm]) + (why_in_flight(arm, entry, expected[arm], ids) if entry else [])
               if expected is not None else ["the run's test %r has no registered message" % name])
        if why:
            found.append({"arm": arm, "trial": trial, "attempt": attempt, "why": why, "recorded": [
                {key: task.get(key) for key in STAMP_KEYS} for task in tasks or ()] + ([dict(
                    {key: entry.get(key) for key in STAMP_KEYS}, in_flight=entry.get("task"))] if entry else [])})

    for arm, numbers in trials.items():
        for trial in sorted(set(numbers)):
            check(arm, run / arm, trial, None)
        for trial, kept in attempts(run / arm):
            if left_a_task_record(kept / ("angelo-trial-%02d" % trial)):
                check(arm, kept, trial, kept.relative_to(run / arm).as_posix())
    return found


def named(entries):
    """Each trial or set-aside attempt that does not show its registered message, as the report's missing evidence
    names it."""
    return ["%s trial %d%s: does not show its arm's registered message (Amendment F): %s; recorded %s" % (
        entry["arm"], entry["trial"], ", the attempt a resume set aside as %s" % entry["attempt"] if entry.get(
            "attempt") else "", "; ".join(entry["why"]), json.dumps(entry["recorded"]) if entry["recorded"]
        else "no readable task record") for entry in entries]
