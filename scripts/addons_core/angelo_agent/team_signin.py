# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Browser sign-in to an Angelo team: PKCE, state and the loopback callback.

OAuth 2.1 authorization code with PKCE S256 for a native app (RFC 8252), as
the console expects (contract 2026-10-08): the app listens on
``http://127.0.0.1:<random port>/callback`` (never ``localhost``), opens the
system browser at ``{console}/oauth/authorize`` and receives ``code`` and
``state``, or ``error`` (for example ``access_denied`` when the person
cancels), plus ``iss`` (RFC 9207), which must name the console the sign-in
started at: a callback without it, or naming another server, is refused.

``LoopbackListener`` is Angelo's only network listener, a deliberate and
narrow exception to "no listener" (doc/angelo/external_cli.rst). It exists
only while a sign-in is in progress: it binds 127.0.0.1 on a port the OS
chooses and closes once the browser has seen the outcome, on ``close`` or
after ``TIMEOUT``. It never logs a request (the query carries the code).

The browser's view (``team_pages``, the console's design): ``/callback``
answers ``303 See Other`` to ``/result`` (or ``/mismatch`` for a wrong
``state`` or ``iss``), so the code never stays in the address bar or history.
``/result`` waits up to ``OUTCOME_WAIT`` for the app to finish the exchange
(``finish``) and shows what really happened: signed in to the team, no seat,
expired, refused by policy, unreachable or cancelled. A second callback shows
"already complete"; any other path is 404. The listener closes once
``/result`` is served, or ``RESULT_GRACE`` after the outcome if the browser
never asks.

Standard library only; no bpy. The caller opens the browser.
"""

import base64
import hashlib
import secrets
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlsplit

from .team_pages import Pages

CLIENT_ID = "angelo-desktop"
SCOPE = "api generate offline_access"
CALLBACK_PATH = "/callback"
TIMEOUT = 600.0          # seconds the browser has to answer
CONNECTION_TIMEOUT = 10  # seconds one connection may take to send its request

OUTCOME_WAIT = 25.0      # seconds /result waits for the token exchange
RESULT_GRACE = 30.0      # seconds the listener stays for a browser that hasn't asked for /result


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair():
    """``(verifier, challenge)``: a 43-character verifier from 32 random bytes and its S256 challenge."""
    verifier = _b64url(secrets.token_bytes(32))
    return verifier, challenge_for(verifier)


def challenge_for(verifier):
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state():
    return _b64url(secrets.token_bytes(16))


def device_name(hostname=None):
    """How the console lists this computer ("Allow Angelo on <device>?"); no personal data beyond the host name."""
    name = (hostname if hostname is not None else socket.gethostname()).strip()
    name = name.removesuffix(".local").removesuffix(".lan")
    printable = "".join(c for c in name if c.isprintable())[:64]
    return printable or "this computer"


def authorize_url(console_url, redirect_uri, challenge, state, device, org_hint="", login_hint=""):
    """The console's allow screen. ``org_hint`` (the managed policy's Organization) preselects it;
    ``login_hint`` (the email of a sign-in the console ended) fills in the sign-in's email step. A console
    that doesn't know them, or finds one malformed, ignores it."""
    values = {"client_id": CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
              "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
              "scope": SCOPE, "device_name": device}
    if org_hint:
        values["org_hint"] = org_hint
    if login_hint:
        values["login_hint"] = login_hint
    return console_url + "/oauth/authorize?" + urlencode(values)


class _Server(socketserver.ThreadingTCPServer):
    # TCPServer, not HTTPServer: HTTPServer.server_bind resolves the host's
    # fully qualified name, which can stall on a misconfigured network.
    address_family = socket.AF_INET
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, listener):
        self.listener = listener
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    timeout = CONNECTION_TIMEOUT
    server_version = "Angelo"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's signature.
        pass  # Never log: the query carries the authorization code.

    def _page(self, kind, **fields):
        status, headers, data = self.server.listener.pages.render(kind, **fields)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _see_other(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name.
        listener, path = self.server.listener, urlsplit(self.path).path
        if path == CALLBACK_PATH:
            values = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=16)
            verdict = listener._accept({key: items[0] for key, items in values.items() if items})
            self._see_other({"accepted": "/result", "repeat": "/already"}.get(verdict, "/mismatch"))
            if verdict == "accepted":
                listener._answered()
        elif path == "/result" and listener._accepted.is_set():
            kind, fields = listener._outcome_for_page()
            self._page(kind, **fields)
            listener._served()
        elif path == "/mismatch":
            self._page("mismatch")
        elif path == "/already" and listener._accepted.is_set():
            self._page("already_used")
        else:
            self._page("not_found")

    def _refuse(self):
        self.send_error(405)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _refuse


