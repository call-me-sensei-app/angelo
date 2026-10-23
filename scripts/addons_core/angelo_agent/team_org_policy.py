# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed-in organization's app policy, and the last one received, kept for a launch without the network.

The console sends the organization's app policy (``orgs.app_policy``: the
switches an owner or admin sets) with every token answer (``team_view.org_policy``);
``managed_policy.current`` combines it with the machine policy, the stricter
value winning. It is not a secret, so the last one received is kept in the
connections folder (``team-org-policy.json``) and applies while a stored
sign-in restores or Angelo is offline: going offline never lifts it. Signing
out forgets it (also a copy left by another console). A console that sends no
policy leaves the machine policy alone.

That file is the window's own offline copy. Processes that run without the
window (the MCP launcher and its headless child) read what the window applies
from ``<state>/mcp/organization.json`` instead (``external_clients.read_organization``):
an explicit record where "no organization" is written too, so a missing file
there counts as the strictest policy, never as none.

Standard library only; no bpy. ``remember`` runs on the main thread.
"""

import json
import os
import stat

from .team_view import OFFLINE, RESTORING, SIGNED_OUT, org_policy

FILE = "team-org-policy.json"
MAX_FILE = 64 * 1024


# What an unreadable or missing policy counts as outside the window: every switch off, edits ask (fails closed).
STRICTEST = {"allowPersonalConnections": False, "allowLocalCliAccess": False, "allowVoice": False,
             "allowCollaborators": False, "allowAutoApproval": False, "requireAskForEdits": True}


def read_regular(path):
    """A small saved file's text: only a regular file, opened without waiting on a FIFO or following a link at its
    name, and at most ``MAX_FILE`` bytes, so ``/dev/zero`` or a huge file never exhausts memory (break-it review N1).
    ``FileNotFoundError`` when there is none; any other problem raises ``OSError`` (``UnicodeDecodeError`` for
    bytes that are not UTF-8, a ``ValueError``)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular file")
        data = os.read(descriptor, MAX_FILE + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_FILE:
        raise OSError("larger than any saved policy")
    return data.decode("utf-8")


def read_saved(directory):
    """The policy in the window's offline copy (any console): None when there is no file; an unreadable or malformed
    file counts as the strictest policy. Not what a process without the window applies: a file that failed to write
    or sits in another connections folder would read as none there (break-it review S1), so the MCP launcher reads
    ``external_clients.read_organization``."""
    path = os.path.join(os.fspath(directory), FILE)
    try:
        saved = json.loads(read_regular(path))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return org_policy(STRICTEST)
    policy = org_policy(saved.get("policy")) if isinstance(saved, dict) else None
    return policy if policy is not None else org_policy(STRICTEST)


class OrganizationPolicy:
    def __init__(self, account, directory):
        self.account = account
        self.path = os.path.join(directory, FILE)
        self.known = None
        self.leftover = False   # A file there this account would not apply (another console's, or unreadable).
        try:
            saved = json.loads(read_regular(self.path))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self.leftover = True
            return
        if isinstance(saved, dict) and saved.get("console") == account.console_url:
            self.known = org_policy(saved.get("policy"))
        self.leftover = self.known is None

    def current(self):
        """The policy to apply now (``managed_policy.set_organization_source``)."""
        view = self.account.view()
        if view.signed_in:
            return view.org_policy
        return self.known if view.status in {RESTORING, OFFLINE} else None

    def remember(self, view):
        """Keep what the console sent last; forget it once signed out, also a file another console left (break-it
        review M1: after a console switch, the earlier console's file outlived the sign-out)."""
        policy = view.org_policy if view.signed_in else None if view.status == SIGNED_OUT else self.known
        if policy == self.known and not (view.status == SIGNED_OUT and self.leftover):
            return
        self.known, self.leftover = policy, False
        try:
            if policy is None:
                try:
                    os.remove(self.path)
                except FileNotFoundError:
                    pass
            else:
                temporary = self.path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump({"version": 1, "console": self.account.console_url, "policy": policy}, handle)
                os.replace(temporary, self.path)
        except OSError:
            pass  # Kept in memory for this session.
