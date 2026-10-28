# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Importing Blender settings from the menus, and the first-launch card's import: operators, menus and notices.

Two entries in File > Defaults and in Preferences > Save & Load: **Import Blender Settings…** (enabled when stock
Blender's folder holds something to bring) and **Undo Settings Import** (shown while the last import's backup
belongs to this Angelo version). Each opens ``settings_import_card.ImportCard`` in the main window (the Preferences
window closes first: it has no 3D view to hold a card); only the card's Import / Put back button acts. The result
is a corner notice (``update_card.NoticeCard``): what came over, any add-on that did not load (one calm line;
Blender keeps it listed in Preferences > Add-ons) and, after a menu import, Undo import.

The setup card's "Use my Blender settings" ends in ``after_setup``: the same import and notice, then the welcome
(the notice waits for it: ``app_notices`` shows queued cards once the window has settled). The work itself is
``settings_import_apply``; while an agent turn runs, both entries wait (their poll says why).
"""

import traceback

import bpy
from bpy.types import Operator

from . import card_host, live_windows, settings_import as si, settings_import_apply as apply, settings_import_card as words
from .ui_text import iface_, n_, rpt_

BUSY = n_("Wait for the agent to finish, then try again")
NOTHING = n_("No Blender settings were found on this computer")
SHARED = n_("Angelo already uses the settings folder set for Blender")
last_outcome = None   # The latest import's or undo's outcome; window fixtures read it.


def _notice(outcome, offer_undo):
    from .update_card import NoticeCard
    icon, title, body, calm = words.notice_text(outcome)
    buttons = [("undo", iface_(words.UNDO_BUTTON), "ghost")] if offer_undo and apply.can_undo() else []
    return NoticeCard(icon, title, body, buttons, _on_notice, lifetime=30 if calm else 0)


def _on_notice(action, host):
    if action == "undo":
        host.close("undo")
        bpy.app.timers.register(_ask_undo, first_interval=0.05)


def _run_import(source, keymap_choices=None, offer_undo=True):
    """Import and queue the result notice; never raises (a failure is one notice and the log)."""
    global last_outcome
    try:
        outcome = apply.run(source, keymap_choices)
    except Exception as error:   # Startup goes on whatever an imported file does.
        traceback.print_exc()
        outcome = {"app": source.app, "version": source.version_label, "exception": str(error), "failed": [],
                   "errors": [], "reloaded_layout": False}
        try:
            bpy.ops.wm.save_userpref()   # The language and look chosen on the card are kept either way.
        except RuntimeError:
            traceback.print_exc()
    last_outcome = outcome
    card_host.request(_notice(outcome, offer_undo))
    return outcome


def after_setup(source, keymap_choices, welcome):
    """The setup card's "Use my Blender settings": import, then the welcome; the notice follows the welcome."""
    from . import intro
    if apply.agent_busy():   # The chat works beside the setup card: a turn started there finishes first.
        bpy.app.timers.register(lambda: after_setup(source, keymap_choices, welcome) and None, first_interval=1.0)
        return
    outcome = _run_import(source, keymap_choices, offer_undo=False)
    if welcome:
        if outcome.get("reloaded_layout"):
            intro.schedule_first_launch()   # Blender's import reloaded the start-up file: wait for it to settle.
        else:
            intro.open_after_setup(bpy.context.window)


def _show(card):
    """Show a card in the main window; a Preferences window (no 3D view to hold it) closes first."""
    for window in live_windows.windows(bpy.context.window_manager):
        if window.screen.is_temporary and any(a.type == 'PREFERENCES' for a in live_windows.areas(window)):
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()
    card_host.request(card)
    card_host.pump(lambda: True)


def _later(function):
    """Run ``function`` once the card's operator has ended (a card's ``closed`` runs inside it)."""
    bpy.app.timers.register(lambda: function() and None, first_interval=0.05)


def _show_queued():
    """Show the queued notice once Blender has reloaded its fonts for the theme put back (``Kept``): a card laid out
    while they are being reloaded measures no text."""
    from . import font_watch
    font_watch.check()
    card_host.pump(lambda: True)


FONTS_SETTLE = 0.6   # Seconds; longer than font_watch's own interval.


def _ask_import():
    source, _ = apply.find_source(include_angelo=False)
    if source is None or apply.agent_busy():
        return None

    def confirmed():
        if not apply.agent_busy():
            _run_import(source)
            bpy.app.timers.register(lambda: _show_queued() and None, first_interval=FONTS_SETTLE)

    _show(words.ImportCard("import", source.app, source.version_label, source.root,
                           lambda choice: choice == "confirm" and _later(confirmed)))
    return None


def _ask_undo():
    manifest = si.backup_matches(apply.backup_folder(), apply.destinations())
    if manifest is None or apply.agent_busy():
        return None

    def confirmed():
        global last_outcome
        from .update_card import NoticeCard
        if not apply.agent_busy():
            last_outcome = dict(apply.undo(), undo=True)
            card_host.request(NoticeCard("check", iface_(words.UNDONE_TITLE), "", lifetime=20))
            bpy.app.timers.register(lambda: _show_queued() and None, first_interval=FONTS_SETTLE)

    _show(words.ImportCard("undo", manifest.get("app", si.BLENDER), manifest.get("source_version", ""), None,
                           lambda choice: choice == "confirm" and _later(confirmed)))
    return None


class ANGELO_OT_import_blender_settings(Operator):
    bl_idname = "angelo.import_blender_settings"
    bl_label = "Import Blender Settings…"
    bl_description = ("Bring your preferences, shortcuts, add-ons and presets over from Blender. Angelo backs up "
                      "its own settings first, so the import can be undone")

    @classmethod
    def poll(cls, context):
        if apply.agent_busy():
            cls.poll_message_set(iface_(BUSY))
            return False
        source, reason = apply.find_source(include_angelo=False)
        if source is None:
            cls.poll_message_set(iface_(SHARED) if reason == si.SHARED else iface_(NOTHING))
        return source is not None

    def execute(self, context):
        bpy.app.timers.register(_ask_import, first_interval=0.01)   # After the menu (and Preferences) closes.
        return {'FINISHED'}


class ANGELO_OT_undo_settings_import(Operator):
    bl_idname = "angelo.undo_settings_import"
    bl_label = "Undo Settings Import"
    bl_description = "Put back the preferences and files Angelo had before your last settings import"

    @classmethod
    def poll(cls, context):
        if apply.agent_busy():
            cls.poll_message_set(iface_(BUSY))
            return False
        return apply.can_undo()

    def execute(self, context):
        if not apply.can_undo():
            self.report({'INFO'}, rpt_("There is no settings import to undo"))
            return {'CANCELLED'}
        bpy.app.timers.register(_ask_undo, first_interval=0.01)
        return {'FINISHED'}


def draw_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator_context = 'EXEC_DEFAULT'
    layout.operator(ANGELO_OT_import_blender_settings.bl_idname, icon='IMPORT')
    if apply.can_undo():
        layout.operator(ANGELO_OT_undo_settings_import.bl_idname, icon='LOOP_BACK')


MENUS = ("TOPBAR_MT_file_defaults", "USERPREF_MT_save_load")
CLASSES = (ANGELO_OT_import_blender_settings, ANGELO_OT_undo_settings_import)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for name in MENUS:
        getattr(bpy.types, name).append(draw_menu)


def unregister():
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.remove(draw_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
