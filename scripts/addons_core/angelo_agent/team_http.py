# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account's HTTP client for the Angelo console.

One request per connection through ``http.client``: verified TLS for https,
plain http only to a loopback host (a configured development console), no
redirects (a 3xx is an answer, never followed, so a token can't be forwarded),
bounded responses and timeouts. Failures say whether the request certainly
never reached the server (``NotSent``: DNS, refused connection, TLS handshake)
or might have (``Unanswered``), because a refresh token may be presented only
once. Proxies are not used, as for Media Studio's provider calls.

Trust (``tls_context``): Python's default roots, which inside Angelo are the
bundled certifi file Blender names in ``SSL_CERT_FILE`` (Blender's own Python
has no other roots on macOS), or certifi directly when no root is loaded (a
script outside the app), plus the managed policy's ``ExtraCACertificates``
(an organization's TLS-inspection root), which is added, never a replacement.
Whatever root vouched for it, a chain holding a distrusted key (``tls_distrust``:
compromised CA keys, withdrawn roots) fails as a certificate verification error.

Standard library only; no bpy. Called only on worker threads.
"""

import http.client
import json
import ssl
from urllib.parse import urlencode, urlsplit

from . import managed_policy, tls_distrust
from .team_endpoints import is_loopback
from .team_errors import NotSent, Unanswered

TIMEOUT = 20
MAX_RESPONSE = 1024 * 1024
USER_AGENT = "Angelo-Desktop/0.1"


def tls_context(policy=None):
    """A verifying client context: default roots (certifi when there are none) plus the policy's extra roots,
    refusing any chain that holds a distrusted key."""
    context = tls_distrust.checked_context(ssl.create_default_context())
    if not context.cert_store_stats().get("x509_ca"):
        try:
            import certifi
            context.load_verify_locations(cafile=certifi.where())
        except (ImportError, OSError, ssl.SSLError):
            pass  # The handshake then fails as NotSent, with the same message as an unreachable console.
    extra = (policy or managed_policy.current()).extra_ca_certificates
    if extra:
        try:
            context.load_verify_locations(cafile=extra)
        except (OSError, ssl.SSLError):
            pass  # Reported by managed_policy (problems) when the file was unusable at startup.
    return context


def http_transport(method, url, headers=None, body=None, timeout=TIMEOUT):
    """``(status, headers with lowercase names, body bytes)`` of one request."""
    parsed = urlsplit(url)
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout,
                                                 context=tls_context())
    elif parsed.scheme == "http" and is_loopback(parsed.hostname or ""):
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    else:
        raise NotSent("Only https, or http to a loopback development console, is used")
    try:
        try:
            connection.connect()
        except (OSError, ValueError):
            raise NotSent("Couldn't connect") from None
        try:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            connection.request(method, path, body=body, headers=dict(headers or {}, **{"User-Agent": USER_AGENT}))
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE + 1)
        except (OSError, ValueError, http.client.HTTPException):
            raise Unanswered("No complete answer") from None
        if len(data) > MAX_RESPONSE:
            raise Unanswered("Oversized answer")
        return response.status, {name.lower(): value for name, value in response.getheaders()}, data
    finally:
        connection.close()


def form_body(values):
    return urlencode(values).encode("ascii"), {"Content-Type": "application/x-www-form-urlencoded",
                                              "Accept": "application/json"}


def json_object(data):
    """A JSON object from a response body, or None."""
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None
