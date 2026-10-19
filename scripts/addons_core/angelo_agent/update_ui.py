# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update check inside Blender: the worker, the schedule, the notice, the status-bar item and Help entries.

Logic lives in ``update_check`` (host-tested); this module is the Blender side. One daemon thread at a
time runs ``Checker.check``; its result is picked up by the persistent ``tick`` timer on the main thread,
which hands an update Angelo can install itself to ``update_ready`` (download and verification in the
background, then "Restart to Update"), shows the notice card (``update_card``) otherwise, keeps the
status-bar item, and starts the next check when it is due (at launch, then about daily). Drawing and menus
never read a file or touch the network: they read the module's last result.

Text is translated where it is shown (``ui_text``).
"""

import threading
import time

import bpy
from bpy.types import Operator

from . import app_version, card_host, product_env, update_card, update_check, update_ready, updates_prefs
from .ui_text import iface_, n_

STARTUP_DELAY = 20.0           # Seconds after launch before the first check (keeps startup free).
DELAY_VARIABLE = "UPDATE_CHECK_DELAY"
_busy = None                   # The running worker thread.
_inbox = []                    # [(manual, Result)] filled by the worker, drained by tick on the main thread.
_lock = threading.Lock()
_started = None                # monotonic time of register().
_launch_check_done = False
last = None                    # The latest update_check.Result (any kind); fixtures and menus read it.
offer = None                   # The current Offer the status bar shows (None: up to date, skipped or unknown).
last_manual_kind = ""

UP_TO_DATE_TITLE = n_("Angelo is up to date")
UP_TO_DATE_BODY = n_("You have Angelo {version}, the latest version.")
COULD_NOT_TITLE = n_("Could not check for updates")
OFFLINE_BODY = n_("Angelo could not reach the update server. Check your internet connection and try again.")
FAILED_BODY = n_("The update information could not be read or verified. Try again later.")
UNAVAILABLE_BODY = n_("Update checks are not available in this build of Angelo.")
DISABLED_TITLE = n_("Update checks are off")
DISABLED_BODY = n_("Your organization turned them off on this computer. Your IT team provides Angelo updates.")
OFFLINE_MODE_BODY = n_("Angelo was started in offline mode, so it does not check for updates.")
TRY_AGAIN, CLOSE = n_("Try again"), n_("Close", "Angelo")  # A dialog button, not a closed shape.
STATUS_ITEM = n_("Angelo {version} available")
STATUS_READY = n_("Restart to Update")


def directory():
    from .ui import state_directory
    return state_directory()


def explicit_offline():
    """Started with ``--offline-mode``. Blender's own "Allow Online Access" preference is not read: it is off by
    default and Angelo's own network features do not depend on it (updates_and_reports.rst)."""
    return bool(bpy.app.online_access_override and not bpy.app.online_access)


def checker():
    return update_check.Checker(directory(), explicit_offline=explicit_offline())


def policy_blocks():
    """The organization turned update checks off (``DisableUpdateCheck``, an unusable ``UpdateFeedURL``)."""
    instance = checker()
    return update_check.disabled_reason(instance.policy, instance.state(), True) == "policy"


def automatic_allowed():
    """Automatic checks (and so background downloads) are allowed: no policy against them, not in offline mode,
    and the person's Check Automatically is on. Reads the state file: call it off the draw path."""
    instance = checker()
    return update_check.disabled_reason(instance.policy, instance.state(), False, explicit_offline()) == ""


def enabled():
    """Whether this session runs the check at all: a window session that is not an automation run."""
    from .app_notices import automation_off
    return not bpy.app.background and not automation_off()


def start_check(manual=False):
    """Run a check on a worker thread. At most one runs at a time; returns False when one already does."""
    global _busy
    with _lock:
        if _busy is not None and _busy.is_alive():
            return False
        instance = checker()

        def work():
            try:
                result = instance.check(manual=manual)
            except Exception as error:  # A worker must never end silently or take Blender down with it.
                result = update_check.Result(update_check.FAILED, detail="exception:" + type(error).__name__)
            with _lock:
                _inbox.append((manual, result))

        _busy = threading.Thread(target=work, name="angelo-update-check", daemon=True)
        _busy.start()
    return True


