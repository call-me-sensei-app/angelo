# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private account observations supplied only by the reviewed normal-browser owner.

These records validate shape and bind generations; constructing one does not authenticate a person. The actual
producer must observe a stable Fab account identity after normal sign-in. No icon, profile, caller flag, cookie
or missing Sign in control is proof. Never put these records or their values in public results or receipts.
"""
from dataclasses import dataclass

from .fab_catalog import FabError, text, token


@dataclass(frozen=True, repr=False, slots=True)
class AccountObservation:
    opaque_account: str
    profile_generation: str
    runtime_generation: str

    def __post_init__(self):
        text(self.opaque_account, 256)
        token(self.profile_generation)
        token(self.runtime_generation)


def current_account(callback, expected, stop):
    """An unavailable producer fails closed. Account/profile/runtime changes revoke this adapter permanently."""
    stop.check()
    observed = callback(stop)
    stop.check()
    if type(observed) is not AccountObservation or observed != expected:
        raise FabError('AUTH_REQUIRED')
    return observed
