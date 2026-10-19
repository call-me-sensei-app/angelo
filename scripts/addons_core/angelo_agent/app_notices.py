# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Update notices, crash reports and What's New: registration, readiness and the startup order.

Registered from the add-on's ``register()``. Everything here is quiet by design:

* Nothing runs in a background session, in an automation run (``ANGELO_DISABLE_INTRO=1``, which the native
  suite runner sets for every suite, or ``ANGELO_DISABLE_DEFAULT_CHAT=1``, which the live-evaluation harness
  sets) or before the person has seen the welcome screen; the notices wait for the window to settle
  (no layout change, intro or interaction in progress) like the welcome screen itself.
* Order on a normal launch: the crash dialog (a crash always comes first), then What's New after an update,
  then an update notice found by the network check. Only one card shows at a time (``card_host``).
* The network check (``update_ui``) starts 20 seconds after launch on its own thread; the crash scan and What's
  New read two small files.

Contract: doc/angelo/updates_and_reports.rst.
"""

import time

import bpy
from bpy.types import Operator

from . import (app_version, card_host, crash_ui, product_env, release_notes, update_card, update_ui, updates_prefs,
               whatsnew_card)
from .ui_text import rpt_

STARTUP_DELAY = 4.0
DELAY_VARIABLE = "NOTICES_DELAY"
_started = None
_scan_started = False
_scan_done = False
_whats_new_done = False
steps = []   # What the startup sequence did, in order; fixtures read it.


def automation_off():
    """True in an automation run: nothing here may show a card or touch the network.

    ``ANGELO_ENABLE_APP_NOTICES=1`` is for the one fixture that tests these cards (``test_blender_update_crash``).
    """
    if product_env.flag("ENABLE_APP_NOTICES"):
        return False
    return product_env.flag("DISABLE_INTRO") or product_env.flag("DISABLE_DEFAULT_CHAT")


def ready():
    """The window has settled: no layout change, welcome screen or interaction is in progress."""
    from . import director, input_policy, intro, ui
    windows = [w for w in bpy.context.window_manager.windows if w.screen and not w.screen.is_temporary]
    welcome_pending = not (intro.seen() or product_env.flag("DISABLE_INTRO"))   # First launch: the welcome comes first.
    if not windows or welcome_pending or intro.is_open() or intro._timer is not None:
        return False
    if director._timer is not None or ui._layout_timer is not None:
        return False
    # A card's own operator is not the person's interaction (a closed one ends at its next event).
    return not [name for name in input_policy.active_interactions(windows[0]) if name != "angelo.card_host"]


def open_whats_new(notes):
    if notes is not None:
        card_host.request(whatsnew_card.WhatsNewCard(notes, update_card.open_link, lambda: None))
        return True
    return False


def tick():
    global _scan_started, _scan_done, _whats_new_done
    if bpy.app.background or automation_off():
        return 30.0
    delay = float(product_env.get(DELAY_VARIABLE, default=STARTUP_DELAY))
    if time.monotonic() - _started < delay or not ready():
        return 1.0
    if not _scan_started:
        _scan_started = True
        crash_ui.start_scan()
        steps.append("scan")
        return 0.3
    if not _scan_done:
        if not crash_ui.poll():
            return 0.3
        _scan_done = True
        steps.append("scanned")
    if not _whats_new_done:   # Queued behind a crash dialog by priority, ahead of an update notice.
        _whats_new_done = True
        checker = update_ui.checker()
        notes, remember = release_notes.whats_new(checker.last_seen(), app_version.current())
        if remember != checker.last_seen():
            checker.mark_seen(remember)
        if open_whats_new(notes):
            steps.append("whats_new")
    card_host.pump(ready)
    return 0.5 if card_host.queued() else 1.0


class ANGELO_OT_whats_new(Operator):
    bl_idname = "angelo.whats_new"
    bl_label = "What's New"
    bl_description = "What changed in this version of Angelo"

    def execute(self, context):
        notes = release_notes.for_version(app_version.current())
        if notes is None:
            releases = release_notes.load()
            notes = releases[0] if releases else None
        if not open_whats_new(notes):
            self.report({'INFO'}, rpt_("There are no release notes to show"))
            return {'CANCELLED'}
        return {'FINISHED'}


def register():
    global _started, _scan_started, _scan_done, _whats_new_done
    _started, _scan_started, _scan_done, _whats_new_done = time.monotonic(), False, False, False
    steps.clear()
    bpy.utils.register_class(ANGELO_OT_whats_new)
    card_host.register()
    update_ui.register()
    crash_ui.register()
    updates_prefs.register()
    bpy.app.timers.register(tick, first_interval=2.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    updates_prefs.unregister()
    crash_ui.unregister()
    update_ui.unregister()
    card_host.unregister()
    bpy.utils.unregister_class(ANGELO_OT_whats_new)
