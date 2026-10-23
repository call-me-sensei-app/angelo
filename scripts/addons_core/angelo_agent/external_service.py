# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-connection CLI command handling on the service thread (never ``bpy``).

``RequestService.handle`` turns one authenticated request into one envelope.
Discovery (``tools``) is answered here. ``status``, ``call`` and
``session new`` become ``Work`` for the host's main-thread drain and are
awaited with bounded timeouts; a timed-out call reports ``pending`` (or
``busy`` if the host never claimed it), never an invented result.
"""

import time
import uuid

from . import external_cli_protocol as protocol
from .external_requests import (DEFERRED_COMMANDS, MAIN_THREAD_TIMEOUT, PHASE1_READ_TOOLS, RateLimiter, Work,
                                WorkQueue, _digest, default_roots, exclusion_reason, project, tools_catalog)
from .external_clients import PYTHON, SCRIPT_FILE_TOOL, TASKS, tools_for
from .routing import TOOLSET_VERSION
from .tool_specs import TOOL_MAP, validate_call

# An app's task: the request's text bound, and how long one status call may wait (the launcher's own deadlines
# keep every MCP client under its tool timeout).
MAX_TASK_TEXT = 32000
# Why a headless session refuses a tool the app may use in Angelo's window (external_mcp.rst, "Headless").
NEEDS_GUI = ("Needs Angelo's window: without it only reads, views and bounded typed edits run (saved to a new file). "
             "Ask the person to open the file in Angelo, or use those tools.")
MAX_TASK_WAIT = 50.0


def script_file_allowed(client, mcp):
    """``angelo_run_script_file``: only a connected app (MCP) the person allowed to run Python scripts."""
    return bool(mcp and client.levels is not None and PYTHON in client.levels)


def allowed_tools(client, mcp=False):
    """The tools this call may use: a connected app's levels on the MCP path, else the phase-1 read-only allowlist."""
    if mcp and client.levels is not None:
        return tools_for(client.levels, headless=getattr(client, "headless", False))
    return PHASE1_READ_TOOLS


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
        started = time.monotonic()
        command, args = request.get("command"), request.get("args", {})
        if self.closing:
            return self._envelope("unavailable", "Local CLI access is turning off", client)
        if not isinstance(args, dict):
            return self._envelope("invalid", "Command arguments must be a JSON object", client)
        if command == "tools":
            return self._tools(args, client)
        if command == "status":
            return self._main_thread(Work("status", client, {"call": args.get("call")}), client,
                                     invalid=args.get("call") is not None and not protocol.valid_call_id(args.get("call")))
        if command == "session":
            if args.get("action") != "new":
                return self._envelope("invalid", "Use `angelo session new`", client)
            return self._main_thread(Work("session_new", client, {}), client)
        if command == "call":
            response = self._call(args, client, started)
            if self.audit is not None:
                self.audit.record(label=client.label, peer_uid=client.peer.uid, peer_pid=client.peer.pid,
                                  command="call", tool=(response.get("call") or {}).get("tool"),
                                  call_id=(response.get("call") or {}).get("id"),
                                  arguments_sha256=_digest(args.get("arguments")), code=response["code"])
            return response
        if command == "task":
            if getattr(client, "headless", False):
                return self._envelope("refused", "needs_gui: Angelo's agent runs tasks only in Angelo's window", client)
            return self._task(args, client)
        if command in DEFERRED_COMMANDS:
            return self._envelope("incompatible", DEFERRED_COMMANDS[command], client)
        return self._envelope("invalid", "Unknown command; use status, tools, call or session new", client)

    def _tools(self, args, client):
        if args.get("presentation") == "mcp":
            if client.levels is None:
                return self._envelope("refused", "Connect this app in Angelo first", client)
            from .external_mcp_tools import app_tools
            return self._envelope("ok", "Tools for this app", client, result=app_tools(client.levels))
        name, include_schema = args.get("tool"), args.get("schema", False)
        if name is not None and (not isinstance(name, str) or name not in TOOL_MAP):
            return self._envelope("invalid", "Unknown Blender tool", client)
        if not isinstance(include_schema, bool):
            return self._envelope("invalid", "schema must be true or false", client)
        return self._envelope("ok", "Read-only phase-1 tools", client, result=tools_catalog(name, include_schema))

    def _main_thread(self, work, client, invalid=False):
        if invalid:
            return self._envelope("invalid", "Use a call ID of 1-128 characters from [A-Za-z0-9._:-]", client)
        if not self.work.submit(work):
            return self._envelope("busy", "Angelo's CLI queue is full; retry shortly", client)
        if not work.wait_claimed(self.main_thread_timeout) and work.abandon():
            return self._envelope("busy", "Angelo's main thread did not respond in time; nothing was done", client)
        if not work.wait(self.main_thread_timeout):
            return self._envelope("busy", "Angelo's main thread did not finish in time", client)
        return self._finish(work, client)

    def _finish(self, work, client, call=None, started=None):
        response = dict(work.response)
        code, message = response.pop("code"), response.pop("message")
        result = response.pop("result", None)
        roots = tuple(response.pop("projection_roots", ())) + default_roots()
        if result is not None:
            result = project(result, roots)
        if started is not None:
            timings = response.setdefault("timings", {})
            timings["total_ms"] = round((time.monotonic() - started) * 1000, 3)
            if work.claimed_at is not None:
                timings["queue_ms"] = round((work.claimed_at - work.enqueued) * 1000, 3)
            if work.completed_at is not None and work.claimed_at is not None:
                timings["host_ms"] = round((work.completed_at - work.claimed_at) * 1000, 3)
        if call is not None:
            response["call"] = dict(call, **(response.get("call") or {}))
        return self._envelope(code, message, client, result=result, **response)

    def _call(self, args, client, started):
        tool, identity = args.get("tool"), args.get("id")
        call = {"id": identity if protocol.valid_call_id(identity) else None,
                "tool": tool if isinstance(tool, str) and len(tool) <= 96 else None, "state": "rejected"}
        mcp = args.get("presentation") == "mcp"
        script_file = tool == SCRIPT_FILE_TOOL
        if not isinstance(tool, str) or (tool not in TOOL_MAP and not script_file):
            return self._envelope("invalid", "Unknown Blender tool; nothing was admitted", client, call=call)
        if mcp and client.levels is None:
            return self._envelope("refused", "Only an app connected in Angelo (Preferences > AI Providers > External "
                                  "AI apps) can use this", client, call=dict(call, state="refused"))
        if (not script_file_allowed(client, mcp)) if script_file else tool not in allowed_tools(client, mcp):
            # Refused before any controller, queue or journal sees the request.
            reason = exclusion_reason(tool) if client.levels is None else app_exclusion(tool)
            extra = {}
            if getattr(client, "headless", False) and tool in tools_for(client.levels):
                reason, extra = NEEDS_GUI, {"needs_gui": True}
            return self._envelope("refused", f"{tool} is not available to external clients: {reason}", client,
                                  call=dict(call, state="refused"), result={"excluded": True, "reason": reason, **extra})
        if call["id"] is None:
            return self._envelope("invalid", "Use a call ID of 1-128 characters from [A-Za-z0-9._:-]", client, call=call)
        arguments = args.get("arguments")
        if not isinstance(arguments, dict):
            return self._envelope("invalid", "Tool arguments must be a JSON object", client, call=call)
        if not mcp and not script_file:
            # An app's call is validated by the direct-tools host code, which returns the exact schemas it needs.
            try:
                arguments = validate_call(tool, arguments)
            except ValueError as error:
                return self._envelope("invalid", f"Invalid {tool} arguments: {error}; nothing was admitted", client,
                                      call=call)
        conversation, expected = args.get("conversation"), args.get("expect_document")
        if conversation is not None and _uuid_or_none(conversation) is None:
            return self._envelope("invalid", "--conversation must be a conversation UUID", client, call=call)
        if expected is not None and (not isinstance(expected, str) or protocol.HEX_DIGEST.fullmatch(expected) is None):
            return self._envelope("invalid", "--expect-document must be a document SHA-256 from status", client, call=call)
        toolset, wait = args.get("toolset"), args.get("wait", protocol.DEFAULT_WAIT)
        if toolset is not None and (type(toolset) is not int or toolset < 1):
            return self._envelope("invalid", "--toolset must be a positive integer", client, call=call)
        if type(wait) not in {int, float} or not 0 <= wait <= protocol.MAX_WAIT:
            return self._envelope("invalid", f"--wait must be 0-{protocol.MAX_WAIT:g} seconds", client, call=call)
        extra = {"toolset_mismatch": True} if toolset is not None and toolset != TOOLSET_VERSION else {}
        if not self.limiter.allow(client.label):
            return self._envelope("busy", "Too many calls from this client label; retry shortly", client,
                                  call=dict(call, state="not_admitted"), **extra)
        work = Work("app_call" if mcp else "call", client, {"tool": tool, "arguments": arguments, "id": identity,
                                     "conversation": _uuid_or_none(conversation), "expect_document": expected})
        if not self.work.submit(work):
            return self._envelope("busy", "Angelo's CLI queue is full; retry with the same ID", client,
                                  call=dict(call, state="not_admitted"), **extra)
        # Pickup is bounded separately from --wait: --wait 0 still admits the
        # call and reports pending, while unclaimed work is withdrawn unrun.
        if not work.wait_claimed(self.main_thread_timeout) and work.abandon():
            return self._envelope("busy", "Angelo did not pick up the call in time; it was not admitted. "
                                  "Retry with the same ID", client, call=dict(call, state="not_admitted"), **extra)
        if not work.wait(max(0.0, started + wait - time.monotonic())) and not work.wait(0.05):
            state = "admitted" if work.admitted else "claimed"
            return self._envelope("pending", f"Still running in Angelo; repeat the identical command (same --id) "
                                  f"to replay its receipt, or run `angelo status --call {identity}`", client,
                                  call=dict(call, state=state), **extra)
        response = self._finish(work, client, call=dict(call, state="finished"), started=started)
        response.update(extra)
        return response

    def _task(self, args, client):
        """An app's task: ``start`` (text), ``status`` (task, wait) or ``stop`` (task), answered by the host."""
        action = args.get("action")
        if client.levels is None or TASKS not in client.levels:
            return self._envelope("refused", "The person has not allowed this app to run Angelo tasks (Preferences > "
                                  "AI Providers > External AI apps)", client)
        payload = {"action": action}
        if action == "start":
            text = args.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TASK_TEXT:
                return self._envelope("invalid", f"A task is 1-{MAX_TASK_TEXT} characters of text", client)
            payload["text"] = text
        elif action in {"status", "stop"}:
            task, wait = args.get("task"), args.get("wait", 0)
            if _uuid_or_none(task) is None:
                return self._envelope("invalid", "task must be the task_id angelo_task returned", client)
            if type(wait) not in {int, float} or not 0 <= wait <= MAX_TASK_WAIT:
                return self._envelope("invalid", f"wait must be 0-{MAX_TASK_WAIT:g} seconds", client)
            payload.update(task=task, wait=wait if action == "status" else 0)
        else:
            return self._envelope("invalid", "Use task start, status or stop", client)
        if not self.limiter.allow(client.label):
            return self._envelope("busy", "Too many calls from this app; retry shortly", client)
        work = Work("task", client, payload)
        if not self.work.submit(work):
            return self._envelope("busy", "Angelo's queue is full; retry shortly", client)
        if not work.wait_claimed(self.main_thread_timeout) and work.abandon():
            return self._envelope("busy", "Angelo's main thread did not respond in time; nothing was done", client)
        if not work.wait(self.main_thread_timeout + payload.get("wait", 0)):
            return self._envelope("busy", "Angelo did not answer in time; ask for the task's status again", client)
        return self._finish(work, client)


def app_exclusion(name):
    """Why a connected app may not call ``name``: a level the person did not allow, or never available to apps."""
    from .external_clients import EDIT_TOOLS, PYTHON_TOOLS, READ_TOOLS
    if name in EDIT_TOOLS:
        return "The person has not allowed this app to edit the scene."
    if name in PYTHON_TOOLS or name == "angelo_run_script_file":
        return "The person has not allowed this app to run Python scripts."
    if name in READ_TOOLS:
        return "Not available to this app."
    return exclusion_reason(name)
