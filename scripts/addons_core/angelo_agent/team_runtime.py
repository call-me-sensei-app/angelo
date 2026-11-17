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
from .provider_registry import EXTERNAL_CODEX_MODES, TEAM_MODES, is_endpoint, organization_mode, service_mode, spec_for

SIGN_IN = "Sign in to your Angelo team in Preferences > AI Providers"
ENDPOINT_KEY = "Add your key for your organization's model server in Preferences > AI Providers"


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
    if is_endpoint(mode):   # An organization's own server: its URL, key header and fixed headers (endpoint_connections).
        from .endpoint_connections import provider_entry
        if spec["removed"]:
            raise RpcError(_removed())
        return {"model_providers.%s.%s" % (mode, key): json.dumps(value) for key, value in provider_entry(spec).items()}
    entry = {"name": spec["name"], "base_url": base_url(mode) if mode in TEAM_MODES else spec["base_url"],
             "env_key": spec["env_key"], "wire_api": spec.get("wire_api", "responses"), "requires_openai_auth": False,
             "supports_websockets": False}
    if mode not in TEAM_MODES and spec.get("auth_header"):
        # A provider whose key travels in its own header (Google: x-goog-api-key), never as a bearer token: Codex
        # reads the header's value from the child's environment variable.
        # A TOML dotted key (``-c`` values are TOML), not an inline table: header names are bare keys.
        del entry["env_key"]
        entry["env_http_headers." + spec["auth_header"]] = spec["env_key"]
    entry.update(spec.get("provider_settings", {}))
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
    (codex_plan.plan_mode); third-party providers do neither. A custom endpoint
    is one only when all its models are OpenAI's (``endpoint_connections``).
    """
    if is_endpoint(mode):
        return spec_for(mode)["openai_service"]
    return service_mode(mode) not in EXTERNAL_CODEX_MODES


def apply_policy(env, mode):
    """The managed policy at a runtime's launch.

    ``RpcError`` when it allows only the organization's connections (team ones
    and its custom endpoints) and ``mode`` is personal. Its
    ``ExtraCACertificates`` reach Claude Code as ``NODE_EXTRA_CA_CERTS`` and
    Codex as ``CODEX_CA_CERTIFICATE``; each adds them to the system's roots
    (doc/angelo/enterprise_policy.rst). The inherited spellings never reach a
    child (``runtime_environment``), so these are the policy's own.
    """
    policy = managed_policy.current()
    if not organization_mode(mode) and not policy.allow_personal_connections:
        raise RpcError(managed_policy.personal_refusal(policy, True))
    if policy.extra_ca_certificates:
        name = "NODE_EXTRA_CA_CERTS" if spec_for(mode)["runtime"] == "claude" else "CODEX_CA_CERTIFICATE"
        env.setdefault(name, policy.extra_ca_certificates)
    return env


def _removed():
    """English, as every runtime error: the chat translates it where it shows it (``display_text.shown``)."""
    from .endpoint_connections import REMOVED
    return REMOVED


def apply_endpoint_environment(env, mode, key):
    """Point a custom endpoint's child at its server with ``key``; ``env`` is changed in place.

    The key goes in the environment only, never on argv or in a file: for Codex in ``endpoint_connections.ENV_KEY``
    (read by the provider entry's ``env_key`` or ``env_http_headers``), for Claude Code in its own variables
    (``endpoint_connections.claude_environment``). The child reaches only the endpoint's host (``team_network``):
    neither runtime runs web search or WebFetch there (``web_search_access.native_tools``), so nothing else needs
    the gateway.
    """
    from . import team_network
    from .endpoint_connections import ENV_KEY, claude_environment, key_value, needs_key
    from . import runtime_relay
    from .endpoint_connections import NO_SEAT, admitted
    spec = spec_for(mode)
    if spec["removed"]:
        raise RpcError(_removed())
    if not runtime_relay.relayed() and not admitted():   # The seat rule, again at launch (a worker's is Angelo's).
        raise RpcError(NO_SEAT)
    if spec["runtime"] == "claude":
        env.update(claude_environment(spec, key))
    elif key and needs_key(spec["credential"]):
        env[ENV_KEY] = key_value(spec, key)
    return team_network.apply(env, spec["runtime"], spec["host"])


def apply_environment(env, mode, grant_key):
    """Point a team mode's child at its gateway route with ``grant_key``; ``env`` is changed in place.

    The child reaches nothing else (``team_network``): its other connections are refused.
    """
    from . import team_network
    from .team_endpoints import gateway_origin
    if mode not in TEAM_MODES:
        raise ValueError("Not a team connection")
    runtime = spec_for(mode)["runtime"]
    if runtime == "claude":
        # Sent as x-api-key, which the gateway accepts for grant keys.
        env["ANTHROPIC_API_KEY"] = grant_key
        env["ANTHROPIC_BASE_URL"] = base_url(mode)
    elif grant_key:
        env[spec_for(mode)["env_key"]] = grant_key  # Codex reads it once at start, as Bearer.
    try:
        host = gateway_origin().host
    except ValueError as error:
        raise RpcError(str(error)) from None
    return team_network.apply(env, runtime, host)


def coding_models(mode, grant_key):
    """The pinned models of a team Codex mode: the gateway refuses balance and catalog probes."""
    from . import runtime_relay
    if not grant_key and runtime_relay.relayed():   # A worker chat: Angelo holds the grant key and checks it (C5).
        return runtime_relay.probe("coding_models")["models"]
    if not grant_key:
        raise RpcError(SIGN_IN)
    return [dict(m, access="team") for m in spec_for(mode)["models"]]


def endpoint_models(mode):
    """A custom endpoint format's models, from its organization's description; ``RpcError`` once it's removed."""
    spec = spec_for(mode)
    if spec["removed"] or not spec["models"]:
        raise RpcError(_removed())
    return [dict(m, access="endpoint") for m in spec["models"]]
