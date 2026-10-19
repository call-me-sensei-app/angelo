# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider-adaptive model tier policy: pure decisions, no I/O.

Pure decision functions over ONE connection's capability profile. They read no
credentials, launches no runtime, sends no request and never names a model of
another connection: routing stays inside the account the chat is bound to,
because billing boundaries belong to the person (``doc/angelo/model_routing.rst``).
``routing.py`` is unrelated; it owns route keys, handoffs and runtime contexts.

Units: prices are USD per million tokens, tokens are integers, latency is
milliseconds. A model's ``measured`` block comes from billed harness runs
(``bench_mcp_vs_angelo.py``, ``eval_live_agent.py``). Without measurement of
both the connection's default model and a candidate for a task kind, the
default runs, exactly as today: routing changes nothing without evidence.
"""

from dataclasses import dataclass

TIERS = ("fast", "balanced", "strong")
TASK_KINDS = frozenset({"scene_edit", "planning", "scene_read", "verification", "vision_check", "conversation"})
SESSION_KINDS = frozenset({"interactive", "autonomous", "benchmark", "subagent"})
BILLING = frozenset({"SUBSCRIPTION", "API_KEY", "LOCAL"})
PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
# Events that can move a request up one rung; anything else (transport,
# authentication, Stop) is not a quality signal and never escalates.
TRIGGERS = frozenset({"plan_rejected", "host_check_failed", "refusal", "low_confidence"})

# Used only when no model is flagged as the connection's default.
FALLBACK_TIER = "balanced"
MIN_TRIALS = 20            # measured tasks of a kind before its pass rate counts
QUALITY_TOLERANCE = 0.02   # a candidate may trail the best measured pass rate by this much
REJECTIONS_BEFORE_ESCALATION = 1  # a fast-tier rejection round is cheap; the next one escalates
AUTOMATIC_ESCALATIONS = 1  # per request; further rungs are only suggested to the person
STICKY_WINDOW, STICKY_ESCALATIONS = 5, 2  # hysteresis before de-escalating
# What Auto must have measured before it may move a chat's work: Angelo cannot
# yet tell an edit from a question before the model answers, so a model must
# hold parity on both. Requests beyond them reach a stronger model through the
# plan's low-confidence flag and the other escalation triggers.
AUTO_KINDS = ("scene_edit", "scene_read")
# A cheaper model is skipped when its measured
# p75 latency on a kind is more than this much worse than the reference's.
LATENCY_TOLERANCE = 0.25


@dataclass(frozen=True)
class Rung:
    model: str
    tier: str
    effort: str


@dataclass(frozen=True)
class Route:
    """The decision for one request on one connection."""
    connection_id: str
    model: str
    tier: str
    effort: str
    cache_ttl: object   # "1h", "5m" or None (the runtime's own default)
    measured: bool      # the choice rests on measured pass rates for this kind
    pinned: bool        # the person chose the model; escalation is only suggested
    reason: str
    ladder: tuple       # higher rungs on the SAME connection, nearest first


@dataclass(frozen=True)
class Step:
    """What to do after a trigger: ``escalate`` automatically or ``suggest`` it."""
    action: str
    rung: object
    reason: str


def _number(value, name):
    if type(value) not in {int, float} or not value >= 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


def clean_profile(profile):
    """Validate one connection's profile; raise ValueError with the field at fault."""
    if not isinstance(profile, dict):
        raise ValueError("A capability profile is a dict")
    connection = profile.get("connection_id")
    if not isinstance(connection, str) or not connection:
        raise ValueError("A profile belongs to exactly one connection_id")
    if profile.get("billing") not in BILLING:
        raise ValueError("billing must be one of " + ", ".join(sorted(BILLING)))
    models, seen = [], set()
    for entry in profile.get("models") or ():
        identity = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Each model needs a unique id")
        if entry.get("tier") not in TIERS:
            raise ValueError(f"{identity}: tier must be one of {', '.join(TIERS)}")
        seen.add(identity)
        prices = entry.get("prices")
        if prices is not None:
            prices = {field: _number(prices.get(field), f"{identity} price {field}") for field in PRICE_FIELDS}
        measured = {}
        for kind, record in (entry.get("measured") or {}).items():
            if kind not in TASK_KINDS or not isinstance(record, dict):
                raise ValueError(f"{identity}: unknown measured task kind {kind!r}")
            if type(record.get("tasks")) is not int or not 0 <= record.get("pass_rate", -1) <= 1:
                raise ValueError(f"{identity}/{kind}: measured needs integer tasks and pass_rate 0-1")
            measured[kind] = dict(record)
        models.append({"id": identity, "tier": entry["tier"], "effort": entry.get("effort") or "low",
                       # The runtime's default row: today's behavior and the quality baseline.
                       "default": entry.get("default") is True, "images": entry.get("images") is True,
                       "prices": prices, "measured": measured,
                       "context_window": int(entry.get("context_window") or 0),
                       # Prompt size above which the provider charges a higher rate
                       # (Haiku 5.5: 100k); 0 when pricing is flat.
                       "price_step_tokens": int(entry.get("price_step_tokens") or 0)})
    if not models:
        raise ValueError("A profile needs at least one model")
    if sum(m["default"] for m in models) > 1:
        raise ValueError("Only one model can be the connection's default")
    return {"connection_id": connection, "runtime": profile.get("runtime"), "billing": profile["billing"],
            "models": models}


