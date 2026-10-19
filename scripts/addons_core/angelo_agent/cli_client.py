# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure-stdlib ``angelo`` client for coding agents: one command, one JSON envelope.

Run by Angelo's bundled Python in isolated mode through the launcher::

    <Angelo Python> -I scripts/angelo/angelo_cli.py status

It writes exactly one JSON envelope to stdout (``--format text`` is for
people), a short note to stderr when the result is not ``ok``, and exits with
the envelope's ``exit`` code. The per-launch token is read from the
owner-only endpoint record; it never appears in argv, the environment or any
output. Nothing here imports ``bpy`` or any other Angelo host module.
"""

import argparse
import json
import os
import secrets
import signal
import socket
import sys
import uuid
from pathlib import Path

from . import external_cli_protocol as protocol
from . import product_env
from .state_paths import STATE_FOLDER, state_override

# The per-version user folder name of this build (the Blender series Angelo is based on).
BLENDER_SERIES = "5.2"
# The user folder names are the application identity's (scripts/modules/angelo_identity.json), never
# Blender's: this client must find the running Angelo's state, not a stock Blender's.
_IDENTITY_FILE = Path(__file__).resolve().parents[2] / "modules" / "angelo_identity.json"
CONNECT_TIMEOUT = 5.0
RESPONSE_MARGIN = 15.0


class UsageError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def default_state_directory(environ=None, platform=None, home=None):
    """Mirror the add-on's ``state_directory()`` without Blender."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    override, _variable = state_override(environ)
    if override is not None:
        return override
    if environ.get("BLENDER_USER_CONFIG"):
        return Path(environ["BLENDER_USER_CONFIG"]) / STATE_FOLDER
    if environ.get("BLENDER_USER_RESOURCES"):
        return Path(environ["BLENDER_USER_RESOURCES"]) / "config" / STATE_FOLDER
    names = json.loads(_IDENTITY_FILE.read_text(encoding="utf-8"))["paths"]
    if platform == "darwin":
        return home / "Library/Application Support" / names["user_dir_name"] / BLENDER_SERIES / "config" / STATE_FOLDER
    if platform == "win32":
        base = Path(environ.get("APPDATA") or home / "AppData/Roaming")
        return base / names["user_dir_name"] / BLENDER_SERIES / "config" / STATE_FOLDER
    base = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / names["user_dir_name_unix"] / BLENDER_SERIES / "config" / STATE_FOLDER


def parser():
    root = _Parser(prog="angelo", description="Observe the open Angelo document from a coding agent (read-only phase 1).")
    root.add_argument("--client", help="client label (1-32 of [A-Za-z0-9._-]); default $ANGELO_CLIENT "
                      "(or a deprecated spelling) or 'external'")
    root.add_argument("--format", choices=("json", "text"), default="json")
    root.add_argument("--state-dir", help="Angelo agent state directory (default: $ANGELO_STATE_DIR or the user config)")
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="instance, document, conversation and one call's receipt")
    status.add_argument("--call", help="report one call ID")
    tools = commands.add_parser("tools", help="allowed read-only tools and excluded tools with reasons")
    tools.add_argument("--tool")
    tools.add_argument("--schema", action="store_true", help="include each inputSchema")
    call = commands.add_parser("call", help="run one allowed read-only tool")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", help="JSON object, or - for stdin (default {})")
    call.add_argument("--args-file")
    call.add_argument("--id", help="call ID (1-128 of [A-Za-z0-9._:-]); generated when omitted")
    call.add_argument("--conversation", help="refuse unless this is still the label's conversation")
    call.add_argument("--expect-document", help="refuse unless this document SHA-256 is still open")
    call.add_argument("--toolset", type=int, help="expected toolset version")
    call.add_argument("--wait", type=float, default=protocol.DEFAULT_WAIT, help="seconds to wait (default 90)")
    session = commands.add_parser("session", help="session new: rotate to a fresh External conversation")
    session.add_argument("action", choices=("new",))
    for name in ("watch", "stop", "observe", "view"):
        commands.add_parser(name, help="not available in phase 1")
    return root


