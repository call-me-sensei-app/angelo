# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine live link's loopback server: authenticated game-engine editors, framed messages. No Blender.

It listens on 127.0.0.1 on a free port. Each connection must prove the endpoint record's token within
``AUTH_SECONDS`` (``engine_link_protocol``); until then nothing it sends is read as a request. A client then sends
small requests (``open``, ``close``, ``status``, ``ping``) that the main thread takes with ``drain``, and receives
whatever the main thread ``send``s. Each client has its own writer thread, so a large scene never blocks Blender's
main thread, and only the newest scene waits to be sent (other frames keep their order): a slow editor skips
versions instead of falling behind. Threads never touch Blender.

**Captures** are the one payload a client may send: when Angelo's agent asks to see a page through the web link
(``capture``), the editor answers that request, and only it, with one PNG of at most ``MAX_CAPTURE`` bytes. The
reader allows the payload while that request is outstanding, from a client that said it can capture; any other
payload still ends the connection (doc/angelo/engine_link.rst, "Transport").
"""

import collections
import itertools
import os
import secrets
import select
import socket
import threading
import time

from . import engine_link_protocol as protocol, thread_start

AUTH_SECONDS = 10.0
IDLE_SECONDS = 45.0        # A client pings every 10 s; silence this long means it is gone.
MAX_CLIENTS = 8            # Authenticated editors.
MAX_PENDING = 4            # Connections still proving the token; the oldest makes way for a new one.
SEND_SECONDS = 120.0       # A frame not taken by the editor in this long ends the connection.
FLUSH_SECONDS = 1.0        # How long close() waits for queued frames (Godot's "ended") to leave.
CLIENT_TYPES = frozenset({"open", "close", "status", "ping"})
MAX_CAPABILITIES = 8
MAX_CAPTURE = 8 * 1024 * 1024   # Bytes of the one PNG a client may send in reply to a capture request.
MAX_DELTAS = 8   # Delta scenes waiting for one editor; past it they go and the editor gets a full scene (``lagging``).
PNG = b"\x89PNG\r\n\x1a\n"
CAPTURE_CODES = frozenset({"captured", "capture_failed"})


def project_key(project):
    """``project`` as ``engine_link_assets.serves`` compares it with an asset's project: resolved and case-folded as
    the platform does. It reads the folder's path, which a slow or network drive can stall, so it runs on the
    client's own thread when the editor authenticates, never on the main thread."""
    try:
        return os.path.normcase(os.path.realpath(project))
    except (OSError, ValueError):   # Unresolvable (an embedded NUL): as said, so it matches no resolved project.
        return os.path.normcase(project)


def client_info(info):
    """What an editor said about itself when it authenticated, bounded: ``engine`` (default "godot", the first
    plugin, which names itself too), ``project`` (and ``project_key``, its ``project_key``), ``project_name``,
    ``plugin``, ``version`` (the engine's version, sent under the engine's own name: ``"godot": "4.7.2"``) and
    ``capabilities`` (what it can show: "durable", "live"; absent when it didn't say, so the engine's adapter
    decides)."""
    info = info if isinstance(info, dict) else {}
    kept = {key: str(info[key])[:400] for key in ("project", "project_name", "plugin") if key in info}
    if "project" in kept:
        kept["project_key"] = project_key(kept["project"])
    engine = str(info.get("engine") or "godot")[:40]
    kept["engine"] = engine
    if engine in info:
        kept["version"] = str(info[engine])[:80]
    capabilities = info.get("capabilities")
    if isinstance(capabilities, list):
        kept["capabilities"] = [str(item)[:32] for item in capabilities[:MAX_CAPABILITIES]]
    return kept


class _Client:
    def __init__(self, ident, sock, address):
        self.id, self.sock, self.address = ident, sock, address
        self.info = {}
        self.authenticated = False
        self.closed = False
        self.started = time.monotonic()
        self.queue = collections.deque()     # (header, payload) in order; at most one scene, the newest.
        self.sending = False                 # A frame taken from the queue is still being written.
        self.wake = threading.Condition()
        self.sent_scenes = 0
        self.capture = None                  # The outstanding capture request: {"id", "deadline", "png", "report"}.
        self.lagging = False                 # Delta scenes were dropped: the editor needs a full scene.


