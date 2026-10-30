# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine live link's wire format, proofs and endpoint record. Standard library only; no Blender.

Engine-neutral: one listener serves every game-engine editor plugin, each naming its engine when it authenticates.
The Godot plugin (``godot_addon/angelo_link``) is the first; doc/angelo/engine_link.rst ("Transport") is the contract
every plugin follows.

* **Frames**: a 4-byte big-endian length, a UTF-8 JSON object of that length (the header), then ``header["bytes"]``
  bytes of payload when the header names any (a .glb). Headers are at most ``MAX_HEADER`` bytes and payloads at most
  ``MAX_PAYLOAD``; anything else closes the connection.
* **Authentication** (mutual, the token never crosses the socket): the server sends ``hello`` with a fresh nonce,
  the client answers ``auth`` with its own nonce and ``HMAC-SHA256(token, "angelo-link/1/client\\n" + server nonce +
  "\\n" + client nonce)``, the server answers ``welcome`` with the same over ``.../server``, which the client checks
  before it trusts anything the server sends.
* **The endpoint record** (``angelo-link.json`` in Angelo's per-user data folder, or ``ANGELO_LINK_RECORD``):
  protocol, loopback port, process ID, the 32-byte token in hex and the app's path (so an editor can bring Angelo
  to the front). Written owner-only and exclusively; removed when the link stops or Angelo quits.
