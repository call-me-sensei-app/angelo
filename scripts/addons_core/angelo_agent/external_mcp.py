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
from .external_clients import EDIT_TOOLS, SCRIPT_FILE_TOOL, AppRegistry
from .external_mcp_tools import FILE_NAMES, MAX_TASK_WAIT, TASK_NAMES, app_tools
from .headless_files import path_digest
from .headless_session import HeadlessSession, HeadlessUnavailable, window_approval

SERVER_NAME = "angelo"
SERVER_TITLE = "Angelo (Hyperbond Studio)"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
ROUND_WAIT = 25.0        # Seconds one request to Angelo waits before the call is asked for again.
# Once Angelo has a call, its answer is "ok" or "stale" (or "pending" while it runs); a refusal, "unavailable" or
# "invalid" then answers the launcher's re-ask, never the call, which ran or is running and is never sent again.
REASK_ANSWERS = frozenset({"refused", "unavailable", "invalid"})
DETACHED = ("The connection to Angelo ended after this call was sent and before its answer: {reason} Whether it ran "
            "is unknown, and nothing was sent again. Observe the scene (and, without Angelo's window, the session's "
            "output file) before repeating it.")
IN_FLIGHT_UNANSWERED = ("Angelo already had this call when its answer could no longer be read: {reason} Whether it "
                        "finished is unknown, and nothing was run again. Observe the scene (and, without Angelo's "
                        "window, the session's output file) before repeating it.")
CALL_DEADLINE = 900.0    # Longest a tool call waits (a person deciding on an approval card) before it says so.
TASK_WAIT = 45.0         # angelo_task / angelo_task_status default wait (MAX_TASK_WAIT at most).
NOT_CONNECTED = ("This app is not connected to Angelo. In Angelo open Preferences > AI Providers > External AI apps "
                 "and connect it, then retry.")
NO_FILE = ("no_file: Angelo's window is not reachable ({reason}). To work on a .blend without it, call angelo_open_file "
           "with the file's absolute path (reads, views and bounded edits saved to a new file).")


def _version():
    try:
        return ".".join(map(str, __import__(__package__).bl_info["version"]))
    except Exception:  # noqa: BLE001 - the version is display data only.
        return "1"


