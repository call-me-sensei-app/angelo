# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A team runtime child reaches only its gateway: every other connection it opens is refused.

Contract: doc/angelo/ai_providers.rst, "Team connections reach only the gateway".
Claude Code and Codex honour the standard proxy variables. A team child gets a
proxy that refuses every connection (``REFUSING_PROXY``, a closed loopback port)
and ``NO_PROXY`` naming the gateway's host alone. The gateway route is therefore
reached directly, as Angelo's own team requests are (no proxy, enterprise_policy.rst
"Network"), and any other host fails at once instead of bypassing the gateway:
telemetry, update checks, WebFetch's direct page fetch, and whatever a newer
runtime adds. The variables a runtime already reads to skip its optional traffic
are set too (``QUIET``), so it does not try and fail. WebFetch reaches the web
through the gateway (``web_fetch_hook``).

Standard library only; no bpy.
"""

import sys
from pathlib import Path

REFUSING_PROXY = "http://127.0.0.1:9"   # The discard port: nothing listens there, so every proxied connect is refused.
# The standard names, and gRPC's own (Node's gRPC library reads grpc_proxy before https_proxy).
PROXY_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "GRPC_PROXY")
BYPASS_VARIABLES = ("NO_PROXY",)
# Inherited settings that open connections of their own or name another proxy: OpenTelemetry exporters (opt-in, and
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC leaves them on), gRPC's own bypass list, and npm's proxy settings, which
# common Node proxy libraries read before the standard names. A team child gets none of them.
DROPPED_PREFIXES = ("OTEL_",)
DROPPED = frozenset({"CLAUDE_CODE_ENABLE_TELEMETRY", "NO_GRPC_PROXY", "NPM_CONFIG_PROXY", "NPM_CONFIG_HTTPS_PROXY",
                     "NPM_CONFIG_HTTP_PROXY", "NPM_CONFIG_NO_PROXY", "NPM_CONFIG_NOPROXY"})
# Claude Code's documented switch for its telemetry, error reports, auto-update and other optional requests: two
# connections to api.anthropic.com per launch without it (2.1.301, measured through a refusing proxy).
QUIET = {"claude": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}, "codex": {}}

# The WebFetch hook (Claude only): Angelo's interpreter runs web_fetch_hook.py isolated (-I), without the site step
# (-S), which it doesn't need and which costs start-up on every WebFetch call, and writing no bytecode (-B: the site
# step is what otherwise keeps a bundled interpreter's __pycache__ out of the signed app). The hook command in the
# session settings names these variables, so the command line stays the same on every machine.
HOOK_PYTHON = "ANGELO_HOOK_PYTHON"
HOOK_SCRIPT = "ANGELO_WEB_FETCH_HOOK"
HOOK_COMMAND = '"$%s" -I -S -B "$%s"' % (HOOK_PYTHON, HOOK_SCRIPT)


def apply(env, runtime, gateway_host):
    """Fence ``env`` (a team child's, changed in place) to ``gateway_host``; every proxy spelling is replaced."""
    if not gateway_host:
        raise ValueError("A team runtime needs its gateway host")
    managed = {name.upper() for name in PROXY_VARIABLES + BYPASS_VARIABLES} | DROPPED
    for name in [name for name in env if name.upper() in managed or name.upper().startswith(DROPPED_PREFIXES)]:
        del env[name]
    for name in PROXY_VARIABLES:
        env[name] = env[name.lower()] = REFUSING_PROXY
    for name in BYPASS_VARIABLES:
        env[name] = env[name.lower()] = gateway_host
    env.update(QUIET[runtime])
    if runtime == "claude":
        env[HOOK_PYTHON] = sys.executable
        env[HOOK_SCRIPT] = str(Path(__file__).with_name("web_fetch_hook.py"))
    return env


def settings(gateway, hooks_blocked=None):
    """A Claude session's settings additions: WebFetch's preflight and page fetch through the gateway's tickets.

    Claude Code's own preflight (``api.anthropic.com/api/web/domain_info``) ignores the base URL, so it is skipped;
    the gateway runs Anthropic's domain check before it issues a ticket (docs/console/gateway.md, "Web fetch").
    When an administrator's Claude Code policy stops hooks (``claude_managed``), the preflight stays: WebFetch then
    fails closed at the fence instead of fetching unchecked.
    """
    if not gateway:
        return {}
    if hooks_blocked is None:
        from .claude_managed import hooks_blocked as blocked
        hooks_blocked = blocked()
    if hooks_blocked:
        return {}
    return {"skipWebFetchPreflight": True, "hooks": {"PreToolUse": [{"matcher": "WebFetch", "hooks": [
        {"type": "command", "command": HOOK_COMMAND, "timeout": 60}]}]}}