class LinkServer:
    """One running link server. ``port`` goes in the endpoint record; thread-safe."""

    def __init__(self, token, app_info=None):
        if not isinstance(token, bytes) or len(token) != 32:
            raise ValueError("The link token is 32 bytes")
        self._token = token
        self._app = dict(app_info or {})
        self._lock = threading.Lock()
        self._clients = {}
        self._inbox = collections.deque()
        self._departed, self._forget = {}, set()   # Info of editors that left, until their last items are handled.
        self._ids = itertools.count(1)
        self.closed = False
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(MAX_CLIENTS)
        self._thread = threading.Thread(target=self._accept, name="Angelo engine link", daemon=True)
        thread_start.start(self._thread, self._listener.close)   # Never a port left listening with nobody to accept.

    @property
    def port(self):
        return self._listener.getsockname()[1]

    # Main thread -----------------------------------------------------------------------------------------------

    def drain(self):
        """[(client id, header)] received since the last call, in order; ``{"type": "connected"}`` when an editor has
        proved the token, ``{"type": "disconnected"}`` when one left. ``info_of`` still answers for an editor that left
        until the next call, so requests it sent just before leaving are read as its own."""
        with self._lock:
            items = list(self._inbox)
            self._inbox.clear()
            for ident in self._forget:
                self._departed.pop(ident, None)
            self._forget = {ident for ident, header in items if header.get("type") == "disconnected"}
        return items

    def info_of(self, client_id):
        """What an editor said about itself (``client_info``): a connected one, or one whose leaving was drained last."""
        with self._lock:
            client = self._clients.get(client_id)
            if client is not None and client.authenticated:
                return dict(client.info)
            return dict(self._departed.get(client_id, {}))

    def clients(self):
        """{client id: what it said about itself (``client_info``)} for authenticated clients."""
        with self._lock:
            return {ident: dict(client.info) for ident, client in self._clients.items() if client.authenticated}

    def send(self, client_id, header, payload=b""):
        """Queue a frame for one client; a whole ``scene`` frame replaces any scene still waiting. A delta scene
        (``kind`` "delta": it applies to the one before) never replaces one: past ``MAX_DELTAS`` waiting, every
        waiting scene goes and the editor is ``lagging``, owed a full scene. False when the client is gone or the
        frame was dropped so."""
        frame = (dict(header), bytes(payload))
        with self._lock:
            client = self._clients.get(client_id)
        if client is None or client.closed or not client.authenticated:
            return False
        with client.wake:
            if header.get("type") == "scene":
                scenes = sum(1 for item in client.queue if item[0].get("type") == "scene")
                delta = header.get("kind") == "delta"
                if not delta or scenes >= MAX_DELTAS:   # An unsent older scene goes; order is otherwise kept.
                    client.queue = collections.deque(item for item in client.queue
                                                     if item[0].get("type") != "scene")
                if delta and scenes >= MAX_DELTAS:
                    client.lagging = True
                    client.wake.notify_all()
                    return False
            client.queue.append(frame)
            client.wake.notify_all()
        return True

    def lagging(self, client_id):
        """Whether a client lost delta scenes it was behind on (cleared as read): its next scene must be whole."""
        with self._lock:
            client = self._clients.get(client_id)
        if client is None:
            return False
        with client.wake:
            found, client.lagging = client.lagging, False
        return found

    def lagging_any(self):
        """Whether any client is owed a full scene (``lagging``), without clearing it."""
        with self._lock:
            clients = list(self._clients.values())
        return any(client.lagging for client in clients)

    def broadcast(self, header, payload=b"", only=None):
        """``send`` to every authenticated client, or those ``only(info)`` accepts; returns how many."""
        return sum(self.send(ident, header, payload) for ident, info in self.clients().items()
                   if only is None or only(info))

    def capture(self, client_id, header, timeout):
        """Ask one client for a picture (``header``: the ``capture`` frame's fields, without ``id``) and wait up to
        ``timeout`` seconds: ``(png, report)``. ``png`` is the PNG it sent, or None; ``report`` is its answer's header
        (``code`` ``captured`` or ``capture_failed`` with a ``message``), or None when it didn't answer or left. One
        capture per client at a time; waits on the calling thread, never on the client's."""
        with self._lock:
            client = self._clients.get(client_id)
        if (client is None or client.closed or not client.authenticated
                or "capture" not in client.info.get("capabilities", ())):
            return None, None
        ident = secrets.token_hex(8)
        with client.wake:
            if client.capture is not None:
                return None, {"code": "capture_failed", "message": "Another capture is waiting"}
            client.capture = {"id": ident, "deadline": time.monotonic() + timeout, "png": None, "report": None,
                              "claimed": False}
        try:
            if not self.send(client_id, dict(header, type="capture", id=ident)):
                return None, None
            with client.wake:
                client.wake.wait_for(lambda: client.capture["report"] is not None or client.closed, timeout)
                found = dict(client.capture)
        finally:
            with client.wake:
                unfinished = client.capture is not None and client.capture["claimed"] and \
                    client.capture["report"] is None
                client.capture = None
            if unfinished:   # Its answer's payload was still arriving: nothing waits for it now (break-it F2).
                self._close(client)
        return found["png"], found["report"]

    def disconnect(self, client_id):
        with self._lock:
            client = self._clients.get(client_id)
        if client is not None:
            self._close(client)

    def close(self, flush=True):
        """Stop listening and drop every client, after up to ``FLUSH_SECONDS`` for queued frames to leave (so an
        editor hears ``ended``). Idempotent."""
        if self.closed:
            return
        self.closed = True
        try:
            self._listener.shutdown(socket.SHUT_RDWR)   # Wakes a blocked accept() where close() alone doesn't.
        except OSError:
            pass
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            clients = list(self._clients.values())
        deadline = time.monotonic() + (FLUSH_SECONDS if flush else 0.0)
        for client in clients:
            with client.wake:
                while (client.queue or client.sending) and not client.closed and time.monotonic() < deadline:
                    client.wake.wait(0.05)
            self._close(client)
        self._thread.join(timeout=5)

    # Threads ---------------------------------------------------------------------------------------------------

    def _accept(self):
        while not self.closed:
            try:
                sock, address = self._listener.accept()
            except OSError:
                return   # Closed.
            if address[0] != "127.0.0.1":
                sock.close()
                continue
            if self.closed:
                sock.close()
                return
            with self._lock:
                pending = sorted((item for item in self._clients.values() if not item.authenticated),
                                 key=lambda item: item.started)
                full = len(self._clients) - len(pending) >= MAX_CLIENTS
                evicted = pending[0] if len(pending) >= MAX_PENDING and not full else None
                client = None if full else _Client(next(self._ids), sock, address)
                if client is not None:
                    self._clients[client.id] = client
            if evicted is not None:   # Silent connections can't keep an editor out: the oldest waiting one goes.
                self._close(evicted)
            if client is None:
                sock.close()
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                threading.Thread(target=self._serve, args=(client,), name="Angelo engine link client",
                                 daemon=True).start()
            except RuntimeError:   # No thread to serve it: refused, its place freed, and the accept loop goes on.
                self._close(client)

    def _deliver(self, client, header):
        with self._lock:
            self._inbox.append((client.id, header))

    def _serve(self, client):
        client.sock.settimeout(SEND_SECONDS)   # Set once: reads wait in select(), so only sends use it.
        try:
            if self._handshake(client):
                threading.Thread(target=self._write, args=(client,), name="Angelo engine link writer",
                                 daemon=True).start()
                self._deliver(client, {"type": "connected"})
                self._read(client)
        except (OSError, protocol.FrameError):
            pass
        finally:
            was_in = client.authenticated
            if was_in:
                with self._lock:
                    self._departed[client.id] = dict(client.info)
            self._close(client)
            if was_in:
                self._deliver(client, {"type": "disconnected"})

    def _send_now(self, client, header, payload=b""):
        client.sock.sendall(protocol.encode(header, payload))   # Blocking, bounded by SEND_SECONDS (_serve).

    def _receive(self, client, reader, deadline):
        """The next frame from ``client`` before ``deadline`` (monotonic), or None when it closed."""
        while True:
            frames = reader.frames()
            if frames:
                return frames[0], frames[1:]
            left = deadline - time.monotonic()
            if left <= 0:
                raise protocol.FrameError("No frame in time")
            ready, _, _ = select.select([client.sock], [], [], min(left, 1.0))   # Never touches the timeout the
            if not ready:                                                       # writer's sends use.
                if client.closed:
                    return None, []
                continue
            data = client.sock.recv(65536)
            if not data:
                return None, []
            reader.feed(data)

    def _handshake(self, client):
        server_nonce = protocol.new_nonce()
        self._send_now(client, {"type": "hello", "protocol": protocol.PROTOCOL, "nonce": server_nonce,
                                "app": self._app})
        reader = protocol.Reader(max_payload=0)   # Clients never send a payload: none is ever buffered.
        (frame, rest) = self._receive(client, reader, time.monotonic() + AUTH_SECONDS)
        if frame is None:
            return False
        header, payload = frame
        client_nonce = header.get("nonce")
        expected = (protocol.client_proof(self._token, server_nonce, client_nonce)
                    if protocol.valid_nonce(client_nonce) else "")
        if header.get("type") != "auth" or payload or rest or not expected or not protocol.proof_matches(
                expected, header.get("proof")):
            self._send_now(client, {"type": "error", "code": "auth",
                                    "message": "Angelo didn't accept this connection"})
            return False
        client.info = client_info(header.get("client"))
        reader.limit(lambda found: self._capture_allowance(client, found))   # Still 0 but for a capture reply.
        client.reader = reader
        self._send_now(client, {"type": "welcome", "proof": protocol.server_proof(self._token, server_nonce,
                                                                                  client_nonce)})
        client.authenticated = True
        return True

    @staticmethod
    def _outstanding(client, header):
        """The client's outstanding capture when ``header`` answers it and it has no answer yet, else None."""
        found = client.capture
        if (found is None or found["report"] is not None or header.get("type") != "status"
                or header.get("code") not in CAPTURE_CODES or header.get("id") != found["id"]):
            return None
        return found

    def _capture_allowance(self, client, header):
        """How many payload bytes ``header`` may announce (the reader asks before buffering any): ``MAX_CAPTURE``
        for the first answer to the outstanding capture, when it is ``captured`` and before its deadline; else 0.
        The first answer's header claims the request whatever it says, so a second answer read in the same chunk,
        before the first is handled, is allowed nothing (break-it F1)."""
        with client.wake:
            found = self._outstanding(client, header)
            if found is None or found["claimed"]:
                return 0
            found["claimed"] = True
            if header.get("code") != "captured" or time.monotonic() > found["deadline"]:
                return 0
            return MAX_CAPTURE

    def _take_capture(self, client, header, payload):
        """Keep a client's answer to its outstanding capture; True when ``header`` was one. A PNG payload with
        anything but a PNG in it ends the connection."""
        if header.get("type") != "status" or header.get("code") not in CAPTURE_CODES:
            return False
        if payload and not payload.startswith(PNG):
            raise protocol.FrameError("A capture is a PNG")
        with client.wake:
            found = self._outstanding(client, header)
            if found is not None:   # An answer after the wait ended, or to another id, is dropped.
                found["png"] = payload if header.get("code") == "captured" and payload else None
                found["report"] = {"code": header.get("code"),
                                   "message": str(header.get("message") or "")[:200]}
                client.wake.notify_all()
        return True

    def _read(self, client):
        reader = client.reader
        while not client.closed:   # Not on self.closed: close() ends each client itself, after the flush, so the
            # reader never drops a connection while the writer still sends its last frames (an editor's "ended").
            frame, rest = self._receive(client, reader, time.monotonic() + IDLE_SECONDS)
            if frame is None:
                return
            for header, payload in [frame] + rest:
                if header["type"] not in CLIENT_TYPES:
                    raise protocol.FrameError("Clients send only small requests")
                if self._take_capture(client, header, payload):
                    continue
                if payload:   # Only a capture reply carries one (``_capture_allowance``).
                    raise protocol.FrameError("Clients send only small requests")
                if header["type"] == "ping":
                    self.send(client.id, {"type": "pong"})
                    continue
                self._deliver(client, header)

    def _write(self, client):
        while not client.closed:
            with client.wake:
                while not client.closed and not client.queue:
                    client.wake.wait(1.0)
                if client.closed:
                    return
                frame = client.queue.popleft()
                client.sending = True
            try:
                self._send_now(client, *frame)
                if frame[0].get("type") == "scene":
                    client.sent_scenes += 1
            except (OSError, protocol.FrameError):
                self._close(client)
                return
            finally:
                with client.wake:   # close() waits for this: a frame being written is still queued for it.
                    client.sending = False
                    client.wake.notify_all()

    def _close(self, client):
        with client.wake:
            if client.closed:
                return
            client.closed = True
            client.wake.notify_all()
        with self._lock:
            self._clients.pop(client.id, None)
        try:
            client.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            client.sock.close()
        except OSError:
            pass