class LoopbackListener:
    """The sign-in callback on 127.0.0.1, alive from ``start`` until the browser saw the outcome.

    ``wait`` returns ``{"code": …}`` or ``{"error": …}`` from the first
    callback whose state (and issuer) match, ``{"error": "timeout"}`` or
    ``{"error": "cancelled"}``; the caller then reports what happened with
    ``finish(kind, **fields)`` (a ``team_pages`` state), which the browser's
    ``/result`` page shows. ``pages`` renders them (translated by the app).
    """

    def __init__(self, state, timeout=TIMEOUT, issuer="", pages=None):
        self.state, self.timeout, self.issuer = state, timeout, issuer
        self.pages = pages or Pages(issuer or "")
        self._result = None
        self._outcome = None
        self._done = threading.Event()       # An answer, a cancel or the timeout.
        self._accepted = threading.Event()   # A matching callback arrived.
        self._finished = threading.Event()   # ``finish`` reported the outcome.
        self._lock = threading.Lock()
        self._server = None
        self._port = 0
        self._thread = None
        self._stopped = threading.Event()  # Set once the socket is closed.

    def start(self):
        self._server = _Server(self)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                        daemon=True, name="AngeloSignInCallback")
        self._thread.start()
        return self

    @property
    def port(self):
        return self._port

    @property
    def redirect_uri(self):
        return "http://127.0.0.1:%d%s" % (self.port, CALLBACK_PATH)

    @property
    def listening(self):
        return self._server is not None

    def _accept(self, values):
        """``"accepted"`` for the first matching callback, ``"repeat"`` for a later one, else ``"mismatch"``."""
        if not secrets.compare_digest(str(values.get("state", "")), self.state):
            return "mismatch"
        if self.issuer and values.get("iss") != self.issuer:
            return "mismatch"  # RFC 9207: an answer from another authorization server (mix-up).
        with self._lock:
            if self._result is not None:
                return "repeat" if self._accepted.is_set() else "mismatch"
            if values.get("error"):
                self._result = {"error": str(values["error"])[:64]}
            elif values.get("code"):
                self._result = {"code": str(values["code"])[:2048]}
            else:
                return "mismatch"
        self._accepted.set()
        return "accepted"

    def _answered(self):
        self._done.set()
        # Stay for the browser's /result, but never longer than the grace after the outcome.
        threading.Thread(target=self._close_after_outcome, daemon=True).start()

    def _close_after_outcome(self):
        self._finished.wait(OUTCOME_WAIT + RESULT_GRACE)
        self._stopped.wait(RESULT_GRACE)
        self.close()

    def _outcome_for_page(self):
        if not self._finished.wait(OUTCOME_WAIT):
            return "waiting", {}
        with self._lock:
            return self._outcome

    def _served(self):
        threading.Thread(target=self.close, daemon=True).start()

    def finish(self, kind, **fields):
        """What the browser's ``/result`` page says (a ``team_pages`` state and its fields)."""
        with self._lock:
            if self._outcome is None:
                self._outcome = (kind, fields)
        self._finished.set()

    def wait(self, timeout=None):
        """Block until an answer, ``close`` or the timeout. Without an answer the listener is closed."""
        if not self._done.wait(self.timeout if timeout is None else timeout):
            with self._lock:
                if self._result is None:
                    self._result = {"error": "timeout"}
        if not self._accepted.is_set():
            self.close()
        with self._lock:
            return dict(self._result or {"error": "cancelled"})

    def close(self):
        """Stop listening now. Idempotent; an unanswered sign-in reads as cancelled."""
        with self._lock:
            server, self._server = self._server, None
            if self._result is None and server is not None and not self._done.is_set():
                self._result = {"error": "cancelled"}
            if self._outcome is None and self._accepted.is_set():
                self._outcome = ("waiting", {})
        self._done.set()
        self._finished.set()
        if server is not None:
            server.shutdown()
            server.server_close()
            self._stopped.set()
        elif self._port:
            self._stopped.wait(5)  # Another thread is closing it; return once the port is free.