"""

import hashlib
import hmac
import json
import os
import secrets
import stat
import struct
import sys
from pathlib import Path

PROTOCOL = "angelo-link/1"
RECORD_FILE = "angelo-link.json"
RECORD_VARIABLE = "LINK_RECORD"   # ANGELO_LINK_RECORD: an explicit record path (fixtures).
MAX_HEADER = 64 * 1024
MAX_PAYLOAD = 512 * 1024 * 1024
MAX_RECORD = 4096
NONCE_HEX = 64
_HEX64 = frozenset("0123456789abcdef")
_CLIENT = PROTOCOL + "/client"
_SERVER = PROTOCOL + "/server"


class FrameError(ValueError):
    """A malformed, oversized or truncated frame; the connection closes."""


def _reject_constant(value):
    raise ValueError("Non-finite JSON numbers are not accepted: " + value)


def encode(header, payload=b""):
    """One frame's bytes: ``header`` (a dict) with ``bytes`` set to the payload's length when there is one."""
    if not isinstance(header, dict):
        raise FrameError("A frame's header is one JSON object")
    if payload:
        if len(payload) > MAX_PAYLOAD:
            raise FrameError("The payload exceeds %d bytes" % MAX_PAYLOAD)
        header = dict(header, bytes=len(payload))
    try:
        body = json.dumps(header, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise FrameError("The header is not finite JSON") from error
    if len(body) > MAX_HEADER:
        raise FrameError("The header exceeds %d bytes" % MAX_HEADER)
    return struct.pack(">I", len(body)) + body + bytes(payload)


def decode_header(body, max_payload=MAX_PAYLOAD):
    """The header dict of ``body`` (the JSON after the length), checked; raises FrameError."""
    try:
        header = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise FrameError("The header is not UTF-8 JSON") from error
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise FrameError("A header is a JSON object with a type")
    size = header.get("bytes", 0)
    if type(size) is not int or not 0 <= size <= max_payload:
        raise FrameError("The payload length is outside 0-%d bytes" % max_payload)
    return header


class Reader:
    """Incremental frame parser for a non-blocking socket: ``feed`` bytes, then take complete ``frames``.

    ``max_payload`` bounds what a header may announce, checked before any of it is buffered (Angelo's server reads
    clients with 0: they never send a payload, so a client can't make it hold more than one header)."""

    def __init__(self, max_payload=MAX_PAYLOAD):
        self._buffer = bytearray()
        self._header = None
        self._max_payload = max_payload

    def feed(self, data):
        self._buffer += data

    def frames(self):
        """[(header, payload bytes)] completed so far. Raises FrameError on a bad frame."""
        done = []
        while True:
            if self._header is None:
                if len(self._buffer) < 4:
                    return done
                (size,) = struct.unpack(">I", self._buffer[:4])
                if not 0 < size <= MAX_HEADER:
                    raise FrameError("The header length %d is outside 1-%d bytes" % (size, MAX_HEADER))
                if len(self._buffer) < 4 + size:
                    return done
                self._header = decode_header(bytes(self._buffer[4:4 + size]), self._max_payload)
                del self._buffer[:4 + size]
            size = self._header.get("bytes", 0)
            if len(self._buffer) < size:
                return done
            payload = bytes(self._buffer[:size])
            del self._buffer[:size]
            done.append((self._header, payload))
            self._header = None


def new_nonce():
    return secrets.token_hex(NONCE_HEX // 2)


def valid_nonce(value):
    return isinstance(value, str) and len(value) == NONCE_HEX and set(value) <= _HEX64


def _proof(context, token, server_nonce, client_nonce):
    if not isinstance(token, bytes) or len(token) != 32:
        raise ValueError("The link token is 32 bytes")
    message = "\n".join((context, server_nonce, client_nonce)).encode("ascii")
    return hmac.new(token, message, hashlib.sha256).hexdigest()


def client_proof(token, server_nonce, client_nonce):
    return _proof(_CLIENT, token, server_nonce, client_nonce)


def server_proof(token, server_nonce, client_nonce):
    return _proof(_SERVER, token, server_nonce, client_nonce)


def proof_matches(expected, supplied):
    return isinstance(supplied, str) and len(supplied) == 64 and hmac.compare_digest(expected, supplied)


def record_path(environ=None):
    """Where the endpoint record lives: ``ANGELO_LINK_RECORD``, else Angelo's per-user data folder."""
    from . import product_env, storage
    environ = os.environ if environ is None else environ
    value, _variable = product_env.lookup(RECORD_VARIABLE, environ)
    if value:
        return Path(value)
    return storage.data_directory(environ) / RECORD_FILE


def _owned(info):
    return not hasattr(os, "geteuid") or info.st_uid == os.geteuid()


def write_record(path, record):
    """Write ``record`` owner-only (0600 in a 0700 folder where modes exist), never through a link or over a live
    record. A leftover record (its process gone) is replaced. Raises FileExistsError when another live Angelo owns
    it, OSError when the folder can't be written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = read_record(path)
    if (existing is not None and existing["pid"] != os.getpid() and process_alive(existing["pid"])
            and listening(existing["port"])):
        raise FileExistsError(existing["pid"])   # Another running Angelo has the link.
    remove_record(path, any_owner=True)
    data = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


def read_record(path):
    """The record at ``path`` when it is a small, owner-only regular file with valid fields, else None."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        modes = sys.platform != "win32"
        if (not stat.S_ISREG(info.st_mode) or not _owned(info) or info.st_size > MAX_RECORD
                or modes and stat.S_IMODE(info.st_mode) & 0o077):
            return None
        data = os.read(descriptor, MAX_RECORD + 1)
    finally:
        os.close(descriptor)
    try:
        record = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError):
        return None
    token = record.get("token") if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get("protocol") != PROTOCOL
            or type(record.get("port")) is not int or not 0 < record["port"] < 65536
            or type(record.get("pid")) is not int or record["pid"] <= 0
            or not isinstance(token, str) or len(token) != 64 or not set(token) <= _HEX64):
        return None
    return record


def remove_record(path, pid=None, any_owner=False):
    """Remove the record when this process (``pid``, default ours) wrote it, or ``any_owner``. Files only."""
    record = read_record(path)
    if record is None and not any_owner:
        return False
    if record is not None and not any_owner and record["pid"] != (pid or os.getpid()):
        return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def listening(port):
    """True when something accepts connections on loopback ``port``: a record whose process ID now belongs to another
    program (reused after a crash and a restart) has no listener, so it doesn't lock the link off."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def process_alive(pid):
    """True when a process with ``pid`` exists (on Windows, when it can be opened)."""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:   # An exited process stays openable while anything holds a handle to it.
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def app_bundle(binary, platform=None):
    """What an editor brings to the front: the application bundle on macOS (``Angelo.app``), else the executable."""
    platform = sys.platform if platform is None else platform
    marker = binary.find(".app/") if platform == "darwin" else -1
    return binary[:marker + 4] if marker >= 0 else binary
