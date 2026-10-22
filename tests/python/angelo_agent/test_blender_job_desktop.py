# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A real background job keeps the computer awake while it runs, and its end reaches the desktop calls.

A Cycles animation job through the real executor (stand-in conversation): while
it runs the system power assertion is held (on macOS read back from ``pmset -g
assertions`` with this process's ID and Angelo's reason), and once no job is left
it is released. With the preference off, a second job holds nothing. The end of a
long job reaches the native notification call, which answers with one of its
documented words (a signed application posts; a development build reports the
notification unavailable and asks for attention instead), and never raises. An
automation run never shows the system's permission question, posts or asks for
attention (``app_notices.automation_off``); this suite then opts in
(``ANGELO_ENABLE_JOB_NOTIFICATIONS=1`` in its own process) to prove the
notification, which posts when the system allows it and otherwise reports the
attention fallback's own answer. On Windows the calls stay off until verified there
(``desktop_os.WINDOWS_VERIFIED``); ``ANGELO_WINDOWS_JOB_DESKTOP=1`` from the caller
turns them on for the verification run.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_job_desktop``; check ``job-desktop.json`` for ``success`` and
``phase == "complete"``.
"""

import os
import subprocess
import sys
from pathlib import Path

import addon_utils
import bpy

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_jobs_fixtures as fx  # noqa: E402
from production_jobs_harness import Harness, ended  # noqa: E402
from angelo_agent import app_notices, desktop_os, job_desktop  # noqa: E402
from angelo_agent.ui_text import iface_  # noqa: E402

harness = Harness("ANGELO_JOB_DESKTOP", "job-desktop.json", "/tmp/angelo-job-desktop-qa", timeout=420)
ctx = harness.ctx
PERMISSIONS = {"authorized", "denied", "not_determined", "unknown", "unavailable", "unsupported"}
# What reached the person: the notification, or the attention fallback's own answer (a window in front gets none).
SHOWN = {"darwin": {"posted", "bounced", "foreground"}, "win32": {"flashed", "foreground", "unsupported"}}


def assertion_listed():
    """macOS: this process holds Angelo's assertion (``pmset`` lists it by pid and name); elsewhere the hold itself."""
    if sys.platform != "darwin":
        return job_desktop.keep_awake().held
    listing = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=30).stdout
    reason = iface_(job_desktop.AWAKE_REASON)
    return any("pid %d(" % os.getpid() in line and "PreventUserIdleSystemSleep" in line and reason in line
               for line in listing.splitlines())


def prefs():
    return ui.preferences()


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        check("automation_run", app_notices.automation_off())
        check("both_preferences_default_on", prefs().job_keep_awake and prefs().job_notifications)
        check("nothing_held_before_a_job", not job_desktop.keep_awake().held and not assertion_listed())
        ctx["a"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "renders/awake", "frame_start": 1,
                                         "frame_end": 40})
        harness.go("a")
    elif phase == "a":
        if ctx["a"]["status"] == "running" and "held_while_running" not in harness.state["checks"]:
            if sys.platform == "win32" and not desktop_os.windows_enabled():   # Off until verified on Windows.
                check("held_while_running", job_desktop.keep_awake().status == "unsupported")
            else:
                check("held_while_running", assertion_listed())
            harness.state["keep_awake_status"] = job_desktop.keep_awake().status
        if not ended(ctx["a"]):
            return 0.1
        return 0.5 if harness.jobs().has_work() else _after_first(harness)
    elif phase == "b" and ended(ctx["b"]):
        check("nothing_held_with_the_preference_off", "b_held" in ctx and not ctx["b_held"])
        prefs().job_keep_awake = True
        harness.finish()
        return None
    elif phase == "b" and ctx["b"]["status"] == "running":
        ctx["b_held"] = ctx.get("b_held", False) or assertion_listed()
    return 0.1


def _after_first(harness):
    check = harness.check
    check("first_job_done", ctx["a"]["status"] == "done")
    check("released_when_no_job_is_left", not job_desktop.keep_awake().held and not assertion_listed())
    check("an_automation_run_is_never_asked", not job_desktop._asked)
    check("an_automation_run_posts_nothing", job_desktop.ended(dict(ctx["a"], started=ctx["a"]["ended"] - 120.0))
          == "automation")
    os.environ["ANGELO_ENABLE_JOB_NOTIFICATIONS"] = "1"   # This suite proves the notification itself.
    # The end of a long job: the same finished record, as if it had run two minutes.
    record = dict(ctx["a"], started=ctx["a"]["ended"] - 120.0)
    status = job_desktop.ended(record)
    from _bpy import _angelo_jobs
    permission = _angelo_jobs.desktop_permission()
    harness.state["desktop"] = {"ended": status, "permission": permission,
                                "notification": list(job_desktop.notification(record))}
    check("permission_is_a_documented_word", permission in PERMISSIONS)
    check("a_long_jobs_end_reaches_the_person", status in SHOWN.get(sys.platform, {"unsupported"}))
    # Allowed to post means posted: a broken notification center never passes as an attention fallback.
    check("posted_when_allowed", permission != "authorized" or status == "posted")
    check("the_notification_names_no_local_path", str(harness.out) not in " ".join(job_desktop.notification(record)))
    check("a_short_jobs_end_is_not_announced", job_desktop.ended(ctx["a"]) == "skipped")
    prefs().job_keep_awake = False
    ctx["b"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "renders/asleep", "frame_start": 1,
                                     "frame_end": 40})
    harness.go("b")
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