class Server:
    def __init__(self, client, state_directory, stdout=None, headless=None, organization=None, machine=None):
        self.client, self.state = client, Path(state_directory)
        # The signed-in organization's app policy as the window last recorded it (external_clients.read_organization;
        # missing or unreadable: the strictest), read at every call; main() passes that reader. None: no organization.
        self.organization = organization or (lambda: None)
        # Whether the window recorded the organization policy at all: a refusal only for want of a record says so,
        # never "your organization" to someone who has none (break-it re-check FR2-L4). main() passes the reader.
        self.recorded = lambda: True
        # This computer's policy, read at every headless call too: main() passes managed_policy.reload so an admin's
        # change reaches a running session (break-it review FR-M3); tests keep the cached one they set.
        self.machine = machine
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._cancelled = set()
        self._lock = threading.Lock()
        # The file this session works on without Angelo's window, once opened (doc/angelo/external_mcp.rst).
        self.headless = headless or HeadlessSession(client, "other", state_directory)
        self.pending_file = None   # The launcher's --file: opened at the first tool call.

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

    def record(self):
        return AppRegistry(self.state).load().get(self.client)

    def levels(self):
        record = self.record()
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

    def access_refusal(self, organization):
        """``_access_refusal`` for a headless call, naming a missing organization record as what it is."""
        refusal = _access_refusal(state=self.state, organization=organization)
        if refusal and organization and not self.recorded() and _access_refusal(state=self.state) is None:
            return UNRECORDED
        return refusal

    def machine_policy(self):
        """``managed_policy.effective`` of this computer's policy, read again when ``machine`` says so; what
        ``_access_refusal`` and ``_headless_approval`` read next is the same policy."""
        from . import managed_policy
        return managed_policy.effective((self.machine or managed_policy.machine)())

    def headless_request(self, request, wait, in_flight=False, track=False):
        """A call on the opened file under the app's authority as the person and policy set it now: read at every
        call, inside the session's lock (``HeadlessSession.request``); ``in_flight`` for the next round of a call the
        child already has (never sent again)."""
        def authority():
            try:
                machine = self.machine_policy()
            except Exception:  # noqa: BLE001 - an unreadable policy never grants more.
                return protocol.envelope("refused", "Angelo could not read this computer's policy")
            record = self.record()
            if AppRegistry(self.state).key(self.client) is None or record is None:
                return protocol.envelope("unavailable", NOT_CONNECTED)
            organization = self.organization()
            refusal = self.access_refusal(organization)
            if refusal:
                return protocol.envelope("refused", refusal)
            return (record["levels"], _headless_approval(self.state, self.client, organization), organization, machine)
        return self.headless.request(request, wait + cli_client.RESPONSE_MARGIN, authority=authority,
                                     in_flight=in_flight, track=track)

    def headless_policy_refusal(self, name):
        """Break-it F3: policy decided here, where none of the person's code runs (and in the child, in its words)."""
        from . import managed_policy
        if name != SCRIPT_FILE_TOOL and name not in EDIT_TOOLS:
            return None
        try:
            self.machine_policy()   # This computer's policy as it is now: the first call after an admin's change too.
            organization = self.organization()
            policy = managed_policy.current()
            policy = managed_policy.with_organization(policy, organization) if organization else policy
            approval = _headless_approval(self.state, self.client, organization)
        except Exception:  # noqa: BLE001 - an unreadable policy never grants more.
            policy = approval = None
        script, auto = name == SCRIPT_FILE_TOOL, policy is not None and policy.allow_auto_approval
        ask = policy is None or policy.require_ask_for_edits or approval in {"ASK", "READ_ONLY"}
        if (script and not auto) or ask:
            from .headless_host import SCRIPT_FILE_REFUSALS, WAITS_FOR_PERSON   # Only to refuse: the child's words.
            return SCRIPT_FILE_REFUSALS["policy" if not auto else "mode"] if script else WAITS_FOR_PERSON
        return None

    def call(self, identity, params):
        name, arguments = params.get("name"), params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        if name in TASK_NAMES:
            return self.task(name, arguments, params)
        if name in FILE_NAMES:
            return self.file_tool(name, arguments)
        if self.pending_file and not self.headless.opened:
            opened, self.pending_file = self.file_tool("angelo_open_file", {"file": self.pending_file}), None
            if opened.get("isError"):
                return opened
        call_id = "mcp-" + uuid.uuid4().hex
        started, rounds = time.monotonic(), 0
        request = {"command": "call", "args": {"tool": name, "arguments": arguments, "id": call_id,
                                               "presentation": "mcp", "wait": ROUND_WAIT}}
        headless = self.headless.opened
        refusal = self.headless_policy_refusal(name) if headless else None
        if refusal:   # Decided here, where none of the person's code runs (break-it F3), and in the child.
            self.headless.audit(name, arguments, "needs_gui")
            return _coded_error("needs_gui", refusal)
        try:
            admitted = False   # Angelo has this call: a later round only asks about it again.
            note = None        # A restart's note, kept until the call's answer (a later round has none of its own).
            while True:
                if headless:
                    value, fresh = self.headless_request(request, ROUND_WAIT, in_flight=admitted, track=True)
                    note = fresh or note
                    self.headless.audit(name, arguments, value.get("code"))
                else:
                    value = self.request(request, ROUND_WAIT)
                if self._cancelled_request(identity):
                    return None
                reason = str(value.get("message") or value.get("code")).rstrip(".") + "."
                detached = (value.get("call") or {}).get("state") == "detached"
                if value.get("code") == "pending":
                    admitted = True
                elif not admitted and detached:
                    value = protocol.envelope("unavailable", DETACHED.format(reason=reason))
                elif admitted and value.get("code") in REASK_ANSWERS:
                    # The app's access, Angelo or the headless child changed or ended while the call ran: say so, and
                    # never present a refusal of the re-ask as the call's own answer (an app told "refused" would think
                    # nothing ran).
                    value = protocol.envelope(value["code"], IN_FLIGHT_UNANSWERED.format(reason=reason))
                elif admitted and value.get("code") == "busy":
                    # A round about a call Angelo already has was not picked up in time: its main thread is still
                    # running that call (a long script blocks it). The call itself was admitted and keeps running, so
                    # this is "still running", never "not admitted" (an app told that would run it a second time).
                    value = {"code": "pending"}
                if value.get("code") != "pending":
                    if (not headless and not admitted and value.get("code") == "unavailable" and not detached
                            and AppRegistry(self.state).key(self.client)):
                        return _coded_error("no_file", NO_FILE.format(reason=value.get("message") or "not running"))
                    return _with_note(_mcp_result(value), note)
                rounds += 1
                if time.monotonic() - started >= CALL_DEADLINE:
                    return _text_error("Still waiting in Angelo (for the person's approval or a long job) after %d "
                                       "minutes. It keeps going there; check the scene before repeating it."
                                       % round(CALL_DEADLINE / 60))
                self._progress(params, rounds, "Waiting in Angelo (approval or running work)")
        finally:
            if headless:   # Answered, cancelled or past its deadline: never running for this launcher again.
                self.headless.forget(call_id)

    def file_tool(self, name, arguments):
        """``angelo_open_file``, ``angelo_file_status``, ``angelo_close_file`` (doc/angelo/external_mcp.rst)."""
        if name == "angelo_close_file":
            was = self.headless.status()["file"]
            self.headless.close()
            return _file_result({"closed": was, "next": "Calls go to Angelo's window."})
        if name == "angelo_file_status":
            return _file_result(self.headless.status())
        record = self.record()
        if record is None or AppRegistry(self.state).key(self.client) is None:
            return _coded_error("unavailable", NOT_CONNECTED)
        try:
            machine = self.machine_policy()
        except Exception:  # noqa: BLE001 - an unreadable policy never grants more.
            return _coded_error("refused", "Angelo could not read this computer's policy")
        organization = self.organization()
        refusal = self.access_refusal(organization)
        if refusal:
            return _coded_error("refused", refusal)
        levels = list(record["levels"])
        path = arguments.get("file")
        gui = self.request({"command": "status"}, 5.0)
        if gui.get("code") == "ok" and path_digest(path) and (gui.get("result") or {}).get("file") == path_digest(path):
            self.headless.close()
            return _file_result({"file": path, "in_angelo_window": True, "next": (
                "Angelo has this file open, so your calls go to its window: the live document, unsaved changes "
                "included, under the person's approvals. Nothing was started.")})
        self.headless.app = record["app"]
        try:
            ready = self.headless.open(path, levels, approval=_headless_approval(self.state, self.client, organization),
                                       organization=organization, machine=machine)
        except HeadlessUnavailable as error:
            return _coded_error(error.code, str(error))
        self.headless.audit(name, arguments, "ok")
        summary = {}
        for tool, tool_arguments in (("angelo_blend_file", {}), ("angelo_inspect_scene", {})):
            value, _note = self.headless_request({"command": "call", "args": {
                "tool": tool, "arguments": tool_arguments, "id": "mcp-" + uuid.uuid4().hex, "presentation": "mcp",
                "wait": ROUND_WAIT}}, ROUND_WAIT)
            summary[tool] = _first_text(_mcp_result(value))
        return _file_result({"file": ready.get("file"), "levels": [level for level in levels if level in ("read", "edit")],
                             "edits": ("saved to a new file next to it, named in each edit's result; the file "
                                       "itself is never written") if "edit" in levels else "not allowed for this app",
                             "folder_writable": ready.get("writable"), "summary": summary})

    def task(self, name, arguments, params):
        if self.headless.opened:
            return _coded_error("needs_gui", "Angelo's agent runs tasks only in Angelo's window; call "
                                "angelo_close_file to use the window, or use Angelo's tools on the open file")
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


