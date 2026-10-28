# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live web preview's local server: loopback only, one random path, GET only. No Blender dependency.

It serves the branded page and the Three.js build bundled with the add-on (``web_preview/``, nothing from a CDN),
the latest export (``scene.glb``), its report (``report.json``) and a server-sent event stream that says when a
new export is ready, so the page reloads it. Every URL starts with a random token, and a request whose Host header
is not this loopback address is refused, so another site in the browser (DNS rebinding included) cannot read the
scene. Threads never touch Blender: the main thread publishes each export with ``publish``.
"""

import base64
import hashlib
import json
import mimetypes
import re
import secrets
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

PAGE_ROOT = Path(__file__).with_name("web_preview")
EVENT_WAIT = 15.0          # Seconds between keep-alive comments on an idle event stream.
MAX_CAPTURE = 8 * 1024 * 1024   # Bytes of the PNG the page sends when Angelo asks for a picture.
MAX_FEEDBACK = 64 * 1024        # Bytes of the page's report on its web shaders.
PNG = b"\x89PNG\r\n\x1a\n"
CONNECTION_TIMEOUT = 30.0
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
         ".wasm": "application/wasm", ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2"}
# The page's one inline script is its import map, allowed by its hash; WebAssembly decoders need wasm-unsafe-eval,
# the loaders' workers and embedded images blob: URLs.
POLICY = ("default-src 'none'; script-src 'self' 'sha256-%s' 'wasm-unsafe-eval'; style-src 'self'; "
          "img-src 'self' blob: data:; connect-src 'self' blob: data:; worker-src 'self' blob:; font-src 'self'; "
          "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.S)


def page_policy(html):
    """The Content-Security-Policy for ``html``: its import map's hash in script-src."""
    found = IMPORT_MAP.search(html)
    digest = hashlib.sha256((found.group(1) if found else "").encode("utf-8")).digest()
    return POLICY % base64.b64encode(digest).decode("ascii")


class _Server(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, preview):
        self.preview = preview
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    timeout = CONNECTION_TIMEOUT
    server_version = "Angelo"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's signature.
        pass   # The page polls and streams; a line per request would flood the console.

    def _send(self, status, body=b"", kind="text/plain; charset=utf-8", head=False):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self._common()
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _common(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", self.server.preview.policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler's naming.
        self._get(head=True)

    def do_GET(self):  # noqa: N802
        self._get(head=False)

    def do_POST(self):  # noqa: N802 - the page's capture and shader report, from this page only.
        preview = self.server.preview
        port = self.server.server_address[1]
        origins = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}
        if self.headers.get("Host") not in {item[7:] for item in origins} or self.headers.get("Origin") not in origins:
            return self._send(403, b"Forbidden")
        name = self.path.split("?", 1)[0][len("/" + preview.token + "/"):] if self.path.startswith(
            "/" + preview.token + "/") else None
        limit = {"capture": MAX_CAPTURE, "feedback": MAX_FEEDBACK}.get(name)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if limit is None:
            return self._send(404, b"Not found")
        if not 0 < length <= limit:
            return self._send(413, b"Too large")
        body = self.rfile.read(length)
        if name == "capture":
            if not body.startswith(PNG):
                return self._send(400, b"Not a PNG")
            preview.receive_capture(body)
        else:
            try:
                report = json.loads(body.decode("utf-8"))
            except (UnicodeError, ValueError):
                return self._send(400, b"Not JSON")
            if not isinstance(report, dict):
                return self._send(400, b"Not an object")
            preview.receive_feedback(report)
        return self._send(204)

    def _get(self, head):
        preview = self.server.preview
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {"127.0.0.1:%d" % port, "localhost:%d" % port}:
            return self._send(403, b"Forbidden", head=head)
        path = self.path.split("?", 1)[0]
        prefix = "/" + preview.token + "/"
        if not path.startswith(prefix):
            return self._send(404, b"Not found", head=head)
        name = path[len(prefix):] or "index.html"
        if name == "events":
            return self._events() if not head else self._send(200, kind="text/event-stream", head=True)
        if name == "report.json":
            return self._send(200, preview.report_bytes(), "application/json; charset=utf-8", head)
        if name == "scene.glb":
            body = preview.scene_bytes()
            return self._send(200 if body else 404, body or b"", "model/gltf-binary", head)
        body, kind = preview.static(name)
        if body is None:
            return self._send(404, b"Not found", head=head)
        return self._send(200, body, kind, head)

    def _events(self):
        preview = self.server.preview
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self._common()
        self.end_headers()
        seen, asked = None, preview.captures_asked
        preview.streams += 1
        try:
            while not preview.closed:
                version, wanted = preview.wait_for_change(seen, asked, EVENT_WAIT)
                if preview.closed:
                    self.wfile.write(b"event: closed\ndata: {}\n\n")
                    break
                if version != seen:
                    seen = version
                    self.wfile.write(b"event: version\ndata: %d\n\n" % version)
                elif wanted != asked:
                    asked = wanted
                    self.wfile.write(b"event: capture\ndata: %d\n\n" % wanted)
                else:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            preview.streams -= 1


