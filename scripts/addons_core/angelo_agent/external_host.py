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

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from . import managed_policy, product_env
from .display_text import Text, shown
from .chat_settings import clean_options
from .external_cli import LocalCliServer, TransportUnavailable
from .external_requests import AuditLog, result_code
from .external_service import RequestService
from .invocations import MAX_MUTATION_IDS, InvocationGuardError, document_digest
from .sessions import SessionStore
from .ui_text import iface_, n_, notice

# The listener's two steady states, shown translated in Preferences (any other status is an error, shown as written).
OFF, LISTENING = n_("Off"), n_("Listening · read-only (phase 1)")
# ANGELO_DISABLE_LOCAL_CLI=1 (or a deprecated spelling, see product_env) keeps the listener closed.
KILL_SWITCH_NAME = "DISABLE_LOCAL_CLI"
KILL_SWITCH = product_env.names(KILL_SWITCH_NAME)[0]
# Session headers require a coding provider mode; External conversations never start one.
EXTERNAL_PROVIDER = "codex_subscription"
COMPOSER_NOTICE = "External client: approvals and Stop only"
DRAIN_ITEMS = 8
DRAIN_SECONDS = 0.004
RETRY_START_SECONDS = 5.0


class ClientRegistry:
    """label -> conversation ID in ``<state>/cli/clients.json`` (owner-only, atomic replace).

    A label is routing and display data, not a security principal.
    """

    def __init__(self, state_directory):
        self.path = Path(state_directory) / "cli" / "clients.json"

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        clients = raw.get("clients") if isinstance(raw, dict) else None
        output = {}
        for label, record in (clients or {}).items() if isinstance(clients, dict) else ():
            try:
                output[label] = str(uuid.UUID(record["session_id"]))
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
        return output

    def save(self, mapping):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name("clients." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "clients": {label: {"session_id": identity}
                                                 for label, identity in mapping.items()}}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


def external_label(state_directory, session_id):
    """The client label owning a conversation, or "" (used when ui builds a controller)."""
    for label, identity in ClientRegistry(state_directory).load().items():
        if identity == session_id:
            return label
    return ""


class UiEnvironment:
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
        controller.set_document(self.document_key())
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


