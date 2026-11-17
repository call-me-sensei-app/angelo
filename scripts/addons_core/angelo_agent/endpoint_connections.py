# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Custom model endpoints as coding connections: one virtual connection per endpoint and request format.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints". An organization's
own model server (``custom_endpoints``) reaches AI Providers as one connection per
format it speaks ("Example proxy · OpenAI", "… · Gemini"). Each is a mode of its
own, ``endpoint_<format>_<endpoint id>`` (``mode_for``), with method ``ENDPOINT``:
not a team connection (no grant key, no Angelo gateway in the path) and not a
personal one (never stored, allowed while the policy turns personal connections
off). ``provider_registry.spec_for`` resolves such a mode here, from the current
endpoints, into a spec shaped like the registry's, whose ``personal`` is the
format's own personal mode, so request rules follow that mode
(``provider_registry.service_mode``).

* **Where endpoints come from** (``effective``): the machine policy's
  ``CustomEndpoints`` when it sets the key (it wins, even empty), else the
  signed-in organization's ``customEndpoints`` (``team_reads``).
* **Who may use them** (``account_admits``): a signed-in team member with a seat
  and model use, for both sources. This is the one place that decides it.
* **Ids**: a connection's id is derived from the console, organization, person and
  mode (as ``team_connections.connection_id``), so a chat finds its connection
  again after a restart or a new sign-in. Each endpoint has one key slot
  (``key_identity``) shared by its formats.
* **A removed endpoint** still resolves, to a spec marked ``removed`` with no
  models, so a saved chat on it reports ``REMOVED`` instead of failing to load.
* **Worker chats** (another Blender process) have no account: ``spec`` asks Angelo
  for the chat's own spec over the relay (``runtime_probes``), never a key.

Standard library only; no bpy.
"""

import re
import time
import uuid

from .custom_endpoints import FORMATS, RUNTIMES
from .ui_text import n_

METHOD = "ENDPOINT"
PROVIDER = "ENDPOINT"
ENV_KEY = "ANGELO_ENDPOINT_KEY"   # Set by the app in an endpoint's Codex child only; never read from the person.
REMOVED = n_("Your organization no longer offers this model server. Choose another connection in AI Providers.")
NO_SEAT = n_("Your organization's model server needs a seat in your Angelo team. Contact your admin for access.")
MODE_PREFIX = "endpoint_"
_MODE = re.compile(r"endpoint_([a-z_]+)_([0-9a-f]{32})")
_FORMAT_BY_TOKEN = {name.replace("-", "_"): name for name in FORMATS}
_NAMESPACE = uuid.UUID("ab6773c2-8f23-4356-8b18-c71f01e265ab")  # Angelo custom endpoint connections.
# A mapped model the registry doesn't list (no coding connection offers it): the efforts every Codex model accepts.
_PROFILE_EFFORTS = ("low", "medium", "high")

_endpoints = ()       # The effective endpoints in this (Angelo's) process, set by ``set_current``.
_admitted = False     # Whether this process's account may use them (``account_admits``), set with them.
_relayed = {}         # A worker's specs, by mode, as Angelo answered them.


def mode_for(endpoint_id, format_id):
    return "%s%s_%s" % (MODE_PREFIX, format_id.replace("-", "_"), endpoint_id)


def parse_mode(mode):
    """``(format id, endpoint id)`` of an endpoint mode, or None for any other string."""
    found = _MODE.fullmatch(mode) if isinstance(mode, str) else None
    if not found or found.group(1) not in _FORMAT_BY_TOKEN:
        return None
    return _FORMAT_BY_TOKEN[found.group(1)], found.group(2)


def is_endpoint(mode):
    return parse_mode(mode) is not None


def runtime_of(mode):
    """"codex" or "claude": the runtime an endpoint mode's format runs on, read from the mode alone."""
    return RUNTIMES[parse_mode(mode)[0]]


def needs_key(credential):
    """Whether a credential kind gives the runtime a key (every kind but ``none``)."""
    return credential != "none"


OFFLINE_GRACE = 600.0   # Seconds a seat confirmed before the console became unreachable still counts.


