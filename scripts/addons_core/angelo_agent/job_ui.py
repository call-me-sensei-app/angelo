# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The background-job manager's home in Blender: its timer, file events, status bar and operators.

One ``JobManager`` serves every chat. Its timer runs only while a job is queued or
running, polls Blender's job on the main thread and redraws chat areas and the
status bar only when what they show changed. A job is independent of the chat
that started it: closing the chat, switching conversations or ending the turn
leaves it running; a file load or snapshot restore ends it (Blender stops its
jobs then). When a job ends, its chat gets a host-only notice row (never sent
to a model; the model reads the jobs block instead).

The status bar item shows the running job with Cancel, then a finished job with
Open for ``RECENT_SECONDS``. Director Mode hides the status bar; the chat card
(``job_chat``) shows the same state there.
"""

import time
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from .job_chat import MOVED_REASON as MOVED, title
from .job_manager import JobManager, install, shared
from .job_spec import PREVIEW, JobRequestError
from .job_store import TERMINAL, JobStore
from .ui_text import iface_, join, n_, plural_, rpt_

POLL_SECONDS = 0.25
RECENT_SECONDS = 30

_shown = {}
_recent = None  # (job id, monotonic deadline) of the last finished job, for the status bar.


def manager(create=True):
    if shared() is None and create:
        from . import ui
        jobs = JobManager(JobStore(ui.state_directory()), on_change=_changed, on_finish=_finished)
        _reconcile(jobs)
        install(jobs)
    return shared()


APP_CLOSED = n_("Angelo closed while this job ran, which stopped it. Nothing was published.")
APP_CLOSED_PUBLISHING = n_("Angelo closed while this job was publishing its output, which may be unfinished.")


def _reconcile(jobs):
    """Jobs a previous run left active: interrupted. An animation render keeps the frames it finished
    (``job_recovery``); any other job's staging is removed (only a folder carrying its marker)."""
    from . import job_outputs, job_recovery
    interrupted = jobs.store.interrupted(now=time.time())
    for record in interrupted:
        publishing = record["detail"].pop("publishing", None)   # Saved before its first link (Runner._announce).
        record["receipt"] = ({"note": APP_CLOSED_PUBLISHING, "output": publishing} if isinstance(publishing, str)
                             else {"note": APP_CLOSED})
        if isinstance(publishing, str) and record["kind"] == job_recovery.KIND:
            # Its folder holds part of the frames: recovery links the rest into it, never a second copy.
            record["detail"]["recover_into"] = publishing.rsplit("/", 1)[-1]
    for record in interrupted + [item for item in job_recovery.pending(jobs.store.records.values())
                                 if item not in interrupted]:
        try:
            if job_recovery.recover(record, jobs.store.save):
                continue
        except Exception as error:   # Never lose a startup to one record: its staging stays for the next start.
            print("Angelo: keeping a render's finished frames failed:", type(error).__name__, error)
            continue
        staging = record["detail"].pop("staging", None)
        if isinstance(staging, str) and record["kind"] != job_recovery.KIND:
            job_outputs.discard_path(Path(staging), record["id"])
        elif isinstance(staging, str):   # A render this can't keep: its folder stays as it is, never removed.
            record["receipt"] = {"note": job_recovery.LEFT_HIDDEN}
    for identity in [identity for identity, record in jobs.store.records.items() if record["kind"] == PREVIEW]:
        del jobs.store.records[identity]  # A look left by a crash: its call is gone, its settings are restored below.
    jobs.store.save()


def redraw():
    """Chat areas, and the status bar (a global area, outside ``Screen.areas``) through its job notifier."""
    for window in getattr(bpy.context.window_manager, "windows", ()):
        for area in window.screen.areas:
            if area.type == 'CONSOLE':
                area.tag_redraw()
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return
    _angelo_jobs.notify()


