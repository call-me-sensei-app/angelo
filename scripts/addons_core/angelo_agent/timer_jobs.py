# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Timer jobs: generators a timer resumes one step per pass never outlive the file, or Blender's data, they work on.

A job suspended between passes ends only when Python closes it, and its cleanup (a ``finally``, a ``with`` exit) runs
then. Left to Python, that was whenever its last reference went: after a load, in the new file (Blender drops a
discarded timer on its next pass); at quit, in interpreter finalization after ``BKE_blender_free``, where the tab
build's ``finally`` read the freed window manager (1.0.6 RC, compiled_gui: SIGSEGV under ``BPY_python_end``). So every
job a timer resumes is made with ``track`` (``test_native_timer_jobs_scan`` finds any that is not), and this module
ends each one still running:

- at ``load_post``, once a load has read the file (Blender discards the jobs' timers, which are non-persistent, only
  when the read succeeds: a failed load fires ``load_post_fail`` and keeps the file, the timers and the jobs, so
  nothing ends then; ``load_pre`` is too early for the same reason). Every job works on the windows' layout, so what
  counts is whether the load replaced it (``layout_kept``): replaced, the job closes with ``leaving()`` True and its
  cleanup touches nothing of the new file; kept (Load UI off, as Angelo's Restore opens a checkpoint: the
  workspaces stay), it closes as after any stop, and puts the layout back;
- at ``exit_pre``, before the quit file, the add-ons' unregister and ``BKE_blender_free``, with ``leaving()`` True from
  then on (sticky: Python's finalization comes later);
- at the add-on's ``unregister``, with the file staying: each cleanup puts the file back as after any stop.

A closed job reads as ``aborted`` to its timer step, which asks before resuming it: a closed generator's
StopIteration is never taken for a finished job. Its ``release`` (what the driver holds beyond the job, such as the
layout lock) runs whatever the cleanup did; it touches only Python state, never Blender's data.
"""

import sys

_jobs = {}   # Job -> None: running jobs, oldest first. Strong: a job leaves only once finished, failed or closed.
_state = {"leaving": False, "quitting": False, "layout": None}   # layout: the workspaces' identities at load_pre.


class Job:
    """A tracked job: resumed as an iterator (``next``), closed by its driver (``close``) or by this module."""
    __slots__ = ("generator", "release", "aborted", "pending")

    def __init__(self, generator, release):
        self.generator, self.release, self.aborted, self.pending = generator, release, False, None

    def __iter__(self):
        return self

    def __next__(self):
        try:
            value = next(self.generator)
        except BaseException:   # Finished (StopIteration, with its value) or failed: it runs no more.
            _jobs.pop(self, None)
            if self.pending is not None:   # Closed during this step: what it holds is released all the same.
                self.pending = None
                _release(self)
            raise
        if self.pending is not None:   # This step ran a load, a quit or unregister that closed it: it ends now.
            close(self, self.pending)
            return None   # Its timer stops.
        return value

    def close(self):
        """The driver ends it itself (an error, a step that gave up): its own code releases what it holds."""
        _jobs.pop(self, None)
        getattr(self.generator, "close", lambda: None)()   # A plain iterator has no cleanup to run.


def track(job, release=None):
    """``job``, the iterator (a generator) a timer resumes, as a ``Job``: closed at a load, a quit or unregister if it
    still runs then, and ``release()`` called then."""
    job = Job(iter(job), release)
    _jobs[job] = None
    return job


def aborted(job):
    """A load, a quit or unregister closed ``job``: its step stops without reading it as finished."""
    return getattr(job, "aborted", False)


def layout_kept():
    """During ``load_post``: whether the load kept the windows' layout, every workspace there before it still there
    (Load UI off swaps the old ones into the new file). Unknown (no ``load_pre`` seen, unreadable) reads as replaced:
    nothing is put back into a layout that may be another file's."""
    recorded = _state["layout"]
    return recorded is not None and recorded <= (_layout() or frozenset())


def _layout():
    try:
        import bpy
        return frozenset(w.session_uid for w in bpy.data.workspaces)
    except Exception:   # noqa: BLE001 - unknown: replaced (layout_kept)
        return None


def leaving():
    """The file is going away (a load, a quit) or Python is finalizing: a job's cleanup touches none of Blender's data,
    and never puts anything back into a file that is going away."""
    return _state["leaving"] or _state["quitting"] or sys.is_finalizing()


def close(job, leaving=True):
    """Close ``job`` now and call its release. ``leaving``: the file or its layout is going away; without it the
    layout stays (unregister, a load that kept it), and the job's cleanup puts it back as after any stop. A job whose
    own step is running (that step ran the load or the unregister) ends, and is released, once the step returns."""
    job.aborted = True
    if getattr(job.generator, "gi_running", False):
        job.pending = leaving
        return
    _jobs.pop(job, None)
    job.pending = None
    previous = _state["leaving"]
    _state["leaving"] = previous or leaving
    try:
        getattr(job.generator, "close", lambda: None)()
    except Exception as error:   # noqa: BLE001 - one job never stops a load, a quit or the others.
        print("Angelo: a timer job did not end cleanly:", error)
    finally:
        _state["leaving"] = previous
        _release(job)


def _release(job):
    if job.release is not None:
        try:
            job.release()
        except Exception as error:   # noqa: BLE001
            print("Angelo: a timer job's hold was not released:", error)


def close_all(leaving=True):
    """Every tracked job still running, closed (a load, a quit; ``leaving=False``: unregister)."""
    for job in list(_jobs):
        close(job, leaving)


def _load_pre(*_args):
    _state["layout"] = _layout()   # Nothing ends here: the load may still fail and keep everything.


def _load_post(*_args):
    close_all(leaving=not layout_kept())


def _exit_pre(*_args):
    _state["quitting"] = True   # Never reset: nothing after it may put anything back (quit.blend, finalization).
    close_all()


# As @persistent: a load never drops them. No bpy at import: any module may import this, host tests included.
_load_pre._bpy_persistent = _load_post._bpy_persistent = _exit_pre._bpy_persistent = True
HANDLERS = (("load_pre", _load_pre), ("load_post", _load_post), ("exit_pre", _exit_pre))


def register():
    """Early in each list (only the drivers' own handlers, registered later, come first): every job is closed, and its
    hold released, before the other handlers look."""
    import bpy
    for name, handler in HANDLERS:
        if handler not in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).insert(0, handler)


def unregister():
    """The add-on goes but the file stays: every job ends now, aborted, and puts the file back."""
    import bpy
    for name, handler in HANDLERS:
        if handler in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).remove(handler)
    close_all(leaving=False)
