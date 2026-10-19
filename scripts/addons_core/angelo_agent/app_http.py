# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The small HTTP client of the update check and the crash report upload.

Both send no credential and no account data, so unlike the team account
(``team_http``, which deliberately avoids proxies because it carries tokens)
this client **follows the system proxy** (environment variables, macOS network
settings, the Windows registry, through ``urllib.request.getproxies`` and its
``no_proxy`` rules) and trusts the same roots as the rest of the app
(``team_http.tls_context``: Angelo's bundled certifi, plus the managed policy's
``ExtraCACertificates``). PAC scripts and proxy authentication schemes beyond
basic are not supported; a network that needs them reads as offline.

Only ``https`` is spoken, or ``http`` to a loopback host for tests and local
development. A GET follows at most 5 redirects, never to a downgraded scheme;
a POST follows none (a body is never replayed). Responses are read in chunks
under a total deadline and a byte limit. Every failure is a ``NetworkError``
with a stable ``kind`` so callers can tell *offline* (quiet, retried later)
from a server that answered with an error.

Standard library only; no bpy. Blocking: call it from a worker thread.
"""

import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import app_version, team_http
from .team_endpoints import is_loopback

OFFLINE, TLS, TIMEOUT, BAD_URL, TOO_LARGE, PROTOCOL = "offline", "tls", "timeout", "bad_url", "too_large", "protocol"
MAX_REDIRECTS = 5


class NetworkError(Exception):
    """``kind`` is one of OFFLINE, TLS, TIMEOUT, BAD_URL, TOO_LARGE, PROTOCOL."""

    def __init__(self, kind, message=""):
        super().__init__(message or kind)
        self.kind = kind


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict   # Lowercase names.
    body: bytes


def user_agent():
    """What the server sees besides the address: the product, its version, the OS family and the CPU."""
    import platform
    system = {"Darwin": "macOS", "Windows": "Windows"}.get(platform.system(), platform.system() or "Unknown")
    return "Angelo/%s (%s; %s)" % (app_version.current(), system, platform.machine() or "unknown")


def check_url(url):
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or not parts.hostname or parts.username or parts.password or len(url) > 2048:
        raise NetworkError(BAD_URL, "Not a usable link")
    if parts.scheme == "https" or (parts.scheme == "http" and is_loopback(parts.hostname)):
        return parts
    raise NetworkError(BAD_URL, "Only https (or http to this computer) is used")


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, follow):
        self.follow = follow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.follow or req.get_method() != "GET":
            return None
        try:
            check_url(newurl)
        except NetworkError:
            return None  # No downgrade and no odd scheme: the 3xx is returned as the answer.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(follow, proxies, context):
    handlers = [urllib.request.ProxyHandler(proxies), _Redirects(follow), urllib.request.HTTPHandler(),
                urllib.request.HTTPSHandler(context=context)]
    return urllib.request.build_opener(*handlers)


def _read(response, limit, deadline):
    chunks, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise NetworkError(TIMEOUT, "The server answered too slowly")
        chunk = response.read(min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise NetworkError(TOO_LARGE, "The answer is larger than %d bytes" % limit)


def _failure(error):
    """The ``NetworkError`` for a transport failure (never for an HTTP status)."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLError):
        return NetworkError(TLS, "The secure connection could not be verified")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return NetworkError(TIMEOUT, "No answer in time")
    if isinstance(error, urllib.error.URLError):
        return NetworkError(OFFLINE, "Could not reach the server")
    return NetworkError(PROTOCOL, "The server's answer could not be read")


def _prepare(method, url, body, headers, proxies, context):
    check_url(url)
    if proxies is None:
        proxies = urllib.request.getproxies()
    if context is None and url.startswith("https:"):
        context = team_http.tls_context()
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=dict({"User-Agent": user_agent()}, **(headers or {})))
    return req, _opener(follow=method == "GET", proxies=proxies, context=context)


def request(method, url, *, body=None, headers=None, timeout=15, limit=256 * 1024, proxies=None, context=None):
    """One request; raises ``NetworkError``. Any HTTP status, including 304 and 4xx/5xx, is a ``Response``."""
    req, opener = _prepare(method, url, body, headers, proxies, context)
    deadline = time.monotonic() + 2 * timeout
    try:
        with opener.open(req, timeout=timeout) as response:
            return Response(response.status, {k.lower(): v for k, v in response.headers.items()},
                            _read(response, limit, deadline))
    except urllib.error.HTTPError as error:  # 304, 4xx, 5xx and an unfollowed 3xx.
        try:
            data = _read(error, limit, deadline)
        except NetworkError:
            data = b""
        finally:
            error.close()
        return Response(error.code, {k.lower(): v for k, v in error.headers.items()}, data)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, OSError, http.client.HTTPException,
            ValueError) as error:
        raise _failure(error) from None


def stream(url, *, headers=None, timeout=30, proxies=None, context=None):
    """A GET whose body the caller reads in chunks (an update download): ``(status, headers, body)``.

    ``body`` has ``read(n)`` and ``close()``; the caller closes it. Same rules as ``request`` (proxy,
    roots, redirects without a downgrade); ``timeout`` applies to each read, and there is no total
    deadline or size limit here: the caller enforces both. Read errors are ``read_failure(error)``.
    """
    req, opener = _prepare("GET", url, None, headers, proxies, context)
    try:
        response = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, error
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, OSError, http.client.HTTPException,
            ValueError) as error:
        raise _failure(error) from None
    return response.status, {k.lower(): v for k, v in response.headers.items()}, response


def read_failure(error):
    """The ``NetworkError`` for an exception raised while reading a ``stream`` body."""
    return _failure(error)
