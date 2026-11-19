# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-connection CLI command handling on the service thread (never ``bpy``).

``RequestService.handle`` turns one authenticated request into one envelope. ``status``, ``session new`` and a
connected app's ``agent`` commands become ``Work`` for the host's main-thread drain and are awaited with bounded
timeouts (``busy`` when the host never claimed it), never an invented result. No client runs a tool: since 2.0.1
``call`` and the tool catalogue are refused for every client, an external agent works through Angelo's agent
(doc/angelo/external_mcp.rst; doc/angelo/external_cli.rst).
"""

import uuid

from . import external_cli_protocol as protocol
from .external_requests import MAIN_THREAD_TIMEOUT, RateLimiter, Work, WorkQueue, _digest, default_roots, project
from .routing import TOOLSET_VERSION

# A connected app's commands to Angelo's agent (external_agent); one read never outlasts an MCP client's tool timeout.
MAX_MESSAGE = 32000
MAX_AGENT_WAIT = 50.0
MAX_GOAL = 2000
AGENT_ACTIONS = frozenset({"send", "read", "status", "stop", "image", "scene", "viewport", "settings", "new_chat",
                           "look", "window", "state", "usage"})   # The last four: external_visibility.
APPROVALS = frozenset({"ASK", "READ_ONLY", "AUTO_BOUNDED"})   # An app may ask for any; only narrowing applies.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_ATTACHMENTS = 4   # references.MAX_IMAGES: a message's attachments, as the composer takes them.
DETAILS = frozenset({"brief", "full"})


def _uuid_or_none(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


class RequestService:
    """Owns validation, the work queue, rate limits and audit for one listener."""

    def __init__(self, snapshot, *, work=None, audit=None, limiter=None, main_thread_timeout=MAIN_THREAD_TIMEOUT):
        self.snapshot = snapshot            # callable -> the host's last published, immutable view
        self.work = work or WorkQueue()
        self.audit = audit
        self.limiter = limiter or RateLimiter()
        self.main_thread_timeout = main_thread_timeout
        self.closing = False

    def describe(self):
        view = self.snapshot()
        return {"toolset_version": TOOLSET_VERSION, "instance": view.get("instance"),
                "blender_version": view.get("blender_version"), "addon_version": view.get("addon_version")}

    def _envelope(self, code, message, client=None, **fields):
        view = self.snapshot()
        conversations = view.get("conversations") or {}
        fields.setdefault("document", view.get("document"))
        fields.setdefault("conversation", conversations.get(client.label) if client else None)
        return protocol.envelope(code, message, toolset_version=TOOLSET_VERSION, **fields)

    def handle(self, request, client):
        from . import headless_window_app
        command, args = request.get("command"), request.get("args", {})
        if self.closing:
            return self._envelope("unavailable", "Local CLI access is turning off", client)
        if headless_window_app.foreign(client.label):   # A hidden session serves only the app that started it (S5).
            return self._envelope("unavailable", headless_window_app.FOREIGN, client)
        headless_window_app.touch()   # A hidden-window session's idle limit counts from the app's last call.
        if command == "agent" and isinstance(args, dict) and args.get("action") == "send":
            headless_window_app.arm()   # The app asked the agent for work: its edits count as changes from now.
        if not isinstance(args, dict):
            return self._envelope("invalid", "Command arguments must be a JSON object", client)
        if command == "tools":
            return self._tools(args, client)
        if command == "status":
            return self._main_thread(Work("status", client, {}), client)
        if command == "session":
            if client.app:   # An app starts a fresh conversation through Angelo's agent (new_chat), keeping its floor.
                return self._envelope("refused", "Use angelo_new_chat: an app's conversations start through Angelo's "
                                      "agent", client)
            if args.get("action") != "new":
                return self._envelope("invalid", "Use `angelo session new`", client)
            return self._main_thread(Work("session_new", client, {}), client)
        if command == "agent":
            return self._agent(args, client)
        if command == "call":   # No client runs a tool (2.0.1); the refusal is audited, as calls were.
            if self.audit is not None:
                tool = args.get("tool") if isinstance(args.get("tool"), str) and len(args["tool"]) <= 96 else None
                self.audit.record(label=client.label, peer_uid=client.peer.uid, peer_pid=client.peer.pid,
                                  command="call", tool=tool, call_id=None,
                                  arguments_sha256=_digest(args.get("arguments")), code="refused")
            return self._envelope("refused", NO_TOOL_CALLS, client)
        return self._envelope("invalid", "Unknown command; use status, session new or agent", client)

    def _tools(self, args, client):
        if args.get("presentation") == "mcp":
            if not client.app:
                return self._envelope("refused", "Connect this app in Angelo first", client)
            from .external_mcp_tools import app_tools
            return self._envelope("ok", "Tools for this app", client, result=app_tools())
        return self._envelope("refused", NO_TOOL_CALLS, client)

    def _main_thread(self, work, client):
        if not self.work.submit(work):
            return self._envelope("busy", "Angelo's CLI queue is full; retry shortly", client)
        if not work.wait_claimed(self.main_thread_timeout) and work.abandon():
            return self._envelope("busy", "Angelo's main thread did not respond in time; nothing was done", client)
        if not work.wait(self.main_thread_timeout):
            return self._envelope("busy", "Angelo's main thread did not finish in time", client)
        return self._finish(work, client)

    def _finish(self, work, client):
        response = dict(work.response)
        code, message = response.pop("code"), response.pop("message")
        result = response.pop("result", None)
        roots = tuple(response.pop("projection_roots", ())) + default_roots()
        if result is not None:
            result = project(result, roots)
        return self._envelope(code, message, client, result=result, **response)

    def _agent(self, args, client):
        """A connected app's command to Angelo's agent in its External conversation (``external_agent``): ``send``
        (message), ``read`` (after, wait, detail), ``status`` (task, after, wait), ``stop``, ``image`` (id), ``scene``,
        ``viewport``, ``settings`` or ``new_chat``."""
        if not client.app:
            return self._envelope("refused", "Only an app connected in Angelo (Preferences > AI Providers > External "
                                  "AI apps) can talk to Angelo's agent", client)
        action = args.get("action")
        if action not in AGENT_ACTIONS:
            return self._envelope("invalid", "Use agent send, read, status, stop, image, scene, viewport, settings, "
                                  "new_chat, look, window, state or usage", client)
        payload, wait = {"action": action}, args.get("wait", 0)
        if type(wait) not in {int, float} or not 0 <= wait <= MAX_AGENT_WAIT:
            return self._envelope("invalid", f"wait must be 0-{MAX_AGENT_WAIT:g} seconds", client)
        detail = args.get("detail", "brief")
        if detail not in DETAILS:
            return self._envelope("invalid", "detail is brief or full", client)
        conversation = args.get("conversation")
        if conversation is not None and _uuid_or_none(conversation) is None:
            return self._envelope("invalid", "conversation must be a conversation id Angelo returned", client)
        if action == "send":
            message = args.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
                return self._envelope("invalid", f"A message is 1-{MAX_MESSAGE} characters of text", client)
            payload["message"] = message
            goal = args.get("goal")
            if goal is not None and (not isinstance(goal, str) or not goal.strip() or len(goal) > MAX_GOAL):
                return self._envelope("invalid", f"goal is 1-{MAX_GOAL} characters of text", client)
            if goal is not None:
                payload["goal"] = goal
            attachments = args.get("attachments", [])
            if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS or not all(
                    isinstance(item, dict) and (set(item) == {"data", "mime"} and isinstance(item["data"], str)
                                                and item["mime"] in IMAGE_TYPES
                                                or set(item) == {"path"} and isinstance(item["path"], str)
                                                and 0 < len(item["path"]) <= 4096) for item in attachments):
                return self._envelope("invalid", f"attachments: at most {MAX_ATTACHMENTS}, each an image "
                                      "{data (base64), mime (png, jpeg or webp)} or a file {path} inside the open "
                                      "project's folder", client)
            if attachments:
                payload["attachments"] = attachments
        if action == "status":
            task = args.get("task")
            if task is not None and (not isinstance(task, str) or len(task) > 128):
                return self._envelope("invalid", "task must be a task id angelo_send returned", client)
            payload["task"] = task or ""
        if action in {"read", "status"}:
            after = args.get("after")
            if after is not None and (not isinstance(after, str) or len(after) > 128):
                return self._envelope("invalid", "after must be a cursor Angelo returned", client)
            payload["after"] = after
        if action in {"settings", "send"}:
            for key, limit in (("model", 200), ("connection", 200), ("effort", 32)):
                value = args.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > limit):
                    return self._envelope("invalid", f"{key} must be text of at most {limit} characters", client)
                if value is not None:
                    payload[key] = value
            approval = args.get("approval") if action == "settings" else None
            if approval is not None and approval not in APPROVALS:
                return self._envelope("invalid", "approval is ASK, READ_ONLY or AUTO_BOUNDED", client)
            if approval is not None:
                payload["approval"] = approval
        if action == "look":   # Checked here, before anything is queued (external_look_args).
            from . import external_look_args
            try:
                payload["look"] = external_look_args.validate(args.get("look", {}))
            except ValueError as error:
                return self._envelope("invalid", str(error), client)
        if action == "image":
            identity = args.get("id")
            if _uuid_or_none(identity) is None:
                return self._envelope("invalid", "id must be a picture id from the conversation's rows", client)
            payload["id"] = identity
        payload.update(wait=wait if action in {"send", "read", "status"} else 0, detail=detail,
                       conversation=_uuid_or_none(conversation))
        if action != "stop" and not self.limiter.allow(client.label):   # A Stop always goes through.
            return self._envelope("busy", "Too many calls from this app; retry shortly", client)
        work = Work("agent", client, payload)
        if not self.work.submit(work):
            return self._envelope("busy", "Angelo's queue is full; retry shortly", client)
        if not work.wait_claimed(self.main_thread_timeout) and work.abandon():
            return self._envelope("busy", "Angelo's main thread did not respond in time; nothing was done", client)
        if not work.wait(self.main_thread_timeout + payload["wait"]):
            if action == "send":   # Taken by Angelo, so it may have gone: never "busy", which an app would resend.
                return self._envelope("uncertain", "Angelo took the message but did not answer in time; read the "
                                      "conversation (angelo_status) before sending it again", client)
            return self._envelope("busy", "Angelo did not answer in time; read the conversation again", client)
        return self._finish(work, client)


NO_TOOL_CALLS = ("Angelo runs no tool for an outside program (2.0.1): an external agent works through Angelo's own "
                 "agent. Connect it in Angelo (Preferences > AI Providers > External AI apps); it then sends Angelo's "
                 "agent messages (angelo_send) and reads its progress (angelo_status, angelo_read)")
