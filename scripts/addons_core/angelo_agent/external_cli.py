# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Off-by-default local CLI listener: private Unix socket, per-launch token, no bpy.

One accept thread plus one short-lived thread per connection (at most
``MAX_CONNECTIONS``). A connection is checked against the peer's credentials
before any frame is read, authenticates with the token challenge, carries
exactly one request and closes. The request handler may wait for main-thread
work but never touches ``bpy``. There is no TCP or LAN listener of any kind.

macOS and Linux share this Unix-socket transport. Windows named pipes are not
implemented: ``start`` refuses there instead of falling back to anything else.
"""

import atexit
import hashlib
import json
import os
import secrets
import socket
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import external_cli_protocol as protocol
from . import product_env

# Private runtime directory and socket names. Renamed with the product; the CLI
# finds the socket through the endpoint record, never by guessing this name.
RUNTIME_NAME = "angelo"

MAX_CONNECTIONS = 8
# macOS sun_path is 104 bytes including the terminating NUL; Linux allows 108.
SUN_PATH_LIMIT = 103
SOL_LOCAL = 0          # <sys/un.h> on macOS
LOCAL_PEERPID = 2      # <sys/un.h> on macOS; display-only
XUCRED_SIZE = 76       # struct xucred: version, uid, ngroups, 16 groups
XUCRED_VERSION = 0
MAX_HELLO_BYTES = 16 * 1024


class TransportUnavailable(RuntimeError):
    """A human-readable reason local CLI access cannot listen."""


@dataclass(frozen=True)
class Peer:
    uid: object = None      # None when the platform has no peer-credential API
    pid: object = None      # display-only; PIDs can be reused
    checked: bool = False


@dataclass(frozen=True)
class ClientInfo:
    label: str
    peer: Peer
    # A connected MCP app's allowed levels (external_clients), proven by its own key; None for a phase-1 CLI
    # client, which stays read-only.
    levels: object = None


def peer_credentials(sock):
    """Kernel-reported peer UID (and PID where available).

    Raises OSError when an available API fails, so the caller refuses the
    connection instead of trusting it. Platforms without an API return an
    unchecked Peer; the private runtime directory is then the only boundary.
    """
    if hasattr(socket, "SO_PEERCRED"):  # Linux: struct ucred {pid_t; uid_t; gid_t}
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
        pid, uid, _gid = struct.unpack("iII", raw)
        return Peer(uid, pid, True)
    if hasattr(socket, "LOCAL_PEERCRED"):  # macOS/BSD: struct xucred
        raw = sock.getsockopt(SOL_LOCAL, socket.LOCAL_PEERCRED, XUCRED_SIZE)
        version, uid = struct.unpack_from("=II", raw)
        if version != XUCRED_VERSION:
            raise OSError("Unknown peer credential structure version")
        pid = None
        if sys.platform == "darwin":
            try:
                pid = struct.unpack("=i", sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))[0]
            except OSError:
                pid = None
        return Peer(uid, pid, True)
    return Peer()


def default_runtime_directory(environ=None, platform=None, uid=None):
    """``$XDG_RUNTIME_DIR/angelo``; macOS uses a short per-user ``$TMPDIR`` path."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    uid = os.geteuid() if uid is None else uid
    override, variable = product_env.lookup("CLI_RUNTIME_DIR", environ)
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise TransportUnavailable(f"{variable} must be an absolute path")
        return path
    if platform == "darwin":
        base = environ.get("TMPDIR") or "/tmp"
        return Path(base) / f"{RUNTIME_NAME}-{uid}"
    runtime = environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_absolute():
        return Path(runtime) / RUNTIME_NAME
    return Path("/tmp") / f"{RUNTIME_NAME}-{uid}"


def create_private_directory(path):
    path = Path(path)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise TransportUnavailable(f"Cannot create the private directory {path}: {error.strerror}") from error
    try:
        return protocol.check_private_directory(path)
    except (protocol.UnsafePath, FileNotFoundError) as error:
        raise TransportUnavailable(str(error)) from error


