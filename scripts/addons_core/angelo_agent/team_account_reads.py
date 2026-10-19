# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account's reads of providers and usage, a part of ``team_account.TeamAccount`` kept in its own module.

``refresh_providers`` and ``refresh_usage`` run on the main thread and only
queue work; ``_task_providers`` and ``_task_usage`` run on the account's worker
thread. ``TeamAccount`` provides ``_lock``, ``_epoch``, ``_view``, ``_last``,
``_monotonic``, ``_enqueue``, ``_update_if``, ``_authorized`` and ``api_get``.

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
            epoch, org, name = self._epoch, self._view.org_id, self._view.org_name
            if self._view.status != SIGNED_IN:
                return
            self._last["providers"] = self._monotonic()  # Also when run inline after a sign-in.
        try:
            value = self._authorized("GET", team_reads.providers_path(org))
        except TeamError as error:
            if error.status == 404:  # The console answers 404 to a non-member.
                self._update_if(epoch, providers=(), providers_error=Text(NOT_A_MEMBER, org=name))
            elif not isinstance(error, SignInRequired):
                self._update_if(epoch, providers_error=error.message)
            return
        self._update_if(epoch, providers=team_reads.providers(value), providers_error="")

    def _task_usage(self):
        with self._lock:
            epoch, view = self._epoch, self._view
        if view.signed_in:
            try:  # The menu simply shows no numbers when they can't be read.
                self._update_if(epoch, usage=team_reads.usage(self.api_get(team_reads.usage_path(view.org_id, view.user_id))))
            except TeamError:
                pass
