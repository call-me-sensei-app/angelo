# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's controls for external AI apps and for Blender MCP (Preferences > AI Providers).

Every effect here runs only from a button whose label says what it does:
"Connect Claude Code" records the app (``external_clients``), adds Angelo to
that app's MCP settings (``external_setup``) and turns on access for apps on
this computer; "Disconnect" removes both and revokes the app's key. Writing another app's
settings can wait on that app's own command (Claude Code's), so it runs on a
worker thread and the card shows its result line.
"""

import sys
import threading

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import external_setup
from .external_apps import waiting
from .external_clients import APP_NAMES, AppRegistry, DEFAULT_CLIENT

OFFERED = ("claude_code", "codex")   # The apps Angelo sets up in 1.0.2 (external_mcp.rst).
from .ui_text import iface_, n_, rpt_

# app -> (ok, template, fields) of the last Connect/Disconnect, shown translated on its card until the next one.
results = {}
ADDED_SESSION = n_("Added Angelo to {app}. Start a new {app} session to use it.")
ADDED_RESTART = n_("Added Angelo to {app}. Restart {app} to load it.")
REMOVED = n_("Removed Angelo from {app}.")
NOT_CHANGED = n_("{app}'s settings were not changed ({reason}). Copy the setup and paste it there yourself.")
_busy = set()


def _state():
    from . import ui
    return ui.state_directory()


def _python():
    """The bundled interpreter that runs the launcher (Blender's ``sys.executable``)."""
    return sys.executable


def _redraw():
    try:
        from . import providers_page_host, preferences_ui
        providers_page_host._model_cache[:] = [0.0, None]
        preferences_ui.redraw()
    except Exception:  # noqa: BLE001 - a redraw is cosmetic.
        pass
    return None


def line(app):
    """The card's translated result line for ``app``, or ""."""
    if app not in results:
        return ""
    _ok, template, fields = results[app]
    return iface_(template, **fields)


def _in_background(app, work, done):
    """Run ``work()`` off the main thread; ``done`` (a template) or the error lands in ``results``; then redraw."""
    if app in _busy:
        return False
    _busy.add(app)

    def run():
        try:
            work()
            results[app] = (True, done, {"app": APP_NAMES[app]})
        except Exception as error:  # noqa: BLE001 - shown on the card, with the snippet to paste.
            results[app] = (False, NOT_CHANGED, {"app": APP_NAMES[app], "reason": str(error)[:200]})
        finally:
            _busy.discard(app)
            bpy.app.timers.register(_redraw, first_interval=0.0)
    threading.Thread(target=run, daemon=True, name="AngeloAppSetup").start()
    return True


class ANGELO_OT_external_app_connect(Operator):
    bl_idname = "angelo.external_app_connect"
    bl_label = "Connect App"
    bl_description = "Add Angelo to this app's MCP settings and let it reach Angelo on this computer"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import managed_policy, ui
        if self.app not in OFFERED:
            return {'CANCELLED'}
        if not managed_policy.current().allow_local_cli_access:
            self.report({'ERROR'}, rpt_("Turned off by your organization."))
            return {'CANCELLED'}
        if sys.platform == "win32":   # No named pipe yet (external_mcp.rst).
            self.report({'ERROR'}, rpt_("Coming to Windows in a later update."))
            return {'CANCELLED'}
        state = _state()
        name = AppRegistry(state).connect(self.app)
        prefs = ui.preferences()
        if prefs is not None and not prefs.allow_local_cli:
            prefs.allow_local_cli = True
            context.preferences.is_dirty = True
        python, app = _python(), self.app
        _in_background(app, lambda: external_setup.connect(app, python, state, name=name),
                       ADDED_SESSION if app in {"claude_code", "codex"} else ADDED_RESTART)
        _redraw()
        return {'FINISHED'}


class ANGELO_OT_external_app_disconnect(Operator):
    bl_idname = "angelo.external_app_disconnect"
    bl_label = "Disconnect App"
    bl_description = "Remove Angelo from this app's MCP settings and revoke its access"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.app not in OFFERED:
            return {'CANCELLED'}
        AppRegistry(_state()).disconnect(DEFAULT_CLIENT[self.app])   # Forgets its script files too.
        app = self.app
        _in_background(app, lambda: external_setup.disconnect(app), REMOVED)
        _redraw()
        return {'FINISHED'}


def _forget_script_files(client):
    from . import script_files
    try:
        return script_files.forget(app=client)
    except OSError as error:   # They still end for this session (script_files.forget).
        print("Angelo: the removed script file approvals could not be saved:", error)
        return None   # Not saved: another Angelo process (a headless session) still reads them (FR-L1).


class ANGELO_OT_external_app_forget_scripts(Operator):
    bl_idname = "angelo.external_app_forget_scripts"
    bl_label = "Forget Allowed Script Files"
    bl_description = "Ask again before this app runs any script file you allowed while it's unchanged"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.app not in OFFERED:
            return {'CANCELLED'}
        if _forget_script_files(DEFAULT_CLIENT[self.app]) is None:
            self.report({'ERROR'}, rpt_("Angelo could not save this change, so a session running without Angelo's "
                                        "window may still allow these script files. Try again."))
            return {'CANCELLED'}
        _redraw()
        return {'FINISHED'}


class ANGELO_OT_external_app_copy_setup(Operator):
    bl_idname = "angelo.external_app_copy_setup"
    bl_label = "Copy Setup"
    bl_description = "Copy this app's MCP setup to paste into its settings yourself"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.app not in OFFERED:
            return {'CANCELLED'}
        context.window_manager.clipboard = external_setup.snippet(self.app, _python(), _state())
        self.report({'INFO'}, rpt_("Copied. Paste it into the app's MCP settings."))
        return {'FINISHED'}


WAITING = n_("{app} is waiting for your approval")


_shown = {"key": (), "app": "", "session": ""}   # What the status bar shows; ``refresh`` keeps it current.


def refresh(ui):
    """Main thread, each tick (ui.pump): track the waiting apps and redraw the status bars when they change."""
    now = tuple(waiting(list(ui._controllers.values()), ui._sessions.active if ui._sessions else None))
    if now == _shown["key"]:
        return
    app, session = "", ""
    if now:
        label, session = now[0]
        record = AppRegistry(ui.state_directory()).load().get(label)
        app = APP_NAMES.get((record or {}).get("app"), label)
    _shown.update(key=now, app=app, session=session)
    from .team_account_ui import redraw_status_bars
    redraw_status_bars()


def draw_status(self, context):
    if not _shown["session"]:
        return
    row = self.layout.row(align=True)
    row.alert = True
    row.operator("angelo.external_app_open", text=iface_(WAITING, app=iface_(_shown["app"])), icon='ERROR',
                 translate=False).session_id = _shown["session"]


class ANGELO_OT_external_app_open(Operator):
    bl_idname = "angelo.external_app_open"
    bl_label = "Open App's Conversation"
    bl_description = "Open the conversation where this app is waiting for your approval"
    bl_options = {'INTERNAL'}
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import console, ui
        if not any(console.is_chat(area) for area in context.window.screen.areas):
            bpy.ops.angelo.open_chat()
        try:
            ui.show_session(self.session_id)
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def register_status():
    bpy.types.STATUSBAR_HT_header.append(draw_status)


def unregister_status():
    bpy.types.STATUSBAR_HT_header.remove(draw_status)


CLASSES = (ANGELO_OT_external_app_open, ANGELO_OT_external_app_connect, ANGELO_OT_external_app_disconnect,
           ANGELO_OT_external_app_copy_setup, ANGELO_OT_external_app_forget_scripts)