def _signature(record):
    eta = record.get("eta_s")
    return (record["status"], int(100 * float(record.get("progress") or 0)), None if eta is None else int(eta // 5),
            record["detail"].get("frames_done"))


def _changed(record):
    if _shown.get(record["id"]) != _signature(record):
        _shown[record["id"]] = _signature(record)
        redraw()
    from . import job_desktop
    job_desktop.sync(manager(create=False))  # Awake from a job's start to the end of the last one.
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)
    from . import look_notify
    look_notify.watch(record, _tick)  # A running look is finished the moment its render ends.


def _finished(record):
    global _recent
    # Its depsgraph updates ran on the job's thread and were not tracked: an unknown edit for every reader.
    from . import blender_tools, render_device, scene_queries
    render_device.observe(record)   # A finished Cycles render's pace on its device: the next one's estimate.
    scene_queries.invalidate(window=True)
    blender_tools.note_unknown_change()
    if record["kind"] == PREVIEW:  # A look: its own tool call reports it; no notice, record or status-bar entry.
        from . import view_render
        view_render.ended(manager(create=False), record)
        redraw()
        return
    _recent = (record["id"], time.monotonic() + RECENT_SECONDS)
    from . import job_desktop, ui
    job_desktop.ended(record)  # The operating system's notification of a long job's end.
    controller = ui._controllers.get(record["session"])
    if controller is None:
        return
    from .job_chat import stored_notice
    with controller.lock:  # Rows change only under the chat's lock, which every save holds (session_writer).
        controller.state["messages"].append({"role": "system", "notice": True, "text": stored_notice(record),
                                             "job": record["id"]})
    controller.version += 1
    try:
        controller.save()
    except OSError:
        pass
    redraw()


def _tick():
    jobs = manager(create=False)
    if jobs is None:
        return None
    try:
        working = jobs.poll()
    except Exception as error:  # Never let one job's failure stop the timer for the queue.
        print("Angelo: background job poll failed:", type(error).__name__, error)
        working = jobs.has_work()
    from . import job_desktop
    job_desktop.sync(jobs)
    if not working:
        redraw()
        return None
    return POLL_SECONDS


def busy_scenes():
    jobs = manager(create=False)
    runner = jobs.runners.get(jobs.running) if jobs and jobs.running else None
    return {runner.scene_uid} if runner is not None else set()


def running_jobs():
    jobs = manager(create=False)
    return {jobs.running} if jobs is not None and jobs.running else set()


@persistent
def _load_pre(_):
    jobs = manager(create=False)
    if jobs is not None and jobs.has_work():
        jobs.document_closed()


@persistent
def _load_post(_):
    from . import job_fluid, job_settings, render_drivers
    job_settings.restore_tags(busy_scenes())
    job_fluid.restore_tags(running_jobs())   # A fluid domain's cache state, set directly (job_fluid).
    render_drivers.document_loaded()  # Its drivers compile afresh under the current auto-execution setting.
    if not bpy.app.timers.is_registered(_keep_pending):
        bpy.app.timers.register(_keep_pending, first_interval=0.1, persistent=True)


def keep_soon():
    """Publish a render's kept frames shortly, after its record ends (a folder moved during the render)."""
    if not bpy.app.timers.is_registered(_keep_pending):
        bpy.app.timers.register(_keep_pending, first_interval=1.0, persistent=True)


def _keep_pending():
    """Publish the finished frames of a render the closed document stopped (Blender's job has ended by now)."""
    from . import job_recovery
    jobs = manager(create=False)
    if jobs is None:
        return None
    for record in job_recovery.pending(list(jobs.store.records.values())):
        if record["id"] == jobs.running:
            continue
        try:
            job_recovery.recover(record, jobs.store.save)
        except Exception as error:   # Its staging stays; the next start tries again.
            print("Angelo: keeping a render's finished frames failed:", type(error).__name__, error)
    redraw()
    return None


def _startup():
    from . import job_fluid, job_settings
    manager()
    job_settings.restore_tags(busy_scenes())
    job_fluid.restore_tags(running_jobs())
    return None


