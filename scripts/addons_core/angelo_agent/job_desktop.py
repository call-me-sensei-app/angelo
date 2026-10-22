# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a background job does outside Angelo's window: keeps the computer awake, and says when it ended.

A render, bake or export can run for hours, and the person often walks away.
Two things help, both on by default and each with a preference
(``job_keep_awake``, ``job_notifications``: ``job_desktop_prefs``, switched in
Preferences > AI Providers, Background jobs):

* **Awake while a job runs.** From the moment a job starts until no job is
  queued or running, the system is held awake (``desktop_os.KeepAwake``): idle
  sleep would otherwise suspend the render. The display may still sleep. A sleep
  the person asks for (a closed lid, the Sleep command) is not prevented. The
  agent's looks (``job_spec.PREVIEW``) are short and never hold it.
* **Told when a long job ends.** A job that ran for ``NOTIFY_AFTER_SECONDS`` or
  more and ended on its own (done, failed, or stopped by its time budget; never a
  cancel by the person or the agent, a file load or a crash) posts the operating
  system's own notification: Notification Center on macOS
  (``_bpy._angelo_jobs.desktop_notify``), under Angelo's name and icon. Where
  notifications are unavailable or not allowed, Angelo asks for attention instead
  (the Dock icon bounces once; on Windows the taskbar button flashes until Angelo
  comes to the front). macOS asks the person once whether Angelo may post
  notifications: when the first job starts, while they are at the computer to
  answer. The permission is read again whenever a job starts.
* **Never in an automation run** (``app_notices.automation_off``): no question,
  notification or attention, unless ``ANGELO_ENABLE_JOB_NOTIFICATIONS=1`` (the
  suite that proves them).

The notification is UI text: translated into the interface language, never sent
to a model (the model reads the jobs block), in reading order for the system
(``for_the_system``: the Arabic catalogue is stored in visual order). It holds
only fixed words, the duration and the output's workspace-relative name: never a
job's reason or note, which can carry a local path.

``ending`` and ``notification`` are pure, so the host suite checks the policy and
the text without Blender (``test_native_job_desktop.py``).
"""

from . import rtl_text
from .ui_text import iface_, join, n_

NOTIFY_AFTER_SECONDS = 60
PREFERENCES = ("job_keep_awake", "job_notifications")   # job_desktop_prefs.JobDesktopPreferences
AWAKE_REASON = n_("Angelo is running a background job")
ENDINGS = {"done": n_("{title} finished"), "failed": n_("{title} failed"),
           "time_budget": n_("{title} stopped at its time budget")}
TOOK = n_("Took {duration}")
# Endings worth announcing, as job_manager writes them (status, and the reason of a cancel). Everything else is
# left out: the person's own cancel (reason "user"), the agent's (it says so in the chat), a file load, a crash.
ANNOUNCED = {("done", None), ("failed", None), ("cancelled", "time_budget")}


def ending(record):
    """The ``ENDINGS`` key of a job's end when it is announced, else None (also for a job shorter than the bound)."""
    from .job_spec import PREVIEW
    status = record.get("status")
    key = (status, record.get("reason") if status == "cancelled" else None)
    if record.get("kind") == PREVIEW or key not in ANNOUNCED:
        return None
    started, ended = record.get("started"), record.get("ended")
    if started is None or ended is None or ended - started < NOTIFY_AFTER_SECONDS:
        return None
    return "time_budget" if status == "cancelled" else status


def announced(record):
    """Whether a job's end is worth a notification: long enough, and not ended by the person or the agent."""
    return ending(record) is not None


def holds_awake(jobs):
    """Whether any job other than a look is queued or running (by its status, never a stale pointer)."""
    from .job_spec import PREVIEW
    from .job_store import ACTIVE
    if jobs is None:
        return False
    records = [jobs.active()] + jobs.queued()
    return any(record is not None and record.get("kind") != PREVIEW and record.get("status") in ACTIVE
               for record in records)


