# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The coding agents' grant key: one per computer, shared by every Angelo window through the OS store.

A grant key (``agk_…``, ``POST /api/oauth/grant-key``, 12 hours) is the API
key a Codex or Claude Code child process starts with; its environment is fixed
for its lifetime, so a 10-minute access token can't work there (contract
2026-10-08). The console keeps at most 8 unexpired keys per grant and revokes
the oldest when a ninth is issued, so every window and restart issuing its own
key could revoke a key a long-running agent still uses. Instead the current key
is kept in the OS credential store next to the refresh token
(``team_token_store``, service ``studio.hyperbond.angelo.team-grant-key``) and
reused while it has more than ``RENEW_BEFORE`` left: one key per computer
about every 10 hours. Issuing and adopting happen under the team lock
(``team_lock``), so two windows never issue at once.

The stored value is ``v1 <expires epoch> <org id> <user id> <key>``; a key of
another person or organization is never adopted. Without a usable store the
key lives in this process's memory only.

Standard library only; no bpy. Called on the account's worker thread.
"""

import re
import threading
import time

from .key_store import KeyStoreError

RENEW_BEFORE = 2 * 3600    # seconds: renew at an idle point with less left (a 12-hour key: after about 10 hours)
MUST_RENEW = 20 * 60       # seconds: renew even mid-turn (a running agent keeps its key until it expires)
USABLE_FOR = 60            # seconds: a key with less left is not handed to a new runtime
KEY = re.compile(r"agk_[A-Za-z0-9_-]{20,200}")
_FORMAT = "v1"


def encode(key, expires, owner):
    return " ".join((_FORMAT, "%d" % expires, owner[0], owner[1], key))


def decode(value):
    """``(key, expires, owner)`` of a stored value, or None when it isn't one."""
    parts = value.split(" ") if isinstance(value, str) else []
    if len(parts) != 5 or parts[0] != _FORMAT or not KEY.fullmatch(parts[4]) or not parts[1].isdigit():
        return None
    return parts[4], float(parts[1]), (parts[2], parts[3])


class GrantKeys:
    """The current key in memory, and the shared copy in ``vault`` (a ``TokenVault``, or None)."""

    def __init__(self, vault=None, clock=time.time):
        self._vault, self._clock = vault, clock
        self._lock = threading.Lock()
        self._key, self._expires, self._owner = "", 0.0, ("", "")

    def current(self, owner, now=None):
        """The key for ``owner`` with at least ``USABLE_FOR`` seconds left, or ""."""
        now = self._clock() if now is None else now
        with self._lock:
            usable = self._key and self._owner == owner and self._expires - now > USABLE_FOR
            return self._key if usable else ""

    def expires(self, owner):
        with self._lock:
            return self._expires if self._key and self._owner == owner else None

    def due(self, owner, idle, now=None):
        """Whether to renew: no key, under ``MUST_RENEW`` left, or under ``RENEW_BEFORE`` at an idle point."""
        now = self._clock() if now is None else now
        with self._lock:
            remaining = self._expires - now if self._key and self._owner == owner else 0
        return remaining <= MUST_RENEW or (idle and remaining <= RENEW_BEFORE)

    def secret(self):
        with self._lock:
            return self._key

    def forget(self):
        """Drop the key from memory (sign-out, another person, a key the gateway refused)."""
        with self._lock:
            self._key, self._expires, self._owner = "", 0.0, ("", "")

    def adopt_stored(self, owner, now=None):
        """Use the shared key when it is this owner's and has more than ``RENEW_BEFORE`` left. Caller holds the team lock."""
        now = self._clock() if now is None else now
        if self._vault is None:
            return False
        try:
            stored = decode(self._vault.read())
        except KeyStoreError:
            return False
        if stored is None or stored[2] != owner or stored[1] - now <= RENEW_BEFORE:
            return False
        with self._lock:
            if stored[0] == self._key:
                return False  # Already ours: nothing newer was shared.
            self._key, self._expires, self._owner = stored[0], stored[1], owner
        return True

    def remember(self, owner, key, lifetime, now=None):
        """Keep a freshly issued key and share it. Caller holds the team lock."""
        now = self._clock() if now is None else now
        with self._lock:
            self._key, self._expires, self._owner = key, now + lifetime, owner
        if self._vault is not None:
            try:
                self._vault.write(encode(key, now + lifetime, owner))
            except KeyStoreError:
                pass  # This window keeps it in memory; others issue their own.

    def erase_stored(self):
        if self._vault is not None:
            try:
                self._vault.delete()
            except KeyStoreError:
                pass  # A revoked grant's key is refused by the gateway anyway.
