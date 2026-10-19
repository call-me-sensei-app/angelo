# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo console and gateway origins of the team account.

The console (``ANGELO_CONSOLE_URL``, default ``https://console.angelo.studio``)
is the OAuth server and team API. The gateway (``ANGELO_GATEWAY_URL``, default
``https://gateway.angelo.studio``) applies the organization's own provider keys
to its members' model and generation calls. Both are read only here, every
time they are needed, so a test or a development shell can point the app at a
local console. A managed policy's ``ConsoleURL`` / ``GatewayURL``
(``managed_policy``) wins over the environment, and an unusable managed value
stops team sign-in rather than falling back to the default.

An origin is ``https://<host>`` on the default port, or plain ``http://`` only
for an explicitly configured loopback host (``127.0.0.1``, ``::1`` or
``localhost``, any port) for development. Anything else (a path, a query,
login information, plain http to another host) is refused with a message that
names the variable, so a mistyped origin can never receive a token.

Standard library only; no bpy.
"""

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import managed_policy, product_env

CONSOLE_VARIABLE = "CONSOLE_URL"
GATEWAY_VARIABLE = "GATEWAY_URL"
DEFAULT_CONSOLE = "https://console.angelo.studio"
DEFAULT_GATEWAY = "https://gateway.angelo.studio"
LOOPBACK_NAMES = frozenset({"localhost"})

# Gateway provider ids (contract 2026-10-08) and how messages name them.
PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google Gemini", "xai": "xAI",
                  "minimax": "MiniMax", "moonshot": "Kimi (Moonshot)", "byteplus": "BytePlus",
                  "tripo": "Tripo", "meshy": "Meshy"}


def is_loopback(host):
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Origin:
    """A validated scheme, host and port. ``url`` has no trailing slash."""

    scheme: str
    host: str
    port: int
    variable: str = ""

    @property
    def loopback(self):
        return is_loopback(self.host)

    @property
    def url(self):
        host = "[%s]" % self.host if ":" in self.host else self.host
        default = 443 if self.scheme == "https" else 80
        return "%s://%s%s" % (self.scheme, host, "" if self.port == default else ":%d" % self.port)

    def join(self, path):
        if not path.startswith("/"):
            raise ValueError("An Angelo service path must start with /")
        return self.url + path

    def owns(self, url):
        """Whether ``url`` is on exactly this origin (scheme, host and port)."""
        try:
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return False
        return (parsed.scheme, (parsed.hostname or "").lower(), port) == (self.scheme, self.host, self.port)


def parse_origin(value, variable):
    """An ``Origin`` from ``value``; ``ValueError`` naming ``variable`` when it is not admitted."""
    problem = "%s must be an https:// origin such as %s (http:// only for 127.0.0.1, ::1 or localhost)" % (
        variable, DEFAULT_CONSOLE if variable.endswith(CONSOLE_VARIABLE) else DEFAULT_GATEWAY)
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port
    except ValueError:
        raise ValueError(problem) from None
    host = (parsed.hostname or "").lower()
    if (not host or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment or any(ord(c) < 33 for c in str(value).strip())):
        raise ValueError(problem)
    if parsed.scheme == "https" and port in {None, 443}:
        return Origin("https", host, 443, variable)
    if parsed.scheme == "http" and is_loopback(host):
        return Origin("http", host, port or 80, variable)
    raise ValueError(problem)


def _configured(name, default, environ, managed, policy_key):
    if managed == managed_policy.INVALID:
        raise ValueError(managed_policy.sign_in_problem(managed_policy.current()) or policy_key)
    if managed:
        return parse_origin(managed, policy_key)
    value, variable = product_env.lookup(name, environ)
    if value is None:
        return parse_origin(default, product_env.PREFIX + name)
    return parse_origin(value, variable)


def console_origin(environ=None, policy=None):
    managed = (policy or managed_policy.current()).console_url
    return _configured(CONSOLE_VARIABLE, DEFAULT_CONSOLE, environ, managed, "ConsoleURL")


def gateway_origin(environ=None, policy=None):
    managed = (policy or managed_policy.current()).gateway_url
    return _configured(GATEWAY_VARIABLE, DEFAULT_GATEWAY, environ, managed, "GatewayURL")


def gateway_for_url(url, environ=None):
    """The configured gateway origin when ``url`` is on it, else None (also when it is misconfigured)."""
    try:
        gateway = gateway_origin(environ)
    except ValueError:
        return None
    return gateway if gateway.owns(url) else None


def gateway_provider(url, environ=None):
    """The gateway provider id in a gateway URL (``/v1/<provider>/...``), or ""."""
    if gateway_for_url(url, environ) is None:
        return ""
    parts = urlsplit(url).path.split("/")
    return parts[2] if len(parts) > 2 and parts[1] == "v1" and parts[2] in PROVIDER_NAMES else ""
