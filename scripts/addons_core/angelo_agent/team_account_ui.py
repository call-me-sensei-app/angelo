# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread glue for the Angelo team account ("Angelo team" in AI Providers).

``ui`` (the add-on's UI module) stays the owner of connections, in-memory keys,
catalogs and controllers. This module owns the one ``team_account.TeamAccount``
and its virtual team connections (``team_connections``). It mirrors the current
grant key into ``ui._memory_keys`` for each usable team coding connection, so
the existing chat, catalog and collaborator paths pass it to the runtime
exactly as they pass an API key; a renewed key reaches a chat at its next
request (``Controller.select`` restarts the warm runtime and resumes its native
context). Signing out removes the key, stops idle runtimes bound to team
connections and keeps their native contexts for a later sign-in by the same
person in the same organization.

The managed policy (``managed_policy``) decides what is shown: a policy-managed
or team context asks the person to contact their administrator when they have
no seat or no providers, and personal connections disappear when the policy
allows only team connections. All text is translated where it is drawn
(``display_text.shown``). No network or key-store I/O runs here; draw code only
reads state.
"""

import os
import sys
import threading

import bpy
from bpy.types import Operator

from . import (managed_policy, preferences_ui, team_connections, team_endpoints, team_lock, team_org_policy, team_pages,
               team_token_store, team_usage_buffer, team_usage_reports)
from .display_text import Text, shown
from .generative import bind_team_generation
from .key_store import store_text
from .provider_registry import PROVIDER_NAMES, is_coding
from .saved_keys import profile_id
from .team_account import TeamAccount
from .team_view import OFFLINE, RESTORING, SIGNED_IN, SIGNING_IN
from .ui_text import iface_, n_
from . import team_chat, text_layout
from .team_chat import (CHAT_SIGNING_IN, note_window_event, team_only_options, welcome_action,  # noqa: F401 (re-exported)
                        welcome_row)

_account = None
_reason = ""        # Why there is no account in this session (an invalid origin or managed value).
_records = {}       # Current team connections by id.
_applied = None     # (view version, grant key) last applied to ui.
_keyed = set()      # Team connection ids whose grant key is in ui._memory_keys.
_org_policy = None  # team_org_policy.OrganizationPolicy of this account.
_chats = None       # Chat sessions last seen: a chat opened re-checks the team's providers (team_chat).

UNAVAILABLE = n_("Team sign-in is unavailable in this session.")
MANAGED = n_("Some settings are managed by your organization.")
MANAGED_PROBLEM = n_("Your organization's Angelo policy has unusable values ({keys}); they were applied in the "
                     "safest way. Contact your admin.")
WAITING = n_("Finish signing in in your browser ({console}). Waiting up to 10 minutes.")
SIGNED_IN_AS = n_("{org} · {email}")
SEAT = n_("Seat: yes. Team connections use your team's provider keys through Angelo.")
NOT_OFFERED = n_("Not offered by your team: {providers}.")
INVITE = n_("Sign in with your Angelo organization account to use your team's provider keys. "
            "Personal connections keep working with or without a team.")
INVITE_REQUIRED = n_("Sign in with your Angelo organization account to use AI features.")
OFFLINE_RETRY = n_("Retrying every minute. Personal connections keep working.")
STORED = n_("Your sign-in is kept in {store}.")
STORED_UNREADABLE = n_("Your sign-in is saved in {store}, but this build can't read it; you'll sign in again after "
                       "restarting.")
STORED_SESSION = n_("Your sign-in lasts until Angelo quits ({reason}).")
NOT_SAVED = n_("it couldn't be saved in {store}")
ENTRY = n_("{provider} · Angelo team · {status}")


def start(ui, *, background, environ=None, platform=None, backend=None, grant_backend=None, access_backend=None):
    """Create the account and resume a stored sign-in (off the main thread). True when enabled."""
    global _account, _reason
    close()
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    try:
        console = team_endpoints.console_origin(environ)
        team_endpoints.gateway_origin(environ)
    except ValueError as error:
        _reason = error.args[0] if error.args else str(error)
        return False
    storage_reason = ""
    if backend is None:
        backend, storage_reason = team_token_store.open_backend(environ, platform, background)
        if backend is not None and grant_backend is None:
            grant_backend, _ = team_token_store.open_backend(environ, platform, background, team_token_store.GRANT_SERVICE,
                                                             team_token_store.GRANT_COMMENT)
        if backend is not None and access_backend is None:
            access_backend, _ = team_token_store.open_backend(environ, platform, background,
                                                              team_token_store.ACCESS_SERVICE,
                                                              team_token_store.ACCESS_COMMENT)
    directory = ui.connection_directory()
    profile = profile_id(directory)
    vault = team_token_store.TokenVault(backend, console.url, profile) if backend is not None else None
    grant_vault = (team_token_store.TokenVault(grant_backend, console.url, profile,
                                               label="Angelo team grant key")  # i18n: label of the OS item
                   if grant_backend is not None else None)
    access_vault = (team_token_store.TokenVault(access_backend, console.url, profile,
                                                label="Angelo team access")  # i18n: label of the OS item
                    if access_backend is not None and vault is not None else None)
    _account = TeamAccount(console.url, vault=vault, grant_vault=grant_vault, access_vault=access_vault,
                           storage_reason=storage_reason, process_lock=team_lock.factory(directory),
                           usage_buffer=team_usage_buffer.UsageBuffer(directory))
    team_usage_reports.attach(_account)  # Chats' finished team tasks are counted for this account.
    global _org_policy
    _org_policy = team_org_policy.OrganizationPolicy(_account, directory)
    managed_policy.set_organization_source(_org_policy.current)
    if not background:
        _account.restore()
    return True


def close():
    global _account, _reason, _applied, _chats
    team_usage_reports.attach(None)
    if _account is not None:
        _account.close()
    managed_policy.set_organization_source(None)
    _account, _reason, _applied, _chats = None, "", None, None
    _records.clear()
    _keyed.clear()


def account():
    return _account


def records():
    return dict(_records)


def find(identity):
    return _records.get(identity)


def signed_in():
    return _account is not None and _account.view().signed_in


def signing_in():
    """Whether the sign-in callback listener may be open (fixtures assert no listener otherwise)."""
    return _account is not None and _account.view().status == SIGNING_IN


def _has_key():
    return bool(_account is not None and _account.grant_key())


def reason(record):
    if _account is None:
        return _reason or Text(UNAVAILABLE)
    return team_connections.unavailable_reason(_account.view(), record, _has_key())


def ready(record):
    return not reason(record)


def status(record):
    if _account is None:
        return reason(record)
    return team_connections.status(_account.view(), record, _has_key())


def health_results():
    """Media Studio health of usable team generation connections, by id."""
    if _account is None:
        return {}
    view = _account.view()
    results = {identity: team_connections.health(view, record) for identity, record in _records.items()}
    return {identity: result for identity, result in results.items() if result is not None}


def binding(record):
    """A team generation binding; ``ValueError`` with the reason when it can't be used."""
    problem = reason(record)
    if problem:
        raise ValueError(problem)
    try:
        gateway = team_endpoints.gateway_origin()
    except ValueError as error:
        raise ValueError(*error.args) from None
    view = _account.view()
    scope = "|".join((_account.console_url, view.org_id, view.user_id))
    return bind_team_generation(record, team_connections.health(view, record), gateway.url, _account, scope)


def pump(ui):
    """Apply account changes on the main thread and queue due background work. True when anything changed.

    Isolated: an unexpected console value is reported and retried, never stopping the rest of the UI pump.
    """
    try:
        return _pump(ui)
    except Exception as error:  # noqa: BLE001
        from .pump_guard import report
        report("team account", error)  # i18n: a log label (pump_guard prints it)
        return False


def _pump(ui):
    global _records, _applied, _chats
    if _account is None:
        return False
    controllers = list(ui._controllers.values())
    chats = tuple(sorted(ui._controllers))
    if chats != _chats:  # A chat opened (or closed): re-check the team and start any new chat on it.
        _chats = chats
        team_chat.recheck()
        team_chat.bind_team_chats(ui)
    key = _account.grant_key()
    for controller in controllers:
        # A team chat failed on sign-in with the current key (the gateway refused it: revoked by the
        # console's 8-key cap, expired, or the seat was removed). Issue another; the next request uses it.
        if (key and controller.connection_error and not controller.busy and controller.api_key == key
                and controller.state.get("connection_id") in _keyed):
            _account.refused_grant_key(key)
    _account.tick(idle=not any(c.busy for c in controllers))
    view, key = _account.view(), _account.grant_key()
    if (view.version, key) == _applied:
        return False
    _applied = (view.version, key)
    if _org_policy is not None:
        _org_policy.remember(view)
    previous, _records = _records, team_connections.records(view, _account.console_url)
    for identity in previous.keys() - _records.keys():
        ui.catalogs().invalidate(identity)
        _withdraw_key(ui, identity)
    for identity, record in _records.items():
        if is_coding(record) and key and ready(record):
            ui._memory_keys[identity] = key
            _keyed.add(identity)
        elif identity in _keyed:
            _withdraw_key(ui, identity)
    team_chat.bind_team_chats(ui)
    for controller in controllers:  # The chat's team row, setup line and model pill follow the account.
        controller.version += 1  # The chat's team row, setup line and model pill follow the account.
    preferences_ui.redraw()
    redraw_status_bars()  # The account item (team_status_bar) follows the account's state.
    return True


def redraw_status_bars():
    """Redraw every window's status bar: a global area, so only notifiers or the message bus reach it."""
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is not None:
        msgbus.publish_rna(key=(bpy.types.Window, "view_layer"))  # The status bar subscribes to it.


def _withdraw_key(ui, identity):
    """Forget a team connection's grant key and stop idle runtimes launched with it."""
    ui._memory_keys.pop(identity, None)
    _keyed.discard(identity)
    for controller in ui._controllers.values():
        if controller.state.get("connection_id") == identity and not controller.busy:
            if controller.provider is not None:
                threading.Thread(target=controller.provider.close, daemon=True).start()
                controller.provider = None
            controller.api_key = ""


def busy_team_chat(ui):
    return any(c.busy and c.state.get("connection_id") in _records for c in ui._controllers.values())


def draw_new_connection(layout):
    """AI Providers' New connection button, unless the organization allows only team connections."""
    if managed_policy.current().allow_personal_connections:
        layout.operator("angelo.new_connection", text="New connection", icon='ADD')


def _wrapped(layout, text, icon='NONE'):
    """Translate, then wrap: a translation can be longer than its English source."""
    for index, line in enumerate(text_layout.label_rows(shown(text))):
        layout.label(text=line, icon=icon if index == 0 else 'NONE', translate=False)


def _storage(view):
    name = store_text().name
    if view.stored == "saved":
        return Text(STORED, store=name)
    if view.stored == "unreadable":
        return Text(STORED_UNREADABLE, store=name)
    if view.stored in {"session", "failed"}:
        # The store's own reason already says the sign-in lasts until Angelo quits (team_token_store).
        return _account.storage_reason or Text(STORED_SESSION, reason=Text(NOT_SAVED, store=name))
    return ""


def draw(layout, ui):
    """The "Angelo team" section of AI Providers. False when the policy hides personal connections."""
    policy = managed_policy.current()
    if policy.managed:
        box = layout.box()
        _wrapped(box, Text(MANAGED), 'LOCKED')
        if policy.problems:
            _wrapped(box, Text(MANAGED_PROBLEM, keys=", ".join(policy.problems)), 'ERROR')
        if not policy.allow_personal_connections:
            _wrapped(box, Text(managed_policy.PERSONAL_OFF))
    layout.separator()
    layout.label(text="Angelo team")
    box = layout.box()
    if _account is None:
        _wrapped(box, _reason or Text(UNAVAILABLE), 'ERROR')
        return policy.allow_personal_connections
    view = _account.view()
    if view.status == SIGNED_IN:
        _draw_signed_in(box, view)
    elif view.status == SIGNING_IN:
        _wrapped(box, Text(WAITING, console=_account.console_url), 'TIME')
        row = box.row()
        row.operator("angelo.team_copy_sign_in_link", text="Copy sign-in link")
        row.operator("angelo.team_cancel_sign_in", text="Cancel")
    elif view.status == RESTORING:
        box.label(text="Restoring your Angelo sign-in…", icon='TIME')
    elif view.status == OFFLINE:
        _wrapped(box, view.message or Text(team_connections.SIGN_IN), 'ERROR')
        if policy.allow_personal_connections:
            _wrapped(box, Text(OFFLINE_RETRY))
        box.operator("angelo.team_sign_out", text="Sign out")
    else:
        _wrapped(box, Text(INVITE_REQUIRED if policy.team_only else INVITE))
        if view.message:
            _wrapped(box, view.message, 'INFO')
        problem = managed_policy.sign_in_problem(policy)
        if problem:
            _wrapped(box, problem, 'ERROR')
        else:
            box.operator("angelo.team_sign_in", text="Sign in to Angelo team", icon='URL')
    return policy.allow_personal_connections and not (policy.require_team_sign_in and not view.signed_in)


def _draw_signed_in(box, view):
    row = box.row()
    row.label(text=shown(Text(SIGNED_IN_AS, org=view.org_name, email=view.email or iface_("signed in"))),
              icon='COMMUNITY', translate=False)
    row.operator("angelo.team_refresh", text="", icon='FILE_REFRESH')
    row.operator("angelo.team_sign_out", text="Sign out")
    if _storage(view):
        _wrapped(box, _storage(view))
    from . import team_access
    from .team_chat import access
    state = access()

    def reason():  # The same reason the chat's row gives, with the console page that fixes it for an admin.
        _wrapped(box, state["title"], 'ERROR')
        if state["page"]:
            box.operator("angelo.team_open_console", text=shown(state["note"]), icon='URL', translate=False).page = state["page"].upper()
        else:
            _wrapped(box, state["note"])
    if state["state"] == team_access.NO_SEAT:  # The connections themselves can't be used, so they aren't listed.
        reason()
        return
    _wrapped(box, Text(SEAT), 'CHECKMARK')
    if state["state"] in {team_access.NO_PROVIDERS, team_access.NO_CODING}:
        reason()
    if view.providers is None and not view.providers_error:
        box.label(text="Checking which providers your team offers…")
    for problem in (view.providers_error, view.grant_error, view.message):
        if problem:
            _wrapped(box, problem, 'ERROR')
    for record in _records.values():
        entry = box.box()
        row = entry.row()
        row.label(text=record["name"], icon='COMMUNITY', translate=False)
        if ready(record):
            row.label(text="Available in chat" if is_coding(record) else "Ready", icon='CHECKMARK')
        _wrapped(entry, Text(ENTRY, provider=PROVIDER_NAMES[record["provider"]], status=status(record)))
    missing = team_connections.missing_providers(view)
    if missing and view.providers:
        _wrapped(box, Text(NOT_OFFERED, providers=", ".join(missing)))


def _require_account():
    if _account is None:
        raise RuntimeError(_reason or Text(UNAVAILABLE))
    return _account


def open_in_browser(url):
    """The one place a sign-in leaves the app, so window fixtures can play the browser instead."""
    return 'FINISHED' in bpy.ops.wm.url_open(url=url)


class ANGELO_OT_team_sign_in(Operator):
    bl_idname = "angelo.team_sign_in"
    bl_label = "Sign in to Angelo team"
    bl_description = ("Open your browser to sign in with your Angelo organization account. Angelo listens on "
                      "127.0.0.1 only until the browser answers (at most 10 minutes)")

    def execute(self, context):
        try:
            account = _require_account()
            # The browser pages in the person's language, translated here on the main thread.
            from bpy.app.translations import locale, pgettext_iface
            catalog, language, direction = team_pages.translations(pgettext_iface, locale)
            url = account.begin_sign_in(pages=team_pages.Pages(account.console_url, catalog=catalog,
                                                               language=language, direction=direction))
        except RuntimeError as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args else iface_(UNAVAILABLE))
            return {'CANCELLED'}
        if not open_in_browser(url):
            self.report({'WARNING'}, iface_("Couldn't open the browser; use Copy sign-in link"))
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_team_cancel_sign_in(Operator):
    bl_idname = "angelo.team_cancel_sign_in"
    bl_label = "Cancel Sign-in"
    bl_description = "Stop waiting for the browser and close the sign-in listener"

    def execute(self, context):
        if _account is not None:
            _account.cancel_sign_in()
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_team_copy_sign_in_link(Operator):
    bl_idname = "angelo.team_copy_sign_in_link"
    bl_label = "Copy Sign-in Link"
    bl_description = "Copy the console link to open in a browser yourself (it contains no secret)"

    def execute(self, context):
        if _account is None or not _account.view().sign_in_url:
            return {'CANCELLED'}
        context.window_manager.clipboard = _account.view().sign_in_url
        self.report({'INFO'}, iface_("Sign-in link copied"))
        return {'FINISHED'}


