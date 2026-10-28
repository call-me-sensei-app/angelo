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

# The agent's looks (RENDER_PREVIEW) are not here: their budget ends them once they have samples to show, which
# the manager decides (view_render), not a hard stop.
DEADLINE_KINDS = {"RENDER_STILL": "RENDER", "RENDER_ANIMATION": "RENDER", "BAKE_TEXTURE": "OBJECT_BAKE"}


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
    if detail.get("engine") != "CYCLES" and record["kind"] != "BAKE_TEXTURE":
        return
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
