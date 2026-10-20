# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the team account reads from the Angelo console, parsed into what the app shows.

Every console API answer is ``{"data": …}`` (contract 2026-10-08). A field of
the wrong type reads as absent; nothing here trusts the server's shapes.

* ``providers``: ``GET /api/orgs/{org}/providers/available``, the provider ids
  the organization has an active key for, limited to the ones Angelo knows.
* ``cache_affinity_secret``: the same answer's ``cacheAffinitySecret``, the
  team's 32 random bytes that key its members' Codex prompt-cache routing
  (``codex_team_keys``). The console sends it to the app's token only, never
  to a browser session; an older console sends none.
* ``usage``: ``GET /api/orgs/{org}/usage/people/{user}``, the person's own
  usage (any member may read their own), reduced to the few numbers the
  account menu shows. Estimated costs are not read: the organization pays its
  providers directly and the console shows costs to admins.
* ``grant_key``: the answer of ``POST /api/oauth/grant-key``.

Standard library only; no bpy.
"""

import re
from urllib.parse import quote

from .team_endpoints import PROVIDER_NAMES
from .team_grants import KEY, USABLE_FOR


_SECRET = re.compile(r"[0-9a-f]{64}")


def _data(value):
    return value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else {}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def providers_path(org_id):
    return "/api/orgs/%s/providers/available" % quote(org_id, safe="")


def providers(value):
    listed = _data(value).get("providers")
    return tuple(sorted({p for p in listed if isinstance(p, str) and p in PROVIDER_NAMES})) if isinstance(listed, list) else ()


def cache_affinity_secret(value):
    """The team's cache-affinity secret (32 bytes) from a providers answer, or None when it holds none."""
    text = _data(value).get("cacheAffinitySecret")
    return bytes.fromhex(text) if isinstance(text, str) and _SECRET.fullmatch(text) else None


def usage_path(org_id, user_id):
    return "/api/orgs/%s/usage/people/%s" % (quote(org_id, safe=""), quote(user_id, safe=""))


def usage(value):
    """``{"tokens", "agent_tasks", "generations", "active_days", "streak", "lifetime_tokens"}`` or None."""
    data = _data(value)
    period, stats = data.get("thisPeriod"), data.get("stats")
    if not isinstance(period, dict) or not isinstance(stats, dict):
        return None
    return {"tokens": _count(period.get("tokens")), "agent_tasks": _count(period.get("agentTasks")),
            "generations": _count(period.get("generations")), "active_days": _count(period.get("activeDays")),
            "streak": _count(stats.get("currentStreakDays")), "lifetime_tokens": _count(stats.get("lifetimeTokens"))}


def grant_key(value):
    """``(key, lifetime seconds)`` of a grant-key answer, or None when it isn't the documented shape."""
    data = _data(value)
    key, lifetime = data.get("key"), data.get("expires_in")
    if (not isinstance(key, str) or not KEY.fullmatch(key) or isinstance(lifetime, bool)
            or not isinstance(lifetime, int) or not 2 * USABLE_FOR <= lifetime <= 86400):
        return None
    return key, lifetime
