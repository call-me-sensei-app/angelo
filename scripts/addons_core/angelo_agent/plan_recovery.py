# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Claude typed plan does when one of its operations fails.

A step that changed nothing never ends a turn by itself
(doc/angelo/agent_execution.rst, "Failed plan steps"):

* A lookup (a read-only tool other than a standalone verification) returns
  data. Only its ``error`` is a failure, and nothing in its data is read as
  check evidence. A failed lookup goes back to the model with every other
  result, in the same turn; the plan's later lookups still run, its edits
  after the failure do not.
* An edit that failed without changing anything (a staged program that was
  refused, a stale binding included, or whose commit rolled back; a
  background-job call the host refused) gets a corrected plan wherever it sits
  in the plan, within the request's correction budget. Operations that ran
  before it are reported and never run again.

A reviewed script that raised is not a step that changed nothing, and it has
its own rule (``script_errors``): the executor tries to reload its Main-data
restore point, and separately reports unverified pixel and external effects.
The model reads the error, its line and that state, then inspects unverified
effects before writing a correction. A request corrects ``script_errors.SCRIPT_CORRECTIONS``
of them, counted apart from the plans the host rejected or refused, so neither
budget can use up the other. A Codex thread reads the same result as a tool
result inside its turn.

A lookup or job start the host refused alone before the plan ran
(``plan_validation``) is answered with its error, never runs, and the plan
stops there: what came before it ran, nothing after it runs. A refused lookup
is then read like a failed lookup, in the same turn; a refused job start is a
start refused at admission and gets the correction above, paid as a rejected plan
(``CLAUDE_PLAN_REJECTIONS``: it was refused at the screen), not as a failed edit. A normalised lookup
makes its further calls in its own slot.

Everything else ends the turn as before: a failed or inconclusive check, a
cleanup warning, a stale binding of an edit that may have run, a decline,
an expiry, Stop and a document change. The host never retries anything
itself; whatever the model submits next is validated and approved like any
other plan, in the conversation's approval mode.
"""

import time
from dataclasses import dataclass, field
from typing import NamedTuple

from . import script_errors
from .claude_evidence import VISUAL_IMAGE_TOOLS
from .claude_outcomes import correctable_failure, native_result_problem
from .plan_schema import CLAUDE_CORE_ACTIONS, staged_rejection_prompt
from .tool_specs import TOOL_MAP
from .ui_text import notice

VERIFY = "angelo_scene_verify"
# A plan the host rejects before running anything returns to the model (a rejected plan with the exact
# schemas of the deferred items it used) at most this often in a row; the next one fails the request as before.
# A plan that ran whole (nothing refused, failed or rejected in it) and changed the scene (``changed_scene``)
# starts the count again: a long request that keeps making progress is not stuck, while a plan that
# repeats a refused step behind an edit still spends the budget. Recorded (Opus 5.5, a 90-object scene,
# 2026-10-21): a look pass whose rounds committed a face turn, eight cups and an eye and material
# script ended Failed at its third correction, before its lighting, grade and renders, though the two
# before it had been corrected. Codex plans end their request when one runs whole, so only Claude's
# loop restarts the count.
CLAUDE_PLAN_REJECTIONS = 2
# An edit that ran and failed without changing anything (a staged program whose check failed, rolled back) returns
# for a correction at most this often in a row, counted apart from rejected plans and restarted by progress like
# them: with one shared budget, two rejected plans left a failed stage no correction and the turn ended with
# nothing built (plan-verify-facts A/B, playground message 3, 2 of 3 trials).
FAILED_EDIT_CORRECTIONS = 2
# Model rounds one request may take, corrections included: the bound on a request that alternates
# progress and corrections.
PLAN_ROUNDS = 16
# Progress restarts the counts only while the corrections of both kinds so far plus a fresh rejection budget fit
# within this: a request that alternates an edit with the same mistake still ends (at most this plus
# FAILED_EDIT_CORRECTIONS corrections of these two kinds in all).
CLAUDE_REQUEST_CORRECTIONS = 3 * CLAUDE_PLAN_REJECTIONS
# Programs whose staged previews are attached automatically, not requested views.
PROGRAM_RESULT_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})
# Results that may carry staged-preview pixels.
PREVIEW_IMAGE_TOOLS = PROGRAM_RESULT_TOOLS | {"angelo_inspect_preview"}
NO_RESULT = "The host returned no usable native result. Inspect before continuing; do not automatically retry edits."
# A failed lookup the person or the host decided: it ends the request like a declined edit.
_DECISIONS = ("The user declined", "Document changed or turn stopped", "no longer owns", "No active turn",
              "ended before this was approved", "cancelled this turn", "The agent turn has ended",
              "The parent consultation ended", "Blender chat disconnected")
_DECISION_FLAGS = ("expired", "revoked", "cancelled", "interrupted")
MAX_NOTE_ERROR = 400


def read_only(tool):
    return bool(TOOL_MAP[tool]["annotations"]["readOnlyHint"])


def is_lookup(tool):
    """A read that proves nothing by itself; a standalone verification is evidence instead."""
    return tool != VERIFY and read_only(tool)


def lookup_problem(result):
    """A lookup's error, or None. Its data (schemas, receipts, statuses) is never an outcome."""
    if not isinstance(result, dict):
        return NO_RESULT
    return str(result["error"]) if result.get("error") else None