def decide(choice, selected):
    """The notice's Later and Skip (and Download, which counts as Later)."""
    global offer
    instance = checker()
    if choice == "skip":
        instance.skip(selected)
        if not selected.critical:
            offer = None
    else:
        instance.snooze(selected)
    _redraw_status()


def show_notice(selected):
    card_host.request(update_card.UpdateCard(selected, app_version.current(), decide, live=update_ready))


def apply(result, manual):
    """Main thread: what the person sees for a finished check."""
    global last, offer, last_manual_kind
    last = result
    if result.kind == update_check.UPDATE:
        offer = None if result.skipped else result.offer
        automatic = update_ready.consider(result.offer, manual) if (manual or not result.skipped) else False
        # An update Angelo installs itself is announced when it is ready (update_ready); a manual check shows
        # its card at once, following the download.
        if manual or not (result.skipped or result.snoozed or automatic):
            show_notice(result.offer)
    elif result.kind == update_check.CURRENT:
        offer = None
    if manual:
        last_manual_kind = result.kind
        notice = _result_notice(result)
        if notice is not None:
            card_host.request(notice)
    updates_prefs.refresh()
    _redraw_status()


def _result_notice(result):
    kind, current = result.kind, app_version.display(app_version.current())
    if kind == update_check.UPDATE:
        return None  # The update card itself is the answer.
    if kind == update_check.CURRENT:
        return update_card.NoticeCard("check", iface_(UP_TO_DATE_TITLE), iface_(UP_TO_DATE_BODY, version=current),
                                      [("close", iface_(CLOSE), "primary")], lifetime=9)
    if kind == update_check.DISABLED:
        return update_card.NoticeCard("alert", iface_(DISABLED_TITLE), iface_(DISABLED_BODY),
                                      [("close", iface_(CLOSE), "primary")])
    if kind == update_check.UNAVAILABLE:
        return update_card.NoticeCard("alert", iface_(COULD_NOT_TITLE), iface_(UNAVAILABLE_BODY),
                                      [("close", iface_(CLOSE), "primary")])
    if result.detail == "offline_mode":
        return update_card.NoticeCard("alert", iface_(DISABLED_TITLE), iface_(OFFLINE_MODE_BODY),
                                      [("close", iface_(CLOSE), "primary")])
    body = OFFLINE_BODY if kind == update_check.OFFLINE else FAILED_BODY
    return update_card.NoticeCard("alert", iface_(COULD_NOT_TITLE), iface_(body),
                                  [("close", iface_(CLOSE), "secondary"), ("retry", iface_(TRY_AGAIN), "primary")],
                                  on_action=_retry)


def _retry(action, host):
    if action == "retry":
        start_check(manual=True)
        host.close("retry")


def redraw_status():
    _redraw_status()


def _redraw_status():
    """Redraw the status bar, the top bar and Preferences after the offer changed.

    The status bar and the top bar are global areas, absent from ``screen.areas``: only the message bus
    reaches them. The status bar listens to ``Window.view_layer`` (as ``team_account_ui`` uses), the top bar to
    its workspace's ``tools``.
    """
    bpy.msgbus.publish_rna(key=(bpy.types.Window, "view_layer"))
    for window in bpy.context.window_manager.windows:
        if window.workspace is not None:
            bpy.msgbus.publish_rna(key=window.workspace.path_resolve("tools", False))
        for area in window.screen.areas:
            if area.type == 'PREFERENCES':
                area.tag_redraw()


def tick():
    """Persistent main-thread timer: collects finished checks and starts the due one. Returns the next interval."""
    global _launch_check_done, offer
    if not enabled():
        return 30.0
    with _lock:
        finished, _inbox[:] = list(_inbox), []
    for manual, result in finished:
        apply(result, manual)
    if _busy is not None and _busy.is_alive():
        return 1.0
    delay = float(product_env.get(DELAY_VARIABLE, default=STARTUP_DELAY))
    if time.monotonic() - _started < delay:
        return 1.0
    instance = checker()
    if not instance.keys:
        return 3600.0   # No trusted signing key in this build: no check can run, so read nothing every minute.
    state = instance.state()
    if not _launch_check_done:
        _launch_check_done = True
        if offer is None and last is None and not instance.policy.disable_update_check:
            pending = instance.pending()   # The stored offer, so the status bar is right at once while offline.
            if pending.kind == update_check.UPDATE and not pending.skipped:
                offer = pending.offer
                # A download left by the last session resumes, a ready one is offered: only where automatic
                # checks are allowed (consider checks), never in offline mode or against a policy.
                update_ready.consider(offer)
                _redraw_status()
        if update_check.disabled_reason(instance.policy, state, False, explicit_offline()) == "":
            if update_check.due(state, time.time(), launch=True):
                start_check()
        return 5.0
    if update_check.disabled_reason(instance.policy, state, False, explicit_offline()) == "":
        if update_check.due(state, time.time()):
            start_check()
    return 60.0


