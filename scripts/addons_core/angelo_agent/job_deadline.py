# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A job's time budget enforced on the job's own thread, and the memory a Cycles job used.

Contract: doc/angelo/long_jobs.rst, "Time budget". The manager's timer checks the budget on the main thread,
which a slow main thread delays (on Metal it is paced by the GPU; a starved GPU held one budget for hours). So
the manager also sets the budget as a deadline that the job's own break check reads on the job's thread
(``_bpy._angelo_jobs.deadline``, ``BKE_angelo_job_deadline.hh``): the job stops itself when it runs out. The
deadline belongs to that one job (its kind and scene): a render or bake the person runs meanwhile never reads it,
and the job's end clears it natively, so it cannot outlive the job (a reload or a dropped manager included). A
job is recorded as stopped by its budget only when its break check returned true because of the deadline
(``deadline_fired``); a job that finished on its own, however late its end is seen, keeps its output.

``observe_memory`` keeps the largest memory a running Cycles job's render reported (``render_stats``) in the
record, so a finished render says what it used. A build without these native entries keeps the timer alone.
"""

import math

# The agent's looks (RENDER_PREVIEW) are not here: their budget ends them once they have samples to show, which
# the manager decides (view_render), not a hard stop.
DEADLINE_KINDS = {"RENDER_STILL": "RENDER", "RENDER_ANIMATION": "RENDER", "RENDER_REGION": "RENDER",
                  "BAKE_TEXTURE": "OBJECT_BAKE", "BAKE_MAPS": "OBJECT_BAKE"}
# Kinds whose Cycles session is their own: a render, or a texture bake (one map or a set).
CYCLES_KINDS = frozenset({"BAKE_TEXTURE", "BAKE_MAPS"})


def _native():
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return None
    # deadline_fired came with the per-job deadline; a build without it has no deadline this module can use.
    return _angelo_jobs if hasattr(_angelo_jobs, "deadline_fired") else None


# What a native call raises for a scene that is gone (deleted or reloaded) or a value it refuses.
_NATIVE_ERRORS = (ReferenceError, TypeError, ValueError, RuntimeError)
ARMED = "deadline_armed"   # Local detail: this job's own deadline was set, so deadline_fired can be its.


def arm(record, scene):
    """Set the deadline of the render or bake job about to start in ``scene`` (the budget counts from its start).
    Best effort: it never decides a start. A scene that is gone raises here, and the runner's own admission refuses
    it with its reason (break-it review 2026-10-23, C1); without a deadline the manager's timer keeps the budget.
    Only a job whose deadline was set can be recorded as stopped by it (``ended_by_budget``)."""
    record["detail"].pop(ARMED, None)
    native = _native()
    job_type = DEADLINE_KINDS.get(record["kind"])
    if native is None or job_type is None or scene is None:
        return
    try:
        native.deadline(scene, job_type, max(0.1, float(record["request"]["minutes"]) * 60))
    except Exception:   # Whatever it raises, the start goes on (the timer keeps the budget).
        return
    record["detail"][ARMED] = True


def rearm(scene, job_type, seconds):
    """A job that runs several of Blender's jobs in turn (a texture set's bakes) sets the rest of its budget before
    each: Blender clears a deadline when the job that read it ends. Best effort, as ``arm``."""
    native = _native()
    if native is None or scene is None:
        return
    try:
        native.deadline(scene, job_type, max(0.1, float(seconds)))
    except Exception:
        pass


def disarm(record, scene):
    """Clear the deadline of this job (its kind and scene) only. Another job's, such as a dropped manager's render
    after Reload Scripts, stays until that job's own end clears it natively."""
    native = _native()
    job_type = DEADLINE_KINDS.get(record["kind"])
    if native is None or job_type is None or scene is None:
        return
    try:
        native.deadline(scene, job_type, None)
    except _NATIVE_ERRORS:   # Its scene is gone: so is its job, whose end cleared the deadline.
        pass


def ended_by_budget(record):
    """True for a running job whose break check stopped it because its deadline passed (never one that finished)."""
    native = _native()
    # deadline_fired belongs to the last deadline set: only this job's if this job set it (a failed arm leaves an
    # earlier job's flag; review round 3).
    return (native is not None and record["status"] == "running" and record.get("started") is not None
            and record["kind"] in DEADLINE_KINDS and record["detail"].get(ARMED) is True
            and bool(native.deadline_fired()))


def observe_memory(record, runner):
    """The running Cycles job's peak memory so far, in MB, kept in ``detail["peak_memory_mb"]``."""
    detail = record["detail"]
    if detail.get("engine") != "CYCLES" and record["kind"] not in CYCLES_KINDS:
        return
    from . import job_render_child
    peak = job_render_child.peak_mb(runner)
    if isinstance(peak, int) and peak > detail.get("peak_memory_mb", 0):   # A child reports its own renderer's.
        detail["peak_memory_mb"] = peak
    if getattr(runner, "child", None) is not None:
        return   # This scene's Render here holds no stats of the child's render.
    native = _native()
    scene = getattr(runner, "scene", None)
    if native is None or not hasattr(native, "render_stats") or scene is None:
        return
    if "first_progress_s" not in detail:
        # The scene's Render keeps its last render's stats until this job's renderer reports: before its first
        # progress, the peak may be an earlier render's (the person's, or a render before a bake; review round 2).
        return
    try:
        stats = native.render_stats(scene)
    except (TypeError, ReferenceError, RuntimeError):
        return
    peak = (stats or {}).get("mem_peak_mb")
    if isinstance(peak, int) and peak > detail.get("peak_memory_mb", 0):
        detail["peak_memory_mb"] = peak


