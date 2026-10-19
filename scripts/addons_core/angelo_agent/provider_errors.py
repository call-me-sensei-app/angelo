# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, sanitized provider error details for user-visible generation failures.

A provider error body is untrusted text. It can echo request data, signed URLs
or credentials. Only structured JSON fields are read (``error.status``/``type``/
``code`` and ``error.message``, or a top-level ``code``/``message``). Exact
credential values, key- and token-like strings, URL login/query/fragment parts
and control characters are removed, and the result is truncated. Raw bodies,
request bodies and keys are never returned, logged or persisted.
"""

import json
import re
import unicodedata
from urllib.parse import urlsplit

MAX_ERROR_BODY = 16 * 1024  # Bytes read from a non-2xx response; error JSON is small.
MAX_DETAIL = 300  # Characters kept after sanitizing.
REDACTED = "[REDACTED]"

_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),  # Google API key.
    # OpenAI-, Tripo-, Meshy-, xAI-style keys and Angelo team grant keys.
    re.compile(r"\b(?:sk|tsk|msy|xai|rk|pk|agk)[-_][A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    # Header- or parameter-like assignments: "x-goog-api-key: ...", "key=...".
    re.compile(r"\b(?:x-goog-api-key|authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|"
               r"token|secret|password|signature|sig|key)\s*[:=]\s*\S+", re.IGNORECASE),
)
_OPAQUE = re.compile(r"(?<![A-Za-z0-9_+=-])[A-Za-z0-9_+=-]{24,}")
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})


def _opaque(match):
    """Long random-looking strings (keys, JWT parts, UUID-style keys); keeps model names and words."""
    text = match.group(0)
    digits = sum(c.isdigit() for c in text)
    mixed = any(c.isupper() for c in text) and any(c.islower() for c in text) and digits
    return REDACTED if mixed or digits >= 4 else text


def credential_values(headers):
    """Values of authentication headers, for exact-match redaction."""
    values = []
    for name, value in (headers or {}).items():
        if not isinstance(value, str) or not any(part in name.lower() for part in ("auth", "key", "token")):
            continue
        values.append(value)
        if value.lower().startswith("bearer "):
            values.append(value[7:].strip())
    return tuple(v for v in values if len(v) >= 8)


def _safe_url(match):
    try:
        parts = urlsplit(match.group(0))
        host = parts.hostname or ""
        port = ":%d" % parts.port if parts.port else ""
    except ValueError:
        return REDACTED
    hidden = "?" + REDACTED if parts.query or parts.fragment else ""
    return "%s://%s%s%s%s" % (parts.scheme.lower(), host, port, parts.path, hidden)


def sanitize(text, secrets=(), limit=MAX_DETAIL):
    """Untrusted provider text made safe to show: one line, redacted, at most ``limit`` characters."""
    if not isinstance(text, str):
        return ""
    text = text[:MAX_ERROR_BODY]
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = "".join(" " if unicodedata.category(c) in _UNSAFE_CATEGORIES else c for c in text)
    text = _URL.sub(_safe_url, text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _OPAQUE.sub(_opaque, text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def error_detail(body, secrets=(), http_status=None):
    """``"LABEL — message"`` from a JSON error body, or ``""`` when it has none.

    Accepts Google's ``{"error": {"code", "status", "message"}}``, the
    Interactions/OpenAI/BytePlus ``{"error": {"code"|"type", "message"}}`` and
    Tripo/Meshy top-level ``{"code", "message"}`` shapes. Non-JSON bodies (proxy
    or CDN pages) yield nothing rather than raw markup.
    """
    try:
        value = json.loads(body[:MAX_ERROR_BODY]) if isinstance(body, (bytes, str)) else body
    except (ValueError, UnicodeError):
        return ""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        value = value[0]  # Some Google endpoints wrap the error object in a list.
    if not isinstance(value, dict):
        return ""
    error = value.get("error")
    source = error if isinstance(error, dict) else value
    message = source.get("message")
    if not isinstance(message, str) and isinstance(error, str):
        message = error
    label = next((v for v in (source.get("status"), source.get("type"), source.get("code"))
                  if isinstance(v, str) and _LABEL.fullmatch(v)), "")
    code = source.get("code")
    if not label and isinstance(code, int) and not isinstance(code, bool) and code not in {0, http_status}:
        label = "code %d" % code
    message = message.strip() if isinstance(message, str) else ""
    text = " — ".join(part for part in (label, message) if part)
    return sanitize(text, secrets)


CODEX_LIMIT_ERRORS = frozenset({"usageLimitExceeded", "rateLimitExceeded", "sessionBudgetExceeded"})
# Prefixes ``codex_error_text`` puts on a TurnError whose ``codexErrorInfo`` names a throttle or an
# overloaded provider, so the chat's notice (``notice_kinds``) and the harness's limit check
# (``eval_provider_limits``) read the provider's own classification, never the wording of its message.
CODEX_TOO_MANY_REQUESTS = "Too many requests (HTTP 429): "
CODEX_OVERLOADED = "Provider overloaded ("
# ``codexErrorInfo`` names that carry the HTTP status of a request Codex gave up on (codex app-server v2).
_HTTP_FAILURES = frozenset({"responseTooManyFailedAttempts", "httpConnectionFailed",
                            "responseStreamConnectionFailed", "responseStreamDisconnected"})
_BUSY_STATUSES = frozenset({502, 503, 504, 529})
# codex-cli 0.163 ends a turn at once, never retried, when a response stops early (openai/codex#53451):
# ``codexErrorInfo`` "other" and this message, the reason as OpenAI gave it (``max_output_tokens``).
CODEX_INCOMPLETE = "The model stopped before finishing its reply (%s). Ask again, or ask for a smaller step: "
_INCOMPLETE = re.compile(r"Incomplete response returned, reason: ([A-Za-z0-9_]{1,40})")


def codex_error_info(error):
    """``(name, http_status)`` of a Codex TurnError's ``codexErrorInfo``; ``(None, None)`` without one.

    codex app-server writes it as a name (``"usageLimitExceeded"``) or as an object keyed by its name
    (``{"responseTooManyFailedAttempts": {"httpStatusCode": 429}}``); the status is an int or None.
    """
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if isinstance(info, str):
        return info, None
    if isinstance(info, dict) and len(info) == 1:
        (name, detail), = info.items()
        status = detail.get("httpStatusCode") if isinstance(detail, dict) else None
        return (name if isinstance(name, str) else None), (status if type(status) is int else None)
    return None, None


def codex_error_kind(error):
    """``"usage"``, ``"rate"``, ``"busy"`` or None: what a Codex TurnError's ``codexErrorInfo`` says happened.

    ``rate`` is HTTP 429 that is not a usage limit (Codex reports a plain 429 with no ``Retry-After`` as
    ``responseTooManyFailedAttempts`` at once, without retrying); ``busy`` is an overloaded provider or a
    request Codex gave up on after 5xx answers.
    """
    name, status = codex_error_info(error)
    if name in ("usageLimitExceeded", "sessionBudgetExceeded"):
        return "usage"
    if name == "rateLimitExceeded" or status == 429:
        return "rate"
    if name == "serverOverloaded" or name in _HTTP_FAILURES and status is not None and (
            status in _BUSY_STATUSES or name == "responseTooManyFailedAttempts" and 500 <= status <= 599):
        return "busy"
    return None


def codex_error_text(error):
    """A Codex TurnError's message, prefixed with the limit, throttle or overload it names, or None. Never raises."""
    if not isinstance(error, dict):
        return None
    message = error.get("message") if isinstance(error.get("message"), str) else "Provider error"
    name, status = codex_error_info(error)
    if name in CODEX_LIMIT_ERRORS:
        return "Provider usage limit reached (%s): %s" % (name, message)
    kind = codex_error_kind(error)
    if kind == "rate":
        return CODEX_TOO_MANY_REQUESTS + message
    if kind == "busy":
        return "%s%s): %s" % (CODEX_OVERLOADED, name if status is None else "HTTP %d" % status, message)
    incomplete = _INCOMPLETE.match(message)
    if incomplete and incomplete.group(1) != "content_filter":
        return CODEX_INCOMPLETE % incomplete.group(1) + message
    return message