def account_admits(view, now=None):
    """Whether this account may use custom endpoints: signed in with a seat and model use (``AccountView``).

    The one seat check for both sources (the policy's and the console's), so a decision to admit people without a
    seat changes only this function. An account that is only offline (the console unreachable, ``OFFLINE``) keeps
    the seat it last had for ``OFFLINE_GRACE`` after the last token that confirmed it (``seat_confirmed_at``,
    monotonic): the console's own bound for a seat removal to apply (its 10-minute access token), and no longer,
    however late the app noticed it was offline.
    """
    if view is None:
        return False
    if view.signed_in:
        return view.can_generate
    from .team_view import OFFLINE
    confirmed = getattr(view, "seat_confirmed_at", None)
    if view.status != OFFLINE or confirmed is None or not view.org_id:
        return False
    now = time.monotonic() if now is None else now
    return view.seat and "generate" in view.scopes and 0 <= now - confirmed < OFFLINE_GRACE


def account_known(view):
    """Whether the account's organization and person are known now: signed in, or only offline (``OFFLINE``)."""
    from .team_view import OFFLINE
    return view is not None and bool(view.org_id) and (view.signed_in or view.status == OFFLINE)


def effective(view, policy):
    """``(endpoints, source, left_out)``: the policy's when it sets ``CustomEndpoints`` ("policy"), else the
    signed-in organization's ("console"). ``left_out`` counts the console's unusable entries; the policy reports
    its own in ``Policy.problems``."""
    if policy.custom_endpoints is not None:   # Only for the policy's own organization (managed_policy requires it).
        tied = bool(policy.organization) and view is not None and view.org_id == policy.organization
        return (policy.custom_endpoints if tied else ()), "policy", 0
    if not account_known(view):
        return (), "console", 0
    return view.custom_endpoints, "console", view.endpoints_left_out


def set_current(endpoints, admitted=False):
    """The effective endpoints of Angelo's own process (``endpoint_ui``), and whether its account may use them; a
    spec is resolved from these, and a launch refuses while ``admitted`` is False (``team_runtime``)."""
    global _endpoints, _admitted
    _endpoints, _admitted = tuple(endpoints), bool(admitted)


def admitted():
    return _admitted


def current():
    return _endpoints


def find(endpoint_id):
    return next((e for e in _endpoints if e.id == endpoint_id), None)


def connection_id(console_url, org_id, user_id, mode):
    return str(uuid.uuid5(_NAMESPACE, "|".join(("connection", console_url, org_id, user_id, mode))))


def claude_model_overrides(mode):
    """Claude Code's ``modelOverrides`` for an endpoint's Messages connection: each mapped model's Anthropic id to the
    server's own name, so Claude Code treats that name as the model it is, with its context window, instead of
    assuming 200K for a name it doesn't know (code.claude.com/docs/en/model-config, "Correct the window for a
    gateway or custom model ID"). Unmapped models and other modes: none."""
    if not is_endpoint(mode) or runtime_of(mode) != "claude":
        return {}
    found = spec(mode)
    if not found or found.get("removed"):
        return {}
    from .model_profiles import MODELS
    overrides = {}
    for model in found["models"]:
        profile = model.get("profile")
        if profile and profile != model["id"] and (MODELS.get(profile) or {}).get("runtime") == "claude":
            overrides.setdefault(profile, model["id"])   # One name per Anthropic model: the first the server lists.
    return overrides


def key_identity(console_url, org_id, user_id, endpoint_id):
    """The key slot (a UUID for ``saved_keys``) of one person's key for one endpoint, shared by its formats."""
    return str(uuid.uuid5(_NAMESPACE, "|".join(("key", console_url, org_id, user_id, endpoint_id))))


def _profile_model(profile):
    """The registry's model entry for an Angelo model id (its efforts, images, context), or None."""
    from .provider_registry import profile_model
    return profile_model(profile)