def _coded_error(code, text):
    """A launcher-level failure with its machine code (doc/angelo/external_mcp.rst, "Headless": codes)."""
    return {"content": [{"type": "text", "text": text}], "structuredContent": {"code": code}, "isError": True}


def _with_note(result, note):
    if not note or not isinstance(result, dict) or not isinstance(result.get("content"), list):
        return result
    return dict(result, content=[{"type": "text", "text": note}, *result["content"]])


UNRECORDED = ("Angelo hasn't recorded this computer's settings for apps yet (or the record can't be read). Open "
              "Angelo once and leave it open for a few seconds, then retry.")
HEADLESS_ACCESS_OFF = ("Access for external apps is off in Angelo. Turn on Preferences > AI Providers > External AI "
                       "apps > \"Allow apps on this computer to reach Angelo\" in Angelo's window, then retry")


def _headless_approval(state, client, organization):
    """The approval a headless child runs under: the person's choice for the app in the window, made Ask when the
    effective policy requires asking for edits (as a new External conversation in the window would be)."""
    from . import managed_policy
    approval = window_approval(state, client)
    policy = managed_policy.with_organization(managed_policy.machine(), organization) if organization \
        else managed_policy.machine()
    if policy.require_ask_for_edits and approval not in {"ASK", "READ_ONLY"}:
        return "ASK"
    return approval


