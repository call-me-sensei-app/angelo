# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the chat's usage meter says: the control's label and the detail card's lines, translated.

Contract: doc/angelo/agent_chat.rst, "Usage meter". Numbers come from ``usage_meter`` (tokens and the
estimate per connection mode) and ``usage_windows`` (a plan's windows); this module only decides how
they read. The rules:

* Money is always an estimate: every figure starts with "≈" and the card says *Estimate*. A plan's
  usage never gets a dollar figure; it says *Included in your plan*.
* A lower bound (a request a runtime did not fully report) says so; a model without a published
  price is counted in tokens and named as not priced.
* Numbers are compact and locale-neutral (``212k``, ``1.8M``, ``$0.42``); times are numeric clock
  times (``18:20``, or the date when not today), so no English word reaches another language.

No bpy, GPU or I/O: the canvas (``chat_usage``) draws what this returns. Pure functions of their inputs,
so the label is computed once per change, not per frame.
"""

import re
import time

from . import model_profiles, provider_registry, usage_meter
from .ui_text import iface_, n_, plural_

PLAN, PAID = "plan", "paid"
WARN_AT = 0.8  # A plan window at or above this share is drawn amber.
SEGMENTS = (("cache_read", n_("Cache reads")), ("cache_write", n_("Cache writes")),
            ("input", n_("Uncached input")), ("output", n_("Output")))
WINDOW_NAMES = {"five_hour": n_("Current 5-hour window"), "seven_day": n_("This week"),
                "seven_day_opus": n_("This week (Opus)"), "seven_day_sonnet": n_("This week (Sonnet)")}
PLAN_NAMES = {"ANTHROPIC": "Claude", "OPENAI": "ChatGPT"}
SHORT_WINDOWS = {"five_hour": n_("5-hour limit"), "seven_day": n_("Weekly limit"),
                 "seven_day_opus": n_("Weekly Opus limit"), "seven_day_sonnet": n_("Weekly Sonnet limit")}


def billing(mode):
    """``PLAN`` for a subscription (or a flat-rate plan key), else ``PAID`` (an API key or a team connection)."""
    spec = provider_registry.SPECS.get(mode) or {}
    return PLAN if spec.get("method") == "SUBSCRIPTION" or spec.get("plan_terms") else PAID


def tokens(count):
    """``940``, ``8.4k``, ``212k``, ``1.8M``."""
    if count < 1000:
        return str(count)
    if count < 10000:
        return iface_("{count}k", count=f"{count / 1000:.1f}")
    if count < 1000000:
        return iface_("{count}k", count=str(round(count / 1000)))
    return iface_("{count}M", count=f"{count / 1000000:.1f}" if count < 100000000 else str(round(count / 1000000)))


def turn_label(count):
    """The live row's token part for a running turn (``usage_meter.turn_tokens``): "562 tokens", "8.4k tokens"."""
    return plural_(count, "{count} token", "{count} tokens") if count < 1000 else \
        iface_("{tokens} tokens", tokens=tokens(count))


def money(usd):
    """``≈ $0.42``; under a cent ``≈ < $0.01``; nothing spent ``$0``."""
    if usd <= 0:
        return "$0"
    amount = "< $0.01" if usd < 0.005 else f"${usd:.2f}" if usd < 100 else f"${usd:,.0f}"
    return iface_("≈ {amount}", amount=amount)


def clock(epoch, now=None):
    """Numeric local time ("18:20", or the date when not today), or "" when the platform cannot convert it."""
    try:
        moment, today = time.localtime(epoch), time.localtime(time.time() if now is None else now)
        return time.strftime("%H:%M" if moment[:3] == today[:3] else "%Y-%m-%d %H:%M", moment)
    except (OverflowError, OSError, ValueError):
        return ""


def model_label(model):
    """``claude-opus-5-5`` → ``Claude Opus 5.5``; ``gpt-6.1-sol`` → ``GPT-6.1 Sol``; others as given."""
    match = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)", model or "")
    if match:
        return "Claude {} {}.{}".format(match.group(1).title(), match.group(2), match.group(3))
    match = re.fullmatch(r"gpt-([\d.]+)-([a-z]+)", model or "")
    return "GPT-{} {}".format(match.group(1), match.group(2).title()) if match else (model or "")


def connection_label(mode, models):
    spec = provider_registry.SPECS.get(mode) or {}
    name = spec.get("name") or mode
    if spec.get("method") == provider_registry.TEAM:
        name = iface_("Team · {provider}", provider=name)
    names = [model_label(model) for model in models[:2]]
    return " · ".join([name] + names)


def _sum(totals, kind=None):
    keys = usage_meter.TOKEN_KEYS + usage_meter.COUNT_KEYS
    output = dict(dict.fromkeys(keys, 0), usd=0.0)
    for mode, bucket in totals.items():
        if kind is None or billing(mode) == kind:
            for key in keys:
                output[key] += bucket.get(key, 0)
            output["usd"] += bucket.get("usd", 0.0)
    output["tokens"] = sum(output[key] for key in usage_meter.TOKEN_KEYS)
    return output


