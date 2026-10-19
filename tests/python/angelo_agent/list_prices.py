# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Model usage of a turn and its API-equivalent price at ``model_profiles`` list prices.

``oneshot_metrics`` re-exports the names its callers use, so they keep importing them from there.

* ``usage_from_claude`` / ``usage_from_codex``: the model usage so far in a
  running turn, summed over its requests; ``claude_requests`` /
  ``codex_requests``: the same turn's usage per model request.
* ``list_cost``: tokens priced at the list prices of ``model_profiles``
  (API-equivalent dollars; a subscription is not billed this way), each cache
  write at the TTL the provider reported for it (``cache_writes``).
* Price steps. A model with a ``price_step_tokens`` charges its higher rates
  for one *request* whose prompt exceeds the step (Haiku 5.5 above 100k
  tokens). Usage summed over several requests is priced at the base rates
  when the sum is within the step (then every request is), per request when
  each request's usage is given and adds up to the sum, at the request's own
  tier when the usage says it is exactly one request, and otherwise not at
  all: ``None``, never the long-context tier by default.
* ``cap_cost``: the cost cap's price, which falls back to an upper bound (every
  token at the higher of the two rate tables) when ``list_cost`` cannot tell
  the tier, so a cap never lapses; the bound is marked.
* ``list_price_basis``: how ``list_cost`` priced a usage, for a report.

Standard library only (the add-on's ``model_profiles`` is imported lazily).
"""

# Cache writes by TTL: (TTL, the normalized usage field, the field of Claude's ``usage.cache_creation``).
WRITE_TTLS = (("5m", "cache_write_5m_input_tokens", "ephemeral_5m_input_tokens"),
              ("1h", "cache_write_1h_input_tokens", "ephemeral_1h_input_tokens"))
CODEX_UNSPLIT = "Codex usage reports no TTL split"   # OpenAI's usage counts writes without one.
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")


def _stream_message(item):
    """A stream message from ``(stamp, message)``, a raw stream line (``{"t_s", "task", "message"}``) or itself."""
    if isinstance(item, tuple):
        return item[1]
    return item["message"] if "t_s" in item and isinstance(item.get("message"), dict) else item


def _claude_responses(events):
    """The latest usage of each distinct model response in Claude Code stream lines (partial messages repeat ids)."""
    latest = {}
    for item in events:
        message = _stream_message(item)
        body = message.get("message") if message.get("type") == "assistant" else None
        if isinstance(body, dict) and body.get("id") and isinstance(body.get("usage"), dict):
            latest[body["id"]] = body["usage"]
    return latest


def claude_requests(events):
    """Each distinct model response's usage in Claude Code stream lines, normalized as ``usage_from_claude``."""
    requests = []
    for usage in _claude_responses(events).values():
        read, write = usage.get("cache_read_input_tokens") or 0, usage.get("cache_creation_input_tokens") or 0
        request = {"input_tokens": (usage.get("input_tokens") or 0) + read + write, "cached_input_tokens": read,
                   "cache_write_input_tokens": write, "output_tokens": usage.get("output_tokens") or 0}
        requests.append(dict(request, **(_split_of(usage) or {})))
    return requests


def usage_from_claude(events):
    """Tokens of the distinct model responses in Claude Code stream lines (``[(stamp, message)]`` or messages)."""
    latest = _claude_responses(events)
    total = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0}
    for usage in latest.values():
        read, write = usage.get("cache_read_input_tokens") or 0, usage.get("cache_creation_input_tokens") or 0
        total["input_tokens"] += (usage.get("input_tokens") or 0) + read + write
        total["cached_input_tokens"] += read
        total["cache_write_input_tokens"] += write
        total["output_tokens"] += usage.get("output_tokens") or 0
    return dict(total, requests=len(latest), **(claude_write_split(events) or {})) if latest else None


def claude_write_split(events):
    """Cache-write tokens per TTL that Claude reported for the distinct responses in stream lines, or None.

    Each response's ``usage.cache_creation`` splits its writes into 5-minute and 1-hour ones (Claude Code writes
    1-hour entries). A response without a split adds nothing to it, so the split can cover fewer writes than the
    total; ``cache_writes`` prices the rest at an assumed TTL. None when no response reported a split.
    """
    split, reported = {key: 0 for _, key, _ in WRITE_TTLS}, False
    for usage in _claude_responses(events).values():
        own = _split_of(usage)
        if own is not None:
            reported = True
            for key in split:
                split[key] += own[key]
    return split if reported else None


def _split_of(usage):
    """One Claude response's cache writes per TTL (``usage.cache_creation``), or None when it reported none."""
    creation = usage.get("cache_creation")
    if not isinstance(creation, dict):
        return None
    return {key: value if type(value) is int and value >= 0 else 0
            for _, key, field in WRITE_TTLS for value in (creation.get(field),)}


