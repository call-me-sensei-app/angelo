# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
"""The connection to Angelo, without Unreal: reads Angelo's endpoint record, connects over loopback, proves the
record's token without sending it (HMAC-SHA256 both ways) and exchanges framed messages. Polled from the editor's
tick, so nothing here blocks: the socket is non-blocking and ``poll`` does whatever is ready.

Angelo's side is ``engine_link_protocol`` / ``engine_link_server`` (the Godot link's transport made engine-neutral);
the Godot plugin's link_client.gd is this file's twin. Frames are a 4-byte big-endian length, a UTF-8 JSON header,
then ``header["bytes"]`` bytes of payload.
"""

import errno
import hashlib
import hmac
import json
import os
import secrets
import select
import socket
import stat
import struct
import sys
import time

PROTOCOL = "angelo-link/1"
RECORD_FILE = "angelo-link.json"
RECORD_VARIABLE = "ANGELO_LINK_RECORD"
ENGINE = "unreal"
MAX_HEADER = 64 * 1024
MAX_PAYLOAD = 512 * 1024 * 1024
MAX_RECORD = 4096
PING_SECONDS = 10.0
AUTH_SECONDS = 10.0
OFF, CONNECTING, AUTHENTICATING, READY = "off", "connecting", "authenticating", "ready"
_HEX = frozenset("0123456789abcdef")


class FrameError(ValueError):
    pass


