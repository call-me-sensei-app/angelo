# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Custom model endpoints: an organization's own LLM server, configured once for all its members.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints". An organization
that routes model use through its own server (LiteLLM, Portkey, OpenRouter,
Azure OpenAI, vLLM, Ollama, LM Studio or an in-house proxy) describes it once:
in the Angelo console (the ``customEndpoints`` field of the team's
``providers/available`` answer) or in the machine policy (``CustomEndpoints``,
which wins). Each member then adds only their own key for it in AI Providers.
The app calls the server directly: no Angelo gateway is in the path.

This module parses that description; nothing here trusts its shape. An entry
that isn't usable is left out whole, never half-applied, and the parse reports
that it left something out so the person sees it (``problems``).

* ``baseUrl``: an ``https://`` origin with an optional path prefix, or plain
  ``http://`` for a loopback host only. No user name, password, query or
  fragment, so a credential never rides in the address.
* ``formats``: which request formats the server speaks, each with its path under
  ``baseUrl`` and how it takes the key (``auth``: any ``header`` outside
  ``_RESERVED_HEADERS``, default ``authorization``, and ``scheme`` ``bearer``
  (default) or ``none``, the key alone). Phase 1 runs three:
  ``openai-responses`` (OpenAI's Responses API) and ``gemini`` (Google's
  ``streamGenerateContent``, Angelo's own Gemini wire) on Angelo's Codex runtime,
  and ``anthropic-messages`` (Anthropic's Messages API) on Claude Code. Unknown
  formats are ignored, so a newer console can describe more.
* ``models``: the server's model names (Azure: deployment names), each in one
  format, each optionally mapped to the Angelo model it is (``profile``, an id in
  ``model_profiles`` run by that format's runtime). A model with no usable
  profile is still offered, marked untested.
* ``extraHeaders``: fixed, non-secret headers some servers route by (Portkey's
  ``x-portkey-provider``). A header that carries a secret is the key's header.
* ``credential``: where the key comes from (``CREDENTIALS``). ``member_key``
  (default): each member's own, in their OS key store. ``none``: the server checks
  no key. ``command``: a program that
  prints a short-lived key (``command``: its absolute path and arguments;
  ``refreshSeconds``: how long a key lasts, default 3600), for SSO-issued tokens;
  **machine policy only** (``endpoints(..., source="policy")``): the console must
  never make members' computers run a program, so a console entry with it is left
  out. An organization's own secrets never reach the app (maintainer, 2026-11-12):
  there is no shared key, and any other value (``org_key`` included) leaves the
  entry out.

Standard library only; no bpy.
"""

import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Wire format id → (the runtime's wire_api, the personal mode whose request rules it follows, a short label). The
# personal mode's registry entry names the runtime (codex or claude).
FORMATS = {
    "openai-responses": ("responses", "codex_api", "OpenAI"),
    "gemini": ("gemini", "gemini_api", "Gemini"),
    "anthropic-messages": ("messages", "claude_api", "Anthropic"),
}
RUNTIMES = {"openai-responses": "codex", "gemini": "codex", "anthropic-messages": "claude"}
SCHEMES = ("bearer", "none")   # How the key is written into its header: "Bearer <key>", or the key alone.
CREDENTIALS = ("member_key", "none", "command")
POLICY_ONLY_CREDENTIALS = frozenset({"command"})   # A program a member's computer runs: the machine policy's call.
MAX_COMMAND_ARGS = 16
REFRESH_SECONDS = (60, 86400, 3600)   # A command's key lifetime: minimum, maximum, default.
MAX_ENDPOINTS = 8
MAX_MODELS = 50
MAX_EXTRA_HEADERS = 8

_ID = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_HEADER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
# A server's own model name, whatever its shape: vLLM's served path ("/data/models/x"), Ollama's "name:tag",
# OpenRouter's "vendor/model", Portkey's "@provider/model", Azure's deployment names. It is only ever sent as data
# (JSON in a Codex -c value and in request bodies, Claude Code's --model argument, a quoted URL segment in the
# check), never as a TOML key or a file name, so any visible ASCII is admitted: no space, control or non-ASCII.
_MODEL = re.compile(r"[\x21-\x7e]{1,200}")
_PATH = re.compile(r"(/[A-Za-z0-9._~@+-]+)*")
_TEXT_UNSAFE = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")
# Headers the runtime or HTTP itself owns; an endpoint may not set or override them.
_RESERVED_HEADERS = frozenset({"host", "content-length", "content-type", "content-encoding", "transfer-encoding",
                               "connection", "keep-alive", "upgrade", "te", "trailer", "accept", "accept-encoding",
                               "cookie", "proxy-authorization", "user-agent", "session-id", "x-request-id"})
# Headers that carry a credential on one of the supported servers: never a fixed (non-secret) extra header, whatever
# the formats' own key headers are. The console refuses the same list.
CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key",
                                "x-portkey-api-key", "x-portkey-virtual-key", "x-litellm-api-key",
                                "ocp-apim-subscription-key", "x-auth-token", "x-access-token", "cookie", "set-cookie",
                                "apikey", "x-functions-key"})   # Kong and Supabase gateways; Azure Functions fronts.
# The class beyond that list: a name that says it carries a secret, or a value written like a credential.
# Words, not substrings: Portkey routes by x-portkey-provider, whose "portkey" holds "key". The console mirrors it.
_CREDENTIAL_WORD = re.compile(r"token|secret|auth|passw|credential")
_KEY_WORD = re.compile(r"(api|access|private|secret|auth|subscription|functions)?keys?")
_CREDENTIAL_VALUE = re.compile(r"(?i)(bearer|basic)\s")


def credential_like(name):
    """Whether a lower-case header name says it carries a secret: a ``-`` part holding a credential word, or one
    that is ``key``/``keys`` alone or after one of ``_KEY_WORD``'s prefixes."""
    return any(_CREDENTIAL_WORD.search(part) or _KEY_WORD.fullmatch(part) for part in name.split("-"))
_HEADER_VALUE = re.compile(r"[\x20-\x7e]{1,256}")   # Printable ASCII: what every runtime's HTTP stack sends.


@dataclass(frozen=True)
class Format:
    path: str            # Under the endpoint's base URL, "" or "/…" without a trailing slash.
    header: str          # The header that carries the member's key, lower case.
    scheme: str          # "bearer" or "none".


@dataclass(frozen=True)
class Model:
    id: str              # The server's own model name, sent as is.
    format: str
    profile: str | None  # The Angelo model it is (a ``model_profiles.MODELS`` id), or None: untested.


@dataclass(frozen=True)
class Endpoint:
    id: str              # 32 lower-case hex digits (the console's UUID without dashes).
    name: str
    base_url: str        # Scheme, host, port and path prefix, without a trailing slash.
    server: str
    formats: tuple       # ((format id, Format), …) in FORMATS order.
    extra_headers: tuple  # ((name, value), …)
    credential: str
    models: tuple        # (Model, …)
    command: tuple = ()  # A ``command`` credential's absolute path and arguments.
    refresh_seconds: int = 0  # How long a ``command`` credential's key lasts.

    @property
    def origin(self):
        """Scheme, host and port: what a key is bound to when it is kept (``endpoint_ui``)."""
        parts = urlsplit(self.base_url)
        return "%s://%s:%d" % (parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))

    @property
    def host(self):
        return urlsplit(self.base_url).hostname

    def format(self, name):
        return dict(self.formats).get(name)

    def url(self, name):
        """The base URL the runtime gets for format ``name``."""
        return self.base_url + self.format(name).path

    def models_in(self, name):
        return tuple(m for m in self.models if m.format == name)


def _text(value, limit):
    text = value.strip() if isinstance(value, str) else ""
    return text if 0 < len(text) <= limit and not _TEXT_UNSAFE.search(text) else None


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")   # An LDH label: what every runtime's resolver takes.


def hostname(host):
    """Whether ``host`` (lower case, as ``urlsplit`` gives it) names one server for every resolver: an IP literal in
    canonical form (a dotted quad of four decimal parts 0-255 without leading zeros, or IPv6 without a zone ID) or a
    DNS name of LDH labels (an internationalized name in its punycode ``xn--`` form; no underscore) whose last label
    does not begin with a digit, at most 253 characters and without a trailing dot. A last label that begins with a
    digit is one some resolver reads as a number (127.1, 0x7f.1, 010.0.0.1, example.1e3, 2130706433) and rewrites to
    another address; no top-level domain begins with one. A zone ID (``fe80::1%eth0``) names an interface and its
    text can carry anything. Nothing else reaches a runtime's ``NO_PROXY`` or its connection (the console's schema
    holds the same rule)."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return getattr(address, "scope_id", None) is None
    labels = host.split(".")
    if labels[-1][:1].isdigit():
        return False
    return (host.isascii() and 0 < len(host) <= 253 and not host.endswith(".")
            and all(_LABEL.fullmatch(label) for label in labels))


def base_url(value):
    """``value`` as a usable base URL (no trailing slash), or None."""
    text = _text(value, 2048)
    if not text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    # "?" and "#" even when empty, and port 0 (dropped by the formatting below, so another port would be used).
    if (not host or not hostname(host) or parts.username is not None or parts.password is not None or "?" in text
            or "#" in text or port == 0 or "@" in parts.netloc):
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and _loopback(host)):
        return None
    path = parts.path.rstrip("/")
    if not _PATH.fullmatch(path) or "/../" in path + "/" or "/./" in path + "/":
        return None
    shown = "[%s]" % host if ":" in host else host
    return "%s://%s%s%s" % (parts.scheme, shown, ":%d" % port if port else "", path)


def _path(value):
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        return None
    path = value.strip().rstrip("/")
    return path if path == "" or (path.startswith("/") and _PATH.fullmatch(path) and "/../" not in path + "/"
                                  and "/./" not in path + "/") else None


def _format(value):
    if not isinstance(value, dict):
        return None
    auth = value.get("auth") if isinstance(value.get("auth"), dict) else {}
    header = auth.get("header", "authorization")
    scheme = auth.get("scheme", "bearer")
    path = _path(value.get("path"))
    if (path is None or not isinstance(header, str) or not _HEADER.fullmatch(header.lower())
            or header.lower() in _RESERVED_HEADERS or scheme not in SCHEMES):
        return None
    return Format(path, header.lower(), scheme)


def _headers(value, auth_headers):
    if value is None:
        return ()
    if not isinstance(value, dict) or len(value) > MAX_EXTRA_HEADERS:
        return None
    output = []
    for name, text in sorted(value.items()):
        name = name.lower() if isinstance(name, str) else ""
        text = text.strip() if isinstance(text, str) else ""
        if (not _HEADER.fullmatch(name) or name in _RESERVED_HEADERS or name in CREDENTIAL_HEADERS
                or credential_like(name) or name in auth_headers or not _HEADER_VALUE.fullmatch(text)
                or _CREDENTIAL_VALUE.match(text)):
            return None
        output.append((name, text))
    return tuple(output)


def model_name(value):
    """Whether ``value`` is a usable model name: visible ASCII (``_MODEL``) that also stays one URL path segment's
    worth of text where a runtime writes it into a path unencoded (the Gemini wire's
    ``models/{model}:streamGenerateContent``): no ``?``, ``#``, ``%`` or ``\\``, and no ``.`` or ``..`` segment.
    One rule for every format, so a name never depends on which format carries it."""
    if not _MODEL.fullmatch(value) or any(c in value for c in "?#%\\"):
        return False
    segments = value.split("/")
    # No "." or ".." segment, and no empty one ("//"); a single leading "/" (vLLM's served path) is allowed.
    return not any(segment in (".", "..") for segment in segments) and "" not in segments[1:]


def _profile(value, format_id):
    from .model_profiles import MODELS   # Pure data; imported late so this module stays import-light.
    if not isinstance(value, str) or value not in MODELS or MODELS[value].get("runtime") != RUNTIMES[format_id]:
        return None   # Only a model the format's own runtime runs (a Claude model for anthropic-messages).
    return value


def _command(value):
    """A ``command`` credential's argv: an absolute program path and plain arguments, or None."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_COMMAND_ARGS:
        return None
    parts = [_text(part, 1024) for part in value]
    if any(part is None for part in parts) or not (parts[0].startswith("/") or re.match(r"[A-Za-z]:\\", parts[0])):
        return None
    return tuple(parts)


def _refresh(value):
    low, high, default = REFRESH_SECONDS
    if value is None:
        return default
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def endpoint(value, source="console"):
    """One endpoint from its JSON object, or None when any part of it is unusable. ``source``: "console" or
    "policy"; only the policy may name a ``command`` credential."""
    if not isinstance(value, dict):
        return None
    identity = value.get("id")
    name = _text(value.get("name"), 64)
    url = base_url(value.get("baseUrl"))
    raw_formats = value.get("formats")
    credential = value.get("credential", "member_key")
    if (not isinstance(identity, str) or not _ID.fullmatch(identity) or not name or not url
            or not isinstance(raw_formats, dict) or credential not in CREDENTIALS
            or (credential in POLICY_ONLY_CREDENTIALS and source != "policy")):
        return None
    command, refresh = (), 0
    if credential == "command":
        command, refresh = _command(value.get("command")), _refresh(value.get("refreshSeconds"))
        if command is None or refresh is None:
            return None
    formats = []
    for key in FORMATS:   # Unknown formats are skipped: a newer console may describe more.
        if key in raw_formats:
            parsed = _format(raw_formats[key])
            if parsed is None:
                return None
            formats.append((key, parsed))
    if not formats:
        return None
    extra = _headers(value.get("extraHeaders"), {f.header for _, f in formats})
    raw_models = value.get("models")
    if extra is None or not isinstance(raw_models, list) or len(raw_models) > MAX_MODELS:
        return None
    known, models, seen = dict(formats), [], set()
    for raw in raw_models:
        if not isinstance(raw, dict) or not isinstance(raw.get("format"), str):
            return None
        if raw["format"] not in FORMATS:
            continue   # A model in a format this version doesn't run.
        model_id = raw.get("id")
        if (raw["format"] not in known or not isinstance(model_id, str) or not model_name(model_id)
                or (raw["format"] == "gemini" and model_id.startswith("/"))   # An empty segment in its URL path.
                or (raw["format"], model_id) in seen):
            return None
        seen.add((raw["format"], model_id))
        models.append(Model(model_id, raw["format"], _profile(raw.get("profile"), raw["format"])))
    if not models:
        return None
    server = value.get("server") if isinstance(value.get("server"), str) and _HEADER.fullmatch(value["server"]) else "other"
    return Endpoint(identity.replace("-", "").lower(), name, url, server, tuple(formats), extra, credential,
                    tuple(models), command, refresh)


def endpoints(value, source="console"):
    """``(endpoints, left_out)`` from a JSON list (or its text): the usable ones, and how many were not.
    ``source`` is passed to ``endpoint`` (only "policy" admits a ``command`` credential).

    ``left_out`` counts entries dropped as unusable or beyond ``MAX_ENDPOINTS``,
    and repeated ids (the first one is kept). A value that isn't a list at all
    gives ``((), 1)``.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return (), 1
    if not isinstance(value, list):
        return (), 1
    output, seen, left_out = [], set(), 0
    for raw in value:
        parsed = endpoint(raw, source)
        if parsed is None or parsed.id in seen or len(output) >= MAX_ENDPOINTS:
            left_out += 1
            continue
        seen.add(parsed.id)
        output.append(parsed)
    return tuple(output), left_out