def output_path(record):
    """The published output's absolute path, if it exists."""
    value = record["detail"].get("output_path")
    path = Path(value) if value else None
    return path if path is not None and path.exists() else None


def output_file(record, name=None, frame=None):
    """The local file of one published output of a job, or None: not reported by this job, or moved since.

    ``name`` is the output's ``file`` as its receipt and the jobs block give it
    (``frame`` picks one frame of an animation's pattern); without a name the
    job's output itself, when that is one file. This is how a tool that shows a
    job's result reaches one view, File Output item, UDIM tile or baked map.
    """
    from .job_store import published_file
    return published_file(output_path(record), record, name, frame)


def output_frames(record, name):
    """The frames of an animation's output that exist, in order (a stopped job kept only some); [] for any other name."""
    from .job_store import published_frames
    return published_frames(output_path(record), record, name)


_resume_cache = {}   # Job ID -> ((folder, its change time), (first, last, count) or None): a card redraws often.


def resume_state(record):
    """``(count, reason)`` when an animation render's published folder lacks ``count`` of its frames and the person
    may render them (``reason`` None) or not now (a message id, the button's disabled reason); None otherwise."""
    from . import job_resume
    from .job_store import ACTIVE, output_names
    saved = job_resume.resume_facts(record)
    folder = output_path(record) if saved is not None else None
    if folder is None or not folder.is_dir():
        return None
    try:
        key = (str(folder), folder.stat().st_mtime_ns)
    except OSError:
        return None
    cached = _resume_cache.get(record["id"])
    if cached is None or cached[0] != key:
        cached = (key, job_resume.missing_frames(output_names(folder), saved))
        _resume_cache[record["id"]] = cached
    gap = cached[1]
    if gap is None:
        return None
    jobs = manager(create=False)
    later = jobs.record(record["detail"].get("resumed_by")) if jobs is not None else None
    if later is not None and later["status"] in ACTIVE:
        return gap[2], job_resume.RESUME_BUSY
    window = getattr(bpy.context, "window", None)
    scene = window.scene.name if window is not None and window.scene is not None else ""
    return gap[2], job_resume.resume_refusal(saved, blend=bpy.data.filepath, scene=scene)


def resume(record, controller, window):
    """Render the frames a render's folder lacks, into that folder: the person's click on its card is the approval
    (as F12 is). Admission is a job's own (camera, bounds, the files it would write); a refusal is kept on the record
    for the card. Returns the new record or None."""
    from . import job_resume, job_runners
    from . import job_render_outputs as render_outputs
    from .job_spec import JobRequestError
    from .job_store import output_names
    state = resume_state(record)
    saved = job_resume.resume_facts(record)
    if state is None or state[1] is not None or saved is None or controller is None or window is None:
        return None
    record["detail"].pop("resume_refused", None)
    try:
        if record["session"] != controller.state.get("id"):
            raise JobRequestError(job_resume.RESUME_OTHER_FILE)
        if Path(getattr(controller.files, "root", "")) != Path(saved["root"]):
            raise JobRequestError(job_resume.RESUME_OTHER_FILE)
        first, last, _count = job_resume.missing_frames(output_names(output_path(record)), saved)
        request = job_resume.resume_request(record, first, last)
        runner = job_runners.build(window, request, controller.files)
        refusal = job_resume.resume_refusal(saved, blend=bpy.data.filepath, scene=window.scene.name,
                                              signature=render_outputs.signature(runner.plan.outputs))
        if refusal is not None:
            raise JobRequestError(refusal)
        runner.skip_requested = True   # Its folder exists: it publishes into it, never under that name anew.
        runner.resume_into = output_path(record).name
        later = manager().submit(record["session"], "", request, runner)
    except JobRequestError as error:
        record["detail"]["resume_refused"] = str(error)[:300]
        manager().store.save()
        redraw()
        return None
    record["detail"]["resumed_by"] = later["id"]
    manager().store.save()
    redraw()
    return later


