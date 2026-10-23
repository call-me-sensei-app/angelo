# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit account roles, official endpoints and pinned model capabilities.

Metadata checked against primary documentation on 2026-10-06. A documented
model is not an entitlement: only a credential-scoped catalog can enable it.
Never infer a billing route from a model-name prefix or an inherited API key.
"""

from .ui_text import n_

CODING = "CODING"
GENAI = "GENAI"
# Section titles and descriptions in Preferences > AI Providers (marked for translation, translated where drawn).
GROUPS = ((CODING, n_("Coding Agents"), n_("Models that operate Blender through the native agent runtime")),
          (GENAI, n_("Media generation"), n_("Image and 3D providers used by Media Studio and Generation Flows")))
PROVIDER_NAMES = {"OPENAI": "OpenAI", "ANTHROPIC": "Anthropic", "XAI": "Grok / xAI",
                  "MINIMAX": "MiniMax", "KIMI": "Kimi / Moonshot", "GOOGLE": "Google Gemini",
                  "BYTEPLUS": "BytePlus / Seedream", "TRIPO": "Tripo", "MESHY": "Meshy"}


def _model(identity, name, *, efforts=(), default_effort="low", images=False,
           fast=None, context=0, default=False):
    return {"id": identity, "name": name, "description": "", "efforts": list(efforts),
            "default_effort": default_effort, "images": images, "fast_tier": fast,
            "context_window": context, "default": default, "runtime_default_alias": False}


SPECS = {
    "codex_subscription": {"group": CODING, "provider": "OPENAI", "method": "SUBSCRIPTION", "runtime": "codex", "name": "ChatGPT"},
    "codex_api": {"group": CODING, "provider": "OPENAI", "method": "API_KEY", "runtime": "codex", "name": "OpenAI API"},
    "claude_subscription": {"group": CODING, "provider": "ANTHROPIC", "method": "SUBSCRIPTION", "runtime": "claude", "name": "Claude"},
    "claude_api": {"group": CODING, "provider": "ANTHROPIC", "method": "API_KEY", "runtime": "claude", "name": "Anthropic API"},
    "grok_api": {"group": CODING, "provider": "XAI", "method": "API_KEY", "runtime": "codex", "name": "Grok API",
                 "base_url": "https://api.x.ai/v1", "probe": "/models", "env_key": "ANGELO_XAI_API_KEY",
                 "docs": "https://docs.x.ai/developers/models/grok-4.6", "web_search": True,
                 "models": [_model("grok-4.6", "Grok 4.6", efforts=("low", "medium", "high", "xhigh"),
                                   default_effort="high", images=True, default=True)]},
    "minimax_api": {"group": CODING, "provider": "MINIMAX", "method": "API_KEY", "runtime": "codex", "name": "MiniMax API",
                    "base_url": "https://api.minimax.io/v1", "probe": "/models", "env_key": "ANGELO_MINIMAX_API_KEY",
                    "docs": "https://platform.minimax.io/docs/api-reference/responses-create",
                    # M3's effort is an on/off control, NOT a graduated depth slider.
                    # Do not advertise Fast until Codex's tier mapping is verified.
                    "models": [_model("MiniMax-M3", "MiniMax M3", efforts=("none", "high"),
                                      default_effort="high", images=True, context=1000000, default=True)]},
    "kimi_api": {"group": CODING, "provider": "KIMI", "method": "API_KEY", "runtime": "codex", "name": "Kimi API",
                 "base_url": "https://api.moonshot.ai/v1", "probe": "/models", "env_key": "ANGELO_KIMI_API_KEY",
                 "docs": "https://platform.kimi.ai/docs/guide/codex-kimi", "web_search": True,
                 "models": [_model("kimi-k3", "Kimi K3", efforts=("low", "high", "max"),
                                   default_effort="high", images=True, context=1048576, default=True)]},
    "openai_images_api": {"group": GENAI, "provider": "OPENAI", "method": "API_KEY", "runtime": None, "name": "OpenAI Images",
                          "base_url": "https://api.openai.com/v1", "probe": "/models", "kind": "IMAGE",
                          "docs": "https://developers.openai.com/api/docs/guides/image-generation",
                          "models": [_model("gpt-image-2.5-flare", "GPT Image 2.5 Flare", default=True),
                                     _model("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst")]},
    "gemini_images_api": {"group": GENAI, "provider": "GOOGLE", "method": "API_KEY", "runtime": None, "name": "Gemini Images",
                          "base_url": "https://generativelanguage.googleapis.com/v1beta", "probe": "/models?pageSize=1000",
                          "auth_header": "x-goog-api-key", "kind": "IMAGE",
                          "docs": "https://ai.google.dev/gemini-api/docs/image-generation",
                          # Google recommends 2.1 for new projects. A key whose
                          # catalog doesn't list it keeps 2 as its default.
                          "models": [_model("gemini-nano-banana-2.1", "Nano Banana 2.1", default=True),
                                     _model("gemini-3.1-flash-image", "Nano Banana 2"),
                                     _model("gemini-3-pro-image", "Nano Banana Pro")]},
    "seedream_api": {"group": GENAI, "provider": "BYTEPLUS", "method": "API_KEY", "runtime": None, "name": "Seedream Images",
                     "base_url": "https://ark.ap-southeast.bytepluses.com/api/v3", "probe": None, "kind": "IMAGE",
                     "docs": "https://docs.byteplus.com/en/docs/modelark/seedream-5-0-pro",
                     "models": [_model("dola-seedream-5-0-pro-260628", "Seedream 5.0 Pro", default=True)]},
    "tripo_api": {"group": GENAI, "provider": "TRIPO", "method": "API_KEY", "runtime": None, "name": "Tripo 3D",
                  "base_url": "https://openapi.tripo3d.ai/v3", "probe": "/account/balance", "kind": "3D",
                  "docs": "https://developers.tripo3d.ai/en/docs/changelog",
                  "models": [_model("P2-20260801", "Tripo P2", default=True),
                             _model("v3.1-20260211", "Tripo 3.1")]},
    "meshy_api": {"group": GENAI, "provider": "MESHY", "method": "API_KEY", "runtime": None, "name": "Meshy 3D",
                  "base_url": "https://api.meshy.ai", "probe": "/openapi/v1/balance", "kind": "3D",
                  "docs": "https://docs.meshy.ai/en/api/changelog",
                  "models": [_model("meshy-7", "Meshy 7", default=True), _model("meshy-7.1", "Meshy 7.1")]},
}
# Interactions image controls each Gemini model documents (image-generation
# guide, "Aspect ratios and image size" tables, checked 2026-10-07). A request
# never sends a value outside its model's list; Studio offers a subset of them.
_GEMINI_ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
_GEMINI_PANORAMIC = ("1:4", "4:1", "1:8", "8:1")
GEMINI_IMAGE_CONTROLS = {
    "gemini-nano-banana-2.1": {"aspect": _GEMINI_ASPECTS + _GEMINI_PANORAMIC, "resolution": ("1K", "2K", "4K")},
    "gemini-3.1-flash-image": {"aspect": _GEMINI_ASPECTS + _GEMINI_PANORAMIC, "resolution": ("512", "1K", "2K", "4K")},
    "gemini-3-pro-image": {"aspect": _GEMINI_ASPECTS, "resolution": ("1K", "2K", "4K")},
}
# Team connections (method TEAM): the same services through the signed-in
# organization's Angelo gateway, which applies the org's own provider key
# (doc/angelo/ai_providers.rst, "Team account and team connections"). Each is
# derived from its personal counterpart. ``gateway_path`` is the provider's own
# base path under the configured gateway origin (contract of 2026-10-08), so
# the URL is resolved at run time and a team spec has no ``base_url``,
# ``probe`` or ``auth_header``: the gateway takes ``Authorization: Bearer``.
# Team connections are derived from the account at run time and are never
# written to connections.json (``team_connections.py``).
TEAM = "TEAM"
TEAM_ENV_KEY = "ANGELO_TEAM_GRANT_KEY"  # Set by the app in a Codex child only; never read from the user.
_TEAM_ROUTES = (
    # personal mode, team mode, display name, gateway provider id, gateway path
    ("claude_api", "claude_team", "Claude", "anthropic", "/v1/anthropic"),
    ("codex_api", "codex_team", "OpenAI", "openai", "/v1/openai/v1"),
    ("grok_api", "grok_team", "Grok", "xai", "/v1/xai/v1"),
    ("minimax_api", "minimax_team", "MiniMax", "minimax", "/v1/minimax/v1"),
    ("kimi_api", "kimi_team", "Kimi", "moonshot", "/v1/moonshot/v1"),
    ("openai_images_api", "openai_images_team", "OpenAI Images", "openai", "/v1/openai/v1"),
    ("gemini_images_api", "gemini_images_team", "Gemini Images", "google", "/v1/google/v1beta"),
    ("seedream_api", "seedream_team", "Seedream Images", "byteplus", "/v1/byteplus/api/v3"),
    ("tripo_api", "tripo_team", "Tripo 3D", "tripo", "/v1/tripo/v3"),
    ("meshy_api", "meshy_team", "Meshy 3D", "meshy", "/v1/meshy"),
)
# The personal OpenAI coding mode lists models from Codex's own account
# catalog; a team connection has no such catalog, so its models are pinned:
# the launch models (doc/angelo/gpt_launch.rst) with a conservative effort set.
_CODEX_TEAM_MODELS = (
    _model("gpt-6.1-sol", "GPT-6.1 Sol", efforts=("low", "medium", "high"), images=True, context=1050000, default=True),
    _model("gpt-6-astra", "GPT-6 Astra", efforts=("low", "medium", "high"), images=True),
)


def _team_spec(personal, name, provider_id, path):
    source = SPECS[personal]
    spec = {key: value for key, value in source.items()
            if key not in {"base_url", "probe", "env_key", "auth_header", "docs", "name", "method", "models"}}
    spec.update(method=TEAM, name=name, personal=personal, team_provider=provider_id, gateway_path=path,
                models=[dict(m) for m in (_CODEX_TEAM_MODELS if personal == "codex_api" else source.get("models", ()))])
    if source["runtime"] == "codex":
        spec["env_key"] = TEAM_ENV_KEY
    if personal == "codex_api":
        spec["web_search"] = True  # As the personal OpenAI mode: Codex's native web search.
    return spec


SPECS.update({team: _team_spec(personal, name, provider_id, path)
              for personal, team, name, provider_id, path in _TEAM_ROUTES})

MODE_BY_CONNECTION = {(s["group"], s["provider"], s["method"]): mode for mode, s in SPECS.items()}
CODING_MODES = frozenset(m for m, s in SPECS.items() if s["group"] == CODING)
EXTERNAL_CODEX_MODES = frozenset(m for m, s in SPECS.items() if s.get("base_url") and s["group"] == CODING)
HTTP_MODES = frozenset(m for m, s in SPECS.items() if "probe" in s)
TEAM_MODES = frozenset(m for m, s in SPECS.items() if s["method"] == TEAM)
TEAM_PROVIDERS = frozenset(s["team_provider"] for s in SPECS.values() if s["method"] == TEAM)
# Codex modes that run through a process-local ``model_providers`` entry whose
# key is in the child's environment (no OpenAI login): personal third-party
# APIs and every team Codex mode.
CUSTOM_PROVIDER_MODES = EXTERNAL_CODEX_MODES | frozenset(m for m in TEAM_MODES if SPECS[m]["runtime"] == "codex")


# A flat-rate coding-plan key (for example Kimi Code or MiniMax M Plan) is a
# connection spec with "plan_terms": {"note": <one short line>, "url": <the
# plan's terms page>}. People may connect their own plan; AI Providers shows the
# note, and automated benchmarks refuse such connections and use the provider's
# pay-per-token API instead (doc/angelo/model_routing.rst). No spec carries it yet.


def plan_terms(mode):
    """The flat plan's usage-terms note of a connection mode, or None."""
    return spec_for(mode).get("plan_terms")


