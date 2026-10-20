# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team-scoped cache-affinity keys: members of one team share the cached opening of a Codex team chat.

Contract: doc/angelo/gpt_launch.rst, "Agent runtime and prompt caching" (Team chats). A ChatGPT
subscription chat keys its thread per account under the person's own secret (``cache_affinity``). A team
chat (``codex_team``) reaches OpenAI through the Angelo gateway with the
organization's one provider key, so its prompt cache is the organization's: every
member's chat could read the opening another member's chat cached, but stock Codex
routes each thread by its own ID and nothing was shared. Here the team chat's
thread is keyed by the *team*, not the member: the account input is the mode and
the organization's ID, digested with HMAC-SHA256 under the team's cache-affinity
secret, 32 random bytes the console gives every member's Angelo with the team's
providers (``team_reads.cache_affinity_secret``), never to a browser session. So
every member's chat with the same model, toolset and static prefix sends the same
key, while anyone who knows only the organization's ID cannot compute it. A member
who leaves the team stops receiving the secret: the console refuses their app's
token (401: the grant is revoked) or answers 404, and the account drops it
(``TeamAccount._authorized``, ``_task_providers``); an administrator can rotate it.

* ``MODES``: the team Codex modes that reach OpenAI itself. A third-party provider
  behind the gateway (Grok, MiniMax, Kimi) keys nothing.
* ``applies``: a team chat on Angelo's agent runtime (never the person's own Codex,
  which ignores the key). Team chats keep their own app-server per chat (they are
  not in ``codex_runtime.ELIGIBLE_MODES``: the gateway's grant key is fixed when
  the process starts), so the support check (``cache_affinity.check``, two local
  requests, nothing created or billed) runs once per process, before its first
  keyed thread, and a failed check is refused exactly as on a shared app-server.
* ``account``: ``((mode, org_id), secret)``, or None when no team is signed in with
  a seat or the console sent no secret (an older console): the thread is then
  started without a key, as before. ``ANGELO_TEAM_CACHE_KEY_SECRET`` (64
  hexadecimal digits, only that spelling) replaces the console's secret, also
  where the console sends none, for a measurement run against a console that
  predates it (a team is still required); any other value is an error.

Effort, the member, the machine, the working directory and every chat or thread
identity are never inputs (``cache_affinity``). Standard library only; no bpy.
"""

import re
import threading
import weakref

from . import cache_affinity, product_env
from .protocol import RpcError
from .provider_registry import TEAM_MODES, spec_for

MODES = frozenset(m for m in TEAM_MODES if spec_for(m)["runtime"] == "codex" and spec_for(m)["personal"] == "codex_api")
VARIABLE = "TEAM_CACHE_KEY_SECRET"  # ANGELO_TEAM_CACHE_KEY_SECRET (product_env.current: no deprecated spelling)
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_checked, _lock = weakref.WeakKeyDictionary(), threading.Lock()


def applies(provider):
    """Whether ``provider``'s new text threads may be team-keyed: a team OpenAI mode on the agent runtime."""
    return (provider.controller.state.get("provider") in MODES
            and getattr(provider, "_runtime_kind", "own") not in (None, "own"))


def account(mode, current=None, environ=None):
    """``((mode, org_id), secret)`` of the signed-in team, or None (module docstring)."""
    if mode not in MODES:
        return None
    if current is None:
        from .team_library_access import account as team_account
        current = team_account()
    view = current.view() if current is not None else None
    if view is None or not view.can_generate or not view.org_id:
        return None
    override = product_env.current(VARIABLE, environ)
    if override is not None:
        if not _HEX.match(override.strip()):
            raise RpcError("ANGELO_TEAM_CACHE_KEY_SECRET must be 64 hexadecimal digits (32 bytes), or unset")
        return (mode, view.org_id), bytes.fromhex(override.strip())
    found = current.cache_affinity_secret()
    if found is None or found[0] != view.org_id:
        return None
    return (mode, found[0]), found[1]


def supported(rpc):
    """Check once per process that it accepts ``cacheAffinityKey``; ``cache_affinity.CheckFailed`` if not."""
    with _lock:
        verdict = _checked.get(rpc)
    if verdict is None:
        verdict = cache_affinity.check(rpc)
        with _lock:
            _checked[rpc] = verdict
    if verdict != cache_affinity.SUPPORTED:
        raise cache_affinity.CheckFailed(verdict)