def cache_share(total):
    """Cache reads as a share of all input tokens (cache reads, cache writes and uncached input), or None."""
    prompt = total["input"] + total["cache_read"] + total["cache_write"]
    return total["cache_read"] / prompt if prompt else None


def cached(total):
    """"93% cached" for a total, or None when it had no input."""
    share = cache_share(total)
    return None if share is None else iface_("{percent}% cached", percent=_percent(share))


def _percent(share):
    """Whole percent; never rounds a partial share up to 100 or down to 0."""
    value = round(share * 100)
    return 99 if value == 100 and share < 1 else 1 if value == 0 and share > 0 else value


def bar(totals, mode, windows=None, *, live=False):
    """The sticky bar's parts for a chat whose current connection is ``mode``.

    ``{"tokens", "cached", "money", "plan", "window", "ring", "warn"}``, or None before the chat's first counted request
    (a chat from before the meter would otherwise read "0 tokens"): the total tokens (a running request ends
    them in "…"), the share of input tokens read from the cache ("93% cached", ``cache_share``), the estimate of the chat's paid usage (None when there is
    none), "Included in your … plan" when the chat's connection is a plan, and that plan's most-used window
    as text and as the ring's share (amber from ``WARN_AT`` or when the plan refused a request).
    """
    total, paid = _sum(totals), _sum(totals, PAID)
    if not total["requests"] and not live:
        return None
    tokens_text = iface_("{tokens} tokens", tokens=tokens(total["tokens"])) + ("…" if live else "")
    cached_text = cached(total)
    used = paid["requests"] or paid["tokens"]  # A running request has tokens before it counts as a request.
    money_text = money(paid["usd"]) if used and (paid["usd"] > 0 or not paid["unpriced"]) else None
    plan_text = window_text = ring = None
    if billing(mode) == PLAN:
        name = PLAN_NAMES.get((provider_registry.SPECS.get(mode) or {}).get("provider"))
        plan_text = iface_("Included in your {plan} plan", plan=name) if name else iface_("Included in your plan")
        busiest = max((windows or {}).get("windows", ()), key=lambda window: window["used"], default=None)
        if busiest is not None:
            ring = busiest["used"]
            limit = (iface_(SHORT_WINDOWS[busiest["kind"]]) if busiest["kind"] in SHORT_WINDOWS
                     else _window_name(busiest.get("minutes")))
            window_text = iface_("{limit}: {percent}% used", limit=limit, percent=round(busiest["used"] * 100))
    warn = ring is not None and (ring >= WARN_AT or bool((windows or {}).get("limited")))
    return {"tokens": tokens_text, "cached": cached_text, "money": money_text, "plan": plan_text,
            "window": window_text, "ring": ring, "warn": warn}