def write_endpoint(path, record):
    """Exclusive, owner-only and never through a link: O_EXCL|O_NOFOLLOW, 0600."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def socket_accepts(path, timeout=0.2):
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class LocalCliServer:
    """Owns the socket, endpoint record and connection threads; never ``bpy``."""

    def __init__(self, state_directory, handler, *, runtime_directory=None, peer_lookup=peer_credentials,
                 max_connections=MAX_CONNECTIONS, frame_timeout=protocol.FRAME_TIMEOUT, describe=None, apps=None):
        self.state_directory = Path(state_directory)
        # name -> (levels, key) of the connected MCP apps (external_clients.AppRegistry.snapshot), read per hello.
        self.apps = apps or (lambda: {})
        self.handler = handler
        self.runtime_directory = Path(runtime_directory) if runtime_directory else None
        self.peer_lookup = peer_lookup
        self.frame_timeout = frame_timeout
        self.describe = describe or (lambda: {})
        self.max_connections = max_connections
        self._slots = threading.BoundedSemaphore(max_connections)
        self._lock = threading.Lock()
        self._connections = set()
        self._stop = threading.Event()
        self._listener = self._thread = None
        self.token = self.instance = self.socket_path = self.endpoint_path = None
        self.counters = {"accepted": 0, "refused_peer": 0, "refused_capacity": 0, "failed_authentication": 0}

    def _count(self, name):
        with self._lock:
            self.counters[name] += 1

    @property
    def listening(self):
        return self._listener is not None and not self._stop.is_set()

    def start(self):
        """Main thread. Bind first, then publish the endpoint naming that socket."""
        if sys.platform == "win32" or not hasattr(socket, "AF_UNIX"):
            raise TransportUnavailable("Local CLI access is not implemented on this platform yet "
                                       "(the Windows named-pipe transport is a later phase)")
        runtime = create_private_directory(self.runtime_directory or default_runtime_directory())
        self.state_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        cli_directory = create_private_directory(self.state_directory / "cli")
        scope = hashlib.sha256(os.fsencode(self.state_directory.resolve())).hexdigest()[:16]
        socket_path = runtime / f"{RUNTIME_NAME}-cli-{scope}.sock"
        if len(os.fsencode(socket_path)) > SUN_PATH_LIMIT:
            raise TransportUnavailable(f"The socket path is longer than {SUN_PATH_LIMIT} bytes; "
                                       "set ANGELO_CLI_RUNTIME_DIR to a shorter private directory")
        endpoint = cli_directory / protocol.ENDPOINT_FILE
        self._clear_stale(endpoint, socket_path)
        token, instance = secrets.token_bytes(32), secrets.token_hex(16)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o077)
        try:
            listener.bind(str(socket_path))
        except OSError as error:
            listener.close()
            raise TransportUnavailable(f"Cannot bind the local CLI socket: {error.strerror}") from error
        finally:
            os.umask(previous)
        try:
            os.chmod(socket_path, 0o600)
            listener.listen(self.max_connections)
            listener.settimeout(0.2)
            write_endpoint(endpoint, {"cli_protocol": protocol.CLI_PROTOCOL, "socket": str(socket_path),
                                      "pid": os.getpid(), "instance": instance, "token": token.hex(),
                                      "created": time.time()})
        except OSError as error:
            listener.close()
            self._unlink_socket(socket_path)
            raise TransportUnavailable(f"Cannot publish the local CLI endpoint: {error.strerror}") from error
        self.token, self.instance = token, instance
        self.socket_path, self.endpoint_path = socket_path, endpoint
        # Quitting Angelo with access on still withdraws the record and socket.
        # Files only: atexit runs after Blender has freed its data.
        atexit.register(self._remove_files)
        self._stop.clear()
        self._listener = listener
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="AngeloCliListener")
        self._thread.start()

    def _clear_stale(self, endpoint, socket_path):
        existing = None
        try:
            existing = protocol.read_endpoint(endpoint)
        except FileNotFoundError:
            pass
        except protocol.UnsafePath as error:
            raise TransportUnavailable(f"Refusing an unsafe endpoint record: {error}. Remove it manually.") from error
        if existing is not None:
            pid = existing["pid"]
            if pid != os.getpid() and process_alive(pid):
                raise TransportUnavailable(f"Another Angelo process (PID {pid}) provides local CLI access for this profile")
            endpoint.unlink()  # A dead process's (or this process's leftover) record.
        try:
            info = os.lstat(socket_path)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise TransportUnavailable(f"Refusing to replace {socket_path}: it is not this user's socket")
        if existing is None and socket_accepts(socket_path):
            raise TransportUnavailable("Another process is listening on this profile's CLI socket")
        socket_path.unlink()

    @staticmethod
    def _unlink_socket(path):
        try:
            info = os.lstat(path)
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid():
                os.unlink(path)
        except OSError:
            pass

    def _remove_endpoint(self):
        """Remove only this instance's record, never a newer instance's."""
        if self.endpoint_path is not None:
            try:
                if protocol.read_endpoint(self.endpoint_path).get("instance") == self.instance:
                    self.endpoint_path.unlink()
            except (OSError, protocol.UnsafePath):
                pass

    def _remove_files(self):
        self._remove_endpoint()
        if self.socket_path is not None:
            self._unlink_socket(self.socket_path)

    def stop(self):
        """Main thread. Withdraw the endpoint first, then the socket and connections."""
        self._stop.set()
        atexit.unregister(self._remove_files)
        self._remove_endpoint()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._listener is not None:
            self._listener.close()
        if self.socket_path is not None:
            self._unlink_socket(self.socket_path)
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._listener = self._thread = None
        self.token = None

    def _accept_loop(self):
        listener = self._listener
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.05)
                continue
            if not self._slots.acquire(blocking=False):
                self._count("refused_capacity")
                self._refuse(connection, "busy", "Too many local CLI connections; retry shortly")
                continue
            self._count("accepted")
            threading.Thread(target=self._serve, args=(connection,), daemon=True,
                             name="AngeloCliConnection").start()

    def _refuse(self, connection, code, message):
        try:
            protocol.send_frame(connection, {"type": "error", "envelope": protocol.envelope(code, message)},
                                64 * 1024, timeout=0.5)
        except (OSError, protocol.FrameError):
            pass
        finally:
            connection.close()

    def _serve(self, connection):
        with self._lock:
            self._connections.add(connection)
        try:
            try:
                peer = self.peer_lookup(connection)
            except OSError:
                self._count("refused_peer")
                return
            if peer.uid is not None and peer.uid != os.geteuid():
                # Refused before any frame is read; nothing about the instance leaks.
                self._count("refused_peer")
                return
            token = self.token
            if token is None or self._stop.is_set():
                return
            server_nonce = secrets.token_hex(32)
            protocol.send_frame(connection, {"type": "challenge", "cli_protocol": protocol.CLI_PROTOCOL,
                                             "server_nonce": server_nonce}, 4096, self.frame_timeout)
            hello = protocol.recv_frame(connection, MAX_HELLO_BYTES, self.frame_timeout)
            refusal, app = self._check_hello(hello, token, server_nonce)
            if refusal is not None:
                protocol.send_frame(connection, {"type": "error", "envelope": refusal}, 64 * 1024, self.frame_timeout)
                return
            welcome = {"type": "welcome", "cli_protocol": protocol.CLI_PROTOCOL,
                       "server_proof": protocol.server_proof(token, server_nonce, hello["client_nonce"])}
            welcome.update({key: value for key, value in self.describe().items() if key not in welcome})
            protocol.send_frame(connection, welcome, 64 * 1024, self.frame_timeout)
            request = protocol.recv_frame(connection, protocol.MAX_REQUEST_BYTES, self.frame_timeout)
            client = ClientInfo(hello["client"], peer, app[0] if app else None)   # The record the proof was checked against.
            if request.get("type") != "request":
                response = protocol.envelope("invalid", "Expected one request frame after the handshake")
            else:
                try:
                    response = self.handler(request, client)
                except Exception as error:  # A service defect must still answer, never hang or print.
                    response = protocol.envelope("unavailable", f"Internal CLI service error ({type(error).__name__})")
            self._respond(connection, response)
        except (protocol.FrameError, OSError):
            pass
        finally:
            with self._lock:
                self._connections.discard(connection)
            connection.close()
            self._slots.release()

    def _check_hello(self, hello, token, server_nonce):
        if hello.get("type") != "hello" or hello.get("cli_protocol") != protocol.CLI_PROTOCOL:
            return protocol.envelope("incompatible", f"This Angelo speaks CLI protocol {protocol.CLI_PROTOCOL}"), None
        if not protocol.valid_nonce(hello.get("client_nonce")):
            self._count("failed_authentication")
            return protocol.envelope("unavailable", "Authentication failed"), None
        expected = protocol.client_proof(token, server_nonce, hello["client_nonce"])
        if not protocol.proof_matches(expected, hello.get("proof")):
            self._count("failed_authentication")
            return protocol.envelope("unavailable", "Authentication failed; read the current endpoint record"), None
        if not protocol.valid_label(hello.get("client")):
            return protocol.envelope("invalid", "Use a client label of 1-32 characters from [A-Za-z0-9._-]"), None
        # A connected app's name is reserved for it: the call must also prove that app's own key. A key proof for a
        # name Angelo no longer knows is an app the person disconnected.
        app = self.apps().get(hello["client"])
        supplied = hello.get("app_proof")
        if app is None and supplied is not None:
            return protocol.envelope("unavailable", "This app was disconnected in Angelo. Connect it again in "
                                     "Preferences > AI Providers > External AI apps"), None
        if app is not None:
            expected = protocol.app_proof(app[1], server_nonce, hello["client_nonce"])
            if not protocol.proof_matches(expected, supplied):
                self._count("failed_authentication")
                return protocol.envelope("unavailable", "This app's key does not match; connect it again in "
                                         "Preferences > AI Providers > External AI apps"), None
        return None, app

    def _respond(self, connection, response):
        try:
            frame = protocol.encode_frame({"type": "response", "envelope": response}, protocol.MAX_RESPONSE_BYTES)
        except protocol.FrameError:
            # Never truncate: report the call ID with an explicit error instead.
            call = response.get("call") if isinstance(response, dict) else None
            oversized = protocol.envelope("tool_failed", "The result exceeds the 8 MiB response limit; narrow the "
                                          "query (page or limit) and call again", call=call,
                                          toolset_version=response.get("toolset_version") if isinstance(response, dict) else None,
                                          document=response.get("document") if isinstance(response, dict) else None,
                                          conversation=response.get("conversation") if isinstance(response, dict) else None)
            frame = protocol.encode_frame({"type": "response", "envelope": oversized}, protocol.MAX_RESPONSE_BYTES)
        connection.settimeout(self.frame_timeout)
        connection.sendall(frame)
