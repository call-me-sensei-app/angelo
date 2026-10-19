# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Totals over benchmark records that keep an unrecorded measurement unknown, never zero.

A task that ran without a value recorded (its API calls, cost, time or tokens)
makes every total that includes it unknown (``None``, printed "unknown" or
"–"), and a report says how many trials or requests a figure covers. A
skipped task sent nothing, so it adds nothing to a total.

Costs have two bases: Claude Code's own estimate (``provider_usage.cost_usd``;
Claude runtimes only, Codex records none) and the harness's list price
(``list_price_usd``, set by ``bench_mcp_vs_angelo.price_records`` at
``model_profiles`` rates). A comparison is priced on one of them for every
side (``cost_basis``): Claude Code's estimate when it was recorded for
everything every side ran (the preferred basis for Claude), otherwise the
list price. One side's estimate is never compared with another's list price,
and the basis is stated once per comparison.

Used by ``bench_mcp_report``, ``scenario_scorecard`` and
``final_runs_report``. Standard library only.
"""

SKIPPED = "skipped"
COST_VALUES = {"reported": lambda record: (record.get("provider_usage") or {}).get("cost_usd"),
               "list": lambda record: record.get("list_price_usd")}
# A one-shot path's field for each basis (oneshot_metrics.angelo_path / mcp_path).
PATH_FIELDS = {"reported": "cost_reported_usd", "list": "cost_list_usd"}


def is_number(value):
    return type(value) in (int, float)


def ran(records):
    """The records whose task was sent (a skipped one sent nothing)."""
    return [record for record in records if record.get("status") != SKIPPED]


def known_sum(records, value):
    """``value(record)`` summed over the records that ran; None when any of them lacks it."""
    total = 0
    for record in ran(records):
        number = value(record)
        if not is_number(number):
            return None
        total += number
    return total


def recorded(values, unit):
    """``"n of m <unit>"``: how many of these values are known."""
    return "%d of %d %s" % (sum(is_number(value) for value in values), len(values), unit)


def cost_basis(estimates, unit):
    """``{"basis", "label"}``: the one cost basis of a comparison, from every Claude Code estimate in it.

    ``estimates`` holds Claude Code's ``cost_usd`` of everything every side of the comparison ran (a task, a
    turn, a request or a side's path), ``unit`` names those things. ``"reported"`` (Claude Code's estimate) when
    each is recorded, else ``"list"`` (the harness's list price), for both sides alike.
    """
    estimates = list(estimates)
    missing = sum(not is_number(value) for value in estimates)
    if estimates and not missing:
        return {"basis": "reported", "label": "Claude Code's own estimate (provider_usage.cost_usd) on both sides, "
                                              "recorded for all %d %s" % (len(estimates), unit)}
    why = ("Claude Code's estimate is missing for %d of %d %s (Codex records none)" % (missing, len(estimates), unit)
           if estimates else "nothing ran")
    return {"basis": "list", "label": "list price at model_profiles rates (list_price_usd) on both sides; %s" % why}


def record_basis(records, unit="tasks"):
    """``cost_basis`` of benchmark records of every side of one comparison (each record that ran)."""
    return cost_basis((COST_VALUES["reported"](record) for record in ran(records)), unit)


def input_per_call(record):
    """A task's input tokens per model API call, or None when either was not recorded."""
    calls, tokens = record.get("api_calls"), (record.get("provider_usage") or {}).get("input_tokens")
    return tokens / calls if calls and is_number(calls) and is_number(tokens) else None


def share(records, part, whole):
    """``part`` as a share of ``whole`` over the tasks that ran and recorded both, with its coverage when partial."""
    pairs = [(part(record), whole(record)) for record in ran(records)]
    known = [(a, b) for a, b in pairs if is_number(a) and is_number(b)]
    if not known or not sum(b for _, b in known):
        return "unknown"
    text = "%.0f%%" % (100 * sum(a for a, _ in known) / sum(b for _, b in known))
    return text if len(known) == len(pairs) else "%s (%d of %d tasks)" % (text, len(known), len(pairs))


def listed(values, scale=1, digits=3):
    """``[10, unknown, 12]``: per-trial totals as text, an unknown one named."""
    return "[%s]" % ", ".join("unknown" if value is None else str(round(value * scale, digits)) for value in values)


def covers(side_totals, key):
    """`` [2 of 3 trials]`` after a per-trial median that leaves some trials unknown, else nothing."""
    covered = side_totals.get(key + "_recorded")
    return " [%s]" % covered if covered and (side_totals.get(key) or {}).get("n", 0) < side_totals.get(
        "trials_with_all_five_timed", 0) else ""
