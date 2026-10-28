# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The job tools on the agent's executor, and the scene guard while a job runs.

``precondition`` admits a request before the person sees its approval (the same
admission ``execute`` repeats), so an approval never offers what will be refused.
``execute`` hands an approved request to the shared ``JobManager`` and returns at
once with the job's ID. Only explicitly classified preconditions prove that a
refusal changed nothing and may be corrected inside the turn
(``claude_outcomes.correctable_failure``). Unclassified startup failures retain
uncertainty about scene effects and recovery. ``guard`` is called by
``BlenderExecutor`` before every
other tool: while a job runs, a tool that would change scene data is refused,
and while a job locks Blender's interface (physics and simulation bakes,
USD/Alembic exports, a render with Lock Interface) only tools that read no
scene data run, because the job's thread drives the scene's own depsgraph.
"""

import json
import os

from .job_spec import JOB_NAMES, LOCKING, JobRequestError, validate_job
from .refusal_codes import error_result, refusal_code
from .ui_text import n_

LOCKED_TURN = n_("Angelo is locked while job {job} runs. Send your message when it ends, or cancel it from its card.")
# The start receipt's ``workspace``: which folder ``output`` is relative to, said without a local path. It is the
# file tools' workspace, whose root the context names once (context_ledger.TurnBlocks.workspace).
WORKSPACE_PROJECT = "the file tools' workspace: the folder the open .blend file is saved in"
WORKSPACE_CHAT = ("the file tools' workspace: Angelo's own folder for this chat, because the .blend file is not saved "
                  "yet (the job's card has an Open button for the result)")

# Tools that touch no Blender data: allowed even while the interface is locked.
SCENE_FREE = frozenset({"angelo_agent_capabilities", "angelo_blender_api", "angelo_agent_metrics"})
# Not read-only, yet never write scene data: allowed beside a running job.
SAFE_BESIDE_JOB = frozenset({"angelo_set_workspace_mode", "angelo_studio_generate", "angelo_studio_enhance",
                             "angelo_studio_create_folder", "angelo_studio_move_asset", "angelo_studio_stop",
                             "angelo_studio_export"})


class JobBusyError(RuntimeError):
    """A tool refused because a background job is using the scene; nothing ran."""
    partial_changes_possible = False
    refusal_code = "WRITER_BUSY"


def _manager():
    from .job_manager import shared
    return shared()


def busy_message(record, locked):
    progress = round(100 * float(record.get("progress") or 0))
    if locked:
        return ("Job %s (%s, %d%%) has locked Blender while it runs, so scene tools wait until it ends. "
                "Tell the person, or cancel it with angelo_job_cancel." % (record["id"], record["kind"], progress))
    return ("Job %s (%s, %d%%) is running, so scene changes wait until it ends. "
            "Tell the person, or cancel it with angelo_job_cancel." % (record["id"], record["kind"], progress))


def guard(name, arguments=None):
    """Raise JobBusyError if ``name`` may not run beside the active job."""
    manager = _manager()
    record = manager.active() if manager is not None else None
    if record is None or name in JOB_NAMES:
        return
    if name == "angelo_view" and isinstance(arguments, dict):
        import re
        from .view_spec import JOB, LAST_JOB
        image = arguments.get("image")
        image = image.partition(":")[0] if isinstance(image, str) else image  # A pass after the colon.
        if image == LAST_JOB or isinstance(image, str) and re.fullmatch(JOB, image):
            return  # Only job records are read while waiting; the finished picture repeats the full guard.
    from .tool_specs import TOOL_MAP
    locked = manager.locks_interface()
    if name in SCENE_FREE:
        return
    if not locked and (TOOL_MAP[name]["annotations"]["readOnlyHint"] or name in SAFE_BESIDE_JOB):
        return
    raise JobBusyError(busy_message(record, locked))


def refuse_turn_while_locked():
    """A new request needs the scene for its observation and snapshot; wait while a job locks it."""
    manager = _manager()
    record = manager.active() if manager is not None else None
    if record is not None and manager.locks_interface():
        # The chat's error row stores the English sentence and shows the template translated (display_text).
        from .display_text import Text
        raise RuntimeError(Text(LOCKED_TURN, job=record["id"]))


def _admit(controller, window, arguments):
    """Validate one start request and build its runner (raises JobRequestError)."""
    if controller is None or window is None:
        raise JobRequestError("Bind a conversation and a Blender window before starting a job", code="OWNERSHIP")
    from . import job_runners, native_programs
    staging = getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)
    if staging():
        raise JobRequestError("A staged change is still in progress; finish or stop it before starting a job", code="WRITER_BUSY")
    request = validate_job("angelo_job_start", arguments)
    target, resolved = None, {}
    from .scene_queries import resolve_object, SceneQueryError
    try:
        if "target" in request:
            target = resolve_object(window, request["target"])
        if "sources" in request:
            resolved["sources"] = [resolve_object(window, handle) for handle in request["sources"]]
        if "cage" in request:
            resolved["cage"] = resolve_object(window, request["cage"])
    except SceneQueryError as error:
        raise JobRequestError(str(error), code=refusal_code(error)) from None
    if "image" in request:
        from .action_world import resolve_image
        try:
            resolved["image"] = resolve_image(request["image"])
        except ValueError as error:  # Expired with its document, or no longer loaded.
            raise JobRequestError("image: " + str(error).split(": ", 1)[-1], code=refusal_code(error)) from None
    runner = job_runners.build(window, request, controller.files, target, resolved)
    output = getattr(runner, "output", None)
    # A job never moves, renames or replaces a file: a taken name is no refusal, the job writes under the next free
    # name, and never under the taken one, even if it is freed before the job ends. Its set's names beside it count.
    runner.skip_requested = output is not None and any(
        os.path.lexists(path) for path in [output, *(output.parent / name for name in getattr(runner, "beside", ()))])
    if runner.skip_requested:
        name = getattr(runner, "output_name", None) or request["output"]
        if output_location(controller, name, next_identity(), getattr(runner, "publishes", "folder"), True)["writes"] \
                is None:   # Every name it could take is taken: said now, not after the job ran.
            raise JobRequestError("output %s and every name tried after it are taken; choose another name" % name,
                                  code="VALIDATION")
    return request, runner


class Precondition(tuple):
    """What an approval's click compares (the scene, the request, the admission facts), and ``shown``: what the card
    also says that may change by the click without changing what was approved. Where a taken name's output goes
    depends on the next job ID and on the disk at dispatch; the job never overwrites, and its result names the path
    (``_execute``), so a moved ID or a name taken meanwhile never refuses the approval."""
    shown = None


def output_location(controller, output, identity, publishes, skip_requested=False):
    """Where a started job's ``output`` lands, in words the agent can pass on, with no local path.

    ``output`` is relative to the chat's file workspace: the saved .blend's folder,
    or Angelo's own folder for the chat while the .blend is unsaved
    (``ui.prepare_controller``). ``writes`` is the name it writes under: ``output``,
    or the next free name when ``output`` is taken (``skip_requested``, or anything
    there now); ``if_taken`` is the one after it, should that be taken too as the
    job ends (``job_output_names``). Recorded runs show the agent otherwise tells
    the person it does not know where the file is or whether its name is final.
    """
    from pathlib import Path, PurePosixPath
    from .job_output_names import free_names
    files = getattr(controller, "files", None)
    root, own = getattr(files, "root", None), getattr(controller, "workspace", None)
    folder = Path(root, *PurePosixPath(output).parent.parts) if root is not None else None
    requested = PurePosixPath(output).name

    def taken(entry):
        return entry == requested and skip_requested or folder is not None and os.path.lexists(folder / entry)
    writes, if_taken = free_names(output, identity, publishes, taken)
    location = {"writes": writes, "if_taken": if_taken}
    if root is not None:
        location["workspace"] = (WORKSPACE_CHAT if own is not None and Path(root) == Path(own) else WORKSPACE_PROJECT)
    return location


def next_identity():
    """The ID the next job will have, when nothing reserved one."""
    jobs = _manager()
    return jobs.store.peek() if jobs is not None and hasattr(jobs.store, "peek") else "j?"


_RESERVED = {}   # (chat, request) -> the job ID its approval card names; taken by that job when it is dispatched.


def _reservation(controller, request, take=False):
    """The job ID an approval card reserved for this request, reserving one now (an approval whose output name is
    taken names the path with it, so two pending cards never name the same one); ``take``: the dispatch takes it."""
    key = (controller.state.get("id"), json.dumps(request, sort_keys=True))
    if take:
        return _RESERVED.pop(key, None)
    if key not in _RESERVED:
        jobs = _manager()
        if jobs is None or not hasattr(jobs.store, "allocate"):
            return next_identity()
        while len(_RESERVED) >= 32:   # Cards declined or expired: their IDs are simply never used.
            _RESERVED.pop(next(iter(_RESERVED)))
        _RESERVED[key] = jobs.store.allocate()
    return _RESERVED[key]


def precondition(controller, window, name, arguments):
    if name == "angelo_job_cancel":
        return None
    request, runner = _admit(controller, window, arguments)
    from . import job_ui, render_extensions
    job_ui.manager().admit(request)  # Never offer an approval the queue would refuse.
    # The extension code it would run, with its fingerprint: a change before dispatch is a changed precondition. Where
    # a taken name's output goes is shown only (Precondition.shown).
    facts = dict(runner.describe(), **render_extensions.facts(runner))
    shown, name = None, getattr(runner, "output_name", None) or request.get("output")
    if getattr(runner, "output", None) is not None:   # The admitted name, as the job writes it.
        shown = {"requested": name}
    if getattr(runner, "skip_requested", False):
        identity = _reservation(controller, request)
        shown.update(job=identity, taken=os.path.lexists(runner.output), writes=output_location(
            controller, name, identity, getattr(runner, "publishes", "folder"), True)["writes"])
    checks = list(getattr(controller, "open_self_checks", None) or ())[:8]
    if checks:   # A build whose own checks fail now: the approval states it, as a fact (self_checks).
        facts["open_checks"] = checks
    result = Precondition((window.scene.session_uid, json.dumps(request, sort_keys=True),
                           json.dumps(facts, sort_keys=True)))
    result.shown = shown
    return result


def execute(controller, window, name, arguments):
    """Start or cancel a job; retain classified refusal or uncertain failure facts."""
    try:
        return _execute(controller, window, name, arguments)
    except JobRequestError as error:  # Only an explicit code can establish an unchanged admission refusal.
        return error_result(error, scene_changed=False)


def _execute(controller, window, name, arguments):
    from . import job_ui
    manager = job_ui.manager()
    if name == "angelo_job_cancel":
        record = manager.cancel(validate_job(name, arguments)["job"], by="agent", session=controller.state["id"])
        return {"job": record["id"], "status": record["status"], "scene_changed": False}
    request, runner = _admit(controller, window, arguments)
    call = getattr(controller, "executing_call", None)
    shown = getattr(getattr(call, "precondition", None), "shown", None) or {}
    if shown.get("writes") and getattr(runner, "output", None) is not None:
        runner.skip_requested = True   # A name the approval said is taken is never written, even if freed since.
    reserved = _reservation(controller, request, take=True) if shown.get("job") else None
    record = manager.submit(controller.state["id"], getattr(call, "id", ""), request, runner,
                            **({"identity": reserved} if reserved else {}))
    result = {"job": record["id"], "status": record["status"], "kind": request["kind"],
              "note": "Runs in the background. Its progress and result arrive in a later message's jobs block; "
                      "do not sleep or poll. Scene changes are refused until it ends."}
    if request["kind"] in ("RENDER_STILL", "RENDER_ANIMATION"):
        result["note"] += (" To see its picture in this turn, call angelo_view with shading RENDERED and image %s: "
                           "that call waits for the job, within bounds." % record["id"])
        for key in ("device", "device_why", "gpu_available", "estimate_s", "estimate_from"):
            if (record.get("detail") or {}).get(key):   # The device it renders on (render_device); an estimate.
                result[key] = record["detail"][key]
    if record["status"] in ("queued", "starting", "running"):
        result["cancel"] = "angelo_job_cancel with job %s stops it" % record["id"]
    if record["status"] == "failed":
        # This is the new submission's actual startup receipt, not a replayed
        # history record. Unclassified native/cleanup failures remain uncertain.
        receipt = record["receipt"]
        result = {"job": record["id"], "status": "failed", "error": record["reason"],
                  "error_code": refusal_code(receipt), "note": receipt.get("note", "")}
        if (result["error_code"] not in {"UNKNOWN", "INTERNAL_ERROR", "CLEANUP_FAILED", "RECOVERY_REQUIRED"}
                and receipt.get("scene_changed") is False and not receipt.get("partial_changes_possible")):
            result["scene_changed"] = False
        else:
            result["partial_changes_possible"] = True
    elif record["status"] == "queued":
        result["position"] = len(manager.queue)
    if request.get("output"):
        name = getattr(runner, "output_name", None) or request["output"]  # A folder for several files.
        location = output_location(controller, name, record["id"], getattr(runner, "publishes", "folder"),
                                   getattr(runner, "skip_requested", False))
        writes = location.pop("writes") or name
        result["output"] = writes if record["status"] != "failed" else name
        if writes != name and record["status"] != "failed":   # Never a refusal: the result names where it goes.
            result["requested"] = name
            output = runner.output
            if os.path.lexists(output):
                why = "%s already exists and is never overwritten" % name
            elif any(os.path.lexists(output.parent / item) for item in getattr(runner, "beside", ())):
                why = "A name its export writes beside %s is taken and never overwritten" % name
            else:
                why = "%s was taken when this job was approved" % name
            result["note"] += " %s, so this job writes %s." % (why, writes)
            record["detail"]["writes"] = writes   # Named so while it runs (job_store.output_name).
            getattr(getattr(manager, "store", None), "save", lambda: None)()
        if record["status"] == "failed":   # Nothing ran: the name asked for, and its first fallback, as before.
            from .job_output_names import fallback_name
            location["if_taken"] = fallback_name(name, record["id"], getattr(runner, "publishes", "folder"))
        result.update(location)
    if request["kind"] in LOCKING or manager.locks_interface():
        result["note"] += " Blender's interface stays locked while it runs."
    return result
