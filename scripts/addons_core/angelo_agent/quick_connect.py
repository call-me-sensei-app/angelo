# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-step Connect from the chat: sign in, check and load models without leaving it.

A Connect click is the person's explicit choice to create (or reuse) a
connection, so it satisfies "auto discovery, not auto setup"
(``native_suggestions.py``). It does exactly what the
Preferences > AI Providers form does, through the same owners:

1. wait for the start-up ``auth status`` check if it is still running;
2. reuse this mode's existing connection, or add one with
   ``ConnectionStore.add`` (reusing the computer's sign-in when it is already
   signed in, otherwise a private profile, as the form does);
3. when it is not signed in, run the provider's own browser sign-in
   (``claude auth login`` / ``codex login``) through ``NativeAuthentication``,
   whose completion re-runs the status check (``ui.pump``);
4. once the connection is ready, bind this chat to it with
   ``ui.choose_model_connection`` and wait until its models load, so the
   composer shows a chosen model.

Failures stay in the chat card with a retry; Preferences is never opened on
the person's behalf. No credential is read, copied or stored here.
"""

import sys
import time
import uuid

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from . import first_run
from .display_text import error_message
from .provider_registry import CODING, is_coding
from .ui_text import iface_, n_

# mode -> {"session", "record", "created", "phase", "reason", "fields", "bound", "started"};
# "reason" is an untranslated message id, filled from "fields" when it is drawn.
_flows = {}
_reconnects = {}  # Token -> an exact chat/record watcher; never shares the welcome mode key.
# A pending "Use an API key" form: the records that existed before it opened.
_key_form = {}
KEY_FORM_SECONDS = 1800


def flow(mode):
    return _flows.get(mode)


def _identity(record):
    return record["mode"] if record["reuse_native"] else record["id"]


def _reusable(store, mode, ready):
    """This mode's existing connection: the one reusing the computer's sign-in first, then a ready one."""
    native = store.native_connection(mode)
    if native:
        return native
    records = [r for r in store.records.values() if r["mode"] == mode]
    return next((r for r in records if ready(r)), records[0] if records else None)


def start(ui, controller, mode):
    """Begin (or retry) connecting ``mode`` for ``controller``'s chat."""
    if mode not in first_run.CONNECT:
        raise ValueError("Only Claude and ChatGPT subscriptions connect in one step")
    if controller.busy:
        raise RuntimeError(iface_("Stop the current turn before connecting"))
    if not first_run.runtime_available(mode, ui._authentication.status.get(mode)):
        raise RuntimeError(iface_(first_run.CONNECT[mode]["install"]))
    _flows[mode] = {"token": uuid.uuid4().hex, "session": controller.state["id"], "record": None, "created": False, "phase": "checking",
                    "reason": "", "fields": {}, "bound": False, "started": time.monotonic()}
    _advance(ui, mode)
    controller.version += 1


def watch_reconnect(ui, controller, connection_id):
    """Watch the exact login the operator already started, independently of other chats and welcome flows."""
    record = ui.connections().records.get(connection_id)
    if not record or record["mode"] not in first_run.CONNECT or controller.selection_connection_id != connection_id:
        return
    attempt = ui._authentication.attempt(_identity(record))
    if attempt is None:
        return  # A watcher cannot invent an accepted authentication attempt.
    for token, item in list(_reconnects.items()):
        if item["controller"] is controller and item["record"] == connection_id:
            _reconnects.pop(token)  # Only this chat's earlier retry; its old Cancel token is now inert.
    token = uuid.uuid4().hex
    _reconnects[token] = {"token": token, "mode": record["mode"], "session": controller.state["id"],
        "controller": controller, "record": connection_id, "created": False, "phase": "signing_in",
        "reason": "", "fields": {}, "bound": False, "started": time.monotonic(),
        "reconnect_identity": _identity(record), "reconnect_attempt": attempt,
        "reconnect_profile": (record["mode"], record["reuse_native"], record.get("native_profile"))}


def reconnect_flow(controller):
    return next((item for item in _reconnects.values() if item["controller"] is controller
                 and item["session"] == controller.state["id"]
                 and item["record"] == controller.selection_connection_id), None)


def cancel_reconnect(ui, controller, token):
    item = _reconnects.get(token)
    if item is None or item["controller"] is not controller or not _reconnect_current(ui, item):
        return False
    _reconnects.pop(token)
    ui._authentication.cancel(item["reconnect_identity"], attempt=item["reconnect_attempt"])
    return True


def _reconnect_current(ui, item):
    controller = ui._controllers.get(item["session"])
    record = ui.connections().records.get(item["record"])
    return bool(controller is item["controller"] and controller.state["id"] == item["session"]
                and controller.selection_connection_id == item["record"] and record
                and (record["mode"], record["reuse_native"], record.get("native_profile")) == item["reconnect_profile"]
                and controller.selection_provider == record["mode"] and _identity(record) == item["reconnect_identity"]
                and ui._authentication.attempt(item["reconnect_identity"]) == item["reconnect_attempt"])