class ANGELO_OT_check_for_updates(Operator):
    bl_idname = "angelo.check_for_updates"
    bl_label = "Check for Updates…"
    bl_description = "Look for a newer version of Angelo, and get it ready to install"

    def execute(self, context):
        start_check(manual=True)
        return {'FINISHED'}


class ANGELO_OT_update_show(Operator):
    bl_idname = "angelo.update_show"
    bl_label = "Show Update"
    bl_description = "Show the available update, with its release notes, or restart to install it"

    def execute(self, context):
        if offer is None:
            start_check(manual=True)
        else:
            show_notice(offer)
        return {'FINISHED'}


def draw_status(self, context):
    if offer is None:
        return
    row = self.layout.row(align=True)
    row.separator(factor=-0.5)
    row.label(text="|", translate=False)  # A separator, as the version's own (uiTemplateStatusInfo).
    row.separator(factor=-0.5)
    row.emboss = 'NONE'
    row.operator("angelo.update_show", text=_status_text(), icon=_status_icon(), translate=False)


def _status_text():
    if update_ready.ready_for(offer):
        return iface_(STATUS_READY)
    return iface_(STATUS_ITEM, version=app_version.display(offer.release.version))


def _status_icon():
    return 'FILE_REFRESH' if update_ready.ready_for(offer) else 'IMPORT'


def draw_help(self, context):
    """Help > Check for Updates… and What's New (Manual mode), and the same in Director's mode menu,
    because Director hides the Help menu (``director.ANGELO_MT_workspace_mode``)."""
    self.layout.separator()
    self.layout.operator("angelo.check_for_updates", icon='FILE_REFRESH')
    self.layout.operator("angelo.whats_new", icon='SEQ_PREVIEW')


def draw_director_bar(self, context):
    """Director hides the status bar, so a pending update sits beside the mode's title in the top bar."""
    from .director_layout import is_director
    if offer is None or context.region.alignment == 'RIGHT' or not is_director(context.window):
        return
    row = self.layout.row(align=True)
    row.separator()
    row.emboss = 'NONE'
    row.operator("angelo.update_show", text=_status_text(), icon=_status_icon(), translate=False)


def _ready_tick():
    """``update_ready``'s pace: every second while an update is being prepared, shown or ready, else every 5."""
    if not enabled():
        return 30.0
    update_ready.tick()
    busy = update_ready._job is not None or update_ready._ready or card_host.is_active("update")
    return 1.0 if busy else 5.0


CLASSES = (ANGELO_OT_check_for_updates, ANGELO_OT_update_show)


def register():
    global _started, _launch_check_done, offer, last
    _started, _launch_check_done, offer, last = time.monotonic(), False, None, None
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.STATUSBAR_HT_header.append(draw_status)
    bpy.types.TOPBAR_MT_help.append(draw_help)
    bpy.types.ANGELO_MT_workspace_mode.append(draw_help)   # Registered by director.py (ui.register runs first).
    bpy.types.TOPBAR_HT_upper_bar.append(draw_director_bar)
    update_ready.register()
    bpy.app.timers.register(tick, first_interval=2.0, persistent=True)
    bpy.app.timers.register(_ready_tick, first_interval=3.0, persistent=True)


def unregister():
    for timer in (tick, _ready_tick):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    update_ready.unregister()
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_director_bar)
    bpy.types.ANGELO_MT_workspace_mode.remove(draw_help)
    bpy.types.TOPBAR_MT_help.remove(draw_help)
    bpy.types.STATUSBAR_HT_header.remove(draw_status)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