def record_path(environ=None, platform=None):
    """Angelo's endpoint record: ``ANGELO_LINK_RECORD``, else ``angelo-link.json`` in Angelo's per-user data folder."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if environ.get(RECORD_VARIABLE):
        return environ[RECORD_VARIABLE]
    home = environ.get("HOME", os.path.expanduser("~"))
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Angelo", RECORD_FILE)
    if platform == "win32":
        local = environ.get("LOCALAPPDATA") or os.path.join(environ.get("USERPROFILE", home), "AppData", "Local")
        return os.path.join(local, "Angelo", RECORD_FILE)
    return os.path.join(environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"), "Angelo", RECORD_FILE)


def read_record(path=None):
    """The record as a dict when it is a small regular file this plugin understands, else None."""
    try:
        info = os.stat(path or record_path())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD:
            return None
        with open(path or record_path(), "rb") as handle:
            record = json.loads(handle.read(MAX_RECORD + 1).decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    token = record.get("token") if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get("protocol") != PROTOCOL or type(record.get("port")) is not int
            or not 0 < record["port"] < 65536 or not isinstance(token, str) or len(token) != 64
            or not set(token) <= _HEX):
        return None
    return record


def encode(header, payload=b""):
    if payload:
        header = dict(header, bytes=len(payload))
    body = json.dumps(header, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_HEADER or len(payload) > MAX_PAYLOAD:
        raise FrameError("frame too large")
    return struct.pack(">I", len(body)) + body + bytes(payload)


class Reader:
    """Incremental frame parser: ``feed`` bytes, take complete ``frames``."""

    def __init__(self):
        self._buffer, self._header = bytearray(), None

    def feed(self, data):
        self._buffer += data

    def frames(self):
        done = []
        while True:
            if self._header is None:
                if len(self._buffer) < 4:
                    return done
                (size,) = struct.unpack(">I", self._buffer[:4])
                if not 0 < size <= MAX_HEADER:
                    raise FrameError("bad header length")
                if len(self._buffer) < 4 + size:
                    return done
                try:
                    header = json.loads(bytes(self._buffer[4:4 + size]).decode("utf-8"))
                except (UnicodeError, ValueError) as error:
                    raise FrameError("header is not JSON") from error
                length = header.get("bytes", 0) if isinstance(header, dict) else None
                if not isinstance(header.get("type") if isinstance(header, dict) else None, str) or \
                        type(length) is not int or not 0 <= length <= MAX_PAYLOAD:
                    raise FrameError("bad header")
                self._header = header
                del self._buffer[:4 + size]
            size = self._header.get("bytes", 0)
            if len(self._buffer) < size:
                return done
            payload = bytes(self._buffer[:size])
            del self._buffer[:size]
            done.append((self._header, payload))
            self._header = None


def proof(token, context, server_nonce, client_nonce):
    message = "\n".join((context, server_nonce, client_nonce)).encode("ascii")
    return hmac.new(token, message, hashlib.sha256).hexdigest()


def _nonce_ok(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


class LinkClient:
    """One connection to Angelo. ``on_message(header, payload)`` gets everything after authentication except pong."""

    def __init__(self, client_info, on_message, record=None, clock=time.monotonic):
        self.client_info = dict(client_info, engine=ENGINE)
        self.on_message, self._record_path, self._clock = on_message, record, clock
        self.state, self.last_error, self.app_path = OFF, "", ""
        self._sock, self._reader, self._out = None, Reader(), bytearray()
        self._queue, self._token, self._since = [], b"", 0.0
        self._server_nonce = self._client_nonce = ""

    def is_ready(self):
        return self.state == READY

    def ensure_connected(self):
        """Start connecting when not connected; False (with ``last_error``) when Angelo isn't listening."""
        if self.state != OFF:
            return True
        record = read_record(self._record_path)
        if record is None:
            self.last_error = ("Angelo isn't listening for Unreal. In Angelo, turn on the engine link from the + menu "
                               "in the chat bar.")
            return False
        self._token, self.app_path = bytes.fromhex(record["token"]), str(record.get("app", ""))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        code = sock.connect_ex(("127.0.0.1", record["port"]))
        if code not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, getattr(errno, "WSAEWOULDBLOCK", -1)):
            sock.close()
            self.last_error = "Couldn't reach Angelo. Is it still open?"
            return False
        self._sock, self._reader, self._out = sock, Reader(), bytearray()
        self._server_nonce = self._client_nonce = ""
        self.state, self._since = CONNECTING, self._clock()
        return True

    def request(self, header):
        """Send ``header`` now, or once the connection is ready."""
        if self.state == READY:
            self._send(header)
        else:
            self._queue.append(dict(header))

    def close(self, error=""):
        if self._sock is not None:
            self._sock.close()
        self._sock, self._out, self._queue = None, bytearray(), []
        if error:
            self.last_error = error
        self.state = OFF

    def poll(self):
        """Do whatever is ready: finish connecting, read frames, authenticate, write, ping. Call every tick."""
        if self._sock is None:
            return
        now = self._clock()
        try:
            readable, writable, _ = select.select([self._sock], [self._sock], [], 0)
            if self.state == CONNECTING:
                if not writable:
                    if now - self._since > AUTH_SECONDS:
                        self.close("Angelo didn't answer.")
                    return
                if self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                    self.close("Couldn't reach Angelo. Is it still open?")
                    return
                self.state = AUTHENTICATING
            if readable:
                self._read()
            if self._sock is not None and self._out:
                sent = self._sock.send(self._out)
                del self._out[:sent]
        except (OSError, FrameError) as error:
            self.close("The connection to Angelo broke (%s)." % type(error).__name__)
            return
        if self.state == AUTHENTICATING and now - self._since > AUTH_SECONDS:
            self.close("Angelo didn't answer.")
        elif self.state == READY and now - self._since > PING_SECONDS:
            self._since = now
            self._send({"type": "ping"})

    def _read(self):
        while self._sock is not None:
            try:
                data = self._sock.recv(1 << 20)
            except (BlockingIOError, InterruptedError):
                return
            if not data:
                self.close("Angelo closed the link." if self.state == READY else "Something else answered on "
                           "Angelo's port.")
                return
            self._reader.feed(data)
            for header, payload in self._reader.frames():
                self._receive(header, payload)
                if self._sock is None:
                    return

    def _receive(self, header, payload):
        kind = header["type"]
        if self.state == AUTHENTICATING:
            if kind == "hello" and header.get("protocol") == PROTOCOL and _nonce_ok(header.get("nonce")) \
                    and not self._server_nonce:
                self._server_nonce, self._client_nonce = header["nonce"], secrets.token_hex(32)
                self._send({"type": "auth", "nonce": self._client_nonce, "client": self.client_info,
                            "proof": proof(self._token, PROTOCOL + "/client", self._server_nonce, self._client_nonce)})
            elif kind == "welcome" and self._server_nonce and hmac.compare_digest(
                    str(header.get("proof", "")),
                    proof(self._token, PROTOCOL + "/server", self._server_nonce, self._client_nonce)):
                self.state, self._since = READY, self._clock()
                for queued in self._queue:
                    self._send(queued)
                self._queue = []
            else:
                self.close("Angelo didn't accept this connection." if kind == "error"
                           else "Something else answered on Angelo's port.")
            return
        if kind != "pong":
            self.on_message(header, payload)

    def _send(self, header):
        self._out += encode(header)
