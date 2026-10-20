# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's MCP server for external AI apps: a stdio launcher over the local CLI socket.

An MCP client (Claude Code, Codex, Claude Desktop, Cursor, VS Code or any
other) starts this with Angelo's bundled Python in isolated mode::

    <Angelo Python> -I <Angelo scripts>/angelo/angelo_mcp.py --client claude-code

It speaks MCP's stdio transport (newline-delimited JSON-RPC 2.0 on stdin and
stdout; nothing else is ever written to stdout) and forwards each tool call to
the running Angelo over the existing private socket (``cli_client``), proving
both the launch token and the app's own key (``external_clients``). There is no
TCP, UDP or HTTP listener, so it can never take a port Blender MCP or anything
else uses. Every effect happens inside Angelo, under the person's approvals.

* ``tools/list``: computed here from the app's allowed levels, so a client that
  starts before Angelo still sees its tools (``external_mcp_tools``); Angelo
  checks the same levels again on every call.
* ``tools/call``: an Angelo tool goes to Angelo as an app call (``external_work``);
  a task tool becomes a ``task`` command (``external_apps``). A call that waits
  (for the person's approval, a render) is asked for again under the same call
  ID, which never runs it twice, sending ``notifications/progress`` meanwhile
  when the client gave a progress token, until ``CALL_DEADLINE``.
* ``notifications/cancelled`` stops waiting and sends nothing more, as MCP
  requires; work already in Angelo is not cancelled (only the person's Stop
  or ``angelo_task_stop`` stops it).

Pure standard library and no ``bpy``; doc/angelo/external_mcp.rst.
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from . import cli_client
from . import external_cli_protocol as protocol
from .external_clients import AppRegistry
from .external_mcp_tools import MAX_TASK_WAIT, TASK_NAMES, app_tools

SERVER_NAME = "angelo"
SERVER_TITLE = "Angelo (Hyperbond Studio)"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
ROUND_WAIT = 25.0        # Seconds one request to Angelo waits before the call is asked for again.
CALL_DEADLINE = 900.0    # Longest a tool call waits (a person deciding on an approval card) before it says so.
TASK_WAIT = 45.0         # angelo_task / angelo_task_status default wait (MAX_TASK_WAIT at most).
NOT_CONNECTED = ("This app is not connected to Angelo. In Angelo open Preferences > AI Providers > External AI apps "
                 "and connect it, then retry.")


def _version():
    try:
        return ".".join(map(str, __import__(__package__).bl_info["version"]))
    except Exception:  # noqa: BLE001 - the version is display data only.
        return "1"


class Server:
    def __init__(self, client, state_directory, stdout=None):
        self.client, self.state = client, Path(state_directory)
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._cancelled = set()
        self._lock = threading.Lock()

    # Transport ------------------------------------------------------------------------------------

    def send(self, message):
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def serve(self, stdin=None):
        stdin = stdin or sys.stdin
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            for item in message if isinstance(message, list) else [message]:
                self.dispatch(item)

    def dispatch(self, message):
        if not isinstance(message, dict):
            return
        method, identity = message.get("method"), message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method is None:
            return   # A response to nothing we sent.
        if method == "notifications/cancelled":
            with self._lock:
                self._cancelled.add(json.dumps(params.get("requestId")))
            return
        if identity is None:
            return   # notifications/initialized and the like.
        if method == "tools/call":
            threading.Thread(target=self._call, args=(identity, params), daemon=True, name="AngeloMcpCall").start()
            return
        try:
            result = self.answer(method, params)
        except LookupError:
            return self.send({"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}})
        self.send({"jsonrpc": "2.0", "id": identity, "result": result})

    # Answers ------------------------------------------------------------------------------------

    def levels(self):
        record = AppRegistry(self.state).load().get(self.client)
        return frozenset(record["levels"]) if record else None

    def answer(self, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            levels = self.levels()
            text = app_tools(levels)["instructions"] if levels else NOT_CONNECTED
            return {"protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": _version()},
                    "instructions": text}
        if method == "ping":
            return {}
        if method == "tools/list":
            levels = self.levels()
            return {"tools": app_tools(levels)["tools"] if levels else []}
        if method in {"resources/list", "prompts/list", "resources/templates/list"}:
            return {method.split("/")[0] if "templates" not in method else "resourceTemplates": []}
        if method == "logging/setLevel":
            return {}
        raise LookupError(method)

    def _cancelled_request(self, identity):
        with self._lock:
            return json.dumps(identity) in self._cancelled

    def _progress(self, params, count, message):
        token = (params.get("_meta") or {}).get("progressToken") if isinstance(params.get("_meta"), dict) else None
        if token is not None:
            self.send({"jsonrpc": "2.0", "method": "notifications/progress",
                       "params": {"progressToken": token, "progress": count, "message": message}})

    def _call(self, identity, params):
        try:
            result = self.call(identity, params)
        except Exception as error:  # noqa: BLE001 - every call is answered; the error is the result.
            result = _text_error("Angelo's MCP launcher failed: %s: %s" % (type(error).__name__, error))
        if result is not None and not self._cancelled_request(identity):
            self.send({"jsonrpc": "2.0", "id": identity, "result": result})

    def request(self, request, wait):
        key = AppRegistry(self.state).key(self.client)
        if key is None:
            return protocol.envelope("unavailable", NOT_CONNECTED)
        return cli_client.run(request, self.state, self.client, wait + cli_client.RESPONSE_MARGIN, key=key)

    def call(self, identity, params):
        name, arguments = params.get("name"), params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        if name in TASK_NAMES:
            return self.task(name, arguments, params)
        call_id = "mcp-" + uuid.uuid4().hex
        started, rounds = time.monotonic(), 0
        request = {"command": "call", "args": {"tool": name, "arguments": arguments, "id": call_id,
                                               "presentation": "mcp", "wait": ROUND_WAIT}}
        while True:
            value = self.request(request, ROUND_WAIT)
            if self._cancelled_request(identity):
                return None
            if value.get("code") != "pending":
                return _mcp_result(value)
            rounds += 1
            if time.monotonic() - started >= CALL_DEADLINE:
                return _text_error("Still waiting in Angelo (for the person's approval or a long job) after %d "
                                   "minutes. It keeps going there; check the scene before repeating it."
                                   % round(CALL_DEADLINE / 60))
            self._progress(params, rounds, "Waiting in Angelo (approval or running work)")

    def task(self, name, arguments, params):
        wait = arguments.get("wait_seconds", TASK_WAIT)
        wait = min(MAX_TASK_WAIT, max(0.0, float(wait))) if isinstance(wait, (int, float)) and not isinstance(wait, bool) else TASK_WAIT
        if name == "angelo_task":
            value = self.request({"command": "task", "args": {"action": "start", "text": arguments.get("task")}}, 0)
            result = value.get("result") if isinstance(value.get("result"), dict) else None
            if result is None or result.get("state") not in {"running", "waiting_for_approval_in_angelo"} or not wait:
                return _task_result(value)
            self._progress(params, 1, result["state"])
            return self._task_wait(result["task_id"], wait, params)
        task = arguments.get("task_id")
        if name == "angelo_task_stop":
            return _task_result(self.request({"command": "task", "args": {"action": "stop", "task": task}}, 0))
        return self._task_wait(task, wait, params)

    def _task_wait(self, task, wait, params):
        deadline, count = time.monotonic() + wait, 1
        while True:
            step = max(0.0, min(5.0, deadline - time.monotonic()))
            value = self.request({"command": "task", "args": {"action": "status", "task": task, "wait": step}}, step)
            result = value.get("result") if isinstance(value.get("result"), dict) else {}
            if result.get("state") not in {"running", "waiting_for_approval_in_angelo"} or time.monotonic() >= deadline:
                return _task_result(value)
            count += 1
            self._progress(params, count, result["state"])


def _text_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _mcp_result(envelope):
    """An app call's MCP result (``external_work``), or the envelope's reason as an error result."""
    result = envelope.get("result")
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        return result
    return _text_error(str(envelope.get("message") or envelope.get("code") or "Angelo did not answer"))


def _task_result(envelope):
    result = envelope.get("result")
    if not isinstance(result, dict):
        return _text_error(str(envelope.get("message") or envelope.get("code") or "Angelo did not answer"))
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": result,
            "isError": result.get("state") in {"failed"} or envelope.get("code") not in {"ok", "not_applied"}}


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="angelo-mcp", description="Angelo's MCP server (stdio) for external AI apps.")
    parser.add_argument("--client", required=True, help="this app's name as connected in Angelo (e.g. claude-code)")
    parser.add_argument("--state-dir", help="Angelo agent state directory (default: as the angelo CLI finds it)")
    options = parser.parse_args(argv)
    if not protocol.valid_label(options.client):
        parser.error("--client must be 1-32 characters from [A-Za-z0-9._-]")
    state = Path(options.state_dir) if options.state_dir else cli_client.default_state_directory(environ)
    # MCP owns stdout: anything stray must go to stderr, never into the JSON-RPC stream.
    stdout, sys.stdout = sys.stdout, sys.stderr
    Server(options.client, state, stdout=stdout).serve(sys.stdin)
    return 0

