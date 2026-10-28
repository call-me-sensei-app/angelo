# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``runtime.probe`` of ``runtime_service.RuntimeService``: the non-secret facts a worker chat may ask Angelo for.

``coding_models``, ``check_connection``, ``list_models`` (a team or external connection's catalog),
``check_access`` (a Claude chat's key and sign-in) or ``cache_affinity`` (the account's keyed digest, never the
secret: a personal one keyed by the account the chat's own child named to ``account/read``, never an identity the
worker sends). Standard library only and no ``bpy``.
"""

from .protocol import RpcError


class Probes:
    """Mixed into ``RuntimeService``: ``executable``, ``_redact`` and ``_account`` are its."""

    def _claude_access(self, chat):
        """``{ready, message}``: ``claude_transport.check_access`` with Angelo's key and sign-in, never the secret."""
        from .claude_transport import check_access
        executable = self.executable("claude", chat.settings)
        try:
            check_access(chat.mode, chat.api_key, executable, _claude_env(chat, executable), local=True)
        except RpcError as error:
            return {"ready": False, "message": self._redact(chat, str(error))}
        return {"ready": True, "message": ""}

    def probe(self, request, chat):
        from . import api_connections, team_runtime
        from .provider_registry import CUSTOM_PROVIDER_MODES, TEAM_MODES
        what = request.get("what")
        if what == "coding_models" or (what == "list_models" and chat.mode in TEAM_MODES):
            return {"ok": True, "models": team_runtime.coding_models(chat.mode, chat.api_key)}
        if what == "check_connection" or (what == "list_models" and chat.mode in CUSTOM_PROVIDER_MODES):
            check = api_connections.check_connection(chat.mode, chat.api_key)
            return {"ok": True, "state": check.state, "verified": check.verified, "message": check.message,
                    "models": check.models}
        if what == "check_access":   # A Claude chat's key and sign-in, checked as the window checks them.
            return dict(self._claude_access(chat), ok=True)
        if what == "cache_affinity":
            if request.get("scope") == "team":
                return {"ok": True, "account": _affinity_account(chat.mode, request)}
            return {"ok": True, "account": _affinity_account(chat.mode, request, self._account(request["session"]))}
        raise RpcError("This probe is not one Angelo runs for a worker")


def _claude_env(chat, executable):
    from .cli_runtime import with_runtime_path
    from .runtime_launch import child_environment, profile_environment
    settings = chat.settings
    return with_runtime_path(profile_environment(child_environment(chat.mode, chat.api_key), chat.mode,
                                                 settings.get("auth_directory", chat.store_directory),
                                                 settings.get("reuse_codex_login", True), settings.get("connection_id"),
                                                 settings.get("native_profile")), executable)


def _affinity_account(mode, request, identity=None):
    """The account's keyed digest a cache-affinity key starts from (``cache_affinity.account_digest``), or None.
    ``identity``: the personal account Angelo saw the chat's child name (``RuntimeService._account``)."""
    from . import cache_affinity, codex_team_keys
    if request.get("scope") == "team":
        found = codex_team_keys.account(mode)
        if found is None:
            return None
        (team_mode, org), secret = found
        return cache_affinity.account_digest(team_mode, org, secret)
    if not isinstance(identity, str) or len(identity) > 256:
        raise RpcError("A cache-affinity probe needs the account Angelo saw")
    from .cache_affinity_secret import secret   # Angelo's own, read here: never the relay's probe again.
    return cache_affinity.account_digest(mode, identity, secret())
