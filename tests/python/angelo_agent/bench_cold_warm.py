# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cold start and returning user: each trial classified by its launch position, never by a cache hit.

Benchmarks report two numbers, both sides measured in both. A trial's first
task is a **cold start** when it is the first chat after a fresh launch, on
either side: every trial of an ordinary run (each is a fresh process pair,
scene and session) and the first launch of a ``--returning-user`` run. A
later trial of a ``--returning-user`` run is a relaunch on a kept profile: a
**returning user** (``launch_of``). Whether the first request read a cached
opening, which any session on the account within the cache's lifetime can
leave behind, is shown beside it for information and never changes the class
(``cache_state``).

A trial's start number is its first task's wall time and list price plus
every warm-up of that launch, its wall time and its price, each counted once
(Angelo's background Codex warm-up: ``eval_receipts.warm_ups_since``), and any
time the first message waited for a readied opening (``--angelo-opening-wait``,
a labelled extra). The wait and the warm-up it waited for overlap, so the
longer of the two is added, never both. The runtime's launch (Claude Code or the Codex app-server,
and their checks) is inside the first task on both sides; the app's own launch
(Angelo or stock Blender) is outside it on both sides. A first task whose
runtime was already running is flagged ``prestarted``: its launch is not in the
number. A warm-up's wall time is added in full, also when it ran beside the
first task, so the number is an upper bound then. Angelo's bundled Codex (the
cache-keyed runtime) sends no warm-up at all (``codex_bases.warm``
refuses on a process with cache-affinity keys); only a stock Codex still warms
up, and any warm-up that ran is counted.

Where the first request is read, in order; a value no record gives is ``None``
(printed "unknown"), never guessed:

* MCP side: the first response of the first task in the trial's raw stream
  (Claude Code's ``claude-stream.jsonl``: ``usage.cache_read_input_tokens``
  and ``cache_creation_input_tokens``; Codex's ``codex-stream.jsonl``: the
  first ``thread/tokenUsage/updated``'s ``last``), else, on Codex, the task's
  recorded ``first_request``.
* Angelo: the first response in the trial's Claude Code transcript, else the
  first chat's first request in the Codex request log (``qa/requests.jsonl``,
  written with ``--request-log``; base warm-ups are left out, as in
  ``final_runs_report.returning_columns``).
* Either side: the first task's own usage when that task made exactly one
  model request, which is then the first request.