def for_the_system(text):
    """Text another program shows (Notification Center, ``pmset``): in reading order.

    Angelo's Arabic catalogue is stored shaped, in visual order, for Blender's canvas; the system reorders and
    shapes right-to-left text itself, as a browser does (``team_pages``).
    """
    return rtl_text.to_logical(text) if rtl_text.is_visual(text) else text


def notification(record):
    """``(identifier, title, body)`` of a job's end, translated now (at display time).

    Only fixed, translated words, the duration and the workspace-relative output: never a job's reason or
    receipt note, which can carry Blender's or the system's own text (a failed publication names a local path).
    """
    from .job_chat import duration, title
    from .job_store import output_name
    key = ending(record) or record["status"]
    heading = iface_(ENDINGS.get(key, ENDINGS["failed"]), title=title(record))
    output = output_name(record) if record["status"] != "failed" and record["receipt"].get("files") else ""
    body = join([iface_(TOOK, duration=duration(record["ended"] - record["started"])), output])
    return "angelo.job." + record["id"], for_the_system(heading), for_the_system(body)


def _preference(name, default=True):
    from . import ui
    prefs = ui.preferences()
    return bool(getattr(prefs, name, default)) if prefs is not None else default


def _native():
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return None
    return _angelo_jobs if hasattr(_angelo_jobs, "desktop_notify") else None


def _automation_off():
    from .app_notices import automation_off
    return automation_off()


_awake = None
_asked = False     # The permission question was asked in this process.
_working = False   # A job held the computer awake at the last sync.


def keep_awake():
    global _awake
    if _awake is None:
        from .desktop_os import KeepAwake
        _awake = KeepAwake()
    return _awake


def sync(jobs):
    """Never raises: a desktop call must not stop the job poll (``job_ui``)."""
    try:
        _sync(jobs)
    except Exception as error:
        print("Angelo: keeping the computer awake failed:", type(error).__name__, error)


def ended(record):
    """Never raises: a desktop call must not stop a job's end (``job_ui._finished``)."""
    try:
        return _ended(record)
    except Exception as error:
        print("Angelo: the job notification failed:", type(error).__name__, error)
        return "failed"


def _sync(jobs):
    """Hold the computer awake while a job (not a look) is queued or running; release it otherwise.

    Called on every change of a job and every poll (``job_ui``), on the main thread.
    When a job starts holding it, the notification permission is read again (the
    person may have changed it in the system's settings), and the first time a
    person at the computer is asked once whether Angelo may post notifications.
    """
    global _asked, _working
    working = holds_awake(jobs)
    started, _working = working and not _working, working
    awake = keep_awake()
    if working and _preference("job_keep_awake"):
        awake.hold(for_the_system(iface_(AWAKE_REASON)))
    else:
        awake.release()
    if not started or not _preference("job_notifications") or not _desktop_allowed():
        return
    native = _native()
    if native is None:
        return
    if not _asked:
        _asked = True
        native.desktop_request_permission()
    native.desktop_permission()   # An answer arrives long before a job of a minute ends; ``desktop_notify`` reads it.


def _desktop_allowed():
    """Never in an automation run, unless the suite that proves the notification opts in."""
    from . import product_env
    return product_env.flag("ENABLE_JOB_NOTIFICATIONS") or not _automation_off()


def _ended(record):
    """A job ended: announce it when it is long and nobody chose to end it. Returns the word for what happened:
    ``posted``, or the attention answer (``bounced``, ``flashed``, ``foreground``), or why nothing was shown."""
    if not announced(record) or not _preference("job_notifications"):
        return "skipped"
    if not _desktop_allowed():
        return "automation"
    identifier, heading, body = notification(record)
    native = _native()
    status = native.desktop_notify(identifier, heading, body) if native is not None else "unsupported"
    if status == "posted":
        return status
    attention = native.desktop_attention() if native is not None else "unsupported"
    if attention != "unsupported":
        return attention
    from .desktop_os import flash_taskbar
    return flash_taskbar()


def shutdown():
    if _awake is not None:
        _awake.release()
