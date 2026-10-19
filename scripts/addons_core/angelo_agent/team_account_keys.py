# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account's grant-key work, a part of ``team_account.TeamAccount`` kept in its own module.

When to fetch or renew the coding agents' key, handing it out, dropping one the
gateway refused, and issuing or adopting the key every window shares
(``team_grants``) under the team lock. ``TeamAccount`` provides ``_grants``,
``_lock``, ``_epoch``, ``_view``, ``_owner``, ``_update``, ``_update_if``,
``_authorized`` and ``_process_lock``.

Standard library only; no bpy.
"""

from . import team_reads
from .display_text import Text
from .provider_registry import CODING, SPECS, TEAM
from .team_errors import BAD_GRANT_KEY, LOCKED, SignInRequired, TeamError
from .team_lock import LockTimeout

CODING_PROVIDERS = frozenset(s["team_provider"] for s in SPECS.values() if s["method"] == TEAM and s["group"] == CODING)


class GrantKeyTasks:
    def grant_key_due(self, idle, now=None):
        """Whether to fetch a grant key: none, expiring soon, or within two hours at an idle point."""
        with self._lock:
            view = self._view
        if not view.can_generate or not (set(view.providers or ()) & CODING_PROVIDERS):
            return False
        return self._grants.due(self._owner(view), idle, now)

    def grant_key(self, now=None):
        """The current grant key, or "" when there is none with at least a minute left."""
        return self._grants.current(self._owner(), now)

    def refused_grant_key(self, key):
        """The gateway refused ``key`` (UNAUTHENTICATED): drop it so the next tick issues another."""
        if key and key == self._grants.secret():
            self._grants.forget()
            self._update(grant_key_expires=None)

    def _task_grant_key(self):
        with self._lock:
            epoch, owner = self._epoch, self._owner()
        try:
            with self._process_lock():  # One issuer at a time; a key another window just issued is reused.
                if not self._grants.adopt_stored(owner):
                    self._issue_grant_key(epoch, owner)
        except LockTimeout:
            self._update_if(epoch, grant_error=Text(LOCKED))
            return
        with self._lock:
            if epoch == self._epoch and self._owner() == owner:
                self._update(grant_key_expires=self._grants.expires(owner))

    def _issue_grant_key(self, epoch, owner):
        try:
            # Bearer POSTs to the console API send JSON (contract update 2026-10-08).
            value = self._authorized("POST", "/api/oauth/grant-key", b"{}", {"Content-Type": "application/json"})
        except TeamError as error:
            if not isinstance(error, SignInRequired):
                self._update_if(epoch, grant_error=error.message)
            return
        issued = team_reads.grant_key(value)
        if issued is None:
            self._update_if(epoch, grant_error=Text(BAD_GRANT_KEY))
            return
        with self._lock:
            if epoch != self._epoch:
                return
        self._grants.remember(owner, *issued)
        self._update_if(epoch, grant_error="")
