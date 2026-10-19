# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The grading contract of the live-eval harness: what a report may count as passed.

A run of a task is ``passed`` only when a check read its result:

* an edit: closed host checks (``checks``), result checks (``result_checks``,
  ``survey_checks``) or a scenario turn's checks (``scenario_checks``) passed,
  and did not already hold before the turn;
* a question: the scene is unchanged AND its answer check passed
  (``eval_answers``), with the observation a visual question needs.

Everything else is ``failed``, ``inconclusive``, ``skipped`` or ``ungraded``,
and every total states all five (``totals``, ``describe``), over every run:
ungraded and inconclusive work is never counted as passed and never dropped
from the denominator, and planned runs that never ran are stated beside the
total (``not_run``). A run is ``inconclusive`` when its checks ran and every
one that did not pass is one its grader could not decide (each marked so,
``undecided``): neither a pass nor a measured fail, shown beside both. It
splits what earlier reports counted as failed; what passes is unchanged. A run is
``ungraded`` when nothing could read its result: an edit whose task says why
(``task["ungraded"]``), a check that already held before the turn, a question
whose ground truth the harness could not establish, or a question whose answer
no check reads.

The last case is the gate sets' questions as version 1 recorded them
(``LEGACY_QUESTIONS``; ``eval_gate``): their prompts asked for no answer form,
so their ``host_check_passed`` is the unchanged scene alone and this contract
reports those runs as ``ungraded`` beside that figure. From version 2 the gate
questions are read like any other. ``GATE_DIGESTS`` pins both gate sets at
every version, so a change to either is a new version: a deliberate, visible
act, stated in every recorded result (``stamp``).

``record["grade"]`` is stamped by the trial driver; ``state`` derives it for
records written before the contract and for the Blender MCP driver's.
Dependency-free: no ``bpy``, importable by every launcher, report and test.
"""

import hashlib
import json

import eval_gate

# 4: numeric leniency is bounded; denials and alternative numbers cannot be discarded as wrapping.
# 3: an answer line is also read where it plainly gives the answer and wraps it (eval_answer_forms), and each
# such reading is recorded and counted. 2: a run is passed only when a check read its result.
CONTRACT = 4
STATES = ("passed", "failed", "inconclusive", "ungraded", "skipped")
# The three verdicts a graded run or trial can have, in the order every report shows them.
VERDICTS = ("passed", "failed", "inconclusive")
# Statuses the Angelo driver reaches only through its grading step.
GRADED_STATUSES = frozenset({"completed", "failed"})
LEGACY_REASON = ("gate-set question as version 1 of the gate sets recorded it: no check reads its answer, and "
                 "its result keeps its recorded meaning because gate results are compared across merges")
# ``source`` of the gate sets' questions: the default set's opening question and the long set's repeated one.
LEGACY_QUESTIONS = frozenset({eval_gate.OPENING, eval_gate.CUBE})
# tasks_digest of the zero-failure gate sets (default; long with 40 tasks) at each gate-set version (eval_gate).
# Version 1 is what gate runs recorded until answers were read; a change to either list is a new version.
GATE_DIGESTS = {1: {"default": "ab9e568a361c7c6c", "long": "a4ea35af28319d93"},
                2: {"default": "962e64ea09e53e92", "long": "94ca4135caaabc3a"}}


def record_contract(record):
    """A normalized record's original grading contract; 0 means unrecorded, never the current contract."""
    return record.get("grading_contract") or 0


def contract_label(contracts):
    return ", ".join(str(value) if value else "unrecorded" for value in sorted(set(contracts)))


def contract_notice(contracts):
    """Why different or unrecorded grading contracts must not be silently pooled."""
    return ("Not comparable: these runs used different grading contracts (%s). Answer parsing and grading "
            "semantics may differ." % contract_label(contracts)) if len(set(contracts)) > 1 else None


def tasks_digest(tasks):
    """A task list's identity: 16 hex digits over its canonical JSON (prompts, checks, answers, order)."""
    return hashlib.sha256(json.dumps(tasks, sort_keys=True, default=str).encode()).hexdigest()[:16]


def answer_check(task):
    """How a question's answer is checked: ``answer`` (against ground truth), ``includes`` (names only) or None."""
    if task.get("answer"):
        return "answer"
    return "includes" if task.get("reply_includes") else None


