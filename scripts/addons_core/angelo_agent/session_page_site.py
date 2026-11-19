# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One app session's live page as a site on Angelo's one local web server (``web_preview_server``). No Blender here.

The page lives under ``/s/<token>/`` with its own unguessable token (24 random bytes), on 127.0.0.1 only, behind the
server's guards (the Host check against DNS rebinding, ``no-store``, ``nosniff``, no referrer, a strict policy with
``form-action 'none'``). It is read-only: GET and HEAD, and nothing it serves acts. It serves the page
(``session_page/``), the conversation's state as the main thread last published it (``state.json``), an event
stream that says when that changed (``events``), the pictures the state names by id (``picture/<id>``), the scene from
the live preview's own export (``scene.glb``, ``scene_feed``) and the preview's look and Three.js build
(``preview/``: ``look.js``, ``drag.js``, ``vendor/three/``). Threads never touch Blender: the main thread publishes.
Closing ends the link at once: open pages are told, and every path under the token answers 404 after.
"""

import json
import re
import secrets
import select
import socket
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import web_preview_server as server

PAGE_ROOT = Path(__file__).with_name("session_page")
PREVIEW_ROOT = server.PAGE_ROOT
PREVIEW_SHARED = ("look.js", "drag.js")   # The preview's modules the page uses as they are; and vendor/three/.
PICTURE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
MAX_PICTURES = 12
MAX_VIEWERS = 8   # Open pages per session at once: each holds a server thread while it streams.
GONE_POLL = 1.0   # Seconds between looks at whether an idle stream's page closed (a reload, a closed tab).
MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))


class SessionSite:
    """A shared session's live page; ``url`` is what the app's result carries. Thread-safe."""

    def __init__(self, marks=None, extra_files=None):
        self.token = secrets.token_urlsafe(24)
        self.prefix = "s/" + self.token
        html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
        for mark, value in (marks or {}).items():   # The wordmark, inline so the theme colours it.
            html = html.replace(mark, value)
        self._page = html.encode("utf-8")
        self.policy = server.page_policy(html)
        self.closed = False
        self._changed = threading.Condition()
        self._version = 0
        self._state = json.dumps({"status": "ready", "version": 0}).encode("utf-8")
        self._pictures = OrderedDict()
        self._extra = dict(extra_files or {})
        self.scene_feed = None   # A callable answering the live preview's latest export (bytes), or None.
        self.viewers = 0         # Open event streams: pages showing the session now.
        self._server = server.open_site(self.prefix, self)

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def path(self):
        return "/%s/" % self.prefix

    @property
    def url(self):
        return "http://127.0.0.1:%d%s" % (self.port, self.path)

    def publish(self, state):
        """The conversation as the main thread built it (``session_page_state.build``): open pages refresh."""
        with self._changed:
            self._version += 1
            self._state = json.dumps(dict(state, version=self._version)).encode("ascii")   # Any text, escaped.
            self._changed.notify_all()

    def publish_picture(self, identity, data):
        """A picture the state names, by its id (never a path); the oldest go past ``MAX_PICTURES``."""
        if not isinstance(identity, str) or not PICTURE_ID.match(identity) or not _whole(data):
            return False
        with self._changed:
            self._pictures[identity] = data
            self._pictures.move_to_end(identity)
            while len(self._pictures) > MAX_PICTURES:
                self._pictures.popitem(last=False)
        return True

    def has_picture(self, identity):
        with self._changed:
            return identity in self._pictures

    def serve(self, handler, name, head):
        """A GET or HEAD under this page's path (the server checked its Host)."""
        if name in ("", "index.html"):
            return handler._send(200, self._page, "text/html; charset=utf-8", head)
        if name == "state.json":
            with self._changed:
                body = self._state
            return handler._send(200, body, "application/json; charset=utf-8", head)
        if name == "events":
            return self._events(handler) if not head else handler._send(200, kind="text/event-stream", head=True)
        if name == "scene.glb":
            feed = self.scene_feed
            body = feed() if feed is not None else None
            return handler._send(200 if body else 404, body or b"Not found", "model/gltf-binary" if body
                                 else "text/plain; charset=utf-8", head)
        if name.startswith("picture/"):
            identity = name[len("picture/"):]
            with self._changed:
                data = self._pictures.get(identity) if PICTURE_ID.match(identity) else None
            kind = next((kind for magic, kind in MAGIC if data and data.startswith(magic)), None)
            if kind is None:
                return handler._send(404, b"Not found", head=head)
            return handler._send(200, data, kind, head)
        if name.startswith("preview/"):
            shared = name[len("preview/"):]
            if shared not in PREVIEW_SHARED and not shared.startswith("vendor/three/"):
                return handler._send(404, b"Not found", head=head)
            body, kind = server.static_file(PREVIEW_ROOT, shared)
        else:
            body, kind = server.static_file(PAGE_ROOT, name, self._extra)
        if body is None:
            return handler._send(404, b"Not found", head=head)
        return handler._send(200, body, kind, head)

    def _events(self, handler):
        with self._changed:   # The cap's check and count together: pages opening at once never pass it.
            full = self.viewers >= MAX_VIEWERS
            if not full:
                self.viewers += 1
        if full:
            return handler._send(429, b"Too many pages open")
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler._common()
            handler.end_headers()
            seen, written = None, time.monotonic()
            while not self.closed:
                with self._changed:
                    if self._version == seen and not self.closed:
                        self._changed.wait(GONE_POLL)
                    version = self._version
                if self.closed:
                    handler.wfile.write(b"event: closed\ndata: {}\n\n")
                    break
                if version != seen:
                    seen = version
                    handler.wfile.write(b"event: version\ndata: %d\n\n" % version)
                elif _gone(handler):
                    break   # The page closed: it stops counting now, not at the next keep-alive.
                elif time.monotonic() - written >= server.EVENT_WAIT:
                    handler.wfile.write(b": keep-alive\n\n")
                else:
                    continue
                handler.wfile.flush()
                written = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self._changed:
                self.viewers -= 1

    def close(self):
        """End the link now: open pages are told, and the token's paths answer 404. Idempotent."""
        if self.closed:
            return
        with self._changed:
            self.closed = True
            self._changed.notify_all()
        server.close_site(self.prefix)


def _gone(handler):
    """Whether an event stream's page closed it: the page never sends on it, so a readable socket is its end."""
    try:
        readable, _, _ = select.select([handler.connection], [], [], 0)
        return bool(readable) and handler.connection.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def _whole(data):
    """Whether ``data`` is a whole PNG, JPEG or WebP picture: never one still being written (a view is reserved empty
    and filled as it is captured), which the page would show broken and never ask for again."""
    if not isinstance(data, bytes):
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data.endswith(b"IEND\xaeB`\x82")
    if data.startswith(b"\xff\xd8\xff"):
        return data.endswith(b"\xff\xd9")
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return len(data) == int.from_bytes(data[4:8], "little") + 8
    return False