def cache_ttl(runtime, session_kind, billing):
    """Prompt-cache TTL for a NEW native session; keep it for the session's life.

    Interactive chats match Claude Code's own default for its main
    conversation: 1 hour (2x input) on a subscription, 5 minutes (1.25x) on
    every key-billed route (``billing`` ``API_KEY``: a personal API key or a
    team's gateway key). Runs without human gaps use 5m on every route. A TTL
    change mid-session partly misses the cached prefix. Other runtimes expose
    no per-session TTL to Angelo yet (None).
    """
    if session_kind not in SESSION_KINDS:
        raise ValueError("Unknown session kind")
    if billing not in BILLING:
        raise ValueError("billing must be one of " + ", ".join(sorted(BILLING)))
    if runtime != "claude":
        return None
    return "1h" if session_kind == "interactive" and billing == "SUBSCRIPTION" else "5m"


def ttl_break_even_share(write_tokens, prefix_tokens, input_price, read_price):
    """Largest share of turns after an idle gap > 5 min at which 5m still beats 1h.

    Per turn, 1h pays 0.75 * input more for the turn's write; 5m pays
    (1.25 * input - read) for the whole prefix after each such gap.
    """
    penalty = prefix_tokens * (1.25 * input_price - read_price)
    return 0.75 * write_tokens * input_price / penalty if penalty > 0 else 1.0


def rebase_ceiling(profile, model_id, *, margin=0.9):
    """Highest context-rebase threshold that keeps every call below a price step.

    None when the model's pricing is flat. A rebase between turns
    (``context_rebase.py``) is cheaper than re-reading a context at a stepped
    rate on every later call.
    """
    model = next((m for m in clean_profile(profile)["models"] if m["id"] == model_id), None)
    if model is None:
        raise ValueError("Unknown model on this connection")
    return int(model["price_step_tokens"] * margin) or None


def _eligible(model, task):
    if task.get("images") and not model["images"]:
        return False
    needed = task.get("context_tokens") or 0
    return not (needed and model["context_window"] and needed > model["context_window"])


def _measured(model, kind):
    record = model["measured"].get(kind)
    return record if record and record["tasks"] >= MIN_TRIALS else None


def _cost(profile, model, kind):
    """Median USD per completed task: 0 locally, measured when known, else None."""
    if profile["billing"] == "LOCAL":
        return 0.0
    record = model["measured"].get(kind) or {}
    cost = record.get("cost_usd")
    return cost if type(cost) in {int, float} and cost >= 0 else None


def _nearest(models, tier):
    """Models of the wanted tier, else the nearest stronger, else the nearest weaker."""
    order = TIERS.index(tier)
    for distance in range(len(TIERS)):
        for index in (order + distance, order - distance):
            if 0 <= index < len(TIERS):
                found = [m for m in models if m["tier"] == TIERS[index]]
                if found:
                    return found
    return []


def _ladder(models, chosen):
    above = [m for m in models if TIERS.index(m["tier"]) > TIERS.index(chosen["tier"])]
    above.sort(key=lambda m: TIERS.index(m["tier"]))
    return tuple(Rung(m["id"], m["tier"], m["effort"]) for m in above)