def _advance(ui, mode, item=None):
    item = _flows[mode] if item is None else item
    store, auth = ui.connections(), ui._authentication
    if item["record"] is None:
        if mode in auth.running:
            return  # The start-up status check decides whether the sign-in can be reused.
        record = _reusable(store, mode, ui.connection_ready)
        if record is None:
            # Same rule as the Add-connection form: reuse only a signed-in default profile.
            reuse = str(auth.status.get(mode, "")).startswith("Connected to ")
            record = store.add(first_run.CONNECT[mode]["provider"], "SUBSCRIPTION", reuse_native=reuse, group=CODING)
            item["created"] = True
            item["created_record"] = dict(record)
        item["record"] = record["id"]
        if not ui.connection_ready(record) and _identity(record) not in auth.running:
            # The provider's own browser sign-in; completion re-checks status in ui.pump.
            item["auth_identity"] = _identity(record)
            item["auth_attempt"] = auth.start(record["mode"], ui.connection_settings(record["mode"], record=record),
                                               ui.state_directory(), login=True, identity=item["auth_identity"])
    record = store.records.get(item["record"])
    if record is None:
        _fail(ui, item, n_("The connection was removed. Try again."))
        return
    identity = _identity(record)
    if "reconnect_identity" in item and not _reconnect_current(ui, item):
        item["phase"] = "done"  # An old notice cannot rebind a changed chat/profile.
        return
    if identity in auth.running:
        item["phase"] = "signing_in" if str(auth.status.get(identity, "")).startswith("Complete sign-in") else "checking"
        return
    if not ui.connection_ready(record):
        # Neither running nor signed in: the browser sign-in or its re-check failed.
        _fail(ui, item, first_run.CONNECT[mode]["retry"])
        return
    _bind(ui, item, record)


def _bind(ui, item, record):
    controller = ui._controllers.get(item["session"])
    if controller is None:
        item["phase"] = "done"
        return
    if not item["bound"]:
        ui.catalogs().errors.pop(record["id"], None)  # A retry loads the catalog again.
        ui.choose_model_connection(controller, record["id"])
        item["bound"] = True
        item["phase"] = "loading"
    error = ui.catalogs().errors.get(record["id"])
    if controller.selection_models or ui.catalogs().entries.get(record["id"]):
        item["phase"] = "done"
    elif error:
        _fail(ui, item, n_("Signed in, but models didn't load: {error}"), error=error[:160])


def _owns_created_setup(ui, item, record):
    if not item.get("created") or record != item.get("created_record"):
        return False
    identity = item.get("auth_identity", _identity(record))
    return ui._authentication.attempt(identity) == item.get("auth_attempt")


def _fail(ui, item, reason, **fields):
    """Show the reason with a retry; drop a connection this click created that never became ready."""
    item.update(phase="failed", reason=reason, fields=fields)
    record = ui.connections().records.get(item.get("record")) if item.get("created") else None
    if record and _owns_created_setup(ui, item, record) and not record["reuse_native"] and not ui.connection_ready(record):
        ui.catalogs().invalidate(record["id"])
        try:
            ui.connections().remove(record["id"])
        except OSError as error:   # Not saved (its file held open): it stays listed for the person to remove.
            print("Angelo: an unfinished connection could not be removed:", error)
        else:
            item.update(record=None, created=False)


def cancel(ui, mode, *, token=None):
    """Stop this exact welcome flow; a drawn Cancel cannot resolve a later mode-key replacement."""
    item = _flows.get(mode)
    if item is None or (token is not None and item["token"] != token):
        return False
    _flows.pop(mode)
    if not item["record"]:
        return True
    store = ui.connections()
    record = store.records.get(item["record"])
    if record is None:
        return
    owns_attempt = (item.get("auth_attempt") is not None
                    and ui._authentication.attempt(item["auth_identity"]) == item["auth_attempt"])
    if owns_attempt:
        ui._authentication.cancel(item["auth_identity"], attempt=item["auth_attempt"])
    if _owns_created_setup(ui, item, record) and not ui.connection_ready(record) and not record["reuse_native"]:
        ui.catalogs().invalidate(record["id"])
        try:
            store.remove(record["id"])
        except OSError as error:   # Not saved (its file held open): it stays listed for the person to remove.
            print("Angelo: an unfinished connection could not be removed:", error)


def watch_key_form(ui, controller):
    """Bind this chat to the coding connection the API-key form is about to create, once it is ready."""
    _key_form.clear()
    _key_form.update(session=controller.state["id"], before=set(ui.connections().records), started=time.monotonic())


