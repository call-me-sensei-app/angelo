# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread owner of local CLI access: preference, listener, External conversations.

Everything here runs from ``ui.pump`` on Blender's main thread. The listener
and connection threads (``external_cli``/``external_service``) never touch
``bpy`` or a controller; they queue ``Work`` that ``ExternalHost.drain``
claims here. Reads go through the real ``Controller.request_tool`` path under
a fenced host request and execute in the shared ``ToolDispatchBudget``.

Each client label maps to one ``External: <label>`` conversation, recorded in
``<state>/cli/clients.json``. It rotates only through ``angelo session new``.
After a document load the conversation is invalid like any chat and calls
report ``stale`` until that explicit rotation.
"""

import os
import tempfile
import time
from pathlib import Path

from . import change_clock, managed_policy, product_env
from .external_agent import AgentChannels, AgentUi
from .external_visibility import VisibilityUi
from .external_clients import AppRegistry
from .display_text import Text, shown
from .chat_settings import clean_options
from .external_cli import LocalCliServer, TransportUnavailable, key_id
from .external_requests import AuditLog, ClientRegistry, external_label  # noqa: F401 (re-exported)
from .external_service import RequestService
from .invocations import MAX_MUTATION_IDS, InvocationGuardError, document_digest
from .sessions import SessionStore
from .ui_text import iface_, n_, notice

# The listener's two steady states, shown translated in Preferences (any other status is an error, shown as written).
OFF, LISTENING = n_("Off"), n_("Listening")
# ANGELO_DISABLE_LOCAL_CLI=1 (or a deprecated spelling, see product_env) keeps the listener closed.
KILL_SWITCH_NAME = "DISABLE_LOCAL_CLI"
KILL_SWITCH = product_env.names(KILL_SWITCH_NAME)[0]
# Session headers require a coding provider mode; External conversations never start one.
EXTERNAL_PROVIDER = "codex_subscription"
COMPOSER_NOTICE = "External app: approvals and Stop only"
DRAIN_ITEMS = 8
DRAIN_SECONDS = 0.004
RETRY_START_SECONDS = 5.0


class UiEnvironment(AgentUi, VisibilityUi):
    """The host's only view of ``ui``; tests substitute a bpy-free double."""

    def __init__(self, ui):
        self.ui = ui

    def state_directory(self):
        return self.ui.state_directory()

    def document_key(self):
        return self.ui.document_key()

    def controllers(self):
        return self.ui._controllers

    def sessions(self):
        self.ui.get_controller()  # Creates the index and makes ui.pump service _controllers.
        return self.ui._sessions

    def access_requested(self):
        prefs = self.ui.preferences()
        return bool(prefs is not None and getattr(prefs, "allow_local_cli", False))

    def make_controller(self, session_id):
        self.sessions()
        controller = self.ui.Controller(self.state_directory(), session_id=session_id)
        from .document_identity import restore
        restore(controller, self.ui)   # As ui.get_controller: a conversation about another file waits for it.
        # As ui.get_controller binds every chat: the snapshot an app's first approved edit is preceded by.
        controller.checkpoint_factory = lambda target=controller: self.ui.capture_checkpoint(target)
        return controller

    def describe(self):
        import bpy
        from importlib import import_module
        version = import_module(__package__).bl_info["version"]
        return {"blender_version": bpy.app.version_string, "addon_version": ".".join(map(str, version))}

    def private_roots(self):
        import bpy
        roots = [(self.state_directory(), "<angelo-state>"), (self.ui.connection_directory(), "<angelo-connections>"),
                 (tempfile.gettempdir(), "<temp>"), (str(Path.home()), "<home>")]
        if bpy.data.filepath:
            roots.append((str(Path(bpy.data.filepath).parent), "<project>"))
        return roots