def route(profile, task, *, pinned_model="", session_kind="interactive"):
    """Choose a model for one request; never another connection's model.

    ``task``: ``kind`` (TASK_KINDS), optional ``images`` (count), optional
    ``context_tokens`` (what the request needs to fit), optional
    ``recent_escalations`` (escalated requests among the last STICKY_WINDOW).
    """
    profile = clean_profile(profile)
    kind = task.get("kind")
    if kind not in TASK_KINDS:
        raise ValueError("Unknown task kind")
    ttl = cache_ttl(profile["runtime"], session_kind, profile["billing"])
    models = [m for m in profile["models"] if _eligible(m, task)]
    if pinned_model:
        chosen = next((m for m in profile["models"] if m["id"] == pinned_model), None)
        if chosen is None:
            raise ValueError("The pinned model is not on this connection; routing never changes accounts")
        return Route(profile["connection_id"], chosen["id"], chosen["tier"], chosen["effort"], ttl,
                     bool(_measured(chosen, kind)), True, "the person chose this model",
                     _ladder(models, chosen))
    if not models:
        raise ValueError("No model on this connection fits the request (images or context)")
    baseline = next((m for m in models if m["default"]), None) or _nearest(models, FALLBACK_TIER)[0]
    measured = [(m, record) for m, record in ((m, _measured(m, kind)) for m in models) if record]
    if measured and (_measured(baseline, kind) or not any(m["default"] for m in models)):
        # Parity is judged against the best measured model, which includes the
        # default whenever there is one: no model is admitted on cost alone.
        best = max(record["pass_rate"] for _, record in measured)
        good = [m for m, record in measured if record["pass_rate"] >= best - QUALITY_TOLERANCE]

        def rank(model):
            cost = _cost(profile, model, kind)
            latency = model["measured"][kind].get("p75_ms", float("inf"))
            # Unknown cost ranks by tier: a cheaper tier is the documented proxy.
            return (cost is None, cost if cost is not None else TIERS.index(model["tier"]), latency)
        chosen = min(good, key=rank)
        reason = "cheapest model within the measured quality bar for " + kind
    else:
        chosen = baseline
        reason = "no measured parity for " + kind + " on this connection: the default runs, as today"
    ladder = _ladder(models, chosen)
    if task.get("recent_escalations", 0) >= STICKY_ESCALATIONS and ladder:
        # De-escalate only after the stronger tier stops being needed.
        chosen = next(m for m in models if m["id"] == ladder[0].model)
        reason += "; held one tier up after repeated escalations"
        ladder = _ladder(models, chosen)
    return Route(profile["connection_id"], chosen["id"], chosen["tier"], chosen["effort"], ttl,
                 bool(_measured(chosen, kind)), False, reason, ladder)


def _auto_cost(profile, model):
    costs = [_cost(profile, model, kind) for kind in AUTO_KINDS]
    return None if None in costs else sum(costs)


def _fast_enough(model, reference, kind):
    """The candidate's measured p75 on ``kind`` is within LATENCY_TOLERANCE of the reference's."""
    candidate, baseline = model["measured"][kind].get("p75_ms"), reference["measured"][kind].get("p75_ms")
    if type(candidate) not in {int, float}:
        return False
    return type(baseline) not in {int, float} or candidate <= baseline * (1 + LATENCY_TOLERANCE)


