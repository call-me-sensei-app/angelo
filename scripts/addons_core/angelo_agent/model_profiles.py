# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Published model facts: tier, prices, context window and caching, with sources.

Pure data plus small lookups; no credentials, network or runtime. Prices are
USD per million tokens as published on the cited pages, read on a row's own
``read_on`` (its ``page``), else on ``READ_ON`` (``SOURCES``).
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
    "google": "https://aistudio.google.com/docs/whats-new-gemini-4-carbon",
}
TIERS = ("fast", "balanced", "strong")


def _prices(input_price, output, cache_read, cache_write_5m, cache_write_1h=None):
    return {"input": input_price, "output": output, "cache_read": cache_read,
            "cache_write_5m": cache_write_5m, "cache_write_1h": cache_write_1h}


OPENAI_STEP_TOKENS = 272000


def _openai(identity, tier, window, max_output, input_price, output, cache_read, *, read_on):
    """A GPT row from its model page (``page``, read on ``read_on``): one 30-minute cache write at 1.25x input, and
    OpenAI's long-context step (2x input and cache, 1.5x output above ``OPENAI_STEP_TOKENS`` input tokens).
    ``context_window`` is a request's input budget, the page's window less its maximum output, on every GPT row."""
    return {"runtime": "codex", "tier": tier, "aliases": (), "context_window": window - max_output, "images": True,
            "window": window, "max_output": max_output,
            "page": "https://developers.openai.com/api/docs/models/" + identity, "read_on": read_on,
            "source": "openai", "price_step_tokens": OPENAI_STEP_TOKENS,
            "prices": _prices(input_price, output, cache_read, input_price * 1.25),
            "prices_above_step": _prices(input_price * 2, output * 1.5, cache_read * 2, input_price * 2.5)}


