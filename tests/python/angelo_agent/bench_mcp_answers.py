# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The answer check of a question on the Blender MCP side of ``bench_mcp_vs_angelo.py``.

The Angelo side reads a question's answer in its trial driver
(``eval_grading_live.LiveGrading``). The MCP side has no controller, so the
benchmark driver does the same three things around its prompt, with the same
modules:

* ``before`` (driver): Blender makes the question's fixtures and computes its
  ground truth over the add-on socket (``setup``, run in Blender by
  ``bench_mcp_grading.answer_setup_code``), before the scene is digested and
  the prompt is sent;
* ``after`` (driver): once the scene has been graded, the answer line is read
  against that truth in every assistant message of the turn, as the Angelo
  side reads every assistant row (``claude_rows``, ``codex_rows``;
  ``eval_answers.grade``), and the fixtures are undone (``restore``, run in
  Blender). The run passes only on the right answer with the scene unchanged
  and the turn not stopped at its time limit, as on the Angelo side.

A task without an ``answer`` costs nothing here: no socket request is made.
Nothing here raises into a trial of billed messages: a defect leaves the
question ungraded, with the reason. Both runtimes of the MCP side (Claude Code
and Codex) call these.
"""

import eval_answers as answers
import survey_fixtures
import survey_truth

# In Blender: what ``setup`` changed and ``restore`` has to undo, kept between the driver's socket requests.
_RESTORE = []
STOPPED = "the turn was stopped at its time limit"


def _undo_data(scene, fixture):
    """What undoes a fixture without this module's state (which a reloaded module would have lost), or None."""
    if fixture["kind"] == "probe_object":
        return {"remove_object": fixture["name"]}
    if fixture["kind"] == "object_offset":
        obj = scene.objects.get(fixture["object"])
        return {"object": fixture["object"], "location": list(obj.location)} if obj is not None else None
    return None


def setup(bpy, scene, task):
    """In Blender, before the prompt: the task's fixtures, then its ground truth (``LiveGrading.before``)."""
    restore(bpy, scene)   # Nothing of an earlier question may be left behind.
    output = {"steps": [], "undo": []}
    for fixture in task.get("fixtures") or ():
        data = _undo_data(scene, fixture)
        try:
            steps, undo = survey_fixtures.apply(bpy, scene, fixture)
        except survey_fixtures.FixtureError as error:
            output["problem"] = "the harness could not make this task's starting state: %s" % error
            return output
        output["steps"] += steps
        if undo is not None:
            _RESTORE.append(undo)
            output["undo"].append(data)
    output["truth"] = survey_truth.truth(bpy, scene, task["answer"]["truth"])
    return output


def restore(bpy, scene, undo=()):
    """In Blender, after grading: undo the transient fixtures of the last ``setup``; how many were undone.

    ``undo`` is what ``setup`` told the driver: used only when this module's own record is gone.
    """
    undone = 0
    while _RESTORE:
        _RESTORE.pop()()
        undone += 1
    for step in () if undone else reversed([step for step in undo if step]):
        if "remove_object" in step:
            found = bpy.data.objects.get(step["remove_object"])
            data = found.data if found is not None else None
            if found is not None:
                bpy.data.objects.remove(found)
                undone += 1
            if data is not None and not data.users:
                bpy.data.meshes.remove(data)
        elif scene.objects.get(step["object"]) is not None:
            scene.objects[step["object"]].location = step["location"]
            undone += 1
    return undone


def claude_rows(events, result):
    """A Claude Code turn as chat rows: the text of each assistant message in order, then its result text
    when that is not already the last of them."""
    texts = ["".join(block.get("text") or "" for block in (message.get("message") or {}).get("content") or []
                     if isinstance(block, dict) and block.get("type") == "text")
             for _, message in events if isinstance(message, dict) and message.get("type") == "assistant"]
    texts = [text for text in texts if text.strip()]
    final = str((result or {}).get("result") or "")
    if final.strip() and (not texts or texts[-1].strip() != final.strip()):
        texts.append(final)
    return [{"role": "assistant", "text": text} for text in texts]


def codex_rows(events):
    """A Codex turn as chat rows: the text of each completed agent message, in order."""
    items = [((message.get("params") or {}).get("item") or {}) for _, message in events
             if isinstance(message, dict) and message.get("method") == "item/completed"]
    return [{"role": "assistant", "text": str(item.get("text") or "")} for item in items
            if item.get("type") == "agentMessage"]


def before(client, grading, task, task_set, record):
    """Driver, before the scene is digested: the question's fixtures and truth; None for any other task."""
    if not task.get("answer"):
        return None
    try:
        made = client.execute(grading.answer_setup_code(task["id"], *task_set))
        made = made if isinstance(made, dict) else {"problem": "the add-on returned no ground truth"}
    except Exception as error:   # Never raises: the question is then ungraded, with the reason.
        made = {"problem": "the ground truth could not be computed (%s)" % str(error)[:300]}
    if made.get("steps"):
        record["harness_fixture"] = made["steps"]
    return made


def undo(client, grading, made):
    """Driver: undo the fixtures ``before`` made (a skipped task, or after grading); never raises.

    Blender says how many it undid: fewer than were made is recorded, because the next task would then
    run on a scene the harness changed.
    """
    if made is None:
        return
    wanted = made.get("undo") if isinstance(made.get("undo"), list) else []
    try:
        done = client.execute(grading.answer_restore_code(wanted))
        undone = done.get("restored") if isinstance(done, dict) else None
        if undone != len(wanted):
            made["restore_error"] = "%r of %d harness fixtures were undone" % (undone, len(wanted))
    except Exception as error:
        made["restore_error"] = str(error)[:300]


def after(client, grading, task, record, made, rows, stopped=False):
    """Driver, after the scene was graded: read the answer in the turn's ``rows``, set the run's final
    result, undo the fixtures. ``stopped``: the turn hit its time limit, which no answer makes a pass.

    Never raises: a trial of billed messages must not end on a defect of this check. A defect leaves the
    question ungraded (failed when the scene changed), with the reason.
    """
    if made is None:
        return
    host_check = record.get("host_check") if isinstance(record.get("host_check"), dict) else {}
    unchanged = record.get("host_check_passed")
    try:
        truth = made.get("truth") if isinstance(made.get("truth"), dict) else {}
        problem = made.get("problem") or truth.get("ungraded") or ("" if "accept" in truth else "no ground truth")
        if not problem:
            result = answers.grade(task["answer"], truth["accept"], list(rows))
            host_check["answer"] = dict(result, truth=truth.get("detail"), **({"turn": STOPPED} if stopped else {}))
            passed = bool(unchanged) and result["outcome"] == "right" and not stopped
    except Exception as error:
        problem = "the answer check failed (%s: %s)" % (type(error).__name__, str(error)[:200])
    if problem:   # Nothing to compare the answer with: ungraded, never passed (a changed scene still fails).
        host_check["answer"] = {"outcome": "ungraded", "reason": problem}
        passed = None if unchanged and not stopped else False
    record.update(host_check=host_check, host_check_passed=passed)
    undo(client, grading, made)
    if made.get("restore_error"):
        record["harness_fixture_errors"] = [made["restore_error"]]