class ExternalHost(AgentChannels):
    def __init__(self, environment, *, server_factory=LocalCliServer, environ=None, runtime_directory=None):
        self.env = environment
        self.server_factory = server_factory
        self.environ = os.environ if environ is None else environ
        self.runtime_directory = runtime_directory
        self.server = self.service = None
        self.status = "Off"
        self.closing = False
        self.failed = False  # After an internal error, stay off until the toggle is cycled.
        self.holder = None   # (version, pid) of another Angelo build that has access; start retries until it's free.
        self.mapping = {}
        self._retry_after = 0.0
        self._snapshot = {}
        # Connected MCP apps (external_clients): records and the listener's key view, reloaded when the
        # registry changes.
        self._apps, self._app_keys, self._apps_stamp = {}, {}, None
        # Apps working through Angelo's agent (external_agent): parked reads, recent sends, conversations each had.
        self._agent_waiters, self._sends, self._owned, self._tasks = [], {}, {}, {}
        self._goals = {}   # label -> conversation -> the goal the app's newest send carried (external_agent._state).
        self._held = {}   # label -> task -> (its pending message, the task it joined): external_status.track.
        self._looks, self._views = {}, {}   # Each app's last looks (throttle) and its viewport pictures (pruned).

    # Lifecycle ---------------------------------------------------------------

    def wanted(self):
        reason = kill_switch_reason(self.environ)
        if reason:
            return False, reason
        if not managed_policy.current().allow_local_cli_access:
            return False, Text(managed_policy.CLI_OFF)  # AllowLocalCLIAccess: the socket stays closed.
        if not self.env.access_requested():
            return False, "Off"
        return True, ""

    def pump(self):
        wanted, reason = self.wanted()
        if not wanted:
            if self.server is not None:
                self.stop(reason)
            self.status, self._retry_after, self.failed, self.holder = reason, 0.0, False, None
            return
        if self.failed or self.server is None and not self.start():
            return
        self.publish()
        self.drain()
        self.pump_agents()

    def apps(self):
        """Connected MCP apps, name -> record (main thread)."""
        return self._apps

    def reload_apps(self, force=False):
        """Re-read the app registry when it changed; a person's Connect or Disconnect takes effect on the next tick."""
        registry = AppRegistry(self.env.state_directory())
        stamps = []
        keys = sorted(registry.keys.iterdir()) if registry.keys.is_dir() else []
        for path in (registry.path, registry.keys, *keys):
            try:
                info = path.stat()   # A stat in the clock's current tick (Windows) is read again next tick.
                stamps.append(change_clock.cache_key(info, (info.st_mtime_ns, info.st_size, info.st_mode)))
            except OSError:
                stamps.append(None)
        if force or stamps != self._apps_stamp:
            before, keys = set(self._apps), dict(self._app_keys)
            self._apps, self._app_keys, self._apps_stamp = registry.load(), registry.snapshot(), stamps
            from . import external_access
            ended = {label: "This app was disconnected in Angelo" for label in before - set(self._apps)}
            ended.update({label: "This app was connected again with a new key in Angelo" for label in keys
                          if label in self._app_keys and keys[label] != self._app_keys[label]})   # A reconnect too.
            for label, why in sorted(ended.items()):   # Its work in flight and queued ends now.
                if self.service is not None:
                    self.service.work.fail_label(label, {"code": "unavailable", "message": why})
                external_access.revoke(self, label, why)

    def start(self):
        if time.monotonic() < self._retry_after:
            return False
        directory = self.env.state_directory()
        self.mapping = ClientRegistry(directory).load()
        service = RequestService(lambda: self._snapshot, audit=AuditLog(Path(directory) / "cli" / "audit.jsonl"))
        self.reload_apps(force=True)
        server = self.server_factory(directory, service.handle, describe=service.describe,
                                     runtime_directory=self.runtime_directory, apps=lambda: self._app_keys,
                                     reserved=lambda: frozenset(self._apps))
        try:
            server.start()
        except TransportUnavailable as error:
            self.status, self._retry_after = "Unavailable: " + str(error), time.monotonic() + RETRY_START_SECONDS
            self.holder = error.holder
            return False
        self.server, self.service, self.closing, self.holder = server, service, False, None
        self.status = LISTENING
        self.publish()
        return True

    def stop(self, reason="Off"):
        """Close the listener and end every app's access (``external_access``); queued work reports unavailable."""
        server, service = self.server, self.service
        self.server = self.service = None
        if service is not None:
            service.closing = True
            service.work.fail_all({"code": "unavailable", "message": "Local CLI access was turned off"})
        self.closing = True
        try:
            from . import external_access
            for label in sorted(set(self._apps) | set(self._owned)):
                external_access.revoke(self, label, "Access for external apps was turned off")
            self.close_agents("Access for external apps was turned off")
        finally:
            self.closing = False
            if server is not None:
                server.stop()
            self._snapshot = {}
            self.status = reason

    def publish(self):
        """Immutable view for connection threads; rebuilt only on the main thread."""
        self.reload_apps()
        self._snapshot = {"document": document_digest(repr(self.env.document_key())),
                          "conversations": dict(self.mapping),
                          "instance": getattr(self.server, "instance", None), **self.env.describe()}

    # Main-thread work ----------------------------------------------------------

    def drain(self):
        deadline = time.monotonic() + DRAIN_SECONDS
        handlers = {"status": self.handle_status, "session_new": self.handle_session_new,
                    "agent": self.handle_agent}
        for index in range(DRAIN_ITEMS):
            # The first item runs even past the deadline, so a tick that starts late still serves a waiting request.
            if self.service is None or index and time.monotonic() >= deadline:
                return
            work = self.service.work.take()
            if work is None:
                return
            if not work.claim():
                continue  # The waiter timed out first; abandoned work never runs.
            client = getattr(work, "client", None)
            if getattr(client, "app", False) and (client.label not in self._apps or
                                                  client.key != key_id(self._app_keys.get(client.label))):
                # Authorized again here, on the main thread: a command admitted before a Disconnect, or proved with a
                # key a reconnect replaced, never runs after it.
                work.complete({"code": "unavailable", "message": "This app was disconnected or connected again in "
                               "Angelo; connect it and send again"})
                continue
            try:
                handlers[work.kind](work)
            except Exception as error:
                # Never leave a waiter hanging; the listener is optional, the chat pump is not.
                work.complete({"code": "unavailable", "message": "Angelo could not process the CLI request "
                               f"({type(error).__name__}: {error})"})
                if not isinstance(error, (OSError, ValueError, RuntimeError, InvocationGuardError)):
                    raise

    def conversation(self, label, *, create):
        directory = self.env.state_directory()
        controllers = self.env.controllers()
        identity = self.mapping.get(label)
        if identity in controllers:
            controller = controllers[identity]
        elif identity and SessionStore(directory, identity).path.is_file():
            controller = controllers[identity] = self.env.make_controller(identity)
        else:
            controller = None
        if controller is not None and controller.state.get("external_app") and label not in self._apps:
            controller = None   # A disconnected app's conversation is never a keyless client's under its name.
        if controller is None:
            if not create:
                return None
            identity = self.create_conversation(label)
            controller = controllers[identity] = self.env.make_controller(identity)
        controller.external_client = label
        if create and label in self._apps:   # The app's own message on its connection now (external_access).
            from .external_access import stamp
            stamp(controller, self._apps[label])
        return controller

    def create_conversation(self, label, previous=None, approval=None):
        """A fresh External conversation for ``label``. ``approval``: the mode of the conversation it replaces (a new
        chat, a move after a load), which a fresh one never loosens (``external_settings.stricter``)."""
        from .external_settings import stricter
        directory = self.env.state_directory()
        state = SessionStore(directory).new(EXTERNAL_PROVIDER)
        # The chat default (Approve for me: bounded typed edits run, scripts and other effects ask), as a new chat;
        # RequireAskForEdits still makes it Ask. Angelo's agent's scripts take the person's standing consents as in any
        # chat (trusted_scripts). A CLI label stays in Ask (external_cli.rst).
        state["options"] = clean_options({} if label in self.apps() else {"approval": "ASK"})
        if label in self._apps:   # Stamped from its start (a new chat, a move too): revoked holds across a restart.
            from .external_access import stamp_value
            state["external_app"] = stamp_value(directory, label, self._apps[label])
        for floor in (approval, self._apps.get(label, {}).get("approval")):   # The conversation it replaces, and the
            if floor:   # mode the app's last conversation ended in (a delete too): the person's stricter choice holds.
                state["options"]["approval"] = stricter(state["options"]["approval"], floor)
        # The first user row titles the switcher entry `External: <label>`,
        # also in builds that predate the CLI (they show it as an ordinary chat).
        state["messages"].append({"role": "user", "text": "External: " + label,
                                  "request_kind": "external_session", "client": label})
        if label in self.apps():
            note = notice("MCP app '{label}' works with Angelo's agent here. Approve or decline what the agent asks, "
                          "press Stop to end its work and Undo or restore as in any chat. The app can never approve.",
                          label=label)
        elif previous:
            note = notice("Local CLI client '{label}'. It reads Angelo's status only and runs no tool: no scene, file, "
                          "memory, billing or provider changes. Rotated from conversation {previous} by `angelo session "
                          "new`.", label=label, previous=previous)
        else:
            note = notice("Local CLI client '{label}'. It reads Angelo's status only and runs no tool: no scene, file, "
                          "memory, billing or provider changes.", label=label)
        state["messages"].append({"role": "system", **note})
        SessionStore(directory, state["id"]).save(state)
        sessions = self.env.sessions()
        sessions.update(state)
        sessions.save()
        ClientRegistry(directory).save({label: state["id"]})   # Only this app's: another instance's stay as they are.
        self.mapping = dict(self.mapping, **{label: state["id"]})
        return state["id"]

    def _reply(self, work, controller, code, message, **fields):
        fields.setdefault("document", document_digest(repr(self.env.document_key())))
        fields.setdefault("conversation", controller.state["id"] if controller is not None else None)
        if fields.get("result") is not None:
            # Captured now, on the main thread: the projection must not depend on
            # a later snapshot that a concurrent stop may already have cleared.
            fields["projection_roots"] = tuple(self.env.private_roots())
        work.complete(dict(fields, code=code, message=message))

    def handle_status(self, work):
        controller = self.conversation(work.client.label, create=False)
        result = {"phase": 1, "read_only": True, "client": work.client.label, "conversation": None,
                  "commands": ["status", "session new"]}
        if controller is not None:
            identity, ledger = controller.state["id"], controller.state.get("invocations") or {}
            result["conversation"] = {
                "id": identity, "title": "External: " + work.client.label,
                "policy": clean_options(controller.state.get("options"))["approval"],
                "document_valid": not controller.document_invalid and controller.document == self.env.document_key(),
                "busy": controller.busy,
                "pending_calls": len(controller.pending),
                "ledger": {"used": len(ledger.get("entries") or {}), "capacity": MAX_MUTATION_IDS,
                           "closed": bool(ledger.get("closed"))}}
        from . import headless_window_app   # A hidden-window session names itself (mcp_headless_window.rst).
        if headless_window_app.session():
            result["hidden_session"] = headless_window_app.session()
        self._reply(work, controller, "ok", "Local CLI access is on (read-only phase 1)", result=result)

    def handle_session_new(self, work):
        label = work.client.label
        old = self.conversation(label, create=False)
        if old is not None and (old.pending or old.busy):
            return self._reply(work, old, "busy", "The current External conversation still has calls in flight")
        previous = old.state["id"] if old is not None else None
        identity = self.create_conversation(label, previous=previous)
        if old is not None:
            old.emit("message", {"role": "system", **notice("`angelo session new` moved client '{label}' to "
                                 "conversation {identity}. This transcript stays readable.", label=label, identity=identity)})
        controller = self.conversation(label, create=False)
        self._reply(work, controller, "ok", "Started a new External conversation",
                    result={"conversation": identity, "previous": previous})


