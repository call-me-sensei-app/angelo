# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's MCP server for external AI apps: a stdio launcher over the local CLI socket.

An MCP client (Claude Code, Codex, Claude Desktop, Cursor, VS Code or any other) starts this with Angelo's bundled
Python in isolated mode::

    <Angelo Python> -I <Angelo scripts>/angelo/angelo_mcp.py --client claude-code

It speaks MCP's stdio transport (newline-delimited JSON-RPC 2.0 on stdin and stdout; nothing else is ever written to
stdout) and forwards each tool call to the running Angelo as an ``agent`` command over the private socket
(``cli_client``), proving both the launch token and the app's own key (``external_clients``). There is no TCP, UDP
or HTTP listener. The app works through Angelo's agent and never calls a tool: Angelo's MCP is an interface to
Angelo's own agent, which the app starts and supervises like a sub-session (doc/angelo/external_mcp.rst).

* ``tools/list``: the agent tools (``external_mcp_tools``), computed here, so a client that starts before Angelo still
  sees them; the tools themselves are ``external_mcp_agent.AgentTools`` (the long poll included).
* ``notifications/cancelled`` stops waiting and sends nothing more, as MCP requires; work already in Angelo goes on
  (only the person's Stop or ``angelo_stop`` stops it).

Pure standard library and no ``bpy``."""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from . import cli_client
from . import external_cli_protocol as protocol
from .external_clients import AppRegistry
from .external_mcp_agent import AgentTools
from .external_mcp_hidden import HIDDEN_NAMES, HiddenTools
from .external_mcp_tools import AGENT_NAMES, app_tools

SERVER_NAME = "angelo"
SERVER_TITLE = "Angelo (Hyperbond Studio)"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_CANCELLED = 256   # Cancellations held for calls still open (each is dropped when its call ends).
NOT_CONNECTED = ("This app is not connected to Angelo. In Angelo open Preferences > AI Providers > External AI apps "
                 "and connect it, then retry.")


def _version():
    try:
        return ".".join(map(str, __import__(__package__).bl_info["version"]))
    except Exception:  # noqa: BLE001 - the version is display data only.
        return "1"


class Server(AgentTools, HiddenTools):
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
            with self._lock:   # Bounded: a cancellation for no open call is dropped when its call ends or ages out.
                if len(self._cancelled) < MAX_CANCELLED:
                    self._cancelled.add(json.dumps(params.get("requestId")))
            return
        if identity is None:
            return   # notifications/initialized and the like.
        if method == "tools/call":
            try:
                threading.Thread(target=self._call, args=(identity, params), daemon=True, name="AngeloMcpCall").start()
            except RuntimeError as error:   # No thread to run it: answered as not run; the bridge keeps serving.
                self.send({"jsonrpc": "2.0", "id": identity,
                           "result": _text_error("Angelo's MCP launcher could not run this call: %s" % error)})
            return
        try:
            result = self.answer(method, params)
        except LookupError:
            return self.send({"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}})
        self.send({"jsonrpc": "2.0", "id": identity, "result": result})

    # Answers ------------------------------------------------------------------------------------

    def connected(self):
        return AppRegistry(self.state).load().get(self.client) is not None

    def answer(self, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            text = app_tools()["instructions"] if self.connected() else NOT_CONNECTED
            mismatch = cli_client.endpoint_mismatch(self.state) if self.connected() else ""
            if mismatch:   # Another Angelo build holds access: every call says so too (cli_client.connect).
                text = mismatch + "\n\n" + text
            return {"protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": _version()},
                    "instructions": text}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": app_tools()["tools"] if self.connected() else []}
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
        with self._lock:   # Its call ended: the cancellation is spent, so the set never grows past the open calls.
            self._cancelled.discard(json.dumps(identity))

    def request(self, request, wait=0.0):
        """One request to Angelo over the private socket, proving this app's key; the envelope it answers."""
        key = AppRegistry(self.state).key(self.client)
        if key is None:
            return protocol.envelope("unavailable", NOT_CONNECTED)
        return cli_client.run(request, self.state, self.client, wait + cli_client.RESPONSE_MARGIN, key=key)

    def call(self, identity, params):
        name, arguments = params.get("name"), params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        if name not in AGENT_NAMES:
            return _text_error("Unknown tool %r: Angelo offers %s" % (name, ", ".join(sorted(AGENT_NAMES))))
        if name in HIDDEN_NAMES:   # Angelo without its window (doc/angelo/mcp_headless_window.rst).
            result = self.hidden_tool(name, arguments)
            if result is not None:
                return result
            name, arguments = "angelo_read", {"wait_seconds": 3}   # A decline is a row there: read it.
        return self.agent(identity, name, arguments, params)


def _text_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


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
    server = Server(options.client, state, stdout=stdout)
    try:
        server.serve(sys.stdin)
    finally:
        server.end_sessions()   # A hidden-window Angelo ends with the app's session (it saves first).
    return 0