class PreviewServer:
    """One running preview server; ``url`` is what the browser opens. Thread-safe."""

    def __init__(self, extra_files=None, marks=None):
        self.token = secrets.token_urlsafe(16)
        html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
        for mark, value in (marks or {}).items():   # The wordmark, inline so the theme colours it.
            html = html.replace(mark, value)
        self._page = html.encode("utf-8")
        self.policy = page_policy(html)
        self.closed = False
        self._changed = threading.Condition()
        self._version = 0
        self._scene = b""
        self._report = b"{}"
        self._extra = dict(extra_files or {})   # Published name -> absolute file (the bundled fonts).
        self.streams = 0                # Open event streams: pages showing the preview now.
        self.captures_asked = 0
        self._capture = None
        self.feedback = {}              # The page's latest report on its web shaders.
        self._server = _Server(self)
        self._thread = threading.Thread(target=self._server.serve_forever, name="Angelo web preview", daemon=True)
        self._thread.start()

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        return "http://127.0.0.1:%d/%s/" % (self.port, self.token)

    @property
    def version(self):
        return self._version

    def publish(self, scene_bytes, report):
        """A new export (or only a new report, with ``scene_bytes`` None): the page reloads it."""
        body = json.dumps(dict(report, version=self._version + 1), ensure_ascii=False).encode("utf-8")
        with self._changed:
            if scene_bytes is not None:
                self._scene = scene_bytes
            self._report = body
            self._version += 1
            self._changed.notify_all()

    def wait_for_change(self, seen, asked, timeout):
        """(version, captures asked) once either moves past what a stream has sent, or after ``timeout``."""
        with self._changed:
            if self._version == seen and self.captures_asked == asked and not self.closed:
                self._changed.wait(timeout)
            return self._version, self.captures_asked

    def capture(self, timeout):
        """A PNG of the page as the browser shows it now, or None when no page answers within ``timeout``."""
        with self._changed:
            self._capture = None
            self.captures_asked += 1
            self._changed.notify_all()
            self._changed.wait_for(lambda: self._capture is not None or self.closed, timeout)
            return self._capture

    def receive_capture(self, png):
        with self._changed:
            self._capture = png
            self._changed.notify_all()

    def receive_feedback(self, report):
        with self._changed:
            self.feedback = report

    def scene_bytes(self):
        with self._changed:
            return self._scene

    def report_bytes(self):
        with self._changed:
            return self._report

    def static(self, name):
        """(bytes, content type) of a page file, or (None, None): only files inside ``web_preview``."""
        if name == "index.html":
            return self._page, "text/html; charset=utf-8"
        if name in self._extra:
            path = Path(self._extra[name])
        else:
            if "\\" in name or any(part in {"", ".", ".."} or part.startswith(".") for part in name.split("/")):
                return None, None
            path = PAGE_ROOT.joinpath(*name.split("/"))
            try:
                if not path.resolve().is_relative_to(PAGE_ROOT.resolve()):
                    return None, None
            except OSError:
                return None, None
        if not path.is_file():
            return None, None
        suffix = path.suffix.lower()
        kind = TYPES.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in {"application/json", "image/svg+xml"}:
            kind += "; charset=utf-8"
        try:
            return path.read_bytes(), kind
        except OSError:
            return None, None

    def close(self):
        """Stop serving now; open event streams end. Idempotent."""
        if self.closed:
            return
        self.closed = True
        with self._changed:
            self._changed.notify_all()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

