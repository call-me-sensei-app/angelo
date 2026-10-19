# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The zero-failure gate sets (``default`` and ``long``): their version, and their questions at each version.

Recorded gate results are compared across merges, so a gate set changes only
as a new version, and no report compares runs of two versions without saying
so (``comparable``, ``NOT_COMPARABLE``).

* Version 1: the sets as recorded before answers were read. A question was
  graded on the unchanged scene alone, so a clean run's 30/30 and 120/120
  include 5 and 24 runs whose answer no check read.
* Version 2: every edit is byte-identical to version 1 (prompt, checks, order).
  Each question keeps its wording and ends with one sentence that asks for an
  answer line (``eval_answer_forms``); the harness computes the truth from the
  scene before it sends the prompt (``survey_truth``) and the run passes only
  on the right answer with the scene unchanged. Neither answer can be given
  without reading the scene: the opening question is asked with one more mesh
  in it (a transient plate, ``survey_fixtures``), because the factory scene's
  answer could be guessed, and the cube question is asked with the cube moved
  by ``CUBE_OFFSET`` for the turn, because where the chat's own edits put it
  could be recalled. None is a yes/no question, so no pair of variants is
  needed.

A version-2 question carries ``gate_version``, so the task list's digest
(``eval_grading.tasks_digest``) covers the version, and every recorded result
states it: a trial report's ``grading.gate_version`` and a benchmark record's
``gate_version``. A result without one is version 1. Dependency-free.
"""

import eval_answers as answers

VERSION = 2
VERSIONS = (1, 2)
SETS = ("default", "long")
# The opening question's extra mesh; removed again when the turn has been graded.
PROBE_MESH = "Brass Marker"
OPENING, CUBE = "new read-only question", "long session: read-only question"
# The cube question's offset, in metres: the harness moves the cube by this for the turn and back afterwards.
CUBE_OFFSET = (0.25, -0.5, 0.75)
NOT_COMPARABLE = ("Not comparable: these runs were recorded with different versions of the gate set (%s). The edits "
                  "are the same tasks; the questions differ in prompt, starting scene and grading, and version 1 "
                  "counted a question as passed without reading its answer.")


def _questions(eval_object):
    """``source`` of a gate question -> what version 2 adds to it."""
    return {OPENING: {"form": "mesh_names", "truth": {"kind": "object_names", "type": "MESH"},
                      "fixtures": [{"kind": "probe_object", "name": PROBE_MESH, "mesh": True}]},
            CUBE: {"form": "box", "truth": {"kind": "dimensions_location", "object": eval_object},
                   "fixtures": [{"kind": "object_offset", "object": eval_object, "location": list(CUBE_OFFSET)}]}}


def versioned(tasks, version, eval_object):
    """A gate task list at ``version`` (None: the current one): edits untouched, questions as that version asks."""
    version = VERSION if version is None else version
    if version not in VERSIONS:
        raise ValueError("Gate-set versions are %s" % ", ".join(map(str, VERSIONS)))
    questions = _questions(eval_object) if version >= 2 else {}
    output = []
    for task in tasks:
        added = questions.get(task.get("source")) if task["kind"] == "read_only" and "answer" not in task else None
        if added is None:
            output.append(task)
            continue
        task = dict(task, prompt=answers.prompt(task["prompt"], added["form"]), gate_version=version,
                    answer={"form": added["form"], "truth": dict(added["truth"])})
        if added.get("fixtures"):
            task["fixtures"] = [dict(fixture) for fixture in added["fixtures"]]
        output.append(task)
    return output


def tasks_version(tasks):
    """The gate-set version a task list was built at (1 when no question says otherwise)."""
    return max((task.get("gate_version", 1) for task in tasks), default=1)


def set_version(task_set, tasks):
    """The gate-set version of a named set's task list; None for a set that is not a gate set."""
    return tasks_version(tasks) if task_set in SETS else None


def stamp_version(stamp):
    """The gate-set version a trial report's ``grading`` states (1 for a report recorded before versions)."""
    return (stamp.get("gate_version") if isinstance(stamp, dict) else None) or 1


def record_version(record):
    """The gate-set version of one benchmark record (1 when it was recorded before versions)."""
    return record.get("gate_version") or 1


def describe(versions):
    return ", ".join("version %d" % version for version in sorted(versions))


def comparable(versions):
    """Whether results of these versions may be shown side by side without a warning."""
    return len(set(versions)) <= 1


# Approval modes (eval_approval). A live-eval trial report states its chat's mode ("approval", and
# "always_allow_scripts"); a benchmark record copies them. One recorded before the default became Auto states
# none: every live eval ran in Ask then.
LEGACY_APPROVAL = "ASK (legacy)"
NOT_COMPARABLE_APPROVAL = ("Not comparable: these runs were recorded in different approval modes (%s). In Auto the "
                           "product approves typed bounded edits itself; in Ask each one waits for the tester's "
                           "approval; with Always allow scripts, scripts run unasked too.")


def approval_mode(stamp):
    """The approval mode a trial report or benchmark record states; ``LEGACY_APPROVAL`` when it states none."""
    if not isinstance(stamp, dict) or not stamp.get("approval"):
        return LEGACY_APPROVAL
    return str(stamp["approval"]) + (" + always allow scripts" if stamp.get("always_allow_scripts") else "")


def modes_comparable(modes):
    """Whether results of these approval modes may be pooled or shown side by side without a warning."""
    return len(set(modes)) <= 1