def with_write_split(usage, events):
    """``usage`` with the turn's cache writes per TTL from its Claude Code stream lines, when it has none yet.

    Usage that already has a split, a stream without one, or a split larger than ``usage``'s writes (another
    source, never mixed in) is returned as it is; otherwise a copy with the split. ``usage`` is never changed.
    """
    if not isinstance(usage, dict) or all(key in usage for _, key, _ in WRITE_TTLS):
        return usage
    split = claude_write_split(events)
    if split is None or sum(split.values()) > (usage.get("cache_write_input_tokens") or 0):
        return usage
    return dict(usage, **split)


def usage_from_codex(events):
    """The thread's running token total in Codex notifications (a one-shot thread has one turn)."""
    total, requests = None, 0
    for item in events:
        message = item[1] if isinstance(item, tuple) else item
        if message.get("method") == "thread/tokenUsage/updated":
            requests += 1
            usage = ((message.get("params") or {}).get("tokenUsage") or {}).get("total")
            if isinstance(usage, dict):
                total = {"input_tokens": usage.get("inputTokens") or 0,
                         "cached_input_tokens": usage.get("cachedInputTokens") or 0,
                         "cache_write_input_tokens": usage.get("cacheWriteInputTokens") or 0,
                         "output_tokens": usage.get("outputTokens") or 0}
    return dict(total, requests=requests) if total else None


def codex_requests(events):
    """Each model request's usage in Codex notifications (its update's ``last``), or None when one lacks it.

    Codex sends ``thread/tokenUsage/updated`` after each model response; an update whose running ``total`` did
    not grow repeats a request already counted (``provider_usage.CodexThreadUsage``).
    """
    requests, previous = [], None
    for item in events:
        message = _stream_message(item)
        if message.get("method") != "thread/tokenUsage/updated":
            continue
        usage = (message.get("params") or {}).get("tokenUsage") or {}
        total, last = usage.get("total"), usage.get("last")
        if not isinstance(total, dict):
            continue
        if previous is not None and not any((total.get(key) or 0) > (previous.get(key) or 0) for key in total):
            continue
        previous = total
        fields = ("inputTokens", "cachedInputTokens", "outputTokens")
        if not isinstance(last, dict) or any(type(last.get(field)) is not int for field in fields):
            return None
        requests.append({"input_tokens": last["inputTokens"], "cached_input_tokens": last["cachedInputTokens"],
                         "cache_write_input_tokens": last.get("cacheWriteInputTokens") or 0,
                         "output_tokens": last["outputTokens"]})
    return requests


def _ttl(cache_ttl):
    return "1h" if cache_ttl == "1h" else "5m"


def _split_recorded(usage):
    return all(type(usage.get(key)) is int for _, key, _ in WRITE_TTLS)


def cache_writes(usage, cache_ttl):
    """``({"5m": tokens, "1h": tokens}, assumed)``: a usage's cache writes by the TTL each was written with.

    The split is the provider's own (``claude_write_split``). Writes it does not attribute to a TTL (no split
    recorded, a response without one, or a Codex usage, which has no split) count at ``cache_ttl`` and are
    returned as ``assumed``. A split larger than the writes contradicts them and is ignored.
    """
    total = usage.get("cache_write_input_tokens") or 0
    split = {ttl: usage.get(key) for ttl, key, _ in WRITE_TTLS}
    if not all(type(value) is int and value >= 0 for value in split.values()) or sum(split.values()) > total:
        split = dict.fromkeys(split, 0)
    assumed = total - sum(split.values())
    split[_ttl(cache_ttl)] += assumed
    return split, assumed


def price_step(model):
    """The prompt tokens above which one request of ``model`` is billed at its higher rates (0: one tier)."""
    from angelo_agent import model_profiles
    identity = model_profiles.resolve(model) if model else None
    return model_profiles.MODELS[identity].get("price_step_tokens", 0) if identity else 0


def _one_request(usage):
    """Whether a usage says it is exactly one model request (never assumed: Angelo's sums count turns)."""
    return (type(usage.get("requests")) is int and usage["requests"] == 1 and usage.get("turns", 1) == 1
            and not usage.get("interrupted_turns"))


def _adds_up(usage, requests):
    """Whether per-request usages are complete and sum to ``usage`` (else they are another source)."""
    if not requests or not all(isinstance(r, dict) and type(r.get("input_tokens")) is int
                               and type(r.get("output_tokens")) is int for r in requests):
        return False
    return all(sum(r.get(key) or 0 for r in requests) == (usage.get(key) or 0) for key in TOKEN_FIELDS)


def _step_plan(usage, model, per_request=None):
    """``(plan, note)``: how the price step applies. Plans: flat, base, one, per_request, unknown."""
    step, prompt = price_step(model), usage.get("input_tokens")
    if not step:
        return "flat", None
    if prompt <= step:
        return "base", "every request within the %d-token price step (base rates)" % step
    if _one_request(usage):
        return "one", "one request above the %d-token price step (its higher rates)" % step
    if _adds_up(usage, per_request):
        return "per_request", "priced per request against the %d-token price step (%d requests)" % (
            step, len(per_request))
    count = usage.get("requests") if type(usage.get("requests")) is int else "an unrecorded number of"
    return "unknown", ("unknown: %d prompt tokens over %s requests exceed the %d-token per-request price step, "
                       "and no per-request usage was recorded" % (prompt, count, step))