Codex reports no cache writes: OpenAI caches a prompt's prefix itself and
charges no write, and the app-server's ``cacheWriteInputTokens`` is a zero
default (``provider_usage.codex_total``), not a measured zero. A Codex first
request therefore has ``write`` None, and its uncached input (input minus
read), which is what the provider then cached, stands in for the write in
``cache_state``; ``classified_by`` says which decided. Standard library only.
"""

import json
import statistics
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.

MARK = "trial_first_request"   # The key on each trial's first record (bench_mcp_vs_angelo.gather).
SIDES = ("mcp", "angelo")
LABELS = {"mcp": "MCP", "angelo": "Angelo"}
STARTS = {"fresh": "cold", "relaunch": "returning"}
RULE = ("A trial's first task is a cold start when it is the first chat after a fresh launch, on either side (every "
        "trial of an ordinary run, the first launch of a returning-user run), and a returning-user start after a "
        "relaunch on a kept profile; never by a cache hit, which is shown for information. A start includes every "
        "warm-up of that launch (wall time and price, counted once) and any wait for a readied opening; the "
        "runtime's launch is inside the first task on both sides, the app's own launch outside it on both sides.")
FIELDS = ("read", "write", "input", "source")


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def _lines(path):
    """Parsed JSON lines of a raw stream or transcript, lazily; an unreadable line is skipped."""
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _message(entry, task):
    """The message of a raw stream line of ``task`` (``{"t_s", "task", "message"}``), or a transcript entry."""
    if "t_s" in entry and isinstance(entry.get("message"), dict):
        return entry["message"] if task is None or entry.get("task") == task else None
    return entry


def from_claude(entries, task=None, source="claude stream"):
    """The first distinct response's cache read and write in Claude Code stream lines or a session transcript.

    A response streams as several lines with one id; the last usage seen for that id is its final one.
    """
    first = usage = None
    for entry in entries:
        message = _message(entry, task)
        body = message.get("message") if message and message.get("type") == "assistant" else None
        if not isinstance(body, dict) or not body.get("id"):
            continue
        if first is not None and body["id"] != first:
            break
        first = body["id"]
        usage = body["usage"] if isinstance(body.get("usage"), dict) else usage
    if usage is None:
        return None
    read, write, fresh = (_tokens(usage.get(key)) for key in
                          ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens"))
    return {"read": read, "write": write, "input": None if None in (read, write, fresh) else fresh + read + write,
            "source": source}


def _codex(input_tokens, cached, written, source):
    # A zero write is Codex's default where the provider reports none, so only a positive one is a measurement.
    return {"read": _tokens(cached), "write": _tokens(written) or None, "input": _tokens(input_tokens),
            "source": source}


def from_codex(entries, task=None, source="codex stream"):
    """The first request's tokens in Codex notifications: the first ``thread/tokenUsage/updated``'s ``last``."""
    for entry in entries:
        message = _message(entry, task)
        if message and message.get("method") == "thread/tokenUsage/updated":
            last = ((message.get("params") or {}).get("tokenUsage") or {}).get("last")
            if isinstance(last, dict):
                return _codex(last.get("inputTokens"), last.get("cachedInputTokens"),
                              last.get("cacheWriteInputTokens"), source)
    return None


def from_usage(task, runtime):
    """A first task's own usage when it made exactly one model request (that request's), else None."""
    usage = task.get("provider_usage") if isinstance(task.get("provider_usage"), dict) else {}
    requests = usage.get("requests") if usage.get("requests") is not None else task.get("api_calls")
    if requests != 1:
        return None
    source = "first task's usage (one request)"
    if runtime == "codex":
        return _codex(usage.get("input_tokens"), usage.get("cached_input_tokens"),
                      usage.get("cache_write_input_tokens"), source)
    return {"read": _tokens(usage.get("cached_input_tokens")), "write": _tokens(usage.get("cache_write_input_tokens")),
            "input": _tokens(usage.get("input_tokens")), "source": source}


def mcp_trial(folder, data):
    """The first request of a recorded MCP trial; ``folder`` holds its ``trial.json`` (``data``) and raw stream."""
    tasks = data.get("tasks") or []
    if not tasks:
        return None
    first, codex = tasks[0], data.get("runtime") == "codex"
    stream = Path(folder) / ("codex-stream.jsonl" if codex else "claude-stream.jsonl")
    found = (from_codex if codex else from_claude)(_lines(stream), first.get("task")) if stream.is_file() else None
    recorded = first.get("first_request")
    if found is None and codex and isinstance(recorded, dict):
        found = _codex(recorded.get("input"), recorded.get("cached"), None, "trial.json first_request")
    return found or from_usage(first, "codex" if codex else "claude")


def angelo_trial(transcript, request_log, first_task):
    """The first request of a recorded Angelo trial: its transcript, its request log, else its first task's usage.

    ``first_task`` is the harness's own first task record (``eval-live-agent.json``), whose receipt names the
    runtime; either path may be None or missing.
    """
    found = from_claude(_lines(transcript), source="transcript") if transcript and Path(transcript).is_file() else None
    if found is None and request_log and Path(request_log).is_file():
        import eval_request_log
        chats = [t for t in eval_request_log.summary(request_log) if not t["warm_up"] and t["requests"]]
        if chats:
            first = chats[0]["first_request"]
            found = _codex(first.get("input"), first.get("cached"), None, "request log")
    runtime = (first_task.get("receipt") or {}).get("runtime") or first_task.get("runtime") or "claude"
    return found or from_usage(first_task, runtime)


def cache_state(found):
    """``(state, classified_by)``: whether the first request ``"wrote"`` or ``"read"`` the cached opening.

    Information only, beside the launch-position class. A request that read nothing wrote the opening even when
    it wrote nothing (a prompt too short to cache does neither); ``(None, None)`` when unknown.
    """
    read = (found or {}).get("read")
    if read is None:
        return None, None
    if found.get("write") is not None:
        written, basis = found["write"], "cache write"
    elif found.get("input") is not None:
        written, basis = found["input"] - read, "uncached input"
    else:
        return None, None
    return ("wrote" if written > read or read == 0 else "read"), basis


def launch_of(trial, returning=False):
    """``"fresh"`` for every trial of an ordinary run and the first of a returning-user run, else ``"relaunch"``."""
    return "relaunch" if returning and type(trial) is int and trial > 1 else "fresh"


def mark(records, found, launch="fresh"):
    """One trial's records, in order, with its launch and first request on the first; returned for ``+=``."""
    if records:
        cache, basis = cache_state(found)
        records[0][MARK] = dict(found or {}, launch=launch, start=STARTS[launch], cache=cache, classified_by=basis)
    return records