def spec_for(mode):
    try:
        return SPECS[mode]
    except KeyError:
        raise ValueError("Unknown AI connection type") from None


def runtime_kind(mode):
    runtime = spec_for(mode)["runtime"]
    if not runtime:
        raise ValueError("Media generation connections cannot run coding agents")
    return runtime


def provider_items(group):
    providers = dict.fromkeys(s["provider"] for s in SPECS.values() if s["group"] == group)
    return [(p, PROVIDER_NAMES[p], "Connect to " + PROVIDER_NAMES[p]) for p in providers]


def methods(group, provider):
    """Methods a person can add in the New connection form; team connections come from signing in."""
    return tuple(s["method"] for s in SPECS.values()
                 if s["group"] == group and s["provider"] == provider and s["method"] != TEAM)


def is_coding(record):
    return spec_for(record["mode"])["group"] == CODING


def is_team(mode):
    return spec_for(mode)["method"] == TEAM


def service_mode(mode):
    """The personal mode whose request formats a mode uses (itself for a personal mode)."""
    return spec_for(mode).get("personal", mode)


def team_mode(mode):
    """The team counterpart of a personal mode, or None."""
    return next((m for m, s in SPECS.items() if s.get("personal") == mode), None)


def billing_kind(mode):
    """``API_KEY`` for pay-per-token connections (an org's keys included), else ``SUBSCRIPTION``."""
    return "API_KEY" if spec_for(mode)["method"] in {"API_KEY", TEAM} else "SUBSCRIPTION"


def billing_label(mode):
    method = spec_for(mode)["method"]
    return {"API_KEY": "API billing", TEAM: "Angelo team"}.get(method, "Subscription")