def auto_route(profile, *, task=None, session_kind="interactive", recent_escalations=0):
    """The Default row's Auto choice behind the measured-capability gate, or None.

    A model is eligible only when it, and the best model it is compared with,
    were each measured on every AUTO_KINDS kind over MIN_TRIALS tasks, its
    pass rate on each is within QUALITY_TOLERANCE of the best, and its p75
    latency on each is at most LATENCY_TOLERANCE worse than the reference's
    (a candidate without a measured p75 is not eligible). Auto returns a
    route only for an eligible model in a cheaper tier than that best model;
    otherwise None, and the connection's Default model runs exactly as before.
    Hysteresis: after STICKY_ESCALATIONS escalated requests of the last
    STICKY_WINDOW, the next eligible tier up is used, or None at the top.
    """
    profile = clean_profile(profile)
    task = dict(task or {}, kind=AUTO_KINDS[0])
    models = [m for m in profile["models"] if _eligible(m, task)]
    complete = [m for m in models if all(_measured(m, kind) for kind in AUTO_KINDS)]
    if len(complete) < 2:
        return None
    best = {kind: max(m["measured"][kind]["pass_rate"] for m in complete) for kind in AUTO_KINDS}
    reference = max(complete, key=lambda m: (sum(m["measured"][k]["pass_rate"] for k in AUTO_KINDS),
                                             TIERS.index(m["tier"])))
    good = [m for m in complete if TIERS.index(m["tier"]) < TIERS.index(reference["tier"])
            and all(m["measured"][k]["pass_rate"] >= best[k] - QUALITY_TOLERANCE for k in AUTO_KINDS)
            and all(_fast_enough(m, reference, k) for k in AUTO_KINDS)]

    def rank(model):
        cost = _auto_cost(profile, model)
        latency = max(model["measured"][kind].get("p75_ms", float("inf")) for kind in AUTO_KINDS)
        return (TIERS.index(model["tier"]), cost is None, cost or 0, latency)
    good.sort(key=rank)
    if recent_escalations >= STICKY_ESCALATIONS:
        good = [m for m in good if TIERS.index(m["tier"]) > TIERS.index(good[0]["tier"])] if good else []
    if not good:
        return None
    chosen = good[0]
    return Route(profile["connection_id"], chosen["id"], chosen["tier"], chosen["effort"],
                 cache_ttl(profile["runtime"], session_kind, profile["billing"]), True, False,
                 "Auto: measured parity with %s on %s" % (reference["id"], ", ".join(AUTO_KINDS)),
                 _ladder(models, chosen))


def next_step(current, trigger, *, committed, escalations=0, rejections=0):
    """The response to one trigger inside a request, or None.

    ``committed``: a live mutation may have happened (any APPLY commit, partial
    or uncertain result). Then nothing is retried automatically; a stronger
    model is only suggested for the person's next message, as for every failed
    edit today. ``escalations``/``rejections`` count this request so far.
    """
    if trigger not in TRIGGERS or not current.ladder:
        return None
    rung = current.ladder[0]
    if current.pinned:
        return Step("suggest", rung, "the person pinned this model; never switch it automatically")
    if committed:
        return Step("suggest", rung, "a change may already be live; inspect before retrying")
    if trigger == "plan_rejected" and rejections < REJECTIONS_BEFORE_ESCALATION:
        return None  # one more cheap round on the same model, as CLAUDE_PLAN_REJECTIONS allows
    if escalations >= AUTOMATIC_ESCALATIONS:
        return Step("suggest", rung, "the automatic escalation for this request is used")
    return Step("escalate", rung, trigger + " before anything was committed")


def delegation_value(lead_prices, helper_prices, work):
    """Compare a read-only fast-tier consultation with the lead doing the work itself.

    ``work`` (tokens): ``helper_prompt`` (the child's whole first prompt; today a
    fresh session writes all of it), ``helper_output``, ``reply`` (what returns
    to the lead), ``bulk`` (what the lead's own reads would add to its history),
    ``remaining_calls`` (later lead calls that re-read its history),
    ``lead_round_usd`` (one lead model round) and ``lead_rounds_saved``.
    Returns a dict with both costs and ``pays_off``; None without prices
    (subscriptions report usage, not a price, for some models).
    """
    if lead_prices is None or helper_prices is None:
        return None
    remaining = work.get("remaining_calls", 0)

    def carried(tokens):  # written once into the lead's history, then re-read
        return tokens * (lead_prices["cache_write"] + remaining * lead_prices["cache_read"]) / 1e6

    helper = (work["helper_prompt"] * helper_prices["cache_write"]
              + work.get("helper_output", 0) * helper_prices["output"]) / 1e6
    consult = helper + work["lead_round_usd"] + carried(work.get("reply", 0))
    alone = work.get("lead_rounds_saved", 0) * work["lead_round_usd"] + carried(work.get("bulk", 0))
    return {"consult_usd": round(consult, 6), "alone_usd": round(alone, 6), "helper_usd": round(helper, 6),
            "pays_off": consult < alone}