def _model(entry, default):
    """An endpoint model as a connection's model entry (``provider_registry._model``'s shape)."""
    known = _profile_model(entry.profile) if entry.profile else None
    if known is None and entry.profile:   # A model_profiles model no coding connection lists (its facts only).
        from .model_profiles import MODELS
        facts = MODELS[entry.profile]
        known = {"efforts": list(_PROFILE_EFFORTS), "images": facts["images"],
                 "context_window": facts["context_window"]}
    return {"id": entry.id, "name": entry.id, "description": "",
            "efforts": list(known["efforts"]) if known else [],
            "images": bool(known["images"]) if known else False, "fast_tier": None,
            "context_window": known.get("context_window", 0) if known else 0, "default": default,
            "runtime_default_alias": False, "profile": entry.profile, "untested": entry.profile is None}


def _openai_service(endpoint, format_id):
    """Whether this format's thread may use OpenAI-only request features (deferred tools, typed plans, code mode):
    only an OpenAI-format endpoint whose every model is mapped to an OpenAI model. Any other model may sit behind a
    translating server, so it gets the function-calling request every Responses server accepts."""
    if format_id != "openai-responses":
        return False
    from .model_profiles import MODELS
    models = endpoint.models_in(format_id)
    return bool(models) and all(m.profile and MODELS[m.profile].get("source") == "openai" for m in models)


def build_spec(endpoint, format_id):
    """The registry-shaped spec of one endpoint format."""
    from .provider_registry import CODING, SPECS
    wire, personal, label = FORMATS[format_id]
    form = endpoint.format(format_id)
    models = endpoint.models_in(format_id)
    runtime = RUNTIMES[format_id]
    spec = {"group": CODING, "provider": PROVIDER, "method": METHOD, "runtime": runtime, "personal": personal,
            "name": ("%s · %s" % (endpoint.name, label))[:64], "endpoint": endpoint.id, "format": format_id,
            "server": endpoint.server, "base_url": endpoint.url(format_id), "host": endpoint.host,
            "credential": endpoint.credential, "env_key": ENV_KEY, "auth_header": form.header,
            "auth_scheme": form.scheme, "extra_headers": dict(endpoint.extra_headers),
            # The format's own provider settings (Gemini: its long thinking before the first byte).
            "provider_settings": dict(SPECS[personal].get("provider_settings", {})),
            "web_search": False,   # Codex's web search is a hosted OpenAI tool; a server may not pass it through.
            "openai_service": _openai_service(endpoint, format_id),
            "models": [_model(m, index == 0) for index, m in enumerate(models)], "removed": False}
    if runtime == "codex":   # Claude Code speaks Messages itself: no Codex wire.
        spec["wire_api"] = wire
    return spec


def removed_spec(mode):
    """What an endpoint mode that is no longer offered resolves to: a coding Codex mode with no models."""
    from .provider_registry import CODING
    format_id, endpoint_id = parse_mode(mode)
    wire, personal, label = FORMATS[format_id]
    spec = {"group": CODING, "provider": PROVIDER, "method": METHOD, "runtime": RUNTIMES[format_id],
            "personal": personal, "name": label, "endpoint": endpoint_id, "format": format_id, "env_key": ENV_KEY,
            "credential": "none", "web_search": False, "openai_service": False, "models": [], "removed": True}
    if RUNTIMES[format_id] == "codex":
        spec["wire_api"] = wire
    return spec


def spec(mode):
    """The spec of endpoint ``mode`` (None for any other string): from the current endpoints, from Angelo when this
    is a worker chat, or ``removed_spec``. Never raises: ``provider_registry.spec_for`` owns the unknown-mode error."""
    parsed = parse_mode(mode)
    if parsed is None:
        return None
    format_id, endpoint_id = parsed
    endpoint = find(endpoint_id)
    if endpoint is not None and endpoint.format(format_id) is not None:
        return build_spec(endpoint, format_id)
    if mode in _relayed:
        return _relayed[mode]
    from . import runtime_relay
    if runtime_relay.relayed():   # A worker chat: Angelo holds the account and resolves its chat's mode (C5).
        from .protocol import RpcError
        try:
            found = runtime_relay.probe("endpoint_spec").get("spec")
        except RpcError:
            found = None   # Angelo couldn't answer: the chat reads its connection as gone, never a crash.
        if isinstance(found, dict) and found.get("method") == METHOD and parse_mode(mode) == (
                found.get("format"), found.get("endpoint")):
            _relayed[mode] = found
            return found
    return removed_spec(mode)