def legacy_question(task):
    """A gate-set question as version 1 asked it: graded on the unchanged scene only, reported as ungraded."""
    return task.get("kind") == "read_only" and task.get("source") in LEGACY_QUESTIONS and answer_check(task) is None


def basis(task):
    """What grades a task, or None when nothing does and the task gives no reason (the guard's failure).

    ``answer``/``includes`` for a question, ``checks``/``result_checks``/
    ``scenario_checks`` for an edit, or ``ungraded`` with a stated reason.
    """
    if task.get("kind") == "read_only":
        return answer_check(task) or ("ungraded" if legacy_question(task) else None)
    for key in ("checks", "result_checks", "scenario_checks"):
        if task.get(key):
            return key
    return "ungraded" if isinstance(task.get("ungraded"), str) and task["ungraded"].strip() else None


def ungraded_reason(task):
    """Why a task is never graded (its own reason, or the version-1 gate questions'), else None."""
    if legacy_question(task):
        return LEGACY_REASON
    return task.get("ungraded") if basis(task) == "ungraded" else None


def _answer_unread(record, task):
    """Whether a question's record lacks a passed answer check. Its own host check decides; without one,
    nothing in the record says an answer was read (the driver stamps ``grade`` when one was)."""
    if (task or record).get("kind") != "read_only":
        return False
    host_check = record.get("host_check")
    if not isinstance(host_check, dict):
        return True
    answer = host_check.get("answer")
    if isinstance(answer, dict):
        return answer.get("outcome") != "right"
    return "reply_missing" not in host_check   # The older names check wrote this key when it ran.


def undecided(host_check):
    """Whether a host check failed only through checks its grader could not decide.

    True when its checks ran and every one that did not pass is marked
    ``inconclusive`` (``scenario_checks.evaluate``, ``survey_checks.evaluate``)
    or has the closed checks' ``inconclusive`` status (``scene_validation``),
    and nothing else it records failed (closed checks kept beside result
    checks, smooth shading, a reply's missing names). False for anything else,
    a grading error included.
    """
    if not isinstance(host_check, dict) or host_check.get("error") or host_check.get("reply_missing"):
        return False
    if host_check.get("smooth_shaded") is False:
        return False
    closed = host_check.get("closed_checks")
    if isinstance(closed, dict) and not closed.get("success") and not undecided(closed):
        return False
    results = host_check.get("results")
    if results is None:
        results = host_check.get("checks")
    if not isinstance(results, list):
        return False
    unmet = [entry for entry in results if not (isinstance(entry, dict) and entry.get("passed") is True)]
    return bool(unmet) and all(isinstance(entry, dict) and (entry.get("inconclusive") is True
                                                            or entry.get("status") == "inconclusive")
                               for entry in unmet)


def _graded_status(record):
    return record.get("graded_status") or record.get("status")


def derive(record, task=None):
    """A graded record's state from its host check; ``task`` (when known) says whether an answer was read."""
    passed = record.get("host_check_passed")
    if passed is True:
        return "ungraded" if _answer_unread(record, task) else "passed"
    if passed is None and record.get("status") in GRADED_STATUSES:
        return "ungraded"
    if passed is False and _graded_status(record) in GRADED_STATUSES and undecided(record.get("host_check")):
        return "inconclusive"
    return "failed"   # A failed check, or a turn that was stopped, timed out or errored before grading.


def state(record, task=None):
    """One run's state under the contract: ``passed``, ``failed``, ``inconclusive``, ``ungraded`` or ``skipped``.

    A record stamped ``failed`` before the third verdict existed reads as ``inconclusive`` when its own host
    check says only undecided checks failed; nothing ever reads as passed that was not stamped so.
    """
    if record.get("status") == "skipped":
        return "skipped"
    grade = record.get("grade")
    if grade == "failed" and record.get("host_check_passed") is False \
            and _graded_status(record) in GRADED_STATUSES and undecided(record.get("host_check")):
        return "inconclusive"
    return grade if grade in STATES else derive(record, task)


def totals(records, tasks=None, planned=None):
    """Counts of every state over ``records``; ``total`` is every run, graded or not.

    ``planned`` (when the caller knows it) is how many runs were meant to
    happen: ``not_run`` then states the ones a stopped trial never reached, so
    they are not silently missing from the denominator either.
    """
    by_id = {task["id"]: task for task in tasks or ()}
    counts = dict.fromkeys(STATES, 0)
    for record in records:
        counts[state(record, by_id.get(record.get("task")))] += 1
    counts["total"] = len(records)
    if planned is not None:
        counts["not_run"] = max(0, planned - len(records))
    return counts