class ANGELO_OT_team_refresh(Operator):
    bl_idname = "angelo.team_refresh"
    bl_label = "Check Team Providers"
    bl_description = "Ask the Angelo console again which providers your team offers"

    def execute(self, context):
        if _account is not None:
            _account.refresh_providers()
        return {'FINISHED'}


class ANGELO_OT_team_sign_out(Operator):
    bl_idname = "angelo.team_sign_out"
    bl_label = "Sign out of Angelo team"
    bl_description = ("Sign this computer out of your Angelo team: the sign-in is revoked and removed from this "
                      "computer. Personal connections are not affected")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from . import ui
        if _account is None:
            return {'CANCELLED'}
        if busy_team_chat(ui):
            self.report({'ERROR'}, iface_("Stop the chat that is using a team connection first"))
            return {'CANCELLED'}
        _account.sign_out()
        pump(ui)
        return {'FINISHED'}


CLASSES = (ANGELO_OT_team_sign_in, ANGELO_OT_team_cancel_sign_in, ANGELO_OT_team_copy_sign_in_link,
           ANGELO_OT_team_refresh, ANGELO_OT_team_sign_out)


def register():
    from . import team_status_bar
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    team_status_bar.register()


def unregister():
    from . import team_status_bar
    close()
    team_status_bar.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
