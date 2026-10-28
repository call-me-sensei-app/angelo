# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a team (managed) connection gives a coding runtime: its gateway route and grant key.

Contract: doc/angelo/ai_providers.rst, "Team account and team connections".
A child process receives exactly one endpoint, the team mode's route on the
app's own configured gateway origin (``team_endpoints``), never an inherited
``ANTHROPIC_BASE_URL`` or provider ``base_url``; the grant key goes in its
environment, never on argv or in a file. Personal modes are untouched.

Standard library only; no bpy.
"""

import json

from . import managed_policy
from .protocol import RpcError
from .provider_registry import EXTERNAL_CODEX_MODES, TEAM_MODES, service_mode, spec_for

SIGN_IN = "Sign in to your Angelo team in Preferences > AI Providers"


def base_url(mode):
    """A team mode's provider route on the configured gateway origin."""
    from .team_endpoints import gateway_origin
    try:
        return gateway_origin().join(spec_for(mode)["gateway_path"])
    except ValueError as error:
        raise RpcError(str(error)) from None


def model_provider_entry(mode):
    """The ``model_providers.<mode>`` overrides of a Codex mode with its own provider entry (``codex_command``).

    A team mode's base URL is its gateway route, a personal external mode's the provider's API. Keys stay in
    the child's environment (``env_key``). A team mode also compresses its requests when the gateway serving
    it decodes them (``request_compression``): an opening is about 3.7x smaller.
    """
    spec = spec_for(mode)
    entry = {"name": spec["name"], "base_url": base_url(mode) if mode in TEAM_MODES else spec["base_url"],
             "env_key": spec["env_key"], "wire_api": "responses", "requires_openai_auth": False,
             "supports_websockets": False}
    compression = request_compression(mode)
    if compression:
        entry["capabilities.request_compression"] = compression
    return {"model_providers.%s.%s" % (mode, key): json.dumps(value) for key, value in entry.items()}


def request_compression(mode):
    """``"zstd"`` when the gateway serving team Codex ``mode`` decodes zstd request bodies, else None.

    The gateway names what it decodes (``gateway_capabilities``); an older one names nothing, and its
    requests stay uncompressed, so the app and the gateway ship in either order. Never for a personal
    mode: a provider's own API takes plain JSON.
    """
    from . import gateway_capabilities
    from .team_endpoints import gateway_origin
    if mode not in TEAM_MODES or spec_for(mode)["runtime"] != "codex":
        return None
    try:
        origin = gateway_origin()
    except ValueError:
        return None  # base_url reports a misconfigured gateway.
    return "zstd" if gateway_capabilities.accepts(origin, "zstd") else None


def openai_service(mode):
    """Whether a Codex mode speaks to OpenAI itself (personal or through the team gateway).

    Such threads defer their tools and may end turns in a typed plan
    (codex_plan.plan_mode); third-party providers do neither.
    """
    return service_mode(mode) not in EXTERNAL_CODEX_MODES


def apply_policy(env, mode):
    """The managed policy at a runtime's launch.

    ``RpcError`` when it allows only team connections and ``mode`` is personal.
    Its ``ExtraCACertificates`` reach Claude Code as ``NODE_EXTRA_CA_CERTS``
    (Node adds them to its roots) unless the environment already names a file;
    Codex is left to the standard variables (doc/angelo/enterprise_policy.rst).
    """
    policy = managed_policy.current()
    if mode not in TEAM_MODES and not policy.allow_personal_connections:
        raise RpcError(managed_policy.personal_refusal(policy, True))
    if policy.extra_ca_certificates and spec_for(mode)["runtime"] == "claude":
        env.setdefault("NODE_EXTRA_CA_CERTS", policy.extra_ca_certificates)
    return env


def apply_environment(env, mode, grant_key):
    """Point a team mode's child at its gateway route with ``grant_key``; ``env`` is changed in place."""
    if mode not in TEAM_MODES:
        raise ValueError("Not a team connection")
    if spec_for(mode)["runtime"] == "claude":
        # Sent as x-api-key, which the gateway accepts for grant keys.
        env["ANTHROPIC_API_KEY"] = grant_key
        env["ANTHROPIC_BASE_URL"] = base_url(mode)
    elif grant_key:
        env[spec_for(mode)["env_key"]] = grant_key  # Codex reads it once at start, as Bearer.
    return env


def coding_models(mode, grant_key):
    """The pinned models of a team Codex mode: the gateway refuses balance and catalog probes."""
    from . import runtime_relay
    if not grant_key and runtime_relay.relayed():   # A worker chat: Angelo holds the grant key and checks it (C5).
        return runtime_relay.probe("coding_models")["models"]
    if not grant_key:
        raise RpcError(SIGN_IN)
    return [dict(m, access="team") for m in spec_for(mode)["models"]]
