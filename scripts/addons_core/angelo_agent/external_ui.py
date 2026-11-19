# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's controls for external AI apps (Preferences > AI Providers).

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

from . import grant_guard  # noqa: E402  (only the person grants: grant_guard.grant)
from . import external_setup, live_windows
from .external_agent import waiting
from .external_clients import APP_NAMES, AppRegistry, DEFAULT_CLIENT

OFFERED = ("claude_code", "codex")   # The apps Angelo sets up in 1.0.2 (external_mcp.rst).
COPYABLE = ("claude_code", "codex", "claude_desktop", "cursor", "vscode", "other")   # Copy setup, Disconnect.
from .ui_text import iface_, n_, rpt_

# app -> (ok, template, fields) of the last Connect/Disconnect, shown translated on its card until the next one.
results = {}
ADDED_SESSION = n_("Added Angelo to {app}. Start a new {app} session to use it.")
ADDED_RESTART = n_("Added Angelo to {app}. Restart {app} to load it.")
# Connect replaced an entry that started another Angelo build's or copy's server (external_setup.elsewhere).
UPDATED_SESSION = n_("Updated Angelo in {app}: it started the MCP server in {previous}. Start a new {app} session "
                     "to use it.")
UPDATED_RESTART = n_("Updated Angelo in {app}: it started the MCP server in {previous}. Restart {app} to load it.")
REMOVED = n_("Removed Angelo from {app}.")
DISCONNECTED_ONLY = n_("Disconnected in Angelo. Remove Angelo from {app}'s MCP settings yourself if it's there.")
NOT_SAVED = n_("Angelo could not save this connection. Check that Angelo's folder can be written, then try again.")
WINDOWS_LATER = n_("Coming to Windows in a later update.")
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


def line(key):
    """The card's translated result line for ``key`` (an app, or a generic MCP app's client), or ""; the app's name
    shown as the card shows it (``MCP app`` translated, product names as written: break-it review N3)."""
    if key not in results:
        return ""
    _ok, template, fields = results[key]
    return iface_(template, **dict(fields, app=iface_(fields["app"])))


def connect_done(app):
    """Connect's result line: it added Angelo, or replaced an entry that started another Angelo's server."""
    session = app in {"claude_code", "codex"}

    def done(previous):
        if previous:
            return (UPDATED_SESSION if session else UPDATED_RESTART), {"previous": previous}
        return ADDED_SESSION if session else ADDED_RESTART
    return done


def _in_background(app, work, done, key=None):
    """Run ``work()`` off the main thread; ``done`` (a template, or a function of what ``work`` returned that gives
    one) or the error lands in ``results[key or app]``; then redraw."""
    key = key or app
    if key in _busy:
        return False
    _busy.add(key)

    def run():
        try:
            result = work()
            template, fields = done(result) if callable(done) else done, {}
            if isinstance(template, tuple):   # (template, its other fields)
                template, fields = template
            results[key] = (True, template, dict(fields, app=APP_NAMES[app]))
        except Exception as error:  # noqa: BLE001 - shown on the card, with the snippet to paste.
            results[key] = (False, NOT_CHANGED, {"app": APP_NAMES[app], "reason": str(error)[:200]})
        finally:
            _busy.discard(key)
            bpy.app.timers.register(_redraw, first_interval=0.0)
    try:
        threading.Thread(target=run, daemon=True, name="AngeloAppSetup").start()
    except RuntimeError as error:   # No thread to set it up on: not busy, and the card says why (``thread_start``).
        _busy.discard(key)
        results[key] = (False, NOT_CHANGED, {"app": APP_NAMES[app], "reason": str(error)[:200]})
    return True


def _refused(operator):
    """Why access for external apps can't be granted here, reported; or False (Connect and Copy setup alike)."""
    from . import managed_policy
    if not managed_policy.current().allow_local_cli_access:
        operator.report({'ERROR'}, rpt_("Turned off by your organization."))
        return True
    if sys.platform == "win32":   # No named pipe yet (external_mcp.rst): nothing registered, access stays off.
        operator.report({'ERROR'}, rpt_(WINDOWS_LATER))
        return True
    return False


def _access_on(context):
    """As Connect and Copy setup do: apps on this computer may reach Angelo (inside the person's guarded call)."""
    from . import ui
    prefs = ui.preferences()
    if prefs is not None and not prefs.allow_local_cli:
        prefs.allow_local_cli = True
        context.preferences.is_dirty = True


class ANGELO_OT_external_app_connect(Operator):
    bl_idname = "angelo.external_app_connect"
    bl_label = "Connect App"
    bl_description = "Add Angelo to this app's MCP settings and let it reach Angelo on this computer"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.app not in OFFERED or _refused(self):
            return {'CANCELLED'}
        state = _state()
        registry = AppRegistry(state)
        known = DEFAULT_CLIENT[self.app] in registry.load()
        try:
            name = registry.connect(self.app)
        except OSError:
            if not known:   # Never a key without its record (break-it review N6).
                try:
                    registry.disconnect(DEFAULT_CLIENT[self.app])
                except OSError:
                    pass
            self.report({'ERROR'}, rpt_(NOT_SAVED))
            return {'CANCELLED'}
        _access_on(context)
        python, app = _python(), self.app

        def work():
            previous = external_setup.elsewhere(app, python, state, name)   # Read before the entry is replaced.
            external_setup.connect(app, python, state, name=name)
            return previous
        _in_background(app, work, connect_done(app))
        _redraw()
        return {'FINISHED'}