def mark_mcp(folder, data, launch="fresh"):
    """A loaded MCP trial's task records, the first marked with the trial's launch and first request."""
    return mark(data.get("tasks") or [], mcp_trial(folder, data), launch)


def mark_angelo(output, trial, trial_report, records, launch="fresh"):
    """An Angelo trial's benchmark records, the first marked with its launch and first request (bench layout)."""
    first = (trial_report.get("tasks") or [{}])[0]
    return mark(records, angelo_trial(Path(output) / ("angelo-trial-%02d-transcript.jsonl" % trial),
                                      Path(output) / ("angelo-trial-%02d" % trial) / "trial-01/qa/requests.jsonl",
                                      first), launch)


# ---------------------------------------------------------------- per trial and per side

def _number(value):
    return value if type(value) in (int, float) else None


def _wall(record):
    for key in ("wall_ms", "total_ms"):   # A live-eval task row has total_ms, the bench's wall_ms.
        if type(record.get(key)) in (int, float):
            return record[key]
    return None


def _requests(record):
    if type(record.get("api_calls")) is int:
        return record["api_calls"]
    requests = (record.get("provider_usage") or {}).get("requests")
    return requests if type(requests) is int else None


def _price(record):
    return _number(record.get("list_price_usd"))


def _total(records, value, expected):
    """The trial's total over its tasks, or None when a task is missing or has no such number."""
    values = [value(record) for record in records]
    if (expected is not None and len(records) != expected) or not values or None in values:
        return None
    return round(sum(values), 4)


def _median(values):
    values = [value for value in values if value is not None]
    return {"n": len(values), "median": round(statistics.median(values), 4) if values else None}


def _order(key):
    side, trial = key
    return SIDES.index(side) if side in SIDES else len(SIDES), trial if type(trial) is int else 0


def warm_ups(rows):
    """``(count, wall ms, list-price USD)`` of a launch's warm-ups over its records; a number None when unknown.

    Each warm-up is recorded once, on the task finished after it, and its price is in that task's list price
    (``bench_mcp_vs_angelo.price_records``) and in ``warm_up_list_price_usd``.
    """
    count, wall, price = 0, 0.0, 0.0
    for row in rows:
        made = row.get("warm_ups") if type(row.get("warm_ups")) is int else 0
        if not made:
            continue
        count += made
        wall = None if wall is None or _number(row.get("warm_up_ms")) is None else wall + row["warm_up_ms"]
        price = (None if price is None or _number(row.get("warm_up_list_price_usd")) is None
                 else price + row["warm_up_list_price_usd"])
    return count, wall, price