def _access_refusal(environ=None, state=None, organization=None):
    """Why no app may use Angelo now: ``AllowLocalCLIAccess`` false or the ``ANGELO_DISABLE_LOCAL_CLI`` kill switch
    (headless sessions included), else None. With ``state`` (a headless call): also the person's own switch as the
    window recorded it (``external_clients.read_access``); never recorded counts as off."""
    from . import managed_policy, product_env
    value, variable = product_env.lookup("DISABLE_LOCAL_CLI", environ)
    if value == "1":
        return "Access for external apps is off on this computer (%s=1)" % variable
    if state is not None:
        from .external_clients import read_access
        if read_access(state) is not True:
            return HEADLESS_ACCESS_OFF
    try:
        policy = managed_policy.current()
        if organization:   # The organization's switch, as Angelo's window applies it (stricter wins).
            policy = managed_policy.with_organization(policy, organization)
        allowed = bool(policy.allow_local_cli_access)
    except Exception:  # noqa: BLE001 - an unreadable policy never grants more.
        allowed = False
    return None if allowed else "Your organization turned off access for external apps"


def _mcp_result(envelope):
    """An app call's MCP result (``external_work``), or the envelope's reason as an error result."""
    result = envelope.get("result")
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        return result
    text = str(envelope.get("message") or envelope.get("code") or "Angelo did not answer")
    if isinstance(result, dict) and result.get("needs_gui"):
        return _coded_error("needs_gui", text)
    if isinstance(result, dict) and result.get("code") == "output_unwritable":
        return _coded_error("output_unwritable", text)
    return _coded_error(str(envelope.get("code") or "unavailable"), text)


def _file_result(value):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": value, "isError": False}


def _first_text(result):
    for block in result.get("content") or ():
        if block.get("type") == "text":
            return block.get("text")
    return None


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
    parser.add_argument("--file", help="open this .blend without Angelo's window at the first call (angelo_open_file)")
    parser.add_argument("--angelo-binary", help="Angelo's executable for headless files (default: next to this Python)")
    options = parser.parse_args(argv)
    if not protocol.valid_label(options.client):
        parser.error("--client must be 1-32 characters from [A-Za-z0-9._-]")
    state = Path(options.state_dir) if options.state_dir else cli_client.default_state_directory(environ)
    # MCP owns stdout: anything stray must go to stderr, never into the JSON-RPC stream.
    stdout, sys.stdout = sys.stdout, sys.stderr
    from . import managed_policy
    from .external_clients import organization_recorded, read_organization
    # The organization's app policy as Angelo's window last recorded it beside the app's key; a missing or
    # unreadable record is the strictest policy, never none (external_clients.read_organization).
    server = Server(options.client, state, stdout=stdout, headless=HeadlessSession(
        options.client, "other", state, binary=Path(options.angelo_binary) if options.angelo_binary else None),
        organization=lambda: read_organization(state), machine=managed_policy.reload)
    server.recorded = lambda: organization_recorded(state)
    server.pending_file = options.file
    try:
        server.serve(sys.stdin)
    finally:
        server.headless.close()   # The child and its private state end with the app's session.
    return 0