def _read_arguments(options):
    if options.args_file and options.arguments is not None:
        raise UsageError("Give the arguments inline, with -, or with --args-file; not several")
    if options.args_file:
        with open(options.args_file, "rb") as handle:
            data = handle.read(protocol.MAX_REQUEST_BYTES + 1)
    elif options.arguments == "-":
        data = sys.stdin.buffer.read(protocol.MAX_REQUEST_BYTES + 1)
    else:
        data = (options.arguments or "{}").encode("utf-8")
    if len(data) > protocol.MAX_REQUEST_BYTES:
        raise UsageError("Tool arguments exceed the 1 MiB request limit")
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=protocol._reject_constant)
    except (UnicodeError, ValueError) as error:
        raise UsageError(f"Tool arguments are not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise UsageError("Tool arguments must be a JSON object")
    return value


def build_request(options):
    """Translate parsed options to the wire request. Raises UsageError."""
    if options.command == "status":
        if options.call is not None and not protocol.valid_call_id(options.call):
            raise UsageError("Use a call ID of 1-128 characters from [A-Za-z0-9._:-]")
        return {"command": "status", "args": {"call": options.call}}
    if options.command == "tools":
        return {"command": "tools", "args": {"tool": options.tool, "schema": options.schema}}
    if options.command == "session":
        return {"command": "session", "args": {"action": options.action}}
    if options.command == "call":
        identity = options.id or str(uuid.uuid4())
        if not protocol.valid_call_id(identity):
            raise UsageError("Use a call ID of 1-128 characters from [A-Za-z0-9._:-]")
        if not 0 <= options.wait <= protocol.MAX_WAIT:
            raise UsageError(f"--wait must be 0-{protocol.MAX_WAIT:g} seconds")
        return {"command": "call", "args": {"tool": options.tool, "arguments": _read_arguments(options),
                                            "id": identity, "conversation": options.conversation,
                                            "expect_document": options.expect_document,
                                            "toolset": options.toolset, "wait": options.wait}}
    return {"command": options.command, "args": {}}


def _unavailable(message, call=None):
    return protocol.envelope("unavailable", message, call=call)


def connect(state_directory, label):
    """Authenticate to the instance named by the endpoint record; returns (socket, None) or (None, envelope)."""
    endpoint = Path(state_directory) / "cli" / protocol.ENDPOINT_FILE
    try:
        protocol.check_private_directory(endpoint.parent)
        record = protocol.read_endpoint(endpoint)
    except FileNotFoundError:
        return None, _unavailable("Local CLI access is off or Angelo is not running (no endpoint record). Enable "
                                  "Preferences > AI Providers > Allow local CLI access")
    except protocol.UnsafePath as error:
        return None, _unavailable(f"Refusing an unsafe endpoint: {error}")
    if record["cli_protocol"] != protocol.CLI_PROTOCOL:
        return None, protocol.envelope("incompatible", f"Angelo speaks CLI protocol {record['cli_protocol']}; "
                                       f"this client speaks {protocol.CLI_PROTOCOL}")
    path = Path(record["socket"])
    try:
        protocol.check_private_directory(path.parent)
    except (protocol.UnsafePath, FileNotFoundError) as error:
        return None, _unavailable(f"Refusing an unsafe or missing socket directory: {error}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.connect(str(path))
        challenge = protocol.recv_frame(sock, 64 * 1024)
        if challenge.get("type") == "error" and isinstance(challenge.get("envelope"), dict):
            sock.close()
            return None, challenge["envelope"]
        if challenge.get("type") != "challenge" or not protocol.valid_nonce(challenge.get("server_nonce")):
            raise protocol.FrameError("Unexpected handshake")
        token = bytes.fromhex(record["token"])
        nonce = secrets.token_hex(32)
        protocol.send_frame(sock, {"type": "hello", "cli_protocol": protocol.CLI_PROTOCOL, "client": label,
                                   "client_nonce": nonce,
                                   "proof": protocol.client_proof(token, challenge["server_nonce"], nonce)}, 16 * 1024)
        welcome = protocol.recv_frame(sock, 64 * 1024)
        if welcome.get("type") == "error" and isinstance(welcome.get("envelope"), dict):
            sock.close()
            return None, welcome["envelope"]
        expected = protocol.server_proof(token, challenge["server_nonce"], nonce)
        if welcome.get("type") != "welcome" or not protocol.proof_matches(expected, welcome.get("server_proof")):
            sock.close()
            return None, _unavailable("The socket did not prove it belongs to the Angelo instance in the endpoint record")
        return sock, None
    except (OSError, protocol.FrameError) as error:
        sock.close()
        return None, _unavailable(f"Angelo is not reachable ({error}); is it running with local CLI access on?")


def run(request, state_directory, label, response_timeout):
    sock, failure = connect(state_directory, label)
    if failure is not None:
        return failure
    try:
        protocol.send_frame(sock, dict(request, type="request"), protocol.MAX_REQUEST_BYTES)
        response = protocol.recv_frame(sock, protocol.MAX_RESPONSE_BYTES, response_timeout)
    except (OSError, protocol.FrameError) as error:
        return _unavailable(f"The connection ended before a response ({error})", call=_call_stub(request))
    finally:
        sock.close()
    value = response.get("envelope")
    if response.get("type") not in {"response", "error"} or not isinstance(value, dict):
        return _unavailable("Angelo sent an unexpected response")
    return value


def _call_stub(request):
    args = request.get("args") or {}
    return {"id": args.get("id"), "tool": args.get("tool"), "state": "detached"} if request.get("command") == "call" else None


def render_text(value):
    lines = [f"{value.get('code')} (exit {value.get('exit')}, retry {value.get('retry')}): "
             + protocol.escape_text(value.get("message", ""))]
    for key in ("conversation", "document", "toolset_version"):
        if value.get(key) is not None:
            lines.append(f"{key}: {protocol.escape_text(value[key])}")
    if value.get("call"):
        lines.append("call: " + json.dumps(value["call"], ensure_ascii=True, sort_keys=True))
    if value.get("result") is not None:
        lines.append("result:")
        lines.extend(json.dumps(value["result"], ensure_ascii=True, indent=2, sort_keys=True).splitlines())
    return "\n".join(lines)


def emit(value, output_format, stdout=None, stderr=None):
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    # ensure_ascii escapes control, ANSI and bidirectional characters in JSON mode too.
    text = render_text(value) if output_format == "text" else json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    stdout.write(text + "\n")
    stdout.flush()
    if not value.get("ok") and output_format == "json":
        stderr.write(f"angelo: {value.get('code')}: {protocol.escape_text(value.get('message', ''))}\n")
    exit_code = value.get("exit")
    return exit_code if type(exit_code) is int and exit_code in protocol.EXIT_CODES.values() else 8


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    output_format = "json"
    try:
        options = parser().parse_args(argv)
        output_format = options.format
        label = options.client or product_env.get("CLIENT", environ) or "external"
        if not protocol.valid_label(label):
            raise UsageError("Use a client label of 1-32 characters from [A-Za-z0-9._-]")
        request = build_request(options)
        state = Path(options.state_dir) if options.state_dir else default_state_directory(environ)
    except (UsageError, OSError) as error:
        return emit(protocol.envelope("invalid", str(error)), output_format)
    wait = request["args"].get("wait", 0) if request["command"] == "call" else protocol.FRAME_TIMEOUT
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        value = run(request, state, label, wait + RESPONSE_MARGIN)
    except KeyboardInterrupt:
        # Detaching is not a human Stop: host work continues and keeps its receipt.
        value = protocol.envelope("pending", "Detached by a signal; Angelo keeps the call. Repeat the identical "
                                  "command or run `angelo status --call ID`", call=_call_stub(request))
    finally:
        signal.signal(signal.SIGTERM, previous)
    return emit(value, output_format)
