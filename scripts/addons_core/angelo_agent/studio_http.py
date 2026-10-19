# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's HTTPS transport to admitted provider hosts and the Angelo gateway.

Split from ``studio_generation`` (which re-exports these names). A request goes
only to an admitted service host (``SERVICE_HOSTS``) or the configured gateway
origin (``team_endpoints``); the socket is pinned to one resolved public
address, so no redirect, DNS rebinding or implicit proxy can receive a
credential. Error bodies are surfaced only for admitted services, sanitized
and bounded. Workers call this; it never touches bpy.
"""

import http.client
import ipaddress
import json
import socket
import uuid
from urllib.parse import urlsplit

from .references import MIME
from .studio_state import Templated
from .ui_text import n_
from .provider_errors import MAX_ERROR_BODY, credential_values, error_detail
from .provider_registry import GENAI, SPECS
from .team_endpoints import PROVIDER_NAMES, gateway_for_url, gateway_provider
from .team_errors import TeamError, gateway_error
from .team_http import tls_context

MAX_JSON = 64 * 1024 * 1024
SERVICE_HOSTS = frozenset(urlsplit(s["base_url"]).hostname for s in SPECS.values() if s.get("base_url"))
# Display names for errors; generation services win over coding services on a shared host.
SERVICE_NAMES = {urlsplit(s["base_url"]).hostname: s["name"] for s in sorted(
    (s for s in SPECS.values() if s.get("base_url")), key=lambda s: s["group"] == GENAI)}


class GenerationError(Templated, RuntimeError):
    """``str(error)`` is the English sentence; ``template``/``fields`` let the editor show it translated."""


class GenerationCancelled(GenerationError):
    pass


class GatewayRejection(GenerationError):
    """The Angelo gateway itself refused a team request (``team_errors.GATEWAY_CODES``)."""

    def __init__(self, error):
        super().__init__(error.message)
        self.code, self.status, self.retry_after = error.code, error.status, error.retry_after


def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise GenerationError(n_("Use a public HTTPS URL without login information"))
    if len(url) > 8192 or any(ord(c) < 32 for c in url):
        raise GenerationError(n_("Invalid asset URL"))
    return parsed


def public_addresses(host):
    try:
        addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    except OSError:
        raise GenerationError(n_("Couldn't resolve the provider or asset host")) from None
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise GenerationError(n_("Local/private network URLs are not allowed"))
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=tls_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def service_name(url):
    provider = gateway_provider(url)
    if provider:
        return PROVIDER_NAMES[provider] + " (through your Angelo team)"
    return SERVICE_NAMES.get(urlsplit(url).hostname, "The provider")


def _sentence(detail):
    return detail if detail.endswith((".", "!", "?", "…")) else detail + "."


def rejection(name, status, detail, team=False):
    """The user-facing message for a non-2xx response; ``detail`` is already sanitized."""
    known = {401: n_("API key rejected; reconnect in AI Providers"), 403: n_("This key doesn't have access to the model"),
             402: n_("The provider requires credits or billing"), 429: n_("Rate limit reached; wait before trying again")}
    if team:  # The provider refused the organization's own key, which an admin manages.
        known.update({401: n_("The provider rejected your team's key; ask a team admin"),
                      403: n_("Your team's key doesn't have access to this model; ask a team admin"),
                      402: n_("Your team's provider account needs credits or billing; ask a team admin")})
    if status in known:
        return known[status] + (f". {name} said: {detail}" if detail else "")
    verb = "returned a server error" if status >= 500 else "rejected the request"
    if detail:
        return f"{name} {verb} (HTTP {status}): {_sentence(detail)} Not retried"
    return f"{name} {verb} (HTTP {status}); not retried"


def _error_body(response):
    # Its own guard: a failed read must not hide the HTTP status itself.
    try:
        return response.read(MAX_ERROR_BODY + 1)
    except (OSError, http.client.HTTPException, ValueError):
        return b""


def request_bytes(url, *, body=None, headers=None, limit=MAX_JSON, timeout=180, service=False):
    # A team request goes to the configured gateway origin (team_endpoints);
    # plain http only when the person configured a loopback development gateway.
    gateway = gateway_for_url(url) if service else None
    if gateway is not None and gateway.loopback:
        parsed = urlsplit(url)
        connection = http.client.HTTPConnection(gateway.host, gateway.port, timeout=timeout)
    else:
        parsed = public_url(url)
        if service and gateway is None and parsed.hostname not in SERVICE_HOSTS:
            raise GenerationError(n_("This is not an admitted provider endpoint"))
        # Resolve once and pin the actual socket, preventing DNS rebinding to a
        # local service. No redirect or implicit proxy can receive an API key.
        address = public_addresses(parsed.hostname)[0]
        connection = _PinnedHTTPS(parsed.hostname, address, timeout)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("POST" if body is not None else "GET", path, body=body, headers=headers or {})
        response = connection.getresponse()
        if response.status != 200 and response.status != 201:
            # Only an admitted service's structured error is surfaced, sanitized
            # and bounded; asset/CDN bodies and request data are never shown.
            if not service:
                raise GenerationError(rejection("The download host", response.status, ""))
            data = _error_body(response)
            refused = gateway_error(response.status, data, response.getheader("Retry-After")) if gateway else None
            if refused is not None:
                raise GatewayRejection(refused)
            detail = error_detail(data, credential_values(headers), response.status)
            raise GenerationError(rejection(service_name(url), response.status, detail, team=gateway is not None))
        length = response.getheader("Content-Length")
        if length and int(length) > limit:
            raise GenerationError(n_("The provider returned a file larger than the download limit"))
        data = response.read(limit + 1)
        if len(data) > limit:
            raise GenerationError(n_("The provider response exceeded the download limit"))
        return data
    except (OSError, http.client.HTTPException, ValueError):
        raise GenerationError(n_("The request couldn't complete securely. It wasn't retried; check provider history before generating again")) from None
    finally:
        connection.close()


def service_json(url, headers, body=None, content_type="application/json", transport=request_bytes):
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = dict(headers, Accept="application/json", **{"User-Agent": "Angelo-MediaStudio/0.1"})
    if body is not None:
        headers["Content-Type"] = content_type
    data = transport(url, body=body, headers=headers, service=True)
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise GenerationError(n_("The provider returned an invalid response")) from None
    if value.get("error") or value.get("code", 0) != 0:
        # Raw service errors can include keys, private URLs or request data;
        # only the sanitized, bounded structured message is shown.
        detail = error_detail(value, credential_values(headers))
        if detail:
            raise GenerationError(f"{service_name(url)} rejected the request: {_sentence(detail)} Not retried")
        raise GenerationError(n_("The provider rejected the request; check model access, credits and input settings"))
    return value


def _headers(binding, stale=None):
    try:
        return binding.request_headers(stale)
    except TeamError as error:  # Signed out, no seat or the console unreachable: nothing was sent.
        raise GenerationError(error.message) from None


def binding_json(binding, url, body=None, content_type="application/json", transport=request_bytes):
    """One service request with current credentials.

    The gateway's UNAUTHENTICATED is decided before anything reaches the
    provider, so that request alone is sent once more with a renewed token; a
    second refusal asks the person to sign in again.
    """
    headers = _headers(binding)
    try:
        return service_json(url, headers, body, content_type, transport)
    except GatewayRejection as refused:
        if refused.code != "UNAUTHENTICATED" or not binding.team:
            raise
    return service_json(url, _headers(binding, headers), body, content_type, transport)


def multipart(fields, files):
    boundary = "Angelo" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                      str(value).encode("utf-8"), b"\r\n"])
    for name, data, mime in files:
        extension = {v: k for k, v in MIME.items()}[mime]
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="reference{extension}"\r\nContent-Type: {mime}\r\n\r\n'.encode(), data, b"\r\n"])
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary
