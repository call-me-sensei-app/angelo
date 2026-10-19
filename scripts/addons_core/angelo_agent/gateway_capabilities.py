# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the team gateway says it decodes: the request encodings its ``/health`` names.

A gateway that decodes compressed request bodies answers ``GET /health`` with
``"requestEncodings": ["gzip", "zstd"]`` (the gateway's compressed-request
contract). A team Codex mode asks its runtime to compress only
when the gateway serving it names zstd (``team_runtime.request_compression``),
so the app and the gateway can be released in either order: an older gateway
answers without the field, and nothing is compressed.

One unauthenticated request per gateway origin and process, never carrying a
key (``team_http``: verified TLS, no redirects, bounded). A gateway that names
zstd is believed for the process; any other answer (no field, an error status,
no answer within ``TIMEOUT``) for ``RETRY_SECONDS``, then it is asked again.
Concurrent callers share one request.

Standard library only; no bpy. Called on worker threads (a connect).
"""

import json
import threading
import time

from . import team_http
from .team_errors import NotSent, Unanswered

PATH = "/health"
TIMEOUT = 2.0
RETRY_SECONDS = 600
MAX_ENCODINGS = 8

_lock = threading.Lock()
_known = {}  # origin URL -> (frozenset of encodings, monotonic expiry or None for the process)
_probing = {}  # origin URL -> lock held while one caller asks


def request_encodings(origin, *, clock=time.monotonic):
    """The request encodings the gateway at ``origin`` (``team_endpoints.Origin``) decodes; empty when unknown."""
    key = origin.url
    with _lock:
        known = _cached(key, clock)
        if known is not None:
            return known
        probe = _probing.setdefault(key, threading.Lock())
    with probe:
        with _lock:
            known = _cached(key, clock)  # Another caller asked while this one waited.
            if known is not None:
                return known
        encodings = _ask(origin)
        with _lock:
            _known[key] = (encodings, None if "zstd" in encodings else clock() + RETRY_SECONDS)
        return encodings


def accepts(origin, encoding, **options):
    """Whether the gateway at ``origin`` named ``encoding`` among the request encodings it decodes."""
    return encoding in request_encodings(origin, **options)


def reset():
    """Forget every answer (tests; a changed gateway origin needs none: answers are per origin)."""
    with _lock:
        _known.clear()


def _cached(key, clock):
    entry = _known.get(key)
    if entry is None or entry[1] is not None and clock() >= entry[1]:
        return None
    return entry[0]


def _ask(origin):
    try:
        status, _headers, body = team_http.http_transport(
            "GET", origin.join(PATH), headers={"Accept": "application/json", "User-Agent": team_http.USER_AGENT},
            timeout=TIMEOUT)
    except (NotSent, Unanswered, OSError, ValueError):
        return frozenset()
    if status != 200:
        return frozenset()
    try:
        names = json.loads(body).get("requestEncodings")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return frozenset()
    if not isinstance(names, list):
        return frozenset()
    return frozenset(name.strip().lower() for name in names[:MAX_ENCODINGS] if isinstance(name, str) and name.strip())