def catch_up(runner):
    """What a render job reported after ``runner``'s last poll: Blender frees a job's progress with the job, so a
    first sample that came after the last poll was unseen (1.0.7: a look stopped then dropped the partial Blender had
    written, and had no prepare_seconds). The job's own thread keeps its last report (``last_progress``,
    ``BKE_angelo_job_progress.hh``); this raises ``runner.progress`` to it and sets ``runner.first_sample`` (seconds
    after the start) when the polls missed it. A build without it changes nothing."""
    try:
        from _bpy import _angelo_jobs as native
    except ImportError:
        return
    scene = getattr(runner, "scene", None)
    try:
        report = native.last_progress(scene, runner.job_type) if scene is not None else None
    except (AttributeError, *_NATIVE_ERRORS):   # AttributeError: a build without the record.
        return
    if report is None:
        return
    progress, since_first = report
    runner.progress = max(runner.progress, min(1.0, progress))
    if runner.first_sample is None and since_first is not None:
        runner.first_sample = min(runner.seconds, max(0.0, runner.seconds - since_first))


# A one-time Cycles kernel compile (angelo_render_device.kernels) is credited to a job's budget, at most its cap: the
# first render after an update compiles for minutes, and a short budget the agent chose was spent on it.
COMPILE_PHASE = ("Cycles is loading or compiling its render kernels, once after an update or on a new GPU; it can "
                 "take a few minutes and is not counted against the time budget")


def reset_kernels():
    """A job starts: the kernel watch is its render's from here (an earlier render's compile is never credited to it,
    break-it F1). Called before the runner starts its render (``JobManager._start``)."""
    try:
        from angelo_render_device import kernels
    except ImportError:
        return
    kernels.WATCH.reset()


def kernel_state(record, runner):
    """``(loading, compile_s)`` of a running Cycles render job's render: its child's (job_render_child), else this
    application's (``kernels.WATCH``). None for any other job: a Cycles bake says no render stats, so it has no compile
    of its own to credit, and is never credited another render's (break-it F1, F7)."""
    detail = record["detail"]
    if detail.get("engine") != "CYCLES" or record["kind"] in CYCLES_KINDS:
        return None
    own = getattr(runner, "kernels", None)
    if callable(own):
        return own()
    child = getattr(runner, "child", None)
    if child is not None:
        return child.kernels()
    try:
        from angelo_render_device import kernels
    except ImportError:
        return None
    return kernels.WATCH.loading(), kernels.WATCH.compile_s()


def budget_s(record):
    """The job's budget in seconds: its minutes, plus the kernel compile its render paid, credited up to the cap."""
    from angelo_render_device import kernels
    return float(record["request"]["minutes"]) * 60 + kernels.credited(record["detail"].get("kernel_compile_s"))


def observe_kernels(record, runner, elapsed):
    """Record the running job's kernel compile (``kernels_loading``, ``kernel_compile_s``) and move its deadline by the
    credit: the native deadline of a render here, the watchdog of a child's. Best effort, as ``arm``."""
    state = kernel_state(record, runner)
    if state is None:
        return
    detail = record["detail"]
    if "first_progress_s" in detail:
        # Only what came before its first progress is the render's kernel load: Cycles' status text can carry a view
        # layer's name while it denoises and finishes, and a name can be the status itself (r2 re-review R1).
        detail["kernels_loading"] = False
        return
    loading, seconds = bool(state[0]), float(state[1] or 0.0)
    if not math.isfinite(seconds) or seconds < 0:   # Never a value the store or the model can't take (break-it F10).
        loading, seconds = False, 0.0
    detail["kernels_loading"] = loading
    if seconds > 0:
        detail["kernel_compile_s"] = round(seconds, 1)
    else:
        detail.pop("kernel_compile_s", None)
    from angelo_render_device import kernels
    credit = kernels.credited(seconds)
    if abs(credit - detail.get("deadline_credit_s", 0.0)) < 1.0:
        return   # The deadline moves in whole seconds: no native call for every poll.
    detail["deadline_credit_s"] = credit
    child = getattr(runner, "child", None)
    if child is not None and callable(getattr(child, "credit", None)):
        child.credit(credit)
    elif detail.get(ARMED) and record["kind"] in DEADLINE_KINDS and record["status"] == "running":
        # Never once the deadline fired: setting one clears the fired flag, and the stop would read as a finish
        # (break-it F6).
        native = _native()
        if native is not None and not native.deadline_fired():
            rearm(getattr(runner, "scene", None), DEADLINE_KINDS[record["kind"]], max(0.1, budget_s(record) - elapsed))


def kernel_note(detail):
    """What the agent is told while the render compiles its kernels (model-facing English), else ""."""
    return COMPILE_PHASE if detail.get("kernels_loading") else ""