def _advance_key_form(ui):
    if not _key_form:
        return
    if time.monotonic() - _key_form["started"] > KEY_FORM_SECONDS:
        _key_form.clear()
        return
    created = [r for i, r in ui.connections().records.items() if i not in _key_form["before"] and is_coding(r)]
    if created and ui.connection_ready(created[0]):
        item = {"session": _key_form["session"], "bound": False, "phase": "loading", "reason": "", "fields": {}}
        _key_form.clear()
        _bind(ui, item, created[0])


def _signature():
    return (tuple(sorted((mode, item["phase"], item["reason"]) for mode, item in _flows.items())),
            tuple(sorted((token, item["phase"], item["reason"]) for token, item in _reconnects.items())), bool(_key_form))


def pump(ui):
    """Advance every flow on the main thread (``ui.pump``); True when the chat card must redraw."""
    before = _signature()
    connected = False
    for mode in list(_flows):
        item = _flows[mode]
        if (item.get("auth_attempt") is not None
                and ui._authentication.attempt(item["auth_identity"]) != item["auth_attempt"]):
            _flows.pop(mode)  # A newer explicit login owns this identity; the earlier welcome cannot bind it.
            continue
        if item["phase"] == "failed":
            continue
        if item["phase"] != "done":
            try:
                _advance(ui, mode)
            except (RuntimeError, ValueError, OSError) as error:
                _fail(ui, item, n_("Couldn't connect: {error}"), error=str(error)[:200])
        if item["phase"] == "done":
            _flows.pop(mode, None)
            connected = True
    if connected:  # Once something connected, an earlier failed attempt is no longer news.
        for mode in [m for m, item in _flows.items() if item["phase"] == "failed"]:
            _flows.pop(mode)
    for token, item in list(_reconnects.items()):
        if not _reconnect_current(ui, item):
            _reconnects.pop(token)  # A closed/switched chat cannot be rebound and releases its watcher reference.
            continue
        if item["phase"] != "failed":
            try:
                _advance(ui, item["mode"], item)
            except (RuntimeError, ValueError, OSError) as error:
                _fail(ui, item, n_("Couldn't connect: {error}"), error=str(error)[:200])
        if item["phase"] == "done":
            _reconnects.pop(token)
    try:
        _advance_key_form(ui)
    except (RuntimeError, ValueError, OSError):
        _key_form.clear()
    # A flow in progress shows a spinner, so its card redraws every pump while it runs (a team sign-in too;
    # without the team module loaded there is none).
    team = sys.modules.get(__package__ + ".team_account_ui")
    return (_signature() != before or any(item["phase"] not in {"failed", "done"} for item in _flows.values())
            or any(item["phase"] not in {"failed", "done"} for item in _reconnects.values())
            or bool(team and team.signing_in()))


def reset():
    _flows.clear()
    _reconnects.clear()
    _key_form.clear()


class ANGELO_OT_quick_connect(Operator):
    bl_idname = "angelo.quick_connect"
    bl_label = "Connect AI Account"
    bl_description = "Connect this chat to your Claude or ChatGPT subscription, signing in through the provider's own browser page"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import ui
        try:
            start(ui, ui.get_controller(), self.mode)
        except (RuntimeError, ValueError, OSError) as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_quick_connect_cancel(Operator):
    bl_idname = "angelo.quick_connect_cancel"
    bl_label = "Cancel Connecting"
    mode: StringProperty(options={'SKIP_SAVE'})
    flow_token: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import ui
        cancel(ui, self.mode, token=self.flow_token)
        return {'FINISHED'}


class ANGELO_OT_chat_example(Operator):
    bl_idname = "angelo.chat_example"
    bl_label = "Use Example Prompt"
    bl_description = "Put this example in the message box; press Return to send it"
    index: IntProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import console, chat_view
        area = context.area
        examples = first_run.suggestions()
        if not (0 <= self.index < len(examples)) or not area or not console.is_chat(area) or not area.spaces.active.history:
            return {'CANCELLED'}
        value = examples[self.index]
        chat_view.set_draft(area.spaces.active.history[-1], value, len(value))
        return {'FINISHED'}


CLASSES = (ANGELO_OT_quick_connect, ANGELO_OT_quick_connect_cancel, ANGELO_OT_chat_example)


def register():
    reset()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    from . import providers_page_host  # The AI Providers canvas page's input (doc/angelo/ai_providers.rst).
    providers_page_host.register()
    if not bpy.app.background:   # The AI Providers hero's banner, read once at start-up (providers_page_model).
        bpy.app.timers.register(providers_page_model_preload(), first_interval=0.1)


def providers_page_model_preload():
    from .providers_page_model import preload_banner
    return preload_banner


def unregister():
    from . import providers_page_host
    providers_page_host.unregister()
    reset()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