def _window_name(minutes):
    same = {300: SHORT_WINDOWS["five_hour"], 10080: SHORT_WINDOWS["seven_day"]}.get(minutes)
    if same:  # Codex's windows of the same length as Claude's read the same.
        return iface_(same)
    return (iface_("{hours}-hour limit", hours=minutes // 60) if minutes and minutes < 1440
            else iface_("{days}-day limit", days=round(minutes / 1440)) if minutes else iface_("Usage limit"))


def card(totals, *, last=None, windows=None, chats=None, now=None):
    """The detail card's content for one scope (a chat's totals, or every chat since Angelo started).

    Returns ``{"headline", "badge", "badge_text", "summary", "segments", "rows", "last", "windows", "notes"}``:
    ``badge`` is ``"estimate"``, ``"plan"`` or None; ``segments`` are ``(key, label, tokens, share)``;
    ``rows`` are ``(label, value)`` per connection; ``windows`` are ``(label, used, detail, warn)``;
    ``notes`` are translated sentences, the estimate's basis first.
    """
    now = time.time() if now is None else now
    total, paid, plan = _sum(totals), _sum(totals, PAID), _sum(totals, PLAN)
    plans = sorted({PLAN_NAMES.get((provider_registry.SPECS.get(mode) or {}).get("provider"), "")
                    for mode in totals if billing(mode) == PLAN} - {""})
    if paid["requests"] or paid["tokens"]:
        headline, badge, badge_text = money(paid["usd"]), "estimate", iface_("Estimate")
    else:
        headline = iface_("{tokens} tokens", tokens=tokens(total["tokens"]))
        badge = "plan" if total["requests"] else None
        badge_text = (iface_("Included in your {plan} plan", plan=" / ".join(plans)) if plans
                      else iface_("Included in your plan"))
    share = cache_share(total)
    parts = [iface_("{tokens} tokens", tokens=tokens(total["tokens"])),
             plural_(total["requests"], "{count} request", "{count} requests")]
    if chats is not None:
        parts.append(plural_(chats, "{count} chat", "{count} chats"))
    if share is not None:
        parts.append(cached(total))
    segments = [(key, iface_(label), total[key], total[key] / total["tokens"] if total["tokens"] else 0)
                for key, label in SEGMENTS]
    rows = []
    for mode, bucket in sorted(totals.items(), key=lambda item: (billing(item[0]) == PLAN, -item[1]["usd"])):
        bucket_tokens = sum(bucket[key] for key in usage_meter.TOKEN_KEYS)
        value = (iface_("{tokens} tokens · included", tokens=tokens(bucket_tokens)) if billing(mode) == PLAN
                 else money(bucket["usd"]) if bucket["usd"] > 0 else iface_("{tokens} tokens", tokens=tokens(bucket_tokens)))
        rows.append((connection_label(mode, bucket.get("models", [])), value))
    last_line = None
    if last:  # The chat's last request: its tokens, how much of its input was cached and, when paid, its estimate.
        last_tokens = sum(last.get(key, 0) for key in usage_meter.TOKEN_KEYS)
        last_parts = [iface_("{tokens} tokens", tokens=tokens(last_tokens)), cached(last)]
        if billing(last.get("mode")) == PAID and not last.get("unpriced"):
            last_parts.append(money(last.get("usd", 0.0)))
        last_line = " · ".join(part for part in last_parts if part)
    notes = _notes(totals, paid, plan, windows, now)
    return {"headline": headline, "badge": badge, "badge_text": badge_text, "summary": " · ".join(parts), "segments": segments,
            "rows": rows, "last": last_line, "windows": window_lines(windows, now), "notes": notes}


def window_lines(windows, now=None):
    """``(label, used, detail, warn)`` per plan window: ``used`` 0-1 for the bar, detail the reset time."""
    output = []
    for window in (windows or {}).get("windows", ()):
        minutes = window.get("minutes")
        label = (iface_(WINDOW_NAMES[window["kind"]]) if window["kind"] in WINDOW_NAMES
                 else iface_("{hours}-hour window", hours=minutes // 60) if minutes and minutes < 1440
                 else iface_("{days}-day window", days=round(minutes / 1440)) if minutes
                 else iface_("Usage window"))
        reset = clock(window["resets_at"], now) if window.get("resets_at") else ""
        detail = (iface_("{percent}% used · resets {time}", percent=round(window["used"] * 100), time=reset) if reset
                  else iface_("{percent}% used", percent=round(window["used"] * 100)))
        output.append((label, min(1.0, window["used"]), detail, window["used"] >= WARN_AT))
    return output


def _notes(totals, paid, plan, windows, now):
    notes = []
    providers = sorted({(provider_registry.SPECS.get(mode) or {}).get("provider", "") for mode in totals
                        if billing(mode) == PAID})
    team = any((provider_registry.SPECS.get(mode) or {}).get("method") == provider_registry.TEAM for mode in totals)
    if paid["requests"]:
        notes.append(iface_("Estimated at published list prices (read {date}). Your organization's bill comes "
                            "from its providers and can differ.", date=model_profiles.READ_ON) if team else
                     iface_("Estimated at published list prices (read {date}). Your bill comes from {provider} "
                            "and can differ.", date=model_profiles.READ_ON,
                            provider=" / ".join(provider_registry.PROVIDER_NAMES.get(p, p) for p in providers if p)))
    if cache_share(_sum(totals)) is not None:
        notes.append(iface_("Cached is the share of input tokens the provider read from its cache instead of "
                            "processing them again."))
    if plan["requests"]:
        names = sorted({PLAN_NAMES.get((provider_registry.SPECS.get(mode) or {}).get("provider"), "")
                        for mode in totals if billing(mode) == PLAN} - {""})
        if names:
            notes.append(iface_("Your {plan} plan covers this usage.", plan=" / ".join(names)))
    if windows and windows.get("windows"):
        minutes = max(0, round((now - windows["at"]) / 60))
        if minutes < 1:
            notes.append(iface_("Plan usage as reported with a reply under a minute ago. Angelo doesn't check it "
                                "in between."))
        else:
            notes.append(plural_(minutes, "Plan usage as reported with a reply {count} minute ago. Angelo doesn't "
                                 "check it in between.", "Plan usage as reported with a reply {count} minutes ago. "
                                 "Angelo doesn't check it in between."))
    if windows and windows.get("overage"):
        notes.append(iface_("The plan's limit is reached and extra usage is in use: the provider charges it "
                            "at API prices."))
    if paid["partial"] or plan["partial"]:
        notes.append(plural_(paid["partial"] + plan["partial"], "At least: {count} request was not fully reported.",
                             "At least: {count} requests were not fully reported."))
    if paid["unpriced"]:
        notes.append(plural_(paid["unpriced"], "{count} request used a model without a published price and is "
                             "not in the estimate.", "{count} requests used a model without a published price "
                             "and are not in the estimate."))
    return notes