def start_numbers(rows):
    """A trial's start: its first task plus every warm-up of the launch and any wait for a readied opening.

    ``{"wall_ms", "list_price_usd", "warm_ups", "warm_up_ms", "opening_wait_ms", "prestarted"}``; a number is
    None when a part of it is unknown. The first task's list price already holds the warm-ups recorded on it.
    """
    first = rows[0]
    count, warm_wall, warm_price = warm_ups(rows)
    waited = _number((first.get("opening") or {}).get("waited_s"))
    opening = round(waited * 1000, 3) if waited is not None else 0.0
    wall, price = _wall(first), _price(first)
    own = _number(first.get("warm_up_list_price_usd")) or 0.0 if first.get("warm_ups") else 0.0
    later = None if warm_price is None else warm_price - own   # Warm-ups recorded on later tasks of the launch.
    # A first message that waited for its readied opening waited for that launch's warm-up too: the two overlap,
    # so the longer of them is added, never both (the wait and the warm-up start when the chat is bound).
    return {"wall_ms": None if None in (wall, warm_wall) else round(wall + max(warm_wall, opening), 3),
            "list_price_usd": None if None in (price, later) else round(price + later, 6),
            "warm_ups": count, "warm_up_ms": warm_wall, "opening_wait_ms": opening,
            "prestarted": first.get("runtime_start") == "prestarted"}


def summarize(records, head_to_head=None):
    """Per trial its launch, first request, start numbers and totals; per side the cold-start and returning medians.

    Totals are over ``head_to_head`` tasks (every task but a preview when None): wall ms, list-price USD
    (``list_price_usd``) and model requests, each None unless every such task has it. Rate-limited trials are
    left out, as from every other median.
    """
    groups = {}
    for record in records:
        if not record.get("invalid"):
            groups.setdefault((record.get("side", "angelo"), record.get("trial")), []).append(record)
    expected = len(head_to_head) if head_to_head is not None else None
    trials = []
    for key in sorted(groups, key=_order):
        rows = groups[key]
        counted = [row for row in rows if (row.get("task") in head_to_head if head_to_head is not None
                                           else row.get("kind") != "preview")]
        found = next((row[MARK] for row in rows if isinstance(row.get(MARK), dict)), None) or {}
        launch = found.get("launch") or "fresh"
        cache, basis = cache_state(found)
        start = start_numbers(counted or rows)
        trials.append({"side": key[0], "trial": key[1], "launch": launch, "start": STARTS[launch],
                       **{field: found.get(field) for field in FIELDS}, "cache": cache, "classified_by": basis,
                       "start_wall_ms": start["wall_ms"], "start_list_price_usd": start["list_price_usd"],
                       **{name: start[name] for name in ("warm_ups", "warm_up_ms", "opening_wait_ms", "prestarted")},
                       "wall_ms": _total(counted, _wall, expected),
                       "list_price_usd": _total(counted, _price, expected),
                       "requests": _total(counted, _requests, expected)})
    sides = {}
    for side in SIDES:
        mine = [row for row in trials if row["side"] == side]
        sides[side] = {"trials": len(mine)}
        for start in ("cold", "returning"):
            group = [row for row in mine if row["start"] == start]
            sides[side][start] = {"trials": [row["trial"] for row in group],
                                  "start_wall_ms": _median([row["start_wall_ms"] for row in group]),
                                  "start_list_price_usd": _median([row["start_list_price_usd"] for row in group]),
                                  **{field: _median([row[field] for row in group])
                                     for field in ("wall_ms", "list_price_usd", "requests")}}
    return {"cold_vs_cold": start_line(trials, "cold"), "returning": start_line(trials, "returning"), "rule": RULE,
            "trials": trials, "sides": sides}


def _count(value):
    return "unknown" if value is None else "{:,}".format(value)


def _usd(value):
    return "unknown" if value is None else "$%.3f" % value


def _seconds(value, unit=" s"):
    return "unknown" if value is None else "%.1f%s" % (value / 1000, unit)


START_NAMES = {"cold": ("Cold start vs cold start", "cold start", "first task after a fresh launch"),
               "returning": ("Returning user", "returning-user start", "first task after a relaunch on a kept profile")}


