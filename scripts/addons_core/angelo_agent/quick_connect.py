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

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from . import first_run
from .provider_registry import CODING, is_coding
from .ui_text import iface_, n_

# mode -> {"session", "record", "created", "phase", "reason", "fields", "bound", "started"};
# "reason" is an untranslated message id, filled from "fields" when it is drawn.
_flows = {}
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
    _flows[mode] = {"session": controller.state["id"], "record": None, "created": False, "phase": "checking",
                    "reason": "", "fields": {}, "bound": False, "started": time.monotonic()}
    _advance(ui, mode)
    controller.version += 1


def _advance(ui, mode):
    item = _flows[mode]
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
        item["record"] = record["id"]
        if not ui.connection_ready(record) and _identity(record) not in auth.running:
            # The provider's own browser sign-in; completion re-checks status in ui.pump.
            auth.start(record["mode"], ui.connection_settings(record["mode"], record=record), ui.state_directory(),
                       login=True, identity=_identity(record))
    record = store.records.get(item["record"])
    if record is None:
        _fail(ui, item, n_("The connection was removed. Try again."))
        return
    identity = _identity(record)
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


def _fail(ui, item, reason, **fields):
    """Show the reason with a retry; drop a connection this click created that never became ready."""
    item.update(phase="failed", reason=reason, fields=fields)
    record = ui.connections().records.get(item.get("record")) if item.get("created") else None
    if record and not record["reuse_native"] and not ui.connection_ready(record):
        ui.catalogs().invalidate(record["id"])
        ui.connections().remove(record["id"])
        item.update(record=None, created=False)


def cancel(ui, mode):
    """Stop waiting; remove a connection this flow created that never became ready."""
    item = _flows.pop(mode, None)
    if not item or not item["record"]:
        return
    store = ui.connections()
    record = store.records.get(item["record"])
    if record is None:
        return
    ui._authentication.cancel(_identity(record))
    if item["created"] and not ui.connection_ready(record) and not record["reuse_native"]:
        ui.catalogs().invalidate(record["id"])
        store.remove(record["id"])


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
    return tuple(sorted((mode, item["phase"], item["reason"]) for mode, item in _flows.items())), bool(_key_form)


def pump(ui):
    """Advance every flow on the main thread (``ui.pump``); True when the chat card must redraw."""
    before = _signature()
    connected = False
    for mode in list(_flows):
        item = _flows[mode]
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
    try:
        _advance_key_form(ui)
    except (RuntimeError, ValueError, OSError):
        _key_form.clear()
    # A flow in progress shows a spinner, so its card redraws every pump while it runs (a team sign-in too;
    # without the team module loaded there is none).
    team = sys.modules.get(__package__ + ".team_account_ui")
    return (_signature() != before or any(item["phase"] not in {"failed", "done"} for item in _flows.values())
            or bool(team and team.signing_in()))


def reset():
    _flows.clear()
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
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_quick_connect_cancel(Operator):
    bl_idname = "angelo.quick_connect_cancel"
    bl_label = "Cancel Connecting"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import ui
        cancel(ui, self.mode)
        return {'FINISHED'}


class ANGELO_OT_chat_example(Operator):
    bl_idname = "angelo.chat_example"
    bl_label = "Use Example Prompt"
    bl_description = "Put this example in the message box; press Return to send it"
    index: IntProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import console, chat_view
        area = context.area
        if not (0 <= self.index < len(first_run.EXAMPLES)) or not area or not console.is_chat(area) or not area.spaces.active.history:
            return {'CANCELLED'}
        value = first_run.EXAMPLES[self.index]
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
