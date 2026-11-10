# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Does a Blender job evaluate the document's scene on its own thread now? Then the main thread leaves it alone.

A render, a bake, an import or a remesh runs on a job thread that evaluates the document's scene (its depsgraph,
frame by frame) or writes the document's data. An evaluation on the main thread meanwhile would race it, so the
parallel sessions' live view holds its reads and pictures (``parallel_sessions._held_now``) and a binding does not
settle its window (``program_context._jobs_run``).

``_angelo_jobs.running_scene_jobs`` (``bpy_angelo_jobs_work.cc``) classifies every ``eWM_JobType`` in a switch that
does not build while a type is missing, and counts running and suspended jobs of any owner; unsure is true. A build
without it keeps each caller's closed list below, the answers before 1.0.10. Main thread only.
"""


def running(bpy, fallback):
    """The types of running jobs that evaluate the document's scene (truthy), or ``fallback(bpy)`` on a build without
    the native classification. Errors propagate: each caller treats one as a job running (fail closed)."""
    try:
        from _bpy import _angelo_jobs
        running_scene_jobs = _angelo_jobs.running_scene_jobs
    except (ImportError, AttributeError):
        return fallback(bpy)
    return tuple(running_scene_jobs())


def live_view_fallback(bpy):
    """The live view's closed list: Blender's renders, previews, bakes and composites, Angelo's point cache,
    geometry-nodes bake and export jobs, and fluids."""
    from . import work_guard
    if work_guard._render(bpy):
        return True
    from _bpy import _angelo_jobs
    return any(_angelo_jobs.progress(scene, "FLUID") is not None for scene in bpy.data.scenes)


EVALUATING_JOBS = ("RENDER", "OBJECT_BAKE")   # The bindings' closed list of Blender's own job types.


def settle_fallback(bpy):
    """The bindings' closed list: Blender's renders and bakes, Angelo's point cache, geometry-nodes bake and export
    jobs (material previews and compositing were left out)."""
    if any(bpy.app.is_job_running(kind) for kind in EVALUATING_JOBS):
        return True
    from . import work_guard
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return False
    return any(_angelo_jobs.progress(scene, kind) is not None for scene in bpy.data.scenes
               for kind in work_guard.OTHER_JOBS)
