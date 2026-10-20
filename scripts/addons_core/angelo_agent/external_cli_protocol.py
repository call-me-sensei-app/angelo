# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Wire contract shared by Angelo's local CLI listener and the ``angelo`` client.

Pure standard library and no ``bpy``: the bundled Python runs the client in
isolated mode (``-I``) and the host's service thread uses the same functions.

Frames are a 4-byte big-endian length followed by one UTF-8 JSON object.
Authentication is a nonce challenge: both sides prove knowledge of the
per-launch token with HMAC-SHA256, so the token itself never crosses the
socket. See ``doc/angelo/external_cli.rst``.
"""

import hashlib
import hmac
import json
import os
import re
import stat
import struct
import time
import unicodedata
from pathlib import Path

# Protocol 2 (2026-10-07) changed only the HMAC domain separators below to the
# Angelo name; a protocol 1 peer gets ``incompatible`` instead of a failed proof.
CLI_PROTOCOL = 2
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
FRAME_TIMEOUT = 10.0
NONCE_HEX = 64  # 32 random bytes, hex encoded.
DEFAULT_WAIT = 90.0
MAX_WAIT = 300.0

# Exit status is part of the public contract; agents branch on it.
EXIT_CODES = {"ok": 0, "tool_failed": 1, "invalid": 2, "not_applied": 3, "refused": 4, "stale": 5,
              "pending": 6, "uncertain": 7, "unavailable": 8, "incompatible": 9, "busy": 10}
RETRY = {"ok": "never", "tool_failed": "never", "invalid": "never", "not_applied": "never",
         "refused": "never", "stale": "after_observe", "pending": "poll", "uncertain": "never",
         "unavailable": "same_id_later", "incompatible": "never", "busy": "same_id_later"}
RETRY_VALUES = frozenset(RETRY.values())

LABEL = re.compile(r"[A-Za-z0-9._-]{1,32}\Z")
CALL_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

# HMAC domain separators: part of the wire contract, so changing one needs a new
# CLI_PROTOCOL.
_CLIENT_CONTEXT = f"angelo-cli/{CLI_PROTOCOL}/client"
_SERVER_CONTEXT = f"angelo-cli/{CLI_PROTOCOL}/server"
# A connected MCP app (external_clients) also proves its own key; an optional hello field, so phase-1 clients
# (no key, read-only) are unchanged.
_APP_CONTEXT = "angelo-mcp/1/app"
MAX_KEY_FILE_BYTES = 128


ENDPOINT_FILE = "endpoint.json"
MAX_ENDPOINT_BYTES = 4096


class FrameError(ValueError):
    """Malformed, oversized, slow or truncated frame. The connection must close."""


class UnsafePath(ValueError):
    """A runtime directory or endpoint record failed the owner/mode/link checks."""


def check_private_directory(path):
    """Require a real directory owned by this effective user with mode 0700 or stricter."""
    info = os.lstat(path)  # FileNotFoundError propagates: "not running" is not "unsafe".
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafePath(f"{path} must be a real directory, not a link")
    if info.st_uid != os.geteuid():
        raise UnsafePath(f"{path} is owned by another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise UnsafePath(f"{path} must be private to its owner (mode 0700)")
    return Path(path)


def read_endpoint(path):
    """Read an owner-only, non-link endpoint record. Never follows a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise
        raise UnsafePath(f"{path} cannot be opened safely (links are refused)") from error
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > MAX_ENDPOINT_BYTES):
            raise UnsafePath(f"{path} must be a small regular file readable only by its owner")
        data = os.read(descriptor, MAX_ENDPOINT_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        record = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as error:
        raise UnsafePath(f"{path} is not a valid endpoint record") from error
    if (not isinstance(record, dict) or type(record.get("cli_protocol")) is not int
            or not isinstance(record.get("socket"), str) or not Path(record["socket"]).is_absolute()
            or type(record.get("pid")) is not int or record["pid"] <= 0
            or not isinstance(record.get("instance"), str) or not 1 <= len(record["instance"]) <= 64
            or not isinstance(record.get("token"), str) or HEX_DIGEST.fullmatch(record["token"]) is None):
        raise UnsafePath(f"{path} is not a valid endpoint record")
    return record


def _reject_constant(value):
    raise ValueError("Non-finite JSON numbers are not accepted: " + value)


def encode_frame(value, limit):
    if not isinstance(value, dict):
        raise FrameError("A frame carries one JSON object")
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise FrameError("The frame is not finite JSON") from error
    if not payload or len(payload) > limit:
        raise FrameError(f"The frame exceeds the {limit}-byte limit")
    return struct.pack(">I", len(payload)) + payload


def send_frame(sock, value, limit, timeout=FRAME_TIMEOUT):
    data = encode_frame(value, limit)
    sock.settimeout(timeout)
    sock.sendall(data)


def _receive_exact(sock, size, deadline):
    chunks, remaining = [], size
    while remaining:
        left = deadline - time.monotonic()
        if left <= 0:
            raise FrameError("The frame was not received in time")
        sock.settimeout(left)
        try:
            chunk = sock.recv(min(remaining, 65536))
        except TimeoutError as error:  # socket.timeout is an alias since 3.10.
            raise FrameError("The frame was not received in time") from error
        if not chunk:
            raise FrameError("The connection closed inside a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock, limit, timeout=FRAME_TIMEOUT):
    """Read one frame within an absolute deadline, so a slow sender cannot hold a slot."""
    deadline = time.monotonic() + timeout
    (size,) = struct.unpack(">I", _receive_exact(sock, 4, deadline))
    if size == 0 or size > limit:
        raise FrameError(f"The frame length {size} is outside 1-{limit} bytes")
    payload = _receive_exact(sock, size, deadline)
    try:
        value = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise FrameError("The frame is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FrameError("A frame carries one JSON object")
    return value


def _proof(context, token, server_nonce, client_nonce):
    if not isinstance(token, bytes) or len(token) != 32:
        raise ValueError("The per-launch token and app keys are 32 bytes")
    message = "\n".join((context, server_nonce, client_nonce)).encode("ascii")
    return hmac.new(token, message, hashlib.sha256).hexdigest()


def client_proof(token, server_nonce, client_nonce):
    return _proof(_CLIENT_CONTEXT, token, server_nonce, client_nonce)


def server_proof(token, server_nonce, client_nonce):
    return _proof(_SERVER_CONTEXT, token, server_nonce, client_nonce)


def app_proof(key, server_nonce, client_nonce):
    """A connected app's proof of its own key (``external_clients``), alongside the launch-token proof."""
    return _proof(_APP_CONTEXT, key, server_nonce, client_nonce)


def read_key(path):
    """An owner-only app key file, read without following a link: 32 bytes, or None when missing or unsafe."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > MAX_KEY_FILE_BYTES):
            return None
        text = os.read(descriptor, MAX_KEY_FILE_BYTES + 1).decode("ascii", "replace").strip()
    finally:
        os.close(descriptor)
    return bytes.fromhex(text) if HEX_DIGEST.fullmatch(text) else None


def valid_nonce(value):
    return isinstance(value, str) and len(value) == NONCE_HEX and HEX_DIGEST.fullmatch(value) is not None


def proof_matches(expected, supplied):
    return isinstance(supplied, str) and len(supplied) == 64 and hmac.compare_digest(expected, supplied)


def valid_label(value):
    return isinstance(value, str) and LABEL.fullmatch(value) is not None


def valid_call_id(value):
    return isinstance(value, str) and CALL_ID.fullmatch(value) is not None


def envelope(code, message, *, toolset_version=None, document=None, conversation=None,
             call=None, result=None, retry=None, **extra):
    """Every response carries the same fields; ``result`` is the projected host result."""
    if code not in EXIT_CODES:
        raise ValueError("Unknown CLI result code")
    retry = RETRY[code] if retry is None else retry
    if retry not in RETRY_VALUES:
        raise ValueError("Unknown CLI retry hint")
    value = {"cli_protocol": CLI_PROTOCOL, "ok": code == "ok", "code": code, "exit": EXIT_CODES[code],
             "retry": retry, "toolset_version": toolset_version, "document": document,
             "conversation": conversation, "call": call, "result": result, "message": str(message)}
    value.update(extra)
    return value


_BIDI_AND_FORMAT = frozenset({"Cf", "Cc", "Cs", "Co", "Cn", "Zl", "Zp"})


def escape_text(value):
    """Escape control, ANSI and bidirectional characters for --format text.

    Scene names and tool results are untrusted data; a terminal must never
    interpret them as escape sequences, reorder them visually or receive
    forged extra lines. Newlines are escaped too; the formatter adds its own.
    """
    output = []
    for character in str(value):
        if unicodedata.category(character) in _BIDI_AND_FORMAT:
            code = ord(character)
            output.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
        else:
            output.append(character)
    return "".join(output)