_host = None
_notice_failed = False


def pump(ui):
    """``ui.pump`` hook. A CLI defect closes the listener; it never breaks the chat pump."""
    global _host
    if _host is None:
        _host = ExternalHost(UiEnvironment(ui))
    try:
        _host.pump()
    except Exception as error:
        from . import swallowed_errors
        swallowed_errors.report("apps' access stopped after an internal error", error, quiet=True)   # Said below.
        try:
            _host.stop("Stopped after an internal error: " + type(error).__name__)
        except Exception:
            _host.server = _host.service = None
        _host.status = f"Stopped after an internal error ({type(error).__name__}: {error}); toggle access to retry"
        _host.failed = True
    from . import session_page   # Apps' live session pages, apart: a page's defect never touches the listener.
    session_page.pump_safely(_host)
    refresh_notice(ui)


def refresh_notice(ui):
    """The status bar's "waiting for your approval" notice (external_ui); a defect there never stops the listener."""
    global _notice_failed
    if _notice_failed:
        return
    try:
        from . import external_ui
        external_ui.refresh(ui)
    except Exception as error:  # noqa: BLE001 - cosmetic; the card in the conversation is still there.
        _notice_failed = True
        print("Angelo: the approval notice is off after an error:", type(error).__name__, error)


def shutdown():
    global _host
    if _host is not None:
        _host.stop()
    _host = None
    from . import session_page   # Every live session page ends with the apps' access.
    session_page.unregister()


def kill_switch_reason(environ=None):
    """``Disabled by <VARIABLE>=1`` naming the variable actually set, or None."""
    value, variable = product_env.lookup(KILL_SWITCH_NAME, environ)
    return f"Disabled by {variable}=1" if value == "1" else None


def status_text():
    return kill_switch_reason() or (_host.status if _host is not None else "Off")


def holder():
    """``(version, pid)`` of another Angelo build that has access while this one waits for it, or None."""
    return _host.holder if _host is not None and not kill_switch_reason() else None


def draw_preferences(layout, prefs):
    box = layout.box()
    box.label(text="External AI apps (MCP) and the angelo CLI")
    if not managed_policy.current().allow_local_cli_access:
        box.label(text=shown(Text(managed_policy.CLI_OFF)), icon='LOCKED', translate=False)
    elif prefs is not None:
        box.prop(prefs, "allow_local_cli")
    box.label(text=iface_("Status: {status}", status=iface_(status_text())), translate=False)  # Errors: as written.
    box.label(text="Apps can never approve their own changes.")
    box.label(text="Private local connection with a key per app. No network port.")
