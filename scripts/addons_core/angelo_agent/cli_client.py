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
from pathlib import Path

from . import external_cli_protocol as protocol
from . import external_build, product_env
from .blender_release import RELEASE
from .state_paths import STATE_FOLDER, state_override

# The per-version user folder name of this build (the Blender series Angelo is based on). One value with the release
# the agent's texts name, so a base move changes it too: a literal here once stayed 5.2 on 5.3, and the CLI and the
# headless worker read the earlier release's folder.
BLENDER_SERIES = RELEASE
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
    root = _Parser(prog="angelo", description="Angelo's local status for a coding agent. An agent works with Angelo "
                   "through Angelo's own agent: connect it in Angelo (Preferences > AI Providers > External AI apps).")
    root.add_argument("--client", help="client label (1-32 of [A-Za-z0-9._-]); default $ANGELO_CLIENT "
                      "(or a deprecated spelling) or 'external'")
    root.add_argument("--format", choices=("json", "text"), default="json")
    root.add_argument("--state-dir", help="Angelo agent state directory (default: $ANGELO_STATE_DIR or the user config)")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="instance, document and this client's conversation")
    session = commands.add_parser("session", help="session new: rotate to a fresh External conversation")
    session.add_argument("action", choices=("new",))
    return root


def build_request(options):
    """Translate parsed options to the wire request. Raises UsageError."""
    if options.command == "session":
        return {"command": "session", "args": {"action": options.action}}
    return {"command": options.command, "args": {}}


def _unavailable(message):
    return protocol.envelope("unavailable", message)


def endpoint_mismatch(state_directory):
    """The incompatible-build sentence for the endpoint record, read without connecting, or "" (``external_build``)."""
    endpoint = Path(state_directory) / "cli" / protocol.ENDPOINT_FILE
    try:
        protocol.check_private_directory(endpoint.parent)
        return external_build.record_mismatch(protocol.read_endpoint(endpoint))
    except (FileNotFoundError, protocol.UnsafePath):
        return ""


def connect(state_directory, label, key=None):
    """Authenticate to the instance named by the endpoint record; returns (socket, None) or (None, envelope).

    ``key`` is a connected app's own 32-byte key (``external_mcp``); its proof rides along the launch-token proof.
    """
    endpoint = Path(state_directory) / "cli" / protocol.ENDPOINT_FILE
    try:
        protocol.check_private_directory(endpoint.parent)
        record = protocol.read_endpoint(endpoint)
    except FileNotFoundError:
        return None, _unavailable("Angelo is not running, or access for external apps is off (Preferences > AI "
                                  "Providers). Open Angelo and retry")
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
        hello = {"type": "hello", "cli_protocol": protocol.CLI_PROTOCOL, "client": label, "client_nonce": nonce,
                 "proof": protocol.client_proof(token, challenge["server_nonce"], nonce),
                 "agent_protocol": external_build.AGENT_PROTOCOL}
        if key is not None:
            hello["app_proof"] = protocol.app_proof(key, challenge["server_nonce"], nonce)
        protocol.send_frame(sock, hello, 16 * 1024)
        welcome = protocol.recv_frame(sock, 64 * 1024)
        if welcome.get("type") == "error" and isinstance(welcome.get("envelope"), dict):
            sock.close()
            return None, welcome["envelope"]
        expected = protocol.server_proof(token, challenge["server_nonce"], nonce)
        if welcome.get("type") != "welcome" or not protocol.proof_matches(expected, welcome.get("server_proof")):
            sock.close()
            return None, _unavailable("The socket did not prove it belongs to the Angelo instance in the endpoint record")
        theirs = welcome.get("agent_protocol")   # Another build holds access (2.0.0 says nothing): never a command.
        if theirs != external_build.AGENT_PROTOCOL:
            sock.close()
            version = external_build.holder_version(dict(record, angelo_version=welcome.get("angelo_version")
                                                         or record.get("angelo_version")))   # A version, or None.
            return None, protocol.envelope("incompatible", external_build.host_mismatch(
                version, record["pid"], theirs if type(theirs) is int else None))
        return sock, None
    except (OSError, protocol.FrameError) as error:
        sock.close()
        return None, _unavailable(f"Angelo is not reachable ({error}); is it running with local CLI access on?")


def run(request, state_directory, label, response_timeout, key=None):
    sock, failure = connect(state_directory, label, key)
    if failure is not None:
        return failure
    try:
        protocol.send_frame(sock, dict(request, type="request"), protocol.MAX_REQUEST_BYTES)
        response = protocol.recv_frame(sock, protocol.MAX_RESPONSE_BYTES, response_timeout)
    except (OSError, protocol.FrameError) as error:
        return _unavailable(f"The connection ended before a response ({error})")
    finally:
        sock.close()
    value = response.get("envelope")
    if response.get("type") not in {"response", "error"} or not isinstance(value, dict):
        return _unavailable("Angelo sent an unexpected response")
    return value


def render_text(value):
    lines = [f"{value.get('code')} (exit {value.get('exit')}, retry {value.get('retry')}): "
             + protocol.escape_text(value.get("message", ""))]
    for key in ("conversation", "document", "toolset_version"):
        if value.get(key) is not None:
            lines.append(f"{key}: {protocol.escape_text(value[key])}")
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
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        value = run(request, state, label, protocol.FRAME_TIMEOUT + RESPONSE_MARGIN)
    except KeyboardInterrupt:
        value = protocol.envelope("unavailable", "Interrupted by a signal before Angelo answered")
    finally:
        signal.signal(signal.SIGTERM, previous)
    return emit(value, output_format)