def decided(result):
    """Whether a failed result is the person's or the host's decision rather than something to fix."""
    error = result.get("error") if isinstance(result, dict) else None
    return (not isinstance(error, str) or any(result.get(flag) for flag in _DECISION_FLAGS)
            or any(fragment in error for fragment in _DECISIONS))


def invalidates_scene_images(operation):
    name = operation["tool"]
    if read_only(name):
        return False
    # These closed tools do not modify live scene geometry. Do not spend six
    # new captures merely for task notes, a layout change or a private preview.
    if name in {"angelo_memory_update", "angelo_discard_preview", "angelo_set_workspace_mode"}:
        return False
    if name in PROGRAM_RESULT_TOOLS and operation["arguments"].get("mode") == "PREVIEW":
        return False
    if name.startswith(("angelo_studio_", "angelo_team_")) and name not in {"angelo_studio_import_3d", "angelo_team_library_import"}:
        return False
    return True


@dataclass
class PlanRun:
    """One plan's results, in order, and why it stopped early."""

    results: list = field(default_factory=list)
    completions: list = field(default_factory=list)  # Parallel, request-owned native proof; never result JSON.
    # (problem, status) of the edit, check or decision that stopped the plan.
    failure: tuple = None
    failed_lookups: list = field(default_factory=list)  # Indexes whose result is an error.
    program_images: set = field(default_factory=set)  # Automatic program previews, not requested views.
    refused: list = field(default_factory=list)  # Indexes of the refused lookups the plan reached: never run.
    refusals: dict = field(default_factory=dict)  # Index -> error of every operation refused alone, reached or not.


def run_operations(operations, call, current, evidence, references, *, completion=None, screened=None, before=None):
    """Run a validated plan in order; None when the request stopped owning the turn.

    ``call(operation)`` dispatches one operation and returns its host result;
    ``current()`` says whether the request still owns the turn. After a failed
    lookup the plan's edits do not run: the model wrote them without the
    result it asked for. ``screened`` (``plan_validation.ScreenedPlan``): at
    its first operation refused alone the plan stops, the operation answered
    with its error and never run (a refused job start as a start refused at
    admission); a normalised lookup also makes its further calls. A self-check
    never holds an operation back: jobs start whatever the checks say
    (``self_checks``); ``before(operation, done, results)`` runs ahead of each dispatch, and once with None after
    the plan (``self_checks.SelfChecks.sharer``). A cancel that leaves its job stopping lets it end, within
    ``STOP_WAIT``, before the plan's next step, so a fix after it never meets a busy scene (``until_stopped``).
    """
    run = PlanRun()
    refused = screened.results if screened is not None else {}
    run.refusals = {index: " ".join([_sentence(refused[index]["error"]), refused[index].get("view_budget", "")]).strip()
                    for index in sorted(refused)}
    try:
        return _run(operations, call, current, evidence, references, completion, before, run, screened)
    finally:
        if before is not None:
            before(None, operations, run.results)


