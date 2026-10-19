# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preferences > System > Updates and Reports: the person's own controls for the update check and crash reports.

Two settings live in the agent state folder (``updates.json``, ``crash-reports.json``), not in the saved
preferences: Updates > Check Automatically, and Crash Reports (ask me each time, always send, never send).
Drawing reads only the in-memory ``cache``, refreshed at registration, after a check, after a crash report
was handled and after a change; it never touches a file. A machine policy (``DisableUpdateCheck``, an
unusable ``UpdateFeedURL``, ``DisableCrashReporting``) locks its setting and says so. Laid out with Blender's
``CenterAlignMixIn``, like the System panels around it.
"""

import time

import bpy
from bl_ui.space_userpref import CenterAlignMixIn
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Panel

from . import app_version, crash_flow, crash_state, managed_policy, update_check, update_ready, update_state
from .ui_text import iface_, n_

CONSENT_ITEMS = (   # Blender translates enum items itself; n_ marks them because they are not inline in the call.
    (crash_state.ASK, n_("Ask Me Each Time"), n_("Show the crash report and ask before anything is sent"), 0),
    (crash_state.ALWAYS, n_("Always Send"),
     n_("Send the crash report after a crash without asking, and say that it was sent"), 1),
    (crash_state.NEVER, n_("Never Send"), n_("Never ask and never send a crash report"), 2),
)
_VALUES = {item[0]: item[3] for item in CONSENT_ITEMS}
_NAMES = {item[3]: item[0] for item in CONSENT_ITEMS}
cache = {"automatic": True, "consent": crash_state.ASK, "last_success": 0.0, "failures": 0, "has_report": False}

MANAGED = n_("Turned off by your organization.")
LAST_CHECK = n_("Last checked {when}.")
NEVER_CHECKED = n_("Not checked yet.")
FAILED_CHECK = n_("The last check could not reach the update server.")
CRASH_NOTE = n_("A crash report holds technical details only: never your scenes, prompts, chats, files or keys.")
AVAILABLE = n_("Angelo {version} is available.")
READY = n_("Angelo {version} is ready. Restart Angelo to install it.")
DOWNLOADING = n_("Downloading Angelo {version}: {percent}%")
MOMENTS_AGO, MINUTES_AGO = n_("just now"), n_("{count} min ago")
HOURS_AGO, DAYS_AGO = n_("{count} h ago"), n_("{count} d ago")


def refresh():
    """Reload the cache from the state files (main thread; never from a draw callback)."""
    from . import update_ui
    folder = update_ui.directory()
    updates, crash = update_state.load(folder), crash_state.load(folder)
    cache.update(automatic=updates.automatic, consent=crash.consent, last_success=updates.last_success,
                 failures=updates.failures, has_report=crash_flow.last_sent(folder) is not None)


def _get_automatic(self):
    return cache["automatic"]


def _set_automatic(self, value):
    from . import update_ui
    update_ui.checker().set_automatic(value)
    refresh()


def _get_consent(self):
    return _VALUES[cache["consent"]]


def _set_consent(self, value):
    from . import crash_ui
    crash_ui.set_consent(_NAMES.get(value, crash_state.ASK))
    refresh()


def ago(seconds):
    """A short relative time for the status line. Compact units, so no plural forms are needed."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return iface_(MOMENTS_AGO)
    if seconds < 3600:
        return iface_(MINUTES_AGO, count=seconds // 60)
    if seconds < 86400:
        return iface_(HOURS_AGO, count=seconds // 3600)
    return iface_(DAYS_AGO, count=seconds // 86400)


class ANGELO_PT_updates_and_reports(CenterAlignMixIn, Panel):
    """Laid out like Blender's own System panels (labels on the left, controls on the right)."""

    bl_label = "Updates and Reports"
    bl_space_type = 'PREFERENCES'
    bl_region_type = 'WINDOW'
    bl_context = "system"

    @staticmethod
    def _note(layout, text, icon='NONE'):
        """A line in the controls' column (the left column stays empty), as Blender's Network panel does."""
        row = layout.split(factor=0.4)
        row.label(text="")
        row.label(text=text, icon=icon, translate=False)

    @staticmethod
    def _offer_text(offer):
        version = app_version.display(offer.release.version)
        phase = update_ready.phase(offer)
        if phase == "ready":
            return iface_(READY, version=version)
        if phase == "downloading":
            done, total = update_ready.progress(offer)
            return iface_(DOWNLOADING, version=version, percent=int(100 * done / total) if total else 0)
        return iface_(AVAILABLE, version=version)

    def draw_centered(self, context, layout):
        from . import update_ui
        policy = managed_policy.current()
        updates_locked = bool(update_check.disabled_reason(policy, update_state.UpdateState(), True))
        wm = context.window_manager
        col = layout.column(heading="Updates")
        col.active = not updates_locked
        col.prop(wm, "angelo_update_automatic", text="Check Automatically")
        if updates_locked:
            self._note(layout, iface_(MANAGED), 'LOCKED')
        elif update_ui.offer is not None:
            self._note(layout, self._offer_text(update_ui.offer), 'IMPORT')
        elif cache["last_success"]:
            self._note(layout, iface_(LAST_CHECK, when=ago(time.time() - cache["last_success"])))
        else:
            self._note(layout, iface_(FAILED_CHECK if cache["failures"] else NEVER_CHECKED))
        row = layout.split(factor=0.4)
        row.label(text="")
        buttons = row.row()
        check = buttons.row()
        check.enabled = not updates_locked
        check.operator("angelo.check_for_updates", text="Check Now")
        if update_ui.offer is not None and update_ready.ready_for(update_ui.offer):
            buttons.operator("angelo.update_show", text="Restart to Update")
        buttons.operator("angelo.whats_new", text="What's New")
        layout.separator()
        col = layout.column()
        col.enabled = not policy.disable_crash_reporting
        col.prop(wm, "angelo_crash_consent", text="Crash Reports", expand=True)
        if policy.disable_crash_reporting:
            self._note(layout, iface_(MANAGED), 'LOCKED')
            return
        self._note(layout, iface_(CRASH_NOTE))
        row = layout.split(factor=0.4)
        row.label(text="")
        last = row.row()
        last.enabled = cache["has_report"]
        last.operator("angelo.crash_report_last", text="View Last Report Sent")


def register():
    bpy.types.WindowManager.angelo_update_automatic = BoolProperty(
        name="Check for updates automatically", get=_get_automatic, set=_set_automatic, options={'SKIP_SAVE'},
        description="Look for a newer Angelo about once a day and get it ready in the background; "
                    "it is installed only when you restart Angelo")
    bpy.types.WindowManager.angelo_crash_consent = EnumProperty(
        name="Crash Reports", items=CONSENT_ITEMS, get=_get_consent, set=_set_consent, options={'SKIP_SAVE'},
        description="What Angelo does with a crash report after it quits unexpectedly")
    bpy.utils.register_class(ANGELO_PT_updates_and_reports)
    refresh()


def unregister():
    bpy.utils.unregister_class(ANGELO_PT_updates_and_reports)
    del bpy.types.WindowManager.angelo_crash_consent
    del bpy.types.WindowManager.angelo_update_automatic
