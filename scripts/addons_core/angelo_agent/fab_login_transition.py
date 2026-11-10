# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker-only person-acknowledged manual→managed ownership transfer; never account proof.

Producer contract: exact cached login_mode, actual acknowledgement, then owner-controlled
manual drain/profile transfer/new managed runtime. Missing transition ownership stays
retained and blocks cleanup; it cannot be repaired by falling back to the old runtime.
"""
from .fab_catalog import FabError


class TransitionFailed(FabError):
    def __init__(self, owner):
        super().__init__('UNAVAILABLE')
        self.owner = owner  # Private exact cleanup ownership, never a public fact or readiness Boolean.


class UnknownTransition:
    """One bounded unresolved transfer: preserve references, never purge a possibly live profile."""
    def __init__(self, original, returned=None):
        self.original, self.returned = original, returned

    def close(self):
        # A producer that loses its partial owner may have spawned a new runtime. Closing only
        # the old runtime cannot prove drain; automatic profile deletion would be unsafe.
        raise TransitionFailed(self)


def mode(owner):
    try:
        value = owner.login_mode  # Closed cached disposition; no DOM, account or state-directory reads.
    except BaseException as error:
        raise TransitionFailed(owner) from error
    if type(value) is not str or value not in ('manual', 'managed'):
        raise TransitionFailed(owner)
    return value


def acknowledge(owner, transport, stop):
    """Person Check only: manual needs a distinct managed runtime; managed check keeps its existing owner."""
    current_mode = mode(owner)
    stop.check()
    try:
        if transport is not None:
            transport.revoke()  # Cached in-memory revocation, not browser or account I/O.
        owner.acknowledge_user_login(completed=True)
    except BaseException as error:
        raise TransitionFailed(getattr(error, 'owner', None) or owner) from error
    if current_mode == 'managed':
        return owner  # Existing managed acknowledgement path, never a fallback for a manual runtime.
    try:
        current = owner.finish_manual_login(stop=stop)
    except BaseException as error:
        # The owner contract retains old profile/new runtime together when partially transferred.
        partial = getattr(error, 'owner', None)
        raise TransitionFailed(partial if partial is not None else UnknownTransition(owner)) from error
    try:
        valid = current is not owner and mode(current) == 'managed'
    except BaseException:
        valid = False
    if not valid:
        raise TransitionFailed(UnknownTransition(owner, current))
    return current