def _run(operations, call, current, evidence, references, completion, before, run, screened):
    refused = screened.results if screened is not None else {}
    dispatched = screened.dispatched() if screened is not None else operations
    for index, operation in enumerate(operations):
        tool = operation["tool"]
        if run.failed_lookups and not read_only(tool):
            break
        if index in refused:   # The plan stops at its first operation refused alone (plan_validation).
            run.results.append(refused[index])
            run.completions.append(None)
            if index in screened.stops:   # A job start: a start refused at admission.
                run.failure = (refused[index]["error"], "failed")
            else:
                run.refused.append(index)
            break
        if not current():
            return None
        if before is not None:
            before(operation, operations[:index], run.results)
        if invalidates_scene_images(operation):
            evidence.invalidate()
        if tool in VISUAL_IMAGE_TOOLS:
            evidence.pin_cameras(dispatched)
        result = call(operation)
        if not current():
            return None
        if tool in VISUAL_IMAGE_TOOLS and not result.get("error"):
            evidence.admit(operation, result)
        if screened is not None and index in screened.companions:
            result = _normalized(result, screened.companions[index], screened.notes[index],
                                 call, current, evidence, dispatched)
            if result is None:
                return None
        if (tool.startswith(("angelo_studio_", "angelo_team_")) or tool in PREVIEW_IMAGE_TOOLS) and not result.get("error"):
            for record in references.tool_images(result):
                evidence.feedback["result/" + record["id"]] = record
                if tool in PROGRAM_RESULT_TOOLS:
                    run.program_images.add("result/" + record["id"])
        if tool == "angelo_job_cancel" and index + 1 < len(operations):
            result = until_stopped(result, current)
        run.results.append(result)
        run.completions.append(completion(result) if completion else None)
        if is_lookup(tool):
            problem = lookup_problem(result)
            if problem and decided(result):
                run.failure = (problem, "interrupted")
                break
            if problem:
                run.failed_lookups.append(index)
            continue
        problem = native_result_problem(result, verification=tool == VERIFY)
        if problem:
            run.failure = (problem, "interrupted" if result.get("error") else "failed")
            break
    return run


def _normalized(result, companions, note, call, current, evidence, dispatched):
    """A normalised lookup's result: its first call's and its further calls', with the note; None if no longer current.

    The calls are independent reads, so a further one is made after a part
    that failed, but not after one the person or the host decided (a stop, a
    document change); the note then says how many were made. A decided part's
    error and flags become the slot's, else the first failed part's, so the
    slot is read as the lookup's own decision or failure.
    """
    parts = [result]
    for extra in companions:
        if lookup_problem(parts[-1]) and decided(parts[-1]):
            break
        if not current():
            return None
        if extra["tool"] in VISUAL_IMAGE_TOOLS:
            evidence.pin_cameras(dispatched)
        part = call(extra)
        if not current():
            return None
        if extra["tool"] in VISUAL_IMAGE_TOOLS and isinstance(part, dict) and not part.get("error"):
            evidence.admit(extra, part)
        parts.append(part)
    if len(parts) <= len(companions):
        note += " Only %d of its %d calls were made: the host stopped after %s." % (
            len(parts), len(companions) + 1, lookup_problem(parts[-1]))
    merged = {"success": True, "normalized": note, "results": parts}
    failed = [part for part in parts if lookup_problem(part)]
    # A stop or a document change on any part outranks a plain failure of another.
    part = next((part for part in failed if decided(part)), failed[0] if failed else None)
    if part is not None:
        merged.update(success=False, error=lookup_problem(part))
        merged.update({flag: part[flag] for flag in _DECISION_FLAGS if isinstance(part, dict) and part.get(flag)})
    return merged


STOP_WAIT = 15.0   # Seconds a cancel waits for its job's stop: measured 0.3-3 s (long_jobs.rst, "Lifecycle").


def until_stopped(result, current, *, wait=time.sleep, clock=time.monotonic, limit=STOP_WAIT):
    """A cancel whose job is still stopping, once that job has ended (or ``limit`` passed): its result with the
    job's status then. Runs on the request's worker thread; Blender's main thread ends the job meanwhile."""
    if not isinstance(result, dict) or result.get("status") != "stopping" or not isinstance(result.get("job"), str):
        return result
    from .job_manager import shared
    from .job_store import ACTIVE
    jobs, deadline = shared(), clock() + limit
    while jobs is not None and current() and clock() < deadline:
        record = jobs.record(result["job"])
        if record is None or record.get("status") not in ACTIVE:
            return dict(result, status=record.get("status")) if record is not None else result
        wait(0.05)
    return result


