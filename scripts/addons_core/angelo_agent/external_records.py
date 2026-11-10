# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Angelo's window records for processes that run without it (the MCP launcher and its headless child).

Mixed into ``external_host.ExternalHost``; each record is its own step of ``pump`` on Blender's main thread, written
owner-only into ``<state>/mcp/`` whenever it differs from what this host recorded (``external_clients``):

* ``access.json``: the person's "Allow apps on this computer to reach Angelo" switch;
* ``organization.json``: the signed-in organization's app policy the window applies, or "none".

Headless counts a record that is missing or unreadable as the strictest value (off, the strictest policy), so a
write that fails is simply retried at the next tick. doc/angelo/external_mcp.rst, "Organization policy". A write
Windows refused past its retries (``shared_replace.Refused``: another program holds the record) is said once and
tried again ``shared_replace.RETRY_SECONDS`` later, never at every tick; a changed value is tried at once. A record
whose write another window left owed and stopped trying (``launcher_records``; the launcher fails closed while a
marker is there) is written again by this window: the newest change among its own value, theirs and the record as it
stands (each record carries when its value was changed), looked for every ``RETRY_SECONDS``. This window keeps
recording its own value: what it records next is its own change.
"""

import time

from . import shared_replace

_UNRECORDED = object()   # Nothing recorded by this host yet (None is a value: "no organization").


def _held(host, name, value):
    """Whether ``value``'s write was refused by a holder (``Refused``) less than ``RETRY_SECONDS`` ago."""
    held = getattr(host, name + "_held", None)
    return held is not None and held[0] == value and time.monotonic() - held[1] < shared_replace.RETRY_SECONDS


def _intent(host, name, value):
    """When this window's ``value`` last changed (``time.time``): 0 for the value it started with (its saved
    settings), so the person's later change in any window outweighs it at a takeover (``launcher_records``)."""
    seen = getattr(host, name, None)
    if seen is None or seen[0] != value:
        seen = (dict(value) if isinstance(value, dict) else value, 0.0 if seen is None else time.time())
        setattr(host, name, seen)
    return seen[1]


def _stale(host, file, state_directory):
    """The markers of ``file``'s writes that did not land and whose writer stopped trying (another window that quit
    or stalls), claimed by this window (``launcher_records.claim_stale``), which writes the newest change among its
    own, theirs and the record as it stands. Looked for no more often than every ``RETRY_SECONDS``; ``state_directory`` is called only then
    (never a stat at every tick)."""
    key, now = "_owed_checked_" + file, time.monotonic()
    if now - getattr(host, key, now - shared_replace.RETRY_SECONDS) < shared_replace.RETRY_SECONDS:
        return ()
    setattr(host, key, now)
    from pathlib import Path
    from .launcher_records import claim_stale
    return claim_stale(Path(state_directory()) / "mcp", file)


def _refused(host, name, value, error):
    if not getattr(host, name, False):
        print("Angelo: a record for apps on this computer could not be written yet:", error)
    setattr(host, name, True)
    kept = dict(value) if isinstance(value, dict) else value
    setattr(host, name + "_held", (kept, time.monotonic()) if isinstance(error, shared_replace.Refused) else None)


class WindowRecords:
    def record_access(self):
        """Keep ``<state>/mcp/access.json`` equal to the person's switch, so headless sessions (which run without
        this window and can't read Blender's preferences) follow it too (``external_mcp._access_refusal``).

        Its own step of ``pump``, never part of a read such as ``wanted``. An environment without a state folder
        (a stand-in, a host that only answers ``wanted``) records nothing; a write that fails is retried next tick
        and headless fails closed meanwhile (a record that was never written, or says off, refuses)."""
        state_directory = getattr(self.env, "state_directory", None)
        if state_directory is None:
            return
        requested = bool(self.env.access_requested())
        intent = _intent(self, "_access_seen", requested)
        if _held(self, "_access_refused", requested):
            return
        from .external_clients import ACCESS_FILE, write_access
        same = requested == getattr(self, "_recorded_access", None)
        taken = _stale(self, ACCESS_FILE, state_directory) if same else ()
        if same and not taken:
            return
        try:
            write_access(state_directory(), requested, claimed=taken, intent=intent)
        except OSError as error:
            _refused(self, "_access_refused", requested, error)
            return
        self._recorded_access, self._access_refused, self._access_refused_held = requested, False, None

    def record_organization(self):
        """Keep ``<state>/mcp/organization.json`` equal to the organization policy this window applies, "none"
        included, so headless sessions apply the same team policy (``external_clients.read_organization``).

        Like ``record_access``: its own step of ``pump``; an environment without ``organization`` or a state folder
        records nothing; a write that fails is retried next tick, and headless counts a missing record as the
        strictest policy meanwhile. Signing out records "none" (break-it review S1, M1)."""
        state_directory = getattr(self.env, "state_directory", None)
        organization = getattr(self.env, "organization", None)
        if state_directory is None or organization is None:
            return
        try:
            policy = organization()
        except LookupError:
            return
        intent = _intent(self, "_policy_seen", policy)
        if _held(self, "_policy_refused", policy):
            return
        from .external_clients import ORGANIZATION_FILE, write_organization
        same = policy == getattr(self, "_recorded_organization", _UNRECORDED)
        taken = _stale(self, ORGANIZATION_FILE, state_directory) if same else ()
        if same and not taken:
            return
        try:
            write_organization(state_directory(), policy, claimed=taken, intent=intent)
        except OSError as error:
            _refused(self, "_policy_refused", policy, error)
            return
        self._recorded_organization = dict(policy) if isinstance(policy, dict) else policy
        self._policy_refused, self._policy_refused_held = False, None