def _rates(model, cache_ttl, prompt_tokens, upper=False):
    """``model_profiles.rates`` for one request; ``upper``: each rate at the higher of the two tables."""
    from angelo_agent import model_profiles
    if not upper:
        return model_profiles.rates(model, cache_ttl, prompt_tokens=prompt_tokens)
    base = model_profiles.rates(model, cache_ttl)
    above = model_profiles.rates(model, cache_ttl, prompt_tokens=price_step(model) + 1)
    return {key: max(base[key], above[key]) for key in base}


def _dollars(usage, model, cache_ttl, upper=False):
    """Unrounded USD of one request (or a sum known to sit in one tier) at the tier of its own prompt."""
    prompt = usage["input_tokens"]
    rates = {ttl: _rates(model, ttl, prompt, upper) for ttl, _, _ in WRITE_TTLS}
    read, write = usage.get("cached_input_tokens") or 0, usage.get("cache_write_input_tokens") or 0
    fresh = max(0, prompt - read - write)
    writes = sum(tokens * rates[ttl]["cache_write"] for ttl, tokens in cache_writes(usage, cache_ttl)[0].items())
    return (fresh * rates["5m"]["input"] + read * rates["5m"]["cache_read"] + writes
            + usage["output_tokens"] * rates["5m"]["output"]) / 1_000_000


def _priceable(usage, model):
    if not isinstance(usage, dict) or not model or type(usage.get("input_tokens")) not in (int, float):
        return False
    return usage.get("output_tokens") is not None and _rates(model, "5m", 0) is not None


def list_cost(usage, model, cache_ttl="5m", per_request=None):
    """API-equivalent dollars of a usage record at ``model_profiles`` list prices; None when either is unknown.

    Each cache write is priced at the TTL the provider reported for it; ``cache_ttl`` prices the writes it did not
    attribute (``cache_writes``). A model with one write rate charges it for both TTLs. ``per_request``: each
    model request's own usage, when recorded, for a model with a price step (module docstring).
    """
    if not _priceable(usage, model):
        return None
    plan, _ = _step_plan(usage, model, per_request)
    if plan == "unknown":
        return None
    if plan == "per_request":
        return round(sum(_dollars(request, model, cache_ttl) for request in per_request), 4)
    return round(_dollars(usage, model, cache_ttl), 4)


def cap_cost(usage, model, cache_ttl="5m", per_request=None):
    """``(USD, bound)``: the cost cap's price of a running usage. ``bound`` is ``"upper"`` when the price step's
    tier could not be told and every token was priced at the higher rates, else None; USD is None when unknown."""
    cost = list_cost(usage, model, cache_ttl, per_request)
    if cost is not None or not _priceable(usage, model) or _step_plan(usage, model, per_request)[0] != "unknown":
        return cost, None
    return round(_dollars(usage, model, cache_ttl, upper=True), 4), "upper"


def list_price_basis(usages, model, cache_ttl, unsplit="no TTL split recorded", per_request=None):
    """How ``list_cost`` priced the cache writes of these usages, and the price step when the model has one.

    At the TTLs the provider reported, or which TTL was assumed for the writes it did not attribute (``unsplit``
    says why there is no split; Codex usage reports none). A model with one write rate needs neither.
    ``per_request`` holds each usage's per-request usages (or None), in the order of ``usages``.
    """
    from angelo_agent import model_profiles
    pairs = [(usage, requests) for usage, requests in zip(usages, list(per_request or ()) + [None] * len(usages))
             if isinstance(usage, dict)]
    usages = [usage for usage, _ in pairs]
    ttl, reported = _ttl(cache_ttl), any(_split_recorded(usage) for usage in usages)
    assumed = sum(cache_writes(usage, ttl)[1] for usage in usages)
    rates = [model_profiles.rates(model, name) for name, _, _ in WRITE_TTLS] if model else [None]
    if None not in rates and rates[0]["cache_write"] == rates[1]["cache_write"]:
        text = "model_profiles list prices, the model's one cache-write rate"
    elif not reported:
        text = "model_profiles list prices, %s cache writes assumed (%s)" % (ttl, unsplit)
    elif assumed:
        text = ("model_profiles list prices, cache writes at the TTLs the provider reported; %d write tokens "
                "without a reported TTL at the %s rate" % (assumed, ttl))
    else:
        text = "model_profiles list prices, cache writes at the TTLs the provider reported"
    notes = [_step_plan(usage, model, requests)[1] for usage, requests in pairs
             if None not in rates and type(usage.get("input_tokens")) in (int, float)]
    notes = list(dict.fromkeys(note for note in notes if note))
    return "; ".join([text] + notes)
