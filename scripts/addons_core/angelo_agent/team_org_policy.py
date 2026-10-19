# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed-in organization's app policy, and the last one received, kept for a launch without the network.

The console sends the organization's app policy (``orgs.app_policy``: the
switches an owner or admin sets) with every token answer (``team_view.org_policy``);
``managed_policy.current`` combines it with the machine policy, the stricter
value winning. It is not a secret, so the last one received is kept in the
connections folder (``team-org-policy.json``) and applies while a stored
sign-in restores or Angelo is offline: going offline never lifts it. Signing
out forgets it. A console that sends no policy leaves the machine policy alone.

Standard library only; no bpy. ``remember`` runs on the main thread.
"""

import json
import os

from .team_view import OFFLINE, RESTORING, SIGNED_OUT, org_policy

FILE = "team-org-policy.json"


class OrganizationPolicy:
    def __init__(self, account, directory):
        self.account = account
        self.path = os.path.join(directory, FILE)
        self.known = None
        try:
            with open(self.path, encoding="utf-8") as handle:
                saved = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(saved, dict) and saved.get("console") == account.console_url:
            self.known = org_policy(saved.get("policy"))

    def current(self):
        """The policy to apply now (``managed_policy.set_organization_source``)."""
        view = self.account.view()
        if view.signed_in:
            return view.org_policy
        return self.known if view.status in {RESTORING, OFFLINE} else None

    def remember(self, view):
        """Keep what the console sent last; forget it once signed out."""
        policy = view.org_policy if view.signed_in else None if view.status == SIGNED_OUT else self.known
        if policy == self.known:
            return
        self.known = policy
        try:
            if policy is None:
                os.remove(self.path)
            else:
                temporary = self.path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump({"version": 1, "console": self.account.console_url, "policy": policy}, handle)
                os.replace(temporary, self.path)
        except OSError:
            pass  # Kept in memory for this session.