NO_JOB = n_("No such job")
ENDED = n_("Job {job} already ended")


def open_output(record):
    """Open the published file or folder; raise JobRequestError with the English ``MOVED`` when it is gone."""
    path = output_path(record)
    if path is None:
        raise JobRequestError(MOVED)
    bpy.ops.wm.path_open(filepath=str(path))


class ANGELO_OT_job_cancel(bpy.types.Operator):
    bl_idname = "angelo.job_cancel"
    bl_label = "Cancel Job"
    bl_description = "Stop this background job. Completed animation frames are kept; everything else is discarded"
    bl_options = {'INTERNAL'}
    job: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        record = manager().record(self.job)
        if record is None or record["status"] in TERMINAL:
            self.report({'WARNING'}, rpt_(NO_JOB) if record is None else rpt_(ENDED, job=self.job))
            return {'CANCELLED'}
        manager().cancel(self.job, by="user")
        redraw()
        return {'FINISHED'}


class ANGELO_OT_job_open(bpy.types.Operator):
    bl_idname = "angelo.job_open"
    bl_label = "Open Job Output"
    bl_description = "Open the file or folder this job wrote"
    bl_options = {'INTERNAL'}
    job: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        record = manager().record(self.job)
        if record is None or output_path(record) is None:
            self.report({'WARNING'}, rpt_(NO_JOB if record is None else MOVED))
            return {'CANCELLED'}
        open_output(record)
        return {'FINISHED'}


def status_item(jobs, now):
    """``(text, icon, action, job id)`` for the status bar, or None. Pure enough to test."""
    record = jobs.active() if jobs else None
    if record is not None:
        from .job_chat import progress_text
        queued = len(jobs.queue)
        text = join([title(record), progress_text(record)]
                    + ([plural_(queued, "{count} queued", "{count} queued")] if queued else []))
        return text, 'RENDER_ANIMATION', "cancel", record["id"]
    if _recent and now < _recent[1] and jobs is not None:
        record = jobs.record(_recent[0])
        if record is not None:
            from .job_chat import STATUS_LABELS
            text = join([title(record), iface_(STATUS_LABELS[record["status"]])])
            return text, 'CHECKMARK' if record["status"] == "done" else 'INFO', "open" if output_path(record) else "", record["id"]
    return None


def draw_status(self, context):
    item = status_item(manager(create=False), time.monotonic())
    if item is None:
        return
    text, icon, action, identity = item
    row = self.layout.row(align=True)
    row.separator()
    row.label(text=text, icon=icon, translate=False)
    if action == "cancel":
        row.operator(ANGELO_OT_job_cancel.bl_idname, text=iface_("Cancel"), icon='CANCEL', translate=False).job = identity
    elif action == "open":
        row.operator(ANGELO_OT_job_open.bl_idname, text=iface_("Open", "Angelo"), icon='FILE_FOLDER',
                     translate=False).job = identity


CLASSES = (ANGELO_OT_job_cancel, ANGELO_OT_job_open)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.STATUSBAR_HT_header.append(draw_status)
    bpy.app.handlers.load_pre.append(_load_pre)
    bpy.app.handlers.load_post.append(_load_post)
    bpy.app.timers.register(_startup, first_interval=1.0, persistent=True)  # Even if a file loads first.
    from . import render_drivers
    render_drivers.watch()  # When scripted drivers may have compiled with auto-execution on (render approval).


def unregister():
    from . import job_desktop, look_notify, render_drivers
    job_desktop.shutdown()
    look_notify.unregister()
    render_drivers.unwatch()
    for timer in (_tick, _startup, _keep_pending):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for collection, handler in ((bpy.app.handlers.load_pre, _load_pre), (bpy.app.handlers.load_post, _load_post)):
        if handler in collection:
            collection.remove(handler)
    bpy.types.STATUSBAR_HT_header.remove(draw_status)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
