# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Published model facts: tier, prices, context window and caching, with sources.

Pure data plus small lookups; no credentials, network or runtime. Prices are
USD per million tokens as published on the cited pages, read on ``READ_ON``.
They are API rates: a subscription spends plan usage instead, and Angelo uses
these rates only as the common yardstick for routing and cost projections
(Claude Code's own estimates use the same rates). A documented model is not an
entitlement; only a connection's catalog says what may run
(``provider_registry.py``). Update a row only from its primary source, with
the new date.
"""

READ_ON = "2026-10-08"
SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "anthropic_haiku": "https://platform.claude.com/docs/en/models/haiku-5-5/overview",
    "openai": "https://developers.openai.com/api/docs/pricing",
    "xai": "https://docs.x.ai/developers/models/grok-4.6",
    "minimax": "https://platform.minimax.io/docs/guides/pricing-paygo",
    "kimi": "https://platform.kimi.ai/docs/pricing/chat",
}
TIERS = ("fast", "balanced", "strong")


def _prices(input_price, output, cache_read, cache_write_5m, cache_write_1h=None):
    return {"input": input_price, "output": output, "cache_read": cache_read,
            "cache_write_5m": cache_write_5m, "cache_write_1h": cache_write_1h}


# runtime: the Angelo runtime that serves the model; aliases: catalog names that
# resolve to it; price_step_tokens: prompt size above which ``prices_above_step``
# apply (0 when flat). Single-model providers are "balanced": routing can only
# change their effort.
MODELS = {
    "claude-opus-5-5": {"runtime": "claude", "tier": "strong", "aliases": ("opus",), "context_window": 1000000,
                        "images": True, "source": "anthropic", "prices": _prices(4.0, 20.0, 0.20, 5.0, 8.0)},
    # Opus 5.5 and Sonnet 5.5 read the cache at 0.05x input (Anthropic rows re-read 2026-10-21; Claude Code
    # 2.1.301's own catalog agrees).
    "claude-sonnet-5-5": {"runtime": "claude", "tier": "balanced", "aliases": ("sonnet",), "context_window": 1000000,
                          "images": True, "source": "anthropic", "prices": _prices(2.0, 10.0, 0.10, 2.50, 4.0)},
    "claude-haiku-5-5": {"runtime": "claude", "tier": "fast", "aliases": ("haiku",), "context_window": 1000000,
                         "images": True, "source": "anthropic_haiku", "price_step_tokens": 100000,
                         "prices": _prices(0.10, 0.50, 0.01, 0.125, 0.20),
                         "prices_above_step": _prices(0.50, 2.50, 0.05, 0.625, 1.0)},
    # GPT-5.6 and later charge 1.25x for cache writes with a fixed 30-minute TTL.
    "gpt-6-astra": {"runtime": "codex", "tier": "strong", "aliases": (), "context_window": 0, "images": True,
                    "source": "openai", "prices": _prices(10.0, 50.0, 1.0, 12.50)},
    "gpt-6.1-sol": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 1050000, "images": True,
                    "source": "openai", "prices": _prices(2.0, 10.0, 0.10, 2.50)},
    "gpt-6-luna": {"runtime": "codex", "tier": "fast", "aliases": (), "context_window": 0, "images": True,
                   "source": "openai", "prices": _prices(0.10, 0.50, 0.01, 0.125)},
    # Automatic caching without a write charge: writes cost the input rate.
    "grok-4.6": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 500000, "images": True,
                 "source": "xai", "price_step_tokens": 200000, "prices": _prices(2.0, 6.0, 0.50, 2.0),
                 "prices_above_step": _prices(4.0, 12.0, 1.0, 4.0)},
    "MiniMax-M3": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 1000000, "images": True,
                   "source": "minimax", "price_step_tokens": 512000, "prices": _prices(0.30, 1.20, 0.06, 0.30),
                   "prices_above_step": _prices(0.60, 2.40, 0.12, 0.60)},
    "kimi-k3": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 1048576, "images": True,
                "source": "kimi", "prices": _prices(3.0, 15.0, 0.30, 3.0, 6.0)},
}


def resolve(model_id):
    """The canonical ID for a model ID or a catalog alias, or None when unknown."""
    if model_id in MODELS:
        return model_id
    return next((identity for identity, facts in MODELS.items() if model_id in facts["aliases"]), None)


def rates(model_id, cache_ttl="1h", *, prompt_tokens=0):
    """``{input, output, cache_read, cache_write}`` USD/MTok for one request, or None.

    ``cache_write`` is the rate of the session's TTL; providers without a 1-hour
    TTL charge their only write rate. ``prompt_tokens`` above a price step
    selects the higher rates.
    """
    identity = resolve(model_id)
    if identity is None:
        return None
    facts = MODELS[identity]
    step = facts.get("price_step_tokens", 0)
    table = facts["prices_above_step"] if step and prompt_tokens > step else facts["prices"]
    write = table["cache_write_1h"] if cache_ttl == "1h" and table["cache_write_1h"] is not None else table["cache_write_5m"]
    return {"input": table["input"], "output": table["output"], "cache_read": table["cache_read"], "cache_write": write}


# Measured results that admit a model to Auto routing, from billed harness runs
# only (``doc/angelo/model_routing.rst``, "How to continue"). Keyed by canonical
# model ID, then the effort it ran at, then task kind:
#   {"tasks": int, "pass_rate": 0-1, "p75_ms": float, "cost_usd": float per task,
#    "source": "<run directory name>", "date": "YYYY-MM-DD"}
# Pass rates describe the model with Angelo's prompts, not an account, so one
# subscription run serves every connection whose catalog offers the model.
# Empty until the 2026-10-08 Sonnet 5.5 and Haiku 5.5 runs are entered: with no
# measured pair, Auto keeps the connection's Default model, as before.
MEASURED = {}


def catalog_ids(catalog):
    """Canonical model ID -> the catalog's own ID for it (an exact ID beats an alias)."""
    output = {}
    for entry in catalog or ():
        identity = entry.get("id") if isinstance(entry, dict) else None
        canonical = resolve(identity) if isinstance(identity, str) else None
        if canonical and (canonical not in output or identity == canonical):
            output[canonical] = identity
    return output


def capability_profile(connection_id, runtime, billing, catalog, *, effort="low", cache_ttl="1h", measured=None):
    """The ``model_routing`` profile of one connection, or None when it offers no known model.

    Only models in this connection's own catalog are listed, under the catalog's
    ID, so a route can never name a model another account would serve.
    """
    measured = MEASURED if measured is None else measured
    models = []
    for canonical, catalog_id in sorted(catalog_ids(catalog).items()):
        facts = MODELS[canonical]
        if facts["runtime"] != runtime:
            continue
        price = rates(canonical, cache_ttl)
        models.append({"id": catalog_id, "tier": facts["tier"], "effort": effort, "images": facts["images"],
                       "context_window": facts["context_window"], "price_step_tokens": facts.get("price_step_tokens", 0),
                       "prices": price, "measured": dict(measured.get(canonical, {}).get(effort, {}))})
    if not models:
        return None
    return {"connection_id": connection_id, "runtime": runtime, "billing": billing, "models": models}