# runtime: the Angelo runtime that serves the model; aliases: catalog names that
# resolve to it; price_step_tokens: prompt size above which ``prices_above_step``
# apply (0 when flat). Single-model providers are "balanced": routing can only
# change their effort.
MODELS = {
    # Claude Fable 5.2 (Anthropic's pricing and models pages, read 2026-11-18; Claude Code 2.1.321 added it as
    # "fable"): cache hits 0.025x input, the full 1M window at standard rates. Selectable where the connection's
    # catalog lists it; nothing measured admits it to Auto (MEASURED).
    "claude-fable-5-2": {"runtime": "claude", "tier": "strong", "aliases": ("fable",), "context_window": 1000000,
                         "images": True, "source": "anthropic", "prices": _prices(8.0, 40.0, 0.20, 10.0, 16.0)},
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
    # GPT-5.6 and later charge 1.25x for cache writes with a fixed 30-minute TTL. Every GPT row: a request of more
    # than 272K input tokens costs 2x input and cache and 1.5x output for the whole request (each model's page).
    # Harness sums of several Codex requests above it are unknown unless each request's usage was recorded
    # (list_prices.py); a subscription has no long-context surcharge (learn.chatgpt.com/codex/pricing). Every page
    # read 2026-11-20 states a 1,050,000-token window and 128,000 maximum output; the 6.2 pages also state the
    # 922,000 maximum input that the others' budget is derived as. In Codex on a subscription the default context is
    # 272,000 (Astra) and 500,000 (Sol); Codex's own catalog sets that (doc/angelo/gpt_launch.rst, "Model IDs").
    # GPT-6.2 Astra and Sol (OpenAI, 2026-11-17): the shipped Astra and Sol.
    "gpt-6.2-astra": _openai("gpt-6.2-astra", "strong", 1050000, 128000, 8.0, 40.0, 0.40, read_on="2026-11-20"),
    "gpt-6.2-sol": _openai("gpt-6.2-sol", "balanced", 1050000, 128000, 2.0, 10.0, 0.10, read_on="2026-11-20"),
    # Earlier generation, selectable while OpenAI serves it.
    "gpt-6-astra": _openai("gpt-6-astra", "strong", 1050000, 128000, 10.0, 50.0, 1.0, read_on="2026-11-20"),
    "gpt-6.1-sol": _openai("gpt-6.1-sol", "balanced", 1050000, 128000, 2.0, 10.0, 0.10, read_on="2026-11-20"),
    "gpt-6-luna": _openai("gpt-6-luna", "fast", 1050000, 128000, 0.10, 0.50, 0.01, read_on="2026-11-20"),
    # Automatic caching without a write charge: writes cost the input rate.
    "grok-4.6": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 500000, "images": True,
                 "source": "xai", "price_step_tokens": 200000, "prices": _prices(2.0, 6.0, 0.50, 2.0),
                 "prices_above_step": _prices(4.0, 12.0, 1.0, 4.0)},
    "MiniMax-M3": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 1000000, "images": True,
                   "source": "minimax", "price_step_tokens": 512000, "prices": _prices(0.30, 1.20, 0.06, 0.30),
                   "prices_above_step": _prices(0.60, 2.40, 0.12, 0.60)},
    # Read 2026-11-12. Standard rates from 2026-12-01 (introductory 2.00/10.00/0.10 through 2026-11-30): a
    # projection made now stays right after the change. Implicit caching only, from 4,096 prompt tokens, with no
    # write charge (writes cost the input rate); thinking is billed as output.
    "gemini-4-carbon": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 1048576,
                        "images": True, "source": "google", "prices": _prices(4.0, 20.0, 0.20, 4.0)},
    # Read 2026-11-23 (Google's pricing page, Standard paid tier; aistudio.google.com/docs/whats-new-gemini-4-neon):
    # paid tier only, implicit caching only (no explicit caching) at 0.15 with no write charge (writes cost the input
    # rate); thinking is billed as output. Tier "balanced", as Carbon: Google places Neon below Carbon ("Use Carbon
    # for your hardest ... problems") but above its everyday Hydrogen, at Sonnet/Sol-class rates; "fast" here is the
    # helper class a consultation may run without its click (controller.standing_consent), which Neon is not. Same
    # tier as Carbon, so routing never escalates from one to the other; the connection's default (Carbon) runs
    # until a measurement says otherwise (model_routing).
    "gemini-4-neon": {"runtime": "codex", "tier": "balanced", "aliases": (), "context_window": 1048576,
                      "images": True, "source": "google", "read_on": "2026-11-23",
                      "page": "https://aistudio.google.com/docs/pricing#gemini-4-neon", "prices": _prices(3.0, 15.0, 0.15, 3.0)},
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


def capability_profile(connection_id, runtime, billing, catalog, *, effort, cache_ttl="1h", measured=None):
    """The ``model_routing`` profile of one connection, or None when it offers no known model.

    Only models in this connection's own catalog are listed, under the catalog's
    ID and in the catalog's order (routing breaks a tie between equal rates by
    that order), so a route can never name a model another account would serve.
    ``effort`` is one level for every model, or a function of the catalog's ID
    giving each model's own (a chat with no chosen effort runs each model at
    that model's default, ``chat_settings.request_effort``); a model's measured
    results are read at its effort. It has no default: the caller knows the
    chat, and a level written here would be a second default.
    """
    measured = MEASURED if measured is None else measured
    models = []
    for canonical, catalog_id in catalog_ids(catalog).items():
        facts = MODELS[canonical]
        if facts["runtime"] != runtime:
            continue
        price = rates(canonical, cache_ttl)
        level = effort(catalog_id) if callable(effort) else effort
        models.append({"id": catalog_id, "tier": facts["tier"], "effort": level, "images": facts["images"],
                       "context_window": facts["context_window"], "price_step_tokens": facts.get("price_step_tokens", 0),
                       "prices": price, "measured": dict(measured.get(canonical, {}).get(level, {}))})
    if not models:
        return None
    return {"connection_id": connection_id, "runtime": runtime, "billing": billing, "models": models}