def describe(counts):
    """``passed 20, failed 0, inconclusive 0, ungraded 5, skipped 0 of 25``: the one line every report prints
    (with ``; 3 planned runs never ran`` when a trial stopped early)."""
    text = "passed %d, failed %d, inconclusive %d, ungraded %d, skipped %d of %d" % tuple(
        counts.get(key, 0) for key in STATES + ("total",))
    return text + ("; %d planned runs never ran" % counts["not_run"] if counts.get("not_run") else "")


def task_outcomes(records, tasks=None):
    """Per task id: runs, completed requests, the contract's states and the older host-check count.

    ``host_checks_passed`` keeps its earlier meaning (``host_check_passed is
    True``), which for a version-1 gate question is the unchanged scene alone;
    ``passed`` is the contract's.
    """
    by_id = {task["id"]: task for task in tasks or ()}
    output = {}
    for record in records:
        entry = output.setdefault(record["task"], {"runs": 0, "completed": 0, "host_checks_passed": 0, "skipped": 0,
                                                   "passed": 0, "failed": 0, "inconclusive": 0, "ungraded": 0})
        entry["runs"] += 1
        entry["completed"] += (record.get("receipt") or {}).get("status") == "completed"
        entry["host_checks_passed"] += record.get("host_check_passed") is True
        entry[state(record, by_id.get(record["task"]))] += 1
    return output


def verdict_counts(verdicts):
    """``{"passed", "failed", "inconclusive", "total"}`` over verdicts (one per trial or check); every verdict
    that is not passed or inconclusive counts as failed, and ``total`` is all of them."""
    verdicts = list(verdicts)
    counts = {key: sum(verdict == key for verdict in verdicts) for key in ("passed", "inconclusive")}
    return {"passed": counts["passed"], "failed": len(verdicts) - counts["passed"] - counts["inconclusive"],
            "inconclusive": counts["inconclusive"], "total": len(verdicts)}


def verdict_text(counts, unit=""):
    """``passed 3, failed 1, inconclusive 1 of 5``: the pass count over every trial with the other two beside it."""
    return "passed %d, failed %d, inconclusive %d of %d%s" % (
        counts.get("passed", 0), counts.get("failed", 0), counts.get("inconclusive", 0), counts.get("total", 0),
        " " + unit if unit else "")


def pass_cell(passed, runs, counts):
    """``3/5 (failed 1, inconclusive 1)``: a table cell's passed of all runs, with the runs the contract
    (``counts``: its totals over the same runs) counts as failed and as inconclusive beside it."""
    counts = counts or {}
    return "%s/%s (failed %d, inconclusive %d)" % (passed, runs, counts.get("failed", 0),
                                                    counts.get("inconclusive", 0))


def check_counts(host_check):
    """``{"passed", "failed", "inconclusive", "total"}`` of one host check's checks (``results``); a grading
    error is one failed check."""
    if not isinstance(host_check, dict):
        return {"passed": 0, "failed": 0, "inconclusive": 0, "total": 0}
    if host_check.get("error"):
        return {"passed": 0, "failed": 1, "inconclusive": 0, "total": 1}
    results = host_check.get("results") or host_check.get("checks") or []
    return verdict_counts("passed" if isinstance(entry, dict) and entry.get("passed") is True
                          else "inconclusive" if isinstance(entry, dict) and (entry.get("inconclusive") is True
                                                                              or entry.get("status") == "inconclusive")
                          else "failed" for entry in results)


def stamp(task_set, tasks):
    """What a trial report records about its grading: the contract, the task list's digest and set version."""
    value = {"contract": CONTRACT, "task_set": task_set, "tasks": len(tasks), "tasks_digest": tasks_digest(tasks)}
    if eval_gate.set_version(task_set, tasks):
        value["gate_version"] = eval_gate.set_version(task_set, tasks)
    if task_set.startswith("survey"):
        import eval_survey_tasks
        value["survey_version"] = eval_survey_tasks.VERSION
    return value


def unguarded(tasks):
    """Ids of tasks that nothing grades and that give no reason: must be empty for every graded set."""
    return [task["id"] for task in tasks if basis(task) is None]
