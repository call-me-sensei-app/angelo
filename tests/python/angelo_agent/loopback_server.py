# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A loopback HTTP server for tests of the update check and the crash upload.

Binds 127.0.0.1 on a free port; nothing leaves the computer. Routes map a
path to a handler ``(request) -> (status, headers, body)``; every request is
recorded (method, path, headers, body) so a test can assert exactly what the
app sent. Used by the host tests and by the GUI suite (which points
``ANGELO_UPDATE_FEED_URL`` and ``ANGELO_CRASH_REPORT_URL`` at it).
"""

import http.server
import threading
import time
from dataclasses import dataclass


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict
    body: bytes = b""


class LoopbackServer:
    def __init__(self):
        self.routes, self.requests, self.delay = {}, [], 0.0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                record = Recorded(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                owner.requests.append(record)
                if owner.delay:
                    time.sleep(owner.delay)
                route = owner.routes.get(self.path.split("?")[0])
                status, headers, payload = route(record) if route else (404, {}, b"not found")
                headers = dict(headers)
                cut = headers.pop("X-Test-Cut", None)   # Announce the whole body, send only this many bytes, hang up.
                self.send_response(status)
                for name, value in {"Content-Length": str(len(payload)), "Connection": "close", **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload if cut is None else payload[:int(cut)])

            do_GET = do_POST = do_HEAD = _serve

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def route(self, path, status=200, body=b"", headers=None):
        self.routes[path] = lambda request, s=status, b=body, h=headers or {}: (s, h, b)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def serve_file(self, path, data, etag='"v1"', cut=None):
        """Serve ``data`` at ``path`` as a download server does: ``Range`` and ``If-Range`` answered (206 or 200),
        a strong ``ETag``; ``cut`` sends only that many bytes of the first full answer, then hangs up."""
        state = {"cut": cut}

        def handler(record):
            headers = {"ETag": etag, "Accept-Ranges": "bytes"}
            wanted, condition = record.headers.get("range", ""), record.headers.get("if-range")
            if wanted.startswith("bytes=") and wanted.endswith("-") and (condition is None or condition == etag):
                start = int(wanted[6:-1])
                if start >= len(data):
                    return 416, {"Content-Range": "bytes */%d" % len(data)}, b""
                headers["Content-Range"] = "bytes %d-%d/%d" % (start, len(data) - 1, len(data))
                return 206, headers, data[start:]
            if state["cut"] is not None:
                headers["X-Test-Cut"], state["cut"] = str(state["cut"]), None
            return 200, headers, data
        self.routes[path] = handler

    def count(self, path=None):
        return sum(1 for r in self.requests if path is None or r.path.split("?")[0] == path)