def start_line(trials, start):
    """One line per kind of start: each side's median start (first task, warm-ups, opening wait), or none."""
    title, name, what = START_NAMES[start]
    if start == "returning" and not any(row["start"] == "returning" for row in trials):
        return None
    parts = []
    for side in SIDES:
        group = [row for row in trials if row["side"] == side and row["start"] == start]
        if not group:
            parts.append("%s: no %s" % (LABELS[side], name))
            continue
        numbers = ", ".join(str(row["trial"]) for row in group)
        which = "trial %s" % numbers if len(group) == 1 else "trials %s, median" % numbers
        extras = [label % count for label, count in (
            ("%d with warm-ups", sum(1 for row in group if row["warm_ups"])),
            ("%d prestarted", sum(1 for row in group if row["prestarted"]))) if count]
        parts.append("%s %s: %s, %s%s" % (
            LABELS[side], which, _usd(_median([row["start_list_price_usd"] for row in group])["median"]),
            _seconds(_median([row["start_wall_ms"] for row in group])["median"]),
            " (%s)" % ", ".join(extras) if extras else ""))
    return "%s (%s, warm-ups and any opening wait included): %s." % (title, what, "; ".join(parts))


def markdown_lines(cold_warm):
    """The SUMMARY.md section: every trial's launch, start and first request, the medians and the start lines."""
    if not cold_warm:
        return []
    trials = cold_warm.get("trials") or []
    if trials and "launch" not in trials[0]:   # A summary made before launch positions: its line, as recorded.
        return ["", "## Cold and warm trials (recorded by the first request's cache use)", "",
                cold_warm.get("rule", ""), "", cold_warm.get("cold_vs_cold", "")]
    lines = ["", "## Cold start and returning user (by launch position)", "", cold_warm["rule"], "",
             "| Side | Trial | Start | First request read / wrote cache | Warm-ups (s) | Start s | Start USD "
             "| Trial wall s | Trial USD | Model requests |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in cold_warm["trials"]:
        start = row["start"] + (" (prestarted)" if row["prestarted"] else "")
        cache = "%s / %s" % (_count(row["read"]), _count(row["write"]))
        warm = "none" if not row["warm_ups"] else "%d (%s)" % (row["warm_ups"], _seconds(row["warm_up_ms"], ""))
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            LABELS.get(row["side"], row["side"]), row["trial"], start, cache, warm,
            _seconds(row["start_wall_ms"], ""), _usd(row["start_list_price_usd"]), _seconds(row["wall_ms"], ""),
            _usd(row["list_price_usd"]), _count(row["requests"])))
    return lines + ["", cold_warm["cold_vs_cold"]] + ([cold_warm["returning"]] if cold_warm.get("returning") else [])


def for_folder(folder, records, layout="bench", returning=False):
    """A finished step's summary from its folder: trials without a mark are marked from their raw records first.

    For results written before the bench recorded first requests (``layout`` "bench": ``mcp-trial-NN``,
    ``angelo-trial-NN`` and the copied transcripts) and for a live eval's folder (``layout`` "eval":
    ``trial-NN``, whose ``records`` are the trials' own task rows). ``returning``: a returning-user step.
    """
    folder, groups = Path(folder), {}
    for record in records:
        groups.setdefault((record.get("side", "angelo"), record.get("trial")), []).append(record)
    for (side, trial), rows in groups.items():
        if type(trial) is not int or any(isinstance(row.get(MARK), dict) and "launch" in row[MARK] for row in rows):
            continue
        launch = launch_of(trial, returning)
        if side == "mcp":
            path = folder / ("mcp-trial-%02d" % trial) / "trial.json"
            mark(rows, mcp_trial(path.parent, json.loads(path.read_text())) if path.is_file() else None, launch)
        elif layout == "eval":
            mark(rows, angelo_trial(None, folder / ("trial-%02d" % trial) / "qa/requests.jsonl", rows[0]), launch)
        else:
            harness = folder / ("angelo-trial-%02d" % trial) / send_record.NAME
            report = (send_record.read(harness).get("trials") or [{}])[0] if send_record.finished(harness) else {}
            mark_angelo(folder, trial, report, rows, launch)
    return summarize(records)
