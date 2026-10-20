# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account's reads of providers and usage, a part of ``team_account.TeamAccount`` kept in its own module.

``refresh_providers`` and ``refresh_usage`` run on the main thread and only
queue work; ``_task_providers`` and ``_task_usage`` run on the account's worker
thread. ``TeamAccount`` provides ``_lock``, ``_epoch``, ``_view``, ``_last``,
``_monotonic``, ``_enqueue``, ``_update_if``, ``_authorized``, ``api_get`` and
``_affinity``.

The providers read also carries the team's cache-affinity secret
(``team_reads.cache_affinity_secret``). It is kept in memory only, with the
organization and person it was read for, never in the view or on disk: a new
read replaces it, an answer that the person is no longer a member (403, 404)
or a sign-out drops it, and ``cache_affinity_secret`` hands it out only while
that same person is signed in to that same organization with a seat.

Standard library only; no bpy.
"""

from . import team_reads
from .display_text import Text
from .team_errors import NOT_A_MEMBER, SignInRequired, TeamError
from .team_view import SIGNED_IN

RETRY_EVERY = 60                 # seconds before retrying failed background work (also ``team_account``'s)


class TeamReadTasks:
    def refresh_providers(self, older_than=0.0):
        """Re-read the team's providers, unless the last check is younger than ``older_than`` seconds."""
        with self._lock:
            fresh = self._monotonic() - self._last.get("providers", float("-inf")) < older_than
            if fresh or self._view.status != SIGNED_IN:
                return False
        return self._enqueue("providers")

    def refresh_usage(self):
        """The account menu opened: read the person's usage again (at most once a minute)."""
        with self._lock:
            stale = self._monotonic() - self._last.get("usage", float("-inf")) >= RETRY_EVERY
        return stale and self._view.signed_in and self._enqueue("usage")

    def _task_providers(self):
        with self._lock:
            epoch, org, name, user = self._epoch, self._view.org_id, self._view.org_name, self._view.user_id
            if self._view.status != SIGNED_IN:
                return
            self._last["providers"] = self._monotonic()  # Also when run inline after a sign-in.
        try:
            value = self._authorized("GET", team_reads.providers_path(org))
        except TeamError as error:
            if error.status in (403, 404):  # No longer a member (the console answers 404): no secret either.
                self._keep_affinity(epoch, None)
            if error.status == 404:
                self._update_if(epoch, providers=(), providers_error=Text(NOT_A_MEMBER, org=name))
            elif not isinstance(error, SignInRequired):
                self._update_if(epoch, providers_error=error.message)
            return
        secret = team_reads.cache_affinity_secret(value)
        self._keep_affinity(epoch, (org, user, secret) if secret else None)
        self._update_if(epoch, providers=team_reads.providers(value), providers_error="")

    def _keep_affinity(self, epoch, held):
        with self._lock:
            if epoch == self._epoch:
                self._affinity = held

    def cache_affinity_secret(self):
        """``(org_id, secret)`` of the signed-in team for its Codex cache keys, or None (module docstring)."""
        with self._lock:
            view, held = self._view, self._affinity
        if held is None or not view.can_generate or held[:2] != (view.org_id, view.user_id):
            return None
        return held[0], held[2]

    def _task_usage(self):
        with self._lock:
            epoch, view = self._epoch, self._view
        if view.signed_in:
            try:  # The menu simply shows no numbers when they can't be read.
                self._update_if(epoch, usage=team_reads.usage(self.api_get(team_reads.usage_path(view.org_id, view.user_id))))
            except TeamError:
                pass