class ExternalHost:
    def __init__(self, environment, *, server_factory=LocalCliServer, environ=None, runtime_directory=None):
        self.env = environment
        self.server_factory = server_factory
        self.environ = os.environ if environ is None else environ
        self.runtime_directory = runtime_directory
        self.server = self.service = None
        self.status = "Off"
        self.closing = False
        self.failed = False  # After an internal error, stay off until the toggle is cycled.
        self.mapping = {}
        self._open = {}  # session_id -> fence of an open External read request
        self._retry_after = 0.0
        self._snapshot = {}

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
            self.status, self._retry_after, self.failed = reason, 0.0, False
            return
        if self.failed or self.server is None and not self.start():
            return
        self.publish()
        self.drain()
        self.close_finished()

    def start(self):
        if time.monotonic() < self._retry_after:
            return False
        directory = self.env.state_directory()
        self.mapping = ClientRegistry(directory).load()
        service = RequestService(lambda: self._snapshot, audit=AuditLog(Path(directory) / "cli" / "audit.jsonl"))
        server = self.server_factory(directory, service.handle, describe=service.describe,
                                     runtime_directory=self.runtime_directory)
        try:
            server.start()
        except TransportUnavailable as error:
            self.status, self._retry_after = "Unavailable: " + str(error), time.monotonic() + RETRY_START_SECONDS
            return False
        self.server, self.service, self.closing = server, service, False
        self.status = LISTENING
        self.publish()
        return True

    def stop(self, reason="Off"):
        """Close the listener and end External reads; queued work reports unavailable."""
        server, service = self.server, self.service
        self.server = self.service = None
        if service is not None:
            service.closing = True
            service.work.fail_all({"code": "unavailable", "message": "Local CLI access was turned off"})
        self.closing = True
        try:
            for identity in list(self._open):
                controller = self.env.controllers().get(identity)
                if controller is not None:
                    controller.cancel_tools("Local CLI access was turned off; the read was not completed")
            self.close_finished()
        finally:
            self.closing = False
            if server is not None:
                server.stop()
            self._snapshot = {}
            self.status = reason

    def publish(self):
        """Immutable view for connection threads; rebuilt only on the main thread."""
        self._snapshot = {"document": document_digest(repr(self.env.document_key())),
                          "conversations": dict(self.mapping),
                          "instance": getattr(self.server, "instance", None), **self.env.describe()}

    # Main-thread work ----------------------------------------------------------

    def drain(self):
        deadline = time.monotonic() + DRAIN_SECONDS
        handlers = {"status": self.handle_status, "call": self.handle_call, "session_new": self.handle_session_new}
        for index in range(DRAIN_ITEMS):
            # The first item runs even past the deadline, so a tick that starts late still serves a waiting request.
            if self.service is None or index and time.monotonic() >= deadline:
                return
            work = self.service.work.take()
            if work is None:
                return
            if not work.claim():
                continue  # The waiter timed out first; abandoned work never runs.
            try:
                handlers[work.kind](work)
            except Exception as error:
                # Never leave a waiter hanging; the listener is optional, the chat pump is not.
                work.complete({"code": "unavailable", "message": "Angelo could not process the CLI request "
                               f"({type(error).__name__}: {error})"})
                if not isinstance(error, (OSError, ValueError, RuntimeError, InvocationGuardError)):
                    raise

    def close_finished(self):
        for identity, fence in list(self._open.items()):
            controller = self.env.controllers().get(identity)
            if controller is None or not controller.pending:
                if controller is not None:
                    controller.close_external_read(fence)
                del self._open[identity]

    def conversation(self, label, *, create):
        directory = self.env.state_directory()
        controllers = self.env.controllers()
        identity = self.mapping.get(label)
        if identity in controllers:
            controller = controllers[identity]
        elif identity and SessionStore(directory, identity).path.is_file():
            controller = controllers[identity] = self.env.make_controller(identity)
        elif create:
            identity = self.create_conversation(label)
            controller = controllers[identity] = self.env.make_controller(identity)
        else:
            return None
        controller.external_client = label
        return controller

    def create_conversation(self, label, previous=None):
        directory = self.env.state_directory()
        state = SessionStore(directory).new(EXTERNAL_PROVIDER)
        # Not a person's chat: it stays in Ask rather than the chat default (Auto for External
        # conversations is an open phase-2 question, doc/angelo/external_cli.rst).
        state["options"] = clean_options({"approval": "ASK"})
        # The first user row titles the switcher entry `External: <label>`,
        # also in builds that predate the CLI (they show it as an ordinary chat).
        state["messages"].append({"role": "user", "text": "External: " + label,
                                  "request_kind": "external_session", "client": label})
        if previous:
            note = notice("Local CLI client '{label}'. Phase 1 is read-only: observations only; no scene, file, memory, "
                          "billing or provider changes. Approvals and Stop only. Rotated from conversation {previous} "
                          "by `angelo session new`.", label=label, previous=previous)
        else:
            note = notice("Local CLI client '{label}'. Phase 1 is read-only: observations only; no scene, file, memory, "
                          "billing or provider changes. Approvals and Stop only.", label=label)
        state["messages"].append({"role": "system", **note})
        SessionStore(directory, state["id"]).save(state)
        sessions = self.env.sessions()
        sessions.update(state)
        sessions.save()
        mapping = dict(self.mapping, **{label: state["id"]})
        ClientRegistry(directory).save(mapping)
        self.mapping = mapping
        return state["id"]

    def _reply(self, work, controller, code, message, **fields):
        fields.setdefault("document", document_digest(repr(self.env.document_key())))
        fields.setdefault("conversation", controller.state["id"] if controller is not None else None)
        if fields.get("result") is not None:
            # Captured now, on the main thread: the projection must not depend on
            # a later snapshot that a concurrent stop may already have cleared.
            fields["projection_roots"] = tuple(self.env.private_roots())
        work.complete(dict(fields, code=code, message=message))

    def handle_call(self, work):
        payload = work.payload
        controller = self.conversation(work.client.label, create=True)
        document = self.env.document_key()
        identity = controller.state["id"]
        if payload["conversation"] and payload["conversation"] != identity:
            return self._reply(work, controller, "stale", "This client's External conversation changed; "
                               "observe again in the current conversation")
        if controller.document_invalid or controller.document != document:
            return self._reply(work, controller, "stale", "The Blender document changed. Run `angelo session new` "
                               "for a fresh External conversation, then observe again")
        if payload["expect_document"] and payload["expect_document"] != document_digest(repr(document)):
            return self._reply(work, controller, "stale", "The open document is not the expected one; observe again")
        if len(controller.pending) >= controller.tools.maxsize:
            return self._reply(work, controller, "busy", "Too many pending calls in this External conversation")
        fence = self._open.get(identity)
        if fence is not None and (not fence.matches(controller) or controller.cancelled) and not controller.pending:
            controller.close_external_read(fence)  # A stopped/ended request with nothing left in flight.
            del self._open[identity]
            fence = None
        if fence is None:
            if controller.busy or controller.pending:
                return self._reply(work, controller, "busy", "This External conversation is finishing earlier work")
            fence = self._open[identity] = controller.open_external_read()
        elif not fence.matches(controller) or controller.cancelled:
            return self._reply(work, controller, "busy", "This External conversation was stopped; retry shortly")
        admission = {"active": True}

        def delivered(result):
            replayed = bool(isinstance(result, dict) and result.get("replayed"))
            if admission["active"] and not replayed:
                code = "refused"  # Synchronous admission refusal: nothing was admitted.
            elif self.closing:
                code = "unavailable"
            else:
                code = result_code(result, cancelled=controller.cancelled,
                                   document_changed=controller.document_invalid or controller.document != document)
            message = "Completed" if code == "ok" else str(result.get("error") or code) if isinstance(result, dict) else code
            self._reply(work, controller, code, message, result=result, conversation=identity,
                        call={"replayed": replayed, "admitted": not admission["active"] or replayed})

        controller.request_tool("external/" + payload["id"], payload["tool"], payload["arguments"], delivered,
                                request_fence=fence)
        admission["active"] = False
        work.admitted = identity + "/external/" + payload["id"] in controller.pending

    def handle_status(self, work):
        controller = self.conversation(work.client.label, create=False)
        result = {"phase": 1, "read_only": True, "client": work.client.label, "conversation": None,
                  "commands": ["status", "tools", "call", "session new"]}
        if controller is not None:
            identity, ledger = controller.state["id"], controller.state.get("invocations") or {}
            result["conversation"] = {
                "id": identity, "title": "External: " + work.client.label,
                "policy": clean_options(controller.state.get("options"))["approval"],
                "document_valid": not controller.document_invalid and controller.document == self.env.document_key(),
                "open_request": identity in self._open, "busy": controller.busy,
                "pending_calls": len(controller.pending),
                "ledger": {"used": len(ledger.get("entries") or {}), "capacity": MAX_MUTATION_IDS,
                           "closed": bool(ledger.get("closed"))}}
        call_id = work.payload.get("call")
        if call_id is not None:
            result["call"] = self.call_status(controller, call_id)
        self._reply(work, controller, "ok", "Local CLI access is on (read-only phase 1)", result=result)

    @staticmethod
    def call_status(controller, call_id):
        if controller is None:
            return {"id": call_id, "known": False, "state": "unknown"}
        key = controller.state["id"] + "/external/" + call_id
        record = controller.state["results"].get(key)
        phase = record.get("phase") if isinstance(record, dict) else None
        call = controller.pending.get(key)
        if call is not None:
            state = "executing" if call.executing else "approval" if call.approval else "queued"
            return {"id": call_id, "known": True, "state": state, "receipt_phase": phase}
        if record is not None:
            return {"id": call_id, "known": True, "state": "finished" if phase == "completed" else "interrupted",
                    "receipt_phase": phase}
        if key in (controller.state.get("invocations") or {}).get("entries", {}):
            return {"id": call_id, "known": True, "state": "admitted_result_pruned", "receipt_phase": None}
        return {"id": call_id, "known": False, "state": "unknown"}

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