def records(view, console_url, endpoints):
    """``{id: record}``: a coding connection per endpoint and format; empty unless the account is known (signed in,
    or only offline: ``account_known``)."""
    if not account_known(view):
        return {}
    output = {}
    for endpoint in endpoints:
        for format_id, _form in endpoint.formats:
            if not endpoint.models_in(format_id):
                continue   # A format with no models offers nothing to choose.
            mode = mode_for(endpoint.id, format_id)
            identity = connection_id(console_url, view.org_id, view.user_id, mode)
            spec_entry = build_spec(endpoint, format_id)
            output[identity] = {"id": identity, "name": spec_entry["name"], "provider": PROVIDER, "method": METHOD,
                                "mode": mode, "model": "", "reuse_native": False, "group": spec_entry["group"],
                                "native_profile": None, "endpoint": endpoint.id}
    return output


def key_value(spec_entry, key):
    """The value the child's key variable holds: ``Bearer <key>`` or the key alone (``auth_scheme``)."""
    if spec_entry["auth_header"] == "authorization" and spec_entry["auth_scheme"] == "bearer":
        return key   # Codex's own ``env_key`` path writes "Authorization: Bearer <key>".
    return "Bearer " + key if spec_entry["auth_scheme"] == "bearer" else key


# Claude Code's own credential variables: ``x-api-key`` from ANTHROPIC_API_KEY, ``Authorization: Bearer`` from
# ANTHROPIC_AUTH_TOKEN. Any other header (and the fixed ones) goes in ANTHROPIC_CUSTOM_HEADERS, one "Name: value" per
# line. Claude Code needs one of the two to start; a key carried in another header (or no key at all) leaves a
# placeholder in ANTHROPIC_API_KEY that is never a secret, so no other header of the request ever carries the key.
NO_KEY = "angelo-no-key"   # i18n: a protocol placeholder, never shown


def claude_environment(spec_entry, key):
    """The variables a Claude Code child on an endpoint's ``anthropic-messages`` format gets."""
    env = {"ANTHROPIC_BASE_URL": spec_entry["base_url"]}
    header, scheme = spec_entry["auth_header"], spec_entry["auth_scheme"]
    custom = ["%s: %s" % (name, value) for name, value in spec_entry["extra_headers"].items()]
    if needs_key(spec_entry["credential"]) and key and header == "authorization" and scheme == "bearer":
        env["ANTHROPIC_AUTH_TOKEN"] = key
    elif needs_key(spec_entry["credential"]) and key and header == "x-api-key" and scheme == "none":
        env["ANTHROPIC_API_KEY"] = key
    else:
        env["ANTHROPIC_API_KEY"] = NO_KEY
        if needs_key(spec_entry["credential"]) and key:
            custom.append("%s: %s" % (header, ("Bearer " + key) if scheme == "bearer" else key))
    if custom:
        env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(custom)
    return env


def provider_entry(spec_entry):
    """The ``model_providers.<mode>`` fields of an endpoint format, before ``team_runtime`` quotes them."""
    entry = {"name": spec_entry["name"], "base_url": spec_entry["base_url"], "wire_api": spec_entry["wire_api"],
             "requires_openai_auth": False, "supports_websockets": False}
    if not needs_key(spec_entry["credential"]):
        pass   # A server that checks no key: no key header at all.
    elif spec_entry["auth_header"] == "authorization" and spec_entry["auth_scheme"] == "bearer":
        entry["env_key"] = ENV_KEY
    else:   # A header of the server's own (api-key, x-goog-api-key, …), or a key with no scheme.
        entry["env_http_headers." + spec_entry["auth_header"]] = ENV_KEY
    for name, value in spec_entry["extra_headers"].items():
        entry["http_headers." + name] = value
    entry.update(spec_entry.get("provider_settings", {}))
    return entry