def unchanged_failure(operations, results):
    """The error of the plan's last result when that edit changed nothing, else None.

    The gates are ``claude_outcomes.correctable_failure``'s, applied to the
    failed operation alone: never a result that may have changed the scene,
    needs cleanup, was declined, expired or stopped, or whose binding went
    stale.
    """
    index = len(results) - 1
    if index < 0 or index >= len(operations):
        return None
    return correctable_failure([operations[index]], [results[index]])


def raised_script(operations, results):
    """The error of a reviewed script that raised as the plan's last result, else None.

    Only the executor's own account counts (``script_errors.raised``): a
    decline, an expiry, Stop or a result the host never saw finish is a
    decision or an unknown, not a script error.
    """
    index = len(results) - 1
    if index < 0 or index >= len(operations) or operations[index]["tool"] != "angelo_execute_python":
        return None
    return results[index]["error"] if script_errors.raised(results[index]) else None


class Correction(NamedTuple):
    """How a failed edit goes back to the model."""

    row: dict  # The system row that tells the person why the turn goes on; None when they have read it already.
    prompt: str  # The whole next prompt when nothing ran before the failure; None when results follow stopped_note.
    script: bool = False  # A script that raised: counted apart from rejected and refused plans.


def counts_again(spent, run, screened, failure, operations, batch_result=None):
    """Whether a plan that ran whole (nothing refused, failed, rejected or counted at the screen) and changed the scene
    restarts both correction counts, within ``CLAUDE_REQUEST_CORRECTIONS`` (``spent``: corrections of both kinds before it).
    A batch is judged by its one ``batch_result``, as ``angelo_apply_batch``; a plan by its operations' results."""
    ran = ([{"tool": "angelo_apply_batch"}], [batch_result]) if batch_result is not None else (operations, run.results)
    return (not failure and not screened.counted() and not run.failed_lookups and not run.refused
            and spent + CLAUDE_PLAN_REJECTIONS <= CLAUDE_REQUEST_CORRECTIONS and changed_scene(*ran))

def changed_scene(operations, results):
    """Whether any of a plan's ``operations`` changed the scene, by its host result: a committed edit, or a
    script that ran and read back what it added, removed or modified. Lookups never count, whatever their data."""
    for operation, result in zip(operations, results):
        tool = TOOL_MAP.get(operation.get("tool")) if isinstance(operation, dict) else None
        if tool is None or tool["annotations"]["readOnlyHint"]:
            continue
        if not isinstance(result, dict) or result.get("error") or result.get("success") is False:
            continue
        changes = result.get("changes")
        if isinstance(changes, dict):
            # What the host read back decides: a committed program reports scene_changed even when its delta is
            # empty, and a no-op commit is no progress.
            if any(changes.get(key) for key in ("added", "removed", "modified")):
                return True
        elif result.get("scene_changed") is True:
            return True
    return False


def correction(operations, run, unchanged, used, scripts, limit=None):
    """The correction for a plan whose last result failed, or None when the turn ends.

    ``unchanged`` is the failed edit's error when it changed nothing
    (``unchanged_failure``, or a batch refused at dispatch). ``used`` is how many
    corrections of this failure's kind the request has had, of ``limit``: failed
    edits (``FAILED_EDIT_CORRECTIONS``, read when called) by default, rejected
    plans for a step refused at the screen;
    ``scripts`` counts raised scripts, and the request context also marks the
    script after its last one.
    """
    if raised_script(operations, run.results) is not None:
        # The request context has shown the person the error and what became of the script's changes.
        used = scripts >= script_errors.SCRIPT_CORRECTIONS or run.results[-1].get("script_corrections_used")
        return None if used else Correction(None, None, True)
    if unchanged is None or used >= (FAILED_EDIT_CORRECTIONS if limit is None else limit):
        return None
    if len(run.results) - len(run.refused) < 2:
        # Nothing ran before it: like a plan rejected before it ran, one corrected plan (with any lookup refused).
        return Correction(dict(notice("The plan stopped before changing the scene; asking for a corrected plan:\n{problem}",
                                      problem=unchanged), role="system"),
                          "\n".join([staged_rejection_prompt(unchanged, operations, CLAUDE_CORE_ACTIONS)]
                                    + refusal_lines(operations, run)))
    return Correction(dict(notice("The plan stopped at operations[{index}]; asking for the edits still needed:\n{problem}",
                                  index=len(run.results) - 1, problem=unchanged), role="system"), None)