class ANGELO_OT_external_app_disconnect(Operator):
    bl_idname = "angelo.external_app_disconnect"
    bl_label = "Disconnect App"
    bl_description = "Remove Angelo from this app's MCP settings and revoke its access"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})
    client: StringProperty(options={'SKIP_SAVE'})   # A generic MCP app's own client (external_copy).

    def execute(self, context):
        if self.app not in COPYABLE:
            return {'CANCELLED'}
        registry = AppRegistry(_state())
        name = _client(self.app, self.client)
        registry.disconnect(name)
        app = self.app
        # Says what happened: the app's settings changed, or only Angelo's side did (break-it review N2).
        _in_background(app, lambda: external_setup.disconnect(app), lambda removed: REMOVED if removed
                       else DISCONNECTED_ONLY, key=name if app == "other" else app)
        _redraw()
        return {'FINISHED'}


def _client(app, client):
    """The connection Disconnect acts on: a generic MCP app's own client, or the app's (never a new one)."""
    from .external_copy import GENERIC
    if app == "other":
        numbered = client.startswith(GENERIC + "-") and client[len(GENERIC) + 1:].isdigit()
        return client if numbered else GENERIC   # Only a client name Copy setup makes: never a path.
    return DEFAULT_CLIENT[app]


class ANGELO_OT_external_app_copy_setup(Operator):
    bl_idname = "angelo.external_app_copy_setup"
    bl_label = "Copy Setup"
    bl_description = "Connect this app in Angelo and copy its MCP setup to paste into its settings yourself"
    bl_options = {'INTERNAL'}
    app: StringProperty(options={'SKIP_SAVE'})
    client: StringProperty(options={'SKIP_SAVE'})   # Copy again for this generic MCP app; empty: a new one.

    def execute(self, context):
        """Connects the app in Angelo when it isn't yet (the person's click, as Connect), then copies its setup: the
        snippet never holds a key (external_copy)."""
        from . import external_copy
        if self.app not in COPYABLE or _refused(self):
            return {'CANCELLED'}
        try:
            _client, snippet = external_copy.copy_setup(_state(), self.app, _python(), self.client)
        except grant_guard.Refused:   # Agent code reached the click path (never the person's own click).
            self.report({'ERROR'}, rpt_(grant_guard.SCRIPT_RUNS))
            return {'CANCELLED'}
        except PermissionError:
            self.report({'ERROR'}, rpt_("Turned off by your organization."))
            return {'CANCELLED'}
        except OSError:
            self.report({'ERROR'}, rpt_(NOT_SAVED))
            return {'CANCELLED'}
        _access_on(context)
        context.window_manager.clipboard = snippet
        self.report({'INFO'}, rpt_("Copied. Paste it into the app's MCP settings."))
        _redraw()
        return {'FINISHED'}


WAITING = n_("{app} is waiting for your approval")


_shown = {"key": (), "app": "", "session": "", "local": ()}   # What the status bar shows; ``refresh`` keeps it current.


def refresh(ui):
    """Main thread, each tick (ui.pump): track the waiting apps and redraw the status bars when they change."""
    from . import approval_wait, chat_toggle, launcher_waiting
    controllers = tuple(ui._controllers.values())
    active = ui._sessions.active if ui._sessions else None
    visible = chat_toggle.chat_shown()
    now = tuple(waiting(controllers, active, chat_shown=visible))
    unseen = approval_wait.unseen(controllers, active, visible)
    local = next((c for c in unseen if not getattr(c, "external_client", "")), None)
    local = (local.state["id"], approval_wait.awaiting(local).id, launcher_waiting.label(local, active)) if local else ()
    if now == _shown["key"] and local == _shown["local"]:
        return
    app, session = "", ""
    if now:
        label, session = now[0]
        record = AppRegistry(ui.state_directory()).load().get(label)
        app = APP_NAMES.get((record or {}).get("app"), label)
    _shown.update(key=now, app=app, session=session, local=local)
    from .team_account_ui import redraw_status_bars
    redraw_status_bars()


def draw_status(self, context):
    if _shown["session"]:
        row = self.layout.row(align=True)
        row.alert = True
        row.operator("angelo.external_app_open", text=iface_(WAITING, app=iface_(_shown["app"])), icon='ERROR',
                     translate=False).session_id = _shown["session"]
    if _shown["local"]:
        row = self.layout.row(align=True)
        row.alert = True
        row.operator("angelo.waiting_chat_open", text=_shown["local"][2], icon='ERROR',
                     translate=False).session_id = _shown["local"][0]


class ANGELO_OT_external_app_open(Operator):
    bl_idname = "angelo.external_app_open"
    bl_label = "Open App's Conversation"
    bl_description = "Open the conversation where this app is waiting for your approval"
    bl_options = {'INTERNAL'}
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import console, ui
        if not any(console.is_chat(area) for area in live_windows.areas(context.window)):
            bpy.ops.angelo.open_chat()
        try:
            ui.show_session(self.session_id)
            from . import approval_wait
            controller = ui._controllers.get(self.session_id)
            if controller is not None:
                approval_wait.request_reveal(self.session_id, approval_wait.awaiting(controller), shield=True, controller=controller)
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_waiting_chat_open(Operator):
    bl_idname = "angelo.waiting_chat_open"
    bl_label = "Show the Angelo chat"
    bl_description = "Show the Angelo chat"
    bl_options = {'INTERNAL'}
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        return ANGELO_OT_external_app_open.execute(self, context)


def register_status():
    bpy.types.STATUSBAR_HT_header.append(draw_status)


def unregister_status():
    bpy.types.STATUSBAR_HT_header.remove(draw_status)


CLASSES = (ANGELO_OT_waiting_chat_open, ANGELO_OT_external_app_open, ANGELO_OT_external_app_connect, ANGELO_OT_external_app_disconnect,
           ANGELO_OT_external_app_copy_setup)


grant_guard.guard(ANGELO_OT_external_app_connect, ANGELO_OT_external_app_copy_setup)
