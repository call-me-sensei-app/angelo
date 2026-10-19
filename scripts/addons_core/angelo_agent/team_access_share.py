# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The access token shared by every Angelo process on this computer, through the OS store.

Every console refresh rotates the refresh token. A process that starts without
an access token must refresh, so without sharing each Angelo window, instance
or test trial started within one token's 10 minutes rotated the sign-in again
(the console saw a refresh every 8-45 seconds from one computer). Instead the
latest token answer is kept in the OS credential store next to the refresh
token (``team_token_store``, service ``studio.hyperbond.angelo.team-access-token``)
and adopted, under the team lock (``team_lock``), while it has more than the
account's margin left. A computer then refreshes about once per token lifetime
however many processes use the sign-in.

Security: the item is protected exactly as the refresh token item is, and
whoever can read one can read the other; the refresh token is the stronger
credential (60 days, and it mints access tokens). An access token adds at most
10 minutes of bearer access the refresh token already grants, and it is erased
with the sign-in. Fewer rotations also mean fewer chances of the dangerous
case, an answer lost after the console rotated (``DROPPED``), and of two
presenters tripping reuse detection.

A stored token is adopted only with the refresh token it was issued with
(``fp``, a SHA-256 prefix of that refresh token, so an access token of an older
rotation or another sign-in is never used), never past ``LIFETIME_CAP`` and never
when it is the token the server just refused. Its account fields go through the
same ``parse_tokens`` validation as a console answer. The value is JSON:
``{"v": 1, "exp": <epoch>, "fp": "<hex>", "access": "<token>", "answer": {...}}``,
at most ``MAX_BYTES`` (Windows Credential Manager holds 2,560); a larger one is
not shared. Without a usable store nothing is shared.

Standard library only; no bpy. Called on the account's worker or a caller's thread.
"""

import hashlib
import json
import time

from .key_store import KeyStoreError
from .team_view import parse_tokens

LIFETIME_CAP = 15 * 60   # seconds: never trust a stored expiry further out (console tokens last 600 s)
MAX_BYTES = 2048         # under Windows Credential Manager's 2,560-byte blob
_FORMAT = 1


def fingerprint(refresh):
    return hashlib.sha256(refresh.encode("utf-8")).hexdigest()[:32]


def _answer(tokens):
    """The non-secret token-answer fields ``parse_tokens`` reads back."""
    answer = {"scope": " ".join(sorted(tokens["scopes"])), "seat": tokens["seat"],
              "org": {"id": tokens["org_id"], "name": tokens["org_name"]},
              "user": {"id": tokens["user_id"], "email": tokens["email"]}}
    if tokens["org_policy"] is not None:
        answer["policy"] = dict(tokens["org_policy"])
    return answer


def encode(tokens, now):
    """The stored value for ``tokens`` (``parse_tokens`` output), or None when it is too large to share."""
    value = json.dumps({"v": _FORMAT, "exp": int(now + tokens["expires_in"]), "fp": fingerprint(tokens["refresh"]),
                        "access": tokens["access"], "answer": _answer(tokens)}, separators=(",", ":"))
    return value if len(value.encode("utf-8")) <= MAX_BYTES else None


def decode(value, refresh, now):
    """``parse_tokens`` output for a stored value issued with ``refresh`` and not yet expired, or None."""
    try:
        stored = json.loads(value) if isinstance(value, str) and len(value) <= MAX_BYTES else None
    except ValueError:
        return None
    if (not isinstance(stored, dict) or stored.get("v") != _FORMAT or not isinstance(stored.get("exp"), int)
            or isinstance(stored.get("exp"), bool) or not isinstance(stored.get("answer"), dict)
            or stored.get("fp") != fingerprint(refresh)):
        return None
    remaining = int(min(stored["exp"] - now, LIFETIME_CAP))
    if remaining < 1:
        return None
    return parse_tokens(dict(stored["answer"], access_token=stored.get("access"), refresh_token=refresh,
                             expires_in=remaining))


class SharedAccess:
    """The stored token answer in ``vault`` (a ``TokenVault``, or None: nothing is shared)."""

    def __init__(self, vault=None, clock=time.time):
        self._vault, self._clock = vault, clock

    def read(self, refresh):
        """The shared answer for ``refresh`` (``expires_in``: seconds left), or None. Caller holds the team lock."""
        if self._vault is None or not refresh:
            return None
        try:
            return decode(self._vault.read(), refresh, self._clock())
        except KeyStoreError:
            return None

    def write(self, tokens):
        """Share a fresh token answer. Caller holds the team lock; a failure only means others refresh."""
        if self._vault is None:
            return
        value = encode(tokens, self._clock())
        try:
            if value is None:
                self._vault.delete()  # Never leave an older answer in place of this one.
            else:
                self._vault.write(value)
        except KeyStoreError:
            pass

    def erase(self):
        if self._vault is not None:
            try:
                self._vault.delete()
            except KeyStoreError:
                pass  # It names a refresh token that no longer works, so it is never adopted.