def _span(first, last):
    return "operations[%d]" % first if first == last else "operations[%d..%d]" % (first, last)


def _sentence(error):
    """An error on one bounded line, ending with a full stop."""
    text = " ".join(str(error or "it failed").split())[:MAX_NOTE_ERROR]
    return text if text.endswith((".", "!", "?")) else text + "."


# The next round's first line after a plan whose operations all ran: a model reads it in every such round,
# so it is short (447 -> 280 B; doc/angelo/performance.rst, "Heavy-scene payloads"). The prefix stays: the
# harness and the program stress replay find continuation rounds by it.
CONTINUED = ("Host confirms these native operations completed. Use the results (live_revisions bind a next "
             "program), the observation and fresh views (one taken before a later edit is historical; visual_evidence "
             "gives image order). Summarize in reply; return operations only if the task still needs them.")


def stopped_note(operations, run, unchanged=None):
    """The next round's first line after a plan that stopped early or had a failed lookup.

    ``unchanged`` is the error of the edit that stopped the plan without
    changing anything (``unchanged_failure``); a script that raised is named
    with the state it left. The results follow the note as JSON, so every
    error is bounded here.
    """
    parts = ["The host ran your plan in order."] + refusal_lines(operations, run)
    for index in run.failed_lookups:
        parts.append("operations[%d] (%s) failed: %s" % (index, operations[index]["tool"],
                                                         _sentence(lookup_problem(run.results[index]))))
    if run.failed_lookups:
        parts.append("A failed lookup changes nothing.")
    index = len(run.results) - 1
    raised = raised_script(operations, run.results) if unchanged is None and run.failure else None
    if unchanged is not None:
        parts.append("operations[%d] (%s) failed and changed nothing: %s"
                     % (index, operations[index]["tool"], _sentence(unchanged)))
    if raised is not None and run.results[index].get("main_data_restored"):
        parts.append("operations[%d] (%s) raised: %s The host restored Blender's Main data to how it was before "
                     "the script, so sending a corrected script is safe; handles from before it are stale and the same "
                     "script unchanged fails the same way. Image pixels and external effects remain unverified. %s If "
                     "it wrote image pixels or files, inspect those first."
                     % (index, operations[index]["tool"], _sentence(raised), script_errors.OUTSIDE))
    elif raised is not None:
        parts.append("operations[%d] (%s) raised: %s What it changed before the error is still in the scene; nothing was "
                     "rolled back or undone, and its result says why and what its change list leaves out. Inspect what "
                     "it left before you write another script: remove or reuse it, and never assume the scene is as it was."
                     % (index, operations[index]["tool"], _sentence(raised)))
    if len(run.results) < len(operations):
        parts.append(_span(len(run.results), len(operations) - 1) + " did not run.")
    parts.append("The results of what ran, the current observation and any fresh views follow. Submit only the "
                 "operations the request still needs (each is approved again) and do not repeat the ones that completed.")
    return " ".join(parts)


def refusal_lines(operations, run):
    """What the next round says of the operations refused before the plan ran: each error, then what to do.

    Every one, including those after the operation that stopped the plan, so
    the model never sends a refused one again unchanged; the job start that
    stopped the plan is the failure the note names instead.
    """
    stopped = len(run.results) - 1 if run.failure else None
    lines = ["operations[%d] (%s) was refused before the plan ran, so it did not run and changed nothing: %s"
             % (index, operations[index]["tool"], error)
             for index, error in run.refusals.items() if index != stopped]
    return lines + ["Ask again with corrected arguments only for what you still need."] if lines else []


def ended_row(operations, run, problem):
    """The system row of a failure that ends the turn, or None when the person has read it already.

    The request context shows a script's error, what became of its changes
    and that the corrections are used (``script_errors.ScriptBudget``).
    """
    if raised_script(operations, run.results) is not None:
        marked = run.results[-1].get("script_corrections_used")
        return None if marked else dict(notice(script_errors.USED_UP), role="system")
    return {"role": "system", "text": problem}
