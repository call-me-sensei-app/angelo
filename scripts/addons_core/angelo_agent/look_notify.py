# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Finish a RENDERED look as soon as Blender's render job ends (on by default; ``ANGELO_LOOK_NOTIFY=0`` turns it off).

The job manager's timer (``job_ui.POLL_SECONDS``, 0.25 s, started with the
job) finishes every job. For a look that adds what remains of that interval
after Blender has ended the render job: 0 to 0.25 s (about 0.125 s on average
over look lengths); a tiny scene's look takes about 0.31 s in all. While a look renders, ``watch`` runs a timer every
``SECONDS`` that makes one native call (``_angelo_jobs.progress`` for the look's
scene) and, the moment Blender reports no render job for it, runs the manager's
tick at once. The 0.25 s timer stays as the fallback and for every other job.
Main thread only.
"""

from . import product_env

FLAG = "LOOK_NOTIFY"
SECONDS = 0.02  # The chat pump's own interval while a turn is busy.

_finish = None  # job_ui's tick, given by ``watch``.


def enabled(environ=None):
    # On by default since 1.0.5 (render-prep-reuse PREREG rule E5, measured on a light scene; performance.rst).
    return product_env.get(FLAG, environ, default="1") != "0"


def _manager():
    from .job_manager import shared
    return shared()


def _timer():
    from .job_runners import native
    from .job_spec import PREVIEW
    jobs = _manager()
    record = jobs.active() if jobs is not None else None
    runner = jobs.runners.get(record["id"]) if record is not None else None
    if runner is None or record["kind"] != PREVIEW:
        return None
    try:
        if native().progress(runner.scene, runner.job_type) is not None:
            return SECONDS
    except (ReferenceError, RuntimeError, TypeError):
        pass  # The scene went away: the manager's tick says what became of the look.
    if _finish is not None:
        _finish()
    return None


def watch(record, finish):
    """A look is running: watch for its end. ``finish`` is the manager's tick (``job_ui._tick``)."""
    global _finish
    from .job_spec import PREVIEW
    if not enabled() or record.get("kind") != PREVIEW or record.get("status") != "running":
        return
    import bpy
    _finish = finish
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=SECONDS)


def unregister():
    import bpy
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
