# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Errors of the team account, the console and the Angelo gateway, in the person's terms.

The gateway answers ``{"type": "error", "error": {"type", "code", "message",
…}}`` with a fixed code (contract 2026-10-08); a provider's own error passes
through unchanged and is not one of these. The console's API (grant keys,
providers) answers ``{"error": {"code", "message"}}`` with the same codes, and
its OAuth endpoints ``{"error", "error_description"}``. A 503 ``UNAVAILABLE``
(for example while the gateway can't check a grant key) is retryable, never a
sign-out.

Messages are ``display_text.Text`` templates (translated where shown); server
text is never shown raw (``provider_errors.sanitize`` bounds and redacts it).
The console ends a sign-in with ``invalid_grant`` for every cause; its
``error_description`` is the only thing that tells "removed from the team"
from "signed out on the console" (contract 2026-10-08), so the two known
descriptions are matched exactly and anything else reads as "ended".

Standard library only; no bpy.
"""

import json

from .display_text import Text
from .provider_errors import sanitize
from .team_endpoints import PROVIDER_NAMES
from .ui_text import n_

SIGN_IN_AGAIN = n_("Sign in to your Angelo team again in Preferences > AI Providers.")
SIGN_IN = n_("Sign in to your Angelo team in Preferences > AI Providers.")
EXPIRED = n_("Your Angelo sign-in has expired. Sign in to your Angelo team again in Preferences > AI Providers.")
ENDED = n_("This computer was signed out of your Angelo team (from the console, or the sign-in expired). "
           "Sign in again in Preferences > AI Providers.")
REMOVED = n_("You're no longer a member of this Angelo team. Contact your admin for access.")
LAPSED = n_("Your Angelo sign-in expired after a long time unused. Sign in again in Preferences > AI Providers.")
REUSED = n_("This computer was signed out of your Angelo team for safety: its sign-in was used twice. "
            "Sign in again in Preferences > AI Providers.")
NO_SEAT = n_("You don't have a seat in this Angelo team. Contact your admin for access.")
NO_KEY = n_("Your team hasn't added a key for {provider}. Contact your admin for access.")
NO_KEY_ANY = n_("Your team hasn't added a key for this provider. Contact your admin for access.")
LIMITED = n_("Your team's Angelo usage limit was reached; try again in {seconds} s.")
LIMITED_LATER = n_("Your team's Angelo usage limit was reached; try again later.")
TOO_LARGE = n_("This request is larger than the Angelo gateway accepts; use smaller references.")
NOT_OFFERED = n_("The Angelo gateway doesn't offer this {provider} operation.")
NOT_OFFERED_ANY = n_("The Angelo gateway doesn't offer this operation.")
UPSTREAM = n_("{provider} didn't answer through the Angelo gateway; not retried.")
UPSTREAM_ANY = n_("The provider didn't answer through the Angelo gateway; not retried.")
GATEWAY_DOWN = n_("The Angelo gateway is unavailable right now; not retried.")
GATEWAY_REFUSED = n_("The Angelo gateway refused the request.")
BUSY = n_("The Angelo console is busy; try again in a minute.")
UNREACHABLE = n_("Couldn't reach {origin}. Check your network; Angelo retries every minute.")
FAILED = n_("Angelo sign-in failed ({code}).")
FAILED_DETAIL = n_("Angelo sign-in failed ({code}): {detail}")
CONSOLE_404 = n_("The Angelo console doesn't offer this request (HTTP 404). Update Angelo.")
CONSOLE_ERROR = n_("The Angelo console couldn't answer (HTTP {status}); try again later.")
INVALID_ANSWER = n_("The Angelo console returned an invalid answer; try again later.")

# The account's own states (team_account).
SIGNED_OUT_NOTE = n_("Signed out.")
UNEXPECTED = n_("The team account hit an unexpected error; try again.")
DROPPED = n_("The connection dropped while renewing your Angelo sign-in. "
             "Sign in to your Angelo team again in Preferences > AI Providers.")
LOCKED = n_("Another Angelo window is renewing your team sign-in; trying again shortly.")
NOT_ACCEPTED = n_("The console didn't accept this sign-in (it may have taken longer than a minute). "
                  "Choose Sign in to try again.")
REFUSED_BROWSER = n_("The console refused the sign-in; choose Sign in to try again.")
BAD_GRANT_KEY = n_("The Angelo console returned an invalid coding-agent key.")
NOT_A_MEMBER = n_("You're no longer a member of {org}. Contact your admin for access.")
BROWSER_ANSWERS = {"access_denied": n_("Sign-in was canceled in the browser."), "cancelled": n_("Sign-in canceled."),
                   "timeout": n_("No answer from the browser within 10 minutes. Choose Sign in to try again.")}

# Why the console ended a sign-in: ``error_reason`` on every ``invalid_grant`` (the console's OAuth contract,
# doc/angelo/ai_providers.rst). On a refresh, ``expired`` is a refresh token unused
# for 60 days; ``reuse_detected`` a rotated token presented again and ``code_reused`` a replayed authorization
# code, each of which ended the sign-in. A console without the field is told apart by its two refresh
# descriptions; an unknown reason reads as signed out.
_REMOVED_DESCRIPTION = "You're no longer a member of this organization."  # i18n: console error_description matched exactly, never shown
_REASONS = {"signed_out": ENDED, "member_removed": REMOVED, "expired": LAPSED, "reuse_detected": REUSED,
            "code_reused": REUSED}

GATEWAY_CODES = frozenset({"UNAUTHENTICATED", "FORBIDDEN", "NOT_FOUND", "NO_PROVIDER_KEY", "PAYLOAD_TOO_LARGE",
                           "RATE_LIMITED", "UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT", "UNAVAILABLE"})
MAX_RETRY_AFTER = 3600  # seconds; a longer or malformed Retry-After is shown as "later".


class TeamError(RuntimeError):
    """A team-account or gateway failure with a fixed, secret-free ``message`` (a ``Text``).

    ``code`` is the gateway or OAuth code when the server sent one; ``status``
    the HTTP status; ``retry_after`` whole seconds from Retry-After, or None.
    """

    def __init__(self, message, *, code="", status=None, retry_after=None, provider=""):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status
        self.retry_after, self.provider = retry_after, provider


class SignInRequired(TeamError):
    """The sign-in ended or can't be renewed; the person must sign in again."""


class NotSent(OSError):
    """The request never reached the server (DNS, refused connection, TLS handshake)."""


class Unanswered(OSError):
    """The request may have reached the server, but no complete answer arrived."""


def provider_name(provider):
    return PROVIDER_NAMES.get(provider, "")


def missing_key_message(provider):
    """The organization offers no key for this provider (NO_PROVIDER_KEY, or not in providers/available)."""
    name = PROVIDER_NAMES.get(provider)
    return Text(NO_KEY, provider=name) if name else Text(NO_KEY_ANY)


def retry_after_seconds(value):
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_AFTER else None


def gateway_message(code, provider="", retry_after=None):
    name = provider_name(provider)
    if code == "UNAUTHENTICATED":
        return Text(EXPIRED)
    if code == "FORBIDDEN":
        return Text(NO_SEAT)
    if code == "NO_PROVIDER_KEY":
        return missing_key_message(provider)
    if code == "RATE_LIMITED":
        return Text(LIMITED, seconds=retry_after) if retry_after is not None else Text(LIMITED_LATER)
    if code == "PAYLOAD_TOO_LARGE":
        return Text(TOO_LARGE)
    if code == "NOT_FOUND":
        return Text(NOT_OFFERED, provider=name) if name else Text(NOT_OFFERED_ANY)
    if code in {"UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT"}:
        return Text(UPSTREAM, provider=name) if name else Text(UPSTREAM_ANY)
    if code == "UNAVAILABLE":
        return Text(GATEWAY_DOWN)
    return Text(GATEWAY_REFUSED)


def _json(body):
    try:
        value = json.loads(body[:16384] if isinstance(body, (bytes, str)) else b"")
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def gateway_error(status, body, retry_after=None, console=False):
    """A ``TeamError`` for a gateway rejection, or None when the body is not one (a provider's own error).

    ``console``: also accept the console API's ``{"error": {"code", "message"}}``.
    """
    value = _json(body)
    error = value.get("error") if value and (console or value.get("type") == "error") else None
    code = error.get("code") if isinstance(error, dict) else None
    if code not in GATEWAY_CODES:
        return None
    provider = error.get("provider") if isinstance(error.get("provider"), str) else ""
    seconds = retry_after_seconds(retry_after) if code == "RATE_LIMITED" else None
    error_type = SignInRequired if code == "UNAUTHENTICATED" else TeamError
    return error_type(gateway_message(code, provider, seconds), code=code, status=status,
                      retry_after=seconds, provider=provider)


def oauth_error(status, body):
    """The console's OAuth answer (``invalid_grant`` and friends) as an exception."""
    value = _json(body) or {}
    code = value.get("error") if isinstance(value.get("error"), str) else ""
    description = value.get("error_description") if isinstance(value.get("error_description"), str) else ""
    if code in {"invalid_grant", "invalid_token", "unauthorized_client"} or status == 401:
        reason = value.get("error_reason")
        reason = reason if isinstance(reason, str) and reason in _REASONS else ""
        if not reason and description == _REMOVED_DESCRIPTION:
            reason = "member_removed"
        error = SignInRequired(Text(_REASONS.get(reason, ENDED)), code=code or "invalid_grant", status=status)
        error.reason = reason  # The console's reason, or the one its description names; "" when neither says.
        return error
    detail = sanitize(description)
    label = code or "HTTP %s" % status
    message = Text(FAILED_DETAIL, code=label, detail=detail) if detail else Text(FAILED, code=label)
    return TeamError(message, code=code, status=status)


def api_error(status, body, retry_after=None):
    """A console API or gateway refusal (``api_get``, providers, grant keys)."""
    known = gateway_error(status, body, retry_after, console=True)
    if known is not None and known.code != "NOT_FOUND":  # A console 404 is not a gateway route.
        return known
    if status == 401:
        return SignInRequired(Text(EXPIRED), status=status)
    if status == 403:
        return TeamError(Text(NO_SEAT), status=status, code="FORBIDDEN")
    if status == 429:
        seconds = retry_after_seconds(retry_after)
        return TeamError(gateway_message("RATE_LIMITED", retry_after=seconds), status=status,
                         code="RATE_LIMITED", retry_after=seconds)
    if status == 404:
        return TeamError(Text(CONSOLE_404), status=status)
    return TeamError(Text(CONSOLE_ERROR, status=status), status=status)


def unreachable(origin_url):
    return TeamError(Text(UNREACHABLE, origin=origin_url))
