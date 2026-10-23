# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Angelo's window records for processes that run without it (the MCP launcher and its headless child).

Mixed into ``external_host.ExternalHost``; each record is its own step of ``pump`` on Blender's main thread, written
owner-only into ``<state>/mcp/`` whenever it differs from what this host recorded (``external_clients``):

* ``access.json``: the person's "Allow apps on this computer to reach Angelo" switch;
* ``organization.json``: the signed-in organization's app policy the window applies, or "none".

Headless counts a record that is missing or unreadable as the strictest value (off, the strictest policy), so a
write that fails is simply retried at the next tick. doc/angelo/external_mcp.rst, "Organization policy".
"""

_UNRECORDED = object()   # Nothing recorded by this host yet (None is a value: "no organization").


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
        if requested == getattr(self, "_recorded_access", None):
            return
        from .external_clients import write_access
        try:
            write_access(state_directory(), requested)
        except OSError:
            return
        self._recorded_access = requested

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
        if policy == getattr(self, "_recorded_organization", _UNRECORDED):
            return
        from .external_clients import write_organization
        try:
            write_organization(state_directory(), policy)
        except OSError:
            return
        self._recorded_organization = dict(policy) if isinstance(policy, dict) else policy