def pump(ui):
    """``ui.pump`` hook. A CLI defect closes the listener; it never breaks the chat pump."""
    global _host
    if _host is None:
        _host = ExternalHost(UiEnvironment(ui))
    try:
        _host.pump()
    except Exception as error:
        try:
            _host.stop("Stopped after an internal error: " + type(error).__name__)
        except Exception:
            _host.server = _host.service = None
        _host.status = f"Stopped after an internal error ({type(error).__name__}: {error}); toggle access to retry"
        _host.failed = True


def shutdown():
    global _host
    if _host is not None:
        _host.stop()
    _host = None


def kill_switch_reason(environ=None):
    """``Disabled by <VARIABLE>=1`` naming the variable actually set, or None."""
    value, variable = product_env.lookup(KILL_SWITCH_NAME, environ)
    return f"Disabled by {variable}=1" if value == "1" else None


def status_text():
    return kill_switch_reason() or (_host.status if _host is not None else "Off")


def draw_preferences(layout, prefs):
    box = layout.box()
    box.label(text="Local CLI access for coding agents")
    if not managed_policy.current().allow_local_cli_access:
        box.label(text=shown(Text(managed_policy.CLI_OFF)), icon='LOCKED', translate=False)
    elif prefs is not None:
        box.prop(prefs, "allow_local_cli")
    box.label(text=iface_("Status: {status}", status=iface_(status_text())), translate=False)  # Errors: as written.
    box.label(text="Read-only phase 1: observation tools only; no scene, file, memory or billing changes.")
    box.label(text="Same-user local socket with a per-launch token. No network listener.")
