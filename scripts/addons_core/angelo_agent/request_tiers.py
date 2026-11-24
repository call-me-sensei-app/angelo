# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each model request's price tier, kept through every sum of usage (``above_step``).

Contract: doc/angelo/model_routing.rst, "Price steps per request". A model with a price step
(``model_profiles``: ``price_step_tokens``) bills a request whose prompt is above it at higher rates for that whole
request; a turn's, a request's or a chat's usage is a sum of requests. So each runtime's per-request counts (Codex's
``last`` usage per model response, Claude Code's per API message) are folded here into ``above_step``: for every step
the model table knows, ``{"<step>": {tokens, requests}}`` of the requests above it (a step no request passed is
absent). The fold of a sum is the sum of the folds, so a usage of any size is priced exactly as (total - above) at
base rates plus above at the higher rates (``split``). A usage without the field is a sum whose requests are unknown.

Standard library only; no bpy.
"""

from . import model_profiles

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")
STEPS = tuple(sorted({facts["price_step_tokens"] for facts in model_profiles.MODELS.values()
                      if facts.get("price_step_tokens")}))
MAX_TOKENS = 10 ** 13
MAX_REQUESTS = 10 ** 7


def _count(value, limit=MAX_TOKENS):
    return value if type(value) is int and 0 <= value <= limit else None


def summed(requests):
    """The four token fields summed over normalized per-request usages."""
    return {key: sum(item.get(key) or 0 for item in requests) for key in FIELDS}


def fold(requests):
    """``above_step`` of normalized per-request usages (``input_tokens`` counts the whole prompt), or None when one
    lacks a field."""
    output = {}
    for item in requests:
        values = [_count(item.get(key)) if isinstance(item, dict) else None for key in FIELDS]
        if None in values:
            return None
        for step in STEPS:
            if values[0] > step:
                bucket = output.setdefault(str(step), dict(dict.fromkeys(FIELDS, 0), requests=0))
                for key, value in zip(FIELDS, values):
                    bucket[key] += value
                bucket["requests"] += 1
    return output


def added(first, second):
    """The fold of two sums; None when either is unknown."""
    if first is None or second is None:
        return None
    output = {step: dict(bucket) for step, bucket in first.items()}
    for step, bucket in second.items():
        into = output.setdefault(step, dict(dict.fromkeys(FIELDS, 0), requests=0))
        for key in FIELDS + ("requests",):
            into[key] += bucket[key]
    return output


def clean(value, usage):
    """A bounded ``above_step`` that fits inside ``usage`` (each step's tokens at most the usage's), else None."""
    if not isinstance(value, dict) or not isinstance(usage, dict) or len(value) > len(STEPS):
        return None
    output = {}
    for step, bucket in value.items():
        if step not in {str(known) for known in STEPS} or not isinstance(bucket, dict):
            return None
        cleaned = {key: _count(bucket.get(key)) for key in FIELDS}
        cleaned["requests"] = _count(bucket.get("requests"), MAX_REQUESTS)
        if None in cleaned.values() or cleaned["requests"] < 1:
            return None
        if any(cleaned[key] > (usage.get(key) or 0) for key in FIELDS):
            return None
        output[step] = cleaned
    return output


def split(usage, step):
    """``(base, above)`` token dicts of ``usage`` at ``step``: the requests at or below it and those above it, or
    None when the usage does not say (no ``above_step``, or a step it was not folded at)."""
    tiers = usage.get("above_step") if isinstance(usage, dict) else None
    if not isinstance(tiers, dict) or step not in STEPS:
        return None
    above = {key: (tiers.get(str(step)) or {}).get(key, 0) for key in FIELDS}
    base = {key: (usage.get(key) or 0) - above[key] for key in FIELDS}
    if any(value < 0 for value in base.values()):
        return None
    return base, above


def from_messages(entries):
    """The fold of a Claude Code round's API messages, ``[uncached, read, write, output, ...]`` each."""
    return fold([{"input_tokens": sum(entry[:3]), "cached_input_tokens": entry[1],
                  "cache_write_input_tokens": entry[2], "output_tokens": entry[3]} for entry in entries])


def accumulate(totals, reports, usage):
    """Add one turn's split to a request's running ``totals``, counting the turns that reported one in ``reports``."""
    tiers = clean(usage.get("above_step"), usage) if isinstance(usage, dict) else None
    if tiers is not None:
        totals["above_step"] = added(totals.get("above_step", {}), tiers)
        reports["above_step"] = reports.get("above_step", 0) + 1


def kept(usage, value):
    """``usage`` (cleaned receipt fields) with ``value``'s split when it fits inside it; changed in place."""
    tiers = clean(value.get("above_step"), usage) if isinstance(value, dict) else None
    if tiers is not None and "turns" in usage:
        usage["above_step"] = tiers   # Receipt-only, like ``requests``: never in a model-read report.
    return usage


def dollars(usage, rates):
    """USD of normalized usage tokens (``input_tokens`` counts the whole prompt) at one tier's rates."""
    read, write = usage.get("cached_input_tokens") or 0, usage.get("cache_write_input_tokens") or 0
    fresh = max(0, (usage.get("input_tokens") or 0) - read - write)
    return (fresh * rates["input"] + read * rates["cache_read"] + write * rates["cache_write"]
            + (usage.get("output_tokens") or 0) * rates["output"]) / 1e6


def priced(usage, model, cache_ttl, dollars):
    """USD of ``usage`` with each request at its own tier, via ``dollars(tokens, rates)``; None when it can't be."""
    identity = model_profiles.resolve(model) if model else None
    if identity is None:
        return None
    step = model_profiles.MODELS[identity].get("price_step_tokens", 0)
    if not step:
        return dollars(usage, model_profiles.rates(identity, cache_ttl))
    parts = split(usage, step)
    if parts is None:
        return None
    base, above = parts
    return (dollars(base, model_profiles.rates(identity, cache_ttl))
            + dollars(above, model_profiles.rates(identity, cache_ttl, prompt_tokens=step + 1)))
